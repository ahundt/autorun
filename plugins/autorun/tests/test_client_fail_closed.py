#!/usr/bin/env python3
"""Client fallback behavior for daemon failures.

Permission-gate hooks must never fail open when the daemon is slow, missing,
or returns invalid data. Lifecycle/context events may continue permissively.
"""

from pathlib import Path

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


def _fail_closed_reason(cli_type: str) -> str:
    """The text a blocked reader actually sees, whatever the platform shape."""
    response = build_daemon_failure_response(
        "PreToolUse",
        cli_type,
        "Daemon error: Could not configure state connection for "
        "daemon_state.sqlite3: unable to open database file",
    )
    hook_specific = response.get("hookSpecificOutput") or {}
    return (
        hook_specific.get("permissionDecisionReason")
        or response.get("reason")
        or response.get("systemMessage")
        or ""
    )


def test_a_daemon_failure_names_an_exit_the_blocked_reader_can_actually_take():
    """The reason must name AUTORUN_DISABLE, and must not say "then retry".

    A daemon whose state backend cannot open does not clear by waiting, and
    every tool call the reader could make to repair it -- including the
    ``autorun --restart-daemon`` this message used to recommend -- is itself a
    PreToolUse call this same gate denies. hook_entry.py:459-496 records that
    deadlock, its cost, and why allowlisting the repair command is refused:
    ``uv tool install autorun --with <package>`` would pass such a check and run
    arbitrary build code. AUTORUN_DISABLE=1 is the one exit a human can take, so
    naming it is the difference between a thirty-second recovery and a lost day.

    Observed live on 2026-08-18: 67 "unable to open database file" entries in
    daemon.log, no fail_closed_tool_gate trace in hook_entry_debug.log -- the
    path that ran was this one, and its reason named neither the escape hatch
    nor a reachable command.
    """
    for platform in hook_platforms():
        reason = _fail_closed_reason(platform.name)
        assert "AUTORUN_DISABLE" in reason, (
            f"{platform.name}: the way out must be named in the reason, "
            f"got {reason!r}"
        )
        assert "then retry" not in reason, (
            f"{platform.name}: a state that never clears must not advise a "
            f"retry, got {reason!r}"
        )


def test_a_lifecycle_failure_stays_open_and_does_not_carry_gate_guidance():
    """Only tool gates fail closed; a lifecycle event must not be blocked."""
    response = build_daemon_failure_response("SessionStart", "claude", "Daemon error: x")
    assert response.get("continue") is True
    assert "permissionDecision" not in (response.get("hookSpecificOutput") or {})


def test_the_wrapper_and_the_client_agree_on_the_unrecoverable_guidance():
    """One sentence, spelled in a stdlib-only hook and in the package.

    hook_entry.py must keep its own copy: it runs precisely when the package
    cannot be imported, so it cannot import this text. That makes drift the
    real risk, which is what this test exists to catch -- the same shape as
    test_the_wrapper_and_the_client_agree_on_the_deadline_variable.
    """
    import importlib.util

    from autorun.client import UNRECOVERABLE_GUIDANCE

    hook_path = Path(__file__).resolve().parents[1] / "hooks" / "hook_entry.py"
    spec = importlib.util.spec_from_file_location("_hook_entry_guidance", hook_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module._INTERVENTION_GUIDANCE == UNRECOVERABLE_GUIDANCE


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


def test_client_forwards_its_effective_deadline_to_the_daemon(monkeypatch):
    wrapper_deadline = __import__("time").monotonic() + 10.0
    monkeypatch.setenv("AUTORUN_HOOK_DEADLINE_MONOTONIC", str(wrapper_deadline))
    monkeypatch.setattr(
        "autorun.client.get_stable_process_identity",
        lambda: StableProcessIdentity(12345, 67890),
    )

    payload, _ = prepare_payload_for_daemon({"hook_event_name": "PreToolUse"})

    assert payload["_autorun_hook_deadline_monotonic"] == pytest.approx(
        wrapper_deadline - 0.2
    )


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
        client_total_budget,
    )

    wrapper_timeouts = CONFIG["hook_wrapper_timeouts_seconds"]

    for platform in hook_platforms():
        wrapper = wrapper_timeouts[platform.name]
        budget = client_total_budget(platform.name)

        assert budget < wrapper, (
            f"{platform.name}: the client may spend {budget}s inside a "
            f"{wrapper}s wrapper, leaving no room to write a response"
        )
        assert budget == wrapper - CLIENT_BUDGET_MARGIN_SECONDS

    assert client_total_budget("unknown") == client_total_budget("claude")


