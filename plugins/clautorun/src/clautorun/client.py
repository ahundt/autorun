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
            print(resp.decode().strip())

            writer.close()
            await writer.wait_closed()

        except (FileNotFoundError, ConnectionRefusedError, PermissionError, OSError) as e:
            if isinstance(e, PermissionError):
                raise  # Can't recover from permission errors
            # Auto-start daemon
            subprocess.Popen(
                [sys.executable, "-m", "clautorun.daemon"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            await asyncio.sleep(0.5)
            await forward(depth + 1)  # Retry with incremented depth

    try:
        asyncio.run(forward())
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
