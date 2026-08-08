"""Exporting a plan must not hold the state lock while copying a file.

``PlanExport.export`` opens the global session state, copies the plan file,
embeds metadata into the copy, and only then commits. The comment at the copy
site says the file I/O is deliberate and "small, <1MB".

That reasoning holds only while the lock is one JSON file being rewritten by
one process at a time. The state lock is global: while it is held, no other
session can record a policy change, a task transition, or a stop decision.
Tying that window to a filesystem copy — which may hit a slow disk, a network
mount, or a full volume — makes every session's writes wait on one file
operation, and the wait is bounded by a half-second hook budget.

So the copy moves out of the lock, and what replaces it has to keep the
properties the lock was providing:

  * a plan exported twice is still exported once;
  * the destination name is still reserved before anything is written to it,
    and a file that was already there is never overwritten;
  * a failure midway leaves no entry claiming an export that did not happen.
"""
from __future__ import annotations

import builtins
import sys
import threading
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
SRC_DIR = PLUGIN_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autorun import plan_export as pe  # noqa: E402
from autorun import session_manager as sm  # noqa: E402


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    directory = tmp_path / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(directory))
    sm._reset_for_testing()
    yield directory
    sm._reset_for_testing()


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    (root / "notes").mkdir(parents=True)
    return root


@pytest.fixture
def plan_file(tmp_path):
    path = tmp_path / "plans" / "some-plan.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# A plan\n\nDo the thing.\n", encoding="utf-8")
    return path


class _Ctx:
    """The little of EventContext that PlanExport reads."""

    def __init__(self, project_dir, session_id="export-session"):
        self.session_id = session_id
        self.cwd = str(project_dir)
        self.event = "PostToolUse"
        self.cli_type = "claude"
        self.tool_name = "ExitPlanMode"
        self.tool_input = {}


@pytest.fixture
def exporter(isolated_state, project):
    return pe.PlanExport(_Ctx(project))


def _state_open_during(monkeypatch, recorder):
    """Record whether the state lock is held when a callable runs."""
    depth = {"value": 0}
    real_session_state = pe.session_state

    import contextlib

    @contextlib.contextmanager
    def counting(*args, **kwargs):
        depth["value"] += 1
        try:
            with real_session_state(*args, **kwargs) as state:
                yield state
        finally:
            depth["value"] -= 1

    monkeypatch.setattr(pe, "session_state", counting)
    recorder["depth"] = depth
    return depth


class TestNoFileIoWhileStateIsLocked:
    def test_the_copy_happens_outside_the_state_lock(
        self, exporter, plan_file, monkeypatch
    ):
        """The observation this whole change exists for."""
        recorder = {}
        depth = _state_open_during(monkeypatch, recorder)
        observed = []

        real_copy = pe.shutil.copy2

        def watched_copy(src, dst, *args, **kwargs):
            observed.append(depth["value"])
            return real_copy(src, dst, *args, **kwargs)

        monkeypatch.setattr(pe.shutil, "copy2", watched_copy)

        result = exporter.export(plan_file)
        assert result["success"], result

        assert observed, "no copy happened, so the test proved nothing"
        assert all(held == 0 for held in observed), (
            "The plan file was copied while the global state lock was held. "
            "Every other session's writes queue behind that copy, on a "
            f"half-second budget. Lock depth during copy: {observed}"
        )

    def test_metadata_embedding_also_happens_outside_the_lock(
        self, exporter, plan_file, monkeypatch
    ):
        recorder = {}
        depth = _state_open_during(monkeypatch, recorder)
        observed = []

        real_embed = pe.embed_plan_metadata

        def watched_embed(*args, **kwargs):
            observed.append(depth["value"])
            return real_embed(*args, **kwargs)

        monkeypatch.setattr(pe, "embed_plan_metadata", watched_embed)

        assert exporter.export(plan_file)["success"]
        assert observed and all(held == 0 for held in observed), (
            f"Metadata was embedded with the state lock held: {observed}"
        )


class TestExportBehaviorIsUnchanged:
    def test_a_plan_is_exported_once(self, exporter, plan_file, project):
        first = exporter.export(plan_file)
        assert first["success"] and not first.get("skipped")

        second = exporter.export(plan_file)
        assert second.get("skipped") is True, (
            "The same plan was exported twice. Duplicate suppression used to "
            "come from doing the check and the write under one lock."
        )
        assert second["destination"] == first["destination"]

    def test_a_tracking_record_never_suppresses_a_missing_file(
        self, exporter, plan_file
    ):
        content_hash = pe.get_content_hash(plan_file)
        missing = exporter.project_dir / "notes" / "missing.md"
        with pe.session_state(pe.GLOBAL_SESSION_ID) as state:
            state["tracking"] = {
                content_hash: {
                    "exported_to": str(missing),
                    "exported_at": "2026-07-22T00:00:00",
                    "rejected": False,
                }
            }

        result = exporter.export(plan_file)
        assert result["success"] and not result.get("skipped"), result
        assert Path(result["destination"]).exists()

    def test_a_modified_export_is_not_accepted_as_a_valid_duplicate(
        self, exporter, plan_file
    ):
        first = exporter.export(plan_file)
        Path(first["destination"]).write_text("tampered", encoding="utf-8")

        second = exporter.export(plan_file)
        assert second["success"] and not second.get("skipped"), second
        assert second["destination"] != first["destination"]

    def test_forcing_a_re_export_still_writes_a_second_file(
        self, exporter, plan_file
    ):
        first = exporter.export(plan_file)
        forced = exporter.export(plan_file, force=True)

        assert forced["success"] and not forced.get("skipped")
        assert forced["destination"] != first["destination"]
        assert Path(first["destination"]).exists()
        assert Path(forced["destination"]).exists()

    def test_the_exported_file_has_the_plan_content(self, exporter, plan_file):
        result = exporter.export(plan_file)
        written = Path(result["destination"]).read_text(encoding="utf-8")
        assert "Do the thing." in written

    def test_the_plan_is_removed_from_active_plans(self, exporter, plan_file):
        exporter.atomic_update_active_plans(
            lambda plans: plans.__setitem__(str(plan_file), {"cwd": "x"})
        )
        exporter.export(plan_file)
        assert str(plan_file) not in exporter.active_plans

    def test_a_rejected_export_goes_to_the_rejected_directory(
        self, exporter, plan_file
    ):
        result = exporter.export(plan_file, rejected=True)
        assert result["success"]
        assert "rejected" in result["destination"]


