#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simplified unit tests for autorun core functionality
"""
import pytest
import sys
from pathlib import Path

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from autorun import CONFIG, COMMAND_HANDLERS
# Daemon-path imports for migrated tests
from autorun.core import EventContext, ThreadSafeDB
from autorun import plugins


def _dispatch(prompt: str, session_id: str = "test-unit") -> dict:
    """Dispatch a command via daemon-path and return the result dict."""
    ctx = EventContext(
        session_id=session_id,
        event="UserPromptSubmit",
        prompt=prompt,
        tool_name="",
        tool_input={},
        store=ThreadSafeDB(),
    )
    return plugins.app.dispatch(ctx) or {}


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
    """Test command handler functions — daemon-path (EventContext + plugins.app.dispatch).

    Canonical replacement for COMMAND_HANDLERS dict (removed in Phase 2):
    SEARCH → /ar:f, ALLOW → /ar:a, JUSTIFY → /ar:j, STATUS → /ar:st,
    stop → /ar:x, emergency_stop → /ar:sos, activate → /ar:go
    """

    @pytest.mark.unit
    def test_command_handlers_exist(self):
        """Test all required command handlers are registered in plugins.app."""
        # Daemon-path uses app.command_handlers keyed by canonical command string
        # Canonical commands registered via @app.command() in plugins.py
        expected_commands = ["/ar:f", "/ar:a", "/ar:j", "/ar:st", "/ar:x", "/ar:sos", "/ar:go"]
        ch = plugins.app.command_handlers
        for cmd in expected_commands:
            assert cmd in ch or any(c.startswith(cmd) or cmd.startswith(c) for c in ch), \
                f"Command {cmd} should be registered in plugins.app.command_handlers; got: {sorted(ch.keys())}"

    @pytest.mark.unit
    def test_policy_handlers_return_strings(self):
        """Policy handlers (/ar:f, /ar:a, /ar:j) return non-empty systemMessage."""
        # Daemon canonical: /ar:f=SEARCH, /ar:a=ALLOW, /ar:j=JUSTIFY
        for cmd in ["/ar:f", "/ar:a", "/ar:j"]:
            result = _dispatch(cmd)
            sm = result.get("systemMessage", "")
            assert isinstance(sm, str), f"Command {cmd} should return systemMessage string"
            assert len(sm) > 0, f"Command {cmd} should return non-empty systemMessage"

    @pytest.mark.unit
    def test_policy_handlers_use_config_values(self):
        """Policy handler responses contain CONFIG-derived policy names (DRY check).

        Daemon format: '✅ AutoFile policy: {name}\\n\\n{desc}'
        Old format was: 'AutoFile policy: {name} - {desc}'
        """
        policy_map = {
            "/ar:f": "SEARCH",
            "/ar:a": "ALLOW",
            "/ar:j": "JUSTIFY",
        }
        for cmd, policy_key in policy_map.items():
            result = _dispatch(cmd)
            sm = result.get("systemMessage", "")
            expected_name, _ = CONFIG["policies"][policy_key]
            assert expected_name in sm, \
                f"Command {cmd} response should contain policy name '{expected_name}'; got: {sm!r}"

    @pytest.mark.unit
    def test_status_handler(self):
        """Status handler (/ar:st) returns non-empty systemMessage."""
        result = _dispatch("/ar:st")
        sm = result.get("systemMessage", "")
        assert isinstance(sm, str), "STATUS (/ar:st) should return systemMessage string"
        assert len(sm) > 0, "STATUS (/ar:st) should return non-empty systemMessage"

    @pytest.mark.unit
    def test_stop_handlers(self):
        """Stop handlers (/ar:x, /ar:sos) return non-empty systemMessage."""
        for cmd in ["/ar:x", "/ar:sos"]:
            result = _dispatch(cmd)
            sm = result.get("systemMessage", "")
            assert isinstance(sm, str), f"Command {cmd} should return systemMessage string"
            assert len(sm) > 0, f"Command {cmd} should return non-empty systemMessage"

    @pytest.mark.unit
    def test_activate_handler_returns_acknowledgment(self):
        """Activate handler (/ar:go) returns acknowledgment with task info.

        Old test expected 'UNINTERRUPTED'/'AUTONOMOUS' in response because old
        handle_activate returned the full injection_template string. New daemon-path
        handle_activate returns a short acknowledgment; injection template is
        separately delivered via ctx.block() mechanism. Check for stage markers.
        """
        result = _dispatch("/ar:go test task description")
        sm = result.get("systemMessage", "")
        assert isinstance(sm, str), "/ar:go should return systemMessage string"
        assert len(sm) > 0, "/ar:go should return non-empty systemMessage"
        # New format: '✅ Autorun: {task}\n📁 {policy}\n🔄 Stages: 1→2→3\n⚠️ EMERGENCY_STOP_SIGNAL'
        assert "Autorun" in sm or "autorun" in sm.lower() or "Stage" in sm or "stage" in sm.lower(), \
            f"/ar:go response should reference autorun or stages; got: {sm!r}"


class TestCommandDetection:
    """Test command detection logic.

    Commands migrated to /ar: prefix (legacy /afs, /afa, /afj, /afst, /autorun, /autostop, /estop
    now exist as aliases in command_mappings but canonical forms are /ar:f, /ar:a, /ar:j, /ar:st,
    /ar:go, /ar:x, /ar:sos).
    """

    @pytest.mark.unit
    def test_policy_commands_detected(self):
        """Test canonical policy commands are in command_mappings and dispatch correctly."""
        mappings = CONFIG["command_mappings"]
        # Canonical /ar: commands (primary) — old /afs etc. are legacy aliases
        policy_commands = ["/ar:f", "/ar:a", "/ar:j", "/ar:st"]

        for cmd in policy_commands:
            found = next((v for k, v in mappings.items() if k == cmd), None)
            assert found is not None, f"Command {cmd} should be in command_mappings"
            # Verify daemon-path dispatch works (handler registered in plugins.app)
            result = _dispatch(cmd)
            sm = result.get("systemMessage", "")
            assert len(sm) > 0, f"Command {cmd} should produce non-empty systemMessage"

    @pytest.mark.unit
    def test_control_commands_detected(self):
        """Test control commands are detected correctly"""
        mappings = CONFIG["command_mappings"]
        # Canonical commands (check both new /ar: and legacy aliases if present)
        control_commands = ["/ar:x", "/ar:sos"]

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
        """Test activate command detection via /ar:go (canonical) and /ar:run (alias)."""
        mappings = CONFIG["command_mappings"]
        # Canonical activate command
        for autorun_cmd in ["/ar:go test task description", "/ar:run test task"]:
            found = next((v for k, v in mappings.items() if autorun_cmd.startswith(k)), None)
            assert found == "activate", \
                f"Command '{autorun_cmd}' should map to 'activate'; got: {found}"
        # Verify daemon-path dispatch works for /ar:go
        ch = plugins.app.command_handlers
        assert any("/ar:go" in k for k in ch) or any(k.startswith("/ar:go") for k in ch) or "/ar:go" in ch, \
            f"/ar:go should be registered in plugins.app.command_handlers; got: {sorted(ch.keys())}"


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
        """Test all registered command handlers are callable via daemon-path.

        Daemon-path: plugins.app.command_handlers contains the registered handlers.
        Old COMMAND_HANDLERS dict was removed in Phase 2; this test verifies
        the canonical replacement (plugins.app.command_handlers) has callable entries.
        """
        ch = plugins.app.command_handlers
        assert len(ch) > 0, "plugins.app.command_handlers should not be empty"
        for cmd, handler_func in ch.items():
            assert callable(handler_func), f"Handler for '{cmd}' should be callable"

    @pytest.mark.unit
    def test_command_handlers_accept_eventcontext(self):
        """Test daemon-path handlers accept EventContext and return results.

        Replaces old test that patched autorun.main.initialize_default_blocks
        (removed in Phase 2) and used state-dict interface. Daemon-path handlers
        take EventContext (not state dicts); _dispatch() helper creates EventContexts.
        """
        # All canonical commands should dispatch without error and return a dict
        canonical_commands = ["/ar:f", "/ar:a", "/ar:j", "/ar:st", "/ar:x", "/ar:sos"]
        for cmd in canonical_commands:
            result = _dispatch(cmd, session_id=f"test-unit-{cmd.replace('/', '-')}")
            assert isinstance(result, dict), \
                f"Daemon dispatch of '{cmd}' should return dict; got {type(result)}"


class TestSecurityFunctions:
    """Test security-related functions"""

    @pytest.mark.unit
    def test_sanitize_log_message_newlines(self):
        """Test that newlines are escaped in log messages"""
        from autorun.main import sanitize_log_message

        # Test newline injection attack
        malicious = "normal\n[FAKE] Injected log entry\nmore"
        result = sanitize_log_message(malicious)
        assert '\n' not in result, "Newlines should be escaped"
        assert '\\n' in result, "Newlines should be replaced with \\n"

    @pytest.mark.unit
    def test_sanitize_log_message_carriage_return(self):
        """Test that carriage returns are escaped"""
        from autorun.main import sanitize_log_message

        malicious = "normal\r\n[FAKE]\rmore"
        result = sanitize_log_message(malicious)
        assert '\r' not in result, "Carriage returns should be escaped"
        assert '\n' not in result, "Newlines should be escaped"

    @pytest.mark.unit
    def test_sanitize_log_message_truncation(self):
        """Test that long messages are truncated"""
        from autorun.main import sanitize_log_message

        long_message = "a" * 20000
        result = sanitize_log_message(long_message, max_length=100)
        assert len(result) < 150, "Message should be truncated"
        assert "truncated" in result, "Should indicate truncation"

    @pytest.mark.unit
    def test_is_safe_regex_rejects_nested_quantifiers(self):
        """Test ReDoS protection rejects dangerous patterns"""
        from autorun.main import is_safe_regex_pattern

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
        from autorun.main import is_safe_regex_pattern

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
        from autorun.main import is_safe_regex_pattern

        long_pattern = "a" * 300
        assert is_safe_regex_pattern(long_pattern) is False, \
            "Long pattern should be rejected"

    @pytest.mark.unit
    def test_is_safe_regex_rejects_invalid_patterns(self):
        """Test that invalid regex syntax is rejected"""
        from autorun.main import is_safe_regex_pattern

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
        - src/autorun/client.py
        - src/autorun/core.py
        - src/autorun/plugins.py
        - src/autorun/integrations.py
        - src/autorun/command_detection.py
        - src/autorun/config.py
        - src/autorun/session_manager.py

        Claude Code treats ANY stderr as "hook error" and silently ignores
        hook JSON responses, disabling all safety features.
        """
        import subprocess
        from pathlib import Path

        src_dir = Path(__file__).parent.parent / "src" / "autorun"
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

        src_dir = Path(__file__).parent.parent / "src" / "autorun"
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

            content = filepath.read_text(encoding="utf-8")
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

        src_dir = Path(__file__).parent.parent / "src" / "autorun"

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


