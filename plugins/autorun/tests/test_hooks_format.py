"""
Test hooks.json format for Claude Code and Gemini CLI compatibility.

This test suite ensures:
1. Source claude-hooks.json uses Claude Code format
2. hooks.json uses Gemini CLI format
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
    hooks_file = get_plugin_root() / "hooks" / "claude-hooks.json"

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
    hooks_file = get_plugin_root() / "hooks" / "claude-hooks.json"

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
    hooks_file = get_plugin_root() / "hooks" / "hooks.json"
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
    hooks_file = get_plugin_root() / "hooks" / "hooks.json"

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
    hooks_file = get_plugin_root() / "hooks" / "hooks.json"

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
    hooks_file = get_plugin_root() / "hooks" / "hooks.json"

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
    hooks_file = get_plugin_root() / "hooks" / "hooks.json"

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
    hooks_file = get_plugin_root() / "hooks" / "claude-hooks.json"

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
    hooks_file = get_plugin_root() / "hooks" / "hooks.json"

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
        get_plugin_root() / "hooks" / "claude-hooks.json",
        get_plugin_root() / "hooks" / "hooks.json"
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

    with open(plugin_json, encoding="utf-8") as f:
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

    def test_gemini_before_tool_covers_exit_plan_mode(self):
        """Gemini BeforeTool must fire for exit_plan_mode (needed by backup export).

        With catch-all (no matcher), all tools including exit_plan_mode are covered.
        If a selective matcher is used, exit_plan_mode must be included.
        """
        hooks_path = get_plugin_root() / "hooks" / "hooks.json"
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
        hooks_path = get_plugin_root() / "hooks" / "claude-hooks.json"
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
        hooks_path = get_plugin_root() / "hooks" / "claude-hooks.json"
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
        hooks_path = get_plugin_root() / "hooks" / "hooks.json"
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
        hooks_path = get_plugin_root() / "hooks" / "hooks.json"
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        before_tool_groups = hooks["hooks"]["BeforeTool"]
        has_catch_all = any("matcher" not in g for g in before_tool_groups)
        assert has_catch_all, (
            f"Gemini BeforeTool must have a catch-all group (no matcher). "
            f"Groups: {before_tool_groups}"
        )

    def test_claude_hooks_pre_tool_use_catches_all_tools(self):
        """Claude PreToolUse must fire for ALL tools (no matcher = catch-all).

        WOLOG: Consistent with PostToolUse catch-all pattern.
        All PreToolUse handlers self-filter by tool name, so no incorrect
        processing occurs. Catch-all future-proofs new handlers.
        """
        hooks_path = get_plugin_root() / "hooks" / "claude-hooks.json"
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        pre_tool_groups = hooks["hooks"]["PreToolUse"]
        has_catch_all = any("matcher" not in g for g in pre_tool_groups)
        assert has_catch_all, (
            f"Claude PreToolUse must have a catch-all group (no matcher). "
            f"Groups: {pre_tool_groups}"
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
        hooks_file = get_plugin_root() / "hooks" / "claude-hooks.json"
        with open(hooks_file, encoding="utf-8") as f:
            data = json.load(f)
        for event in data.get("hooks", {}):
            assert event in CLAUDE_CODE_VALID_EVENTS, (
                f"'{event}' in claude-hooks.json is not a valid Claude Code event. "
                f"Valid: {sorted(CLAUDE_CODE_VALID_EVENTS)}"
            )

    def test_gemini_hooks_only_valid_gemini_events(self):
        """hooks.json must only use Gemini CLI event names."""
        hooks_file = get_plugin_root() / "hooks" / "hooks.json"
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

        claude_file = get_plugin_root() / "hooks" / "claude-hooks.json"
        with open(claude_file, encoding="utf-8") as f:
            claude_events = set(json.load(f).get("hooks", {}).keys())
        contamination = claude_events & gemini_only
        assert not contamination, f"Claude hooks contains Gemini-only events: {contamination}"

        gemini_file = get_plugin_root() / "hooks" / "hooks.json"
        with open(gemini_file, encoding="utf-8") as f:
            gemini_events = set(json.load(f).get("hooks", {}).keys())
        contamination = gemini_events & claude_only
        assert not contamination, f"Gemini hooks contains Claude-only events: {contamination}"


class TestCacheCleanup:
    """Test that Claude Code plugin cache does not contain Gemini hooks."""

    def test_claude_cache_has_no_gemini_hooks(self):
        """Claude Code plugin cache must NOT contain hooks.json (Gemini format).

        Bug: Claude Code scans entire hooks/ dir, rejects ALL hooks if any file
        has invalid events. hooks.json has BeforeAgent/BeforeTool (Gemini-only).
        """
        cache_hooks_dir = Path.home() / ".claude" / "plugins" / "cache" / "autorun" / "ar"
        if not cache_hooks_dir.exists():
            pytest.skip("Plugin cache not installed")
        version_dirs = [d for d in cache_hooks_dir.iterdir() if d.is_dir()]
        if not version_dirs:
            pytest.skip("No version directories in cache")
        # Use semver-aware sort: split on '.' and compare as integers.
        # Lexicographic sorted() puts "0.9.0" AFTER "0.10.0" which is wrong.
        def _semver_key(p: Path):
            try:
                return tuple(int(x) for x in p.name.split("."))
            except ValueError:
                return (0,)
        latest = max(version_dirs, key=_semver_key)
        gemini_hooks = latest / "hooks" / "hooks.json"
        assert not gemini_hooks.exists(), (
            f"Gemini hooks.json in Claude Code cache at {gemini_hooks}. "
            "This causes ALL plugin hooks to fail. Run: "
            "uv run --project plugins/autorun python -m autorun --install --force"
        )
        # Also verify claude-hooks.json IS present (not accidentally deleted)
        claude_hooks = latest / "hooks" / "claude-hooks.json"
        assert claude_hooks.exists(), (
            f"claude-hooks.json missing from cache at {claude_hooks}. "
            "Plugin hooks will not load without it."
        )


class TestCleanCrossCliHooks:
    """Unit tests for _clean_cross_cli_hooks() function."""

    def test_clean_claude_removes_gemini_file(self):
        """_clean_cross_cli_hooks removes Gemini files from Claude cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir) / "hooks"
            hooks_dir.mkdir()
            (hooks_dir / "claude-hooks.json").write_text("{}")
            (hooks_dir / "hooks.json").write_text("{}")
            (hooks_dir / "old.bak").write_text("")

            from autorun.install import _clean_cross_cli_hooks
            _clean_cross_cli_hooks(Path(tmpdir), target_cli="claude")

            assert (hooks_dir / "claude-hooks.json").exists()
            assert not (hooks_dir / "hooks.json").exists()
            assert not (hooks_dir / "old.bak").exists()

    def test_clean_gemini_removes_claude_file(self):
        """_clean_cross_cli_hooks removes Claude files from Gemini cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir) / "hooks"
            hooks_dir.mkdir()
            (hooks_dir / "claude-hooks.json").write_text("{}")
            (hooks_dir / "hooks.json").write_text("{}")

            from autorun.install import _clean_cross_cli_hooks
            _clean_cross_cli_hooks(Path(tmpdir), target_cli="gemini")

            assert not (hooks_dir / "claude-hooks.json").exists()
            assert (hooks_dir / "hooks.json").exists()

    def test_skips_symlinks(self):
        """_clean_cross_cli_hooks must NOT delete symlinks (could delete source)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir) / "hooks"
            hooks_dir.mkdir()
            real_file = Path(tmpdir) / "real_hooks.json"
            real_file.write_text("{}")
            (hooks_dir / "claude-hooks.json").write_text("{}")
            (hooks_dir / "hooks.json").symlink_to(real_file)

            from autorun.install import _clean_cross_cli_hooks
            _clean_cross_cli_hooks(Path(tmpdir), target_cli="claude")

            # Symlink should NOT be deleted; source file must survive
            assert (hooks_dir / "hooks.json").is_symlink()
            assert real_file.exists()
            assert (hooks_dir / "claude-hooks.json").exists()

    def test_no_hooks_dir(self):
        """Handles missing hooks/ directory gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from autorun.install import _clean_cross_cli_hooks
            _clean_cross_cli_hooks(Path(tmpdir), target_cli="claude")
            # No error, no crash

    def test_single_file_only_preserved(self):
        """Don't delete hooks.json if claude-hooks.json doesn't exist (non-dual-CLI plugin)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir) / "hooks"
            hooks_dir.mkdir()
            (hooks_dir / "hooks.json").write_text("{}")
            # No claude-hooks.json — this is a single-CLI plugin

            from autorun.install import _clean_cross_cli_hooks
            _clean_cross_cli_hooks(Path(tmpdir), target_cli="claude")

            # hooks.json should be preserved (safety guard)
            assert (hooks_dir / "hooks.json").exists()


    def test_read_only_file_does_not_crash(self):
        """PermissionError on unlink() must not crash the installer."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir) / "hooks"
            hooks_dir.mkdir()
            (hooks_dir / "claude-hooks.json").write_text("{}")
            target = hooks_dir / "hooks.json"
            target.write_text("{}")
            # Make read-only
            import os
            os.chmod(str(target), 0o444)
            # Also make parent read-only to prevent deletion
            os.chmod(str(hooks_dir), 0o555)

            from autorun.install import _clean_cross_cli_hooks
            try:
                # Should NOT raise — PermissionError is caught internally
                _clean_cross_cli_hooks(Path(tmpdir), target_cli="claude")
            finally:
                # Restore permissions for cleanup
                os.chmod(str(hooks_dir), 0o755)
                os.chmod(str(target), 0o644)

    def test_invalid_target_cli_is_noop(self):
        """Invalid target_cli value should not crash or delete anything."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir) / "hooks"
            hooks_dir.mkdir()
            (hooks_dir / "claude-hooks.json").write_text("{}")
            (hooks_dir / "hooks.json").write_text("{}")

            from autorun.install import _clean_cross_cli_hooks
            # "invalid" doesn't match "claude" or "gemini" branches — falls through
            # with no remove_files, so nothing is deleted. No error.
            _clean_cross_cli_hooks(Path(tmpdir), target_cli="invalid")

            assert (hooks_dir / "claude-hooks.json").exists()
            assert (hooks_dir / "hooks.json").exists()

    def test_bak_symlink_not_deleted(self):
        """Backup symlinks should not be deleted (safety guard)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir) / "hooks"
            hooks_dir.mkdir()
            (hooks_dir / "claude-hooks.json").write_text("{}")
            real_file = Path(tmpdir) / "real.bak"
            real_file.write_text("important")
            (hooks_dir / "backup.bak").symlink_to(real_file)

            from autorun.install import _clean_cross_cli_hooks
            _clean_cross_cli_hooks(Path(tmpdir), target_cli="claude")

            # Symlink .bak should survive, source should survive
            assert (hooks_dir / "backup.bak").is_symlink()
            assert real_file.exists()


class TestSemverSortInCacheTest:
    """Verify the semver sort fix in test_claude_cache_has_no_gemini_hooks."""

    def test_semver_key_sorts_correctly(self):
        """0.10.0 must sort AFTER 0.9.0 (not lexicographically before)."""
        from pathlib import PurePosixPath

        def _semver_key(p):
            try:
                return tuple(int(x) for x in p.name.split("."))
            except ValueError:
                return (0,)

        paths = [PurePosixPath("0.9.0"), PurePosixPath("0.10.0"), PurePosixPath("0.2.0")]
        result = max(paths, key=_semver_key)
        assert result.name == "0.10.0", f"Expected 0.10.0 as latest, got {result.name}"

        # Verify lexicographic sort would get it WRONG
        lex_sorted = sorted(paths)
        assert lex_sorted[-1].name != "0.10.0", "Lexicographic sort should NOT work for semver"

    def test_semver_key_handles_non_numeric(self):
        """Non-numeric version dirs should not crash the sort."""
        from pathlib import PurePosixPath

        def _semver_key(p):
            try:
                return tuple(int(x) for x in p.name.split("."))
            except ValueError:
                return (0,)

        paths = [PurePosixPath("latest"), PurePosixPath("0.10.0"), PurePosixPath("0.9.0")]
        result = max(paths, key=_semver_key)
        assert result.name == "0.10.0"


class TestSelfHealCacheHooks:
    """Edge case tests for _self_heal_cache_hooks in core.py."""

    def test_nonexistent_cache_root(self):
        """_self_heal_cache_hooks should silently skip if cache root doesn't exist."""
        # This is tested implicitly by the code — cache_root.exists() returns False.
        # Verify the function doesn't crash when called directly.
        import types
        from unittest.mock import patch, MagicMock

        mock_daemon = MagicMock()
        # Directly test the method body logic
        with patch("autorun.install._clean_cross_cli_hooks") as mock_clean, \
             patch("autorun.install.MARKETPLACE", "autorun"), \
             patch("pathlib.Path.home") as mock_home:
            fake_home = Path(tempfile.mkdtemp())
            mock_home.return_value = fake_home
            try:
                # No cache dir exists — should return without error
                from autorun.core import AutorunDaemon
                daemon = AutorunDaemon.__new__(AutorunDaemon)
                daemon._self_heal_cache_hooks()
                mock_clean.assert_not_called()
            finally:
                import shutil
                shutil.rmtree(fake_home, ignore_errors=True)

    def test_permission_error_on_iterdir(self):
        """PermissionError iterating cache dirs should be caught by broad except."""
        from unittest.mock import patch, MagicMock

        fake_home = Path(tempfile.mkdtemp())
        try:
            cache_root = fake_home / ".claude" / "plugins" / "cache" / "autorun"
            cache_root.mkdir(parents=True)
            # Make unreadable
            import os
            os.chmod(str(cache_root), 0o000)

            with patch("pathlib.Path.home", return_value=fake_home):
                from autorun.core import AutorunDaemon
                daemon = AutorunDaemon.__new__(AutorunDaemon)
                # Should NOT raise — caught by broad except
                daemon._self_heal_cache_hooks()
        finally:
            import os
            os.chmod(str(cache_root), 0o755)
            import shutil
            shutil.rmtree(fake_home, ignore_errors=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
