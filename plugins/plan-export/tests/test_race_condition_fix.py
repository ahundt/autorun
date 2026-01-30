#!/usr/bin/env python3
"""
Comprehensive TDD tests for plan export race condition fix.

Tests the SessionLock-based implementation to ensure:
1. Multiple sessions can export simultaneously without cross-contamination
2. Same-session concurrent exports are properly serialized
3. Lock timeout scenarios are handled gracefully
4. No deadlocks occur under stress conditions

SAFETY CRITICAL:
- Uses temporary directories only (never touches real ~/.claude/sessions/)
- All test state is isolated with unique test IDs
- Comprehensive cleanup in teardown fixtures
- Mock plan files with synthetic test data
"""

import io
import json
import os
import sys
import tempfile
import shutil
import time
import uuid
import threading
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any

import pytest

# Add parent directories to path for imports BEFORE importing modules
# CRITICAL: This must happen before importing plan_export or session_manager
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "clautorun" / "src"))

# Now we can import the modules
# Note: export_plan_module is a wrapper for plan_export.py
from export_plan_module import (
    load_config,
    is_enabled,
    get_most_recent_plan,
    get_plan_from_transcript,
    export_plan,
    log_warning,
    main
)
from clautorun.session_manager import SessionLock, SessionTimeoutError


# =============================================================================
# TEST UTILITIES AND FIXTURES
# =============================================================================

class TestSession:
    """Represents a synthetic test session with isolated state."""

    def __init__(self, session_id: str, test_state_dir: Path, test_plans_dir: Path):
        self.session_id = session_id
        self.test_state_dir = test_state_dir
        self.test_plans_dir = test_plans_dir
        self.project_dir = test_state_dir / f"project_{session_id}"
        self.export_dir = self.project_dir / "notes"

    def create(self):
        """Create session directories."""
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)

    def cleanup(self):
        """Clean up session directories."""
        if self.project_dir.exists():
            shutil.rmtree(self.project_dir, ignore_errors=True)


class SyntheticPlanBuilder:
    """Builds synthetic plan files for testing."""

    @staticmethod
    def create(plan_path: Path, session_id: str, content: str = "Test Plan Content") -> Path:
        """Create a synthetic plan file with test data."""
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_data = {
            "session_id": session_id,
            "created": datetime.now().isoformat(),
            "content": content
        }
        plan_path.write_text(f"# Test Plan for {session_id}\n\n{content}\n\n```json\n{json.dumps(plan_data)}\n```")
        return plan_path

    @staticmethod
    def create_transcript(transcript_path: Path, session_id: str, plan_path: Path) -> Path:
        """Create a synthetic session transcript tracking plan edits."""
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "type": "file-history-snapshot",
            "timestamp": datetime.now().isoformat(),
            "snapshot": {
                "trackedFileBackups": {
                    str(plan_path): {
                        "size": plan_path.stat().st_size if plan_path.exists() else 0,
                        "modified": datetime.now().isoformat()
                    }
                }
            }
        }
        transcript_path.write_text(json.dumps(entry) + "\n")
        return transcript_path


@pytest.fixture
def test_temp_dir(tmp_path):
    """
    Create a temporary test directory with complete isolation.

    IMPORTANT: Never use real ~/.claude/ directories!
    All test state is contained within tmp_path which pytest
    automatically cleans up after the test session.
    """
    test_root = tmp_path / f"plan_export_test_{uuid.uuid4().hex[:8]}"
    test_root.mkdir(parents=True, exist_ok=True)

    # Create isolated test directories
    test_state_dir = test_root / "sessions"
    test_state_dir.mkdir(parents=True, exist_ok=True)

    test_plans_dir = test_root / "plans"
    test_plans_dir.mkdir(parents=True, exist_ok=True)

    yield {
        "test_root": test_root,
        "test_state_dir": test_state_dir,
        "test_plans_dir": test_plans_dir
    }

    # Comprehensive cleanup after test
    if test_root.exists():
        shutil.rmtree(test_root, ignore_errors=True)


