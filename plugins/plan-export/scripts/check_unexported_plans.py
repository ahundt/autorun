#!/usr/bin/env python3
"""
Check for Unexported Plans - SessionStart Hook Workaround

This script runs on SessionStart to catch plans that weren't exported
due to the "fresh context" bug (Option 1 in plan accept doesn't fire
PostToolUse hooks).

Strategy:
1. Check for recent plans in ~/.claude/plans/ (last 24 hours)
2. Compare against export tracking file to find unexported ones
3. Export any unexported plans to current project's notes/
4. Uses content hashing to prevent double-exports (robust to bug fix)

Tracking File: ~/.claude/plan-export-tracking.json
Format: {"<content_hash>": {"exported_at": "<timestamp>", "destination": "<path>"}}
"""

import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# =============================================================================
# Configuration
# =============================================================================

PLANS_DIR = Path.home() / ".claude" / "plans"
TRACKING_FILE = Path.home() / ".claude" / "plan-export-tracking.json"
CONFIG_FILE = Path.home() / ".claude" / "plan-export.config.json"
MAX_PLAN_AGE_HOURS = 24  # Only check plans from the last 24 hours
MAX_TRACKING_AGE_DAYS = 7  # Clean up tracking entries older than 7 days

# =============================================================================
# Helper Functions
# =============================================================================

