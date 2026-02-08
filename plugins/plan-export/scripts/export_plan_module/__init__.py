"""
Module wrapper for plan_export - provides compatibility with old API.

The implementation has been refactored to use clautorun's daemon infrastructure.
This module provides compatibility shims for existing tests.
"""

import sys
from pathlib import Path

# Add clautorun to path
CLAUTORUN_SRC = Path(__file__).parent.parent.parent.parent / "clautorun" / "src"
sys.path.insert(0, str(CLAUTORUN_SRC))

# Import from new clautorun module
from clautorun.plan_export import (
    PlanExport,
    PlanExportConfig,
    GLOBAL_SESSION_ID,
)
from clautorun.core import EventContext, ThreadSafeDB
from clautorun.session_manager import session_state

# === Compatibility shims for old API ===


def load_config():
    """Load configuration - compatibility wrapper."""
    config = PlanExportConfig.load()
    # Return as dict for compatibility with old tests
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


def is_enabled():
    """Check if plan export is enabled - compatibility wrapper."""
    return PlanExportConfig.load().enabled


def log_warning(message: str):
    """Log warning message - compatibility wrapper."""
    config = PlanExportConfig.load()
    if config.debug_logging:
        from datetime import datetime
        try:
            debug_log = Path.home() / ".claude" / "plan-export-debug.log"
            with open(debug_log, "a") as f:
                f.write(f"[{datetime.now()}] WARNING: {message}\n")
        except Exception:
            pass


def get_most_recent_plan():
    """Find most recent plan file - compatibility wrapper."""
    plans_dir = Path.home() / ".claude" / "plans"
    if not plans_dir.exists():
        return None
    plan_files = list(plans_dir.glob("*.md"))
    if not plan_files:
        return None
    plan_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return plan_files[0]


