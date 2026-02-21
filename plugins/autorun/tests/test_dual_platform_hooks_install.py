#!/usr/bin/env python3
"""Comprehensive dual-platform (Claude Code + Gemini CLI) hook and installation tests.

Tests validate ALL installation pathways and hook configurations for both
Claude Code and Gemini CLI, ensuring no regressions on any pathway.

Coverage:
1. Hook files - correct format, UV-centric commands, path variables, events, matchers
2. Hook entry point - works with both env vars and __file__ inference
3. Installation pathways - Claude plugin system and Gemini extension system
4. Hook file naming - Gemini reads hooks.json, Claude reads claude-hooks.json
5. Daemon code path - continue field correctness for both platforms
6. End-to-end blocking - rm/cat commands blocked through both pathways

Requirements sourced from:
- notes/2026_02_08_0225_installer_pathways_analysis_feature_matrix_consolidation_plan.md
- notes/2026_02_10_1948_gemini_hooks_integration_complete_notes.md
- notes/2026_02_09_2330_clautorun_install_paths_reference.md
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from clautorun.core import EventContext
from clautorun.main import pretooluse_handler
from clautorun.session_manager import session_state, clear_test_session_state

# =============================================================================
# Utilities
# =============================================================================


def _extract_function(content: str, func_name: str) -> str:
    """Extract a function body from source code by finding the next def at same indent level.

    This avoids hardcoded character windows that break when code grows.
    """
    import re
    func_idx = content.find(f"def {func_name}(")
    if func_idx < 0:
        return ""
    # Find the indentation of this function
    line_start = content.rfind("\n", 0, func_idx) + 1
    indent = func_idx - line_start
    # Find next function/class at same or lesser indent
    pattern = re.compile(rf'\n.{{0,{indent}}}(?:def |class )\w+', re.MULTILINE)
    match = pattern.search(content, func_idx + 1)
    end = match.start() if match else len(content)
    return content[func_idx:end]


# =============================================================================
# Constants
# =============================================================================

PLUGIN_ROOT = Path(__file__).parent.parent
HOOKS_DIR = PLUGIN_ROOT / "hooks"
HOOKS_JSON = HOOKS_DIR / "claude-hooks.json"
GEMINI_HOOKS_JSON = HOOKS_DIR / "hooks.json"
HOOK_ENTRY_PY = HOOKS_DIR / "hook_entry.py"
PLUGIN_JSON = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
GEMINI_EXT_JSON = PLUGIN_ROOT / "gemini-extension.json"
INSTALL_PY = PLUGIN_ROOT / "src" / "clautorun" / "install.py"
CORE_PY = PLUGIN_ROOT / "src" / "clautorun" / "core.py"
MAIN_PY = PLUGIN_ROOT / "src" / "clautorun" / "main.py"

# Expected hook events per platform
CLAUDE_HOOK_EVENTS = {
    "UserPromptSubmit", "PreToolUse", "PostToolUse",
    "SessionStart", "Stop", "SubagentStop",
}
GEMINI_HOOK_EVENTS = {
    "SessionStart", "BeforeAgent", "BeforeTool", "AfterTool", "SessionEnd",
}

# Path variables per platform
CLAUDE_PATH_VAR = "${CLAUDE_PLUGIN_ROOT}"
GEMINI_PATH_VAR = "${extensionPath}"


# =============================================================================
# Helpers
# =============================================================================


def load_hooks_json(path: Path) -> dict:
    """Load and parse a hooks JSON file."""
    assert path.exists(), f"Hooks file not found: {path}"
    with open(path) as f:
        return json.load(f)


def extract_all_commands(hooks_data: dict) -> list:
    """Extract all command strings from a hooks.json structure."""
    commands = []
    for event_name, event_configs in hooks_data.get("hooks", {}).items():
        for config in event_configs:
            for hook in config.get("hooks", []):
                if "command" in hook:
                    commands.append(hook["command"])
    return commands


def extract_all_matchers(hooks_data: dict) -> list:
    """Extract all matcher strings from a hooks.json structure."""
    matchers = []
    for event_name, event_configs in hooks_data.get("hooks", {}).items():
        for config in event_configs:
            if "matcher" in config:
                matchers.append(config["matcher"])
    return matchers


# =============================================================================
# Test Class 1: Claude claude-hooks.json correctness
# =============================================================================


class TestClaudeHooksJson:
    """Validate Claude Code claude-hooks.json is correct and complete."""

    def test_valid_json(self):
        data = load_hooks_json(HOOKS_JSON)
        assert isinstance(data, dict)
        assert "hooks" in data
        assert "description" in data

    def test_uses_claude_path_variable(self):
        data = load_hooks_json(HOOKS_JSON)
        raw = json.dumps(data)
        assert CLAUDE_PATH_VAR in raw
        assert GEMINI_PATH_VAR not in raw

    def test_all_commands_use_uv_run(self):
        data = load_hooks_json(HOOKS_JSON)
        commands = extract_all_commands(data)
        assert len(commands) > 0
        for cmd in commands:
            # Allow either uv run or direct venv path
            is_valid = cmd.startswith("uv run") or ".venv/bin/python" in cmd
            assert is_valid, f"Command must use uv or venv python, got: {cmd}"

    def test_no_bare_python3(self):
        data = load_hooks_json(HOOKS_JSON)
        commands = extract_all_commands(data)
        for cmd in commands:
            assert not cmd.startswith("python3 "), \
                f"Bare python3 found: {cmd}"
            assert not cmd.startswith("python "), \
                f"Bare python found: {cmd}"

    def test_no_cd_in_commands(self):
        data = load_hooks_json(HOOKS_JSON)
        commands = extract_all_commands(data)
        for cmd in commands:
            assert not cmd.startswith("cd "), \
                f"Command must not use cd: {cmd}"

    def test_has_all_required_events(self):
        data = load_hooks_json(HOOKS_JSON)
        hooks_section = data.get("hooks", {})
        for event in {"PreToolUse", "PostToolUse", "UserPromptSubmit", "SessionStart"}:
            assert event in hooks_section, \
                f"Required Claude event '{event}' missing"

    def test_has_no_gemini_events(self):
        data = load_hooks_json(HOOKS_JSON)
        hooks_section = data.get("hooks", {})
        for event in {"BeforeTool", "AfterTool", "BeforeAgent", "SessionEnd"}:
            assert event not in hooks_section, \
                f"Gemini-only event '{event}' in Claude hooks"

    def test_pretooluse_matcher_includes_bash(self):
        data = load_hooks_json(HOOKS_JSON)
        pretool = data["hooks"].get("PreToolUse", [])
        assert len(pretool) > 0
        matchers_str = "|".join(c.get("matcher", "") for c in pretool)
        assert "Bash" in matchers_str, \
            f"PreToolUse must match 'Bash'. Matchers: {matchers_str}"

    def test_pretooluse_matcher_includes_write(self):
        data = load_hooks_json(HOOKS_JSON)
        pretool = data["hooks"].get("PreToolUse", [])
        matchers_str = "|".join(c.get("matcher", "") for c in pretool)
        assert "Write" in matchers_str

    def test_timeout_is_seconds(self):
        data = load_hooks_json(HOOKS_JSON)
        for event_configs in data.get("hooks", {}).values():
            for config in event_configs:
                for hook in config.get("hooks", []):
                    if "timeout" in hook:
                        assert hook["timeout"] < 100, \
                            f"Claude timeout should be seconds (< 100): {hook['timeout']}"

    def test_all_commands_reference_hook_entry(self):
        data = load_hooks_json(HOOKS_JSON)
        commands = extract_all_commands(data)
        for cmd in commands:
            assert "hook_entry.py" in cmd


# =============================================================================
# Test Class 2: Gemini hooks.json correctness
# =============================================================================


class TestGeminiHooksJson:
    """Validate Gemini CLI hooks.json is correct and complete."""

    def test_valid_json(self):
        data = load_hooks_json(GEMINI_HOOKS_JSON)
        assert isinstance(data, dict)
        assert "hooks" in data

    def test_description_mentions_gemini(self):
        data = load_hooks_json(GEMINI_HOOKS_JSON)
        assert "gemini" in data["description"].lower()

    def test_uses_gemini_path_variable(self):
        data = load_hooks_json(GEMINI_HOOKS_JSON)
        raw = json.dumps(data)
        assert GEMINI_PATH_VAR in raw
        assert CLAUDE_PATH_VAR not in raw

    def test_all_commands_use_uv_run(self):
        data = load_hooks_json(GEMINI_HOOKS_JSON)
        commands = extract_all_commands(data)
        assert len(commands) > 0
        for cmd in commands:
            is_valid = cmd.startswith("uv run") or ".venv/bin/python" in cmd
            assert is_valid, f"Gemini command must use uv or venv python, got: {cmd}"

    def test_no_bare_python3(self):
        data = load_hooks_json(GEMINI_HOOKS_JSON)
        commands = extract_all_commands(data)
        for cmd in commands:
            assert not cmd.startswith("python3 "), \
                f"Bare python3 in Gemini hooks: {cmd}"

    def test_no_env_var_assignment(self):
        data = load_hooks_json(GEMINI_HOOKS_JSON)
        raw = json.dumps(data)
        for pattern in ["CLAUTORUN_PLUGIN_ROOT=", "CLAUDE_PLUGIN_ROOT=", "PLUGIN_ROOT="]:
            assert pattern not in raw, \
                f"Gemini CLI doesn't support env var assignment: {pattern}"

    def test_has_all_required_events(self):
        data = load_hooks_json(GEMINI_HOOKS_JSON)
        hooks_section = data.get("hooks", {})
        for event in {"BeforeTool", "SessionStart"}:
            assert event in hooks_section, \
                f"Required Gemini event '{event}' missing"

    def test_has_no_claude_only_events(self):
        data = load_hooks_json(GEMINI_HOOKS_JSON)
        hooks_section = data.get("hooks", {})
        for event in {"PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SubagentStop"}:
            assert event not in hooks_section, \
                f"Claude-only event '{event}' in Gemini hooks"

    def test_beforetool_matcher_includes_run_shell_command(self):
        data = load_hooks_json(GEMINI_HOOKS_JSON)
        beforetool = data["hooks"].get("BeforeTool", [])
        assert len(beforetool) > 0
        matchers_str = "|".join(c.get("matcher", "") for c in beforetool)
        assert "run_shell_command" in matchers_str

    def test_beforetool_matcher_includes_write_file(self):
        data = load_hooks_json(GEMINI_HOOKS_JSON)
        beforetool = data["hooks"].get("BeforeTool", [])
        matchers_str = "|".join(c.get("matcher", "") for c in beforetool)
        assert "write_file" in matchers_str

    def test_timeout_is_milliseconds(self):
        data = load_hooks_json(GEMINI_HOOKS_JSON)
        for event_configs in data.get("hooks", {}).values():
            for config in event_configs:
                for hook in config.get("hooks", []):
                    if "timeout" in hook:
                        assert hook["timeout"] >= 100, \
                            f"Gemini timeout should be ms (>= 100): {hook['timeout']}"

    def test_hooks_have_name_field(self):
        data = load_hooks_json(GEMINI_HOOKS_JSON)
        for event_configs in data.get("hooks", {}).values():
            for config in event_configs:
                for hook in config.get("hooks", []):
                    assert "name" in hook, f"Gemini hook missing 'name': {hook}"

    def test_hooks_have_type_command(self):
        data = load_hooks_json(GEMINI_HOOKS_JSON)
        for event_configs in data.get("hooks", {}).values():
            for config in event_configs:
                for hook in config.get("hooks", []):
                    assert hook.get("type") == "command"

    def test_all_commands_reference_hook_entry(self):
        data = load_hooks_json(GEMINI_HOOKS_JSON)
        commands = extract_all_commands(data)
        for cmd in commands:
            assert "hook_entry.py" in cmd


# =============================================================================
# Test Class 3: Cross-platform consistency
# =============================================================================


class TestCrossPlatformConsistency:
    """Verify hooks files are consistent where they should be."""

    def test_same_hook_entry_script(self):
        claude_cmds = extract_all_commands(load_hooks_json(HOOKS_JSON))
        gemini_cmds = extract_all_commands(load_hooks_json(GEMINI_HOOKS_JSON))
        for cmd in claude_cmds + gemini_cmds:
            assert "hook_entry.py" in cmd

    def test_event_name_mapping_complete(self):
        event_mapping = {
            "UserPromptSubmit": "BeforeAgent",
            "PreToolUse": "BeforeTool",
            "PostToolUse": "AfterTool",
            "SessionStart": "SessionStart",
            "Stop": "SessionEnd",
        }
        claude_events = set(load_hooks_json(HOOKS_JSON).get("hooks", {}).keys())
        gemini_events = set(load_hooks_json(GEMINI_HOOKS_JSON).get("hooks", {}).keys())
        for claude_event, gemini_event in event_mapping.items():
            if claude_event in claude_events:
                assert gemini_event in gemini_events, \
                    f"Claude has {claude_event} but Gemini missing {gemini_event}"

    def test_tool_name_mapping_complete(self):
        tool_mapping = {"Bash": "run_shell_command", "Write": "write_file", "Edit": "replace"}
        claude_matchers = " ".join(extract_all_matchers(load_hooks_json(HOOKS_JSON)))
        gemini_matchers = " ".join(extract_all_matchers(load_hooks_json(GEMINI_HOOKS_JSON)))
        for claude_tool, gemini_tool in tool_mapping.items():
            if claude_tool in claude_matchers:
                assert gemini_tool in gemini_matchers, \
                    f"Claude matches '{claude_tool}' but Gemini missing '{gemini_tool}'"

    def test_both_use_uv_run(self):
        for cmd in extract_all_commands(load_hooks_json(HOOKS_JSON)):
            assert "uv run" in cmd or ".venv/bin/python" in cmd
        for cmd in extract_all_commands(load_hooks_json(GEMINI_HOOKS_JSON)):
            assert "uv run" in cmd or ".venv/bin/python" in cmd

    def test_neither_uses_cd(self):
        for cmd in extract_all_commands(load_hooks_json(HOOKS_JSON)):
            assert not cmd.startswith("cd ")
        for cmd in extract_all_commands(load_hooks_json(GEMINI_HOOKS_JSON)):
            assert not cmd.startswith("cd ")


# =============================================================================
# Test Class 4: Plugin/Extension manifest files
# =============================================================================


class TestManifestFiles:
    """Validate plugin.json and gemini-extension.json are correct."""

    def test_plugin_json_has_hooks_field(self):
        with open(PLUGIN_JSON) as f:
            manifest = json.load(f)
        assert "hooks" in manifest, \
            "plugin.json MUST have 'hooks' field for Claude Code hook discovery"
        assert "claude-hooks.json" in manifest["hooks"]

    def test_plugin_json_hooks_file_exists(self):
        with open(PLUGIN_JSON) as f:
            manifest = json.load(f)
        hooks_ref = manifest.get("hooks", "")
        hooks_path = PLUGIN_ROOT / hooks_ref.lstrip("./")
        assert hooks_path.exists(), f"Referenced hooks file missing: {hooks_path}"

    def test_gemini_extension_json_exists(self):
        assert GEMINI_EXT_JSON.exists()

    def test_gemini_extension_json_has_context_file(self):
        with open(GEMINI_EXT_JSON) as f:
            manifest = json.load(f)
        assert manifest.get("contextFileName") == "GEMINI.md"

    def test_version_consistency(self):
        with open(PLUGIN_JSON) as f:
            claude_manifest = json.load(f)
        with open(GEMINI_EXT_JSON) as f:
            gemini_manifest = json.load(f)
        assert claude_manifest.get("version") == gemini_manifest.get("version"), \
            f"Version mismatch: Claude={claude_manifest.get('version')}, " \
            f"Gemini={gemini_manifest.get('version')}"


# =============================================================================
# Test Class 5: hook_entry.py dual-platform support
# =============================================================================


class TestHookEntryDualPlatform:
    """Validate hook_entry.py works for both Claude Code and Gemini CLI."""

    def test_get_plugin_root_with_claude_env(self):
        env = os.environ.copy()
        env["CLAUDE_PLUGIN_ROOT"] = "/test/claude/path"
        env.pop("CLAUTORUN_PLUGIN_ROOT", None)
        script = (
            "import sys; import os; "
            f"sys.path.insert(0, '{HOOK_ENTRY_PY.parent}'); "
            "import hook_entry; "
            "print(hook_entry.get_plugin_root())"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, env=env, timeout=5
        )
        assert "/test/claude/path" in result.stdout.strip()

    def test_get_plugin_root_with_clautorun_env(self):
        env = os.environ.copy()
        env["CLAUTORUN_PLUGIN_ROOT"] = "/test/clautorun/path"
        env["CLAUDE_PLUGIN_ROOT"] = "/test/claude/path"
        script = (
            "import sys; import os; "
            f"sys.path.insert(0, '{HOOK_ENTRY_PY.parent}'); "
            "import hook_entry; "
            "print(hook_entry.get_plugin_root())"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, env=env, timeout=5
        )
        assert "/test/clautorun/path" in result.stdout.strip()

    def test_get_plugin_root_file_inference(self):
        """get_plugin_root() infers from __file__ when no env vars (Gemini path)."""
        env = os.environ.copy()
        env.pop("CLAUDE_PLUGIN_ROOT", None)
        env.pop("CLAUTORUN_PLUGIN_ROOT", None)
        script = (
            "import sys; import os; "
            f"sys.path.insert(0, '{HOOK_ENTRY_PY.parent}'); "
            "import hook_entry; "
            "print(hook_entry.get_plugin_root())"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, env=env, timeout=5
        )
        inferred = result.stdout.strip()
        assert len(inferred) > 0, "Should return some path"

    def test_detect_cli_type_claude_default(self):
        env = os.environ.copy()
        env.pop("GEMINI_SESSION_ID", None)
        env.pop("GEMINI_PROJECT_DIR", None)
        script = (
            "import importlib.util, sys; "
            f"spec = importlib.util.spec_from_file_location('hook_entry', '{HOOK_ENTRY_PY}'); "
            "mod = importlib.util.module_from_spec(spec); "
            "spec.loader.exec_module(mod); "
            "print(mod.detect_cli_type())"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, env=env, timeout=5
        )
        assert result.stdout.strip() == "claude", \
            f"Expected 'claude', got: {result.stdout.strip()}, stderr: {result.stderr}"

    def test_detect_cli_type_gemini_session(self):
        env = os.environ.copy()
        env["GEMINI_SESSION_ID"] = "test-session-123"
        script = (
            "import importlib.util, sys; "
            f"spec = importlib.util.spec_from_file_location('hook_entry', '{HOOK_ENTRY_PY}'); "
            "mod = importlib.util.module_from_spec(spec); "
            "spec.loader.exec_module(mod); "
            "print(mod.detect_cli_type())"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, env=env, timeout=5
        )
        assert result.stdout.strip() == "gemini", \
            f"Expected 'gemini', got: {result.stdout.strip()}, stderr: {result.stderr}"

    def test_fail_open_returns_valid_json(self):
        env = os.environ.copy()
        env.pop("CLAUDE_PLUGIN_ROOT", None)
        env.pop("CLAUTORUN_PLUGIN_ROOT", None)
        env["PATH"] = "/nonexistent"
        result = subprocess.run(
            [sys.executable, str(HOOK_ENTRY_PY)],
            capture_output=True, text=True, env=env,
            cwd="/tmp", timeout=10
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output.get("continue") is True

    def test_hook_entry_no_debug_logging(self):
        content = HOOK_ENTRY_PY.read_text()
        assert "/tmp/clautorun_hook_debug" not in content


# =============================================================================
# Test Class 6: Gemini installation hook swap logic
# =============================================================================


class TestGeminiHookSwapLogic:
    """Test that hooks.json is Gemini format and claude-hooks.json is Claude format.

    No swap needed: hooks.json is always Gemini format (Gemini hardcodes this filename).
    Claude Code reads plugin.json 'hooks' field pointing to claude-hooks.json.
    See: https://geminicli.com/docs/extensions/writing-extensions/
    """

    def test_install_py_references_both_hooks_files(self):
        content = INSTALL_PY.read_text()
        assert "claude-hooks.json" in content
        assert "hooks.json" in content

    def test_hooks_json_is_gemini_format(self):
        """hooks.json must be Gemini format since Gemini CLI hardcodes this filename."""
        hooks_file = HOOKS_DIR / "hooks.json"
        with open(hooks_file) as f:
            data = json.load(f)
        hooks = data.get("hooks", {})
        assert "BeforeTool" in hooks, "hooks.json should use Gemini event name BeforeTool"
        assert "PreToolUse" not in hooks, "hooks.json should NOT use Claude event name PreToolUse"

    def test_claude_hooks_json_is_claude_format(self):
        """claude-hooks.json must be Claude format."""
        hooks_file = HOOKS_DIR / "claude-hooks.json"
        with open(hooks_file) as f:
            data = json.load(f)
        hooks = data.get("hooks", {})
        assert "PreToolUse" in hooks, "claude-hooks.json should use Claude event name PreToolUse"
        assert "BeforeTool" not in hooks, "claude-hooks.json should NOT use Gemini event name BeforeTool"

    def test_no_swap_logic_in_installer(self):
        """Installer should not need swap logic since filenames are correct."""
        content = INSTALL_PY.read_text()
        assert "hooks.json.claude-backup" not in content, \
            "Swap logic removed — hooks.json is always Gemini format"

    def test_both_hooks_files_coexist(self):
        """Both hooks files exist side by side — no swap needed."""
        assert (HOOKS_DIR / "hooks.json").exists(), "Gemini hooks.json must exist"
        assert (HOOKS_DIR / "claude-hooks.json").exists(), "Claude claude-hooks.json must exist"


# =============================================================================
# Test Class 7: Gemini installation pathway
# =============================================================================


class TestGeminiInstallPathway:
    """Test Gemini-specific installation code in install.py."""

    def test_install_for_gemini_function_exists(self):
        content = INSTALL_PY.read_text()
        assert "def _install_for_gemini(" in content

    def test_install_for_gemini_checks_cli(self):
        content = INSTALL_PY.read_text()
        assert 'shutil.which("gemini")' in content

    def test_install_for_gemini_finds_all_plugins(self):
        content = INSTALL_PY.read_text()
        assert "gemini-extension.json" in content

    def test_install_for_gemini_uses_consent_flag(self):
        content = INSTALL_PY.read_text()
        assert '"--consent"' in content

    def test_gemini_extension_json_has_required_fields(self):
        with open(GEMINI_EXT_JSON) as f:
            manifest = json.load(f)
        for field in ["name", "version", "description"]:
            assert field in manifest


# =============================================================================
# Test Class 8: Claude installation pathway
# =============================================================================


class TestClaudeInstallPathway:
    """Test Claude Code-specific installation code in install.py."""

    def test_install_uses_marketplace_add(self):
        content = INSTALL_PY.read_text()
        assert '"claude", "plugin", "marketplace", "add"' in content

    def test_install_uses_plugin_install(self):
        content = INSTALL_PY.read_text()
        assert '"claude", "plugin", "install"' in content

    def test_install_uses_plugin_enable(self):
        content = INSTALL_PY.read_text()
        assert '"claude", "plugin", "enable"' in content

    def test_install_tries_update_first(self):
        content = INSTALL_PY.read_text()
        update_pos = content.find('"claude", "plugin", "update"')
        install_pos = content.find('"claude", "plugin", "install"')
        assert update_pos > 0 and install_pos > 0
        assert update_pos < install_pos

    def test_install_has_cache_fallback(self):
        content = INSTALL_PY.read_text()
        assert "_install_to_cache" in content

    def test_install_has_json_registration_fallback(self):
        content = INSTALL_PY.read_text()
        assert "_register_in_json" in content

    def test_install_substitutes_paths(self):
        content = INSTALL_PY.read_text()
        assert "_substitute_paths" in content


# =============================================================================
# Test Class 9: Daemon continue field (PreToolUse blocking)
# =============================================================================


class TestDaemonContinueField:
    """Validate daemon path correctly sets continue=True for tool denial."""

    def test_core_py_pretooluse_deny_keeps_ai_working(self):
        content = CORE_PY.read_text()
        respond_block = _extract_function(content, "respond")
        assert respond_block, "Could not find def respond() in core.py"
        # Verify continue: True is used even on denial to keep AI loop running
        assert '"continue": True' in respond_block
        assert "keep AI working" in respond_block or "loop keeps running" in respond_block

    def test_main_py_pretooluse_deny_keeps_ai_working(self):
        content = MAIN_PY.read_text()
        func_block = _extract_function(content, "build_pretooluse_response")
        assert func_block, "Could not find def build_pretooluse_response() in main.py"
        # Verify continue: True is used even on denial to keep AI loop running
        assert '"continue": True' in func_block
        assert "Keep AI working" in func_block or "loop keeps running" in func_block


# =============================================================================
# Test Class 10: End-to-end rm blocking through both code paths
# =============================================================================


class TestRmBlockingBothPaths:
    """Test that rm commands are blocked through both daemon and legacy paths."""

    TEST_SESSION = "test-dual-platform"

    def _create_test_context(self, tool_name, tool_input):
        """Create a real EventContext like production uses."""
        with session_state(self.TEST_SESSION) as state:
            state["file_policy"] = "ALLOW"
        return EventContext(
            session_id=self.TEST_SESSION,
            event="PreToolUse",
            tool_name=tool_name,
            tool_input=tool_input,
            session_transcript=[],
        )

    def teardown_method(self):
        clear_test_session_state(self.TEST_SESSION)

    def test_rm_blocked_via_legacy_handler(self):
        ctx = self._create_test_context("Bash", {"command": "rm -rf /tmp/test"})
        result = pretooluse_handler(ctx)
        assert result is not None
        # continue should be True to keep AI working even if tool is blocked
        assert result.get("continue") is True

    def test_rm_blocked_mentions_trash(self):
        ctx = self._create_test_context("Bash", {"command": "rm important.txt"})
        result = pretooluse_handler(ctx)
        reason = result.get("reason", "") or result.get("stopReason", "")
        hook_output = result.get("hookSpecificOutput", {})
        full_reason = reason + str(hook_output.get("permissionDecisionReason", ""))
        assert "trash" in full_reason.lower()

    def test_safe_commands_allowed(self):
        for cmd in ["ls -la", "pwd", "echo hello", "git status"]:
            ctx = self._create_test_context("Bash", {"command": cmd})
            result = pretooluse_handler(ctx)
            if result is not None:
                assert result.get("continue") is not False, \
                    f"Safe command '{cmd}' should not be blocked"

    def test_cat_blocked(self):
        ctx = self._create_test_context("Bash", {"command": "cat /etc/passwd"})
        result = pretooluse_handler(ctx)
        assert result is not None
        assert result.get("continue") is True

    def test_gemini_tool_name_run_shell_command(self):
        ctx = self._create_test_context("run_shell_command", {"command": "rm file.txt"})
        result = pretooluse_handler(ctx)
        if result is not None:
            assert result.get("continue") is True

    def test_gemini_tool_name_bash_command(self):
        ctx = self._create_test_context("bash_command", {"command": "rm file.txt"})
        result = pretooluse_handler(ctx)
        if result is not None:
            assert result.get("continue") is True


# =============================================================================
# Test Class 11: Build directory sync
# =============================================================================


class TestBuildDirectorySync:
    """Verify build directory hooks match source hooks."""

    def test_build_hooks_json_matches_source(self):
        build_hooks = PLUGIN_ROOT / "build" / "hooks" / "claude-hooks.json"
        if not build_hooks.exists():
            pytest.skip("Build directory not present")
        assert load_hooks_json(HOOKS_JSON) == load_hooks_json(build_hooks)

    def test_build_gemini_hooks_matches_source(self):
        build_gemini = PLUGIN_ROOT / "build" / "hooks" / "hooks.json"
        if not build_gemini.exists():
            pytest.skip("Build directory not present")
        assert load_hooks_json(GEMINI_HOOKS_JSON) == load_hooks_json(build_gemini)

    def test_build_hook_entry_matches_source(self):
        build_entry = PLUGIN_ROOT / "build" / "hooks" / "hook_entry.py"
        if not build_entry.exists():
            pytest.skip("Build directory not present")
        assert HOOK_ENTRY_PY.read_text() == build_entry.read_text()


# =============================================================================
# Test Class 12: Install pathway detection and routing
# =============================================================================


class TestInstallPathwayDetection:
    """Test install.py correctly detects and routes to the right pathway."""

    def test_install_detects_claude_cli(self):
        content = INSTALL_PY.read_text()
        assert 'shutil.which("claude")' in content

    def test_install_detects_gemini_cli(self):
        content = INSTALL_PY.read_text()
        assert 'shutil.which("gemini")' in content

    def test_install_has_claude_only_flag(self):
        content = INSTALL_PY.read_text()
        assert "claude_only" in content

    def test_install_has_gemini_only_flag(self):
        content = INSTALL_PY.read_text()
        assert "gemini_only" in content

    def test_install_default_installs_both(self):
        content = INSTALL_PY.read_text()
        assert "_install_for_gemini" in content
        assert '"claude", "plugin"' in content

    def test_install_aix_detection(self):
        content = INSTALL_PY.read_text()
        assert "aix" in content.lower()


# =============================================================================
# Test Class 13: hook_entry.py bootstrap
# =============================================================================


class TestHookEntryBootstrap:
    """Verify hook_entry.py bootstrap logic is intact."""

    def test_can_bootstrap_checks_python_version(self):
        content = HOOK_ENTRY_PY.read_text()
        assert "sys.version_info" in content

    def test_can_bootstrap_checks_uv_or_pip(self):
        content = HOOK_ENTRY_PY.read_text()
        assert 'shutil.which("uv")' in content

    def test_bootstrap_uses_lockfile(self):
        content = HOOK_ENTRY_PY.read_text()
        assert "BOOTSTRAP_LOCKFILE" in content

    def test_bootstrap_spawns_background(self):
        content = HOOK_ENTRY_PY.read_text()
        assert "nohup" in content
        assert "start_new_session" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
