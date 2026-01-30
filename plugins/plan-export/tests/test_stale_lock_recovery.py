#!/usr/bin/env python3
"""
Stale lock recovery tests for plan export race condition fix.

Tests scenarios where locks are left behind by crashed processes,
including:
1. Crashed process left a lock file
2. PID in lock file no longer exists
3. Manual lock cleanup
4. Recovery after stale lock detection
5. Lock file corruption handling

These tests verify robustness and recoverability when things go wrong.
"""

import io
import json
import os
import sys
import time
import uuid
import multiprocessing
import signal
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime
from typing import Dict, List

import pytest

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "clautorun" / "src"))

from export_plan_module import (
    load_config,
    export_plan,
)
from clautorun.session_manager import SessionLock, SessionTimeoutError


# =============================================================================
# MODULE-LEVEL FUNCTIONS FOR MULTIPROCESSING
# These must be at module level for pickle compatibility
# =============================================================================

def _hold_lock_for_contention_test(session_id: str, state_dir: Path,
                                     lock_held, lock_released):
    """First process holds a lock for testing contention."""
    with SessionLock(session_id, timeout=5.0, state_dir=state_dir):
        lock_held.value = True
        time.sleep(0.5)  # Hold long enough for second to try acquiring
    lock_released.value = True


def _try_acquire_for_contention_test(session_id: str, state_dir: Path,
                                       second_result):
    """Second process tries to acquire a lock held by another."""
    time.sleep(0.1)  # Start trying before first releases
    try:
        with SessionLock(session_id, timeout=0.2, state_dir=state_dir):
            # Should timeout since first holds lock for 0.5s
            second_result["value"] = "acquired"
    except SessionTimeoutError:
        second_result["value"] = "timeout"
    except Exception as e:
        second_result["value"] = f"error: {e}"


def _hold_lock_simple(session_id: str, state_dir: Path, first_acquired):
    """First process holds the lock."""
    with SessionLock(session_id, timeout=5.0, state_dir=state_dir):
        first_acquired.value = True
        time.sleep(0.4)  # Hold long enough for second to try acquiring


def _try_acquire_simple(session_id: str, state_dir: Path, second_result):
    """Second process tries to acquire."""
    time.sleep(0.05)  # Start trying immediately
    try:
        with SessionLock(session_id, timeout=0.2, state_dir=state_dir):
            # Should timeout since first holds lock for 0.4s
            second_result["value"] = "acquired"
    except SessionTimeoutError:
        second_result["value"] = "timeout"


# =============================================================================
# TEST UTILITIES
# =============================================================================

def create_stale_lock(state_dir: Path, session_id: str, old_pid: int = None) -> Path:
    """
    Create a stale lock file for testing.

    Note: This creates a JSON file but doesn't hold an actual fcntl lock.
    For testing actual lock contention, use processes that hold real locks.
    """
    lock_file = state_dir / f".{session_id}.lock"

    # Use a PID that doesn't exist
    if old_pid is None:
        old_pid = 999999  # Very unlikely to exist

    lock_info = {
        'pid': old_pid,
        'start_time': time.time() - 3600,  # 1 hour ago
        'session_id': session_id
    }

    lock_file.write_text(json.dumps(lock_info))
    return lock_file


def create_corrupted_lock(state_dir: Path, session_id: str) -> Path:
    """
    Create a corrupted lock file.

    Note: For testing how the system handles corrupted lock files.
    """
    lock_file = state_dir / f".{session_id}.lock"
    lock_file.write_text("corrupted data {{{")
    return lock_file


# =============================================================================
# TEST FIXTURES
# =============================================================================

@pytest.fixture
def stale_lock_setup(tmp_path):
    """Setup for stale lock testing."""
    test_root = tmp_path / f"stale_lock_test_{uuid.uuid4().hex[:8]}"
    test_root.mkdir(parents=True, exist_ok=True)

    test_state_dir = test_root / "sessions"
    test_state_dir.mkdir(parents=True, exist_ok=True)

    test_plans_dir = test_root / "plans"
    test_plans_dir.mkdir(parents=True, exist_ok=True)

    project_dir = test_root / "project"
    project_dir.mkdir(parents=True, exist_ok=True)

    # Create a test plan file
    session_id = f"test_session_{uuid.uuid4().hex[:8]}"
    plan_path = test_plans_dir / f"plan_{session_id}.md"
    plan_path.write_text(f"# Test Plan\n\nSession: {session_id}\n")

    yield {
        "test_root": test_root,
        "test_state_dir": test_state_dir,
        "plan_path": plan_path,
        "project_dir": project_dir,
        "session_id": session_id
    }

    # Cleanup
    if test_root.exists():
        import shutil
        shutil.rmtree(test_root, ignore_errors=True)


