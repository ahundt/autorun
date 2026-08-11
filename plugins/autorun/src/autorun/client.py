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
Fails open on any errors.

Hook Exit Codes:
----------------
Exit code 0 = hook succeeded (even when denying tool access)
Exit code 2 = blocking ERROR causing "hook error"

The JSON permissionDecision: "deny" blocks the tool, not exit code.

References:
- GitHub Issues: #4669, #18312, #13744, #20946
- Exit code semantics: https://claude.com/blog/how-to-configure-hooks
- Hook docs: https://code.claude.com/docs/en/hooks
"""

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
    DEBUG_ENABLED = False

from . import ipc

DEBUG_LOG = ipc.AUTORUN_LOG_FILE
_TOOL_GATE_EVENTS = {"PreToolUse", "BeforeTool", "PermissionRequest"}
_STABLE_PID_PARENT_SCAN_DEPTH = 12
_PROCESS_BIRTH_UNITS_PER_SECOND = 1_000_000
DAEMON_START_RETRY_SECONDS = 0.1
#: Recursion guard only. The real bound is the deadline from
#: client_total_budget(); forward() recurses, so a runaway needs a hard stop.
#: This must never bind before the deadline does, or the client gives up with
#: budget still unspent -- which is what a cap of 8 did, ending a cold start at
#: 0.8s on a host whose interpreter needs longer to boot than that.
#: test_the_attempt_cap_cannot_bind_before_the_deadline holds it above the
#: largest budget divided by this sleep.
DAEMON_START_ATTEMPTS = 64


def _hook_platform_process_markers() -> tuple[str, ...]:
    """Return process-name markers for hook-capable CLI parents."""
    try:
        from .platforms import hook_platforms

        markers = {marker.lower() for platform in hook_platforms() for marker in (platform.name, platform.binary) if marker}
        # Common installed process name for Claude Code; kept as compatibility data
        # beside the registry-derived names instead of a separate branch.
        markers.add("claude-code")
        return tuple(sorted(markers, key=len, reverse=True))
    except Exception:
        return ("claude-code", "forgecode", "claude", "gemini", "qwen", "codex", "forge")


def is_tool_gate_event(event: str) -> bool:
    """Return True when fail-open would allow a tool to run."""
    return event in _TOOL_GATE_EVENTS


def daemon_response_timeout_for_cli(cli_type: str) -> float:
    """Return how long the client should wait for a daemon response.

    Values live in CONFIG so they can be checked against daemon dispatch and
    hook-wrapper budgets. Keeping this path config-backed prevents regressions
    where the client times out before the daemon's own fail-safe budget fires.
    """
    from .config import CONFIG

    timeouts = CONFIG["daemon_client_response_timeouts_seconds"]
    return float(timeouts.get(cli_type, timeouts["claude"]))


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
    from .config import CONFIG

    wrappers = CONFIG["hook_wrapper_timeouts_seconds"]
    wrapper = float(wrappers.get(cli_type, wrappers["claude"]))
    return max(wrapper - CLIENT_BUDGET_MARGIN_SECONDS, 0.1)


#: Set by hooks/hook_entry.py to the monotonic instant its wrapper gives up.
DEADLINE_ENV_VAR = "AUTORUN_HOOK_DEADLINE_MONOTONIC"


def client_deadline(cli_type: str) -> float:
    """The monotonic instant this client must stop by.

    Prefer the wrapper's own deadline when it supplied one. Budgeting from the
    wrapper timeout alone assumes the clock starts when this function runs, but
    the wrapper started counting before spawning this process, and interpreter
    startup -- plus a ``uv run`` resolve, which is not cheap on Windows -- comes
    out of the same allowance. Deriving locally therefore overruns by exactly
    the startup cost, and the wrapper kills the client before it can write the
    failure response that explains itself. That is the difference between the
    client reporting why the daemon did not answer and the harness reporting
    only "autorun CLI timed out".

    ``time.monotonic()`` is system-wide on Linux, macOS and Windows, so the
    instant recorded in the parent is meaningful here. A value that is absent,
    unparseable, already past, or improbably far ahead falls back to the local
    budget: a stale variable inherited from an unrelated process must not make
    every hook fail instantly.
    """
    local = time.monotonic() + client_total_budget(cli_type)
    raw = os.environ.get(DEADLINE_ENV_VAR)
    if not raw:
        return local
    try:
        supplied = float(raw)
    except (TypeError, ValueError):
        return local
    remaining = supplied - time.monotonic()
    if remaining <= 0 or remaining > _MAX_PLAUSIBLE_WRAPPER_SECONDS:
        return local
    return supplied - CLIENT_BUDGET_MARGIN_SECONDS


#: Any wrapper budget beyond this is a stale or foreign value, not ours. The
#: largest configured wrapper timeout is 5s; 60s leaves room for a future one
#: while still rejecting a deadline inherited from an unrelated process.
_MAX_PLAUSIBLE_WRAPPER_SECONDS = 60.0


def _hook_specific_harness_cli_event_name(event: str, cli_type: str) -> str:
    """Return the harness CLI event name placed in hookSpecificOutput."""
    try:
        from .platforms import to_autorun_event, to_harness_cli_event

        return to_harness_cli_event(to_autorun_event(event, cli_type), cli_type)
    except Exception:
        return event


def build_daemon_failure_response(
    event: str,
    cli_type: str,
    message: str,
    event_code: str = "daemon_failure",
) -> dict:
    """Build a platform-correct fallback for daemon communication failures.

    Permission-gate hooks fail closed. Lifecycle/context hooks fail open.
    """
    tagged_message = f"[AR_EVENT_V1:{event_code}] {message}".strip()
    if not is_tool_gate_event(event):
        return {
            "continue": True,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": f"[autorun] {tagged_message}" if message else "",
        }

    reason = (
        f"[autorun] {tagged_message}. Blocking tool use because autorun could not evaluate this permission gate. Run `autorun --restart-daemon`, then retry."
    )
    try:
        from .platforms import platform_for

        protocol = platform_for(cli_type).hook_protocol
    except Exception:
        from .platforms import CLAUDE_HOOKS

        protocol = CLAUDE_HOOKS
    return protocol.fail_closed_pretool_response(reason, _hook_specific_harness_cli_event_name(event, cli_type))


def _log_hook_lifecycle(message: str, **kwargs) -> None:
    """DRY helper for hook lifecycle logging. Only active when AUTORUN_DEBUG=1."""
    if not DEBUG_ENABLED:
        return
    try:
        DEBUG_LOG.parent.mkdir(exist_ok=True)
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now()}] {message}\n")
            for key, value in kwargs.items():
                f.write(f"{key}: {value}\n")
    except Exception:
        pass  # Never fail on logging


def output_hook_response(response: dict | str, event: str = "unknown", cli_type: str = "claude", source: str = "daemon") -> int:
    """Unified hook response output handler with two clear pathways (DRY).

    Single consolidation point for ALL 4 input paths:
    - Path 1: Normal daemon response (success)
    - Path 2: JSON decode error (fallback)
    - Path 3: Buffer overflow error (fail-open)
    - Path 4: Exception (fail-open)

    TWO OUTPUT PATHWAYS selected by single flag check:
    - Pathway A (Bug #4669 Workaround): JSON + stderr + exit 2
    - Pathway B (Standard): JSON + exit 0

    Args:
        response: Response dict OR raw string (for fallback cases)
        event: Normalized event name (e.g., PreToolUse)
        cli_type: Target CLI identifier from autorun.platforms
        source: Source ("daemon", "daemon-raw", "buffer-error", "exception")

    Returns:
        int: Exit code (0, 1, or 2)

    Reference: docs/hooks_api_reference.md lines 395-427
    """
    from .config import should_use_exit2_workaround
    from .core import validate_hook_response

    # ═══════════════════════════════════════════════════════════════
    # SHARED: Pass-through — no response (None or {}) means nothing fired
    # ═══════════════════════════════════════════════════════════════
    # Daemon sends {} when dispatch() returned None (no rules matched).
    # Output nothing to stdout → Claude Code ignores this hook entirely.
    # This allows parallel hooks (e.g. RTK) to apply updatedInput without conflict.
    # Reference: Issue #10936 — any stderr at exit 0 shows as "Hook Error" in UI,
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
    # SHARED: Handle raw string fallback (JSON decode error)
    # ═══════════════════════════════════════════════════════════════
    if isinstance(response, str):
        logger.debug(f"Outputting raw response from {source}")
        print(response)
        return 0

    # ═══════════════════════════════════════════════════════════════
    # SHARED: Enforce strict schema filtering (CRITICAL for Claude Code)
    # ═══════════════════════════════════════════════════════════════
    # This prevents "Invalid input" errors when daemon returns Gemini-style fields to Claude.
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
    if decision == "deny" and should_use_exit2_workaround({"cli_type": cli_type}):
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

    try:
        markers = _hook_platform_process_markers()
        current = psutil.Process()
        stable = None
        for _ in range(_STABLE_PID_PARENT_SCAN_DEPTH):
            parent = current.parent()
            if not parent:
                break
            name = parent.name().lower()
            try:
                cmdline = " ".join(parent.cmdline()).lower()
            except Exception:
                cmdline = ""
            if any(marker in name or marker in cmdline for marker in markers):
                stable = parent
                break
            current = parent
        stable = stable or psutil.Process(fallback_pid)
        try:
            started_at_units = round(stable.create_time() * _PROCESS_BIRTH_UNITS_PER_SECOND)
        except (psutil.Error, ValueError, AttributeError, TypeError):
            started_at_units = None
        return StableProcessIdentity(
            pid=stable.pid,
            started_at_units=started_at_units,
        )
    except psutil.Error:
        return StableProcessIdentity(fallback_pid, None)


def get_stable_pid() -> int:
    """Compatibility accessor for callers that need only the stable PID."""
    return get_stable_process_identity().pid


def prepare_payload_for_daemon(payload: dict | None) -> tuple[dict, str]:
    """Add client-side runtime context and explicit CLI identity for the daemon.

    The daemon runs in a separate process, so environment variables set by
    `autorun --cli ...` in this short-lived client are not a reliable identity
    channel. Persist the resolved cli_type into the JSON payload before sending.
    """
    payload = dict(payload or {})

    # Inject context for daemon lifecycle management.
    process = get_stable_process_identity()
    if "_pid" not in payload:
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
        payload["_cwd"] = payload.get("cwd") or os.getcwd()

    from .config import detect_cli_type

    cli_type = detect_cli_type(payload)
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

    # Lifecycle logging (DRY)
    hook_event = payload.get("hook_event_name", "unknown")
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
    deadline = client_deadline(cli_type)

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
        try:
            from .core import READ_BUFFER_LIMIT

            reader, writer = await ipc.connect(limit=READ_BUFFER_LIMIT)
            try:
                writer.write(json.dumps(payload).encode() + b"\n")
                await writer.drain()

                resp = await asyncio.wait_for(
                    reader.readuntil(b"\n"),
                    # Whichever is smaller: the configured per-harness wait, or
                    # what is left of the shared budget. A cold start that
                    # already spent part of the budget must not then start a
                    # full-length response wait and push the total past the
                    # wrapper timeout.
                    timeout=min(
                        daemon_response_timeout_for_cli(cli_type),
                        max(deadline - time.monotonic(), 0.05),
                    ),
                )
                resp_text = resp.decode().strip()

                _log_hook_lifecycle("DAEMON→CLIENT RAW RESPONSE", FullResponse=resp_text)

                # Parse response and route through unified output handler
                try:
                    resp_json = json.loads(resp_text)
                    return output_hook_response(resp_json, event=hook_event, cli_type=cli_type, source="daemon")
                except json.JSONDecodeError:
                    if is_tool_gate_event(hook_event):
                        return output_hook_response(
                            build_daemon_failure_response(
                                hook_event,
                                cli_type,
                                "Daemon returned invalid JSON",
                            ),
                            event=hook_event,
                            cli_type=cli_type,
                            source="daemon-invalid-json",
                        )
                    # Not valid JSON, output as-is
                    return output_hook_response(resp_text, event=hook_event, cli_type=cli_type, source="daemon-raw")
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass

        except asyncio.LimitOverrunError as e:
            # Response from daemon exceeded buffer (shouldn't happen - response is tiny)
            logger.error(f"Client buffer error: {e}")
            return output_hook_response(
                build_daemon_failure_response(
                    hook_event,
                    cli_type,
                    f"Client buffer error: Daemon response too large. {e}",
                ),
                event=hook_event,
                cli_type=cli_type,
                source="buffer-error",
            )
        except (FileNotFoundError, ConnectionRefusedError, PermissionError, OSError) as e:
            if isinstance(e, PermissionError):
                raise  # Can't recover from permission errors

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

                restart_lock_path = ipc.AUTORUN_CONFIG_DIR / "daemon-restart.lock"
                restart_probe = FileLock(str(restart_lock_path), timeout=0)
                restart_probe.acquire()
                restart_probe.release()
                # restart_lock is free — no restart in progress
            except FlockTimeout:
                restart_in_progress = True
                logger.debug(f"Restart in progress, waiting (depth={depth})")
            except (FileNotFoundError, OSError):
                pass  # Lock file dir doesn't exist — no restart in progress

            if not restart_in_progress:
                # Check 2: Is a daemon alive (holding flock)?
                try:
                    flock_path = ipc.AUTORUN_LOCK_PATH.with_suffix(".flock")
                    daemon_probe = FileLock(str(flock_path), timeout=0)
                    daemon_probe.acquire()
                    daemon_probe.release()
                    # Flock is free — no daemon holds it
                    # Check PID file for process that hasn't cleaned up
                    lock_path = ipc.AUTORUN_LOCK_PATH
                    if lock_path.exists():
                        try:
                            pid = int(lock_path.read_text().strip())
                            import psutil

                            if psutil.pid_exists(pid):
                                pass  # PID alive but socket not ready — wait
                            else:
                                lock_path.unlink(missing_ok=True)
                                should_spawn = True
                        except (ValueError, OSError):
                            lock_path.unlink(missing_ok=True)
                            should_spawn = True
                    else:
                        should_spawn = True
                except FlockTimeout:
                    # Daemon flock held — daemon is alive, socket may be starting
                    logger.debug(f"Daemon flock held, waiting (depth={depth})")
                except (FileNotFoundError, OSError):
                    # Config dir doesn't exist (first run) — spawn daemon
                    should_spawn = True

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
        return asyncio.run(forward())
    except Exception as e:
        logger.error(f"Client exception while contacting daemon: {e}", exc_info=True)
        return output_hook_response(
            build_daemon_failure_response(
                hook_event,
                cli_type,
                f"Daemon unavailable or timed out: {e}",
                event_code="daemon_unavailable_or_timeout",
            ),
            event=hook_event,
            cli_type=cli_type,
            source="exception",
        )


if __name__ == "__main__":
    run_client()
