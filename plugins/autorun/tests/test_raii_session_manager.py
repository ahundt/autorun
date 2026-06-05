#!/usr/bin/env python3
"""Tests for the JSON/filelock-backed session state manager."""

import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _session_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _state_dir() -> Path:
    return Path(os.environ["AUTORUN_TEST_STATE_DIR"])


def test_raii_resource_management():
    """State writes persist when the context exits and the lock file remains observable."""
    from autorun.session_manager import session_state

    session_id = _session_id("raii")
    lock_file = _state_dir() / "daemon_state.json.lock"
    state_file = _state_dir() / "daemon_state.json"

    with session_state(session_id) as state:
        state["worker_1"] = "complete"

    assert state_file.exists()
    assert lock_file.exists()

    with session_state(session_id) as state:
        assert state["worker_1"] == "complete"
        state["worker_2"] = "complete"

    with session_state(session_id) as state:
        assert state["worker_1"] == "complete"
        assert state["worker_2"] == "complete"


def test_concurrent_raii_safety():
    """Concurrent threads serialize through session_state without losing writes."""
    from autorun.session_manager import session_state

    session_id = _session_id("concurrent_raii")
    results = []
    errors = []

    def concurrent_worker(worker_id):
        try:
            with session_state(session_id) as state:
                state[f"worker_{worker_id}_start"] = time.time()
                state[f"worker_{worker_id}_pid"] = os.getpid()
                time.sleep(0.02)
                results.append({
                    "worker_id": worker_id,
                    "pid": state[f"worker_{worker_id}_pid"],
                })
        except Exception as e:
            errors.append((worker_id, str(e)))

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(concurrent_worker, i) for i in range(15)]
        for future in futures:
            future.result()

    assert errors == []
    assert len(results) == 15

    with session_state(session_id) as state:
        for worker_id in range(15):
            assert f"worker_{worker_id}_start" in state
            assert state[f"worker_{worker_id}_pid"] == os.getpid()


def test_timeout_behavior():
    """A waiter with a short timeout fails while another thread holds the state lock."""
    from autorun.session_manager import SessionTimeoutError, session_state

    session_id = _session_id("timeout")
    results = []

    def blocking_worker():
        try:
            with session_state(session_id, timeout=2.0) as state:
                state["blocking_worker"] = "holding"
                time.sleep(0.6)
                results.append("blocking_completed")
        except Exception as e:
            results.append(f"blocking_error: {e}")

    def timeout_worker():
        try:
            time.sleep(0.05)
            with session_state(session_id, timeout=0.1) as state:
                state["timeout_worker"] = "should_not_reach"
                results.append("timeout_unexpected_success")
        except SessionTimeoutError:
            results.append("timeout_expected")
        except Exception as e:
            results.append(f"timeout_unexpected_error: {e}")

    def quick_worker():
        try:
            time.sleep(0.8)
            with session_state(session_id, timeout=1.0) as state:
                state["quick_worker"] = "success"
                results.append("quick_success")
        except Exception as e:
            results.append(f"quick_error: {e}")

    threads = [
        threading.Thread(target=blocking_worker),
        threading.Thread(target=timeout_worker),
        threading.Thread(target=quick_worker),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert "blocking_completed" in results
    assert "timeout_expected" in results
    assert "quick_success" in results
    assert not any("unexpected" in result or "error" in result for result in results)


def test_shared_access_isolation():
    """The shared-session wrapper uses the same isolated test state backend."""
    from autorun.session_manager import session_state, shared_session_state

    session_id = _session_id("shared_access")
    results = []
    errors = []

    def exclusive_worker():
        try:
            with session_state(session_id, timeout=2.0) as state:
                state["exclusive_data"] = "exclusive_value"
                state["exclusive_timestamp"] = time.time()
                results.append({"type": "exclusive", "success": True})
        except Exception as e:
            errors.append(("exclusive", str(e)))

    def shared_worker(worker_id):
        try:
            with shared_session_state(session_id, timeout=2.0) as state:
                results.append({
                    "type": "shared",
                    "worker_id": worker_id,
                    "exclusive_data": state.get("exclusive_data"),
                    "success": True,
                })
        except Exception as e:
            errors.append((f"shared_{worker_id}", str(e)))

    exclusive_thread = threading.Thread(target=exclusive_worker)
    exclusive_thread.start()
    exclusive_thread.join()

    shared_threads = [threading.Thread(target=shared_worker, args=(i,)) for i in range(3)]
    for thread in shared_threads:
        thread.start()
    for thread in shared_threads:
        thread.join()

    assert errors == []
    assert len(results) == 4
    assert sum(1 for r in results if r["type"] == "exclusive") == 1
    shared_results = [r for r in results if r["type"] == "shared"]
    assert len(shared_results) == 3
    assert all(r["exclusive_data"] == "exclusive_value" for r in shared_results)


def test_error_handling_robustness():
    """Invalid session IDs fail before creating ambiguous state prefixes."""
    import pytest

    from autorun.session_manager import SessionStateError, session_state

    for invalid_session_id in ("", "   ", None):
        with pytest.raises(SessionStateError):
            with session_state(invalid_session_id, timeout=1.0):
                pass


def test_dry_principles():
    """Convenience wrappers share the same session manager and state file."""
    from autorun.session_manager import get_session_manager, session_state, shared_session_state

    session_id = _session_id("dry")
    manager1 = get_session_manager()
    manager2 = get_session_manager()

    assert manager1 is manager2

    with session_state(session_id) as state:
        state["test"] = "value1"

    with shared_session_state(session_id) as state:
        assert state["test"] == "value1"
        state["test"] = "value2"

    with session_state(session_id) as state:
        assert state["test"] == "value2"


def run_comprehensive_raii_tests():
    """Run this module as a script for manual diagnostics."""
    tests = [
        ("RAII Resource Management", test_raii_resource_management),
        ("Concurrent RAII Safety", test_concurrent_raii_safety),
        ("Timeout Behavior", test_timeout_behavior),
        ("Shared Access Isolation", test_shared_access_isolation),
        ("Error Handling Robustness", test_error_handling_robustness),
        ("DRY Principles", test_dry_principles),
    ]

    for test_name, test_func in tests:
        print(f"Running {test_name}...")
        test_func()
        print(f"{test_name}: PASSED")

    return True


if __name__ == "__main__":
    run_comprehensive_raii_tests()
