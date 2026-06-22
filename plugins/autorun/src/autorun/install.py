#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plugin installation and management for autorun.

Consolidated installer with complete dependency bootstrap and cache fallback.
Superset of install.py and install_plugins.py capabilities.

Usage:
    autorun --install                    # Install all plugins
    autorun --install autorun          # Install specific plugin
    autorun --install --force            # Force reinstall
    autorun --install --tool             # Also install UV CLI tools
    autorun --uninstall                  # Uninstall plugins
    autorun --status                     # Show installation status
"""
# Python 2 / version guard — AI assistants frequently invoke `python` (Python 2 on many
# systems) instead of `python3`, wasting tokens trying to debug confusing import errors.
# This guard outputs a clear, actionable error message so the AI (and user) knows exactly
# how to fix the problem without further investigation.
# Note: Python 3 requires `from __future__ import annotations` to be the first executable
# statement, so the guard code must appear after it. Python 2 users invoking this file
# directly see a SyntaxError on the `from __future__` line; the hook system's
# error_handling.py handles that case.
from __future__ import annotations

import sys as _sys
if _sys.version_info < (3, 10):
    _sys.stderr.write(
        "ERROR: autorun requires Python 3.10+. You are running Python " +
        ".".join(str(v) for v in _sys.version_info[:2]) + ".\n"
        "Fix: Use `uv run python -m autorun --install` or `python3 -m autorun --install`.\n"
        "     Install uv: https://docs.astral.sh/uv/getting-started/installation/\n"
    )
    _sys.exit(1)
del _sys

import json
import logging
import os
import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from functools import lru_cache
from pathlib import Path

from . import ipc
from .platforms import PLATFORMS

try:
    import tomllib
except ImportError:  # pragma: no cover - Python <3.11 compatibility
    import tomli as tomllib

# Configure logging (CLI entry points will set level)
logger = logging.getLogger(__name__)

__all__ = [
    "install_plugins",
    "uninstall_plugins",
    "show_status",
    "install_main",
    "PluginName",
    "CmdResult",
    "MARKETPLACE",
]


# =============================================================================
# Constants
# =============================================================================

# Must match .claude-plugin/marketplace.json "name" field
MARKETPLACE = "autorun"


# =============================================================================
# Data Types
# =============================================================================


class PluginName(str, Enum):
    """Valid plugin names. Prevents typos and enables IDE completion."""

    AUTORUN = "autorun"
    PDF_EXTRACTOR = "pdf-extractor"

    @classmethod
    def all(cls) -> list[str]:
        """Return all valid plugin names."""
        return [p.value for p in cls]

    @classmethod
    def validate(cls, name: str) -> bool:
        """Check if a plugin name is valid."""
        return name in cls.all()


@dataclass(frozen=True, slots=True)
class CmdResult:
    """Immutable result from command execution."""

    ok: bool
    output: str  # Combined stdout+stderr for error detection

    def has_text(self, text: str) -> bool:
        """Check if output contains text (case-insensitive)."""
        return text.lower() in self.output.lower()


# =============================================================================
# Subprocess Helper
# =============================================================================


def run_cmd(
    cmd: list[str],
    timeout: int = 60,
    check_executable: bool = True,
    cwd: Path | str | None = None,
) -> CmdResult:
    """Run command with proper error handling.

    Args:
        cmd: Command and arguments
        timeout: Seconds before timeout
        check_executable: If True, verify first arg is in PATH
        cwd: Working directory for command execution

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
            cwd=cwd,
        )
        # Combine stdout+stderr - some tools output errors to stdout
        output = f"{r.stdout}\n{r.stderr}".strip()
        return CmdResult(r.returncode == 0, output)
    except subprocess.TimeoutExpired:
        return CmdResult(False, f"Command timed out after {timeout}s")
    except OSError as e:
        return CmdResult(False, f"OS error: {e}")


# =============================================================================
# Daemon Management
# =============================================================================


def _restart_daemon_if_running() -> None:
    """Restart the autorun daemon if it's currently running.

    Called at the end of install to ensure the daemon picks up new code/config.
    Imports restart_daemon() from autorun.restart_daemon.
    Non-fatal: installation succeeds even if daemon restart fails.
    """
    try:
        from autorun.restart_daemon import get_daemon_pid, restart_daemon

        pid = get_daemon_pid()
    except Exception as e:
        logger.debug(f"Could not check daemon state before restart: {e}")
        return

    if not pid:
        return

    print()
    print("Restarting daemon to pick up changes...")

    try:
        result = restart_daemon()
        if result == 0:
            print("   Daemon restarted")
        else:
            print("   Daemon restart returned non-zero (non-fatal)")
    except Exception as e:
        logger.warning(f"Daemon restart failed: {e}")
        print(f"   Daemon restart failed (non-fatal): {e}")


# =============================================================================
# UV/Pip Compositional Helpers
# =============================================================================


@lru_cache(maxsize=1)
def has_uv() -> bool:
    """Check if UV is available in PATH (cached for performance).

    Returns:
        True if UV is available, False otherwise
    """
    return shutil.which("uv") is not None


def get_python_runner() -> list[str]:
    """Get Python runner command with UV-first fallback to pip.

    Returns UV-wrapped python command when UV available, falls back to
    bare python command when UV not installed.

    Returns:
        ["uv", "run", "python"] if UV available, else ["python"]

    Examples:
        >>> # When UV available:
        >>> runner = get_python_runner()  # ["uv", "run", "python"]
        >>> cmd = [*runner, "-m", "autorun", "--install"]
        >>> # Result: ["uv", "run", "python", "-m", "autorun", "--install"]

        >>> # When UV unavailable:
        >>> runner = get_python_runner()  # ["python"]
        >>> cmd = [*runner, "-m", "autorun", "--install"]
        >>> # Result: ["python", "-m", "autorun", "--install"]
    """
    return ["uv", "run", "python"] if has_uv() else ["python"]


# =============================================================================
# Error Message Formatter (DRY)
# =============================================================================


@dataclass(frozen=True)
class ErrorFormatter:
    """Centralized error message formatting with actionable remediation.

    All error messages follow WOLOG principle: easy to use correctly,
    hard to use incorrectly. Each error includes:
    1. Clear description of the problem
    2. Multiple solution options (ordered by recommendation)
    3. Troubleshooting section with common pitfalls

    This is a frozen dataclass to ensure immutability and prevent
    accidental modification of error templates.
    """

    MARKETPLACE_NOT_FOUND = """
Could not find marketplace root (.claude-plugin/marketplace.json).

This usually means autorun is installed as a package, not from source.

━━━ SOLUTION OPTIONS ━━━

Option 1: Install via Plugin System (Recommended)
  # For Claude Code:
  claude plugin install https://github.com/ahundt/autorun.git

  # For Gemini CLI:
  gemini extensions install https://github.com/ahundt/autorun.git

Option 2: Local Development from Source
  cd /path/to/autorun  # Git clone directory
  {install_command}

Option 3: AIX Multi-Platform Install
  # Installs for all detected CLIs (Claude, Gemini, OpenCode, Codex)
  aix skills install ahundt/autorun

━━━ TROUBLESHOOTING ━━━

If you're seeing this after 'pip install autorun':
  The pip package doesn't include plugin files (.claude-plugin/, commands/).
  Use Option 1 (plugin install) or Option 2 (local clone) instead.

Need help? https://github.com/ahundt/autorun/issues
"""

    UV_NOT_FOUND = """
UV not found in PATH.

━━━ INSTALL UV ━━━

macOS/Linux:
  curl -LsSf https://astral.sh/uv/install.sh | sh

Windows:
  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

Homebrew:
  brew install uv

Alternatively, use pip fallback:
  {pip_fallback_command}

Docs: https://docs.astral.sh/uv/getting-started/installation/
"""

    @staticmethod
    def marketplace_not_found() -> str:
        """Format marketplace root not found error with UV/pip pathways.

        Returns:
            Formatted error message with installation commands
        """
        runner = get_python_runner()
        install_cmd = " ".join([
            *runner,
            "-m", "plugins.autorun.src.autorun.install",
            "--install", "--force"
        ])
        return ErrorFormatter.MARKETPLACE_NOT_FOUND.format(install_command=install_cmd)

    @staticmethod
    def uv_not_found(pip_fallback: str) -> str:
        """Format UV not found error with installation instructions.

        Args:
            pip_fallback: Pip fallback command to show

        Returns:
            Formatted error message with UV install instructions
        """
        return ErrorFormatter.UV_NOT_FOUND.format(pip_fallback_command=pip_fallback)


# =============================================================================
# Discovery Functions
# =============================================================================


@lru_cache(maxsize=1)
def find_marketplace_root() -> Path:
    """Find the autorun marketplace root directory dynamically.

    Supports all installation pathways:
    1. Source repository (git clone): Walk up from __file__ to find .claude-plugin/
    2. Editable install (uv pip install -e .): Check direct_url.json for source path
    3. UV tool install: Check known tool paths, then try to find dev repo
    4. Gemini extension: Check ~/.gemini/extensions/
    5. Claude plugin cache: Check ~/.claude/plugins/cache/

    Returns:
        Path to marketplace root directory containing .claude-plugin/marketplace.json

    Raises:
        FileNotFoundError: If marketplace root cannot be found in any location
    """
    # Strategy 1: Walk up from __file__ FIRST - respect where we're actually running from
    # Works for: source repo, Gemini extensions, Claude cache, editable installs
    # This ensures Gemini/Claude use their own copies, not jumping to dev repo
    current = Path(__file__).resolve()
    root = None
    for parent in [current, *current.parents]:
        marker = parent / ".claude-plugin" / "marketplace.json"
        if marker.exists():
            # Filter out backup/reference directories unless explicitly in their subdirectories
            parent_str = str(parent).lower()
            if "backup" in parent_str or "reference" in parent_str:
                # Only use backup/reference if we're actually running FROM them
                if str(Path(__file__).resolve()).startswith(str(parent)):
                    root = parent
                # Otherwise skip and keep searching
                continue
            root = parent
    
    if root:
        return root

    # Strategy 2: Check if this is an editable install - look for direct_url.json
    # Works for: uv pip install -e . (points back to source)
    try:
        package_dir = Path(__file__).parent  # autorun package directory
        dist_info_dirs = list(package_dir.parent.glob("autorun*.dist-info"))
        for dist_info in dist_info_dirs:
            direct_url_file = dist_info / "direct_url.json"
            if direct_url_file.exists():
                import json
                data = json.loads(direct_url_file.read_text())
                if "dir_info" in data and "editable" in data["dir_info"]:
                    # This is an editable install - get the source directory
                    source_dir = Path(data["url"].replace("file://", ""))
                    # Source could be workspace root or plugin dir
                    for candidate in [source_dir, source_dir / "plugins" / "autorun"]:
                        marker = candidate / ".claude-plugin" / "marketplace.json"
                        if marker.exists():
                            return candidate
    except (ImportError, json.JSONDecodeError, KeyError, OSError):
        pass

    # Strategy 3: UV tool install fallback - search for any marketplace root
    # Works for: uv tool install . when above strategies failed
    # Only reaches here if UV tool can't find files via Strategy 1 (walk up)
    current_path = str(Path(__file__).resolve())
    if ".local/share/uv/tools" in current_path or ".local/share/uv/python" in current_path:
        # This is a UV tool install - search common base directories for any project
        # containing .claude-plugin/marketplace.json
        search_bases = [
            Path.home() / ".claude",  # Claude-specific projects
            Path.home(),               # Home directory projects
            Path("/opt"),              # System-wide installs
        ]

        # Check if AUTORUN_DEV_PATH env var is set (for custom locations)
        if "AUTORUN_DEV_PATH" in os.environ:
            dev_path = Path(os.environ["AUTORUN_DEV_PATH"])
            if (dev_path / ".claude-plugin" / "marketplace.json").exists():
                return dev_path

        # Search for marketplace.json in common base directories
        for base in search_bases:
            if not base.exists():
                continue
            # Search up to 4 levels deep for .claude-plugin/marketplace.json
            for depth in range(1, 5):
                pattern = "/".join(["*"] * depth) + "/.claude-plugin/marketplace.json"
                try:
                    matches = list(base.glob(pattern))
                    if matches:
                        # Filter and sort matches to prefer main repo over backups/references
                        filtered_matches = []
                        for match in matches:
                            root = match.parent.parent
                            root_str = str(root).lower()
                            # Skip backup and reference directories
                            if "backup" in root_str or "reference" in root_str:
                                continue
                            filtered_matches.append(root)

                        if filtered_matches:
                            # Sort to prefer exact "autorun" name
                            filtered_matches.sort(key=lambda p: (
                                p.name != "autorun",  # Prefer exact name
                                "-" in p.name,  # Deprioritize names with dashes
                                str(p),  # Alphabetical tiebreaker
                            ))
                            return filtered_matches[0]
                except (PermissionError, OSError):
                    continue

    # Strategy 4: Check common development paths (last resort fallback)
    # Works for: any scenario where source repo exists in standard locations
    # Check AUTORUN_DEV_PATH first
    if "AUTORUN_DEV_PATH" in os.environ:
        dev_path = Path(os.environ["AUTORUN_DEV_PATH"])
        marker = dev_path / ".claude-plugin" / "marketplace.json"
        if marker.exists():
            return dev_path

    # Check exact common paths first (most specific)
    exact_paths = [
        Path.home() / ".claude" / "autorun" / "plugins" / "autorun",
        Path.home() / ".claude" / "autorun",
        Path.home() / "autorun" / "plugins" / "autorun",
        Path.home() / "autorun",
    ]

    for candidate in exact_paths:
        marker = candidate / ".claude-plugin" / "marketplace.json"
        if marker.exists():
            return candidate

    # Search common project locations more broadly (only if exact paths failed)
    search_patterns = [
        Path.home() / ".claude" / "*" / "plugins" / "autorun",
        Path.home() / "*" / "plugins" / "autorun",
    ]

    for pattern in search_patterns:
        try:
            # Sort to prefer non-backup, non-reference directories
            # Lower sort key = higher priority, so use negation for preferred attributes
            candidates = sorted(
                pattern.parent.glob(pattern.name),
                key=lambda p: (
                    # Prioritize exact "autorun" name (not "autorun-*")
                    p.parent.name != "autorun" if "autorun" in str(p.parent) else True,
                    # Deprioritize backup/reference (True sorts after False)
                    "backup" in str(p).lower(),
                    "reference" in str(p).lower(),
                    # Deprioritize names with dashes
                    "-" in p.parent.name,
                    # Alphabetical as final tiebreaker
                    str(p),
                )
            )
            for candidate in candidates:
                if not candidate.is_dir():
                    continue
                marker = candidate / ".claude-plugin" / "marketplace.json"
                if marker.exists():
                    return candidate
        except (PermissionError, OSError):
            continue

    # Strategy 5: Check known Gemini extension paths
    # Works for: gemini extensions install or gemini extensions link
    gemini_home = Path.home() / ".gemini" / "extensions"
    for ext_name in ["ar", "autorun-workspace", "autorun"]:
        ext_dir = gemini_home / ext_name
        if ext_dir.exists():
            # Could be at workspace root or in plugins/autorun/
            for candidate in [ext_dir, ext_dir / "plugins" / "autorun"]:
                marker = candidate / ".claude-plugin" / "marketplace.json"
                if marker.exists():
                    return candidate

    # Strategy 6: Check Claude plugin cache
    # Works for: claude plugin install (copies to cache)
    claude_cache = Path.home() / ".claude" / "plugins" / "cache" / "autorun"
    if claude_cache.exists():
        # Find the latest version directory (semver-aware sort)
        def _ver_key(p: Path) -> tuple:
            try:
                return tuple(int(x) for x in p.name.split("."))
            except (ValueError, TypeError):
                return (0,)
        version_dirs = sorted(claude_cache.glob("autorun/*"), key=_ver_key, reverse=True)
        for version_dir in version_dirs:
            marker = version_dir / ".claude-plugin" / "marketplace.json"
            if marker.exists():
                return version_dir

    # No marketplace root found - provide clear guidance
    raise FileNotFoundError(ErrorFormatter.marketplace_not_found())


