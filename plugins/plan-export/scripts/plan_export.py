#!/usr/bin/env python3
"""
Export Plan Script - Copies the session's plan file to project notes.

Called by PostToolUse hook when ExitPlanMode is triggered.
Copies plan from ~/.claude/plans/ to configurable location.

Session Isolation:
  Uses transcript_path from hook input to identify which plan file
  belongs to the current session. Falls back to most recent file
  only if transcript parsing fails.

Approval Detection:
  Uses permission_mode field from PostToolUse hook to determine if the
  user approved the plan. Based on Claude Code hooks documentation and
  GitHub issue #5036, tool_response is unreliable for approval detection.

  Permission modes:
    - "acceptEdits"      → Approved (user accepted edits)
    - "bypassPermissions" → Approved (user bypassed permissions)
    - "plan"             → Approved (user exited plan mode)
    - "default"          → Approved (user exited plan mode)

  Rationale: Exiting plan mode by any method indicates intent to proceed.
  Only explicit cancellation prevents plan export (and doesn't trigger hook).

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

# Import SessionLock for session-isolated plan export
# This prevents race conditions when multiple sessions export simultaneously
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "clautorun" / "src"))
from clautorun.session_manager import SessionLock, SessionTimeoutError

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


def log_warning(message: str) -> None:
    """Log warning message to debug log if enabled."""
    config = load_config()
    if config.get("debug_logging", False):
        try:
            debug_log = Path.home() / ".claude" / "plan-export-debug.log"
            with open(debug_log, "a") as f:
                f.write(f"[{datetime.now()}] WARNING: {message}\n")
        except Exception:
            pass  # Don't let logging break the export


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


def get_plan_from_metadata(plan_path: Path) -> str | None:
    """Extract session_id from plan file metadata.

    Plan files exported by this plugin include YAML frontmatter with:
    - session_id: The session that created this plan
    - original_path: The original plan file path
    - export_timestamp: When the plan was exported

    This provides recoverability when transcript parsing fails or when
    sessions are resumed in new Claude Code instances.

    Args:
        plan_path: Path to the plan file to check.

    Returns:
        The session_id from metadata, or None if not found.
    """
    try:
        content = plan_path.read_text(encoding="utf-8")

        # Check for YAML frontmatter (starts with ---)
        if not content.startswith("---"):
            return None

        # Extract frontmatter (between first and second ---)
        frontmatter_end = content.find("\n---", 4)
        if frontmatter_end == -1:
            return None

        frontmatter = content[4:frontmatter_end].strip()

        # Parse simple YAML-like metadata
        for line in frontmatter.split("\n"):
            if line.startswith("session_id:"):
                session_id = line.split(":", 1)[1].strip()
                # Remove quotes if present
                session_id = session_id.strip('"\'')
                return session_id

    except (IOError, UnicodeDecodeError):
        pass

    return None


def find_plan_by_session_id(session_id: str) -> Path | None:
    """Find a plan file by searching for session_id in metadata.

    This is a fallback method when transcript parsing fails but
    the plan file has embedded metadata from a previous export.

    Args:
        session_id: The session ID to search for.

    Returns:
        Path to the plan file, or None if not found.
    """
    plans_dir = Path.home() / ".claude" / "plans"
    if not plans_dir.exists():
        return None

    plan_files = list(plans_dir.glob("*.md"))
    if not plan_files:
        return None

    # Check each plan file for matching session_id in metadata
    for plan_path in plan_files:
        metadata_session_id = get_plan_from_metadata(plan_path)
        if metadata_session_id == session_id:
            return plan_path

    return None


def embed_plan_metadata(plan_path: Path, session_id: str, export_destination: Path) -> None:
    """Embed metadata into exported plan file for recoverability.

    Adds YAML frontmatter to the exported plan file containing:
    - session_id: The session that created this plan
    - original_path: The source plan file path
    - export_timestamp: When the plan was exported
    - export_destination: Where the plan was exported to

    This metadata enables:
    - Finding the correct plan when sessions are resumed
    - Recoverability when transcript parsing fails
    - Debugging and audit trail

    Args:
        plan_path: The source plan file path.
        session_id: The current session ID.
        export_destination: Where the plan is being exported to.
    """
    try:
        content = export_destination.read_text(encoding="utf-8")

        # Check if metadata already exists
        if content.startswith("---"):
            # Already has metadata, skip
            return

        # Create YAML frontmatter
        metadata = f"""---
