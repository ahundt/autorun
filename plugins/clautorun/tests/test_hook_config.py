"""
Tests that hook configuration files (claude-hooks.json and hooks.json) have:
1. Correct timeout values (Claude uses seconds, Gemini uses milliseconds)
2. Gemini BeforeTool matcher includes exit_plan_mode for PreToolUse backup

Source of timeout unit truth:
  notes/hooks_api_reference.md:825 — Claude Code timeout is in SECONDS
  notes/hooks_api_reference.md:857 — Gemini CLI timeout is in MILLISECONDS

Timeout requirements:
  Claude Code: timeout >= 5 (seconds) — daemon warmup can take ~2-3s
  Gemini CLI: timeout >= 5000 (ms = 5 seconds) — same effective requirement
"""
import json
from pathlib import Path


def _hooks_dir() -> Path:
    """Find hooks directory relative to this test file."""
    return Path(__file__).parent.parent / "hooks"


class TestHookTimeouts:
    """Verify hook timeout values are adequate for daemon warmup."""

    def test_claude_hooks_timeout_adequate(self):
        """claude-hooks.json timeout must be >= 5 seconds (Claude uses seconds unit)."""
        hooks_path = _hooks_dir() / "claude-hooks.json"
        hooks = json.loads(hooks_path.read_text())

        for event, handlers in hooks["hooks"].items():
            for handler_group in handlers:
                for hook in handler_group.get("hooks", []):
                    timeout = hook.get("timeout", 0)
                    assert timeout >= 5, (
                        f"claude-hooks.json {event} hook timeout={timeout}s too short. "
                        f"Need >= 5s for daemon warmup. "
                        f"Note: Claude Code timeout is in SECONDS (not ms)."
                    )

    def test_gemini_hooks_timeout_adequate(self):
        """hooks.json timeout must be >= 5000ms (Gemini uses milliseconds unit)."""
        hooks_path = _hooks_dir() / "hooks.json"
        hooks = json.loads(hooks_path.read_text())

        for event, handlers in hooks["hooks"].items():
            for handler_group in handlers:
                for hook in handler_group.get("hooks", []):
                    timeout = hook.get("timeout", 0)
                    assert timeout >= 5000, (
                        f"hooks.json {event} hook timeout={timeout}ms too short. "
                        f"Need >= 5000ms for daemon warmup. "
                        f"Note: Gemini CLI timeout is in MILLISECONDS (not seconds)."
                    )


class TestGeminiHookMatchers:
    """Verify Gemini hook matchers include all required tool names."""

    def test_gemini_before_tool_matcher_includes_exit_plan_mode(self):
        """Gemini BeforeTool must include exit_plan_mode for PreToolUse backup hook.

        Without exit_plan_mode in BeforeTool matcher, track_and_export_plans_early()
        never fires for Gemini ExitPlanMode calls. The primary AfterTool path still
        works, but the PreToolUse backup is disabled for Gemini.
        """
        hooks_path = _hooks_dir() / "hooks.json"
        hooks = json.loads(hooks_path.read_text())

        before_tool_hooks = hooks["hooks"].get("BeforeTool", [])
        assert len(before_tool_hooks) > 0, "No BeforeTool hooks registered in hooks.json"

        matcher = before_tool_hooks[0]["matcher"]
        assert "exit_plan_mode" in matcher, (
            f"hooks.json BeforeTool matcher missing 'exit_plan_mode'. "
            f"Current matcher: '{matcher}'. "
            f"Fix: add '|exit_plan_mode' to enable Gemini PreToolUse backup for plan export."
        )

    def test_claude_hooks_exit_plan_mode_in_pre_tool_use(self):
        """claude-hooks.json PreToolUse must include ExitPlanMode for backup export."""
        hooks_path = _hooks_dir() / "claude-hooks.json"
        hooks = json.loads(hooks_path.read_text())

        pre_tool_hooks = hooks["hooks"].get("PreToolUse", [])
        assert len(pre_tool_hooks) > 0, "No PreToolUse hooks in claude-hooks.json"

        # Find PreToolUse hook that handles ExitPlanMode
        matchers = [h["matcher"] for h in pre_tool_hooks if "matcher" in h]
        combined = "|".join(matchers)
        assert "ExitPlanMode" in combined, (
            f"claude-hooks.json PreToolUse missing ExitPlanMode. Matchers: {matchers}"
        )

    def test_claude_hooks_exit_plan_mode_in_post_tool_use(self):
        """claude-hooks.json PostToolUse must include ExitPlanMode for primary export."""
        hooks_path = _hooks_dir() / "claude-hooks.json"
        hooks = json.loads(hooks_path.read_text())

        post_tool_hooks = hooks["hooks"].get("PostToolUse", [])
        assert len(post_tool_hooks) > 0, "No PostToolUse hooks in claude-hooks.json"

        matchers = [h["matcher"] for h in post_tool_hooks if "matcher" in h]
        combined = "|".join(matchers)
        assert "ExitPlanMode" in combined, (
            f"claude-hooks.json PostToolUse missing ExitPlanMode. Matchers: {matchers}"
        )
