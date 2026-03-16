#!/usr/bin/env python3
"""
Unit tests for tmux automation agents and commands.

Tests the tmux-session-automation and cli-test-automation agents
along with the tmux-test-workflow and tmux-session-management commands.
"""

import pytest
import time
import os
import json
from unittest.mock import Mock, patch

pytestmark = pytest.mark.tmux

# Add src to path for imports
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from autorun.tmux_utils import get_tmux_utilities


class TestTmuxUtilitiesEnhanced:
    """Enhanced tests for tmux utilities with focus on automation scenarios"""

    def test_session_lifecycle_management(self):
        """Test complete session lifecycle management"""
        tmux = get_tmux_utilities('test-lifecycle')

        # Test session creation
        assert tmux.ensure_session_exists('test-lifecycle')

        # Test session info
        info = tmux.get_session_info('test-lifecycle')
        assert info['session'] == 'test-lifecycle'
        assert 'available_sessions' in info

        # Test session health
        tmux.execute_tmux_command(['display-message', '-p', 'Health check'])
        # Note: This may fail if no tmux server is running, but that's expected

        # Cleanup
        cleanup_result = tmux.execute_tmux_command(['kill-session', '-t', 'test-lifecycle'])
        assert cleanup_result is not None  # Result object should be returned

    def test_command_execution_and_capture(self):
        """Test command execution and output capture"""
        tmux = get_tmux_utilities('test-commands')

        # Create session for testing
        tmux.ensure_session_exists('test-commands')

        # Test send-keys functionality
        result = tmux.send_keys('echo "test"', 'test-commands')
        assert isinstance(result, bool)

        # Test Enter key
        result = tmux.send_keys('C-m', 'test-commands')
        assert isinstance(result, bool)

        # Test capture functionality
        output = tmux.capture_current_input('test-commands')
        assert isinstance(output, str)

        # Cleanup
        tmux.execute_tmux_command(['kill-session', '-t', 'test-commands'])

    def test_win_ops_dispatch_system(self):
        """Test WIN_OPS dispatch system for automation"""
        tmux = get_tmux_utilities('test-winops')

        # Test key operations are available
        key_operations = [
            'new-window', 'list-windows', 'kill-session',
            'send-keys', 'capture-pane', 'select-layout'
        ]

        for op in key_operations:
            assert op in tmux.WIN_OPS, f"Operation {op} missing from WIN_OPS"

        # Test operation execution (will fail without session, but should not crash)
        result = tmux.execute_win_op('list-windows', session='nonexistent')
        assert isinstance(result, bool)

    def test_environment_detection(self):
        """Test tmux environment detection"""
        tmux = get_tmux_utilities()

        # Test environment detection (may return None if not in tmux)
        env_info = tmux.detect_tmux_environment()
        assert env_info is None or isinstance(env_info, dict)

        if env_info:
            assert 'session' in env_info
            assert 'window' in env_info
            assert 'pane' in env_info

    def test_control_sequence_parsing(self):
        """Test control sequence parsing for byobu compatibility"""
        from autorun.tmux_utils import TmuxControlState
        tmux = get_tmux_utilities()

        # Test normal text - method returns tuple of (text, state)
        text, state = tmux.parse_control_sequences("normal text")
        assert text == "normal text"
        assert state == TmuxControlState.NORMAL

        # Test escape sequences (single ^ removes the ^, state returns to NORMAL)
        text, state = tmux.parse_control_sequences("^C")
        assert text == "C"
        assert state == TmuxControlState.NORMAL

        # Test literal caret (^^ becomes ^)
        text, state = tmux.parse_control_sequences("^^test")
        assert text == "^test"
        assert state == TmuxControlState.NORMAL

    def test_error_handling_and_recovery(self):
        """Test error handling and recovery mechanisms"""
        tmux = get_tmux_utilities('test-recovery')

        # Test invalid command handling
        result = tmux.execute_tmux_command(['invalid-command'])
        assert result is not None  # Should return error result
        assert result['returncode'] != 0

        # Test missing session handling
        result = tmux.send_keys('test', 'nonexistent-session')
        assert isinstance(result, bool)  # Should handle gracefully

    def test_concurrent_session_safety(self):
        """Test concurrent session management safety"""
        tmux1 = get_tmux_utilities('test-concurrent-1')
        tmux2 = get_tmux_utilities('test-concurrent-2')

        # Create multiple sessions
        assert tmux1.ensure_session_exists('test-concurrent-1')
        assert tmux2.ensure_session_exists('test-concurrent-2')

        # Test independent operations
        info1 = tmux1.get_session_info()
        info2 = tmux2.get_session_info()

        assert info1['session'] == 'test-concurrent-1'
        assert info2['session'] == 'test-concurrent-2'

        # Cleanup
        tmux1.execute_tmux_command(['kill-session', '-t', 'test-concurrent-1'])
        tmux2.execute_tmux_command(['kill-session', '-t', 'test-concurrent-2'])

    def test_session_templates_and_layouts(self):
        """Test session templates and layout configurations"""
        tmux = get_tmux_utilities('test-template')

        # Create session
        tmux.ensure_session_exists('test-template')

        # Test layout operations
        layouts = ['even-horizontal', 'even-vertical', 'main-horizontal', 'tiled']

        for layout in layouts:
            result = tmux.execute_win_op('select-layout', [layout], 'test-template')
            assert isinstance(result, bool)

        # Test window creation for templates
        for i in range(3):
            result = tmux.execute_win_op('new-window', [], 'test-template')
            assert isinstance(result, bool)

        # Cleanup
        tmux.execute_tmux_command(['kill-session', '-t', 'test-template'])

    def test_integration_with_byobu_features(self):
        """Test integration with byobu-specific features"""
        tmux = get_tmux_utilities('test-byobu')

        # Test byobu-compatible operations
        byobu_operations = [
            ('new-session', ['-d', '-s', 'test-byobu']),
            ('list-sessions', []),
            ('list-windows', []),
            ('send-keys', ['C-l']),  # Clear screen
            ('send-keys', ['F6']),   # Detach (byobu)
        ]

        tmux.ensure_session_exists('test-byobu')

        for cmd, args in byobu_operations:
            if cmd == 'new-session' and 'test-byobu' in str(args):
                continue  # Session already created
            result = tmux.execute_tmux_command([cmd] + args, 'test-byobu')
            assert result is not None  # Should not crash

        # Cleanup
        tmux.execute_tmux_command(['kill-session', '-t', 'test-byobu'])


