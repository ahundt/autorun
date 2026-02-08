#!/usr/bin/env python3
"""
Plan Export Script - Daemon client with fallback for plan export.

This script handles PostToolUse and SessionStart hooks for plan export.
It tries to delegate to the clautorun daemon first (fast path: 1-5ms),
falling back to direct execution if the daemon isn't running (slow path: 50-150ms).

CLAUDE CODE BUG WORKAROUND:
    Bug: Claude Code's "fresh context" option (button 1 in plan accept dialog)
    does NOT fire PostToolUse hooks for ExitPlanMode. Plans accepted with
    Option 1 are silently lost because the hook never triggers.

    Workaround: The SessionStart hook checks on each new session whether the
    previous session had an unexported plan. If found, it exports the plan.
    State is stored in a global shelve (not session-scoped) to survive the
    session_id change that happens with Option 1.

Hook Types Handled:
    - PostToolUse(Write/Edit): Track plan file writes for recovery
    - PostToolUse(ExitPlanMode): Export plan immediately (Option 2 path)
    - SessionStart: Recover unexported plans (Option 1 workaround)

Configuration (~/.claude/plan-export.config.json):
    enabled                  - Enable/disable plan export (default: true)
    output_plan_dir          - Directory for exported plans (default: "notes")
    filename_pattern         - Filename template (default: "{datetime}_{name}")
    extension                - File extension (default: ".md")
    export_rejected          - Save rejected plans (default: true)
    output_rejected_plan_dir - Directory for rejected plans (default: "notes/rejected")
    debug_logging            - Enable debug logging (default: false)
    notify_claude            - Show export confirmation message (default: true)

Template Variables:
    {YYYY}     - 4-digit year (2025)
    {YY}       - 2-digit year (25)
    {MM}       - Month 01-12
    {DD}       - Day 01-31
    {HH}       - Hour 00-23
    {mm}       - Minute 00-59
    {date}     - Full date YYYY_MM_DD
    {datetime} - Full datetime YYYY_MM_DD_HHmm
    {name}     - Extracted plan name from heading
    {original} - Original plan filename (without .md)
"""

import hashlib
import json
import socket
import sys
from pathlib import Path

# Add clautorun to path for imports
CLAUTORUN_SRC = Path(__file__).parent.parent.parent / "clautorun" / "src"
sys.path.insert(0, str(CLAUTORUN_SRC))


# === Backwards-compatible API for tests ===
# These functions are also available via export_plan_module for cleaner imports

def detect_hook_type(hook_input: dict) -> str:
    """Detect which hook triggered this script."""
    if "tool_name" in hook_input:
        return "PostToolUse"
    return "SessionStart"


def get_content_hash(file_path) -> str:
    """Get SHA256 hash of file content (first 16 chars)."""
    try:
        content = Path(file_path).read_bytes()
        return hashlib.sha256(content).hexdigest()[:16]
    except IOError:
        return ""


def handle_session_start(hook_input: dict) -> None:
    """Handle SessionStart - recover unexported plans."""
    fallback_execution(hook_input)


def export_plan(plan_path, project_dir, session_id: str = None):
    """Export the plan file - compatibility wrapper for tests."""
    try:
        from clautorun.plan_export import PlanExport, PlanExportConfig
        from clautorun.core import EventContext, ThreadSafeDB
    except ImportError:
        return {"success": False, "message": "clautorun not available"}

    store = ThreadSafeDB()
    ctx = EventContext(
        session_id=session_id or "unknown",
        event="PostToolUse",
        tool_name="ExitPlanMode",
        tool_input={"cwd": str(project_dir)},
        store=store
    )
    config = PlanExportConfig.load()
    exporter = PlanExport(ctx, config)
    result = exporter.export(Path(plan_path))

    if result["success"]:
        return {
            "success": True,
            "source": str(plan_path),
            "destination": str(project_dir / config.output_plan_dir),
            "message": result["message"]
        }
    return {
        "success": False,
        "source": str(plan_path),
        "destination": "",
        "message": result.get("error", "Export failed")
    }


# Additional compatibility functions for tests
def get_config_path():
    """Get config path - compatibility wrapper."""
    return Path.home() / ".claude" / "plan-export.config.json"


def load_config():
    """Load config - compatibility wrapper."""
    try:
        from clautorun.plan_export import PlanExportConfig
        config = PlanExportConfig.load()
        return {
            "enabled": config.enabled,
            "output_plan_dir": config.output_plan_dir,
            "filename_pattern": config.filename_pattern,
            "extension": config.extension,
            "export_rejected": config.export_rejected,
            "output_rejected_plan_dir": config.output_rejected_plan_dir,
            "debug_logging": config.debug_logging,
            "notify_claude": config.notify_claude,
        }
    except ImportError:
        return {
            "enabled": True,
            "output_plan_dir": "notes",
            "filename_pattern": "{datetime}_{name}",
            "extension": ".md",
            "export_rejected": True,
            "output_rejected_plan_dir": "notes/rejected",
            "debug_logging": False,
            "notify_claude": True,
        }


def is_enabled():
    """Check if enabled - compatibility wrapper."""
    return load_config().get("enabled", True)


def try_daemon(hook_input: dict) -> bool:
    """Try to send request to daemon. Returns True if succeeded.

    The daemon handles plan export via @app.on() handlers registered in
    plugins/clautorun/src/clautorun/plan_export.py. This is the fast path
    (1-5ms vs 50-150ms for direct execution).

    Args:
        hook_input: The hook payload from Claude Code

    Returns:
        True if daemon handled the request, False if not available
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

    This is the slow path (50-150ms Python startup + imports).
    Uses the same PlanExport class as the daemon but without caching benefits.

    Args:
        hook_input: The hook payload from Claude Code
    """
    try:
        from clautorun.plan_export import PlanExport, PlanExportConfig
        from clautorun.core import EventContext, ThreadSafeDB
    except ImportError:
        # clautorun not available - output success and exit
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

    # Create a minimal store for the context
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
        # Track plan file writes
        file_path = tool_input.get("file_path", "")
        exporter.record_write(file_path)
        print(json.dumps({"continue": True}))

    elif tool_name == "ExitPlanMode":
        # Export plan (Option 2 - regular accept)
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
    # Read hook input from stdin
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
