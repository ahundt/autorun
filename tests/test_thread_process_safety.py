#!/usr/bin/env python3
"""Test thread and process safety of the new session state system"""

import sys
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
import multiprocessing

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

def test_thread_safety():
    """Test thread safety with multiple concurrent threads"""
    print("🔍 Testing Thread Safety...")

    from clautorun.claude_code_plugin import session_state

    session_id = "thread_test_session"
    results = []
    errors = []

    def thread_worker(worker_id):
        try:
            with session_state(session_id) as state:
                # Each thread should get isolated access
                state[f"worker_{worker_id}_start"] = time.time()
                state[f"worker_{worker_id}_data"] = f"data_from_worker_{worker_id}"

                # Small delay to simulate work
                time.sleep(0.05)

                # Read back our data
                start_time = state[f"worker_{worker_id}_start"]
                data = state[f"worker_{worker_id}_data"]

                results.append({
                    'worker_id': worker_id,
                    'start_time': start_time,
                    'data': data,
                    'state_keys': list(state.keys())
                })

        except Exception as e:
            errors.append((worker_id, str(e)))

    # Start multiple threads
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(thread_worker, i) for i in range(20)]

        # Wait for completion
        for future in futures:
            future.result()

    print(f"Thread safety test: {len(results)} completed, {len(errors)} errors")

    # Verify each worker's data is intact
    for result in results:
        expected_data = f"data_from_worker_{result['worker_id']}"
        if result['data'] != expected_data:
            print(f"❌ Worker {result['worker_id']} data corruption: expected {expected_data}, got {result['data']}")
        else:
            print(f"✅ Worker {result['worker_id']} data intact")

    return len(errors) == 0

def test_shared_access_safety():
    """Test shared access scenarios (AI monitor use case)"""
    print("\n🔍 Testing Shared Access Safety...")

    from clautorun.claude_code_plugin import session_state, shared_session_state

    session_id = "shared_access_test"
    results = []
    errors = []

    def exclusive_worker(worker_id):
        """Worker with exclusive access"""
        try:
            with session_state(session_id) as state:
                state["exclusive_worker"] = worker_id
                state["exclusive_timestamp"] = time.time()

                # Simulate work
                time.sleep(0.1)

                results.append({
                    'type': 'exclusive',
                    'worker_id': worker_id,
                    'exclusive_worker': state.get("exclusive_worker"),
                    'shared_count': state.get("_shared_access_count", 0)
                })
        except Exception as e:
            errors.append((f"exclusive_{worker_id}", str(e)))

    def shared_worker(worker_id):
        """Worker with shared access"""
        try:
            with shared_session_state(session_id) as state:
                # Should be able to access state concurrently
                exclusive_worker = state.get("exclusive_worker")
                shared_count = state.get("_shared_access_count", 0)

                results.append({
                    'type': 'shared',
                    'worker_id': worker_id,
                    'exclusive_worker': exclusive_worker,
                    'shared_count': shared_count
                })
        except Exception as e:
            errors.append((f"shared_{worker_id}", str(e)))

    # Start exclusive access worker first
    exclusive_thread = threading.Thread(target=exclusive_worker, args=(1,))
    exclusive_thread.start()

    # Small delay to let exclusive worker start
    time.sleep(0.01)

    # Start shared access workers
    shared_threads = []
    for i in range(5):
        thread = threading.Thread(target=shared_worker, args=(i,))
        shared_threads.append(thread)
        thread.start()

    # Wait for all threads
    exclusive_thread.join()
    for thread in shared_threads:
        thread.join()

    print(f"Shared access test: {len(results)} completed, {len(errors)} errors")

    # Check results
    exclusive_results = [r for r in results if r['type'] == 'exclusive']
    shared_results = [r for r in results if r['type'] == 'shared']

    print(f"  Exclusive workers: {len(exclusive_results)}")
    print(f"  Shared workers: {len(shared_results)}")

    return len(errors) == 0

