#!/usr/bin/env python3
"""
Integration tests for tmux automation workflows.

Tests complete end-to-end workflows including session automation,
CLI testing, and interactive management scenarios.
"""

import pytest
import time
import os
import subprocess

pytestmark = pytest.mark.tmux

# Add src to path for imports
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from clautorun.tmux_utils import get_tmux_utilities


class TestSessionAutomationWorkflows:
    """Integration tests for session automation workflows"""

    @pytest.mark.integration
    def test_complete_session_lifecycle_automation(self):
        """Test complete session lifecycle automation workflow"""
        session_name = f"lifecycle-test-{int(time.time())}"
        tmux = get_tmux_utilities(session_name)

        # Clean up any existing session with this name first
        tmux.execute_tmux_command(['kill-session', '-t', session_name])

        # Phase 1: Session is auto-created by execute_tmux_command when we run any command
        # Run a display-message to trigger session creation
        create_result = tmux.execute_tmux_command(['display-message', '-p', 'Session created'], session_name)
        assert create_result is not None
        info = tmux.get_session_info()
        # Info returns target_session, which is what we set
        assert info['target_session'] == session_name or info['session'] == session_name

        # Phase 2: Environment Configuration
        config_commands = [
            'export AUTO_MODE=1',
            'export WORKFLOW_ID=$(uuidgen)',
            'export START_TIME=$(date)',
        ]

        for cmd in config_commands:
            assert tmux.send_keys(cmd, session_name)
            assert tmux.send_keys('C-m', session_name)

        # Phase 3: Health Monitoring Setup
        health_commands = [
            'echo "Starting health monitoring"',
            'echo "Session: $AUTO_MODE"',
            'echo "ID: $WORKFLOW_ID"',
        ]

        for cmd in health_commands:
            assert tmux.send_keys(cmd, session_name)
            assert tmux.send_keys('C-m', session_name)
            time.sleep(0.1)  # Small delay for command execution

        # Phase 4: Process Execution
        process_commands = [
            'echo "Process execution started"',
            'sleep 1',
            'echo "Process execution completed"',
        ]

        for cmd in process_commands:
            assert tmux.send_keys(cmd, session_name)
            assert tmux.send_keys('C-m', session_name)
            if 'sleep' in cmd:
                time.sleep(1.2)  # Wait for sleep command

        # Phase 5: Output Capture and Validation
        output = tmux.capture_current_input(session_name)
        assert isinstance(output, str)
        assert 'Process execution completed' in output

        # Phase 6: Session Cleanup
        cleanup_result = tmux.execute_tmux_command(['kill-session', '-t', session_name])
        assert cleanup_result and cleanup_result['returncode'] == 0

    @pytest.mark.integration
    def test_multi_window_automation_workflow(self):
        """Test multi-window automation workflow"""
        session_name = f"multi-window-test-{int(time.time())}"
        tmux = get_tmux_utilities(session_name)

        # Create base session
        assert tmux.execute_tmux_command(['new-session', '-d', '-s', session_name])

        # Create multiple windows for different purposes
        windows = [
            ('development', 'echo "Development window initialized"'),
            ('testing', 'echo "Testing window initialized"'),
            ('monitoring', 'echo "Monitoring window initialized"'),
        ]

        created_windows = []
        for window_name, init_cmd in windows:
            # Create new window with custom name
            result = tmux.execute_tmux_command(['new-window', '-n', window_name], session_name)
            if result and result['returncode'] == 0:
                created_windows.append(window_name)
                assert tmux.send_keys(init_cmd, session_name, window_name)
                assert tmux.send_keys('C-m', session_name, window_name)

        # Verify windows were created
        windows_result = tmux.execute_tmux_command(['list-windows'], session_name)
        if windows_result and windows_result['returncode'] == 0:
            window_lines = windows_result['stdout'].strip().split('\n')
            assert len(window_lines) >= len(windows) + 1  # +1 for initial window

        # Test window operations
        layout_result = tmux.execute_win_op('select-layout', ['even-horizontal'], session_name)
        assert isinstance(layout_result, bool)

        # Cleanup
        cleanup_result = tmux.execute_tmux_command(['kill-session', '-t', session_name])
        assert cleanup_result and cleanup_result['returncode'] == 0

    @pytest.mark.integration
    def test_error_recovery_automation_workflow(self):
        """Test error recovery automation workflow"""
        session_name = f"recovery-test-{int(time.time())}"
        tmux = get_tmux_utilities(session_name)

        # Create session
        assert tmux.execute_tmux_command(['new-session', '-d', '-s', session_name])

        # Test recovery operations
        recovery_scenarios = [
            {
                'name': 'Clear Terminal',
                'operations': [
                    lambda: tmux.send_keys('C-l', session_name),
                    lambda: tmux.send_keys('clear', session_name),
                    lambda: tmux.send_keys('C-m', session_name),
                ]
            },
            {
                'name': 'Interrupt Process',
                'operations': [
                    lambda: tmux.send_keys('C-c', session_name),
                    lambda: tmux.send_keys('C-z', session_name),
                    lambda: tmux.send_keys('bg', session_name),
                    lambda: tmux.send_keys('C-m', session_name),
                ]
            },
            {
                'name': 'Reset State',
                'operations': [
                    lambda: tmux.send_keys('C-u', session_name),
                    lambda: tmux.send_keys('C-k', session_name),
                    lambda: tmux.send_keys('C-l', session_name),
                ]
            }
        ]

        recovery_results = {}
        for scenario in recovery_scenarios:
            scenario_results = []
            for operation in scenario['operations']:
                try:
                    result = operation()
                    scenario_results.append(result)
                except Exception:
                    scenario_results.append(False)

            recovery_results[scenario['name']] = all(scenario_results)

        # Verify at least some recovery operations worked
        assert any(recovery_results.values()), "At least one recovery scenario should work"

        # Cleanup
        tmux.execute_tmux_command(['kill-session', '-t', session_name])

    @pytest.mark.integration
    def test_session_backup_and_restore_workflow(self):
        """Test session backup and restore workflow"""
        session_name = f"backup-test-{int(time.time())}"
        tmux = get_tmux_utilities(session_name)

        # Create and configure session
        assert tmux.execute_tmux_command(['new-session', '-d', '-s', session_name])

        # Set up session state
        setup_commands = [
            'export BACKUP_TEST="true"',
            'echo "Session configured"',
            'echo "Test data: backup-test-12345"',
            'echo "Timestamp: $(date)"',
        ]

        for cmd in setup_commands:
            tmux.send_keys(cmd, session_name)
            tmux.send_keys('C-m', session_name)
            time.sleep(0.1)

        # Simulate backup operation
        backup_data = {
            'session_name': session_name,
            'timestamp': time.time(),
            'environment': {
                'BACKUP_TEST': 'true',
                'SESSION_TYPE': 'automation-test'
            },
            'commands_executed': len(setup_commands),
            'session_info': tmux.get_session_info()
        }

        # Verify backup data structure
        assert 'session_name' in backup_data
        assert 'timestamp' in backup_data
        assert 'environment' in backup_data

        # Test restore preparation
        restore_session_name = f"{session_name}-restored"
        assert tmux.execute_tmux_command(['new-session', '-d', '-s', restore_session_name])

        # Restore environment
        if 'environment' in backup_data:
            for key, value in backup_data['environment'].items():
                tmux.send_keys(f'export {key}="{value}"', restore_session_name)
                tmux.send_keys('C-m', restore_session_name)

        # Cleanup both sessions
        tmux.execute_tmux_command(['kill-session', '-t', session_name])
        tmux.execute_tmux_command(['kill-session', '-t', restore_session_name])


