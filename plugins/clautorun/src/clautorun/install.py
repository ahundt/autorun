#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plugin installation and management for clautorun.

Consolidated installer with complete dependency bootstrap and cache fallback.
Superset of install.py and install_plugins.py capabilities.

Usage:
    clautorun --install                    # Install all plugins
    clautorun --install clautorun          # Install specific plugin
    clautorun --install --force-install    # Force reinstall
    clautorun --install --tool             # Also install UV CLI tools
    clautorun --uninstall                  # Uninstall plugins
    clautorun --status                     # Show installation status
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from functools import lru_cache
from pathlib import Path

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
MARKETPLACE = "clautorun"


# =============================================================================
# Data Types
# =============================================================================


class PluginName(str, Enum):
    """Valid plugin names. Prevents typos and enables IDE completion."""

    CLAUTORUN = "clautorun"
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
    """Restart the clautorun daemon if it's currently running.

    Called at the end of install to ensure the daemon picks up new code/config.
    Imports restart_daemon() from scripts/restart_daemon.py.
    Non-fatal: installation succeeds even if daemon restart fails.
    """
    lock_path = Path.home() / ".clautorun" / "daemon.lock"

    # Quick check: skip entirely if no daemon is running
    if not lock_path.exists():
        return
    try:
        pid = int(lock_path.read_text().strip())
        os.kill(pid, 0)
    except (ValueError, OSError):
        return

    print()
    print("Restarting daemon to pick up changes...")

    # Import restart_daemon from scripts module
    scripts_dir = Path(__file__).resolve().parent.parent.parent / "scripts"
    if not scripts_dir.exists():
        try:
            scripts_dir = find_marketplace_root() / "scripts"
        except FileNotFoundError:
            pass

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "restart_daemon",
            scripts_dir / "restart_daemon.py",
        )
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            result = module.restart_daemon()
            if result == 0:
                print("   Daemon restarted")
            else:
                print("   Daemon restart returned non-zero (non-fatal)")
        else:
            print("   Could not load restart_daemon module (non-fatal)")
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
        >>> cmd = [*runner, "-m", "clautorun", "--install"]
        >>> # Result: ["uv", "run", "python", "-m", "clautorun", "--install"]

        >>> # When UV unavailable:
        >>> runner = get_python_runner()  # ["python"]
        >>> cmd = [*runner, "-m", "clautorun", "--install"]
        >>> # Result: ["python", "-m", "clautorun", "--install"]
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

This usually means clautorun is installed as a package, not from source.

━━━ SOLUTION OPTIONS ━━━

Option 1: Install via Plugin System (Recommended)
  # For Claude Code:
  claude plugin install https://github.com/ahundt/clautorun.git

  # For Gemini CLI:
  gemini extensions install https://github.com/ahundt/clautorun.git

Option 2: Local Development from Source
  cd /path/to/clautorun  # Git clone directory
  {install_command}

Option 3: AIX Multi-Platform Install
  # Installs for all detected CLIs (Claude, Gemini, OpenCode, Codex)
  aix skills install ahundt/clautorun

━━━ TROUBLESHOOTING ━━━

If you're seeing this after 'pip install clautorun':
  The pip package doesn't include plugin files (.claude-plugin/, commands/).
  Use Option 1 (plugin install) or Option 2 (local clone) instead.