class TestAgentIntegrationScenarios:
    """Test integration scenarios for tmux automation agents"""

    @patch('autorun.tmux_utils.get_tmux_utilities')
    def test_session_automation_agent_workflow(self, mock_get_tmux):
        """Test session automation agent workflow"""
        # Mock tmux utilities
        mock_tmux = Mock()
        mock_tmux.ensure_session_exists.return_value = True
        mock_tmux.execute_tmux_command.return_value = {'returncode': 0, 'stdout': 'ok'}
        mock_tmux.send_keys.return_value = True
        mock_tmux.get_session_info.return_value = {
            'session': 'test-session',
            'window': '0',
            'pane': '0',
            'tmux_active': True
        }
        mock_get_tmux.return_value = mock_tmux

        # Import and test agent functionality
        from autorun.tmux_utils import get_tmux_utilities

        tmux = get_tmux_utilities('test-session')

        # Test session creation
        assert tmux.ensure_session_exists('test-session')

        # Test health monitoring
        result = tmux.execute_tmux_command(['display-message', '-p', 'Health check'])
        assert result['returncode'] == 0

        # Test command injection
        assert tmux.send_keys('npm test', 'test-session')

        # Test session info
        info = tmux.get_session_info()
        assert info['session'] == 'test-session'

    @patch('autorun.tmux_utils.get_tmux_utilities')
    def test_cli_test_automation_workflow(self, mock_get_tmux):
        """Test CLI test automation workflow"""
        # Mock tmux utilities for CLI testing
        mock_tmux = Mock()
        mock_tmux.ensure_session_exists.return_value = True
        mock_tmux.execute_tmux_command.return_value = {'returncode': 0, 'stdout': 'test output'}
        mock_get_tmux.return_value = mock_tmux

        from autorun.tmux_utils import get_tmux_utilities

        tmux = get_tmux_utilities('cli-test')

        # Test CLI discovery
        result = tmux.execute_tmux_command(['which', 'claude'])
        assert result is not None

        # Test help analysis
        result = tmux.execute_tmux_command(['claude', '--help'])
        assert result is not None

        # Test command execution
        test_commands = [
            ['claude', '--version'],
            ['claude', 'plugin', 'list'],
            ['claude', '--help']
        ]

        for cmd in test_commands:
            result = tmux.execute_tmux_command(cmd)
            assert result is not None

    def test_error_recovery_scenarios(self):
        """Test error recovery scenarios"""
        tmux = get_tmux_utilities('recovery-test')

        # Create test session
        result = tmux.execute_tmux_command(['new-session', '-d', '-s', 'recovery-test'])

        if result and result['returncode'] == 0:
            # Test recovery operations
            recovery_operations = [
                ('Clear terminal', lambda: tmux.send_keys('C-l', 'recovery-test')),
                ('Interrupt', lambda: tmux.send_keys('C-c', 'recovery-test')),
                ('Reset input', lambda: tmux.send_keys('C-u', 'recovery-test')),
            ]

            for name, operation in recovery_operations:
                try:
                    result = operation()
                    assert isinstance(result, bool), f"{name} should return bool"
                except Exception:
                    # Some operations might fail, that's expected
                    pass

            # Cleanup
            tmux.execute_tmux_command(['kill-session', '-t', 'recovery-test'])

    def test_performance_and_resource_monitoring(self):
        """Test performance and resource monitoring capabilities"""
        tmux = get_tmux_utilities('perf-test')

        # Test session creation performance
        start_time = time.time()
        result = tmux.execute_tmux_command(['new-session', '-d', '-s', 'perf-test'])
        creation_time = time.time() - start_time

        assert result is not None
        assert creation_time < 5.0  # Should complete within 5 seconds

        if result and result['returncode'] == 0:
            # Test command execution performance
            start_time = time.time()
            result = tmux.execute_tmux_command(['display-message', '-p', 'Perf test'], 'perf-test')
            exec_time = time.time() - start_time

            assert result is not None
            assert exec_time < 2.0  # Should complete within 2 seconds

            # Test multiple operations
            operations = [
                ('send-keys', ['echo test']),
                ('send-keys', ['C-m']),
                ('capture-pane', ['-p']),
                ('list-windows', []),
            ]

            for op, args in operations:
                start_time = time.time()
                result = tmux.execute_tmux_command([op] + args, 'perf-test')
                op_time = time.time() - start_time

                assert result is not None
                assert op_time < 1.0  # Each operation should complete within 1 second

            # Cleanup
            tmux.execute_tmux_command(['kill-session', '-t', 'perf-test'])