def test_the_client_deadline_comes_from_the_wrapper_when_it_supplies_one(monkeypatch):
    """Startup cost belongs to the wrapper's clock, not to a fresh local one.

    Budgeting from the wrapper timeout alone assumes the clock starts inside
    the client, but the wrapper began counting before spawning it, and
    interpreter startup plus a `uv run` resolve is charged to the same
    allowance. The client then overran by its own startup cost and the wrapper
    killed it mid-request, so the harness reported "autorun CLI timed out
    after 5s" and the client's own explanation was never written.
    """
    import time as _time

    from autorun.client import (
        CLIENT_BUDGET_MARGIN_SECONDS,
        DEADLINE_ENV_VAR,
        client_deadline,
        client_total_budget,
    )

    supplied = _time.monotonic() + 2.0
    monkeypatch.setenv(DEADLINE_ENV_VAR, repr(supplied))
    assert client_deadline("claude") == supplied - CLIENT_BUDGET_MARGIN_SECONDS

    # A deadline that already passed, is unparseable, or is implausibly distant
    # is a stale or foreign value; falling back beats failing every hook.
    local_budget = client_total_budget("claude")
    for value in (repr(_time.monotonic() - 5.0), "not-a-float", repr(_time.monotonic() + 3600)):
        monkeypatch.setenv(DEADLINE_ENV_VAR, value)
        assert client_deadline("claude") > _time.monotonic() + local_budget - 1.0

    monkeypatch.delenv(DEADLINE_ENV_VAR, raising=False)
    assert client_deadline("claude") > _time.monotonic()


def test_a_deadline_squeezed_below_the_margin_still_buys_one_attempt(monkeypatch):
    """Subtracting the margin must not hand back an instant already past.

    The margin is slack for the client to write its own response, and it was
    subtracted unconditionally. A wrapper deadline closer than the margin —
    a slow cold start, a `uv run` resolve on Windows — therefore produced a
    deadline in the past, and `forward()` opens with `if remaining <= 0: raise`.
    So the client gave up *without opening a socket* and reported "Daemon failed
    to start after budget exhausted", which is not what happened: a warm daemon
    answers over a unix socket in about a millisecond, and there were still
    tenths of a second left to ask it in.

    The floor is the one `forward()` already uses for its own read timeout, so
    the two agree instead of one guaranteeing the other can never run.
    """
    import time as _time

    from autorun.client import (
        DEADLINE_ENV_VAR,
        MINIMUM_ATTEMPT_SECONDS,
        client_deadline,
    )

    for remaining in (0.19, 0.1, 0.01):
        monkeypatch.setenv(DEADLINE_ENV_VAR, repr(_time.monotonic() + remaining))
        deadline = client_deadline("claude")

        assert deadline > _time.monotonic(), (
            f"{remaining}s left produced an expired deadline; the client would "
            f"report a startup failure without ever connecting"
        )
        assert deadline <= _time.monotonic() + MINIMUM_ATTEMPT_SECONDS + 0.05


