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
Clautorun v0.7 Client - Thin Forwarder to Daemon

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
    from .logging_utils import get_logger
    logger = get_logger(__name__)
except ImportError:
    # Fallback if logging_utils not available (shouldn't happen)
    import logging
    logger = logging.getLogger(__name__)

SOCKET_PATH = Path.home() / ".clautorun" / "daemon.sock"
DEBUG_LOG = Path.home() / ".clautorun" / "daemon.log"


def _log_hook_lifecycle(message: str, **kwargs) -> None:
    """DRY helper for hook lifecycle logging.

    Args:
        message: Log message
        **kwargs: Key-value pairs to log
    """
    try:
        DEBUG_LOG.parent.mkdir(exist_ok=True)
        with open(DEBUG_LOG, 'a') as f:
            f.write(f"[{datetime.datetime.now()}] {message}\n")
            for key, value in kwargs.items():
                f.write(f"{key}: {value}\n")
    except Exception:
        pass  # Never fail on logging


def output_hook_response(response: dict | str, source: str = "daemon") -> None:
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
        source: Source ("daemon", "daemon-raw", "buffer-error", "exception")

    Exits:
        0: Pathway B (standard - allow OR Gemini deny)
        2: Pathway A (workaround - Claude Code deny)

    Reference: notes/hooks_api_reference.md lines 395-427
    """
    from .config import should_use_exit2_workaround

    # ═══════════════════════════════════════════════════════════════
    # SHARED: Handle raw string fallback (JSON decode error)
    # ═══════════════════════════════════════════════════════════════
    if isinstance(response, str):
        logger.debug(f"Outputting raw response from {source}")
        print(response)
        sys.exit(0)
        return

    # ═══════════════════════════════════════════════════════════════
    # SHARED: Extract decision (DRY - works for Claude and Gemini)
    # ═══════════════════════════════════════════════════════════════
    decision = response.get('hookSpecificOutput', {}).get('permissionDecision',
                                                          response.get('decision', 'allow'))

    logger.info(f"Hook response: source={source}, decision={decision}")

    # ═══════════════════════════════════════════════════════════════
    # SHARED: Always print JSON to stdout first
    # ═══════════════════════════════════════════════════════════════
    print(json.dumps(response))

    # Lifecycle logging before exit (DRY)
    exit_code = 2 if (decision == "deny" and should_use_exit2_workaround()) else 0
    _log_hook_lifecycle("DAEMON→CLIENT RESPONSE", Source=source, Decision=decision, ExitCode=exit_code)

    # ═══════════════════════════════════════════════════════════════
    # SINGLE FLAG CHECK: Select pathway
    # ═══════════════════════════════════════════════════════════════
    if decision == "deny" and should_use_exit2_workaround():
        # ╔═══════════════════════════════════════════════════════════╗
        # ║ PATHWAY A: Bug #4669 Workaround (Claude Code)           ║
        # ║ - Print reason to stderr (AI sees this)                 ║
        # ║ - Exit code 2 (ONLY way blocking works in Claude Code)  ║
        # ╚═══════════════════════════════════════════════════════════╝
        reason = response.get('hookSpecificOutput', {}).get('permissionDecisionReason',
                                                            response.get('reason', 'Tool blocked'))

        logger.info("Applying exit-2 workaround (Claude Code bug #4669)")
        print(reason, file=sys.stderr)
        sys.exit(2)
    else:
        # ╔═══════════════════════════════════════════════════════════╗
        # ║ PATHWAY B: Standard Behavior                             ║
        # ║ - Gemini respects JSON decision field                    ║
        # ║ - Allow decisions in Claude Code                         ║
        # ║ - Exit code 0 (normal success)                           ║
        # ╚═══════════════════════════════════════════════════════════╝
        sys.exit(0)


def get_stable_pid() -> int:
    """Traverse up process tree to find the stable CLI process ID.

    Avoids using the ephemeral hook_entry.py PID. Looks for 'gemini' or 'claude'.
    Falls back to ppid if discovery fails.
    """
    try:
        import psutil
        current = psutil.Process()
        # Search up to 5 levels for CLI binary
        for _ in range(5):
            parent = current.parent()
            if not parent:
                break
            name = parent.name().lower()
            if "gemini" in name or "claude" in name:
                return parent.pid
            current = parent
    except (ImportError, Exception):
        pass
    return os.getppid()


def run_client():
    """Forward hook payload to daemon."""
    # Read stdin payload
    payload = {}
    try:
        if not sys.stdin.isatty():
            payload = json.load(sys.stdin)
    except Exception:
        pass

    # Inject context for daemon lifecycle management
    # CRITICAL: Use stable PID to prevent premature daemon cleanup
    payload["_pid"] = get_stable_pid()
    payload["_cwd"] = os.getcwd()   # Current working directory

    # Lifecycle logging (DRY)
    hook_event = payload.get('hook_event_name', 'unknown')
    hook_source = payload.get('source', '')
    tool_name = payload.get('tool_name', '')

    _log_hook_lifecycle("\n" + "="*80 + "\nCLIENT→DAEMON REQUEST",
                        Event=hook_event, Source=hook_source, Tool=tool_name,
                        PayloadKeys=list(payload.keys()),
                        FullPayload=json.dumps(payload, indent=2))

    logger.debug(f"Forwarding hook to daemon: event={hook_event}, tool={tool_name}")

    async def forward(depth: int = 0):
        if depth > 2:
            raise RuntimeError("Daemon failed to start after 3 attempts")
        try:
            from .core import READ_BUFFER_LIMIT
            reader, writer = await asyncio.open_unix_connection(
                path=str(SOCKET_PATH),
                limit=READ_BUFFER_LIMIT
            )
            writer.write(json.dumps(payload).encode() + b'\n')
            await writer.drain()

            resp = await asyncio.wait_for(reader.readuntil(b'\n'), timeout=5.0)
            resp_text = resp.decode().strip()
            
            _log_hook_lifecycle("DAEMON→CLIENT RAW RESPONSE", FullResponse=resp_text)

            # Parse response and route through unified output handler
            try:
                resp_json = json.loads(resp_text)
                output_hook_response(resp_json, source="daemon")
            except json.JSONDecodeError:
                # Not valid JSON, output as-is
                output_hook_response(resp_text, source="daemon-raw")

            writer.close()
            await writer.wait_closed()

        except asyncio.LimitOverrunError as e:
            # Response from daemon exceeded buffer (shouldn't happen - response is tiny)
            logger.error(f"Client buffer error: {e}")
            output_hook_response({
                "continue": True,
                "stopReason": "",
                "suppressOutput": False,
                "systemMessage": f"Client buffer error: Daemon response too large. {e}",
                "decision": "allow",
                "hookSpecificOutput": {"permissionDecision": "allow"}
            }, source="buffer-error")
        except (FileNotFoundError, ConnectionRefusedError, PermissionError, OSError) as e:
            if isinstance(e, PermissionError):
                raise  # Can't recover from permission errors
            # Check if daemon is already running via PID file
            # (pattern from install.py:140-149, PID written by core.py:987-990)
            lock_path = Path.home() / ".clautorun" / "daemon.lock"
            daemon_alive = False
            if lock_path.exists():
                try:
                    pid = int(lock_path.read_text().strip())
                    os.kill(pid, 0)  # Check if process is alive (signal 0)
                    daemon_alive = True
                except (ValueError, ProcessLookupError, PermissionError):
                    # PID invalid or process dead — safe to spawn new daemon
                    lock_path.unlink(missing_ok=True)

            if not daemon_alive:
                # Auto-start daemon - use -c to run directly (works with editable installs)
                logger.info("Daemon not running, auto-starting...")
                src_dir = Path(__file__).parent.parent
                daemon_code = "import sys; sys.path.insert(0, '{0}'); from clautorun.daemon import main; main()".format(
                    str(src_dir)
                )
                subprocess.Popen(
                    [sys.executable, "-c", daemon_code],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
            else:
                logger.debug(f"Daemon alive (PID in lock file), retrying connection (depth={depth})")
            await asyncio.sleep(0.5)
            await forward(depth + 1)  # Retry with incremented depth

    try:
        asyncio.run(forward())
    except SystemExit:
        raise  # Re-raise SystemExit to preserve exit code
    except Exception as e:
        # Fail open
        logger.error(f"Client exception (fail-open): {e}", exc_info=True)
        output_hook_response({
            "continue": True,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": "",
            "decision": "allow",
            "hookSpecificOutput": {"permissionDecision": "allow"}
        }, source="exception")


if __name__ == "__main__":
    run_client()