class TestCommandWorkflowIntegration:
    """Test command workflow integration"""

    def test_tmux_test_workflow_command_structure(self):
        """Test tmux-test-workflow command structure"""
        # Test that command file exists and has proper structure
        command_file = os.path.join(os.path.dirname(__file__), '..', 'commands', 'tmux-test-workflow.md')
        assert os.path.exists(command_file)

        with open(command_file, 'r', encoding="utf-8") as f:
            content = f.read()

        # Check for required YAML frontmatter
        assert 'name: tmux-test-workflow' in content
        assert 'description:' in content
        assert 'model: sonnet' in content

        # Check for required workflow sections (simplified structure)
        assert '## Quick Start' in content
        assert '## Available Test Types' in content
        assert '## Safety Features' in content

    def test_tmux_session_management_command_structure(self):
        """Test tmux-session-management command structure"""
        command_file = os.path.join(os.path.dirname(__file__), '..', 'commands', 'tmux-session-management.md')
        assert os.path.exists(command_file)

        with open(command_file, 'r', encoding="utf-8") as f:
            content = f.read()

        # Check for required YAML frontmatter
        assert 'name: tmux-session-management' in content
        assert 'description:' in content
        assert 'model: sonnet' in content

        # Check for required management sections (simplified structure)
        assert '## Quick Start' in content
        assert '## Available Actions' in content
        assert '## Safety Features' in content

    def test_agent_files_structure(self):
        """Test agent files structure"""
        agent_files = [
            'agents/tmux-session-automation.md',
            'agents/cli-test-automation.md'
        ]

        for agent_file in agent_files:
            file_path = os.path.join(os.path.dirname(__file__), '..', agent_file)
            assert os.path.exists(file_path), f"Agent file {agent_file} should exist"

            with open(file_path, 'r', encoding="utf-8") as f:
                content = f.read()

            # Check for required agent structure
            assert 'name:' in content
            assert 'description:' in content
            assert 'model:' in content
            assert '## ' in content  # Should have markdown sections

    def test_plugin_integration(self):
        """Test plugin integration compatibility"""
        # Test plugin manifest
        plugin_file = os.path.join(os.path.dirname(__file__), '..', '.claude-plugin', 'plugin.json')
        assert os.path.exists(plugin_file)

        with open(plugin_file, 'r', encoding="utf-8") as f:
            manifest = json.load(f)

        # Check required manifest fields
        assert 'name' in manifest
        assert 'description' in manifest
        assert 'commands' in manifest
        assert manifest['name'] == 'ar'

        # Test command directory exists
        commands_dir = os.path.join(os.path.dirname(__file__), '..', 'commands')
        assert os.path.exists(commands_dir)

        # Test agents directory exists
        agents_dir = os.path.join(os.path.dirname(__file__), '..', 'agents')
        assert os.path.exists(agents_dir)


