#!/usr/bin/env python3

# Copyright 2025 Andrew Hundt <ATHundt@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Clautorun Claude Code plugin installation and management"""
import sys
import json
import shutil
import argparse
import subprocess

# Python 2/3 compatibility
try:
    from pathlib import Path
    from typing import Optional
except ImportError:
    # Python 2.7 compatibility
    try:
        from pathlib2 import Path
    except ImportError:
        # Fallback for systems without pathlib2
        import os as Path
    Optional = type(None)


def check_python_version():
    """
    Check Python version and provide helpful error messages for incompatible versions.

    Returns:
        bool: True if Python version is compatible, False otherwise
    """
    # Check for Python 2.x - critical error
    if sys.version_info[0] < 3:
        print("=" * 70)
        print("ERROR: clautorun requires Python 3.10 or higher")
        print("=" * 70)
        print()
        print("You are using Python {}.{} which is incompatible.".format(
            sys.version_info[0], sys.version_info[1]))
        print()
        print("SOLUTIONS:")
        print("1. Use UV package manager for proper Python management (RECOMMENDED):")
        print("   # Check if UV is already installed:")
        print("   uv --version")
        print("   # If UV is not installed, install it:")
        print("   curl -LsSf https://astral.sh/uv/install.sh | sh")
        print("   # Create virtual environment and install dependencies:")
        print("   uv venv")
        print("   source .venv/bin/activate")
        print("   uv sync --extra claude-code")
        print("   # Install the plugin:")
        print("   python3 src/clautorun/install.py install")
        print()
        print("2. Use python3 explicitly:")
        print("   python3 src/clautorun/install.py install")
        print()
        print("3. Activate your UV virtual environment:")
        print("   source .venv/bin/activate")
        print("   python3 src/clautorun/install.py install")
        print()
        print("4. Update your system default (if you have admin rights):")
        print("   ln -sf /usr/bin/python3 /usr/local/bin/python")
        print()
        print("=" * 70)
        return False

    # Check for Python 3.0-3.9 - warning but allow usage
    if sys.version_info < (3, 10):
        print("=" * 70)
        print("WARNING: Python 3.10+ recommended")
        print("=" * 70)
        print()
        print("You are using Python {}.{}.{}.".format(
            sys.version_info[0], sys.version_info[1], sys.version_info[2]))
        print("clautorun requires Python 3.10+ for full compatibility.")
        print()
        print("RECOMMENDED SOLUTIONS:")
        print("1. Use UV with Python 3.10+ (RECOMMENDED):")
        print("   uv venv --python 3.10")
        print("   source .venv/bin/activate")
        print("   uv sync --extra claude-code")
        print("   python src/clautorun/install.py install")
        print()
        print("2. Install Python 3.10+ and activate virtual environment:")
        print("   python3.10 -m venv .venv")
        print("   source .venv/bin/activate")
        print("   python3 src/clautorun/install.py install")
        print()
        print("=" * 70)

    return True


