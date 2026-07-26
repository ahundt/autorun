"""Uninstall removes every artifact install wrote, and nothing else.

`test_uninstall_cleanup.py` covers the two surfaces that already had teardown:
harness memory blocks and bridged skill *symlinks*. Everything else install
wrote survived an uninstall:

- Gemini, Qwen and Antigravity extension directories, materialized by
  `<cli> extensions install` and then filled in by
  `_sync_gemini_extension_resources`.
- ForgeCode command files copied into ``<base>/commands/``.
- Bridged skills written in ``copy`` mode — a real directory, so the
  symlink-only cleanup skipped them. That made ``--claude-agents-skills copy``
  a one-way door.
- autorun's entry in the Codex personal marketplace and the plugin source
  directory it points at.
- ``.autorun-install.lock`` files left by `staged_replacement`.
- The running daemon, which keeps serving hooks from code that no longer
  exists on disk.

Every removal here is gated on a self-identifying ownership marker rather than
on a name, because all of these locations are shared with the user and with
other tools. A directory autorun did not create is never deleted, which is the
invariant each "leaves ... alone" test below pins down.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.install import (  # noqa: E402
    CmdResult,
    OwnedMarker,
    bridge_agents_skills,
    platform_extensions_dir,
    read_owned_marker,
    uninstall_plugins,
    write_owned_marker,
)
from autorun.platforms import PLATFORMS  # noqa: E402


class ScratchHome(type(Path())):  # type: ignore[misc]
    """A HOME path that also records the commands uninstall ran.

    Subclassed rather than wrapped so every existing `home / ".forge"` idiom in
    these tests keeps working; ``Path`` defines ``__slots__``, so an attribute
    cannot simply be attached to an instance.
    """

    commands: list[list[str]]


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point HOME at a scratch tree and record every external command."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FORGE_CONFIG", raising=False)
    monkeypatch.delenv("AUTORUN_CLAUDE_AGENTS_SKILLS", raising=False)

    calls: list[list[str]] = []

    def _record(cmd, *_a, **_k):
        calls.append(list(cmd))
        return CmdResult(True, "ok")

    monkeypatch.setattr("autorun.install.run_cmd", _record)
    monkeypatch.setattr("autorun.install._restart_daemon_if_running", lambda: None)
    monkeypatch.setattr("autorun.install._stop_daemon_if_running", lambda: None)
    home = ScratchHome(tmp_path)
    home.commands = calls
    return home


def _config_dir(home: Path, platform_name: str) -> Path:
    platform = PLATFORMS[platform_name]
    return home / platform.config_dir.lstrip("~/").rstrip("/")


# --------------------------------------------------------------------------
# Ownership markers
# --------------------------------------------------------------------------


def test_marker_round_trips_plugin_and_files(tmp_path):
    write_owned_marker(tmp_path, plugin="ar", files=("a.md", "b.md"))
    assert read_owned_marker(tmp_path) == OwnedMarker(
        plugin="ar", files=("a.md", "b.md")
    )


def test_unmarked_directory_reads_as_not_owned(tmp_path):
    assert read_owned_marker(tmp_path) is None


def test_legacy_prose_marker_still_reads_as_owned(tmp_path):
    """Markers written before this change are plain prose, not JSON.

    Installs predating the manifest format must keep being recognized as
    autorun's, or an upgrade would strand every directory they claim.
    """
    (tmp_path / ".autorun-owned").write_text(
        "Autorun-owned. Safe to delete to un-claim this directory.\n",
        encoding="utf-8",
    )
    marker = read_owned_marker(tmp_path)
    assert marker is not None
    assert marker.plugin == ""
    assert marker.files == ()


def test_marker_keeps_a_human_readable_note(tmp_path):
    """Someone who finds this file must learn what deleting it does."""
    write_owned_marker(tmp_path, plugin="ar")
    text = (tmp_path / ".autorun-owned").read_text(encoding="utf-8")
    assert "autorun" in text.lower()
    payload = json.loads(text)
    assert any("delete" in str(v).lower() for v in payload.values())


# --------------------------------------------------------------------------
# Gemini-family extension directories
# --------------------------------------------------------------------------


@pytest.mark.parametrize("platform_name", ["gemini", "qwen", "antigravity"])
def test_uninstall_removes_an_owned_extension_directory(isolated_home, platform_name):
    ext_root = platform_extensions_dir(PLATFORMS[platform_name])
    assert ext_root is not None
    ext = ext_root / "ar"
    ext.mkdir(parents=True)
    (ext / "gemini-extension.json").write_text('{"name": "ar"}', encoding="utf-8")
    write_owned_marker(ext, plugin="ar")

    uninstall_plugins("all")

    assert not ext.exists()


@pytest.mark.parametrize("platform_name", ["gemini", "qwen", "antigravity"])
def test_uninstall_leaves_an_unowned_extension_alone(isolated_home, platform_name):
    """Another extension in the same directory is not autorun's to delete."""
    ext_root = platform_extensions_dir(PLATFORMS[platform_name])
    assert ext_root is not None
    other = ext_root / "conductor"
    other.mkdir(parents=True)
    (other / "gemini-extension.json").write_text('{"name": "x"}', encoding="utf-8")

    uninstall_plugins("all")

    assert other.is_dir()