Need help? https://github.com/ahundt/clautorun/issues
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
            "-m", "plugins.clautorun.src.clautorun.install",
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
    """Find the clautorun marketplace root directory dynamically.

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
    for parent in [current, *current.parents]:
        marker = parent / ".claude-plugin" / "marketplace.json"
        if marker.exists():
            # Filter out backup/reference directories unless explicitly in their subdirectories
            parent_str = str(parent).lower()
            if "backup" in parent_str or "reference" in parent_str:
                # Only use backup/reference if we're actually running FROM them
                if str(Path(__file__).resolve()).startswith(str(parent)):
                    return parent
                # Otherwise skip and keep searching
                continue
            return parent

    # Strategy 2: Check if this is an editable install - look for direct_url.json
    # Works for: uv pip install -e . (points back to source)
    try:
        package_dir = Path(__file__).parent  # clautorun package directory
        dist_info_dirs = list(package_dir.parent.glob("clautorun*.dist-info"))
        for dist_info in dist_info_dirs:
            direct_url_file = dist_info / "direct_url.json"
            if direct_url_file.exists():
                import json
                data = json.loads(direct_url_file.read_text())
                if "dir_info" in data and "editable" in data["dir_info"]:
                    # This is an editable install - get the source directory
                    source_dir = Path(data["url"].replace("file://", ""))
                    # Source could be workspace root or plugin dir
                    for candidate in [source_dir, source_dir / "plugins" / "clautorun"]:
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

        # Check if CLAUTORUN_DEV_PATH env var is set (for custom locations)
        if "CLAUTORUN_DEV_PATH" in os.environ:
            dev_path = Path(os.environ["CLAUTORUN_DEV_PATH"])
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
                            # Sort to prefer exact "clautorun" name
                            filtered_matches.sort(key=lambda p: (
                                p.name != "clautorun",  # Prefer exact name
                                "-" in p.name,  # Deprioritize names with dashes
                                str(p),  # Alphabetical tiebreaker
                            ))
                            return filtered_matches[0]
                except (PermissionError, OSError):
                    continue

    # Strategy 4: Check common development paths (last resort fallback)
    # Works for: any scenario where source repo exists in standard locations
    # Check CLAUTORUN_DEV_PATH first
    if "CLAUTORUN_DEV_PATH" in os.environ:
        dev_path = Path(os.environ["CLAUTORUN_DEV_PATH"])
        marker = dev_path / ".claude-plugin" / "marketplace.json"
        if marker.exists():
            return dev_path

    # Check exact common paths first (most specific)
    exact_paths = [
        Path.home() / ".claude" / "clautorun" / "plugins" / "clautorun",
        Path.home() / ".claude" / "clautorun",
        Path.home() / "clautorun" / "plugins" / "clautorun",
        Path.home() / "clautorun",
    ]

    for candidate in exact_paths:
        marker = candidate / ".claude-plugin" / "marketplace.json"
        if marker.exists():
            return candidate

    # Search common project locations more broadly (only if exact paths failed)
    search_patterns = [
        Path.home() / ".claude" / "*" / "plugins" / "clautorun",
        Path.home() / "*" / "plugins" / "clautorun",
    ]

    for pattern in search_patterns:
        try:
            # Sort to prefer non-backup, non-reference directories
            # Lower sort key = higher priority, so use negation for preferred attributes
            candidates = sorted(
                pattern.parent.glob(pattern.name),
                key=lambda p: (
                    # Prioritize exact "clautorun" name (not "clautorun-*")
                    p.parent.name != "clautorun" if "clautorun" in str(p.parent) else True,
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
    for ext_name in ["clautorun-workspace", "clautorun"]:
        ext_dir = gemini_home / ext_name
        if ext_dir.exists():
            # Could be at workspace root or in plugins/clautorun/
            for candidate in [ext_dir, ext_dir / "plugins" / "clautorun"]:
                marker = candidate / ".claude-plugin" / "marketplace.json"
                if marker.exists():
                    return candidate

    # Strategy 6: Check Claude plugin cache
    # Works for: claude plugin install (copies to cache)
    claude_cache = Path.home() / ".claude" / "plugins" / "cache" / "clautorun"
    if claude_cache.exists():
        # Find the latest version directory
        version_dirs = sorted(claude_cache.glob("clautorun/*"), reverse=True)
        for version_dir in version_dirs:
            marker = version_dir / ".claude-plugin" / "marketplace.json"
            if marker.exists():
                return version_dir

    # No marketplace root found - provide clear guidance
    raise FileNotFoundError(ErrorFormatter.marketplace_not_found())


def _read_plugin_version(plugin_dir: Path) -> str:
    """Read version from plugin.json manifest.

    Args:
        plugin_dir: Path to plugin directory

    Returns:
        Version string from plugin.json, or "0.8.0" as fallback
    """
    manifest = plugin_dir / ".claude-plugin" / "plugin.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text())
            return data.get("version", "0.8.0")
        except (json.JSONDecodeError, OSError):
            pass
    return "0.8.0"


def _check_hook_conflicts() -> None:
    """Check for plugins with conflicting PreToolUse hooks.

    Warns if hookify or other plugins have PreToolUse hooks that might
    override clautorun's command blocking.
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
            print("   hookify has PreToolUse hooks that may override clautorun's command blocking.")
            print("   If rm/git-reset commands are not being blocked, disable hookify:")
            print("   Edit ~/.claude/settings.json and set:")
            print('   "hookify@claude-code-plugins": false')
            print("   Then restart Claude Code.")

        # Check for other plugins with PreToolUse hooks
        cache_dir = Path.home() / ".claude" / "plugins" / "cache"
        if cache_dir.exists():
            conflicting = []
            for hooks_file in cache_dir.glob("*/*/*/hooks/hooks.json"):
                try:
                    hooks_data = json.loads(hooks_file.read_text())
                    if "PreToolUse" in hooks_data.get("hooks", {}):
                        plugin_name = hooks_file.parts[-4] + "@" + hooks_file.parts[-3]
                        if plugin_name != "clautorun@clautorun" and enabled.get(plugin_name, False):
                            conflicting.append(plugin_name)
                except:
                    continue

            if conflicting:
                print(f"\n⚠️  Other plugins with PreToolUse hooks detected: {', '.join(conflicting)}")
                print("   These may interfere with clautorun's command blocking.")

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
    if not selection or selection == "all":
        return PluginName.all()

    seen: set[str] = set()
    plugins = []
    for name in selection.split(","):
        name = name.strip()
        if not name or name in seen:
            continue
        if not PluginName.validate(name):
            logger.warning(f"Unknown plugin: {name!r} (valid: {', '.join(PluginName.all())})")
            print(f"Unknown plugin: {name!r} (valid: {', '.join(PluginName.all())})")
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
            ErrorFormatter.uv_not_found("pip install -e . && python -m clautorun --install"),
        )

    if not (plugin_dir / "pyproject.toml").exists():
        return CmdResult(False, f"pyproject.toml not found in {plugin_dir}")

    if not (plugin_dir / "uv.lock").exists():
        return CmdResult(False, f"uv.lock not found — run 'uv sync' first")

    if not (plugin_dir / ".venv").exists():
        return CmdResult(False, ".venv not found — run 'uv sync' first")

    return CmdResult(True, "UV environment OK")