def _read_plugin_version(plugin_dir: Path) -> str:
    """Read version from pyproject.toml, falling back to plugin.json.

    Args:
        plugin_dir: Path to plugin directory

    Returns:
        Version string from package metadata, or "0.12.0" as fallback
    """
    pyproject = plugin_dir / "pyproject.toml"
    if pyproject.exists():
        try:
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
            version = data.get("project", {}).get("version")
            if isinstance(version, str) and version:
                return version
        except (OSError, tomllib.TOMLDecodeError):
            pass

    manifest = plugin_dir / ".claude-plugin" / "plugin.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text())
            return data.get("version", "0.12.0")
        except (json.JSONDecodeError, OSError):
            pass
    return "0.12.0"


def _check_hook_conflicts() -> None:
    """Check for plugins with conflicting PreToolUse hooks.

    Warns if hookify or other plugins have PreToolUse hooks that might
    override autorun's command blocking.
    """
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        return

    try:
        settings = json.loads(settings_path.read_text())
        enabled = settings.get("enabledPlugins", {})

        # Check hookify specifically (known to conflict)
        if enabled.get("hookify@claude-code-plugins", False):
            print("\n⚠️  WARNING: hookify plugin is enabled")
            print("   hookify has PreToolUse hooks that may override autorun's command blocking.")
            print("   If rm/git-reset commands are not being blocked, disable hookify:")
            print("   Edit ~/.claude/settings.json and set:")
            print('   "hookify@claude-code-plugins": false')
            print("   Then restart Claude Code.")

        # Check for other plugins with PreToolUse hooks
        # Scan both hooks.json and claude-hooks.json since plugins may use either name
        cache_dir = Path.home() / ".claude" / "plugins" / "cache"
        if cache_dir.exists():
            conflicting = []
            for hooks_file in list(cache_dir.glob("*/*/*/hooks/hooks.json")) + \
                              list(cache_dir.glob("*/*/*/hooks/claude-hooks.json")):
                try:
                    hooks_data = json.loads(hooks_file.read_text())
                    if "PreToolUse" in hooks_data.get("hooks", {}):
                        plugin_name = hooks_file.parts[-4] + "@" + hooks_file.parts[-3]
                        if plugin_name != "autorun@autorun" and enabled.get(plugin_name, False):
                            conflicting.append(plugin_name)
                except Exception:
                    continue

            if conflicting:
                print(f"\n⚠️  Other plugins with PreToolUse hooks detected: {', '.join(conflicting)}")
                print("   These may interfere with autorun's command blocking.")

    except Exception as e:
        # Non-fatal - just log
        logger.debug(f"Could not check for hook conflicts: {e}")


# =============================================================================
# Validation Functions
# =============================================================================


def _parse_selection(selection: str) -> list[str]:
    """Parse and validate plugin selection string.

    Args:
        selection: "all" or comma-separated plugin names

    Returns:
        List of validated plugin names
    """
    # Get the source of truth for valid plugins
    valid_plugins = PluginName.all()
    try:
        root = find_marketplace_root()
        manifest = root / ".claude-plugin" / "marketplace.json"
        if manifest.exists():
            import json
            with open(manifest, encoding="utf-8") as f:
                data = json.load(f)
                valid_plugins = [p["name"] for p in data.get("plugins", [])]
    except Exception as e:
        logger.warning(f"Dynamic marketplace discovery failed: {e}")

    # Handle "all" case
    if not selection or selection == "all":
        return valid_plugins

    seen: set[str] = set()
    plugins = []
    for name in selection.split(","):
        name = name.strip()
        if not name or name in seen:
            continue
        if name not in valid_plugins:
            logger.warning(f"Unknown plugin: {name!r} (valid: {', '.join(valid_plugins)})")
            print(f"Unknown plugin: {name!r} (valid: {', '.join(valid_plugins)})")
            continue
        seen.add(name)
        plugins.append(name)

    return plugins


def _check_uv_env(plugin_dir: Path) -> CmdResult:
    """Check UV is available and project files exist.

    Args:
        plugin_dir: Path to plugin directory to validate

    Returns:
        CmdResult with ok=True if environment is valid
    """
    if not shutil.which("uv"):
        return CmdResult(
            False,
            ErrorFormatter.uv_not_found("pip install -e . && python -m autorun --install"),
        )

    if not (plugin_dir / "pyproject.toml").exists():
        return CmdResult(False, f"pyproject.toml not found in {plugin_dir}")

    if not (plugin_dir / "uv.lock").exists():
        return CmdResult(False, "uv.lock not found — run 'uv sync' first")

    if not (plugin_dir / ".venv").exists():
        return CmdResult(False, ".venv not found — run 'uv sync' first")

    return CmdResult(True, "UV environment OK")


def detect_available_clis() -> dict[str, bool]:
    """Detect which AI CLIs are available on the system.

    Returns a dict keyed by every Platform name in the PLATFORMS registry
    so adding a new platform requires no change here.
    """
    return {p.name: shutil.which(p.binary) is not None for p in PLATFORMS.values()}


def determine_target_clis(
    claude_only: bool,
    gemini_only: bool,
    available: dict[str, bool],
    codex_only: bool = False,
    antigravity_only: bool = False,
) -> list[str]:
    """Determine which CLIs to install for based on flags and availability.

    Args:
        claude_only: If True, include only Claude Code in install targets
        gemini_only: If True, include only Gemini CLI in install targets
        codex_only: If True, include only Codex CLI in install targets
        antigravity_only: If True, include only Antigravity CLI in install targets
        available: Dict of CLI availability from detect_available_clis()

    Returns:
        List of CLI names to install for (e.g., ["claude", "gemini", "codex"])

    Logic:
        - If no platform flags: install for all available CLIs
        - If any platform flags: install for the selected available CLIs
    """
    selected = []
    if claude_only:
        selected.append("claude")
    if gemini_only:
        selected.append("gemini")
    if antigravity_only:
        selected.append("antigravity")
    if codex_only:
        selected.append("codex")
    if selected:
        return [name for name in selected if available.get(name)]

    # Default: install for all available CLIs (insertion order from PLATFORMS)
    return [name for name, avail in available.items() if avail]


# =============================================================================
# Install Operations
# =============================================================================


def _sync_dependencies() -> CmdResult:
    """Install required dependencies using appropriate method for environment.

    Installs:
    - claude-code extra: Claude Code integration dependencies
    - bashlex: Required for pipe detection in command blocking

    For source/editable installs: Uses `uv sync` with pyproject.toml extras
    For UV tool installs: Uses `uv pip install` into tool environment
    For other package installs: Uses `pip install` with sys.executable

    Returns:
        CmdResult indicating success/failure
    """
    # Check if we're in a UV tool environment
    current_path = str(Path(__file__).resolve())
    is_uv_tool = ".local/share/uv/tools" in current_path or ".local/share/uv/python" in current_path

    if is_uv_tool:
        # UV tool install: install bashlex into the tool's environment
        # Use the same python executable that's running this code
        return run_cmd(
            ["uv", "pip", "install", "--python", sys.executable, "-q", "bashlex"],
            timeout=60,
        )

    # Source/editable install: use uv sync if pyproject.toml exists
    try:
        marketplace_root = find_marketplace_root()
        # If the root has a plugins/ directory, the plugin is in plugins/autorun
        # If the root IS the autorun directory (e.g. nested), use it directly
        if (marketplace_root / "plugins" / "autorun").exists():
            plugin_dir = marketplace_root / "plugins" / "autorun"
        else:
            plugin_dir = marketplace_root

        # Check if we have a pyproject.toml and can run uv sync
        if (plugin_dir / "pyproject.toml").exists():
            return run_cmd(
                ["uv", "sync", "--extra", "claude-code", "--extra", "bashlex"],
                timeout=120,
                cwd=plugin_dir,
            )
    except FileNotFoundError:
        pass

    # Fallback: use pip install with current python (works for any environment)
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "bashlex"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return CmdResult(result.returncode == 0, result.stdout or result.stderr)
    except Exception as e:
        return CmdResult(False, f"Failed to install bashlex: {e}")


def _install_pdf_deps() -> CmdResult:
    """Install pdf-extractor's core Python deps via uv pip.

    Returns:
        CmdResult indicating success/failure, or success if plugin not present
    """
    root = find_marketplace_root()
    
    # Robust plugin discovery
    potential_paths = [
        root / "plugins" / "pdf-extractor",
        root / "pdf-extractor",
        root.parent / "pdf-extractor"
    ]
    
    pdf_dir = None
    for p in potential_paths:
        if p.is_dir() and (p / ".claude-plugin").exists():
            pdf_dir = p
            break
            
    if not pdf_dir:
        # Fallback: if root itself is pdf-extractor
        if root.name == "pdf-extractor":
            pdf_dir = root
        else:
            return CmdResult(True, "pdf-extractor not present, skipping")

    return run_cmd(
        ["uv", "pip", "install", "--python", sys.executable, "-q",
         "pdfplumber", "pdfminer.six", "PyPDF2", "markitdown", "tqdm"],
        timeout=120,
    )


# --- BUG #24115 & #14449 WORKAROUND START --- DELETE WHEN BOTH BUGS ARE FIXED ---
# BUG #24115 (Claude Code): plugin loader reads hooks/ from the marketplace
#   source dir in addition to the installed cache. Any Gemini event name in
#   `plugins/autorun/hooks/` fails Claude's strict Zod with `invalid_key`.
#   https://github.com/anthropics/claude-code/issues/24115
# BUG #14449 (Gemini CLI): hooks.json path is hardcoded at
#   `<extension_root>/hooks/hooks.json`; the manifest's `hooks` field is ignored.
#   https://github.com/google-gemini/gemini-cli/issues/14449
#   https://github.com/google-gemini/gemini-cli/pull/14460
#
# Workaround (applies to both bugs; they co-motivate the same repo split):
#   - plugins/autorun/hooks/hooks.json holds Claude events ONLY (default path)
#   - Gemini assets staged under plugins/autorun/src/autorun/gemini_template/
#     (under src/ so Claude's source scanner does not reach them)
#   - The installer calls `gemini extensions install <template_dir>` and then
#     copies hook_entry.py into ~/.gemini/extensions/<name>/hooks/
#
# Configuration (either env var OR CONFIG entry):
#   AUTORUN_BUG_CLAUDE_CODE_MARKETPLACE_SOURCE_SCAN_BUG_24115_WORKAROUND_ENABLED
#   AUTORUN_BUG_GEMINI_CLI_HOOKS_JSON_HARDCODED_BUG_14449_WORKAROUND_ENABLED
#
# Both default to True. Values: true|1|auto (apply workaround) | false|0|never
# (skip workaround, likely produces broken install until bugs are fixed).
#
# Removal (when bugs are fixed):
#   1. Set both CONFIG keys to False and verify tests still pass.
#   2. Move plugins/autorun/src/autorun/gemini_template/ back to plugin root.
#   3. Delete this bracketed block (START→END), _gemini_template_dir,
#      _copy_hook_entry_to_gemini_ext, _migrate_legacy_layout.
#   4. Simplify _install_for_gemini to install from plugin_dir directly.


def _bug_24115_workaround_enabled() -> bool:
    """Check if the Claude Code marketplace-source scan workaround is enabled.

    BUG #24115 regression gate. Env var wins over CONFIG.
    """
    from .config import CONFIG
    _KEY = "AUTORUN_BUG_CLAUDE_CODE_MARKETPLACE_SOURCE_SCAN_BUG_24115_WORKAROUND_ENABLED"
    env = os.environ.get(_KEY, "").lower().strip()
    if env in ("false", "0", "never"):
        return False
    if env in ("true", "1", "auto", "always"):
        return True
    return bool(CONFIG.get(_KEY, True))


def _bug_14449_workaround_enabled() -> bool:
    """Check if the Gemini hardcoded-hooks-path workaround is enabled.

    BUG #14449 regression gate. Env var wins over CONFIG.
    """
    from .config import CONFIG
    _KEY = "AUTORUN_BUG_GEMINI_CLI_HOOKS_JSON_HARDCODED_BUG_14449_WORKAROUND_ENABLED"
    env = os.environ.get(_KEY, "").lower().strip()
    if env in ("false", "0", "never"):
        return False
    if env in ("true", "1", "auto", "always"):
        return True
    return bool(CONFIG.get(_KEY, True))


def _gemini_template_dir(plugin_dir: Path) -> Path:
    """Return the Gemini extension template directory for a Claude plugin.

    The template lives under src/autorun/gemini_template/ so Claude Code's
    bug #24115 marketplace-source scanner (which only walks hooks/) never
    touches it. When either workaround is disabled (both bugs presumed
    fixed), callers should skip the template path entirely and use the
    plugin root.

    References:
        - anthropics/claude-code#24115 (hooks dir scanned from source)
        - google-gemini/gemini-cli#14449 (hooks.json hardcoded at ext root)
    """
    return plugin_dir / "src" / "autorun" / "gemini_template"


def _copy_hook_entry_to_gemini_ext(plugin_dir: Path, ext_dir: Path) -> None:
    """Copy plugins/autorun/hooks/hook_entry.py into an installed Gemini
    extension dir so ${extensionPath}/hooks/hook_entry.py resolves at runtime.

    The Gemini hooks template references ${extensionPath}/hooks/hook_entry.py
    (the "hooks/" subdir matches how Claude references it, keeping one mental
    model for both CLIs). gemini extensions install copies everything under
    the source path, but since we install from the template dir (not plugin_dir),
    hook_entry.py is not picked up automatically — we copy it explicitly.

    BUG #14449 WORKAROUND STEP.
    """
    source = plugin_dir / "hooks" / "hook_entry.py"
    if not source.exists():
        logger.warning(f"hook_entry.py not found at {source}; Gemini hooks will fail")
        return
    target_dir = ext_dir / "hooks"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "hook_entry.py"
    shutil.copy2(source, target)
    logger.debug(f"Copied hook_entry.py → {target}")


def _copy_tree(src: Path, dst: Path) -> bool:
    """Mirror a plugin-owned resource tree into an installed location."""
    if not src.is_dir():
        return False
    if dst.exists() or dst.is_symlink():
        if dst.is_symlink() or dst.is_file():
            dst.unlink()
        else:
            shutil.rmtree(dst)
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.tmp", "*~", "*.bak"),
    )
    return True


