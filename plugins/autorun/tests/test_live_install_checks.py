"""The opt-in live-install checks must look at the live install.

``plugins/autorun/conftest.py`` points ``HOME`` at a scratch directory. The
checks used to read ``Path.home()``, so with
``AUTORUN_ENABLE_LIVE_INSTALL_CHECKS=1`` on a fully installed machine the
documented command reported "6 skipped": a gate that passed by checking
nothing. The suite's installed-copy canary had the same fault.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from e2e_support import (
    LIVE_INSTALL_CHECKS_ENV,
    REAL_HOME_ENV,
    live_install_env,
    live_install_missing,
    real_home,
)

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CACHE_CHECK = "tests/test_hook_entry.py::TestAllLocationsSync::test_cache_matches_source_hook_entry"


def test_the_real_home_is_not_the_scratch_home_the_suite_runs_in():
    assert real_home() != Path.home()
    assert str(real_home()) == os.environ[REAL_HOME_ENV]


def test_the_installed_copy_canary_fingerprints_the_real_home(tmp_path, monkeypatch):
    import conftest

    cached = tmp_path / ".claude/plugins/cache/autorun/ar/9.9.9/hooks/hook_entry.py"
    cached.parent.mkdir(parents=True)
    cached.write_text("installed\n", encoding="utf-8")
    monkeypatch.setenv(REAL_HOME_ENV, str(tmp_path))

    before = conftest._live_install_fingerprint()
    cached.write_text("modified\n", encoding="utf-8")
    after = conftest._live_install_fingerprint()

    assert os.path.normpath(str(cached)) in before
    assert before != after


def test_a_missing_install_fails_when_the_checks_are_enabled_and_skips_otherwise(monkeypatch):
    monkeypatch.setenv(LIVE_INSTALL_CHECKS_ENV, "1")
    with pytest.raises(pytest.fail.Exception, match="autorun --install"):
        live_install_missing("The Claude plugin cache", "autorun --install")
    assert live_install_env()["HOME"] == str(real_home())

    monkeypatch.delenv(LIVE_INSTALL_CHECKS_ENV)
    with pytest.raises(pytest.skip.Exception):
        live_install_missing("The Claude plugin cache", "autorun --install")
    assert live_install_env() is None


def _run_cache_check(home: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, LIVE_INSTALL_CHECKS_ENV: "1", REAL_HOME_ENV: str(home)}
    return subprocess.run(
        [sys.executable, "-m", "pytest", CACHE_CHECK, "-q", "-p", "no:cacheprovider", "-n", "0"],
        cwd=PLUGIN_ROOT, env=env, capture_output=True, text=True, timeout=180,
    )


def test_the_enabled_cache_check_runs_against_the_real_home_instead_of_skipping(tmp_path):
    """Through pytest itself: the command users run, not only the helper."""
    import json

    version = json.loads(
        (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )["version"]
    installed = tmp_path / "installed"
    hooks = installed / ".claude/plugins/cache/autorun/ar" / version / "hooks"
    hooks.mkdir(parents=True)
    shutil.copy2(PLUGIN_ROOT / "hooks" / "hook_entry.py", hooks / "hook_entry.py")

    current = _run_cache_check(installed)
    assert "1 passed" in current.stdout and "skipped" not in current.stdout, current.stdout

    (hooks / "hook_entry.py").write_text("stale\n", encoding="utf-8")
    stale = _run_cache_check(installed)
    assert "1 failed" in stale.stdout and "doesn't match source" in stale.stdout, stale.stdout

    empty = tmp_path / "empty"
    empty.mkdir()
    missing = _run_cache_check(empty)
    assert "1 failed" in missing.stdout and "is not installed under" in missing.stdout, missing.stdout
