#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright 2025 Andrew Hundt <ATHundt@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Autorun v0.7 Client - Thin Forwarder to Daemon

Forwards hook payloads to daemon via Unix socket.
Auto-starts daemon if not running.
Tool-gate failures use local critical guards; other events fail open.

Hook Exit Codes:
----------------
Exit code 0 = hook succeeded (even when denying tool access)
Exit code 2 = blocking ERROR causing "hook error"

The JSON permissionDecision: "deny" blocks the tool, not exit code.

References:
- GitHub Issues: https://github.com/anthropics/claude-code/issues/4669, https://github.com/anthropics/claude-code/issues/18312,
  https://github.com/anthropics/claude-code/issues/13744, https://github.com/anthropics/claude-code/issues/20946
- Exit code semantics: https://claude.com/blog/how-to-configure-hooks
- Hook docs: https://code.claude.com/docs/en/hooks
"""

import re
import os
import sys
import json
import time
import asyncio
import subprocess
import datetime
from dataclasses import dataclass
from pathlib import Path

try:
    from .logging_utils import get_logger, DEBUG_ENABLED

    logger = get_logger(__name__)
except ImportError:
    # Fallback if logging_utils not available (shouldn't happen)
    import logging

    logger = logging.getLogger(__name__)
    logger.addHandler(logging.NullHandler())
    DEBUG_ENABLED = False

from . import ipc
from .platforms import DEGRADED_EVENT_CODE, PLATFORMS as _PLATFORMS

DEBUG_LOG = ipc.AUTORUN_LOG_FILE
#: The autorun tool gates plus every harness name for them in the registry
#: (legacy Gemini's BeforeTool). hooks/hook_entry.py keeps a stdlib copy,
#: pinned to this by test_hook_entry.py.
_AUTORUN_TOOL_GATES = frozenset({"PreToolUse", "PermissionRequest"})
_TOOL_GATE_EVENTS = _AUTORUN_TOOL_GATES | {
    harness_event
    for platform in _PLATFORMS.values()
    for harness_event, autorun_event in platform.harness_cli_to_autorun_events.items()
    if autorun_event in _AUTORUN_TOOL_GATES
}
_STABLE_PID_PARENT_SCAN_DEPTH = 12
_PROCESS_BIRTH_UNITS_PER_SECOND = ipc.PROCESS_BIRTH_UNITS_PER_SECOND
DAEMON_START_RETRY_SECONDS = 0.1
#: Recursion guard only. The real bound is the deadline from
#: client_total_budget(); forward() recurses, so a runaway needs a hard stop.
#: This must never bind before the deadline does, or the client gives up with
#: budget still unspent -- which is what a cap of 8 did, ending a cold start at
#: 0.8s on a host whose interpreter needs longer to boot than that.
#: test_the_attempt_cap_cannot_bind_before_the_deadline holds it above the
#: largest budget divided by this sleep.
DAEMON_START_ATTEMPTS = 64


def harness_process_names() -> dict[str, str]:
    """Map each executable name of a registered harness to its platform name.

    Every platform counts, hook-capable or not (ForgeCode has no hooks but
    still runs in a terminal), so the hook's parent scan and tmux discovery
    share one table derived from ``platforms.PLATFORMS``.
    """
    try:
        from .platforms import PLATFORMS

        return {marker.lower(): platform.name for platform in PLATFORMS.values()
                for marker in (platform.name, platform.binary, *platform.process_names) if marker}
    except Exception:
        names = {name: name for name in ("claude", "gemini", "qwen", "codex")}
        names.update({"claude-code": "claude", "forgecode": "forgecode", "forge": "forgecode"})
        return names


def is_tool_gate_event(event: str) -> bool:
    """Return True when fail-open would allow a tool to run."""
    return event in _TOOL_GATE_EVENTS


def daemon_response_timeout_for_cli(cli_type: str) -> float:
    """Return how long the client should wait for a daemon response.

    Values live in CONFIG so they can be checked against daemon dispatch and
    hook-wrapper budgets. Keeping this path config-backed prevents regressions
    where the client times out before the daemon's own fail-safe budget fires.
    """
    from .config import harness_setting

    return float(harness_setting("daemon_client_response_timeouts_seconds", cli_type))


#: Slack left inside the wrapper budget for reading stdin, detecting the CLI,
#: and writing the response. The client must return *before* the wrapper fires,
#: not exactly when it does.
CLIENT_BUDGET_MARGIN_SECONDS = 0.2

def client_total_budget(cli_type: str) -> float:
    """Total wall-clock the client may spend, cold start and response together.

    A cold start and a response used to be two independent constants that were
    each checked against the wrapper budget but never checked as a sum. They
    exceed it on four of seven harnesses: gemini, antigravity and qwen wait
    0.8s starting the daemon plus 3.5s for a reply against a 4.0s wrapper, and
    opencode 0.8 + 4.0 against 4.5. The wrapper kills the hook first, so the
    client's own bound is unreachable and its failure response -- the one that
    explains what happened -- is never written. This is also why a Windows cold
    start, which is slower than the 0.143s a POSIX host needs, could not fit in
    the fixed 0.8s allowance no matter how the daemon behaved.

    Deriving one budget from the wrapper makes the sum correct by construction:
    a slow cold start spends the response's share rather than failing at a
    constant, and no pair of constants can drift past the wrapper again.
    """
    from .config import harness_setting

    wrapper = float(harness_setting("hook_wrapper_timeouts_seconds", cli_type))
    return max(wrapper - CLIENT_BUDGET_MARGIN_SECONDS, 0.1)


#: Set by hooks/hook_entry.py to the monotonic instant its wrapper gives up.
#: Named once in config.py so a second in-package reader cannot drift from this
#: one; hook_entry.py keeps its own copy because it must stay stdlib-only.
from .config import HOOK_DEADLINE_ENV_VAR as DEADLINE_ENV_VAR  # noqa: E402
from .config import HOOK_DEADLINE_PAYLOAD_KEY as DEADLINE_PAYLOAD_KEY  # noqa: E402
from .config import monotonic_deadline  # noqa: E402
from .config import DEGRADED_CHECK_RESERVE_SECONDS  # noqa: E402

#: How many attempts to allow a pid that is alive but holds no daemon flock.
#: The gap between a daemon starting and taking its flock is short; anything
#: longer is a stale record, and no daemon flock is held either way.
_STALE_PID_PATIENCE_ATTEMPTS = 10

#: How many attempts a daemon this client spawned gets to take its flock
#: before one more is started (2 s at DAEMON_START_RETRY_SECONDS). Cold starts
#: measured 0.6-1.2 s on a Windows runner; one alive but stuck past this (an
#: antivirus scan, a hung import) would otherwise hold the client until its
#: budget ran out.
_SPAWNED_DAEMON_PATIENCE_ATTEMPTS = 20


def daemon_record_is_live(lock_path) -> bool:
    """Whether daemon.lock names a process that is really this daemon.

    The file holds ``"<pid> <start-time-units>"``. A pid on its own does not
    identify anything: the number is reused, aggressively so on Windows, so
    ``psutil.pid_exists`` answers yes for whatever unrelated process inherited
    it. The client then believed a stale record indefinitely and never spawned
    a daemon -- every attempt reported "no daemon was spawned by this client",
    and no timeout could fix it because nothing was ever starting.

    Comparing the recorded start time to the live process rejects a reused pid.
    A record written by an older version carries no start time and cannot be
    verified; it is treated as not live, because the caller only consults this
    when no daemon holds the flock, and spawning a second daemon is recoverable
    while refusing to spawn any is not.
    """
    return ipc.daemon_record_pid(lock_path) is not None


def client_deadline(cli_type: str) -> float:
    """Full hook deadline, preserving the wrapper's clock even when expired."""
    local = time.monotonic() + client_total_budget(cli_type) + CLIENT_BUDGET_MARGIN_SECONDS
    supplied = monotonic_deadline(os.environ.get(DEADLINE_ENV_VAR))
    return local if supplied is None else supplied


