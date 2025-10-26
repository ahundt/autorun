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
"""Command Discovery Engine for clautorun - Enhanced with main.py DRY patterns"""

import json
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import subprocess
import shelve

# Cache for discovered commands to avoid repeated filesystem scans
_command_cache = {}
_cache_timestamp = None

# Follow main.py pattern for handlers and configuration
DISCOVERY_HANDLERS = {}
def discovery_handler(name):
    """Decorator to register discovery handlers - following main.py pattern"""
    def dec(f):
        DISCOVERY_HANDLERS[name] = f
        return f
    return dec

# Discovery location handlers - following main.py handler pattern
@discovery_handler("global_commands")
def discover_global_commands() -> Dict[str, Dict]:
    """Handler for discovering global commands"""
    commands = {}
    global_commands_dir = Path.home() / ".claude" / "commands"
    if global_commands_dir.exists():
        for cmd_file in global_commands_dir.glob("*.md"):
            cmd_name = f"/{cmd_file.stem}"
            commands[cmd_name] = {
                "type": "global_command",
                "path": str(cmd_file),
                "display_name": cmd_file.stem,
                "source": "global_commands",
                "format": "markdown"
            }
    return commands

@discovery_handler("plugin_commands")
def discover_plugin_commands() -> Dict[str, Dict]:
    """Handler for discovering plugin commands"""
    commands = {}
    plugins_dir = Path.home() / ".claude" / "plugins"
    if plugins_dir.exists():
        for plugin_dir in plugins_dir.iterdir():
            if plugin_dir.is_dir():
                commands_dir = plugin_dir / "commands"
                if commands_dir.exists():
                    # Check for .claude-plugin/plugin.json for plugin name
                    plugin_name = plugin_dir.name
                    plugin_manifest = plugin_dir / ".claude-plugin" / "plugin.json"
                    if plugin_manifest.exists():
                        try:
                            with open(plugin_manifest, 'r') as f:
                                manifest = json.load(f)
                                plugin_name = manifest.get('name', plugin_dir.name)
                        except:
                            pass

                    for cmd_file in commands_dir.glob("*.md"):
                        cmd_name = f"/{cmd_file.stem}"
                        # Check if plugin has manifest with command list
                        plugin_prefix = f"/{plugin_name}:"
                        commands[cmd_name] = {
                            "type": "plugin_command",
                            "path": str(cmd_file),
                            "display_name": cmd_file.stem,
                            "source": plugin_name,
                            "format": "markdown",
                            "plugin_prefix": plugin_prefix
                        }
                        commands[plugin_prefix + cmd_name[1:]] = commands[cmd_name]
    return commands

@discovery_handler("local_commands")
def discover_local_commands() -> Dict[str, Dict]:
    """Handler for discovering local commands"""
    commands = {}
    local_commands_dir = Path.cwd() / "commands"
    if local_commands_dir.exists():
        for cmd_file in local_commands_dir.glob("*.md"):
            cmd_name = f"/{cmd_file.stem}"
            commands[cmd_name] = {
                "type": "local_command",
                "path": str(cmd_file),
                "display_name": cmd_file.stem,
                "source": "local",
                "format": "markdown"
            }
    return commands

@discovery_handler("executable_commands")
def discover_executable_commands() -> Dict[str, Dict]:
    """Handler for discovering executable commands"""
    commands = {}
    command_dirs = [
        Path.home() / ".claude" / "commands",
        Path.cwd() / "commands"
    ]

    for commands_dir in command_dirs:
        if commands_dir.exists():
            for cmd_file in commands_dir.glob("*"):
                if cmd_file.is_file() and cmd_file.stat().st_mode & 0o111:  # Executable
                    cmd_name = f"/{cmd_file.name}"
                    commands[cmd_name] = {
                        "type": "executable_command",
                        "path": str(cmd_file),
                        "display_name": cmd_file.name,
                        "source": "executable",
                        "format": "executable"
                    }
    return commands

def discover_existing_commands(force_refresh: bool = False) -> Dict[str, Dict]:
    """
    Discover existing slash commands using efficient handler dispatch.

    Enhanced version following main.py patterns - uses handler dispatch for
    modular discovery of different command types.
    """
    global _command_cache, _cache_timestamp

    # Check cache validity - same pattern as main.py
    if not force_refresh and _command_cache and _cache_timestamp:
        try:
            cache_age = time.time() - _cache_timestamp
            if cache_age < 300:  # 5 minutes cache validity
                return _command_cache
        except:
            pass

    commands = {}

    # Efficient dispatch using discovery handlers - following main.py pattern
    for handler_name, handler_func in DISCOVERY_HANDLERS.items():
        try:
            discovered = handler_func()
            commands.update(discovered)
        except Exception as e:
            # Graceful error handling - continue with other handlers
            pass

    # Update cache
    _command_cache = commands
    _cache_timestamp = time.time()

    return commands

