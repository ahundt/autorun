#!/usr/bin/env python3

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
Clautorun v0.7 Entry Point with Feature Flag

Set CLAUTORUN_USE_DAEMON=1 to use daemon mode (experimental)
Default is legacy mode (CLAUTORUN_USE_DAEMON=0) for safety during rollout
"""
import os
import sys


# Feature flag for gradual rollout
# Default to legacy mode (0) for safety
# Set CLAUTORUN_USE_DAEMON=1 to enable daemon mode
USE_DAEMON = os.environ.get("CLAUTORUN_USE_DAEMON", "0") == "1"


def main():
    """Main entry point for python -m clautorun"""
    # Handle install/uninstall/check commands first
    if len(sys.argv) > 1 and sys.argv[1] in ["install", "uninstall", "check"]:
        from .install import main as install_main
        sys.argv = ["clautorun"] + sys.argv[1:]
        install_main()
        return

    if USE_DAEMON:
        # New daemon mode - forwards to Unix socket daemon
        from .client import run_client
        run_client()
    else:
        # Legacy mode - direct hook handling
        from .main import main as app_main
        app_main()


if __name__ == "__main__":
    main()