def _hook_specific_harness_cli_event_name(event: str, cli_type: str) -> str:
    """Return the harness CLI event name placed in hookSpecificOutput."""
    try:
        from .platforms import to_autorun_event, to_harness_cli_event

        return to_harness_cli_event(to_autorun_event(event, cli_type), cli_type)
    except Exception:
        return event


#: Guidance for a gate failure that waiting cannot clear.
#:
#: Shared verbatim with ``hooks/hook_entry.py:_INTERVENTION_GUIDANCE``. That
#: module runs precisely when this package cannot be imported, so it must keep
#: its own copy rather than import this one; drift between them is caught by
#: test_client_fail_closed.py::test_the_wrapper_and_the_client_agree_on_the_
#: unrecoverable_guidance, the same shape as the DEADLINE_ENV_VAR agreement.
#:
#: Two properties are load-bearing and are asserted, not merely intended.
#:
#: It must not say "then retry". A daemon whose state backend cannot open does
#: not clear by waiting, and hook_entry.py:471-477 records what that advice
#: cost: every attached session looping against a hook that could not succeed.
#:
#: It must name AUTORUN_DISABLE. Under explicit overload_policy=block, or
#: when the package cannot import, every repair command is itself a denied
#: PreToolUse. That historical deadlock (hook_entry.py comments) is why a
#: human must have an exit. Allowlisting a repair command was refused because
#: `uv tool install autorun --with <package>` can execute arbitrary build code.
#: With the default critical-guards policy, clients now allow ordinary tools
#: and consult only shipped guards; state failures no longer deny every tool.
#:
#: Kept ASCII on purpose: the exit-2 deny path prints this to stderr as raw
#: text, whose encoding belongs to whatever launched the harness.
UNRECOVERABLE_GUIDANCE = (
    "Retrying will not help: the same failure repeats. Repair it in a terminal "
    "(run the repair step named in the error, or `autorun --status` for "
    "diagnosis) and the next hook recovers on its own. To stand autorun down, "
    "ask the user to start a new session with AUTORUN_DISABLE=1 set where the "
    "harness is launched; a prefix on a command has no effect."
)


#: Denials the daemon issues while it is healthy but loaded: a handler ran past
#: its budget, or its session is backing off after one did. Both clear on their
#: own after ``daemon_dispatch_timeout_cooldown_seconds``. Agents act on denial
#: text, and on 2026-11-06 about fifteen of them obeyed the restart advice
#: below: twenty restarts in thirty minutes, each one deleting the socket every
#: other session was using. So these say wait, never restart.
LOAD_EVENT_CODES = frozenset({
    "daemon_dispatch_timeout",
    "daemon_dispatch_contained",
    # A tool call whose guard could not take the state lock in time.
    "state_lock_contended",
})

