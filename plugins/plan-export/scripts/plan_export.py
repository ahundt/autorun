#!/usr/bin/env python3
"""
Plan Export Hook Script - Entry point for Claude Code hooks.

This script is called by Claude Code via hooks.json. It delegates to the
clautorun daemon (fast: 1-5ms) or falls back to direct execution (50-150ms).

See clautorun.plan_export for full documentation including:
- Configuration options (~/.claude/plan-export.config.json)
- Template variables ({datetime}, {name}, etc.)
- Bug workaround details (Option 1 fresh context bug)
- Thread safety and concurrency model
"""

import json
import socket
import sys
from pathlib import Path

# Add clautorun to path for imports
CLAUTORUN_SRC = Path(__file__).parent.parent.parent / "clautorun" / "src"
sys.path.insert(0, str(CLAUTORUN_SRC))

# Re-export common functions for backwards compatibility
# All implementation is in clautorun.plan_export
from clautorun.plan_export import (  # noqa: E402
    # Classes
    PlanExport,
    PlanExportConfig,
    # Constants
    GLOBAL_SESSION_ID,
    DEFAULT_CONFIG,
    CONFIG_PATH,
    PLANS_DIR,
    # Helper functions
    detect_hook_type,
    get_content_hash,
    get_config_path,
    load_config,
    is_enabled,
    log_warning,
    # Export functions
    export_plan,
    handle_session_start,
)


def try_daemon(hook_input: dict) -> bool:
    """Try to send request to daemon. Returns True if succeeded.

    The daemon handles plan export via @app.on() handlers registered in
    clautorun.plan_export. This is the fast path (1-5ms vs 50-150ms).
    """
    try:
        from clautorun.core import SOCKET_PATH
    except ImportError:
        return False

    if not SOCKET_PATH.exists():
        return False

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        sock.connect(str(SOCKET_PATH))
        sock.sendall(json.dumps(hook_input).encode() + b'\n')
        response = sock.recv(8192)
        sock.close()
        # Forward daemon response to stdout for Claude Code
        print(response.decode().strip())
        return True
    except (ConnectionRefusedError, socket.timeout, OSError, BrokenPipeError):
        return False


def fallback_execution(hook_input: dict) -> None:
    """Direct execution when daemon not running.

    Uses clautorun.plan_export classes directly (slow path: 50-150ms).
    """
    try:
        from clautorun.plan_export import PlanExport, PlanExportConfig
        from clautorun.core import EventContext, ThreadSafeDB
    except ImportError:
        print(json.dumps({"continue": True}))
        return

    config = PlanExportConfig.load()
    if not config.enabled:
        print(json.dumps({"continue": True}))
        return

    # Build EventContext from hook input
    session_id = hook_input.get("session_id", "unknown")
    tool_name = hook_input.get("tool_name")
    tool_input = hook_input.get("tool_input", {})
    tool_result = hook_input.get("tool_response", hook_input.get("tool_result"))
    cwd = hook_input.get("cwd")

    store = ThreadSafeDB()
    ctx = EventContext(
        session_id=session_id,
        event=hook_input.get("hook_event_name", ""),
        tool_name=tool_name,
        tool_input=tool_input,
        tool_result=tool_result,
        store=store
    )

    # Inject cwd into tool_input for project_dir access
    if cwd:
        ctx._tool_input["cwd"] = cwd

    exporter = PlanExport(ctx, config)

    # Dispatch based on hook type
    if tool_name in ("Write", "Edit"):
        exporter.record_write(tool_input.get("file_path", ""))
        print(json.dumps({"continue": True}))

    elif tool_name == "ExitPlanMode":
        plan = exporter.get_current_plan()
        if plan:
            result = exporter.export(plan)
            if result["success"] and config.notify_claude:
                print(json.dumps({
                    "continue": True,
                    "systemMessage": f"📋 {result['message']}"
                }))
                return
        print(json.dumps({"continue": True}))

    elif "session_id" in hook_input and tool_name is None:
        # SessionStart - recover unexported plans
        for plan in exporter.get_unexported():
            result = exporter.export(plan)
            if result["success"] and config.notify_claude:
                print(json.dumps({
                    "continue": True,
                    "systemMessage": f"📋 Recovered: {result['message']} (from fresh context)"
                }))
                return
        print(json.dumps({"continue": True}))

    else:
        print(json.dumps({"continue": True}))


def main():
    """Main entry point - daemon client with fallback."""
    try:
        if sys.stdin.isatty():
            hook_input = {}
        else:
            hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        hook_input = {}

    # Try daemon first (fast path: 1-5ms)
    if try_daemon(hook_input):
        return

    # Fallback to direct execution (slow path: 50-150ms)
    fallback_execution(hook_input)


if __name__ == "__main__":
    main()
