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

import importlib.util
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from autorun.core import EventContext
from autorun import plugins
from autorun.session_manager import session_state, clear_test_session_state


# Daemon-path helper — replaces deleted pretooluse_handler
def _pretooluse(ctx):
    """Run daemon-path PreToolUse chain: file policy then command blocking."""
    result = plugins.enforce_file_policy(ctx)
    if result is not None:
        return result
    result = plugins.check_blocked_commands(ctx)
    if result is not None:
        return result
    return ctx.allow()

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
# Post-split layout:
#   - plugins/autorun/hooks/hooks.json    → Claude Code events (default path)
#   - plugins/autorun/src/autorun/gemini_template/hooks/hooks.json
#                                           → Gemini CLI events (template; hidden
#                                             from Claude bug #24115 scanner)
HOOKS_JSON = HOOKS_DIR / "hooks.json"
GEMINI_TEMPLATE_DIR = PLUGIN_ROOT / "src" / "autorun" / "gemini_template"
GEMINI_HOOKS_JSON = GEMINI_TEMPLATE_DIR / "hooks" / "hooks.json"
HOOK_ENTRY_PY = HOOKS_DIR / "hook_entry.py"
PLUGIN_JSON = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
GEMINI_EXT_JSON = GEMINI_TEMPLATE_DIR / "gemini-extension.json"
INSTALL_PY = PLUGIN_ROOT / "src" / "autorun" / "install.py"
CORE_PY = PLUGIN_ROOT / "src" / "autorun" / "core.py"
MAIN_PY = PLUGIN_ROOT / "src" / "autorun" / "main.py"
REPO_ROOT = PLUGIN_ROOT.parent.parent

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
    with open(path, encoding="utf-8") as f:
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
        assert set(data) == {"hooks"}, (
            "Codex plugin hooks/hooks.json is parsed as HooksFile with "
            "deny_unknown_fields; metadata belongs in .claude-plugin/plugin.json"
        )

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

    def test_pretooluse_covers_bash(self):
        data = load_hooks_json(HOOKS_JSON)
        pretool = data["hooks"].get("PreToolUse", [])
        assert len(pretool) > 0
        # Catch-all (no matcher) covers all tools including Bash
        has_catch_all = any("matcher" not in g for g in pretool)
        matchers_str = "|".join(c.get("matcher", "") for c in pretool)
        assert has_catch_all or "Bash" in matchers_str, \
            f"PreToolUse must cover 'Bash' (catch-all or in matcher). Matchers: {matchers_str}"

    def test_pretooluse_covers_write(self):
        data = load_hooks_json(HOOKS_JSON)
        pretool = data["hooks"].get("PreToolUse", [])
        has_catch_all = any("matcher" not in g for g in pretool)
        matchers_str = "|".join(c.get("matcher", "") for c in pretool)
        assert has_catch_all or "Write" in matchers_str, \
            f"PreToolUse must cover 'Write' (catch-all or in matcher). Matchers: {matchers_str}"

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

    def test_all_uv_commands_use_no_sync(self):
        """Claude hook subprocesses must not repair uv envs in the hot path."""
        data = load_hooks_json(HOOKS_JSON)
        commands = extract_all_commands(data)
        for cmd in commands:
            if cmd.startswith("uv run"):
                assert "--no-sync" in cmd, cmd

    def test_default_plugin_hooks_do_not_force_claude_cli(self):
        """Codex plugin loading also parses hooks/hooks.json, so it must auto-detect."""
        data = load_hooks_json(HOOKS_JSON)
        commands = extract_all_commands(data)
        for cmd in commands:
            assert "--cli claude" not in cmd, (
                "hooks/hooks.json is shared by Claude and Codex plugin loading; "
                "forcing Claude makes Codex PreToolUse emit Claude-shaped blocks"
            )


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
        for pattern in ["AUTORUN_PLUGIN_ROOT=", "CLAUDE_PLUGIN_ROOT=", "PLUGIN_ROOT="]:
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

    def test_beforetool_covers_run_shell_command(self):
        data = load_hooks_json(GEMINI_HOOKS_JSON)
        beforetool = data["hooks"].get("BeforeTool", [])
        assert len(beforetool) > 0
        has_catch_all = any("matcher" not in g for g in beforetool)
        matchers_str = "|".join(c.get("matcher", "") for c in beforetool)
        assert has_catch_all or "run_shell_command" in matchers_str, \
            f"BeforeTool must cover run_shell_command (catch-all or in matcher). Matchers: {matchers_str}"

    def test_beforetool_covers_write_file(self):
        data = load_hooks_json(GEMINI_HOOKS_JSON)
        beforetool = data["hooks"].get("BeforeTool", [])
        has_catch_all = any("matcher" not in g for g in beforetool)
        matchers_str = "|".join(c.get("matcher", "") for c in beforetool)
        assert has_catch_all or "write_file" in matchers_str, \
            f"BeforeTool must cover write_file (catch-all or in matcher). Matchers: {matchers_str}"

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

    def test_all_uv_commands_use_no_sync(self):
        """Gemini-family hooks must not run uv sync under 5s hook timeouts."""
        data = load_hooks_json(GEMINI_HOOKS_JSON)
        commands = extract_all_commands(data)
        for cmd in commands:
            if cmd.startswith("uv run"):
                assert "--no-sync" in cmd, cmd


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
        """Verify Claude tool names in matchers have Gemini equivalents.

        Gemini BeforeTool/AfterTool are catch-all (no matcher), which covers
        all tools. When Gemini uses catch-all, the mapping check is satisfied
        because catch-all covers everything.
        """
        tool_mapping = {"Bash": "run_shell_command", "Write": "write_file", "Edit": "replace"}
        claude_matchers = " ".join(extract_all_matchers(load_hooks_json(HOOKS_JSON)))
        gemini_data = load_hooks_json(GEMINI_HOOKS_JSON)
        gemini_matchers = " ".join(extract_all_matchers(gemini_data))
        # Check if Gemini has catch-all BeforeTool/AfterTool (covers all tools)
        gemini_hooks = gemini_data.get("hooks", {})
        gemini_has_catch_all_pretool = any(
            "matcher" not in g
            for g in gemini_hooks.get("BeforeTool", [])
        )
        gemini_has_catch_all_posttool = any(
            "matcher" not in g
            for g in gemini_hooks.get("AfterTool", [])
        )
        for claude_tool, gemini_tool in tool_mapping.items():
            if claude_tool in claude_matchers:
                # Gemini catch-all covers all tools implicitly
                covered = (
                    gemini_tool in gemini_matchers
                    or gemini_has_catch_all_pretool
                    or gemini_has_catch_all_posttool
                )
                assert covered, \
                    f"Claude matches '{claude_tool}' but Gemini missing '{gemini_tool}' " \
                    f"(and no catch-all BeforeTool/AfterTool)"

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

    def test_plugin_json_uses_default_hooks_path(self):
        """Post-split: plugin.json has NO 'hooks' field (uses default hooks/hooks.json)."""
        with open(PLUGIN_JSON, encoding="utf-8") as f:
            manifest = json.load(f)
        assert "hooks" not in manifest, (
            f"plugin.json should not declare a 'hooks' field — Claude Code auto-discovers "
            f"hooks/hooks.json. Got: {manifest.get('hooks')!r}"
        )

    def test_default_claude_hooks_file_exists(self):
        """hooks/hooks.json must exist at the default discovery path."""
        assert HOOKS_JSON.exists(), (
            f"Claude default hooks file missing: {HOOKS_JSON}"
        )

    def test_gemini_extension_json_exists(self):
        assert GEMINI_EXT_JSON.exists()

    def test_gemini_extension_json_has_context_file(self):
        with open(GEMINI_EXT_JSON, encoding="utf-8") as f:
            manifest = json.load(f)
        assert manifest.get("contextFileName") == "GEMINI.md"

    def test_version_consistency(self):
        """Release-facing version numbers must match the autorun package."""
        root_pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        plugin_pyproject = tomllib.loads((PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        expected = plugin_pyproject["project"]["version"]

        with open(PLUGIN_JSON, encoding="utf-8") as f:
            claude_manifest = json.load(f)
        with open(GEMINI_EXT_JSON, encoding="utf-8") as f:
            gemini_manifest = json.load(f)
        codex_manifest = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        metadata = json.loads((PLUGIN_ROOT / "src" / "autorun" / "metadata.json").read_text(encoding="utf-8"))
        marketplace = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        workspace_init = (REPO_ROOT / "src" / "autorun_workspace" / "__init__.py").read_text(encoding="utf-8")
        autorun_init = (PLUGIN_ROOT / "src" / "autorun" / "__init__.py").read_text(encoding="utf-8")
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        gemini_md = (REPO_ROOT / "GEMINI.md").read_text(encoding="utf-8")
        skill_versions = [
            (PLUGIN_ROOT / "skills" / "ai-session-tools" / "SKILL.md").read_text(encoding="utf-8"),
            (PLUGIN_ROOT / "skills" / "claude-session-tools" / "SKILL.md").read_text(encoding="utf-8"),
        ]

        actual = {
            "workspace pyproject.toml": root_pyproject["project"]["version"],
            "plugins/autorun/pyproject.toml": expected,
            ".claude-plugin/plugin.json": claude_manifest.get("version"),
            ".codex-plugin/plugin.json": codex_manifest.get("version"),
            "gemini-extension.json": gemini_manifest.get("version"),
            "metadata.json": metadata.get("version"),
            "workspace __version__": f'__version__ = "{expected}"' in workspace_init,
            "autorun __version__": f'__version__ = "{expected}"' in autorun_init,
            "README expected output": f"autorun-workspace@{expected}" in readme,
            "GEMINI expected output": f"ar@{expected}" in gemini_md,
            "GEMINI stale 0.11.0": "0.11.0" not in gemini_md,
            "ai-session-tools skill": f'version: "{expected}"' in skill_versions[0],
            "claude-session-tools skill": f'version: "{expected}"' in skill_versions[1],
        }
        for plugin in marketplace.get("plugins", []):
            if plugin.get("name") in {"ar", "pdf-extractor"}:
                actual[f"marketplace {plugin['name']}"] = plugin.get("version")

        mismatches = {
            name: value
            for name, value in actual.items()
            if value is not True and value != expected
        }
        assert not mismatches, f"Release version mismatch against {expected}: {mismatches}"


# =============================================================================
# Test Class 5: hook_entry.py dual-platform support
# =============================================================================


class TestHookEntryDualPlatform:
    """Validate hook_entry.py works for both Claude Code and Gemini CLI."""

    def test_get_plugin_root_with_claude_env(self):
        env = os.environ.copy()
        env["CLAUDE_PLUGIN_ROOT"] = "/test/claude/path"
        env.pop("AUTORUN_PLUGIN_ROOT", None)
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

    def test_get_plugin_root_with_autorun_env(self):
        env = os.environ.copy()
        env["AUTORUN_PLUGIN_ROOT"] = "/test/autorun/path"
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
        assert "/test/autorun/path" in result.stdout.strip()

    def test_get_plugin_root_file_inference(self):
        """get_plugin_root() infers from __file__ when no env vars (Gemini path)."""
        env = os.environ.copy()
        env.pop("CLAUDE_PLUGIN_ROOT", None)
        env.pop("AUTORUN_PLUGIN_ROOT", None)
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

    def test_detect_cli_type_codex_payload_without_cli_arg(self, monkeypatch):
        """Plugin cache hooks may be invoked by Codex without ~/.codex/hooks.json."""
        spec = importlib.util.spec_from_file_location("autorun_hook_entry_test", HOOK_ENTRY_PY)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)

        monkeypatch.setattr(module.sys, "argv", ["hook_entry.py"])
        payload = {
            "hook_event_name": "PreToolUse",
            "transcript_path": "/Users/example/.codex/sessions/rollout.jsonl",
        }

        assert module.detect_cli_type(payload) == "codex"

    def test_fail_open_returns_valid_json(self):
        env = os.environ.copy()
        env.pop("CLAUDE_PLUGIN_ROOT", None)
        env.pop("AUTORUN_PLUGIN_ROOT", None)
        env["PATH"] = "/nonexistent"
        result = subprocess.run(
            [sys.executable, str(HOOK_ENTRY_PY)],
            capture_output=True, text=True, env=env,
            cwd="/tmp", timeout=10
        )
        assert result.returncode == 0
        # Phase 1B: empty stdout = pass-through = implicit allow (no rules fired).
        # This IS the correct fail-open behavior — Claude Code treats no JSON output
        # as implicit allow, same as {"continue": true}.
        output = json.loads(result.stdout) if result.stdout.strip() else {}
        assert output.get("continue", True) is True

    def test_hook_entry_no_debug_logging(self):
        content = HOOK_ENTRY_PY.read_text(encoding="utf-8")
        assert "/tmp/autorun_hook_debug" not in content


# =============================================================================
# Test Class 6: Gemini installation hook swap logic
# =============================================================================


class TestGeminiHookSwapLogic:
    """Test that hooks.json is Gemini format and claude-hooks.json is Claude format.

    Post-split layout:
      - plugins/autorun/hooks/hooks.json → Claude (default discovery path)
      - plugins/autorun/src/autorun/gemini_template/hooks/hooks.json → Gemini

    Both files share the filename `hooks.json` but live in different roots so
    each CLI's scanner sees only the events valid for it.
    """

    def test_install_py_references_both_hooks_files(self):
        content = INSTALL_PY.read_text(encoding="utf-8")
        assert "hooks.json" in content
        assert "gemini_template" in content, (
            "install.py must reference the gemini_template path"
        )

    def test_hooks_json_is_gemini_format(self):
        """gemini_template/hooks/hooks.json must be Gemini format."""
        with open(GEMINI_HOOKS_JSON, encoding="utf-8") as f:
            data = json.load(f)
        hooks = data.get("hooks", {})
        assert "BeforeTool" in hooks, (
            f"Gemini hooks template should use BeforeTool at {GEMINI_HOOKS_JSON}"
        )
        assert "PreToolUse" not in hooks, (
            "Gemini hooks template should NOT use Claude event PreToolUse"
        )

    def test_claude_hooks_json_is_claude_format(self):
        """plugins/autorun/hooks/hooks.json must be Claude format."""
        with open(HOOKS_JSON, encoding="utf-8") as f:
            data = json.load(f)
        hooks = data.get("hooks", {})
        assert "PreToolUse" in hooks, (
            f"Claude hooks should use PreToolUse at {HOOKS_JSON}"
        )
        assert "BeforeTool" not in hooks, (
            "Claude hooks should NOT use Gemini event BeforeTool"
        )

    def test_no_swap_logic_in_installer(self):
        """Installer should not need swap logic."""
        content = INSTALL_PY.read_text(encoding="utf-8")
        assert "hooks.json.claude-backup" not in content
        # Legacy helper deleted as part of template refactor.
        assert "_clean_cross_cli_hooks" not in content, (
            "_clean_cross_cli_hooks should be fully removed; no references allowed"
        )

    def test_both_hooks_files_coexist(self):
        """Each CLI's hooks live in its own root — no shared directory."""
        assert HOOKS_JSON.exists(), f"Claude hooks missing at {HOOKS_JSON}"
        assert GEMINI_HOOKS_JSON.exists(), (
            f"Gemini hooks template missing at {GEMINI_HOOKS_JSON}"
        )


# =============================================================================
# Test Class 7: Gemini installation pathway
# =============================================================================


class TestGeminiInstallPathway:
    """Test Gemini-specific installation code in install.py."""

    def test_install_for_gemini_function_exists(self):
        content = INSTALL_PY.read_text(encoding="utf-8")
        assert "def _install_for_gemini(" in content

    def test_install_for_gemini_checks_cli(self):
        content = INSTALL_PY.read_text(encoding="utf-8")
        assert 'shutil.which("gemini")' in content

    def test_install_for_gemini_finds_all_plugins(self):
        content = INSTALL_PY.read_text(encoding="utf-8")
        assert "gemini-extension.json" in content

    def test_install_for_gemini_uses_consent_flag(self):
        content = INSTALL_PY.read_text(encoding="utf-8")
        assert '"--consent"' in content

    def test_gemini_extension_json_has_required_fields(self):
        with open(GEMINI_EXT_JSON, encoding="utf-8") as f:
            manifest = json.load(f)
        for field in ["name", "version", "description"]:
            assert field in manifest


# =============================================================================
# Test Class 8: Claude installation pathway
# =============================================================================


class TestClaudeInstallPathway:
    """Test Claude Code-specific installation code in install.py."""

    def test_install_uses_marketplace_add(self):
        content = INSTALL_PY.read_text(encoding="utf-8")
        assert '"claude", "plugin", "marketplace", "add"' in content

    def test_install_uses_plugin_install(self):
        content = INSTALL_PY.read_text(encoding="utf-8")
        assert '"claude", "plugin", "install"' in content

    def test_install_uses_plugin_enable(self):
        content = INSTALL_PY.read_text(encoding="utf-8")
        assert '"claude", "plugin", "enable"' in content

    def test_install_tries_update_first(self):
        content = INSTALL_PY.read_text(encoding="utf-8")
        update_pos = content.find('"claude", "plugin", "update"')
        install_pos = content.find('"claude", "plugin", "install"')
        assert update_pos > 0 and install_pos > 0
        assert update_pos < install_pos

    def test_install_has_cache_fallback(self):
        content = INSTALL_PY.read_text(encoding="utf-8")
        assert "_install_to_cache" in content

    def test_install_has_json_registration_fallback(self):
        content = INSTALL_PY.read_text(encoding="utf-8")
        assert "_register_in_json" in content

    def test_install_substitutes_paths(self):
        content = INSTALL_PY.read_text(encoding="utf-8")
        assert "_substitute_paths" in content


# =============================================================================
# Test Class 9: Daemon continue field (PreToolUse blocking)
# =============================================================================


class TestDaemonContinueField:
    """Validate daemon path correctly sets continue=True for tool denial."""

    def test_core_py_pretooluse_deny_keeps_ai_working(self):
        content = CORE_PY.read_text(encoding="utf-8")
        respond_block = _extract_function(content, "respond")
        assert respond_block, "Could not find def respond() in core.py"
        # Verify continue: True is used even on denial to keep AI loop running
        assert '"continue": True' in respond_block
        assert "keep AI working" in respond_block or "loop keeps running" in respond_block

    def test_plugins_py_enforce_file_policy_deny_keeps_ai_working(self):
        """Verify enforce_file_policy in plugins.py keeps AI working on denial."""
        plugins_py = PLUGIN_ROOT / "src" / "autorun" / "plugins.py"
        content = plugins_py.read_text(encoding="utf-8")
        func_block = _extract_function(content, "enforce_file_policy")
        assert func_block, "Could not find def enforce_file_policy() in plugins.py"
        # Verify the function exists and delegates to ctx.deny() which uses continue: True
        assert "enforce_file_policy" in func_block
        # The response format (continue: True) is in core.py EventContext.respond()
        core_content = CORE_PY.read_text(encoding="utf-8")
        respond_block = _extract_function(core_content, "respond")
        assert '"continue": True' in respond_block, \
            "core.py EventContext.respond() must use continue: True to keep AI working"


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
        result = _pretooluse(ctx)
        assert result is not None
        # continue should be True to keep AI working even if tool is blocked
        assert result.get("continue") is True

    def test_rm_blocked_mentions_trash(self):
        ctx = self._create_test_context("Bash", {"command": "rm important.txt"})
        result = _pretooluse(ctx)
        reason = result.get("reason", "") or result.get("stopReason", "")
        hook_output = result.get("hookSpecificOutput", {})
        full_reason = reason + str(hook_output.get("permissionDecisionReason", ""))
        assert "trash" in full_reason.lower()

    def test_safe_commands_allowed(self):
        for cmd in ["ls -la", "pwd", "echo hello", "git status"]:
            ctx = self._create_test_context("Bash", {"command": cmd})
            result = _pretooluse(ctx)
            if result is not None:
                assert result.get("continue") is not False, \
                    f"Safe command '{cmd}' should not be blocked"

    def test_cat_blocked(self):
        ctx = self._create_test_context("Bash", {"command": "cat /etc/passwd"})
        result = _pretooluse(ctx)
        assert result is not None
        assert result.get("continue") is True

    def test_gemini_tool_name_run_shell_command(self):
        ctx = self._create_test_context("run_shell_command", {"command": "rm file.txt"})
        result = _pretooluse(ctx)
        if result is not None:
            assert result.get("continue") is True

    def test_gemini_tool_name_bash_command(self):
        ctx = self._create_test_context("bash_command", {"command": "rm file.txt"})
        result = _pretooluse(ctx)
        if result is not None:
            assert result.get("continue") is True


# =============================================================================
# Test Class 11: Build directory sync
# =============================================================================


class TestBuildDirectorySync:
    """Verify build directory hooks match source hooks.

    setuptools creates build/lib/autorun/ during 'uv build' or 'pip install'.
    If package-data config is wrong, hooks in the build artifact may be stale
    or missing entirely. These tests guard against that.
    """

    def test_build_hooks_json_matches_source(self):
        build_hooks = PLUGIN_ROOT / "build" / "hooks" / "claude-hooks.json"
        if not build_hooks.exists():
            pytest.skip("Build directory not present (run 'uv build' first)")
        assert load_hooks_json(HOOKS_JSON) == load_hooks_json(build_hooks)

    def test_build_gemini_hooks_matches_source(self):
        build_gemini = PLUGIN_ROOT / "build" / "hooks" / "hooks.json"
        if not build_gemini.exists():
            pytest.skip("Build directory not present (run 'uv build' first)")
        assert load_hooks_json(GEMINI_HOOKS_JSON) == load_hooks_json(build_gemini)

    def test_build_hook_entry_matches_source(self):
        build_entry = PLUGIN_ROOT / "build" / "hooks" / "hook_entry.py"
        if not build_entry.exists():
            pytest.skip("Build directory not present (run 'uv build' first)")
        assert HOOK_ENTRY_PY.read_text(encoding="utf-8") == build_entry.read_text(encoding="utf-8")


class TestDeployedCopiesMatchSource:
    """Verify deployed copies (Claude cache, Gemini extension) match source.

    install.py uses shutil.copytree to deploy to:
    - Claude cache: ~/.claude/plugins/cache/autorun/ar/<version>/
    - Gemini extension: ~/.gemini/extensions/ar/

    These tests verify the deployed copies stay in sync with source after install.
    """

    def test_claude_cache_hooks_match_source(self):
        """Claude plugin cache hooks must have same events as source after install."""
        cache_hooks = Path.home() / ".claude/plugins/cache/autorun/ar"
        if not cache_hooks.exists():
            pytest.skip("Claude plugin cache not installed")
        # Find highest version
        versions = sorted(
            [d for d in cache_hooks.iterdir() if d.is_dir()],
            key=lambda p: p.name,
            reverse=True,
        )
        if not versions:
            pytest.skip("No version directories in cache")
        cached = versions[0] / "hooks" / "claude-hooks.json"
        if not cached.exists():
            pytest.skip("No claude-hooks.json in cache")
        # Cache may be RTK-patched (Bash removed from matcher), so compare
        # event structure rather than exact content.
        import json
        source = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        cache = json.loads(cached.read_text(encoding="utf-8"))
        assert set(source["hooks"].keys()) == set(cache["hooks"].keys()), (
            f"Cache events don't match source. "
            f"Source: {set(source['hooks'].keys())} "
            f"Cache: {set(cache['hooks'].keys())}"
        )

    def test_claude_cache_hook_entry_matches_source(self):
        """Claude plugin cache hook_entry.py must match source."""
        cache_hooks = Path.home() / ".claude/plugins/cache/autorun/ar"
        if not cache_hooks.exists():
            pytest.skip("Claude plugin cache not installed")
        versions = sorted(
            [d for d in cache_hooks.iterdir() if d.is_dir()],
            key=lambda p: p.name,
            reverse=True,
        )
        if not versions:
            pytest.skip("No version directories in cache")
        cached_entry = versions[0] / "hooks" / "hook_entry.py"
        if not cached_entry.exists():
            pytest.skip("No hook_entry.py in cache")
        assert HOOK_ENTRY_PY.read_text(encoding="utf-8") == cached_entry.read_text(encoding="utf-8"), (
            "Cache hook_entry.py doesn't match source. "
            "Run: uv run --project plugins/autorun python -m autorun --install --force"
        )

    def test_gemini_extension_hooks_match_source(self):
        """Gemini extension hooks.json must match source after install."""
        ext_hooks = Path.home() / ".gemini/extensions/ar/hooks/hooks.json"
        if not ext_hooks.exists():
            pytest.skip("Gemini extension not installed")
        source_content = GEMINI_HOOKS_JSON.read_text(encoding="utf-8")
        ext_content = ext_hooks.read_text(encoding="utf-8")
        assert source_content == ext_content, (
            "Gemini extension hooks.json doesn't match source. "
            "Run: uv run --project plugins/autorun python -m autorun --install --force"
        )

    def test_gemini_extension_hook_entry_matches_source(self):
        """Gemini extension hook_entry.py must match source after install."""
        ext_entry = Path.home() / ".gemini/extensions/ar/hooks/hook_entry.py"
        if not ext_entry.exists():
            pytest.skip("Gemini extension not installed")
        source_content = HOOK_ENTRY_PY.read_text(encoding="utf-8")
        ext_content = ext_entry.read_text(encoding="utf-8")
        assert source_content == ext_content, (
            "Gemini extension hook_entry.py doesn't match source. "
            "Run: uv run --project plugins/autorun python -m autorun --install --force"
        )


# =============================================================================
# Test Class 12: Install pathway detection and routing
# =============================================================================


class TestInstallPathwayDetection:
    """Test install.py correctly detects and routes to the right pathway."""

    def test_install_detects_claude_cli(self):
        content = INSTALL_PY.read_text(encoding="utf-8")
        assert 'shutil.which("claude")' in content

    def test_install_detects_gemini_cli(self):
        content = INSTALL_PY.read_text(encoding="utf-8")
        assert 'shutil.which("gemini")' in content

    def test_install_has_claude_only_flag(self):
        content = INSTALL_PY.read_text(encoding="utf-8")
        assert "claude_only" in content

    def test_install_has_gemini_only_flag(self):
        content = INSTALL_PY.read_text(encoding="utf-8")
        assert "gemini_only" in content

    def test_install_default_installs_both(self):
        content = INSTALL_PY.read_text(encoding="utf-8")
        assert "_install_for_gemini" in content
        assert '"claude", "plugin"' in content

    def test_install_does_not_expose_aix_flags(self):
        content = INSTALL_PY.read_text(encoding="utf-8")
        assert "--aix" not in content
        assert "--no-aix" not in content


# =============================================================================
# Test Class 13: hook_entry.py bootstrap
# =============================================================================


class TestHookEntryBootstrap:
    """Verify hook_entry.py bootstrap logic is intact."""

    def test_can_bootstrap_checks_python_version(self):
        content = HOOK_ENTRY_PY.read_text(encoding="utf-8")
        assert "sys.version_info" in content

    def test_can_bootstrap_checks_uv_or_pip(self):
        content = HOOK_ENTRY_PY.read_text(encoding="utf-8")
        assert 'shutil.which("uv")' in content

    def test_bootstrap_uses_lockfile(self):
        content = HOOK_ENTRY_PY.read_text(encoding="utf-8")
        assert "BOOTSTRAP_LOCKFILE" in content

    def test_bootstrap_spawns_background(self):
        content = HOOK_ENTRY_PY.read_text(encoding="utf-8")
        assert "nohup" in content
        assert "start_new_session" in content


# =============================================================================
# Test Class 14: _install_for_gemini marketplace.json source-field resolution
# =============================================================================


class TestInstallForGeminiMarketplaceResolution:
    """Functional tests for Strategy 0: marketplace.json source-field resolution.

    Regression tests for the bug where _install_for_gemini() searched for a
    directory literally named after the plugin logical name (e.g. "ar") but the
    actual directory was named differently (e.g. "autorun").

    Covers: plugins/autorun/src/autorun/install.py lines 1004-1021, 1028-1031.
    """

    def _make_plugin_dir(self, base: Path, dirname: str, ext_name: str) -> Path:
        """Create a minimal plugin directory with gemini-extension.json."""
        plugin_dir = base / dirname
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "gemini-extension.json").write_text(
            json.dumps({"name": ext_name, "version": "1.0.0", "description": "test"})
        )
        return plugin_dir

    def _make_marketplace(self, root: Path, plugins: list[dict]) -> Path:
        """Create .claude-plugin/marketplace.json with given plugin entries."""
        claude_plugin_dir = root / ".claude-plugin"
        claude_plugin_dir.mkdir(parents=True, exist_ok=True)
        marketplace = {
            "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
            "name": "test-marketplace",
            "version": "1.0.0",
            "description": "test",
            "plugins": plugins,
        }
        mp_path = claude_plugin_dir / "marketplace.json"
        mp_path.write_text(json.dumps(marketplace))
        return mp_path

    def _run(self, tmp: Path, plugins: list[str], gemini_ok: bool = True):
        """Call _install_for_gemini with all external calls mocked."""
        import autorun.install as install_mod

        gemini_dir = tmp / ".gemini"
        gemini_dir.mkdir(exist_ok=True)

        mock_result = MagicMock()
        mock_result.ok = gemini_ok
        mock_result.has_text = MagicMock(return_value=False)

        with (
            patch.object(install_mod.shutil, "which", return_value="/usr/bin/gemini"),
            patch("autorun.install.Path.home", return_value=tmp),
            patch.object(install_mod, "run_cmd", return_value=mock_result),
        ):
            return install_mod._install_for_gemini(tmp, plugins)

    # ------------------------------------------------------------------
    # Strategy 0: marketplace.json source-field resolution
    # ------------------------------------------------------------------

    def test_name_differs_from_dir_resolved_via_marketplace_json(self, tmp_path):
        """Core regression: name='ar' but dir='autorun' — must install correctly."""
        self._make_plugin_dir(tmp_path / "plugins", "autorun", "ar")
        self._make_marketplace(tmp_path, [
            {"name": "ar", "source": "./plugins/autorun"},
        ])
        success, msg = self._run(tmp_path, ["ar"])
        assert success, f"Expected success but got: {msg}"

    def test_name_matches_dir_still_works(self, tmp_path):
        """Traditional case: name='pdf-extractor' dir='pdf-extractor' — unaffected."""
        self._make_plugin_dir(tmp_path / "plugins", "pdf-extractor", "pdf-extractor")
        self._make_marketplace(tmp_path, [
            {"name": "pdf-extractor", "source": "./plugins/pdf-extractor"},
        ])
        success, msg = self._run(tmp_path, ["pdf-extractor"])
        assert success, f"Expected success but got: {msg}"

    def test_multiple_plugins_all_resolved(self, tmp_path):
        """Both ar and pdf-extractor resolved via marketplace.json in one call."""
        self._make_plugin_dir(tmp_path / "plugins", "autorun", "ar")
        self._make_plugin_dir(tmp_path / "plugins", "pdf-extractor", "pdf-extractor")
        self._make_marketplace(tmp_path, [
            {"name": "ar", "source": "./plugins/autorun"},
            {"name": "pdf-extractor", "source": "./plugins/pdf-extractor"},
        ])
        success, msg = self._run(tmp_path, ["ar", "pdf-extractor"])
        assert success, f"Expected success but got: {msg}"

    # ------------------------------------------------------------------
    # Edge cases: marketplace.json missing / malformed / incomplete
    # ------------------------------------------------------------------

    def test_missing_marketplace_json_falls_through_to_strategy1(self, tmp_path):
        """No marketplace.json → falls through to strategy 1 (plugins/<name> lookup)."""
        # Plugin dir name matches logical name → strategy 1 finds it
        self._make_plugin_dir(tmp_path / "plugins", "pdf-extractor", "pdf-extractor")
        # No marketplace.json created
        success, msg = self._run(tmp_path, ["pdf-extractor"])
        assert success, f"Expected strategy 1 fallback to succeed but got: {msg}"

    def test_malformed_marketplace_json_falls_through(self, tmp_path):
        """Malformed JSON → exception caught, falls through to strategy 1."""
        self._make_plugin_dir(tmp_path / "plugins", "pdf-extractor", "pdf-extractor")
        claude_plugin_dir = tmp_path / ".claude-plugin"
        claude_plugin_dir.mkdir()
        (claude_plugin_dir / "marketplace.json").write_text("{not valid json}")
        success, msg = self._run(tmp_path, ["pdf-extractor"])
        assert success, f"Expected graceful fallback but got: {msg}"

    def test_source_dir_missing_skipped_silently(self, tmp_path):
        """source points to non-existent dir → skipped, fallback to strategy 1."""
        self._make_plugin_dir(tmp_path / "plugins", "pdf-extractor", "pdf-extractor")
        self._make_marketplace(tmp_path, [
            {"name": "ar", "source": "./plugins/does-not-exist"},
            {"name": "pdf-extractor", "source": "./plugins/pdf-extractor"},
        ])
        # ar will fail (missing dir); pdf-extractor should still succeed
        success, msg = self._run(tmp_path, ["pdf-extractor"])
        assert success, f"Expected pdf-extractor to succeed but got: {msg}"

    def test_source_dir_no_gemini_extension_json_skipped(self, tmp_path):
        """source dir exists but has no gemini-extension.json → excluded from map."""
        bad_dir = tmp_path / "plugins" / "autorun"
        bad_dir.mkdir(parents=True)
        # No gemini-extension.json in bad_dir
        self._make_plugin_dir(tmp_path / "plugins", "pdf-extractor", "pdf-extractor")
        self._make_marketplace(tmp_path, [
            {"name": "ar", "source": "./plugins/autorun"},
            {"name": "pdf-extractor", "source": "./plugins/pdf-extractor"},
        ])
        success, msg = self._run(tmp_path, ["pdf-extractor"])
        assert success, f"Expected pdf-extractor to succeed but got: {msg}"

    def test_empty_name_field_skipped(self, tmp_path):
        """marketplace.json entry with empty name → skipped without crash."""
        self._make_plugin_dir(tmp_path / "plugins", "pdf-extractor", "pdf-extractor")
        self._make_marketplace(tmp_path, [
            {"name": "", "source": "./plugins/autorun"},
            {"name": "pdf-extractor", "source": "./plugins/pdf-extractor"},
        ])
        success, msg = self._run(tmp_path, ["pdf-extractor"])
        assert success, f"Expected pdf-extractor to succeed but got: {msg}"

    def test_empty_source_field_skipped(self, tmp_path):
        """marketplace.json entry with empty source → skipped without crash."""
        self._make_plugin_dir(tmp_path / "plugins", "pdf-extractor", "pdf-extractor")
        self._make_marketplace(tmp_path, [
            {"name": "ar", "source": ""},
            {"name": "pdf-extractor", "source": "./plugins/pdf-extractor"},
        ])
        success, msg = self._run(tmp_path, ["pdf-extractor"])
        assert success, f"Expected pdf-extractor to succeed but got: {msg}"

    def test_no_plugins_found_returns_false(self, tmp_path):
        """Plugin requested but not found anywhere → returns False."""
        # marketplace.json exists but doesn't include requested plugin
        self._make_marketplace(tmp_path, [])
        success, msg = self._run(tmp_path, ["nonexistent-plugin"])
        assert not success

    # ------------------------------------------------------------------
    # Regression: hardcoded "cr, pdf-extractor" success message removed
    # ------------------------------------------------------------------

    def test_no_hardcoded_cr_pdf_extractor_in_success_message(self):
        """Verify the old hardcoded 'cr, pdf-extractor' string was removed."""
        content = INSTALL_PY.read_text(encoding="utf-8")
        assert "cr, pdf-extractor" not in content, (
            "Hardcoded 'cr, pdf-extractor' found in install.py — "
            "success message must use the actual plugins list"
        )

    def test_success_message_uses_join(self):
        """Verify Gemini success message uses ', '.join(plugins) not a literal string."""
        content = INSTALL_PY.read_text(encoding="utf-8")
        assert "', '.join(plugins)" in content

    # ------------------------------------------------------------------
    # Source-code level: new strategy 0 logic exists
    # ------------------------------------------------------------------

    def test_strategy_0_marketplace_source_map_built(self):
        """Source code contains marketplace_source_map construction."""
        content = INSTALL_PY.read_text(encoding="utf-8")
        assert "marketplace_source_map" in content

    def test_strategy_0_reads_source_field(self):
        """Source code reads 'source' field from marketplace.json entries."""
        content = INSTALL_PY.read_text(encoding="utf-8")
        assert '_entry.get("source", "")' in content

    def test_strategy_0_checks_gemini_extension_json(self):
        """Strategy 0 verifies gemini-extension.json exists before adding to map."""
        content = INSTALL_PY.read_text(encoding="utf-8")
        func = _extract_function(content, "_install_gemini_family_extensions")
        # Both the marketplace_source_map building and the per-plugin loop check it
        assert func.count("gemini-extension.json") >= 2


