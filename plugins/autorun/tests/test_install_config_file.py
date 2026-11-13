"""Install settings read ``autorun.config.json``, including on unattended reinstalls.

The self-update re-registration and the hook bootstrap both run
``autorun --install --force`` with no other flags, so the file is the only tier
that carries a user's install choices across updates. These tests write a
real file under a redirected ``AUTORUN_HOME`` and resolve through the same
entry points the installer uses.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from autorun.config import USER_CONFIG_FILENAME
from autorun.installer import settings


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    home = tmp_path / "autorun-home"
    home.mkdir()
    monkeypatch.setenv("AUTORUN_HOME", str(home))
    for declaration in settings.INSTALL_SETTINGS:
        for name in (declaration.env, *declaration.aliases):
            monkeypatch.delenv(name, raising=False)
    return home


def _write(home: Path, document: object) -> None:
    (home / USER_CONFIG_FILENAME).write_text(json.dumps(document), encoding="utf-8")


def test_every_install_setting_resolves_from_the_file(config_home):
    _write(config_home, {
        "skill_placement": {"codex": "native"},
        "shared_skills_bridge": "copy",
        "codex_hook_source": "plugin",
        "codex_plugin_marketplace": "github",
        "hook_no_sync": False,
        "hook_python": "/opt/python3.13",
        "write_source_metadata": True,
        "cache_install_lock_timeout_seconds": 45,
        "tool": True,
        "conductor": False,
        "update_method": "uv",
        "custom_harness": ["mine=codex:codex:~/.codex-mine"],
    })
    resolved = {s.name: s.resolve() for s in settings.INSTALL_SETTINGS}
    assert {name: r.source for name, r in resolved.items()} == dict.fromkeys(resolved, "config")
    assert resolved["skill_placement"].value == {"codex": "native"}
    assert resolved["hook_no_sync"].value is False
    assert resolved["custom_harness"].value[0].config_dir == "~/.codex-mine"


def test_the_flagless_reinstall_keeps_the_files_choices(config_home, tmp_path):
    """What `--update` and the hook bootstrap run: a fresh process, no flags.

    A subprocess, because CONFIG reads the file once at import: an in-process
    check would see this test's CONFIG, not the one a reinstall would load.
    ``custom_harnesses`` is the one install key CONFIG declares, so it reaches
    the installer through the runtime overlay; the others through the file view.
    """
    _write(config_home, {
        "codex_hook_source": "both",
        "skill_placement": {"codex": "native"},
        "custom_harnesses": ["work=codex:codex:~/.codex-work"],
    })
    probe = (
        "import json; from autorun.installer import entrypoint; "
        "resolved, custom = entrypoint._resolved_settings(); "
        "print(json.dumps([resolved['codex_hook_source'], resolved['skill_placement'], "
        "[c.name for c in custom]]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=tmp_path, capture_output=True, text=True,
        env={**os.environ, "AUTORUN_HOME": str(config_home)}, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["both", {"codex": "native"}, ["work"]]


def test_env_and_flag_still_outrank_the_file(config_home, monkeypatch):
    _write(config_home, {"codex_hook_source": "plugin"})
    monkeypatch.setenv("AUTORUN_CODEX_HOOK_SOURCE", "both")
    assert settings.CODEX_HOOK_SOURCE.resolve().value == "both"
    assert settings.CODEX_HOOK_SOURCE.resolve("none").value == "none"


def test_an_invalid_file_value_falls_through_to_the_default(config_home):
    _write(config_home, {"codex_hook_source": "everywhere", "hook_no_sync": "garbage"})
    assert settings.CODEX_HOOK_SOURCE.resolve() == settings.Resolved("user", "default")
    assert settings.HOOK_NO_SYNC.resolve() == settings.Resolved(True, "default")


@pytest.mark.parametrize("state", ["no file", "no autorun home", "not json", "a list"])
def test_before_first_install_or_with_a_broken_file_defaults_apply(config_home, state):
    if state == "no autorun home":
        config_home.rmdir()
    elif state == "not json":
        (config_home / USER_CONFIG_FILENAME).write_text("{oops", encoding="utf-8")
    elif state == "a list":
        _write(config_home, ["codex_hook_source"])
    assert {s.resolve().source for s in settings.INSTALL_SETTINGS} == {"default"}


def test_an_explicit_config_mapping_never_reads_the_file(config_home):
    """Tests pass `config=` to isolate; that must stay a closed world."""
    _write(config_home, {"codex_hook_source": "plugin"})
    assert settings.CODEX_HOOK_SOURCE.resolve(env={}, config={}).source == "default"


def test_the_file_reaches_install_settings_without_changing_runtime_config(config_home):
    """Runtime CONFIG still declines keys it does not declare."""
    from autorun.config import apply_user_config, default_config

    _write(config_home, {"codex_hook_source": "plugin"})
    assert "codex_hook_source" not in apply_user_config(default_config())
