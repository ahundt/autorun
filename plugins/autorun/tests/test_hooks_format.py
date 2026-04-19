"""
Test hooks.json format for Claude Code and Gemini CLI compatibility.

Post-split layout (see notes/2026_04_19_1627_fix_arautorun_failed_to_load...):
- plugins/autorun/hooks/hooks.json       → Claude Code events (default path)
- plugins/ar-gemini/hooks/hooks.json      → Gemini CLI events (separate root)

This test suite ensures:
1. Source Claude hooks.json uses Claude Code format
2. Gemini hooks.json uses Gemini CLI format
3. Formats are mutually exclusive and correct
4. All required hook events are present
5. No cross-CLI event contamination (prevents hook loading failures)
6. Claude Code plugin cache contains only Claude-compatible hooks files
"""

import json
import tempfile
import pytest
from pathlib import Path


def get_plugin_root():
    """Get Claude plugin root directory."""
    return Path(__file__).parent.parent


def get_gemini_template_root():
    """Gemini extension template dir (under src/ so Claude doesn't scan it).

    The installer materializes ~/.gemini/extensions/ar/ from this template.
    """
    return get_plugin_root() / "src" / "autorun" / "gemini_template"


def get_claude_hooks_path():
    """Path to Claude Code hooks file (default hooks/hooks.json location)."""
    return get_plugin_root() / "hooks" / "hooks.json"


def get_gemini_hooks_path():
    """Path to Gemini CLI hooks template (mirrors extension layout)."""
    return get_gemini_template_root() / "hooks" / "hooks.json"


def test_source_hooks_json_is_claude_format():
    """Test that source hooks.json uses Claude Code format.

    RED: Initially failed because hooks.json had Gemini format
    GREEN: Fixed by restoring Claude format
    REFACTOR: Improved test coverage
    """
    hooks_file = get_claude_hooks_path()
    assert hooks_file.exists(), "hooks.json not found"

    with open(hooks_file, encoding="utf-8") as f:
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
    hooks_file = get_claude_hooks_path()

    with open(hooks_file, encoding="utf-8") as f:
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
    """Test that source claude-hooks.json uses Claude Code conventions.

    With catch-all matchers (no matcher field), tool names may not appear
    in the JSON itself. Instead verify: no Gemini tool names present,
    and uses ${CLAUDE_PLUGIN_ROOT} (not ${extensionPath}).
    """
    hooks_file = get_claude_hooks_path()

    with open(hooks_file, encoding="utf-8") as f:
        hooks_data = json.load(f)

    hooks_json_str = json.dumps(hooks_data)

    # Any matchers that DO exist should use Claude tool names, not Gemini
    gemini_tools = ["write_file", "run_shell_command", "replace"]
    found_gemini_tools = [tool for tool in gemini_tools if tool in hooks_json_str]

    assert len(found_gemini_tools) == 0, \
        f"Should NOT find Gemini tool names {gemini_tools}, found: {found_gemini_tools}"

    # Must use Claude Code variable, not Gemini
    assert "${CLAUDE_PLUGIN_ROOT}" in hooks_json_str, \
        "Claude hooks must use ${CLAUDE_PLUGIN_ROOT}"
    assert "${extensionPath}" not in hooks_json_str, \
        "Claude hooks must NOT use Gemini's ${extensionPath}"


def test_gemini_hooks_json_is_gemini_format():
    """Test that hooks.json uses Gemini CLI format."""
    hooks_file = get_gemini_hooks_path()
    assert hooks_file.exists(), "hooks.json not found"

    with open(hooks_file, encoding="utf-8") as f:
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
    hooks_file = get_gemini_hooks_path()

    with open(hooks_file, encoding="utf-8") as f:
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
    """Test that hooks.json uses Gemini CLI conventions.

    With catch-all matchers (no matcher field), tool names may not appear
    in the JSON itself. Instead verify: no Claude-only tool names present,
    and uses ${extensionPath} (not ${CLAUDE_PLUGIN_ROOT}).
    """
    hooks_file = get_gemini_hooks_path()

    with open(hooks_file, encoding="utf-8") as f:
        hooks_data = json.load(f)

    hooks_json_str = json.dumps(hooks_data)

    # Any matchers that DO exist should use Gemini tool names, not Claude
    claude_only_tools = ["Bash", "Write|Edit", "TaskCreate|TaskUpdate"]
    for tool_pattern in claude_only_tools:
        assert tool_pattern not in hooks_json_str, \
            f"Claude-only tool pattern '{tool_pattern}' should NOT be in Gemini hooks"

    # Must use Gemini variable, not Claude
    assert "${extensionPath}" in hooks_json_str, \
        "Gemini hooks must use ${extensionPath}"
    assert "${CLAUDE_PLUGIN_ROOT}" not in hooks_json_str, \
        "Gemini hooks must NOT use Claude's ${CLAUDE_PLUGIN_ROOT}"


