"""
Test hooks.json format for Claude Code and Gemini CLI compatibility.

This test suite ensures:
1. Source claude-hooks.json uses Claude Code format
2. hooks.json uses Gemini CLI format
3. Formats are mutually exclusive and correct
4. All required hook events are present
"""

import json
import pytest
from pathlib import Path


def get_plugin_root():
    """Get plugin root directory."""
    return Path(__file__).parent.parent


def test_source_hooks_json_is_claude_format():
    """Test that source hooks.json uses Claude Code format.

    RED: Initially failed because hooks.json had Gemini format
    GREEN: Fixed by restoring Claude format
    REFACTOR: Improved test coverage
    """
    hooks_file = get_plugin_root() / "hooks" / "claude-hooks.json"
    assert hooks_file.exists(), "hooks.json not found"

    with open(hooks_file) as f:
        hooks_data = json.load(f)

    # Check description mentions Claude or daemon
    description = hooks_data.get("description", "")
    assert "daemon" in description.lower() or "claude" in description.lower(), \
        f"Description should mention daemon or Claude, got: {description}"

    # Check uses CLAUDE_PLUGIN_ROOT not extensionPath
    hooks_json_str = json.dumps(hooks_data)
    assert "${CLAUDE_PLUGIN_ROOT}" in hooks_json_str, \
        "Claude claude-hooks.json should use ${CLAUDE_PLUGIN_ROOT}"
    assert "${extensionPath}" not in hooks_json_str, \
        "Claude claude-hooks.json should NOT use ${extensionPath}"


def test_source_hooks_json_has_claude_events():
    """Test that source hooks.json uses Claude Code event names."""
    hooks_file = get_plugin_root() / "hooks" / "claude-hooks.json"

    with open(hooks_file) as f:
        hooks_data = json.load(f)

    hooks_section = hooks_data.get("hooks", {})

    # Claude Code event names
    claude_events = {
        "PreToolUse", "PostToolUse", "UserPromptSubmit",
        "SessionStart", "Stop", "SubagentStop"
    }

    # Gemini CLI event names (should NOT be present)
    gemini_events = {
        "BeforeTool", "AfterTool", "BeforeAgent", "SessionEnd"
    }

    # Check Claude events present
    for event in claude_events:
        if event in {"Stop", "SubagentStop", "SessionStart"}:
            # These are optional
            continue
        assert event in hooks_section, \
            f"Claude event '{event}' should be in hooks.json"

    # Check Gemini events NOT present
    for event in gemini_events:
        assert event not in hooks_section, \
            f"Gemini event '{event}' should NOT be in Claude claude-hooks.json"


def test_source_hooks_json_has_claude_tool_names():
    """Test that source hooks.json uses Claude Code tool names."""
    hooks_file = get_plugin_root() / "hooks" / "claude-hooks.json"

    with open(hooks_file) as f:
        hooks_data = json.load(f)

    hooks_json_str = json.dumps(hooks_data)

    # Claude Code tool names should be present
    claude_tools = ["Write", "Bash", "Edit", "ExitPlanMode", "TaskCreate"]
    found_claude_tools = [tool for tool in claude_tools if tool in hooks_json_str]

    assert len(found_claude_tools) > 0, \
        f"Should find Claude tool names like {claude_tools}"

    # Gemini CLI tool names should NOT be present
    gemini_tools = ["write_file", "run_shell_command", "replace"]
    found_gemini_tools = [tool for tool in gemini_tools if tool in hooks_json_str]

    assert len(found_gemini_tools) == 0, \
        f"Should NOT find Gemini tool names {gemini_tools}, found: {found_gemini_tools}"


