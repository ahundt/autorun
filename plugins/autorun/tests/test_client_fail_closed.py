#!/usr/bin/env python3
"""Client fallback behavior for daemon failures.

Permission-gate hooks must never fail open when the daemon is slow, missing,
or returns invalid data. Lifecycle/context events may continue permissively.
"""

import pytest

from autorun.client import (
    build_daemon_failure_response,
    daemon_response_timeout_for_cli,
    get_stable_pid,
    is_tool_gate_event,
    prepare_payload_for_daemon,
    StableProcessIdentity,
)
from autorun.config import CONFIG
from autorun.platforms import hook_platforms


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
    monkeypatch.setattr(
        "autorun.client.get_stable_process_identity",
        lambda: StableProcessIdentity(12345, 67890),
    )

    payload, cli_type = prepare_payload_for_daemon(
        {
            "hook_event_name": "PreToolUse",
            "session_id": "client-cli-type",
            "tool_name": "Bash",
            "tool_input": {"command": "rm file"},
        }
    )

    assert cli_type == "codex"
    assert payload["cli_type"] == "codex"
    assert payload["_pid"] == 12345
    assert payload["_pid_started_at_units"] == 67890
    assert "_cwd" in payload


def test_client_preserves_identity_already_supplied_by_the_hook_wrapper(monkeypatch):
    monkeypatch.setattr(
        "autorun.client.get_stable_process_identity",
        lambda: StableProcessIdentity(12345, 67890),
    )

    payload, _ = prepare_payload_for_daemon(
        {"_pid": 42, "_pid_started_at_units": 84}
    )

    assert payload["_pid"] == 42
    assert payload["_pid_started_at_units"] == 84

    payload, _ = prepare_payload_for_daemon({"_pid": 42})
    assert "_pid_started_at_units" not in payload


def test_client_response_timeouts_are_config_backed_and_above_dispatch_budget():
    """Client waits must outlast daemon dispatch, with values owned by CONFIG."""
    response_timeouts = CONFIG["daemon_client_response_timeouts_seconds"]
    wrapper_timeouts = CONFIG["hook_wrapper_timeouts_seconds"]
    max_dispatch = max(CONFIG["daemon_dispatch_timeouts_seconds"].values())

    for platform in hook_platforms():
        assert platform.name in response_timeouts
        timeout = daemon_response_timeout_for_cli(platform.name)
        assert timeout == response_timeouts[platform.name]
        assert timeout > max_dispatch
        assert wrapper_timeouts[platform.name] > timeout

    assert daemon_response_timeout_for_cli("unknown") == response_timeouts["claude"]


def test_a_cold_start_and_a_response_together_fit_inside_the_wrapper():
    """The sum, which the ordering check above never constrained.

    Ordering held while the total did not. A cold start waited
    DAEMON_START_ATTEMPTS * DAEMON_START_RETRY_SECONDS and a response waited its
    configured timeout, and nothing compared their sum to the wrapper budget:
    gemini, antigravity and qwen came to 0.8 + 3.5 against a 4.0s wrapper and
    opencode to 0.8 + 4.0 against 4.5, each exceeding by 0.3s. The wrapper kills
    the hook first, so the client's own bound is unreachable and the failure
    response that explains the timeout is never written -- the shape of the
    original 8.1s retry ladder running inside a 5s wrapper.

    client_total_budget derives one budget from the wrapper, so this passes by
    construction; the assertion exists to catch a future constant that reopens
    the gap.
    """
    from autorun.client import (
        CLIENT_BUDGET_MARGIN_SECONDS,
        DAEMON_START_ATTEMPTS,
        DAEMON_START_RETRY_SECONDS,
        client_total_budget,
    )

    wrapper_timeouts = CONFIG["hook_wrapper_timeouts_seconds"]
    cold_start = DAEMON_START_ATTEMPTS * DAEMON_START_RETRY_SECONDS

    for platform in hook_platforms():
        wrapper = wrapper_timeouts[platform.name]
        budget = client_total_budget(platform.name)

        assert budget < wrapper, (
            f"{platform.name}: the client may spend {budget}s inside a "
            f"{wrapper}s wrapper, leaving no room to write a response"
        )
        assert budget == wrapper - CLIENT_BUDGET_MARGIN_SECONDS

    assert client_total_budget("unknown") == client_total_budget("claude")


