#!/usr/bin/env python3
"""Integration tests for injection effectiveness monitoring"""

import sys
import os
import time

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

try:
    from autorun.main import get_injection_monitor, inject_continue_prompt, inject_verification_prompt
    from autorun.injection_monitoring import InjectionMethod, InjectionOutcome
    INTEGRATION_TEST_AVAILABLE = True
except ImportError as e:
    print(f"Import error: {e}")
    INTEGRATION_TEST_AVAILABLE = False


def test_injection_monitoring_integration():
    """Test injection monitoring integration with main.py"""
    if not INTEGRATION_TEST_AVAILABLE:
        print("❌ Injection monitoring integration not available")
        return False

    try:
        # Get the global monitor
        monitor = get_injection_monitor()
        if not monitor:
            print("❌ Failed to get injection monitor")
            return False

        print("✅ Injection monitor initialized successfully")

        # Test state for injection functions
        test_state = {
            "session_id": "integration_test_session",
            "file_policy": "ALLOW",
            "verification_attempts": 1,
            "activation_prompt": "Test task for integration",
            "transcript": "Test transcript content"
        }

        # Test continue prompt injection with monitoring
        print("🔄 Testing continue prompt injection...")
        initial_count = len(monitor.injection_attempts)

        inject_continue_prompt(test_state)

        # Check that injection was recorded
        if len(monitor.injection_attempts) > initial_count:
            print("✅ Continue prompt injection recorded")
        else:
            print("❌ Continue prompt injection not recorded")
            return False

        # Test verification prompt injection with monitoring
        print("🔄 Testing verification prompt injection...")
        initial_count = len(monitor.injection_attempts)

        inject_verification_prompt(test_state)

        # Check that injection was recorded
        if len(monitor.injection_attempts) > initial_count:
            print("✅ Verification prompt injection recorded")
        else:
            print("❌ Verification prompt injection not recorded")
            return False

        # Test metrics calculation
        metrics = monitor.calculate_metrics()
        print(f"✅ Metrics calculated: {metrics.total_attempts} total attempts, {metrics.success_rate:.1%} success rate")

        # Test export functionality
        json_export = monitor.export_monitoring_data("json")
        if json_export and "export_timestamp" in json_export:
            print("✅ JSON export successful")
        else:
            print("❌ JSON export failed")
            return False

        csv_export = monitor.export_monitoring_data("csv")
        if csv_export and "attempt_id,timestamp,method" in csv_export:
            print("✅ CSV export successful")
        else:
            print("❌ CSV export failed")
            return False

        return True

    except Exception as e:
        print(f"❌ Integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_injection_outcome_update():
    """Test injection outcome update functionality"""
    if not INTEGRATION_TEST_AVAILABLE:
        print("❌ Injection monitoring integration not available")
        return False

    try:
        monitor = get_injection_monitor()
        if not monitor:
            print("❌ Failed to get injection monitor")
            return False

        # Record a test injection with outcome to be updated
        test_state = {
            "session_id": "outcome_test_session",
            "last_injection_attempt_id": f"outcome_test_{int(time.time() * 1000)}",
            "last_injection_start_time": time.time()
        }

        # Manually add an attempt to be updated
        from autorun.injection_monitoring import InjectionAttempt
        attempt = InjectionAttempt(
            attempt_id=test_state["last_injection_attempt_id"],
            timestamp=test_state["last_injection_start_time"],
            method=InjectionMethod.HOOK_INTEGRATION,
            session_id=test_state["session_id"],
            prompt_type="continue",
            prompt_content="Test prompt",
            outcome=InjectionOutcome.SUCCESS,  # Will be updated
            response_time_ms=0,  # Will be updated
            success_indicators=["Test"]
        )
        monitor.injection_attempts.append(attempt)

        # Import and test the update function
        from autorun.main import update_injection_outcome

        # Wait a bit to have a measurable response time
        time.sleep(0.1)

        # Update the outcome
        update_injection_outcome(test_state, InjectionOutcome.PARTIAL, "Test error message")

        # Check that the attempt was updated
        updated_attempt = None
        for att in monitor.injection_attempts:
            if att.attempt_id == test_state["last_injection_attempt_id"]:
                updated_attempt = att
                break

        if updated_attempt and updated_attempt.outcome == InjectionOutcome.PARTIAL:
            if updated_attempt.response_time_ms > 0:
                print("✅ Injection outcome updated successfully")
                print(f"   Response time: {updated_attempt.response_time_ms:.1f}ms")
                print(f"   Error message: {updated_attempt.error_message}")
                return True
            else:
                print("❌ Response time not updated")
                return False
        else:
            print("❌ Injection attempt not found or not updated")
            return False

    except Exception as e:
        print(f"❌ Outcome update test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_reliability_report_generation():
    """Test reliability report generation"""
    if not INTEGRATION_TEST_AVAILABLE:
        print("❌ Injection monitoring integration not available")
        return False

    try:
        monitor = get_injection_monitor()
        if not monitor:
            print("❌ Failed to get injection monitor")
            return False

        # Generate multiple test attempts with different outcomes
        test_data = [
            (InjectionMethod.API_DIRECT, "continue", InjectionOutcome.SUCCESS, 200),
            (InjectionMethod.TMUX_INJECTION, "verification", InjectionOutcome.PARTIAL, 500),
            (InjectionMethod.HOOK_INTEGRATION, "forced_compliance", InjectionOutcome.SUCCESS, 300),
            (InjectionMethod.PLUGIN_COMMAND, "continue", InjectionOutcome.TIMEOUT, 5000)
        ]

        for method, prompt_type, outcome, response_time in test_data:
            monitor.record_injection_attempt(
                method=method,
                session_id=f"report_test_{method.value}",
                prompt_type=prompt_type,
                prompt_content=f"Test {prompt_type}",
                outcome=outcome,
                response_time_ms=response_time,
                success_indicators=[f"Test {method.value}"]
            )

        # Generate reliability report
        report = monitor.generate_reliability_report(time_period_hours=24.0)

        if (report.report_id and
            len(report.overall_metrics) > 0 and
            len(report.recommendations) > 0):

            print("✅ Reliability report generated successfully")
            print(f"   Report ID: {report.report_id}")
            print(f"   Overall metrics entries: {len(report.overall_metrics)}")
            print(f"   Recommendations: {len(report.recommendations)}")

            # Print sample recommendations
            for i, rec in enumerate(report.recommendations[:3], 1):
                print(f"   {i}. {rec}")

            return True
        else:
            print("❌ Reliability report generation failed")
            return False

    except Exception as e:
        print(f"❌ Reliability report test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all integration tests"""
    print("🚀 Running Injection Monitoring Integration Tests")
    print("=" * 50)

    tests = [
        ("Basic Integration Test", test_injection_monitoring_integration),
        ("Outcome Update Test", test_injection_outcome_update),
        ("Reliability Report Test", test_reliability_report_generation)
    ]

    passed = 0
    total = len(tests)

    for test_name, test_func in tests:
        print(f"\n📋 {test_name}")
        print("-" * 30)

        if test_func():
            print(f"✅ {test_name} PASSED")
            passed += 1
        else:
            print(f"❌ {test_name} FAILED")

    print(f"\n{'='*50}")
    print(f"Test Results: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 All integration tests passed!")
        return 0
    else:
        print("💥 Some integration tests failed!")
        return 1


if __name__ == "__main__":
    exit(main())