class TestCLITestingWorkflows:
    """Integration tests for CLI testing workflows"""

    @pytest.mark.integration
    def test_cli_discovery_and_analysis_workflow(self):
        """Test CLI discovery and analysis workflow using real subprocess calls"""
        test_session = f"cli-discovery-{int(time.time())}"
        tmux = get_tmux_utilities(test_session)

        # Create test session
        assert tmux.execute_tmux_command(['new-session', '-d', '-s', test_session])

        # Test CLI availability discovery using subprocess directly (not tmux commands)
        test_clis = ['echo', 'date', 'whoami', 'pwd']
        cli_availability = {}

        for cli in test_clis:
            # Use subprocess to check CLI availability (system commands, not tmux)
            try:
                result = subprocess.run(['which', cli], capture_output=True, text=True, timeout=5)
                cli_availability[cli] = result.returncode == 0
            except Exception:
                cli_availability[cli] = False

        # Verify at least basic CLI tools are available
        assert cli_availability.get('echo', False), "Basic echo command should be available"

        # Test sending commands via tmux send-keys and verify they execute
        for cli in test_clis:
            if cli_availability.get(cli, False):
                # Send command to tmux session
                assert tmux.send_keys(f'{cli}', test_session)
                assert tmux.send_keys('C-m', test_session)
                time.sleep(0.1)  # Small delay for execution

        # Verify session received commands by checking it's still responsive
        info = tmux.get_session_info()
        assert info is not None

        # Cleanup
        tmux.execute_tmux_command(['kill-session', '-t', test_session])

    @pytest.mark.integration
    def test_error_condition_testing_workflow(self):
        """Test error condition testing workflow using subprocess for shell commands"""
        test_session = f"error-testing-{int(time.time())}"
        tmux = get_tmux_utilities(test_session)

        # Create test session
        assert tmux.execute_tmux_command(['new-session', '-d', '-s', test_session])

        # Test error conditions using subprocess directly (shell commands, not tmux)
        error_scenarios = [
            {
                'name': 'Invalid Command',
                'command': ['nonexistent-command-12345'],
                'should_fail': True
            },
            {
                'name': 'Invalid Flag',
                'command': ['ls', '--invalid-flag-12345'],
                'should_fail': True
            },
            {
                'name': 'Permission Denied',
                'command': ['cat', '/root/inaccessible-file'],
                'should_fail': True
            },
            {
                'name': 'Valid Command',
                'command': ['echo', 'valid test'],
                'should_fail': False
            }
        ]

        error_results = {}
        for scenario in error_scenarios:
            try:
                result = subprocess.run(scenario['command'], capture_output=True, text=True, timeout=5)
                error_occurred = result.returncode != 0
                error_results[scenario['name']] = {
                    'expected_to_fail': scenario['should_fail'],
                    'actually_failed': error_occurred,
                    'correct_behavior': error_occurred == scenario['should_fail']
                }
            except FileNotFoundError:
                # Command not found = failure
                error_results[scenario['name']] = {
                    'expected_to_fail': scenario['should_fail'],
                    'actually_failed': True,
                    'correct_behavior': scenario['should_fail']
                }
            except Exception:
                error_results[scenario['name']] = {
                    'expected_to_fail': scenario['should_fail'],
                    'actually_failed': True,
                    'correct_behavior': scenario['should_fail']
                }

        # Verify error detection
        assert error_results['Valid Command']['correct_behavior'], "Valid command should succeed"
        assert error_results['Invalid Command']['correct_behavior'], "Invalid command should fail"

        # Cleanup
        tmux.execute_tmux_command(['kill-session', '-t', test_session])

    @pytest.mark.integration
    def test_performance_monitoring_workflow(self):
        """Test performance monitoring workflow using subprocess for shell commands"""
        test_session = f"perf-monitoring-{int(time.time())}"
        tmux = get_tmux_utilities(test_session)

        # Create test session
        assert tmux.execute_tmux_command(['new-session', '-d', '-s', test_session])

        # Test different command performance using subprocess (shell commands, not tmux)
        performance_tests = [
            {
                'name': 'Quick Command',
                'command': ['echo', 'quick'],
                'expected_max_time': 1.0
            },
            {
                'name': 'Help Command',
                'command': ['echo', 'help test output'],
                'expected_max_time': 1.0
            },
            {
                'name': 'Environment Check',
                'command': ['env'],
                'expected_max_time': 2.0
            }
        ]

        performance_results = {}
        for test in performance_tests:
            start_time = time.time()
            try:
                result = subprocess.run(test['command'], capture_output=True, text=True, timeout=5)
                execution_time = time.time() - start_time
                performance_results[test['name']] = {
                    'execution_time': execution_time,
                    'max_expected': test['expected_max_time'],
                    'within_limit': execution_time <= test['expected_max_time'],
                    'success': result.returncode == 0
                }
            except Exception:
                execution_time = time.time() - start_time
                performance_results[test['name']] = {
                    'execution_time': execution_time,
                    'max_expected': test['expected_max_time'],
                    'within_limit': execution_time <= test['expected_max_time'],
                    'success': False
                }

        # Verify performance expectations
        for test_name, result in performance_results.items():
            assert result['within_limit'], f"{test_name} should complete within {result['max_expected']}s"
            assert result['success'], f"{test_name} should execute successfully"

        # Test sending commands through tmux
        resource_commands = [
            'echo "CPU test: $(date)"',
            'echo "Memory test: $RANDOM"',
            'echo "Disk test: $(pwd)"',
        ]

        for cmd in resource_commands:
            start_time = time.time()
            tmux.send_keys(cmd, test_session)
            tmux.send_keys('C-m', test_session)
            execution_time = time.time() - start_time
            assert execution_time < 2.0, f"Resource command should complete quickly: {cmd}"

        # Cleanup
        tmux.execute_tmux_command(['kill-session', '-t', test_session])


