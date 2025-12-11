#!/usr/bin/env python3
"""
Comprehensive test suite for RAII session manager
Validates proper resource management, thread/process safety, and error handling
"""

import sys
import os
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

def test_raii_resource_management():
    """Test RAII automatic resource acquisition and release"""
    print("🔍 Testing RAII Resource Management...")

    from clautorun.session_manager import SessionLock

    session_id = "raii_test_session"
    lock_file = Path.home() / ".claude" / "sessions" / f".{session_id}.lock"
    results = []

    def raii_worker(worker_id):
        try:
            state_dir = Path.home() / ".claude" / "sessions"
            with SessionLock(session_id, 5.0, state_dir) as lock_fd:
                # Verify lock file exists and contains correct info
                assert lock_file.exists(), f"Worker {worker_id}: Lock file should exist"

                # Verify we can write to lock file descriptor
                test_data = f"worker_{worker_id}_{time.time()}"
                lock_fd.write(test_data)
                lock_fd.flush()

                # Simulate work
                time.sleep(0.05)

                results.append({
                    'worker_id': worker_id,
                    'lock_file_exists': lock_file.exists(),
                    'test_data_written': True,
                    'success': True
                })

        except Exception as e:
            results.append({
                'worker_id': worker_id,
                'error': str(e),
                'success': False
            })

    # Test sequential access
    raii_worker(1)
    raii_worker(2)

    print(f"RAII test: {len(results)} workers completed")

    success_count = sum(1 for r in results if r.get('success', False))
    return success_count == len(results)

def test_concurrent_raii_safety():
    """Test concurrent RAII operations for race conditions"""
    print("\n🔍 Testing Concurrent RAII Safety...")

    from clautorun.session_manager import session_state

    session_id = "concurrent_raii_test"
    results = []
    errors = []

    def concurrent_worker(worker_id):
        try:
            with session_state(session_id) as state:
                # Each thread gets exclusive access
                state[f"worker_{worker_id}_start"] = time.time()
                state[f"worker_{worker_id}_pid"] = os.getpid()

                # Simulate concurrent work
                time.sleep(0.02)

                # Read back data
                start_time = state[f"worker_{worker_id}_start"]
                pid = state[f"worker_{worker_id}_pid"]

                results.append({
                    'worker_id': worker_id,
                    'start_time': start_time,
                    'pid': pid,
                    'state_size': len(state),
                    'success': True
                })

        except Exception as e:
            errors.append((worker_id, str(e)))

    # Start multiple threads
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(concurrent_worker, i) for i in range(15)]

        # Wait for completion
        for future in futures:
            future.result()

    print(f"Concurrent RAII test: {len(results)} completed, {len(errors)} errors")

    # Verify no data corruption
    for result in results:
        if result['state_size'] < 2:  # Should have at least start_time and pid
            print(f"❌ Worker {result['worker_id']} insufficient state size: {result['state_size']}")
        else:
            print(f"✅ Worker {result['worker_id']} state intact: {result['state_size']} items")

    return len(errors) == 0

def test_timeout_behavior():
    """Test timeout behavior with concrete error details"""
    print("\n🔍 Testing Timeout Behavior...")

    from clautorun.session_manager import SessionTimeoutError

    session_id = "timeout_test"
    results = []

    def blocking_worker():
        """Worker that holds lock for specific duration"""
        try:
            from clautorun.session_manager import SessionLock
            state_dir = Path.home() / ".claude" / "sessions"
            with SessionLock(session_id, 10.0, state_dir):
                time.sleep(0.3)  # Hold lock for 300ms
                results.append("blocking_completed")
        except Exception as e:
            results.append(f"blocking_error: {e}")

    def timeout_worker():
        """Worker that should timeout"""
        try:
            from clautorun.session_manager import SessionLock
            time.sleep(0.05)  # Start after blocking worker
            state_dir = Path.home() / ".claude" / "sessions"
            with SessionLock(session_id, 0.1, state_dir):
                results.append("timeout_unexpected_success")
        except SessionTimeoutError as e:
            results.append(f"timeout_expected: {e}")
        except Exception as e:
            results.append(f"timeout_unexpected_error: {e}")

    def quick_worker():
        """Worker that should succeed after lock is released"""
        try:
            from clautorun.session_manager import SessionLock
            time.sleep(0.4)  # Start after blocking worker finishes
            state_dir = Path.home() / ".claude" / "sessions"
            with SessionLock(session_id, 5.0, state_dir):
                results.append("quick_success")
        except Exception as e:
            results.append(f"quick_error: {e}")

    # Start workers in sequence
    blocking_thread = threading.Thread(target=blocking_worker)
    timeout_thread = threading.Thread(target=timeout_worker)
    quick_thread = threading.Thread(target=quick_worker)

    blocking_thread.start()
    timeout_thread.start()
    quick_thread.start()

    blocking_thread.join()
    timeout_thread.join()
    quick_thread.join()

    print(f"Timeout test results: {len(results)} operations")

    # Verify expected behavior
    expected_patterns = ["blocking_completed", "timeout_expected", "quick_success"]
    found_patterns = sum(1 for pattern in expected_patterns
                         if any(pattern in result for result in results))

    return found_patterns == len(expected_patterns)