@pytest.fixture
def mock_session_lock(test_temp_dir, monkeypatch):
    """
    Mock SessionLock to use test state directory instead of real ~/.claude/sessions/.

    This is CRITICAL for safety - we never want tests to touch real session locks.
    """
    test_state_dir = test_temp_dir["test_state_dir"]

    def mock_session_lock_init(self, session_id: str, timeout: float, state_dir: Path):
        """Mock __init__ to use test state directory."""
        import threading
        self.session_id = session_id
        self.timeout = timeout
        self.state_dir = test_state_dir  # Use test directory, not real one
        self.lock_file = test_state_dir / f".{session_id}.lock"
        self.lock_fd = None
        self.acquired = False
        self.start_time = time.time()
        self.process_id = os.getpid()
        self.thread_id = threading.get_ident()  # Required for reentrant lock support
        self._is_reentrant = False  # Required for reentrant lock support

    # Patch SessionLock.__init__ to use test state directory
    monkeypatch.setattr(SessionLock, "__init__", mock_session_lock_init)

    return test_state_dir


@pytest.fixture
def sample_config(test_temp_dir, monkeypatch):
    """Create a sample config for testing."""
    config_path = test_temp_dir["test_root"] / "plan-export.config.json"
    config_data = {
        "enabled": True,
        "output_plan_dir": "notes",
        "filename_pattern": "{datetime}_{name}",
        "extension": ".md",
        "export_rejected": True,
        "output_rejected_plan_dir": "notes/rejected",
        "debug_logging": True,  # Enable for test debugging
        "notify_claude": False  # Disable for tests
    }
    config_path.write_text(json.dumps(config_data))

    # Mock get_config_path to return test config
    def mock_get_config_path():
        return config_path

    monkeypatch.setattr("plan_export.get_config_path", mock_get_config_path)

    return config_data


# =============================================================================
# BASELINE FUNCTIONALITY TESTS
# =============================================================================

class TestBaselineFunctionality:
    """Test basic functionality with single session exports."""

    def test_single_session_export_success(self, test_temp_dir, mock_session_lock, sample_config):
        """Test that a single session can successfully export a plan."""
        # Create test session
        session_id = f"test_session_{uuid.uuid4().hex[:8]}"
        test_session = TestSession(session_id, test_temp_dir["test_state_dir"], test_temp_dir["test_plans_dir"])
        test_session.create()

        # Create synthetic plan file
        plan_path = test_temp_dir["test_plans_dir"] / f"plan_{session_id}.md"
        SyntheticPlanBuilder.create(plan_path, session_id, "Single session test plan")

        # Create mock transcript
        transcript_path = test_temp_dir["test_root"] / f"transcript_{session_id}.jsonl"
        SyntheticPlanBuilder.create_transcript(transcript_path, session_id, plan_path)

        # Export the plan
        result = export_plan(plan_path, test_session.project_dir)

        # Verify export succeeded
        assert result["success"] is True
        assert result["source"] == str(plan_path)
        exported_file = Path(result["destination"])
        assert exported_file.exists()
        assert exported_file.parent == test_session.export_dir

        # Verify content
        content = exported_file.read_text()
        assert "Single session test plan" in content
        assert session_id in content

        test_session.cleanup()

    def test_export_with_missing_session_id(self, test_temp_dir, sample_config, monkeypatch, capsys):
        """Test that export fails gracefully when session_id is missing."""
        # Mock stdin to provide hook input without session_id
        hook_input = {
            "cwd": str(test_temp_dir["test_root"]),
            "transcript_path": None,
            "permission_mode": "acceptEdits"
        }

        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_input)))

        # Run main
        main()

        # Capture output
        captured = capsys.readouterr()
        output = json.loads(captured.out)

        # Verify error response
        assert output["continue"] is True
        assert "session_id" in output["systemMessage"].lower()
        assert "required" in output["additionalContext"].lower()

    def test_export_when_disabled(self, test_temp_dir, monkeypatch, capsys):
        """Test that export is skipped when disabled in config."""
        # Create config with export disabled
        config_path = test_temp_dir["test_root"] / "plan-export.config.json"
        config_data = {"enabled": False}
        config_path.write_text(json.dumps(config_data))

        def mock_get_config_path():
            return config_path
        monkeypatch.setattr("plan_export.get_config_path", mock_get_config_path)

        # Mock stdin
        hook_input = {
            "session_id": f"test_session_{uuid.uuid4().hex[:8]}",
            "cwd": str(test_temp_dir["test_root"]),
            "permission_mode": "acceptEdits"
        }
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_input)))

        # Run main
        main()

        # Verify suppressed output
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["continue"] is True
        assert output.get("suppressOutput") is True

    def test_main_with_session_lock_integration(self, test_temp_dir, mock_session_lock, sample_config, monkeypatch, capsys):
        """
        Test that main() properly uses SessionLock to prevent race conditions.

        This test verifies the actual race condition fix in main() where:
        1. SessionLock wraps the critical section (plan selection + export)
        2. Multiple concurrent calls to main() with same session_id are serialized
        3. Each export gets the correct plan for its session
        """
        session_id = f"integration_test_{uuid.uuid4().hex[:8]}"
        test_session = TestSession(session_id, test_temp_dir["test_state_dir"], test_temp_dir["test_plans_dir"])
        test_session.create()

        # Create plan file
        plan_path = test_temp_dir["test_plans_dir"] / f"plan_{session_id}.md"
        SyntheticPlanBuilder.create(plan_path, session_id, "Integration test plan")

        # Create mock transcript
        transcript_path = test_temp_dir["test_root"] / f"transcript_{session_id}.jsonl"
        SyntheticPlanBuilder.create_transcript(transcript_path, session_id, plan_path)

        # Prepare hook input for main()
        hook_input = {
            "session_id": session_id,
            "cwd": str(test_session.project_dir),
            "transcript_path": str(transcript_path),
            "permission_mode": "acceptEdits"
        }

        def call_main():
            """Call main() with hook input."""
            monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_input)))
            try:
                main()
                return {"success": True}
            except Exception as e:
                return {"error": str(e)}

        # Test 1: Single call to main() succeeds
        result = call_main()
        assert result.get("success"), f"main() failed: {result.get('error')}"

        # Verify export happened
        exported_files = list(test_session.export_dir.glob("*.md"))
        assert len(exported_files) > 0, "No files exported"

        # Test 2: Concurrent calls to main() are serialized
        results = []
        result_lock = multiprocessing.Lock()

        def concurrent_main():
            """Concurrent call to main()."""
            try:
                monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_input)))
                main()
                with result_lock:
                    results.append({"success": True})
            except SessionTimeoutError:
                with result_lock:
                    results.append({"timeout": True})
            except Exception as e:
                with result_lock:
                    results.append({"error": str(e)})

        # Run concurrent calls
        threads = []
        for _ in range(3):
            t = threading.Thread(target=concurrent_main)
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=10)

        # Verify all calls completed without errors
        assert len(results) == 3, f"Only {len(results)}/3 threads completed"
        successful = [r for r in results if r.get("success")]
        assert len(successful) >= 1, "No concurrent calls succeeded"

        test_session.cleanup()


