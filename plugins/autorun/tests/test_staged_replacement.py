"""Atomic directory publication with rollback, as one RAII scope.

Three installers carried byte-identical copies of this dance — take a lock,
stage into a sibling temp dir, move the old target aside, rename the new one in,
restore the backup if the rename fails: `_install_antigravity_cli_bundle`,
`_install_codex_skills` and `_ensure_codex_plugin_source`. They differed only in
what they staged. A fourth copy was about to be written for the skills bridge.

Expressing it as a context manager makes the guarantees the caller's to rely on
rather than the caller's to reimplement: the lock is released, the temp dir is
removed, and the previous contents are restored on any failure, on every exit
path — matching `durable_io._owned_descriptor`, which owns one descriptor for
exactly one scope.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.install import staged_replacement  # noqa: E402


def _write_tree(root: Path, marker: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(marker, encoding="utf-8")
    (root / "nested").mkdir(exist_ok=True)
    (root / "nested" / "file.txt").write_text(marker, encoding="utf-8")
    return root


# --------------------------------------------------------------------------
# Publication
# --------------------------------------------------------------------------


def test_publishes_a_new_directory(tmp_path):
    target = tmp_path / "plugin"
    with staged_replacement(target, prefix=".t-") as staged:
        _write_tree(staged, "new")
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "new"
    assert (target / "nested" / "file.txt").is_file()


def test_replaces_an_existing_directory(tmp_path):
    target = _write_tree(tmp_path / "plugin", "old")
    with staged_replacement(target, prefix=".t-") as staged:
        _write_tree(staged, "new")
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "new"


def test_replaces_a_symlink_at_the_target(tmp_path):
    """A symlinked target must be swapped, not written through."""
    elsewhere = _write_tree(tmp_path / "elsewhere", "elsewhere")
    target = tmp_path / "plugin"
    target.symlink_to(elsewhere)

    with staged_replacement(target, prefix=".t-") as staged:
        _write_tree(staged, "new")

    assert not target.is_symlink()
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "new"
    assert (elsewhere / "SKILL.md").read_text(encoding="utf-8") == "elsewhere"


def test_creates_the_parent_directory(tmp_path):
    target = tmp_path / "deep" / "nested" / "plugin"
    with staged_replacement(target, prefix=".t-") as staged:
        _write_tree(staged, "new")
    assert (target / "SKILL.md").is_file()


# --------------------------------------------------------------------------
# Rollback — the reason this is a context manager
# --------------------------------------------------------------------------


def test_a_failure_while_staging_leaves_the_original_intact(tmp_path):
    target = _write_tree(tmp_path / "plugin", "old")

    with pytest.raises(RuntimeError):
        with staged_replacement(target, prefix=".t-") as staged:
            _write_tree(staged, "half-written")
            raise RuntimeError("staging blew up")

    assert (target / "SKILL.md").read_text(encoding="utf-8") == "old"
    assert (target / "nested" / "file.txt").read_text(encoding="utf-8") == "old"


def test_a_failure_while_staging_leaves_no_target_when_there_was_none(tmp_path):
    target = tmp_path / "plugin"

    with pytest.raises(RuntimeError):
        with staged_replacement(target, prefix=".t-") as staged:
            _write_tree(staged, "half-written")
            raise RuntimeError("boom")

    assert not target.exists()


def test_a_failed_rename_restores_the_previous_contents(tmp_path, monkeypatch):
    """The rollback branch: os.replace fails after the backup is taken."""
    target = _write_tree(tmp_path / "plugin", "old")
    real_replace = os.replace
    calls = {"n": 0}

    def _flaky(src, dst):
        calls["n"] += 1
        # First call moves the target aside; fail the second (staged -> target).
        if calls["n"] == 2:
            raise OSError("rename failed")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _flaky)

    with pytest.raises(OSError):
        with staged_replacement(target, prefix=".t-") as staged:
            _write_tree(staged, "new")

    assert (target / "SKILL.md").read_text(encoding="utf-8") == "old"


# --------------------------------------------------------------------------
# Cleanup — nothing left behind on any exit path
# --------------------------------------------------------------------------


def test_no_staging_directory_survives_success(tmp_path):
    target = tmp_path / "plugin"
    with staged_replacement(target, prefix=".probe-") as staged:
        _write_tree(staged, "new")
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".probe-")]
    assert leftovers == []


def test_no_staging_directory_survives_failure(tmp_path):
    target = tmp_path / "plugin"
    with pytest.raises(RuntimeError):
        with staged_replacement(target, prefix=".probe-") as staged:
            _write_tree(staged, "new")
            raise RuntimeError("boom")
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".probe-")]
    assert leftovers == []


def test_the_lock_is_released_so_a_second_pass_can_run(tmp_path):
    target = tmp_path / "plugin"
    for marker in ("first", "second"):
        with staged_replacement(target, prefix=".t-") as staged:
            _write_tree(staged, marker)
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "second"


def test_the_lock_is_released_after_a_failure(tmp_path):
    target = tmp_path / "plugin"
    with pytest.raises(RuntimeError):
        with staged_replacement(target, prefix=".t-") as staged:
            _write_tree(staged, "boom")
            raise RuntimeError("boom")
    with staged_replacement(target, prefix=".t-") as staged:
        _write_tree(staged, "recovered")
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "recovered"


def test_staging_happens_beside_the_target(tmp_path):
    """Same filesystem, or os.replace is not atomic."""
    target = tmp_path / "plugin"
    seen: list[Path] = []
    with staged_replacement(target, prefix=".t-") as staged:
        seen.append(staged)
        _write_tree(staged, "new")
    assert seen[0].parent.parent == target.parent


# --------------------------------------------------------------------------
# Preconditions must run inside the lock, not before it
# --------------------------------------------------------------------------


def test_a_precondition_refusal_leaves_the_target_untouched(tmp_path):
    """Ownership checks belong in the same critical section as the write.

    `_ensure_codex_plugin_source` checked "is this directory user-authored?"
    inside one lock scope, released it, then reacquired it to publish. A
    user-authored directory created in that window was silently replaced —
    defeating the guard's entire purpose.
    """
    from autorun.install import StagedReplacementRefused

    target = _write_tree(tmp_path / "plugin", "theirs")

    with pytest.raises(StagedReplacementRefused, match="user-owned"):
        with staged_replacement(
            target, prefix=".t-", precondition=lambda: "user-owned directory"
        ) as staged:
            _write_tree(staged, "ours")

    assert (target / "SKILL.md").read_text(encoding="utf-8") == "theirs"


def test_a_passing_precondition_publishes_normally(tmp_path):
    target = tmp_path / "plugin"
    with staged_replacement(target, prefix=".t-", precondition=lambda: None) as staged:
        _write_tree(staged, "ours")
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "ours"


def test_the_precondition_sees_state_under_the_lock(tmp_path):
    """It must observe the target as it is at publication time."""
    target = _write_tree(tmp_path / "plugin", "existing")
    seen = {}

    def _check():
        seen["existed"] = (target / "SKILL.md").read_text(encoding="utf-8")
        return None

    with staged_replacement(target, prefix=".t-", precondition=_check) as staged:
        _write_tree(staged, "new")

    assert seen["existed"] == "existing"


def test_a_refusal_leaves_no_staging_directory(tmp_path):
    from autorun.install import StagedReplacementRefused

    target = tmp_path / "plugin"
    with pytest.raises(StagedReplacementRefused):
        with staged_replacement(target, prefix=".probe-", precondition=lambda: "no"):
            pass
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".probe-")] == []
