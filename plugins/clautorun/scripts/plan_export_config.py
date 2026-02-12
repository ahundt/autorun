#!/usr/bin/env python3
"""
Configuration script for plan export (part of clautorun plugin).

Usage (invoked via uv from slash commands):
    uv run python plan_export_config.py enable            - Enable plan export
    uv run python plan_export_config.py disable           - Disable plan export
    uv run python plan_export_config.py status            - Show current status
    uv run python plan_export_config.py dir <path>        - Set output directory
    uv run python plan_export_config.py pattern <pattern> - Set filename pattern
    uv run python plan_export_config.py rejected-toggle   - Toggle rejected plan export
    uv run python plan_export_config.py rejected-dir <path> - Set rejected plan directory
    uv run python plan_export_config.py reset             - Reset to defaults

Template Variables (for dir and pattern):
    {YYYY}     - 4-digit year (2025)
    {YY}       - 2-digit year (25)
    {MM}       - Month 01-12
    {DD}       - Day 01-31
    {HH}       - Hour 00-23
    {mm}       - Minute 00-59
    {ss}       - Second 00-59
    {date}     - Full date YYYY_MM_DD
    {datetime} - Full date+time YYYY_MM_DD_HHmm
    {name}     - Extracted plan name from heading
    {original} - Original plan filename (without .md)
"""

import json
import sys
from pathlib import Path

# Default configuration
DEFAULT_CONFIG = {
    "enabled": True,
    "output_plan_dir": "notes",
    "filename_pattern": "{datetime}_{name}",
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
            # Migrate legacy key name (was "output_dir" before merge into clautorun)
            if "output_dir" in user_config and "output_plan_dir" not in user_config:
                user_config["output_plan_dir"] = user_config.pop("output_dir")
            config.update(user_config)
        except (json.JSONDecodeError, IOError):
            pass
    return config


def save_config(config: dict) -> None:
    """Save the configuration."""
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)


def enable():
    """Enable plan export."""
    config = load_config()
    config["enabled"] = True
    save_config(config)
    print("Plan export ENABLED")
    show_current_settings(config)


def disable():
    """Disable plan export."""
    config = load_config()
    config["enabled"] = False
    save_config(config)
    print("Plan export DISABLED")


def set_dir(path: str):
    """Set the output directory."""
    config = load_config()
    config["output_plan_dir"] = path
    save_config(config)
    print(f"Output directory set to: {path}")
    show_current_settings(config)


def set_pattern(pattern: str):
    """Set the filename pattern."""
    config = load_config()
    config["filename_pattern"] = pattern
    save_config(config)
    print(f"Filename pattern set to: {pattern}")
    show_current_settings(config)


def toggle_rejected():
    """Toggle rejected plan export on/off."""
    config = load_config()
    current = config.get("export_rejected", False)
    config["export_rejected"] = not current
    save_config(config)
    state = "ENABLED" if config["export_rejected"] else "DISABLED"
    print(f"Rejected plan export {state}")
    if config["export_rejected"]:
        print(f"  Rejected plans dir: {config.get('output_rejected_plan_dir', 'notes/rejected')}")


def set_rejected_dir(path: str):
    """Set the rejected plans output directory."""
    config = load_config()
    config["output_rejected_plan_dir"] = path
    save_config(config)
    print(f"Rejected plans directory set to: {path}")


def reset():
    """Reset to default configuration."""
    save_config(DEFAULT_CONFIG.copy())
    print("Configuration reset to defaults")
    show_current_settings(DEFAULT_CONFIG)


def show_current_settings(config: dict):
    """Show the current output settings."""
    print("\nCurrent Settings:")
    print(f"  Directory: {config.get('output_plan_dir', 'notes')}")
    print(f"  Pattern:   {config.get('filename_pattern', '{datetime}_{name}')}")
    print(f"  Extension: {config.get('extension', '.md')}")

    # Show example output with all template variables expanded
    replacements = {
        '{datetime}': '2025_12_10_1430', '{date}': '2025_12_10',
        '{YYYY}': '2025', '{YY}': '25', '{MM}': '12', '{DD}': '10',
        '{HH}': '14', '{mm}': '30', '{ss}': '45',
        '{name}': 'my_plan', '{original}': 'fuzzy-dancing-star',
    }
    example_dir = config.get('output_plan_dir', 'notes')
    example_file = config.get('filename_pattern', '{datetime}_{name}')
    for var, val in replacements.items():
        example_dir = example_dir.replace(var, val)
        example_file = example_file.replace(var, val)
    print(f"\n  Example: {example_dir}/{example_file}{config.get('extension', '.md')}")

    # Show rejected plan settings if enabled
    if config.get('export_rejected', False):
        rejected_dir = config.get('output_rejected_plan_dir', 'notes/rejected')
        print(f"  Rejected: {rejected_dir}/")


def status():
    """Show current status."""
    config = load_config()
    enabled = config.get("enabled", True)

    print("Plan Export Configuration")
    print("=" * 60)
    print(f"  Status:    {'ENABLED' if enabled else 'DISABLED'}")
    show_current_settings(config)

    # Show plans directory info
    plans_dir = Path.home() / ".claude" / "plans"
    if plans_dir.exists():
        plan_count = len(list(plans_dir.glob("*.md")))
        print(f"\n  Plans source: {plans_dir}")
        print(f"  Plan files:   {plan_count}")

    print(f"\n  Config file:  {get_config_path()}")
    print("=" * 60)

    print("\nTemplate Variables:")
    print("  {YYYY} {YY} {MM} {DD} {HH} {mm} {ss} {date} {datetime} {name} {original}")
    print("\nCommands: enable, disable, dir, pattern, rejected-toggle, rejected-dir, reset")


def show_help():
    """Show help message."""
    print(__doc__)


def main():
    if len(sys.argv) < 2:
        status()
        return

    command = sys.argv[1].lower()

    if command == "enable":
        enable()
    elif command == "disable":
        disable()
    elif command == "status":
        status()
    elif command == "dir" or command == "path":
        if len(sys.argv) < 3:
            print("Usage: uv run python plan_export_config.py dir <path>")
            print("Example: uv run python plan_export_config.py dir note/{YYYY}/{MM}")
            sys.exit(1)
        set_dir(sys.argv[2])
    elif command == "pattern":
        if len(sys.argv) < 3:
            print("Usage: uv run python plan_export_config.py pattern <pattern>")
            print("Example: uv run python plan_export_config.py pattern {date}_{name}")
            sys.exit(1)
        set_pattern(sys.argv[2])
    elif command == "rejected-toggle":
        toggle_rejected()
    elif command == "rejected-dir":
        if len(sys.argv) < 3:
            print("Usage: uv run python plan_export_config.py rejected-dir <path>")
            print("Example: uv run python plan_export_config.py rejected-dir notes/rejected")
            sys.exit(1)
        set_rejected_dir(sys.argv[2])
    elif command == "reset":
        reset()
    elif command in ("help", "-h", "--help"):
        show_help()
    else:
        print(f"Unknown command: {command}")
        print("Use 'uv run python plan_export_config.py help' for usage information")
        sys.exit(1)


if __name__ == "__main__":
    main()