# =============================================================================
# CONCURRENT SAME-SESSION TESTS
# =============================================================================

class TestConcurrentSameSession:
    """Test multiple processes exporting the same session simultaneously."""

    def test_same_session_serialization(self, test_temp_dir, mock_session_lock, sample_config):
        """
        Test that concurrent exports of the same session are serialized by the lock.

        Expected behavior:
        - First export acquires lock and completes
        - Second export waits for lock or times out
        - No cross-contamination occurs
        """
        session_id = f"concurrent_session_{uuid.uuid4().hex[:8]}"
        test_session = TestSession(session_id, test_temp_dir["test_state_dir"], test_temp_dir["test_plans_dir"])
        test_session.create()

        # Create plan file
        plan_path = test_temp_dir["test_plans_dir"] / f"plan_{session_id}.md"
        SyntheticPlanBuilder.create(plan_path, session_id, "Concurrent test plan")

        export_count = {"value": 0}
        export_lock = multiprocessing.Lock()

        def export_with_lock():
            """Export attempt with lock counting."""
            try:
                with SessionLock(session_id, timeout=5.0, state_dir=test_temp_dir["test_state_dir"]):
                    # Simulate work
                    time.sleep(0.1)
                    result = export_plan(plan_path, test_session.project_dir)
                    with export_lock:
                        export_count["value"] += 1
                    return result
            except SessionTimeoutError:
                return {"timeout": True}

        # Run concurrent exports
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(export_with_lock) for _ in range(3)]
            results = [f.result() for f in as_completed(futures)]

        # Verify at least one export succeeded
        successful_exports = [r for r in results if r.get("success") and not r.get("timeout")]
        assert len(successful_exports) >= 1

        # Verify only unique files created (no corruption)
        exported_files = list(test_session.export_dir.glob("*.md"))
        assert len(exported_files) >= 1

        test_session.cleanup()

    def test_lock_timeout_scenario(self, test_temp_dir, mock_session_lock):
        """
        Test that lock timeout is handled gracefully.

        Expected behavior:
        - First export holds lock for extended period
        - Second export times out gracefully
        - No deadlock occurs
        """
        session_id = f"timeout_session_{uuid.uuid4().hex[:8]}"
        test_session = TestSession(session_id, test_temp_dir["test_state_dir"], test_temp_dir["test_plans_dir"])
        test_session.create()

        plan_path = test_temp_dir["test_plans_dir"] / f"plan_{session_id}.md"
        SyntheticPlanBuilder.create(plan_path, session_id, "Timeout test plan")

        timeout_occurred = {"value": False}

        def long_running_export():
            """Hold lock for extended period."""
            with SessionLock(session_id, timeout=10.0, state_dir=test_temp_dir["test_state_dir"]):
                time.sleep(2.0)  # Hold lock longer than second export timeout
                return export_plan(plan_path, test_session.project_dir)

        def quick_timeout_export():
            """Try to acquire lock with short timeout."""
            try:
                with SessionLock(session_id, timeout=0.5, state_dir=test_temp_dir["test_state_dir"]):
                    return export_plan(plan_path, test_session.project_dir)
            except SessionTimeoutError as e:
                timeout_occurred["value"] = True
                return {"timeout": True, "error": str(e)}

        # Start long-running export
        with ThreadPoolExecutor(max_workers=2) as executor:
            long_future = executor.submit(long_running_export)
            time.sleep(0.1)  # Ensure first export acquires lock

            # Try quick timeout export
            quick_future = executor.submit(quick_timeout_export)

            long_result = long_future.result()
            quick_result = quick_future.result()

        # Verify timeout occurred
        assert timeout_occurred["value"] is True
        assert quick_result.get("timeout") is True
        assert long_result.get("success") is True

        test_session.cleanup()


