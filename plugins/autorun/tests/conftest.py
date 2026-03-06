# -*- coding: utf-8 -*-
# conftest.py — pytest automatically discovers and loads this file before running
# any tests in this directory. This is a standard pytest convention:
# https://docs.pytest.org/en/stable/reference/fixtures.html#conftest-py-sharing-fixtures-across-files
#
# This file (plugins/autorun/tests/conftest.py) provides:
#   - Custom pytest markers (slow, stress, race, daemon, e2e, serial)
#   - Serial/parallel test assignment based on file name
#   - DaemonManager: protects production daemon PIDs; manages test-spawned daemons
#   - Shared fixtures: unique_session_id, temp_session_dir, ensure_single_daemon, etc.
#   - pytest_sessionstart / pytest_sessionfinish hooks for cleanup
#
# The parent conftest.py (plugins/autorun/conftest.py) runs first and provides
# a Python 3.10+ version guard via src/autorun/python_check.py.
"""
pytest configuration and fixtures for autorun testing

Environment Variables:
    AUTORUN_KEEP_TEST_ARTIFACTS: Set to 'true', '1', or 'yes' to keep test artifacts
                                   for debugging instead of cleaning them up.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import psutil

import pytest


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "slow: marks tests as slow")
    config.addinivalue_line("markers", "stress: marks tests as stress tests")
    config.addinivalue_line("markers", "race: marks tests as race condition tests")
    config.addinivalue_line("markers", "daemon: marks tests that require a running daemon")
    config.addinivalue_line("markers", "e2e: marks end-to-end tests")
    config.addinivalue_line("markers", "serial: marks tests that must run serially")



# Test file groups for automatic serial/parallel assignment
_SERIAL_SHELVE_TESTS = {
    "test_database_functionality", "test_stale_lock_recovery",
    "test_same_session_multi_process", "test_race_condition_fix",
    "test_command_blocking_comprehensive", "test_command_blocking",
    "test_policy_enforcement_matrix", "test_three_stage_completion",
    "test_task_lifecycle_integration", "test_task_lifecycle_failure_modes",
    "test_task_lifecycle_edge_cases", "test_task_lifecycle_ghost_task_bug",
    "test_thread_safety_simple", "test_e2e_policy_lifecycle",
    "test_session_lifecycle_edge_cases",
}

_SERIAL_DAEMON_TESTS = {
    "test_hook_entry", "test_dual_platform_hooks_install",
    "test_session_persistence_hooks", "test_gemini_e2e_improved",
    "test_gemini_e2e_real_money", "test_gemini_before_tool_hooks",
    "test_task_cli_commands", "test_demo",
}

_SERIAL_TMUX_TESTS = {
    "test_tmux_injector", "test_tmux_workflows_integration",
    "test_tmux_automation_agents", "test_tmux_compliance",
    "test_tmux_utils_enhanced", "test_session_targeting_diagnostic",
    "test_session_targeting_regression", "test_bang_syntax",
    "test_injection_monitoring", "test_injection_integration",
    "test_session_start_handler", "test_edge_cases_comprehensive",
}


def pytest_collection_modifyitems(config, items):
    """Auto-assign serial/parallel markers based on test file dependencies."""
    for item in items:
        # Extract test file stem from nodeid
        parts = item.nodeid.split("::")
        if not parts:
            continue
        file_stem = parts[0].rsplit("/", 1)[-1].replace(".py", "")

        if file_stem in _SERIAL_SHELVE_TESTS:
            item.add_marker(pytest.mark.serial)
        elif file_stem in _SERIAL_DAEMON_TESTS:
            item.add_marker(pytest.mark.daemon)
            item.add_marker(pytest.mark.serial)
        elif file_stem in _SERIAL_TMUX_TESTS:
            item.add_marker(pytest.mark.serial)


# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from autorun import CONFIG


# =============================================================================
# DAEMON LIFECYCLE MANAGEMENT (DRY — all daemon ops consolidated here)
# =============================================================================

class DaemonManager:
    """Centralized daemon lifecycle management for tests.

    Uses psutil for cross-platform process discovery and termination
    (works on Linux, macOS, and Windows — replaces pgrep/kill).

    Tracks production daemon PIDs (recorded before tests) so tests never kill
    daemons belonging to real coding sessions.

    Usage:
        # In pytest_sessionstart: DaemonManager.snapshot_production_pids()
        # In fixture:             DaemonManager.kill_test_daemons()
        # In pytest_sessionfinish: DaemonManager.cleanup()
    """

    # PIDs that existed before the test suite started — never killed
    _production_pids: set = set()

    # PIDs spawned by tests — killed on cleanup
    _test_spawned_pids: set = set()

    @classmethod
    def _get_all_daemon_pids(cls) -> list:
        """Get all autorun daemon PIDs currently running.

        Uses psutil.process_iter() for cross-platform process discovery
        (replaces Unix-only pgrep -f autorun.daemon).
        Skipped on Windows: daemon uses Unix sockets (AF_UNIX), unavailable on Windows.
        """
        if sys.platform == "win32":
            return []
        pids = []
        for proc in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline') or []
                cmdline_str = ' '.join(cmdline)
                if 'autorun.daemon' in cmdline_str:
                    pids.append(str(proc.info['pid']))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        return pids

    @classmethod
    def snapshot_production_pids(cls):
        """Record daemon PIDs that exist before tests start.

        Called once in pytest_sessionstart. These PIDs are protected from
        all test cleanup operations.
        """
        cls._production_pids = set(cls._get_all_daemon_pids())
        if cls._production_pids and os.getenv("DEBUG", "").lower() in {"true", "1", "yes"}:
            print(f"\n[DEBUG] Production daemon PIDs (protected): {cls._production_pids}")

    @classmethod
    def get_test_daemon_pids(cls) -> list:
        """Get PIDs of daemons spawned during testing (excludes production)."""
        all_pids = set(cls._get_all_daemon_pids())
        return sorted(all_pids - cls._production_pids)

    @classmethod
    def _kill_pid(cls, pid_str: str):
        """Kill a process by PID string. Cross-platform via psutil."""
        try:
            proc = psutil.Process(int(pid_str))
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
            pass

    @classmethod
    def kill_test_daemons(cls):
        """Kill only test-spawned daemon processes. Never touches production PIDs."""
        test_pids = cls.get_test_daemon_pids()
        for pid in test_pids:
            cls._kill_pid(pid)
        if test_pids:
            time.sleep(0.3)
            cls._test_spawned_pids -= set(test_pids)

    @classmethod
    def spawn_test_daemon(cls):
        """Start a test daemon and track its PID.

        Returns the PID of the spawned daemon, or None on failure.
        """
        before = set(cls._get_all_daemon_pids())
        try:
            subprocess.run(
                [sys.executable, "-m", "autorun", "--restart-daemon"],
                capture_output=True, timeout=30
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        time.sleep(0.5)
        after = set(cls._get_all_daemon_pids())
        new_pids = after - before
        cls._test_spawned_pids.update(new_pids)
        return next(iter(new_pids), None)

    @classmethod
    def verify_daemon_count(cls) -> tuple:
        """Check test-spawned daemon count.

        Returns:
            (test_pids, production_pids) — both as lists
        """
        return cls.get_test_daemon_pids(), sorted(cls._production_pids)

    @classmethod
    def cleanup(cls):
        """Kill all test-spawned daemons. Called at session end.

        Idempotent — safe to call multiple times.
        """
        cls.kill_test_daemons()
        cls._test_spawned_pids.clear()

    @classmethod
    def assert_daemon_count(cls, max_test_daemons: int = 1):
        """Assert test daemon count is within limits. Cleans extras first.

        Returns (test_pids, production_pids) for diagnostics.
        Use this in tests instead of raw pgrep/kill calls.
        """
        import warnings

        # Kill extras first, keep oldest
        test_pids = cls.get_test_daemon_pids()
        if len(test_pids) > max_test_daemons:
            for pid in test_pids[max_test_daemons:]:
                cls._kill_pid(pid)
            time.sleep(0.3)
            test_pids = cls.get_test_daemon_pids()

        prod_pids = sorted(cls._production_pids & set(cls._get_all_daemon_pids()))

        if len(test_pids) > max_test_daemons:
            pytest.fail(
                f"Too many test daemons ({len(test_pids)}): {test_pids}. "
                f"Production daemons ({len(prod_pids)}): {prod_pids}."
            )
        elif len(test_pids) > 0:
            warnings.warn(
                f"Test daemons running ({len(test_pids)}): {test_pids}. "
                f"Production daemons ({len(prod_pids)}): {prod_pids}.",
                stacklevel=2
            )

        return test_pids, prod_pids


@pytest.fixture(scope="session")
def test_timeout():
    """Default timeout for test operations."""
    return 10.0


@pytest.fixture(scope="session")
def stress_test_timeout():
    """Extended timeout for stress tests."""
    return 60.0



def should_keep_test_artifacts():
    """Check if test artifacts should be kept for debugging.

    Set AUTORUN_KEEP_TEST_ARTIFACTS=true to keep all test artifacts.
    """
    value = os.getenv("AUTORUN_KEEP_TEST_ARTIFACTS", "false").lower().strip()
    return value in {"true", "1", "yes", "on", "enabled"}


# Global registry to track session IDs created during tests
_test_session_ids = set()


def register_test_session(session_id: str):
    """Register a session ID for cleanup after tests."""
    _test_session_ids.add(session_id)


def cleanup_test_sessions():
    """Clean up all registered test sessions.

    This removes database files created during tests.
    Skipped if AUTORUN_KEEP_TEST_ARTIFACTS is set.
    """
    if should_keep_test_artifacts():
        print(f"\n[DEBUG] Keeping {len(_test_session_ids)} test session artifacts for debugging")
        return

    state_dir = Path.home() / ".claude" / "sessions"
    if not state_dir.exists():
        return

    cleaned = 0
    for session_id in _test_session_ids:
        # Direct file removal with known shelve suffixes (no glob — slow with 10K+ files)
        for prefix in [session_id, f"test_backend_{session_id}", f"test_dumbdbm_{session_id}",
                       f"plugin_{session_id}.db", f"plugin_{session_id}_dumb.db"]:
            base = str(state_dir / prefix)
            for suffix in ["", ".db", ".dir", ".bak", ".dat"]:
                try:
                    os.remove(base + suffix)
                    cleaned += 1
                except OSError:
                    pass

    _test_session_ids.clear()
    if cleaned > 0 and os.getenv("DEBUG", "").lower() in {"true", "1", "yes"}:
        print(f"\n[DEBUG] Cleaned up {cleaned} test session files")


@pytest.fixture
def temp_session_dir():
    """Create a temporary directory for session storage"""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    if not should_keep_test_artifacts():
        shutil.rmtree(temp_dir, ignore_errors=True)
    else:
        print(f"\n[DEBUG] Keeping temp session dir: {temp_dir}")


@pytest.fixture
def mock_session_state(temp_session_dir):
    """Create a mock session state for testing"""
    # Simple fixture that doesn't try to patch STATE_DIR
    # This avoids the AttributeError with function objects
    yield temp_session_dir


@pytest.fixture
def unique_session_id():
    """Generate a unique session ID for testing and register it for cleanup.

    Usage:
        def test_something(unique_session_id):
            session_id = unique_session_id()
            # Use session_id in test...
    """
    created_ids = []

    def _generate():
        session_id = f"test_session_{uuid.uuid4().hex[:8]}"
        created_ids.append(session_id)
        register_test_session(session_id)
        return session_id

    yield _generate

    # Cleanup specific to this test (no glob — slow with 10K+ files)
    if not should_keep_test_artifacts():
        state_dir = Path.home() / ".claude" / "sessions"
        if state_dir.exists():
            for session_id in created_ids:
                for prefix in [session_id, f"test_backend_{session_id}", f"test_dumbdbm_{session_id}",
                               f"plugin_{session_id}.db", f"plugin_{session_id}_dumb.db"]:
                    base = str(state_dir / prefix)
                    for suffix in ["", ".db", ".dir", ".bak", ".dat"]:
                        try:
                            os.remove(base + suffix)
                        except OSError:
                            pass


# =============================================================================
# PYTEST SESSION HOOKS (daemon + session lifecycle)
# =============================================================================

def pytest_sessionstart(session):
    """Record production daemon PIDs before any tests run."""
    DaemonManager.snapshot_production_pids()


def pytest_sessionfinish(session, exitstatus):
    """Clean up test sessions and test-spawned daemons after pytest finishes."""
    cleanup_test_sessions()
    DaemonManager.cleanup()


@pytest.fixture(scope="session")
def ensure_single_daemon():
    """Session-scoped fixture that ensures a test daemon is running.

    Uses DaemonManager to:
    - Kill only test-spawned daemons (never production ones)
    - Start a fresh test daemon
    - Clean up on teardown

    Tests that need a daemon should depend on this fixture.
    """
    # Kill any test-spawned daemons from previous runs
    DaemonManager.kill_test_daemons()

    # Spawn a fresh test daemon
    DaemonManager.spawn_test_daemon()

    yield

    # Cleanup test-spawned daemons
    DaemonManager.kill_test_daemons()


@pytest.fixture
def daemon_manager():
    """Per-test access to DaemonManager for daemon lifecycle operations.

    Usage:
        def test_daemon_count(daemon_manager):
            test_pids, prod_pids = daemon_manager.verify_daemon_count()
            assert len(test_pids) <= 1
    """
    return DaemonManager


@pytest.fixture
def mock_session_state_factory():
    """Factory for creating mock session states with configurable defaults.

    Usage:
        def test_something(mock_session_state_factory):
            state = mock_session_state_factory(policy="SEARCH", status="active")
            # Use state in test...

    Reduces mock duplication across tests by providing a single factory.
    """
    def _create(policy="ALLOW", status="inactive", stage="INITIAL", **extra):
        state = {
            "file_policy": policy,
            "session_status": status,
            "autorun_stage": stage,
            "activation_prompt": "",
            "recheck_count": 0,
        }
        state.update(extra)
        return state

    return _create


@pytest.fixture
def policy_responses():
    """Expected policy response strings - generated from CONFIG."""
    return {
        policy: f"AutoFile policy: {CONFIG['policies'][policy][0]} - {CONFIG['policies'][policy][1]}"
        for policy in ["SEARCH", "ALLOW", "JUSTIFY"]
    }


@pytest.fixture
def sample_commands():
    """Sample commands for testing"""
    return {
        "policy_commands": ["/afs", "/afa", "/afj", "/afst"],
        "control_commands": ["/autostop", "/estop"],
        "normal_commands": ["help me", "what is this", "test file"],
        "autorun_command": "/autorun test task description"
    }


@pytest.fixture
def expected_responses():
    """Expected responses for commands - generated from CONFIG."""
    return {
        "/afs": f"AutoFile policy: {CONFIG['policies']['SEARCH'][0]} - {CONFIG['policies']['SEARCH'][1]}",
        "/afa": f"AutoFile policy: {CONFIG['policies']['ALLOW'][0]} - {CONFIG['policies']['ALLOW'][1]}",
        "/afj": f"AutoFile policy: {CONFIG['policies']['JUSTIFY'][0]} - {CONFIG['policies']['JUSTIFY'][1]}",
        "/afst": f"Current policy: {CONFIG['policies']['ALLOW'][0]}",
        "/autostop": "Autorun stopped",
        "/estop": "Emergency stop activated"
    }


@pytest.fixture
def plugin_input_data():
    """Sample input data for plugin testing"""
    return {
        "prompt": "/afs",
        "session_id": "test_session",
        "session_transcript": []
    }


@pytest.fixture
def hook_input_data():
    """Sample input data for hook testing"""
    return {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "test_session",
        "prompt": "/afa",
        "session_transcript": []
    }