class TestDestinationSafety:
    def test_an_existing_file_is_never_overwritten(self, exporter, plan_file):
        first = exporter.export(plan_file)
        original = Path(first["destination"])
        original.write_text("someone else edited this", encoding="utf-8")

        second = exporter.export(plan_file, force=True)

        assert Path(second["destination"]) != original
        assert original.read_text(encoding="utf-8") == "someone else edited this", (
            "An export overwrote a file it did not create."
        )

    def test_a_file_appearing_between_choosing_and_writing_is_not_clobbered(
        self, exporter, plan_file, monkeypatch
    ):
        """The name has to be reserved by creating it, not by checking first.

        Checking whether a name is free and then writing to it leaves a gap.
        Outside the lock that gap is real: another process, or the user, can
        take the name in between.
        """
        assert exporter.export(plan_file)["success"]

        real_open = builtins.open
        stolen = {"done": False, "path": None}

        def open_but_name_is_taken(path, mode="r", *args, **kwargs):
            candidate = Path(path)
            if mode == "x" and not stolen["done"] and candidate.suffix == ".md":
                # Another actor wins immediately before our exclusive create.
                with real_open(candidate, "w", encoding="utf-8") as handle:
                    handle.write("taken by someone else")
                stolen["done"] = True
                stolen["path"] = candidate
            return real_open(path, mode, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", open_but_name_is_taken)
        second = exporter.export(plan_file, force=True)
        monkeypatch.undo()

        assert second["success"], second
        assert stolen["done"] is True
        assert stolen["path"].read_text(encoding="utf-8") == "taken by someone else"
        destination = Path(second["destination"])
        assert destination != stolen["path"]
        assert "Do the thing." in destination.read_text(encoding="utf-8"), (
            "The export wrote somewhere it did not own, or lost its content."
        )

    def test_concurrent_exports_of_the_same_plan_produce_one_record(
        self, exporter, plan_file, project
    ):
        """Two hooks racing must not both claim to be the first export."""
        results = []
        barrier = threading.Barrier(3)

        def run():
            worker = pe.PlanExport(_Ctx(project))
            barrier.wait(timeout=10)
            results.append(worker.export(plan_file))

        threads = [threading.Thread(target=run) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        successful = [r for r in results if r.get("success")]
        assert len(successful) == 3, f"an export failed outright: {results}"
        fresh = [r for r in successful if not r.get("skipped")]
        assert len(fresh) == 1, (
            "More than one concurrent export recorded itself as the first. "
            f"Results: {results}"
        )


class TestFailureLeavesNoFalseRecord:
    def test_a_failed_copy_does_not_record_an_export(
        self, exporter, plan_file, monkeypatch
    ):
        def refuse(*args, **kwargs):
            raise OSError("No space left on device")

        monkeypatch.setattr(pe.shutil, "copy2", refuse)

        result = exporter.export(plan_file)
        monkeypatch.undo()

        assert not result.get("success"), (
            "A failed copy reported success."
        )
        assert exporter.export(plan_file).get("skipped") is not True, (
            "A failed export left a record claiming the plan was exported, so "
            "the retry was suppressed and the plan is now lost."
        )

    def test_a_failed_copy_leaves_no_partial_file_behind(
        self, exporter, plan_file, project, monkeypatch
    ):
        def refuse(*args, **kwargs):
            raise OSError("No space left on device")

        monkeypatch.setattr(pe.shutil, "copy2", refuse)
        exporter.export(plan_file)
        monkeypatch.undo()

        leftovers = list((project / "notes").rglob("*.md"))
        assert not leftovers, (
            f"A failed export left files behind: {leftovers}"
        )

    def test_a_crash_after_file_publication_is_adopted_on_retry(
        self, exporter, plan_file, monkeypatch
    ):
        real_record = type(exporter)._record_complete_export
        calls = {"count": 0}

        def crash_once(self, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise KeyboardInterrupt("after publication before tracking")
            return real_record(self, *args, **kwargs)

        monkeypatch.setattr(type(exporter), "_record_complete_export", crash_once)
        with pytest.raises(KeyboardInterrupt):
            exporter.export(plan_file)

        result = exporter.export(plan_file)
        assert result["success"]
        assert result.get("recovered") is True
        exported = list((exporter.project_dir / "notes").glob("*.md"))
        assert len(exported) == 1, exported
