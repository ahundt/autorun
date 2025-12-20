#!/usr/bin/env python3
"""
Export Plan Script - Copies the session's plan file to project notes.

Called by PostToolUse hook when ExitPlanMode is triggered.
Copies plan from ~/.claude/plans/ to configurable location.

Session Isolation:
  Uses transcript_path from hook input to identify which plan file
  belongs to the current session. Falls back to most recent file
  only if transcript parsing fails.

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

Configuration (~/.claude/plan-export.config.json):
  enabled                  - Enable/disable plan export (default: true)
  output_plan_dir          - Directory for exported plans (default: "notes")
  filename_pattern         - Filename template (default: "{datetime}_{name}")
  extension                - File extension (default: ".md")
  export_rejected          - Save rejected plans (default: true)
  output_rejected_plan_dir - Directory for rejected plans (default: "notes/rejected")
  debug_logging            - Enable debug logging to diagnose issues (default: false)
  notify_claude            - Show export confirmation message to Claude (default: true)
"""

import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Default configuration
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


def get_config_path() -> Path:
    """Get the path to the plugin config file."""
    return Path.home() / ".claude" / "plan-export.config.json"


def load_config() -> dict:
    """Load the current configuration with defaults."""
    config = DEFAULT_CONFIG.copy()
    config_path = get_config_path()
    if config_path.exists():
        try:
            with open(config_path) as f:
                user_config = json.load(f)
            config.update(user_config)
        except (json.JSONDecodeError, IOError):
            pass
    return config


def is_enabled() -> bool:
    """Check if plan export is enabled."""
    return load_config().get("enabled", True)


def get_most_recent_plan() -> Path | None:
    """Find the most recently modified plan file.

    Note: This is a fallback method. Prefer get_plan_from_transcript() when
    transcript_path is available, as it correctly identifies the session's plan.
    """
    plans_dir = Path.home() / ".claude" / "plans"
    if not plans_dir.exists():
        return None

    plan_files = list(plans_dir.glob("*.md"))
    if not plan_files:
        return None

    # Sort by modification time, most recent first
    plan_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return plan_files[0]


def get_plan_from_transcript(transcript_path: str) -> Path | None:
    """Extract plan file path from session transcript.

    Parses the JSONL transcript to find file-history-snapshot entries
    that track which plan file was edited in this session. This ensures
    the correct plan is exported when multiple sessions are active.

    Args:
        transcript_path: Path to the session's JSONL transcript file.

    Returns:
        Path to the plan file edited in this session, or None if not found.
    """
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
                    if entry.get("type") == "file-history-snapshot":
                        tracked = entry.get("snapshot", {}).get("trackedFileBackups", {})
                        for file_path in tracked.keys():
                            # Use proper path comparison to avoid false positives
                            # e.g., don't match /plans-backup/ when looking for /plans/
                            try:
                                path_obj = Path(file_path)
                                if path_obj.parent == plans_dir and file_path.endswith(".md"):
                                    found_plans.add(file_path)
                            except (ValueError, TypeError):
                                continue
                except json.JSONDecodeError:
                    continue
    except IOError:
        return None

    if not found_plans:
        return None

    # Return the plan file (usually only one per session)
    # If multiple, use modification time as tiebreaker
    valid_plans = []
    for p in found_plans:
        path = Path(p)
        if path.exists():
            valid_plans.append(path)
    if not valid_plans:
        return None

    valid_plans.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return valid_plans[0]


def extract_useful_name(plan_path: Path) -> str:
    """Extract a useful name from the plan content or filename.

    Tries to find a meaningful name from:
    1. First heading in the plan
    2. First non-empty line
    3. Original filename
    """
    try:
        content = plan_path.read_text(encoding="utf-8")
        lines = content.strip().split("\n")

        for line in lines:
            line = line.strip()
            # Skip empty lines
            if not line:
                continue

            # Check for markdown heading
            if line.startswith("#"):
                # Remove # prefix and clean up
                name = re.sub(r"^#+\s*", "", line)
                name = sanitize_filename(name)
                if name:
                    return name[:50]  # Limit length

            # Use first non-empty line if no heading found
            name = sanitize_filename(line)
            if name:
                return name[:50]
    except IOError:
        pass

    # Fallback to original filename without extension
    return plan_path.stem