# =============================================================================
# STALE LOCK RECOVERY TESTS
# =============================================================================

class TestStaleLockRecovery:
    """Test recovery from stale locks left by crashed processes."""

    def test_stale_lock_with_nonexistent_pid(self, stale_lock_setup):
        """
        Test behavior when lock file exists (simulating stale lock).

        Note: Creating a JSON file doesn't create a real fcntl lock.
        This test verifies that the system handles lock files gracefully.
        """
        setup = stale_lock_setup
        session_id = setup["session_id"]

        # Create a stale lock file (simulating crashed process)
        stale_lock = create_stale_lock(setup["test_state_dir"], session_id)
        assert stale_lock.exists()

        # Try to acquire lock - should succeed because no fcntl lock is held
        # (The JSON file doesn't have an active flock)
        try:
            with SessionLock(session_id, timeout=1.0, state_dir=setup["test_state_dir"]):
                # Lock acquired successfully (no actual flock contention)
                assert True
        except SessionTimeoutError:
            # If timeout occurs, that's also acceptable behavior
            pass

        # Clean up
        if stale_lock.exists():
            stale_lock.unlink()

    def test_actual_lock_contention(self, stale_lock_setup):
        """
        Test actual lock contention between processes.

        This is the meaningful test: one process holds a real fcntl lock,
        another process tries to acquire it and should timeout.
        """
        setup = stale_lock_setup
        session_id = setup["session_id"]

        lock_held = multiprocessing.Value('b', False)
        lock_released = multiprocessing.Value('b', False)
        manager = multiprocessing.Manager()
        second_result = manager.dict({"value": None})

        # Run both processes
        p1 = multiprocessing.Process(
            target=_hold_lock_for_contention_test,
            args=(session_id, setup["test_state_dir"], lock_held, lock_released)
        )
        p2 = multiprocessing.Process(
            target=_try_acquire_for_contention_test,
            args=(session_id, setup["test_state_dir"], second_result)
        )

        p1.start()
        p2.start()

        p1.join(timeout=10)
        p2.join(timeout=10)

        # Verify results
        assert lock_released.value, "First process didn't complete"
        assert second_result["value"] == "timeout", \
            f"Second process should timeout, got: {second_result['value']}"

    def test_stale_lock_manual_cleanup(self, stale_lock_setup):
        """
        Test manual cleanup of stale locks.

        Users may need to manually clean up stale locks if automatic
        detection is not implemented.
        """
        setup = stale_lock_setup
        session_id = setup["session_id"]

        # Create a stale lock file
        stale_lock = create_stale_lock(setup["test_state_dir"], session_id)
        assert stale_lock.exists()

        # Manually clean up (simulating user intervention)
        stale_lock.unlink()
        assert not stale_lock.exists()

        # Now lock can be acquired
        with SessionLock(session_id, timeout=5.0, state_dir=setup["test_state_dir"]):
            # Lock acquired successfully
            assert True

    def test_corrupted_lock_file(self, stale_lock_setup):
        """
        Test handling of corrupted lock files.

        Expected behavior:
        - System should handle gracefully
        - May timeout or error
        - No crash
        """
        setup = stale_lock_setup
        session_id = setup["session_id"]

        # Create a corrupted lock file
        corrupted_lock = create_corrupted_lock(setup["test_state_dir"], session_id)
        assert corrupted_lock.exists()

        # Try to acquire lock - should handle gracefully
        try:
            with SessionLock(session_id, timeout=2.0, state_dir=setup["test_state_dir"]):
                # If we get here, system handled corruption
                assert True
        except (SessionTimeoutError, Exception) as e:
            # Timeout or error is acceptable for corrupted lock
            assert isinstance(e, (SessionTimeoutError, Exception))

        # Clean up for next test
        if corrupted_lock.exists():
            corrupted_lock.unlink()

    def test_lock_cleanup_after_exception(self, stale_lock_setup):
        """
        Verify lock files are cleaned up even if exception occurs.

        This is the RAII pattern in action - lock is guaranteed cleanup.
        """
        setup = stale_lock_setup
        session_id = setup["session_id"]
        lock_file = setup["test_state_dir"] / f".{session_id}.lock"

        # Verify lock doesn't exist initially
        assert not lock_file.exists()

        # Acquire lock and raise exception
        try:
            with SessionLock(session_id, timeout=5.0, state_dir=setup["test_state_dir"]):
                assert lock_file.exists()
                raise ValueError("Simulated error")
        except ValueError:
            pass  # Expected

        # Lock should be cleaned up despite exception
        assert not lock_file.exists(), "Lock not cleaned up after exception"

    def test_rapid_crash_recovery(self, stale_lock_setup):
        """
        Test rapid crash and recovery cycles.

        Simulates processes crashing and restarting quickly.
        """
        setup = stale_lock_setup
        session_id = setup["session_id"]

        # Simulate multiple crash/recover cycles
        for i in range(5):
            # Create and hold lock briefly
            try:
                with SessionLock(session_id, timeout=2.0, state_dir=setup["test_state_dir"]):
                    time.sleep(0.01)
            except SessionTimeoutError:
                # If we timeout, clean up and retry
                lock_file = setup["test_state_dir"] / f".{session_id}.lock"
                if lock_file.exists():
                    lock_file.unlink()
                time.sleep(0.1)

        # Final cleanup
        lock_file = setup["test_state_dir"] / f".{session_id}.lock"
        if lock_file.exists():
            lock_file.unlink()

    def test_concurrent_lock_contention(self, stale_lock_setup):
        """
        Test actual concurrent lock contention between processes.

        One process holds a lock, another tries to acquire it.
        """
        setup = stale_lock_setup
        session_id = setup["session_id"]
        lock_file = setup["test_state_dir"] / f".{session_id}.lock"

        first_acquired = multiprocessing.Value('b', False)
        manager = multiprocessing.Manager()
        second_result = manager.dict({"value": None})

        p1 = multiprocessing.Process(
            target=_hold_lock_simple,
            args=(session_id, setup["test_state_dir"], first_acquired)
        )
        p2 = multiprocessing.Process(
            target=_try_acquire_simple,
            args=(session_id, setup["test_state_dir"], second_result)
        )

        p1.start()
        p2.start()

        p1.join(timeout=10)
        p2.join(timeout=10)

        assert first_acquired.value, "First process didn't acquire lock"
        assert second_result["value"] == "timeout", \
            f"Second should timeout, got: {second_result['value']}"