session_id: {session_id}
original_path: {plan_path}
export_timestamp: {datetime.now().isoformat()}
export_destination: {export_destination}
---

"""

        # Prepend metadata to content
        export_destination.write_text(metadata + content, encoding="utf-8")

    except (IOError, UnicodeDecodeError):
        # Don't fail export if metadata embedding fails
        pass


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
                    return name  # Don't truncate - preserve full words

            # Use first non-empty line if no heading found
            name = sanitize_filename(line)
            if name:
                return name  # Don't truncate - preserve full words
    except IOError:
        pass

    # Fallback to original filename without extension
    return plan_path.stem


def sanitize_filename(name: str) -> str:
    """Convert a string to a safe filename component.

    Rules:
    - Preserve full words (no truncation)
    - Detect majority separator (underscore vs dash) and use that consistently
    - Elide redundant separators to single instance
    - Remove unsafe characters
    """
    # Remove or replace unsafe characters (extended set for safety)
    name = re.sub(r'[<>:"/\\|?*&@#$%^!`~\[\]{}();\']+', "", name)

    # Count separators to determine majority preference
    underscore_count = name.count('_')
    dash_count = name.count('-')

    # Determine which separator to use (prefer the one that appears more)
    if underscore_count >= dash_count:
        # Prefer underscores
        name = re.sub(r"[\s.,-]+", "_", name)
        # Collapse multiple underscores
        name = re.sub(r"_+", "_", name)
        # Remove leading/trailing underscores
        name = name.strip("_")
    else:
        # Prefer dashes
        name = re.sub(r"[\s_.,]+", "-", name)
        # Collapse multiple dashes
        name = re.sub(r"-+", "-", name)
        # Remove leading/trailing dashes
        name = name.strip("-")

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


def export_plan(plan_path: Path, project_dir: Path, session_id: str = None) -> dict:
    """Export the plan file to the configured location.

    Args:
        plan_path: The source plan file to export.
        project_dir: The project directory where notes will be saved.
        session_id: The current session ID (for metadata embedding).

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

    # Embed metadata for recoverability (if session_id provided)
    if session_id:
        embed_plan_metadata(plan_path, session_id, dest_path)

    return {
        "success": True,
        "source": str(plan_path),
        "destination": str(dest_path),
        "message": f"Plan exported to {dest_path.relative_to(project_dir)}"
    }