def detect_available_clis() -> dict[str, bool]:
    """Detect which AI CLIs are available on the system.

    Returns:
        Dict mapping CLI name to availability: {"claude": bool, "gemini": bool}
    """
    return {
        "claude": shutil.which("claude") is not None,
        "gemini": shutil.which("gemini") is not None,
    }


def determine_target_clis(
    claude_only: bool,
    gemini_only: bool,
    available: dict[str, bool]
) -> list[str]:
    """Determine which CLIs to install for based on flags and availability.

    Args:
        claude_only: If True, include Claude Code in install targets
        gemini_only: If True, include Gemini CLI in install targets
        available: Dict of CLI availability from detect_available_clis()

    Returns:
        List of CLI names to install for (e.g., ["claude", "gemini"])

    Logic:
        - If both flags False (default): install for all available CLIs
        - If both flags True: install for both CLIs (if available)
        - If only claude_only: install only for Claude
        - If only gemini_only: install only for Gemini
    """
    # If both flags are set, install for both
    if claude_only and gemini_only:
        targets = []
        if available["claude"]:
            targets.append("claude")
        if available["gemini"]:
            targets.append("gemini")
        return targets

    # If only claude flag is set
    if claude_only:
        return ["claude"] if available["claude"] else []

    # If only gemini flag is set
    if gemini_only:
        return ["gemini"] if available["gemini"] else []

    # Default: install for all available CLIs
    return [cli for cli, avail in available.items() if avail]


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
        # If the root has a plugins/ directory, the plugin is in plugins/clautorun
        # If the root IS the clautorun directory (e.g. nested), use it directly
        if (marketplace_root / "plugins" / "clautorun").exists():
            plugin_dir = marketplace_root / "plugins" / "clautorun"
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
    pdf_dir = root / "plugins" / "pdf-extractor"
    if not pdf_dir.exists():
        return CmdResult(True, "pdf-extractor not present, skipping")

    return run_cmd(
        ["uv", "pip", "install", "--python", sys.executable, "-q",
         "pdfplumber", "pdfminer.six", "PyPDF2", "markitdown", "tqdm"],
        timeout=120,
    )


