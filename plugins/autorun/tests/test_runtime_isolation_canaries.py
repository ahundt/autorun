"""The test suite must never touch the live daemon's runtime artifacts.

Two guarantees are checked here, and they are deliberately checked in two
different ways because one of them cannot be proven from inside the suite it
protects.

  1. *Ordering*: the isolation variables have to be set before ``autorun`` is
     imported at all, because module import resolves paths. A test running
     inside an already-imported process cannot observe a violation that
     happened at import time, so that guarantee is checked by launching a
     fresh interpreter and inspecting what it resolves.

  2. *Containment*: every runtime path the loaded modules resolved must live
     under the temporary root. This one is checkable in-process and catches
     any module that recomputes a path later from a hard-coded home.

Adding a new persistent artifact — a database, a write-ahead log, a staging
file, a receipt, an archive — means adding it to the containment check. An
artifact that is not listed is not protected.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
SRC_DIR = PLUGIN_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

REQUIRED_ISOLATION_VARS = (
    "AUTORUN_HOME",
    "AUTORUN_TEST_STATE_DIR",
    "AUTORUN_TEST_RUNTIME_DIR",
)


def _runtime_root() -> Path:
    return Path(os.environ["AUTORUN_TEST_RUNTIME_DIR"]).resolve()


class TestIsolationVariables:
    @pytest.mark.parametrize("name", REQUIRED_ISOLATION_VARS)
    def test_the_variable_is_set_and_points_somewhere_real(self, name):
        value = os.environ.get(name)
        assert value, (
            f"{name} is unset. The suite would resolve this artifact under the "
            "real home directory and could overwrite live daemon state."
        )
        assert Path(value).is_dir(), f"{name}={value!r} is not a directory"

    def test_state_and_home_live_under_the_runtime_root(self):
        root = _runtime_root()
        for name in ("AUTORUN_HOME", "AUTORUN_TEST_STATE_DIR"):
            resolved = Path(os.environ[name]).resolve()
            assert root == resolved or root in resolved.parents, (
                f"{name}={resolved} escapes the temporary runtime root {root}, "
                "so suite cleanup would not remove it."
            )

    def test_the_runtime_root_is_not_the_users_home(self):
        root = _runtime_root()
        live_config_dir = (Path.home() / ".claude").resolve()
        assert root != live_config_dir and live_config_dir not in root.parents, (
            f"The runtime root {root} is inside the live configuration "
            f"directory {live_config_dir}, so the suite could overwrite real "
            "session state."
        )


class TestPathContainment:
    def test_the_resolved_state_directory_is_inside_the_runtime_root(self):
        from autorun.session_manager import get_session_manager

        state_dir = get_session_manager().state_dir.resolve()
        root = _runtime_root()
        assert root in state_dir.parents or root == state_dir, (
            f"Session state resolved to {state_dir}, outside {root}."
        )

    def test_no_state_artifact_was_created_outside_the_runtime_root(self, tmp_path):
        """Writing state must not produce files anywhere but the temp root."""
        from autorun.session_manager import get_session_manager, session_state

        state_dir = get_session_manager().state_dir.resolve()
        with session_state("isolation-canary") as state:
            state["written"] = True

        produced = [p for p in state_dir.iterdir() if p.is_file()]
        assert produced, "the write produced no artifact at all"
        root = _runtime_root()
        for path in produced:
            assert root in path.resolve().parents, (
                f"{path} was written outside the temporary runtime root."
            )


# ── Import ordering, checked from a separate interpreter ─────────────────────

_PROBE = textwrap.dedent(
    """
    import json, os, sys

    # Record what a fresh interpreter sees before autorun is imported at all.
    before = {name: os.environ.get(name) for name in %(names)r}

    sys.path.insert(0, %(src)r)
    import autorun
    from autorun.session_manager import get_session_manager

    print(json.dumps({
        "before": before,
        "state_dir": str(get_session_manager().state_dir.resolve()),
    }))
    """
)


def _run_probe(env: dict) -> dict:
    script = _PROBE % {"names": list(REQUIRED_ISOLATION_VARS), "src": str(SRC_DIR)}
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


@pytest.mark.subprocess
class TestImportOrdering:
    def test_a_child_inheriting_this_environment_resolves_state_inside_the_root(self):
        """The variables must already be exported, not applied by a fixture.

        Subprocesses spawned by tests inherit only the environment. If the
        suite relied on a fixture to redirect state after import, this child
        would resolve the live directory.
        """
        result = _run_probe(dict(os.environ))

        for name in REQUIRED_ISOLATION_VARS:
            assert result["before"][name], (
                f"{name} is not exported to child processes, so a subprocess "
                "spawned by a test would use live paths."
            )

        root = _runtime_root()
        assert root in Path(result["state_dir"]).parents, (
            f"A child process resolved state to {result['state_dir']}, "
            f"outside {root}."
        )

    def test_without_the_variables_a_child_resolves_the_live_directory(self):
        """Proves the check above tests something real.

        With the variables removed the same probe must fall back to the real
        location. If it did not, the containment assertion would pass even
        when isolation was broken.
        """
        env = {k: v for k, v in os.environ.items()
               if k not in REQUIRED_ISOLATION_VARS}
        result = _run_probe(env)

        resolved = Path(result["state_dir"])
        assert _runtime_root() not in resolved.parents, (
            "Removing the isolation variables changed nothing, so the "
            "containment check cannot detect a real leak."
        )