# =============================================================================
# Task Staleness Reminder + Three-Stage Stage-Reset Guard (v0.9)
# =============================================================================
# TDD: these tests are written FIRST. Run them before implementation to confirm
# they fail, then implement Steps 1-5 until all pass.

from typing import Optional as _Optional
from autorun.task_lifecycle import TaskLifecycle as _TaskLifecycle


def _make_post_tool_ctx(
    tool_name: str,
    session_id: str,
    *,
    autorun_active: bool = True,
    task_staleness_enabled: bool = True,
    tool_calls_since_task_update: int = 0,
    task_staleness_threshold: _Optional[int] = None,
) -> EventContext:
    """Build PostToolUse EventContext for staleness tests."""
    ctx = EventContext(
        session_id=session_id,
        event="PostToolUse",
        prompt="",
        tool_name=tool_name,
        tool_input={},
        tool_result="",
        store=ThreadSafeDB(),
    )
    ctx.autorun_active = autorun_active
    ctx.task_staleness_enabled = task_staleness_enabled
    ctx.tool_calls_since_task_update = tool_calls_since_task_update
    if task_staleness_threshold is not None:
        ctx.task_staleness_threshold = task_staleness_threshold
    return ctx


def _make_pending_task(session_id: str, task_id: str = "1", subject: str = "test task") -> None:
    """Create a pending task in the task lifecycle DB for the given session.

    Uses TaskLifecycle.create_task() directly to avoid the regex-parsing
    fragility of handle_task_create() which expects "Task #N created" format.
    """
    manager = _TaskLifecycle(session_id=session_id)
    manager.create_task(task_id, {"subject": subject}, f"Task #{task_id} created successfully")


# ── Staleness counter ──────────────────────────────────────────────────────

def test_staleness_counter_increments_on_tool_call():
    """Counter increments for non-task tool calls when autorun active."""
    ctx = _make_post_tool_ctx("Bash", "test-stale-incr",
                               tool_calls_since_task_update=0)
    plugins.app.dispatch(ctx)
    assert (ctx.tool_calls_since_task_update or 0) == 1


