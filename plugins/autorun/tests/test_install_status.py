"""Health checks, the report, and the metadata stamp.

The check that matters most is the one a file listing cannot make. Autorun
fails open by design, so a hook whose interpreter cannot resolve is installed,
is invoked, fails, allows the tool call, and says nothing — which from the
outside is indistinguishable from a hook that ran and approved. That is the
failure this module exists to surface.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("AUTORUN_HOME", "/tmp/autorun-test-home")
os.environ.setdefault("AUTORUN_TEST_STATE_DIR", "/tmp/autorun-test-state")

from autorun.installer import memory, status  # noqa: E402
from autorun.installer.fs import Verdict  # noqa: E402
from autorun.installer.status import Level  # noqa: E402


def runner(returncode: int = 0):
    def run(argv):
        return subprocess.CompletedProcess(argv, returncode, "", "")

    return run


# ─── The check a listing cannot make ─────────────────────────────────────────


def test_a_hook_that_cannot_start_is_reported_as_broken():
    found = status.health(hook_command=("uv", "run"), run=runner(127))

    assert found[0].level is Level.BROKEN
    assert "re-run the install" in found[0].fix


def test_a_hook_that_starts_is_ok():
    assert status.health(hook_command=("uv", "run"), run=runner(0))[0].level is Level.OK


def test_a_missing_interpreter_is_reported_never_raised():
    def explodes(argv):
        raise FileNotFoundError("uv")

    found = status.health(hook_command=("uv", "run"), run=explodes)

    assert found[0].level is Level.BROKEN
    assert "FileNotFoundError" in found[0].detail


def test_an_unconfigured_hook_is_a_warning_not_a_break():
    """Nothing to run is a different situation from something that will not."""
    assert status.health(run=runner(0))[0].level is Level.WARN


def test_a_broken_finding_carries_the_fix_in_its_description():
    described = status.health(hook_command=("x",), run=runner(1))[0].describe()

    assert "fix:" in described


# ─── Findings the walk cannot produce ────────────────────────────────────────


def test_a_block_from_a_version_we_no_longer_ship_is_reported(tmp_path):
    """Uninstall removes the slugs it knows, so an unknown one stays in the
    user's file forever with nothing reporting it."""
    theirs = tmp_path / "AGENTS.md"
    memory.splice(theirs, "old guidance", memory.Block("retired-slug"))

    found = status.health(
        hook_command=("x",), memory_files=[theirs], known_slugs=["guidance"], run=runner(0)
    )

    assert any(f.level is Level.WARN and "retired-slug" in f.check for f in found)


def test_a_block_we_do_know_is_not_reported(tmp_path):
    theirs = tmp_path / "AGENTS.md"
    memory.splice(theirs, "ours", memory.Block("guidance"))

    found = status.health(
        hook_command=("x",), memory_files=[theirs], known_slugs=["guidance"], run=runner(0)
    )

    assert not any("memory block" in f.check for f in found)


def test_one_product_under_two_names_is_reported():
    """The shape that made a Codex tree survive every uninstall: the registry
    listed the plugin twice and each install resolved whichever it read last."""
    found = status.health(
        hook_command=("x",), registry_entries={"codex": ["ar", "ar"]}, run=runner(0)
    )

    assert any("duplicate" in f.detail for f in found)


def test_a_registry_listing_one_product_once_is_clean():
    found = status.health(
        hook_command=("x",), registry_entries={"codex": ["ar", "pdf-extractor"]}, run=runner(0)
    )

    assert not any("duplicate" in f.detail for f in found)


# ─── The report leads with outcomes ──────────────────────────────────────────


class Decision:
    def __init__(self, verdict, text):
        self.verdict, self._text = verdict, text

    def describe(self):
        return self._text


@pytest.fixture
def hundred_skips_and_one_keep():
    decisions = [Decision(Verdict.SKIP, "skipped") for _ in range(100)]
    decisions.append(Decision(Verdict.KEEP, "your edit in skills/commit"))
    return decisions


def test_totals_lead_and_a_kept_edit_is_not_buried(hundred_skips_and_one_keep):
    lines = list(status.report_lines(hundred_skips_and_one_keep))

    assert lines[0] == "keep=1 skip=100"
    assert len(lines) == 2
    assert "your edit" in lines[1]


def test_verbose_lists_everything(hundred_skips_and_one_keep):
    assert len(list(status.report_lines(hundred_skips_and_one_keep, verbose=True))) == 102


def test_an_ok_finding_is_silent_unless_asked_for():
    good = status.Finding("hook command", Level.OK)

    assert list(status.report_lines([], [good])) == []
    assert list(status.report_lines([], [good], verbose=True))


# ─── Metadata ────────────────────────────────────────────────────────────────


def test_metadata_is_reproducible_from_the_source_epoch():
    """A timestamp read from the clock makes every rebuild differ and every
    diff noisy."""
    env = {"SOURCE_DATE_EPOCH": "1700000000"}

    first = status.metadata_document("1.0.0", commit="abc", env=env)
    second = status.metadata_document("1.0.0", commit="abc", env=env)

    assert first == second
    assert first["build_time"].startswith("2023-")


def test_a_local_install_does_not_dirty_a_tracked_metadata_file(tmp_path):
    """Otherwise a developer running their own installer gets an unexplained
    change in git status."""
    plugin = tmp_path / "plugins" / "autorun"
    (plugin / status.METADATA_SUBPATH.parent).mkdir(parents=True)
    tracked = plugin / status.METADATA_SUBPATH
    tracked.write_text('{"version": "old"}\n', encoding="utf-8")

    assert status.write_metadata(plugin, {"version": "new"}) is None
    assert tracked.read_text(encoding="utf-8") == '{"version": "old"}\n'


def test_an_explicit_release_build_stamps_it(tmp_path):
    plugin = tmp_path / "plugins" / "autorun"
    (plugin / status.METADATA_SUBPATH.parent).mkdir(parents=True)
    (plugin / status.METADATA_SUBPATH).write_text("{}\n", encoding="utf-8")

    written = status.write_metadata(plugin, {"version": "1.0.0", "commit": "abc"}, allowed=True)

    assert written is not None
    assert json.loads(written.read_text(encoding="utf-8"))["commit"] == "abc"


def test_stamping_the_same_content_twice_writes_nothing(tmp_path):
    plugin = tmp_path / "plugins" / "autorun"
    document = {"version": "1.0.0"}
    status.write_metadata(plugin, document)

    assert status.write_metadata(plugin, document, allowed=True) is None


def test_a_plugin_with_no_metadata_yet_is_stamped_without_the_opt_in(tmp_path):
    plugin = tmp_path / "plugins" / "fresh"

    written = status.write_metadata(plugin, {"version": "1.0.0"})

    assert written == plugin / status.METADATA_SUBPATH
