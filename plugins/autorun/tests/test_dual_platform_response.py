#!/usr/bin/env python3
"""TDD for platform-correct response logic in core.py."""

import pytest

from autorun.core import EventContext, normalize_hook_payload
from autorun.main import build_hook_response


HARNESSES = ("claude", "gemini", "qwen", "antigravity", "codex")


def _dual_pretool_response(
    root_decision,
    permission_decision,
    event_name,
    *,
    include_root_permission=False,
    hide_duplicate_reason=False,
):
    """Build the frozen Claude/Gemini golden shape, independently of HookProtocol."""
    response = {
        "decision": root_decision,
        "reason": "" if hide_duplicate_reason else "Reason",
        "continue": True,
        "stopReason": "",
        "suppressOutput": False,
        "systemMessage": "" if hide_duplicate_reason else "Reason",
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "permissionDecision": permission_decision,
            "permissionDecisionReason": "Reason",
        },
    }
    if include_root_permission:
        response["permissionDecision"] = permission_decision
    return response


PRETOOL_WIRE_CASES = (
    ("claude", "allow", _dual_pretool_response("approve", "allow", "PreToolUse", include_root_permission=True)),
    (
        "claude",
        "deny",
        _dual_pretool_response("block", "deny", "PreToolUse", include_root_permission=True, hide_duplicate_reason=True),
    ),
    ("claude", "ask", _dual_pretool_response("block", "ask", "PreToolUse", include_root_permission=True)),
    ("gemini", "allow", _dual_pretool_response("allow", "allow", "BeforeTool")),
    ("gemini", "deny", _dual_pretool_response("deny", "deny", "BeforeTool")),
    ("gemini", "ask", _dual_pretool_response("deny", "deny", "BeforeTool")),
    (
        "qwen",
        "allow",
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "permissionDecisionReason": "Reason"}},
    ),
    (
        "qwen",
        "deny",
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "Reason"}},
    ),
    (
        "qwen",
        "ask",
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": "Reason"}},
    ),
    ("antigravity", "allow", {"decision": "allow", "reason": "Reason"}),
    ("antigravity", "deny", {"decision": "deny", "reason": "Reason"}),
    ("antigravity", "ask", {"decision": "ask", "reason": "Reason"}),
    (
        "codex",
        "allow",
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "Reason"}},
    ),
    (
        "codex",
        "deny",
        {
            "decision": "block",
            "reason": "Reason",
            "systemMessage": "Reason",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Reason",
            },
        },
    ),
    (
        "codex",
        "ask",
        {
            "decision": "block",
            "reason": "Reason",
            "systemMessage": "Reason",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Reason",
            },
        },
    ),
)


