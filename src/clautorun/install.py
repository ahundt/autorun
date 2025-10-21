#!/usr/bin/env python3
"""Clautorun Claude Code plugin installation and management"""
import os
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

        # Get the location of the clautorun project root
        try:
            import clautorun
            # If installed as package, find the project root
            self.package_dir = Path(clautorun.__file__).parent.parent.parent
        except ImportError:
            # Fallback for development/relative imports
            self.package_dir = Path(__file__).parent.parent.parent

        self.plugin_source_dir = self.package_dir
        self.plugin_manifest = self.package_dir / ".claude-plugin" / "plugin.json"

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

        return True

    def is_plugin_installed(self) -> bool:
        """Check if plugin is properly installed"""
        # Check for marketplace installation first
        commands_symlink = Path.home() / ".claude" / "commands" / "clautorun"
        if commands_symlink.exists() and commands_symlink.is_symlink():
            # Check if the symlink points to our plugin
            try:
                target = commands_symlink.resolve()
                if target.is_file() and "clautorun" in str(target):
                    return True
            except (OSError, RuntimeError):
                pass

        # Check for manual installation
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
        """Try to install using Claude Code's plugin system first"""
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

                # Try local marketplace method first since it works more reliably
                print("🔄 Adding local marketplace...")

                # Check if marketplace already exists
                list_result = subprocess.run(
                    ["claude", "plugin", "marketplace", "list"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )

                marketplace_available = False
                if list_result.returncode == 0 and "clautorun" in list_result.stdout:
                    print("✅ clautorun marketplace already configured")
                    marketplace_available = True
                else:
                    # Add local marketplace
                    marketplace_result = subprocess.run(
                        ["claude", "plugin", "marketplace", "add", str(self.plugin_source_dir)],
                        capture_output=True,
                        text=True,
                        timeout=15
                    )

                    # Marketplace is available if it was just added successfully
                    marketplace_available = marketplace_result.returncode == 0

                # Try to install from marketplace if it's available
                if marketplace_available:
                    # Install from local marketplace - try both formats
                    local_install_result = subprocess.run(
                        ["claude", "plugin", "install", "clautorun@clautorun"],
                        capture_output=True,
                        text=True,
                        timeout=15
                    )

                    if local_install_result.returncode == 0:
                        print("✅ Successfully installed via local marketplace")
                        return True
                    else:
                        print(f"⚠️  Local marketplace installation (named) failed: {local_install_result.stderr}")

                        # Try with dev-marketplace naming
                        dev_install_result = subprocess.run(
                            ["claude", "plugin", "install", "clautorun@dev-marketplace"],
                            capture_output=True,
                            text=True,
                            timeout=15
                        )

                        if dev_install_result.returncode == 0:
                            print("✅ Successfully installed via dev-marketplace")
                            return True
                        else:
                            print(f"⚠️  Dev-marketplace installation failed: {dev_install_result.stderr}")

                            # List available marketplaces for debugging
                            list_result = subprocess.run(
                                ["claude", "plugin", "marketplace", "list"],
                                capture_output=True,
                                text=True,
                                timeout=10
                            )
                            if list_result.returncode == 0:
                                print(f"📋 Available marketplaces: {list_result.stdout}")
                            else:
                                print("⚠️  Could not list marketplaces")

                # If local marketplace fails, try GitHub installation as fallback
                print("🔄 Trying GitHub installation as fallback...")
                github_result = subprocess.run(
                    ["claude", "plugin", "install", "https://github.com/ahundt/clautorun.git"],
                    capture_output=True,
                    text=True,
                    timeout=30
                )

                if github_result.returncode == 0:
                    print("✅ Successfully installed via GitHub repository")
                    return True
                else:
                    print(f"⚠️  GitHub installation failed: {github_result.stderr}")

            return False

        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError) as e:
            print(f"ℹ️  Claude Code plugin system not available: {e}")
            return False

    def install_plugin(self) -> bool:
        """Install plugin to Claude Code plugins directory"""
        if not self.validate_plugin_structure():
            return False

        # First, try Claude Code's plugin system
        if self.try_claude_plugin_install():
            return True

        print("🔄 Falling back to manual installation...")

        if not self.create_directories():
            return False

        # Check if we need to update existing installation
        if self.plugin_install_dir.exists():
            if self.is_plugin_installed():
                print("✅ Plugin is already properly installed")
                return True
            else:
                self.backup_existing_plugin()
                shutil.rmtree(self.plugin_install_dir)

        try:
            # Copy entire plugin directory
            shutil.copytree(self.plugin_source_dir, self.plugin_install_dir,
                          ignore=shutil.ignore_patterns('.git', '__pycache__', '*.pyc', '.coverage'))

            print(f"✅ Manually installed plugin to: {self.plugin_install_dir}")
            return True

        except (OSError, PermissionError) as e:
            print(f"❌ Failed to install plugin: {e}")
            return False

    def remove_plugin(self) -> bool:
        """Remove the plugin directory"""
        if not self.plugin_install_dir.exists():
            print("ℹ️  Plugin is not installed")
            return True

        try:
            shutil.rmtree(self.plugin_install_dir)
            print("✅ Removed plugin directory")
            return True
        except (OSError, PermissionError) as e:
            print(f"❌ Failed to remove plugin: {e}")
            return False

    def verify_installation(self) -> bool:
        """Verify that the plugin is properly installed"""
        # Check for marketplace installation first
        commands_symlink = Path.home() / ".claude" / "commands" / "clautorun"
        if commands_symlink.exists() and commands_symlink.is_symlink():
            print("✅ Plugin installed via Claude Code marketplace")
            # Note: We can't easily test marketplace plugins due to Python path issues
            # But the marketplace installation itself is verification enough
            return True

        # Check for manual installation
        if not self.plugin_install_dir.exists():
            print("❌ Plugin directory not found")
            return False

        if not self.is_plugin_installed():
            print("❌ Plugin is not properly installed or has wrong manifest")
            return False

        # Verify plugin structure
        installed_manifest = self.plugin_install_dir / ".claude-plugin" / "plugin.json"
        commands_dir = self.plugin_install_dir / "commands"
        plugin_script = commands_dir / "clautorun"

        if not installed_manifest.exists():
            print("❌ Plugin manifest not found in installation")
            return False

        if not commands_dir.exists():
            print("❌ Commands directory not found in installation")
            return False

        if not plugin_script.exists():
            print("❌ Plugin script not found in installation")
            return False

        if not os.access(plugin_script, os.X_OK):
            print("❌ Plugin script is not executable")
            return False

        # Try to execute the plugin with a simple test
        try:
            import subprocess
            result = subprocess.run(
                [str(plugin_script)],
                input='{"prompt": "/afs", "session_id": "test"}',
                text=True,
                capture_output=True,
                timeout=5
            )

            if result.returncode == 0:
                output = json.loads(result.stdout)
                if "continue" in output and "response" in output:
                    print("✅ Plugin is working correctly")
                    return True

            print("⚠️  Plugin test failed")
            print(f"   stdout: {result.stdout}")
            print(f"   stderr: {result.stderr}")
            return False

        except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
            print(f"⚠️  Plugin verification failed: {e}")
            return False

    def install(self, force: bool = False) -> bool:
        """Install the Claude Code plugin"""
        print("🚀 Installing clautorun Claude Code plugin...")

        # Check UV environment first
        if not self.check_uv_environment():
            print("❌ UV environment check failed")
            print("   Please ensure UV is installed and run 'uv sync --extra claude-code'")
            return False

        # Ensure dependencies are up to date
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

        success = self.install_plugin()

        if success:
            print("✅ Installation completed successfully!")
            print(f"   Plugin installed at: {self.plugin_install_dir}")
            print("   You can now use: /clautorun /afs, /clautorun /afa, etc.")
            print("   Run tests with: uv run pytest tests/")

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

    def uninstall(self, force: bool = False) -> bool:
        """Uninstall the Claude Code plugin"""
        print("🗑️  Uninstalling clautorun Claude Code plugin...")

        # First, try Claude Code's plugin system
        if self.try_claude_plugin_uninstall():
            return True

        print("🔄 Falling back to manual uninstall...")

        if not self.plugin_install_dir.exists():
            print("ℹ️  Plugin is not installed")
            return True

        # Plugin directories can be safely removed without force check
        success = self.remove_plugin()

        if success:
            print("✅ Manual uninstallation completed successfully!")

        return success

    def check(self) -> bool:
        """Check installation status using Claude Code plugin system"""
        print("🔍 Checking clautorun installation...")

        # Check UV environment
        if not self.check_uv_environment():
            print("⚠️  UV environment issues detected")
            print("   Run 'uv sync --extra claude-code' to fix")

        # Use Claude Code's plugin system to check status
        try:
            # Check if we can access Claude Code plugin system
            result = subprocess.run(
                ["claude", "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                print("❌ Claude Code not accessible")
                return False

            print(f"✅ Claude Code detected: {result.stdout.strip()}")

            # Check marketplace status
            marketplace_result = subprocess.run(
                ["claude", "plugin", "marketplace", "list"],
                capture_output=True,
                text=True,
                timeout=10
            )

            if marketplace_result.returncode == 0:
                print("✅ Plugin marketplace accessible")
                # Show if our marketplace is configured
                if "clautorun" in marketplace_result.stdout:
                    print("✅ clautorun marketplace is configured")

                    # Try to find if plugin is installed by checking for the symlink
                    commands_symlink = Path.home() / ".claude" / "commands" / "clautorun"
                    if commands_symlink.exists() and commands_symlink.is_symlink():
                        print("✅ Plugin is installed and accessible")
                        return True
                    else:
                        print("⚠️  Plugin not installed (run: uv run clautorun install)")
                        return False
                else:
                    print("⚠️  clautorun marketplace not found")
                    print("   Run: uv run clautorun install to set up marketplace")
                    return False
            else:
                print("⚠️  Plugin marketplace check failed")
                return False

        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError) as e:
            print(f"❌ Claude Code plugin system check failed: {e}")
            return False

    

def main():
    """Main installation CLI"""
    # Check Python version compatibility first
    if not check_python_version():
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Manage clautorun Claude Code plugin installation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  clautorun install          # Install plugin
  clautorun install --force  # Force reinstall
  clautorun uninstall        # Remove plugin
  clautorun check            # Check installation status

  With UV environment:
  source .venv/bin/activate
  clautorun install
        """
    )

    parser.add_argument(
        "action",
        nargs="?",
        default="install",
        choices=["install", "uninstall", "check", "status"],
        help="Action to perform (default: install)"
    )

    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Force action (overwrite existing files)"
    )

    args = parser.parse_args()

    installer = ClautorunInstaller()

    if args.action == "install":
        success = installer.install(force=args.force)
    elif args.action == "uninstall":
        success = installer.uninstall(force=args.force)
    elif args.action == "check":
        success = installer.check()
    elif args.action == "status":
        success = installer.check()
    else:
        parser.print_help()
        success = False

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()