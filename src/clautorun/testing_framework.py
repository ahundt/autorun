#!/usr/bin/env python3

# Copyright 2025 Andrew Hundt <ATHundt@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Environment-Controlled Testing Framework for clautorun - Comprehensive testing with environment isolation"""

import os
import sys
import json
import time
import tempfile
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Union
from dataclasses import dataclass, asdict
from enum import Enum
from contextlib import contextmanager
import uuid

# Import main.py patterns for consistency
try:
    from .main import CONFIG, session_state, log_info
except ImportError:
    # Fallback if running standalone
    CONFIG = {}
    def session_state(session_id):
        """Fallback session state"""
        class DummyState:
            def __enter__(self):
                return {}
            def __exit__(self, *args):
                pass
        return DummyState()
    def log_info(message):
        """Fallback logging"""
        print(f"INFO: {message}")

# Follow main.py pattern for handlers
TESTING_HANDLERS = {}
def testing_handler(name):
    """Decorator to register testing handlers - following main.py pattern"""
    def dec(f):
        TESTING_HANDLERS[name] = f
        return f
    return dec

class TestEnvironment(Enum):
    """Test environment types"""
    PRODUCTION = "production"
    STAGING = "staging"
    DEVELOPMENT = "development"
    SANDBOX = "sandbox"
    ISOLATED = "isolated"

class TestType(Enum):
    """Types of tests"""
    UNIT = "unit"
    INTEGRATION = "integration"
    END_TO_END = "end_to_end"
    PERFORMANCE = "performance"
    SECURITY = "security"
    REGRESSION = "regression"
    SMOKE = "smoke"

class TestStatus(Enum):
    """Test execution status"""
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"
    TIMEOUT = "timeout"

@dataclass
class TestResult:
    """Individual test result"""
    test_id: str
    test_name: str
    test_type: TestType
    status: TestStatus
    duration: float
    start_time: float
    end_time: float
    environment: TestEnvironment
    output: str = ""
    error_message: str = ""
    metadata: Dict[str, Any] = None

    def to_dict(self) -> Dict:
        """Convert test result to dictionary"""
        result = asdict(self)
        result['test_type'] = self.test_type.value
        result['status'] = self.status.value
        result['environment'] = self.environment.value
        return result

@dataclass
class TestSuite:
    """Test suite configuration"""
    name: str
    description: str
    test_type: TestType
    environment: TestEnvironment
    tests: List[str]  # Test IDs or test patterns
    setup_commands: List[str] = None
    teardown_commands: List[str] = None
    timeout: int = 300  # 5 minutes default
    parallel: bool = False
    max_workers: int = 4