def test_gemini_hooks_have_type_field():
    """Test that Gemini hooks include 'type' field (required by Gemini CLI)."""
    hooks_file = get_gemini_hooks_path()

    with open(hooks_file, encoding="utf-8") as f:
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
    hooks_file = get_gemini_hooks_path()

    with open(hooks_file, encoding="utf-8") as f:
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
    hooks_file = get_claude_hooks_path()

    with open(hooks_file, encoding="utf-8") as f:
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
    hooks_file = get_gemini_hooks_path()

    with open(hooks_file, encoding="utf-8") as f:
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
        get_claude_hooks_path(),
        get_gemini_hooks_path()
    ]

    for hooks_file in hooks_files:
        assert hooks_file.exists(), f"{hooks_file.name} not found"

        with open(hooks_file, encoding="utf-8") as f:
            try:
                data = json.load(f)
                assert isinstance(data, dict), f"{hooks_file.name} should be a JSON object"
                assert "hooks" in data, f"{hooks_file.name} should have 'hooks' key"
            except json.JSONDecodeError as e:
                pytest.fail(f"{hooks_file.name} is not valid JSON: {e}")


def test_plugin_json_uses_default_hooks_path():
    """plugin.json must NOT declare an explicit 'hooks' field.

    Post-split layout: Claude Code auto-discovers hooks/hooks.json by default.
    Adding an explicit "hooks": "./hooks/claude-hooks.json" override fights
    the default and previously created dual-loading confusion. Remove the
    field entirely and let the default discovery path win.
    """
    plugin_json = get_plugin_root() / ".claude-plugin" / "plugin.json"
    assert plugin_json.exists(), ".claude-plugin/plugin.json not found"

    with open(plugin_json, encoding="utf-8") as f:
        manifest = json.load(f)

    assert "hooks" not in manifest, (
        f"plugin.json should NOT declare a 'hooks' field; Claude Code uses the "
        f"default hooks/hooks.json path. Current value: {manifest.get('hooks')!r}"
    )

    # The default-path file must exist.
    default_hooks = get_plugin_root() / "hooks" / "hooks.json"
    assert default_hooks.exists(), (
        f"Claude Code default hooks file missing: {default_hooks}. "
        "Without it, plugin hooks never fire."
    )


