#!/usr/bin/env python3
"""
Basic usage example for autorun

This example demonstrates how to use the simplified imports from the autorun package.
Instead of importing from specific modules, you can now import everything directly
from the autorun package.

Instead of:
  from autorun.main import CONFIG, stop_handler
  from autorun.main import HANDLERS as HOOK_HANDLERS

You can now simply use:
  from autorun import CONFIG, stop_handler, hook_handlers
"""

import sys
import os
from pathlib import Path

# Add src to path for testing
sys.path.insert(0, str(Path(__file__).parent / "src"))

# Test all the important imports that now work directly from autorun
from autorun import (
    # Core configuration and functions
    CONFIG,
    main,
    COMMAND_HANDLERS,

    # AI monitor workflow functions
    stop_handler,
    pretooluse_handler,
    intercept_commands_sync,
    inject_continue_prompt,
    inject_verification_prompt,
    is_premature_stop,
    should_trigger_verification,

    # Response builders
    build_hook_response,
    build_pretooluse_response,

    # Session management
    session_state,
    log_info,

    # Command handlers
    handle_search,
    handle_allow,
    handle_justify,
    handle_status,
    handle_stop,
    handle_emergency_stop,
    handle_activate,

    # Hook integration
    hook_handlers,
)

def demo_simplified_imports():
    """Demonstrate that simplified imports work"""
    print("🎉 Simplified imports from autorun package!")
    print("=" * 50)

    # Show key configuration
    print("✅ Core Configuration:")
    print(f"   Completion marker: {CONFIG['completion_marker'][:30]}...")
    print(f"   Emergency stop: {CONFIG['emergency_stop_phrase']}")
    print(f"   Max recheck count: {CONFIG['max_recheck_count']}")
    print()

    # Show command mappings
    print("✅ Command Mappings:")
    for cmd, action in CONFIG["command_mappings"].items():
        print(f"   {cmd} → {action}")
    print()

    # Show available hook handlers
    print("✅ Hook Handlers:")
    for hook in hook_handlers:
        print(f"   {hook}")
    print()

    # Test that functions are callable
    print("✅ Function Imports:")
    print(f"   stop_handler: {callable(stop_handler)}")
    print(f"   session_state: {callable(session_state)}")
    print(f"   build_hook_response: {callable(build_hook_response)}")
    print()

    print("🚀 All imports work directly from 'import autorun'!")
    print("   No more 'from autorun.main import ...' needed!")

if __name__ == "__main__":
    demo_simplified_imports()