def test_staleness_injection_at_threshold():
    """Warning injected when counter reaches threshold and incomplete tasks exist."""
    sid = "test-stale-inject"
    _make_pending_task(sid, "1", "incomplete task")
    ctx = _make_post_tool_ctx("Bash", sid,
                               tool_calls_since_task_update=2,
                               task_staleness_threshold=3)
    result = plugins.app.dispatch(ctx) or {}
    additional = result.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "TASK LIST STALE" in additional


def test_staleness_counter_resets_on_task_create():
    """TaskCreate call resets the counter."""
    ctx = _make_post_tool_ctx("TaskCreate", "test-stale-reset-create",
                               tool_calls_since_task_update=20)
    plugins.app.dispatch(ctx)
    assert (ctx.tool_calls_since_task_update or 0) == 0


def test_staleness_counter_resets_on_task_update():
    """TaskUpdate call resets the counter."""
    ctx = _make_post_tool_ctx("TaskUpdate", "test-stale-reset-update",
                               tool_calls_since_task_update=20)
    plugins.app.dispatch(ctx)
    assert (ctx.tool_calls_since_task_update or 0) == 0


def test_staleness_disabled_no_injection():
    """Disabled reminder does not inject even over threshold."""
    ctx = _make_post_tool_ctx("Bash", "test-stale-off",
                               task_staleness_enabled=False,
                               tool_calls_since_task_update=50)
    result = plugins.app.dispatch(ctx) or {}
    assert "TASK LIST STALE" not in str(result)


def test_staleness_fires_without_autorun_when_tasks_exist():
    """Staleness reminder fires even when autorun_active=False if incomplete tasks exist."""
    sid = "test-stale-no-autorun-with-tasks"
    _make_pending_task(sid, "1", "incomplete task")
    ctx = _make_post_tool_ctx("Bash", sid,
                               autorun_active=False,
                               tool_calls_since_task_update=50,
                               task_staleness_threshold=3)
    result = plugins.app.dispatch(ctx) or {}
    additional = result.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "TASK LIST STALE" in additional


def test_staleness_no_injection_when_all_tasks_complete():
    """Staleness reminder does not fire when tasks exist but all are complete."""
    sid = "test-stale-all-complete"
    _make_pending_task(sid, "1", "done task")
    # Mark it complete
    from autorun.session_manager import session_state
    key = f"__task_lifecycle__{sid}"
    with session_state(key) as st:
        tasks = st.get("tasks", {})
        if "1" in tasks:
            tasks["1"]["status"] = "completed"
            st["tasks"] = tasks
    ctx = _make_post_tool_ctx("Bash", sid,
                               tool_calls_since_task_update=50,
                               task_staleness_threshold=3)
    result = plugins.app.dispatch(ctx) or {}
    assert "TASK LIST STALE" not in str(result)
    assert "NO TASKS EXIST" not in str(result)


def test_staleness_fires_when_zero_tasks_exist():
    """Staleness reminder fires with lower threshold when zero tasks exist."""
    ctx = _make_post_tool_ctx("Bash", "test-stale-zero-tasks",
                               tool_calls_since_task_update=10,
                               task_staleness_threshold=25)
    result = plugins.app.dispatch(ctx) or {}
    additional = result.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "NO TASKS EXIST" in additional


def test_staleness_no_fire_below_zero_tasks_threshold():
    """Staleness reminder does NOT fire below the no_tasks_threshold (default 5)."""
    ctx = _make_post_tool_ctx("Bash", "test-stale-zero-below-thresh",
                               tool_calls_since_task_update=3,
                               task_staleness_threshold=25)
    result = plugins.app.dispatch(ctx) or {}
    assert "NO TASKS EXIST" not in str(result)
    assert "TASK LIST STALE" not in str(result)


# ── Task creation reminder (v0.10) ───────────────────────────────────────

def test_plan_command_sets_planning_reminder_flag():
    """Plan command sets plan_awaiting_planning_tasks flag."""
    sid = "test-plan-cmd-flag"
    _dispatch("/ar:plannew", session_id=sid)
    ctx = EventContext(session_id=sid, event="PostToolUse", prompt="",
                       tool_name="Bash", store=ThreadSafeDB())
    assert ctx.plan_awaiting_planning_tasks is True
    assert ctx.plan_active is True


def test_planning_reminder_fires_on_every_post_tool_use():
    """Reminder fires on every PostToolUse when planning flag is set."""
    sid = "test-planning-reminder-fires"
    store = ThreadSafeDB()
    ctx = EventContext(session_id=sid, event="PostToolUse", prompt="",
                       tool_name="Bash", tool_input={}, tool_result="",
                       store=store)
    ctx.plan_awaiting_planning_tasks = True
    results = []
    for _ in range(3):
        ctx = EventContext(session_id=sid, event="PostToolUse", prompt="",
                           tool_name="Bash", tool_input={}, tool_result="",
                           store=store)
        result = plugins.app.dispatch(ctx) or {}
        results.append(result)
    assert all("PLANNING TASKS REQUIRED" in str(r) for r in results)


def test_planning_reminder_clears_on_task_create():
    """TaskCreate clears the planning reminder flag."""
    sid = "test-planning-clears"
    store = ThreadSafeDB()
    ctx = EventContext(session_id=sid, event="PostToolUse", prompt="",
                       tool_name="Bash", tool_input={}, tool_result="",
                       store=store)
    ctx.plan_awaiting_planning_tasks = True

    # TaskCreate should clear the flag
    ctx2 = EventContext(session_id=sid, event="PostToolUse", prompt="",
                        tool_name="TaskCreate", tool_input={}, tool_result="",
                        store=store)
    plugins.app.dispatch(ctx2)

    # Next call should be silent
    ctx3 = EventContext(session_id=sid, event="PostToolUse", prompt="",
                        tool_name="Bash", tool_input={}, tool_result="",
                        store=store)
    result = plugins.app.dispatch(ctx3) or {}
    assert "PLANNING TASKS REQUIRED" not in str(result)


def test_plan_acceptance_sets_execution_reminder_and_clears_planning():
    """Plan acceptance sets execution flag and clears planning flag."""
    sid = "test-acceptance-flags"
    store = ThreadSafeDB()

    # Simulate: plan command was invoked (planning flag set)
    ctx = EventContext(session_id=sid, event="PostToolUse", prompt="",
                       tool_name="Bash", tool_input={}, tool_result="",
                       store=store)
    ctx.plan_awaiting_planning_tasks = True
    ctx.plan_arguments = "test plan"

    # Simulate plan acceptance via detect_plan_approval
    ctx2 = EventContext(session_id=sid, event="PostToolUse", prompt="",
                        tool_name="ExitPlanMode", tool_input={},
                        tool_result="User has approved your plan. You can now start coding.",
                        store=store)
    plugins.app.dispatch(ctx2)

    # Check flags
    ctx3 = EventContext(session_id=sid, event="PostToolUse", prompt="",
                        tool_name="Bash", tool_input={}, tool_result="",
                        store=store)
    assert ctx3.plan_awaiting_planning_tasks is False
    assert ctx3.plan_awaiting_execution_tasks is True


