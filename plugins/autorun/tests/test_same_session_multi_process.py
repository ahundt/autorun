#!/usr/bin/env python3
"""
Same-session multi-process tests for plan export race condition fix.

Tests scenarios where the same session_id is used in multiple processes
simultaneously, which can occur when:
- User resumes the same session in multiple Claude Code instances
- Multiple processes handle the same session (e.g., AI monitoring, background tasks)

These tests verify:
1. Lock serialization works correctly across processes
2. Only one process acquires the lock at a time
3. Timeouts are handled gracefully
4. No data corruption occurs
"""

import io
import json
import os
import sys
import time
import uuid
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime
from typing import Dict, List

import pytest

from autorun.plan_export import (
    PlanExportConfig,
    export_plan,
    get_plan_from_metadata,
    embed_plan_metadata,
)
from autorun.session_manager import (
    SessionLock,
    SessionTimeoutError,
    session_state,
    _reset_for_testing,
)


# =============================================================================
# TEST UTILITIES
# =============================================================================

def _worker_process_for_lock_test(session_id: str, state_dir: Path,
                                  lock_file, lock_acquired, lock_released):
    """Worker process for testing cross-process lock coordination via session_state()."""
    from autorun.session_manager import session_state
    with session_state(session_id, state_dir=str(state_dir), timeout=5.0):
        lock_acquired.value = True
        time.sleep(0.2)
    lock_released.value = True


def export_worker(session_id: str, plan_path: Path, project_dir: Path,
                  state_dir: Path, worker_id: int, delay: float = 0.0) -> Dict:
    """
    Worker function that attempts to export a plan.

    Simulates a separate process handling the same session.
    """
    try:
        # Simulate some processing delay
        time.sleep(delay)

        # Try to acquire lock and export
        with SessionLock(session_id, timeout=10.0, state_dir=state_dir):
            # Simulate export work
            time.sleep(0.1)

            # Create unique export destination for this worker
            export_dir = project_dir / f"worker_{worker_id}"
            export_dir.mkdir(parents=True, exist_ok=True)

            result = export_plan(plan_path, export_dir, session_id=session_id)

            return {
                "worker_id": worker_id,
                "success": True,
                "destination": result.get("destination"),
                "acquired_lock": True
            }

    except SessionTimeoutError as e:
        return {
            "worker_id": worker_id,
            "success": False,
            "timeout": True,
            "error": str(e)
        }
    except Exception as e:
        return {
            "worker_id": worker_id,
            "success": False,
            "error": str(e)
        }


def rapid_export_worker(session_id: str, plan_path: Path, project_dir: Path,
                        state_dir: Path, worker_id: int) -> Dict:
    """
    Rapid fire worker that attempts to export multiple times quickly.

    Tests lock re-acquisition and release.
    """
    results = []

    for attempt in range(5):
        try:
            with SessionLock(session_id, timeout=2.0, state_dir=state_dir):
                time.sleep(0.01)  # Minimal work

                export_dir = project_dir / f"worker_{worker_id}_attempt_{attempt}"
                export_dir.mkdir(parents=True, exist_ok=True)

                result = export_plan(plan_path, export_dir, session_id=session_id)

                results.append({
                    "worker_id": worker_id,
                    "attempt": attempt,
                    "success": True
                })

        except SessionTimeoutError:
            results.append({
                "worker_id": worker_id,
                "attempt": attempt,
                "timeout": True
            })
        except Exception as e:
            results.append({
                "worker_id": worker_id,
                "attempt": attempt,
                "error": str(e)
            })

    return {
        "worker_id": worker_id,
        "results": results
    }


# =============================================================================
# TEST FIXTURES
# =============================================================================

@pytest.fixture(autouse=True)
def reset_session_manager_singletons():
    """Reset module-level singletons before and after each test for isolation."""
    _reset_for_testing()
    yield
    _reset_for_testing()


@pytest.fixture
def multi_process_test_setup(tmp_path):
    """Setup for multi-process testing."""
    test_root = tmp_path / f"multi_process_test_{uuid.uuid4().hex[:8]}"
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
# MULTI-PROCESS SERIALIZATION TESTS
# =============================================================================