#: A request the daemon never answered. A loaded daemon and a wedged one look
#: the same: the kernel queues the connection for either. Earlier advice said
#: retry once, then restart (with restart coalescing). The 2026-11-06 restart
#: storm above showed why tool failures must never request a restart under
#: load. Now only overload_policy=block reaches this denial text; other
#: policies make a local decision. The JS bridge carries this exact text.
RESPONSE_TIMEOUT_GUIDANCE = (
    "Blocking this tool call because the autorun daemon took the request but "
    "did not answer in time and a local decision is unavailable or overload_policy=block is selected. "
    "Wait and retry when autorun answers again; do not restart the daemon. "
    "If this persists, tell the user; AUTORUN_DISABLE=1 at harness launch "
    "stands autorun down."
)

#: Errors that mean a request was sent and no answer came back. On 3.10
#: asyncio.TimeoutError is not an OSError, so it is listed on its own.
UNANSWERED_REQUEST_ERRORS = (OSError, asyncio.TimeoutError)


class DaemonDidNotAnswer(RuntimeError):
    """The daemon accepted a request and missed the deadline answering it.

    Busy or stuck: the client cannot tell which (see RESPONSE_TIMEOUT_GUIDANCE).
    """


def failure_event_code(error: BaseException) -> str:
    """The event code a client-side failure is reported under."""
    if isinstance(error, DaemonDidNotAnswer):
        return "daemon_response_timeout"
    return "daemon_unavailable_or_timeout"


def load_guidance() -> str:
    """Tell an agent that a load denial clears by itself, and how long to wait."""
    from .config import CONFIG

    cooldown = float(CONFIG["daemon_dispatch_timeout_cooldown_seconds"])
    return (
        "Blocking this tool call because autorun is busy and a local decision "
        "is unavailable or overload_policy=block is selected. "
        f"The session cooldown is {cooldown:g}s: retry the "
        "same tool call after that. Do not run `autorun --restart-daemon`: a "
        "restart cuts off every session sharing the daemon and adds load. If "
        "this repeats for minutes, tell the user; `autorun --status` diagnoses it. "
        "The user can launch the harness with AUTORUN_DISABLE=1 to stand autorun down."
    )


DEGRADED_FAILURE_CODES = LOAD_EVENT_CODES | {
    "daemon_dispatch_stalled", "state_evaluation_failed", "daemon_failure",
    "daemon_response_timeout", "daemon_unavailable_or_timeout",
}
DEGRADED_NOTICE = (
    "Autorun could not answer, so built-in destructive-command guards are still "
    "enforced locally while /ar:no rules, /ar:ok grants, the file policy and "
    "task and plan gating are not checked until autorun answers again. "
    "Do not restart the daemon. If this lasts more than a few minutes, tell "
    "the user: `autorun --status` diagnoses it."
)
DEGRADED_ALLOW_NOTICE = (
    "Autorun could not answer and overload_policy=allow permits this call "
    "without built-in destructive-command guards, /ar:no rules, /ar:ok grants, "
    "the file policy or task and plan gating until autorun answers again. "
    "Do not restart the daemon. If this lasts more than a few minutes, tell "
    "the user: `autorun --status` diagnoses it."
)


_FAILURE_TAG = re.compile(r"^\[autorun\] \[AR_EVENT_V1:([a-z_]+)\]")


def degraded_failure_code(response: dict) -> str | None:
    """Recognize only infrastructure failures, never a normal policy denial."""
    specific = response.get("hookSpecificOutput") or {}
    reason = (specific.get("permissionDecisionReason")
              or (specific.get("decision") or {}).get("message")
              or response.get("reason") or response.get("systemMessage") or "")
    # The daemon puts the tag first. A policy denial can quote the command it
    # blocked, so a tag anywhere else in the text is the command's, not ours.
    match = _FAILURE_TAG.match(reason)
    return match.group(1) if match and match.group(1) in DEGRADED_FAILURE_CODES else None


def _critical_guard_reason(payload: dict, cli_type: str) -> str | None:
    """Shipped block guards only, using the daemon's matcher and predicates."""
    from types import SimpleNamespace
    from .config import DEFAULT_INTEGRATIONS
    from .command_detection import command_matches_pattern, shell_command_from_tool_input
    from .integrations import Integration, check_when_predicate
    from .platforms import hook_payload_cwd

    inputs = payload.get("tool_input") or {}
    ctx = SimpleNamespace(tool_input=inputs, cli_type=cli_type,
                          cwd=hook_payload_cwd(payload) or os.getcwd())
    command = shell_command_from_tool_input(inputs)
    for name, spec in DEFAULT_INTEGRATIONS.items():
        guard = Integration.from_dict(name, spec)
        if guard.event not in {"bash", "all"} or guard.action_for_cli(cli_type) != "block":
            continue
        for pattern in guard.patterns:
            if (command_matches_pattern(command, pattern)
                    and check_when_predicate(guard.when, ctx, pattern, raise_errors=True)):
                from .platforms import format_suggestion
                return format_suggestion(guard.message_for_cli(cli_type), cli_type)
    return None