def test_uninstall_asks_the_harness_cli_first(isolated_home):
    """The CLI owns its own registry; deleting the directory behind its back
    leaves a dangling entry in `gemini extensions list`."""
    ext_root = platform_extensions_dir(PLATFORMS["gemini"])
    ext = ext_root / "ar"
    ext.mkdir(parents=True)
    write_owned_marker(ext, plugin="ar")

    uninstall_plugins("all")

    assert ["gemini", "extensions", "uninstall", "ar"] in isolated_home.commands


def test_partial_uninstall_keeps_another_plugins_extension(isolated_home):
    """The marker records the marketplace name `--uninstall` selects on.

    `plugins/autorun` registers as `ar`, so recording the directory name would
    match nothing and quietly leave both extensions in place.
    """
    ext_root = platform_extensions_dir(PLATFORMS["gemini"])
    mine = ext_root / "ar"
    mine.mkdir(parents=True)
    write_owned_marker(mine, plugin="ar")
    theirs = ext_root / "pdf"
    theirs.mkdir(parents=True)
    write_owned_marker(theirs, plugin="pdf-extractor")

    uninstall_plugins("ar")

    assert not mine.exists()
    assert theirs.is_dir()


def test_partial_uninstall_keeps_an_unattributed_artifact(isolated_home):
    """An older install recorded no plugin. Guessing would delete a keeper."""
    ext_root = platform_extensions_dir(PLATFORMS["gemini"])
    legacy = ext_root / "ar"
    legacy.mkdir(parents=True)
    write_owned_marker(legacy)

    uninstall_plugins("ar")

    assert legacy.is_dir()

    uninstall_plugins("all")

    assert not legacy.exists()


def test_the_registry_name_comes_from_the_plugin_manifest():
    """Pins the mapping the markers depend on, at the real plugin."""
    from autorun.install import _plugin_registry_name

    assert _plugin_registry_name(Path(__file__).resolve().parents[1]) == "ar"


def test_uninstall_survives_a_missing_harness_cli(isolated_home, monkeypatch):
    """`gemini` may be gone by the time autorun is uninstalled."""
    monkeypatch.setattr(
        "autorun.install.run_cmd",
        lambda *a, **k: CmdResult(False, "command not found"),
    )
    ext_root = platform_extensions_dir(PLATFORMS["gemini"])
    ext = ext_root / "ar"
    ext.mkdir(parents=True)
    write_owned_marker(ext, plugin="ar")

    assert uninstall_plugins("all") == 0
    assert not ext.exists(), "directory must be removed even when the CLI fails"


# --------------------------------------------------------------------------
# ForgeCode commands
# --------------------------------------------------------------------------


def test_uninstall_removes_only_the_forge_commands_autorun_copied(isolated_home):
    base = isolated_home / ".forge"
    cmds = base / "commands"
    cmds.mkdir(parents=True)
    (cmds / "ar-go.md").write_text("ours", encoding="utf-8")
    (cmds / "my-own.md").write_text("mine", encoding="utf-8")
    write_owned_marker(cmds, plugin="ar", files=("ar-go.md",))

    uninstall_plugins("all")

    assert not (cmds / "ar-go.md").exists()
    assert (cmds / "my-own.md").read_text(encoding="utf-8") == "mine"


def test_uninstall_removes_an_emptied_forge_commands_dir(isolated_home):
    base = isolated_home / ".forge"
    cmds = base / "commands"
    cmds.mkdir(parents=True)
    (cmds / "ar-go.md").write_text("ours", encoding="utf-8")
    write_owned_marker(cmds, plugin="ar", files=("ar-go.md",))

    uninstall_plugins("all")

    assert not cmds.exists(), "an empty autorun-created dir is litter"