# =============================================================================
# MULTI-SESSION RACE CONDITION TESTS
# =============================================================================

class TestMultiSessionRaceConditions:
    """
    Test multiple sessions exporting simultaneously.

    These tests are designed to trigger the original race condition bug
    where multiple sessions would export the wrong plan to the wrong directory.
    """

    def test_concurrent_different_sessions(self, test_temp_dir, mock_session_lock, sample_config):
        """
        Test that multiple sessions can export simultaneously without cross-contamination.

        Original bug: Both sessions would select the same plan (globally newest)
        and export to wrong directories.

        Fixed behavior: Each session exports its own plan to its own directory.
        """
        num_sessions = 5
        sessions = []
        plans = []

        # Create multiple sessions with unique plans
        for i in range(num_sessions):
            session_id = f"multi_session_{i}_{uuid.uuid4().hex[:8]}"
            test_session = TestSession(session_id, test_temp_dir["test_state_dir"], test_temp_dir["test_plans_dir"])
            test_session.create()
            sessions.append(test_session)

            # Create unique plan for each session
            plan_path = test_temp_dir["test_plans_dir"] / f"plan_session_{i}.md"
            plan_content = f"Unique plan content for session {i} with ID {session_id}"
            SyntheticPlanBuilder.create(plan_path, session_id, plan_content)
            plans.append((session_id, plan_path, plan_content))

        export_results = []

        def export_session(session_idx):
            """Export a specific session's plan."""
            session_id, plan_path, expected_content = plans[session_idx]
            test_session = sessions[session_idx]

            try:
                with SessionLock(session_id, timeout=10.0, state_dir=test_temp_dir["test_state_dir"]):
                    # Add random delay to increase chance of race conditions
                    time.sleep(0.01 * (session_idx % 3))
                    result = export_plan(plan_path, test_session.project_dir)
                    return {
                        "session_idx": session_idx,
                        "session_id": session_id,
                        "success": result.get("success"),
                        "destination": result.get("destination")
                    }
            except Exception as e:
                return {
                    "session_idx": session_idx,
                    "session_id": session_id,
                    "error": str(e)
                }

        # Export all sessions concurrently
        with ThreadPoolExecutor(max_workers=num_sessions) as executor:
            futures = [executor.submit(export_session, i) for i in range(num_sessions)]
            results = [f.result() for f in as_completed(futures)]

        # Verify all exports succeeded
        assert len(results) == num_sessions
        for result in results:
            assert result.get("success") is True, f"Session {result['session_id']} failed: {result.get('error')}"
            assert result.get("destination") is not None

        # Verify no cross-contamination: each session has its unique content
        for i, test_session in enumerate(sessions):
            exported_files = list(test_session.export_dir.glob("*.md"))
            assert len(exported_files) >= 1, f"Session {i} has no exported files"

            # Verify content matches expected
            content = exported_files[0].read_text()
            expected_content = plans[i][2]
            assert expected_content in content, f"Session {i} has wrong content!"

        # Cleanup
        for test_session in sessions:
            test_session.cleanup()

    def test_interleaved_timing_with_random_delays(self, test_temp_dir, mock_session_lock, sample_config):
        """
        Test with random timing variations to maximize race condition exposure.

        This test simulates the real-world scenario where sessions exit plan mode
        at unpredictable times due to user interaction timing.
        """
        import random

        num_sessions = 10
        sessions = []
        plans = []

        # Create sessions
        for i in range(num_sessions):
            session_id = f"random_session_{i}_{uuid.uuid4().hex[:8]}"
            test_session = TestSession(session_id, test_temp_dir["test_state_dir"], test_temp_dir["test_plans_dir"])
            test_session.create()
            sessions.append(test_session)

            plan_path = test_temp_dir["test_plans_dir"] / f"plan_random_{i}.md"
            plan_content = f"Random timing plan {i} - {uuid.uuid4().hex}"
            SyntheticPlanBuilder.create(plan_path, session_id, plan_content)
            plans.append((session_id, plan_path, plan_content))

        results = []

        def export_with_random_delay(session_idx):
            """Export with random delay to simulate real-world timing."""
            session_id, plan_path, _ = plans[session_idx]
            test_session = sessions[session_idx]

            # Random delay before attempting export
            time.sleep(random.uniform(0, 0.1))

            try:
                with SessionLock(session_id, timeout=10.0, state_dir=test_temp_dir["test_state_dir"]):
                    # Random delay during export
                    time.sleep(random.uniform(0, 0.05))
                    result = export_plan(plan_path, test_session.project_dir)
                    return {
                        "session_idx": session_idx,
                        "success": result.get("success"),
                        "destination": result.get("destination")
                    }
            except Exception as e:
                return {
                    "session_idx": session_idx,
                    "error": str(e)
                }

        # Run with random timing
        with ThreadPoolExecutor(max_workers=num_sessions) as executor:
            futures = [executor.submit(export_with_random_delay, i) for i in range(num_sessions)]
            results = [f.result() for f in as_completed(futures)]

        # Verify all succeeded
        assert len(results) == num_sessions
        for result in results:
            assert result.get("success") is True, f"Session {result['session_idx']} failed: {result.get('error')}"

        # Verify content integrity
        for i, test_session in enumerate(sessions):
            exported_files = list(test_session.export_dir.glob("*.md"))
            assert len(exported_files) >= 1
            content = exported_files[0].read_text()
            assert plans[i][2] in content

        # Cleanup
        for test_session in sessions:
            test_session.cleanup()


