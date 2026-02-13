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
from pathlib import Path

try:
    from .logging_utils import get_logger
    logger = get_logger(__name__)
except ImportError:
    # Fallback if logging_utils not available (shouldn't happen)
    import logging
    logger = logging.getLogger(__name__)

SOCKET_PATH = Path.home() / ".clautorun" / "daemon.sock"


def output_hook_response(response: dict | str, source: str = "daemon") -> None:
    """Unified hook response output handler (DRY, WOLOG).

    Single consolidation point for ALL output paths:
    - Normal daemon response
    - JSON decode error fallback
    - Buffer error response
    - Exception fail-open response

    Handles:
    1. Print JSON to stdout (always)
    2. Auto-detect CLI type (Claude vs Gemini)
    3. Apply exit-2 workaround if needed (bug #4669)
    4. Exit with correct code

    Args:
        response: Response dict OR raw string (for fallback cases)
        source: Source of response ("daemon", "daemon-raw", "buffer-error", "exception") - for logging

    Exits:
        0: Normal (allow decision OR Gemini with deny)
        2: Claude Code workaround (deny decision with exit-2 + stderr)

    Reference: notes/hooks_api_reference.md lines 395-427 (unified blocking pattern)
    """
    from .config import should_use_exit2_workaround

    # Handle raw string fallback (JSON decode error)
    if isinstance(response, str):
        logger.debug(f"Outputting raw response from {source}")
        print(response)
        sys.exit(0)
        return

    # Extract decision from response (works for both Claude and Gemini formats)
    decision = response.get('hookSpecificOutput', {}).get('permissionDecision',
                                                          response.get('decision', 'allow'))

    # Log decision for diagnostics (file-only)
    logger.info(f"Hook response: source={source}, decision={decision}")

    # Always print JSON to stdout first
    print(json.dumps(response))

    # Apply exit-2 workaround if needed (Claude Code bug #4669)
    if decision == "deny" and should_use_exit2_workaround():
        # Extract reason (try Claude format first, then Gemini format)
        reason = response.get('hookSpecificOutput', {}).get('permissionDecisionReason',
                                                            response.get('reason', 'Tool blocked'))

        logger.info("Applying exit-2 workaround (Claude Code bug #4669)")

        # Print reason to stderr (Claude Code feeds this back to AI)
        print(reason, file=sys.stderr)

        # Exit with code 2 (actual blocking)
        sys.exit(2)

    # Normal exit (allow decision OR Gemini CLI with deny)
    sys.exit(0)


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
    payload["_pid"] = os.getppid()  # Claude session PID
    payload["_cwd"] = os.getcwd()   # Current working directory

    logger.debug(f"Forwarding hook to daemon: event={payload.get('hook_event_name')}, tool={payload.get('tool_name')}")

    async def forward(depth: int = 0):
        if depth > 2:
            raise RuntimeError("Daemon failed to start after 3 attempts")
        try:
            reader, writer = await asyncio.open_unix_connection(path=str(SOCKET_PATH))
            writer.write(json.dumps(payload).encode() + b'\n')
            await writer.drain()

            resp = await asyncio.wait_for(reader.readuntil(b'\n'), timeout=5.0)
            resp_text = resp.decode().strip()

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
