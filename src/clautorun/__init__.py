#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
clautorun - Claude Agent SDK Command Interceptor

A lightweight, efficient command interceptor for Claude Code that saves tokens by processing
autorun commands locally before they reach the AI.

Features:
- Zero AI token consumption for autorun commands
- Interactive mode with smart Ctrl+C handling
- Multiple integration methods (hooks, MCP, plugin)
- Efficient dispatch patterns matching autorun5.py
- Full compatibility with existing autorun workflows
"""

import sys

# Check Python version compatibility (removed problematic import)
# from .python_check import check_python_version
# if not check_python_version():
#     sys.exit(1)

__version__ = "0.1.0"
__author__ = "Your Name"
__email__ = "your.email@example.com"

# Simple export of core functionality
__all__ = [
    "main",
    "CONFIG",
    "COMMAND_HANDLERS",
    "stop_handler", "pretooluse_handler", "claude_code_handler",
    "inject_continue_prompt", "inject_verification_prompt",
    "is_premature_stop", "should_trigger_verification",
    "build_hook_response", "build_pretooluse_response",
    "session_state", "log_info",
    "handle_search", "handle_allow", "handle_justify", "handle_status",
    "handle_stop", "handle_emergency_stop", "handle_activate"
]
from .claude_code_plugin import main as claude_code_plugin_main
from .agent_sdk_hook import HOOK_HANDLERS as hook_handlers
from .mcp_server import create_mcp_server

__all__ = [
    # Core functions
    "main",
    "CONFIG",
    "COMMAND_HANDLERS",

    # AI monitor workflow
    "stop_handler",
    "pretooluse_handler",
    "claude_code_handler",
    "inject_continue_prompt",
    "inject_verification_prompt",
    "is_premature_stop",
    "should_trigger_verification",

    # Response builders
    "build_hook_response",
    "build_pretooluse_response",

    # Session management
    "session_state",
    "log_info",

    # Command handlers
    "handle_search",
    "handle_allow",
    "handle_justify",
    "handle_status",
    "handle_stop",
    "handle_emergency_stop",
    "handle_activate",

    # Integration components
    "hook_handlers",
    "create_mcp_server"
]