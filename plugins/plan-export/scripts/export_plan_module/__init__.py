"""
Module wrapper for plan_export script.

The actual script is in ../plan_export.py. This module uses importlib to load
the script as a module for testing purposes.
"""

import importlib.util
import sys
from pathlib import Path

# Get the path to the plan_export.py script
_script_path = Path(__file__).parent.parent / "plan_export.py"

# Load the script as a module using importlib
spec = importlib.util.spec_from_file_location("plan_export", _script_path)
export_plan_module = importlib.util.module_from_spec(spec)
sys.modules["plan_export"] = export_plan_module
spec.loader.exec_module(export_plan_module)

# Export the functions we need for testing
load_config = export_plan_module.load_config
is_enabled = export_plan_module.is_enabled
get_most_recent_plan = export_plan_module.get_most_recent_plan
get_plan_from_transcript = export_plan_module.get_plan_from_transcript
get_plan_from_metadata = export_plan_module.get_plan_from_metadata
find_plan_by_session_id = export_plan_module.find_plan_by_session_id
embed_plan_metadata = export_plan_module.embed_plan_metadata
export_plan = export_plan_module.export_plan
export_rejected_plan = export_plan_module.export_rejected_plan
log_warning = export_plan_module.log_warning
main = export_plan_module.main

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
]