class ClautorunInstaller:
    """Manages Claude Code plugin installation for clautorun"""

    def __init__(self):
        self.home_dir = Path.home()
        self.claude_dir = self.home_dir / ".claude"
        self.plugins_dir = self.claude_dir / "plugins"
        self.plugin_name = "clautorun"
        self.plugin_install_dir = self.plugins_dir / self.plugin_name

        # Get the location of the clautorun plugin directory
        # install.py is at: ~/.claude/clautorun/plugins/clautorun/src/clautorun/install.py
        # So parent.parent.parent = ~/.claude/clautorun/plugins/clautorun/ (plugin dir)
        try:
            import clautorun
            # If installed as package, find the plugin directory
            self.package_dir = Path(clautorun.__file__).parent.parent.parent
        except ImportError:
            # Fallback for development/relative imports
            self.package_dir = Path(__file__).parent.parent.parent

        self.plugin_source_dir = self.package_dir
        self.plugin_manifest = self.package_dir / ".claude-plugin" / "plugin.json"

        # Marketplace root is the repository root containing plugins/ subdirectory
        # ~/.claude/clautorun/plugins/clautorun/ → ~/.claude/clautorun/
        self.marketplace_root = self.package_dir.parent.parent

        # Cache directory where Claude Code actually installs plugins
        self.cache_dir = self.plugins_dir / "cache" / "clautorun" / self.plugin_name

    def detect_claude_code_installation(self) -> bool:
        """Detect if Claude Code is installed"""
        return self.claude_dir.exists()

    def check_uv_environment(self) -> bool:
        """Check if UV is available and properly configured"""
        try:
            # Check if UV is available
            uv_result = subprocess.run(
                ["uv", "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )

            if uv_result.returncode != 0:
                print("❌ UV is not installed or not in PATH")
                print("   Please install UV: https://github.com/astral-sh/uv")
                return False

            print(f"✅ UV detected: {uv_result.stdout.strip()}")

            # Check if we're in a UV-managed project
            uv_toml = self.package_dir / "pyproject.toml"
            uv_lock = self.package_dir / "uv.lock"

            if not uv_toml.exists():
                print("⚠️  Not in a UV project (no pyproject.toml found)")
                return False

            if not uv_lock.exists():
                print("⚠️  UV lock file not found, run 'uv sync' first")
                return False

            # Check if virtual environment exists
            venv_dir = self.package_dir / ".venv"
            if not venv_dir.exists():
                print("⚠️  UV virtual environment not found")
                print("   Run: uv sync")
                return False

            print("✅ UV environment is properly configured")
            return True

        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError) as e:
            print(f"❌ UV environment check failed: {e}")
            return False

    def ensure_dependencies(self) -> bool:
        """Ensure dependencies are installed using UV"""
        try:
            print("🔄 Checking dependencies with UV...")

            # Run uv sync to ensure dependencies are up to date
            sync_result = subprocess.run(
                ["uv", "sync", "--extra", "claude-code"],
                cwd=self.package_dir,
                capture_output=True,
                text=True,
                timeout=60
            )

            if sync_result.returncode != 0:
                print(f"⚠️  UV sync failed: {sync_result.stderr}")
                return False

            # Install clautorun package in editable mode in the UV venv
            print("🔄 Installing clautorun package in UV environment...")
            install_result = subprocess.run(
                ["uv", "pip", "install", "-e", "."],
                cwd=self.package_dir,
                capture_output=True,
                text=True,
                timeout=60
            )

            if install_result.returncode == 0:
                print("✅ Dependencies synchronized with UV")
                print("✅ clautorun package installed in UV environment")
                return True
            else:
                print(f"⚠️  Package installation failed: {install_result.stderr}")
                return False

        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError) as e:
            print(f"❌ Dependency synchronization failed: {e}")
            return False

    def create_directories(self) -> bool:
        """Create necessary directories if they don't exist"""
        try:
            self.plugins_dir.mkdir(parents=True, exist_ok=True)
            return True
        except (OSError, PermissionError) as e:
            print(f"❌ Failed to create directories: {e}")
            return False

    def backup_existing_plugin(self) -> Optional[Path]:
        """Backup existing plugin if it exists"""
        if not self.plugin_install_dir.exists():
            return None

        backup_path = self.plugin_install_dir.with_suffix('.backup')
        try:
            if backup_path.exists():
                # Remove old backup
                shutil.rmtree(backup_path)

            # Create backup of existing plugin
            shutil.copytree(self.plugin_install_dir, backup_path)
            print(f"📦 Backed up existing plugin to {backup_path}")
            return backup_path
        except (OSError, PermissionError) as e:
            print(f"⚠️  Warning: Could not backup existing plugin: {e}")
            return None

    def substitute_plugin_paths(self) -> bool:
        """Substitute ${CLAUDE_PLUGIN_ROOT} with actual path in plugin.json"""
        try:
            plugin_json_path = self.plugin_source_dir / ".claude-plugin" / "plugin.json"
            if not plugin_json_path.exists():
                print(f"❌ Plugin manifest not found: {plugin_json_path}")
                return False

            # Read the plugin.json file
            with open(plugin_json_path, 'r') as f:
                content = f.read()

            # Substitute ${CLAUDE_PLUGIN_ROOT} with actual path
            content = content.replace("${CLAUDE_PLUGIN_ROOT}", str(self.plugin_source_dir))

            # Write the modified content back
            with open(plugin_json_path, 'w') as f:
                f.write(content)

            print(f"✅ Substituted plugin paths in {plugin_json_path}")
            return True

        except (OSError, PermissionError) as e:
            print(f"❌ Failed to substitute plugin paths: {e}")
            return False

    def validate_plugin_structure(self) -> bool:
        """Check if plugin has proper structure"""
        if not self.plugin_manifest.exists():
            print(f"❌ Plugin manifest not found: {self.plugin_manifest}")
            return False

        commands_dir = self.plugin_source_dir / "commands"
        if not commands_dir.exists():
            print(f"❌ Commands directory not found: {commands_dir}")
            return False

        # Ensure marketplace.json exists in .claude-plugin directory
        marketplace_src = self.plugin_source_dir / "marketplace.json"
        marketplace_dst = self.plugin_source_dir / ".claude-plugin" / "marketplace.json"

        if marketplace_src.exists() and not marketplace_dst.exists():
            try:
                shutil.copy2(marketplace_src, marketplace_dst)
                print("✅ Copied marketplace.json to .claude-plugin directory")
            except (OSError, PermissionError) as e:
                print(f"⚠️  Warning: Could not copy marketplace.json: {e}")

        # Substitute plugin paths
        if not self.substitute_plugin_paths():
            return False

        return True

    def is_plugin_installed(self) -> bool:
        """Check if plugin is properly installed (marketplace or manual)"""
        # Check method 1: Commands are available via Claude Code (best check for marketplace)
        try:
            test_result = subprocess.run(
                ["sh", "-c", "echo '/help' | claude -p --output-format json 2>/dev/null | jq '.[] | select(.type==\"system\") | .slash_commands | map(select(. | contains(\"afs\")))' 2>/dev/null"],
                capture_output=True,
                text=True,
                timeout=10
            )

            # If commands are available, plugin is installed and working
            if test_result.returncode == 0 and test_result.stdout.strip() not in ('[]', ''):
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
            pass

        # Check method 2: Marketplace is configured (works for both local and remote)
        try:
            marketplace_result = subprocess.run(
                ["claude", "plugin", "marketplace", "list"],
                capture_output=True,
                text=True,
                timeout=10
            )

            if marketplace_result.returncode == 0 and "clautorun" in marketplace_result.stdout:
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
            pass

        # Check method 3: Manual installation in ~/.claude/plugins
        if not self.plugin_install_dir.exists():
            return False

        installed_manifest = self.plugin_install_dir / ".claude-plugin" / "plugin.json"
        if not installed_manifest.exists():
            return False

        # Compare manifests to ensure it's our plugin
        try:
            with open(self.plugin_manifest) as src, open(installed_manifest) as dst:
                src_data = json.load(src)
                dst_data = json.load(dst)
                return src_data.get("name") == dst_data.get("name")
        except (json.JSONDecodeError, OSError):
            return False

    def try_claude_plugin_install(self) -> bool:
        """Try to install/update using Claude Code's plugin system first"""
        try:
            # Check if we're in a Claude Code session by trying to run claude command
            result = subprocess.run(
                ["claude", "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                print("🤖 Claude Code detected, attempting plugin installation...")

                # Check if marketplace already exists
                list_result = subprocess.run(
                    ["claude", "plugin", "marketplace", "list"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )

                marketplace_available = False
                marketplace_needs_refresh = False

                if list_result.returncode == 0 and "clautorun" in list_result.stdout:
                    print("✅ clautorun marketplace already configured")
                    marketplace_available = True
                    marketplace_needs_refresh = True  # Refresh to pick up latest changes
                else:
                    # Add local marketplace from REPO ROOT (not plugin directory)
                    # Marketplace root: ~/.claude/clautorun (contains plugins/ subdirectory)
                    print(f"🔄 Adding local marketplace from: {self.marketplace_root}")
                    marketplace_result = subprocess.run(
                        ["claude", "plugin", "marketplace", "add", str(self.marketplace_root)],
                        capture_output=True,
                        text=True,
                        timeout=15
                    )

                    if marketplace_result.returncode == 0:
                        print("✅ Local marketplace added successfully")
                        marketplace_available = True
                    else:
                        print(f"⚠️  Failed to add marketplace: {marketplace_result.stderr}")

                # Refresh marketplace to pick up latest plugin changes
                if marketplace_available and marketplace_needs_refresh:
                    print("🔄 Refreshing marketplace to pick up latest changes...")
                    refresh_result = subprocess.run(
                        ["claude", "plugin", "marketplace", "update", "clautorun"],
                        capture_output=True,
                        text=True,
                        timeout=15
                    )
                    if refresh_result.returncode == 0:
                        print("✅ Marketplace refreshed successfully")
                    else:
                        print(f"ℹ️  Marketplace refresh: {refresh_result.stderr.strip()}")

                # Try to update first (if already installed), then install
                if marketplace_available:
                    # First try update (faster, preserves settings)
                    print("🔄 Attempting plugin update...")
                    update_result = subprocess.run(
                        ["claude", "plugin", "update", "clautorun@clautorun"],
                        capture_output=True,
                        text=True,
                        timeout=30
                    )

                    if update_result.returncode == 0:
                        print("✅ Successfully updated plugin from local marketplace")
                        return True
                    else:
                        print(f"ℹ️  Update not available (may not be installed yet): {update_result.stderr.strip()}")

                    # Try fresh install
                    print("🔄 Attempting fresh plugin install...")
                    install_result = subprocess.run(
                        ["claude", "plugin", "install", "clautorun@clautorun"],
                        capture_output=True,
                        text=True,
                        timeout=30
                    )

                    if install_result.returncode == 0:
                        print("✅ Successfully installed via local marketplace")
                        return True
                    else:
                        print(f"⚠️  Local marketplace installation failed: {install_result.stderr}")

                        # List available marketplaces for debugging
                        list_result = subprocess.run(
                            ["claude", "plugin", "marketplace", "list"],
                            capture_output=True,
                            text=True,
                            timeout=10
                        )
                        if list_result.returncode == 0:
                            print(f"📋 Available marketplaces:\n{list_result.stdout}")

                # Note: GitHub direct URL installation is not supported by Claude Code
                # Plugin installation requires the marketplace to be added first via:
                # claude plugin marketplace add https://github.com/ahundt/clautorun.git
                # Then install via: claude plugin install clautorun@<marketplace-name>

            return False

        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError) as e:
            print(f"ℹ️  Claude Code plugin system not available: {e}")
            return False

    def install_plugin(self) -> bool:
        """Install plugin to Claude Code plugins directory"""
        if not self.validate_plugin_structure():
            return False

        # First, try Claude Code's plugin system (preferred)
        if self.try_claude_plugin_install():
            return True

        print("🔄 Falling back to manual cache installation...")

        # Manual fallback: install directly to the cache directory
        # This ensures Claude Code will find and use the plugin
        return self.install_to_cache()

    def install_to_cache(self) -> bool:
        """Install plugin directly to Claude Code's cache directory"""
        try:
            # Read version from plugin.json
            version = "0.6.1"  # Default
            try:
                with open(self.plugin_manifest) as f:
                    data = json.load(f)
                    version = data.get("version", version)
            except (json.JSONDecodeError, OSError):
                pass

            # Target: ~/.claude/plugins/cache/clautorun/clautorun/<version>/
            cache_version_dir = self.cache_dir / version

            # Create parent directories
            cache_version_dir.parent.mkdir(parents=True, exist_ok=True)

            # Backup existing cache if present
            if cache_version_dir.exists():
                backup_path = cache_version_dir.with_suffix('.backup')
                if backup_path.exists():
                    shutil.rmtree(backup_path)
                print(f"📦 Backing up existing cache to: {backup_path}")
                shutil.move(str(cache_version_dir), str(backup_path))

            # Copy plugin to cache
            shutil.copytree(
                self.plugin_source_dir,
                cache_version_dir,
                ignore=shutil.ignore_patterns('.git', '__pycache__', '*.pyc', '.coverage', '.venv')
            )

            print(f"✅ Installed plugin to cache: {cache_version_dir}")

            # Verify critical directories were copied (Unix glob * doesn't match hidden dirs)
            claude_plugin_dir = cache_version_dir / ".claude-plugin"
            hooks_dir = cache_version_dir / "hooks"

            if not claude_plugin_dir.exists():
                print("⚠️  WARNING: .claude-plugin/ was not copied - copying now...")
                shutil.copytree(
                    self.plugin_source_dir / ".claude-plugin",
                    claude_plugin_dir
                )
                print("✅ Copied .claude-plugin/ directory")

            if not hooks_dir.exists():
                print("⚠️  WARNING: hooks/ was not copied - copying now...")
                shutil.copytree(
                    self.plugin_source_dir / "hooks",
                    hooks_dir
                )
                print("✅ Copied hooks/ directory")

            # Update installed_plugins.json to register the plugin
            if self.update_installed_plugins_json(cache_version_dir, version):
                print("✅ Registered plugin in installed_plugins.json")
            else:
                print("⚠️  Could not update installed_plugins.json (plugin may not be recognized)")

            return True

        except (OSError, PermissionError) as e:
            print(f"❌ Failed to install to cache: {e}")
            return False

    def update_installed_plugins_json(self, install_path: Path, version: str) -> bool:
        """Update installed_plugins.json to register the manually installed plugin"""
        try:
            installed_plugins_file = self.plugins_dir / "installed_plugins.json"

            # Load existing or create new
            if installed_plugins_file.exists():
                with open(installed_plugins_file) as f:
                    data = json.load(f)
            else:
                data = {"version": 2, "plugins": {}}

            # Get current timestamp
            from datetime import datetime
            timestamp = datetime.utcnow().isoformat() + "Z"

            # Add/update clautorun entry
            plugin_key = "clautorun@clautorun"
            data["plugins"][plugin_key] = [{
                "scope": "user",
                "installPath": str(install_path),
                "version": version,
                "installedAt": timestamp,
                "lastUpdated": timestamp,
                "gitCommitSha": "manual-install"
            }]

            # Write back
            with open(installed_plugins_file, 'w') as f:
                json.dump(data, f, indent=2)

            return True

        except (json.JSONDecodeError, OSError, PermissionError) as e:
            print(f"⚠️  Could not update installed_plugins.json: {e}")
            return False

    def remove_plugin(self) -> bool:
        """Remove the plugin from both cache and legacy manual install locations"""
        removed_any = False

        # Remove from cache (current location)
        if self.cache_dir.exists():
            try:
                shutil.rmtree(self.cache_dir)
                print(f"✅ Removed plugin from cache: {self.cache_dir}")
                removed_any = True
            except (OSError, PermissionError) as e:
                print(f"❌ Failed to remove from cache: {e}")

        # Remove legacy manual install location if it exists
        if self.plugin_install_dir.exists():
            try:
                shutil.rmtree(self.plugin_install_dir)
                print(f"✅ Removed legacy plugin directory: {self.plugin_install_dir}")
                removed_any = True
            except (OSError, PermissionError) as e:
                print(f"❌ Failed to remove legacy directory: {e}")

        if not removed_any:
            print("ℹ️  Plugin is not installed in any location")

        return True

    def sync_to_cache(self) -> bool:
        """
        Sync source to cache without going through plugin system.

        This is useful for development when you want to quickly test changes
        without restarting Claude Code or running full plugin commands.

        Usage: uv run clautorun sync
        """
        print("🔄 Syncing source to cache...")
        return self.install_to_cache()

    def verify_installation(self) -> bool:
        """Verify that the plugin is properly installed (marketplace or manual)"""
        # Check method 1: Marketplace installation - commands are available
        try:
            marketplace_result = subprocess.run(
                ["claude", "plugin", "marketplace", "list"],
                capture_output=True,
                text=True,
                timeout=10
            )

            if marketplace_result.returncode == 0 and "clautorun" in marketplace_result.stdout:
                # Marketplace is configured - check if commands work
                test_result = subprocess.run(
                    ["sh", "-c", "echo '/help' | claude -p --output-format json | jq '.[] | select(.type==\"system\") | .slash_commands | map(select(. | contains(\"afs\")))' 2>/dev/null"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )

                if test_result.returncode == 0 and test_result.stdout.strip() not in ('[]', ''):
                    return True  # Marketplace installation is working
        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
            pass

        # Check method 2: Manual installation - verify structure
        if self.plugin_install_dir.exists():
            installed_manifest = self.plugin_install_dir / ".claude-plugin" / "plugin.json"
            commands_dir = self.plugin_install_dir / "commands"
            main_script = self.plugin_install_dir / "src" / "clautorun" / "main.py"

            if installed_manifest.exists() and commands_dir.exists() and main_script.exists():
                # Verify it's our plugin by comparing manifest
                try:
                    with open(installed_manifest) as f:
                        data = json.load(f)
                        if data.get("name") == "clautorun":
                            return True  # Manual installation structure is valid
                except (json.JSONDecodeError, OSError):
                    pass

        return False

    def install_uv_tool(self) -> bool:
        """Install clautorun-interactive as global UV tool"""
        try:
            print("🔧 Installing clautorun-interactive as global UV tool...")

            # Install as UV tool
            result = subprocess.run(
                ["uv", "tool", "install", ".", "--force"],
                cwd=self.package_dir,
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode == 0:
                print("✅ UV tool installed successfully")
                print("   Available commands: clautorun-interactive, clautorun-install")
                return True
            else:
                print(f"⚠️  UV tool installation failed: {result.stderr}")
                return False

        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError) as e:
            print(f"❌ UV tool installation failed: {e}")
            return False

    def install(self, force: bool = False) -> bool:
        """Install the Claude Code plugin"""
        print("🚀 Installing clautorun Claude Code plugin...")

        # Prerequisite checks
        if not self.check_uv_environment():
            print("❌ UV environment check failed")
            print("   Please ensure UV is installed and run 'uv sync --extra claude-code'")
            return False

        if not self.ensure_dependencies():
            print("❌ Failed to synchronize dependencies")
            return False

        if not self.detect_claude_code_installation():
            print("❌ Claude Code installation not detected")
            print(f"   Expected directory: {self.claude_dir}")
            return False

        if force and self.plugin_install_dir.exists():
            print("🔄 Force mode: removing existing installation")
            if not self.remove_plugin():
                return False

        # Primary goal: Install the plugin
        success = self.install_plugin()

        if success:
            print("✅ Installation completed successfully!")
            print(f"   Plugin installed at: {self.plugin_install_dir}")
            print("   You can now use: /afs, /afa, /afj, /afst, /autorun, /autoproc")
            print("   Run tests with: uv run pytest tests/")

            # Secondary: Install UV tool for interactive mode (optional, best-effort)
            print("\n🔧 Installing interactive mode tool (optional)...")
            self.install_uv_tool()  # Don't fail overall install if this fails

        return success

    def try_claude_plugin_uninstall(self) -> bool:
        """Try to uninstall using Claude Code's plugin system first"""
        try:
            # Check if we're in a Claude Code session
            result = subprocess.run(
                ["claude", "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                print("🤖 Claude Code detected, attempting plugin uninstall...")

                # Try to uninstall using Claude Code
                uninstall_result = subprocess.run(
                    ["claude", "plugin", "uninstall", "clautorun"],
                    capture_output=True,
                    text=True,
                    timeout=15
                )

                if uninstall_result.returncode == 0:
                    print("✅ Successfully uninstalled via Claude Code plugin system")
                    return True
                else:
                    print(f"⚠️  Claude Code uninstall failed: {uninstall_result.stderr}")

            return False

        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError) as e:
            print(f"ℹ️  Claude Code plugin system not available: {e}")
            return False

    def uninstall_uv_tool(self) -> bool:
        """Uninstall clautorun UV tools"""
        try:
            print("🔧 Uninstalling clautorun UV tools...")

            result = subprocess.run(
                ["uv", "tool", "uninstall", "clautorun"],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                print("✅ UV tools uninstalled successfully")
                return True
            else:
                # Check if it's just not installed
                if "not installed" in result.stderr.lower() or "not found" in result.stderr.lower():
                    print("ℹ️  UV tools were not installed")
                    return True
                print(f"⚠️  UV tool uninstall failed: {result.stderr}")
                return False

        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError) as e:
            print(f"⚠️  UV tool uninstall failed: {e}")
            return False

    def uninstall(self, force: bool = False) -> bool:
        """Uninstall the Claude Code plugin and UV tools"""
        print("🗑️  Uninstalling clautorun...")
        print()

        # Uninstall UV tools
        uv_success = self.uninstall_uv_tool()

        # Uninstall Claude Code plugin
        plugin_success = self.try_claude_plugin_uninstall()

        # Summary
        print("\n" + "=" * 70)
        if plugin_success and uv_success:
            print("✅ Complete uninstallation successful!")
        elif plugin_success:
            print("✅ Plugin uninstalled (UV tools already removed or not installed)")
        elif uv_success:
            print("✅ UV tools uninstalled (plugin already removed or not installed)")
        else:
            print("⚠️  Some components may still be installed")
            print("   Check manually: claude plugin marketplace list")
            print("   Check manually: uv tool list")

        print("=" * 70)
        return plugin_success or uv_success

    def check(self) -> bool:
        """Check installation status for both plugin and UV tools"""
        print("🔍 Checking clautorun installation...")
        print()

        all_good = True

        # Check UV environment
        print("1. UV Environment:")
        if self.check_uv_environment():
            print("   ✅ UV environment is properly configured")
        else:
            print("   ⚠️  UV environment issues detected")
            print("   Run 'uv sync --extra claude-code' to fix")
            all_good = False

        # Check UV tool installation
        print("\n2. UV Tools (Interactive Mode):")
        try:
            result = subprocess.run(
                ["which", "clautorun-interactive"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                print(f"   ✅ clautorun-interactive: {result.stdout.strip()}")
            else:
                print("   ❌ clautorun-interactive not found")
                print("   Run: uv tool install . (from clautorun directory)")
                all_good = False

            # Check for clautorun-install
            result2 = subprocess.run(
                ["which", "clautorun-install"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result2.returncode == 0:
                print(f"   ✅ clautorun-install: {result2.stdout.strip()}")
            else:
                print("   ❌ clautorun-install not found")
                all_good = False

        except (subprocess.TimeoutExpired, FileNotFoundError):
            print("   ❌ UV tools not installed")
            all_good = False

        # Check Claude Code plugin
        print("\n3. Claude Code Plugin:")
        try:
            result = subprocess.run(
                ["claude", "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                print("   ❌ Claude Code not accessible")
                all_good = False
            else:
                print(f"   ✅ Claude Code: {result.stdout.strip()}")

                # Check marketplace
                marketplace_result = subprocess.run(
                    ["claude", "plugin", "marketplace", "list"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )

                if marketplace_result.returncode == 0:
                    if "clautorun" in marketplace_result.stdout:
                        print("   ✅ clautorun marketplace configured")

                        # Verify commands are available
                        if self.verify_installation():
                            print("   ✅ Plugin commands available")
                        else:
                            print("   ⚠️  Plugin may need reinstallation")
                            all_good = False
                    else:
                        print("   ❌ clautorun marketplace not configured")
                        print("   Run: claude plugin marketplace add .")
                        all_good = False
                else:
                    print("   ⚠️  Cannot check marketplace")
                    all_good = False

        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError) as e:
            print(f"   ❌ Claude Code check failed: {e}")
            all_good = False

        print()
        if all_good:
            print("✅ All components are properly installed")
        else:
            print("⚠️  Some components need attention")
            print("   Run: clautorun-install install --force")

        return all_good


class MarketplaceInstaller:
    """Installs the full clautorun marketplace (all 3 plugins)"""

    def __init__(self):
        self.home_dir = Path.home()
        self.claude_dir = self.home_dir / ".claude"
        self.plugins_dir = self.claude_dir / "plugins"

        # Get the location of the clautorun plugin directory
        # install.py is at: ~/.claude/clautorun/plugins/clautorun/src/clautorun/install.py
        try:
            import clautorun
            self.package_dir = Path(clautorun.__file__).parent.parent.parent
        except ImportError:
            self.package_dir = Path(__file__).parent.parent.parent

        # Marketplace root is the repository root containing plugins/ subdirectory
        self.marketplace_root = self.package_dir.parent.parent
        self.plugins_directory = self.marketplace_root / "plugins"

        # Plugins to install from the marketplace
        self.plugins = ["clautorun", "plan-export", "pdf-extractor"]

    def install_marketplace(self) -> bool:
        """Install the full marketplace with all plugins"""
        print(f"📦 clautorun-marketplace v0.6.1")
        print(f"📍 Marketplace root: {self.marketplace_root}")
        print(f"📍 Plugins directory: {self.plugins_directory}")
        print()

        # Step 1: Add the marketplace root (where .claude-plugin/marketplace.json is) as a marketplace
        print(f"🔧 Adding clautorun marketplace...")
        try:
            result = subprocess.run(
                ["claude", "plugin", "marketplace", "add", str(self.marketplace_root)],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                print(f"   ✅ Added clautorun marketplace")
            else:
                # Marketplace might already exist, that's ok
                if "already" in result.stderr.lower() or "exists" in result.stderr.lower():
                    print(f"   ℹ️  clautorun marketplace already exists")
                else:
                    print(f"   ⚠️  Marketplace add: {result.stderr.strip()}")
        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError) as e:
            print(f"   ⚠️  Could not add marketplace: {e}")
            # Continue anyway - might still work

        print()

        # Step 2: Install each plugin from the marketplace
        success_count = 0
        failed = []

        for plugin_name in self.plugins:
            print(f"🔧 Installing {plugin_name}...")

            # Install plugin from marketplace
            try:
                result = subprocess.run(
                    ["claude", "plugin", "install", f"{plugin_name}@clautorun"],
                    capture_output=True,
                    text=True,
                    timeout=60
                )

                if result.returncode == 0:
                    print(f"   ✅ Installed {plugin_name} plugin")
                    success_count += 1
                else:
                    # Plugin might already be installed
                    if "already" in result.stderr.lower() or "exists" in result.stderr.lower():
                        print(f"   ℹ️  {plugin_name} plugin already installed")
                        success_count += 1
                    else:
                        print(f"   ❌ Failed to install {plugin_name}: {result.stderr.strip()}")
                        failed.append(plugin_name)
            except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError) as e:
                print(f"   ❌ Installation failed for {plugin_name}: {e}")
                failed.append(plugin_name)

            print()

        # Summary
        print("=" * 60)
        if success_count == len(self.plugins):
            print(f"✅ Successfully installed all {success_count} plugins!")
        else:
            print(f"⚠️  Installed {success_count}/{len(self.plugins)} plugins")

        if failed:
            print(f"❌ Failed plugins: {', '.join(failed)}")

        print()
        print("Available commands:")
        print("  /cr:*             - clautorun commands (autorun, file policies, tmux)")
        print("  /plan-export:*    - plan export commands")
        print("  /pdf-extractor:*  - PDF extraction commands")
        print()
        print("Run '/help' to see all available commands.")

        return success_count == len(self.plugins)


def main():
    """Main installation CLI"""
    # Check Python version compatibility first
    if not check_python_version():
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Manage clautorun Claude Code plugin and UV tool installation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  clautorun-install install          # Install plugin and UV tools
  clautorun-install install --force  # Force reinstall both
  clautorun-install uninstall        # Remove plugin and UV tools
  clautorun-install check            # Check installation status
  clautorun-install sync             # Sync source to cache (dev workflow)

Installation includes:
  1. Claude Code plugin (via marketplace or cache fallback)
     - Provides: /afs, /afa, /afj, /afst, /autorun, /autoproc, /cr:* commands
  2. UV global tools
     - clautorun-interactive: Interactive command processor
     - clautorun-install: This installer tool

Development workflow:
  1. Edit source files in ~/.claude/clautorun/plugins/clautorun/
  2. Run: clautorun-install sync    # Sync to cache
  3. Restart Claude Code to pick up changes

  With UV environment:
  source .venv/bin/activate
  clautorun-install install
        """
    )

    parser.add_argument(
        "action",
        nargs="?",
        default="install",
        choices=["install", "uninstall", "check", "status", "sync"],
        help="Action to perform (default: install)"
    )

    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Force action (overwrite existing files)"
    )

    parser.add_argument(
        "--marketplace", "-m",
        action="store_true",
        help="Install the full marketplace (all 3 plugins: clautorun, plan-export, pdf-extractor)"
    )

    args = parser.parse_args()

    # Use marketplace installer if --marketplace flag is set
    if args.marketplace and args.action == "install":
        marketplace_installer = MarketplaceInstaller()
        success = marketplace_installer.install_marketplace()
        sys.exit(0 if success else 1)

    installer = ClautorunInstaller()

    if args.action == "install":
        success = installer.install(force=args.force)
    elif args.action == "uninstall":
        success = installer.uninstall(force=args.force)
    elif args.action == "check":
        success = installer.check()
    elif args.action == "status":
        success = installer.check()
    elif args.action == "sync":
        success = installer.sync_to_cache()
    else:
        parser.print_help()
        success = False

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()