def test_execution_reminder_fires_until_task_create():
    """Execution reminder fires until TaskCreate is called."""
    sid = "test-exec-reminder"
    store = ThreadSafeDB()
    ctx = EventContext(session_id=sid, event="PostToolUse", prompt="",
                       tool_name="Bash", tool_input={}, tool_result="",
                       store=store)
    ctx.plan_awaiting_execution_tasks = True

    # Should fire
    ctx2 = EventContext(session_id=sid, event="PostToolUse", prompt="",
                        tool_name="Bash", tool_input={}, tool_result="",
                        store=store)
    result = plugins.app.dispatch(ctx2) or {}
    assert "EXECUTION TASKS REQUIRED" in str(result)

    # TaskCreate clears
    ctx3 = EventContext(session_id=sid, event="PostToolUse", prompt="",
                        tool_name="TaskCreate", tool_input={}, tool_result="",
                        store=store)
    plugins.app.dispatch(ctx3)

    # Should be silent
    ctx4 = EventContext(session_id=sid, event="PostToolUse", prompt="",
                        tool_name="Bash", tool_input={}, tool_result="",
                        store=store)
    result2 = plugins.app.dispatch(ctx4) or {}
    assert "EXECUTION TASKS REQUIRED" not in str(result2)


def test_execution_reminder_references_tdd_exec():
    """Execution reminder message contains [TDD] and [EXEC]."""
    from autorun.config import CONFIG
    msg = CONFIG["plan_execution_task_reminder"]
    assert "[TDD]" in msg
    assert "[EXEC]" in msg
    assert "[VERIFY]" in msg


def test_planning_reminder_references_planning_format():
    """Planning reminder message contains [PLANNING]."""
    from autorun.config import CONFIG
    msg = CONFIG["plan_planning_task_reminder"]
    assert "[PLANNING]" in msg


def test_no_reminder_when_flags_not_set():
    """No reminder fires when both flags are False (default)."""
    ctx = EventContext(session_id="test-no-reminder", event="PostToolUse",
                       prompt="", tool_name="Bash", tool_input={}, tool_result="",
                       store=ThreadSafeDB())
    result = plugins.app.dispatch(ctx) or {}
    assert "PLANNING TASKS REQUIRED" not in str(result)
    assert "EXECUTION TASKS REQUIRED" not in str(result)


# ── /ar:tasks command ──────────────────────────────────────────────────────

def test_tasks_command_on():
    """/ar:tasks on enables staleness reminders and returns confirmation."""
    result = _dispatch("/ar:tasks on", session_id="test-tasks-cmd-on")
    assert "enabled" in str(result).lower()


def test_tasks_command_off():
    """/ar:tasks off disables staleness reminders."""
    sid = "test-tasks-cmd-off"
    _dispatch("/ar:tasks off", session_id=sid)
    ctx = EventContext(session_id=sid, event="PostToolUse", prompt="",
                       tool_name="Bash", store=ThreadSafeDB())
    assert ctx.task_staleness_enabled is False


def test_tasks_command_threshold_override():
    """/ar:tasks 5 sets per-session threshold."""
    sid = "test-tasks-thresh"
    _dispatch("/ar:tasks 5", session_id=sid)
    ctx = EventContext(session_id=sid, event="PostToolUse", prompt="",
                       tool_name="Bash", store=ThreadSafeDB())
    assert ctx.task_staleness_threshold == 5


def test_tasks_command_status():
    """/ar:tasks (no args) returns a status string."""
    result = _dispatch("/ar:tasks", session_id="test-tasks-status")
    text = str(result).lower()
    assert "staleness" in text or "on" in text or "off" in text


def test_tasks_command_shows_task_summary():
    """/ar:tasks (no args) shows task counts and incomplete task subjects when lifecycle is enabled."""
    from unittest.mock import patch, MagicMock

    fake_tasks = {
        "1": {"id": "1", "subject": "Implement login", "status": "completed"},
        "2": {"id": "2", "subject": "Add tests", "status": "in_progress"},
        "3": {"id": "3", "subject": "Write docs", "status": "pending"},
    }
    fake_incomplete = [
        {"id": "2", "subject": "Add tests", "status": "in_progress"},
        {"id": "3", "subject": "Write docs", "status": "pending"},
    ]

    mock_manager = MagicMock()
    mock_manager.tasks = fake_tasks
    mock_manager.get_incomplete_tasks.return_value = fake_incomplete

    with patch("autorun.plugins.task_lifecycle.is_enabled", return_value=True), \
         patch("autorun.plugins.task_lifecycle.TaskLifecycle", return_value=mock_manager):
        result = _dispatch("/ar:tasks", session_id="test-tasks-summary")

    text = str(result)
    assert "3 total" in text, f"Should show total task count, got: {text}"
    assert "1 done" in text, f"Should show completed count, got: {text}"
    assert "1 active" in text, f"Should show in_progress count, got: {text}"
    assert "1 pending" in text, f"Should show pending count, got: {text}"
    assert "Add tests" in text, f"Should show incomplete task subject, got: {text}"
    assert "Write docs" in text, f"Should show incomplete task subject, got: {text}"
    assert "Staleness" in text or "staleness" in text, f"Should still show staleness info, got: {text}"


def test_tasks_command_no_tasks_shows_none_tracked():
    """/ar:tasks (no args) shows 'none tracked' when lifecycle enabled but no tasks."""
    from unittest.mock import patch, MagicMock

    mock_manager = MagicMock()
    mock_manager.tasks = {}

    with patch("autorun.plugins.task_lifecycle.is_enabled", return_value=True), \
         patch("autorun.plugins.task_lifecycle.TaskLifecycle", return_value=mock_manager):
        result = _dispatch("/ar:tasks", session_id="test-tasks-none")

    text = str(result)
    assert "none tracked" in text, f"Should show 'none tracked', got: {text}"


def test_staleness_threshold_zero_validation():
    """Threshold 0 is rejected with an error message."""
    result = _dispatch("/ar:tasks 0", session_id="test-thresh-zero")
    text = str(result).lower()
    assert "invalid" in text or "positive" in text or "at least" in text


def test_staleness_threshold_negative_validation():
    """Negative threshold is rejected with an error message (not silent fallthrough)."""
    result = _dispatch("/ar:tasks -5", session_id="test-thresh-neg")
    text = str(result).lower()
    assert "invalid" in text or "positive" in text


