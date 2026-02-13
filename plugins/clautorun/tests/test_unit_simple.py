#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simplified unit tests for clautorun core functionality
"""
import pytest
import sys
from pathlib import Path

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from clautorun import CONFIG, COMMAND_HANDLERS


class TestConfiguration:
    """Test configuration constants and mappings"""

    @pytest.mark.unit
    def test_three_stage_confirmations(self):
        """Test three-stage confirmation markers are present and correct"""
        # Stage 1 - dual-key pattern
        assert "stage1_instruction" in CONFIG
        assert "stage1_completion" in CONFIG  # Text injected TO AI
        assert "stage1_message" in CONFIG     # AI outputs BACK
        assert isinstance(CONFIG["stage1_message"], str)
        assert len(CONFIG["stage1_message"]) > 0
        assert "AUTORUN_INITIAL_TASKS_COMPLETED" in CONFIG["stage1_message"]

        # Stage 2 - dual-key pattern
        assert "stage2_instruction" in CONFIG
        assert "stage2_completion" in CONFIG  # Text injected TO AI
        assert "stage2_message" in CONFIG     # AI outputs BACK
        assert isinstance(CONFIG["stage2_message"], str)
        assert len(CONFIG["stage2_message"]) > 0
        assert "CRITICALLY_EVALUATING" in CONFIG["stage2_message"]

        # Stage 3 - dual-key pattern
        assert "stage3_instruction" in CONFIG
        assert "stage3_completion" in CONFIG  # Text injected TO AI
        assert "stage3_message" in CONFIG     # AI outputs BACK
        assert isinstance(CONFIG["stage3_message"], str)
        assert len(CONFIG["stage3_message"]) > 0
        assert "AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY" in CONFIG["stage3_message"]

    @pytest.mark.unit
    def test_emergency_stop(self):
        """Test emergency stop is present"""
        assert "emergency_stop" in CONFIG
        assert isinstance(CONFIG["emergency_stop"], str)
        assert len(CONFIG["emergency_stop"]) > 0

    @pytest.mark.unit
    def test_policies_configuration(self):
        """Test file policies are properly configured"""
        assert "policies" in CONFIG
        policies = CONFIG["policies"]

        # Check required policies exist
        required_policies = ["ALLOW", "JUSTIFY", "SEARCH"]
        for policy in required_policies:
            assert policy in policies, f"Missing policy: {policy}"
            assert isinstance(policies[policy], tuple), f"Policy {policy} should be a tuple"
            assert len(policies[policy]) == 2, f"Policy {policy} should have 2 elements"

    @pytest.mark.unit
    def test_command_mappings(self):
        """Test command mappings are properly configured"""
        assert "command_mappings" in CONFIG
        mappings = CONFIG["command_mappings"]

        # Check essential commands
        essential_commands = ["/afs", "/afa", "/afj", "/afst", "/autostop", "/estop"]
        for cmd in essential_commands:
            assert cmd in mappings, f"Missing command mapping: {cmd}"
            assert mappings[cmd], f"Command {cmd} should map to an action"

    @pytest.mark.unit
    def test_injection_template_present(self):
        """Test injection template is present and contains three-stage placeholders"""
        assert "injection_template" in CONFIG
        template = CONFIG["injection_template"]

        # Check for required three-stage placeholders (updated for dual-key pattern)
        required_placeholders = [
            "{emergency_stop}",
            "{stage1_instruction}",
            "{stage1_message}",  # Updated: AI output confirmation
            "{stage2_instruction}",
            "{stage2_message}",  # Updated: AI output confirmation
            "{stage3_instruction}",
            "{stage3_message}",  # Updated: AI output confirmation
            "{policy_instructions}"
        ]

        for placeholder in required_placeholders:
            assert placeholder in template, f"Missing placeholder: {placeholder}"

    @pytest.mark.unit
    def test_recheck_template_present(self):
        """Test recheck template is present"""
        assert "recheck_template" in CONFIG
        template = CONFIG["recheck_template"]

        # Check for required placeholders
        required_placeholders = [
            "{activation_prompt}",
            "{recheck_count}",
            "{max_recheck_count}"
        ]

        for placeholder in required_placeholders:
            assert placeholder in template, f"Missing placeholder: {placeholder}"


class TestCommandHandlers:
    """Test command handler functions"""

    @pytest.mark.unit
    def test_command_handlers_exist(self):
        """Test all required command handlers exist"""
        required_handlers = [
            "SEARCH", "ALLOW", "JUSTIFY", "STATUS", "stop", "emergency_stop", "activate"
        ]

        for handler in required_handlers:
            assert handler in COMMAND_HANDLERS, f"Missing handler: {handler}"
            assert callable(COMMAND_HANDLERS[handler]), f"Handler {handler} should be callable"

    @pytest.mark.unit
    def test_policy_handlers_return_strings(self):
        """Test policy handlers return correct string responses"""
        # Test each policy handler returns a string
        for policy in ["SEARCH", "ALLOW", "JUSTIFY"]:
            handler = COMMAND_HANDLERS[policy]
            result = handler({})
            assert isinstance(result, str), f"Handler {policy} should return string"
            assert len(result) > 0, f"Handler {policy} should return non-empty string"

    @pytest.mark.unit
    def test_policy_handlers_use_config_values(self):
        """Test policy handlers return strings derived from CONFIG (DRY).

        This verifies the DRY principle - handlers should use CONFIG['policies']
        tuple unpacking rather than hardcoded strings.
        """
        for policy in ["SEARCH", "ALLOW", "JUSTIFY"]:
            handler = COMMAND_HANDLERS[policy]
            result = handler({})

            # Verify the exact format matches CONFIG
            expected_name, expected_desc = CONFIG["policies"][policy]
            expected_format = f"AutoFile policy: {expected_name} - {expected_desc}"
            assert result == expected_format, \
                f"Handler {policy} should return CONFIG-derived string.\n" \
                f"Expected: {expected_format}\n" \
                f"Got: {result}"

    @pytest.mark.unit
    def test_status_handler(self):
        """Test status handler works without session state"""
        handler = COMMAND_HANDLERS["STATUS"]
        result = handler({})
        assert isinstance(result, str), "STATUS handler should return string"
        assert len(result) > 0, "STATUS handler should return non-empty string"

    @pytest.mark.unit
    def test_stop_handlers(self):
        """Test stop handlers return correct responses"""
        for handler_key in ["stop", "emergency_stop"]:
            handler = COMMAND_HANDLERS[handler_key]
            result = handler({})
            assert isinstance(result, str), f"{handler_key} handler should return string"
            assert len(result) > 0, f"{handler_key} handler should return non-empty string"

    @pytest.mark.unit
    def test_activate_handler_returns_injection_template(self):
        """Test activate handler returns injection template content"""
        handler = COMMAND_HANDLERS["activate"]
        test_state = {"session_id": "test_session"}  # Set session_id for monitor
        result = handler(test_state, "/autorun test task")

        assert isinstance(result, str), "activate handler should return string"
        assert "UNINTERRUPTED" in result, "Response should contain injection template"
        assert "AUTONOMOUS" in result, "Response should contain injection template"


class TestCommandDetection:
    """Test command detection logic"""

    @pytest.mark.unit
    def test_policy_commands_detected(self):
        """Test policy commands are detected correctly"""
        mappings = CONFIG["command_mappings"]
        policy_commands = ["/afs", "/afa", "/afj", "/afst"]

        for cmd in policy_commands:
            found = next((v for k, v in mappings.items() if k == cmd), None)
            assert found is not None, f"Command {cmd} should be detected"
            assert found in COMMAND_HANDLERS, f"Command {cmd} should have handler"

    @pytest.mark.unit
    def test_control_commands_detected(self):
        """Test control commands are detected correctly"""
        mappings = CONFIG["command_mappings"]
        control_commands = ["/autostop", "/estop"]

        for cmd in control_commands:
            found = next((v for k, v in mappings.items() if k == cmd), None)
            assert found is not None, f"Command {cmd} should be detected"
            assert found in ["stop", "emergency_stop"], f"Command {cmd} should map to stop or emergency_stop"

    @pytest.mark.unit
    def test_normal_commands_not_detected(self):
        """Test normal commands are not detected as autorun commands"""
        mappings = CONFIG["command_mappings"]
        normal_commands = ["help me", "what is this", "test file"]

        for cmd in normal_commands:
            found = next((v for k, v in mappings.items() if k == cmd), None)
            assert found is None, f"Normal command '{cmd}' should not be detected as autorun command"

    @pytest.mark.unit
    def test_autorun_command_detection(self):
        """Test autorun command detection"""
        mappings = CONFIG["command_mappings"]
        autorun_cmd = "/autorun test task description"

        found = next((v for k, v in mappings.items() if autorun_cmd.startswith(k)), None)
        assert found == "activate", "Autorun command should be detected as 'activate'"
        assert "activate" in COMMAND_HANDLERS, "Activate handler should exist"


class TestBasicFunctionality:
    """Test basic functionality without session state"""

    @pytest.mark.unit
    def test_configuration_constants_are_strings(self):
        """Test configuration constants are strings (updated for dual-key pattern)"""
        for key in ["stage1_message", "stage2_message", "stage3_message", "emergency_stop"]:
            assert key in CONFIG
            assert isinstance(CONFIG[key], str)
            assert len(CONFIG[key]) > 0

    @pytest.mark.unit
    def test_policies_have_correct_structure(self):
        """Test policies have correct tuple structure"""
        policies = CONFIG["policies"]
        for policy_name, policy_data in policies.items():
            assert isinstance(policy_data, tuple), f"Policy {policy_name} should be tuple"
            assert len(policy_data) == 2, f"Policy {policy_name} should have 2 elements"
            assert isinstance(policy_data[0], str), f"Policy {policy_name} first element should be string"
            assert isinstance(policy_data[1], str), f"Policy {policy_name} second element should be string"

    @pytest.mark.unit
    def test_command_handlers_are_callable(self):
        """Test all command handlers are callable"""
        for handler_name, handler_func in COMMAND_HANDLERS.items():
            assert callable(handler_func), f"Handler {handler_name} should be callable"

    @pytest.mark.unit
    def test_command_handlers_accept_state_argument(self):
        """Test command handlers accept state argument"""
        from unittest.mock import patch, MagicMock

        # Mock session_state to avoid shelve/dbm errors in handlers that access persistence
        mock_session_ctx = MagicMock()
        mock_session_ctx.__enter__ = MagicMock(return_value={})
        mock_session_ctx.__exit__ = MagicMock(return_value=None)

        with patch('clautorun.main.session_state', return_value=mock_session_ctx), \
             patch('clautorun.main.initialize_default_blocks', return_value=None):
            # Test each handler with appropriate arguments
            for handler_name, handler_func in COMMAND_HANDLERS.items():
                try:
                    # All handlers should accept at least one argument
                    if handler_name in ["activate", "ACTIVATE"]:
                        # Activate handler needs session_id and prompt argument
                        test_state = {"session_id": "test_session"}
                        result = handler_func(test_state, "/autorun test")
                    else:
                        # Other handlers need state dict with session_id
                        test_state = {"session_id": "test_session"}
                        result = handler_func(test_state)
                    assert result is not None, f"Handler {handler_name} should return something"
                except TypeError as e:
                    if "missing" in str(e) and "positional argument" in str(e):
                        pytest.fail(f"Handler {handler_name} should accept at least one argument")
                    else:
                        raise


class TestSecurityFunctions:
    """Test security-related functions"""

    @pytest.mark.unit
    def test_sanitize_log_message_newlines(self):
        """Test that newlines are escaped in log messages"""
        from clautorun.main import sanitize_log_message

        # Test newline injection attack
        malicious = "normal\n[FAKE] Injected log entry\nmore"
        result = sanitize_log_message(malicious)
        assert '\n' not in result, "Newlines should be escaped"
        assert '\\n' in result, "Newlines should be replaced with \\n"

    @pytest.mark.unit
    def test_sanitize_log_message_carriage_return(self):
        """Test that carriage returns are escaped"""
        from clautorun.main import sanitize_log_message

        malicious = "normal\r\n[FAKE]\rmore"
        result = sanitize_log_message(malicious)
        assert '\r' not in result, "Carriage returns should be escaped"
        assert '\n' not in result, "Newlines should be escaped"

    @pytest.mark.unit
    def test_sanitize_log_message_truncation(self):
        """Test that long messages are truncated"""
        from clautorun.main import sanitize_log_message

        long_message = "a" * 20000
        result = sanitize_log_message(long_message, max_length=100)
        assert len(result) < 150, "Message should be truncated"
        assert "truncated" in result, "Should indicate truncation"

    @pytest.mark.unit
    def test_is_safe_regex_rejects_nested_quantifiers(self):
        """Test ReDoS protection rejects dangerous patterns"""
        from clautorun.main import is_safe_regex_pattern

        # Dangerous patterns with nested quantifiers
        dangerous_patterns = [
            "(a+)+",      # Classic ReDoS
            "(a*)+",
            "(a+)*",
            "([a-z]+)*",
            "((a+))+",
        ]

        for pattern in dangerous_patterns:
            assert is_safe_regex_pattern(pattern) is False, \
                f"Dangerous pattern should be rejected: {pattern}"

    @pytest.mark.unit
    def test_is_safe_regex_accepts_safe_patterns(self):
        """Test that safe regex patterns are accepted"""
        from clautorun.main import is_safe_regex_pattern

        safe_patterns = [
            r"rm\s+-rf",
            r"git\s+reset",
            r"eval\(",
            r"[a-z]+",
            r"\d{3}-\d{4}",
        ]

        for pattern in safe_patterns:
            assert is_safe_regex_pattern(pattern) is True, \
                f"Safe pattern should be accepted: {pattern}"

    @pytest.mark.unit
    def test_is_safe_regex_rejects_long_patterns(self):
        """Test that excessively long patterns are rejected"""
        from clautorun.main import is_safe_regex_pattern

        long_pattern = "a" * 300
        assert is_safe_regex_pattern(long_pattern) is False, \
            "Long pattern should be rejected"

    @pytest.mark.unit
    def test_is_safe_regex_rejects_invalid_patterns(self):
        """Test that invalid regex syntax is rejected"""
        from clautorun.main import is_safe_regex_pattern

        invalid_patterns = [
            "[unclosed",
            "(unclosed",
            "**invalid",
        ]

        for pattern in invalid_patterns:
            assert is_safe_regex_pattern(pattern) is False, \
                f"Invalid pattern should be rejected: {pattern}"


class TestCodeQuality:
    """Test code quality requirements - no stderr/stdout pollution"""

    @pytest.mark.unit
    def test_no_stderr_writes_in_hook_path(self):
        """Test that hook execution path has zero stderr writes.

        Hook execution path includes:
        - hooks/hook_entry.py
        - src/clautorun/client.py
        - src/clautorun/core.py
        - src/clautorun/plugins.py
        - src/clautorun/integrations.py
        - src/clautorun/command_detection.py
        - src/clautorun/config.py
        - src/clautorun/session_manager.py

        Claude Code treats ANY stderr as "hook error" and silently ignores
        hook JSON responses, disabling all safety features.
        """
        import subprocess
        from pathlib import Path

        src_dir = Path(__file__).parent.parent / "src" / "clautorun"
        hook_path_files = [
            "client.py",
            "core.py",
            "plugins.py",
            "integrations.py",
            "command_detection.py",
            "config.py",
            "session_manager.py",
        ]

        for filename in hook_path_files:
            filepath = src_dir / filename
            if not filepath.exists():
                continue

            # Search for stderr writes
            # EXCEPTION: print(reason, file=sys.stderr) is ALLOWED for exit code 2 workaround (Bug #4669)
            result = subprocess.run(
                ["grep", "-n", "file=sys.stderr", str(filepath)],
                capture_output=True,
                text=True
            )
    
            if result.returncode == 0:  # grep found matches
                # Filter out the intentional workaround
                violations = [v for v in result.stdout.strip().split('\n') 
                             if "print(reason, file=sys.stderr)" not in v]
                
                if violations:
                    pytest.fail(
                        f"\n{'='*70}\n"
                        f"CRITICAL: Found {len(violations)} stderr write(s) in {filename}\n"
                        f"{'='*70}\n"
                        f"Claude Code hook requirement: ZERO stderr output\n"
                        f"Impact: Hook errors, silent safety feature failures\n\n"
                        f"Violations found:\n" + "\n".join(violations) + "\n"
                        f"{'='*70}\n"
                        f"FIX: Replace with logging_utils.get_logger() or remove\n"
                        f"{'='*70}"
                    )

    @pytest.mark.unit
    def test_no_default_logging_in_hook_path(self):
        """Test that hook path has no logging.basicConfig without handlers.

        Python's logging defaults to stderr when no handlers specified.
        This breaks Claude Code hooks.
        """
        import subprocess
        from pathlib import Path

        src_dir = Path(__file__).parent.parent / "src" / "clautorun"
        hook_path_files = [
            "client.py",
            "core.py",
            "plugins.py",
            "integrations.py",
            "command_detection.py",
            "session_manager.py",
        ]

        violations = []
        for filename in hook_path_files:
            filepath = src_dir / filename
            if not filepath.exists():
                continue

            content = filepath.read_text()
            lines = content.split('\n')

            for i, line in enumerate(lines, 1):
                # Check for logging.basicConfig without explicit handlers or filename
                # Both handlers= and filename= are safe (create file handlers)
                # Missing both means default stderr handler
                if 'logging.basicConfig' in line:
                    # Get context around this line (±10 lines)
                    start_idx = max(0, i - 10)
                    end_idx = min(len(lines), i + 10)
                    context = '\n'.join(lines[start_idx:end_idx])

                    # Check if handlers= or filename= appears in context
                    if 'handlers=' not in context and 'filename=' not in context:
                        violations.append(f"{filename}:{i}: {line.strip()}")

        if violations:
            pytest.fail(
                f"\n{'='*70}\n"
                f"CRITICAL: Found logging.basicConfig without handlers\n"
                f"{'='*70}\n"
                f"Python logging defaults to stderr when no handlers specified\n"
                f"Impact: Hook errors, silent safety feature failures\n\n"
                f"Violations:\n" + "\n".join(f"  {v}" for v in violations) + "\n"
                f"{'='*70}\n"
                f"FIX: Use handlers=[logging.FileHandler(...)] or handlers=[logging.NullHandler()]\n"
                f"See: logging_utils.py for correct pattern\n"
                f"{'='*70}"
            )

    @pytest.mark.unit
    def test_no_print_to_stderr_anywhere(self):
        """Test that NO Python files have print(..., file=sys.stderr).

        This is a global check across all source files to prevent
        accidental introduction of stderr output.
        """
        import subprocess
        from pathlib import Path

        src_dir = Path(__file__).parent.parent / "src" / "clautorun"

        # Search all Python files recursively
        result = subprocess.run(
            ["grep", "-r", "-n", "file=sys.stderr", "--include=*.py", str(src_dir)],
            capture_output=True,
            text=True
        )

        if result.returncode == 0:  # grep found matches
            # EXCEPTION: print(reason, file=sys.stderr) is ALLOWED for exit code 2 workaround (Bug #4669)
            violations = [v for v in result.stdout.strip().split('\n')
                         if "print(reason, file=sys.stderr)" not in v]
            
            if violations:
                pytest.fail(
                    f"\n{'='*70}\n"
                    f"CRITICAL: Found {len(violations)} file=sys.stderr usage(s)\n"
                    f"{'='*70}\n"
                    f"Requirement: ZERO stderr output in entire codebase\n"
                    f"Impact: Breaks Claude Code hooks, disables safety features\n\n"
                    f"Files with violations:\n" + "\n".join(violations) + "\n"
                    f"{'='*70}\n"
                    f"FIX: Use logging_utils.get_logger() or print() without file= arg\n"
                    f"CLI error messages: Use print() to stdout (not stderr)\n"
                    f"Diagnostics: Use logger.error/info/debug (file-only)\n"
                    f"{'='*70}"
                )

        