def _install_to_cache(plugin_name: str) -> bool:
    """Fallback: copy plugin to ~/.claude/plugins/cache/ and register in JSON.

    Used when `claude plugin install` fails (CI, air-gapped, broken plugin system).

    Args:
        plugin_name: Name of plugin to install

    Returns:
        True if cache install succeeded, False otherwise
    """
    root = find_marketplace_root()
    # find_marketplace_root() returns a plugin directory (e.g., plugins/clautorun/)
    # not the workspace root. To find the requested plugin:
    # 1. If root IS the requested plugin, use it directly
    # 2. Otherwise, check sibling directories (root.parent / plugin_name)
    if root.name == plugin_name and (root / ".claude-plugin").exists():
        plugin_dir = root
    else:
        plugin_dir = root.parent / plugin_name
    if not plugin_dir.exists():
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

    # Substitute ${CLAUDE_PLUGIN_ROOT} in copied files
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
    """Replace ${CLAUDE_PLUGIN_ROOT} with actual path in plugin.json and hooks.json.

    Only needed for cache-installed plugins — Claude Code handles this
    for marketplace-installed plugins.

    Args:
        plugin_dir: Path to plugin directory in cache
    """
    for rel_path in [".claude-plugin/plugin.json", "hooks/hooks.json"]:
        fp = plugin_dir / rel_path
        if not fp.exists():
            continue
        try:
            content = fp.read_text()
            if "${CLAUDE_PLUGIN_ROOT}" in content:
                fp.write_text(content.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_dir)))
        except OSError as e:
            logger.warning(f"Failed to substitute paths in {rel_path}: {e}")