# ── Stage reset in handle_stop() ──────────────────────────────────────────

def test_stage_reset_when_stage2_completed_and_tasks_outstanding():
    """handle_stop() resets STAGE_2_COMPLETED -> STAGE_2 when tasks outstanding."""
    sid = "test-stage-reset"
    _make_pending_task(sid, task_id="1", subject="test outstanding task")

    ctx = EventContext(
        session_id=sid,
        event="Stop",
        prompt="",
        store=ThreadSafeDB(),
    )
    ctx.autorun_active = True
    ctx.autorun_stage = EventContext.STAGE_2_COMPLETED
    result = plugins.app.dispatch(ctx) or {}

    assert ctx.autorun_stage == EventContext.STAGE_2, (
        f"Stage should be STAGE_2 after reset, got {ctx.autorun_stage}"
    )
    combined = str(result)
    assert "CANNOT STOP" in combined or "THREE-STAGE" in combined


def test_no_stage_reset_when_no_tasks_outstanding():
    """handle_stop() does NOT reset stage when no tasks outstanding.

    With no outstanding tasks, prevent_premature_stop returns None and
    autorun_injection runs. autorun_injection at STAGE_2_COMPLETED injects
    countdown messages (lines 1054-1061 of plugins.py) — result is not None.
    The key assertion is: stage remains STAGE_2_COMPLETED (no reset), and
    the THREE-STAGE SYSTEM RESET message is NOT injected.
    """
    sid = "test-stage-no-reset"
    # No tasks created for this session_id
    ctx = EventContext(
        session_id=sid,
        event="Stop",
        prompt="",
        store=ThreadSafeDB(),
    )
    ctx.autorun_active = True
    ctx.autorun_stage = EventContext.STAGE_2_COMPLETED

    result = plugins.app.dispatch(ctx)
    # Stage must remain STAGE_2_COMPLETED — no reset without outstanding tasks
    assert ctx.autorun_stage == EventContext.STAGE_2_COMPLETED
    # The stage-reset message must NOT appear
    assert "THREE-STAGE SYSTEM RESET" not in str(result)


def test_stage_reset_counter_also_reset():
    """Stage reset also resets staleness counter to avoid immediate re-trigger."""
    sid = "test-stage-counter-reset"
    _make_pending_task(sid, task_id="1", subject="outstanding task")

    ctx = EventContext(
        session_id=sid,
        event="Stop",
        prompt="",
        store=ThreadSafeDB(),
    )
    ctx.autorun_active = True
    ctx.autorun_stage = EventContext.STAGE_2_COMPLETED
    ctx.tool_calls_since_task_update = 24

    plugins.app.dispatch(ctx)

    # If stage was reset (tasks were found), counter must also be reset
    if ctx.autorun_stage == EventContext.STAGE_2:
        assert (ctx.tool_calls_since_task_update or 0) == 0, (
            "Counter should be reset to 0 when stage is reset"
        )


# ── Session isolation ─────────────────────────────────────────────────────
# Use timestamp-based unique session IDs to avoid stale shelve state
# from previous test runs contaminating assertions.

def _iso_sid(label: str) -> str:
    """Generate unique session ID for isolation tests."""
    import time
    return f"iso-{int(time.time() * 1000)}-{label}"


def test_staleness_counter_session_isolation():
    """Staleness counter for session A must not bleed into session B.

    Regression guard: EventContext keys are {session_id}:{field}. If keying
    were broken (e.g., global state), session B would see session A's counter.
    """
    shared_store = ThreadSafeDB()  # Single store like production daemon
    sid_a = _iso_sid("counter-A")
    sid_b = _iso_sid("counter-B")

    # Session A: increment counter to 10
    ctx_a = EventContext(
        session_id=sid_a,
        event="PostToolUse",
        prompt="",
        tool_name="Bash",
        store=shared_store,
    )
    ctx_a.autorun_active = True
    ctx_a.task_staleness_enabled = True
    ctx_a.tool_calls_since_task_update = 10

    # Session B: fresh context on same store, different session_id
    ctx_b = EventContext(
        session_id=sid_b,
        event="PostToolUse",
        prompt="",
        tool_name="Bash",
        store=shared_store,
    )
    ctx_b.autorun_active = True
    ctx_b.task_staleness_enabled = True

    # Session B's counter should be 0 (default), not 10 (session A's value)
    assert (ctx_b.tool_calls_since_task_update or 0) == 0, (
        f"Session B counter should be 0, got {ctx_b.tool_calls_since_task_update} "
        f"(leaked from session A)"
    )

    # Dispatch on B — should increment to 1, not 11
    plugins.app.dispatch(ctx_b)
    assert (ctx_b.tool_calls_since_task_update or 0) == 1, (
        f"Session B counter should be 1 after one tool call, "
        f"got {ctx_b.tool_calls_since_task_update}"
    )

    # Session A's counter should still be 10 (untouched by B's dispatch)
    ctx_a2 = EventContext(
        session_id=sid_a,
        event="PostToolUse",
        prompt="",
        tool_name="Bash",
        store=shared_store,
    )
    assert (ctx_a2.tool_calls_since_task_update or 0) == 10, (
        f"Session A counter should still be 10, got {ctx_a2.tool_calls_since_task_update} "
        f"(corrupted by session B)"
    )


def test_staleness_enabled_session_isolation():
    """Disabling staleness in session A must not affect session B.

    Guards against global state leaking the enabled/disabled flag.
    """
    shared_store = ThreadSafeDB()
    sid_a = _iso_sid("enable-A")
    sid_b = _iso_sid("enable-B")

    # Session A: disable staleness
    ctx_a = EventContext(
        session_id=sid_a,
        event="PostToolUse",
        prompt="",
        tool_name="Bash",
        store=shared_store,
    )
    ctx_a.task_staleness_enabled = False

    # Session B: should still be enabled (default=True)
    ctx_b = EventContext(
        session_id=sid_b,
        event="PostToolUse",
        prompt="",
        tool_name="Bash",
        store=shared_store,
    )
    assert ctx_b.task_staleness_enabled is True, (
        f"Session B should have staleness enabled (default), "
        f"got {ctx_b.task_staleness_enabled} (leaked from session A)"
    )