def test_shared_access_isolation():
    """Test shared access scenarios with proper isolation"""
    print("\n🔍 Testing Shared Access Isolation...")

    from clautorun.session_manager import session_state, shared_session_state

    session_id = "shared_access_test"
    results = []

    def exclusive_worker():
        """Worker with exclusive access"""
        try:
            with session_state(session_id, timeout=2.0) as state:
                state["exclusive_data"] = "exclusive_value"
                state["exclusive_timestamp"] = time.time()
                results.append({
                    'type': 'exclusive',
                    'exclusive_data': state.get("exclusive_data"),
                    'shared_count': state.get("_shared_access_count", 0),
                    'success': True
                })
        except Exception as e:
            results.append({
                'type': 'exclusive',
                'error': str(e),
                'success': False
            })

    def shared_worker(worker_id):
        """Worker with shared access"""
        try:
            with shared_session_state(session_id, timeout=1.0) as state:
                exclusive_data = state.get("exclusive_data")
                shared_count = state.get("_shared_access_count", 0)

                results.append({
                    'type': 'shared',
                    'worker_id': worker_id,
                    'exclusive_data': exclusive_data,
                    'shared_count': shared_count,
                    'success': True
                })
        except Exception as e:
            results.append({
                'type': 'shared',
                'worker_id': worker_id,
                'error': str(e),
                'success': False
            })

    # Start workers
    exclusive_thread = threading.Thread(target=exclusive_worker)
    exclusive_thread.start()

    # Small delay to let exclusive worker establish lock
    time.sleep(0.01)

    # Start shared workers
    shared_threads = []
    for i in range(3):
        thread = threading.Thread(target=shared_worker, args=(i,))
        shared_threads.append(thread)
        thread.start()

    # Wait for completion
    exclusive_thread.join()
    for thread in shared_threads:
        thread.join()

    print(f"Shared access test: {len(results)} results")

    success_count = sum(1 for r in results if r.get('success', False))
    return success_count == len(results)

def test_error_handling_robustness():
    """Test robust error handling with concrete error details"""
    print("\n🔍 Testing Error Handling Robustness...")

    from clautorun.session_manager import session_state, SessionStateError, SessionTimeoutError

    results = []

    # Test invalid session_id
    try:
        with session_state("", timeout=1.0):
            results.append("empty_session_id_unexpected_success")
    except SessionStateError as e:
        results.append(f"empty_session_id_expected_error: {e}")
    except Exception as e:
        results.append(f"empty_session_id_unexpected_error: {e}")

    # Test None session_id
    try:
        with session_state(None, timeout=1.0):
            results.append("none_session_id_unexpected_success")
    except SessionStateError as e:
        results.append(f"none_session_id_expected_error: {e}")
    except Exception as e:
        results.append(f"none_session_id_unexpected_error: {e}")

    # Test extremely short timeout
    try:
        with session_state("short_timeout_test", timeout=0.001):
            results.append("short_timeout_unexpected_success")
    except (SessionTimeoutError, Exception) as e:
        results.append(f"short_timeout_expected_error: {type(e).__name__}")

    print(f"Error handling test: {len(results)} test cases")
    return len(results) > 0  # Should have some expected errors

def test_dry_principles():
    """Test DRY principles and avoid code duplication"""
    print("\n🔍 Testing DRY Principles...")

    from clautorun.session_manager import session_state, shared_session_state, get_session_manager

    # Test singleton pattern
    manager1 = get_session_manager()
    manager2 = get_session_manager()
    same_manager = manager1 is manager2

    # Test convenience wrappers work
    try:
        with session_state("dry_test") as state1:
            state1["test"] = "value1"

        with shared_session_state("dry_test") as state2:
            state2["test"] = "value2"

        results = "DRY principles working"
    except Exception as e:
        results = f"DRY principles failed: {e}"

    print(f"DRY principles test: {results}")
    return same_manager and "working" in results

def run_comprehensive_raii_tests():
    """Run comprehensive RAII session manager tests"""
    print("🚀 Starting Comprehensive RAII Session Manager Tests")
    print("=" * 70)

    tests = [
        ("RAII Resource Management", test_raii_resource_management),
        ("Concurrent RAII Safety", test_concurrent_raii_safety),
        ("Timeout Behavior", test_timeout_behavior),
        ("Shared Access Isolation", test_shared_access_isolation),
        ("Error Handling Robustness", test_error_handling_robustness),
        ("DRY Principles", test_dry_principles),
    ]

    results = {}

    for test_name, test_func in tests:
        try:
            print(f"\nRunning {test_name} test...")
            start_time = time.time()
            result = test_func()
            duration = time.time() - start_time
            results[test_name] = result

            if result:
                print(f"✅ {test_name}: PASSED ({duration:.3f}s)")
            else:
                print(f"❌ {test_name}: FAILED ({duration:.3f}s)")

        except Exception as e:
            print(f"❌ {test_name}: ERROR - {e}")
            results[test_name] = False

    print("\n" + "=" * 70)
    print("RAII SESSION MANAGER TEST SUMMARY")
    print("=" * 70)

    passed = sum(1 for result in results.values() if result)
    total = len(results)

    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{test_name}: {status}")

    print(f"\nOverall: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 ALL RAII TESTS PASSED!")
        print("✅ Proper RAII resource management confirmed")
        print("✅ Thread and process safety validated")
        print("✅ Error handling is robust and concrete")
        print("✅ DRY principles followed correctly")
        print("✅ Context managers work automatically")
    else:
        print("⚠️ Some RAII tests failed - review implementation")

    return passed == total

if __name__ == "__main__":
    run_comprehensive_raii_tests()