def degraded_tool_response(payload: dict, cli_type: str, event_code: str,
                           message: str = "Autorun could not produce a normal decision") -> dict:
    """Stateless fallback: guard errors or exhausted budgets deny shell calls
    with wait-and-retry guidance and allow non-shell tools; never consult state.

    A daemonized thread inside this hook bounds guard imports, parsing and git
    probes on all OSes; the hook stops waiting at its remaining deadline.
    """
    from .config import overload_policy
    from .platforms import format_commands_for_cli, is_shell_tool, normalize_tool_payload, platform_for

    event = payload.get("hook_event_name") or payload.get("type") or "PreToolUse"
    if not is_tool_gate_event(event):
        return build_daemon_failure_response(event, cli_type, message, event_code)
    policy = overload_policy()
    if policy == "block":
        return build_daemon_failure_response(event, cli_type, message, event_code)
    decision = "allow"
    reason = DEGRADED_ALLOW_NOTICE if policy == "allow" else DEGRADED_NOTICE
    platform = platform_for(cli_type)
    tool_name, tool_input = normalize_tool_payload(payload, cli_type)
    payload = {**payload, "tool_name": tool_name, "tool_input": tool_input}
    is_shell = is_shell_tool(tool_name, tool_input, cli_type)
    if policy == "critical-guards" and is_shell:
        import queue
        import threading

        result = queue.Queue(maxsize=1)
        def check():
            try:
                result.put((_critical_guard_reason(payload, cli_type), None))
            except Exception as exc:
                result.put((None, exc))

        deadline = monotonic_deadline(payload.get(DEADLINE_PAYLOAD_KEY))
        if deadline is None:
            deadline = client_deadline(cli_type)
        budget = max(0.0, deadline - time.monotonic() - 0.025)
        try:
            if budget <= 0:
                raise TimeoutError("no guard budget remains")
            threading.Thread(target=check, daemon=True).start()
            guard_reason, error = result.get(timeout=budget)
            if error is not None:
                raise error
            if guard_reason:
                decision = "deny"
                reason = (guard_reason + "\nAutorun is degraded and session grants "
                          "(/ar:ok) cannot be consulted right now; retry when it recovers.")
        except Exception as exc:
            logger.warning("Degraded critical guards unavailable: %s", type(exc).__name__)
            decision = "deny"
            reason = ("Autorun is degraded and its critical guards could not finish; "
                      "wait and retry this shell call when it recovers; do not restart the daemon.")
    tagged = format_commands_for_cli(
        f"[autorun] [AR_EVENT_V1:{event_code}] [AR_EVENT_V1:{DEGRADED_EVENT_CODE}] {reason}", cli_type)
    return platform.hook_protocol.degraded_tool_response(
        decision, tagged, _hook_specific_harness_cli_event_name(event, cli_type))


def build_daemon_failure_response(
    event: str,
    cli_type: str,
    message: str,
    event_code: str = "daemon_failure",
) -> dict:
    """Build a platform-correct fallback for daemon communication failures.

    Build the daemon's failure signal or an explicit overload_policy=block
    answer. Hook clients replace gate failures with degraded_tool_response;
    lifecycle/context hooks keep their existing fail-open answer.
    """
    tagged_message = f"[AR_EVENT_V1:{event_code}] {message}".strip()
    if not is_tool_gate_event(event):
        return {
            "continue": True,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": f"[autorun] {tagged_message}" if message else "",
        }

    if event_code in LOAD_EVENT_CODES:
        reason = f"[autorun] {tagged_message}. {load_guidance()}"
    elif event_code == "daemon_response_timeout":
        reason = f"[autorun] {tagged_message}. {RESPONSE_TIMEOUT_GUIDANCE}"
    else:
        reason = (
            f"[autorun] {tagged_message}. Blocking tool use because autorun could "
            f"not evaluate this permission gate; a local decision is unavailable "
            f"or overload_policy=block is selected. "
            f"Do not restart the daemon under load. {UNRECOVERABLE_GUIDANCE}"
        )
    try:
        from .platforms import platform_for

        protocol = platform_for(cli_type).hook_protocol
    except Exception:
        from .platforms import CLAUDE_HOOKS

        protocol = CLAUDE_HOOKS
    harness_event = _hook_specific_harness_cli_event_name(event, cli_type)
    if harness_event == "PermissionRequest":
        return protocol.fail_closed_permission_request_response(reason, harness_event)
    return protocol.fail_closed_pretool_response(reason, harness_event)


def _log_hook_lifecycle(message: str, **kwargs) -> None:
    """DRY helper for hook lifecycle logging. Only active when AUTORUN_DEBUG=1."""
    if not DEBUG_ENABLED:
        return
    try:
        ipc.ensure_config_dir(DEBUG_LOG.parent)
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now()}] {message}\n")
            for key, value in kwargs.items():
                f.write(f"{key}: {value}\n")
    except Exception:
        pass  # Never fail on logging