class TestDualPlatformResponse:
    """Verify that respond() logic returns platform-correct JSON."""

    def test_stop_injection_claude(self):
        """Claude Stop event should use decision='block' and continue=True."""
        ctx = EventContext("test", "Stop", cli_type="claude")
        resp = ctx.respond("block", "Keep working")
        assert resp["decision"] == "block"
        assert resp["continue"] is True
        # Stop event uses systemMessage for the injection
        assert resp["systemMessage"] == "Keep working"

    def test_stop_injection_gemini(self):
        """Gemini Stop event should use decision='deny' and continue=True."""
        ctx = EventContext("test", "Stop", cli_type="gemini")
        resp = ctx.respond("block", "Keep working")
        # CRITICAL: For Gemini, AfterAgent (Stop) needs 'deny' to trigger turn retry
        assert resp["decision"] == "deny"
        assert resp["continue"] is True
        assert resp["reason"] == "Keep working"

    def test_pretooluse_deny_claude(self):
        """Claude PreToolUse deny should return block/deny schema."""
        ctx = EventContext("test", "PreToolUse", cli_type="claude")
        resp = ctx.respond("deny", "Blocked")
        assert resp["decision"] == "block"
        assert resp["permissionDecision"] == "deny"
        assert resp["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_pretooluse_deny_gemini(self):
        """Gemini PreToolUse deny should return simple decision='deny'."""
        ctx = EventContext("test", "PreToolUse", cli_type="gemini")
        resp = ctx.respond("deny", "Blocked")
        assert resp["decision"] == "deny"
        # We now include hookSpecificOutput for universal test compatibility
        assert "hookSpecificOutput" in resp
        assert resp["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_ask_mapping_gemini(self):
        """Gemini doesn't support 'ask', should map to 'deny'."""
        ctx = EventContext("test", "PreToolUse", cli_type="gemini")
        resp = ctx.respond("ask", "Are you sure?")
        assert resp["decision"] == "deny"

    def test_qwen_uses_native_events_and_preserves_ask(self):
        ctx = EventContext("test", "PreToolUse", cli_type="qwen")
        resp = ctx.respond("ask", "Confirm this tool")
        assert resp["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert resp["hookSpecificOutput"]["permissionDecision"] == "ask"

    def test_qwen_stop_blocks_stop_without_duplicate_message_channel(self):
        ctx = EventContext("test", "Stop", cli_type="qwen")
        assert ctx.respond("block", "Keep working") == {"decision": "block", "reason": "Keep working"}

    def test_antigravity_pretool_and_stop_use_native_contract(self):
        tool = EventContext("test", "PreToolUse", cli_type="antigravity")
        assert tool.respond("deny", "Blocked") == {"decision": "deny", "reason": "Blocked"}
        stop = EventContext("test", "Stop", cli_type="antigravity")
        assert stop.respond("block", "Keep working") == {"decision": "continue", "reason": "Keep working"}

    def test_codex_stop_uses_one_continuation_channel(self):
        ctx = EventContext("test", "Stop", cli_type="codex")
        assert ctx.respond("block", "Keep working") == {"decision": "block", "reason": "Keep working"}

    def test_ask_capability_matrix_is_platform_native(self):
        claude = EventContext("test", "PreToolUse", cli_type="claude").respond("ask", "Why")
        gemini = EventContext("test", "PreToolUse", cli_type="gemini").respond("ask", "Why")
        qwen = EventContext("test", "PreToolUse", cli_type="qwen").respond("ask", "Why")
        agy = EventContext("test", "PreToolUse", cli_type="antigravity").respond("ask", "Why")
        codex = EventContext("test", "PreToolUse", cli_type="codex").respond("ask", "Why")

        assert claude["hookSpecificOutput"]["permissionDecision"] == "ask"
        assert gemini["decision"] == "deny"
        assert gemini["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert qwen == {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": "Why",
            }
        }
        assert agy == {"decision": "ask", "reason": "Why"}
        assert codex["decision"] == "block"

    @pytest.mark.parametrize(
        ("cli_type", "event", "expected"),
        [
            (
                "claude",
                "UserPromptSubmit",
                {
                    "continue": True,
                    "stopReason": "",
                    "suppressOutput": False,
                    "systemMessage": "Blocked",
                    "decision": "approve",
                    "reason": "",
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": "Blocked",
                    },
                },
            ),
            (
                "claude",
                "PostToolUse",
                {
                    "continue": True,
                    "stopReason": "",
                    "suppressOutput": False,
                    "systemMessage": "Blocked",
                    "decision": "approve",
                    "reason": "",
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": "Blocked",
                    },
                },
            ),
            (
                "gemini",
                "UserPromptSubmit",
                {
                    "continue": True,
                    "stopReason": "",
                    "suppressOutput": False,
                    "systemMessage": "Blocked",
                    "decision": "allow",
                    "reason": "",
                    "hookSpecificOutput": {
                        "hookEventName": "BeforeAgent",
                        "additionalContext": "Blocked",
                    },
                },
            ),
            (
                "gemini",
                "PostToolUse",
                {
                    "continue": True,
                    "stopReason": "",
                    "suppressOutput": False,
                    "systemMessage": "Blocked",
                    "hookSpecificOutput": {
                        "hookEventName": "AfterTool",
                        "additionalContext": "Blocked",
                    },
                },
            ),
            (
                "qwen",
                "UserPromptSubmit",
                {
                    "continue": True,
                    "stopReason": "",
                    "suppressOutput": False,
                    "systemMessage": "Blocked",
                    "decision": "allow",
                    "reason": "",
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": "Blocked",
                    },
                },
            ),
            (
                "qwen",
                "PostToolUse",
                {
                    "continue": True,
                    "stopReason": "",
                    "suppressOutput": False,
                    "systemMessage": "Blocked",
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": "Blocked",
                    },
                },
            ),
            (
                "antigravity",
                "UserPromptSubmit",
                {
                    "continue": True,
                    "stopReason": "",
                    "suppressOutput": False,
                    "systemMessage": "Blocked",
                    "decision": "allow",
                    "reason": "",
                    "hookSpecificOutput": {
                        "hookEventName": "PreInvocation",
                        "additionalContext": "Blocked",
                    },
                },
            ),
            (
                "antigravity",
                "PostToolUse",
                {
                    "continue": True,
                    "stopReason": "",
                    "suppressOutput": False,
                    "systemMessage": "Blocked",
                    "hookSpecificOutput": {
                        # Agy names tool-result hooks PostToolUse. PostInvocation
                        # is its separate model-lifecycle event (AfterModel).
                        "hookEventName": "PostToolUse",
                        "additionalContext": "Blocked",
                    },
                },
            ),
            (
                "codex",
                "UserPromptSubmit",
                {
                    "continue": True,
                    "stopReason": "",
                    "suppressOutput": False,
                    "systemMessage": "Blocked",
                    "decision": "block",
                    "reason": "Blocked",
                },
            ),
            (
                "codex",
                "PostToolUse",
                {
                    "continue": True,
                    "stopReason": "",
                    "systemMessage": "Blocked",
                    "decision": "block",
                    "reason": "Blocked",
                },
            ),
        ],
    )
    def test_context_deny_wire_contract_regressions(self, cli_type, event, expected):
        """Pin compatibility-sensitive context outputs, including Agy feedback."""
        response = EventContext("context-matrix", event, cli_type=cli_type).respond("deny", "Blocked")
        assert response == expected

    @pytest.mark.parametrize(("cli_type", "decision", "expected"), PRETOOL_WIRE_CASES)
    def test_pretool_decision_matrix_exact(self, cli_type, decision, expected):
        """Pin every native PreTool shape; no extra field may silently leak."""
        response = EventContext("pretool-matrix", "PreToolUse", cli_type=cli_type).respond(decision, "Reason")
        assert response == expected
        assert response.get("continue") is not False

    @pytest.mark.parametrize(
        ("cli_type", "expected"),
        [
            ("claude", {"continue": True, "decision": "block", "reason": "Reason", "stopReason": "", "suppressOutput": False, "systemMessage": "Reason"}),
            ("gemini", {"continue": True, "decision": "deny", "reason": "Reason", "stopReason": "", "suppressOutput": False, "systemMessage": "Reason"}),
            ("qwen", {"decision": "block", "reason": "Reason"}),
            ("antigravity", {"decision": "continue", "reason": "Reason"}),
            ("codex", {"decision": "block", "reason": "Reason"}),
        ],
    )
    def test_stop_block_matrix_exact(self, cli_type, expected):
        assert EventContext("stop-matrix", "Stop", cli_type=cli_type).respond("block", "Reason") == expected

    @pytest.mark.parametrize(
        ("cli_type", "expected"),
        [
            ("claude", {"continue": True, "decision": "block", "reason": "Reason", "stopReason": "", "suppressOutput": False, "systemMessage": "Reason"}),
            ("gemini", {"continue": True, "decision": "deny", "reason": "Reason", "stopReason": "", "suppressOutput": False, "systemMessage": "Reason"}),
            ("qwen", {"decision": "block", "reason": "Reason"}),
            ("antigravity", {"decision": "continue", "reason": "Reason"}),
            ("codex", {"decision": "block", "reason": "Reason"}),
        ],
    )
    def test_subagent_stop_block_matrix_exact(self, cli_type, expected):
        assert EventContext("subagent-stop-matrix", "SubagentStop", cli_type=cli_type).respond("block", "Reason") == expected

    @pytest.mark.parametrize(
        ("cli_type", "stop_decision"),
        [
            ("claude", "block"),
            ("gemini", "deny"),
            ("qwen", "block"),
            ("antigravity", "continue"),
            ("codex", "block"),
        ],
    )
    def test_legacy_stop_response_builder_uses_native_stop_decision(self, cli_type, stop_decision):
        """The compatibility shim must use Stop semantics, not tool-deny words."""
        ctx = EventContext("legacy-stop-builder", "Stop", cli_type=cli_type)
        response = build_hook_response(decision="block", reason="Reason", event_name="Stop", ctx=ctx)
        assert response["decision"] == stop_decision

    @pytest.mark.parametrize("cli_type", HARNESSES)
    def test_stop_allow_never_blocks_or_stops_agent(self, cli_type):
        response = EventContext("stop-allow", "Stop", cli_type=cli_type).respond("allow", "Reason")
        if cli_type == "qwen":
            assert response == {}
        else:
            assert response == {
                "continue": True,
                "stopReason": "",
                "suppressOutput": False,
                "systemMessage": "Reason",
            }

    @pytest.mark.parametrize("cli_type", HARNESSES)
    def test_session_start_matrix_exact(self, cli_type):
        assert EventContext("start-matrix", "SessionStart", cli_type=cli_type).respond("allow", "Reason") == {
            "continue": True,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": "Reason",
        }

    @pytest.mark.parametrize("cli_type", ("claude", "gemini", "qwen", "antigravity"))
    def test_context_channel_routing_preserves_pre_protocol_semantics(self, cli_type):
        response = EventContext("channel-matrix", "UserPromptSubmit", cli_type=cli_type).respond("allow", "Reason", to_human=False, to_ai="AI only")
        assert response.get("systemMessage") == "AI only"
        assert response.get("reason") == "Reason"
        assert response["hookSpecificOutput"]["additionalContext"] == "AI only"
        assert response.get("continue") is not False

    def test_codex_empty_context_channels_emit_no_payload(self):
        response = EventContext("codex-empty-context", "UserPromptSubmit", cli_type="codex").respond("allow", "", to_human=False, to_ai=False)
        assert response == {}


@pytest.mark.parametrize(
    ("cli_type", "external", "internal"),
    [
        ("gemini", "BeforeTool", "PreToolUse"),
        ("qwen", "PreToolUse", "PreToolUse"),
        ("antigravity", "PreInvocation", "UserPromptSubmit"),
        ("antigravity", "PostInvocation", "AfterModel"),
        ("codex", "Stop", "Stop"),
    ],
)
def test_payload_normalization_uses_selected_protocol(cli_type, external, internal):
    normalized = normalize_hook_payload(
        {
            "cli_type": cli_type,
            "hook_event_name": external,
            "session_id": "protocol-test",
        }
    )
    assert normalized["cli_type"] == cli_type
    assert normalized["hook_event_name"] == internal


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
