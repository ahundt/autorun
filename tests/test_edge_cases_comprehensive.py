#!/usr/bin/env python3
"""Comprehensive edge case testing suite for clautorun"""

import sys
import os
import json
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

def test_plugin_edge_cases():
    """Test edge cases for claude_code_plugin.py"""
    print("🔍 Testing Plugin Edge Cases...")

    from clautorun.claude_code_plugin import main

    test_cases = [
        # Empty input
        {"input": "", "description": "Empty input"},

        # Invalid JSON
        {"input": "{invalid json", "description": "Malformed JSON"},
        {"input": '{"incomplete": ', "description": "Incomplete JSON"},
        {"input": 'null', "description": "Null JSON"},
        {"input": '{"prompt": null}', "description": "Null prompt"},

        # Missing required fields
        {"input": '{}', "description": "Empty JSON object"},
        {"input": '{"session_id": "test"}', "description": "Missing prompt"},
        {"input": '{"prompt": "/test"}', "description": "Missing session_id"},

        # Extreme inputs
        {"input": '{"prompt": "' + "a" * 10000 + '"}', "description": "Very long prompt"},
        {"input": '{"prompt": "/test", "session_id": "' + "b" * 1000 + '"}', "description": "Very long session ID"},

        # Unicode and special characters
        {"input": '{"prompt": "/test 🚀 emoji", "session_id": "测试"}', "description": "Unicode characters"},
        {"input": '{"prompt": "/test\\n\\r\\t", "session_id": "test"}', "description": "Escape sequences"},

        # Edge case commands
        {"input": '{"prompt": "/", "session_id": "test"}', "description": "Single slash"},
        {"input": '{"prompt": "/unknown_command", "session_id": "test"}', "description": "Unknown command"},
        {"input": '{"prompt": "/autorun", "session_id": "test"}', "description": "Autorun without args"},
        {"input": '{"prompt": "/clautorun", "session_id": "test"}', "description": "Clautorun without args"},

        # Command variations
        {"input": '{"prompt": "/CLAUTORUN /afs", "session_id": "test"}', "description": "Uppercase command"},
        {"input": '{"prompt": "/clautorun /afs", "session_id": "test"}', "description": "Mixed case command"},
        {"input": '{"prompt": "/clautorun    /afs", "session_id": "test"}', "description": "Extra spaces"},
        {"input": '{"prompt": "/clautorun/afs", "session_id": "test"}', "description": "No space separator"},
    ]

    for i, test_case in enumerate(test_cases, 1):
        try:
            with patch('sys.stdin', Mock(read=Mock(return_value=test_case["input"]))):
                with patch('sys.stdout', Mock()) as mock_stdout:
                    with patch('sys.stderr', Mock()):
                        main()

                        # Check if output was written
                        if mock_stdout.write.called:
                            output_calls = [call[0][0] for call in mock_stdout.write.call_args_list]
                            output = ''.join(output_calls)

                            try:
                                result = json.loads(output)
                                assert isinstance(result, dict)
                                assert 'continue' in result
                                assert 'response' in result
                                print(f"✅ Test {i}: {test_case['description']} - Valid JSON response")
                            except json.JSONDecodeError:
                                print(f"⚠️ Test {i}: {test_case['description']} - Invalid JSON response")
                        else:
                            print(f"⚠️ Test {i}: {test_case['description']} - No output")

        except Exception as e:
            print(f"❌ Test {i}: {test_case['description']} - Error: {e}")

