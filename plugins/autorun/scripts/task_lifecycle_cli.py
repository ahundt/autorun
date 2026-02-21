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

"""CLI for task lifecycle management.

Called from slash commands in plugins/clautorun/commands/*.md.
Delegates to TaskLifecycle class methods.

Usage (invoked via uv from slash commands):
    uv run python task_lifecycle_cli.py --status [SESSION_ID]
    uv run python task_lifecycle_cli.py --export SESSION_ID OUTPUT_PATH [--format json|csv|markdown]
    uv run python task_lifecycle_cli.py --clear [SESSION_ID] [--all] [--no-confirm]
    uv run python task_lifecycle_cli.py --configure
    uv run python task_lifecycle_cli.py --enable
    uv run python task_lifecycle_cli.py --disable
"""

import sys
import argparse
from pathlib import Path

# Add plugin root to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / 'src'))

from clautorun.task_lifecycle import TaskLifecycle


def main():
    parser = argparse.ArgumentParser(
        description="Task lifecycle management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    # Subcommands
    parser.add_argument("--status", nargs='?', const='current', metavar="SESSION_ID",
                       help="Show task status for session (default: current)")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Show full task details")
    parser.add_argument("--format", choices=['text', 'json', 'table'], default='text',
                       help="Output format (default: text)")

    parser.add_argument("--export", nargs=2, metavar=("SESSION_ID", "OUTPUT"),
                       help="Export task data to file")
    parser.add_argument("--export-format", choices=['json', 'csv', 'markdown'], default='json',
                       help="Export format (default: json)")
    parser.add_argument("--include-completed", action="store_true",
                       help="Include completed/deleted tasks in export")

    parser.add_argument("--clear", nargs='?', const='current', metavar="SESSION_ID",
                       help="Clear task data for session (default: current)")
    parser.add_argument("--all", action="store_true",
                       help="Clear all sessions (use with --clear)")
    parser.add_argument("--no-confirm", action="store_true",
                       help="Skip confirmation prompt")

    parser.add_argument("--configure", action="store_true",
                       help="Show configuration (interactive if TTY)")
    parser.add_argument("--interactive", action="store_true",
                       help="Force interactive mode (requires TTY)")
    parser.add_argument("--enable", action="store_true",
                       help="Enable task lifecycle tracking")
    parser.add_argument("--disable", action="store_true",
                       help="Disable task lifecycle tracking")

    args = parser.parse_args()

    # Dispatch to TaskLifecycle class methods
    if args.status is not None:
        session_id = None if args.status == 'current' else args.status
        return TaskLifecycle.cli_status(session_id=session_id, verbose=args.verbose, format=args.format)

    elif args.export:
        return TaskLifecycle.cli_export(
            session_id=args.export[0],
            output_path=args.export[1],
            format=args.export_format,
            include_completed=args.include_completed
        )

    elif args.clear is not None:
        session_id = None if args.clear == 'current' else args.clear
        return TaskLifecycle.cli_clear(
            session_id=session_id,
            all_sessions=args.all,
            confirm=not args.no_confirm
        )

    elif args.configure:
        return TaskLifecycle.cli_configure(interactive=args.interactive)

    elif args.enable:
        return TaskLifecycle.cli_enable()

    elif args.disable:
        return TaskLifecycle.cli_disable()

    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