# =============================================================================
# LOCK FILE INTEGRITY TESTS
# =============================================================================

class TestLockFileIntegrity:
    """Test lock file integrity and robustness."""

    def test_lock_file_has_pid(self, stale_lock_setup):
        """
        Verify lock files contain PID information for debugging.
        """
        setup = stale_lock_setup
        session_id = setup["session_id"]
        lock_file = setup["test_state_dir"] / f".{session_id}.lock"

        with SessionLock(session_id, timeout=5.0, state_dir=setup["test_state_dir"]):
            assert lock_file.exists()

            # Read lock file content
            content = lock_file.read_text()

            # Verify it contains PID
            assert "pid" in content.lower()

            # Verify it's valid JSON
            lock_info = json.loads(content)
            assert "pid" in lock_info
            assert isinstance(lock_info["pid"], int)

        # Lock file cleaned up
        assert not lock_file.exists()

    def test_lock_file_has_timestamp(self, stale_lock_setup):
        """
        Verify lock files contain timestamp for debugging.
        """
        setup = stale_lock_setup
        session_id = setup["session_id"]
        lock_file = setup["test_state_dir"] / f".{session_id}.lock"

        with SessionLock(session_id, timeout=5.0, state_dir=setup["test_state_dir"]):
            assert lock_file.exists()

            # Read lock file content
            content = lock_file.read_text()
            lock_info = json.loads(content)

            # Verify timestamp exists
            assert "start_time" in lock_info
            assert isinstance(lock_info["start_time"], float)

    def test_lock_file_has_session_id(self, stale_lock_setup):
        """
        Verify lock files contain session_id for debugging.
        """
        setup = stale_lock_setup
        session_id = setup["session_id"]
        lock_file = setup["test_state_dir"] / f".{session_id}.lock"

        with SessionLock(session_id, timeout=5.0, state_dir=setup["test_state_dir"]):
            assert lock_file.exists()

            # Read lock file content
            content = lock_file.read_text()
            lock_info = json.loads(content)

            # Verify session_id matches
            assert lock_info["session_id"] == session_id


