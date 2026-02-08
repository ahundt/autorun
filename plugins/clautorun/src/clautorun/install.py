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
    clautorun --sync                       # Sync source to cache (dev)
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
    "sync_to_cache",
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
# Discovery Functions
# =============================================================================


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


def _read_plugin_version(plugin_dir: Path) -> str:
    """Read version from plugin.json manifest.

    Args:
        plugin_dir: Path to plugin directory

    Returns:
        Version string from plugin.json, or "0.7.0" as fallback
    """
    manifest = plugin_dir / ".claude-plugin" / "plugin.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text())
            return data.get("version", "0.7.0")
        except (json.JSONDecodeError, OSError):
            pass
    return "0.7.0"


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
        return CmdResult(False, "uv not found in PATH — install from https://github.com/astral-sh/uv")

    if not (plugin_dir / "pyproject.toml").exists():
        return CmdResult(False, f"pyproject.toml not found in {plugin_dir}")

    if not (plugin_dir / "uv.lock").exists():
        return CmdResult(False, f"uv.lock not found — run 'uv sync' first")

    if not (plugin_dir / ".venv").exists():
        return CmdResult(False, ".venv not found — run 'uv sync' first")

    return CmdResult(True, "UV environment OK")


# =============================================================================
# Install Operations
# =============================================================================


def _sync_dependencies() -> CmdResult:
    """Run uv sync --extra claude-code from the clautorun plugin directory.

    Returns:
        CmdResult indicating success/failure
    """
    plugin_dir = find_marketplace_root() / "plugins" / "clautorun"
    return run_cmd(
        ["uv", "sync", "--extra", "claude-code"],
        timeout=120,
        cwd=plugin_dir,
    )


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
    plugin_dir = root / "plugins" / plugin_name
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


# =============================================================================
# Main Function - Installation
# =============================================================================


def install_plugins(
    selection: str = "all",
    *,
    tool: bool = False,
    force: bool = False,
) -> int:
    """Install and enable Claude Code plugins with complete dependency bootstrap.

    Args:
        selection: "all" or comma-separated plugin names (e.g., "clautorun,pdf-extractor")
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
        - Missing UV -> warning only (can still install via marketplace)
        - Cache fallback -> when marketplace install fails
    """
    # Python version check
    if sys.version_info < (3, 10):
        print(f"Python {sys.version_info.major}.{sys.version_info.minor} detected. "
              f"clautorun requires Python 3.10+.")
        return 1

    # Parse and validate plugin selection
    plugins = _parse_selection(selection)
    if not plugins:
        print("No valid plugins specified")
        return 1

    # Verify claude CLI is available
    if not shutil.which("claude"):
        print("claude CLI not found. Install Claude Code first:")
        print("   https://docs.anthropic.com/claude/docs/claude-code")
        return 1

    # Verify ~/.claude/ directory exists
    claude_dir = Path.home() / ".claude"
    if not claude_dir.exists():
        print("~/.claude/ directory not found. Claude Code may not be initialized.")
        print("Run 'claude' once to initialize, then retry.")
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
        __version__ = "0.7.0"

    print(f"clautorun v{__version__}")
    print(f"Marketplace root: {marketplace_root}")
    print()

    # UV environment check (warning only, not blocker)
    plugin_dir = marketplace_root / "plugins" / "clautorun"
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

        # Try update first (faster, preserves settings)
        upd = run_cmd(["claude", "plugin", "update", f"{name}@{MARKETPLACE}"])
        if upd.ok:
            # Enable after update (update doesn't guarantee enabled state)
            enable_result = run_cmd(["claude", "plugin", "enable", f"{name}@{MARKETPLACE}"])
            if enable_result.ok or enable_result.has_text("already"):
                print("updated")
                succeeded.append(name)
                continue
            else:
                print(f"enable after update failed: {enable_result.output}")
                failed.append(name)
                continue

        # Fall back to fresh install
        result = run_cmd(["claude", "plugin", "install", f"{name}@{MARKETPLACE}"])
        if not result.ok and not result.has_text("already"):
            # Marketplace install failed — try cache fallback
            print("marketplace failed, trying cache...", end=" ", flush=True)
            if _install_to_cache(name):
                print("ok (cache)")
                succeeded.append(name)
            else:
                print("cache failed")
                failed.append(name)
            continue

        # Enable (critical: without this, hooks don't run)
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
    print("  /cr:*             - clautorun commands (autorun, file policies, plan export, tmux)")
    print("  /pdf-extractor:*  - PDF extraction commands")
    print()
    print("Run '/help' to see all available commands.")

    return 0 if len(succeeded) == len(plugins) else 1


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

    return 0 if all_ok else 1


# =============================================================================
# Main Function - Dev Workflow
# =============================================================================


def sync_to_cache() -> int:
    """Dev workflow: copy source to Claude Code cache without full reinstall.

    Returns:
        Exit code: 0 = success
    """
    root = find_marketplace_root()
    print("Syncing plugins to cache...")

    for plugin in PluginName.all():
        plugin_dir = root / "plugins" / plugin
        if not plugin_dir.exists():
            print(f"   {plugin}: not found, skipping")
            continue

        print(f"   {plugin}...", end=" ", flush=True)
        if _install_to_cache(plugin):
            print("ok")
        else:
            print("failed")

    print()
    print("Sync complete. Restart Claude Code for changes to take effect.")
    return 0


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
    elif args[0] == "sync":
        return ["--sync"]
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