def test_gemini_hooks_json_is_gemini_format():
    """Test that hooks.json uses Gemini CLI format."""
    hooks_file = get_plugin_root() / "hooks" / "hooks.json"
    assert hooks_file.exists(), "hooks.json not found"

    with open(hooks_file) as f:
        hooks_data = json.load(f)

    # Check description mentions Gemini
    description = hooks_data.get("description", "")
    assert "gemini" in description.lower(), \
        f"Description should mention Gemini, got: {description}"

    # Check uses ${extensionPath} not CLAUDE_PLUGIN_ROOT
    hooks_json_str = json.dumps(hooks_data)
    assert "${extensionPath}" in hooks_json_str, \
        "Gemini hooks should use ${extensionPath}"
    assert "${CLAUDE_PLUGIN_ROOT}" not in hooks_json_str, \
        "Gemini hooks should NOT use ${CLAUDE_PLUGIN_ROOT}"


def test_gemini_hooks_json_has_gemini_events():
    """Test that hooks.json uses Gemini CLI event names."""
    hooks_file = get_plugin_root() / "hooks" / "hooks.json"

    with open(hooks_file) as f:
        hooks_data = json.load(f)

    hooks_section = hooks_data.get("hooks", {})

    # Gemini CLI event names
    gemini_events = {
        "BeforeTool", "AfterTool", "SessionStart", "SessionEnd"
    }

    # Check at least some Gemini events present
    found_events = [event for event in gemini_events if event in hooks_section]
    assert len(found_events) >= 2, \
        f"Should find Gemini events like {gemini_events}, found: {found_events}"

    # Claude Code specific events should NOT be present
    claude_only_events = {"PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SubagentStop"}

    for event in claude_only_events:
        assert event not in hooks_section, \
            f"Claude-only event '{event}' should NOT be in Gemini hooks"


def test_gemini_hooks_json_has_gemini_tool_names():
    """Test that hooks.json uses Gemini CLI tool names."""
    hooks_file = get_plugin_root() / "hooks" / "hooks.json"

    with open(hooks_file) as f:
        hooks_data = json.load(f)

    hooks_json_str = json.dumps(hooks_data)

    # Gemini CLI tool names should be present
    gemini_tools = ["write_file", "run_shell_command", "replace"]
    found_gemini_tools = [tool for tool in gemini_tools if tool in hooks_json_str]

    assert len(found_gemini_tools) >= 2, \
        f"Should find Gemini tool names like {gemini_tools}, found: {found_gemini_tools}"

    # Claude Code tool names should NOT be present (except common ones)
    claude_only_tools = ["Bash", "Write|Edit", "TaskCreate|TaskUpdate"]

    for tool_pattern in claude_only_tools:
        assert tool_pattern not in hooks_json_str, \
            f"Claude-only tool pattern '{tool_pattern}' should NOT be in Gemini hooks"


def test_gemini_hooks_have_type_field():
    """Test that Gemini hooks include 'type' field (required by Gemini CLI)."""
    hooks_file = get_plugin_root() / "hooks" / "hooks.json"

    with open(hooks_file) as f:
        hooks_data = json.load(f)

    hooks_section = hooks_data.get("hooks", {})

    # Check at least one hook has type: command
    found_type_field = False

    for event_name, event_configs in hooks_section.items():
        for config in event_configs:
            hooks_list = config.get("hooks", [])
            for hook in hooks_list:
                if "type" in hook and hook["type"] == "command":
                    found_type_field = True
                    break

    assert found_type_field, "Gemini hooks should have 'type': 'command' field"


def test_gemini_hooks_have_timeout():
    """Test that Gemini hooks include timeout (recommended for Gemini CLI)."""
    hooks_file = get_plugin_root() / "hooks" / "hooks.json"

    with open(hooks_file) as f:
        hooks_data = json.load(f)

    hooks_section = hooks_data.get("hooks", {})

    # Check at least one hook has timeout
    found_timeout = False

    for event_name, event_configs in hooks_section.items():
        for config in event_configs:
            hooks_list = config.get("hooks", [])
            for hook in hooks_list:
                if "timeout" in hook:
                    found_timeout = True
                    # Gemini uses milliseconds, Claude uses seconds
                    # Gemini timeout should be > 100 (at least 100ms)
                    assert hook["timeout"] >= 100, \
                        f"Gemini timeout should be in milliseconds (>= 100), got: {hook['timeout']}"
                    break

    assert found_timeout, "Gemini hooks should have 'timeout' field"


