# -*- coding: utf-8 -*-
"""
pytest configuration and fixtures for clautorun testing

Environment Variables:
    CLAUTORUN_KEEP_TEST_ARTIFACTS: Set to 'true', '1', or 'yes' to keep test artifacts
                                   for debugging instead of cleaning them up.
"""
import pytest
import tempfile
import shutil
import sys
import os
import uuid

# Python 2/3 compatibility
try:
    from pathlib import Path
except ImportError:
    # Python 2.7 fallback
    import os as os_module
    class Path(object):
        def __init__(self, path):
            self.path = str(path)
        def __str__(self):
            return self.path
        def __div__(self, other):
            return Path(os_module.path.join(self.path, str(other)))
        def __truediv__(self, other):
            return Path(os_module.path.join(self.path, str(other)))
        @property
        def parent(self):
            return Path(os_module.path.dirname(self.path))

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))



def should_keep_test_artifacts():
    """Check if test artifacts should be kept for debugging.

    Set CLAUTORUN_KEEP_TEST_ARTIFACTS=true to keep all test artifacts.
    """
    value = os.getenv("CLAUTORUN_KEEP_TEST_ARTIFACTS", "false").lower().strip()
    return value in {"true", "1", "yes", "on", "enabled"}


# Global registry to track session IDs created during tests
_test_session_ids = set()


def register_test_session(session_id: str):
    """Register a session ID for cleanup after tests."""
    _test_session_ids.add(session_id)


def cleanup_test_sessions():
    """Clean up all registered test sessions.

    This removes database files created during tests.
    Skipped if CLAUTORUN_KEEP_TEST_ARTIFACTS is set.
    """
    if should_keep_test_artifacts():
        print(f"\n[DEBUG] Keeping {len(_test_session_ids)} test session artifacts for debugging")
        return

    state_dir = Path.home() / ".claude" / "sessions"
    if not state_dir.exists():
        return

    cleaned = 0
    for session_id in _test_session_ids:
        # Clean up all files matching this session ID pattern
        for pattern in [f"{session_id}*", f"test_backend_{session_id}*", f"test_dumbdbm_{session_id}*"]:
            for filepath in state_dir.glob(pattern):
                try:
                    filepath.unlink()
                    cleaned += 1
                except (OSError, IOError):
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

    # Cleanup specific to this test
    if not should_keep_test_artifacts():
        state_dir = Path.home() / ".claude" / "sessions"
        if state_dir.exists():
            for session_id in created_ids:
                for pattern in [f"{session_id}*", f"test_backend_{session_id}*", f"test_dumbdbm_{session_id}*"]:
                    for filepath in state_dir.glob(pattern):
                        try:
                            filepath.unlink()
                        except (OSError, IOError):
                            pass


# Register cleanup to run at session end
def pytest_sessionfinish(session, exitstatus):
    """Clean up all test sessions after pytest finishes."""
    cleanup_test_sessions()


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
    """Expected policy response strings for testing file policy commands.

    These match the responses from CONFIG["policies"] in config.py.
    """
    return {
        "SEARCH": "AutoFile policy: strict-search - STRICT SEARCH: ONLY modify existing files. Use Glob/Grep. NO new files.",
        "ALLOW": "AutoFile policy: allow-all - ALLOW ALL: Full permission to create/modify files.",
        "JUSTIFY": "AutoFile policy: justify-create - JUSTIFIED: Search existing first. Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> for new files.",
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
    """Expected responses for commands"""
    return {
        "/afs": "AutoFile policy: strict-search - STRICT SEARCH: ONLY modify existing files. Use Glob/Grep. NO new files.",
        "/afa": "AutoFile policy: allow-all - ALLOW ALL: Full permission to create/modify files.",
        "/afj": "AutoFile policy: justify-create - JUSTIFIED: Search existing first. Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> for new files.",
        "/afst": "Current policy: allow-all",
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