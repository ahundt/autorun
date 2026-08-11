#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright 2025 Andrew Hundt <ATHundt@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""The live-install canary must actually fire.

conftest.py fingerprints autorun's installed copy at session start and reports
any change at session finish. A canary nobody has seen fail is indistinguishable
from a canary that silently stopped watching, and it reads as coverage while
providing none -- so every branch of it is exercised here against a fake home.

What it guards: AUTORUN_HOME and AUTORUN_TEST_STATE_DIR redirect state, but
nothing redirects the installed plugin, the harness settings pointing at it, or
the shared marketplace registry. A test that shells out to an installer edits
the user's working install and the suite still passes.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

CONFTEST = Path(__file__).resolve().parent / "conftest.py"


def _load_canary():
    """Load the canary helpers from conftest.py without importing the package."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("autorun_conftest_canary", CONFTEST)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


# --- the fingerprint notices each way an artifact can change -----------------


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """A home containing one installed artifact, with HOME and USERPROFILE moved.

    Path.home() reads USERPROFILE on Windows and HOME elsewhere and never
    consults the other, so moving one name relocates the home on one platform
    only -- the test would then read a sandbox and touch a real home.
    """
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(tmp_path))
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"hooks": {}}), encoding="utf-8")
    return tmp_path, settings


def test_an_unchanged_install_produces_no_difference(fake_home):
    canary = _load_canary()
    before = canary._live_install_fingerprint()
    assert before == canary._live_install_fingerprint()


def test_an_edited_artifact_is_detected(fake_home):
    _, settings = fake_home
    canary = _load_canary()

    before = canary._live_install_fingerprint()
    settings.write_text(json.dumps({"hooks": {"PreToolUse": []}}), encoding="utf-8")
    after = canary._live_install_fingerprint()

    assert before != after, "editing an installed artifact went unnoticed"
    assert str(settings) in {p for p in set(before) | set(after) if before.get(p) != after.get(p)}


def test_a_deleted_artifact_is_detected(fake_home):
    """The failure that started this: an installed file disappearing."""
    _, settings = fake_home
    canary = _load_canary()

    before = canary._live_install_fingerprint()
    settings.unlink()
    after = canary._live_install_fingerprint()

    assert before != after, (
        "an installed artifact was deleted and the fingerprint did not change, "
        "which is the exact shape of the failure this canary exists for"
    )


def test_deleted_cached_runtime_package_is_detected(fake_home):
    """Catch the incident shape: the venv survived but its packages vanished."""
    home, _ = fake_home
    package = (
        home
        / ".claude/plugins/cache/autorun/ar/test-version/.venv/lib/python3.13/site-packages/autorun/__init__.py"
    )
    package.parent.mkdir(parents=True)
    package.write_text("", encoding="utf-8")
    canary = _load_canary()

    before = canary._live_install_fingerprint()
    package.unlink()
    after = canary._live_install_fingerprint()

    assert before != after, "cached runtime package deletion went unnoticed"


def test_a_created_artifact_is_detected(tmp_path, monkeypatch):
    """A home with no install must still notice one appearing mid-run."""
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(tmp_path))
    canary = _load_canary()

    before = canary._live_install_fingerprint()
    created = tmp_path / ".claude" / "settings.json"
    created.parent.mkdir(parents=True)
    created.write_text("{}", encoding="utf-8")
    after = canary._live_install_fingerprint()

    assert before != after, "an install appearing during the run went unnoticed"


def test_a_same_size_rewrite_is_detected(fake_home):
    """Size alone is not enough; the mtime carries an in-place edit."""
    _, settings = fake_home
    canary = _load_canary()
    original = settings.read_text(encoding="utf-8")

    before = canary._live_install_fingerprint()
    replacement = original.replace("hooks", "hookz")
    assert len(replacement) == len(original), "test needs a same-length rewrite"
    settings.write_text(replacement, encoding="utf-8")
    after = canary._live_install_fingerprint()

    assert before != after, "a same-size in-place edit went unnoticed"


# --- the canary is scoped to the selection it can speak for ------------------


class _Config:
    def __init__(self, markers):
        self._markers = markers

    def getoption(self, name, default=None):
        return self._markers if name == "-m" else default


@pytest.mark.parametrize(
    ("markers", "enabled"),
    [
        ("not tmux and not e2e and not release", True),
        ("e2e", True),
        ("release", True),
        ("", True),
    ],
)
def test_the_canary_runs_for_every_selection(
    markers, enabled, monkeypatch
):
    canary = _load_canary()
    monkeypatch.delenv("AUTORUN_ALLOW_LIVE_INSTALL_WRITES", raising=False)
    assert canary._live_install_canary_enabled(_Config(markers)) is enabled


def test_a_deliberate_run_can_opt_out(monkeypatch):
    canary = _load_canary()
    monkeypatch.setenv("AUTORUN_ALLOW_LIVE_INSTALL_WRITES", "1")
    config = _Config("not tmux and not e2e and not release")
    assert canary._live_install_canary_enabled(config) is False


# --- end to end: a test that writes to the install fails the run -------------


@pytest.mark.timeout(120)
def test_a_suite_that_edits_the_install_exits_non_zero(tmp_path):
    """The whole point, exercised through a real pytest run in a fake home.

    Asserting on the helpers alone would keep passing if the fingerprint were
    never wired into pytest_sessionfinish, or if the exit status were not set.
    """
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{}", encoding="utf-8")

    suite = tmp_path / "suite"
    suite.mkdir()
    # Only the canary is copied. Importing the real conftest would pull in the
    # package and its daemon management, and the point here is to exercise the
    # canary's own wiring, not autorun's.
    (suite / "conftest.py").write_text(
        "import os\n\n"
        '_LIVE_INSTALL_GLOBS = ("~/.claude/settings.json",)\n\n\n'
        + _canary_source(),
        encoding="utf-8",
    )
    (suite / "test_writes.py").write_text(
        textwrap.dedent(
            """
            import json, os
            from pathlib import Path

            def test_touches_the_installed_settings():
                target = Path(os.path.expanduser("~/.claude/settings.json"))
                target.write_text(json.dumps({"hooks": {"PreToolUse": []}}))
                assert target.exists()
            """
        ),
        encoding="utf-8",
    )

    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop("AUTORUN_ALLOW_LIVE_INSTALL_WRITES", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(suite), "-q", "-p", "no:cacheprovider",
         "-m", "not tmux and not e2e and not release"],
        capture_output=True, text=True, env=env, cwd=str(suite), timeout=110,
    )

    assert "LIVE INSTALL MODIFIED" in result.stdout, (
        "a test wrote to the installed settings and the canary said nothing:\n"
        f"{result.stdout[-2000:]}"
    )
    assert result.returncode != 0, (
        "the canary reported the write but the run still exited 0, so CI would "
        f"treat it as a pass:\n{result.stdout[-2000:]}"
    )


def _canary_source() -> str:
    """The canary helpers lifted from conftest.py, plus minimal pytest hooks.

    Copied rather than imported so the temp suite exercises the shipped code:
    a hand-written duplicate would keep passing after conftest.py drifted, which
    is the failure mode this whole file exists to rule out.

    The two slices deliberately skip conftest's own pytest_sessionstart, which
    calls DaemonManager and would NameError here. Slicing straight through to
    pytest_sessionfinish would drag it in, and it only stayed harmless because
    the hook appended below happens to shadow it.
    """
    source = CONFTEST.read_text(encoding="utf-8")

    def between(start_marker: str, end_marker: str) -> str:
        start = source.index(start_marker)
        return source[start : source.index(end_marker, start)]

    body = (
        between("def _live_install_fingerprint(", "def pytest_sessionstart(")
        + between("def _check_live_install_unchanged(", "def pytest_sessionfinish(")
    )
    return body + textwrap.dedent(
        """
        def pytest_sessionstart(session):
            if _live_install_canary_enabled(session.config):
                session.config._autorun_live_install = _live_install_fingerprint()


        def pytest_sessionfinish(session, exitstatus):
            _check_live_install_unchanged(session)
        """
    )
