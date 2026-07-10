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
import asyncio
import subprocess
import datetime
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


def _hook_platform_process_markers() -> tuple[str, ...]:
    """Return process-name markers for hook-capable CLI parents."""
    try:
        from .platforms import hook_platforms

        markers = {
            marker.lower()
            for platform in hook_platforms()
            for marker in (platform.name, platform.binary)
            if marker
        }
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


def _hook_specific_event_name(event: str, cli_type: str) -> str:
    """Return the platform event name used inside hookSpecificOutput."""
    try:
        from .platforms import platform_for

        platform = platform_for(cli_type)
        return platform.internal_to_cli_events.get(event, event)
    except Exception:
        if event == "BeforeTool":
            return "PreToolUse"
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
        f"[autorun] {tagged_message}. Blocking tool use because autorun could not "
        "evaluate this permission gate. Run `autorun --restart-daemon`, then retry."
    )
    hook_specific = {
        "hookEventName": _hook_specific_event_name(event, cli_type),
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }

    if cli_type == "codex":
        return {
            "decision": "block",
            "reason": reason,
            "systemMessage": reason,
            "hookSpecificOutput": hook_specific,
        }

    try:
        from .platforms import platform_for

        schema_type = platform_for(cli_type).schema_type
    except Exception:
        schema_type = "strict"

    if schema_type == "permissive":
        return {
            "decision": "deny",
            "reason": reason,
            "continue": True,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": reason,
            "hookSpecificOutput": hook_specific,
        }

    return {
        "decision": "block",
        "permissionDecision": "deny",
        "reason": "",
        "continue": True,
        "stopReason": "",
        "suppressOutput": False,
        "systemMessage": "",
        "hookSpecificOutput": hook_specific,
    }


def _log_hook_lifecycle(message: str, **kwargs) -> None:
    """DRY helper for hook lifecycle logging. Only active when AUTORUN_DEBUG=1."""
    if not DEBUG_ENABLED:
        return
    try:
        DEBUG_LOG.parent.mkdir(exist_ok=True)
        with open(DEBUG_LOG, 'a', encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now()}] {message}\n")
            for key, value in kwargs.items():
                f.write(f"{key}: {value}\n")
    except Exception:
        pass  # Never fail on logging


def output_hook_response(response: dict | str, event: str = "unknown", 
                         cli_type: str = "claude", source: str = "daemon") -> int:
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

    Reference: notes/hooks_api_reference.md lines 395-427
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
        try:
            from .platforms import platform_for

            schema_type = platform_for(cli_type).schema_type
        except Exception:
            schema_type = "strict"
        if schema_type == "permissive":
            # Gemini-family CLIs expect valid JSON if a hook is registered
            print(json.dumps({"continue": True}))
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
    decision = response.get('hookSpecificOutput', {}).get('permissionDecision',
                                                          response.get('decision', 'allow'))

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
        reason = response.get('hookSpecificOutput', {}).get('permissionDecisionReason',
                                                            response.get('reason', 'Tool blocked'))

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


def get_stable_pid() -> int:
    """Traverse up process tree to find the stable CLI process ID.

    Avoids using the ephemeral hook_entry.py/uv/python PID. Looks for any
    hook-capable platform registered in platforms.py, so new harnesses do not
    need a separate branch here. Falls back to ppid if discovery fails.
    """
    try:
        import psutil

        markers = _hook_platform_process_markers()
        current = psutil.Process()
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
                return parent.pid
            current = parent
    except (ImportError, Exception):
        pass
    return os.getppid()


def prepare_payload_for_daemon(payload: dict | None) -> tuple[dict, str]:
    """Add client-side runtime context and explicit CLI identity for the daemon.

    The daemon runs in a separate process, so environment variables set by
    `autorun --cli ...` in this short-lived client are not a reliable identity
    channel. Persist the resolved cli_type into the JSON payload before sending.
    """
    payload = dict(payload or {})

    # Inject context for daemon lifecycle management.
    payload["_pid"] = get_stable_pid()
    if "_cwd" not in payload:
        payload["_cwd"] = os.getcwd()

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
    hook_event = payload.get('hook_event_name', 'unknown')
    hook_source = payload.get('source', '')
    tool_name = payload.get('tool_name', '')

    _log_hook_lifecycle("\n" + "="*80 + "\nCLIENT→DAEMON REQUEST",
                        Event=hook_event, Source=hook_source, Tool=tool_name,
                        PayloadKeys=list(payload.keys()))

    logger.debug(f"Forwarding hook to daemon: event={hook_event}, cli={cli_type}, tool={tool_name}")

    async def forward(depth: int = 0):
        if depth > 5:
            raise RuntimeError("Daemon failed to start after 6 attempts")
        try:
            from .core import READ_BUFFER_LIMIT
            reader, writer = await ipc.connect(limit=READ_BUFFER_LIMIT)
            writer.write(json.dumps(payload).encode() + b'\n')
            await writer.drain()

            resp = await asyncio.wait_for(
                reader.readuntil(b'\n'),
                timeout=daemon_response_timeout_for_cli(cli_type),
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
                    flock_path = ipc.AUTORUN_LOCK_PATH.with_suffix('.flock')
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
                daemon_code = (
                    "import sys; sys.path.insert(0, '{0}'); "
                    "from autorun.daemon import main; main()"
                ).format(str(src_dir))
                subprocess.Popen(
                    [sys.executable, "-c", daemon_code],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            else:
                logger.debug(f"Waiting for daemon (depth={depth})")

            # Capped exponential backoff: 0.3, 0.6, 1.2, 2.0, 2.0, 2.0s
            await asyncio.sleep(min(0.3 * (2 ** depth), 2.0))
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