def test_uninstall_leaves_an_unmarked_forge_commands_dir_alone(isolated_home):
    cmds = isolated_home / ".forge" / "commands"
    cmds.mkdir(parents=True)
    (cmds / "ar-go.md").write_text("user wrote this by hand", encoding="utf-8")

    uninstall_plugins("all")

    assert (cmds / "ar-go.md").is_file()


def test_forge_uninstall_follows_FORGE_CONFIG(isolated_home, monkeypatch):
    custom = isolated_home / "custom_forge"
    cmds = custom / "commands"
    cmds.mkdir(parents=True)
    (cmds / "ar-go.md").write_text("ours", encoding="utf-8")
    write_owned_marker(cmds, plugin="ar", files=("ar-go.md",))
    monkeypatch.setenv("FORGE_CONFIG", str(custom))

    uninstall_plugins("all")

    assert not (cmds / "ar-go.md").exists()


# --------------------------------------------------------------------------
# Bridged skills installed in copy mode
# --------------------------------------------------------------------------


def _make_agents_skill(home: Path, name: str) -> Path:
    skill = home / ".agents" / "skills" / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test\n---\n\nbody\n", encoding="utf-8"
    )
    return skill


def test_copy_mode_marks_what_it_wrote(isolated_home):
    _make_agents_skill(isolated_home, "shared-skill")
    bridge = bridge_agents_skills(PLATFORMS["claude"], mode="copy")
    assert bridge.linked == ("shared-skill",)

    copied = isolated_home / ".claude" / "skills" / "shared-skill"
    assert not copied.is_symlink()
    assert read_owned_marker(copied) is not None


def test_uninstall_removes_a_copy_mode_bridged_skill(isolated_home):
    """`--claude-agents-skills copy` was a one-way door before this."""
    source = _make_agents_skill(isolated_home, "shared-skill")
    bridge_agents_skills(PLATFORMS["claude"], mode="copy")
    copied = isolated_home / ".claude" / "skills" / "shared-skill"
    assert copied.is_dir()

    uninstall_plugins("all")

    assert not copied.exists()
    assert source.is_dir(), "the shared source must survive"


def test_uninstall_keeps_a_user_authored_skill_of_the_same_name(isolated_home):
    claude_skills = isolated_home / ".claude" / "skills"
    claude_skills.mkdir(parents=True)
    mine = claude_skills / "shared-skill"
    mine.mkdir()
    (mine / "SKILL.md").write_text("mine", encoding="utf-8")

    uninstall_plugins("all")

    assert (mine / "SKILL.md").read_text(encoding="utf-8") == "mine"


def test_copy_mode_never_claims_a_directory_it_did_not_create(isolated_home):
    """Bridging skips existing names, so no marker may appear on one."""
    _make_agents_skill(isolated_home, "shared-skill")
    claude_skills = isolated_home / ".claude" / "skills"
    claude_skills.mkdir(parents=True)
    mine = claude_skills / "shared-skill"
    mine.mkdir()
    (mine / "SKILL.md").write_text("mine", encoding="utf-8")

    bridge = bridge_agents_skills(PLATFORMS["claude"], mode="copy")

    assert bridge.skipped_existing == ("shared-skill",)
    assert read_owned_marker(mine) is None


def test_dry_run_copy_writes_nothing(isolated_home):
    _make_agents_skill(isolated_home, "shared-skill")
    bridge_agents_skills(PLATFORMS["claude"], mode="copy", dry_run=True)
    assert not (isolated_home / ".claude" / "skills" / "shared-skill").exists()


# --------------------------------------------------------------------------
# Codex personal marketplace and plugin source
# --------------------------------------------------------------------------


def _write_personal_marketplace(home: Path, plugins: list[dict]) -> Path:
    path = home / ".agents" / "plugins" / "marketplace.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"name": "personal", "plugins": plugins}, indent=2),
        encoding="utf-8",
    )
    return path


def test_uninstall_drops_the_codex_marketplace_entry(isolated_home):
    path = _write_personal_marketplace(
        isolated_home, [{"name": "autorun", "source": "./plugins/autorun"}]
    )

    uninstall_plugins("all")

    names = [p["name"] for p in json.loads(path.read_text(encoding="utf-8"))["plugins"]]
    assert "autorun" not in names


def test_uninstall_keeps_other_codex_marketplace_entries(isolated_home):
    path = _write_personal_marketplace(
        isolated_home,
        [
            {"name": "autorun", "source": "./plugins/autorun"},
            {"name": "someone-else", "source": "./plugins/other"},
        ],
    )

    uninstall_plugins("all")

    names = [p["name"] for p in json.loads(path.read_text(encoding="utf-8"))["plugins"]]
    assert names == ["someone-else"]


