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

__version__ = "0.1.0"
__author__ = "Your Name"
__email__ = "your.email@example.com"

# Export main functions for easier import
from .main import main, CONFIG, COMMAND_HANDLERS
from .agent_sdk_hook import HANDLERS as hook_handlers
from .mcp_server import create_mcp_server

__all__ = [
    "main",
    "CONFIG",
    "COMMAND_HANDLERS",
    "hook_handlers",
    "create_mcp_server"
]