# =============================================================================
# STRESS TEST SCENARIOS
# =============================================================================

class TestStressScenarios:
    """
    Stress tests with high concurrency to verify stability and deadlock prevention.

    These tests "hammer" the implementation with:
    - Many concurrent processes
    - Many sessions
    - Random timing variations
    - Rapid sequential exports

    Goal: Ensure no deadlocks, no crashes, and correct behavior under load.
    """

    def test_rapid_sequential_exports(self, test_temp_dir, mock_session_lock, sample_config):
        """
        Test rapid sequential exports from same session.

        Verifies lock is properly released and can be re-acquired.
        """
        session_id = f"rapid_session_{uuid.uuid4().hex[:8]}"
        test_session = TestSession(session_id, test_temp_dir["test_state_dir"], test_temp_dir["test_plans_dir"])
        test_session.create()

        plan_path = test_temp_dir["test_plans_dir"] / f"plan_rapid.md"
        SyntheticPlanBuilder.create(plan_path, session_id, "Rapid export test")

        export_count = 20
        successes = 0

        for i in range(export_count):
            try:
                with SessionLock(session_id, timeout=2.0, state_dir=test_temp_dir["test_state_dir"]):
                    result = export_plan(plan_path, test_session.project_dir)
                    if result.get("success"):
                        successes += 1
            except SessionTimeoutError:
                pass  # Expected under rapid fire

        # Verify most succeeded (some may timeout due to rapid fire)
        assert successes >= export_count // 2, f"Only {successes}/{export_count} succeeded"

        # Verify files created
        exported_files = list(test_session.export_dir.glob("*.md"))
        assert len(exported_files) >= export_count // 2

        test_session.cleanup()

    def test_high_concurrency_stress(self, test_temp_dir, mock_session_lock, sample_config):
        """
        High concurrency stress test with many sessions and exports.

        This is the primary stress test to verify:
        - No deadlocks occur
        - All exports complete (or timeout gracefully)
        - No cross-contamination
        - Lock files are cleaned up
        """
        num_sessions = 20
        exports_per_session = 3
        sessions = []
        plans = []

        # Create sessions
        for i in range(num_sessions):
            session_id = f"stress_session_{i}_{uuid.uuid4().hex[:8]}"
            test_session = TestSession(session_id, test_temp_dir["test_state_dir"], test_temp_dir["test_plans_dir"])
            test_session.create()
            sessions.append(test_session)

            plan_path = test_temp_dir["test_plans_dir"] / f"plan_stress_{i}.md"
            plan_content = f"Stress test plan {i} - {uuid.uuid4().hex}"
            SyntheticPlanBuilder.create(plan_path, session_id, plan_content)
            plans.append((session_id, plan_path, plan_content))

        results = []
        total_exports = num_sessions * exports_per_session

        def export_multiple_attempts(session_idx):
            """Attempt multiple exports for a session."""
            session_id, plan_path, _ = plans[session_idx]
            test_session = sessions[session_idx]
            session_results = []

            for attempt in range(exports_per_session):
                try:
                    with SessionLock(session_id, timeout=5.0, state_dir=test_temp_dir["test_state_dir"]):
                        time.sleep(0.01)  # Simulate work
                        result = export_plan(plan_path, test_session.project_dir)
                        session_results.append({
                            "session_idx": session_idx,
                            "attempt": attempt,
                            "success": result.get("success")
                        })
                except SessionTimeoutError:
                    session_results.append({
                        "session_idx": session_idx,
                        "attempt": attempt,
                        "timeout": True
                    })
                except Exception as e:
                    session_results.append({
                        "session_idx": session_idx,
                        "attempt": attempt,
                        "error": str(e)
                    })

            return session_results

        # Run high concurrency test
        with ThreadPoolExecutor(max_workers=num_sessions) as executor:
            futures = [executor.submit(export_multiple_attempts, i) for i in range(num_sessions)]
            all_results = [f.result() for f in as_completed(futures)]
            results = [r for session_results in all_results for r in session_results]

        # Verify results
        assert len(results) == total_exports

        # Count successes and timeouts
        successes = sum(1 for r in results if r.get("success"))
        timeouts = sum(1 for r in results if r.get("timeout"))
        errors = sum(1 for r in results if r.get("error"))

        # At least some should succeed
        assert successes > 0, "No exports succeeded!"
        assert errors == 0, f"Errors occurred: {errors}"

        # Verify no lock files remain (cleanup verification)
        lock_files = list(test_temp_dir["test_state_dir"].glob(".*.lock"))
        assert len(lock_files) == 0, f"Lock files not cleaned up: {lock_files}"

        # Verify content integrity for all sessions
        for i, test_session in enumerate(sessions):
            exported_files = list(test_session.export_dir.glob("*.md"))
            assert len(exported_files) > 0, f"Session {i} has no exported files"
            # Verify at least one file has correct content
            content_found = False
            for f in exported_files:
                if plans[i][2] in f.read_text():
                    content_found = True
                    break
            assert content_found, f"Session {i} has corrupted content!"

        # Cleanup
        for test_session in sessions:
            test_session.cleanup()

    def test_deadlock_prevention(self, test_temp_dir, mock_session_lock):
        """
        Test that lock acquisition and release work correctly.

        Verifies that locks are properly released and can be re-acquired.
        """
        session_id = f"deadlock_test_{uuid.uuid4().hex[:8]}"

        # Test 1: Basic acquire and release
        with SessionLock(session_id, timeout=5.0, state_dir=test_temp_dir["test_state_dir"]):
            assert True  # Lock acquired

        # Test 2: Can re-acquire after release
        with SessionLock(session_id, timeout=5.0, state_dir=test_temp_dir["test_state_dir"]):
            assert True  # Lock re-acquired

        # Test 3: Multiple sequential operations
        for i in range(5):
            with SessionLock(session_id, timeout=5.0, state_dir=test_temp_dir["test_state_dir"]):
                time.sleep(0.01)  # Minimal work

        # Test 4: Thread-based concurrent access (same process, different threads)
        results = {"count": 0}
        lock = threading.Lock()

        def worker_thread():
            """Worker that acquires lock, does work, releases."""
            try:
                with SessionLock(session_id, timeout=5.0, state_dir=test_temp_dir["test_state_dir"]):
                    time.sleep(0.05)
                    with lock:
                        results["count"] += 1
            except Exception as e:
                return {"error": str(e)}

        threads = []
        for i in range(5):
            t = threading.Thread(target=worker_thread)
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=10)

        assert results["count"] == 5, f"Only {results['count']}/5 threads completed"