def sanitize_filename(name: str) -> str:
    """Convert a string to a safe filename component."""
    # Remove or replace unsafe characters (extended set for safety)
    name = re.sub(r'[<>:"/\\|?*&@#$%^!`~\[\]{}();\']+', "", name)
    # Replace spaces, underscores, and other separators with single underscore
    name = re.sub(r"[\s_.,]+", "_", name)
    name = re.sub(r"-+", "-", name)
    # Remove leading/trailing underscores and hyphens
    name = name.strip("_-")
    # Convert to lowercase for consistency
    name = name.lower()
    return name


def expand_template(template: str, plan_path: Path, plan_name: str) -> str:
    """Expand template variables in a string.

    Variables:
      {YYYY}     - 4-digit year
      {YY}       - 2-digit year
      {MM}       - Month 01-12
      {DD}       - Day 01-31
      {HH}       - Hour 00-23
      {mm}       - Minute 00-59
      {date}     - Full date YYYY_MM_DD
      {datetime} - Full datetime YYYY_MM_DD_HHmm
      {name}     - Extracted plan name
      {original} - Original filename without extension
    """
    now = datetime.now()
    replacements = {
        "{YYYY}": now.strftime("%Y"),
        "{YY}": now.strftime("%y"),
        "{MM}": now.strftime("%m"),
        "{DD}": now.strftime("%d"),
        "{HH}": now.strftime("%H"),
        "{mm}": now.strftime("%M"),
        "{date}": now.strftime("%Y_%m_%d"),
        "{datetime}": now.strftime("%Y_%m_%d_%H%M"),
        "{name}": plan_name,
        "{original}": plan_path.stem,
    }

    result = template
    for var, value in replacements.items():
        result = result.replace(var, value)
    return result


def export_plan(plan_path: Path, project_dir: Path) -> dict:
    """Export the plan file to the configured location.

    Returns a dict with status information for the hook response.
    """
    config = load_config()
    output_plan_dir = config.get("output_plan_dir", "notes")
    filename_pattern = config.get("filename_pattern", "{datetime}_{name}")
    extension = config.get("extension", ".md")

    # Extract useful name for template
    useful_name = extract_useful_name(plan_path)

    # Expand templates in output_plan_dir (supports {YYYY}/{MM} style paths)
    expanded_dir = expand_template(output_plan_dir, plan_path, useful_name)
    note_dir = project_dir / expanded_dir
    note_dir.mkdir(parents=True, exist_ok=True)

    # Expand template in filename
    base_filename = expand_template(filename_pattern, plan_path, useful_name)
    # Sanitize the expanded filename
    base_filename = sanitize_filename(base_filename)
    dest_filename = f"{base_filename}{extension}"
    dest_path = note_dir / dest_filename

    # Handle filename collision by adding a suffix
    counter = 1
    while dest_path.exists():
        dest_filename = f"{base_filename}_{counter}{extension}"
        dest_path = note_dir / dest_filename
        counter += 1

    # Copy the plan file
    shutil.copy2(plan_path, dest_path)

    return {
        "success": True,
        "source": str(plan_path),
        "destination": str(dest_path),
        "message": f"Plan exported to {dest_path.relative_to(project_dir)}"
    }