def test_injection_monitoring_edge_cases():
    """Test edge cases for injection_monitoring.py"""
    print("\n🔍 Testing Injection Monitoring Edge Cases...")

    from clautorun.injection_monitoring import InjectionEffectivenessMonitor, InjectionMethod, InjectionOutcome

    monitor = InjectionEffectivenessMonitor()

    # Test edge cases for record_injection_attempt
    edge_cases = [
        # Extreme values
        {
            "method": InjectionMethod.API_DIRECT,
            "session_id": "",
            "prompt_type": "",
            "prompt_content": "",
            "outcome": InjectionOutcome.SUCCESS,
            "response_time_ms": -1.0,  # Negative response time
            "description": "Negative response time"
        },
        {
            "method": InjectionMethod.API_DIRECT,
            "session_id": "test",
            "prompt_type": "test",
            "prompt_content": "test",
            "outcome": InjectionOutcome.SUCCESS,
            "response_time_ms": float('inf'),  # Infinite response time
            "description": "Infinite response time"
        },
        {
            "method": InjectionMethod.API_DIRECT,
            "session_id": "test",
            "prompt_type": "test",
            "prompt_content": "test",
            "outcome": InjectionOutcome.SUCCESS,
            "response_time_ms": 0.0,  # Zero response time
            "description": "Zero response time"
        },
        # Very long strings
        {
            "method": InjectionMethod.API_DIRECT,
            "session_id": "x" * 1000,
            "prompt_type": "y" * 1000,
            "prompt_content": "z" * 10000,
            "outcome": InjectionOutcome.SUCCESS,
            "response_time_ms": 100.0,
            "description": "Very long strings"
        },
        # Unicode edge cases
        {
            "method": InjectionMethod.API_DIRECT,
            "session_id": "测试🚀",
            "prompt_type": "测试类型",
            "prompt_content": "测试内容 with émojis 🎉",
            "outcome": InjectionOutcome.SUCCESS,
            "response_time_ms": 100.0,
            "description": "Unicode content"
        }
    ]

    for i, case in enumerate(edge_cases, 1):
        try:
            # Extract description separately since it's not a function parameter
            description = case.pop("description")
            attempt_id = monitor.record_injection_attempt(**case)
            print(f"✅ Test {i}: {description} - Attempt ID: {attempt_id}")
            case["description"] = description  # Restore for potential use
        except Exception as e:
            description = case.get("description", f"Test {i}")
            print(f"❌ Test {i}: {description} - Error: {e}")

    # Test metrics calculation edge cases
    try:
        # Calculate metrics with no data
        metrics = monitor.calculate_metrics(
            method=InjectionMethod.TMUX_INJECTION,  # Method with no attempts
            time_window_hours=1.0
        )
        assert metrics.total_attempts == 0
        assert metrics.success_rate == 0.0
        print("✅ Metrics calculation with no data")

        # Calculate metrics with negative time window
        metrics = monitor.calculate_metrics(time_window_hours=-1.0)
        print("✅ Metrics calculation with negative time window")

        # Calculate metrics with very large time window
        metrics = monitor.calculate_metrics(time_window_hours=999999.0)
        print("✅ Metrics calculation with large time window")

    except Exception as e:
        print(f"❌ Metrics calculation edge case failed: {e}")

def test_tmux_utils_edge_cases():
    """Test edge cases for tmux_utils.py"""
    print("\n🔍 Testing Tmux Utils Edge Cases...")

    try:
        from clautorun.tmux_utils import get_tmux_utilities
        tmux = get_tmux_utilities()

        # Test edge cases
        edge_cases = [
            ("", "Empty session ID"),
            ("   ", "Whitespace session ID"),
            ("../../../etc/passwd", "Path traversal attempt"),
            ("session\nwith\nnewlines", "Session with newlines"),
            ("session\twith\ttabs", "Session with tabs"),
            ("session\rwith\rcarriage", "Session with carriage returns"),
            ("🚀emoji🎉session", "Emoji session"),
            ("a" * 1000, "Very long session ID"),
            ("../../../", "Trailing path separator"),
            ("CON", "Windows reserved name"),
            ("aux", "Windows reserved name"),
            ("session with spaces", "Spaces in session"),
        ]

        for session_id, description in edge_cases:
            try:
                result = tmux.get_session_info(session_id)
                print(f"✅ {description}: Handled gracefully")
            except Exception as e:
                print(f"⚠️ {description}: {e}")

        # Test control sequence parsing edge cases
        control_cases = [
            ("", "Empty string"),
            ("^", "Single caret"),
            ("^^", "Double caret"),
            ("^^^", "Triple caret"),
            ("^a^b^c", "Multiple carets"),
            ("text^", "Trailing caret"),
            ("^text", "Leading caret"),
            ("text^^text^^text", "Mixed double carets"),
            ("🚀^emoji🎀", "Unicode with caret"),
        ]

        for text, description in control_cases:
            try:
                result = tmux.parse_control_sequences(text)
                print(f"✅ Control parsing - {description}: {result}")
            except Exception as e:
                print(f"⚠️ Control parsing - {description}: {e}")

    except ImportError:
        print("⚠️ Tmux utils not available, skipping tests")

