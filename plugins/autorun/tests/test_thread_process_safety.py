#!/usr/bin/env python3
"""Thread and process safety tests for the session state backend."""

import multiprocessing
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _session_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def test_thread_safety():
    """Multiple threads can update one session without losing their own writes."""
    from autorun import session_state

    session_id = _session_id("thread")
    results = []
    errors = []

    def thread_worker(worker_id):
        try:
            with session_state(session_id) as state:
                state[f"worker_{worker_id}_start"] = time.time()
                state[f"worker_{worker_id}_data"] = f"data_from_worker_{worker_id}"
                time.sleep(0.05)
                results.append({
                    "worker_id": worker_id,
                    "data": state[f"worker_{worker_id}_data"],
                })
        except Exception as e:
            errors.append((worker_id, str(e)))

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(thread_worker, i) for i in range(20)]
        for future in futures:
            future.result()

    assert errors == []
    assert len(results) == 20
    for result in results:
        assert result["data"] == f"data_from_worker_{result['worker_id']}"

    with session_state(session_id) as state:
        for worker_id in range(20):
            assert state[f"worker_{worker_id}_data"] == f"data_from_worker_{worker_id}"


def test_shared_access_safety():
    """shared_session_state sees data committed by session_state."""
    from autorun import session_state, shared_session_state

    session_id = _session_id("shared")
    results = []
    errors = []

    def exclusive_worker(worker_id):
        try:
            with session_state(session_id) as state:
                state["exclusive_worker"] = worker_id
                state["exclusive_timestamp"] = time.time()
                time.sleep(0.1)
                results.append({"type": "exclusive", "worker_id": worker_id})
        except Exception as e:
            errors.append((f"exclusive_{worker_id}", str(e)))

    def shared_worker(worker_id):
        try:
            with shared_session_state(session_id) as state:
                results.append({
                    "type": "shared",
                    "worker_id": worker_id,
                    "exclusive_worker": state.get("exclusive_worker"),
                })
        except Exception as e:
            errors.append((f"shared_{worker_id}", str(e)))

    exclusive_thread = threading.Thread(target=exclusive_worker, args=(1,))
    exclusive_thread.start()
    exclusive_thread.join()

    shared_threads = [threading.Thread(target=shared_worker, args=(i,)) for i in range(5)]
    for thread in shared_threads:
        thread.start()
    for thread in shared_threads:
        thread.join()

    assert errors == []
    exclusive_results = [r for r in results if r["type"] == "exclusive"]
    shared_results = [r for r in results if r["type"] == "shared"]
    assert len(exclusive_results) == 1
    assert len(shared_results) == 5
    assert all(r["exclusive_worker"] == 1 for r in shared_results)


def test_lock_timeout_behavior():
    """A short-timeout waiter fails while another thread owns the state lock."""
    from autorun import session_state

    session_id = _session_id("timeout")
    results = []

    def quick_worker():
        try:
            time.sleep(0.8)
            with session_state(session_id, timeout=1.0) as state:
                state["quick_worker"] = "success"
                results.append("quick_success")
        except Exception as e:
            results.append(f"quick_error: {e}")

    def blocking_worker():
        try:
            with session_state(session_id, timeout=1.0) as state:
                state["blocking_worker"] = "holding"
                time.sleep(0.6)
                results.append("blocking_success")
        except Exception as e:
            results.append(f"blocking_error: {e}")

    def timeout_worker():
        try:
            time.sleep(0.05)
            with session_state(session_id, timeout=0.1) as state:
                state["timeout_worker"] = "should_not_reach"
                results.append("timeout_success")
        except Exception as e:
            if "timeout" in str(e).lower() or "TimeoutError" in type(e).__name__:
                results.append("timeout_expected")
            else:
                results.append(f"timeout_unexpected_error: {e}")

    threads = [
        threading.Thread(target=blocking_worker),
        threading.Thread(target=timeout_worker),
        threading.Thread(target=quick_worker),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert "blocking_success" in results
    assert "timeout_expected" in results
    assert "quick_success" in results
    assert "timeout_success" not in results
    assert not any("error" in result for result in results)


def process_worker(worker_id, session_id, result_queue):
    """Top-level worker function for multiprocessing."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
        from autorun import session_state

        with session_state(session_id) as state:
            state[f"process_{worker_id}"] = f"data_from_process_{worker_id}"
            state[f"process_{worker_id}_pid"] = os.getpid()
            state[f"process_{worker_id}_time"] = time.time()
            time.sleep(0.1)
            result_queue.put({
                "worker_id": worker_id,
                "pid": state[f"process_{worker_id}_pid"],
                "data": state[f"process_{worker_id}"],
                "success": True,
            })
    except Exception as e:
        result_queue.put({
            "worker_id": worker_id,
            "error": str(e),
            "success": False,
        })


def test_multiprocess_safety():
    """Multiple processes can update one session through the file lock."""
    session_id = _session_id("process")
    results = []

    with multiprocessing.Manager() as manager:
        result_queue = manager.Queue()
        with ProcessPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(process_worker, i, session_id, result_queue)
                for i in range(8)
            ]
            for future in futures:
                future.result()

        while not result_queue.empty():
            results.append(result_queue.get())

    assert len(results) == 8
    assert all(r.get("success") for r in results), results
    for result in results:
        assert result["data"] == f"data_from_process_{result['worker_id']}"


def run_all_safety_tests():
    """Run this module as a script for manual diagnostics."""
    tests = [
        ("Thread Safety", test_thread_safety),
        ("Shared Access Safety", test_shared_access_safety),
        ("Lock Timeout Behavior", test_lock_timeout_behavior),
        ("Process Safety", test_multiprocess_safety),
    ]

    for test_name, test_func in tests:
        print(f"Running {test_name}...")
        test_func()
        print(f"{test_name}: PASSED")

    return True


if __name__ == "__main__":
    run_all_safety_tests()