def _install_for_gemini(
    marketplace_root: Path,
    force: bool = False,
) -> tuple[bool, str]:
    """Install each plugin separately for Gemini CLI.

    Note: Gemini treats each plugin as a separate extension, not a workspace.

    Args:
        marketplace_root: Path to marketplace root directory (plugin directory)
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
    # marketplace_root: /Users/user/.claude/clautorun/plugins/clautorun
    # plugins_dir: /Users/user/.claude/clautorun/plugins
    plugins_dir = marketplace_root.parent

    # Find all plugin directories (must have gemini-extension.json)
    plugins_to_install = []
    for plugin_dir in plugins_dir.iterdir():
        if plugin_dir.is_dir() and (plugin_dir / "gemini-extension.json").exists():
            plugins_to_install.append(plugin_dir)

    if not plugins_to_install:
        return (False, "No plugins found with gemini-extension.json")

    print()
    print(f"Installing {len(plugins_to_install)} plugin(s) for Gemini CLI...")

    success_count = 0
    failed_plugins = []

    for plugin_dir in plugins_to_install:
        plugin_name = plugin_dir.name

        # Read gemini-extension.json to get the extension name
        try:
            import json
            with open(plugin_dir / "gemini-extension.json") as f:
                ext_config = json.load(f)
                ext_name = ext_config.get("name", plugin_name)
        except Exception:
            ext_name = plugin_name

        print(f"   Installing {plugin_name} (name: {ext_name})...")

        # Prepare hooks for Gemini: copy gemini-hooks.json → hooks/hooks.json
        # This allows different hook configs for Claude (hooks.json) vs Gemini (gemini-hooks.json)
        gemini_hooks_file = plugin_dir / "hooks" / "gemini-hooks.json"
        hooks_file = plugin_dir / "hooks" / "hooks.json"
        hooks_backup = plugin_dir / "hooks" / "hooks.json.claude-backup"

        if gemini_hooks_file.exists():
            # Backup Claude hooks.json before overwriting
            if hooks_file.exists() and not hooks_backup.exists():
                shutil.copy2(hooks_file, hooks_backup)

            # Copy Gemini hooks into place
            shutil.copy2(gemini_hooks_file, hooks_file)
            print(f"   → Prepared Gemini hooks (backed up Claude hooks to hooks.json.claude-backup)")

        if force:
            run_cmd(["gemini", "extensions", "uninstall", ext_name])

        result = run_cmd(["gemini", "extensions", "install", str(plugin_dir), "--consent"])

        # Restore Claude hooks after Gemini installation
        if hooks_backup.exists():
            shutil.copy2(hooks_backup, hooks_file)
            hooks_backup.unlink()  # Remove backup after restoring
            print(f"   → Restored Claude hooks.json")

        if result.ok or result.has_text("already installed"):
            print(f"   ✓ {ext_name} installed successfully")
            success_count += 1
        else:
            print(f"   ✗ {ext_name} installation failed: {result.output}")
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
        True if clautorun-workspace is installed
    """
    result = run_cmd(["gemini", "extensions", "list"])
    return result.ok and "clautorun-workspace" in result.output


