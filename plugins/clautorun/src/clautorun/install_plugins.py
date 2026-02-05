#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plugin installation and management for clautorun.

This module provides unified installation functionality for all clautorun marketplace plugins.
It replaces the separate clautorun-marketplace package with a unified `clautorun --install` interface.

Usage:
    clautorun --install                    # Install all plugins
    clautorun --install clautorun          # Install specific plugin
    clautorun --install --force            # Force reinstall (dev workflow)
    clautorun --status                     # Show installation status
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path

__all__ = ["install_plugins", "show_status", "PluginName", "MARKETPLACE"]


class PluginName(str, Enum):
    """Valid plugin names. Prevents typos and enables IDE completion."""

    CLAUTORUN = "clautorun"
    PLAN_EXPORT = "plan-export"
    PDF_EXTRACTOR = "pdf-extractor"

    @classmethod
    def all(cls) -> list[str]:
        """Return all valid plugin names."""
        return [p.value for p in cls]

    @classmethod
    def validate(cls, name: str) -> bool:
        """Check if a plugin name is valid."""
        return name in cls.all()


# Must match .claude-plugin/marketplace.json "name" field
MARKETPLACE = "clautorun"


@dataclass(frozen=True, slots=True)
class CmdResult:
    """Immutable result from command execution."""

    ok: bool
    output: str  # Combined stdout+stderr for error detection

    def has_text(self, text: str) -> bool:
        """Check if output contains text (case-insensitive)."""
        return text.lower() in self.output.lower()


def run_cmd(
    cmd: list[str],
    timeout: int = 60,
    check_executable: bool = True,
) -> CmdResult:
    """Run command with proper error handling.

    Args:
        cmd: Command and arguments
        timeout: Seconds before timeout
        check_executable: If True, verify first arg is in PATH

    Returns:
        CmdResult with ok status and combined output
    """
    if check_executable and not shutil.which(cmd[0]):
        return CmdResult(False, f"{cmd[0]} not found in PATH")

    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        # Combine stdout+stderr - some tools output errors to stdout
        output = f"{r.stdout}\n{r.stderr}".strip()
        return CmdResult(r.returncode == 0, output)
    except subprocess.TimeoutExpired:
        return CmdResult(False, f"Command timed out after {timeout}s")
    except OSError as e:
        return CmdResult(False, f"OS error: {e}")


@lru_cache(maxsize=1)
def find_marketplace_root() -> Path:
    """Find the clautorun marketplace root directory.

    Searches upward from this file for .claude-plugin/marketplace.json.
    Result is cached since it doesn't change during execution.

    Returns:
        Path to marketplace root directory

    Raises:
        FileNotFoundError: If marketplace root cannot be found
    """
    current = Path(__file__).resolve()
    for parent in [current, *current.parents]:
        marker = parent / ".claude-plugin" / "marketplace.json"
        if marker.exists():
            return parent
    raise FileNotFoundError(
        "Could not find marketplace root (.claude-plugin/marketplace.json). "
        "Ensure you're running from within the clautorun repository."
    )