def test_staleness_threshold_session_isolation():
    """Custom threshold in session A must not affect session B.

    Session A sets threshold=5; session B should still see None (use CONFIG default).
    """
    shared_store = ThreadSafeDB()
    sid_a = _iso_sid("thresh-A")
    sid_b = _iso_sid("thresh-B")

    # Session A: custom threshold
    ctx_a = EventContext(
        session_id=sid_a,
        event="PostToolUse",
        prompt="",
        tool_name="Bash",
        store=shared_store,
    )
    ctx_a.task_staleness_threshold = 5

    # Session B: should see None (CONFIG default, not 5)
    ctx_b = EventContext(
        session_id=sid_b,
        event="PostToolUse",
        prompt="",
        tool_name="Bash",
        store=shared_store,
    )
    assert ctx_b.task_staleness_threshold is None, (
        f"Session B threshold should be None (default), "
        f"got {ctx_b.task_staleness_threshold} (leaked from session A)"
    )


# ── E2E: full dispatch pathway tests ─────────────────────────────────────

def _e2e_post_tool(tool_name: str, session_id: str, store: ThreadSafeDB) -> dict:
    """Dispatch a PostToolUse event through the full handler chain.

    Uses a shared store to simulate the daemon's persistent ThreadSafeDB,
    so state set by one dispatch persists to the next (same as production).

    Sets autorun_active=True (for 3-stage pipeline tests).
    Does NOT force task_staleness_enabled — lets store/defaults supply it,
    so /ar:tasks off is respected across dispatches.
    """
    ctx = EventContext(
        session_id=session_id,
        event="PostToolUse",
        prompt="",
        tool_name=tool_name,
        tool_input={},
        tool_result="",
        store=store,
    )
    ctx.autorun_active = True
    return plugins.app.dispatch(ctx) or {}


def _e2e_stop(session_id: str, store: ThreadSafeDB, autorun_stage: int = 0) -> dict:
    """Dispatch a Stop event through the full handler chain."""
    ctx = EventContext(
        session_id=session_id,
        event="Stop",
        prompt="",
        store=store,
    )
    ctx.autorun_active = True
    ctx.autorun_stage = autorun_stage
    result = plugins.app.dispatch(ctx) or {}
    return result, ctx


def _e2e_command(prompt: str, session_id: str, store: ThreadSafeDB) -> dict:
    """Dispatch a UserPromptSubmit (command) through the full handler chain."""
    ctx = EventContext(
        session_id=session_id,
        event="UserPromptSubmit",
        prompt=prompt,
        tool_name="",
        tool_input={},
        store=store,
    )
    return plugins.app.dispatch(ctx) or {}