def test_uninstall_tolerates_a_corrupt_marketplace_file(isolated_home):
    path = isolated_home / ".agents" / "plugins" / "marketplace.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    assert uninstall_plugins("all") == 0
    assert path.read_text(encoding="utf-8") == "{not json"


def test_uninstall_removes_the_owned_codex_plugin_source(isolated_home):
    source = isolated_home / "plugins" / "autorun"
    source.mkdir(parents=True)
    write_owned_marker(source, plugin="ar")

    uninstall_plugins("all")

    assert not source.exists()


def test_uninstall_leaves_a_user_owned_codex_plugin_source(isolated_home):
    """A hand-maintained ~/plugins/autorun is the user's work, not ours."""
    source = isolated_home / "plugins" / "autorun"
    source.mkdir(parents=True)
    (source / "plugin.json").write_text('{"name": "autorun"}', encoding="utf-8")

    uninstall_plugins("all")

    assert (source / "plugin.json").is_file()


# --------------------------------------------------------------------------
# Locks and daemon
# --------------------------------------------------------------------------


def test_uninstall_removes_install_lock_files(isolated_home):
    skills = isolated_home / ".agents" / "skills"
    skills.mkdir(parents=True)
    lock = skills / ".autorun-install.lock"
    lock.write_text("", encoding="utf-8")

    uninstall_plugins("all")

    assert not lock.exists()


def test_uninstall_stops_the_daemon_rather_than_restarting_it(
    isolated_home, monkeypatch
):
    """Restarting would relaunch code that no longer exists on disk."""
    stopped: list[bool] = []
    monkeypatch.setattr(
        "autorun.install._stop_daemon_if_running", lambda: stopped.append(True)
    )
    restarted: list[bool] = []
    monkeypatch.setattr(
        "autorun.install._restart_daemon_if_running", lambda: restarted.append(True)
    )

    uninstall_plugins("all")

    assert stopped == [True]
    assert restarted == []


def test_partial_uninstall_leaves_the_daemon_running(isolated_home, monkeypatch):
    """The remaining plugins still need it."""
    stopped: list[bool] = []
    monkeypatch.setattr(
        "autorun.install._stop_daemon_if_running", lambda: stopped.append(True)
    )

    uninstall_plugins("autorun")

    assert stopped == []


# --------------------------------------------------------------------------
# Whole-tree invariant
# --------------------------------------------------------------------------


def test_uninstall_on_a_clean_machine_creates_nothing(isolated_home):
    """Teardown must not materialize the directories it is looking for."""
    before = {p for p in isolated_home.rglob("*")}

    assert uninstall_plugins("all") == 0

    assert {p for p in isolated_home.rglob("*")} == before


def test_uninstall_is_idempotent_across_every_surface(isolated_home):
    _make_agents_skill(isolated_home, "shared-skill")
    bridge_agents_skills(PLATFORMS["claude"], mode="copy")
    ext = platform_extensions_dir(PLATFORMS["gemini"]) / "ar"
    ext.mkdir(parents=True)
    write_owned_marker(ext, plugin="ar")
    _write_personal_marketplace(
        isolated_home, [{"name": "autorun", "source": "./plugins/autorun"}]
    )

    assert uninstall_plugins("all") == 0
    first = sorted(str(p.relative_to(isolated_home)) for p in isolated_home.rglob("*"))
    assert uninstall_plugins("all") == 0
    second = sorted(str(p.relative_to(isolated_home)) for p in isolated_home.rglob("*"))
    assert first == second


def test_uninstall_says_what_it_kept(isolated_home, capsys, monkeypatch):
    """Session history stays, but silently leaving a directory reads as a bug."""
    state = isolated_home / ".autorun"
    state.mkdir()
    (state / "session.db").write_text("history", encoding="utf-8")
    # AUTORUN_HOME is what actually locates the state dir; conftest points it
    # away from HOME so the suite cannot touch the live daemon.
    monkeypatch.setenv("AUTORUN_HOME", str(state))

    uninstall_plugins("all")

    out = capsys.readouterr().out
    assert str(state) in out
    assert "history" in out.lower()
    assert (state / "session.db").is_file()


def test_uninstall_says_nothing_when_there_is_no_state_to_keep(
    isolated_home, capsys, monkeypatch
):
    monkeypatch.setenv("AUTORUN_HOME", str(isolated_home / "absent"))

    uninstall_plugins("all")

    assert "Kept" not in capsys.readouterr().out
