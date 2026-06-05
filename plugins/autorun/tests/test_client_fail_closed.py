#!/usr/bin/env python3
"""Client fallback behavior for daemon failures.

Permission-gate hooks must never fail open when the daemon is slow, missing,
or returns invalid data. Lifecycle/context events may continue permissively.
"""

from autorun.client import (
    build_daemon_failure_response,
    is_tool_gate_event,
    prepare_payload_for_daemon,
)


def test_client_recognizes_all_supported_tool_gate_events():
    assert is_tool_gate_event("PreToolUse") is True
    assert is_tool_gate_event("BeforeTool") is True
    assert is_tool_gate_event("PermissionRequest") is True
    assert is_tool_gate_event("SessionStart") is False
    assert is_tool_gate_event("UserPromptSubmit") is False


def test_client_forwards_explicit_cli_type_to_daemon(monkeypatch):
    """Direct `autorun --cli codex` must not let ambient Gemini env win later."""
    monkeypatch.setenv("AUTORUN_CLI_TYPE", "codex")
    monkeypatch.setenv("GEMINI_CLI", "1")
    monkeypatch.setattr("autorun.client.get_stable_pid", lambda: 12345)

    payload, cli_type = prepare_payload_for_daemon({
        "hook_event_name": "PreToolUse",
        "session_id": "client-cli-type",
        "tool_name": "Bash",
        "tool_input": {"command": "rm file"},
    })

    assert cli_type == "codex"
    assert payload["cli_type"] == "codex"
    assert payload["_pid"] == 12345
    assert "_cwd" in payload


def test_claude_daemon_failure_on_pretooluse_fails_closed():
    response = build_daemon_failure_response(
        "PreToolUse", "claude", "Daemon response timed out"
    )

    assert response["decision"] == "block"
    assert response["permissionDecision"] == "deny"
    assert response["continue"] is True
    assert response["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "timed out" in response["hookSpecificOutput"]["permissionDecisionReason"]


def test_gemini_daemon_failure_on_beforetool_fails_closed():
    response = build_daemon_failure_response(
        "BeforeTool", "gemini", "Daemon response timed out"
    )

    assert response["decision"] == "deny"
    assert response["continue"] is True
    assert response["hookSpecificOutput"]["hookEventName"] == "BeforeTool"
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "timed out" in response["reason"]


def test_codex_daemon_failure_on_pretooluse_uses_codex_block_schema():
    response = build_daemon_failure_response(
        "PreToolUse", "codex", "Daemon response timed out"
    )

    assert response["decision"] == "block"
    assert response["reason"]
    assert response["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "continue" not in response
    assert "stopReason" not in response
    assert "suppressOutput" not in response
    assert "permissionDecision" not in response


def test_lifecycle_daemon_failure_stays_fail_open():
    response = build_daemon_failure_response(
        "SessionStart", "claude", "Daemon response timed out"
    )

    assert response["continue"] is True
    assert "decision" not in response
    assert "hookSpecificOutput" not in response