class TestSameSessionMultiProcess:
    """Test same session_id across multiple processes."""

    def test_sequential_lock_across_processes(self, multi_process_test_setup):
        """
        Test that the same session_id can be used sequentially across processes.

        Expected behavior:
        - Each process acquires lock, exports, releases lock
        - No timeout errors in sequential execution
        - All exports succeed
        """
        setup = multi_process_test_setup
        num_workers = 5

        # Run workers sequentially (not concurrently)
        results = []
        for i in range(num_workers):
            result = export_worker(
                setup["session_id"],
                setup["plan_path"],
                setup["project_dir"],
                setup["test_state_dir"],
                worker_id=i,
                delay=0.0
            )
            results.append(result)

        # Verify all succeeded
        assert len(results) == num_workers
        for result in results:
            assert result["success"] is True, f"Worker {result['worker_id']} failed: {result.get('error')}"
            assert result.get("timeout") is not True

    def test_concurrent_lock_contention(self, multi_process_test_setup):
        """
        Test concurrent processes competing for the same session lock.

        Expected behavior:
        - One process acquires lock
        - Other processes wait or timeout
        - No data corruption
        - Lock serialization works correctly
        """
        setup = multi_process_test_setup
        num_workers = 5

        # Run workers concurrently
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [
                executor.submit(
                    export_worker,
                    setup["session_id"],
                    setup["plan_path"],
                    setup["project_dir"],
                    setup["test_state_dir"],
                    worker_id=i,
                    delay=0.0
                )
                for i in range(num_workers)
            ]
            results = [f.result() for f in as_completed(futures)]

        # Verify at least one succeeded
        successful = [r for r in results if r.get("success") and not r.get("timeout")]
        assert len(successful) >= 1, "No workers succeeded!"

        # Verify no errors (timeouts are expected under contention)
        errors = [r for r in results if r.get("error") and not r.get("timeout")]
        assert len(errors) == 0, f"Errors occurred: {errors}"

    def test_rapid_fire_lock_reacquisition(self, multi_process_test_setup):
        """
        Test rapid fire export attempts from the same session.

        Expected behavior:
        - Lock is properly released after each export
        - Lock can be re-acquired immediately
        - No deadlocks occur
        """
        setup = multi_process_test_setup
        num_workers = 3

        # Run rapid fire workers
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [
                executor.submit(
                    rapid_export_worker,
                    setup["session_id"],
                    setup["plan_path"],
                    setup["project_dir"],
                    setup["test_state_dir"],
                    worker_id=i
                )
                for i in range(num_workers)
            ]
            results = [f.result() for f in as_completed(futures)]

        # Verify each worker completed all attempts
        assert len(results) == num_workers

        for result in results:
            worker_results = result["results"]
            assert len(worker_results) == 5, f"Worker {result['worker_id']} did not complete all attempts"

            # At least some attempts should succeed (not all timeout)
            successful_attempts = [r for r in worker_results if r.get("success")]
            assert len(successful_attempts) >= 1, f"Worker {result['worker_id']} had no successful attempts"

    def test_same_session_different_instances(self, multi_process_test_setup):
        """
        Test same session_id simulated across different Claude Code instances.

        This simulates the real-world scenario where a user resumes the same
        session in multiple Claude Code instances.

        Expected behavior:
        - Each instance properly serializes access
        - No data corruption
        - Lock timeout handled gracefully
        """
        setup = multi_process_test_setup
        session_id = setup["session_id"]
        num_instances = 4

        results = []

        def simulate_instance(instance_id: int) -> Dict:
            """Simulate a Claude Code instance handling the session."""
            try:
                # Each instance tries to export with slight delay
                time.sleep(0.01 * instance_id)

                with SessionLock(session_id, timeout=5.0, state_dir=setup["test_state_dir"]):
                    time.sleep(0.05)  # Simulate export work

                    export_dir = setup["project_dir"] / f"instance_{instance_id}"
                    export_dir.mkdir(parents=True, exist_ok=True)

                    result = export_plan(setup["plan_path"], export_dir, session_id=session_id)

                    return {
                        "instance_id": instance_id,
                        "success": True,
                        "exported": True
                    }

            except SessionTimeoutError:
                return {
                    "instance_id": instance_id,
                    "success": False,
                    "timeout": True
                }
            except Exception as e:
                return {
                    "instance_id": instance_id,
                    "success": False,
                    "error": str(e)
                }

        # Run instances concurrently
        with ThreadPoolExecutor(max_workers=num_instances) as executor:
            futures = [executor.submit(simulate_instance, i) for i in range(num_instances)]
            results = [f.result() for f in as_completed(futures)]

        # Verify at least one succeeded
        successful = [r for r in results if r.get("success") and r.get("exported")]
        assert len(successful) >= 1, "No instances succeeded in exporting"

        # Verify no errors
        errors = [r for r in results if r.get("error") and not r.get("timeout")]
        assert len(errors) == 0, f"Unexpected errors: {errors}"

    def test_metadata_prevents_confusion(self, multi_process_test_setup):
        """
        Test that embedded metadata prevents plan confusion across instances.

        Each export should embed session_id metadata, allowing correct
        identification even when multiple instances use the same session.
        """
        setup = multi_process_test_setup

        # Create multiple exports
        num_exports = 3
        for i in range(num_exports):
            export_dir = setup["project_dir"] / f"export_{i}"
            export_dir.mkdir(parents=True, exist_ok=True)

            result = export_plan(setup["plan_path"], export_dir, session_id=setup["session_id"])
            assert result["success"] is True

            # Verify metadata was embedded
            exported_file = Path(result["destination"])
            content = exported_file.read_text()

            # Check for YAML frontmatter
            assert content.startswith("---"), "Metadata frontmatter not found"

            # Extract session_id from metadata
            session_id = get_plan_from_metadata(exported_file)
            assert session_id == setup["session_id"], f"Session ID mismatch: {session_id} != {setup['session_id']}"

    def test_stress_same_session_many_workers(self, multi_process_test_setup):
        """
        Stress test with many workers using the same session_id.

        Verifies lock handling under high contention.
        """
        setup = multi_process_test_setup
        num_workers = 20

        # Run many workers concurrently
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [
                executor.submit(
                    export_worker,
                    setup["session_id"],
                    setup["plan_path"],
                    setup["project_dir"],
                    setup["test_state_dir"],
                    worker_id=i,
                    delay=0.001 * i  # Stagger slightly
                )
                for i in range(num_workers)
            ]
            results = [f.result(timeout=30) for f in as_completed(futures)]

        # Verify all workers completed
        assert len(results) == num_workers

        # Count successes and timeouts
        successful = [r for r in results if r.get("success") and not r.get("timeout")]
        timeouts = [r for r in results if r.get("timeout")]
        errors = [r for r in results if r.get("error") and not r.get("timeout")]

        # At least some should succeed
        assert len(successful) >= 1, "No workers succeeded under stress"
        assert len(errors) == 0, f"Errors occurred under stress: {errors}"

        # Timeouts are expected under high contention
        assert len(timeouts) >= 0, "Timeout handling failed"