def test_the_wrapper_and_the_client_agree_on_the_deadline_variable():
    """One name, spelled in a stdlib-only hook and in the package."""
    import importlib.util

    from autorun.client import DEADLINE_ENV_VAR

    hook_path = Path(__file__).resolve().parents[1] / "hooks" / "hook_entry.py"
    spec = importlib.util.spec_from_file_location("_hook_entry_deadline", hook_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.DEADLINE_ENV_VAR == DEADLINE_ENV_VAR


class TestDaemonRecordIdentity:
    """A pid alone cannot say whether the recorded daemon still exists.

    The number is reused, aggressively so on Windows. The client consults this
    only when no daemon holds the flock, and the one branch that declined to
    spawn without a flock holder trusted `psutil.pid_exists`, so any unrelated
    process inheriting the number kept a stale record alive forever. Every
    attempt then reported "no daemon was spawned by this client" -- a wedge no
    timeout could clear, because nothing was ever starting.
    """

    def test_a_record_matching_a_live_process_is_live(self, tmp_path):
        import os

        from autorun.client import daemon_record_is_live
        from autorun.core import _process_start_units

        record = tmp_path / "daemon.lock"
        record.write_text(f"{os.getpid()} {_process_start_units(os.getpid())}")
        assert daemon_record_is_live(record) is True

    def test_a_reused_pid_is_not_live(self, tmp_path):
        """Same pid, different birth: the process that wrote this is gone."""
        import os

        from autorun.client import daemon_record_is_live
        from autorun.core import _process_start_units

        record = tmp_path / "daemon.lock"
        record.write_text(f"{os.getpid()} {_process_start_units(os.getpid()) - 1}")
        assert daemon_record_is_live(record) is False

    @pytest.mark.parametrize(
        "content",
        [
            "",
            "not-a-pid",
            "4294967295 12345",  # a pid that does not exist
            pytest.param("1234", id="legacy-bare-pid-cannot-be-verified"),
        ],
    )
    def test_an_unverifiable_record_is_not_live(self, tmp_path, content):
        """Refusing to spawn is worse than spawning twice.

        A second daemon loses the flock race and exits; a client that never
        spawns leaves every hook without one for the life of the record.
        """
        from autorun.client import daemon_record_is_live

        record = tmp_path / "daemon.lock"
        record.write_text(content)
        assert daemon_record_is_live(record) is False

    def test_a_missing_record_is_not_live(self, tmp_path):
        from autorun.client import daemon_record_is_live

        assert daemon_record_is_live(tmp_path / "absent.lock") is False


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
    # The template contributes fixed text plus the caller's own message, and
    # nothing else. This used to assert `"command" not in rendered.lower()`,
    # which is weaker than the privacy property it stood for: `message` is
    # interpolated into the reason by design, so a caller passing
    # `rm -rf /home/user/secret` would have leaked while that assertion passed,
    # and the bare word occurs legitimately in autorun's own repair guidance.
    # The real property is enforced at the call sites, below.
    assert "handler timed out" in rendered


def test_no_daemon_failure_call_site_passes_caller_tool_input():
    """Spec check: privacy here is a property of the call sites.

    ``build_daemon_failure_response`` echoes its ``message`` into a reason the
    harness displays, so it cannot defend privacy by itself. What keeps tool
    input out of that reason is that every caller passes a fixed description --
    an event name, a timeout, an exception -- and never the payload.

    REQUIREMENT for new call sites: describe the failure, never quote what the
    user was running.
    """
    import ast

    source_root = Path(__file__).resolve().parents[1] / "src"
    forbidden = ("tool_input", "tool_name", "tool_response", "payload", "stdin")
    leaks = []
    for path in sorted(source_root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "build_daemon_failure_response" not in text:
            continue
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name != "build_daemon_failure_response":
                continue
            for argument in node.args[2:] + [k.value for k in node.keywords]:
                segment = ast.get_source_segment(text, argument) or ""
                if any(token in segment for token in forbidden):
                    leaks.append(f"{path.name}:{argument.lineno}: {segment}")

    assert not leaks, (
        "a fail-closed reason is shown to the user and written to logs; it must "
        "describe the failure, not quote the tool input that hit it:\n  "
        + "\n  ".join(leaks)
    )


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