class TestGeminiExtensionResourceSync:
    """Regression coverage for resources materialized after Gemini install."""

    def _make_plugin(self, root: Path) -> Path:
        plugin_dir = root / "plugins" / "autorun"
        (plugin_dir / "hooks").mkdir(parents=True)
        (plugin_dir / "commands").mkdir()
        (plugin_dir / "skills" / "cache").mkdir(parents=True)
        (plugin_dir / "hooks" / "hook_entry.py").write_text("print('hook')\n", encoding="utf-8")
        (plugin_dir / "commands" / "status.md").write_text(
            "---\ndescription: Show status\n---\nShow status for $ARGUMENTS\n",
            encoding="utf-8",
        )
        (plugin_dir / "commands" / "status.md.tmp").write_text(
            "temporary editor artifact\n",
            encoding="utf-8",
        )
        (plugin_dir / "commands" / "__pycache__").mkdir()
        (plugin_dir / "commands" / "__pycache__" / "status.pyc").write_bytes(b"bytecode")
        (plugin_dir / "skills" / "cache" / "SKILL.md").write_text(
            "# Cache\n\nCache instructions.\n",
            encoding="utf-8",
        )
        (plugin_dir / "skills" / "cache" / "__pycache__").mkdir()
        (plugin_dir / "skills" / "cache" / "__pycache__" / "cache.pyc").write_bytes(b"bytecode")
        return plugin_dir

    def test_sync_installs_hooks_commands_toml_and_skills(self, tmp_path):
        import autorun.install as install_mod

        plugin_dir = self._make_plugin(tmp_path)
        ext_dir = tmp_path / ".gemini" / "extensions" / "ar"
        ext_dir.mkdir(parents=True)

        commands_generated, skills_synced = install_mod._sync_gemini_extension_resources(
            plugin_dir,
            ext_dir,
            "ar",
        )

        assert commands_generated == 1
        assert skills_synced == 1
        assert (ext_dir / "hooks" / "hook_entry.py").read_text(encoding="utf-8") == "print('hook')\n"
        assert (ext_dir / "commands" / "status.md").is_file()
        toml = (ext_dir / "commands" / "ar" / "status.toml").read_text(encoding="utf-8")
        assert 'description = "Show status"' in toml
        assert "{{args}}" in toml
        assert (ext_dir / "skills" / "cache" / "SKILL.md").read_text(encoding="utf-8").startswith("# Cache")
        assert not (ext_dir / "commands" / "status.md.tmp").exists()
        assert not (ext_dir / "commands" / "__pycache__").exists()
        assert not (ext_dir / "skills" / "cache" / "__pycache__").exists()