def _count_skill_dirs(skills_dir: Path) -> int:
    """Count top-level Gemini/Claude skill directories in a copied skills tree."""
    if not skills_dir.is_dir():
        return 0
    return sum(
        1
        for child in skills_dir.iterdir()
        if child.is_dir() and (child / "SKILL.md").is_file()
    )


def _sync_gemini_extension_resources(
    plugin_dir: Path,
    ext_dir: Path,
    ext_name: str,
) -> tuple[int, int]:
    """Materialize shared autorun resources into an installed Gemini extension.

    Gemini extension install reads from the Gemini-specific template, while
    autorun's commands, skills, and executable hook entry are shared with other
    harnesses at the plugin root. Keep that source tree single-owned, and sync
    the shared resources into the installed extension after Gemini has created
    or confirmed the extension directory.

    Returns:
        (generated_command_count, synced_skill_count)
    """
    template_dir = _gemini_template_dir(plugin_dir)
    gemini_src = template_dir if (template_dir / "gemini-extension.json").is_file() else plugin_dir

    manifest_src = gemini_src / "gemini-extension.json"
    if manifest_src.is_file():
        shutil.copy2(manifest_src, ext_dir / "gemini-extension.json")

    _copy_tree(gemini_src / "hooks", ext_dir / "hooks")
    _copy_hook_entry_to_gemini_ext(plugin_dir, ext_dir)

    commands_generated = 0
    if _copy_tree(plugin_dir / "commands", ext_dir / "commands"):
        commands_generated = _generate_gemini_toml_commands(ext_dir, ext_name)

    skills_synced = 0
    if _copy_tree(plugin_dir / "skills", ext_dir / "skills"):
        skills_synced = _count_skill_dirs(ext_dir / "skills")

    return (commands_generated, skills_synced)


def _migrate_legacy_layout(plugin_dir: Path) -> None:
    """Fail-fast detector for stale working trees from before the template refactor.

    Only applies to plugins that have migrated to the template layout. A
    plugin without src/autorun/gemini_template/ is assumed to still use the
    legacy layout legitimately (e.g., pdf-extractor today).

    When BOTH a template dir AND a legacy manifest at plugin root exist, the
    checkout is inconsistent and the installer should abort with an
    actionable message rather than produce a broken dual install.

    BUG #24115 & #14449 WORKAROUND STEP: disable via either bug's env var.
    """
    if not (_bug_24115_workaround_enabled() and _bug_14449_workaround_enabled()):
        # Workaround disabled — assume legacy single-root layout is correct.
        return
    template = _gemini_template_dir(plugin_dir)
    legacy_manifest = plugin_dir / "gemini-extension.json"
    if template.is_dir() and legacy_manifest.exists():
        raise SystemExit(
            f"Legacy Gemini manifest detected at {legacy_manifest}. "
            "This plugin has already migrated to the gemini_template/ layout. "
            "Fix: git checkout -- plugins/autorun or delete the stale file, then "
            "rerun autorun --install."
        )
# --- BUG #24115 & #14449 WORKAROUND END ---


def _install_to_cache(plugin_name: str) -> bool:
    """Fallback: copy plugin to ~/.claude/plugins/cache/ and register in JSON.

    Used when `claude plugin install` fails (CI, air-gapped, broken plugin system).

    Args:
        plugin_name: Name of plugin to install

    Returns:
        True if cache install succeeded, False otherwise
    """
    root = find_marketplace_root()
    plugin_dir = _resolve_plugin_dir(root, plugin_name)
    if not plugin_dir or not (plugin_dir / ".claude-plugin").exists():
        return False

    # Read version from plugin.json
    version = _read_plugin_version(plugin_dir)

    cache_dir = Path.home() / ".claude" / "plugins" / "cache" / MARKETPLACE / plugin_name / version
    cache_dir.parent.mkdir(parents=True, exist_ok=True)

    # Backup existing cache
    if cache_dir.exists():
        backup = cache_dir.with_suffix(".backup")
        if backup.exists():
            shutil.rmtree(backup)
        shutil.move(str(cache_dir), str(backup))

    # Copy plugin to cache (ignore build artifacts)
    try:
        shutil.copytree(
            plugin_dir,
            cache_dir,
            ignore=shutil.ignore_patterns('.git', '__pycache__', '*.pyc', '.coverage', '.venv', '.pytest_cache')
        )
    except OSError as e:
        logger.error(f"Failed to copy {plugin_name} to cache: {e}")
        return False

    # No cross-CLI cleanup required after the programmatic split:
    # plugins/autorun/hooks/hooks.json holds Claude-only events, and Gemini
    # assets live under src/autorun/gemini_template/ which is out of Claude's
    # marketplace-source scan path (bug #24115).
    _substitute_paths(cache_dir)

    # Register in installed_plugins.json
    return _register_in_json(cache_dir, plugin_name, version)


