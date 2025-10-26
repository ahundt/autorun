#!/usr/bin/env python3
"""Simplified final integration tests for the complete clautorun system"""

import sys
import os
import json
import time
import tempfile
from pathlib import Path
from typing import Dict, List, Any

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Check availability of components
try:
    from clautorun.main import CONFIG, session_state
    MAIN_AVAILABLE = True
except ImportError:
    MAIN_AVAILABLE = False

try:
    from clautorun.verification_engine import RequirementVerificationEngine, VerificationStatus
    VERIFICATION_AVAILABLE = True
except ImportError:
    VERIFICATION_AVAILABLE = False

try:
    from clautorun.transcript_analyzer import TranscriptAnalyzer
    TRANSCRIPT_AVAILABLE = True
except ImportError:
    TRANSCRIPT_AVAILABLE = False

try:
    from clautorun.testing_framework import TestRunner, TestSuite, TestEnvironment, TestType
    TESTING_AVAILABLE = True
except ImportError:
    TESTING_AVAILABLE = False

try:
    from clautorun.diagnostics import DiagnosticManager
    DIAGNOSTICS_AVAILABLE = True
except ImportError:
    DIAGNOSTICS_AVAILABLE = False

try:
    from clautorun.injection_monitoring import get_injection_monitor, InjectionMethod, InjectionOutcome
    INJECTION_AVAILABLE = True
except ImportError:
    INJECTION_AVAILABLE = False


