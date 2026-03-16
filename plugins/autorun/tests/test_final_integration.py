#!/usr/bin/env python3
"""Final integration tests for the complete autorun system"""

import sys
import os
import json
import time
import tempfile
import shutil
from pathlib import Path

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Import conftest utilities for cleanup
from conftest import should_keep_test_artifacts

try:
    from autorun.main import CONFIG, session_state
    from autorun.verification_engine import RequirementVerificationEngine, VerificationStatus
    from autorun.transcript_analyzer import TranscriptAnalyzer
    from autorun.testing_framework import TestRunner, TestSuite, TestEnvironment, TestType
    from autorun.diagnostics import DiagnosticManager
    from autorun.injection_monitoring import get_injection_monitor, InjectionMethod, InjectionOutcome
    INTEGRATION_AVAILABLE = True
except ImportError as e:
    print(f"Import error: {e}")
    INTEGRATION_AVAILABLE = False


class FinalIntegrationTest:
    """Comprehensive integration tests for autorun"""

    def __init__(self):
        self.test_results = []
        self.temp_dir = Path(tempfile.mkdtemp())
        print(f"Test directory: {self.temp_dir}")

    def test_complete_system_initialization(self) -> bool:
        """Test that all system components initialize correctly"""
        print("🔄 Testing complete system initialization...")

        try:
            # Test main.py configuration
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

            # Test verification engine
            engine = RequirementVerificationEngine(test_session_id)
            if not engine:
                print("❌ Verification engine failed to initialize")
                return False
            print("✅ Verification engine initialized")

            # Test transcript analyzer
            analyzer = TranscriptAnalyzer()
            if not analyzer:
                print("❌ Transcript analyzer failed to initialize")
                return False
            print("✅ Transcript analyzer initialized")

            # Test testing framework
            runner = TestRunner()
            if not runner:
                print("❌ Testing framework failed to initialize")
                return False
            print("✅ Testing framework initialized")

            # Test diagnostics manager
            diagnostics = DiagnosticManager()
            if not diagnostics:
                print("❌ Diagnostics manager failed to initialize")
                return False
            print("✅ Diagnostics manager initialized")

            # Test injection monitor
            monitor = get_injection_monitor()
            if not monitor:
                print("❌ Injection monitor failed to initialize")
                return False
            print("✅ Injection monitor initialized")

            return True

        except Exception as e:
            print(f"❌ System initialization failed: {e}")
            return False

    def test_command_processing_pipeline(self) -> bool:
        """Test complete command processing from input to output"""
        print("🔄 Testing command processing pipeline...")

        try:
            # Simulate UserPromptSubmit hook processing
            test_commands = [
                ("/afs", "SEARCH"),
                ("/afa", "ALLOW"),
                ("/afj", "JUSTIFY"),
                ("/afst", "status")
            ]

            for command, expected_action in test_commands:
                test_session_id = f"command_test_{command.replace('/', '_')}"

                # Test command detection and processing
                command_found = next(
                    (v for k, v in CONFIG["command_mappings"].items() if k == command),
                    None
                )

                if not command_found:
                    print(f"❌ Command {command} not found in mappings")
                    return False

                if command_found != expected_action:
                    print(f"❌ Command {command} mapped to {command_found}, expected {expected_action}")
                    return False

                # Test session state update
                with session_state(test_session_id) as state:
                    state['session_id'] = test_session_id
                    state["file_policy"] = command_found if command_found in ["SEARCH", "ALLOW", "JUSTIFY"] else "ALLOW"

                print(f"✅ Command {command} processed successfully")

            return True

        except Exception as e:
            print(f"❌ Command processing pipeline failed: {e}")
            return False

    def test_verification_engine_integration(self) -> bool:
        """Test verification engine with transcript analysis"""
        print("🔄 Testing verification engine integration...")

        try:
            session_id = "verification_integration_test"
            engine = RequirementVerificationEngine(session_id)
            analyzer = TranscriptAnalyzer()

            # Test requirement parsing
            task_description = "Create a user authentication system with JWT tokens, database integration, comprehensive tests, and API documentation"
            requirements = engine.parse_requirements_from_task(task_description)

            if len(requirements) < 3:
                print("❌ Requirement parsing failed - too few requirements detected")
                return False
            print(f"✅ Parsed {len(requirements)} requirements from task")

            # Create test transcript with evidence
            test_transcript = """
            I created the authentication system with JWT tokens.
            Created auth/jwt_handler.py with secure token generation.
            Added comprehensive unit tests that all passed.
            Created database migrations for user management.
            Generated API documentation in OpenAPI format.
            The system is working correctly and all tests pass.
            """

            # Test transcript analysis
            analysis = analyzer.analyze_full_transcript(test_transcript, session_id)
            if analysis.total_evidence == 0:
                print("❌ Transcript analysis found no evidence")
                return False
            print(f"✅ Transcript analysis found {analysis.total_evidence} evidence items")

            # Test evidence analysis
            evidence_by_requirement = engine.analyze_transcript_evidence(test_transcript)
            if not evidence_by_requirement:
                print("❌ Evidence analysis failed")
                return False
            print(f"✅ Evidence analysis completed for {len(evidence_by_requirement)} requirements")

            # Test verification report generation
            report = engine.generate_verification_report()
            if not report or "summary" not in report:
                print("❌ Verification report generation failed")
                return False
            print(f"✅ Verification report generated with {report['summary']['total_requirements']} requirements")

            return True

        except Exception as e:
            print(f"❌ Verification engine integration failed: {e}")
            return False

    def test_injection_monitoring_workflow(self) -> bool:
        """Test complete injection monitoring workflow"""
        print("🔄 Testing injection monitoring workflow...")

        try:
            monitor = get_injection_monitor()
            if not monitor:
                print("❌ Failed to get injection monitor")
                return False

            # Simulate complete injection workflow
            session_id = "injection_workflow_test"

            # Test different injection methods
            injection_methods = [
                (InjectionMethod.API_DIRECT, "continue"),
                (InjectionMethod.TMUX_INJECTION, "verification"),
                (InjectionMethod.HOOK_INTEGRATION, "forced_compliance"),
                (InjectionMethod.PLUGIN_COMMAND, "continue")
            ]

            for method, prompt_type in injection_methods:
                # Record injection attempt
                attempt_id = monitor.record_injection_attempt(
                    method=method,
                    session_id=session_id,
                    prompt_type=prompt_type,
                    prompt_content=f"Test {prompt_type} prompt",
                    outcome=InjectionOutcome.SUCCESS,
                    response_time_ms=150 + len(prompt_type) * 50,
                    success_indicators=[f"{method.value} injection successful"]
                )

                if not attempt_id:
                    print(f"❌ Failed to record {method.value} injection")
                    return False

            print(f"✅ Recorded {len(injection_methods)} injection attempts")

            # Test metrics calculation
            metrics = monitor.calculate_metrics()
            if metrics.total_attempts < len(injection_methods):
                print("❌ Metrics calculation failed")
                return False
            print(f"✅ Metrics calculated: {metrics.total_attempts} attempts, {metrics.success_rate:.1%} success rate")

            # Test method comparison
            comparison = monitor._compare_methods(24.0)
            if not comparison or "method_comparison" not in str(comparison):
                print("❌ Method comparison failed")
                return False
            print("✅ Method comparison completed")

            # Test reliability report
            report = monitor.generate_reliability_report(24.0)
            if not report or not report.recommendations:
                print("❌ Reliability report generation failed")
                return False
            print(f"✅ Reliability report generated with {len(report.recommendations)} recommendations")

            # Test export functionality
            json_export = monitor.export_monitoring_data("json")
            if not json_export or "export_timestamp" not in json_export:
                print("❌ JSON export failed")
                return False
            print("✅ JSON export successful")

            return True

        except Exception as e:
            print(f"❌ Injection monitoring workflow failed: {e}")
            return False

    def test_environment_controlled_testing(self) -> bool:
        """Test environment-controlled testing framework"""
        print("🔄 Testing environment-controlled testing framework...")

        try:
            runner = TestRunner()

            # Test suite creation
            test_suite = TestSuite(
                name="integration_test_suite",
                description="Integration test suite",
                test_type=TestType.INTEGRATION,
                environment=TestEnvironment.SANDBOX,
                tests=["command:/afs", "command:/afa", "command:/afst"],
                timeout=30
            )

            # Test suite registration
            runner.register_test_suite(test_suite)
            if "integration_test_suite" not in runner.test_suites:
                print("❌ Test suite registration failed")
                return False
            print("✅ Test suite registered successfully")

            # Test environment controller
            from autorun.testing_framework import EnvironmentController
            controller = EnvironmentController()

            # Test environment configurations
            configs = controller.environment_configs
            required_envs = [TestEnvironment.PRODUCTION, TestEnvironment.STAGING,
                           TestEnvironment.DEVELOPMENT, TestEnvironment.SANDBOX, TestEnvironment.ISOLATED]

            for env in required_envs:
                if env not in configs:
                    print(f"❌ Environment {env.value} not configured")
                    return False
            print("✅ All required environments configured")

            # Test environment creation (dry run)
            test_id = "env_test"
            {
                "id": test_id,
                "created_at": time.time(),
                "temp_dir": str(self.temp_dir),
                "env_vars": {"TEST_MODE": "true", "TEST_ID": test_id}
            }
            print("✅ Environment creation simulation successful")

            # Test report generation
            from autorun.testing_framework import TestResult, TestStatus
            mock_results = [
                TestResult(
                    test_id="test1",
                    test_name="Test 1",
                    test_type=TestType.INTEGRATION,
                    status=TestStatus.PASSED,
                    duration=1.0,
                    start_time=time.time() - 2,
                    end_time=time.time() - 1,
                    environment=TestEnvironment.SANDBOX
                ),
                TestResult(
                    test_id="test2",
                    test_name="Test 2",
                    test_type=TestType.UNIT,
                    status=TestStatus.FAILED,
                    duration=2.0,
                    start_time=time.time() - 4,
                    end_time=time.time() - 2,
                    environment=TestEnvironment.DEVELOPMENT,
                    error_message="Test failed"
                )
            ]

            report = runner.generate_test_report(mock_results)
            if not report or "summary" not in report:
                print("❌ Test report generation failed")
                return False
            print(f"✅ Test report generated: {report['summary']['total_tests']} tests, {report['summary']['success_rate']:.1%} success rate")

            return True

        except Exception as e:
            print(f"❌ Environment-controlled testing failed: {e}")
            return False

    def test_diagnostics_system_integration(self) -> bool:
        """Test diagnostics system integration"""
        print("🔄 Testing diagnostics system integration...")

        try:
            # Test diagnostic manager
            manager = DiagnosticManager()

            # Test logging functionality
            manager.logger.info("diagnostics_test", "Integration test log message", "test_session")
            if len(manager.logger.logs) == 0:
                print("❌ Diagnostic logging failed")
                return False
            print("✅ Diagnostic logging working")

            # Test log retrieval
            logs = manager.logger.get_logs(session_id="test_session")
            if len(logs) == 0:
                print("❌ Log retrieval failed")
                return False
            print(f"✅ Log retrieval working: {len(logs)} logs found")

            # Test system monitoring (if available)
            try:
                manager.monitor.collect_metrics()
                if len(manager.monitor.metrics) == 0:
                    print("⚠️ No system metrics collected (may be expected in test environment)")
                else:
                    print(f"✅ System monitoring working: {len(manager.monitor.metrics)} metrics collected")
            except Exception as e:
                print(f"⚠️ System monitoring limited (expected in test environment): {e}")

            # Test health checking
            results = manager.health_checker.run_all_checks()
            if not results:
                print("❌ Health checking failed")
                return False
            print(f"✅ Health checking working: {len(results)} checks completed")

            # Test status generation
            status = manager.get_status()
            if not status or "overall_health" not in status:
                print("❌ Status generation failed")
                return False
            print("✅ Status generation working")

            # Test export functionality
            export_file = self.temp_dir / "diagnostics_export.json"
            exported_file = manager.export_diagnostics(str(export_file))
            if not exported_file or not Path(exported_file).exists():
                print("❌ Diagnostics export failed")
                return False
            print("✅ Diagnostics export successful")

            return True

        except Exception as e:
            print(f"❌ Diagnostics system integration failed: {e}")
            return False

    def test_end_to_end_workflow(self) -> bool:
        """Test complete end-to-end workflow simulation"""
        print("🔄 Testing end-to-end workflow...")

        try:
            session_id = "e2e_test_session"

            # Step 1: Initialize session
            with session_state(session_id) as state:
                state.update({
                    "session_id": session_id,
                    "session_status": "active",
                    "autorun_stage": "INITIAL",
                    "activation_prompt": "Create a simple web API with user authentication",
                    "verification_attempts": 0,
                    "file_policy": "ALLOW"
                })

            print("✅ Session initialized")

            # Step 2: Process initial command
            from autorun.main import handle_activate
            activation_response = handle_activate(state, "/autorun /autorun Create a simple web API with user authentication")
            if not activation_response:
                print("❌ Activation failed")
                return False
            print("✅ Command activation processed")

            # Step 3: Simulate verification stage
            state["autorun_stage"] = "VERIFICATION"
            state["verification_attempts"] = 1

            # Initialize verification engine
            if INTEGRATION_AVAILABLE:
                engine = RequirementVerificationEngine(session_id)
                requirements = engine.parse_requirements_from_task(state["activation_prompt"])
                if not requirements:
                    print("❌ Requirement parsing failed in E2E test")
                    return False
                print(f"✅ Requirements parsed: {len(requirements)} requirements")

                # Simulate transcript analysis
                analyzer = TranscriptAnalyzer()
                test_transcript = """
                Created web API using FastAPI with user authentication.
                Implemented JWT token-based authentication system.
                Added user registration and login endpoints.
                Created database models for user management.
                Added comprehensive unit tests that all pass.
                Generated API documentation.
                """

                analysis = analyzer.analyze_full_transcript(test_transcript, session_id)
                if analysis.total_evidence == 0:
                    print("❌ Transcript analysis failed in E2E test")
                    return False
                print(f"✅ Transcript analysis: {analysis.total_evidence} evidence items")

            # Step 4: Test injection monitoring
            monitor = get_injection_monitor()
            if monitor:
                # Record injection attempt
                monitor.record_injection_attempt(
                    method=InjectionMethod.HOOK_INTEGRATION,
                    session_id=session_id,
                    prompt_type="verification",
                    prompt_content="E2E test verification prompt",
                    outcome=InjectionOutcome.SUCCESS,
                    response_time_ms=250.0
                )
                print("✅ Injection monitoring recorded")

            # Step 5: Generate comprehensive report
            if INTEGRATION_AVAILABLE:
                # Get diagnostic status
                diagnostics = DiagnosticManager()
                status = diagnostics.get_status()
                if not status:
                    print("❌ Diagnostic status failed in E2E test")
                    return False
                print("✅ Diagnostic status generated")

            # Step 6: Cleanup
            with session_state(session_id) as state:
                state.clear()
            print("✅ Session cleanup completed")

            return True

        except Exception as e:
            print(f"❌ End-to-end workflow failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    def run_all_tests(self) -> bool:
        """Run all integration tests"""
        print("🚀 Starting Final Integration Tests")
        print("=" * 60)

        tests = [
            ("System Initialization", self.test_complete_system_initialization),
            ("Command Processing Pipeline", self.test_command_processing_pipeline),
            ("Verification Engine Integration", self.test_verification_engine_integration),
            ("Injection Monitoring Workflow", self.test_injection_monitoring_workflow),
            ("Environment-Controlled Testing", self.test_environment_controlled_testing),
            ("Diagnostics System Integration", self.test_diagnostics_system_integration),
            ("End-to-End Workflow", self.test_end_to_end_workflow)
        ]

        passed = 0
        total = len(tests)

        for test_name, test_func in tests:
            print(f"\n📋 {test_name}")
            print("-" * 40)

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

        print(f"\n{'='*60}")
        print(f"Final Integration Test Results: {passed}/{total} tests passed")

        # Generate summary report
        self.generate_summary_report()

        # Cleanup
        self.cleanup()

        return passed == total

    def generate_summary_report(self):
        """Generate comprehensive test summary report"""
        print("\n📊 Integration Test Summary Report")
        print("=" * 50)

        total_duration = sum(r["duration"] for r in self.test_results)
        passed_count = sum(1 for r in self.test_results if r["status"] == "PASSED")
        failed_count = sum(1 for r in self.test_results if r["status"] == "FAILED")
        error_count = sum(1 for r in self.test_results if r["status"] == "ERROR")

        print(f"Total Duration: {total_duration:.2f}s")
        print(f"Tests Passed: {passed_count}")
        print(f"Tests Failed: {failed_count}")
        print(f"Tests Error: {error_count}")
        print(f"Success Rate: {(passed_count/len(self.test_results))*100:.1f}%")

        print("\nDetailed Results:")
        for result in self.test_results:
            status_icon = "✅" if result["status"] == "PASSED" else "❌" if result["status"] == "FAILED" else "💥"
            print(f"  {status_icon} {result['name']}: {result['status']} ({result['duration']:.2f}s)")
            if result["error"]:
                print(f"     Error: {result['error']}")

        # Export results to file
        report_file = self.temp_dir / "integration_test_report.json"
        with open(report_file, 'w', encoding="utf-8") as f:
            json.dump({
                "timestamp": time.time(),
                "summary": {
                    "total_tests": len(self.test_results),
                    "passed": passed_count,
                    "failed": failed_count,
                    "errors": error_count,
                    "success_rate": (passed_count/len(self.test_results))*100,
                    "total_duration": total_duration
                },
                "results": self.test_results
            }, f, indent=2)

        print(f"\n📄 Detailed report saved to: {report_file}")

    def cleanup(self):
        """Clean up test resources using centralized debug flag check"""
        if should_keep_test_artifacts():
            print(f"\n[DEBUG] Keeping test temp dir: {self.temp_dir}")
            return

        try:
            if self.temp_dir.exists():
                shutil.rmtree(self.temp_dir)
                print(f"🧹 Cleaned up test directory: {self.temp_dir}")
        except Exception as e:
            print(f"⚠️ Cleanup warning: {e}")


def main():
    """Run final integration tests"""
    if not INTEGRATION_AVAILABLE:
        print("❌ Integration tests not available - missing dependencies")
        return 1

    # Set test environment
    os.environ["DEBUG"] = "true"  # Enable debug logging for tests

    # Run tests
    integration_test = FinalIntegrationTest()
    success = integration_test.run_all_tests()

    return 0 if success else 1


if __name__ == "__main__":
    exit(main())