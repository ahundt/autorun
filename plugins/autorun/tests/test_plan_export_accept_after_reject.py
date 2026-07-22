"""Rejecting a plan must not make it unexportable if it is later accepted.

Export deduplicates on the plan's content hash, which is right: the same plan
should not be written to notes/ twice. But a plan that is *not* accepted is
also recorded under that hash, pointing at its notes/rejected/ backup — and
those two records were indistinguishable.

So the sequence that actually happens when a plan is revised and re-proposed
— reject, adjust nothing, accept — hits the deduplication branch, returns the
rejected-directory path, and reports success. The accepted plan never reaches
notes/. Nothing looks wrong: the hook prints "Plan exported to
notes/rejected/...", which reads like a successful export of a rejected plan.

That is how this repository's own state-store plan was approved and then
found only under notes/rejected/.

A record therefore has to say which kind of export it was. Records written
before this distinction existed carry no flag, so their kind is inferred from
whether the path they point at is inside the rejected directory.
"""
from __future__ import annotations

import sys
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
    def __init__(self, project_dir, session_id="accept-after-reject"):
        self.session_id = session_id
        self.cwd = str(project_dir)
        self.event = "PostToolUse"
        self.cli_type = "claude"
        self.tool_name = "ExitPlanMode"
        self.tool_input = {}


@pytest.fixture
def exporter(isolated_state, project):
    return pe.PlanExport(_Ctx(project))


def _is_under(path: str, directory: str) -> bool:
    return directory in Path(path).parts


class TestAcceptAfterReject:
    def test_accepting_a_previously_rejected_plan_exports_it(
        self, exporter, plan_file
    ):
        """The reported failure, in order."""
        exporter.backup_to_rejected(plan_file, "plan")
        exporter.finalize_backup(plan_file)

        accepted = exporter.export(plan_file)

        assert not accepted.get("skipped"), (
            "The accepted plan was suppressed by the record of its own "
            "rejection, so it was never written to notes/. The hook still "
            f"reported success. Result: {accepted}"
        )
        assert accepted["success"]
        assert not _is_under(accepted["destination"], "rejected"), (
            f"The accepted plan landed in the rejected directory: "
            f"{accepted['destination']}"
        )
        assert Path(accepted["destination"]).exists()

    def test_the_rejected_backup_is_left_alone(self, exporter, plan_file):
        """Accepting later must not remove the record of the earlier decision."""
        exporter.backup_to_rejected(plan_file, "plan")
        finalized = exporter.finalize_backup(plan_file)
        backup = finalized.get("destination") or ""

        exporter.export(plan_file)

        if backup:
            assert Path(backup).exists(), "the rejected backup was removed"

    def test_the_content_still_arrives_intact(self, exporter, plan_file):
        exporter.backup_to_rejected(plan_file, "plan")
        exporter.finalize_backup(plan_file)

        destination = Path(exporter.export(plan_file)["destination"])
        assert "Do the thing." in destination.read_text(encoding="utf-8")


class TestDeduplicationStillHolds:
    def test_accepting_the_same_plan_twice_exports_once(self, exporter, plan_file):
        first = exporter.export(plan_file)
        second = exporter.export(plan_file)

        assert not first.get("skipped")
        assert second.get("skipped") is True, (
            "Distinguishing accepted from rejected records broke ordinary "
            "duplicate suppression."
        )
        assert second["destination"] == first["destination"]

    def test_rejecting_the_same_plan_twice_records_once(self, exporter, plan_file):
        exporter.backup_to_rejected(plan_file, "plan")
        exporter.finalize_backup(plan_file)

        again = exporter.export(plan_file, rejected=True)
        assert again.get("skipped") is True, (
            f"A second rejected export was not suppressed: {again}"
        )

    def test_exporting_rejected_after_accepting_is_suppressed(
        self, exporter, plan_file
    ):
        """Symmetry: an accepted record answers a later rejected request."""
        exporter.export(plan_file)
        rejected = exporter.export(plan_file, rejected=True)
        assert rejected.get("skipped") is True

    def test_force_still_overrides_both(self, exporter, plan_file):
        first = exporter.export(plan_file)
        forced = exporter.export(plan_file, force=True)

        assert not forced.get("skipped")
        assert forced["destination"] != first["destination"]


class TestRecordsWrittenBeforeThisDistinction:
    """Existing installations have records with no kind on them.

    Re-exporting everything on first run would be worse than the bug, so the
    kind of an old record is inferred from where it points.
    """

    def _write_legacy_record(self, exporter, plan_file, destination: str):
        content_hash = pe.get_content_hash(plan_file)

        def add(tracking):
            tracking[content_hash] = {
                "exported_to": destination,
                "exported_at": "2026-01-01T00:00:00",
            }

        exporter.atomic_update_tracking(add)

    def test_a_legacy_rejected_record_does_not_suppress_acceptance(
        self, exporter, plan_file, project
    ):
        legacy = project / "notes" / "rejected" / "old-backup.md"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("old", encoding="utf-8")
        self._write_legacy_record(exporter, plan_file, str(legacy))

        accepted = exporter.export(plan_file)

        assert not accepted.get("skipped"), (
            "A record written before this distinction, pointing into the "
            "rejected directory, still suppressed an accepted export."
        )
        assert not _is_under(accepted["destination"], "rejected")

    def test_a_legacy_accepted_record_still_suppresses_re_export(
        self, exporter, plan_file, project
    ):
        legacy = project / "notes" / "old-export.md"
        legacy.write_text("old", encoding="utf-8")
        self._write_legacy_record(exporter, plan_file, str(legacy))

        assert exporter.export(plan_file).get("skipped") is True, (
            "An old accepted record stopped suppressing duplicates, so every "
            "previously exported plan would be written again."
        )

    def test_a_legacy_record_with_no_path_cannot_suppress_a_real_export(
        self, exporter, plan_file
    ):
        """A claim with no durable file is not evidence of an export."""
        self._write_legacy_record(exporter, plan_file, "")
        result = exporter.export(plan_file)
        assert result["success"] and not result.get("skipped")
        assert Path(result["destination"]).exists()
