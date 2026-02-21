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

SOCKET_PATH = Path.home() / ".autorun" / "daemon.sock"
DEBUG_LOG = Path.home() / ".autorun" / "daemon.log"


def _log_hook_lifecycle(message: str, **kwargs) -> None:
    """DRY helper for hook lifecycle logging. Only active when AUTORUN_DEBUG=1."""
    if not DEBUG_ENABLED:
        return
    try:
        DEBUG_LOG.parent.mkdir(exist_ok=True)
        with open(DEBUG_LOG, 'a') as f:
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
        cli_type: Target CLI ("claude" or "gemini")
        source: Source ("daemon", "daemon-raw", "buffer-error", "exception")

    Returns:
        int: Exit code (0, 1, or 2)

    Reference: notes/hooks_api_reference.md lines 395-427
    """
    from .config import should_use_exit2_workaround
    from .core import validate_hook_response

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

    # Inject context for daemon lifecycle management
    # CRITICAL: Use stable PID to prevent premature daemon cleanup
    payload["_pid"] = get_stable_pid()
    if "_cwd" not in payload:
        payload["_cwd"] = os.getcwd()   # Current working directory (don't overwrite if already set)

    # Detect CLI type for schema enforcement
    from .config import detect_cli_type
    cli_type = detect_cli_type(payload)

    # Lifecycle logging (DRY)
    hook_event = payload.get('hook_event_name', 'unknown')
    hook_source = payload.get('source', '')
    tool_name = payload.get('tool_name', '')

    _log_hook_lifecycle("\n" + "="*80 + "\nCLIENT→DAEMON REQUEST",
                        Event=hook_event, Source=hook_source, Tool=tool_name,
                        PayloadKeys=list(payload.keys()))

    logger.debug(f"Forwarding hook to daemon: event={hook_event}, cli={cli_type}, tool={tool_name}")

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
                return output_hook_response(resp_json, event=hook_event, cli_type=cli_type, source="daemon")
            except json.JSONDecodeError:
                # Not valid JSON, output as-is
                return output_hook_response(resp_text, event=hook_event, cli_type=cli_type, source="daemon-raw")

            writer.close()
            await writer.wait_closed()

        except asyncio.LimitOverrunError as e:
            # Response from daemon exceeded buffer (shouldn't happen - response is tiny)
            logger.error(f"Client buffer error: {e}")
            return output_hook_response({
                "continue": True,
                "stopReason": "",
                "suppressOutput": False,
                "systemMessage": f"Client buffer error: Daemon response too large. {e}"
            }, event=hook_event, cli_type=cli_type, source="buffer-error")
        except (FileNotFoundError, ConnectionRefusedError, PermissionError, OSError) as e:
            if isinstance(e, PermissionError):
                raise  # Can't recover from permission errors
            # Check if daemon is already running via PID file
            # (pattern from install.py:140-149, PID written by core.py:987-990)
            lock_path = Path.home() / ".autorun" / "daemon.lock"
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
                daemon_code = "import sys; sys.path.insert(0, '{0}'); from autorun.daemon import main; main()".format(
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
            return await forward(depth + 1)  # Retry with incremented depth

    try:
        return asyncio.run(forward())
    except Exception as e:
        # Fail open
        logger.error(f"Client exception (fail-open): {e}", exc_info=True)
        return output_hook_response({
            "continue": True,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": ""
        }, event=hook_event, cli_type=cli_type, source="exception")


if __name__ == "__main__":
    run_client()