def test_the_attempt_cap_cannot_bind_before_the_deadline():
    """The retry cap is a recursion guard, not the cold-start bound.

    With the cap at 8 and the sleep at 0.1s a cold start ended after 0.8s
    however much budget remained. A POSIX daemon reaches its first accepted
    connection in about 0.14s so that was invisible; Windows process creation
    is far slower, and there the client gave up while the daemon it had just
    spawned was still booting -- reported as "Daemon failed to start after 8
    attempts" with the wrapper budget almost entirely unused.

    Raising the budget alone would not have helped: whichever of the two bounds
    is smaller decides, so the cap has to stay above every budget it serves.
    """
    from autorun.client import (
        DAEMON_START_ATTEMPTS,
        DAEMON_START_RETRY_SECONDS,
        client_total_budget,
    )

    for platform in hook_platforms():
        budget = client_total_budget(platform.name)
        reachable = DAEMON_START_ATTEMPTS * DAEMON_START_RETRY_SECONDS
        assert reachable >= budget, (
            f"{platform.name}: {DAEMON_START_ATTEMPTS} attempts of "
            f"{DAEMON_START_RETRY_SECONDS}s reach only {reachable}s of a "
            f"{budget}s budget, so the cap ends the cold start early and the "
            f"remaining {budget - reachable:.1f}s is never used"
        )


def test_daemon_failure_response_embeds_privacy_safe_versioned_event_code():
    import json

    from autorun.client import build_daemon_failure_response

    response = build_daemon_failure_response(
        "PreToolUse",
        "claude",
        "handler timed out",
        event_code="daemon_dispatch_timeout",
    )
    rendered = json.dumps(response)
    assert "[AR_EVENT_V1:daemon_dispatch_timeout]" in rendered
    assert "command" not in rendered.lower()


def test_cli_argument_choices_come_from_platform_registry():
    """`autorun --cli` help must not drift from registered hook platforms."""
    from autorun.__main__ import create_parser

    parser = create_parser()
    cli_action = next(action for action in parser._actions if action.dest == "cli")
    assert tuple(cli_action.choices) == tuple(platform.name for platform in hook_platforms())


def test_get_stable_pid_recognizes_codex_parent_after_wrappers(monkeypatch):
    """Codex hooks must share one parent-derived fallback session across invocations."""
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

    codex = FakeProcess(42000, "codex")
    zsh = FakeProcess(42001, "zsh", codex)
    uv = FakeProcess(42002, "uv", zsh)
    python = FakeProcess(42003, "python3.12", uv)

    monkeypatch.setattr("os.getppid", lambda: 99999)
    with mock.patch("psutil.Process", return_value=python):
        assert get_stable_pid() == 42000


@pytest.mark.parametrize(
    ("event", "cli", "decision", "root_fields"),
    [
        (
            "PreToolUse",
            "claude",
            "block",
            {
                "decision",
                "permissionDecision",
                "reason",
                "continue",
                "stopReason",
                "suppressOutput",
                "systemMessage",
                "hookSpecificOutput",
            },
        ),
        (
            "BeforeTool",
            "gemini",
            "deny",
            {
                "decision",
                "reason",
                "continue",
                "stopReason",
                "suppressOutput",
                "systemMessage",
                "hookSpecificOutput",
            },
        ),
        ("PreToolUse", "qwen", None, {"hookSpecificOutput"}),
        ("PreToolUse", "antigravity", "deny", {"decision", "reason"}),
        (
            "PreToolUse",
            "codex",
            "block",
            {
                "decision",
                "reason",
                "systemMessage",
                "hookSpecificOutput",
            },
        ),
    ],
)
def test_daemon_failure_tool_gates_use_exact_native_fail_closed_schema(event, cli, decision, root_fields):
    response = build_daemon_failure_response(event, cli, "Daemon response timed out")

    assert set(response) == root_fields
    if decision is not None:
        assert response["decision"] == decision
    hook_output = response.get("hookSpecificOutput")
    if hook_output is not None:
        assert hook_output["hookEventName"] == event
        assert hook_output["permissionDecision"] == "deny"
        assert "timed out" in hook_output["permissionDecisionReason"]
    else:
        assert "timed out" in response["reason"]


def test_lifecycle_daemon_failure_stays_fail_open():
    response = build_daemon_failure_response("SessionStart", "claude", "Daemon response timed out")

    assert response["continue"] is True
    assert "decision" not in response
    assert "hookSpecificOutput" not in response