def output_hook_response(response: dict, event: str = "unknown", cli_type: str = "claude", source: str = "daemon") -> int:
    """Unified hook response output handler with two clear pathways (DRY).

    Single consolidation point for ALL 4 input paths:
    - Path 1: Normal daemon response (success)
    - Path 2: JSON decode error (local gate policy, others unhandled)
    - Path 3: Buffer overflow error (local gate policy, others fail open)
    - Path 4: Exception (local gate policy, others fail open)

    Healthy and degraded denials use the same configured workaround.
    The two output pathways are:
    - Pathway A (Bug #4669 Workaround): JSON + stderr + exit 2
    - Pathway B (Standard): JSON + exit 0

    Args:
        response: Response dict
        event: Normalized event name (e.g., PreToolUse)
        cli_type: Target CLI identifier from autorun.platforms
        source: Source ("daemon", "daemon-raw", "buffer-error", "exception")

    Returns:
        int: Exit code (0, 1, or 2)

    Reference: docs/hooks_api_reference.md lines 395-427
    """
    from .config import overload_policy, should_use_exit2_workaround
    validate = source != "degraded" or overload_policy() == "block"
    if validate:
        from .core import validate_hook_response

    # ═══════════════════════════════════════════════════════════════
    # SHARED: Pass-through — no response (None or {}) means nothing fired
    # ═══════════════════════════════════════════════════════════════
    # Daemon sends {} when dispatch() returned None (no rules matched).
    # Output nothing to stdout → Claude Code ignores this hook entirely.
    # This allows parallel hooks (e.g. RTK) to apply updatedInput without conflict.
    # Reference: Issue https://github.com/anthropics/claude-code/issues/10936 — any stderr at exit 0 shows as "Hook Error" in UI,
    # so we also avoid all stderr here. Just exit 0 silently.
    if not response:
        protocol = None
        try:
            from .platforms import platform_for

            protocol = platform_for(cli_type).hook_protocol
            empty_response = protocol.response_for_unhandled_hook(event)
        except Exception:
            empty_response = {}
        if empty_response or (
            protocol is not None and protocol.requires_json_for_unhandled_hook
        ):
            print(json.dumps(empty_response))
        sys.exit(0)

    # ═══════════════════════════════════════════════════════════════
    # SHARED: Enforce strict schema filtering (CRITICAL for Claude Code)
    # ═══════════════════════════════════════════════════════════════
    # This prevents "Invalid input" errors when daemon returns Gemini-style fields to Claude.
    if validate:
        response = validate_hook_response(event, response, cli_type=cli_type)

    # ═══════════════════════════════════════════════════════════════
    # SHARED: Extract decision (DRY - works for Claude and Gemini)
    # ═══════════════════════════════════════════════════════════════
    decision = response.get("hookSpecificOutput", {}).get("permissionDecision", response.get("decision", "allow"))

    logger.info(f"Hook response: event={event}, cli={cli_type}, source={source}, decision={decision}")

    # ═══════════════════════════════════════════════════════════════
    # SHARED: Always print JSON to stdout first
    # ═══════════════════════════════════════════════════════════════
    print(json.dumps(response))

    # Lifecycle logging before exit (DRY)
    exit_code = 2 if (decision == "deny" and should_use_exit2_workaround({"cli_type": cli_type})) else 0
    _log_hook_lifecycle("DAEMON→CLIENT RESPONSE", Source=source, Decision=decision, ExitCode=exit_code)

    # ═══════════════════════════════════════════════════════════════
    # SINGLE FLAG CHECK: Select pathway
    # ═══════════════════════════════════════════════════════════════
    if exit_code == 2:
        # ╔═══════════════════════════════════════════════════════════╗
        # ║ PATHWAY A: Bug #4669 Workaround (Claude Code)           ║
        # ║ - Print reason to stderr (AI sees this)                 ║
        # ║ - Exit code 2 (ONLY way blocking works in Claude Code)  ║
        # ╚═══════════════════════════════════════════════════════════╝
        reason = response.get("hookSpecificOutput", {}).get("permissionDecisionReason", response.get("reason", "Tool blocked"))

        logger.info("Applying exit-2 workaround (Claude Code bug #4669)")
        print(reason, file=sys.stderr)
        return 2
    else:
        # ╔═══════════════════════════════════════════════════════════╗
        # ║ PATHWAY B: Standard Behavior                             ║
        # ║ - Gemini respects JSON decision field                    ║
        # ║ - Allow decisions in Claude Code                         ║
        # ║ - Exit code 0 (normal success)                           ║
        # ╚═══════════════════════════════════════════════════════════╝
        return 0


@dataclass(frozen=True, slots=True)
class StableProcessIdentity:
    """PID and birth time captured from the same live process object."""

    pid: int
    started_at_units: int | None


def get_stable_process_identity() -> StableProcessIdentity:
    """Traverse to a stable CLI process and capture its birth time.

    Avoids using the ephemeral hook_entry.py/uv/python PID. Looks for any
    hook-capable platform registered in platforms.py, so new harnesses do not
    need a separate branch here. Missing birth evidence remains explicit so it
    cannot be mistaken for session authority.
    """
    fallback_pid = os.getppid()
    try:
        import psutil
    except ImportError:
        return StableProcessIdentity(fallback_pid, None)

    from .tmux_utils import is_named_process

    try:
        markers = harness_process_names()
        current = psutil.Process()
        stable = None
        for _ in range(_STABLE_PID_PARENT_SCAN_DEPTH):
            parent = current.parent()
            if not parent:
                break
            try:
                argv = parent.cmdline()
            except Exception:
                argv = []
            # Match the executable, not its arguments: the hook's own python
            # and uv carry ~/.claude/... paths and would otherwise match.
            if is_named_process(parent.name(), argv, markers):
                stable = parent
                break
            current = parent
        stable = stable or psutil.Process(fallback_pid)
        try:
            started_at_units = round(stable.create_time() * _PROCESS_BIRTH_UNITS_PER_SECOND)
        except (psutil.Error, OSError, ValueError, AttributeError, TypeError):
            started_at_units = None
        return StableProcessIdentity(
            pid=stable.pid,
            started_at_units=started_at_units,
        )
    except (psutil.Error, OSError):
        return StableProcessIdentity(fallback_pid, None)


#: Launchers a harness may start a hook command through. Each exits with its hook,
#: so its PID says nothing about whether the session is still running.
HOOK_LAUNCHERS = frozenset({"sh", "bash", "dash", "zsh", "ksh", "fish", "pwsh", "powershell", "cmd", "uv"})


def _is_windows_venv_redirector(process) -> bool:
    """A venv's Scripts\\python.exe on Windows is a redirector: it starts the
    base interpreter as a child and waits, so the hook's parent is a process
    that exits with the hook. On POSIX the venv interpreter is a symlink and
    no such process exists."""
    if sys.platform != "win32":
        return False
    try:
        return (Path(process.exe()).parent.parent / "pyvenv.cfg").is_file()
    except Exception:
        return False