class TestHookTimeouts:
    """Verify hook timeouts are adequate for both CLIs.

    Claude Code timeout unit: seconds (timeout: 10 = 10 seconds).
    Gemini CLI timeout unit: milliseconds (timeout: 5000 = 5 seconds).
    Source: notes/hooks_api_reference.md:825 (Claude), :857 (Gemini).
    """

    def test_claude_hooks_timeout_adequate(self):
        """claude-hooks.json timeouts must be >= 5 seconds (Claude uses seconds)."""
        hooks_path = get_claude_hooks_path()
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
        hooks_path = get_gemini_hooks_path()
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

    def test_gemini_before_tool_covers_exit_plan_mode(self):
        """Gemini BeforeTool must fire for exit_plan_mode (needed by backup export).

        With catch-all (no matcher), all tools including exit_plan_mode are covered.
        If a selective matcher is used, exit_plan_mode must be included.
        """
        hooks_path = get_gemini_hooks_path()
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        before_tool_groups = hooks["hooks"]["BeforeTool"]
        assert len(before_tool_groups) > 0, "No BeforeTool hooks registered"
        covers_exit = any(
            "matcher" not in g or "exit_plan_mode" in g.get("matcher", "")
            for g in before_tool_groups
        )
        assert covers_exit, (
            f"Gemini BeforeTool must cover exit_plan_mode (catch-all or in matcher). "
            f"Groups: {before_tool_groups}"
        )

    def test_claude_hooks_pre_tool_use_covers_exit_plan_mode(self):
        """Claude PreToolUse must fire for ExitPlanMode (needed by backup export).

        With catch-all (no matcher), all tools including ExitPlanMode are covered.
        If a selective matcher is used, ExitPlanMode must be included.
        """
        hooks_path = get_claude_hooks_path()
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        pre_tool_groups = hooks["hooks"]["PreToolUse"]
        # Either catch-all (no matcher) or explicit ExitPlanMode in matcher
        covers_exit = any(
            "matcher" not in g or "ExitPlanMode" in g.get("matcher", "")
            for g in pre_tool_groups
        )
        assert covers_exit, (
            f"Claude PreToolUse must cover ExitPlanMode (catch-all or in matcher). "
            f"Groups: {pre_tool_groups}"
        )

    def test_claude_hooks_post_tool_use_catches_all_tools(self):
        """Claude PostToolUse must fire for ALL tools (no matcher = catch-all).

        Required by: check_task_staleness (counts all tool calls),
        detect_plan_approval (ExitPlanMode), task lifecycle tracking.
        Previously had per-tool matchers that missed Read/Grep/Glob/Agent,
        causing staleness counter to never reach threshold.
        """
        hooks_path = get_claude_hooks_path()
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        post_tool_groups = hooks["hooks"]["PostToolUse"]
        # At least one group must have no matcher (catch-all)
        has_catch_all = any("matcher" not in g for g in post_tool_groups)
        assert has_catch_all, (
            f"Claude PostToolUse must have a catch-all group (no matcher). "
            f"Groups: {post_tool_groups}"
        )

    def test_gemini_hooks_after_tool_catches_all_tools(self):
        """Gemini AfterTool must fire for ALL tools (no matcher = catch-all).

        Same requirement as Claude PostToolUse: deliver_pending_stop_injection,
        check_task_staleness, and remind_until_tasks_created need ALL tools.
        Previously had selective matchers that excluded run_shell_command,
        glob, grep_search, task tools — silently breaking 3 handlers.
        """
        hooks_path = get_gemini_hooks_path()
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        after_tool_groups = hooks["hooks"]["AfterTool"]
        has_catch_all = any("matcher" not in g for g in after_tool_groups)
        assert has_catch_all, (
            f"Gemini AfterTool must have a catch-all group (no matcher). "
            f"Groups: {after_tool_groups}"
        )

    def test_gemini_before_tool_catches_all_tools(self):
        """Gemini BeforeTool must fire for ALL tools (no matcher = catch-all).

        WOLOG: Consistent with AfterTool/PostToolUse catch-all pattern.
        All PreToolUse handlers self-filter by tool name, so no incorrect
        processing occurs. Catch-all future-proofs new handlers.
        """
        hooks_path = get_gemini_hooks_path()
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        before_tool_groups = hooks["hooks"]["BeforeTool"]
        has_catch_all = any("matcher" not in g for g in before_tool_groups)
        assert has_catch_all, (
            f"Gemini BeforeTool must have a catch-all group (no matcher). "
            f"Groups: {before_tool_groups}"
        )

    def test_claude_pretooluse_is_catch_all(self):
        """Claude PreToolUse must be catch-all for 100% enforcement coverage.

        enforce_stop_injection and enforce_task_staleness must fire for ALL tools.
        Catch-all is safe with RTK: when no enforcement is active, autorun returns
        None → exits silently → RTK's updatedInput applies normally. When enforcement
        IS active (deny), tool is blocked → RTK rewrite irrelevant.
        """
        hooks_path = get_claude_hooks_path()
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        pre_tool_groups = hooks["hooks"]["PreToolUse"]
        has_catch_all = any("matcher" not in g for g in pre_tool_groups)
        assert has_catch_all, (
            f"Claude PreToolUse must be catch-all (no matcher) for 100% enforcement. "
            f"Groups: {pre_tool_groups}"
        )

    def test_claude_pretooluse_covers_required_tools(self):
        """Claude PreToolUse must be catch-all, covering ALL tools including these required ones.

        Required tools per handler:
        - enforce_file_policy: Write
        - gate_exit_plan_mode: ExitPlanMode
        - check_blocked_commands: Bash, Write, Edit
        - track_and_export_plans_early: Write, Edit, ExitPlanMode
        - enforce_stop_injection: ALL tools (catch-all required)
        - enforce_task_staleness: ALL tools (catch-all required)

        Catch-all (no matcher field) covers all of these by definition.
        If someone adds a matcher back, this test fails for every tool not listed.
        Fix: remove the "matcher" field from PreToolUse in claude-hooks.json.
        """
        hooks_path = get_claude_hooks_path()
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        pre_tool_groups = hooks["hooks"]["PreToolUse"]

        # Must be catch-all (no matcher) — enforce_stop_injection and
        # enforce_task_staleness need to fire for ALL tools without exception.
        catch_all_group = None
        for group in pre_tool_groups:
            if "matcher" not in group:
                catch_all_group = group
                break
        assert catch_all_group is not None, (
            "claude-hooks.json PreToolUse must have a catch-all group (no 'matcher' field). "
            "enforce_stop_injection and enforce_task_staleness require 100% tool coverage. "
            "Fix: remove the 'matcher' field from the PreToolUse entry in "
            "plugins/autorun/hooks/claude-hooks.json. "
            f"Current groups: {pre_tool_groups}"
        )
        assert "hooks" in catch_all_group and len(catch_all_group["hooks"]) > 0, (
            "Catch-all PreToolUse group must have at least one hook command. "
            f"Group: {catch_all_group}"
        )

        # Verify catch-all covers every required tool (by definition it does,
        # but if someone replaces catch-all with a matcher, each tool must be listed)
        required_tools = [
            "Write", "Edit", "Bash", "ExitPlanMode",
            "Read", "Grep", "Glob", "Agent",
            "WebFetch", "WebSearch", "NotebookEdit", "LSP",
            "AskUserQuestion", "Skill", "TaskCreate", "TaskUpdate", "TaskList",
        ]
        matchers = "|".join(g.get("matcher", "") for g in pre_tool_groups)
        has_explicit_matcher = any("matcher" in g for g in pre_tool_groups)
        if has_explicit_matcher:
            for tool in required_tools:
                assert tool in matchers, (
                    f"PreToolUse explicit matcher missing '{tool}'. "
                    f"enforce_stop_injection needs ALL tools — use catch-all instead. "
                    f"Fix: remove 'matcher' from PreToolUse in claude-hooks.json. "
                    f"Current matchers: {matchers}"
                )