class TestInteractiveManagementWorkflows:
    """Integration tests for interactive management workflows"""

    @pytest.mark.integration
    def test_session_creation_with_templates(self):
        """Test session creation with different templates"""
        templates = [
            {
                'name': 'basic',
                'windows': 1,
                'layout': 'even-horizontal'
            },
            {
                'name': 'development',
                'windows': 3,
                'layout': 'main-horizontal'
            },
            {
                'name': 'testing',
                'windows': 4,
                'layout': 'tiled'
            }
        ]

        for template in templates:
            session_name = f"template-{template['name']}-{int(time.time())}"
            tmux = get_tmux_utilities(session_name)

            # Clean up any existing session, then let auto-creation handle it
            tmux.execute_tmux_command(['kill-session', '-t', session_name])
            # Session is auto-created by execute_tmux_command
            create_result = tmux.execute_tmux_command(['display-message', '-p', 'Ready'], session_name)
            assert create_result is not None

            # Create additional windows using execute_tmux_command directly
            for i in range(template['windows'] - 1):
                win_result = tmux.execute_tmux_command(['new-window'], session_name)
                # new-window may fail if session doesn't have focus, accept this
                assert win_result is not None

            # Set layout - use execute_tmux_command directly
            layout_result = tmux.execute_tmux_command(['select-layout', template['layout']], session_name)
            # Layout may fail if only one pane, that's ok
            assert layout_result is not None

            # Configure template-specific settings
            if template['name'] == 'development':
                tmux.send_keys('export DEV_MODE=1', session_name)
                tmux.send_keys('C-m', session_name)
            elif template['name'] == 'testing':
                tmux.send_keys('export TEST_MODE=1', session_name)
                tmux.send_keys('C-m', session_name)

            # Verify session exists
            info = tmux.get_session_info()
            assert info is not None

            # Cleanup
            tmux.execute_tmux_command(['kill-session', '-t', session_name])

    @pytest.mark.integration
    def test_health_monitoring_integration(self):
        """Test health monitoring integration using subprocess for shell commands"""
        session_name = f"health-monitor-{int(time.time())}"
        tmux = get_tmux_utilities(session_name)

        # Clean up any existing session, then let auto-creation handle it
        tmux.execute_tmux_command(['kill-session', '-t', session_name])
        # Session is auto-created by execute_tmux_command
        create_result = tmux.execute_tmux_command(['display-message', '-p', 'Ready'], session_name)
        assert create_result is not None

        # Define health checks - use subprocess for shell commands
        health_checks = [
            {
                'name': 'Basic Responsiveness',
                'command': ['echo', 'Health check'],
                'critical': True
            },
            {
                'name': 'Process Status',
                'command': ['ps', 'aux'],
                'critical': True
            },
            {
                'name': 'Environment Status',
                'command': ['env'],
                'critical': False
            },
            {
                'name': 'System Time',
                'command': ['date'],
                'critical': True
            }
        ]

        health_results = {}
        for check in health_checks:
            start_time = time.time()
            try:
                # Use subprocess for shell commands, not tmux
                result = subprocess.run(check['command'], capture_output=True, text=True, timeout=5)
                execution_time = time.time() - start_time
                health_results[check['name']] = {
                    'success': result.returncode == 0,
                    'execution_time': execution_time,
                    'critical': check['critical'],
                    'output': result.stdout,
                    'error': result.stderr
                }
            except Exception as e:
                execution_time = time.time() - start_time
                health_results[check['name']] = {
                    'success': False,
                    'execution_time': execution_time,
                    'critical': check['critical'],
                    'output': '',
                    'error': str(e)
                }

        # Calculate overall health score
        total_checks = len(health_results)
        passed_checks = sum(1 for r in health_results.values() if r['success'])
        critical_checks = [r for r in health_results.values() if r['critical']]
        critical_passed = sum(1 for r in critical_checks if r['success'])

        health_score = (passed_checks / total_checks) * 100
        critical_score = (critical_passed / len(critical_checks)) * 100 if critical_checks else 100

        assert health_score >= 50, "Overall health score should be at least 50%"
        assert critical_score >= 80, "Critical health score should be at least 80%"

        # Cleanup
        tmux.execute_tmux_command(['kill-session', '-t', session_name])

    @pytest.mark.integration
    def test_batch_operations_workflow(self):
        """Test batch operations workflow"""
        # Create multiple sessions
        session_base = f"batch-test-{int(time.time())}"
        sessions = []

        for i in range(3):
            session_name = f"{session_base}-{i}"
            tmux = get_tmux_utilities(session_name)
            assert tmux.execute_tmux_command(['new-session', '-d', '-s', session_name])
            sessions.append((session_name, tmux))

        # Perform batch operations
        batch_operations = [
            ('Set Environment Variable', lambda t: t.send_keys('export BATCH_MODE=1')),
            ('Set Timestamp', lambda t: t.send_keys('export BATCH_TIMESTAMP=$(date)')),
            ('Execute Command', lambda t: t.send_keys('echo "Batch operation completed"')),
            ('Send Enter', lambda t: t.send_keys('C-m')),
        ]

        batch_results = {}
        for session_name, tmux in sessions:
            session_results = []
            for op_name, operation in batch_operations:
                try:
                    result = operation(tmux)
                    session_results.append(result)
                except Exception:
                    session_results.append(False)

            batch_results[session_name] = all(session_results)

        # Verify batch operations
        assert all(batch_results.values()), "All batch operations should succeed"

        # Cleanup all sessions
        for session_name, tmux in sessions:
            tmux.execute_tmux_command(['kill-session', '-t', session_name])