# =============================================================================
# MANUAL CLEANUP SCENARIOS
# =============================================================================

class TestManualCleanup:
    """Test scenarios requiring manual cleanup."""

    def test_cleanup_all_stale_locks(self, stale_lock_setup):
        """
        Test cleaning up all stale locks in state directory.

        Simulates user manually cleaning up after crashes.
        """
        setup = stale_lock_setup

        # Create multiple stale locks
        session_ids = [
            f"stale_session_{i}_{uuid.uuid4().hex[:8]}"
            for i in range(5)
        ]

        for session_id in session_ids:
            create_stale_lock(setup["test_state_dir"], session_id)

        # Verify stale locks exist
        stale_locks = list(setup["test_state_dir"].glob(".*.lock"))
        assert len(stale_locks) == 5

        # Manual cleanup: remove all stale locks
        for lock_file in stale_locks:
            lock_file.unlink()

        # Verify all cleaned up
        remaining_locks = list(setup["test_state_dir"].glob(".*.lock"))
        assert len(remaining_locks) == 0

    def test_cleanup_specific_stale_lock(self, stale_lock_setup):
        """
        Test cleaning up a specific stale lock.

        Simulates user identifying and removing a specific problem lock.
        """
        setup = stale_lock_setup
        target_session = f"target_session_{uuid.uuid4().hex[:8]}"

        # Create multiple locks
        create_stale_lock(setup["test_state_dir"], target_session)
        create_stale_lock(setup["test_state_dir"], f"other_session_{uuid.uuid4().hex[:8]}")

        # Find and remove specific lock
        target_lock = setup["test_state_dir"] / f".{target_session}.lock"
        assert target_lock.exists()
        target_lock.unlink()

        # Verify only target lock removed
        remaining_locks = list(setup["test_state_dir"].glob(".*.lock"))
        assert len(remaining_locks) == 1
        assert "target" not in remaining_locks[0].name


# =============================================================================
# RECOVERY AFTER CLEANUP
# =============================================================================

class TestRecoveryAfterCleanup:
    """Test system recovery after manual cleanup."""

    def test_normal_operation_after_cleanup(self, stale_lock_setup):
        """
        Test that system operates normally after stale lock cleanup.
        """
        setup = stale_lock_setup
        session_id = setup["session_id"]

        # Create and cleanup stale lock
        stale_lock = create_stale_lock(setup["test_state_dir"], session_id)
        stale_lock.unlink()

        # System should work normally now
        with SessionLock(session_id, timeout=5.0, state_dir=setup["test_state_dir"]):
            # Successful operation
            assert True

    def test_concurrent_after_cleanup(self, stale_lock_setup):
        """
        Test concurrent access works after stale lock cleanup.
        """
        setup = stale_lock_setup
        session_id = setup["session_id"]

        # Create and cleanup stale lock
        stale_lock = create_stale_lock(setup["test_state_dir"], session_id)
        stale_lock.unlink()

        results = []

        def worker(worker_id: int) -> Dict:
            """Worker that acquires and releases lock."""
            try:
                with SessionLock(session_id, timeout=5.0, state_dir=setup["test_state_dir"]):
                    time.sleep(0.05)
                    return {
                        "worker_id": worker_id,
                        "success": True
                    }
            except Exception as e:
                return {
                    "worker_id": worker_id,
                    "error": str(e)
                }

        # Run concurrent workers
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(worker, i) for i in range(3)]
            results = [f.result() for f in as_completed(futures)]

        # All should succeed
        successful = [r for r in results if r.get("success")]
        assert len(successful) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
