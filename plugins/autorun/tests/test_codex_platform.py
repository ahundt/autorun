"""Tests for Codex CLI platform support (v0.11.0 / C3).

Codex shares Claude Code's hook contract for events and file/shell tools:
same event names, same strict JSON schema (additionalProperties:false). The
divergences captured here:
  - schema_type: still "strict" but no exit-2 workaround required
  - additionalContext NOT dropped (Bug #18534 is Claude-only)
  - env vars CODEX_SESSION_ID + CODEX_PROJECT_DIR (no env'd session id in 0.133;
    we still test "set the env var → detect codex" so future versions stay covered)
  - task progress: native update_plan checklist, not Claude TaskCreate/TaskUpdate
"""
from __future__ import annotations

from autorun.platforms import PLATFORMS, get_platform


# ─── Registry presence ────────────────────────────────────────────────────────

def test_codex_platform_registered():
    p = get_platform("codex")
    assert p is not None
    assert p.name == "codex"
    assert p.display_name == "Codex CLI"
    assert p.binary == "codex"


def test_codex_supports_hooks():
    assert PLATFORMS["codex"].has_hooks is True


def test_codex_uses_strict_schema():
    """Codex uses additionalProperties:false on hook responses (verified per
    v0.133 binary HookEventNameWire enum + schema)."""
    assert PLATFORMS["codex"].schema_type == "strict"


def test_codex_has_no_exit2_workaround():
    """Codex returns exit 0 + JSON deny natively — no exit-2 dance needed."""
    assert PLATFORMS["codex"].has_exit2_workaround is False


def test_codex_does_not_drop_additional_context():
    """Bug #18534 is Claude-only; Codex's hookSpecificOutput.additionalContext
    is delivered correctly."""
    assert PLATFORMS["codex"].drops_additional_context is False


# ─── Detection ────────────────────────────────────────────────────────────────

def test_codex_detected_via_explicit_cli_type():
    from autorun.config import detect_cli_type
    assert detect_cli_type({"cli_type": "codex"}) == "codex"
    assert detect_cli_type({"source": "codex"}) == "codex"


def test_explicit_cli_env_overrides_ambient_platform_hints(monkeypatch):
    from autorun.config import detect_cli_type

    monkeypatch.setenv("AUTORUN_CLI_TYPE", "codex")
    monkeypatch.setenv("GEMINI_CLI", "1")
    assert detect_cli_type({"sessionId": "ambient-gemini-session"}) == "codex"


