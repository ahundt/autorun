#!/usr/bin/env python3
"""clautorun-marketplace installer - Registers all plugins with Claude Code"""

import subprocess
import sys
from pathlib import Path


def main():
    """
    Marketplace installer entry point.
    Registers the clautorun marketplace and installs all 3 plugins.
    """
    # Determine the marketplace root directory
    # This script is installed at: ~/.claude/clautorun/src/clautorun_marketplace/__init__.py
    # So parent.parent = ~/.claude/clautorun/ (marketplace root)
    try:
        # When installed as package
        import clautorun_marketplace
        marketplace_root = Path(clautorun_marketplace.__file__).parent.parent.parent
    except (ImportError, AttributeError):
        # Fallback for development
        marketplace_root = Path(__file__).parent.parent.parent

    plugins_dir = marketplace_root / "plugins"

    print(f"📦 clautorun-marketplace v0.6.0")
    print(f"📍 Marketplace root: {marketplace_root}")
    print(f"📍 Plugins directory: {plugins_dir}")
    print()

    # Plugins to install from the marketplace
    plugins = ["clautorun", "plan-export", "pdf-extractor"]

    # Step 1: Add the marketplace root (where .claude-plugin/marketplace.json is) as a marketplace
    print(f"🔧 Adding clautorun marketplace...")
    try:
        result = subprocess.run(
            ["claude", "plugin", "marketplace", "add", str(marketplace_root)],
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

    for plugin_name in plugins:
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
    if success_count == len(plugins):
        print(f"✅ Successfully installed all {success_count} plugins!")
    else:
        print(f"⚠️  Installed {success_count}/{len(plugins)} plugins")

    if failed:
        print(f"❌ Failed plugins: {', '.join(failed)}")

    print()
    print("Available commands:")
    print("  /cr:*             - clautorun commands (autorun, file policies, tmux)")
    print("  /plan-export:*    - plan export commands")
    print("  /pdf-extractor:*  - PDF extraction commands")
    print()
    print("Run '/help' to see all available commands.")

    # Return exit code (0 = success, 1 = failure)
    # Note: The auto-generated entry point does sys.exit(main()), so we must
    # return an integer exit code, not a boolean (sys.exit(True) == sys.exit(1))
    return 0 if success_count == len(plugins) else 1


if __name__ == "__main__":
    sys.exit(main())