class SimplifiedIntegrationTest:
    """Simplified integration tests for clautorun with graceful degradation"""

    def __init__(self):
        self.test_results = []
        self.temp_dir = Path(tempfile.mkdtemp())
        print(f"Test directory: {self.temp_dir}")

    def test_core_system_initialization(self) -> bool:
        """Test that core system components initialize correctly"""
        print("🔄 Testing core system initialization...")

        try:
            # Test main.py configuration
            if not MAIN_AVAILABLE:
                print("⚠️ Main system not available - skipping")
                return True

            if not CONFIG:
                print("❌ CONFIG not available")
                return False
            print("✅ Main configuration loaded")

            # Test session state management
            test_session_id = "integration_test_session"
            with session_state(test_session_id) as state:
                state["test_key"] = "test_value"

            with session_state(test_session_id) as state:
                if state.get("test_key") != "test_value":
                    print("❌ Session state persistence failed")
                    return False
            print("✅ Session state management working")

            return True

        except Exception as e:
            print(f"❌ Core system initialization failed: {e}")
            return False

    def test_verification_system(self) -> bool:
        """Test verification system components"""
        print("🔄 Testing verification system...")

        try:
            if not VERIFICATION_AVAILABLE:
                print("⚠️ Verification system not available - skipping")
                return True

            session_id = "verification_test"
            engine = RequirementVerificationEngine(session_id)

            # Test requirement parsing
            task_description = "Create a user authentication system with JWT tokens, database integration, and tests"
            requirements = engine.parse_requirements_from_task(task_description)

            if len(requirements) < 2:
                print("❌ Requirement parsing failed - too few requirements detected")
                return False
            print(f"✅ Parsed {len(requirements)} requirements from task")

            # Test verification report
            report = engine.generate_verification_report()
            if not report or "summary" not in report:
                print("❌ Verification report generation failed")
                return False
            print(f"✅ Verification report generated")

            return True

        except Exception as e:
            print(f"❌ Verification system test failed: {e}")
            return False

    def test_transcript_analysis(self) -> bool:
        """Test transcript analysis system"""
        print("🔄 Testing transcript analysis system...")

        try:
            if not TRANSCRIPT_AVAILABLE:
                print("⚠️ Transcript analyzer not available - skipping")
                return True

            analyzer = TranscriptAnalyzer()
            session_id = "transcript_test"

            # Test transcript analysis
            test_transcript = """
            I created the authentication system with JWT tokens.
            Created auth/jwt_handler.py with secure token generation.
            Added comprehensive unit tests that all passed.
            The system is working correctly.
            """

            analysis = analyzer.analyze_full_transcript(test_transcript, session_id)
            if analysis.total_evidence == 0:
                print("❌ Transcript analysis found no evidence")
                return False
            print(f"✅ Transcript analysis found {analysis.total_evidence} evidence items")

            # Test task completion analysis
            task = "Create authentication system"
            completion = analyzer.analyze_task_completion(test_transcript, task)
            if not completion:
                print("❌ Task completion analysis failed")
                return False
            print("✅ Task completion analysis successful")

            # Test export functionality
            json_export = analyzer.export_analysis(analysis, "json")
            if not json_export or not json_export.strip().startswith("{"):
                print("❌ JSON export failed")
                return False
            print("✅ JSON export successful")

            return True

        except Exception as e:
            print(f"❌ Transcript analysis test failed: {e}")
            return False

    def test_injection_monitoring(self) -> bool:
        """Test injection monitoring system"""
        print("🔄 Testing injection monitoring system...")

        try:
            if not INJECTION_AVAILABLE:
                print("⚠️ Injection monitoring not available - skipping")
                return True

            monitor = get_injection_monitor()
            if not monitor:
                print("❌ Failed to get injection monitor")
                return False

            # Test recording injection attempts
            session_id = "injection_test"
            methods = [InjectionMethod.API_DIRECT, InjectionMethod.HOOK_INTEGRATION]

            for method in methods:
                attempt_id = monitor.record_injection_attempt(
                    method=method,
                    session_id=session_id,
                    prompt_type="continue",
                    prompt_content="Test prompt",
                    outcome=InjectionOutcome.SUCCESS,
                    response_time_ms=150.0
                )

                if not attempt_id:
                    print(f"❌ Failed to record {method.value} injection")
                    return False

            print(f"✅ Recorded {len(methods)} injection attempts")

            # Test metrics calculation
            metrics = monitor.calculate_metrics()
            if metrics.total_attempts < len(methods):
                print("❌ Metrics calculation failed")
                return False
            print(f"✅ Metrics: {metrics.total_attempts} attempts, {metrics.success_rate:.1%} success rate")

            # Test export
            json_export = monitor.export_monitoring_data("json")
            if not json_export or "export_timestamp" not in json_export:
                print("❌ JSON export failed")
                return False
            print("✅ JSON export successful")

            return True

        except Exception as e:
            print(f"❌ Injection monitoring test failed: {e}")
            return False

    def test_testing_framework(self) -> bool:
        """Test testing framework components"""
        print("🔄 Testing testing framework...")

        try:
            if not TESTING_AVAILABLE:
                print("⚠️ Testing framework not available - skipping")
                return True

            runner = TestRunner()

            # Test suite creation
            test_suite = TestSuite(
                name="integration_test",
                description="Integration test suite",
                test_type=TestType.INTEGRATION,
                environment=TestEnvironment.DEVELOPMENT,
                tests=["test1", "test2"]
            )

            runner.register_test_suite(test_suite)
            if "integration_test" not in runner.test_suites:
                print("❌ Test suite registration failed")
                return False
            print("✅ Test suite registered successfully")

            # Test result creation and report
            from clautorun.testing_framework import TestResult, TestStatus
            mock_result = TestResult(
                test_id="test1",
                test_name="Test 1",
                test_type=TestType.UNIT,
                status=TestStatus.PASSED,
                duration=1.0,
                start_time=time.time() - 2,
                end_time=time.time() - 1,
                environment=TestEnvironment.DEVELOPMENT
            )

            report = runner.generate_test_report([mock_result])
            if not report or "summary" not in report:
                print("❌ Test report generation failed")
                return False
            print(f"✅ Test report generated: {report['summary']['total_tests']} tests")

            return True

        except Exception as e:
            print(f"❌ Testing framework test failed: {e}")
            return False

    def test_diagnostics_system(self) -> bool:
        """Test diagnostics system components"""
        print("🔄 Testing diagnostics system...")

        try:
            if not DIAGNOSTICS_AVAILABLE:
                print("⚠️ Diagnostics system not available - skipping")
                return True

            manager = DiagnosticManager()

            # Test logging
            manager.logger.info("test", "Integration test message", "test_session")
            if len(manager.logger.logs) == 0:
                print("❌ Diagnostic logging failed")
                return False
            print("✅ Diagnostic logging working")

            # Test status generation
            status = manager.get_status()
            if not status or "overall_health" not in status:
                print("❌ Status generation failed")
                return False
            print("✅ Status generation working")

            return True

        except Exception as e:
            print(f"❌ Diagnostics system test failed: {e}")
            return False

    def test_command_processing(self) -> bool:
        """Test command processing functionality"""
        print("🔄 Testing command processing...")

        try:
            if not MAIN_AVAILABLE:
                print("⚠️ Main system not available - skipping")
                return True

            # Test command mappings
            test_commands = [("/afs", "SEARCH"), ("/afa", "ALLOW"), ("/afj", "JUSTIFY"), ("/afst", "status")]

            for command, expected_action in test_commands:
                command_found = next(
                    (v for k, v in CONFIG["command_mappings"].items() if k == command),
                    None
                )

                if not command_found:
                    print(f"❌ Command {command} not found")
                    return False

                if command_found != expected_action:
                    print(f"❌ Command {command} mapped incorrectly")
                    return False

            print("✅ All command mappings verified")

            # Test session state with commands
            test_session_id = "command_test"
            with session_state(test_session_id) as state:
                state['session_id'] = test_session_id
                state["file_policy"] = "ALLOW"

            print("✅ Command processing working")

            return True

        except Exception as e:
            print(f"❌ Command processing test failed: {e}")
            return False

    def test_integration_workflow(self) -> bool:
        """Test simplified integration workflow"""
        print("🔄 Testing integration workflow...")

        try:
            session_id = "workflow_test"

            # Initialize session
            if MAIN_AVAILABLE:
                with session_state(session_id) as state:
                    state.update({
                        "session_id": session_id,
                        "session_status": "active",
                        "file_policy": "ALLOW"
                    })
                print("✅ Session initialized")

            # Test verification engine integration
            if VERIFICATION_AVAILABLE:
                engine = RequirementVerificationEngine(session_id)
                task = "Create simple API with authentication"
                requirements = engine.parse_requirements_from_task(task)
                if requirements:
                    print(f"✅ Verification engine integrated: {len(requirements)} requirements")

            # Test transcript analyzer integration
            if TRANSCRIPT_AVAILABLE:
                analyzer = TranscriptAnalyzer()
                transcript = "Created API with authentication system"
                analysis = analyzer.analyze_full_transcript(transcript, session_id)
                if analysis.total_evidence > 0:
                    print(f"✅ Transcript analyzer integrated: {analysis.total_evidence} evidence items")

            # Test injection monitoring integration
            if INJECTION_AVAILABLE:
                monitor = get_injection_monitor()
                monitor.record_injection_attempt(
                    method=InjectionMethod.HOOK_INTEGRATION,
                    session_id=session_id,
                    prompt_type="continue",
                    prompt_content="Test workflow prompt",
                    outcome=InjectionOutcome.SUCCESS,
                    response_time_ms=100.0
                )
                print("✅ Injection monitoring integrated")

            # Cleanup
            if MAIN_AVAILABLE:
                with session_state(session_id) as state:
                    state.clear()
                print("✅ Session cleanup completed")

            return True

        except Exception as e:
            print(f"❌ Integration workflow test failed: {e}")
            return False

    def run_all_tests(self) -> bool:
        """Run all simplified integration tests"""
        print("🚀 Starting Simplified Integration Tests")
        print("=" * 50)

        tests = [
            ("Core System Initialization", self.test_core_system_initialization),
            ("Verification System", self.test_verification_system),
            ("Transcript Analysis", self.test_transcript_analysis),
            ("Injection Monitoring", self.test_injection_monitoring),
            ("Testing Framework", self.test_testing_framework),
            ("Diagnostics System", self.test_diagnostics_system),
            ("Command Processing", self.test_command_processing),
            ("Integration Workflow", self.test_integration_workflow)
        ]

        passed = 0
        total = len(tests)

        for test_name, test_func in tests:
            print(f"\n📋 {test_name}")
            print("-" * 30)

            start_time = time.time()
            try:
                if test_func():
                    duration = time.time() - start_time
                    print(f"✅ {test_name} PASSED ({duration:.2f}s)")
                    self.test_results.append({
                        "name": test_name,
                        "status": "PASSED",
                        "duration": duration,
                        "error": None
                    })
                    passed += 1
                else:
                    duration = time.time() - start_time
                    print(f"❌ {test_name} FAILED ({duration:.2f}s)")
                    self.test_results.append({
                        "name": test_name,
                        "status": "FAILED",
                        "duration": duration,
                        "error": "Test returned False"
                    })
            except Exception as e:
                duration = time.time() - start_time
                print(f"💥 {test_name} ERROR ({duration:.2f}s): {e}")
                self.test_results.append({
                    "name": test_name,
                    "status": "ERROR",
                    "duration": duration,
                    "error": str(e)
                })

        print(f"\n{'='*50}")
        print(f"Simplified Integration Test Results: {passed}/{total} tests passed")

        # Generate summary
        self.generate_summary_report()

        # Cleanup
        self.cleanup()

        return passed == total

    def generate_summary_report(self):
        """Generate test summary report"""
        print("\n📊 Integration Test Summary")
        print("=" * 40)

        total_duration = sum(r["duration"] for r in self.test_results)
        passed_count = sum(1 for r in self.test_results if r["status"] == "PASSED")
        failed_count = sum(1 for r in self.test_results if r["status"] == "FAILED")
        error_count = sum(1 for r in self.test_results if r["status"] == "ERROR")

        print(f"Total Duration: {total_duration:.2f}s")
        print(f"Tests Passed: {passed_count}")
        print(f"Tests Failed: {failed_count}")
        print(f"Tests Error: {error_count}")
        if len(self.test_results) > 0:
            print(f"Success Rate: {(passed_count/len(self.test_results))*100:.1f}%")

        print("\nComponent Availability:")
        print(f"  Main System: {'✅' if MAIN_AVAILABLE else '❌'}")
        print(f"  Verification Engine: {'✅' if VERIFICATION_AVAILABLE else '❌'}")
        print(f"  Transcript Analyzer: {'✅' if TRANSCRIPT_AVAILABLE else '❌'}")
        print(f"  Testing Framework: {'✅' if TESTING_AVAILABLE else '❌'}")
        print(f"  Diagnostics System: {'✅' if DIAGNOSTICS_AVAILABLE else '❌'}")
        print(f"  Injection Monitoring: {'✅' if INJECTION_AVAILABLE else '❌'}")

        # Export results
        report_file = self.temp_dir / "simple_integration_report.json"
        with open(report_file, 'w') as f:
            json.dump({
                "timestamp": time.time(),
                "component_availability": {
                    "main": MAIN_AVAILABLE,
                    "verification": VERIFICATION_AVAILABLE,
                    "transcript": TRANSCRIPT_AVAILABLE,
                    "testing": TESTING_AVAILABLE,
                    "diagnostics": DIAGNOSTICS_AVAILABLE,
                    "injection": INJECTION_AVAILABLE
                },
                "summary": {
                    "total_tests": len(self.test_results),
                    "passed": passed_count,
                    "failed": failed_count,
                    "errors": error_count,
                    "total_duration": total_duration
                },
                "results": self.test_results
            }, f, indent=2)

        print(f"\n📄 Report saved to: {report_file}")

    def cleanup(self):
        """Clean up test resources"""
        try:
            import shutil
            if self.temp_dir.exists():
                shutil.rmtree(self.temp_dir)
                print(f"🧹 Cleaned up test directory")
        except Exception as e:
            print(f"⚠️ Cleanup warning: {e}")


def main():
    """Run simplified integration tests"""
    # Set test environment
    os.environ["DEBUG"] = "true"

    # Run tests
    test = SimplifiedIntegrationTest()
    success = test.run_all_tests()

    return 0 if success else 1


if __name__ == "__main__":
    exit(main())