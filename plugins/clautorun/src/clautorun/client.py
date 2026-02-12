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

Claude Code Bug #4669 Workaround:
---------------------------------
When daemon returns a deny decision, exit with code 2 to ACTUALLY block
the tool. Claude Code ignores permissionDecision: "deny" in JSON output,
but respects exit code 2 as a blocking error.

References:
- GitHub Issues: #4669, #18312, #13744, #20946
- Exit code 2 docs: https://claude.com/blog/how-to-configure-hooks
"""
import os
import sys
import json
import asyncio
import subprocess
from pathlib import Path

SOCKET_PATH = Path.home() / ".clautorun" / "daemon.sock"


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

    async def forward(depth: int = 0):
        if depth > 2:
            raise RuntimeError("Daemon failed to start after 3 attempts")
        try:
            reader, writer = await asyncio.open_unix_connection(path=str(SOCKET_PATH))
            writer.write(json.dumps(payload).encode() + b'\n')
            await writer.drain()

            resp = await asyncio.wait_for(reader.readuntil(b'\n'), timeout=5.0)
            resp_text = resp.decode().strip()

            # Parse response to check for exit code 2 marker
            try:
                resp_json = json.loads(resp_text)
                # Check for deny decision requiring exit code 2
                # (workaround for Claude Code bug #4669)
                exit_code_2 = resp_json.pop("_exit_code_2", False)
                reason = resp_json.get("systemMessage", "")

                # Re-serialize without the internal marker
                print(json.dumps(resp_json))

                if exit_code_2:
                    # Write reason to stderr (Claude Code shows this)
                    print(reason, file=sys.stderr)
                    sys.exit(2)
            except json.JSONDecodeError:
                # Not valid JSON, just print as-is
                print(resp_text)

            writer.close()
            await writer.wait_closed()

        except asyncio.LimitOverrunError as e:
            # Response from daemon exceeded buffer (shouldn't happen - response is tiny)
            print(json.dumps({
                "continue": True,
                "stopReason": "",
                "suppressOutput": False,
                "systemMessage": f"Client buffer error: Daemon response too large. This is a bug. {e}"
            }))
        except (FileNotFoundError, ConnectionRefusedError, PermissionError, OSError) as e:
            if isinstance(e, PermissionError):
                raise  # Can't recover from permission errors
            # Auto-start daemon - use -c to run directly (works with editable installs)
            # Get the src directory: __file__ is src/clautorun/client.py, so parent.parent is src/
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
            await asyncio.sleep(0.5)
            await forward(depth + 1)  # Retry with incremented depth

    try:
        asyncio.run(forward())
    except SystemExit:
        raise  # Re-raise SystemExit to preserve exit code
    except Exception:
        # Fail open
        print(json.dumps({
            "continue": True,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": ""
        }))


if __name__ == "__main__":
    run_client()