def _register_in_json(install_path: Path, plugin_name: str, version: str) -> bool:
    """Register plugin in installed_plugins.json for Claude Code discovery.

    Args:
        install_path: Path where plugin is installed
        plugin_name: Name of the plugin
        version: Plugin version string

    Returns:
        True if registration succeeded
    """
    plugins_dir = Path.home() / ".claude" / "plugins"
    json_file = plugins_dir / "installed_plugins.json"

    data = {"version": 2, "plugins": {}}
    if json_file.exists():
        try:
            data = json.loads(json_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    ts = datetime.now(timezone.utc).isoformat()

    key = f"{plugin_name}@{MARKETPLACE}"
    data["plugins"][key] = [{
        "scope": "user",
        "installPath": str(install_path),
        "version": version,
        "installedAt": ts,
        "lastUpdated": ts,
        "gitCommitSha": "manual-install"
    }]

    try:
        json_file.write_text(json.dumps(data, indent=2))
        return True
    except OSError as e:
        logger.error(f"Failed to update installed_plugins.json: {e}")
        return False


def _substitute_paths(plugin_dir: Path) -> None:
    """Replace ${CLAUDE_PLUGIN_ROOT} placeholder with actual absolute path.

    Only substitutes ${CLAUDE_PLUGIN_ROOT} (Claude Code).
    ${extensionPath} is resolved at runtime by Gemini CLI and must NOT be
    pre-resolved here (see https://geminicli.com/docs/extensions/reference/).
    Recursively processes .json and .md files in core directories.

    Args:
        plugin_dir: Path to plugin directory
    """
    abs_path = str(plugin_dir.resolve())
    
    # 1. Manifests and Hooks (Fixed paths).
    # hooks/hooks.json now holds Claude Code events exclusively (post-split).
    # Gemini assets live under src/autorun/gemini_template/ and are materialized
    # at install time by _install_for_gemini — never substituted here, because
    # Gemini resolves ${extensionPath} at runtime.
    target_files = [
        ".claude-plugin/plugin.json",
        "hooks/hooks.json",
    ]
    
    # 2. Commands and Skills (Recursive discovery)
    for folder in ["commands", "skills"]:
        folder_path = plugin_dir / folder
        if folder_path.is_dir():
            for md_file in folder_path.rglob("*.md"):
                target_files.append(str(md_file.relative_to(plugin_dir)))

    for rel_path in target_files:
        fp = plugin_dir / rel_path
        if not fp.exists() or fp.is_symlink():
            continue
            
        try:
            content = fp.read_text(encoding="utf-8")
            original_content = content

            # Substitute Claude Code variable only.
            # ${extensionPath} is resolved at runtime by Gemini CLI
            # (see https://geminicli.com/docs/extensions/reference/)
            # and must NOT be pre-resolved by the installer.
            if "${CLAUDE_PLUGIN_ROOT}" in content:
                content = content.replace("${CLAUDE_PLUGIN_ROOT}", abs_path)

            if content != original_content:
                fp.write_text(content, encoding="utf-8")
                logger.debug(f"Substituted paths in {rel_path}")
        except OSError as e:
            logger.warning(f"Failed to substitute paths in {rel_path}: {e}")


def _generate_gemini_toml_commands(ext_dir: Path, ext_name: str) -> int:
    """Convert .md command files to .toml format for Gemini CLI.

    Gemini CLI reads commands from commands/<ext_name>/<cmd>.toml files (TOML format),
    while Claude Code reads commands from commands/<cmd>.md files (Markdown format).
    This function generates TOML equivalents so both CLIs work from the same source.

    Directory structure: commands/ar/status.toml -> /ar:status
    TOML format: description + prompt fields (see conductor extension for reference)

    References:
        - Extension commands (TOML format): https://geminicli.com/docs/extensions/reference/
        - Writing extensions: https://geminicli.com/docs/extensions/writing-extensions/
        - Hook support in extensions: https://github.com/google-gemini/gemini-cli/issues/14449
        - Conductor extension (reference implementation): https://github.com/gemini-cli-extensions/conductor

    Args:
        ext_dir: Installed extension directory (e.g., ~/.gemini/extensions/ar/)
        ext_name: Extension name (e.g., "ar") used for command namespace

    Returns:
        Number of TOML files generated
    """
    commands_dir = ext_dir / "commands"
    if not commands_dir.is_dir():
        return 0

    # Create namespaced directory: commands/ar/
    toml_dir = commands_dir / ext_name
    toml_dir.mkdir(exist_ok=True)

    count = 0
    for md_file in sorted(commands_dir.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")

            # Parse YAML frontmatter (between --- delimiters)
            description = ""
            body = content
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    # Extract description from frontmatter
                    for line in parts[1].strip().splitlines():
                        if line.startswith("description:"):
                            description = line.split(":", 1)[1].strip().strip("'\"")
                            break
                    body = parts[2].strip()

            # Convert $ARGUMENTS to {{args}} (Gemini convention)
            body = body.replace("$ARGUMENTS", "{{args}}")

            # Support tool mapping for Gemini CLI (Interoperability Superset)
            # Reuses CLI_TOOL_NAMES from core.py to ensure suggestions in .toml
            # use Gemini tool names (e.g. read_file) instead of Claude names (e.g. Read)
            try:
                from .core import CLI_TOOL_NAMES
                gemini_tools = CLI_TOOL_NAMES.get("gemini", {})
                for claude_name, gemini_name in gemini_tools.items():
                    # Match {tool_name} placeholder in prompt
                    placeholder = "{" + claude_name + "}"
                    if placeholder in body:
                        body = body.replace(placeholder, "{" + gemini_name + "}")
            except ImportError:
                # Fallback if core.py cannot be imported during bootstrap
                pass

            # Escape backslashes and triple quotes in body for TOML multi-line strings
            # Gemini CLI parser fails on unescaped backslashes in regex (e.g. \()
            safe_body = body.replace('\\', '\\\\').replace('"""', '\\"\\"\\"')

            # Write TOML file
            toml_content = f'description = "{description}"\n'
            toml_content += f'prompt = """\n{safe_body}\n"""\n'

            toml_path = toml_dir / f"{md_file.stem}.toml"
            toml_path.write_text(toml_content, encoding="utf-8")
            count += 1
        except Exception as e:
            logger.warning(f"Failed to convert {md_file.name} to TOML: {e}")

    return count


def _install_for_gemini(
    marketplace_root: Path,
    plugins: list[str],
    force: bool = False,
) -> tuple[bool, str]:
    """Install selected plugins for Gemini CLI.

    Note: Gemini treats each plugin as a separate extension, not a workspace.

    Args:
        marketplace_root: Path to marketplace root directory (plugin directory)
        plugins: List of plugin names to install (e.g., ["autorun", "pdf-extractor"])
        force: Force reinstall even if same version

    Returns:
        Tuple of (success: bool, message: str)
    """
    if not shutil.which("gemini"):
        msg = "gemini CLI not found. Install from: npm install -g @google-labs/gemini-cli"
        print(msg)
        return (False, msg)

    gemini_dir = Path.home() / ".gemini"
    if not gemini_dir.exists():
        print("~/.gemini/ directory not found. Run 'gemini' once to initialize.")
        return (False, "~/.gemini/ not found")

    # Find all plugins in the marketplace
    # Strategy 1: marketplace_root contains plugins/ (workspace root)
    # Strategy 2: marketplace_root/.. contains other plugins (plugin root)
    potential_plugin_dirs = [
        marketplace_root / "plugins",
        marketplace_root.parent
    ]

    # A plugin dir now advertises Gemini support via either (a) legacy
    # gemini-extension.json at plugin root (pdf-extractor still uses this)
    # or (b) a gemini_template/ with gemini-extension.json inside it
    # (autorun post-refactor).
    def _gemini_source(plugin_dir: Path) -> Path | None:
        """Return the directory to hand to `gemini extensions install`.

        Prefers the programmatic template path. Falls back to legacy layout.
        Returns None if the plugin does not ship a Gemini manifest.
        """
        template = _gemini_template_dir(plugin_dir)
        if template.is_dir() and (template / "gemini-extension.json").exists():
            return template
        if (plugin_dir / "gemini-extension.json").exists():
            return plugin_dir
        return None

    # Build name→source map from marketplace.json (plugin name may differ from dir name,
    # e.g. name="ar" maps to source="./plugins/autorun")
    marketplace_source_map: dict[str, Path] = {}
    marketplace_json = marketplace_root / ".claude-plugin" / "marketplace.json"
    if marketplace_json.exists():
        try:
            import json as _json
            with open(marketplace_json, encoding="utf-8") as _f:
                _mdata = _json.load(_f)
            for _entry in _mdata.get("plugins", []):
                _entry_name = _entry.get("name", "")
                _source = _entry.get("source", "")
                if _entry_name and _source:
                    _resolved = (marketplace_root / _source).resolve()
                    if _resolved.is_dir() and _gemini_source(_resolved) is not None:
                        marketplace_source_map[_entry_name] = _resolved
        except Exception:
            pass

    # plugins_to_install holds (plugin_dir, gemini_source_dir) pairs so the
    # installer can copy hook_entry.py from plugin_dir/hooks/ while handing
    # gemini_source_dir to `gemini extensions install`.
    plugins_to_install: list[tuple[Path, Path]] = []
    for name in plugins:
        candidate: Path | None = None

        # Strategy 0: resolve via marketplace.json source field
        if name in marketplace_source_map:
            candidate = marketplace_source_map[name]

        if candidate is None:
            for p_dir in potential_plugin_dirs:
                target = p_dir / name
                if target.is_dir() and _gemini_source(target) is not None:
                    candidate = target
                    break

        if candidate is None:
            # Check if marketplace_root itself is the plugin
            if marketplace_root.name == name and _gemini_source(marketplace_root) is not None:
                candidate = marketplace_root

        if candidate is not None:
            src = _gemini_source(candidate)
            if src is not None:
                plugins_to_install.append((candidate, src))

    if not plugins_to_install:
        return (False, f"No plugins found matching selection: {', '.join(plugins)} in {marketplace_root}")

    print()
    print(f"Installing {len(plugins_to_install)} plugin(s) for Gemini CLI...")

    success_count = 0
    failed_plugins = []

    for plugin_dir, gemini_src in plugins_to_install:
        plugin_name = plugin_dir.name

        # Detect stale pre-refactor layout before doing anything destructive.
        _migrate_legacy_layout(plugin_dir)

        # Read gemini-extension.json to get the extension name
        try:
            import json
            with open(gemini_src / "gemini-extension.json", encoding="utf-8") as f:
                ext_config = json.load(f)
                ext_name = ext_config.get("name", plugin_name)
        except Exception:
            ext_name = plugin_name

        print(f"   Installing {plugin_name} (name: {ext_name})...")

        # gemini extensions install expects a persistent source path. Gemini
        # 0.28.2 creates absolute symlinks into that path, so the source must
        # not be a TemporaryDirectory. For autorun, gemini_src points at
        # plugins/autorun/src/autorun/gemini_template — a persistent repo path.
        # hook_entry.py lives outside the template (shared with Claude) and is
        # copied in after install completes via _copy_hook_entry_to_gemini_ext.

        if force:
            run_cmd(["gemini", "extensions", "uninstall", ext_name])

        # Install directly from the persistent source path
        result = run_cmd(["gemini", "extensions", "install", str(gemini_src), "--consent"])

        if result.ok or result.has_text("already installed"):
            print(f"   ✓ {ext_name} installed successfully")
            installed_dir = gemini_dir / "extensions" / ext_name
            if installed_dir.is_dir():
                n, skills_count = _sync_gemini_extension_resources(
                    plugin_dir,
                    installed_dir,
                    ext_name,
                )
                if n > 0:
                    print(f"   ✓ Generated {n} TOML command files for /{ext_name}:* commands")
                if skills_count > 0:
                    print(f"   ✓ Synced {skills_count} skill(s) for Gemini")
            success_count += 1
        else:
            print(f"   ✗ {ext_name} installation failed:")
            print(f"     Status: {result.ok}")
            print(f"     Output: {result.output}")
            failed_plugins.append(ext_name)

    print()
    if success_count == len(plugins_to_install):
        print(f"✓ All {success_count} plugin(s) installed successfully")
        return (True, "success")
    elif success_count > 0:
        msg = f"Partial success: {success_count}/{len(plugins_to_install)} plugins installed. Failed: {', '.join(failed_plugins)}"
        print(f"⚠ {msg}")
        return (True, msg)
    else:
        msg = f"All plugins failed to install: {', '.join(failed_plugins)}"
        print(f"✗ {msg}")
        return (False, msg)


def _resolve_plugin_dir(marketplace_root: Path, name: str) -> Path | None:
    """Find a plugin directory by name under the marketplace root.

    Tries (in order):
        1. marketplace_root/.claude-plugin/marketplace.json → source field
           (covers cases where plugin name != directory name, e.g. name="ar"
            registered with source="./plugins/autorun")
        2. marketplace_root/plugins/<name>            (direct directory match)
        3. marketplace_root/<name>                    (flat layout)
        4. marketplace_root.parent/<name>             (legacy sibling layout)
        5. marketplace_root itself (basename matches)
    """
    manifest = marketplace_root / ".claude-plugin" / "marketplace.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            for entry in data.get("plugins", []):
                if entry.get("name") != name:
                    continue
                source = entry.get("source", "")
                if not source:
                    continue
                resolved = (marketplace_root / source).resolve()
                if resolved.is_dir():
                    return resolved
        except Exception:
            pass

    for candidate in (
        marketplace_root / "plugins" / name,
        marketplace_root / name,
        marketplace_root.parent / name,
    ):
        if candidate.is_dir():
            return candidate
    if marketplace_root.name == name and marketplace_root.is_dir():
        return marketplace_root
    return None


def _substitute_claude_cache_paths(marketplace_root: Path, plugin_name: str) -> bool:
    """Substitute ${CLAUDE_PLUGIN_ROOT} in Claude's cached plugin copy.

    Claude Code does not reliably expand this variable for local marketplace
    plugins. The logical plugin name may also differ from the source directory
    name, so cache repair must resolve through marketplace.json instead of
    guessing plugins/<name>.
    """
    cache_root = Path.home() / ".claude" / "plugins" / "cache" / MARKETPLACE / plugin_name
    cache_dirs: list[Path] = []

    plugin_dir = _resolve_plugin_dir(marketplace_root, plugin_name)
    if plugin_dir is not None:
        version = _read_plugin_version(plugin_dir)
        versioned_cache = cache_root / version
        if versioned_cache.is_dir():
            cache_dirs.append(versioned_cache)

    if cache_root.is_dir():
        cache_dirs.extend(sorted(p for p in cache_root.iterdir() if p.is_dir()))

    seen: set[Path] = set()
    repaired = False
    for cache_dir in cache_dirs:
        resolved = cache_dir.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        _substitute_paths(cache_dir)
        repaired = True
    return repaired


def _autorun_plugin_dir(marketplace_root: Path, plugins: list[str]) -> Path | None:
    """Locate the autorun plugin directory regardless of which name(s) the
    plugins list uses. Tries the requested names first, then falls back to
    well-known aliases ("autorun", "ar").
    """
    for candidate in list(plugins) + ["autorun", "ar"]:
        plugin_dir = _resolve_plugin_dir(marketplace_root, candidate)
        if plugin_dir and plugin_dir.name == "autorun":
            return plugin_dir
    return None


_CODEX_AUTORUN_COMMAND_MARK = "/hooks/hook_entry.py --cli codex"
_CODEX_PLUGIN_NAME = "autorun"
_CODEX_PLUGIN_SOURCE_PATH = "./plugins/autorun"
_CODEX_PLUGIN_OWNED_MARKER = ".autorun-owned"
_CODEX_PERSONAL_MARKETPLACE_NAME = "personal"


@dataclass(frozen=True, slots=True)
class CodexPluginMarketplaceInstall:
    """Result from publishing autorun's Codex plugin package locally."""

    source_ready: bool
    marketplace_ready: bool
    skipped_user_owned_source: bool = False
    skipped_conflicting_marketplace: bool = False
    reason: str = ""


def _build_codex_hook_block(plugin_dir: Path) -> dict:
    """Build the autorun hook block for ~/.codex/hooks.json.

    Path resolution: user-level Codex hooks do NOT receive ${PLUGIN_ROOT}
    or ${CLAUDE_PLUGIN_ROOT} — those are set only for plugin-bundled
    hooks per https://developers.openai.com/codex/hooks. We resolve the
    plugin directory to an absolute path at install time. Re-running the
    installer updates the absolute path if the repo moved.

    Schema: every event uses the canonical {matcher (omitted = wildcard),
    hooks: [{type, command, timeout}]} wrapper used by Claude Code's
    hooks.json. Bare {type, command} entries are silently dropped by
    Codex's strict schema (observed: /hooks TUI showed 0/0 installed
    for the affected events).

    Event coverage: PreToolUse, PostToolUse, UserPromptSubmit,
    SessionStart, Stop, SubagentStop. SubagentStart, PermissionRequest,
    PreCompact, PostCompact are intentionally omitted — autorun has no
    handlers for them today and registering empty subprocess calls would
    raise daemon load without benefit.
    """
    plugin_abs = str(plugin_dir.resolve())
    hook_entry = f"{plugin_abs}/hooks/hook_entry.py"
    command = (
        f"uv run --quiet --project {plugin_abs} python {hook_entry} --cli codex"
    )

    entry = {
        "hooks": [{"type": "command", "command": command, "timeout": 10}]
    }

    return {
        "PreToolUse": [entry],
        "PostToolUse": [entry],
        "UserPromptSubmit": [entry],
        "SessionStart": [entry],
        "Stop": [entry],
        "SubagentStop": [entry],
    }


def _merge_codex_hooks(existing: dict, autorun_block: dict) -> dict:
    """Merge autorun hook entries into an existing ~/.codex/hooks.json structure.

    Preserves the user's custom hooks for any event. For events where autorun
    contributes, the autorun-owned entries (marked with `_autorun_owned: True`)
    are replaced — non-autorun entries the user added are kept.
    """
    merged = dict(existing) if isinstance(existing, dict) else {}
    hooks = dict(merged.get("hooks", {})) if isinstance(merged.get("hooks"), dict) else {}

    def _is_autorun_command(h):
        """Match autorun's canonical Codex hook command string.

        Identifies both the new format (absolute path + --cli codex) and
        the historical `${PLUGIN_ROOT}/hooks/hook_entry.py --cli codex`
        marker so re-installs from older autorun versions still cleanup.
        """
        return (
            isinstance(h, dict)
            and h.get("type") == "command"
            and _CODEX_AUTORUN_COMMAND_MARK in (h.get("command") or "")
        )

    def _is_autorun_entry(e):
        if not isinstance(e, dict):
            return False
        if e.get("_autorun_owned"):
            return True                                    # legacy marker
        if _is_autorun_command(e):
            return True                                    # legacy bare entry
        inner = e.get("hooks")
        return isinstance(inner, list) and any(_is_autorun_command(h) for h in inner)

    def _strip_autorun(entries):
        if not isinstance(entries, list):
            return entries
        stripped = []
        for e in entries:
            if _is_autorun_entry(e):
                continue
            # Drop legacy _autorun_owned inner entries if any survive
            if isinstance(e, dict) and isinstance(e.get("hooks"), list):
                keep_inner = [
                    h for h in e["hooks"]
                    if not (isinstance(h, dict) and (h.get("_autorun_owned") or _is_autorun_command(h)))
                ]
                if not keep_inner:
                    continue
                e = dict(e)
                e["hooks"] = keep_inner
            stripped.append(e)
        return stripped

    for event, autorun_entries in autorun_block.items():
        existing_entries = _strip_autorun(hooks.get(event, []))
        hooks[event] = list(existing_entries) + list(autorun_entries)

    # Drop any events autorun no longer manages but previously installed
    # (e.g. SessionEnd, which Codex never accepted) so re-install fully
    # cleans up legacy entries.
    for event in list(hooks.keys()):
        if event in autorun_block:
            continue
        cleaned = _strip_autorun(hooks[event])
        if cleaned:
            hooks[event] = cleaned
        else:
            del hooks[event]

    merged["hooks"] = hooks
    return merged


def _install_for_codex(
    marketplace_root: Path,
    plugins: list[str],
    force: bool = False,
) -> tuple[bool, str]:
    """Install autorun hooks at ~/.codex/hooks.json (user-level, always active).

    Codex's user-level hooks file is always active and bypasses the
    `features.plugin_hooks=true` opt-in required for plugin-bundled hooks.
    This mirrors autorun's "install once globally" model for Claude
    (via the plugin manifest) and Gemini (via the extension manifest).

    Args:
        marketplace_root: Path to marketplace root directory (plugin directory)
        plugins: List of plugin names to install (only "autorun" is supported
                 for Codex hook integration; others are no-ops)
        force: Reserved for parity with other installers; merge logic is
               idempotent so force has no effect here today.

    Returns:
        Tuple of (success: bool, message: str)
    """
    plugin_dir = _autorun_plugin_dir(marketplace_root, plugins)
    if plugin_dir is None:
        return (False, f"autorun plugin not found under {marketplace_root}")

    codex_dir = Path.home() / ".codex"
    codex_dir.mkdir(exist_ok=True)
    hooks_path = codex_dir / "hooks.json"

    existing = {}
    if hooks_path.exists():
        try:
            existing = json.loads(hooks_path.read_text(encoding="utf-8"))
        except Exception as exc:  # malformed file — surface but don't clobber
            return (False, f"~/.codex/hooks.json is not valid JSON: {exc}")

    autorun_block = _build_codex_hook_block(plugin_dir)
    merged = _merge_codex_hooks(existing, autorun_block)
    hooks_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")

    agents_written = _install_codex_agents_md(plugin_dir, codex_dir)
    skills_installed, skills_skipped = _install_codex_skills(plugin_dir)
    plugin_marketplace = _install_codex_plugin_marketplace(plugin_dir)

    print()
    print("✓ Codex hooks installed at ~/.codex/hooks.json")
    if agents_written:
        print("✓ Advisory safety guidance written to ~/.codex/AGENTS.md")
    if skills_installed:
        print(f"✓ Installed {skills_installed} autorun skill(s) at ~/.agents/skills/")
    if skills_skipped:
        print(
            f"  ({skills_skipped} user-authored skill(s) with matching names "
            f"left untouched)"
        )
    if plugin_marketplace.marketplace_ready:
        print("✓ Codex plugin marketplace entry written to ~/.agents/plugins/marketplace.json")
    elif plugin_marketplace.reason:
        print(f"  Codex plugin marketplace skipped: {plugin_marketplace.reason}")
    print("  Next: run '/hooks' inside Codex CLI to trust the new hook hashes.")
    print("        (Codex silently skips hooks until they are approved.)")
    return (True, "success")


_CODEX_SKILL_OWNED_MARKER = ".autorun-owned"


def _install_codex_skills(plugin_dir: Path) -> tuple[int, int]:
    """Copy autorun skills into ~/.agents/skills/ (Codex global skills dir).

    Per https://developers.openai.com/codex/skills the user-level skills
    directory Codex scans is `$HOME/.agents/skills/` (NOT `~/.codex/skills/`,
    which is unused). We copy each plugin skill dir into ~/.agents/skills/<name>/
    and drop a `.autorun-owned` marker file so subsequent re-installs can
    replace ours in place without ever clobbering a user-authored skill
    that happens to share the same kebab-case name.

    Returns:
        (installed_count, skipped_count) — skipped counts user-authored
        skills (no marker) that we deliberately left intact.
    """
    src_root = plugin_dir / "skills"
    if not src_root.is_dir():
        return (0, 0)

    dst_root = Path.home() / ".agents" / "skills"
    dst_root.mkdir(parents=True, exist_ok=True)

    installed = 0
    skipped = 0
    for skill_src in sorted(src_root.iterdir()):
        if not skill_src.is_dir():
            continue
        if not (skill_src / "SKILL.md").is_file():
            continue

        skill_dst = dst_root / skill_src.name
        if skill_dst.exists() and not (skill_dst / _CODEX_SKILL_OWNED_MARKER).is_file():
            # User-authored skill with the same name — never touch it.
            skipped += 1
            continue

        if skill_dst.exists():
            shutil.rmtree(skill_dst)
        shutil.copytree(skill_src, skill_dst)
        (skill_dst / _CODEX_SKILL_OWNED_MARKER).write_text(
            "Autorun-owned. Safe to delete to un-claim this directory; the\n"
            "next autorun install will then leave it alone as user-authored.\n",
            encoding="utf-8",
        )
        installed += 1
    return (installed, skipped)


def _codex_personal_marketplace_path() -> Path:
    """Return Codex's implicit home marketplace manifest path."""
    return Path.home() / ".agents" / "plugins" / "marketplace.json"


def _codex_plugin_source_dir() -> Path:
    """Return the local plugin source path referenced by the home marketplace."""
    return Path.home() / "plugins" / _CODEX_PLUGIN_NAME


def _codex_plugin_marketplace_entry() -> dict:
    """Build the marketplace entry Codex expects under ~/.agents/plugins/."""
    return {
        "name": _CODEX_PLUGIN_NAME,
        "source": {"source": "local", "path": _CODEX_PLUGIN_SOURCE_PATH},
        "policy": {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        },
        "category": "Productivity",
    }


def _same_resolved_path(left: Path, right: Path) -> bool:
    """Compare paths after resolving symlinks; broken paths simply mismatch."""
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


def _copy_codex_plugin_source(plugin_dir: Path, target: Path) -> None:
    """Copy the plugin source with real files for Codex's plugin cache.

    Codex's local plugin cache copier copies regular files and directories but
    ignores symbolic links. Autorun keeps a few cross-harness skill entrypoints
    as `SKILL.md` symlinks, so the personal Codex plugin source must
    dereference those links before `codex plugin add autorun@personal` copies
    the bundle into `~/.codex/plugins/cache/...`.
    """
    shutil.copytree(
        plugin_dir,
        target,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            "__pycache__",
            "*.pyc",
            "*.pyo",
            ".coverage",
            "htmlcov",
        ),
    )
    (target / _CODEX_PLUGIN_OWNED_MARKER).write_text(
        "Autorun-owned Codex plugin source copy. Safe to delete; rerun "
        "`autorun --install --codex` to recreate it.\n",
        encoding="utf-8",
    )


def _remove_owned_codex_plugin_source(target: Path) -> None:
    """Remove only a plugin source path autorun previously owned."""
    if target.is_symlink() or target.is_file():
        target.unlink()
    else:
        shutil.rmtree(target)


def _ensure_codex_plugin_source(plugin_dir: Path) -> tuple[bool, str]:
    """Materialize ~/plugins/autorun for Codex's implicit home marketplace.

    Codex resolves `./plugins/autorun` relative to the home marketplace root.
    The source is an autorun-owned copy instead of a symlink because Codex's
    plugin cache copy path ignores link-backed `SKILL.md` files. Re-installs
    replace only an autorun-owned path and never touch a user-authored plugin
    directory with the same name.
    """
    manifest = plugin_dir / ".codex-plugin" / "plugin.json"
    if not manifest.is_file():
        return (False, f"missing Codex plugin manifest at {manifest}")

    target = _codex_plugin_source_dir()
    if target.is_symlink():
        if _same_resolved_path(target, plugin_dir):
            _remove_owned_codex_plugin_source(target)
        else:
            return (False, f"user-owned symlink exists at {target}")

    if target.exists():
        if not (target / _CODEX_PLUGIN_OWNED_MARKER).is_file():
            return (False, f"user-owned directory exists at {target}")
        _remove_owned_codex_plugin_source(target)

    target.parent.mkdir(parents=True, exist_ok=True)
    _copy_codex_plugin_source(plugin_dir, target)
    return (True, "copy")


def _load_codex_personal_marketplace(path: Path) -> tuple[dict, str | None]:
    """Load or initialize ~/.agents/plugins/marketplace.json."""
    if not path.is_file():
        return (
            {
                "name": _CODEX_PERSONAL_MARKETPLACE_NAME,
                "interface": {"displayName": "Personal"},
                "plugins": [],
            },
            None,
        )

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return ({}, f"{path} is not valid JSON: {exc}")

    if not isinstance(data, dict):
        return ({}, f"{path} must contain a JSON object")

    data.setdefault("name", _CODEX_PERSONAL_MARKETPLACE_NAME)
    interface = data.setdefault("interface", {})
    if isinstance(interface, dict):
        interface.setdefault("displayName", "Personal")
    else:
        data["interface"] = {"displayName": "Personal"}
    plugins = data.setdefault("plugins", [])
    if not isinstance(plugins, list):
        return ({}, f"{path} plugins field must be a list")
    return (data, None)


def _upsert_codex_plugin_entry(marketplace: dict) -> tuple[dict, str | None]:
    """Insert autorun's Codex plugin entry without overwriting conflicts."""
    plugins = marketplace.get("plugins", [])
    entry = _codex_plugin_marketplace_entry()
    next_plugins: list[dict] = []
    inserted = False

    for plugin in plugins:
        if not isinstance(plugin, dict) or plugin.get("name") != _CODEX_PLUGIN_NAME:
            next_plugins.append(plugin)
            continue

        source = plugin.get("source")
        if source != entry["source"]:
            return (
                marketplace,
                f"existing marketplace entry {_CODEX_PLUGIN_NAME!r} uses {source!r}",
            )
        if not inserted:
            next_plugins.append(entry)
            inserted = True

    if not inserted:
        next_plugins.append(entry)

    updated = dict(marketplace)
    updated["plugins"] = next_plugins
    return (updated, None)


def _install_codex_plugin_marketplace(plugin_dir: Path) -> CodexPluginMarketplaceInstall:
    """Publish autorun's skills as a Codex plugin in the home marketplace.

    User-level hooks stay in ~/.codex/hooks.json so enforcement has a single
    active source. The plugin package exists for Codex-native skill discovery,
    install surfaces, and future MCP/app packaging.
    """
    source_ready, source_message = _ensure_codex_plugin_source(plugin_dir)
    if not source_ready:
        return CodexPluginMarketplaceInstall(
            source_ready=False,
            marketplace_ready=False,
            skipped_user_owned_source="user-owned" in source_message,
            reason=source_message,
        )

    path = _codex_personal_marketplace_path()
    marketplace, error = _load_codex_personal_marketplace(path)
    if error:
        return CodexPluginMarketplaceInstall(
            source_ready=True,
            marketplace_ready=False,
            reason=error,
        )

    marketplace, conflict = _upsert_codex_plugin_entry(marketplace)
    if conflict:
        return CodexPluginMarketplaceInstall(
            source_ready=True,
            marketplace_ready=False,
            skipped_conflicting_marketplace=True,
            reason=conflict,
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(marketplace, indent=2) + "\n", encoding="utf-8")
    return CodexPluginMarketplaceInstall(
        source_ready=True,
        marketplace_ready=True,
        reason=source_message,
    )


def _install_codex_plugin_with_cli(force: bool = False) -> CmdResult:
    """Install the local autorun Codex plugin after marketplace publication."""
    if not shutil.which("codex"):
        return CmdResult(False, "codex CLI not found")

    plugin_id = f"{_CODEX_PLUGIN_NAME}@{_CODEX_PERSONAL_MARKETPLACE_NAME}"
    if force:
        remove = run_cmd(["codex", "plugin", "remove", plugin_id], timeout=120)
        if not (remove.ok or remove.has_text("not installed") or remove.has_text("not found")):
            return remove

    result = run_cmd(
        ["codex", "plugin", "add", plugin_id],
        timeout=120,
    )
    if result.ok or result.has_text("already"):
        return CmdResult(True, result.output)
    return result


def _codex_plugin_marketplace_status() -> tuple[bool, str]:
    """Return whether the Codex home marketplace exposes autorun."""
    path = _codex_personal_marketplace_path()
    if not path.is_file():
        return (False, "✗ not installed")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return (False, f"✗ unreadable ({exc})")

    plugins = data.get("plugins", [])
    if not isinstance(plugins, list):
        return (False, "✗ incomplete (plugins field is not a list)")

    entry = next(
        (
            plugin for plugin in plugins
            if isinstance(plugin, dict) and plugin.get("name") == _CODEX_PLUGIN_NAME
        ),
        None,
    )
    if entry is None:
        return (False, "✗ missing autorun entry")

    if entry.get("source") != _codex_plugin_marketplace_entry()["source"]:
        return (False, "✗ autorun entry points at unexpected source")

    source_dir = _codex_plugin_source_dir()
    if not (source_dir / ".codex-plugin" / "plugin.json").is_file():
        return (False, "✗ plugin source missing .codex-plugin/plugin.json")

    cache_root = (
        Path.home()
        / ".codex"
        / "plugins"
        / "cache"
        / _CODEX_PERSONAL_MARKETPLACE_NAME
        / _CODEX_PLUGIN_NAME
    )
    installed = (
        cache_root.is_dir()
        and any(
            (child / ".codex-plugin" / "plugin.json").is_file()
            for child in cache_root.iterdir()
            if child.is_dir()
        )
    )
    if not installed:
        return (True, "✓ available")

    config = Path.home() / ".codex" / "config.toml"
    if not config.is_file():
        return (True, "✓ installed")

    try:
        text = config.read_text(encoding="utf-8")
    except OSError:
        return (True, "✓ installed")

    section_header = f'[plugins."{_CODEX_PLUGIN_NAME}@{_CODEX_PERSONAL_MARKETPLACE_NAME}"]'
    if section_header not in text:
        return (True, "✓ installed")

    section = text.split(section_header, 1)[1].split("\n[", 1)[0]
    enabled = any(line.strip() == "enabled = true" for line in section.splitlines())
    return (True, "✓ installed, enabled" if enabled else "✓ installed")


def _platform_app_status(platform_name: str) -> tuple[bool, str]:
    """Return whether a platform desktop app is installed and where it was found."""
    platform = PLATFORMS.get(platform_name)
    if platform is None:
        return (False, "unknown platform")
    for raw_path in platform.app_paths:
        path = Path(raw_path).expanduser()
        if path.exists():
            return (True, str(path))
    if platform.app_paths:
        return (False, ", ".join(platform.app_paths))
    return (False, "no app path configured")


def _count_codex_user_skills(skills_root: Path) -> int:
    """Count autorun-owned user skills installed where Codex scans directly."""
    if not skills_root.is_dir():
        return 0
    return sum(
        1
        for child in skills_root.iterdir()
        if (
            child.is_dir()
            and (child / "SKILL.md").is_file()
            and (child / _CODEX_SKILL_OWNED_MARKER).is_file()
        )
    )


def _count_latest_codex_plugin_cache_skills() -> int:
    """Count skills in the newest installed autorun Codex plugin cache entry."""
    cache_root = (
        Path.home()
        / ".codex"
        / "plugins"
        / "cache"
        / _CODEX_PERSONAL_MARKETPLACE_NAME
        / _CODEX_PLUGIN_NAME
    )
    if not cache_root.is_dir():
        return 0
    counts = [
        _count_skill_dirs(child / "skills")
        for child in cache_root.iterdir()
        if child.is_dir()
    ]
    return max(counts, default=0)



_CODEX_AGENTS_START = "<!-- autorun:codex-agents-md:start -->"
_CODEX_AGENTS_END = "<!-- autorun:codex-agents-md:end -->"


def _install_codex_agents_md(plugin_dir: Path, codex_dir: Path) -> bool:
    """Write autorun's advisory block into ~/.codex/AGENTS.md.

    Codex injects ~/.codex/AGENTS.md into every session
    (https://developers.openai.com/codex/guides/agents-md, 32 KiB limit).
    We append a sentinel-delimited block so:
      - existing user content is preserved
      - re-installing replaces just our block (idempotent)
      - a future uninstall can strip our block cleanly

    Returns True if a template was installed, False if the template is
    missing from the plugin (older builds, partial extracts).
    """
    template = plugin_dir / "src" / "autorun" / "codex_template" / "AGENTS.md"
    if not template.is_file():
        return False

    raw = template.read_text(encoding="utf-8")
    # Strip any pre-existing sentinels so we always wrap with one canonical
    # pair — keeps the merge boundary stable regardless of whether the
    # checked-in template author remembered to add the markers.
    body = raw.replace(_CODEX_AGENTS_START, "").replace(_CODEX_AGENTS_END, "")
    block = f"{_CODEX_AGENTS_START}\n{body.strip()}\n{_CODEX_AGENTS_END}\n"

    target = codex_dir / "AGENTS.md"
    existing = target.read_text(encoding="utf-8") if target.is_file() else ""

    start = existing.find(_CODEX_AGENTS_START)
    end = existing.find(_CODEX_AGENTS_END)
    if start != -1 and end != -1 and end > start:
        prefix = existing[:start].rstrip("\n")
        suffix = existing[end + len(_CODEX_AGENTS_END):].lstrip("\n")
        parts = [p for p in (prefix, block.rstrip(), suffix.rstrip()) if p]
        new = "\n\n".join(parts) + "\n"
    elif existing.strip():
        new = existing.rstrip("\n") + "\n\n" + block
    else:
        new = block

    target.write_text(new, encoding="utf-8")
    return True


def _resolve_forge_base() -> Path:
    """Resolve ForgeCode's base config path.

    Per crates/forge_config/src/reader.rs:58-84 the precedence is:
        FORGE_CONFIG env var > ~/forge/ (legacy, only if it exists) > ~/.forge/.
    """
    env = os.environ.get("FORGE_CONFIG")
    if env:
        return Path(env)
    legacy = Path.home() / "forge"
    if legacy.is_dir():
        return legacy
    return Path.home() / ".forge"


def _install_for_forgecode(
    marketplace_root: Path,
    plugins: list[str],
    force: bool = False,
) -> tuple[bool, str]:
    """Install autorun commands + AGENTS.md into ForgeCode's config dir.

    ForgeCode has no external hook system, so this install is template-only:
    commands land under <base>/commands/*.md and the advisory safety
    guidance lands at <base>/AGENTS.md. ForgeCode reads both at startup
    and injects AGENTS.md content as "custom instructions".

    Args:
        marketplace_root: Path to marketplace root directory.
        plugins: List of plugin names to install (only "autorun" provides
                 a ForgeCode template today; others are no-ops).
        force: Reserved for parity with other installers; copy is
               idempotent so force has no effect today.
    """
    plugin_dir = _autorun_plugin_dir(marketplace_root, plugins)
    if plugin_dir is None:
        return (False, f"autorun plugin not found under {marketplace_root}")

    template = plugin_dir / "src" / "autorun" / "forgecode_template"
    if not template.is_dir():
        return (False, f"forgecode_template missing at {template}")

    base = _resolve_forge_base()
    base.mkdir(parents=True, exist_ok=True)
    cmds_dst = base / "commands"
    cmds_dst.mkdir(exist_ok=True)

    cmds_src = template / "commands"
    for src in cmds_src.glob("*.md"):
        shutil.copy2(src, cmds_dst / src.name)

    agents_src = template / "AGENTS.md"
    if agents_src.is_file():
        shutil.copy2(agents_src, base / "AGENTS.md")

    print()
    print(f"✓ ForgeCode commands installed at {base}/commands/")
    print(f"✓ Advisory safety guidance written to {base}/AGENTS.md")
    print("  Note: ForgeCode has no external hooks — guards run advisory only.")
    return (True, "success")


def _install_conductor(force: bool = False) -> tuple[bool, str]:
    """Install Conductor extension for Gemini CLI (plan mode).

    Conductor provides Context → Spec → Plan → Implement workflow.
    GitHub: https://github.com/gemini-cli-extensions/conductor

    Args:
        force: Force reinstall even if already installed

    Returns:
        Tuple of (success: bool, message: str)
    """
    if not shutil.which("gemini"):
        return (False, "gemini CLI not available")

    print()
    print("Installing Conductor extension for Gemini CLI...")

    if force:
        print("Force mode: uninstalling existing Conductor...")
        run_cmd(["gemini", "extensions", "uninstall", "conductor"])

    result = run_cmd([
        "gemini", "extensions", "install",
        "https://github.com/gemini-cli-extensions/conductor",
        "--auto-update",
        "--consent"
    ])

    if result.ok or result.has_text("already installed"):
        print("   Conductor extension installed")
        print("   Commands: /conductor:setup, /conductor:newTrack, /conductor:implement")
        return (True, "success")
    else:
        print(f"   Conductor installation failed: {result.output}")
        return (False, result.output)


def _verify_gemini_installation() -> bool:
    """Verify Gemini workspace installation.

    Returns:
        True if autorun-workspace is installed
    """
    result = run_cmd(["gemini", "extensions", "list"])
    # Check by directory existence (more reliable than parsing CLI output)
    gemini_ext = Path.home() / ".gemini" / "extensions"
    for name in ["ar", "autorun-workspace", "autorun"]:
        if (gemini_ext / name / "hooks" / "hooks.json").exists():
            return True
    # Fallback: check CLI output
    return result.ok and ("autorun" in result.output)


def _verify_conductor_installation() -> bool:
    """Verify Conductor extension installation.

    Returns:
        True if conductor is installed
    """
    result = run_cmd(["gemini", "extensions", "list"])
    return result.ok and "conductor" in result.output


def _install_for_antigravity(
    marketplace_root: Path,
    plugins: list[str],
    force: bool = False,
) -> tuple[bool, str]:
    """Install autorun into Google Antigravity through its Gemini importer.

    `agy plugin import gemini` is the documented local migration surface exposed
    by `agy plugin help`; local verification on 2026-06-22 with `agy` 1.0.10
    imports the existing Gemini `ar` extension with skills, commands, and hooks.
    The native `agy plugin install <target>` schema expects a plugin.json bundle,
    so direct native bundles remain a separate 0.13.0 acceptance item.
    """
    del marketplace_root, force  # The importer reads installed Gemini extensions.

    if "autorun" not in plugins and "ar" not in plugins:
        return (True, "no autorun plugin selected")
    if not shutil.which("agy"):
        return (False, "agy CLI not found")

    print()
    print("Importing Gemini autorun extension into Google Antigravity...")
    result = run_cmd(["agy", "plugin", "import", "gemini"], timeout=120)
    if not result.ok:
        return (False, result.output)

    verify = run_cmd(["agy", "plugin", "list"], timeout=30)
    if not verify.ok:
        return (False, f"agy plugin import succeeded, but list failed: {verify.output}")
    if '"name": "ar"' not in verify.output and "ar" not in verify.output:
        return (False, "agy plugin list did not report imported ar plugin")

    print("   Antigravity ar plugin imported from Gemini CLI")
    return (True, "success")


# =============================================================================
# AIX Integration - Unified Multi-Platform Installation
# =============================================================================
# CRITICAL: AIX integration follows existing argparse patterns (see plugins.py)
# No click/typer dependencies added - maintains UV workspace structure


def detect_aix_installed() -> bool:
    """Check if AIX is installed and available.

    Returns:
        True if AIX CLI is in PATH
    """
    return shutil.which("aix") is not None


def install_via_aix(force: bool = False) -> tuple[bool, str]:
    """Install autorun locally using AIX if available.

    AIX provides unified installation across Claude Code, Gemini CLI,
    Google Antigravity, OpenCode, and Codex CLI platforms.

    CRITICAL: This performs LOCAL installation only (aix skills install .).
    Does NOT publish to public AIX registry - that requires manual user action.

    Args:
        force: Force reinstall even if already installed

    Returns:
        Tuple of (success: bool, message: str)
    """
    if not detect_aix_installed():
        return (False, "AIX not installed")

    print()
    print("Installing autorun via AIX...")
    print("AIX will auto-detect and install for all available platforms")

    # Get repository root (2 levels up from plugin directory)
    plugin_root = Path(__file__).parent.parent.parent.parent
    aix_manifest = plugin_root / "aix.toml"

    if not aix_manifest.exists():
        return (False, f"AIX manifest not found: {aix_manifest}")

    # Install via AIX (LOCAL installation only)
    cmd = ["aix", "skills", "install", str(plugin_root)]
    if force:
        cmd.append("--force")

    result = run_cmd(cmd)

    if result.ok or result.has_text("already installed"):
        print("   ✓ autorun installed via AIX")

        # Verify which platforms were installed
        verify_result = run_cmd(["aix", "skills", "list"])
        installed_platforms = []
        if verify_result.ok:
            print("\n   Installed on platforms:")
            if "claude_code" in verify_result.output:
                print("   • Claude Code")
                installed_platforms.append("claude")
            if "gemini_cli" in verify_result.output:
                print("   • Gemini CLI")
                installed_platforms.append("gemini")
            if "antigravity" in verify_result.output:
                print("   • Google Antigravity")
                installed_platforms.append("antigravity")
            if "opencode" in verify_result.output:
                print("   • OpenCode")
                installed_platforms.append("opencode")
            if "codex_cli" in verify_result.output:
                print("   • Codex CLI")
                installed_platforms.append("codex")

        # CRITICAL: Verify hooks are registered (essential for autorun functionality)
        # AIX may not fully support hook registration, so we verify and provide guidance
        print("\n   Verifying hook registration...")
        hooks_ok = True

        for platform in installed_platforms:
            if platform == "claude":
                # Check Claude Code hooks
                hooks_path = Path.home() / ".claude" / "hooks.json"
                if hooks_path.exists():
                    import json
                    try:
                        with open(hooks_path, encoding="utf-8") as f:
                            hooks_data = json.load(f)
                        # Check if autorun hooks are present
                        has_hooks = any("autorun" in str(hook) for hook in hooks_data.get("hooks", []))
                        if has_hooks:
                            print("   ✓ Claude Code hooks registered")
                        else:
                            print("   ⚠️  Claude Code hooks may not be registered")
                            hooks_ok = False
                    except Exception:
                        print("   ⚠️  Could not verify Claude Code hooks")
                        hooks_ok = False
                else:
                    print("   ⚠️  Claude Code hooks file not found")
                    hooks_ok = False

            elif platform == "gemini":
                # Check Gemini CLI hooks
                gemini_config = Path.home() / ".config" / "gemini-cli" / "config.json"
                if gemini_config.exists():
                    import json
                    try:
                        with open(gemini_config, encoding="utf-8") as f:
                            config_data = json.load(f)
                        # Check if autorun hooks are present
                        has_hooks = any("autorun" in str(ext) for ext in config_data.get("extensions", []))
                        if has_hooks:
                            print("   ✓ Gemini CLI hooks registered")
                        else:
                            print("   ⚠️  Gemini CLI hooks may not be registered")
                            hooks_ok = False
                    except Exception:
                        print("   ⚠️  Could not verify Gemini CLI hooks")
                        hooks_ok = False
                else:
                    print("   ⚠️  Gemini CLI config file not found")
                    hooks_ok = False

        if not hooks_ok:
            print("\n   ⚠️  Hook registration incomplete!")
            print("   SOLUTION: Autorun has built-in bootstrap that will auto-register")
            print("            hooks on first use. Alternatively, run manually:")
            print("            $ autorun --install")
            print("\n   Why this matters: Hooks enable PreToolUse/PostToolUse functionality,")
            print("                      which powers file policies, command blocking, etc.")

        return (True, "success")
    else:
        return (False, result.output)


def _update_package_metadata(plugin_dir: Path) -> None:
    """Automatically update plugin_dir/src/autorun/metadata.json with current
    commit and build time.

    Args:
        plugin_dir: The PLUGIN directory (e.g., plugins/autorun/), not the
            workspace root. Writes to plugin_dir/src/autorun/metadata.json.
            Walks up to find the enclosing .git for the commit SHA.
    """
    try:
        # 1. Get git commit by walking up from plugin_dir looking for .git
        commit_dir: Path | None = None
        for p in [plugin_dir, *plugin_dir.parents]:
            if (p / ".git").exists():
                commit_dir = p
                break

        if commit_dir is None:
            msg = f"No .git directory found walking up from {plugin_dir}. Run from a git clone to include version metadata."
            logger.info(msg)
            print(f"   ℹ️  Note: {msg}")
            return

        try:
            # Get git commit with '+' suffix for uncommitted changes
            commit = subprocess.check_output(
                ["git", "describe", "--always", "--dirty=+", "--exclude", "*"], 
                cwd=commit_dir, text=True, stderr=subprocess.DEVNULL
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            msg = "Git command failed or not installed. Version metadata will be 'unknown'."
            logger.warning(msg)
            print(f"   ⚠️  Warning: {msg}")
            commit = "unknown"
        
        # 2. Get current time
        build_time = datetime.now(timezone.utc).isoformat()
        
        # 3. Resolve metadata file path
        # Marketplace root is /plugins/autorun
        meta_file = plugin_dir / "src" / "autorun" / "metadata.json"
        
        # 4. Write metadata with robust error handling
        try:
            meta_file.parent.mkdir(parents=True, exist_ok=True)
            import json
            data = {
                "version": _read_plugin_version(plugin_dir),
                "commit": commit,
                "build_time": build_time
            }
            meta_file.write_text(json.dumps(data, indent=2))
            logger.info(f"Updated metadata: commit {commit[:7]}, time {build_time}")
        except (OSError, PermissionError) as e:
            msg = f"Permission denied writing {meta_file}. Check directory permissions: {e}"
            logger.warning(msg)
            print(f"   ❌ Error: {msg}")
            
    except Exception as e:
        logger.warning(f"Unexpected error updating metadata: {e}")


# =============================================================================
# ai-session-tools (aise) Installation
# =============================================================================

# Pinned version for ai-session-tools. Update when releasing a new version.
_AISE_VERSION = "0.3.1"
_AISE_REPO = "git+https://github.com/ahundt/ai_session_tools.git"


def _install_aise(force: bool = False) -> bool:
    """Install ai-session-tools (aise) as a global UV tool.

    aise is a separate CLI for searching/recovering AI session history.
    Autorun does not import it at runtime — it's installed alongside autorun
    for user convenience.

    Install order:
        1. If not --force: check if aise is already installed and working → skip
        2. Try PyPI release → fastest, most reliable
        3. Try git tag → fallback if PyPI is behind
        4. Fall back to git main branch → always available, latest code

    Args:
        force: Force reinstall even if already installed

    Returns:
        True if aise is available after this call (installed or already present)
    """
    print("Installing ai-session-tools (aise)...")

    # Step 1: Check if already installed (skip if not --force)
    if not force:
        aise_path = shutil.which("aise")
        if aise_path:
            # Verify it actually runs
            check = run_cmd(["aise", "--version"], timeout=10)
            if check.ok:
                print(f"   aise: already installed ({aise_path})")
                return True
            logger.debug(f"aise found at {aise_path} but --version failed: {check.output}")

    # Step 2: Try PyPI release (fastest, most reliable)
    aise_result = run_cmd(
        ["uv", "tool", "install", "--force", f"ai-session-tools=={_AISE_VERSION}"],
        timeout=120,
    )
    if aise_result.ok:
        print(f"   aise: ok (PyPI {_AISE_VERSION})")
        return True

    # Step 3: Try git tag (fallback if PyPI is behind or unavailable)
    logger.debug(f"aise PyPI {_AISE_VERSION} failed, trying git tag: {aise_result.output}")
    aise_result = run_cmd(
        ["uv", "tool", "install", "--force", f"{_AISE_REPO}@v{_AISE_VERSION}"],
        timeout=120,
    )
    if aise_result.ok:
        print(f"   aise: ok (git v{_AISE_VERSION})")
        return True

    # Step 4: Fall back to git main branch (always available)
    logger.debug(f"aise git tag v{_AISE_VERSION} failed, trying main: {aise_result.output}")
    aise_result = run_cmd(
        ["uv", "tool", "install", "--force", f"{_AISE_REPO}@main"],
        timeout=120,
    )
    if aise_result.ok:
        print(f"   aise: ok (git main, v{_AISE_VERSION} not yet available)")
        return True

    # All attempts failed — non-fatal, autorun works without aise
    print("   aise: install failed (optional, continuing)")
    logger.warning(f"aise install failed: {aise_result.output}")
    return False


# =============================================================================
# Main Function - Installation
# =============================================================================


def install_plugins(
    selection: str = "all",
    *,
    tool: bool = False,
    force: bool = False,
    claude_only: bool = False,
    gemini_only: bool = False,
    codex_only: bool = False,
    antigravity_only: bool = False,
    conductor: bool = True,
    use_aix: bool = None,  # NEW: Auto-detect AIX if None (default behavior)
) -> int:
    """Install and enable plugins for Claude Code and/or Gemini CLI.

    CRITICAL: Will auto-detect and use AIX for LOCAL installation if available.
    Does NOT publish to public AIX registry (requires manual user action).

    Args:
        selection: "all" or comma-separated plugin names (e.g., "autorun,pdf-extractor")
        tool: Also run `uv tool install` for global CLI availability
        force: Force reinstall even if already installed (for dev with same version)
        claude_only: Install only for Claude Code (default: False)
        gemini_only: Install only for Gemini CLI (default: False)
        codex_only: Install only for Codex CLI (default: False)
        antigravity_only: Install only for Google Antigravity CLI (default: False)
        conductor: Install Conductor extension for Gemini (default: True)
        use_aix: Use AIX for installation (None = auto-detect, True = force use, False = skip)

    Returns:
        Exit code: 0 = success, 1 = failure

    Behavior:
        - Default (no CLI flags): Installs for all available CLIs with maximum capability
        - --claude: Installs only for Claude Code (error if not available)
        - --gemini: Installs only for Gemini CLI (error if not available)
        - --antigravity: Imports Gemini extensions into Google Antigravity only
        - --codex: Installs only for Codex CLI (error if not available)
        - Multiple platform flags: Installs for all selected CLIs
        - --no-conductor: Skip Conductor (reduce scope to workspace only)
        - --aix: Force use AIX (fail if not installed)
        - --no-aix: Skip AIX even if installed
        - Continues even if one CLI fails (reports status for each)

    Note:
        Commands and skills work natively via extension manifest.
    """
    # Ensure metadata is fresh before install starts.
    # find_marketplace_root() returns the workspace root (which has its own
    # .claude-plugin/marketplace.json listing ./plugins/autorun as a source).
    # The metadata and manifests must be written INTO the plugin dir, not the
    # workspace root, otherwise stray src/autorun/metadata.json and
    # src/autorun/gemini_template/ files leak into the workspace on every
    # `autorun --install` run. Prefer `<root>/plugins/autorun` when present,
    # fall back to `root` only if the workspace root IS the plugin dir
    # (single-plugin repo).
    root = find_marketplace_root()
    plugin_candidate = root / "plugins" / "autorun"
    if plugin_candidate.is_dir() and (plugin_candidate / ".claude-plugin" / "plugin.json").exists():
        plugin_root = plugin_candidate
    elif (root / ".claude-plugin" / "plugin.json").exists():
        plugin_root = root
    else:
        plugin_root = plugin_candidate  # best-effort fallback

    _update_package_metadata(plugin_root)

    # NEW: Harmonize manifests from aix.toml (Single Source of Truth)
    try:
        from autorun.aix_manifest import generate_manifests
        if plugin_root.exists():
            generate_manifests(plugin_root)
    except ImportError:
        logger.warning("Could not import generate_manifests, skipping manifest harmonization")
    except Exception as e:
        logger.error(f"Manifest generation failed: {e}")

    # Re-import to get fresh values if we're in the same process
    import importlib
    import autorun
    importlib.reload(autorun)
    from autorun import __version__, __commit__, __build_time__
    
    print(f"autorun v{__version__}")
    print(f"Commit: {__commit__}")
    print(f"Build Time: {__build_time__}")

    # Python version check
    if sys.version_info < (3, 10):
        print(f"Python {sys.version_info.major}.{sys.version_info.minor} detected. "
              f"autorun requires Python 3.10+.")
        return 1

    # NEW: Auto-detect AIX and use for local installation if available
    # CRITICAL: Only does LOCAL install (aix skills install .), never publishes
    if use_aix is None:
        use_aix = detect_aix_installed()  # Auto-detect by default

    if use_aix and not (claude_only or gemini_only or codex_only or antigravity_only):
        aix_success, aix_msg = install_via_aix(force)
        if aix_success:
            print("\n✓ Installation via AIX completed successfully")
            print("Run `aix skills list` to see all installed platforms")
            return 0
        else:
            print(f"\n⚠️  AIX installation failed: {aix_msg}")
            print("Falling back to direct installation...")

    # Parse and validate plugin selection
    plugins = _parse_selection(selection)
    if not plugins:
        print("No valid plugins specified")
        return 1

    # Detect available CLIs
    available = detect_available_clis()

    try:
        target_clis = determine_target_clis(
            claude_only,
            gemini_only,
            available,
            codex_only=codex_only,
            antigravity_only=antigravity_only,
        )
    except ValueError as e:
        print(f"Error: {e}")
        return 1

    if not target_clis:
        print("No target CLIs available or specified.")
        print(f"Available CLIs: {', '.join([k for k, v in available.items() if v]) or 'none'}")
        if claude_only and not available["claude"]:
            print("   Claude Code not found. Install from:")
            print("   https://docs.anthropic.com/claude/docs/claude-code")
        if gemini_only and not available["gemini"]:
            print("   Gemini CLI not found. Install from:")
            print("   npm install -g @google-labs/gemini-cli")
        if antigravity_only and not available["antigravity"]:
            print("   Antigravity CLI not found. Install from:")
            print("   brew install --cask antigravity-cli")
        if codex_only and not available["codex"]:
            print("   Codex CLI not found. Install from:")
            print("   https://developers.openai.com/codex/")
        return 1

    # Ensure marketplace is added
    try:
        marketplace_root = find_marketplace_root()
    except FileNotFoundError as e:
        print(f"{e}")
        return 1

    # Import version
    try:
        from autorun import __version__
    except ImportError:
        __version__ = "0.12.0"

    print(f"autorun v{__version__}")
    print(f"Marketplace root: {marketplace_root}")
    print(f"Target CLIs: {', '.join(target_clis)}")
    print()

    # UV environment check (warning only, not blocker)
    if (marketplace_root / "plugins" / "autorun").exists():
        plugin_dir = marketplace_root / "plugins" / "autorun"
    else:
        plugin_dir = marketplace_root

    uv_check = _check_uv_env(plugin_dir)
    if not uv_check.ok:
        logger.warning(f"UV environment: {uv_check.output}")
        print(f"⚠️  {uv_check.output}")
    else:
        logger.info("UV environment OK")

    # Sync autorun Python dependencies (critical for hooks to work)
    if shutil.which("uv"):
        print("Installing autorun Python dependencies...")
        dep_result = _sync_dependencies()
        if dep_result.ok:
            print("   Dependencies synced")
        else:
            logger.warning(f"Dependency sync failed: {dep_result.output}")
            print(f"⚠️  Dependency sync: {dep_result.output}")

    # Install pdf-extractor dependencies if plugin is selected
    if "pdf-extractor" in plugins:
        print("Installing pdf-extractor dependencies...")
        pdf_result = _install_pdf_deps()
        if pdf_result.ok:
            if "skipping" not in pdf_result.output:
                print("   PDF dependencies installed")
        else:
            logger.warning(f"PDF deps: {pdf_result.output}")
            print(f"⚠️  PDF deps: {pdf_result.output}")

    # Track overall success
    all_succeeded = True
    claude_succeeded: list[str] = []
    claude_failed: list[str] = []
    claude_success = False
    gemini_success = False
    antigravity_success = False

    # Install for Claude Code
    if "claude" in target_clis:
        print()
        print("Adding autorun marketplace for Claude Code...")
        result = run_cmd(["claude", "plugin", "marketplace", "add", str(marketplace_root)])
        if result.ok:
            print("   Added autorun marketplace")
        elif result.has_text("already"):
            print("   autorun marketplace already exists")
        else:
            print(f"   Marketplace add: {result.output}")

        # Uninstall first if force flag (for same-version reinstall)
        if force:
            print()
            print("Force mode: uninstalling existing plugins...")
            for name in plugins:
                # Use name@MARKETPLACE to ensure we hit the right one
                run_cmd(["claude", "plugin", "uninstall", f"{name}@{MARKETPLACE}"])

        # Install + enable each plugin
        print()
        print(f"Installing {len(plugins)} plugin(s) for Claude Code:")

        for name in plugins:
            print(f"   {name}...", end=" ", flush=True)
            
            # Use fully qualified name for all operations
            fq_name = f"{name}@{MARKETPLACE}"

            # Try update first (faster, preserves settings)
            upd = run_cmd(["claude", "plugin", "update", fq_name])
            if upd.ok:
                # Enable after update (update doesn't guarantee enabled state)
                enable_result = run_cmd(["claude", "plugin", "enable", fq_name])
                if enable_result.ok or enable_result.has_text("already"):
                    print("updated")
                    _substitute_claude_cache_paths(marketplace_root, name)

                    claude_succeeded.append(name)
                    continue
                else:
                    print(f"enable after update failed: {enable_result.output}")
                    claude_failed.append(name)
                    continue

            # Fall back to fresh install
            result = run_cmd(["claude", "plugin", "install", fq_name])
            if not result.ok and not result.has_text("already"):
                # Marketplace install failed — try cache fallback
                print("marketplace failed, trying cache...", end=" ", flush=True)
                if _install_to_cache(name):
                    print("ok (cache)")
                    claude_succeeded.append(name)
                else:
                    print("cache failed")
                    claude_failed.append(name)
                continue

            # Enable (critical: without this, hooks don't run)
            result = run_cmd(["claude", "plugin", "enable", fq_name])
            if result.ok or result.has_text("already"):
                print("ok")
                _substitute_claude_cache_paths(marketplace_root, name)

                claude_succeeded.append(name)
            else:
                print(f"enable failed: {result.output}")
                claude_failed.append(name)

        claude_success = len(claude_succeeded) == len(plugins)
        all_succeeded = all_succeeded and claude_success

    # Install for Gemini CLI
    if "gemini" in target_clis:
        gemini_success, gemini_msg = _install_for_gemini(marketplace_root, plugins, force)
        all_succeeded = all_succeeded and gemini_success

        # Install Conductor if requested and Gemini install succeeded
        if conductor and gemini_success:
            conductor_success, conductor_msg = _install_conductor(force)
            # Note: Conductor failure doesn't affect overall success
            # (it's an optional enhancement)

    if "antigravity" in target_clis:
        antigravity_success, antigravity_msg = _install_for_antigravity(marketplace_root, plugins, force)
        if not antigravity_success:
            print(f"   Antigravity import failed: {antigravity_msg}")
        all_succeeded = all_succeeded and antigravity_success

    # Install for Codex CLI (user-level ~/.codex/hooks.json — always active)
    codex_success = False
    codex_plugin_success = False
    codex_plugin_msg = ""
    if "codex" in target_clis:
        codex_success, codex_msg = _install_for_codex(marketplace_root, plugins, force)
        all_succeeded = all_succeeded and codex_success
        if codex_success:
            print()
            print("Installing Codex plugin package...")
            codex_plugin_result = _install_codex_plugin_with_cli(force=force)
            codex_plugin_success = codex_plugin_result.ok
            codex_plugin_msg = codex_plugin_result.output
            if codex_plugin_success:
                print("   autorun@personal installed/enabled")
            else:
                print(f"   Codex plugin add failed: {codex_plugin_msg}")
            all_succeeded = all_succeeded and codex_plugin_success

    # Install for ForgeCode (template-only — commands + AGENTS.md, no hooks)
    forge_success = False
    if "forgecode" in target_clis:
        forge_success, forge_msg = _install_for_forgecode(marketplace_root, plugins, force)
        all_succeeded = all_succeeded and forge_success

    # Optional: uv tool install for global CLI
    if tool:
        print()
        print("Installing UV tool...")
        result = run_cmd(["uv", "tool", "install", ".", "--force"], timeout=120)
        if result.ok:
            print("   uv tool: ok")
        else:
            print(f"   uv tool: {result.output}")

        # Install ai-session-tools (aise) as a global UV tool.
        # Autorun doesn't import aise at runtime — it's a separate CLI for
        # searching/recovering AI session history. Install order:
        #   1. Check if aise is already installed and working → skip if --force not given
        #   2. Try pinned release tag (v0.3.1) → preferred for reproducibility
        #   3. Fall back to main branch → always available, latest code
        _install_aise(force=force)

    # Check for hook conflicts (warn if hookify or others might interfere)
    _check_hook_conflicts()

    # Summary
    print()
    print("=" * 60)

    if "claude" in target_clis:
        if claude_success:
            print(f"✓ Claude Code: Installed {len(claude_succeeded)}/{len(plugins)} plugins")
        else:
            print(f"✗ Claude Code: Installed {len(claude_succeeded)}/{len(plugins)} plugins")
            if claude_failed:
                print(f"  Failed: {', '.join(claude_failed)}")

    if "gemini" in target_clis:
        if gemini_success:
            print(f"✓ Gemini CLI: Plugins installed ({', '.join(plugins)})")
            if conductor:
                conductor_ok = _verify_conductor_installation()
                if conductor_ok:
                    print("✓ Gemini CLI: Conductor extension installed")
                else:
                    print("⚠️  Gemini CLI: Conductor installation failed (optional)")
        else:
            print("✗ Gemini CLI: Installation failed")

    if "antigravity" in target_clis:
        if antigravity_success:
            print("✓ Google Antigravity: imported Gemini ar plugin with commands, skills, and hooks")
        else:
            print("✗ Google Antigravity: import failed")

    if "codex" in target_clis:
        if codex_success:
            print("✓ Codex CLI: hooks installed at ~/.codex/hooks.json (run /hooks inside Codex to trust)")
            if codex_plugin_success:
                print("✓ Codex CLI: plugin installed as autorun@personal")
            else:
                print("✗ Codex CLI: plugin install failed")
        else:
            print("✗ Codex CLI: hooks install failed")

    if "forgecode" in target_clis:
        if forge_success:
            print("✓ ForgeCode: commands + AGENTS.md installed (advisory — no hook enforcement)")
        else:
            print("✗ ForgeCode: install failed")

    print()
    print("Available commands:")
    print("  /ar:*             - autorun commands (autorun, file policies, plan export, tmux)")
    print("  /pdf-extractor:*  - PDF extraction commands")
    if "gemini" in target_clis and conductor:
        print("  /conductor:*      - Conductor plan mode (Gemini only)")
    if "antigravity" in target_clis:
        print("  agy plugin list   - verify imported Antigravity plugins")
    print()
    print("Run '/help' to see all available commands.")

    # Restart daemon if running (picks up new code/config)
    _restart_daemon_if_running()

    return 0 if all_succeeded else 1


# =============================================================================
# Main Function - Uninstall
# =============================================================================


def uninstall_plugins(selection: str = "all") -> int:
    """Uninstall plugins, UV tools, and cache entries.

    Args:
        selection: "all" or comma-separated plugin names

    Returns:
        Exit code: 0 = success
    """
    plugins = _parse_selection(selection)

    print(f"Uninstalling {len(plugins)} plugin(s)...")
    for name in plugins:
        print(f"   {name}...", end=" ", flush=True)
        result = run_cmd(["claude", "plugin", "uninstall", f"{name}@{MARKETPLACE}"])
        if result.ok or result.has_text("not found"):
            print("ok")
        else:
            print(f"warning: {result.output}")

    # Uninstall UV tool
    print("   UV tool...", end=" ", flush=True)
    result = run_cmd(["uv", "tool", "uninstall", "autorun"])
    if result.ok or result.has_text("not installed"):
        print("ok")
    else:
        print(f"warning: {result.output}")

    # Remove cache entries
    cache_base = Path.home() / ".claude" / "plugins" / "cache" / MARKETPLACE
    if cache_base.exists():
        shutil.rmtree(cache_base)
        print(f"   Removed cache: {cache_base}")

    # Remove legacy manual install dir
    legacy_dir = Path.home() / ".claude" / "plugins" / "autorun"
    if legacy_dir.exists():
        shutil.rmtree(legacy_dir)
        print(f"   Removed legacy dir: {legacy_dir}")

    print()
    print("Uninstall complete.")
    return 0


# =============================================================================
# Main Function - Status
# =============================================================================


def show_status() -> int:
    """Show installation status of all plugins, UV environment, and CLI tools.

    Returns:
        Exit code: 0 = all installed, 1 = some missing
    """
    print("Plugin Status:")
    print("-" * 60)

    # Check claude CLI
    claude_ok = shutil.which("claude") is not None
    if claude_ok:
        print("  claude CLI: found")
    else:
        print("  claude CLI: not found")
    claude_app_ok, claude_app_status = _platform_app_status("claude")
    print(f"  Claude app: {'✓ installed' if claude_app_ok else '✗ not found'} ({claude_app_status})")

    if not claude_ok:
        print()
        print("Install Claude Code first")
        return 1

    # UV environment check
    try:
        marketplace_root = find_marketplace_root()
        # Ensure we don't double-append plugins/autorun
        if (marketplace_root / "pyproject.toml").exists():
            plugin_dir = marketplace_root
        else:
            plugin_dir = marketplace_root / "plugins" / "autorun"
        uv_result = _check_uv_env(plugin_dir)
        print(f"  UV environment: {'OK' if uv_result.ok else uv_result.output}")
    except FileNotFoundError:
        print("  UV environment: marketplace not found")

    # Check each plugin
    all_ok = True
    result = run_cmd(["claude", "plugin", "list"])

    print()
    print("Plugins:")
    for plugin in PluginName.all():
        # Check if plugin appears in list with "enabled" status
        is_installed = plugin in result.output and "enabled" in result.output
        if is_installed:
            status = "✓ enabled"
        else:
            status = "✗ not installed"
            all_ok = False
        print(f"  {plugin}: {status}")

    # Check UV CLI tools in PATH
    print()
    print("UV CLI Tools:")
    for tool_name in ["autorun", "aise"]:
        path = shutil.which(tool_name)
        if path:
            print(f"  {tool_name}: {path}")
        else:
            print(f"  {tool_name}: not found")

    # Check for venv
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    if plugin_root:
        venv_path = Path(plugin_root) / ".venv"
        if venv_path.exists():
            print(f"\n  venv: {venv_path}")
        else:
            print("\n  venv: not found")

    # Check Gemini CLI
    print()
    print("-" * 60)
    print("Gemini CLI:")

    gemini_ok = shutil.which("gemini") is not None
    if gemini_ok:
        print("  gemini CLI: found")

        result = run_cmd(["gemini", "extensions", "list"])
        if result.ok:
            # Check for each plugin separately (new Gemini architecture)
            for plugin in ["ar", "pdf-extractor"]:
                is_installed = plugin in result.output
                print(f"  {plugin}: {'✓ installed' if is_installed else '✗ not installed'}")

            conductor = "conductor" in result.output
            print(f"  conductor: {'✓ installed' if conductor else '✗ not installed (optional)'}")

            # Note: Commands and skills work natively via extension manifest
            # No need to check for aix-translated TOML files
        else:
            print(f"  extensions list failed: {result.output}")
            all_ok = False
    else:
        print("  gemini CLI: not found")
        print("  Install: npm install -g @google-labs/gemini-cli")

    # Check Codex CLI user-level install.
    print()
    print("-" * 60)
    print("Codex CLI:")

    codex_ok = shutil.which("codex") is not None
    print(f"  codex CLI: {'found' if codex_ok else 'not found'}")
    codex_dir = Path.home() / ".codex"
    codex_hooks = codex_dir / "hooks.json"
    required_codex_events = {
        "PreToolUse",
        "PostToolUse",
        "UserPromptSubmit",
        "SessionStart",
        "Stop",
        "SubagentStop",
    }
    if codex_hooks.is_file():
        try:
            hooks_data = json.loads(codex_hooks.read_text(encoding="utf-8"))
            events = set(hooks_data.get("hooks", {}))
            has_events = required_codex_events.issubset(events)
            has_autorun = "--cli codex" in json.dumps(hooks_data.get("hooks", {}))
            hooks_status = "✓ installed" if has_events and has_autorun else "✗ incomplete"
            if codex_ok and hooks_status.startswith("✗"):
                all_ok = False
            print(f"  hooks.json: {hooks_status}")
            if not has_events:
                missing = ", ".join(sorted(required_codex_events - events))
                print(f"    missing events: {missing}")
        except (json.JSONDecodeError, OSError) as e:
            print(f"  hooks.json: ✗ unreadable ({e})")
            if codex_ok:
                all_ok = False
    else:
        print("  hooks.json: ✗ not installed")
        if codex_ok:
            all_ok = False

    codex_agents = codex_dir / "AGENTS.md"
    print(f"  AGENTS.md: {'✓ installed' if codex_agents.is_file() else '✗ not installed'}")
    skills_root = Path.home() / ".agents" / "skills"
    user_skill_count = _count_codex_user_skills(skills_root)
    plugin_source_skill_count = _count_skill_dirs(_codex_plugin_source_dir() / "skills")
    plugin_cache_skill_count = _count_latest_codex_plugin_cache_skills()
    skills_ok = user_skill_count > 0 and plugin_source_skill_count > 0
    print(
        f"  skills: {'✓' if skills_ok else '✗'} "
        f"{user_skill_count} user, "
        f"{plugin_source_skill_count} plugin source, "
        f"{plugin_cache_skill_count} plugin cache"
    )
    if codex_ok and not skills_ok:
        all_ok = False
    marketplace_ok, marketplace_status = _codex_plugin_marketplace_status()
    print(f"  plugin marketplace: {marketplace_status}")
    if codex_ok and not marketplace_ok:
        all_ok = False
    if not codex_ok:
        print("  Install: https://developers.openai.com/codex/")

    codex_app_ok, codex_app_status = _platform_app_status("codex")
    print(f"  Codex app: {'✓ installed' if codex_app_ok else '✗ not found'} ({codex_app_status})")

    # Check Antigravity app/CLI successor surface.
    print()
    print("-" * 60)
    print("Google Antigravity:")

    antigravity_ok = shutil.which("agy") is not None
    print(f"  agy CLI: {'found' if antigravity_ok else 'not found'}")
    antigravity_app_ok, antigravity_app_status = _platform_app_status("antigravity")
    print(
        f"  Antigravity app: "
        f"{'✓ installed' if antigravity_app_ok else '✗ not found'} ({antigravity_app_status})"
    )
    if antigravity_ok:
        result = run_cmd(["agy", "plugin", "list"], timeout=30)
        if result.ok:
            has_ar = '"name": "ar"' in result.output or "ar" in result.output
            print(f"  ar plugin: {'✓ imported' if has_ar else '✗ not imported'}")
            if not has_ar:
                all_ok = False
        else:
            print(f"  plugins: ✗ list failed: {result.output}")
            all_ok = False
    else:
        print("  Install CLI: brew install --cask antigravity-cli")

    # Check ForgeCode advisory install.
    print()
    print("-" * 60)
    print("ForgeCode:")

    forge_base = _resolve_forge_base()
    forge_commands = forge_base / "commands"
    forge_agents = forge_base / "AGENTS.md"
    forge_command_count = len(list(forge_commands.glob("ar-*.md"))) if forge_commands.is_dir() else 0
    print(f"  commands: {'✓ installed' if forge_command_count else '✗ not installed'} ({forge_command_count})")
    print(f"  AGENTS.md: {'✓ installed' if forge_agents.is_file() else '✗ not installed'}")
    print("  hooks: advisory only (ForgeCode has no external hook system)")

    return 0 if all_ok else 1


# =============================================================================
# Self-Update Mechanism
# =============================================================================


def check_for_updates() -> tuple[bool, str, str]:
    """Check if autorun update is available using stdlib (no dependencies).

    Uses importlib.metadata for current version and GitHub API for latest release.
    Handles network failures and missing package gracefully.

    Returns:
        Tuple of (update_available: bool, current_version: str, latest_version: str)

    Examples:
        >>> update_available, current, latest = check_for_updates()
        >>> if update_available:
        ...     print(f"Update available: {current} → {latest}")
    """
    import json
    import urllib.request
    from importlib.metadata import version as get_version, PackageNotFoundError

    try:
        current = get_version("autorun")
    except PackageNotFoundError:
        return (False, "unknown", "unknown")

    try:
        url = "https://api.github.com/repos/ahundt/autorun/releases/latest"
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "autorun-installer")
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read())
            latest = data["tag_name"].lstrip("v")
            # Semantic version comparison using integer tuples
            def _parse_ver(v: str) -> tuple[int, ...]:
                return tuple(int(x) for x in v.split("."))
            try:
                return (_parse_ver(latest) > _parse_ver(current), current, latest)
            except (ValueError, TypeError):
                # Unparseable version — don't risk a wrong comparison
                return (False, current, latest)
    except (urllib.error.URLError, json.JSONDecodeError, KeyError):
        return (False, current, "unknown")


