"""Coverage for two install paths that ran by default with no tests at all.

`_install_conductor` installs a third-party Gemini extension on every install
where Gemini is a target and `conductor` is requested — a network operation
against a GitHub URL, with nothing pinning its behavior.

`_copy_tree` is the primitive behind every Gemini/Qwen/Antigravity resource
sync. It deletes the destination before copying, which is correct for a
directory autorun owns outright and catastrophic for one it shares. The
ForgeCode installer made exactly that mistake with `shutil.copy2` and destroyed
user-authored `AGENTS.md` files until `test_forgecode_install.py` pinned it.
These tests state which destinations `_copy_tree` is allowed to be pointed at,
so the same defect cannot reappear through the other harnesses.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.install import (  # noqa: E402
    CmdResult,
    _copy_tree,
    _install_conductor,
    _sync_gemini_extension_resources,
)

REAL_PLUGIN_DIR = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# _install_conductor
# --------------------------------------------------------------------------


@pytest.fixture
def gemini_available(monkeypatch):
    """Pretend `gemini` is on PATH and record what gets run."""
    monkeypatch.setattr("autorun.install.shutil.which", lambda name: f"/usr/bin/{name}")
    calls: list[list[str]] = []

    def _record(cmd, *_a, **_k):
        calls.append(list(cmd))
        return CmdResult(True, "installed")

    monkeypatch.setattr("autorun.install.run_cmd", _record)
    return calls


def test_conductor_is_skipped_when_gemini_is_absent(monkeypatch):
    """It must not claim success on a machine that cannot run it."""
    monkeypatch.setattr("autorun.install.shutil.which", lambda _name: None)
    ok, msg = _install_conductor()
    assert ok is False
    assert "gemini" in msg.lower()


def test_conductor_installs_from_the_upstream_repository(gemini_available):
    ok, _msg = _install_conductor()
    assert ok
    install = [c for c in gemini_available if "install" in c]
    assert install, "no install command was run"
    assert any("gemini-cli-extensions/conductor" in part for part in install[0])


def test_conductor_consents_and_enables_auto_update(gemini_available):
    """Both flags are load-bearing: without --consent the install prompts and
    hangs a non-interactive installer."""
    _install_conductor()
    install = next(c for c in gemini_available if "install" in c)
    assert "--consent" in install
    assert "--auto-update" in install


def test_conductor_force_uninstalls_before_reinstalling(gemini_available):
    _install_conductor(force=True)
    assert ["gemini", "extensions", "uninstall", "conductor"] == gemini_available[0]


def test_conductor_without_force_does_not_uninstall(gemini_available):
    _install_conductor(force=False)
    assert not any("uninstall" in c for c in gemini_available)


def test_conductor_treats_already_installed_as_success(monkeypatch):
    monkeypatch.setattr("autorun.install.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        "autorun.install.run_cmd",
        lambda *a, **k: CmdResult(False, "extension already installed"),
    )
    ok, _msg = _install_conductor()
    assert ok is True


def test_conductor_reports_a_real_failure(monkeypatch):
    monkeypatch.setattr("autorun.install.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        "autorun.install.run_cmd",
        lambda *a, **k: CmdResult(False, "network unreachable"),
    )
    ok, msg = _install_conductor()
    assert ok is False
    assert "network unreachable" in msg


# --------------------------------------------------------------------------
# _copy_tree
# --------------------------------------------------------------------------


def _tree(root: Path, name: str, files: dict[str, str]) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    for relative, text in files.items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return directory


def test_copy_tree_reports_a_missing_source_instead_of_creating_one(tmp_path):
    assert _copy_tree(tmp_path / "absent", tmp_path / "dst") is False
    assert not (tmp_path / "dst").exists()


def test_copy_tree_mirrors_content(tmp_path):
    src = _tree(tmp_path, "src", {"a.md": "one", "nested/b.md": "two"})
    dst = tmp_path / "dst"

    assert _copy_tree(src, dst) is True
    assert (dst / "a.md").read_text(encoding="utf-8") == "one"
    assert (dst / "nested" / "b.md").read_text(encoding="utf-8") == "two"


def test_copy_tree_replaces_rather_than_merges(tmp_path):
    """This is the whole hazard: a file the previous version shipped and the
    current one dropped must not linger, so the destination is cleared. It
    follows that `_copy_tree` may only ever target a directory autorun owns
    outright — never one shared with the user."""
    src = _tree(tmp_path, "src", {"current.md": "new"})
    dst = _tree(tmp_path, "dst", {"removed-upstream.md": "stale"})

    _copy_tree(src, dst)

    assert (dst / "current.md").is_file()
    assert not (dst / "removed-upstream.md").exists()


def test_copy_tree_skips_build_artifacts(tmp_path):
    src = _tree(
        tmp_path,
        "src",
        {
            "keep.md": "keep",
            "__pycache__/x.pyc": "junk",
            "scratch.tmp": "junk",
            "backup.bak": "junk",
            "editor~": "junk",
        },
    )
    dst = tmp_path / "dst"

    _copy_tree(src, dst)

    assert (dst / "keep.md").is_file()
    assert not (dst / "__pycache__").exists()
    for junk in ("scratch.tmp", "backup.bak", "editor~"):
        assert not (dst / junk).exists()


def test_copy_tree_replaces_a_symlink_at_the_destination(tmp_path):
    """A leftover link from an older layout must not redirect the copy into
    whatever it points at."""
    src = _tree(tmp_path, "src", {"a.md": "one"})
    elsewhere = _tree(tmp_path, "elsewhere", {"theirs.md": "not ours"})
    dst = tmp_path / "dst"
    dst.symlink_to(elsewhere)

    _copy_tree(src, dst)

    assert not dst.is_symlink()
    assert (dst / "a.md").read_text(encoding="utf-8") == "one"
    assert (elsewhere / "theirs.md").is_file(), "the link target must survive"


def test_copy_tree_replaces_a_file_at_the_destination(tmp_path):
    src = _tree(tmp_path, "src", {"a.md": "one"})
    dst = tmp_path / "dst"
    dst.write_text("was a file", encoding="utf-8")

    _copy_tree(src, dst)

    assert dst.is_dir()


def test_gemini_resource_sync_only_writes_autorun_owned_subdirectories(tmp_path):
    """The destinations `_copy_tree` is pointed at during a Gemini-family sync.

    `hooks/`, `commands/` and `skills/` inside an installed extension are
    autorun's; the extension root is not, so nothing here may clear it. A future
    change that pointed `_copy_tree` at the root — or at a harness config dir —
    would destroy whatever else lives there, which is the ForgeCode `AGENTS.md`
    defect in a different location.
    """
    ext_dir = tmp_path / "extensions" / "ar"
    ext_dir.mkdir(parents=True)
    sibling = ext_dir / "user-notes.md"
    sibling.write_text("the user put this here", encoding="utf-8")

    _sync_gemini_extension_resources(REAL_PLUGIN_DIR, ext_dir, "ar", "gemini")

    assert sibling.read_text(encoding="utf-8") == "the user put this here"
    assert (ext_dir / "commands").is_dir()
    assert (ext_dir / "skills").is_dir()
