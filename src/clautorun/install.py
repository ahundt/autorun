#!/usr/bin/env python3
"""Clautorun Claude Code plugin installation and management"""
import os
import sys
import json
import shutil
import argparse
from pathlib import Path
from typing import Optional


class ClautorunInstaller:
    """Manages Claude Code plugin installation for clautorun"""

    def __init__(self):
        self.home_dir = Path.home()
        self.claude_dir = self.home_dir / ".claude"
        self.commands_dir = self.claude_dir / "commands"
        self.plugin_name = "clautorun"

        # Get the location of the installed clautorun package
        try:
            import clautorun
            self.package_dir = Path(clautorun.__file__).parent
        except ImportError:
            # Fallback for development/relative imports
            self.package_dir = Path(__file__).parent

        self.plugin_source = self.package_dir / "claude_code_plugin.py"
        self.plugin_target = self.commands_dir / self.plugin_name

    def detect_claude_code_installation(self) -> bool:
        """Detect if Claude Code is installed"""
        return self.claude_dir.exists()

    def create_directories(self) -> bool:
        """Create necessary directories if they don't exist"""
        try:
            self.commands_dir.mkdir(parents=True, exist_ok=True)
            return True
        except (OSError, PermissionError) as e:
            print(f"❌ Failed to create directories: {e}")
            return False

    def backup_existing_plugin(self) -> Optional[Path]:
        """Backup existing plugin if it exists"""
        if not self.plugin_target.exists():
            return None

        backup_path = self.plugin_target.with_suffix('.backup')
        try:
            if backup_path.exists():
                # Remove old backup
                backup_path.unlink()

            # Create backup of existing plugin
            shutil.copy2(self.plugin_target, backup_path)
            print(f"📦 Backed up existing plugin to {backup_path}")
            return backup_path
        except (OSError, PermissionError) as e:
            print(f"⚠️  Warning: Could not backup existing plugin: {e}")
            return None

    def is_symlink_valid(self) -> bool:
        """Check if existing symlink is valid"""
        if not self.plugin_target.is_symlink():
            return False

        try:
            # Check if symlink points to our source
            target = self.plugin_target.resolve()
            source = self.plugin_source.resolve()
            return target == source
        except (OSError, RuntimeError):
            return False

    def create_plugin_symlink(self) -> bool:
        """Create symlink from package to Claude Code commands directory"""
        if not self.plugin_source.exists():
            print(f"❌ Plugin source not found: {self.plugin_source}")
            return False

        if not self.create_directories():
            return False

        # Check if we need to update existing installation
        if self.plugin_target.exists():
            if self.is_symlink_valid():
                print("✅ Plugin is already properly installed")
                return True
            else:
                self.backup_existing_plugin()
                self.plugin_target.unlink()

        try:
            # Create relative symlink for portability
            relative_target = os.path.relpath(self.plugin_source, self.commands_dir)
            self.plugin_target.symlink_to(relative_target)

            # Make executable
            self.plugin_target.chmod(0o755)

            print(f"✅ Created plugin symlink: {self.plugin_target} -> {self.plugin_source}")
            return True

        except (OSError, PermissionError) as e:
            print(f"❌ Failed to create symlink: {e}")
            return False

    def remove_plugin_symlink(self) -> bool:
        """Remove the plugin symlink"""
        if not self.plugin_target.exists():
            print("ℹ️  Plugin is not installed")
            return True

        try:
            if self.plugin_target.is_symlink():
                self.plugin_target.unlink()
                print("✅ Removed plugin symlink")
                return True
            else:
                print("⚠️  Existing plugin is not a symlink. Use --force to remove.")
                return False
        except (OSError, PermissionError) as e:
            print(f"❌ Failed to remove plugin: {e}")
            return False

    def verify_installation(self) -> bool:
        """Verify that the plugin is properly installed"""
        if not self.plugin_target.exists():
            print("❌ Plugin not found")
            return False

        if not self.is_symlink_valid():
            print("❌ Plugin symlink is invalid or points to wrong location")
            return False

        if not os.access(self.plugin_target, os.X_OK):
            print("❌ Plugin is not executable")
            return False

        # Try to execute the plugin with a simple test
        try:
            import subprocess
            result = subprocess.run(
                [str(self.plugin_target)],
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

        if not self.detect_claude_code_installation():
            print("❌ Claude Code installation not detected")
            print(f"   Expected directory: {self.claude_dir}")
            return False

        if force and self.plugin_target.exists():
            print("🔄 Force mode: removing existing installation")
            if not self.remove_plugin_symlink():
                return False

        success = self.create_plugin_symlink()

        if success:
            print("✅ Installation completed successfully!")
            print(f"   Plugin installed at: {self.plugin_target}")
            print("   You can now use: /clautorun /afs, /clautorun /afa, etc.")

        return success

    def uninstall(self, force: bool = False) -> bool:
        """Uninstall the Claude Code plugin"""
        print("🗑️  Uninstalling clautorun Claude Code plugin...")

        if not self.plugin_target.exists():
            print("ℹ️  Plugin is not installed")
            return True

        if not force and not self.plugin_target.is_symlink():
            print("⚠️  Existing plugin is not a symlink (may be manually installed)")
            print("   Use --force to remove anyway")
            return False

        success = self.remove_plugin_symlink()

        if success:
            print("✅ Uninstallation completed successfully!")

        return success

    def check(self) -> bool:
        """Check installation status"""
        print("🔍 Checking clautorun installation...")

        print(f"   Package directory: {self.package_dir}")
        print(f"   Plugin source: {self.plugin_source}")
        print(f"   Plugin target: {self.plugin_target}")

        if not self.detect_claude_code_installation():
            print("❌ Claude Code installation not detected")
            return False

        if not self.plugin_source.exists():
            print("❌ Plugin source file not found")
            return False

        if not self.plugin_target.exists():
            print("❌ Plugin is not installed")
            return False

        if self.is_symlink_valid():
            print("✅ Plugin symlink is valid")
        else:
            print("❌ Plugin symlink is invalid")
            return False

        return self.verify_installation()


def main():
    """Main installation CLI"""
    parser = argparse.ArgumentParser(
        description="Manage clautorun Claude Code plugin installation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m clautorun install          # Install plugin
  python -m clautorun install --force  # Force reinstall
  python -m clautorun uninstall        # Remove plugin
  python -m clautorun check            # Check installation status
        """
    )

    parser.add_argument(
        "action",
        choices=["install", "uninstall", "check"],
        help="Action to perform"
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
    else:
        parser.print_help()
        success = False

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()