def launcher_pid(pid: int) -> int:
    """The first ancestor of ``pid`` that is not a hook launcher, or ``pid``.

    hooks/hook_entry.py reports its parent. Claude Code's POSIX command
    `exec`s the interpreter, so that parent is the harness; Gemini CLI runs
    hooks through PowerShell on Windows and Claude Code through Git Bash, and
    there the parent is a shell that exits with the hook, which made a live
    session look ended to plan export (_plan_session_still_running). ``uv run``
    likewise stays between the interpreter and harness until the hook exits.
    """
    try:
        import psutil

        from .tmux_utils import is_named_process

        process = psutil.Process(pid)
        for _ in range(_STABLE_PID_PARENT_SCAN_DEPTH):
            if not (
                is_named_process(process.name(), (), HOOK_LAUNCHERS)
                or _is_windows_venv_redirector(process)
            ):
                return process.pid
            parent = process.parent()
            if parent is None:
                break
            process = parent
    except Exception:  # psutil missing, process gone, or access denied
        pass
    return pid


def resolve_launcher_pid(payload: dict) -> None:
    """Point a hook payload's ``_pid`` past hook launchers, in place.

    A payload carrying ``_pid_started_at_units`` came from
    get_stable_process_identity, which already walked to the harness.
    """
    pid = payload.get("_pid")
    if isinstance(pid, int) and pid > 0 and "_pid_started_at_units" not in payload:
        resolved_pid = launcher_pid(pid)
        payload["_pid"] = resolved_pid
        started_at_units = ipc.process_start_units(resolved_pid)
        if started_at_units > 0:
            payload["_pid_started_at_units"] = started_at_units


def prepare_payload_for_daemon(payload: dict | None) -> tuple[dict, str]:
    """Add client-side runtime context and explicit CLI identity for the daemon.

    The daemon runs in a separate process, so environment variables set by
    `autorun --cli ...` in this short-lived client are not a reliable identity
    channel. Persist the resolved cli_type into the JSON payload before sending.
    """
    payload = dict(payload or {})
    from .config import detect_cli_type

    cli_type = detect_cli_type(payload)
    # Stamp before process discovery; an upstream deadline is never refreshed.
    deadline = monotonic_deadline(payload.get(DEADLINE_PAYLOAD_KEY))
    if deadline is None:
        deadline = client_deadline(cli_type)
    payload[DEADLINE_PAYLOAD_KEY] = time.time() + deadline - time.monotonic()

    # Inject context for daemon lifecycle management. A _pid already present
    # came from hooks/hook_entry.py (os.getppid(), on both of its paths) or a
    # JS bridge (its own pid) and wins; the walk is for direct invocations.
    if "_pid" not in payload:
        process = get_stable_process_identity()
        payload["_pid"] = process.pid
        payload["_pid_started_at_units"] = process.started_at_units
    if "_cwd" not in payload:
        # Every supported harness reports the project directory in the payload's
        # "cwd" field (Claude Code, Gemini CLI, Qwen Code, Antigravity, Codex).
        # Prefer it over this process's own working directory: the two usually
        # coincide, but not when the hook runs under the daemon, from a git
        # worktree, or from a harness launched elsewhere — and plan_export.py's
        # project_dir uses this value to choose which project's notes/ directory
        # receives the archived plan.
        from .platforms import hook_payload_cwd
        payload["_cwd"] = hook_payload_cwd(payload) or os.getcwd()

    payload["cli_type"] = cli_type

    return payload, cli_type


def run_client() -> int:
    """Forward hook payload to daemon.

    Returns:
        int: Exit code (0, 1, or 2)
    """
    # Read stdin payload
    payload = {}
    try:
        if not sys.stdin.isatty():
            payload = json.load(sys.stdin)
    except Exception:
        pass

    payload, cli_type = prepare_payload_for_daemon(payload)
    return asyncio.run(forward_prepared_payload(payload, cli_type))