def install_plugins(
    selection: str = "all",
    *,
    tool: bool = False,
    force: bool = False,
) -> int:
    """Install and enable Claude Code plugins.

    Args:
        selection: "all" or comma-separated plugin names (e.g., "clautorun,plan-export")
        tool: Also run `uv tool install` for global CLI availability
        force: Force reinstall even if already installed (for dev with same version)

    Returns:
        Exit code: 0 = success, 1 = failure

    Edge cases handled:
        - Invalid plugin names -> error message, skip
        - Empty selection -> treated as "all"
        - Duplicate plugins -> deduplicated
        - Missing claude CLI -> early exit with guidance
        - Partial failures -> continues with remaining, reports count
    """
    # Parse and validate plugin selection
    if not selection or selection == "all":
        plugins = PluginName.all()
    else:
        # Deduplicate while preserving order
        seen: set[str] = set()
        plugins = []
        for name in selection.split(","):
            name = name.strip()
            if not name or name in seen:
                continue
            if not PluginName.validate(name):
                print(f"Unknown plugin: {name!r} (valid: {', '.join(PluginName.all())})")
                continue
            seen.add(name)
            plugins.append(name)

    if not plugins:
        print("No valid plugins specified")
        return 1

    # Verify claude CLI is available
    if not shutil.which("claude"):
        print("claude CLI not found. Install Claude Code first:")
        print("   https://docs.anthropic.com/claude/docs/claude-code")
        return 1

    # Ensure marketplace is added
    try:
        marketplace_root = find_marketplace_root()
    except FileNotFoundError as e:
        print(f"{e}")
        return 1

    print(f"clautorun-marketplace v0.7.0")
    print(f"Marketplace root: {marketplace_root}")
    print()

    print("Adding clautorun marketplace...")
    result = run_cmd(["claude", "plugin", "marketplace", "add", str(marketplace_root)])
    if result.ok:
        print("   Added clautorun marketplace")
    elif result.has_text("already"):
        print("   clautorun marketplace already exists")
    else:
        print(f"   Marketplace add: {result.output}")

    # Uninstall first if force flag (for same-version reinstall)
    if force:
        print()
        print("Force mode: uninstalling existing plugins...")
        for name in plugins:
            run_cmd(["claude", "plugin", "uninstall", f"{name}@{MARKETPLACE}"])

    # Install + enable each plugin
    print()
    print(f"Installing {len(plugins)} plugin(s):")
    succeeded: list[str] = []
    failed: list[str] = []

    for name in plugins:
        print(f"   {name}...", end=" ", flush=True)

        # Install
        result = run_cmd(["claude", "plugin", "install", f"{name}@{MARKETPLACE}"])
        if not result.ok and not result.has_text("already"):
            print(f"install failed: {result.output}")
            failed.append(name)
            continue

        # Enable
        result = run_cmd(["claude", "plugin", "enable", f"{name}@{MARKETPLACE}"])
        if result.ok or result.has_text("already"):
            print("ok")
            succeeded.append(name)
        else:
            print(f"enable failed: {result.output}")
            failed.append(name)

    # Optional: uv tool install for global CLI
    if tool:
        print()
        print("Installing UV tool...")
        result = run_cmd(["uv", "tool", "install", ".", "--force"], timeout=120)
        if result.ok:
            print("   uv tool: ok")
        else:
            print(f"   uv tool: {result.output}")

    # Summary
    print()
    print("=" * 60)
    if len(succeeded) == len(plugins):
        print(f"Successfully installed all {len(succeeded)} plugins!")
    else:
        print(f"Installed {len(succeeded)}/{len(plugins)} plugins")

    if failed:
        print(f"Failed plugins: {', '.join(failed)}")

    print()
    print("Available commands:")
    print("  /cr:*             - clautorun commands (autorun, file policies, tmux)")
    print("  /plan-export:*    - plan export commands")
    print("  /pdf-extractor:*  - PDF extraction commands")
    print()
    print("Run '/help' to see all available commands.")

    return 0 if len(succeeded) == len(plugins) else 1


def show_status() -> int:
    """Show installation status of all plugins.

    Returns:
        Exit code: 0 = all installed, 1 = some missing
    """
    print("Plugin Status:")
    print("-" * 40)

    # Check claude CLI
    claude_ok = shutil.which("claude") is not None
    if claude_ok:
        print("  claude CLI: found")
    else:
        print("  claude CLI: not found")

    if not claude_ok:
        print()
        print("Install Claude Code first")
        return 1

    # Check each plugin
    all_ok = True
    result = run_cmd(["claude", "plugin", "list"])

    for plugin in PluginName.all():
        # Check if plugin appears in list with "enabled" status
        is_installed = plugin in result.output and "enabled" in result.output
        if is_installed:
            status = "enabled"
        else:
            status = "not installed"
        print(f"  {plugin}: {status}")
        if not is_installed:
            all_ok = False

    # Check for venv
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    if plugin_root:
        venv_path = Path(plugin_root) / ".venv"
        if venv_path.exists():
            print(f"\n  venv: exists at {venv_path}")
        else:
            print("\n  venv: not found")

    return 0 if all_ok else 1


# Backward compatibility alias for clautorun-marketplace
def marketplace_main() -> int:
    """Entry point for backward compatibility with clautorun-marketplace.

    This function is called when users run the old `clautorun-marketplace` command.
    It simply calls install_plugins() with default arguments.
    """
    return install_plugins()


if __name__ == "__main__":
    import sys

    sys.exit(install_plugins())