class TestStalenessE2E:
    """End-to-end tests exercising full dispatch chain with persistent store.

    Uses unique session IDs per test run (timestamp prefix) to avoid stale
    shelve state from previous runs contaminating assertions.
    """

    def setup_method(self):
        import time
        self.store = ThreadSafeDB()  # Shared store like production daemon
        self._prefix = f"e2e-{int(time.time() * 1000)}"

    def _sid(self, label: str) -> str:
        """Generate unique session ID for this test run."""
        return f"{self._prefix}-{label}"

    # ── Counter lifecycle ──────────────────────────────────────────────

    def test_counter_increments_across_dispatches(self):
        """Counter persists and increments across separate dispatch calls."""
        sid = self._sid("counter-persist")
        for i in range(5):
            _e2e_post_tool("Bash", sid, self.store)
        ctx = EventContext(session_id=sid, event="PostToolUse", prompt="",
                           tool_name="Read", store=self.store)
        assert (ctx.tool_calls_since_task_update or 0) == 5

    def test_multiple_injection_cycles(self):
        """Counter resets after injection and can trigger again."""
        sid = self._sid("multi-cycle")
        _make_pending_task(sid, "1", "incomplete task")
        # Set threshold to 3 via command
        _e2e_command("/ar:tasks 3", sid, self.store)

        injections = []
        for i in range(9):
            result = _e2e_post_tool("Bash", sid, self.store)
            if "TASK LIST STALE" in str(result):
                injections.append(i)

        # Should inject at tool calls 3, 6, 9 → indices 2, 5, 8
        assert len(injections) == 3, (
            f"Expected 3 injections at threshold=3 over 9 calls, got {len(injections)} "
            f"at indices {injections}"
        )

    def test_task_create_resets_counter_mid_cycle(self):
        """TaskCreate mid-cycle resets counter; injection doesn't fire early."""
        sid = self._sid("create-mid-cycle")
        _e2e_command("/ar:tasks 5", sid, self.store)

        # 3 tool calls, then TaskCreate, then 4 more — total 7 non-task calls
        # but longest streak is 4, so no injection at threshold=5
        for _ in range(3):
            _e2e_post_tool("Bash", sid, self.store)
        _e2e_post_tool("TaskCreate", sid, self.store)
        results = []
        for _ in range(4):
            results.append(_e2e_post_tool("Bash", sid, self.store))

        assert all("TASK LIST STALE" not in str(r) for r in results), (
            "No injection expected — longest streak is 4, threshold is 5"
        )

    def test_task_update_resets_counter_mid_cycle(self):
        """TaskUpdate resets counter identically to TaskCreate."""
        sid = self._sid("update-mid-cycle")
        _e2e_command("/ar:tasks 5", sid, self.store)

        for _ in range(3):
            _e2e_post_tool("Bash", sid, self.store)
        _e2e_post_tool("TaskUpdate", sid, self.store)
        results = []
        for _ in range(4):
            results.append(_e2e_post_tool("Bash", sid, self.store))

        assert all("TASK LIST STALE" not in str(r) for r in results)

    def test_task_list_does_not_reset_counter(self):
        """TaskList should NOT reset the counter (only Create/Update do)."""
        sid = self._sid("list-no-reset")
        _make_pending_task(sid, "1", "incomplete task")
        _e2e_command("/ar:tasks 3", sid, self.store)

        _e2e_post_tool("Bash", sid, self.store)      # count=1
        _e2e_post_tool("TaskList", sid, self.store)   # count=2 (NOT reset)
        result = _e2e_post_tool("Bash", sid, self.store)  # count=3 → inject

        assert "TASK LIST STALE" in str(result), (
            "TaskList should not reset the counter — injection expected at count=3"
        )

    def test_task_get_does_not_reset_counter(self):
        """TaskGet should NOT reset the counter."""
        sid = self._sid("get-no-reset")
        _make_pending_task(sid, "1", "incomplete task")
        _e2e_command("/ar:tasks 3", sid, self.store)

        _e2e_post_tool("Bash", sid, self.store)      # count=1
        _e2e_post_tool("TaskGet", sid, self.store)    # count=2 (NOT reset)
        result = _e2e_post_tool("Bash", sid, self.store)  # count=3 → inject

        assert "TASK LIST STALE" in str(result), (
            "TaskGet should not reset the counter — injection expected at count=3"
        )

    def test_lowercase_task_create_resets_counter(self):
        """Gemini-style tool name 'task_create' should also reset counter."""
        sid = self._sid("lowercase-create")
        _e2e_command("/ar:tasks 3", sid, self.store)

        _e2e_post_tool("Bash", sid, self.store)        # count=1
        _e2e_post_tool("Bash", sid, self.store)        # count=2
        _e2e_post_tool("task_create", sid, self.store)  # reset
        result = _e2e_post_tool("Bash", sid, self.store)  # count=1

        assert "TASK LIST STALE" not in str(result), (
            "task_create should reset counter — no injection expected at count=1"
        )

    def test_lowercase_task_update_resets_counter(self):
        """Gemini-style 'task_update' should also reset counter."""
        sid = self._sid("lowercase-update")
        _e2e_command("/ar:tasks 3", sid, self.store)

        _e2e_post_tool("Bash", sid, self.store)
        _e2e_post_tool("Bash", sid, self.store)
        _e2e_post_tool("task_update", sid, self.store)  # reset
        result = _e2e_post_tool("Bash", sid, self.store)

        assert "TASK LIST STALE" not in str(result)

    # ── /ar:tasks command → handler interaction ────────────────────────

    def test_command_off_then_handler_silent(self):
        """/ar:tasks off → PostToolUse handler does not inject even over threshold."""
        sid = self._sid("off-silent")
        _e2e_command("/ar:tasks 3", sid, self.store)
        _e2e_command("/ar:tasks off", sid, self.store)

        results = []
        for _ in range(10):
            results.append(_e2e_post_tool("Bash", sid, self.store))

        assert all("TASK LIST STALE" not in str(r) for r in results), (
            "No injection expected when staleness is disabled"
        )

    def test_command_on_after_off_resumes(self):
        """/ar:tasks on after off → handler resumes injection."""
        sid = self._sid("on-after-off")
        _make_pending_task(sid, "1", "incomplete task")
        _e2e_command("/ar:tasks 3", sid, self.store)
        _e2e_command("/ar:tasks off", sid, self.store)

        # 5 silent calls
        for _ in range(5):
            _e2e_post_tool("Bash", sid, self.store)

        # Re-enable
        _e2e_command("/ar:tasks on", sid, self.store)

        # Counter was reset by /ar:tasks on; 3 more calls should trigger
        results = []
        for _ in range(3):
            results.append(_e2e_post_tool("Bash", sid, self.store))

        assert "TASK LIST STALE" in str(results[-1]), (
            "Injection expected after re-enabling at call #3"
        )

    def test_command_threshold_change_takes_effect(self):
        """/ar:tasks <n> changes threshold; handler uses new value."""
        sid = self._sid("thresh-change")
        _make_pending_task(sid, "1", "incomplete task")
        _e2e_command("/ar:tasks 10", sid, self.store)

        # 5 calls — should NOT inject at threshold=10
        results = []
        for _ in range(5):
            results.append(_e2e_post_tool("Bash", sid, self.store))
        assert all("TASK LIST STALE" not in str(r) for r in results)

        # Lower threshold to 2 (counter was reset by the command)
        _e2e_command("/ar:tasks 2", sid, self.store)

        # 2 more calls should trigger
        _e2e_post_tool("Bash", sid, self.store)
        result = _e2e_post_tool("Bash", sid, self.store)
        assert "TASK LIST STALE" in str(result)

    def test_command_invalid_string_rejected(self):
        """/ar:tasks abc returns error message."""
        result = _e2e_command("/ar:tasks abc", self._sid("invalid-str"), self.store)
        text = str(result).lower()
        assert "invalid" in text

    def test_command_float_rejected(self):
        """/ar:tasks 2.5 is rejected (not a valid integer)."""
        result = _e2e_command("/ar:tasks 2.5", self._sid("float"), self.store)
        text = str(result).lower()
        assert "invalid" in text

    def test_command_status_shows_count(self):
        """/ar:tasks (no args) shows current counter value."""
        sid = self._sid("status-count")
        # Increment counter by dispatching some tool calls
        for _ in range(7):
            _e2e_post_tool("Bash", sid, self.store)

        result = _e2e_command("/ar:tasks", sid, self.store)
        text = str(result)
        # Should show 7/25 (or similar count/threshold)
        assert "7" in text, f"Status should show count=7, got: {text}"

    # ── Emergency stop / autorun_active=False ──────────────────────────

    def test_no_tasks_no_injection_e2e(self):
        """When no incomplete tasks exist, handler is silent even over threshold."""
        sid = self._sid("no-tasks-silent")
        store = self.store

        _e2e_command("/ar:tasks 2", sid, store)

        results = []
        for _ in range(5):
            results.append(_e2e_post_tool("Bash", sid, store))

        assert all("TASK LIST STALE" not in str(r) for r in results)

    def test_fires_without_autorun_e2e(self):
        """Reminder fires when autorun_active=False but incomplete tasks exist."""
        sid = self._sid("no-autorun-with-tasks")
        store = self.store
        _make_pending_task(sid, "1", "incomplete task")

        _e2e_command("/ar:tasks 2", sid, store)

        # Dispatch with autorun_active=False — injection at 2nd call (threshold=2)
        results = []
        for _ in range(2):
            ctx = EventContext(session_id=sid, event="PostToolUse", prompt="",
                               tool_name="Bash", tool_input={}, tool_result="",
                               store=store)
            ctx.autorun_active = False
            result = plugins.app.dispatch(ctx) or {}
            results.append(result)

        assert any("TASK LIST STALE" in str(r) for r in results), (
            "Reminder should fire with incomplete tasks even when autorun_active=False"
        )

    # ── Stage reset in Stop handler ────────────────────────────────────

    def test_stage_reset_full_workflow(self):
        """Full e2e: tools → threshold → inject → stop with tasks → stage reset."""
        sid = self._sid("full-workflow")
        store = self.store
        _make_pending_task(sid, task_id="1", subject="e2e outstanding task")
        _e2e_command("/ar:tasks 3", sid, store)

        # Phase 1: 3 tool calls → injection (task exists so reminder fires)
        for _ in range(2):
            _e2e_post_tool("Bash", sid, store)
        result = _e2e_post_tool("Bash", sid, store)
        assert "TASK LIST STALE" in str(result)

        # Phase 2: TaskUpdate resets counter
        _e2e_post_tool("TaskUpdate", sid, store)
        ctx_check = EventContext(session_id=sid, event="PostToolUse",
                                  prompt="", tool_name="Bash", store=store)
        assert (ctx_check.tool_calls_since_task_update or 0) == 0

        # Phase 3: Outstanding task exists, attempt stop at STAGE_2_COMPLETED
        result, ctx = _e2e_stop(sid, store, autorun_stage=EventContext.STAGE_2_COMPLETED)

        assert ctx.autorun_stage == EventContext.STAGE_2, (
            f"Stage should be reset to STAGE_2, got {ctx.autorun_stage}"
        )
        assert "THREE-STAGE SYSTEM RESET" in str(result)
        assert (ctx.tool_calls_since_task_update or 0) == 0

    def test_stop_stage3_not_reset(self):
        """STAGE_3 with outstanding tasks does NOT trigger stage reset.

        Only STAGE_2_COMPLETED triggers the reset — STAGE_3 is a terminal stage.
        """
        sid = self._sid("stage3-no-reset")
        _make_pending_task(sid, task_id="1", subject="stage3 task")
        result, ctx = _e2e_stop(sid, self.store, autorun_stage=EventContext.STAGE_3)

        # Stop is still blocked (tasks outstanding), but no THREE-STAGE RESET
        assert "THREE-STAGE SYSTEM RESET" not in str(result)
        assert ctx.autorun_stage == EventContext.STAGE_3

    def test_stop_stage1_not_reset(self):
        """STAGE_1 with outstanding tasks does NOT trigger stage reset."""
        sid = self._sid("stage1-no-reset")
        _make_pending_task(sid, task_id="1", subject="stage1 task")
        result, ctx = _e2e_stop(sid, self.store, autorun_stage=EventContext.STAGE_1)

        assert "THREE-STAGE SYSTEM RESET" not in str(result)
        assert ctx.autorun_stage == EventContext.STAGE_1

    def test_stop_no_tasks_no_block(self):
        """Stop with no outstanding tasks is allowed (not blocked)."""
        sid = self._sid("stop-clean")
        result, ctx = _e2e_stop(sid, self.store, autorun_stage=EventContext.STAGE_2_COMPLETED)

        # No tasks → prevent_premature_stop returns None → autorun_injection runs
        # The key check: "CANNOT STOP" should NOT appear
        assert "CANNOT STOP" not in str(result)

    def test_stop_with_completed_tasks_not_blocked(self):
        """Stop allowed when all tasks are completed (not outstanding)."""
        sid = self._sid("stop-completed")
        _make_pending_task(sid, task_id="1", subject="completed task")
        # Mark it completed
        from autorun.task_lifecycle import TaskLifecycle as _TL
        mgr = _TL(session_id=sid)
        mgr.update_task("1", {"status": "completed"}, "Completed")

        result, ctx = _e2e_stop(sid, self.store, autorun_stage=EventContext.STAGE_2_COMPLETED)
        assert "CANNOT STOP" not in str(result)
        assert "THREE-STAGE SYSTEM RESET" not in str(result)

    # ── Resume / session lifecycle ─────────────────────────────────────

    def test_counter_persists_across_resume(self):
        """Counter survives session resume (same session_id, new EventContext).

        Simulates: AI runs 15 tool calls → session closed → claude --resume
        → new EventContext with same session_id → counter should be 15 (from shelve).
        """
        sid = self._sid("resume")

        # Phase 1: active session, 15 tool calls
        for _ in range(15):
            _e2e_post_tool("Bash", sid, self.store)

        # Phase 2: simulate resume — new ThreadSafeDB (daemon restart), same sid
        resumed_store = ThreadSafeDB()
        ctx = EventContext(
            session_id=sid, event="PostToolUse", prompt="",
            tool_name="Bash", store=resumed_store,
        )
        assert (ctx.tool_calls_since_task_update or 0) == 15, (
            f"Counter should persist across resume, expected 15, "
            f"got {ctx.tool_calls_since_task_update}"
        )

    def test_enabled_flag_persists_across_resume(self):
        """/ar:tasks off persists across session resume."""
        sid = self._sid("resume-off")

        # Disable in original session
        _e2e_command("/ar:tasks off", sid, self.store)

        # Resume: new store, same sid
        resumed_store = ThreadSafeDB()
        ctx = EventContext(
            session_id=sid, event="PostToolUse", prompt="",
            tool_name="Bash", store=resumed_store,
        )
        assert ctx.task_staleness_enabled is False, (
            "task_staleness_enabled=False should persist across resume"
        )

    def test_threshold_persists_across_resume(self):
        """Custom threshold persists across session resume."""
        sid = self._sid("resume-thresh")

        _e2e_command("/ar:tasks 7", sid, self.store)

        resumed_store = ThreadSafeDB()
        ctx = EventContext(
            session_id=sid, event="PostToolUse", prompt="",
            tool_name="Bash", store=resumed_store,
        )
        assert ctx.task_staleness_threshold == 7, (
            f"Threshold should persist across resume, expected 7, "
            f"got {ctx.task_staleness_threshold}"
        )

    def test_new_session_starts_clean(self):
        """New session (different session_id) starts with default counter=0.

        Simulates: session A ran with counter=20 → user starts fresh session B
        → B should have counter=0, enabled=True, threshold=None.
        """
        sid_old = self._sid("old-session")
        sid_new = self._sid("new-session")

        # Old session: counter at 20, disabled, threshold=3
        _e2e_command("/ar:tasks 3", sid_old, self.store)
        _e2e_command("/ar:tasks off", sid_old, self.store)
        for _ in range(20):
            ctx = EventContext(
                session_id=sid_old, event="PostToolUse", prompt="",
                tool_name="Bash", store=self.store,
            )
            ctx.tool_calls_since_task_update = (ctx.tool_calls_since_task_update or 0) + 1

        # New session: everything should be default
        ctx_new = EventContext(
            session_id=sid_new, event="PostToolUse", prompt="",
            tool_name="Bash", store=self.store,
        )
        assert (ctx_new.tool_calls_since_task_update or 0) == 0, "New session counter should be 0"
        assert ctx_new.task_staleness_enabled is True, "New session should have staleness enabled"
        assert ctx_new.task_staleness_threshold is None, "New session threshold should be None"

    def test_no_tasks_no_injection_after_resume(self):
        """Without incomplete tasks, staleness doesn't fire even with high counter.

        Counter may be high from previous work, but if all tasks are done,
        no reminder is needed.
        """
        sid = self._sid("no-tasks-resume")

        # Build up counter
        for _ in range(20):
            _e2e_post_tool("Bash", sid, self.store)

        # Low threshold, but no tasks
        _e2e_command("/ar:tasks 3", sid, self.store)

        results = []
        for _ in range(5):
            results.append(_e2e_post_tool("Bash", sid, self.store))

        assert all("TASK LIST STALE" not in str(r) for r in results), (
            "No injection expected — no incomplete tasks exist"
        )