def get_plan_from_transcript(transcript_path: str):
    """Extract plan file path from session transcript - compatibility wrapper."""
    import json
    plans_dir = Path.home() / ".claude" / "plans"
    transcript = Path(transcript_path)

    if not transcript.exists():
        return None

    found_plans = set()
    try:
        with open(transcript, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "assistant":
                        message = entry.get("message", {})
                        content = message.get("content", [])
                        if isinstance(content, list):
                            for item in content:
                                if item.get("type") == "tool_use":
                                    tool_name = item.get("name", "")
                                    if tool_name in ["Write", "Edit"]:
                                        tool_input = item.get("input", {})
                                        file_path = tool_input.get("file_path", "")
                                        if file_path and file_path.startswith(str(plans_dir)) and file_path.endswith(".md"):
                                            found_plans.add(file_path)
                except json.JSONDecodeError:
                    continue
    except IOError:
        return None

    if not found_plans:
        return None

    valid_plans = []
    for p in found_plans:
        path = Path(p)
        if path.exists():
            valid_plans.append(path)
    if not valid_plans:
        return None

    valid_plans.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return valid_plans[0]


def get_plan_from_metadata(plan_path):
    """Extract session_id from plan file metadata - compatibility wrapper."""
    try:
        content = plan_path.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return None
        frontmatter_end = content.find("\n---", 4)
        if frontmatter_end == -1:
            return None
        frontmatter = content[4:frontmatter_end].strip()
        for line in frontmatter.split("\n"):
            if line.startswith("session_id:"):
                session_id = line.split(":", 1)[1].strip()
                session_id = session_id.strip('"\'')
                return session_id
    except (IOError, UnicodeDecodeError):
        pass
    return None


def find_plan_by_session_id(session_id: str):
    """Find a plan file by session_id in metadata - compatibility wrapper."""
    plans_dir = Path.home() / ".claude" / "plans"
    if not plans_dir.exists():
        return None
    plan_files = list(plans_dir.glob("*.md"))
    if not plan_files:
        return None
    for plan_path in plan_files:
        metadata_session_id = get_plan_from_metadata(plan_path)
        if metadata_session_id == session_id:
            return plan_path
    return None


def embed_plan_metadata(plan_path, session_id: str, export_destination):
    """Embed metadata into exported plan file - compatibility wrapper."""
    from datetime import datetime
    try:
        content = export_destination.read_text(encoding="utf-8")
        if content.startswith("---"):
            return
        metadata = f"""---
session_id: {session_id}
original_path: {plan_path}
export_timestamp: {datetime.now().isoformat()}
export_destination: {export_destination}
---

"""
        export_destination.write_text(metadata + content, encoding="utf-8")
    except (IOError, UnicodeDecodeError):
        pass


def export_plan(plan_path, project_dir, session_id: str = None):
    """Export the plan file - compatibility wrapper."""
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

    # Convert to old format
    if result["success"]:
        dest_path = project_dir / config.output_plan_dir / f"exported_plan.md"  # Approximate
        return {
            "success": True,
            "source": str(plan_path),
            "destination": str(dest_path),
            "message": result["message"]
        }
    return {
        "success": False,
        "source": str(plan_path),
        "destination": "",
        "message": result.get("error", "Export failed")
    }


def export_rejected_plan(plan_path, project_dir, session_id: str = None):
    """Export a rejected plan - compatibility wrapper."""
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
    result = exporter.export(Path(plan_path), rejected=True)

    if result["success"]:
        dest_path = project_dir / config.output_rejected_plan_dir / f"rejected_plan.md"
        return {
            "success": True,
            "source": str(plan_path),
            "destination": str(dest_path),
            "message": result["message"]
        }
    return {
        "success": False,
        "source": str(plan_path),
        "destination": "",
        "message": result.get("error", "Export failed")
    }


def main():
    """Main function - delegates to plan_export.py script."""
    # Import the actual script's main function
    script_path = Path(__file__).parent.parent / "plan_export.py"
    import importlib.util
    spec = importlib.util.spec_from_file_location("plan_export_script", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main()


# Additional functions expected by tests
def get_content_hash(file_path):
    """Get content hash - compatibility wrapper."""
    import hashlib
    try:
        content = Path(file_path).read_bytes()
        return hashlib.sha256(content).hexdigest()[:16]
    except IOError:
        return ""


def load_tracking():
    """Load export tracking data - compatibility wrapper."""
    with session_state(GLOBAL_SESSION_ID) as state:
        return dict(state.get("tracking", {}))


def save_tracking(tracking: dict):
    """Save export tracking data - compatibility wrapper."""
    with session_state(GLOBAL_SESSION_ID) as state:
        state["tracking"] = tracking


def record_export(plan_path, dest_path):
    """Record a successful export - compatibility wrapper."""
    from datetime import datetime
    content_hash = get_content_hash(plan_path)
    if not content_hash:
        return
    with session_state(GLOBAL_SESSION_ID) as state:
        tracking = state.get("tracking", {})
        tracking[content_hash] = {
            "exported_at": datetime.now().isoformat(),
            "destination": str(dest_path),
            "source": str(plan_path),
            "via": "compatibility_wrapper"
        }
        state["tracking"] = tracking


def detect_hook_type(hook_input: dict) -> str:
    """Detect which hook triggered this script - compatibility wrapper."""
    if "tool_name" in hook_input:
        return "PostToolUse"
    return "SessionStart"


def handle_session_start(hook_input: dict):
    """Handle SessionStart - compatibility wrapper."""
    import json
    store = ThreadSafeDB()
    session_id = hook_input.get("session_id", "unknown")
    cwd = hook_input.get("cwd", str(Path.cwd()))

    ctx = EventContext(
        session_id=session_id,
        event="SessionStart",
        tool_input={"cwd": cwd},
        store=store
    )

    config = PlanExportConfig.load()
    if not config.enabled:
        print(json.dumps({"continue": True, "suppressOutput": True}))
        return

    exporter = PlanExport(ctx, config)
    for plan in exporter.get_unexported():
        result = exporter.export(plan)
        if result["success"] and config.notify_claude:
            print(json.dumps({
                "continue": True,
                "systemMessage": f"📋 Recovered: {result['message']}",
            }))
            return
    print(json.dumps({"continue": True}))


# Re-export tracking functions for tests
TRACKING_FILE = Path.home() / ".claude" / "plan-export-tracking.json"
DEFAULT_CONFIG = {
    "enabled": True,
    "output_plan_dir": "notes",
    "filename_pattern": "{datetime}_{name}",
    "extension": ".md",
    "export_rejected": True,
    "output_rejected_plan_dir": "notes/rejected",
    "debug_logging": False,
    "notify_claude": True
}


__all__ = [
    "load_config",
    "is_enabled",
    "get_most_recent_plan",
    "get_plan_from_transcript",
    "get_plan_from_metadata",
    "find_plan_by_session_id",
    "embed_plan_metadata",
    "export_plan",
    "export_rejected_plan",
    "log_warning",
    "main",
    "get_content_hash",
    "load_tracking",
    "save_tracking",
    "record_export",
    "detect_hook_type",
    "handle_session_start",
    "TRACKING_FILE",
    "DEFAULT_CONFIG",
    "PlanExport",
    "PlanExportConfig",
]