def test_claude_hooks_timeout_is_seconds():
    """Test that Claude hooks use seconds for timeout (not milliseconds)."""
    hooks_file = get_plugin_root() / "hooks" / "claude-hooks.json"

    with open(hooks_file) as f:
        hooks_data = json.load(f)

    hooks_section = hooks_data.get("hooks", {})

    # Check timeouts if present
    for event_name, event_configs in hooks_section.items():
        if isinstance(event_configs, list):
            for config in event_configs:
                hooks_list = config.get("hooks", [])
                for hook in hooks_list:
                    if "timeout" in hook:
                        # Claude uses seconds, so should be small number (< 100)
                        assert hook["timeout"] < 100, \
                            f"Claude timeout should be in seconds (< 100), got: {hook['timeout']}"


def test_no_environment_variable_assignment_in_gemini_hooks():
    """Test that Gemini hooks don't use environment variable assignment syntax.

    Gemini CLI doesn't support: VAR=value command
    Should use: uv run --project ${extensionPath} python ${extensionPath}/...
    """
    hooks_file = get_plugin_root() / "hooks" / "hooks.json"

    with open(hooks_file) as f:
        hooks_data = json.load(f)

    hooks_json_str = json.dumps(hooks_data)

    # Check for env var assignment pattern (should NOT exist)
    patterns_to_avoid = [
        "AUTORUN_PLUGIN_ROOT=",
        "CLAUDE_PLUGIN_ROOT=",
        "PLUGIN_ROOT="
    ]

    for pattern in patterns_to_avoid:
        assert pattern not in hooks_json_str, \
            f"Gemini hooks should NOT use env var assignment '{pattern}'"


def test_both_hooks_files_are_valid_json():
    """Test that both hooks files are valid JSON."""
    hooks_files = [
        get_plugin_root() / "hooks" / "claude-hooks.json",
        get_plugin_root() / "hooks" / "hooks.json"
    ]

    for hooks_file in hooks_files:
        assert hooks_file.exists(), f"{hooks_file.name} not found"

        with open(hooks_file) as f:
            try:
                data = json.load(f)
                assert isinstance(data, dict), f"{hooks_file.name} should be a JSON object"
                assert "hooks" in data, f"{hooks_file.name} should have 'hooks' key"
            except json.JSONDecodeError as e:
                pytest.fail(f"{hooks_file.name} is not valid JSON: {e}")


def test_plugin_json_references_hooks():
    """Test that plugin.json has 'hooks' field pointing to hooks/hooks.json.

    Without this field, Claude Code will NOT discover or execute hooks.
    This was a critical bug: hooks.json existed but Claude Code never loaded it
    because plugin.json didn't reference it.

    RED: plugin.json was missing "hooks" field entirely
    GREEN: Added "hooks": "./hooks/hooks.json" to plugin.json
    """
    plugin_json = get_plugin_root() / ".claude-plugin" / "plugin.json"
    assert plugin_json.exists(), ".claude-plugin/plugin.json not found"

    with open(plugin_json) as f:
        manifest = json.load(f)

    assert "hooks" in manifest, \
        "plugin.json MUST have 'hooks' field for Claude Code to discover hooks. " \
        "Without it, hooks.json is ignored and PreToolUse blocking doesn't work."

    hooks_path = manifest["hooks"]
    assert "claude-hooks.json" in hooks_path, \
        f"hooks field should reference hooks.json, got: {hooks_path}"

    # Verify the referenced file actually exists
    hooks_file = get_plugin_root() / ".claude-plugin" / Path(hooks_path)
    # Resolve relative to .claude-plugin directory
    if not hooks_file.exists():
        hooks_file = get_plugin_root() / hooks_path.lstrip("./")
    assert hooks_file.exists(), \
        f"Referenced hooks file does not exist: {hooks_file}"


