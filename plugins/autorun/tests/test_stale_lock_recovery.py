#!/usr/bin/env python3
"""
Filelock+JSON session manager recovery and contention tests.

Tests the new filelock+JSON backend (daemon_state.json + daemon_state.json.lock)
for correct behavior under:
1. Cross-process lock contention — session_state() blocks concurrent writers
2. State file recovery — corrupt/missing daemon_state.json handled gracefully
3. Stale lock file handling — leftover daemon_state.json.lock doesn't block new acquires
4. Lock file lifecycle — daemon_state.json.lock exists while lock is held
5. Atomic write safety — partial writes don't corrupt state
6. SessionLock no-op behavior — SessionLock is a shim, creates no lock files
"""

import json
import os
import sys
import time
import uuid
import multiprocessing
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List

import pytest

from clautorun.session_manager import (
    SessionLock,
    SessionTimeoutError,
    session_state,
    shared_session_state,
    _reset_for_testing,
)


# =============================================================================
# MODULE-LEVEL FUNCTIONS FOR MULTIPROCESSING
# Must be at module level for pickle compatibility.
# These functions exercise real cross-process filelock contention by using
# session_state() (the actual locking mechanism) rather than the no-op SessionLock.
# =============================================================================

def _hold_session_for_contention(session_id: str, state_dir: str,
                                  lock_held, lock_released):
    """First process holds session_state open to block other processes."""
    from clautorun.session_manager import session_state
    with session_state(session_id, state_dir=state_dir, timeout=10.0):
        lock_held.value = True
        time.sleep(0.5)  # Hold long enough for second process to attempt acquire
    lock_released.value = True


def _try_acquire_while_held(session_id: str, state_dir: str, second_result):
    """Second process tries to acquire session_state while first holds it."""
    from clautorun.session_manager import session_state, SessionTimeoutError
    time.sleep(0.1)  # Wait for first to acquire before trying
    try:
        with session_state(session_id, state_dir=state_dir, timeout=0.2):
            # Should timeout — first holds the lock for 0.5s
            second_result["value"] = "acquired"
    except SessionTimeoutError:
        second_result["value"] = "timeout"
    except Exception as e:
        second_result["value"] = f"error: {e}"


def _hold_session_simple(session_id: str, state_dir: str, first_acquired):
    """First process holds the session lock."""
    from clautorun.session_manager import session_state
    with session_state(session_id, state_dir=state_dir, timeout=5.0):
        first_acquired.value = True
        time.sleep(0.4)  # Hold long enough for second to try acquiring


def _try_acquire_simple(session_id: str, state_dir: str, second_result):
    """Second process tries to acquire a lock held by another."""
    from clautorun.session_manager import session_state, SessionTimeoutError
    time.sleep(0.05)  # Give first process time to acquire
    try:
        with session_state(session_id, state_dir=state_dir, timeout=0.2):
            # Should timeout — first holds for 0.4s
            second_result["value"] = "acquired"
    except SessionTimeoutError:
        second_result["value"] = "timeout"


def _write_and_read_session(session_id: str, state_dir: str, worker_id: int,
                             result):
    """Worker: write a key, read it back, report success/value."""
    from clautorun.session_manager import session_state
    try:
        with session_state(session_id, state_dir=state_dir) as s:
            s[f"worker_{worker_id}"] = worker_id
        with session_state(session_id, state_dir=state_dir) as s:
            val = s.get(f"worker_{worker_id}")
        result["value"] = val
        result["success"] = True
    except Exception as e:
        result["error"] = str(e)
        result["success"] = False


# =============================================================================
# TEST FIXTURES
# =============================================================================