def _verify_conductor_installation() -> bool:
    """Verify Conductor extension installation.

    Returns:
        True if conductor is installed
    """
    result = run_cmd(["gemini", "extensions", "list"])
    return result.ok and "conductor" in result.output


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
    """Install clautorun locally using AIX if available.

    AIX provides unified installation across Claude Code, Gemini CLI,
    OpenCode, and Codex CLI platforms.

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
    print("Installing clautorun via AIX...")
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
        print("   ✓ clautorun installed via AIX")

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
            if "opencode" in verify_result.output:
                print("   • OpenCode")
                installed_platforms.append("opencode")
            if "codex_cli" in verify_result.output:
                print("   • Codex CLI")
                installed_platforms.append("codex")

        # CRITICAL: Verify hooks are registered (essential for clautorun functionality)
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
                        with open(hooks_path) as f:
                            hooks_data = json.load(f)
                        # Check if clautorun hooks are present
                        has_hooks = any("clautorun" in str(hook) for hook in hooks_data.get("hooks", []))
                        if has_hooks:
                            print(f"   ✓ Claude Code hooks registered")
                        else:
                            print(f"   ⚠️  Claude Code hooks may not be registered")
                            hooks_ok = False
                    except Exception:
                        print(f"   ⚠️  Could not verify Claude Code hooks")
                        hooks_ok = False
                else:
                    print(f"   ⚠️  Claude Code hooks file not found")
                    hooks_ok = False

            elif platform == "gemini":
                # Check Gemini CLI hooks
                gemini_config = Path.home() / ".config" / "gemini-cli" / "config.json"
                if gemini_config.exists():
                    import json
                    try:
                        with open(gemini_config) as f:
                            config_data = json.load(f)
                        # Check if clautorun hooks are present
                        has_hooks = any("clautorun" in str(ext) for ext in config_data.get("extensions", []))
                        if has_hooks:
                            print(f"   ✓ Gemini CLI hooks registered")
                        else:
                            print(f"   ⚠️  Gemini CLI hooks may not be registered")
                            hooks_ok = False
                    except Exception:
                        print(f"   ⚠️  Could not verify Gemini CLI hooks")
                        hooks_ok = False
                else:
                    print(f"   ⚠️  Gemini CLI config file not found")
                    hooks_ok = False

        if not hooks_ok:
            print("\n   ⚠️  Hook registration incomplete!")
            print("   SOLUTION: Clautorun has built-in bootstrap that will auto-register")
            print("            hooks on first use. Alternatively, run manually:")
            print("            $ clautorun --install")
            print("\n   Why this matters: Hooks enable PreToolUse/PostToolUse functionality,")
            print("                      which powers file policies, command blocking, etc.")

        return (True, "success")
    else:
        return (False, result.output)


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
    conductor: bool = True,
    use_aix: bool = None,  # NEW: Auto-detect AIX if None (default behavior)
) -> int:
    """Install and enable plugins for Claude Code and/or Gemini CLI.

    CRITICAL: Will auto-detect and use AIX for LOCAL installation if available.
    Does NOT publish to public AIX registry (requires manual user action).

    Args:
        selection: "all" or comma-separated plugin names (e.g., "clautorun,pdf-extractor")
        tool: Also run `uv tool install` for global CLI availability
        force: Force reinstall even if already installed (for dev with same version)
        claude_only: Install only for Claude Code (default: False)
        gemini_only: Install only for Gemini CLI (default: False)
        conductor: Install Conductor extension for Gemini (default: True)
        use_aix: Use AIX for installation (None = auto-detect, True = force use, False = skip)

    Returns:
        Exit code: 0 = success, 1 = failure

    Behavior:
        - Default (no CLI flags): Installs for all available CLIs with maximum capability
        - --claude: Installs only for Claude Code (error if not available)
        - --gemini: Installs only for Gemini CLI (error if not available)
        - --claude --gemini: Installs for both CLIs
        - --no-conductor: Skip Conductor (reduce scope to workspace only)
        - --aix: Force use AIX (fail if not installed)
        - --no-aix: Skip AIX even if installed
        - Continues even if one CLI fails (reports status for each)

    Note:
        Commands and skills work natively via extension manifest.
    """
    # Python version check
    if sys.version_info < (3, 10):
        print(f"Python {sys.version_info.major}.{sys.version_info.minor} detected. "
              f"clautorun requires Python 3.10+.")
        return 1

    # NEW: Auto-detect AIX and use for local installation if available
    # CRITICAL: Only does LOCAL install (aix skills install .), never publishes
    if use_aix is None:
        use_aix = detect_aix_installed()  # Auto-detect by default

    if use_aix and not (claude_only or gemini_only):
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
        target_clis = determine_target_clis(claude_only, gemini_only, available)
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
        return 1

    # Ensure marketplace is added
    try:
        marketplace_root = find_marketplace_root()
    except FileNotFoundError as e:
        print(f"{e}")
        return 1

    # Import version
    try:
        from clautorun import __version__
    except ImportError:
        __version__ = "0.8.0"

    print(f"clautorun v{__version__}")
    print(f"Marketplace root: {marketplace_root}")
    print(f"Target CLIs: {', '.join(target_clis)}")
    print()

    # UV environment check (warning only, not blocker)
    if (marketplace_root / "plugins" / "clautorun").exists():
        plugin_dir = marketplace_root / "plugins" / "clautorun"
    else:
        plugin_dir = marketplace_root

    uv_check = _check_uv_env(plugin_dir)
    if not uv_check.ok:
        logger.warning(f"UV environment: {uv_check.output}")
        print(f"⚠️  {uv_check.output}")
    else:
        logger.info("UV environment OK")

    # Sync clautorun Python dependencies (critical for hooks to work)
    if shutil.which("uv"):
        print("Installing clautorun Python dependencies...")
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

    # Install for Claude Code
    if "claude" in target_clis:
        print()
        print("Adding clautorun marketplace for Claude Code...")
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
        print(f"Installing {len(plugins)} plugin(s) for Claude Code:")

        for name in plugins:
            print(f"   {name}...", end=" ", flush=True)

            # Try update first (faster, preserves settings)
            upd = run_cmd(["claude", "plugin", "update", f"{name}@{MARKETPLACE}"])
            if upd.ok:
                # Enable after update (update doesn't guarantee enabled state)
                enable_result = run_cmd(["claude", "plugin", "enable", f"{name}@{MARKETPLACE}"])
                if enable_result.ok or enable_result.has_text("already"):
                    print("updated")
                    claude_succeeded.append(name)
                    continue
                else:
                    print(f"enable after update failed: {enable_result.output}")
                    claude_failed.append(name)
                    continue

            # Fall back to fresh install
            result = run_cmd(["claude", "plugin", "install", f"{name}@{MARKETPLACE}"])
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
            result = run_cmd(["claude", "plugin", "enable", f"{name}@{MARKETPLACE}"])
            if result.ok or result.has_text("already"):
                print("ok")
                claude_succeeded.append(name)
            else:
                print(f"enable failed: {result.output}")
                claude_failed.append(name)

        claude_success = len(claude_succeeded) == len(plugins)
        all_succeeded = all_succeeded and claude_success

    # Install for Gemini CLI
    if "gemini" in target_clis:
        gemini_success, gemini_msg = _install_for_gemini(marketplace_root, force)
        all_succeeded = all_succeeded and gemini_success

        # Install Conductor if requested and Gemini install succeeded
        if conductor and gemini_success:
            conductor_success, conductor_msg = _install_conductor(force)
            # Note: Conductor failure doesn't affect overall success
            # (it's an optional enhancement)

    # Optional: uv tool install for global CLI
    if tool:
        print()
        print("Installing UV tool...")
        result = run_cmd(["uv", "tool", "install", ".", "--force"], timeout=120)
        if result.ok:
            print("   uv tool: ok")
        else:
            print(f"   uv tool: {result.output}")

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
            print(f"✓ Gemini CLI: Workspace installed (clautorun-workspace@0.8.0)")
            if conductor:
                conductor_ok = _verify_conductor_installation()
                if conductor_ok:
                    print(f"✓ Gemini CLI: Conductor extension installed")
                else:
                    print(f"⚠️  Gemini CLI: Conductor installation failed (optional)")
        else:
            print(f"✗ Gemini CLI: Installation failed")

    print()
    print("Available commands:")
    print("  /cr:*             - clautorun commands (autorun, file policies, plan export, tmux)")
    print("  /pdf-extractor:*  - PDF extraction commands")
    if "gemini" in target_clis and conductor:
        print("  /conductor:*      - Conductor plan mode (Gemini only)")
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
    result = run_cmd(["uv", "tool", "uninstall", "clautorun"])
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
    legacy_dir = Path.home() / ".claude" / "plugins" / "clautorun"
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

    if not claude_ok:
        print()
        print("Install Claude Code first")
        return 1

    # UV environment check
    try:
        marketplace_root = find_marketplace_root()
        plugin_dir = marketplace_root / "plugins" / "clautorun"
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
    for tool_name in ["clautorun", "clautorun-install", "clautorun-interactive"]:
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
            workspace = "clautorun-workspace" in result.output
            print(f"  clautorun-workspace: {'✓ installed' if workspace else '✗ not installed'}")

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

    return 0 if all_ok else 1


# =============================================================================
# Self-Update Mechanism
# =============================================================================


def check_for_updates() -> tuple[bool, str, str]:
    """Check if clautorun update is available using stdlib (no dependencies).

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
        current = get_version("clautorun")
    except PackageNotFoundError:
        return (False, "unknown", "unknown")

    try:
        url = "https://api.github.com/repos/ahundt/clautorun/releases/latest"
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "clautorun-installer")
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read())
            latest = data["tag_name"].lstrip("v")
            # Simple string comparison (semantic versioning)
            return (latest > current, current, latest)
    except (urllib.error.URLError, json.JSONDecodeError, KeyError):
        return (False, current, "unknown")