@dataclass(frozen=True)
class UpdateStrategy:
    """Installation method detection for self-updates.

    Detects how autorun was installed to choose correct update pathway:
    - AIX: Highest priority (manages all CLIs)
    - Plugin: Claude Code or Gemini CLI plugin system
    - UV: UV package manager
    - Pip: Python pip package manager
    """

    method: str  # "plugin", "uv", "pip", "aix"
    cli: str | None  # "claude", "gemini", None

    @staticmethod
    def detect() -> "UpdateStrategy":
        """Auto-detect installation method for updates.

        Priority order:
        1. AIX (if installed and managing autorun)
        2. Claude Code plugin (if claude CLI found + autorun in list)
        3. Gemini CLI plugin (if gemini CLI found + autorun in list)
        4. UV (if UV available)
        5. Pip (fallback)

        Returns:
            UpdateStrategy with detected method and CLI
        """
        # Try AIX first (highest priority)
        if detect_aix_installed():
            return UpdateStrategy("aix", None)

        # Try plugin systems
        if shutil.which("claude"):
            result = run_cmd(["claude", "plugin", "list"], timeout=10)
            if result.ok and "autorun" in result.output:
                return UpdateStrategy("plugin", "claude")

        if shutil.which("gemini"):
            result = run_cmd(["gemini", "extensions", "list"], timeout=10)
            if result.ok and "autorun" in result.output:
                return UpdateStrategy("plugin", "gemini")
            # Also check directory directly
            gemini_ext = Path.home() / ".gemini" / "extensions"
            for name in ["ar", "autorun-workspace", "autorun"]:
                if (gemini_ext / name / "hooks" / "hooks.json").exists():
                    return UpdateStrategy("plugin", "gemini")

        # Fall back to package manager
        return UpdateStrategy("uv" if has_uv() else "pip", None)