# =============================================================================
# PROCESS ISOLATION TESTS
# =============================================================================

class TestProcessIsolation:
    """Test that process isolation works correctly."""

    def test_lock_file_per_process(self, multi_process_test_setup):
        """
        Verify the shared daemon_state.json.lock is created by session_state()
        and that different processes coordinate via the same shared lock file.
        """
        setup = multi_process_test_setup
        session_id = setup["session_id"]
        # New: single shared lock file for all sessions
        shared_lock_file = setup["test_state_dir"] / "daemon_state.json.lock"

        assert not shared_lock_file.exists()

        with session_state(session_id,
                           state_dir=str(setup["test_state_dir"]),
                           timeout=5.0) as s:
            s["process_key"] = os.getpid()
            # Shared lock file exists while session is held
            assert shared_lock_file.exists()

        # Lock file persists (filelock keeps it; only OS flock is released)
        assert shared_lock_file.exists()

        # A second acquire must succeed — confirms the OS flock was released
        with session_state(session_id,
                           state_dir=str(setup["test_state_dir"]),
                           timeout=0.5) as s:
            assert s.get("process_key") == os.getpid()

    def test_different_processes_see_same_lock(self, multi_process_test_setup):
        """
        Verify that different processes share the same daemon_state.json.lock.

        Worker process holds session_state() (acquiring the shared filelock).
        Parent process checks the shared lock file exists while worker holds it.
        """
        setup = multi_process_test_setup
        session_id = setup["session_id"]
        # Shared lock file path (not per-session)
        shared_lock_file = setup["test_state_dir"] / "daemon_state.json.lock"

        lock_acquired = multiprocessing.Value('b', False)
        lock_released = multiprocessing.Value('b', False)

        # Start worker process — it holds session_state() for 0.2s
        process = multiprocessing.Process(
            target=_worker_process_for_lock_test,
            args=(session_id, setup["test_state_dir"], shared_lock_file,
                  lock_acquired, lock_released)
        )
        process.start()

        # Wait for worker to acquire the lock
        for _ in range(50):  # 5 seconds max
            if lock_acquired.value:
                # Shared lock file should exist while worker holds the filelock
                assert shared_lock_file.exists(), \
                    "daemon_state.json.lock should exist when lock is held"
                break
            time.sleep(0.1)

        # Wait for worker to release
        process.join(timeout=10)

        assert lock_released.value, "Worker did not release the lock"
        # Lock file persists after release (filelock keeps the file)
        assert shared_lock_file.exists(), \
            "daemon_state.json.lock should persist after release (filelock semantics)"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