class TestHookTimeouts:
    """Verify hook timeouts are adequate for both CLIs.

    Claude Code timeout unit: seconds (timeout: 10 = 10 seconds).
    Gemini CLI timeout unit: milliseconds (timeout: 5000 = 5 seconds).
    Source: notes/hooks_api_reference.md:825 (Claude), :857 (Gemini).
    """

    def test_claude_hooks_timeout_adequate(self):
        """claude-hooks.json timeouts must be >= 5 seconds (Claude uses seconds)."""
        hooks_path = get_plugin_root() / "hooks" / "claude-hooks.json"
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        for event, handler_groups in hooks["hooks"].items():
            for handler_group in handler_groups:
                for hook in handler_group.get("hooks", []):
                    timeout = hook.get("timeout", 0)
                    assert timeout >= 5, (
                        f"{event} hook timeout {timeout}s too short "
                        f"(need >= 5s for daemon startup warmup)"
                    )

    def test_gemini_hooks_timeout_adequate(self):
        """hooks.json timeouts must be >= 5000ms (Gemini uses milliseconds)."""
        hooks_path = get_plugin_root() / "hooks" / "hooks.json"
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        for event, handler_groups in hooks["hooks"].items():
            for handler_group in handler_groups:
                for hook in handler_group.get("hooks", []):
                    timeout = hook.get("timeout", 0)
                    assert timeout >= 5000, (
                        f"Gemini {event} hook timeout {timeout}ms too short "
                        f"(need >= 5000ms = 5 seconds)"
                    )


class TestGeminiHookMatchers:
    """Verify Gemini hooks.json matchers include all required tool names.

    Gemini uses different tool names than Claude Code. Missing a tool name
    in a matcher means the hook never fires for that tool.
    """

    def test_gemini_before_tool_matcher_includes_exit_plan_mode(self):
        """Gemini BeforeTool matcher must include exit_plan_mode.

        Without this, track_and_export_plans_early() (PreToolUse backup)
        never fires for Gemini ExitPlanMode. Only the AfterTool path works.
        Fixed: added |exit_plan_mode to hooks/hooks.json BeforeTool matcher.
        """
        hooks_path = get_plugin_root() / "hooks" / "hooks.json"
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        before_tool_groups = hooks["hooks"]["BeforeTool"]
        assert len(before_tool_groups) > 0, "No BeforeTool hooks registered"
        matcher = before_tool_groups[0]["matcher"]
        assert "exit_plan_mode" in matcher, (
            f"Gemini BeforeTool matcher missing exit_plan_mode. "
            f"Current matcher: {matcher}"
        )

    def test_claude_hooks_exit_plan_mode_in_pre_tool_use(self):
        """Claude PreToolUse matcher must include ExitPlanMode for backup export.

        Structure: hooks["hooks"]["PreToolUse"] is a list of handler_groups.
        Each handler_group has "matcher" at the top level (not inside "hooks" items).
        """
        hooks_path = get_plugin_root() / "hooks" / "claude-hooks.json"
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        pre_tool_groups = hooks["hooks"]["PreToolUse"]
        matchers = [g.get("matcher", "") for g in pre_tool_groups]
        assert any("ExitPlanMode" in m for m in matchers), (
            f"Claude PreToolUse must match ExitPlanMode. Matchers: {matchers}"
        )

    def test_claude_hooks_exit_plan_mode_in_post_tool_use(self):
        """Claude PostToolUse matcher must include ExitPlanMode for primary export.

        Structure: hooks["hooks"]["PostToolUse"] is a list of handler_groups.
        Each handler_group has "matcher" at the top level (not inside "hooks" items).
        """
        hooks_path = get_plugin_root() / "hooks" / "claude-hooks.json"
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        post_tool_groups = hooks["hooks"]["PostToolUse"]
        matchers = [g.get("matcher", "") for g in post_tool_groups]
        assert any("ExitPlanMode" in m for m in matchers), (
            f"Claude PostToolUse must match ExitPlanMode. Matchers: {matchers}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