def export_rejected_plan(plan_path: Path, project_dir: Path) -> dict:
    """Export a rejected plan to the rejected plan directory.

    Rejected plans are saved to the configured rejected plan directory
    (default: "notes/rejected").

    Returns a dict with status information for the hook response.
    """
    config = load_config()
    output_rejected_plan_dir = config.get("output_rejected_plan_dir", "notes/rejected")
    filename_pattern = config.get("filename_pattern", "{datetime}_{name}")
    extension = config.get("extension", ".md")

    # Extract useful name for template
    useful_name = extract_useful_name(plan_path)

    # Expand templates in output_rejected_plan_dir (supports {YYYY}/{MM} style paths)
    expanded_dir = expand_template(output_rejected_plan_dir, plan_path, useful_name)
    note_dir = project_dir / expanded_dir
    note_dir.mkdir(parents=True, exist_ok=True)

    # Expand template in filename
    base_filename = expand_template(filename_pattern, plan_path, useful_name)
    base_filename = sanitize_filename(base_filename)
    dest_filename = f"{base_filename}{extension}"
    dest_path = note_dir / dest_filename

    # Handle filename collision by adding a suffix
    counter = 1
    while dest_path.exists():
        dest_filename = f"{base_filename}_{counter}{extension}"
        dest_path = note_dir / dest_filename
        counter += 1

    # Copy the plan file
    shutil.copy2(plan_path, dest_path)

    return {
        "success": True,
        "source": str(plan_path),
        "destination": str(dest_path),
        "message": f"Rejected plan saved to {dest_path.relative_to(project_dir)}"
    }


def main():
    """Main entry point - called by PostToolUse hook."""
    # Read hook input from stdin
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        hook_input = {}

    # Check if enabled
    if not is_enabled():
        result = {
            "continue": True,
            "suppressOutput": True
        }
        print(json.dumps(result))
        return

    # Get project directory from hook input or current working directory
    project_dir = Path(hook_input.get("cwd", os.getcwd()))

    # Get transcript path for session-aware plan selection
    transcript_path = hook_input.get("transcript_path")

    # Get tool_response to detect if plan was approved
    tool_response = hook_input.get("tool_response", {})

    # Detect if plan was approved
    # tool_response contains "User has approved your plan" when approved
    is_approved = "approved" in str(tool_response).lower()

    # Debug logging to diagnose approval detection (if enabled in config)
    config = load_config()
    if config.get("debug_logging", False):
        try:
            debug_log = Path.home() / ".claude" / "plan-export-debug.log"
            with open(debug_log, "a") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"Timestamp: {datetime.now()}\n")
                f.write(f"is_approved: {is_approved}\n")
                f.write(f"\ntool_response type: {type(tool_response)}\n")
                f.write(f"tool_response: {json.dumps(tool_response, indent=2)}\n")
                f.write(f"\ntool_input: {json.dumps(hook_input.get('tool_input', {}), indent=2)}\n")
                f.write(f"{'='*60}\n")
        except Exception as e:
            # Don't let logging errors break the export
            pass

    # Try to get plan from session transcript first (fixes cross-session bug)
    plan_path = None
    if transcript_path:
        plan_path = get_plan_from_transcript(transcript_path)

    # Fall back to most recent plan if transcript parsing failed
    if not plan_path:
        plan_path = get_most_recent_plan()

    if not plan_path:
        result = {
            "continue": True,
            "systemMessage": "No plan files found to export."
        }
        print(json.dumps(result))
        return

    config = load_config()

    # Export based on approval status
    try:
        if is_approved:
            export_result = export_plan(plan_path, project_dir)
        elif config.get("export_rejected", True):
            export_result = export_rejected_plan(plan_path, project_dir)
        else:
            # Rejected plans disabled, skip export
            result = {
                "continue": True,
                "suppressOutput": True
            }
            print(json.dumps(result))
            return

        # Conditionally show export message to Claude based on notify_claude setting
        if config.get("notify_claude", True):
            result = {
                "continue": True,
                "systemMessage": export_result["message"]
            }
        else:
            result = {
                "continue": True,
                "suppressOutput": True
            }
    except Exception as e:
        # Always show errors regardless of notify_claude setting
        result = {
            "continue": True,
            "systemMessage": f"Plan export failed: {e}"
        }

    print(json.dumps(result))


if __name__ == "__main__":
    main()
