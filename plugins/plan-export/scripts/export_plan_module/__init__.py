"""
Module wrapper for plan_export - pure re-exports from clautorun.plan_export.

All implementation is in clautorun.plan_export. This module exists only for
backwards compatibility with code that imports from export_plan_module.
"""

import sys
from pathlib import Path

# Add clautorun to path
CLAUTORUN_SRC = Path(__file__).parent.parent.parent.parent / "clautorun" / "src"
sys.path.insert(0, str(CLAUTORUN_SRC))

# Re-export everything from clautorun.plan_export
from clautorun.plan_export import (  # noqa: E402, F401
    # Classes
    PlanExport,
    PlanExportConfig,
    # Constants
    GLOBAL_SESSION_ID,
    DEFAULT_CONFIG,
    CONFIG_PATH,
    PLANS_DIR,
    DEBUG_LOG_PATH,
    # Config functions
    get_config_path,
    load_config,
    is_enabled,
    # Utility functions
    detect_hook_type,
    get_content_hash,
    log_warning,
    # Plan discovery
    get_most_recent_plan,
    get_plan_from_transcript,
    get_plan_from_metadata,
    find_plan_by_session_id,
    # Tracking functions
    load_tracking,
    save_tracking,
    record_export,
    # Export functions
    export_plan,
    export_rejected_plan,
    handle_session_start,
    embed_plan_metadata,
)

# For backwards compatibility, also expose TRACKING_FILE as alias
TRACKING_FILE = Path.home() / ".claude" / "plan-export-tracking.json"


def main():
    """Main function - delegates to plan_export.py script."""
    script_path = Path(__file__).parent.parent / "plan_export.py"
    import importlib.util
    spec = importlib.util.spec_from_file_location("plan_export_script", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main()


__all__ = [
    # Classes
    "PlanExport",
    "PlanExportConfig",
    # Constants
    "GLOBAL_SESSION_ID",
    "DEFAULT_CONFIG",
    "CONFIG_PATH",
    "PLANS_DIR",
    "DEBUG_LOG_PATH",
    "TRACKING_FILE",
    # Config functions
    "get_config_path",
    "load_config",
    "is_enabled",
    # Utility functions
    "detect_hook_type",
    "get_content_hash",
    "log_warning",
    # Plan discovery
    "get_most_recent_plan",
    "get_plan_from_transcript",
    "get_plan_from_metadata",
    "find_plan_by_session_id",
    # Tracking functions
    "load_tracking",
    "save_tracking",
    "record_export",
    # Export functions
    "export_plan",
    "export_rejected_plan",
    "handle_session_start",
    "embed_plan_metadata",
    # Main
    "main",
]