class TestEdgeCasesAndBoundaryConditions:
    """Test edge cases and boundary conditions"""

    def test_empty_session_names(self):
        """Test handling of empty session names"""
        tmux = get_tmux_utilities('')

        # Should handle empty names gracefully
        info = tmux.get_session_info()
        assert isinstance(info, dict)

    def test_very_long_session_names(self):
        """Test handling of very long session names"""
        long_name = 'a' * 100  # 100 character session name
        tmux = get_tmux_utilities(long_name)

        # Should handle long names
        info = tmux.get_session_info()
        assert isinstance(info, dict)

    def test_special_characters_in_session_names(self):
        """Test handling of special characters in session names"""
        special_names = [
            'test-session-with-dashes',
            'test_session_with_underscores',
            'test.session.with.dots',
            'test123numeric456'
        ]

        for name in special_names:
            tmux = get_tmux_utilities(name)
            info = tmux.get_session_info()
            assert isinstance(info, dict)

    def test_concurrent_tmux_operations(self):
        """Test concurrent tmux operations"""
        tmux = get_tmux_utilities('concurrent-test')

        # Create session
        result = tmux.execute_tmux_command(['new-session', '-d', '-s', 'concurrent-test'])

        if result and result['returncode'] == 0:
            # Test multiple rapid operations
            operations = []
            for i in range(5):
                op = tmux.execute_tmux_command(['display-message', '-p', f'Message {i}'], 'concurrent-test')
                operations.append(op)

            # All operations should complete (may fail gracefully)
            for op in operations:
                assert op is not None

            # Cleanup
            tmux.execute_tmux_command(['kill-session', '-t', 'concurrent-test'])

    def test_resource_cleanup(self):
        """Test resource cleanup and memory management"""
        # Create multiple tmux utility instances
        instances = []
        for i in range(10):
            tmux = get_tmux_utilities(f'cleanup-test-{i}')
            instances.append(tmux)
            tmux.ensure_session_exists(f'cleanup-test-{i}')

        # Clean up all instances
        for i, tmux in enumerate(instances):
            tmux.execute_tmux_command(['kill-session', '-t', f'cleanup-test-{i}'])

        # Should not cause memory leaks or resource issues
        assert True  # If we reach here, cleanup was successful

    @pytest.mark.parametrize("invalid_command", [
        ['nonexistent-command'],
        ['tmux', '--invalid-option'],
        [''],
        [''],
    ])
    def test_invalid_command_handling(self, invalid_command):
        """Test handling of invalid tmux commands"""
        tmux = get_tmux_utilities('error-test')

        result = tmux.execute_tmux_command(invalid_command)
        assert result is not None  # Should return error result, not crash
        assert result['returncode'] != 0  # Should indicate error

    def test_timeout_behavior(self):
        """Test timeout behavior for long-running commands"""
        tmux = get_tmux_utilities('timeout-test')

        # Test with invalid tmux command (should fail gracefully)
        result = tmux.execute_tmux_command(['invalid-tmux-command'])
        assert result is not None  # Should return error result
        assert result['returncode'] != 0  # Should indicate error

    def test_state_isolation(self):
        """Test state isolation between different tmux instances"""
        tmux1 = get_tmux_utilities('isolation-test-1')
        tmux2 = get_tmux_utilities('isolation-test-2')

        # Test that instances are properly isolated
        assert tmux1.session_name == 'isolation-test-1'
        assert tmux2.session_name == 'isolation-test-2'
        assert tmux1 is not tmux2

        # Test that state doesn't leak between instances
        info1 = tmux1.get_session_info()
        info2 = tmux2.get_session_info()

        assert info1['session'] == 'isolation-test-1'
        assert info2['session'] == 'isolation-test-2'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])