@dataclass(frozen=True)
class UpdateStrategy:
    """Installation method detection for self-updates.

    Detects how clautorun was installed to choose correct update pathway:
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
        1. AIX (if installed and managing clautorun)
        2. Claude Code plugin (if claude CLI found + clautorun in list)
        3. Gemini CLI plugin (if gemini CLI found + clautorun in list)
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
            if result.ok and "clautorun" in result.output:
                return UpdateStrategy("plugin", "claude")

        if shutil.which("gemini"):
            result = run_cmd(["gemini", "extensions", "list"], timeout=10)
            if result.ok and "clautorun-workspace" in result.output:
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
        return run_cmd(["aix", "skills", "update", "clautorun"], timeout=120)

    elif method == "plugin":
        # Try both CLIs (one will succeed)
        if shutil.which("claude"):
            result = run_cmd(["claude", "plugin", "update", "clautorun"], timeout=60)
            if result.ok:
                return result

        if shutil.which("gemini"):
            result = run_cmd(["gemini", "extensions", "update", "clautorun-workspace"], timeout=60)
            if result.ok:
                return result

        return CmdResult(False, "No plugin CLI found for update")

    elif method == "uv":
        # UV pathway: install + register
        result = run_cmd([
            "uv", "pip", "install", "--upgrade",
            "git+https://github.com/ahundt/clautorun.git"
        ], timeout=120)
        if result.ok:
            # Re-register plugins
            runner = get_python_runner()
            return run_cmd([*runner, "-m", "clautorun", "--install", "--force"], timeout=120)
        return result

    elif method == "pip":
        # Pip pathway: install + register
        result = run_cmd([
            "pip", "install", "--upgrade",
            "git+https://github.com/ahundt/clautorun.git"
        ], timeout=120)
        if result.ok:
            return run_cmd(["python", "-m", "clautorun", "--install", "--force"], timeout=120)
        return result

    else:
        return CmdResult(False, f"Unknown update method: {method}")


# =============================================================================
# Main Function - Dev Workflow
# =============================================================================



# =============================================================================
# CLI Entry Points
# =============================================================================


def _map_legacy_flags(args: list[str]) -> list[str]:
    """Map legacy install.py flags to modern __main__.py flags.

    Args:
        args: sys.argv[1:] from clautorun-install invocation

    Returns:
        Mapped argv for __main__.main()
    """
    if not args or args[0] == "install":
        rest = args[1:] if args else []
        result = ["--install"]
        for flag in rest:
            if flag in ("--force", "-f"):
                result.append("--force-install")
            elif flag == "--tool":
                result.append("--tool")
            # --marketplace, -m: ignored (all is already the default)
        return result
    elif args[0] == "uninstall":
        return ["--uninstall"]
    elif args[0] in ("check", "status"):
        return ["--status"]
    else:
        # Unknown subcommand → default to install
        return ["--install"]


def install_main() -> None:
    """Entry point for clautorun-install command.

    Maps legacy install.py subcommands to modern __main__.py flags.
    Uses argv parameter passing (no sys.argv mutation).
    """
    # Configure logging for CLI use (simple format)
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    mapped_argv = _map_legacy_flags(sys.argv[1:])

    from .__main__ import main
    sys.exit(main(argv=mapped_argv))


if __name__ == "__main__":
    # Configure logging for CLI use
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    sys.exit(install_plugins())