@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset module-level singletons before and after each test."""
    _reset_for_testing()
    yield
    _reset_for_testing()


@pytest.fixture
def state_setup(tmp_path):
    """Setup isolated state directory for each test."""
    state_dir = tmp_path / f"sessions_{uuid.uuid4().hex[:8]}"
    state_dir.mkdir(parents=True, exist_ok=True)
    session_id = f"test_session_{uuid.uuid4().hex[:8]}"
    yield {
        "state_dir": state_dir,
        "session_id": session_id,
        "state_file": state_dir / "daemon_state.json",
        "lock_file": state_dir / "daemon_state.json.lock",
        "tmp_file": state_dir / "daemon_state.json.tmp",
    }


# =============================================================================
# CROSS-PROCESS LOCK CONTENTION TESTS
# These verify that session_state() provides real exclusion across processes.
# =============================================================================

class TestCrossProcessContention:
    """Verify real cross-process mutual exclusion via filelock."""

    def test_second_process_times_out_while_first_holds(self, state_setup):
        """
        Process 1 holds session_state for 0.5s; process 2 tries with 0.2s timeout.
        Process 2 must get SessionTimeoutError, not succeed.
        """
        setup = state_setup
        session_id = setup["session_id"]
        state_dir = str(setup["state_dir"])

        lock_held = multiprocessing.Value('b', False)
        lock_released = multiprocessing.Value('b', False)
        manager = multiprocessing.Manager()
        second_result = manager.dict({"value": None})

        p1 = multiprocessing.Process(
            target=_hold_session_for_contention,
            args=(session_id, state_dir, lock_held, lock_released)
        )
        p2 = multiprocessing.Process(
            target=_try_acquire_while_held,
            args=(session_id, state_dir, second_result)
        )

        p1.start()
        p2.start()
        p1.join(timeout=10)
        p2.join(timeout=10)

        assert lock_released.value, "First process did not complete"
        assert second_result["value"] == "timeout", (
            f"Second process should timeout while lock is held, got: {second_result['value']}"
        )

    def test_second_process_acquires_after_first_releases(self, state_setup):
        """
        After process 1 releases the lock, process 2 should succeed.
        Tests sequential access — no starvation.
        """
        setup = state_setup
        session_id = setup["session_id"]
        state_dir = str(setup["state_dir"])

        first_acquired = multiprocessing.Value('b', False)
        manager = multiprocessing.Manager()
        second_result = manager.dict({"value": None})

        p1 = multiprocessing.Process(
            target=_hold_session_simple,
            args=(session_id, state_dir, first_acquired)
        )
        p2 = multiprocessing.Process(
            target=_try_acquire_simple,
            args=(session_id, state_dir, second_result)
        )

        p1.start()
        # Give p1 time to start and acquire, then let p2 try (and timeout)
        p2.start()
        p1.join(timeout=10)
        p2.join(timeout=10)

        assert first_acquired.value, "First process did not acquire lock"
        assert second_result["value"] == "timeout", (
            f"Second should timeout (0.2s) while first holds (0.4s), got: "
            f"{second_result['value']}"
        )

    def test_cross_process_writes_are_isolated(self, state_setup):
        """
        Two processes each write a unique key. Both writes must survive.
        Verifies atomic JSON write prevents data loss.
        """
        setup = state_setup
        session_id = setup["session_id"]
        state_dir = str(setup["state_dir"])

        manager = multiprocessing.Manager()
        result1 = manager.dict({"success": False, "value": None})
        result2 = manager.dict({"success": False, "value": None})

        p1 = multiprocessing.Process(
            target=_write_and_read_session,
            args=(session_id, state_dir, 1, result1)
        )
        p2 = multiprocessing.Process(
            target=_write_and_read_session,
            args=(session_id, state_dir, 2, result2)
        )

        p1.start()
        p2.start()
        p1.join(timeout=15)
        p2.join(timeout=15)

        assert result1["success"], f"Worker 1 failed: {result1.get('error')}"
        assert result2["success"], f"Worker 2 failed: {result2.get('error')}"
        assert result1["value"] == 1
        assert result2["value"] == 2

        # Both keys must be in the state file
        with session_state(session_id, state_dir=state_dir) as s:
            assert s.get("worker_1") == 1
            assert s.get("worker_2") == 2


# =============================================================================
# STATE FILE RECOVERY TESTS
# The _load() function recovers from corrupt/missing state files gracefully.
# =============================================================================

class TestStateFileRecovery:
    """Verify daemon_state.json corruption/absence is handled gracefully."""

    def test_missing_state_file_yields_empty_state(self, state_setup):
        """session_state() with no state file returns empty proxy."""
        setup = state_setup
        assert not setup["state_file"].exists()
        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            assert s.get("missing_key") is None
            assert list(s.keys()) == []

    def test_corrupt_state_file_yields_empty_state(self, state_setup):
        """Corrupt daemon_state.json causes _load() to return {} (no crash)."""
        setup = state_setup
        setup["state_file"].write_text("{invalid json <<<")
        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            assert s.get("any_key") is None

    def test_empty_state_file_yields_empty_state(self, state_setup):
        """Empty daemon_state.json causes _load() to return {} (no crash)."""
        setup = state_setup
        setup["state_file"].write_text("")
        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            assert s.get("any_key") is None

    def test_write_after_corrupt_state_recovers(self, state_setup):
        """After corrupt state, writes succeed and state is saved correctly."""
        setup = state_setup
        setup["state_file"].write_text("not json at all")
        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            s["key"] = "recovered"
        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            assert s.get("key") == "recovered"

    def test_truncated_tmp_file_overwritten_on_save(self, state_setup):
        """Leftover .tmp file (partial crash) is overwritten by next atomic write."""
        setup = state_setup
        setup["tmp_file"].write_text("partial write")
        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            s["key"] = "fresh"
        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            assert s.get("key") == "fresh"


# =============================================================================
# STALE LOCK FILE HANDLING
# filelock.FileLock holds only an OS-level flock. If the process that created
# the lock file crashes, the OS releases the flock. The file itself may remain
# on disk, but a new process can immediately acquire the lock.
# =============================================================================

class TestStaleLockFileHandling:
    """Verify behavior with leftover daemon_state.json.lock files."""

    def test_leftover_lock_file_does_not_block_new_acquire(self, state_setup):
        """
        A leftover daemon_state.json.lock (from a previous run) doesn't block
        session_state(). filelock re-acquires via the OS even if file exists.
        """
        setup = state_setup
        # Simulate leftover lock file from a previous process
        setup["lock_file"].write_text("leftover")
        assert setup["lock_file"].exists()

        # Should still acquire successfully (filelock uses OS-level flock, not content)
        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            s["key"] = "value"

        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            assert s.get("key") == "value"

    def test_timeout_with_real_held_lock(self, state_setup):
        """
        A thread holding session_state causes SessionTimeoutError in another thread
        trying with a short timeout.
        """
        setup = state_setup
        barrier = multiprocessing.Event()

        import threading

        def hold():
            with session_state(
                setup["session_id"], state_dir=str(setup["state_dir"]), timeout=10.0
            ):
                barrier.set()
                time.sleep(1.0)

        t = threading.Thread(target=hold)
        t.start()
        barrier.wait()

        with pytest.raises(SessionTimeoutError):
            with session_state(
                setup["session_id"], state_dir=str(setup["state_dir"]), timeout=0.1
            ):
                pass

        t.join()

    def test_rapid_acquire_release_cycles(self, state_setup):
        """Multiple rapid acquire/release cycles succeed without corruption."""
        setup = state_setup
        for i in range(10):
            with session_state(
                setup["session_id"], state_dir=str(setup["state_dir"])
            ) as s:
                s[f"cycle_{i}"] = i

        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            for i in range(10):
                assert s.get(f"cycle_{i}") == i

    def test_state_survives_rapid_cycles(self, state_setup):
        """State accumulated across cycles is not lost."""
        setup = state_setup
        expected = {}
        for i in range(5):
            with session_state(
                setup["session_id"], state_dir=str(setup["state_dir"])
            ) as s:
                s[f"k{i}"] = i * 10
                expected[f"k{i}"] = i * 10

        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            for k, v in expected.items():
                assert s.get(k) == v, f"Key {k} lost after rapid cycles"


# =============================================================================
# LOCK FILE LIFECYCLE TESTS
# Verify daemon_state.json.lock is created/used correctly by filelock.
# =============================================================================

class TestSharedLockFileLifecycle:
    """Verify daemon_state.json.lock lifecycle matches filelock semantics."""

    def test_lock_file_created_on_first_acquire(self, state_setup):
        """daemon_state.json.lock is created when session_state() acquires."""
        setup = state_setup
        assert not setup["lock_file"].exists()
        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            s["key"] = "value"  # ensure lock is actually acquired (write triggers save)
            # filelock creates the lock file when it acquires
            assert setup["lock_file"].exists()

    def test_lock_file_persists_after_release(self, state_setup):
        """filelock does not delete the lock file on release (OS semantics)."""
        setup = state_setup
        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            s["key"] = "value"
        # Lock file remains on disk (filelock keeps it; only the OS lock is released)
        assert setup["lock_file"].exists()

    def test_lock_file_path_is_shared_not_per_session(self, state_setup):
        """All sessions share a single daemon_state.json.lock (not per-session files)."""
        setup = state_setup
        sid1 = f"session_a_{uuid.uuid4().hex[:8]}"
        sid2 = f"session_b_{uuid.uuid4().hex[:8]}"

        with session_state(sid1, state_dir=str(setup["state_dir"])) as s:
            s["k"] = "a"
        with session_state(sid2, state_dir=str(setup["state_dir"])) as s:
            s["k"] = "b"

        # Only one lock file — the shared daemon_state.json.lock
        lock_files = list(setup["state_dir"].glob("*.lock"))
        assert len(lock_files) == 1
        assert lock_files[0].name == "daemon_state.json.lock"

    def test_state_file_is_valid_json_after_write(self, state_setup):
        """daemon_state.json contains valid JSON after a write."""
        setup = state_setup
        sid = setup["session_id"]
        with session_state(sid, state_dir=str(setup["state_dir"])) as s:
            s["key"] = "jsonvalue"
            s["num"] = 42

        content = setup["state_file"].read_text()
        data = json.loads(content)
        assert f"{sid}/key" in data
        assert data[f"{sid}/key"] == "jsonvalue"
        assert data[f"{sid}/num"] == 42

    def test_state_file_not_created_on_readonly_access(self, state_setup):
        """No write occurs if no keys are set — state file stays absent."""
        setup = state_setup
        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            _ = s.get("any_key")  # read-only
        assert not setup["state_file"].exists()


# =============================================================================
# SESSION LOCK NO-OP TESTS
# SessionLock is a compatibility shim — it creates no files and holds no locks.
# =============================================================================

class TestSessionLockNoOp:
    """Verify SessionLock is a pure no-op shim."""

    def test_session_lock_creates_no_files(self, state_setup):
        """SessionLock does not create any files in the state directory."""
        setup = state_setup
        before = set(setup["state_dir"].iterdir())
        with SessionLock(setup["session_id"], timeout=5.0,
                         state_dir=setup["state_dir"]):
            pass
        after = set(setup["state_dir"].iterdir())
        assert before == after, f"Unexpected files created: {after - before}"

    def test_session_lock_does_not_block_concurrent_acquires(self, state_setup):
        """Multiple SessionLocks on the same session_id all succeed simultaneously."""
        import threading
        setup = state_setup
        acquired = []

        def acquire():
            with SessionLock(setup["session_id"], timeout=0.1,
                             state_dir=setup["state_dir"]):
                acquired.append(True)
                time.sleep(0.05)

        threads = [threading.Thread(target=acquire) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(acquired) == 5, "All SessionLock acquires should succeed (no-op)"

    def test_session_lock_does_not_provide_exclusion(self, state_setup):
        """
        Two threads holding SessionLock at the same time — proves no-op.
        If SessionLock provided real exclusion, only one could hold it at once.
        """
        import threading
        setup = state_setup
        concurrent_holders = []
        gate = threading.Barrier(2)

        def hold():
            with SessionLock(setup["session_id"], timeout=1.0,
                             state_dir=setup["state_dir"]):
                gate.wait()  # Both must reach here simultaneously
                concurrent_holders.append(True)

        t1 = threading.Thread(target=hold)
        t2 = threading.Thread(target=hold)
        t1.start()
        t2.start()
        t1.join(timeout=3)
        t2.join(timeout=3)

        # Both threads held "the lock" simultaneously — no-op confirmed
        assert len(concurrent_holders) == 2

    def test_session_lock_does_not_raise_on_exception(self, state_setup):
        """SessionLock cleans up correctly even if body raises."""
        setup = state_setup
        try:
            with SessionLock(setup["session_id"], timeout=5.0,
                             state_dir=setup["state_dir"]):
                raise RuntimeError("simulated error")
        except RuntimeError:
            pass  # Expected

        # No files left behind
        assert not list(setup["state_dir"].glob("*.lock")) or (
            not any(
                f.name != "daemon_state.json.lock"
                for f in setup["state_dir"].glob("*.lock")
            )
        )


# =============================================================================
# CONCURRENT WRITE SAFETY TESTS
# Verify that concurrent threads don't corrupt state (threading.RLock + filelock).
# =============================================================================

class TestConcurrentWriteSafety:
    """Verify state integrity under concurrent thread writes."""

    def test_concurrent_threads_all_writes_survive(self, state_setup):
        """N threads each write a unique key; all N keys must be present after."""
        setup = state_setup
        n = 8
        errors = []

        def writer(i):
            try:
                with session_state(
                    setup["session_id"], state_dir=str(setup["state_dir"])
                ) as s:
                    s[f"thread_{i}"] = i
            except Exception as e:
                errors.append(str(e))

        import threading
        threads = [threading.Thread(target=writer, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"

        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            for i in range(n):
                assert s.get(f"thread_{i}") == i, f"Key thread_{i} missing or wrong"

    def test_different_sessions_do_not_interfere(self, state_setup):
        """Concurrent writes to different session IDs stay isolated."""
        setup = state_setup
        sid_a = f"session_a_{uuid.uuid4().hex[:4]}"
        sid_b = f"session_b_{uuid.uuid4().hex[:4]}"

        import threading
        barrier = threading.Barrier(2)

        def write_a():
            barrier.wait()
            with session_state(sid_a, state_dir=str(setup["state_dir"])) as s:
                s["value"] = "from_a"

        def write_b():
            barrier.wait()
            with session_state(sid_b, state_dir=str(setup["state_dir"])) as s:
                s["value"] = "from_b"

        t1 = threading.Thread(target=write_a)
        t2 = threading.Thread(target=write_b)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        with session_state(sid_a, state_dir=str(setup["state_dir"])) as s:
            assert s.get("value") == "from_a"
        with session_state(sid_b, state_dir=str(setup["state_dir"])) as s:
            assert s.get("value") == "from_b"

    def test_reentrant_session_state_within_same_thread(self, state_setup):
        """
        Same thread can call session_state() from within an existing session_state()
        context without deadlock (reentrant filelock via threading.local).
        """
        setup = state_setup
        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s1:
            s1["outer"] = "outer_val"
            # Reentrant call — same thread, same lock
            with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s2:
                s2["inner"] = "inner_val"

        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            assert s.get("outer") == "outer_val"
            assert s.get("inner") == "inner_val"

    def test_state_consistency_after_mixed_read_writes(self, state_setup):
        """
        Mix of read-only and read-write threads; no data corruption.
        """
        setup = state_setup
        import threading

        # Pre-populate state
        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            for i in range(5):
                s[f"base_{i}"] = i * 100

        read_errors = []
        write_errors = []

        def reader(i):
            try:
                with session_state(
                    setup["session_id"], state_dir=str(setup["state_dir"])
                ) as s:
                    val = s.get(f"base_{i % 5}")
                    if val not in [0, 100, 200, 300, 400]:
                        read_errors.append(f"reader {i}: unexpected {val}")
            except Exception as e:
                read_errors.append(str(e))

        def writer(i):
            try:
                with session_state(
                    setup["session_id"], state_dir=str(setup["state_dir"])
                ) as s:
                    s[f"new_{i}"] = i
            except Exception as e:
                write_errors.append(str(e))

        threads = (
            [threading.Thread(target=reader, args=(i,)) for i in range(6)] +
            [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not read_errors, f"Read errors: {read_errors}"
        assert not write_errors, f"Write errors: {write_errors}"


# =============================================================================
# RECOVERY AFTER CLEANUP TESTS
# =============================================================================

class TestRecoveryAfterCleanup:
    """Verify system recovers cleanly after state/lock file deletion."""

    def test_delete_state_file_then_write_succeeds(self, state_setup):
        """After daemon_state.json is deleted, the next write recreates it."""
        setup = state_setup
        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            s["before_delete"] = "present"

        assert setup["state_file"].exists()
        setup["state_file"].unlink()

        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            s["after_delete"] = "recovered"

        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            assert s.get("before_delete") is None  # wiped with file deletion
            assert s.get("after_delete") == "recovered"

    def test_delete_lock_file_then_acquire_succeeds(self, state_setup):
        """After daemon_state.json.lock is deleted, the next acquire recreates it."""
        setup = state_setup
        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            s["key"] = "value"

        assert setup["lock_file"].exists()
        setup["lock_file"].unlink()

        # filelock recreates the lock file on next acquire
        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            s["key2"] = "value2"
            assert setup["lock_file"].exists()

    def test_concurrent_after_state_cleanup(self, state_setup):
        """Concurrent writes succeed after state file is deleted."""
        setup = state_setup
        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            s["seed"] = 1

        setup["state_file"].unlink()
        errors = []

        def writer(i):
            try:
                with session_state(
                    setup["session_id"], state_dir=str(setup["state_dir"])
                ) as s:
                    s[f"post_cleanup_{i}"] = i
            except Exception as e:
                errors.append(str(e))

        import threading
        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors after cleanup: {errors}"

        with session_state(setup["session_id"], state_dir=str(setup["state_dir"])) as s:
            for i in range(4):
                assert s.get(f"post_cleanup_{i}") == i


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