def perform_self_update(method: str = "auto") -> CmdResult:
    """Perform self-update using detected or specified installation method.

    Args:
        method: "auto" (detect), "plugin", "uv", "pip", "aix"

    Returns:
        CmdResult indicating success/failure

    Examples:
        >>> # Auto-detect and update
        >>> result = perform_self_update()
        >>> print(result.output)

        >>> # Force specific method
        >>> result = perform_self_update(method="uv")
    """
    update_available, current, latest = check_for_updates()

    if not update_available:
        return CmdResult(True, f"Already on latest version ({current})")

    print(f"Update available: {current} → {latest}")

    # Auto-detect if needed
    if method == "auto":
        strategy = UpdateStrategy.detect()
        method = strategy.method
        print(f"Detected installation method: {method}")

    # Strategy pattern - each method is a separate handler
    if method == "aix":
        return run_cmd(["aix", "skills", "update", "autorun"], timeout=120)

    elif method == "plugin":
        # Try both CLIs (one will succeed)
        if shutil.which("claude"):
            result = run_cmd(["claude", "plugin", "update", "autorun"], timeout=60)
            if result.ok:
                return result

        if shutil.which("gemini"):
            # Try current name first, then legacy names
            gemini_ext = Path.home() / ".gemini" / "extensions"
            ext_name = "ar"  # current default
            for name in ["ar", "autorun-workspace", "autorun"]:
                if (gemini_ext / name).exists():
                    ext_name = name
                    break
            result = run_cmd(["gemini", "extensions", "update", ext_name], timeout=60)
            if result.ok:
                return result

        return CmdResult(False, "No plugin CLI found for update")

    elif method == "uv":
        # UV pathway: install + register
        result = run_cmd([
            "uv", "pip", "install", "--upgrade",
            "git+https://github.com/ahundt/autorun.git"
        ], timeout=120)
        if result.ok:
            # Re-register plugins
            runner = get_python_runner()
            return run_cmd([*runner, "-m", "autorun", "--install", "--force"], timeout=120)
        return result

    elif method == "pip":
        # Pip pathway: install + register
        result = run_cmd([
            "pip", "install", "--upgrade",
            "git+https://github.com/ahundt/autorun.git"
        ], timeout=120)
        if result.ok:
            return run_cmd(["python", "-m", "autorun", "--install", "--force"], timeout=120)
        return result

    else:
        return CmdResult(False, f"Unknown update method: {method}")