async def forward_prepared_payload(payload: dict, cli_type: str) -> int:
    """Use one absolute hook deadline stamped before setup: every daemon wait
    ends at deadline - reserve, leaving local guards their reserved time; if
    that instant has passed, skip daemon contact and check locally immediately.
    """
    # Lifecycle logging (DRY)
    hook_event = payload.get("hook_event_name") or payload.get("type") or "unknown"
    hook_source = payload.get("source", "")
    tool_name = payload.get("tool_name", "")

    _log_hook_lifecycle("\n" + "=" * 80 + "\nCLIENT→DAEMON REQUEST", Event=hook_event, Source=hook_source, Tool=tool_name, PayloadKeys=list(payload.keys()))

    logger.debug(f"Forwarding hook to daemon: event={hook_event}, cli={cli_type}, tool={tool_name}")

    # The connection error from the most recent attempt. Without it the caller
    # is told only how many times the retry ran, which is the same message for
    # a missing endpoint, a refused connection, and a daemon that exits on
    # startup. A list because forward() rebinds it from a nested scope.
    last_connect_error: list = []
    # Handles for daemons this client spawned. Popen was previously called and
    # the result dropped, so a child that exited immediately looked exactly
    # like one still booting: the client retried until its budget ran out and
    # reported that no daemon answered, never that the daemon it started was
    # already dead or why.
    spawned: list = []
    # forward() attempt number of the latest spawn; a list for the nested scope.
    last_spawn_attempt: list = []
    waited_for_daemon = False
    deadline = monotonic_deadline(payload[DEADLINE_PAYLOAD_KEY])
    if deadline is None:  # prepare_payload_for_daemon set it; never fail open on a bad value
        deadline = client_deadline(cli_type)
    deadline -= DEGRADED_CHECK_RESERVE_SECONDS if is_tool_gate_event(hook_event) else CLIENT_BUDGET_MARGIN_SECONDS

    def remaining_wait() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError("daemon budget exhausted; local check reserve remains")
        return remaining

    def _spawn_outcome() -> str:
        if not spawned:
            return "no daemon was spawned by this client"
        codes = [process.poll() for process in spawned]
        if all(code is None for code in codes):
            return f"{len(codes)} spawned daemon(s) still running"
        # A Windows child that cannot initialise exits before running any of
        # our code, which is why its log stays empty; the exit status is then
        # the only evidence there is. 3221225785 (0xC0000142) is a DLL
        # initialisation failure, and 1 with an empty log usually means the
        # interpreter died before logging was configured.
        return "spawned daemon exit codes: " + ", ".join(
            "running" if code is None else str(code) for code in codes
        )

    async def forward(depth: int = 0):
        nonlocal waited_for_daemon
        remaining = deadline - time.monotonic()
        if depth >= DAEMON_START_ATTEMPTS or remaining <= 0:
            cause = (
                f": last connection error was {last_connect_error[0]!r}"
                if last_connect_error
                else ""
            )
            spent = "budget exhausted" if remaining <= 0 else f"{DAEMON_START_ATTEMPTS} attempts"
            raise RuntimeError(
                f"Daemon failed to start after {spent}"
                f"{cause}. {_spawn_outcome()}. Daemon startup output, if any, "
                f"is in {ipc.AUTORUN_LOG_FILE}"
            )
        sent = False
        try:
            from .ipc import READ_BUFFER_LIMIT

            wait = remaining_wait()
            reader, writer = await asyncio.wait_for(
                ipc.connect(limit=READ_BUFFER_LIMIT),
                timeout=wait,
            )
            try:
                remaining_wait()
                sent = True
                writer.write(json.dumps(payload).encode() + b"\n")
                wait = remaining_wait()
                await asyncio.wait_for(
                    writer.drain(),
                    timeout=wait,
                )

                wait = min(daemon_response_timeout_for_cli(cli_type), remaining_wait())
                resp = await asyncio.wait_for(
                    reader.readuntil(b"\n"),
                    # Whichever is smaller: the configured per-harness wait, or
                    # what is left of the shared budget. A cold start that
                    # already spent part of the budget must not then start a
                    # full-length response wait and push the total past the
                    # wrapper timeout.
                    timeout=wait,
                )
                resp_text = resp.decode().strip()

                _log_hook_lifecycle("DAEMON→CLIENT RAW RESPONSE", FullResponse=resp_text)

                # Parse response and route through unified output handler
                try:
                    resp_json = json.loads(resp_text)
                    code = degraded_failure_code(resp_json) if is_tool_gate_event(hook_event) else None
                    if code:
                        return output_hook_response(
                            degraded_tool_response(payload, cli_type, code),
                            event=hook_event, cli_type=cli_type, source="degraded")
                    return output_hook_response(resp_json, event=hook_event, cli_type=cli_type, source="daemon")
                except json.JSONDecodeError:
                    if is_tool_gate_event(hook_event):
                        return output_hook_response(
                            degraded_tool_response(payload, cli_type, "daemon_failure", "Daemon returned invalid JSON"),
                            event=hook_event,
                            cli_type=cli_type,
                            source="degraded",
                        )
                    # Not a reply this harness can read: Gemini CLI, Qwen
                    # Code and Antigravity require JSON on stdout, so answer
                    # as if the daemon had nothing to say.
                    logger.warning("Daemon returned invalid JSON for %s: %.200s", hook_event, resp_text)
                    return output_hook_response({}, event=hook_event, cli_type=cli_type, source="daemon-invalid-json")
            finally:
                writer.close()
                try:
                    wait = deadline - time.monotonic()
                    if wait > 0:
                        await asyncio.wait_for(writer.wait_closed(), timeout=wait)
                except (ConnectionError, OSError, asyncio.TimeoutError):
                    pass

        except asyncio.LimitOverrunError as e:
            # Response from daemon exceeded buffer (shouldn't happen - response is tiny)
            logger.error(f"Client buffer error: {e}")
            return output_hook_response(
                degraded_tool_response(payload, cli_type, "daemon_failure", f"Client buffer error: Daemon response too large. {e}"),
                event=hook_event,
                cli_type=cli_type,
                source="degraded" if is_tool_gate_event(hook_event) else "buffer-error",
            )
        except UNANSWERED_REQUEST_ERRORS as e:
            if isinstance(e, PermissionError):
                raise  # Can't recover from permission errors
            if sent:
                # A daemon accepted the connection and may already be running
                # this hook, so sending it again could run it twice. A
                # response timeout lands here too, on every Python version.
                raise DaemonDidNotAnswer(
                    f"Daemon accepted the request but did not answer: {e!r}"
                ) from e

            remaining_wait()  # Do not start/retry a daemon using the guard reserve.

            last_connect_error[:] = [e]
            should_spawn = False

            # === RESTART-AWARE SPAWN DECISION ===
            # Check two locks before deciding to spawn:
            #   1. restart_lock — is a restart in progress?
            #   2. daemon flock — is a daemon alive?
            # If either is held, do NOT spawn — just wait and retry.
            # Advisory locks are kernel-managed: released on process death (POSIX guarantee).

            # Check 1: Is a restart in progress?
            restart_in_progress = False
            try:
                from filelock import FileLock, Timeout as FlockTimeout

                restart_probe = FileLock(str(ipc.daemon_restart_lock_path()), timeout=0)
                restart_probe.acquire()
                restart_probe.release()
                # restart_lock is free — no restart in progress
            except FlockTimeout:
                restart_in_progress = True
                waited_for_daemon = True
                logger.debug(f"Restart in progress, waiting (depth={depth})")
            except (FileNotFoundError, OSError):
                pass  # Lock file dir doesn't exist — no restart in progress

            if not restart_in_progress:
                # Check 2: Is a daemon alive (holding flock)?
                try:
                    daemon_probe = FileLock(str(ipc.daemon_flock_path()), timeout=0)
                    daemon_probe.acquire()
                    daemon_probe.release()
                    # Flock is free — no daemon holds it
                    # Check PID file for process that hasn't cleaned up
                    lock_path = ipc.AUTORUN_LOCK_PATH
                    if lock_path.exists():
                        if daemon_record_is_live(lock_path):
                            waited_for_daemon = True
                            # A daemon that has started but not yet taken the
                            # flock. Real, and narrow, so it is worth waiting
                            # -- but only so long: this branch is the single
                            # path that declines to spawn while no daemon holds
                            # the flock, and waiting in it forever is what left
                            # every attempt reporting that no daemon was
                            # spawned.
                            #
                            # The record is NOT removed here. It names a
                            # process that demonstrably exists, and deleting a
                            # healthy daemon's discovery record to start a
                            # competitor breaks the daemon that was about to
                            # answer. Spawning alongside it is safe on its own:
                            # the flock decides, and the loser exits.
                            if depth >= _STALE_PID_PATIENCE_ATTEMPTS:
                                should_spawn = True
                        else:
                            lock_path.unlink(missing_ok=True)
                            should_spawn = True
                    else:
                        should_spawn = True
                except FlockTimeout:
                    waited_for_daemon = True
                    # Daemon flock held — daemon is alive, socket may be starting
                    logger.debug(f"Daemon flock held, waiting (depth={depth})")
                except (FileNotFoundError, OSError):
                    # Config dir doesn't exist (first run) — spawn daemon
                    should_spawn = True

            # The daemon takes its flock only after importing autorun, so the
            # checks above cannot see one this client started a moment ago.
            # Spawning again on every retry started one duplicate per 0.1 s;
            # on Windows, where that import takes most of a second, they
            # competed with the real daemon until the hook budget ran out.
            if (
                should_spawn
                and any(process.poll() is None for process in spawned)
                and depth - last_spawn_attempt[-1] < _SPAWNED_DAEMON_PATIENCE_ATTEMPTS
            ):
                should_spawn = False
            if should_spawn:
                logger.info("Daemon not running, auto-starting...")
                src_dir = Path(__file__).parent.parent
                # !r, not a quoted {0}: on Windows src_dir is C:\Users\...,
                # and inside a plain literal \U is an invalid escape, so the
                # spawned interpreter died with a SyntaxError. The daemon then
                # never existed, every hook fell through to the CLI, and the
                # CLI waited on the daemon it had just failed to start until
                # the caller's timeout -- the "autorun CLI timed out after 5s"
                # every Windows event reported. repr() quotes and escapes.
                daemon_code = (
                    "import sys; sys.path.insert(0, {0!r}); "
                    "from autorun.daemon import main; main()"
                ).format(str(src_dir))
                # stderr goes to the daemon's own log, not DEVNULL. A daemon
                # that dies during startup used to leave nothing behind, so an
                # import error, a failed bind, and a merely slow start all
                # produced the same "Daemon failed to start after N attempts"
                # with no way to tell them apart -- which is the state Windows
                # reported with no log to explain it. This is the daemon's
                # stderr written to a file, never a hook's stream, so it cannot
                # be read as a hook failure.
                try:
                    ipc.ensure_config_dir()
                    startup_log = open(
                        ipc.AUTORUN_LOG_FILE, "a", encoding="utf-8", errors="replace"
                    )
                except OSError:
                    startup_log = None
                last_spawn_attempt.append(depth)
                try:
                    spawned.append(
                        subprocess.Popen(
                            [sys.executable, "-c", daemon_code],
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=startup_log or subprocess.DEVNULL,
                            **ipc.detached_spawn_kwargs(),
                        )
                    )
                finally:
                    # The child keeps its own duplicate of the descriptor, so
                    # closing this one leaves the daemon's stderr intact and
                    # does not leak a handle per spawn attempt.
                    if startup_log is not None:
                        startup_log.close()
            else:
                logger.debug(f"Waiting for daemon (depth={depth})")

            # Never sleep past the shared deadline: the next attempt has to be
            # able to report the failure while the wrapper is still listening.
            await asyncio.sleep(
                min(DAEMON_START_RETRY_SECONDS, max(deadline - time.monotonic(), 0.0))
            )
            return await forward(depth + 1)

    try:
        return await forward()
    except Exception as e:
        logger.error(f"Client exception while contacting daemon: {e}", exc_info=True)
        return output_hook_response(
            degraded_tool_response(
                payload,
                cli_type,
                event_code=(
                    "daemon_response_timeout"
                    if waited_for_daemon and isinstance(e, RuntimeError)
                    else failure_event_code(e)
                ),
                message=f"Daemon unavailable or timed out: {e}",
            ),
            event=hook_event,
            cli_type=cli_type,
            source="degraded" if is_tool_gate_event(hook_event) else "exception",
        )


if __name__ == "__main__":
    from .logging_utils import use_utf8_stdio

    use_utf8_stdio()
    run_client()