def test_codex_detected_via_session_id_env(monkeypatch):
    for k in ("GEMINI_SESSION_ID", "GEMINI_PROJECT_DIR", "GEMINI_CLI",
              "FORGE_CONFIG"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CODEX_SESSION_ID", "codex-abc")
    from autorun.config import detect_cli_type
    assert detect_cli_type() == "codex"


def test_codex_detected_via_payload_session_id(monkeypatch):
    for k in ("GEMINI_SESSION_ID", "GEMINI_PROJECT_DIR", "GEMINI_CLI",
              "CODEX_SESSION_ID", "FORGE_CONFIG"):
        monkeypatch.delenv(k, raising=False)
    from autorun.config import detect_cli_type
    assert detect_cli_type({"CODEX_SESSION_ID": "codex-xyz"}) == "codex"


def test_codex_detected_via_transcript_path(monkeypatch):
    for k in ("GEMINI_SESSION_ID", "GEMINI_PROJECT_DIR", "GEMINI_CLI",
              "CODEX_SESSION_ID", "FORGE_CONFIG"):
        monkeypatch.delenv(k, raising=False)
    from autorun.config import detect_cli_type
    assert detect_cli_type({"transcript_path": "/home/u/.codex/session.jsonl"}) == "codex"


# ─── Event names (identity with Claude) ───────────────────────────────────────

def test_codex_event_names_are_identity():
    """Codex uses the same canonical event names as Claude; the internal->cli
    map is identity for all in-use events."""
    p = PLATFORMS["codex"]
    for ev in ("PreToolUse", "PostToolUse", "Stop", "SessionStart",
               "SessionEnd", "UserPromptSubmit"):
        assert p.internal_to_cli_events.get(ev) == ev


def test_codex_get_cli_event_name_identity():
    from autorun.core import get_cli_event_name
    for ev in ("PreToolUse", "PostToolUse", "Stop", "SessionStart"):
        assert get_cli_event_name(ev, "codex") == ev


# ─── Tool names (same as Claude) ──────────────────────────────────────────────

def test_codex_tool_names_match_claude():
    """Codex file/shell API tool names match Claude (PascalCase)."""
    codex_tools = PLATFORMS["codex"].tool_names
    claude_tools = PLATFORMS["claude"].tool_names
    for key in ("grep", "glob", "read", "write", "edit", "bash"):
        assert codex_tools[key] == claude_tools[key], (
            f"Codex tool_names[{key!r}] must match Claude (verified per v0.133 hook spec)"
        )


def test_codex_task_progress_uses_update_plan():
    """Codex exposes task/checklist progress as update_plan, not Claude task tools."""
    p = PLATFORMS["codex"]
    assert p.task_management_style == "plan_checklist"
    assert p.task_plan_tools == frozenset({"update_plan"})
    assert p.tool_names["task_progress"] == "update_plan"
    assert "TaskCreate" not in p.task_create_tools


def test_codex_command_prefix_metadata_accepts_plain_prompt_forms():
    """Command spelling differences are platform data, not dispatch branches."""
    p = PLATFORMS["codex"]
    assert p.command_prefixes == ("/ar:", "ar:", "ar ")
    assert p.command_display_prefix == "ar:"


def test_claude_and_gemini_keep_native_slash_command_prefixes():
    assert PLATFORMS["claude"].command_prefixes == ("/ar:",)
    assert PLATFORMS["claude"].command_display_prefix == "/ar:"
    assert PLATFORMS["gemini"].command_prefixes == ("/ar:",)
    assert PLATFORMS["gemini"].command_display_prefix == "/ar:"


def test_codex_get_tool_names():
    from autorun.core import get_tool_names
    tools = get_tool_names("codex")
    assert tools["grep"] == "Grep"
    assert tools["bash"] == "Bash"
    assert tools["task_progress"] == "update_plan"


# ─── format_suggestion ────────────────────────────────────────────────────────

def test_codex_format_suggestion_uses_claude_tool_names():
    from autorun.core import format_suggestion
    assert format_suggestion("Use {grep} then {edit}", "codex") == "Use Grep then Edit"


def test_codex_formats_autorun_commands_with_platform_display_prefix():
    from autorun.core import canonicalize_command_prompt, format_command_for_cli, format_commands_for_cli

    assert canonicalize_command_prompt("ar:ok git push", "codex") == "/ar:ok git push"
    assert canonicalize_command_prompt("ar ok git push", "codex") == "/ar:ok git push"
    assert canonicalize_command_prompt("/ar:ok git push", "codex") == "/ar:ok git push"
    assert format_command_for_cli("/ar:task-ignore <id>", "codex") == "ar:task-ignore <id>"
    assert format_commands_for_cli("Try /ar:ok git push then /ar:st", "codex") == (
        "Try ar:ok git push then ar:st"
    )
    assert format_commands_for_cli("Try /ar:ok git push", "claude") == "Try /ar:ok git push"


# ─── Install metadata ─────────────────────────────────────────────────────────

def test_codex_install_metadata():
    p = PLATFORMS["codex"]
    assert p.config_dir == "~/.codex/"
    # User-level install at ~/.codex/hooks.json — no template directory needed
    assert p.template_dir is None
    # ${PLUGIN_ROOT} is Codex's primary path var; ${CLAUDE_PLUGIN_ROOT} is set
    # as a compat alias in the same environment.
    assert "PLUGIN_ROOT" in p.hooks_path_var
    assert p.install_fn_name == "_install_for_codex"


def test_codex_response_capabilities_are_not_claude_clone():
    """Codex has Claude-like event names but Codex-specific output schemas."""
    p = PLATFORMS["codex"]
    assert p.normal_allow_decision is None
    assert p.block_decision == "block"
    assert "UserPromptSubmit" in p.supports_additional_context_events
    assert "continue" in p.unsupported_response_fields_by_event["PreToolUse"]
    assert "permissionDecision" in p.unsupported_response_fields_by_event["PreToolUse"]


# ─── _bug_18534_human_channels: Codex should NOT trigger workaround ───────────

def test_codex_does_not_trigger_bug_18534_workaround(monkeypatch):
    """Codex's additionalContext works correctly; the workaround that upgrades
    channel='ai' to also reach systemMessage is Claude-only."""
    monkeypatch.delenv("AUTORUN_BUG_CLAUDE_CODE_IGNORES_ADDITIONAL_CONTEXT_JSON_ENTRY_BUG_18534_WORKAROUND_ENABLED", raising=False)
    from autorun.core import _bug_18534_human_channels
    chans = _bug_18534_human_channels("codex")
    assert "ai" not in chans
    assert chans == {"human", "both"}


# ─── hook_entry --cli codex acceptance ────────────────────────────────────────

def test_hook_entry_detect_cli_type_supports_codex(monkeypatch):
    """The hook_entry.detect_cli_type function recognises codex via env+arg."""
    # Import via path to avoid module collision with autorun.config.detect_cli_type
    import importlib.util
    from pathlib import Path
    he_path = Path(__file__).parent.parent / "hooks" / "hook_entry.py"
    spec = importlib.util.spec_from_file_location("autorun_hook_entry_for_test", he_path)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setattr("sys.argv", ["hook_entry.py", "--cli", "codex"])
    monkeypatch.delenv("GEMINI_SESSION_ID", raising=False)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    monkeypatch.delenv("FORGE_CONFIG", raising=False)
    spec.loader.exec_module(mod)
    assert mod.detect_cli_type() == "codex"