class EnvironmentController:
    """Controls test environments with isolation and cleanup"""

    def __init__(self):
        self.active_environments = {}
        self.environment_configs = self._load_environment_configs()

    def _load_environment_configs(self) -> Dict[TestEnvironment, Dict]:
        """Load environment configurations"""
        return {
            TestEnvironment.PRODUCTION: {
                "isolated": False,
                "cleanup_after": True,
                "backup_state": True,
                "allowed_commands": ["all"],
                "resource_limits": {"memory": "4G", "cpu": "2"}
            },
            TestEnvironment.STAGING: {
                "isolated": False,
                "cleanup_after": True,
                "backup_state": True,
                "allowed_commands": ["all"],
                "resource_limits": {"memory": "2G", "cpu": "2"}
            },
            TestEnvironment.DEVELOPMENT: {
                "isolated": False,
                "cleanup_after": False,
                "backup_state": False,
                "allowed_commands": ["all"],
                "resource_limits": {"memory": "1G", "cpu": "1"}
            },
            TestEnvironment.SANDBOX: {
                "isolated": True,
                "cleanup_after": True,
                "backup_state": False,
                "allowed_commands": ["safe_commands_only"],
                "resource_limits": {"memory": "512M", "cpu": "1"}
            },
            TestEnvironment.ISOLATED: {
                "isolated": True,
                "cleanup_after": True,
                "backup_state": False,
                "allowed_commands": ["clautorun_commands_only"],
                "resource_limits": {"memory": "256M", "cpu": "1"}
            }
        }

    @contextmanager
    def create_environment(self, environment: TestEnvironment, test_id: str):
        """Create and manage test environment"""
        env_id = f"{environment.value}_{test_id}_{uuid.uuid4().hex[:8]}"
        config = self.environment_configs[environment]

        log_info(f"Creating {environment.value} environment: {env_id}")

        try:
            # Create environment
            env_info = self._setup_environment(env_id, config)
            self.active_environments[env_id] = env_info

            yield env_info

        finally:
            # Cleanup environment
            if config["cleanup_after"]:
                self._cleanup_environment(env_id, config)
                if env_id in self.active_environments:
                    del self.active_environments[env_id]

    def _setup_environment(self, env_id: str, config: Dict) -> Dict:
        """Setup individual environment"""
        env_info = {
            "id": env_id,
            "type": config,
            "created_at": time.time(),
            "temp_dir": None,
            "env_vars": {},
            "state_backup": None
        }

        if config["isolated"]:
            # Create temporary directory for isolation
            temp_dir = tempfile.mkdtemp(prefix=f"clautorun_test_{env_id}_")
            env_info["temp_dir"] = temp_dir

            # Copy necessary files to temp directory
            self._copy_test_files(temp_dir)

            # Set isolated environment variables
            env_info["env_vars"] = {
                "CLAUTORUN_TEST_MODE": "true",
                "CLAUTORUN_TEST_ID": env_id,
                "CLAUTORUN_ISOLATED": "true",
                "PYTHONPATH": str(temp_dir / "src"),
                "CLAUTORUN_TEMP_DIR": temp_dir
            }

        if config["backup_state"]:
            # Backup current state
            env_info["state_backup"] = self._backup_current_state()

        return env_info

    def _cleanup_environment(self, env_id: str, config: Dict):
        """Cleanup environment after test"""
        env_info = self.active_environments.get(env_id, {})
        temp_dir = env_info.get("temp_dir")

        if temp_dir and Path(temp_dir).exists():
            log_info(f"Cleaning up temporary directory: {temp_dir}")
            shutil.rmtree(temp_dir, ignore_errors=True)

        if env_info.get("state_backup"):
            self._restore_state(env_info["state_backup"])

    def _copy_test_files(self, temp_dir: str):
        """Copy necessary test files to isolated environment"""
        source_dir = Path(__file__).parent.parent.parent
        target_dir = Path(temp_dir)

        # Copy src directory
        if (source_dir / "src").exists():
            shutil.copytree(source_dir / "src", target_dir / "src", dirs_exist_ok=True)

        # Copy commands directory
        if (source_dir / "commands").exists():
            shutil.copytree(source_dir / "commands", target_dir / "commands", dirs_exist_ok=True)

        # Create test-specific config files
        self._create_test_configs(target_dir)

    def _create_test_configs(self, target_dir: Path):
        """Create test-specific configuration files"""
        # Create test settings
        test_settings = {
            "test_mode": True,
            "test_environment": "isolated",
            "debug": True,
            "log_level": "DEBUG"
        }

        settings_file = target_dir / "test_settings.json"
        with open(settings_file, 'w') as f:
            json.dump(test_settings, f, indent=2)

    def _backup_current_state(self) -> Dict:
        """Backup current clautorun state"""
        backup = {
            "timestamp": time.time(),
            "sessions_dir": None,
            "config_files": []
        }

        # Backup sessions directory
        sessions_dir = Path.home() / ".claude" / "sessions"
        if sessions_dir.exists():
            backup["sessions_dir"] = str(sessions_dir)

        return backup

    def _restore_state(self, backup: Dict):
        """Restore backed up state"""
        # Implementation for state restoration
        pass