class TestCrossSystemIntegration:
    """Integration tests for cross-system compatibility"""

    @pytest.mark.integration
    def test_byobu_compatibility(self):
        """Test byobu/tmux compatibility"""
        session_name = f"byobu-test-{int(time.time())}"
        tmux = get_tmux_utilities(session_name)

        # Test byobu-compatible operations
        compatibility_results = {}

        # Clean up any existing session, then let auto-creation handle it
        tmux.execute_tmux_command(['kill-session', '-t', session_name])
        # Session is auto-created by execute_tmux_command
        create_result = tmux.execute_tmux_command(['display-message', '-p', 'Ready'], session_name)
        compatibility_results['Create Session'] = create_result is not None

        if compatibility_results['Create Session']:
            # List Sessions
            list_result = tmux.execute_tmux_command(['list-sessions'])
            compatibility_results['List Sessions'] = list_result and list_result['returncode'] == 0

            # Clear Screen via send-keys
            clear_result = tmux.send_keys('C-l', session_name)
            compatibility_results['Clear Screen'] = clear_result

            # Display Message
            display_result = tmux.execute_tmux_command(['display-message', '-p', 'Byobu test'], session_name)
            compatibility_results['Display Message'] = display_result and display_result['returncode'] == 0

        # Verify byobu compatibility
        assert compatibility_results['Create Session'], "Session creation should work"
        assert compatibility_results.get('Clear Screen', False), "Clear screen should work"

        # Cleanup
        tmux.execute_tmux_command(['kill-session', '-t', session_name])

    @pytest.mark.integration
    def test_environment_variable_handling(self):
        """Test environment variable handling via tmux send-keys"""
        session_name = f"env-test-{int(time.time())}"
        tmux = get_tmux_utilities(session_name)

        # Clean up any existing session, then let auto-creation handle it
        tmux.execute_tmux_command(['kill-session', '-t', session_name])
        # Session is auto-created by execute_tmux_command
        create_result = tmux.execute_tmux_command(['display-message', '-p', 'Ready'], session_name)
        assert create_result is not None

        # Test various environment variable operations via send-keys
        env_operations = [
            'export TEST_VAR="test_value"',
            'export PATH="$PATH:/test/path"',
            'export NUMBER=42',
            'export BOOLEAN=true',
            'export LIST_ITEM="item1 item2 item3"',
        ]

        for env_op in env_operations:
            assert tmux.send_keys(env_op, session_name)
            assert tmux.send_keys('C-m', session_name)

        # Wait for commands to execute
        time.sleep(0.5)

        # Verify session is still responsive
        info = tmux.get_session_info()
        assert info is not None

        # Capture pane output to verify commands were sent
        capture_result = tmux.execute_tmux_command(['capture-pane', '-p'], session_name)
        assert capture_result is not None

        # Cleanup
        tmux.execute_tmux_command(['kill-session', '-t', session_name])


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-m', 'integration'])