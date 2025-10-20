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

        return True

    def is_plugin_installed(self) -> bool:
        """Check if plugin is properly installed"""
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

    def install_plugin(self) -> bool:
        """Install plugin to Claude Code plugins directory"""
        if not self.validate_plugin_structure():
            return False

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

            print(f"✅ Installed plugin to: {self.plugin_install_dir}")
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

        return success

    def uninstall(self, force: bool = False) -> bool:
        """Uninstall the Claude Code plugin"""
        print("🗑️  Uninstalling clautorun Claude Code plugin...")

        if not self.plugin_install_dir.exists():
            print("ℹ️  Plugin is not installed")
            return True

        # Plugin directories can be safely removed without force check
        success = self.remove_plugin()

        if success:
            print("✅ Uninstallation completed successfully!")

        return success

    def check(self) -> bool:
        """Check installation status"""
        print("🔍 Checking clautorun installation...")

        print(f"   Plugin source directory: {self.plugin_source_dir}")
        print(f"   Plugin manifest: {self.plugin_manifest}")
        print(f"   Plugin install directory: {self.plugin_install_dir}")

        if not self.detect_claude_code_installation():
            print("❌ Claude Code installation not detected")
            return False

        if not self.validate_plugin_structure():
            print("❌ Plugin source structure is invalid")
            return False

        if not self.plugin_install_dir.exists():
            print("❌ Plugin is not installed")
            return False

        if self.is_plugin_installed():
            print("✅ Plugin is properly installed")
        else:
            print("❌ Plugin installation is invalid")
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