# =============================================================================
# Main Function - Dev Workflow
# =============================================================================



# =============================================================================
# CLI Entry Points
# =============================================================================
# Note: Legacy autorun-install entry point removed in v0.8.0
# Use 'autorun --install' instead


def _map_legacy_flags(args: list[str]) -> list[str]:
    """Map legacy autorun-install subcommands to __main__.py flags.

    Provides backward compatibility for the old `autorun-install install`
    style commands by converting them to `autorun --install` style flags.

    Args:
        args: Legacy positional arguments (e.g., ["install", "--force"])

    Returns:
        Mapped flag-style arguments (e.g., ["--install", "--force-install"])
    """
    if not args:
        return ["--install"]

    subcommand = args[0].lower()
    remaining = args[1:]

    mapping = {
        "install": "--install",
        "uninstall": "--uninstall",
        "check": "--status",
        "status": "--status",
    }

    if subcommand not in mapping:
        return ["--install"]

    result = [mapping[subcommand]]

    for flag in remaining:
        if flag == "--force":
            result.append("--force-install")
        else:
            result.append(flag)

    return result


def install_main():
    """Entry point for autorun-install console script.

    Provides backward compatibility with the legacy `autorun-install`
    command by mapping legacy subcommands to the unified CLI flags.
    """
    args = sys.argv[1:]
    mapped = _map_legacy_flags(args)
    sys.exit(_install_module_main(mapped))