# --- Canonical valid event sets ---
# Source: docs/claude-code-hooks-api.md, docs/gemini-cli-hooks-api.md,
#         https://code.claude.com/docs/en/plugins-reference
CLAUDE_CODE_VALID_EVENTS = {
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "Notification",
    "UserPromptSubmit", "SessionStart", "SessionEnd", "Stop",
    "SubagentStart", "SubagentStop", "PreCompact", "PermissionRequest",
    "Setup", "TeammateIdle", "TaskCompleted", "Elicitation",
    "ElicitationResult", "ConfigChange", "WorktreeCreate",
    "WorktreeRemove", "InstructionsLoaded",
}

GEMINI_CLI_VALID_EVENTS = {
    "BeforeTool", "AfterTool", "BeforeAgent", "AfterAgent",
    "BeforeModel", "AfterModel", "BeforeToolSelection",
    "SessionStart", "SessionEnd", "Notification", "PreCompress",
}


class TestCrossCliEventValidation:
    """Validate hooks files contain only valid event names for their target CLI.

    Bug: Claude Code scans entire hooks/ dir and rejects ALL hooks if any
    .json file has unknown events. Having Gemini's hooks.json with BeforeAgent
    in the Claude Code cache silently disables all plugin hooks.
    """

    def test_claude_hooks_only_valid_claude_events(self):
        """claude-hooks.json must only use Claude Code event names."""
        hooks_file = get_claude_hooks_path()
        with open(hooks_file, encoding="utf-8") as f:
            data = json.load(f)
        for event in data.get("hooks", {}):
            assert event in CLAUDE_CODE_VALID_EVENTS, (
                f"'{event}' in claude-hooks.json is not a valid Claude Code event. "
                f"Valid: {sorted(CLAUDE_CODE_VALID_EVENTS)}"
            )

    def test_gemini_hooks_only_valid_gemini_events(self):
        """hooks.json must only use Gemini CLI event names."""
        hooks_file = get_gemini_hooks_path()
        with open(hooks_file, encoding="utf-8") as f:
            data = json.load(f)
        for event in data.get("hooks", {}):
            assert event in GEMINI_CLI_VALID_EVENTS, (
                f"'{event}' in hooks.json is not a valid Gemini CLI event. "
                f"Valid: {sorted(GEMINI_CLI_VALID_EVENTS)}"
            )

    def test_no_cross_cli_event_contamination(self):
        """Neither hooks file should contain events exclusive to the other CLI."""
        claude_only = CLAUDE_CODE_VALID_EVENTS - GEMINI_CLI_VALID_EVENTS
        gemini_only = GEMINI_CLI_VALID_EVENTS - CLAUDE_CODE_VALID_EVENTS

        claude_file = get_claude_hooks_path()
        with open(claude_file, encoding="utf-8") as f:
            claude_events = set(json.load(f).get("hooks", {}).keys())
        contamination = claude_events & gemini_only
        assert not contamination, f"Claude hooks contains Gemini-only events: {contamination}"

        gemini_file = get_gemini_hooks_path()
        with open(gemini_file, encoding="utf-8") as f:
            gemini_events = set(json.load(f).get("hooks", {}).keys())
        contamination = gemini_events & claude_only
        assert not contamination, f"Gemini hooks contains Claude-only events: {contamination}"


