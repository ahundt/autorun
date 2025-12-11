#!/usr/bin/env python3
"""
Export Plan Script - Copies the most recent plan file to project notes.

Called by PostToolUse hook when ExitPlanMode is triggered.
Copies plan from ~/.claude/plans/ to configurable location.

Template Variables:
  {YYYY}     - 4-digit year (2025)
  {YY}       - 2-digit year (25)
  {MM}       - Month 01-12
  {DD}       - Day 01-31
  {date}     - Full date YYYY_MM_DD
  {name}     - Extracted plan name from heading
  {original} - Original plan filename (without .md)
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
    "output_dir": "note",
    "filename_pattern": "{date}_{name}",
    "extension": ".md"
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
    """Find the most recently modified plan file."""
    plans_dir = Path.home() / ".claude" / "plans"
    if not plans_dir.exists():
        return None

    plan_files = list(plans_dir.glob("*.md"))
    if not plan_files:
        return None

    # Sort by modification time, most recent first
    plan_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return plan_files[0]


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
      {date}     - Full date YYYY_MM_DD
      {name}     - Extracted plan name
      {original} - Original filename without extension
    """
    now = datetime.now()
    replacements = {
        "{YYYY}": now.strftime("%Y"),
        "{YY}": now.strftime("%y"),
        "{MM}": now.strftime("%m"),
        "{DD}": now.strftime("%d"),
        "{date}": now.strftime("%Y_%m_%d"),
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
    output_dir = config.get("output_dir", "note")
    filename_pattern = config.get("filename_pattern", "{date}_{name}")
    extension = config.get("extension", ".md")

    # Extract useful name for template
    useful_name = extract_useful_name(plan_path)

    # Expand templates in output_dir (supports {YYYY}/{MM} style paths)
    expanded_dir = expand_template(output_dir, plan_path, useful_name)
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

    # Find most recent plan
    plan_path = get_most_recent_plan()
    if not plan_path:
        result = {
            "continue": True,
            "systemMessage": "No plan files found to export."
        }
        print(json.dumps(result))
        return

    # Export the plan
    try:
        export_result = export_plan(plan_path, project_dir)
        result = {
            "continue": True,
            "systemMessage": export_result["message"]
        }
    except Exception as e:
        result = {
            "continue": True,
            "systemMessage": f"Plan export failed: {e}"
        }

    print(json.dumps(result))


if __name__ == "__main__":
    main()