def test_concurrent_operations():
    """Test concurrent operations and thread safety"""
    print("\n🔍 Testing Concurrent Operations...")

    # Test injection monitoring thread safety
    from clautorun.injection_monitoring import get_injection_monitor, InjectionMethod, InjectionOutcome

    monitor = get_injection_monitor()
    results = []
    errors = []

    def worker(worker_id):
        try:
            for i in range(10):
                attempt_id = monitor.record_injection_attempt(
                    method=InjectionMethod.API_DIRECT,
                    session_id=f"worker_{worker_id}",
                    prompt_type="concurrent_test",
                    prompt_content=f"Test from worker {worker_id} - attempt {i}",
                    outcome=InjectionOutcome.SUCCESS,
                    response_time_ms=100.0 + i
                )
                results.append((worker_id, i, attempt_id))
                time.sleep(0.001)  # Small delay
        except Exception as e:
            errors.append((worker_id, str(e)))

    # Start multiple threads
    threads = []
    for worker_id in range(5):
        thread = threading.Thread(target=worker, args=(worker_id,))
        threads.append(thread)
        thread.start()

    # Wait for all threads to complete
    for thread in threads:
        thread.join()

    print(f"✅ Concurrent operations completed: {len(results)} records, {len(errors)} errors")
    if errors:
        for worker_id, error in errors:
            print(f"❌ Worker {worker_id} error: {error}")

def test_data_persistence_edge_cases():
    """Test data persistence and recovery scenarios"""
    print("\n🔍 Testing Data Persistence Edge Cases...")

    import tempfile
    import shutil
    import json

    # Create temporary directory for testing
    temp_dir = tempfile.mkdtemp()

    try:
        from clautorun.injection_monitoring import InjectionEffectivenessMonitor, InjectionMethod, InjectionOutcome

        # Test with invalid storage directory
        try:
            monitor = InjectionEffectivenessMonitor(storage_dir=Path("/invalid/path/that/does/not/exist"))
            print("✅ Invalid storage directory handled gracefully")
        except Exception as e:
            print(f"⚠️ Invalid storage directory: {e}")

        # Test with read-only directory
        readonly_dir = Path(temp_dir) / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o444)  # Read-only

        try:
            monitor = InjectionEffectivenessMonitor(storage_dir=readonly_dir)
            monitor.record_injection_attempt(
                method=InjectionMethod.API_DIRECT,
                session_id="test",
                prompt_type="test",
                prompt_content="test",
                outcome=InjectionOutcome.SUCCESS,
                response_time_ms=100.0
            )
            print("✅ Read-only directory handled gracefully")
        except Exception as e:
            print(f"⚠️ Read-only directory: {e}")
        finally:
            readonly_dir.chmod(0o755)  # Restore permissions for cleanup

        # Test corrupted data file
        monitor = InjectionEffectivenessMonitor(storage_dir=Path(temp_dir))
        data_file = monitor.storage_dir / "injection_data.json"

        # Write corrupted JSON
        with open(data_file, 'w') as f:
            f.write('{"invalid": json content}')

        try:
            # Create new monitor instance to test loading
            monitor2 = InjectionEffectivenessMonitor(storage_dir=Path(temp_dir))
            print("✅ Corrupted JSON file handled gracefully")
        except Exception as e:
            print(f"⚠️ Corrupted JSON file: {e}")

        # Test truncated data file
        with open(data_file, 'w') as f:
            f.write('{"attempts": [{"incomplete":')

        try:
            monitor3 = InjectionEffectivenessMonitor(storage_dir=Path(temp_dir))
            print("✅ Truncated JSON file handled gracefully")
        except Exception as e:
            print(f"⚠️ Truncated JSON file: {e}")

        # Test data file with wrong structure
        with open(data_file, 'w') as f:
            json.dump({"wrong_structure": {"data": "here"}}, f)

        try:
            monitor4 = InjectionEffectivenessMonitor(storage_dir=Path(temp_dir))
            print("✅ Wrong structure JSON handled gracefully")
        except Exception as e:
            print(f"⚠️ Wrong structure JSON: {e}")

    finally:
        # Cleanup
        shutil.rmtree(temp_dir, ignore_errors=True)