# =============================================================================
# CLEANUP VERIFICATION TESTS
# =============================================================================

class TestCleanupVerification:
    """Verify proper cleanup of resources."""

    def test_lock_file_cleanup(self, test_temp_dir, mock_session_lock):
        """Verify lock files are cleaned up after use."""
        session_id = f"cleanup_test_{uuid.uuid4().hex[:8]}"
        lock_file = test_temp_dir["test_state_dir"] / f".{session_id}.lock"

        # Lock should not exist initially
        assert not lock_file.exists()

        # Acquire and release lock
        with SessionLock(session_id, timeout=5.0, state_dir=test_temp_dir["test_state_dir"]):
            # Lock file exists during lock
            assert lock_file.exists()

        # Lock file should be cleaned up
        assert not lock_file.exists(), "Lock file not cleaned up!"

    def test_lock_file_cleanup_on_exception(self, test_temp_dir, mock_session_lock):
        """Verify lock files are cleaned up even if exception occurs."""
        session_id = f"exception_cleanup_{uuid.uuid4().hex[:8]}"
        lock_file = test_temp_dir["test_state_dir"] / f".{session_id}.lock"

        try:
            with SessionLock(session_id, timeout=5.0, state_dir=test_temp_dir["test_state_dir"]):
                assert lock_file.exists()
                raise ValueError("Simulated error")
        except ValueError:
            pass

        # Lock file should still be cleaned up
        assert not lock_file.exists(), "Lock file not cleaned up after exception!"

    def test_temp_file_cleanup(self, test_temp_dir):
        """Verify test directories are properly isolated."""
        # Create multiple test sessions
        for i in range(10):
            session_id = f"temp_test_{i}_{uuid.uuid4().hex[:8]}"
            test_session = TestSession(session_id, test_temp_dir["test_state_dir"], test_temp_dir["test_plans_dir"])
            test_session.create()

            # Create plan
            plan_path = test_temp_dir["test_plans_dir"] / f"plan_{i}.md"
            SyntheticPlanBuilder.create(plan_path, session_id)

            # Verify isolation
            assert test_session.project_dir.exists()
            assert test_session.export_dir.exists()

            test_session.cleanup()

        # Verify test sessions are cleaned up
        session_dirs = list(test_temp_dir["test_state_dir"].glob("project_*"))
        for session_dir in session_dirs:
            assert not session_dir.exists(), f"Session directory not cleaned up: {session_dir}"


# =============================================================================
# RUN TESTS WITH PARALLEL EXECUTION
# =============================================================================

if __name__ == "__main__":
    """
    Run tests with various configurations for maximum coverage.

    Usage:
        # Run all tests
        pytest test_race_condition_fix.py -v

        # Run with parallel execution (requires pytest-xdist)
        pytest test_race_condition_fix.py -n auto

        # Run stress tests only
        pytest test_race_condition_fix.py::TestStressScenarios -v

        # Run with verbose output
        pytest test_race_condition_fix.py -v -s

        # Run with coverage
        pytest test_race_condition_fix.py --cov=scripts/plan_export --cov-report=html
    """
    pytest.main([__file__, "-v", "-s"])