class TestCacheCleanup:
    """Test that Claude Code plugin cache contains valid Claude hooks.

    Post-split layout: the cache holds only plugins/autorun/ contents, which
    after the rename means hooks/hooks.json with Claude events. No Gemini
    hook files should ever appear in the Claude cache.
    """

    def test_claude_cache_only_has_claude_hooks(self):
        """Cache must hold hooks/hooks.json with Claude events, no Gemini files."""
        cache_hooks_dir = Path.home() / ".claude" / "plugins" / "cache" / "autorun" / "ar"
        if not cache_hooks_dir.exists():
            pytest.skip("Plugin cache not installed")
        version_dirs = [d for d in cache_hooks_dir.iterdir() if d.is_dir()]
        if not version_dirs:
            pytest.skip("No version directories in cache")
        # Semver-aware sort: split on '.' and compare as integers.
        def _semver_key(p: Path):
            try:
                return tuple(int(x) for x in p.name.split("."))
            except ValueError:
                return (0,)
        latest = max(version_dirs, key=_semver_key)
        hooks_json = latest / "hooks" / "hooks.json"
        assert hooks_json.exists(), (
            f"Claude cache missing hooks/hooks.json at {hooks_json}. "
            "Plugin hooks will not load without it."
        )
        # No leftover claude-hooks.json or any file with Gemini events.
        legacy = latest / "hooks" / "claude-hooks.json"
        assert not legacy.exists(), (
            f"Stale claude-hooks.json at {legacy}; should be renamed to hooks.json."
        )
        data = json.loads(hooks_json.read_text(encoding="utf-8"))
        events = set(data.get("hooks", {}).keys())
        gemini_only = GEMINI_CLI_VALID_EVENTS - CLAUDE_CODE_VALID_EVENTS
        contamination = events & gemini_only
        assert not contamination, (
            f"Claude cache hooks.json contains Gemini events: {contamination}. "
            "This is the root cause of bug #24115 invalid_key failures."
        )


class TestSemverSortInCacheTest:
    """Verify the semver sort fix in test_claude_cache_has_no_gemini_hooks."""

    def test_semver_key_sorts_correctly(self):
        """0.10.1 must sort AFTER 0.9.0 (not lexicographically before)."""
        from pathlib import PurePosixPath

        def _semver_key(p):
            try:
                return tuple(int(x) for x in p.name.split("."))
            except ValueError:
                return (0,)

        paths = [PurePosixPath("0.9.0"), PurePosixPath("0.10.1"), PurePosixPath("0.2.0")]
        result = max(paths, key=_semver_key)
        assert result.name == "0.10.1", f"Expected 0.10.1 as latest, got {result.name}"

        # Verify lexicographic sort would get it WRONG
        lex_sorted = sorted(paths)
        assert lex_sorted[-1].name != "0.10.1", "Lexicographic sort should NOT work for semver"

    def test_semver_key_handles_non_numeric(self):
        """Non-numeric version dirs should not crash the sort."""
        from pathlib import PurePosixPath

        def _semver_key(p):
            try:
                return tuple(int(x) for x in p.name.split("."))
            except ValueError:
                return (0,)

        paths = [PurePosixPath("latest"), PurePosixPath("0.10.1"), PurePosixPath("0.9.0")]
        result = max(paths, key=_semver_key)
        assert result.name == "0.10.1"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