def load_config() -> dict:
    """Load plan-export configuration."""
    default_config = {
        "enabled": True,
        "output_plan_dir": "notes",
        "filename_pattern": "{datetime}_{name}",
        "extension": ".md",
        "notify_claude": True,
        "debug_logging": False,
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                user_config = json.load(f)
            default_config.update(user_config)
        except (json.JSONDecodeError, IOError):
            pass
    return default_config


def load_tracking() -> dict:
    """Load export tracking data."""
    if TRACKING_FILE.exists():
        try:
            with open(TRACKING_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_tracking(tracking: dict) -> None:
    """Save export tracking data."""
    try:
        with open(TRACKING_FILE, "w") as f:
            json.dump(tracking, f, indent=2)
    except IOError:
        pass


def get_content_hash(file_path: Path) -> str:
    """Get SHA256 hash of file content."""
    try:
        content = file_path.read_bytes()
        return hashlib.sha256(content).hexdigest()[:16]  # Use first 16 chars
    except IOError:
        return ""


def clean_old_tracking_entries(tracking: dict) -> dict:
    """Remove tracking entries older than MAX_TRACKING_AGE_DAYS."""
    cutoff = datetime.now() - timedelta(days=MAX_TRACKING_AGE_DAYS)
    cutoff_str = cutoff.isoformat()

    cleaned = {}
    for hash_key, entry in tracking.items():
        exported_at = entry.get("exported_at", "")
        if exported_at >= cutoff_str:
            cleaned[hash_key] = entry

    return cleaned


def get_recent_plans() -> list[Path]:
    """Get plans modified in the last MAX_PLAN_AGE_HOURS."""
    if not PLANS_DIR.exists():
        return []

    cutoff = time.time() - (MAX_PLAN_AGE_HOURS * 3600)
    recent = []

    for plan_file in PLANS_DIR.glob("*.md"):
        try:
            if plan_file.stat().st_mtime >= cutoff:
                recent.append(plan_file)
        except OSError:
            continue

    # Sort by modification time, newest first
    recent.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return recent


def sanitize_filename(name: str) -> str:
    """Convert a string to a safe filename component."""
    import re
    # Remove unsafe characters
    name = re.sub(r'[<>:"/\\|?*&@#$%^!`~\[\]{}();\']+', "", name)
    # Replace spaces and other separators with underscores
    name = re.sub(r"[\s.,-]+", "_", name)
    # Collapse multiple underscores
    name = re.sub(r"_+", "_", name)
    # Remove leading/trailing underscores
    name = name.strip("_").lower()
    return name


def extract_plan_name(plan_path: Path) -> str:
    """Extract useful name from plan content."""
    import re
    try:
        content = plan_path.read_text(encoding="utf-8")
        lines = content.strip().split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Check for markdown heading
            if line.startswith("#"):
                name = re.sub(r"^#+\s*", "", line)
                name = sanitize_filename(name)
                if name:
                    return name
            # Use first non-empty line
            name = sanitize_filename(line)
            if name:
                return name
    except IOError:
        pass
    return plan_path.stem


def export_plan(plan_path: Path, project_dir: Path) -> dict | None:
    """Export a single plan to the project's notes directory."""
    config = load_config()
    output_dir = project_dir / config.get("output_plan_dir", "notes")
    extension = config.get("extension", ".md")

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate filename
    now = datetime.now()
    plan_name = extract_plan_name(plan_path)
    filename_pattern = config.get("filename_pattern", "{datetime}_{name}")

    base_filename = filename_pattern.replace("{datetime}", now.strftime("%Y_%m_%d_%H%M"))
    base_filename = base_filename.replace("{date}", now.strftime("%Y_%m_%d"))
    base_filename = base_filename.replace("{name}", plan_name)
    base_filename = base_filename.replace("{original}", plan_path.stem)
    base_filename = sanitize_filename(base_filename)

    dest_filename = f"{base_filename}{extension}"
    dest_path = output_dir / dest_filename

    # Handle collision
    counter = 1
    while dest_path.exists():
        dest_filename = f"{base_filename}_{counter}{extension}"
        dest_path = output_dir / dest_filename
        counter += 1

    # Copy the plan
    try:
        shutil.copy2(plan_path, dest_path)
        return {
            "source": str(plan_path),
            "destination": str(dest_path),
            "message": f"Plan exported to {dest_path.relative_to(project_dir)}"
        }
    except IOError as e:
        return None


def log_debug(message: str) -> None:
    """Write to debug log if enabled."""
    config = load_config()
    if config.get("debug_logging", False):
        try:
            debug_log = Path.home() / ".claude" / "plan-export-debug.log"
            with open(debug_log, "a") as f:
                f.write(f"[{datetime.now()}] check_unexported: {message}\n")
        except IOError:
            pass


# =============================================================================
# Main
# =============================================================================

def main():
    """Check for unexported plans on session start."""
    # Read hook input
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        hook_input = {}

    config = load_config()
    if not config.get("enabled", True):
        print(json.dumps({"continue": True}))
        return

    # Get current project directory
    project_dir = Path(hook_input.get("cwd", os.getcwd()))
    session_id = hook_input.get("session_id", "unknown")

    log_debug(f"Session {session_id} started in {project_dir}")

    # Load and clean tracking
    tracking = load_tracking()
    tracking = clean_old_tracking_entries(tracking)

    # Get recent plans
    recent_plans = get_recent_plans()
    if not recent_plans:
        log_debug("No recent plans found")
        print(json.dumps({"continue": True}))
        return

    # Find unexported plans
    unexported = []
    for plan_path in recent_plans:
        content_hash = get_content_hash(plan_path)
        if not content_hash:
            continue

        if content_hash not in tracking:
            unexported.append((plan_path, content_hash))
            log_debug(f"Found unexported plan: {plan_path.name} (hash: {content_hash})")

    if not unexported:
        log_debug("All recent plans already exported")
        print(json.dumps({"continue": True}))
        return

    # Export unexported plans (only the most recent one to avoid spam)
    # We only export the most recent to minimize noise on session start
    plan_path, content_hash = unexported[0]

    log_debug(f"Exporting missed plan: {plan_path.name}")
    result = export_plan(plan_path, project_dir)

    if result:
        # Record in tracking
        tracking[content_hash] = {
            "exported_at": datetime.now().isoformat(),
            "destination": result["destination"],
            "source": result["source"],
            "via": "session_start_workaround"
        }
        save_tracking(tracking)

        # Notify user
        if config.get("notify_claude", True):
            message = f"📋 Recovered unexported plan: {result['message']}"
            print(json.dumps({
                "continue": True,
                "systemMessage": message,
                "additionalContext": f"\n{message}\n(Plan was not exported due to fresh context reset)\n"
            }))
        else:
            print(json.dumps({"continue": True}))
    else:
        log_debug(f"Failed to export {plan_path.name}")
        print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
