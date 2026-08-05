"""Atomic directory publication with rollback, as one RAII scope.

Three installers carried byte-identical copies of this dance — take a lock,
stage into a sibling temp dir, move the old target aside, rename the new one in,
restore the backup if the rename fails: `_install_antigravity_cli_bundle`,
`_install_shared_agent_skills` and `_ensure_codex_plugin_source`. They differed only in
what they staged. A fourth copy was about to be written for the skills bridge.

Expressing it as a context manager makes the guarantees the caller's to rely on
rather than the caller's to reimplement: the lock is released, the temp dir is
removed, and the previous contents are restored on any failure, on every exit
path — matching `durable_io._owned_descriptor`, which owns one descriptor for
exactly one scope.
"""
from __future__ import annotations

import os
import shutil
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


# === Retired skills must not outlive their source ===
#
# The publisher was additive: it copied the current skill set in and never
# removed a skill that had left the source tree. Found by the lateral matrix
# after autorun-maintainer moved out of plugins/autorun/skills — it remained in
# ~/.agents/skills, the Claude cache, and the Codex cache. A user who updates
# keeps a retired skill indefinitely, and it keeps appearing in their catalog.
#
# Pruning must never reach a user-authored skill that happens to share a name,
# which is exactly what the .autorun-owned marker already distinguishes.

def _plugin_with_skills(tmp_path, names):
    plugin_dir = tmp_path / "plugins" / "autorun"
    for name in names:
        d = plugin_dir / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name} skill\n---\n", encoding="utf-8"
        )
    return plugin_dir


def test_a_skill_removed_from_source_is_pruned_from_the_shared_root(tmp_path, monkeypatch):
    from autorun import install as inst

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    inst._install_shared_agent_skills(_plugin_with_skills(tmp_path, ["keeper", "retiree"]))
    shared = inst.shared_agents_skills_dir()
    assert (shared / "retiree" / "SKILL.md").is_file(), "fixture must publish both"

    # Second install with the skill gone from source, as a real removal looks.
    shutil.rmtree(tmp_path / "plugins" / "autorun" / "skills" / "retiree")
    inst._install_shared_agent_skills(tmp_path / "plugins" / "autorun")

    assert (shared / "keeper" / "SKILL.md").is_file(), "current skills must survive"
    assert not (shared / "retiree").exists(), (
        "a skill removed from source stayed in the shared root, so it keeps "
        "appearing in every harness catalog that reads it"
    )


def test_switching_a_harness_to_the_shared_route_keeps_user_skills(tmp_path):
    """Dropping the native copy must prune ours, not the whole directory.

    Qwen Code reads ~/.agents/skills (Storage.getUserSkillsDirs maps
    [".qwen", ".agents"] over os.homedir()), so its route moved from the
    extension copy to the shared root. The branch that retires the native copy
    ran shutil.rmtree on <ext>/skills/, taking every skill the user had put
    there with it — the publish branch beside it had already been fixed to
    respect the ownership marker, and this one had not.
    """
    from autorun import install as inst

    plugin_dir = _plugin_with_skills(tmp_path, ["ours"])
    ext_dir = tmp_path / "extensions" / "ar"
    ext_dir.mkdir(parents=True)

    inst._sync_gemini_extension_resources(
        plugin_dir, ext_dir, "ar", "qwen", include_skills=True
    )
    assert (ext_dir / "skills" / "ours" / "SKILL.md").is_file(), "fixture must publish"

    mine = ext_dir / "skills" / "hand-written"
    mine.mkdir()
    (mine / "SKILL.md").write_text("---\nname: hand-written\n---\nmy own\n", encoding="utf-8")
    assert inst.read_owned_marker(mine) is None, "fixture must be unmarked"

    # The same install after the harness gains a shared-root route.
    inst._sync_gemini_extension_resources(
        plugin_dir, ext_dir, "ar", "qwen", include_skills=False
    )

    assert not (ext_dir / "skills" / "ours").exists(), (
        "the plugin's own skill must leave with the route, or the harness "
        "sees it from both the extension and the shared root"
    )
    assert mine.is_dir() and (mine / "SKILL.md").read_text(encoding="utf-8") == (
        "---\nname: hand-written\n---\nmy own\n"
    ), "retiring autorun's route must not delete a skill the user wrote"


