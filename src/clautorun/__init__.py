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

# Minimal exports to avoid circular imports
__all__ = [
    "__version__",
    "__author__",
    "__email__"
]