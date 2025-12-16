#!/usr/bin/env python3
"""
Configuration script for plan-export plugin.

Usage:
    python3 config.py enable              - Enable plan export
    python3 config.py disable             - Disable plan export
    python3 config.py status              - Show current status
    python3 config.py dir <path>          - Set output directory
    python3 config.py pattern <pattern>   - Set filename pattern
    python3 config.py preset <name>       - Apply a preset configuration
    python3 config.py presets             - List available presets
    python3 config.py reset               - Reset to defaults

Template Variables (for dir and pattern):
    {YYYY}     - 4-digit year (2025)
    {YY}       - 2-digit year (25)
    {MM}       - Month 01-12
    {DD}       - Day 01-31
    {date}     - Full date YYYY_MM_DD
    {name}     - Extracted plan name from heading
    {original} - Original plan filename (without .md)
"""

import json
import sys
from pathlib import Path

# Default configuration
DEFAULT_CONFIG = {
    "enabled": True,
    "output_dir": "notes",
    "filename_pattern": "{date}_{name}",
    "extension": ".md"
}

# Preset configurations
PRESETS = {
    "default": {
        "output_dir": "notes",
        "filename_pattern": "{date}_{name}",
        "description": "Standard: notes/YYYY_MM_DD_name.md"
    },
    "plans": {
        "output_dir": "plans",
        "filename_pattern": "{date}_{name}",
        "description": "Plans folder: plans/YYYY_MM_DD_name.md"
    },
    "docs": {
        "output_dir": "docs/plans",
        "filename_pattern": "{date}_{name}",
        "description": "Documentation: docs/plans/YYYY_MM_DD_name.md"
    },
    "dated": {
        "output_dir": "notes/{YYYY}/{MM}",
        "filename_pattern": "{DD}_{name}",
        "description": "Date hierarchy: notes/YYYY/MM/DD_name.md"
    },
    "yearly": {
        "output_dir": "notes/{YYYY}",
        "filename_pattern": "{MM}_{DD}_{name}",
        "description": "Yearly folders: notes/YYYY/MM_DD_name.md"
    },
    "simple": {
        "output_dir": "notes",
        "filename_pattern": "{name}",
        "description": "Name only: notes/name.md (overwrites!)"
    },
    "archive": {
        "output_dir": ".archive/plans/{YYYY}",
        "filename_pattern": "{date}_{name}",
        "description": "Hidden archive: .archive/plans/YYYY/date_name.md"
    },
    "original": {
        "output_dir": "notes",
        "filename_pattern": "{date}_{original}",
        "description": "Keep original name: notes/YYYY_MM_DD_original.md"
    }
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
    config["output_dir"] = path
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


def apply_preset(name: str):
    """Apply a preset configuration."""
    if name not in PRESETS:
        print(f"Unknown preset: {name}")
        print("Available presets:")
        list_presets()
        sys.exit(1)

    preset = PRESETS[name]
    config = load_config()
    config["output_dir"] = preset["output_dir"]
    config["filename_pattern"] = preset["filename_pattern"]
    save_config(config)
    print(f"Applied preset: {name}")
    print(f"  {preset['description']}")
    show_current_settings(config)


def list_presets():
    """List available presets."""
    print("\nAvailable Presets:")
    print("-" * 60)
    for name, preset in PRESETS.items():
        print(f"  {name:12} - {preset['description']}")
    print("-" * 60)
    print("\nUsage: python3 config.py preset <name>")


def reset():
    """Reset to default configuration."""
    save_config(DEFAULT_CONFIG.copy())
    print("Configuration reset to defaults")
    show_current_settings(DEFAULT_CONFIG)


def show_current_settings(config: dict):
    """Show the current output settings."""
    print("\nCurrent Settings:")
    print(f"  Directory: {config.get('output_dir', 'notes')}")
    print(f"  Pattern:   {config.get('filename_pattern', '{date}_{name}')}")
    print(f"  Extension: {config.get('extension', '.md')}")

    # Show example output
    example_dir = config.get('output_dir', 'notes').replace('{YYYY}', '2025').replace('{YY}', '25').replace('{MM}', '12').replace('{DD}', '10')
    example_file = config.get('filename_pattern', '{date}_{name}').replace('{date}', '2025_12_10').replace('{YYYY}', '2025').replace('{YY}', '25').replace('{MM}', '12').replace('{DD}', '10').replace('{name}', 'my_plan').replace('{original}', 'fuzzy-dancing-star')
    print(f"\n  Example: {example_dir}/{example_file}{config.get('extension', '.md')}")


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
    print("  {YYYY} {YY} {MM} {DD} {date} {name} {original}")
    print("\nCommands: enable, disable, dir, pattern, preset, presets, reset")


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
            print("Usage: python3 config.py dir <path>")
            print("Example: python3 config.py dir note/{YYYY}/{MM}")
            sys.exit(1)
        set_dir(sys.argv[2])
    elif command == "pattern":
        if len(sys.argv) < 3:
            print("Usage: python3 config.py pattern <pattern>")
            print("Example: python3 config.py pattern {date}_{name}")
            sys.exit(1)
        set_pattern(sys.argv[2])
    elif command == "preset":
        if len(sys.argv) < 3:
            print("Usage: python3 config.py preset <name>")
            list_presets()
            sys.exit(1)
        apply_preset(sys.argv[2])
    elif command == "presets":
        list_presets()
    elif command == "reset":
        reset()
    elif command in ("help", "-h", "--help"):
        show_help()
    else:
        print(f"Unknown command: {command}")
        print("Use 'python3 config.py help' for usage information")
        sys.exit(1)


if __name__ == "__main__":
    main()