def load_command_content(cmd_info: Dict) -> str:
    """Load the content of a command file."""
    if not cmd_info or "path" not in cmd_info:
        return ""

    try:
        with open(cmd_info["path"], 'r', encoding='utf-8') as f:
            content = f.read()

        # For markdown commands, extract frontmatter and content
        if cmd_info.get("format") == "markdown":
            # Split frontmatter from content
            if content.startswith('---'):
                parts = content.split('---', 2)
                if len(parts) >= 3:
                    return parts[2].strip()

        return content.strip()

    except Exception as e:
        return f"Error loading command content: {e}"

def parse_command_args(prompt: str, command_name: str) -> Tuple[str, str]:
    """
    Parse command arguments from prompt.

    Returns tuple of (clean_command, args_string)
    """
    # Remove the command name from prompt to get arguments
    if prompt.startswith(command_name):
        args_part = prompt[len(command_name):].strip()
        return command_name, args_part
    return prompt, ""

def validate_command_exists(command_name: str) -> bool:
    """Check if a command exists - efficient validation using generator pattern"""
    commands = discover_existing_commands()
    # Efficient lookup following main.py pattern
    return command_name in commands

def get_command_metadata(command_name: str) -> Optional[Dict]:
    """Get metadata for a specific command - following main.py pattern"""
    commands = discover_existing_commands()
    return commands.get(command_name)

def find_command_by_pattern(pattern: str, limit: int = 10) -> List[Dict]:
    """Find commands matching a pattern - efficient pattern matching"""
    commands = discover_existing_commands()
    pattern_lower = pattern.lower().lstrip('/')

    # Efficient pattern matching using list comprehension - following main.py style
    matches = [
        {"command": cmd_name, "score": 100 if cmd_name.lower() == f"/{pattern_lower}" else 50, **cmd_info}
        for cmd_name, cmd_info in commands.items()
        if pattern_lower in cmd_name.lower() or pattern_lower in cmd_info.get("display_name", "").lower()
    ]

    # Sort by score and limit results
    matches.sort(key=lambda x: x["score"], reverse=True)
    return matches[:limit]

def search_commands(query: str, limit: int = 10) -> List[Dict]:
    """Search for commands - efficient scoring following main.py pattern"""
    commands = discover_existing_commands()
    query_lower = query.lower()

    # Efficient scoring using list comprehension - following main.py patterns
    matches = [
        (score, cmd_name, cmd_info)
        for cmd_name, cmd_info in commands.items()
        for score in [(
            100 if cmd_name.lower() == query_lower else
            50 if query_lower in cmd_name.lower() else
            30 if query_lower in cmd_info.get("display_name", "").lower() else
            10 if query_lower in cmd_info.get("source", "").lower() else
            0
        )]
        if score > 0
    ]

    # Sort by score descending and return top matches - following main.py pattern
    matches.sort(key=lambda x: x[0], reverse=True)
    return [{"command": cmd_name, "score": score, **cmd_info}
            for score, cmd_name, cmd_info in matches[:limit]]

def invalidate_cache():
    """Invalidate the command discovery cache."""
    global _command_cache, _cache_timestamp
    _command_cache = {}
    _cache_timestamp = None

def get_command_statistics() -> Dict[str, int]:
    """Get statistics about discovered commands."""
    commands = discover_existing_commands()

    stats = {
        "total": len(commands),
        "global_commands": 0,
        "plugin_commands": 0,
        "local_commands": 0,
        "executable_commands": 0,
        "markdown_commands": 0
    }

    for cmd_info in commands.values():
        cmd_type = cmd_info.get("type", "unknown")
        if cmd_type in stats:
            stats[cmd_type] += 1

        if cmd_info.get("format") == "markdown":
            stats["markdown_commands"] += 1
        elif cmd_info.get("format") == "executable":
            stats["executable_commands"] += 1

    return stats

# Export main functions
__all__ = [
    'discover_existing_commands',
    'load_command_content',
    'parse_command_args',
    'validate_command_exists',
    'get_command_metadata',
    'search_commands',
    'invalidate_cache',
    'get_command_statistics'
]