def test_boundary_conditions():
    """Test boundary conditions and input validation"""
    print("\n🔍 Testing Boundary Conditions...")

    from clautorun.injection_monitoring import InjectionEffectivenessMonitor, InjectionMethod, InjectionOutcome

    monitor = InjectionEffectivenessMonitor()

    # Test boundary values
    boundary_cases = [
        (0.0, "Zero response time"),
        (0.001, "Very small response time"),
        (999999.999, "Very large response time"),
        (-0.001, "Negative small response time"),
        (-999.0, "Negative large response time"),
        (float('inf'), "Infinite response time"),
        (float('-inf'), "Negative infinite response time"),
    ]

    for response_time, description in boundary_cases:
        try:
            attempt_id = monitor.record_injection_attempt(
                method=InjectionMethod.API_DIRECT,
                session_id="boundary_test",
                prompt_type="boundary_test",
                prompt_content="boundary test",
                outcome=InjectionOutcome.SUCCESS,
                response_time_ms=response_time
            )
            print(f"✅ {description}: {attempt_id}")
        except Exception as e:
            print(f"⚠️ {description}: {e}")

    # Test count boundaries
    try:
        # Test with max_records boundary
        small_monitor = InjectionEffectivenessMonitor(max_records=1)

        # Add multiple records
        for i in range(3):
            small_monitor.record_injection_attempt(
                method=InjectionMethod.API_DIRECT,
                session_id=f"boundary_test_{i}",
                prompt_type="test",
                prompt_content="test",
                outcome=InjectionOutcome.SUCCESS,
                response_time_ms=100.0
            )

        # Should only keep 1 record
        assert len(small_monitor.injection_attempts) <= 1
        print("✅ Max records boundary enforced")

    except Exception as e:
        print(f"❌ Max records boundary test failed: {e}")

def test_error_handling_modes():
    """Test various error handling and failure modes"""
    print("\n🔍 Testing Error Handling Modes...")

    # Test import error handling
    try:
        with patch.dict('sys.modules', {'clautorun.diagnostics': None}):
            # This should gracefully handle missing diagnostics
            from clautorun.injection_monitoring import InjectionEffectivenessMonitor
            monitor = InjectionEffectivenessMonitor()
            print("✅ Missing diagnostics handled gracefully")
    except Exception as e:
        print(f"⚠️ Missing diagnostics handling: {e}")

    # Test transcript analyzer unavailability
    try:
        with patch.dict('sys.modules', {'clautorun.transcript_analyzer': None}):
            from clautorun.injection_monitoring import InjectionEffectivenessMonitor
            monitor = InjectionEffectivenessMonitor()
            print("✅ Missing transcript analyzer handled gracefully")
    except Exception as e:
        print(f"⚠️ Missing transcript analyzer handling: {e}")

    # Test filesystem error scenarios
    import tempfile
    import os

    temp_dir = tempfile.mkdtemp()

    try:
        from clautorun.injection_monitoring import InjectionEffectivenessMonitor
        monitor = InjectionEffectivenessMonitor(storage_dir=Path(temp_dir))

        # Remove directory permissions during operation
        data_file = monitor.storage_dir / "test_file.json"
        monitor.storage_dir.chmod(0o000)  # No permissions

        try:
            attempt_id = monitor.record_injection_attempt(
                method="api_direct",
                session_id="permission_test",
                prompt_type="test",
                prompt_content="test",
                outcome="success",
                response_time_ms=100.0
            )
            print("✅ Filesystem permission error handled gracefully")
        except Exception as e:
            print(f"⚠️ Filesystem permission error: {e}")
        finally:
            monitor.storage_dir.chmod(0o755)  # Restore permissions

    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

def run_all_edge_case_tests():
    """Run comprehensive edge case test suite"""
    print("🚀 Starting Comprehensive Edge Case Testing")
    print("=" * 60)

    test_plugin_edge_cases()
    test_injection_monitoring_edge_cases()
    test_tmux_utils_edge_cases()
    test_concurrent_operations()
    test_data_persistence_edge_cases()
    test_boundary_conditions()
    test_error_handling_modes()

    print("\n" + "=" * 60)
    print("🎉 Edge Case Testing Complete!")
    print("Review the output above for any ❌ errors or ⚠️ warnings.")

if __name__ == "__main__":
    run_all_edge_case_tests()