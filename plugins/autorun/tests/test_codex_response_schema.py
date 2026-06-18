#!/usr/bin/env python3
"""Codex hook response schema tests.

These tests intentionally encode the current Codex hook release behavior:
normal allow responses must not use Claude's legacy decision="approve"
shape, and every registered autorun command must return Codex-valid JSON.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from autorun import plugins, task_lifecycle
from autorun.core import (
    EventContext,
    ThreadSafeDB,
    format_suggestion,
    get_cli_event_name,
    normalize_hook_payload,
    resolve_session_key,
    validate_hook_response,
)
from autorun.client import prepare_payload_for_daemon
from autorun.session_manager import session_state


PLUGIN_ROOT = Path(__file__).parent.parent
HOOK_ENTRY = PLUGIN_ROOT / "hooks" / "hook_entry.py"
CODEX_E2E = PLUGIN_ROOT / "tests" / "test_codex_e2e_real_money.py"


def _load_codex_e2e_module():
    spec = importlib.util.spec_from_file_location("autorun_test_codex_e2e_real_money", CODEX_E2E)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def isolate_global_policy_state():
    """Keep command-matrix global allow/block commands from polluting later tests."""
    with session_state("__global__") as st:
        st["global_allowed_patterns"] = []
        st["global_blocked_patterns"] = []
    yield
    with session_state("__global__") as st:
        st["global_allowed_patterns"] = []
        st["global_blocked_patterns"] = []


def assert_codex_response_valid(event: str, response: dict | None) -> None:
    """Assert response only uses fields Codex currently supports for event."""
    if response in (None, {}):
        return

    assert isinstance(response, dict)
    assert response.get("decision") != "approve", response

    common = {"continue", "stopReason", "systemMessage", "suppressOutput"}
    hso_common = {"hookEventName", "additionalContext"}

    if event in {"SessionStart", "UserPromptSubmit", "SubagentStop", "Stop"}:
        allowed = common | {"decision", "reason", "hookSpecificOutput"}
        assert set(response) <= allowed, response
        if "decision" in response:
            assert response["decision"] == "block", response
            assert isinstance(response.get("reason"), str) and response["reason"], response
        elif "reason" in response:
            pytest.fail(f"Codex {event} reason is only valid with decision='block': {response}")
        if "hookSpecificOutput" in response:
            assert set(response["hookSpecificOutput"]) <= hso_common, response
            assert response["hookSpecificOutput"].get("hookEventName") == event, response
        return

    if event == "PreToolUse":
        unsupported = {"continue", "stopReason", "suppressOutput"}
        assert unsupported.isdisjoint(response), response
        allowed = {"systemMessage", "decision", "reason", "hookSpecificOutput"}
        assert set(response) <= allowed, response
        if "decision" in response:
            assert response["decision"] == "block", response
            assert isinstance(response.get("reason"), str) and response["reason"], response
        hso_allowed = {
            "hookEventName",
            "permissionDecision",
            "permissionDecisionReason",
            "additionalContext",
            "updatedInput",
        }
        if "hookSpecificOutput" in response:
            hso = response["hookSpecificOutput"]
            assert set(hso) <= hso_allowed, response
            assert hso.get("hookEventName") == "PreToolUse", response
            assert hso.get("permissionDecision") != "ask", response
            if hso.get("permissionDecision") == "allow":
                assert "updatedInput" in hso, response
            if "permissionDecisionReason" in hso:
                assert hso.get("permissionDecision") in {"deny", "allow"}, response
        return

    if event == "PostToolUse":
        assert "suppressOutput" not in response, response
        allowed = {"systemMessage", "continue", "stopReason", "decision", "reason", "hookSpecificOutput"}
        assert set(response) <= allowed, response
        if "decision" in response:
            assert response["decision"] == "block", response
            assert isinstance(response.get("reason"), str) and response["reason"], response
        if "hookSpecificOutput" in response:
            assert set(response["hookSpecificOutput"]) <= hso_common, response
            assert response["hookSpecificOutput"].get("hookEventName") == "PostToolUse", response
        return

    pytest.fail(f"Unhandled Codex event in schema test: {event}")


def _codex_context(event: str, **kwargs) -> EventContext:
    return EventContext(
        session_id=f"codex-schema-{uuid.uuid4().hex}",
        event=event,
        cli_type="codex",
        store=ThreadSafeDB(),
        **kwargs,
    )


def _sample_prompt(alias: str) -> str:
    """Return a safe prompt for a registered command alias."""
    if alias in {"/ar:ok", "/ar:globalok"}:
        return f"{alias} autorun-matrix-allow-pattern 1"
    if alias in {"/ar:no", "/ar:globalno"}:
        return f"{alias} regex:test-block test block"
    if alias == "/ar:tasks":
        return "/ar:tasks 3"
    if alias == "/ar:task-ignore":
        return "/ar:task-ignore nonexistent-test-task no longer relevant"
    if alias == "/ar:cache":
        return "/ar:cache status"
    if alias in {"/ar:go", "/ar:run", "/ar:gp", "/ar:proc", "/autorun", "/autoproc", "activate"}:
        return f"{alias} test task"
    if alias in {"NEW_PLAN", "REFINE_PLAN", "UPDATE_PLAN", "PROCESS_PLAN"}:
        return alias
    return alias


def test_codex_user_prompt_submit_allow_has_no_approve_decision():
    ctx = _codex_context("UserPromptSubmit", prompt="normal user prompt")
    response = ctx.respond("allow")
    assert_codex_response_valid("UserPromptSubmit", response)
    assert response == {}


def test_codex_e2e_model_selector_prefers_refreshed_spark_catalog(monkeypatch):
    e2e = _load_codex_e2e_module()
    monkeypatch.delenv("AUTORUN_CODEX_E2E_MODEL", raising=False)
    monkeypatch.delenv("AUTORUN_CODEX_E2E_ALLOW_NON_SPARK_MODEL", raising=False)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        assert cmd == ["codex", "debug", "models"]
        catalog = {"models": [{"slug": "gpt-5.3-codex-spark"}]}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(catalog), stderr="")

    monkeypatch.setattr(e2e.subprocess, "run", fake_run)

    assert e2e._choose_codex_e2e_model() == "gpt-5.3-codex-spark"
    assert calls == [["codex", "debug", "models"]]


def test_codex_e2e_model_selector_rejects_non_spark_override_by_default(monkeypatch):
    e2e = _load_codex_e2e_module()
    monkeypatch.setenv("AUTORUN_CODEX_E2E_MODEL", "gpt-5.3-codex")
    monkeypatch.delenv("AUTORUN_CODEX_E2E_ALLOW_NON_SPARK_MODEL", raising=False)

    with pytest.raises(pytest.skip.Exception):
        e2e._choose_codex_e2e_model()


def test_codex_e2e_model_selector_allows_explicit_non_spark_override(monkeypatch):
    e2e = _load_codex_e2e_module()
    monkeypatch.setenv("AUTORUN_CODEX_E2E_MODEL", "gpt-5.3-codex")
    monkeypatch.setenv("AUTORUN_CODEX_E2E_ALLOW_NON_SPARK_MODEL", "1")

    assert e2e._choose_codex_e2e_model() == "gpt-5.3-codex"


def test_codex_e2e_exec_command_uses_current_exec_flags(tmp_path):
    e2e = _load_codex_e2e_module()
    cmd = e2e._codex_exec_command(
        "gpt-5.3-codex-spark",
        tmp_path,
        tmp_path / "last-message.txt",
        "/ar:st",
    )
    assert cmd[:2] == ["codex", "exec"]
    assert "--ask-for-approval" not in cmd
    assert "--dangerously-bypass-hook-trust" in cmd
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert cmd[cmd.index("--model") + 1] == "gpt-5.3-codex-spark"


def test_codex_command_response_schema_is_valid():
    ctx = _codex_context("UserPromptSubmit", prompt="/ar:st")
    response = ctx.command_response("status text")
    assert_codex_response_valid("UserPromptSubmit", response)
    assert response["hookSpecificOutput"]["additionalContext"] == "status text"
    assert "decision" not in response
    assert "reason" not in response


def test_codex_user_prompt_submit_block_uses_block_decision_and_reason():
    ctx = _codex_context("UserPromptSubmit", prompt="/blocked")
    response = ctx.respond("deny", "Blocked prompt")
    assert_codex_response_valid("UserPromptSubmit", response)
    assert response["decision"] == "block"
    assert response["reason"] == "Blocked prompt"


@pytest.mark.parametrize("alias", sorted(plugins.app.command_handlers))
def test_every_autorun_command_returns_codex_valid_userprompt_json(alias):
    prompt = _sample_prompt(alias)
    ctx = _codex_context("UserPromptSubmit", prompt=prompt)
    response = plugins.app.dispatch(ctx)
    assert response is not None, f"{alias} did not dispatch"
    assert_codex_response_valid("UserPromptSubmit", response)


@pytest.mark.parametrize("cli_type", ["claude", "gemini", "codex"])
@pytest.mark.parametrize("alias", sorted(plugins.app.command_handlers))
def test_every_autorun_command_alias_dispatches_for_all_hook_platforms(cli_type, alias):
    prompt = _sample_prompt(alias)
    ctx = EventContext(
        session_id=f"cmd-matrix-{cli_type}-{uuid.uuid4().hex}",
        event="UserPromptSubmit",
        prompt=prompt,
        cli_type=cli_type,
        store=ThreadSafeDB(),
    )
    response = plugins.app.dispatch(ctx)
    assert response is not None, f"{cli_type} {alias} did not dispatch"
    visible = response.get("systemMessage") or response.get("hookSpecificOutput", {}).get("additionalContext")
    assert visible, response
    if "hookSpecificOutput" in response:
        assert response["hookSpecificOutput"]["hookEventName"] == get_cli_event_name(
            "UserPromptSubmit", cli_type
        )
    if cli_type == "codex":
        assert_codex_response_valid("UserPromptSubmit", response)


@pytest.mark.parametrize("cli_type", ["claude", "gemini", "codex"])
def test_registered_commands_return_visible_output_for_all_hook_platforms(cli_type):
    ctx = EventContext(
        session_id=f"cmd-visible-{cli_type}",
        event="UserPromptSubmit",
        prompt="/ar:st",
        cli_type=cli_type,
        store=ThreadSafeDB(),
    )
    response = plugins.app.dispatch(ctx)
    assert response is not None
    visible = response.get("systemMessage") or response.get("hookSpecificOutput", {}).get("additionalContext")
    assert visible, response
    if cli_type == "codex":
        assert_codex_response_valid("UserPromptSubmit", response)


@pytest.mark.parametrize("cli_type", ["claude", "gemini", "codex"])
def test_stateful_policy_command_mutates_shared_store_for_all_hook_platforms(cli_type):
    store = ThreadSafeDB()
    session_id = f"stateful-policy-{cli_type}-{uuid.uuid4().hex}"
    ctx = EventContext(
        session_id=session_id,
        event="UserPromptSubmit",
        prompt="/ar:j",
        cli_type=cli_type,
        store=store,
    )
    response = plugins.app.dispatch(ctx)
    assert response is not None
    if cli_type == "codex":
        assert_codex_response_valid("UserPromptSubmit", response)

    verify = EventContext(
        session_id=session_id,
        event="PreToolUse",
        cli_type=cli_type,
        store=store,
    )
    assert verify.file_policy == "JUSTIFY"


def test_codex_plain_ar_alias_dispatches_without_leading_slash():
    ctx = _codex_context("UserPromptSubmit", prompt="ar:st")
    response = plugins.app.dispatch(ctx)
    assert response is not None
    assert_codex_response_valid("UserPromptSubmit", response)
    assert "AutoFile policy" in json.dumps(response)


@pytest.mark.parametrize(
    "alias",
    sorted(a for a in plugins.app.command_handlers if a.startswith("/ar:")),
)
def test_every_slash_ar_command_also_dispatches_with_codex_plain_prefix(alias):
    prompt = _sample_prompt(alias).removeprefix("/")
    ctx = _codex_context("UserPromptSubmit", prompt=prompt)
    response = plugins.app.dispatch(ctx)
    assert response is not None, f"{prompt} did not dispatch through plain ar:* prefix"
    assert_codex_response_valid("UserPromptSubmit", response)


@pytest.mark.parametrize("prompt", ["ar:st", "ar st"])
def test_codex_accepts_colon_and_space_plain_ar_command_spelling(prompt):
    ctx = _codex_context("UserPromptSubmit", prompt=prompt)
    response = plugins.app.dispatch(ctx)
    assert response is not None
    assert_codex_response_valid("UserPromptSubmit", response)
    assert "AutoFile policy" in json.dumps(response)


def test_codex_plain_ar_allow_alias_unblocks_same_session_command():
    store = ThreadSafeDB()
    session_id = f"codex-plain-allow-{uuid.uuid4().hex}"

    denied_ctx = EventContext(
        session_id=session_id,
        event="PreToolUse",
        tool_name="Bash",
        tool_input={"command": "git push origin main"},
        cli_type="codex",
        store=store,
    )
    denied = plugins.check_blocked_commands(denied_ctx)
    assert denied is not None
    assert_codex_response_valid("PreToolUse", denied)

    allow_ctx = EventContext(
        session_id=session_id,
        event="UserPromptSubmit",
        prompt="ar:ok git push",
        cli_type="codex",
        store=store,
    )
    allow_response = plugins.app.dispatch(allow_ctx)
    assert allow_response is not None
    assert_codex_response_valid("UserPromptSubmit", allow_response)
    assert "git push" in json.dumps(allow_response)

    allowed_ctx = EventContext(
        session_id=session_id,
        event="PreToolUse",
        tool_name="Bash",
        tool_input={"command": "git push origin main"},
        cli_type="codex",
        store=store,
    )
    allowed = plugins.check_blocked_commands(allowed_ctx)
    assert allowed is not None
    assert_codex_response_valid("PreToolUse", allowed)
    hso = allowed["hookSpecificOutput"]
    assert "permissionDecision" not in hso
    assert "additionalContext" in hso
    assert "git push" in hso["additionalContext"]


def test_codex_allow_without_session_id_uses_stable_cli_parent(monkeypatch):
    """One Codex ar:ok must apply to the next tool hook even through wrappers."""
    from unittest import mock

    class FakeProcess:
        def __init__(self, pid, name, parent=None):
            self.pid = pid
            self._name = name
            self._parent = parent

        def name(self):
            return self._name

        def parent(self):
            return self._parent

        def cmdline(self):
            return [self._name]

    codex = FakeProcess(42000, "codex")
    zsh = FakeProcess(42001, "zsh", codex)
    uv = FakeProcess(42002, "uv", zsh)
    python = FakeProcess(42003, "python3.12", uv)
    ephemeral_ppids = iter([11111, 22222])
    store = ThreadSafeDB()

    def context_from_payload(payload):
        normalized = normalize_hook_payload(payload)
        session_id = resolve_session_key(
            payload.get("_pid"), payload.get("_cwd", ""), normalized["session_id"]
        )
        return EventContext(
            session_id=session_id,
            event=normalized["hook_event_name"],
            prompt=normalized["prompt"],
            tool_name=normalized["tool_name"],
            tool_input=normalized["tool_input"],
            cli_type=normalized["cli_type"],
            cwd=payload.get("_cwd"),
            store=store,
            transcript_path=normalized.get("transcript_path"),
        )

    monkeypatch.setenv("AUTORUN_CLI_TYPE", "codex")
    monkeypatch.setattr("os.getppid", lambda: next(ephemeral_ppids))
    with mock.patch("psutil.Process", return_value=python):
        allow_payload, _ = prepare_payload_for_daemon({
            "hook_event_name": "UserPromptSubmit",
            "prompt": "ar:ok git push",
        })
        push_payload, _ = prepare_payload_for_daemon({
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
        })

    allow_response = plugins.app.dispatch(context_from_payload(allow_payload))
    assert_codex_response_valid("UserPromptSubmit", allow_response)

    push_response = plugins.check_blocked_commands(context_from_payload(push_payload))
    assert push_response is not None
    assert_codex_response_valid("PreToolUse", push_response)
    hso = push_response["hookSpecificOutput"]
    assert "permissionDecision" not in hso
    assert "additionalContext" in hso
    assert "Allowed 'git push'" in hso["additionalContext"]


def _write_codex_rollout_user_messages(path: Path, prompts: list[str], *, event_msg: bool = False) -> None:
    lines = []
    for index, prompt in enumerate(prompts):
        timestamp = f"2026-06-18T15:45:{index:02d}Z"
        if event_msg:
            entry = {
                "timestamp": timestamp,
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": prompt,
                    "kind": "plain",
                },
            }
        else:
            entry = {
                "timestamp": timestamp,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                },
            }
        lines.append(json.dumps(entry))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.parametrize("event_msg", [False, True])
def test_codex_transcript_ar_ok_fallback_unblocks_when_prompt_hook_missing(tmp_path, event_msg):
    store = ThreadSafeDB()
    session_id = f"codex-transcript-allow-{uuid.uuid4().hex}"
    transcript = tmp_path / "rollout.jsonl"
    _write_codex_rollout_user_messages(transcript, ["ar:ok 'git push'"], event_msg=event_msg)

    ctx = EventContext(
        session_id=session_id,
        event="PreToolUse",
        tool_name="Bash",
        tool_input={"command": "git push origin main"},
        cli_type="codex",
        store=store,
        transcript_path=str(transcript),
    )

    response = plugins.check_blocked_commands(ctx)

    assert response is not None
    assert_codex_response_valid("PreToolUse", response)
    hso = response["hookSpecificOutput"]
    assert "permissionDecision" not in hso
    assert "additionalContext" in hso
    assert "Allowed 'git push'" in hso["additionalContext"]


def test_codex_transcript_multiline_ar_ok_uses_only_first_line(tmp_path):
    store = ThreadSafeDB()
    session_id = f"codex-transcript-multiline-allow-{uuid.uuid4().hex}"
    transcript = tmp_path / "rollout.jsonl"
    _write_codex_rollout_user_messages(
        transcript,
        [
            "\n".join(
                [
                    "ar:ok git push",
                    "Use the shell tool to run exactly:",
                    "git push --dry-run no-such-remote HEAD",
                    "After the command returns, answer exactly: COMMAND_RAN",
                ]
            )
        ],
    )

    ctx = EventContext(
        session_id=session_id,
        event="PreToolUse",
        tool_name="Bash",
        tool_input={"command": "git push --dry-run no-such-remote HEAD"},
        cli_type="codex",
        store=store,
        transcript_path=str(transcript),
    )

    response = plugins.check_blocked_commands(ctx)

    assert response is not None
    assert_codex_response_valid("PreToolUse", response)
    hso = response["hookSpecificOutput"]
    assert "permissionDecision" not in hso
    assert "Allowed 'git push'" in hso["additionalContext"]
    assert ctx.session_allowed_patterns[-1]["pattern"] == "git push"


def test_codex_transcript_ar_ok_notifies_on_first_later_safe_tool(tmp_path):
    store = ThreadSafeDB()
    session_id = f"codex-transcript-safe-tool-notice-{uuid.uuid4().hex}"
    transcript = tmp_path / "rollout.jsonl"
    _write_codex_rollout_user_messages(transcript, ["ar:ok 'git push'"])

    ctx = EventContext(
        session_id=session_id,
        event="PreToolUse",
        tool_name="Bash",
        tool_input={"command": "echo safe"},
        cli_type="codex",
        store=store,
        transcript_path=str(transcript),
    )

    response = plugins.check_blocked_commands(ctx)

    assert response is not None
    assert_codex_response_valid("PreToolUse", response)
    hso = response["hookSpecificOutput"]
    assert "permissionDecision" not in hso
    assert "systemMessage" not in response
    assert "Autorun processed latest Codex command" in hso["additionalContext"]
    assert "Allowed" in hso["additionalContext"]
    assert "git push" in hso["additionalContext"]
    assert ctx.session_allowed_patterns[-1]["pattern"] == "git push"
    assert ctx.session_allowed_patterns[-1]["remaining_uses"] == 1


def test_codex_transcript_ar_ok_fallback_does_not_replay_consumed_prompt(tmp_path):
    store = ThreadSafeDB()
    session_id = f"codex-transcript-no-replay-{uuid.uuid4().hex}"
    transcript = tmp_path / "rollout.jsonl"
    _write_codex_rollout_user_messages(transcript, ["ar:ok 'git push'"])

    first_ctx = EventContext(
        session_id=session_id,
        event="PreToolUse",
        tool_name="Bash",
        tool_input={"command": "git push origin main"},
        cli_type="codex",
        store=store,
        transcript_path=str(transcript),
    )
    first = plugins.check_blocked_commands(first_ctx)
    assert first is not None
    assert_codex_response_valid("PreToolUse", first)
    assert "Allowed 'git push'" in json.dumps(first)

    second_ctx = EventContext(
        session_id=session_id,
        event="PreToolUse",
        tool_name="Bash",
        tool_input={"command": "git push origin feature"},
        cli_type="codex",
        store=store,
        transcript_path=str(transcript),
    )
    second = plugins.check_blocked_commands(second_ctx)

    assert second is not None
    assert_codex_response_valid("PreToolUse", second)
    hso = second["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
    assert "git push requires explicit user permission" in hso["permissionDecisionReason"]


def test_codex_transcript_freeform_permission_text_does_not_unblock_push(tmp_path):
    store = ThreadSafeDB()
    session_id = f"codex-transcript-freeform-{uuid.uuid4().hex}"
    transcript = tmp_path / "rollout.jsonl"
    _write_codex_rollout_user_messages(transcript, ["ok do an escalated push attempt"])

    ctx = EventContext(
        session_id=session_id,
        event="PreToolUse",
        tool_name="Bash",
        tool_input={"command": "git push origin main"},
        cli_type="codex",
        store=store,
        transcript_path=str(transcript),
    )
    response = plugins.check_blocked_commands(ctx)

    assert response is not None
    assert_codex_response_valid("PreToolUse", response)
    hso = response["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
    assert "git push requires explicit user permission" in hso["permissionDecisionReason"]


def test_codex_transcript_stale_ar_ok_before_newer_user_text_does_not_unblock_push(tmp_path):
    store = ThreadSafeDB()
    session_id = f"codex-transcript-stale-allow-{uuid.uuid4().hex}"
    transcript = tmp_path / "rollout.jsonl"
    _write_codex_rollout_user_messages(
        transcript,
        ["ar:ok 'git push'", "actually do not push yet"],
    )

    ctx = EventContext(
        session_id=session_id,
        event="PreToolUse",
        tool_name="Bash",
        tool_input={"command": "git push origin main"},
        cli_type="codex",
        store=store,
        transcript_path=str(transcript),
    )
    response = plugins.check_blocked_commands(ctx)

    assert response is not None
    assert_codex_response_valid("PreToolUse", response)
    hso = response["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
    assert "git push requires explicit user permission" in hso["permissionDecisionReason"]


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("ar:ok git push", "git push"),
        ("ar:ok git push 5m", "git push"),
        ("ar:ok 5m git push", "git push"),
        ("ar:ok 'sleep 5'", "sleep 5"),
    ],
)
def test_codex_plain_ar_allow_supports_unquoted_multiword_patterns(prompt, expected):
    store = ThreadSafeDB()
    session_id = f"codex-plain-allow-pattern-{uuid.uuid4().hex}"
    ctx = EventContext(
        session_id=session_id,
        event="UserPromptSubmit",
        prompt=prompt,
        cli_type="codex",
        store=store,
    )
    response = plugins.app.dispatch(ctx)
    assert response is not None
    assert_codex_response_valid("UserPromptSubmit", response)
    assert expected in json.dumps(response)
    assert ctx.session_allowed_patterns[-1]["pattern"] == expected


@pytest.mark.parametrize("prompt_prefix", ["/ar:task-ignore", "ar:task-ignore", "ar task-ignore"])
def test_task_ignore_command_marks_task_ignored_across_native_and_plain_aliases(prompt_prefix):
    session_id = f"task-ignore-{uuid.uuid4().hex}"
    store = ThreadSafeDB()
    setup_ctx = EventContext(
        session_id=session_id,
        event="UserPromptSubmit",
        prompt="setup",
        cli_type="codex",
        store=store,
    )
    manager = task_lifecycle.TaskLifecycle(ctx=setup_ctx)
    manager.create_task("T-ignore", {"subject": "Temporary tracked work"}, "created")

    ctx = EventContext(
        session_id=session_id,
        event="UserPromptSubmit",
        prompt=f"{prompt_prefix} T-ignore no longer relevant",
        cli_type="codex",
        store=store,
    )
    response = plugins.app.dispatch(ctx)

    assert response is not None
    assert_codex_response_valid("UserPromptSubmit", response)
    assert task_lifecycle.TaskLifecycle(ctx=ctx).tasks["T-ignore"]["status"] == "ignored"
    assert "no longer relevant" in json.dumps(response)


def test_codex_suggestions_use_plain_ar_aliases_not_rejected_slash_commands():
    message = "To allow (default 1 use): /ar:ok git push\nBlock globally: /ar:globalno git push"
    assert format_suggestion(message, "codex") == (
        "To allow (default 1 use): ar:ok git push\nBlock globally: ar:globalno git push"
    )
    assert format_suggestion(message, "claude") == message


def test_codex_pretooluse_deny_omits_unsupported_common_fields():
    ctx = _codex_context("PreToolUse", tool_name="Bash", tool_input={"command": "rm test"})
    response = ctx.respond("deny", "Blocked")
    assert_codex_response_valid("PreToolUse", response)
    hso = response["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
    assert "continue" not in response
    assert "stopReason" not in response
    assert "suppressOutput" not in response


def test_codex_pretooluse_allow_warning_uses_additional_context_not_permission_decision():
    response = validate_hook_response(
        "PreToolUse",
        {
            "systemMessage": "warn about this command",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": "warn about this command",
            },
        },
        cli_type="codex",
    )

    assert_codex_response_valid("PreToolUse", response)
    hso = response["hookSpecificOutput"]
    assert "permissionDecision" not in hso
    assert "permissionDecisionReason" not in hso
    assert "systemMessage" not in response
    assert hso["additionalContext"] == "warn about this command"


def test_codex_pretooluse_human_only_system_message_is_kept():
    response = validate_hook_response(
        "PreToolUse",
        {"systemMessage": "human-only hook notice"},
        cli_type="codex",
    )

    assert response == {"systemMessage": "human-only hook notice"}
    assert_codex_response_valid("PreToolUse", response)


def test_codex_pretooluse_git_commit_rules_warning_is_valid_context():
    ctx = _codex_context("PreToolUse", tool_name="Bash", tool_input={"command": "git status"})
    response = plugins.check_blocked_commands(ctx)

    assert response is not None
    assert_codex_response_valid("PreToolUse", response)
    assert "Git commit rules" in json.dumps(response)
    hso = response["hookSpecificOutput"]
    assert "permissionDecision" not in hso
    assert "permissionDecisionReason" not in hso
    assert "additionalContext" in hso


def test_codex_pretooluse_updated_input_requires_allow_permission_decision():
    response = validate_hook_response(
        "PreToolUse",
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "updatedInput": {"command": "git status"},
            }
        },
        cli_type="codex",
    )
    hso = response["hookSpecificOutput"]
    assert hso["updatedInput"] == {"command": "git status"}
    assert hso["permissionDecision"] == "allow"
    assert hso["permissionDecisionReason"] == ""
    assert_codex_response_valid("PreToolUse", response)


def test_codex_pretooluse_block_drops_updated_input():
    response = validate_hook_response(
        "PreToolUse",
        {
            "decision": "block",
            "reason": "Blocked",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Blocked",
                "updatedInput": {"command": "git status"},
            },
        },
        cli_type="codex",
    )
    hso = response["hookSpecificOutput"]
    assert "updatedInput" not in hso
    assert hso["permissionDecision"] == "deny"
    assert_codex_response_valid("PreToolUse", response)


def test_codex_stop_block_uses_continue_shape():
    ctx = _codex_context("Stop")
    response = ctx.respond("block", "Run one more pass")
    assert_codex_response_valid("Stop", response)
    assert response["decision"] == "block"
    assert response["reason"] == "Run one more pass"


def test_hook_entry_codex_userprompt_submit_no_approve_decision():
    env = os.environ.copy()
    env["AUTORUN_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": f"hook-entry-codex-{uuid.uuid4().hex}",
        "prompt": "/ar:st",
        "cwd": str(PLUGIN_ROOT),
        "permission_mode": "default",
    }
    result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(PLUGIN_ROOT),
            "python",
            str(HOOK_ENTRY),
            "--cli",
            "codex",
        ],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert_codex_response_valid("UserPromptSubmit", response)
    assert response.get("decision") != "approve"