def test_pruning_never_removes_a_user_authored_skill(tmp_path, monkeypatch):
    """No marker means not ours, whatever its name."""
    from autorun import install as inst

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    inst._install_shared_agent_skills(_plugin_with_skills(tmp_path, ["keeper"]))
    shared = inst.shared_agents_skills_dir()

    mine = shared / "hand-written"
    mine.mkdir()
    (mine / "SKILL.md").write_text("---\nname: hand-written\n---\nmy own\n", encoding="utf-8")
    assert inst.read_owned_marker(mine) is None, "fixture must be unmarked"

    inst._install_shared_agent_skills(tmp_path / "plugins" / "autorun")

    assert (mine / "SKILL.md").read_text(encoding="utf-8") == "---\nname: hand-written\n---\nmy own\n", (
        "an unmarked skill is user-authored and must never be pruned"
    )


# === Installed skills a user edited or added must survive a reinstall ===
#
# _copy_tree rmtree's the destination before copying, so the Gemini-family
# extension route destroyed anything a user had written under
# <ext>/skills/ — an edit to an installed SKILL.md, or a skill of their own
# dropped beside ours. The shared ~/.agents/skills root already had the right
# policy (marker-gated, conflicts reported by name); the extension route did
# not, which is One Problem, Two Solutions with the unsafe one winning.

def _ext_with_user_content(tmp_path, plugin_dir):
    ext_dir = tmp_path / "ext"
    (ext_dir / "skills").mkdir(parents=True)
    # A skill of the user's own, sharing no name with ours.
    theirs = ext_dir / "skills" / "user-authored"
    theirs.mkdir()
    (theirs / "SKILL.md").write_text("---\nname: user-authored\n---\nmine\n", encoding="utf-8")
    return ext_dir


def test_reinstall_preserves_a_user_authored_skill_in_the_extension(tmp_path, monkeypatch):
    from autorun import install as inst

    plugin_dir = _plugin_with_skills(tmp_path, ["ours"])
    ext_dir = _ext_with_user_content(tmp_path, plugin_dir)

    inst._sync_gemini_extension_resources(
        plugin_dir, ext_dir, "ar", "gemini", include_skills=True
    )

    theirs = ext_dir / "skills" / "user-authored" / "SKILL.md"
    assert theirs.is_file(), "a user-authored skill was destroyed by reinstall"
    assert theirs.read_text(encoding="utf-8") == "---\nname: user-authored\n---\nmine\n"
    assert (ext_dir / "skills" / "ours" / "SKILL.md").is_file(), "our skill must install"


def test_reinstall_preserves_a_user_edit_to_a_skill_we_did_not_install(tmp_path):
    """An unmarked directory sharing our name is theirs, not a stale copy of ours."""
    from autorun import install as inst

    plugin_dir = _plugin_with_skills(tmp_path, ["ours"])
    ext_dir = tmp_path / "ext"
    collision = ext_dir / "skills" / "ours"
    collision.mkdir(parents=True)
    (collision / "SKILL.md").write_text("---\nname: ours\n---\nhand edited\n", encoding="utf-8")

    inst._sync_gemini_extension_resources(
        plugin_dir, ext_dir, "ar", "gemini", include_skills=True
    )

    assert (collision / "SKILL.md").read_text(encoding="utf-8") == "---\nname: ours\n---\nhand edited\n", (
        "an unmarked skill must never be overwritten, even when the name collides"
    )


def test_reinstall_prunes_our_retired_skill_from_the_extension(tmp_path):
    """A skill removed upstream must leave the extension too.

    Added because a mutation that deleted the prune passed every other test in
    this area: they all build a fresh extension directory, so nothing retired
    exists for the prune to act on. A guard needs a fixture that has something
    to lose.
    """
    from autorun import install as inst

    plugin_dir = _plugin_with_skills(tmp_path, ["keeper", "retiree"])
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()

    inst._sync_gemini_extension_resources(
        plugin_dir, ext_dir, "ar", "gemini", include_skills=True
    )
    assert (ext_dir / "skills" / "retiree" / "SKILL.md").is_file(), "fixture must install both"

    shutil.rmtree(plugin_dir / "skills" / "retiree")
    inst._sync_gemini_extension_resources(
        plugin_dir, ext_dir, "ar", "gemini", include_skills=True
    )

    assert (ext_dir / "skills" / "keeper" / "SKILL.md").is_file()
    assert not (ext_dir / "skills" / "retiree").exists(), (
        "a skill removed upstream stayed in the extension and keeps appearing "
        "in that harness's catalog"
    )