class TestClaudeCachePathSubstitution:
    """Regression coverage for local Claude marketplace cache path substitution."""

    def _make_marketplace(self, root: Path, plugins: list[dict]) -> None:
        claude_plugin_dir = root / ".claude-plugin"
        claude_plugin_dir.mkdir(parents=True, exist_ok=True)
        (claude_plugin_dir / "marketplace.json").write_text(
            json.dumps({
                "name": "autorun",
                "version": "1.0.0",
                "plugins": plugins,
            })
        )

    def _make_plugin_source(self, root: Path, dirname: str, version: str) -> Path:
        plugin_dir = root / "plugins" / dirname
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": dirname, "version": version})
        )
        return plugin_dir

    def _make_cached_hooks(self, home: Path, plugin_name: str, version: str) -> Path:
        cache_dir = (
            home
            / ".claude"
            / "plugins"
            / "cache"
            / "autorun"
            / plugin_name
            / version
        )
        hooks_dir = cache_dir / "hooks"
        hooks_dir.mkdir(parents=True)
        # Legacy cached hook command: this fixture intentionally predates
        # --no-sync so cache path substitution is tested against old installs.
        (hooks_dir / "hooks.json").write_text(
            json.dumps({
                "hooks": {
                    "PreToolUse": [{
                        "hooks": [{
                            "type": "command",
                            "command": (
                                "uv run --quiet --project ${CLAUDE_PLUGIN_ROOT} "
                                "python ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py "
                                "--cli claude"
                            ),
                        }]
                    }]
                }
            })
        )
        return cache_dir

    def test_substitutes_cache_for_alias_resolved_by_marketplace_json(self, tmp_path):
        """Logical plugin name ar maps to source dir plugins/autorun."""
        import autorun.install as install_mod

        self._make_plugin_source(tmp_path, "autorun", "9.8.7")
        self._make_marketplace(tmp_path, [
            {"name": "ar", "source": "./plugins/autorun"},
        ])
        cache_dir = self._make_cached_hooks(tmp_path, "ar", "9.8.7")

        with patch("autorun.install.Path.home", return_value=tmp_path):
            assert install_mod._substitute_claude_cache_paths(tmp_path, "ar")

        hooks_text = (cache_dir / "hooks" / "hooks.json").read_text()
        assert "${CLAUDE_PLUGIN_ROOT}" not in hooks_text
        assert str(cache_dir.resolve()) in hooks_text

    def test_substitutes_existing_cache_versions_when_source_lookup_fails(self, tmp_path):
        """Already-cached hooks are still repaired if the source tree moved."""
        import autorun.install as install_mod

        self._make_marketplace(tmp_path, [])
        cache_dir = self._make_cached_hooks(tmp_path, "ar", "1.2.3")

        with patch("autorun.install.Path.home", return_value=tmp_path):
            assert install_mod._substitute_claude_cache_paths(tmp_path, "ar")

        hooks_text = (cache_dir / "hooks" / "hooks.json").read_text()
        assert "${CLAUDE_PLUGIN_ROOT}" not in hooks_text
        assert str(cache_dir.resolve()) in hooks_text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