def _create_install_module_parser() -> argparse.ArgumentParser:
    """Create the direct install-module parser.

    This supports the documented local development command:
    `python -m plugins.autorun.src.autorun.install --install --codex`.
    The primary user CLI remains autorun.__main__; this parser exists so
    the module-level fallback does not silently ignore platform flags.
    """
    parser = argparse.ArgumentParser(
        prog="python -m plugins.autorun.src.autorun.install",
        description="Install autorun hooks, skills, and plugin assets.",
    )
    parser.add_argument("selection", nargs="?", default="all")
    parser.add_argument("--install", action="store_true", help="Install selected plugins")
    parser.add_argument("--uninstall", action="store_true", help="Uninstall selected plugins")
    parser.add_argument("--status", action="store_true", help="Show installation status")
    parser.add_argument("--force", "--force-install", dest="force", action="store_true")
    parser.add_argument("--tool", action="store_true", help="Also install UV CLI tools")
    parser.add_argument("--claude", action="store_true", help="Install for Claude Code only")
    parser.add_argument("--gemini", action="store_true", help="Install for Gemini CLI only")
    parser.add_argument("--antigravity", action="store_true", help="Install for Google Antigravity CLI only")
    parser.add_argument("--codex", action="store_true", help="Install for Codex CLI only")
    parser.add_argument(
        "--conductor",
        action="store_true",
        default=True,
        help="Install Conductor extension for Gemini",
    )
    parser.add_argument(
        "--no-conductor",
        action="store_false",
        dest="conductor",
        help="Skip Conductor extension installation for Gemini",
    )
    parser.add_argument(
        "--aix",
        action="store_true",
        dest="use_aix",
        default=None,
        help="Force AIX installation",
    )
    parser.add_argument(
        "--no-aix",
        action="store_false",
        dest="use_aix",
        help="Skip AIX installation",
    )
    return parser


def _install_module_main(argv: list[str] | None = None) -> int:
    """Entry point for `python -m plugins.autorun.src.autorun.install`."""
    parser = _create_install_module_parser()
    args = parser.parse_args(argv)

    if args.uninstall:
        return uninstall_plugins(args.selection)
    if args.status:
        return show_status()

    return install_plugins(
        args.selection,
        tool=args.tool,
        force=args.force,
        claude_only=args.claude,
        gemini_only=args.gemini,
        codex_only=args.codex,
        antigravity_only=args.antigravity,
        conductor=args.conductor,
        use_aix=args.use_aix,
    )


if __name__ == "__main__":
    # Configure logging for CLI use (file-only when AUTORUN_DEBUG=1, disabled otherwise)
    import os
    if os.environ.get('AUTORUN_DEBUG') == '1':
        from pathlib import Path
        log_file = ipc.AUTORUN_LOG_FILE
        logging.basicConfig(
            handlers=[logging.FileHandler(log_file)],
            level=logging.DEBUG,
            format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
        )
    else:
        # Disable logging - add NullHandler to prevent default stderr handler
        logging.basicConfig(handlers=[logging.NullHandler()], level=logging.CRITICAL + 1)
    sys.exit(_install_module_main())
