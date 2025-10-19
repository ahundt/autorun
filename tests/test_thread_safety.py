#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Thread safety and multiprocessing tests for clautorun session state
"""
import pytest
import threading
import multiprocessing
import time
import tempfile
import os
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor

# Add src directory to Python path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from clautorun.main import session_state, CONFIG, COMMAND_HANDLERS


class TestThreadSafety:
    """Test thread safety of session state management"""

    @pytest.mark.unit
    def test_concurrent_session_state_access(self):
        """Test multiple threads accessing the same session"""
        session_id = "thread_test_concurrent"
        results = []
        errors = []

        def worker_thread(thread_id):
            try:
                with session_state(session_id) as state:
                    # Set thread-specific value
                    state[f"thread_{thread_id}"] = f"value_{thread_id}"
                    time.sleep(0.01)  # Small delay to increase contention
                    # Read all values
                    current_state = dict(state)
                    results.append((thread_id, current_state))
            except Exception as e:
                errors.append((thread_id, str(e)))

        # Run multiple threads
        threads = []
        for i in range(10):
            thread = threading.Thread(target=worker_thread, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify no errors occurred
        assert len(errors) == 0, f"Thread errors occurred: {errors}"
        assert len(results) == 10, "Not all threads completed"

        # Verify session state consistency
        with session_state(session_id) as state:
            final_state = dict(state)
            # Should have values from all threads
            for i in range(10):
                assert f"thread_{i}" in final_state, f"Missing value from thread {i}"
                assert final_state[f"thread_{i}"] == f"value_{i}", f"Incorrect value for thread {i}"

    @pytest.mark.unit
    def test_concurrent_policy_commands(self):
        """Test concurrent policy command execution"""
        session_id = "thread_test_commands"
        command_sequence = []

        def command_worker(command_name):
            try:
                with session_state(session_id) as state:
                    response = COMMAND_HANDLERS[command_name](state)
                    command_sequence.append((command_name, response))
            except Exception as e:
                command_sequence.append((command_name, f"ERROR: {e}"))

        # Run multiple commands concurrently
        commands = ["SEARCH", "ALLOW", "JUSTIFY", "SEARCH", "ALLOW"]
        threads = []

        for cmd in commands:
            thread = threading.Thread(target=command_worker, args=(cmd,))
            threads.append(thread)
            thread.start()

        # Wait for completion
        for thread in threads:
            thread.join()

        # Verify all commands executed
        assert len(command_sequence) == 5, "Not all commands completed"
        for cmd, response in command_sequence:
            assert not response.startswith("ERROR:"), f"Command {cmd} failed: {response}"
            assert "AutoFile policy:" in response, f"Invalid response for {cmd}"

        # Verify final state is consistent
        with session_state(session_id) as state:
            assert "file_policy" in state, "File policy not set"
            assert state["file_policy"] in ["SEARCH", "ALLOW", "JUSTIFY"], "Invalid policy value"

    @pytest.mark.unit
    def test_session_isolation_between_threads(self):
        """Test that different sessions are isolated between threads"""
        results = []

        def session_worker(session_id):
            try:
                with session_state(session_id) as state:
                    state["unique_value"] = f"session_{session_id}"
                    time.sleep(0.01)
                    value = state.get("unique_value")
                    results.append((session_id, value))
            except Exception as e:
                results.append((session_id, f"ERROR: {e}"))

        # Run multiple threads with different session IDs
        session_ids = ["session_1", "session_2", "session_3"]
        threads = []

        for session_id in session_ids:
            thread = threading.Thread(target=session_worker, args=(session_id,))
            threads.append(thread)
            thread.start()

        # Wait for completion
        for thread in threads:
            thread.join()

        # Verify session isolation
        assert len(results) == 3, "Not all sessions completed"
        for session_id, value in results:
            assert not value.startswith("ERROR:"), f"Session {session_id} failed: {value}"
            assert value == f"session_{session_id}", f"Session isolation failed for {session_id}"


class TestMultiprocessingSafety:
    """Test multiprocessing safety of session state management"""

    @pytest.mark.unit
    def test_multiprocess_session_access(self):
        """Test session state access from multiple processes"""
        session_id = "multiprocess_test"

        def process_worker(process_id):
            try:
                with session_state(session_id) as state:
                    # Set process-specific value
                    state[f"process_{process_id}"] = f"value_{process_id}"
                    time.sleep(0.01)
                    # Get current state
                    current_state = dict(state)
                    return (process_id, len(current_state), "SUCCESS")
            except Exception as e:
                return (process_id, 0, f"ERROR: {e}")

        # Run multiple processes
        with ProcessPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(process_worker, i) for i in range(4)]
            results = [future.result() for future in futures]

        # Verify all processes completed
        assert len(results) == 4, "Not all processes completed"
        for process_id, count, status in results:
            assert status == "SUCCESS", f"Process {process_id} failed: {status}"
            assert count > 0, f"Process {process_id} found no state data"

        # Verify final state contains all process data
        with session_state(session_id) as state:
            final_state = dict(state)
            for i in range(4):
                assert f"process_{i}" in final_state, f"Missing data from process {i}"

    @pytest.mark.unit
    def test_concurrent_command_execution_processes(self):
        """Test concurrent command execution across processes"""
        session_id = "multiprocess_commands"

        def command_process_worker(command):
            try:
                with session_state(session_id) as state:
                    response = COMMAND_HANDLERS[command](state)
                    return (command, response, "SUCCESS")
            except Exception as e:
                return (command, str(e), "ERROR")

        # Test different commands in parallel
        commands = ["SEARCH", "ALLOW", "JUSTIFY"]
        with ProcessPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(command_process_worker, cmd) for cmd in commands]
            results = [future.result() for future in futures]

        # Verify all commands executed successfully
        assert len(results) == 3, "Not all commands completed"
        for command, response, status in results:
            assert status == "SUCCESS", f"Command {command} failed: {response}"
            assert "AutoFile policy:" in response, f"Invalid response: {response}"

    @pytest.mark.unit
    def test_session_state_consistency_across_processes(self):
        """Test session state consistency when accessed from multiple processes"""
        session_id = "consistency_test"

        def consistency_worker(worker_id):
            try:
                with session_state(session_id) as state:
                    # Read existing counter
                    counter = state.get("counter", 0)
                    time.sleep(0.01)  # Simulate work
                    # Increment counter
                    new_counter = counter + 1
                    state["counter"] = new_counter
                    state[f"worker_{worker_id}"] = True
                    return (worker_id, new_counter, "SUCCESS")
            except Exception as e:
                return (worker_id, 0, f"ERROR: {e}")

        # Run multiple workers
        with ProcessPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(consistency_worker, i) for i in range(4)]
            results = [future.result() for future in futures]

        # Verify all workers completed
        assert len(results) == 4, "Not all workers completed"
        for worker_id, counter, status in results:
            assert status == "SUCCESS", f"Worker {worker_id} failed: {status}"

        # Verify final state
        with session_state(session_id) as state:
            final_state = dict(state)
            assert "counter" in final_state, "Counter not found in final state"
            assert final_state["counter"] >= 4, f"Counter should be at least 4, got {final_state['counter']}"

            # Verify all workers marked themselves
            for i in range(4):
                assert final_state.get(f"worker_{i}", False), f"Worker {i} not marked in state"


class TestStressTesting:
    """Stress tests for thread and process safety"""

    @pytest.mark.unit
    def test_high_concurrency_thread_access(self):
        """Test high concurrency access with many threads"""
        session_id = "stress_test_threads"
        num_threads = 20
        operations_per_thread = 10
        completed_operations = []

        def stress_worker(thread_id):
            try:
                for operation in range(operations_per_thread):
                    with session_state(session_id) as state:
                        key = f"thread_{thread_id}_op_{operation}"
                        state[key] = f"value_{thread_id}_{operation}"
                        completed_operations.append(key)
            except Exception as e:
                completed_operations.append(f"ERROR_thread_{thread_id}: {e}")

        # Start all threads
        threads = []
        for i in range(num_threads):
            thread = threading.Thread(target=stress_worker, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for completion
        for thread in threads:
            thread.join()

        # Verify operations
        expected_operations = num_threads * operations_per_thread
        error_operations = [op for op in completed_operations if op.startswith("ERROR")]
        successful_operations = [op for op in completed_operations if not op.startswith("ERROR")]

        assert len(error_operations) == 0, f"Errors occurred: {error_operations}"
        assert len(successful_operations) == expected_operations, f"Expected {expected_operations} operations, got {len(successful_operations)}"

        # Verify final state
        with session_state(session_id) as state:
            final_state = dict(state)
            for op in successful_operations:
                assert op in final_state, f"Operation {op} not persisted"

    @pytest.mark.unit
    def test_mixed_thread_process_access(self):
        """Test mixed access from both threads and processes"""
        session_id = "mixed_access_test"
        results = []

        def thread_worker(thread_id):
            try:
                with session_state(session_id) as state:
                    state[f"thread_{thread_id}"] = f"thread_value_{thread_id}"
                    return (f"thread_{thread_id}", "SUCCESS")
            except Exception as e:
                return (f"thread_{thread_id}", f"ERROR: {e}")

        def process_worker(process_id):
            try:
                with session_state(session_id) as state:
                    state[f"process_{process_id}"] = f"process_value_{process_id}"
                    return (f"process_{process_id}", "SUCCESS")
            except Exception as e:
                return (f"process_{process_id}", f"ERROR: {e}")

        # Run threads
        with ThreadPoolExecutor(max_workers=3) as thread_executor:
            thread_futures = [thread_executor.submit(thread_worker, i) for i in range(3)]
            thread_results = [future.result() for future in thread_futures]

        # Run processes
        with ProcessPoolExecutor(max_workers=2) as process_executor:
            process_futures = [process_executor.submit(process_worker, i) for i in range(2)]
            process_results = [future.result() for future in process_futures]

        all_results = thread_results + process_results

        # Verify all operations succeeded
        assert len(all_results) == 5, "Not all operations completed"
        for identifier, status in all_results:
            assert status == "SUCCESS", f"Operation {identifier} failed: {status}"

        # Verify final state
        with session_state(session_id) as state:
            final_state = dict(state)
            for identifier, status in all_results:
                assert identifier in final_state, f"Identifier {identifier} not found in state"
                assert final_state[identifier] == f"{identifier.replace('_', '_value_')}", f"Incorrect value for {identifier}"