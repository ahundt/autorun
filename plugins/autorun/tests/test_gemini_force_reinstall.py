"""`--install --force` must not leave a Gemini-family extension half-installed.

Reproduced on a real machine while running the release gate:

    $ autorun --install --force
       Installing autorun (name: ar)...
       ✗ ar installation failed:
         Status: False
    Extension integrity store cannot be verified. Please delete
    /Users/<user>/.gemini/extension_integrity.json to reset it.

`force` runs `gemini extensions uninstall <name>` before installing. That
removes `commands/` and `skills/` but leaves the extension *registered*, so
when the follow-up install fails the extension is still listed while missing
most of its contents — and `gemini extensions install` then refuses to repair
it, reporting "already installed". A working install had been destroyed and
could not be restored by re-running the installer.

Two things this pins:

- The integrity-store failure is recognized and reported with the file to
  delete, instead of being flattened into "Status: False". The CLI names the
  remedy; autorun was swallowing it.
- After a force-uninstall, a failed install is retried once without the
  force-uninstall, because the extension may already be back in a state where a
  plain install succeeds. Autorun never deletes the user's integrity store
  itself — that is their file, and the CLI tells them what to do with it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.install import (  # noqa: E402
    CmdResult,
    _gemini_integrity_store_failure,
    _install_gemini_family_extensions,
)

INTEGRITY_ERROR = (
    "Extension integrity store cannot be verified. Please delete "
    "/home/u/.gemini/extension_integrity.json to reset it."
)


def _marketplace(tmp_path: Path) -> Path:
    """Build a marketplace tree with one Gemini-capable plugin."""
    root = tmp_path / "marketplace"
    plugin_dir = root / "plugins" / "autorun"
    template = plugin_dir / "src" / "autorun" / "gemini_template"
    (template / "hooks").mkdir(parents=True)
    (template / "gemini-extension.json").write_text('{"name": "ar"}', encoding="utf-8")
    (template / "hooks" / "hooks.json").write_text('{"hooks": {}}', encoding="utf-8")
    (plugin_dir / "hooks").mkdir()
    (plugin_dir / "hooks" / "hook_entry.py").write_text("# hook\n", encoding="utf-8")
    (plugin_dir / ".claude-plugin").mkdir()
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "ar"}', encoding="utf-8"
    )
    meta = root / ".claude-plugin"
    meta.mkdir()
    (meta / "marketplace.json").write_text(
        '{"plugins": [{"name": "ar", "source": "./plugins/autorun"}]}', encoding="utf-8"
    )
    return root


@pytest.fixture
def gemini_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".gemini" / "extensions" / "ar").mkdir(parents=True)
    monkeypatch.setattr(
        "autorun.install.shutil.which", lambda name: f"/usr/bin/{name}"
    )
    return tmp_path


def test_integrity_store_failure_is_recognized():
    assert _gemini_integrity_store_failure(INTEGRITY_ERROR) is True
    assert _gemini_integrity_store_failure("some other error") is False
    assert _gemini_integrity_store_failure("") is False


def test_integrity_store_failure_names_the_file_to_delete(
    gemini_home, tmp_path, capsys, monkeypatch
):
    """The CLI states the remedy; autorun must not swallow it."""
    def _run(cmd, *_a, **_k):
        if cmd[:3] == ["gemini", "extensions", "install"]:
            return CmdResult(False, INTEGRITY_ERROR)
        return CmdResult(True, "ok")

    monkeypatch.setattr("autorun.install.run_cmd", _run)

    _install_gemini_family_extensions(
        marketplace_root=_marketplace(tmp_path),
        plugins=["ar"],
        force=True,
        cli_name="gemini",
        display_name="Legacy Gemini CLI",
        config_dir=gemini_home / ".gemini",
        install_hint="npm i -g gemini",
    )

    out = capsys.readouterr().out
    assert "extension_integrity.json" in out
    assert "gemini extensions uninstall ar" in out


def test_a_failed_forced_install_is_retried_once_without_the_uninstall(
    gemini_home, tmp_path, monkeypatch
):
    """The uninstall already happened; a second uninstall cannot help, but the
    extension may now install cleanly."""
    attempts: list[list[str]] = []

    def _run(cmd, *_a, **_k):
        attempts.append(list(cmd))
        if cmd[:3] == ["gemini", "extensions", "install"]:
            installs = [c for c in attempts if c[:3] == ["gemini", "extensions", "install"]]
            if len(installs) == 1:
                return CmdResult(False, INTEGRITY_ERROR)
            return CmdResult(True, "installed")
        return CmdResult(True, "ok")

    monkeypatch.setattr("autorun.install.run_cmd", _run)

    ok, _msg = _install_gemini_family_extensions(
        marketplace_root=_marketplace(tmp_path),
        plugins=["ar"],
        force=True,
        cli_name="gemini",
        display_name="Legacy Gemini CLI",
        config_dir=gemini_home / ".gemini",
        install_hint="npm i -g gemini",
    )

    installs = [c for c in attempts if c[:3] == ["gemini", "extensions", "install"]]
    uninstalls = [c for c in attempts if c[:3] == ["gemini", "extensions", "uninstall"]]
    assert len(installs) == 2, "the failed install must be retried once"
    assert len(uninstalls) == 1, "the retry must not uninstall again"
    assert ok is True


def test_a_non_forced_install_is_not_retried(gemini_home, tmp_path, monkeypatch):
    """Nothing was destroyed, so a retry would only repeat the same failure."""
    attempts: list[list[str]] = []

    def _run(cmd, *_a, **_k):
        attempts.append(list(cmd))
        if cmd[:3] == ["gemini", "extensions", "install"]:
            return CmdResult(False, "network unreachable")
        return CmdResult(True, "ok")

    monkeypatch.setattr("autorun.install.run_cmd", _run)

    _install_gemini_family_extensions(
        marketplace_root=_marketplace(tmp_path),
        plugins=["ar"],
        force=False,
        cli_name="gemini",
        display_name="Legacy Gemini CLI",
        config_dir=gemini_home / ".gemini",
        install_hint="npm i -g gemini",
    )

    installs = [c for c in attempts if c[:3] == ["gemini", "extensions", "install"]]
    assert len(installs) == 1


def test_autorun_never_deletes_the_users_integrity_store(
    gemini_home, tmp_path, monkeypatch
):
    """It is Gemini's file and the CLI tells the user what to do with it."""
    integrity = gemini_home / ".gemini" / "extension_integrity.json"
    integrity.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "autorun.install.run_cmd",
        lambda cmd, *a, **k: CmdResult(False, INTEGRITY_ERROR)
        if cmd[:3] == ["gemini", "extensions", "install"]
        else CmdResult(True, "ok"),
    )

    _install_gemini_family_extensions(
        marketplace_root=_marketplace(tmp_path),
        plugins=["ar"],
        force=True,
        cli_name="gemini",
        display_name="Legacy Gemini CLI",
        config_dir=gemini_home / ".gemini",
        install_hint="npm i -g gemini",
    )

    assert integrity.is_file()


def test_a_half_installed_extension_is_reported_not_called_success(
    gemini_home, tmp_path, monkeypatch, capsys
):
    """The state force left behind: registered, but missing its contents."""
    monkeypatch.setattr(
        "autorun.install.run_cmd",
        lambda cmd, *a, **k: CmdResult(False, "Extension \"ar\" is already installed.")
        if cmd[:3] == ["gemini", "extensions", "install"]
        else CmdResult(True, "ok"),
    )

    ok, msg = _install_gemini_family_extensions(
        marketplace_root=_marketplace(tmp_path),
        plugins=["ar"],
        force=True,
        cli_name="gemini",
        display_name="Legacy Gemini CLI",
        config_dir=gemini_home / ".gemini",
        install_hint="npm i -g gemini",
    )

    # "already installed" after a force-uninstall means the install did NOT
    # happen — the extension is stuck in the half-removed state, so reporting
    # success here is what hid the defect.
    assert ok is False
    assert "ar" in msg