def test_lock_timeout_behavior():
    """Test lock timeout behavior"""
    print("\n🔍 Testing Lock Timeout Behavior...")

    from clautorun.claude_code_plugin import session_state
    import time

    session_id = "timeout_test"
    results = []

    def quick_worker():
        """Quick worker that should succeed"""
        try:
            with session_state(session_id, timeout=1.0) as state:
                state["quick_worker"] = "success"
                results.append("quick_success")
        except Exception as e:
            results.append(f"quick_error: {e}")

    def blocking_worker():
        """Worker that holds the lock"""
        try:
            with session_state(session_id, timeout=1.0) as state:
                state["blocking_worker"] = "holding"
                time.sleep(0.5)  # Hold lock for 0.5 seconds
                results.append("blocking_success")
        except Exception as e:
            results.append(f"blocking_error: {e}")

    def timeout_worker():
        """Worker that should timeout"""
        try:
            time.sleep(0.1)  # Start after blocking worker
            with session_state(session_id, timeout=0.2) as state:
                state["timeout_worker"] = "should_not_reach"
                results.append("timeout_success")
        except Exception as e:
            if "timeout" in str(e).lower() or "TimeoutError" in str(type(e).__name__):
                results.append("timeout_expected")
            else:
                results.append(f"timeout_unexpected_error: {e}")
  
    # Start workers
    blocking_thread = threading.Thread(target=blocking_worker)
    timeout_thread = threading.Thread(target=timeout_worker)
    quick_thread = threading.Thread(target=quick_worker)

    # Start in order
    blocking_thread.start()
    timeout_thread.start()
    quick_thread.start()

    # Wait for completion
    blocking_thread.join()
    timeout_thread.join()
    quick_thread.join()

    print(f"Timeout test results: {results}")

    # Check that we got expected behavior
    expected_patterns = ["blocking_success", "timeout_expected", "quick_success"]
    success = all(pattern in ' '.join(results) for pattern in expected_patterns)

    return success

# Top-level function for multiprocessing (can't be nested)
def process_worker(worker_id, session_id, result_queue):
    """Worker function for multiprocessing"""
    try:
        # Import inside function to avoid issues with multiprocessing
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        from clautorun.claude_code_plugin import session_state

        with session_state(session_id) as state:
            # Each process should work independently
            state[f"process_{worker_id}"] = f"data_from_process_{worker_id}"
            state[f"process_{worker_id}_pid"] = os.getpid()
            state[f"process_{worker_id}_time"] = time.time()

            # Small delay
            time.sleep(0.1)

            # Read back data
            data = state[f"process_{worker_id}"]
            pid = state[f"process_{worker_id}_pid"]

            result_queue.put({
                'worker_id': worker_id,
                'pid': pid,
                'data': data,
                'success': True
            })

    except Exception as e:
        result_queue.put({
            'worker_id': worker_id,
            'error': str(e),
            'success': False
        })

def test_multiprocess_safety():
    """Test process safety with multiple processes"""
    print("\n🔍 Testing Process Safety...")

    session_id = "process_test_session"
    results = []

    # Use multiprocessing
    with multiprocessing.Manager() as manager:
        result_queue = manager.Queue()

        # Create process pool
        with ProcessPoolExecutor(max_workers=4) as executor:
            futures = []

            # Submit worker tasks
            for i in range(8):
                future = executor.submit(process_worker, i, session_id, result_queue)
                futures.append(future)

            # Wait for all futures
            for future in futures:
                future.result()

            # Collect results
            while not result_queue.empty():
                results.append(result_queue.get())

    print(f"Process safety test: {len(results)} results")

    success_count = sum(1 for r in results if r.get('success', False))
    error_count = len(results) - success_count

    print(f"  Successful processes: {success_count}")
    print(f"  Failed processes: {error_count}")

    return error_count == 0

def run_all_safety_tests():
    """Run all thread and process safety tests"""
    print("🚀 Starting Thread and Process Safety Tests")
    print("=" * 60)

    tests = [
        ("Thread Safety", test_thread_safety),
        ("Shared Access Safety", test_shared_access_safety),
        ("Lock Timeout Behavior", test_lock_timeout_behavior),
        ("Process Safety", test_multiprocess_safety),
    ]

    results = {}

    for test_name, test_func in tests:
        try:
            print(f"\nRunning {test_name} test...")
            result = test_func()
            results[test_name] = result

            if result:
                print(f"✅ {test_name}: PASSED")
            else:
                print(f"❌ {test_name}: FAILED")

        except Exception as e:
            print(f"❌ {test_name}: ERROR - {e}")
            results[test_name] = False

    print("\n" + "=" * 60)
    print("SAFETY TEST SUMMARY")
    print("=" * 60)

    passed = sum(1 for result in results.values() if result)
    total = len(results)

    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{test_name}: {status}")

    print(f"\nOverall: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 ALL SAFETY TESTS PASSED!")
    else:
        print("⚠️ Some safety tests failed - review the implementation")

    return passed == total

if __name__ == "__main__":
    run_all_safety_tests()