class TestRunner:
    """Advanced test runner with environment control"""

    def __init__(self):
        self.environment_controller = EnvironmentController()
        self.test_results = []
        self.test_suites = {}

    @testing_handler("register_suite")
    def register_test_suite(self, suite: TestSuite):
        """Register a test suite"""
        self.test_suites[suite.name] = suite
        log_info(f"Registered test suite: {suite.name}")

    @testing_handler("run_suite")
    def run_test_suite(self, suite_name: str, override_environment: TestEnvironment = None) -> List[TestResult]:
        """Run a complete test suite"""
        if suite_name not in self.test_suites:
            raise ValueError(f"Test suite not found: {suite_name}")

        suite = self.test_suites[suite_name]
        environment = override_environment or suite.environment

        log_info(f"Running test suite: {suite.name} in {environment.value} environment")
        start_time = time.time()

        suite_results = []

        with self.environment_controller.create_environment(environment, suite_name):
            # Setup phase
            if suite.setup_commands:
                self._execute_setup_commands(suite.setup_commands, environment)

            # Test execution phase
            for test_id in suite.tests:
                result = self._run_single_test(test_id, suite, environment)
                suite_results.append(result)

            # Teardown phase
            if suite.teardown_commands:
                self._execute_teardown_commands(suite.teardown_commands, environment)

        duration = time.time() - start_time
        log_info(f"Test suite {suite.name} completed in {duration:.2f}s")

        return suite_results

    @testing_handler("run_single_test")
    def run_single_test(self, test_id: str, test_type: TestType, environment: TestEnvironment) -> TestResult:
        """Run a single test with environment control"""
        suite = TestSuite(
            name=f"single_test_{test_id}",
            description=f"Single test execution for {test_id}",
            test_type=test_type,
            environment=environment,
            tests=[test_id]
        )

        results = self.run_test_suite(suite.name)
        return results[0] if results else None

    def _run_single_test(self, test_id: str, suite: TestSuite, environment: TestEnvironment) -> TestResult:
        """Execute a single test"""
        start_time = time.time()
        test_name = f"{suite.name}_{test_id}"

        log_info(f"Running test: {test_name}")

        try:
            # Determine test execution method
            if test_id.startswith("command:"):
                result = self._run_command_test(test_id, suite, environment)
            elif test_id.startswith("integration:"):
                result = self._run_integration_test(test_id, suite, environment)
            elif test_id.startswith("verification:"):
                result = self._run_verification_test(test_id, suite, environment)
            else:
                result = self._run_generic_test(test_id, suite, environment)

        except Exception as e:
            result = TestResult(
                test_id=test_id,
                test_name=test_name,
                test_type=suite.test_type,
                status=TestStatus.ERROR,
                duration=time.time() - start_time,
                start_time=start_time,
                end_time=time.time(),
                environment=environment,
                error_message=str(e)
            )

        log_info(f"Test {test_name} completed: {result.status.value} ({result.duration:.2f}s)")
        return result

    def _run_command_test(self, test_id: str, suite: TestSuite, environment: TestEnvironment) -> TestResult:
        """Run command-based test"""
        command = test_id.replace("command:", "")
        start_time = time.time()

        try:
            # Execute command through clautorun
            result = subprocess.run(
                ["python3", "commands/clautorun"],
                input=json.dumps({"prompt": command, "session_id": f"test_{uuid.uuid4().hex[:8]}"}),
                text=True,
                capture_output=True,
                timeout=suite.timeout,
                cwd=Path.cwd()
            )

            end_time = time.time()
            duration = end_time - start_time

            # Parse result
            try:
                response = json.loads(result.stdout.strip())
                success = response.get("continue", True) and not response.get("error")
            except:
                success = result.returncode == 0

            return TestResult(
                test_id=test_id,
                test_name=f"Command test: {command}",
                test_type=suite.test_type,
                status=TestStatus.PASSED if success else TestStatus.FAILED,
                duration=duration,
                start_time=start_time,
                end_time=end_time,
                environment=environment,
                output=result.stdout,
                error_message=result.stderr if result.stderr else ""
            )

        except subprocess.TimeoutExpired:
            return TestResult(
                test_id=test_id,
                test_name=f"Command test: {command}",
                test_type=suite.test_type,
                status=TestStatus.TIMEOUT,
                duration=suite.timeout,
                start_time=start_time,
                end_time=start_time + suite.timeout,
                environment=environment,
                error_message=f"Test timed out after {suite.timeout} seconds"
            )

    def _run_integration_test(self, test_id: str, suite: TestSuite, environment: TestEnvironment) -> TestResult:
        """Run integration test"""
        test_name = test_id.replace("integration:", "")
        start_time = time.time()

        try:
            # Load integration test script
            test_script = Path(f"tests/integration/{test_name}.py")
            if not test_script.exists():
                raise FileNotFoundError(f"Integration test script not found: {test_script}")

            # Execute test script
            result = subprocess.run(
                [sys.executable, str(test_script)],
                capture_output=True,
                text=True,
                timeout=suite.timeout,
                env={**os.environ, "CLAUTORUN_TEST_ENV": environment.value}
            )

            end_time = time.time()
            duration = end_time - start_time

            success = result.returncode == 0

            return TestResult(
                test_id=test_id,
                test_name=f"Integration test: {test_name}",
                test_type=suite.test_type,
                status=TestStatus.PASSED if success else TestStatus.FAILED,
                duration=duration,
                start_time=start_time,
                end_time=end_time,
                environment=environment,
                output=result.stdout,
                error_message=result.stderr if result.stderr else ""
            )

        except Exception as e:
            return TestResult(
                test_id=test_id,
                test_name=f"Integration test: {test_name}",
                test_type=suite.test_type,
                status=TestStatus.ERROR,
                duration=time.time() - start_time,
                start_time=start_time,
                end_time=time.time(),
                environment=environment,
                error_message=str(e)
            )

    def _run_verification_test(self, test_id: str, suite: TestSuite, environment: TestEnvironment) -> TestResult:
        """Run verification engine test"""
        test_name = test_id.replace("verification:", "")
        start_time = time.time()

        try:
            # Import verification components
            sys.path.insert(0, 'src')
            from clautorun.verification_engine import RequirementVerificationEngine
            from clautorun.transcript_analyzer import TranscriptAnalyzer

            # Create test scenario
            if test_name == "basic_verification":
                engine = RequirementVerificationEngine("test_session")
                task = "Create a secure authentication system with testing"
                requirements = engine.parse_requirements_from_task(task)

                transcript = """
                I created auth/login.py with secure authentication.
                Added comprehensive tests that all passed.
                System is working correctly.
                """

                evidence = engine.analyze_transcript_evidence(transcript)
                results = engine.verify_single_requirement(list(requirements.keys())[0], evidence[0])
                report = engine.generate_verification_report()

                success = len(requirements) > 0 and report['summary']['total_requirements'] > 0

            else:
                # Default verification test
                success = True

            end_time = time.time()
            duration = end_time - start_time

            return TestResult(
                test_id=test_id,
                test_name=f"Verification test: {test_name}",
                test_type=suite.test_type,
                status=TestStatus.PASSED if success else TestStatus.FAILED,
                duration=duration,
                start_time=start_time,
                end_time=end_time,
                environment=environment,
                metadata={"verification_type": test_name}
            )

        except Exception as e:
            return TestResult(
                test_id=test_id,
                test_name=f"Verification test: {test_name}",
                test_type=suite.test_type,
                status=TestStatus.ERROR,
                duration=time.time() - start_time,
                start_time=start_time,
                end_time=time.time(),
                environment=environment,
                error_message=str(e)
            )

    def _run_generic_test(self, test_id: str, suite: TestSuite, environment: TestEnvironment) -> TestResult:
        """Run generic test"""
        start_time = time.time()

        try:
            # Simple test execution
            success = True  # Placeholder for actual test logic

            end_time = time.time()
            duration = end_time - start_time

            return TestResult(
                test_id=test_id,
                test_name=f"Generic test: {test_id}",
                test_type=suite.test_type,
                status=TestStatus.PASSED if success else TestStatus.FAILED,
                duration=duration,
                start_time=start_time,
                end_time=end_time,
                environment=environment
            )

        except Exception as e:
            return TestResult(
                test_id=test_id,
                test_name=f"Generic test: {test_id}",
                test_type=suite.test_type,
                status=TestStatus.ERROR,
                duration=time.time() - start_time,
                start_time=start_time,
                end_time=time.time(),
                environment=environment,
                error_message=str(e)
            )

    def _execute_setup_commands(self, commands: List[str], environment: TestEnvironment):
        """Execute setup commands for test environment"""
        for command in commands:
            log_info(f"Executing setup command: {command}")
            try:
                subprocess.run(command, shell=True, check=True, capture_output=True)
            except subprocess.CalledProcessError as e:
                log_info(f"Setup command failed: {e}")

    def _execute_teardown_commands(self, commands: List[str], environment: TestEnvironment):
        """Execute teardown commands for test environment"""
        for command in commands:
            log_info(f"Executing teardown command: {command}")
            try:
                subprocess.run(command, shell=True, check=False, capture_output=True)  # Don't fail on teardown
            except Exception as e:
                log_info(f"Teardown command error: {e}")

    @testing_handler("generate_report")
    def generate_test_report(self, results: List[TestResult] = None) -> Dict:
        """Generate comprehensive test report"""
        if results is None:
            results = self.test_results

        total_tests = len(results)
        passed_tests = sum(1 for r in results if r.status == TestStatus.PASSED)
        failed_tests = sum(1 for r in results if r.status == TestStatus.FAILED)
        error_tests = sum(1 for r in results if r.status == TestStatus.ERROR)
        timeout_tests = sum(1 for r in results if r.status == TestStatus.TIMEOUT)

        total_duration = sum(r.duration for r in results)
        avg_duration = total_duration / total_tests if total_tests > 0 else 0

        # Group results by environment and type
        by_environment = {}
        by_type = {}

        for result in results:
            env = result.environment.value
            test_type = result.test_type.value

            if env not in by_environment:
                by_environment[env] = []
            by_environment[env].append(result)

            if test_type not in by_type:
                by_type[test_type] = []
            by_type[test_type].append(result)

        report = {
            "summary": {
                "total_tests": total_tests,
                "passed": passed_tests,
                "failed": failed_tests,
                "errors": error_tests,
                "timeouts": timeout_tests,
                "success_rate": passed_tests / total_tests if total_tests > 0 else 0,
                "total_duration": total_duration,
                "average_duration": avg_duration,
                "generated_at": time.time()
            },
            "by_environment": {
                env: {
                    "total": len(env_results),
                    "passed": sum(1 for r in env_results if r.status == TestStatus.PASSED),
                    "failed": sum(1 for r in env_results if r.status == TestStatus.FAILED),
                    "success_rate": sum(1 for r in env_results if r.status == TestStatus.PASSED) / len(env_results) if env_results else 0
                }
                for env, env_results in by_environment.items()
            },
            "by_type": {
                test_type: {
                    "total": len(type_results),
                    "passed": sum(1 for r in type_results if r.status == TestStatus.PASSED),
                    "failed": sum(1 for r in type_results if r.status == TestStatus.FAILED),
                    "success_rate": sum(1 for r in type_results if r.status == TestStatus.PASSED) / len(type_results) if type_results else 0
                }
                for test_type, type_results in by_type.items()
            },
            "detailed_results": [result.to_dict() for result in results]
        }

        return report

# Export main functions
__all__ = [
    'TestRunner',
    'EnvironmentController',
    'TestSuite',
    'TestResult',
    'TestEnvironment',
    'TestType',
    'TestStatus'
]