def export_rejected_plan(plan_path: Path, project_dir: Path, session_id: str = None) -> dict:
    """Export a rejected plan to the rejected plan directory.

    Rejected plans are saved to the configured rejected plan directory
    (default: "notes/rejected").

    Args:
        plan_path: The source plan file to export.
        project_dir: The project directory where notes will be saved.
        session_id: The current session ID (for metadata embedding).

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

    # Embed metadata for recoverability (if session_id provided)
    if session_id:
        embed_plan_metadata(plan_path, session_id, dest_path)

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

    # Extract and validate session_id from hook input
    # Session ID is required for session-isolated plan export to prevent race conditions
    session_id = hook_input.get("session_id", "unknown")
    if session_id == "unknown":
        result = {
            "continue": True,
            "systemMessage": "Warning: session_id missing from hook input",
            "additionalContext": "\n⚠️ Export skipped: session_id required for safe export\n"
        }
        print(json.dumps(result))
        return

    # Get project directory from hook input or current working directory
    project_dir = Path(hook_input.get("cwd", os.getcwd()))

    # Get transcript path for session-aware plan selection
    transcript_path = hook_input.get("transcript_path")

    # Get tool_response to detect if plan was approved
    tool_response = hook_input.get("tool_response", {})

    # Detect if plan was approved using permission_mode field
    # Based on Claude Code hooks documentation and GitHub issue #5036:
    # - tool_response is unreliable for approval detection
    # - permission_mode field indicates user's approval context
    permission_mode = hook_input.get("permission_mode", "default")

    # Check permission_mode for approval signals
    if permission_mode in ["acceptEdits", "bypassPermissions"]:
        # User explicitly accepted edits or bypassed permissions
        is_approved = True
    elif permission_mode == "plan":
        # User is in plan mode - exiting plan mode is implicit approval
        is_approved = True
    else:
        # Default mode - treat as approved (user exited plan mode)
        # Rationale: Exiting plan mode indicates intent to proceed
        is_approved = True

    # Note: Only explicit cancellation (which doesn't trigger PostToolUse)
    # prevents plan export. Exiting plan mode by any method indicates approval.

    # Debug logging to diagnose approval detection (if enabled in config)
    config = load_config()
    if config.get("debug_logging", False):
        try:
            debug_log = Path.home() / ".claude" / "plan-export-debug.log"
            with open(debug_log, "a") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"Timestamp: {datetime.now()}\n")
                f.write(f"permission_mode: {permission_mode}\n")
                f.write(f"is_approved: {is_approved}\n")
                f.write(f"\ntool_response type: {type(tool_response)}\n")
                f.write(f"tool_response: {json.dumps(tool_response, indent=2)}\n")
                f.write(f"\ntool_input: {json.dumps(hook_input.get('tool_input', {}), indent=2)}\n")
                f.write(f"{'='*60}\n")
        except Exception as e:
            # Don't let logging errors break the export
            pass

    # Wrap entire plan selection + export in session lock for race condition safety
    # This prevents multiple sessions from exporting simultaneously and causing cross-contamination
    STATE_DIR = Path.home() / ".claude" / "sessions"
    LOCK_TIMEOUT = 10.0  # Seconds

    try:
        with SessionLock(session_id, timeout=LOCK_TIMEOUT, state_dir=STATE_DIR):
            # === BEGIN CRITICAL SECTION ===
            # Mutually exclusive per session - prevents race conditions

            # Step 1: Get plan path (session-isolated)
            plan_path = None
            if transcript_path:
                plan_path = get_plan_from_transcript(transcript_path)

            # Fallback 1: Try to find plan by session_id in metadata
            # This handles session resume scenarios where transcript parsing fails
            if not plan_path:
                config = load_config()
                if config.get("debug_logging", False):
                    log_warning(f"Session {session_id}: transcript parsing failed, trying metadata fallback")
                plan_path = find_plan_by_session_id(session_id)

            # Fallback 2: Most recent plan (original behavior)
            if not plan_path:
                config = load_config()
                if config.get("debug_logging", False):
                    log_warning(f"Session {session_id}: metadata search failed, using most recent plan")
                plan_path = get_most_recent_plan()

            if not plan_path:
                result = {
                    "continue": True,
                    "systemMessage": "No plan files found to export.",
                    "additionalContext": "\n📋 No plan files found to export.\n"
                }
                print(json.dumps(result))
                return

            # Step 2: Export (atomic with selection due to lock)
            # Pass session_id for metadata embedding in exported file
            config = load_config()
            if is_approved:
                export_result = export_plan(plan_path, project_dir, session_id=session_id)
            elif config.get("export_rejected", True):
                export_result = export_rejected_plan(plan_path, project_dir, session_id=session_id)
            else:
                result = {"continue": True, "suppressOutput": True}
                print(json.dumps(result))
                return

            # === END CRITICAL SECTION ===

        # Lock released automatically here

        # Conditionally show export message to Claude AND user via additionalContext
        # Reload config to ensure we have the latest settings
        config = load_config()
        if config.get("notify_claude", True):
            result = {
                "continue": True,
                "systemMessage": export_result["message"],
                "additionalContext": f"\n\n📋 {export_result['message']}\n"
            }
        else:
            result = {
                "continue": True,
                "suppressOutput": True
            }
        print(json.dumps(result))

    except SessionTimeoutError as e:
        # Another export in progress for this session
        result = {
            "continue": True,
            "systemMessage": f"Export skipped: {e}",
            "additionalContext": f"\n⚠️ Export skipped: Another operation in progress\n"
        }
        print(json.dumps(result))
        return

    except Exception as e:
        result = {
            "continue": True,
            "systemMessage": f"Plan export failed: {e}",
            "additionalContext": f"\n❌ Plan export failed: {e}\n"
        }
        print(json.dumps(result))
        return


if __name__ == "__main__":
    main()
