#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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
"""
⚠️ DEPRECATED: Legacy Hook Handler (v0.6.1) ⚠️

This file is preserved for backward compatibility only.
The canonical v0.7 implementation is in:
- core.py:220-958 (EventContext magic state, ClautorunApp dispatcher, ClautorunDaemon server)
- plugins.py:1-772 (All 5 plugins with DRY factories - 78% code reduction)
- session_manager.py:54-421 (RAII SessionLock - reused from v0.6.1)

v0.7 provides:
- 10-30x faster hook response (1-5ms vs 50-150ms via Unix socket daemon)
- 78% code reduction via factory patterns (_make_policy_handler, _make_block_op, _make_plan_handler)
- Magic state persistence (ctx.file_policy = "SEARCH" auto-persists)
- Better maintainability (771 LOC vs 2,188 LOC)

To use this legacy mode: export CLAUTORUN_USE_DAEMON=0
Default (after v0.7): export CLAUTORUN_USE_DAEMON=1 (v0.7 daemon mode)

This file will be removed in v0.8.0 after daemon mode stability is confirmed.

---

ORIGINAL DOCSTRING (v0.6.1):

Clautorun Hook Handler - Source of Truth

PRIMARY HOOK HANDLER for Claude Code plugin system.
- Called directly by hooks.json via: python3 ${CLAUDE_PLUGIN_ROOT}/src/clautorun/main.py
- Contains complete three-stage verification logic with verification_engine and ai_monitor
- Handles all hook events: UserPromptSubmit, PreToolUse, Stop, SubagentStop
- Entry points: clautorun and clautorun-interactive (UV commands)
- Modes: HOOK_INTEGRATION (default) and INTERACTIVE (Agent SDK client)

Features:
- RequirementVerificationEngine for evidence-based task completion verification
- AI Monitor for session lifecycle management and crash detection
- Agent SDK Client (ClaudeSDKClient) for bidirectional communication
- Enhanced transcript analysis with requirement extraction
"""

# Standard library imports for path configuration
import os
import sys
from pathlib import Path

# Configure sys.path before importing clautorun packages
# Enables relative imports across different execution contexts:
# - Hook execution: Uses CLAUDE_PLUGIN_ROOT environment variable
# - Module execution: Works with python3 -m clautorun
# - Script execution: Uses __file__ introspection

_plugin_root = os.environ.get('CLAUDE_PLUGIN_ROOT')
if _plugin_root:
    _src_dir = os.path.join(_plugin_root, 'src')
    if _src_dir not in sys.path:
        sys.path.insert(0, _src_dir)
else:
    # Fallback for direct script execution
    _src_dir = str(Path(__file__).parent.parent)
    if _src_dir not in sys.path:
        sys.path.insert(0, _src_dir)

# Enable relative imports when running as main script
if __name__ == '__main__' and __package__ is None:
    __package__ = 'clautorun'

# Package and external imports (after sys.path is configured)
import json
import time
import asyncio
from datetime import datetime
from typing import Optional, List, Dict
import re as regex_module

# Import Agent SDK
try:
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, query
except ImportError:
    print("❌ Claude Agent SDK not installed. Run: pip install claude-agent-sdk")
    sys.exit(1)

# Import the ai_monitor library, with a fallback if it's not found.
try:
    from . import ai_monitor
except ImportError:
    try:
        # Fallback for when running as script directly
        import ai_monitor
    except ImportError:
        # Silent fallback - don't print as it pollutes hook output
        ai_monitor = None

# Import verification engine for enhanced two-stage verification
try:
    from .verification_engine import (
        RequirementVerificationEngine,
        VerificationStatus,
        RequirementType,
        RequirementEvidence
    )
    VERIFICATION_ENGINE_AVAILABLE = True
except ImportError:
    # Fallback if verification engine not available
    VERIFICATION_ENGINE_AVAILABLE = False
    RequirementVerificationEngine = None
    VerificationStatus = None
    RequirementType = None
    RequirementEvidence = None

# Import enhanced transcript analyzer
try:
    from .transcript_analyzer import (
        TranscriptAnalyzer,
        EvidenceType,
        ConfidenceLevel
    )
    TRANSCRIPT_ANALYZER_AVAILABLE = True
except ImportError:
    # Fallback if transcript analyzer not available
    TRANSCRIPT_ANALYZER_AVAILABLE = False
    TranscriptAnalyzer = None
    EvidenceType = None
    ConfidenceLevel = None

# Import injection effectiveness monitoring system
try:
    from .injection_monitoring import (
        InjectionEffectivenessMonitor,
        InjectionMethod,
        InjectionOutcome,
        record_injection
    )
    INJECTION_MONITORING_AVAILABLE = True
except ImportError:
    # Fallback if injection monitoring not available
    INJECTION_MONITORING_AVAILABLE = False
    InjectionEffectivenessMonitor = None
    InjectionMethod = None
    InjectionOutcome = None

# Import centralized tmux utilities for DRY compliance and standards enforcement
try:
    from .tmux_utils import get_tmux_utilities
    TMUX_UTILS_AVAILABLE = True
except ImportError:
    # Fallback if tmux utilities not available
    TMUX_UTILS_AVAILABLE = False
    get_tmux_utilities = None

# Global injection monitor instance
_injection_monitor: Optional[InjectionEffectivenessMonitor] = None

# Import centralized configuration (DRY principle)
from .config import (
    CONFIG, BASH_TOOLS, WRITE_TOOLS, EDIT_TOOLS, FILE_TOOLS, PLAN_TOOLS
)

# Import robust command detection (fixes substring matching bug)
# Uses bashlex AST parsing when available, falls back to shlex
try:
    from .command_detection import command_matches_pattern as ast_command_matches_pattern
    AST_COMMAND_DETECTION_AVAILABLE = True
except ImportError:
    AST_COMMAND_DETECTION_AVAILABLE = False
    ast_command_matches_pattern = None

# Global tmux utilities instance for session management
_tmux_utilities = None

def get_global_tmux_utils():
    """Get global tmux utilities instance with proper session management"""
    global _tmux_utilities
    if _tmux_utilities is None and TMUX_UTILS_AVAILABLE:
        _tmux_utilities = get_tmux_utilities()  # Uses default "clautorun" session
        log_info("Centralized tmux utilities initialized with default session")
    return _tmux_utilities

def get_injection_monitor() -> Optional[InjectionEffectivenessMonitor]:
    """Get or create global injection monitor instance"""
    global _injection_monitor
    if _injection_monitor is None and INJECTION_MONITORING_AVAILABLE:
        try:
            _injection_monitor = InjectionEffectivenessMonitor()
            log_info("Injection monitoring initialized")
        except Exception as e:
            log_info(f"Failed to initialize injection monitoring: {e}")
    return _injection_monitor

def update_injection_outcome(state, outcome: InjectionOutcome, error_message: Optional[str] = None):
    """Update the outcome of the last injection attempt with response time"""
    if not INJECTION_MONITORING_AVAILABLE:
        return

    monitor = get_injection_monitor()
    if not monitor:
        return

    state.get("session_id", "unknown")
    last_injection_id = state.get("last_injection_attempt_id")
    start_time = state.get("last_injection_start_time")

    if not last_injection_id or not start_time:
        return

    try:
        # Calculate response time
        response_time_ms = (time.time() - start_time) * 1000

        # Update the injection attempt with actual outcome and response time
        for attempt in monitor.injection_attempts:
            if attempt.attempt_id == last_injection_id and attempt.response_time_ms == 0:
                attempt.outcome = outcome
                attempt.response_time_ms = response_time_ms
                attempt.error_message = error_message
                break

        log_info(f"Updated injection outcome: {outcome.value} ({response_time_ms:.1f}ms)")

    except Exception as e:
        log_info(f"Failed to update injection outcome: {e}")

# State management - core session infrastructure
STATE_DIR = Path.home() / ".claude" / "sessions"
STATE_DIR.mkdir(parents=True, exist_ok=True)

def sanitize_log_message(message: str, max_length: int = 10000) -> str:
    """Sanitize message for safe logging - prevents log injection attacks.

    Security: Untrusted data (transcripts, prompts, model outputs) must be
    sanitized before logging to prevent log injection via embedded newlines.

    Args:
        message: Raw message string (may contain newlines, control chars)
        max_length: Maximum message length (truncates if exceeded)

    Returns:
        Sanitized string safe for single-line log entry
    """
    if not isinstance(message, str):
        message = str(message)
    # Replace newlines and carriage returns with escaped versions
    message = message.replace('\r\n', '\\r\\n').replace('\n', '\\n').replace('\r', '\\r')
    # Replace other control characters that could affect log parsing
    message = ''.join(c if c.isprintable() or c == ' ' else f'\\x{ord(c):02x}' for c in message)
    # Truncate excessively long messages
    if len(message) > max_length:
        message = message[:max_length] + "... (truncated)"
    return message


def log_info(message):
    """Log info message to file with DEBUG environment variable control"""
    # Only log if DEBUG environment variable is set to true
    # Handle various forms of "true": true, True, TRUE, 1, yes, YES, etc.
    debug_value = os.getenv("DEBUG", "false").lower().strip()
    true_values = {"true", "1", "yes", "on", "enabled"}
    if debug_value not in true_values:
        return

    try:
        # Ensure directory exists
        STATE_DIR.mkdir(parents=True, exist_ok=True)

        # Sanitize message to prevent log injection
        safe_message = sanitize_log_message(message)

        # Log to main autorun log
        with open(STATE_DIR / "autorun.log", "a") as f:
            log_time = time.strftime('%Y-%m-%d %H:%M:%S')
            pid = os.getpid()
            f.write(f"[{log_time}] {pid}: {safe_message}\n")
            f.flush()

        # Separate log for PreToolUse debugging
        if "PreToolUse" in message:
            with open(STATE_DIR / "pretooluse_debug.log", "a") as debug_f:
                debug_f.write(f"[{log_time}] {pid}: {safe_message}\n")
                debug_f.flush()

    except Exception as e:
        # Fallback logging to stderr in case of write failure
        print(f"Log write failed: {e}", file=sys.stderr)

# Import robust session state management from session_manager module
# This provides RAII-based session state with proper file locking and backend selection
# Fixes Issue #29 (process-local _session_backends) and Issue #28 (filename extensions)
from clautorun.session_manager import session_state

# Import _not_in_pipe for pipe detection predicate
try:
    from clautorun.integrations import _not_in_pipe
except ImportError as e:
    # Import failed - define fallback and log diagnostic info
    print("=" * 70, file=sys.stderr)
    print("IMPORT ERROR: Failed to load command predicate", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(f"Module: clautorun.integrations", file=sys.stderr)
    print(f"Function: _not_in_pipe", file=sys.stderr)
    print(f"Exception: {type(e).__name__}: {e}", file=sys.stderr)
    print(f"\nIMPACT: Pipe detection will not work correctly", file=sys.stderr)
    print(f"SYMPTOM: Commands like 'git log | grep fix' may be blocked", file=sys.stderr)
    print(f"ACTION: Check clautorun installation and module paths", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    # Define a fallback that always returns True (block everything)
    def _not_in_pipe(ctx):
        return True

# =============================================================================
# Command Blocking State Management
# =============================================================================

# Global config file for blocking patterns
GLOBAL_CONFIG_FILE = STATE_DIR.parent / "config" / "command-blocks.json"


def get_session_blocks(session_id: str) -> List[Dict]:
    """
    Get blocked patterns for a session.

    Args:
        session_id: Claude session identifier

    Returns:
        List of blocked pattern dictionaries with pattern, suggestion, pattern_type, added_at
        Note: For backward compatibility, missing pattern_type defaults to "literal"
    """
    with session_state(session_id) as state:
        patterns = state.get("session_blocked_patterns", [])
        # Ensure pattern_type exists for backward compatibility
        for p in patterns:
            if "pattern_type" not in p:
                p["pattern_type"] = "literal"
        return patterns


def add_session_block(session_id: str, pattern: str, suggestion: Optional[str] = None, pattern_type: str = "literal") -> bool:
    """
    Add a blocked pattern to session state.

    Args:
        session_id: Claude session identifier
        pattern: Pattern string to block
        suggestion: Optional suggestion message
        pattern_type: Pattern matching type ("literal", "regex", "glob")

    Returns:
        True if added, False if already exists
    """
    from .config import DEFAULT_INTEGRATIONS

    with session_state(session_id) as state:
        patterns = state.get("session_blocked_patterns", [])

        # Check if pattern already exists
        for p in patterns:
            if p["pattern"] == pattern:
                return False

        # Use default suggestion if not provided
        if suggestion is None:
            integration = DEFAULT_INTEGRATIONS.get(pattern, {})
            suggestion = integration.get("suggestion", f"Pattern '{pattern}' is blocked")

        patterns.append({
            "pattern": pattern,
            "suggestion": suggestion,
            "pattern_type": pattern_type,
            "added_at": datetime.now().isoformat()
        })
        state["session_blocked_patterns"] = patterns
        return True


def remove_session_block(session_id: str, pattern: str) -> bool:
    """
    Remove a blocked pattern from session state.

    Args:
        session_id: Claude session identifier
        pattern: Pattern string to remove

    Returns:
        True if removed, False if not found
    """
    with session_state(session_id) as state:
        patterns = state.get("session_blocked_patterns", [])
        original_length = len(patterns)
        patterns = [p for p in patterns if p["pattern"] != pattern]
        state["session_blocked_patterns"] = patterns
        return len(patterns) < original_length


def clear_session_blocks(session_id: str, pattern: Optional[str] = None) -> int:
    """
    Clear session blocks (all or specific pattern).

    Args:
        session_id: Claude session identifier
        pattern: Optional specific pattern to clear, None clears all

    Returns:
        Number of patterns cleared
    """
    with session_state(session_id) as state:
        if pattern is None:
            count = len(state.get("session_blocked_patterns", []))
            state["session_blocked_patterns"] = []
            return count
        else:
            return 1 if remove_session_block(session_id, pattern) else 0


def get_global_blocks() -> List[Dict]:
    """
    Get globally blocked patterns.

    Returns:
        List of blocked pattern dictionaries with pattern, suggestion, pattern_type, added_at
        Note: For backward compatibility, missing pattern_type defaults to "literal"
    """
    # Ensure defaults are initialized on first access
    initialize_default_blocks()

    if not GLOBAL_CONFIG_FILE.exists():
        return []

    try:
        with open(GLOBAL_CONFIG_FILE, 'r') as f:
            config = json.load(f)
            patterns = config.get("global_blocked_patterns", [])
            # Ensure pattern_type exists for backward compatibility
            for p in patterns:
                if "pattern_type" not in p:
                    p["pattern_type"] = "literal"
            return patterns
    except (json.JSONDecodeError, IOError):
        return []


def initialize_default_blocks() -> bool:
    """
    Initialize default blocked patterns from DEFAULT_INTEGRATIONS on first run.

    Returns:
        True if initialized, False if already initialized
    """
    from .config import DEFAULT_INTEGRATIONS

    GLOBAL_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Check if already initialized
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, 'r') as f:
                config = json.load(f)
                if config.get("initialized_defaults", False):
                    return False
        except (json.JSONDecodeError, IOError):
            pass

    # Initialize with defaults
    config = {
        "version": "0.8.0",
        "initialized_defaults": True,
        "global_blocked_patterns": []
    }

    for pattern, integration in DEFAULT_INTEGRATIONS.items():
        config["global_blocked_patterns"].append({
            "pattern": pattern,
            "suggestion": integration.get("suggestion", f"Pattern '{pattern}' is blocked"),
            "severity": integration.get("severity", "high"),
            "added_at": datetime.now().isoformat()
        })

    with open(GLOBAL_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

    return True


def add_global_block(pattern: str, suggestion: Optional[str] = None, pattern_type: str = "literal") -> bool:
    """
    Add a globally blocked pattern.

    Args:
        pattern: Pattern string to block
        suggestion: Optional suggestion message
        pattern_type: Pattern matching type ("literal", "regex", "glob")

    Returns:
        True if added, False if already exists
    """
    from .config import DEFAULT_INTEGRATIONS

    GLOBAL_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Load existing config
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, 'r') as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError):
            config = {"version": "0.8.0", "global_blocked_patterns": []}
    else:
        config = {"version": "0.8.0", "global_blocked_patterns": []}

    patterns = config.get("global_blocked_patterns", [])

    # Check if pattern already exists
    for p in patterns:
        if p["pattern"] == pattern:
            return False

    # Use default suggestion if not provided
    if suggestion is None:
        integration = DEFAULT_INTEGRATIONS.get(pattern, {})
        suggestion = integration.get("suggestion", f"Pattern '{pattern}' is blocked")

    patterns.append({
        "pattern": pattern,
        "suggestion": suggestion,
        "pattern_type": pattern_type,
        "added_at": datetime.now().isoformat()
    })

    config["global_blocked_patterns"] = patterns

    with open(GLOBAL_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

    return True


def remove_global_block(pattern: str) -> bool:
    """
    Remove a globally blocked pattern.

    Args:
        pattern: Pattern string to remove

    Returns:
        True if removed, False if not found
    """
    if not GLOBAL_CONFIG_FILE.exists():
        return False

    with open(GLOBAL_CONFIG_FILE, 'r') as f:
        config = json.load(f)

    patterns = config.get("global_blocked_patterns", [])
    original_length = len(patterns)
    patterns = [p for p in patterns if p["pattern"] != pattern]

    config["global_blocked_patterns"] = patterns

    with open(GLOBAL_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

    return len(patterns) < original_length


def parse_pattern_and_description(args: str) -> tuple[str, str | None, str]:
    """
    Parse pattern and optional description from command arguments.

    Supports multiple formats:
    1. /cr:no pattern                    # No description, uses DEFAULT_INTEGRATIONS
    2. /cr:no "pattern with spaces"      # Quoted pattern, no description
    3. /cr:no "pattern" description      # Quoted pattern with custom description
    4. /cr:no pattern description text   # Unquoted pattern with description

    Pattern type prefixes (opt-in):
    5. /cr:no regex:pattern              # Regex pattern matching
    6. /cr:no glob:pattern               # Glob pattern matching
    7. /cr:no "/regex.*$/"               # Auto-detect regex using /pattern/ syntax

    Args:
        args: Command arguments after the command name

    Returns:
        Tuple of (pattern, description, pattern_type)
        - pattern: The pattern string to match
        - description: Custom description or None
        - pattern_type: One of "literal", "regex", "glob"

    Examples:
        >>> parse_pattern_and_description("rm")
        ('rm', None, 'literal')
        >>> parse_pattern_and_description('"rm -rf"')
        ('rm -rf', None, 'literal')
        >>> parse_pattern_and_description('"exec(" unsafe function')
        ('exec(', 'unsafe function', 'literal')
        >>> parse_pattern_and_description('regex:exec\\(')
        ('exec\\(', None, 'regex')
        >>> parse_pattern_and_description('glob:*.tmp')
        ('*.tmp', None, 'glob')
    """
    import shlex

    args = args.strip()
    if not args:
        raise ValueError("No pattern provided")

    pattern_type = "literal"  # Default to literal matching

    # Check for regex: prefix
    if args.startswith("regex:"):
        pattern_type = "regex"
        args = args[6:].lstrip()

    # Check for glob: prefix
    elif args.startswith("glob:"):
        pattern_type = "glob"
        args = args[5:].lstrip()

    # Use shlex to parse quoted strings
    try:
        parts = shlex.split(args)
    except ValueError:
        # Fallback to simple split if shlex fails
        parts = args.split(None, 1)

    if not parts:
        raise ValueError("No pattern provided")

    pattern = parts[0]

    # Check for /pattern/ regex syntax (auto-detect) on first token
    # This must happen after splitting to handle descriptions correctly
    if pattern_type == "literal" and pattern.startswith("/") and pattern.endswith("/") and len(pattern) > 2:
        potential_regex = pattern[1:-1]
        # Simple heuristic: if it has regex metacharacters, treat as regex
        regex_chars = set(r"[]{}()*+?|^$.\" ")
        if any(c in potential_regex for c in regex_chars):
            pattern_type = "regex"
            pattern = potential_regex

    # Everything after the pattern is the description
    description = None
    if len(parts) > 1:
        description = " ".join(parts[1:])

    return pattern, description, pattern_type


def is_safe_regex_pattern(pattern: str, max_length: int = 200) -> bool:
    """Validate regex pattern for safety against ReDoS attacks.

    Security: User-provided regex patterns can cause catastrophic backtracking
    (ReDoS). This function rejects patterns with known dangerous constructs.

    Args:
        pattern: Regex pattern to validate
        max_length: Maximum allowed pattern length

    Returns:
        True if pattern is considered safe, False otherwise

    Dangerous patterns blocked:
        - Nested quantifiers: (a+)+, (a*)+, (a+)*, etc.
        - Overlapping alternations with quantifiers
        - Excessively long patterns
    """
    if len(pattern) > max_length:
        return False

    # Detect nested quantifiers - the primary cause of catastrophic backtracking
    # Pattern: quantifier followed by another quantifier on the same group
    # Examples: (a+)+, (a*)+, (a+)*, (a?)*, (.+)+, ((a+))+, etc.
    nested_quantifier_patterns = [
        r'\([^)]*[+*?]\)[+*?]',       # (x+)+, (x*)+, etc.
        r'\([^)]*[+*?]\)\{',           # (x+){n}, (x*){n}, etc.
        r'\[[^\]]*\][+*?][+*?]',       # [abc]+*, etc.
        r'[+*?]\{[0-9,]+\}[+*?]',      # a{2,}+, etc.
        r'\)\)+[+*?]',                 # ))+ nested groups with quantifier
        r'\([^)]*\([^)]*[+*?]\)',      # ((x+)) nested capturing groups
    ]

    for dangerous_pattern in nested_quantifier_patterns:
        try:
            if regex_module.search(dangerous_pattern, pattern):
                return False
        except regex_module.error:
            return False

    # Also try to compile to catch syntax errors early
    try:
        regex_module.compile(pattern)
    except regex_module.error:
        return False

    return True


def command_matches_pattern(command: str, pattern: str, pattern_type: str = "literal") -> bool:
    """
    Check if a command matches a blocked pattern.

    Args:
        command: Full command string
        pattern: Pattern to match against
        pattern_type: One of "literal", "regex", "glob" (default: "literal")

    Returns:
        True if command matches pattern

    Security:
        Regex patterns are validated against ReDoS attacks before execution.
        Invalid or dangerous patterns fall back to literal substring matching.
    """
    import fnmatch

    command = command.strip()
    pattern = pattern.strip()

    if not command or not pattern:
        return False

    # Regex matching with ReDoS protection
    if pattern_type == "regex":
        # Validate pattern safety before execution
        if not is_safe_regex_pattern(pattern):
            log_info(f"Unsafe regex pattern rejected (ReDoS protection): {pattern[:50]}")
            # Fall back to literal matching for safety
            return pattern in command
        try:
            return bool(regex_module.search(pattern, command))
        except regex_module.error:
            # Invalid regex, fall back to literal substring match
            return pattern in command

    # Glob matching
    if pattern_type == "glob":
        return fnmatch.fnmatch(command, pattern)

    # Literal matching (default) - use AST-based detection to avoid substring bugs
    # The AST-based function correctly handles:
    # - "/cr:plannew" does NOT match "rm" (substring in "plannew")
    # - "sudo rm file" DOES match "rm" (command position)
    # - "echo rm" does NOT match "rm" (argument position)
    if AST_COMMAND_DETECTION_AVAILABLE:
        return ast_command_matches_pattern(command, pattern)

    # Fallback if AST detection unavailable: use safer heuristics
    # Exact match
    if command == pattern:
        return True

    # Command name match (pattern is just the command)
    # Split by shell operators and spaces
    command_parts = regex_module.split(r'[|&;\s]+', command)
    if pattern in command_parts:
        return True

    # Multi-word pattern match (e.g., "dd if=")
    if ' ' in pattern:
        if pattern in command:
            return True

    # Pattern starts with command name
    if command.startswith(pattern + ' '):
        return True

    return False


# =============================================================================
# Predicate Functions for Conditional Command Blocking
# =============================================================================
# These predicates return True to block (condition met), False to allow.
# Used by DEFAULT_INTEGRATIONS entries with "when" field.
# =============================================================================

def _has_unstaged_changes(command: str) -> bool:
    """Return True to block if there are unstaged changes (work to lose)."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True, text=True, timeout=2
        )
        return bool(result.stdout.strip())
    except Exception:
        return True  # Fail-safe: block on error


def _file_has_unstaged_changes(command: str) -> bool:
    """Return True to block if specific file has unstaged changes."""
    try:
        import shlex
        parts = shlex.split(command)
        file_path = parts[-1] if len(parts) > 1 else None
        # Handle "git checkout -- file" pattern
        if "--" in parts:
            idx = parts.index("--")
            file_path = parts[idx + 1] if idx + 1 < len(parts) else None
        if not file_path:
            return True  # Can't determine file, fail-safe: block
        result = subprocess.run(
            ["git", "diff", "--name-only", "--", file_path],
            capture_output=True, text=True, timeout=2
        )
        return bool(result.stdout.strip())
    except Exception:
        return True  # Fail-safe: block on error


def _stash_exists(command: str) -> bool:
    """Return True to block if stash has entries (something to lose)."""
    try:
        result = subprocess.run(
            ["git", "stash", "list"],
            capture_output=True, text=True, timeout=2
        )
        return bool(result.stdout.strip())
    except Exception:
        return True  # Fail-safe: block on error


# Wrapper for _not_in_pipe to match predicate signature
def _not_in_pipe_predicate(command: str) -> bool:
    """Wrapper for _not_in_pipe that takes command string instead of context.

    Returns True to BLOCK (not in pipe), False to ALLOW (in pipe).
    """
    try:
        from unittest.mock import MagicMock
        ctx = MagicMock()
        ctx.tool_input = {'command': command}
        return _not_in_pipe(ctx)
    except Exception as e:
        # Predicate evaluation failed - log details and fail-safe to BLOCK
        print(f"ERROR: Predicate '_not_in_pipe' evaluation failed", file=sys.stderr)
        print(f"  Command: {command[:100]}...", file=sys.stderr)
        print(f"  Exception: {type(e).__name__}: {e}", file=sys.stderr)
        print(f"  IMPACT: Command will be blocked (fail-safe behavior)", file=sys.stderr)
        return True  # Fail-safe: block on error


# Predicate lookup table
# CRITICAL: _not_in_pipe MUST be in this table for pipe detection to work
_PREDICATES = {
    "_has_unstaged_changes": _has_unstaged_changes,
    "_file_has_unstaged_changes": _file_has_unstaged_changes,
    "_stash_exists": _stash_exists,
    "_not_in_pipe": _not_in_pipe_predicate,  # Task #17: Allow grep/head/tail/cat in pipes
}

# Verify CONFIG references only defined predicates
# This catches mismatches between CONFIG['default_integrations'] and _PREDICATES
_undefined_predicates = set()
for pattern, config in CONFIG.get("default_integrations", {}).items():
    when_predicate = config.get("when")
    if when_predicate and when_predicate not in _PREDICATES:
        _undefined_predicates.add((pattern, when_predicate))

if _undefined_predicates:
    print("=" * 70, file=sys.stderr)
    print("CONFIGURATION ERROR: Predicate Mismatch Detected", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(f"Found {len(_undefined_predicates)} command pattern(s) referencing undefined predicates:", file=sys.stderr)
    for pattern, predicate in sorted(_undefined_predicates):
        print(f"  - Pattern '{pattern}' uses undefined predicate '{predicate}'", file=sys.stderr)
    print(f"\nDefined predicates: {sorted(_PREDICATES.keys())}", file=sys.stderr)
    print("\nIMPACT: Commands matching these patterns will be incorrectly blocked", file=sys.stderr)
    print("ACTION: Add missing predicates to _PREDICATES lookup table in main.py", file=sys.stderr)
    print("=" * 70, file=sys.stderr)


def should_block_command(session_id: str, command: str) -> Optional[Dict]:
    """
    Check if a command should be blocked.

    Args:
        session_id: Claude session identifier
        command: Command string to check

    Returns:
        Block dict with 'pattern', 'suggestion', 'pattern_type' if blocked, None otherwise
    """
    # Check session blocks first (highest priority)
    for block in get_session_blocks(session_id):
        pattern_type = block.get("pattern_type", "literal")
        if command_matches_pattern(command, block["pattern"], pattern_type):
            return block

    # Check global blocks (fallback)
    for block in get_global_blocks():
        pattern_type = block.get("pattern_type", "literal")
        if command_matches_pattern(command, block["pattern"], pattern_type):
            return block

    # Check default integrations (built-in safety rules)
    for pattern, config in CONFIG["default_integrations"].items():
        if command_matches_pattern(command, pattern):
            # Skip if action is "warn" (warn = allow + message, not block)
            # This allows warn integrations to pass through without blocking
            if config.get("action") == "warn":
                continue  # Don't block - warning will be injected separately

            # Check predicate if present (when field)
            when_name = config.get("when")
            if when_name and when_name in _PREDICATES:
                if not _PREDICATES[when_name](command):
                    continue  # Predicate says allow (no changes to lose)

            result = {
                "pattern": pattern,
                "suggestion": config["suggestion"],
                "pattern_type": "literal"
            }
            # Add optional fields if present
            if config.get("commands"):
                result["commands"] = config["commands"]
            if config.get("commands_description"):
                result["commands_description"] = config["commands_description"]
            return result

    # Not blocked
    return None


def get_command_warning(session_id: str, command: str) -> Optional[str]:
    """
    Get warning message for command if any warn integration matches.

    This is used to inject warning messages for commands that match
    integrations with action: "warn" (allow + message).

    Args:
        session_id: Claude session identifier (for future session-specific warns)
        command: Command string to check

    Returns:
        Warning message if a warn integration matches, None otherwise
    """
    for pattern, config in CONFIG["default_integrations"].items():
        if config.get("action") == "warn" and command_matches_pattern(command, pattern):
            return config.get("suggestion", "")
    return None


# =============================================================================
# CLAUDE CODE HOOK RESPONSE SEMANTICS
# Documentation: https://code.claude.com/docs/en/hooks
# =============================================================================
#
# COMMON FIELDS (all hooks):
#   "continue": true      - Claude continues after hook runs (DEFAULT)
#   "continue": false     - Claude STOPS processing after hooks run
#   "stopReason": "..."   - Message shown when continue is false
#   "systemMessage": "..."- Warning/info message shown to user
#   "suppressOutput": bool- Whether to suppress tool output
#
# STOP/SUBAGENTSTOP SPECIFIC FIELDS:
#   "decision": "block"   - PREVENTS Claude from stopping (makes it CONTINUE)
#   "reason": "..."       - REQUIRED when blocking - tells Claude what to do next
#
# CRITICAL: For Stop hooks to keep Claude working:
#   - Use continue=True + decision="block" + reason="instructions"
#   - Do NOT use continue=False thinking it "blocks the stop" - that makes Claude STOP!
#
# See stop_handler() at line ~1431 for usage examples.
# =============================================================================

def build_hook_response(continue_execution=True, stop_reason="", system_message="",
                        decision=None, reason=None):
    """Build standardized JSON hook response.

    For Stop/SubagentStop hooks that need to keep Claude working:
    - Set continue_execution=True (default)
    - Set decision="block" to prevent the stop
    - Set reason="..." with instructions for Claude

    See documentation block above for full semantics.
    """
    response = {"continue": continue_execution, "stopReason": stop_reason,
                "suppressOutput": False, "systemMessage": system_message}
    # Stop-hook-specific fields for blocking stops
    if decision is not None:
        response["decision"] = decision
    if reason is not None:
        response["reason"] = reason
    return response

def build_pretooluse_response(decision="allow", reason=""):
    """Build PreToolUse hook response for permission decisions

    The 'continue' field should always be True for PreToolUse hooks to allow
    the conversation to continue. The 'permissionDecision' field controls
    whether the specific tool is allowed or denied.
    """
    return {"continue": True, "stopReason": "", "suppressOutput": False,
            "systemMessage": json.dumps(reason)[1:-1] if reason else "",
            "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                  "permissionDecisionReason": json.dumps(reason)[1:-1] if reason else ""}}


def has_valid_justification(*texts: str) -> bool:
    """
    Check if any provided text contains a valid AUTOFILE_JUSTIFICATION tag.

    Validates that the content between tags is not:
    - The default placeholder "reason"
    - Empty or whitespace-only

    Args:
        *texts: One or more strings to search (transcript, file content, etc.)

    Returns:
        True if valid justification found, False otherwise
    """
    combined = " ".join(texts)
    excluded = {"reason", ""}
    pattern = r'<AUTOFILE_JUSTIFICATION>(.*?)</AUTOFILE_JUSTIFICATION>'
    matches = regex_module.findall(pattern, combined, regex_module.DOTALL | regex_module.IGNORECASE)
    return any(m.strip().lower() not in excluded for m in matches)


# Ultra-efficient dispatch system - O(1) command lookup via decorator pattern
HANDLERS = {}
def handler(name):
    """Decorator to register command handlers in dispatch table"""
    def dec(f):
        HANDLERS[name] = f
        return f
    return dec

def _manage_monitor(state: dict, action: str):
    """Centralized helper for all ai-monitor process management."""
    if not ai_monitor or "session_id" not in state:
        return
    session_id = state["session_id"]
    if action == 'start':
        if state.get("ai_monitor_pid"):
            ai_monitor.stop_monitor(session_id)
        pid = ai_monitor.start_monitor(
            session_id=session_id, prompt="continue working",
            stop_marker=CONFIG["stage3_message"], max_cycles=20, prompt_on_start=True
        )
        log_info(f"Started ai-monitor for session {session_id} with PID: {pid}")
        state["ai_monitor_pid"] = pid
    elif action == 'stop':
        if state.get("ai_monitor_pid"):
            ai_monitor.stop_monitor(session_id)
            log_info(f"Stopped monitor for session {session_id}")
            state["ai_monitor_pid"] = None


# Command handlers - core policy and control commands
def handle_search(state):
    """Handle SEARCH command - update state and return response"""
    state["file_policy"] = "SEARCH"
    policy_name, policy_desc = CONFIG['policies']['SEARCH']
    return f"AutoFile policy: {policy_name} - {policy_desc}"

def handle_allow(state):
    """Handle ALLOW command - update state and return response"""
    state["file_policy"] = "ALLOW"
    policy_name, policy_desc = CONFIG['policies']['ALLOW']
    return f"AutoFile policy: {policy_name} - {policy_desc}"

def handle_justify(state):
    """Handle JUSTIFY command - update state and return response"""
    state["file_policy"] = "JUSTIFY"
    policy_name, policy_desc = CONFIG['policies']['JUSTIFY']
    return f"AutoFile policy: {policy_name} - {policy_desc}"

def handle_status(state):
    """Handle STATUS command - return current policy"""
    policy = state.get("file_policy", "ALLOW")
    policy_name = CONFIG["policies"][policy][0]
    return f"Current policy: {policy_name}"

def handle_stop(state):
    """Handle STOP command - update state and return response"""
    # Note: session_id must be in state (added by intercept_commands caller)
    _manage_monitor(state, 'stop')
    state["session_status"] = "stopped"
    return "Autorun stopped"

def handle_emergency_stop(state):
    """Handle EMERGENCY_STOP command - update state and return response"""
    log_info("Emergency stop: autorun session")
    # Note: session_id must be in state (added by intercept_commands caller)
    _manage_monitor(state, 'stop')
    state["session_status"] = "emergency_stopped"
    return "Emergency stop activated"

def handle_activate(state, prompt=""):
    """Handle AUTORUN activation - complete autorun setup with injection template and tmux standards"""
    log_info("Activating autorun: autorun session")

    # Preserve file_policy and session_id before clearing
    old_file_policy = state.get("file_policy", "ALLOW")
    old_session_id = state.get("session_id")

    # Enforce tmux session standards if available
    if TMUX_UTILS_AVAILABLE:
        try:
            tmux_utils = get_global_tmux_utils()
            if tmux_utils:
                # Ensure default "clautorun" session exists and is available
                tmux_utils.ensure_session_exists()
                session_info = tmux_utils.get_session_info()
                log_info(f"tmux session ensured: {session_info['session']} (tmux active: {session_info['tmux_active']})")

                # Store tmux session info in state for monitoring integration
                state["tmux_session"] = session_info["session"]
                state["tmux_active"] = session_info["tmux_active"]
        except Exception as e:
            log_info(f"tmux session setup failed: {e}")

    # Clear and setup session state for new command
    state.clear()
    state.update({
        "session_status": "active",
        "autorun_stage": "INITIAL",
        "activation_prompt": prompt,
        "verification_attempts": 0,
        "file_policy": old_file_policy,
        "session_id": old_session_id  # Restore session_id for monitor
    })

    # Start monitor AFTER state is set up with session_id
    _manage_monitor(state, 'start')

    # Generate injection template with current policy
    policy = state["file_policy"]
    policy_instructions = CONFIG["policies"][policy][1]

    injection = CONFIG["injection_template"].format(
        emergency_stop=CONFIG["emergency_stop"],
        stage1_instruction=CONFIG["stage1_instruction"],
        stage1_message=CONFIG["stage1_message"],
        stage2_instruction=CONFIG["stage2_instruction"],
        stage2_message=CONFIG["stage2_message"],
        stage3_instruction=CONFIG["stage3_instruction"],
        stage3_message=CONFIG["stage3_message"],
        stage3_instructions=get_stage3_instructions(state),
        policy_instructions=policy_instructions
    )

    return injection

# Additional control commands
COMMAND_HANDLERS = {
    "SEARCH": handle_search,
    "ALLOW": handle_allow,
    "JUSTIFY": handle_justify,
    "STATUS": handle_status,
    "status": handle_status,  # Add lowercase version for /afst command
    "stop": handle_stop,         # Add lowercase version for /autostop command
    "STOP": handle_stop,
    "emergency_stop": handle_emergency_stop,  # Add lowercase version for /estop command
    "EMERGENCY_STOP": handle_emergency_stop,
    "activate": handle_activate
}


# =============================================================================
# Command Blocking Handlers
# =============================================================================

def handle_block_pattern(state):
    """
    Handle /cr:no <pattern> [description] command.

    Blocks a command pattern in the current session.

    Usage:
        /cr:no rm                                  # Block rm command (uses DEFAULT_INTEGRATIONS)
        /cr:no dd if=                              # Block dd with input
        /cr:no "exec(" unsafe function             # Block with custom description
        /cr:no regex:\\b(eval|assert)\\(            # Block with regex pattern
        /cr:no glob:*.tmp temporary files           # Block with glob pattern

    Examples:
        /cr:no rm
        /cr:no "rm -rf"
        /cr:no dd if=
        /cr:no "exec(" unsafe eval function - use alternatives
        /cr:no regex:eval\\( dangerous eval usage
        /cr:no glob:*.tmp no temporary files allowed
    """
    from .config import DEFAULT_INTEGRATIONS

    prompt = state.get("activation_prompt", "")
    session_id = state.get("session_id", "")

    # Extract pattern from prompt
    # Format: "/cr:no <pattern> [description]"
    parts = prompt.split(None, 1)
    if len(parts) < 2:
        return f"❌ Usage: /cr:no <pattern> [description]\n" \
               f"\n" \
               f"Examples:\n" \
               f"  /cr:no rm\n" \
               f"  /cr:no dd if=\n" \
               f'  /cr:no "exec(" unsafe function\n' \
               f"  /cr:no regex:eval\\( dangerous\n" \
               f"  /cr:no glob:*.tmp temporary files"

    try:
        pattern, description, pattern_type = parse_pattern_and_description(parts[1])
    except ValueError as e:
        return f"❌ Error: {e}\n\n" \
               f"Usage: /cr:no <pattern> [description]"

    # Add block to session state
    added = add_session_block(session_id, pattern, description, pattern_type)

    if added:
        # Build suggestion for display
        if description:
            suggestion = description
        else:
            integration = DEFAULT_INTEGRATIONS.get(pattern, {})
            suggestion = integration.get("suggestion", f"Pattern '{pattern}' blocked")

        type_indicator = f" ({pattern_type})" if pattern_type != "literal" else ""

        return f"✅ Blocked: {pattern}{type_indicator}\n" \
               f"💡 {suggestion}\n\n" \
               f"Session blocks: {len(get_session_blocks(session_id))}\n" \
               f"Commands: /cr:ok {pattern} | /cr:clear | /cr:status"
    else:
        return f"⚠️ Pattern '{pattern}' is already blocked in this session.\n" \
               f"Commands: /cr:status | /cr:clear {pattern}"


def handle_allow_pattern(state):
    """
    Handle /cr:ok <pattern> command.

    Allows a previously blocked command pattern in the current session.

    Usage:
        /cr:ok rm              # Allow rm command
        /cr:ok dd if=          # Allow dd with input

    Examples:
        /cr:ok rm
        /cr:ok "rm -rf"
    """
    prompt = state.get("activation_prompt", "")
    session_id = state.get("session_id", "")

    # Extract pattern from prompt
    # Use split with maxsplit=1 to preserve patterns with spaces
    parts = prompt.split(None, 1)
    if len(parts) < 2:
        return f"❌ Usage: /cr:ok <pattern>\n" \
               f"Example: /cr:ok rm"

    pattern = parts[1].strip()

    # Remove block from session state
    removed = remove_session_block(session_id, pattern)

    if removed:
        return f"✅ Allowed: {pattern}\n\n" \
               f"Session blocks: {len(get_session_blocks(session_id))}\n" \
               f"Commands: /cr:no {pattern} | /cr:status"
    else:
        return f"⚠️ Pattern '{pattern}' was not blocked in this session.\n" \
               f"Commands: /cr:status | /cr:no {pattern}"


def handle_clear_pattern(state):
    """
    Handle /cr:clear [pattern] command.

    Clears session blocks (all or specific pattern).
    Falls back to global defaults.

    Usage:
        /cr:clear             # Clear all session blocks
        /cr:clear rm          # Clear specific pattern

    Examples:
        /cr:clear
        /cr:clear rm
    """
    prompt = state.get("activation_prompt", "")
    session_id = state.get("session_id", "")

    # Extract optional pattern
    # Use split with maxsplit=1 to preserve patterns with spaces
    parts = prompt.split(None, 1)
    pattern = parts[1].strip() if len(parts) > 1 else None

    # Clear blocks
    count = clear_session_blocks(session_id, pattern)

    if pattern:
        return f"🔄 Cleared: {pattern}\n" \
               f"Using global defaults now.\n" \
               f"Session blocks: {len(get_session_blocks(session_id))}"
    else:
        # Get global block count
        global_count = len(get_global_blocks())

        return f"🔄 Cleared all session blocks.\n" \
               f"Using global defaults ({global_count} patterns).\n" \
               f"Commands: /cr:status | /cr:globalstatus"


def handle_block_status(state):
    """
    Handle /cr:status command.

    Shows current blocked patterns for session and global.

    Usage:
        /cr:status

    Example output:
        Session blocks (2):
          - rm → Use 'trash' instead
          - dd if= → Avoid direct disk writes

        Global blocks (1):
          - --force → Avoid force flags
    """
    session_id = state.get("session_id", "")

    # Get session blocks
    session_blocks = get_session_blocks(session_id)
    global_blocks = get_global_blocks()

    # Build status message
    lines = ["📊 Command Blocking Status\n"]

    if session_blocks:
        lines.append(f"Session blocks ({len(session_blocks)}):")
        for block in session_blocks:
            pattern_type = block.get('pattern_type', 'literal')
            type_suffix = f" ({pattern_type})" if pattern_type != 'literal' else ""
            lines.append(f"  - {block['pattern']}{type_suffix}")
            if block.get('suggestion'):
                lines.append(f"    → {block['suggestion']}")
    else:
        lines.append("Session blocks: None (using global defaults)")

    lines.append("")

    if global_blocks:
        lines.append(f"Global blocks ({len(global_blocks)}):")
        for block in global_blocks:
            pattern_type = block.get('pattern_type', 'literal')
            type_suffix = f" ({pattern_type})" if pattern_type != 'literal' else ""
            lines.append(f"  - {block['pattern']}{type_suffix}")
            if block.get('suggestion'):
                lines.append(f"    → {block['suggestion']}")
    else:
        lines.append("Global blocks: None")

    lines.append("")
    lines.append("Commands: /cr:no <pattern> | /cr:ok <pattern> | /cr:clear | /cr:globalno <pattern>")
    lines.append("")
    lines.append("Pattern types: literal (default) | regex:<pattern> | glob:<pattern>")

    return "\n".join(lines)


def handle_global_block_pattern(state):
    """
    Handle /cr:globalno <pattern> [description] command.

    Sets a global default to block a pattern.
    Affects all sessions that don't have session-specific overrides.

    Usage:
        /cr:globalno rm                                  # Block rm globally (uses DEFAULT_INTEGRATIONS)
        /cr:globalno dd if=                              # Block dangerous dd commands
        /cr:globalno "exec(" unsafe function             # Block with custom description
        /cr:globalno regex:\\b(eval|assert)\\(            # Block with regex pattern
        /cr:globalno glob:*.tmp temporary files           # Block with glob pattern

    Examples:
        /cr:globalno rm
        /cr:globalno --force
        /cr:globalno "exec(" unsafe exec function blocked
        /cr:globalno regex:eval\\( dangerous eval usage
        /cr:globalno glob:*.tmp no temporary files
    """
    from .config import DEFAULT_INTEGRATIONS

    prompt = state.get("activation_prompt", "")

    # Extract pattern from prompt
    # Format: "/cr:globalno <pattern> [description]"
    parts = prompt.split(None, 1)
    if len(parts) < 2:
        return f"❌ Usage: /cr:globalno <pattern> [description]\n" \
               f"\n" \
               f"Examples:\n" \
               f"  /cr:globalno rm\n" \
               f"  /cr:globalno dd if=\n" \
               f'  /cr:globalno "exec(" unsafe function\n' \
               f"  /cr:globalno regex:eval\\( dangerous\n" \
               f"  /cr:globalno glob:*.tmp temporary files"

    try:
        pattern, description, pattern_type = parse_pattern_and_description(parts[1])
    except ValueError as e:
        return f"❌ Error: {e}\n\n" \
               f"Usage: /cr:globalno <pattern> [description]"

    # Add global block
    added = add_global_block(pattern, description, pattern_type)

    if added:
        # Build suggestion for display
        if description:
            suggestion = description
        else:
            integration = DEFAULT_INTEGRATIONS.get(pattern, {})
            suggestion = integration.get("suggestion", f"Pattern '{pattern}' blocked")

        type_indicator = f" ({pattern_type})" if pattern_type != "literal" else ""

        return f"✅ Global block: {pattern}{type_indicator}\n" \
               f"💡 {suggestion}\n\n" \
               f"Global blocks: {len(get_global_blocks())}\n" \
               f"Commands: /cr:globalok {pattern} | /cr:globalstatus"
    else:
        return f"⚠️ Pattern '{pattern}' is already globally blocked.\n" \
               f"Commands: /cr:globalstatus"


def handle_global_allow_pattern(state):
    """
    Handle /cr:globalok <pattern> command.

    Removes a global block for a pattern.

    Usage:
        /cr:globalok rm        # Allow rm globally

    Examples:
        /cr:globalok rm
        /cr:globalok --force
    """
    prompt = state.get("activation_prompt", "")

    # Extract pattern from prompt
    # Use split with maxsplit=1 to preserve patterns with spaces
    parts = prompt.split(None, 1)
    if len(parts) < 2:
        return f"❌ Usage: /cr:globalok <pattern>\n" \
               f"Example: /cr:globalok rm"

    pattern = parts[1].strip()

    # Remove global block
    removed = remove_global_block(pattern)

    if removed:
        return f"✅ Global allow: {pattern}\n\n" \
               f"Global blocks: {len(get_global_blocks())}\n" \
               f"Commands: /cr:globalno {pattern} | /cr:globalstatus"
    else:
        return f"⚠️ Pattern '{pattern}' was not globally blocked.\n" \
               f"Commands: /cr:globalstatus"


def handle_global_block_status(state):
    """
    Handle /cr:globalstatus command.

    Shows globally blocked patterns.

    Usage:
        /cr:globalstatus

    Example output:
        Global blocks (2):
          - rm → Use 'trash' instead
          - dd if= → Avoid direct disk writes
    """
    global_blocks = get_global_blocks()

    # Build status message
    lines = ["🌐 Global Command Blocks\n"]

    if global_blocks:
        lines.append(f"Global blocks ({len(global_blocks)}):")
        for block in global_blocks:
            lines.append(f"  - {block['pattern']}")
            if block.get('suggestion'):
                lines.append(f"    → {block['suggestion']}")
    else:
        lines.append("Global blocks: None")

    lines.append("")
    lines.append("Commands: /cr:globalno <pattern> | /cr:globalok <pattern> | /cr:status")

    return "\n".join(lines)


# Update COMMAND_HANDLERS to include blocking handlers
COMMAND_HANDLERS.update({
    "BLOCK_PATTERN": handle_block_pattern,
    "ALLOW_PATTERN": handle_allow_pattern,
    "CLEAR_PATTERN": handle_clear_pattern,
    "GLOBAL_BLOCK_PATTERN": handle_global_block_pattern,
    "GLOBAL_ALLOW_PATTERN": handle_global_allow_pattern,
    "GLOBAL_BLOCK_STATUS": handle_global_block_status,
})


# Claude Code hook handlers - ultra-compact
@handler("UserPromptSubmit")
def claude_code_handler(ctx):
    """Claude Code UserPromptSubmit hook - sync command processing"""
    prompt = ctx.prompt.strip()
    session_id = ctx.session_id

    # Efficient command detection - O(1) lookup via command_mappings
    command = next((v for k, v in CONFIG["command_mappings"].items() if k == prompt), None)
    if not command:
        # Check for commands that support arguments (autorun)
        command = next((v for k, v in CONFIG["command_mappings"].items() if prompt.startswith(k)), None)

    if command and command in COMMAND_HANDLERS:
        # Handle command locally, don't send to AI
        with session_state(session_id) as state:
            state['session_id'] = session_id
            if command == "activate":
                response = COMMAND_HANDLERS[command](state, prompt)
                # Autorun command should NOT continue to AI - injection template is complete
                return build_hook_response(False, "", response)
            elif command in ["stop", "emergency_stop"]:
                response = COMMAND_HANDLERS[command](state)
                # Stop commands should NOT continue to AI
                return build_hook_response(False, "", response)
            else:
                response = COMMAND_HANDLERS[command](state)
                # Policy and status commands should continue to AI
                return build_hook_response(True, "", response)

    # Let AI handle non-commands
    return build_hook_response()

@handler("PreToolUse")
def pretooluse_handler(ctx):
    """PreToolUse hook - command blocking and file policy enforcement"""
    # =========================================================================
    # NEW: Command blocking check (highest priority)
    # =========================================================================
    if ctx.tool_name in BASH_TOOLS:
        # Handle None tool_input gracefully (defensive programming)
        tool_input = ctx.tool_input or {}
        command = tool_input.get("command", "")

        # Check if command should be blocked
        block_info = should_block_command(ctx.session_id, command)

        if block_info:
            # Command is blocked
            pattern = block_info["pattern"]
            suggestion = block_info.get("suggestion", f"Pattern '{pattern}' is blocked")

            return build_pretooluse_response(
                decision="deny",
                reason=f"Command blocked: {pattern}\n{suggestion}\n\n"
                       f"To allow in this session: /cr:ok {pattern}\n"
                       f"To show status: /cr:status"
            )

        # Check for warnings (allow but show message for action: "warn")
        warning = get_command_warning(ctx.session_id, command)
        if warning:
            return build_pretooluse_response("allow", warning)

    # =========================================================================
    # EXISTING: AutoFile policy enforcement
    # =========================================================================
    # Extract file path from Write tool input (handle None gracefully)
    tool_input = ctx.tool_input or {}
    file_path = tool_input.get("file_path", "")

    # Debug logging using log_info for consistent logging
    log_info(f"PreToolUse Debug: Tool Name: {ctx.tool_name}")
    log_info(f"PreToolUse Debug: File Path: {file_path}")
    log_info(f"PreToolUse Debug: Tool Input: {ctx.tool_input}")

    # Apply file creation policies - enhanced based on test expectations
    session_id = ctx.session_id
    with session_state(session_id) as state:
        file_policy = state.get("file_policy", "ALLOW")

        # For non-Write tools, always allow - file policies only apply to file creation
        # The Write tool is the only tool that creates files, so other tools don't need policy checks
        if ctx.tool_name not in WRITE_TOOLS:
            return build_pretooluse_response("allow", "Non-Write tool allowed - file policies only apply to Write tool")

        # Write tools - always apply policy
        if file_policy == "SEARCH":
            # Extensive logging for SEARCH policy enforcement
            log_info("PreToolUse SEARCH Policy Debug:")
            log_info(f"  Current Policy: {file_policy}")
            log_info(f"  File Path: {file_path}")

            # Security: Normalize and validate path before existence check
            # This prevents some path traversal edge cases, though Claude Code's
            # sandbox provides the primary protection against unauthorized access.
            file_exists = False
            if file_path:
                try:
                    # Resolve to absolute path (normalizes ../.. sequences)
                    resolved_path = Path(file_path).resolve()
                    file_exists = resolved_path.exists() and resolved_path.is_file()
                    log_info(f"  Resolved Path: {resolved_path}")
                    log_info(f"  Path Exists (file): {file_exists}")
                except (OSError, ValueError) as e:
                    # Invalid path - treat as non-existent
                    log_info(f"  Path resolution error: {e}")
                    file_exists = False

            log_info(f"  Session State: {state}")

            # SEARCH policy blocks new file creation but allows editing existing files
            if file_path and file_exists:
                # File exists - allow editing
                log_info("  Decision: ALLOW (Existing file modification)")
                return build_pretooluse_response("allow", "Existing file modification allowed under SEARCH policy")
            else:
                # No file path or file doesn't exist - block new file creation
                # Use policy description which contains "NO new files" as expected by tests
                log_info("  Decision: DENY (No file or does not exist)")
                return build_pretooluse_response("deny", f"SEARCH policy: {CONFIG['policies']['SEARCH'][1]}")

        elif file_policy == "JUSTIFY":
            # JUSTIFY policy: Allow existing file modifications, require justification for NEW files only
            # Check if file exists first
            file_exists = False
            if file_path:
                try:
                    resolved_path = Path(file_path).resolve()
                    file_exists = resolved_path.exists() and resolved_path.is_file()
                except (OSError, ValueError):
                    file_exists = False

            # If file exists, allow modification without justification
            if file_exists:
                return build_pretooluse_response("allow", "Existing file modification allowed under JUSTIFY policy")

            # For NEW files, check for justification in state flag, transcript, OR Write tool content
            justification_found = (
                state.get("autofile_justification_detected", False) or
                has_valid_justification(str(ctx.session_transcript), str(ctx.tool_input.get("content", "")))
            )
            if not justification_found:
                return build_pretooluse_response("deny", f"JUSTIFY policy: {CONFIG['policies']['JUSTIFY'][1]}")

        # ALLOW policy - allow all operations
        return build_pretooluse_response("allow", "File operations allowed under ALLOW policy")

# ai_monitor integration: Continuation enforcement functions
def inject_continue_prompt(state):
    """Inject continue working prompt - ai_monitor functionality with monitoring"""
    log_info("Injecting continue working prompt - preventing premature stop")

    # Get injection monitor
    monitor = get_injection_monitor()
    injection_start_time = time.time()
    session_id = state.get("session_id", "unknown")

    # Determine injection method based on current context
    injection_method = InjectionMethod.HOOK_INTEGRATION
    if ai_monitor and state.get("ai_monitor_pid"):
        injection_method = InjectionMethod.TMUX_INJECTION

    # CRITICAL: Use full injection template with stop signal instructions
    # This is NOT a simple continue message - it includes critical stop conditions
    policy = state.get("file_policy", "ALLOW")
    policy_instructions = CONFIG["policies"][policy][1]

    continue_message = CONFIG["injection_template"].format(
        emergency_stop=CONFIG["emergency_stop"],
        stage1_instruction=CONFIG["stage1_instruction"],
        stage1_message=CONFIG["stage1_message"],
        stage2_instruction=CONFIG["stage2_instruction"],
        stage2_message=CONFIG["stage2_message"],
        stage3_instruction=CONFIG["stage3_instruction"],
        stage3_message=CONFIG["stage3_message"],
        stage3_instructions=get_stage3_instructions(state),
        policy_instructions=policy_instructions
    )

    # Record injection attempt
    if monitor:
        try:
            # Calculate context size from transcript
            transcript_length = len(str(state.get("transcript", "")))

            monitor.record_injection_attempt(
                method=injection_method,
                session_id=session_id,
                prompt_type="continue",
                prompt_content=continue_message[:200] + "...",  # Truncate for storage
                outcome=InjectionOutcome.SUCCESS,  # Assume success initially
                response_time_ms=0,  # Will be updated after response
                success_indicators=["Continue prompt injected"],
                context_size=0,
                transcript_length=transcript_length,
                follow_up_required=False,
                user_intervention=False
            )

            # Store injection attempt ID for later response time tracking
            state["last_injection_attempt_id"] = f"{session_id}_{int(injection_start_time * 1000)}"
            state["last_injection_start_time"] = injection_start_time

        except Exception as e:
            log_info(f"Failed to record injection attempt: {e}")

    # Keep Claude working by blocking the stop - see docs at build_hook_response() (~line 546)
    return build_hook_response(
        continue_execution=True,
        stop_reason="",
        system_message=continue_message,
        decision="block",
        reason=continue_message
    )

def inject_verification_prompt(state):
    """Inject verification prompt - enhanced two-stage verification with forced compliance and monitoring"""
    verification_attempts = state.get('verification_attempts', 1)
    log_info(f"Injecting verification prompt - attempt {verification_attempts}")

    # Get injection monitor
    monitor = get_injection_monitor()
    injection_start_time = time.time()
    session_id = state.get("session_id", "unknown")

    # Determine injection method and prompt type
    injection_method = InjectionMethod.HOOK_INTEGRATION
    if ai_monitor and state.get("ai_monitor_pid"):
        injection_method = InjectionMethod.TMUX_INJECTION

    # Determine prompt type based on verification attempts
    if verification_attempts > CONFIG["max_recheck_count"] - 1:
        prompt_type = "forced_compliance"
    else:
        prompt_type = "verification"

    # Initialize verification engine if available
    verification_requirements = ""
    if VERIFICATION_ENGINE_AVAILABLE and verification_attempts == 1:
        try:
            engine = RequirementVerificationEngine(state.get("session_id", "default"))
            activation_prompt = state.get("activation_prompt", "")

            # Parse requirements from original task
            requirements = engine.parse_requirements_from_task(activation_prompt)

            if requirements:
                # Format requirements for prompt
                req_text = "8. SPECIFIC REQUIREMENTS TO VERIFY:\n"
                for i, req in enumerate(requirements[:5], 1):  # Limit to 5 requirements
                    req_text += f"   {i}. {req.description} (Type: {req.requirement_type.value}, Mandatory: {req.mandatory})\n"
                verification_requirements = req_text

                # Store engine in state for later use
                state["verification_engine"] = engine

        except Exception as e:
            log_info(f"Verification engine initialization failed: {e}")

    # Choose template based on verification attempts
    if verification_attempts > CONFIG["max_recheck_count"] - 1:
        # Force compliance on final attempt
        template = CONFIG["forced_compliance_template"]
        log_info("Using forced compliance template")
        success_indicators = ["Forced compliance activated"]
    else:
        template = CONFIG["recheck_template"]
        success_indicators = [f"Verification prompt injected (attempt {verification_attempts})"]

    verification_prompt = template.format(
        activation_prompt=state.get("activation_prompt", "original task"),
        stage3_message=CONFIG["stage3_message"],
        recheck_count=verification_attempts,
        max_recheck_count=CONFIG["max_recheck_count"],
        verification_requirements=verification_requirements
    )

    # Record injection attempt
    if monitor:
        try:
            # Calculate context size from transcript
            transcript_length = len(str(state.get("transcript", "")))

            monitor.record_injection_attempt(
                method=injection_method,
                session_id=session_id,
                prompt_type=prompt_type,
                prompt_content=verification_prompt[:200] + "...",  # Truncate for storage
                outcome=InjectionOutcome.SUCCESS,  # Assume success initially
                response_time_ms=0,  # Will be updated after response
                success_indicators=success_indicators,
                context_size=0,
                transcript_length=transcript_length,
                follow_up_required=verification_attempts < CONFIG["max_recheck_count"],
                user_intervention=False
            )

            # Store injection attempt ID for later response time tracking
            state["last_injection_attempt_id"] = f"{session_id}_{int(injection_start_time * 1000)}"
            state["last_injection_start_time"] = injection_start_time

        except Exception as e:
            log_info(f"Failed to record verification injection attempt: {e}")

    # Keep Claude working by blocking the stop - see docs at build_hook_response() (~line 546)
    return build_hook_response(
        continue_execution=True,
        stop_reason="",
        system_message=verification_prompt,
        decision="block",
        reason=verification_prompt
    )

def analyze_verification_results(state, transcript):
    """Analyze verification results using enhanced verification engine and transcript analyzer"""
    if not VERIFICATION_ENGINE_AVAILABLE or "verification_engine" not in state:
        return None

    try:
        engine = state["verification_engine"]
        activation_prompt = state.get("activation_prompt", "")

        # Enhanced transcript analysis using transcript analyzer
        transcript_analysis = None
        if TRANSCRIPT_ANALYZER_AVAILABLE:
            try:
                analyzer = TranscriptAnalyzer()
                transcript_analysis = analyzer.analyze_full_transcript(transcript, state.get("session_id", "default"))

                # Analyze task completion specifically
                task_completion = analyzer.analyze_task_completion(transcript, activation_prompt)
                state["task_completion_analysis"] = task_completion

                log_info(f"Transcript analysis found {transcript_analysis.total_evidence} evidence items")
            except Exception as e:
                log_info(f"Enhanced transcript analysis failed: {e}")

        # Analyze transcript for evidence using verification engine
        evidence_by_requirement = engine.analyze_transcript_evidence(transcript)

        # Enhance evidence with transcript analyzer results
        if transcript_analysis:
            evidence_by_requirement = _enhance_evidence_with_analysis(
                evidence_by_requirement, transcript_analysis
            )

        # Verify each requirement
        results = {}
        for req_id, evidence in evidence_by_requirement.items():
            result = engine.verify_single_requirement(req_id, evidence)
            results[req_id] = result

        # Generate verification report
        report = engine.generate_verification_report()

        # Enhance report with transcript analysis data
        if transcript_analysis:
            report["transcript_analysis"] = {
                "total_evidence": transcript_analysis.total_evidence,
                "confidence_score": transcript_analysis.confidence_score,
                "evidence_summary": transcript_analysis.summary,
                "high_confidence_evidence": transcript_analysis.summary.get("high_confidence_evidence", 0)
            }

        # Check if forced compliance is needed
        failed_mandatory = [
            req_id for req_id, result in results.items()
            if (engine.requirements.get(req_id) and
                engine.requirements[req_id].mandatory and
                result.status != VerificationStatus.COMPLETED)
        ]

        # Use enhanced completion confidence to determine forced compliance
        completion_confidence = state.get("task_completion_analysis", {}).get("completion_confidence", 0.0)
        should_force_compliance = (
            (failed_mandatory and state.get('verification_attempts', 1) >= CONFIG["max_recheck_count"]) or
            (completion_confidence > 0.7 and state.get('verification_attempts', 1) >= CONFIG["max_recheck_count"] - 1)
        )

        if should_force_compliance and failed_mandatory:
            log_info("Forcing compliance for failed mandatory requirements")
            engine.force_requirement_compliance(failed_mandatory)
            report["forced_compliance_reason"] = "Failed mandatory requirements after max attempts"

        # Store enhanced report in state
        state["verification_report"] = report
        state["transcript_analysis_result"] = transcript_analysis

        log_info(f"Enhanced verification analysis complete: {report['summary']['completed']}/{report['summary']['total_requirements']} completed")
        if transcript_analysis:
            log_info(f"Transcript confidence score: {transcript_analysis.confidence_score:.2f}")

        return report

    except Exception as e:
        log_info(f"Enhanced verification analysis failed: {e}")
        return None

def _enhance_evidence_with_analysis(evidence_by_requirement, transcript_analysis):
    """Enhance verification evidence with transcript analyzer results"""
    if not transcript_analysis:
        return evidence_by_requirement

    # Map transcript analyzer evidence types to verification engine evidence
    type_mapping = {
        EvidenceType.FILE_OPERATION: "file_operation",
        EvidenceType.TEST_RESULT: "test_result",
        EvidenceType.SUCCESS_INDICATOR: "success_indicator"
    }

    for req_id, evidence_list in evidence_by_requirement.items():
        # Add high-confidence evidence from transcript analyzer
        for evidence_type, mapped_type in type_mapping.items():
            if evidence_type in transcript_analysis.evidence_by_type:
                for analyzer_evidence in transcript_analysis.evidence_by_type[evidence_type]:
                    if analyzer_evidence.confidence in [ConfidenceLevel.HIGH, ConfidenceLevel.VERY_HIGH]:
                        # Convert analyzer evidence to verification engine format
                        enhanced_evidence = RequirementEvidence(
                            requirement_id=req_id,
                            evidence_type=mapped_type,
                            evidence_data=analyzer_evidence.content,
                            confidence_score=analyzer_evidence.confidence.value,
                            timestamp=analyzer_evidence.timestamp,
                            source_location=f"transcript_analyzer:{analyzer_evidence.position}"
                        )
                        evidence_list.append(enhanced_evidence)

    return evidence_by_requirement

def is_premature_stop(ctx, state):
    """Check if this is a premature stop - ai_monitor logic"""
    # Only active autorun sessions are protected
    if state.get("session_status") != "active":
        return False

    # Get transcript for analysis
    transcript = str(getattr(ctx, 'session_transcript', []))

    # Check if ANY stage confirmation is present (including descriptive completion marker)
    # NOTE: Markdown command files use the descriptive "completion_marker" string,
    # while the three-stage hook system uses stage1/2/3_confirmation strings.
    # The hook system recognizes BOTH for compatibility.
    if (CONFIG["stage1_message"] in transcript or
        CONFIG["stage2_message"] in transcript or
        CONFIG["stage3_message"] in transcript or
        CONFIG["completion_marker"] in transcript):
        return False  # Proper completion of some stage

    # Check if emergency stop was used
    if CONFIG["emergency_stop"] in transcript:
        return False  # Intentional emergency stop

    return True  # Premature stop - needs intervention

def get_stage3_instructions(state):
    """Get stage 3 instructions based on current state"""
    stage = state.get("autorun_stage", "INITIAL")
    hook_call_count = state.get("hook_call_count", 0)

    if stage == "STAGE2_COMPLETED":
        # After stage 2, start countdown for stage 3
        remaining_calls = CONFIG["stage3_countdown_calls"] - hook_call_count
        if remaining_calls > 0:
            return f"After {remaining_calls} more hook calls, Stage 3 instructions will be revealed. Continue with evaluation."
        else:
            return f"STAGE 3: {CONFIG['stage3_instruction']}. Output **{CONFIG['stage3_message']}** to complete."
    else:
        return "Complete Stage 1 before proceeding to Stage 2."

def should_trigger_stage2(state):
    """Check if we should trigger stage 2"""
    return (state.get("autorun_stage") == "INITIAL" and
            state.get("stage1_completed", False))

def should_trigger_verification(state):
    """Check if we should trigger verification stage"""
    return (state.get("autorun_stage") == "INITIAL" and
            state.get("verification_attempts", 0) < CONFIG["max_recheck_count"])

@handler("Stop")
@handler("SubagentStop")
def stop_handler(ctx):
    """Enhanced stop handler with three-stage completion system and AI monitor lifecycle management.

    STOP HOOK SEMANTICS - See documentation at build_hook_response() (~line 546)

    To KEEP CLAUDE WORKING from a Stop hook:
      return build_hook_response(True, "", msg, decision="block", reason=msg)

    To ALLOW CLAUDE TO STOP:
      return build_hook_response()  # defaults work
    """
    session_id = getattr(ctx, 'session_id', 'default')
    transcript = str(getattr(ctx, 'session_transcript', []))

    with session_state(session_id) as state:
        # Ensure session_id is in state for _manage_monitor
        state['session_id'] = session_id
        # Increment hook call count for stage 3 countdown
        state['hook_call_count'] = state.get('hook_call_count', 0) + 1

        # Check for plan acceptance trigger - activate autorun if plan was just accepted
        # Only trigger on main agent Stop, not SubagentStop (subagents may echo plan content)
        plan_marker = CONFIG.get("plan_accepted_marker", "PLAN ACCEPTED")
        hook_event = getattr(ctx, 'hook_event_name', 'Stop')
        is_main_agent_stop = hook_event == "Stop"
        if is_main_agent_stop and plan_marker in transcript and state.get("session_status") != "active":
            log_info(f"Plan acceptance detected, activating autorun for session {session_id}")
            injection = handle_activate(state, "Execute the accepted plan per the Plan Acceptance and Execution Protocol")
            # KEEP CLAUDE WORKING: decision="block" prevents Claude from stopping
            # continue=True allows hook processing to complete, decision="block" prevents the Stop event
            # reason=injection tells Claude what to do next
            # See https://code.claude.com/docs/en/hooks and build_hook_response() (~line 546)
            return build_hook_response(True, "", injection, decision="block", reason=injection)

        # Only intervene in active autorun sessions
        if state.get("session_status") != "active":
            # Normal cleanup for non-autorun sessions
            state.clear()
            return build_hook_response()

        current_stage = state.get("autorun_stage", "INITIAL")
        log_info(f"Three-stage system: stage={current_stage}, hook_calls={state['hook_call_count']}")

        # STAGE 1: Initial work - check for stage 1 confirmation
        if current_stage == "INITIAL":
            # Check for stage 1 confirmation (AI outputs this to complete stage 1)
            if CONFIG["stage1_message"] in transcript:
                log_info(f"Stage 1 completion detected for session {session_id}")
                state["autorun_stage"] = "STAGE2"
                state["stage1_completed"] = True
                state["stage1_completion_timestamp"] = time.time()

                # KEEP CLAUDE WORKING: Inject stage 2 instructions
                # decision="block" prevents Stop, reason tells Claude what to do next
                stage2_prompt = f"STAGE 2: {CONFIG['stage2_instruction']}. Output **{CONFIG['stage2_message']}** when complete."
                return build_hook_response(True, "", stage2_prompt, decision="block", reason=stage2_prompt)

            # Handle premature stage 3 attempt in stage 1 (also recognize descriptive completion_marker)
            elif CONFIG["stage3_message"] in transcript or CONFIG["completion_marker"] in transcript:
                log_info(f"Premature stage 3 attempt detected in stage 1 for session {session_id}")
                # KEEP CLAUDE WORKING: Block premature completion, redirect to stage 1
                stage1_continuation = f"You must complete Stage 1 first. Output **{CONFIG['stage1_message']}** when done."
                return build_hook_response(True, "", stage1_continuation, decision="block", reason=stage1_continuation)

            # Handle premature stop (no completion markers)
            elif is_premature_stop(ctx, state):
                log_info(f"Premature stop detected in Stage 1 for session {session_id}")
                return inject_continue_prompt(state)

        # STAGE 2: Critical evaluation
        elif current_stage == "STAGE2":
            # Check for stage 2 confirmation (AI outputs this to complete stage 2)
            if CONFIG["stage2_message"] in transcript:
                log_info(f"Stage 2 completion detected for session {session_id}")
                state["autorun_stage"] = "STAGE2_COMPLETED"
                state["stage2_completion_timestamp"] = time.time()
                state["hook_call_count"] = 0  # Reset countdown for stage 3

                # KEEP CLAUDE WORKING: Start countdown for stage 3
                # decision="block" prevents Stop, reason tells Claude what to do next
                remaining_calls = CONFIG["stage3_countdown_calls"]
                countdown_msg = f"Stage 2 complete. Continue working for {remaining_calls} more cycles before Stage 3 instructions are revealed."
                return build_hook_response(True, "", countdown_msg, decision="block", reason=countdown_msg)

            # Block premature stage 3 attempt in stage 2 (also recognize descriptive completion_marker)
            # Check BEFORE is_premature_stop to prevent dual-marker bypass
            elif CONFIG["stage3_message"] in transcript or CONFIG["completion_marker"] in transcript:
                log_info(f"Premature stage 3 attempt detected in stage 2 for session {session_id}")
                # KEEP CLAUDE WORKING: Block premature completion, redirect to stage 2
                stage2_continuation = f"You must complete Stage 2 first. Output **{CONFIG['stage2_message']}** when done."
                return build_hook_response(True, "", stage2_continuation, decision="block", reason=stage2_continuation)

            # Handle premature stop in stage 2
            elif is_premature_stop(ctx, state):
                log_info(f"Premature stop detected in Stage 2 for session {session_id}")
                # KEEP CLAUDE WORKING: Resume stage 2 work
                stage2_continuation = f"Continue with Stage 2: {CONFIG['stage2_instruction']}. Output **{CONFIG['stage2_message']}** when complete."
                return build_hook_response(True, "", stage2_continuation, decision="block", reason=stage2_continuation)

        # STAGE 2 COMPLETED: Countdown to stage 3
        elif current_stage == "STAGE2_COMPLETED":
            hook_call_count = state.get("hook_call_count", 0)
            remaining_calls = CONFIG["stage3_countdown_calls"] - hook_call_count

            # Check if stage 3 confirmation was attempted (also recognize descriptive completion_marker)
            if CONFIG["stage3_message"] in transcript or CONFIG["completion_marker"] in transcript:
                if remaining_calls > 0:
                    # Early attempt - reset to STAGE2 (not INITIAL) to preserve progress
                    log_info(f"Early stage 3 attempt detected, {remaining_calls} calls remaining")
                    # KEEP CLAUDE WORKING: Too early for stage 3, redirect back to stage 2
                    reset_msg = f"Too early for Stage 3. Continue with Stage 2: {CONFIG['stage2_instruction']}"
                    state["autorun_stage"] = "STAGE2"
                    # Don't reset stage1_completed - preserve progress
                    return build_hook_response(True, "", reset_msg, decision="block", reason=reset_msg)
                else:
                    log_info(f"Stage 3 completion detected for session {session_id}")
                    # Proper stage 3 completion - stop monitor and cleanup
                    log_info("Stopping AI monitor after successful stage 3 completion")
                    _manage_monitor(state, 'stop')
                    state.clear()
                    # ALLOW CLAUDE TO STOP: All stages complete, we WANT Claude to stop here
                    # continue=False means Claude will stop (this is the desired behavior)
                    return build_hook_response(False, "", "✅ Three-stage completion successful!")

            # Continue countdown and provide status updates
            elif remaining_calls > 0:
                # Alternating behavior: Status on even calls, recovery injection on odd calls
                # This ensures AI can recover if it genuinely stops during countdown period
                if hook_call_count % 2 == 0:  # Provide simple status updates every 2 calls
                    # KEEP CLAUDE WORKING: Countdown status, continue working
                    status_msg = f"Stage 3 countdown: {remaining_calls} calls remaining. Continue with evaluation."
                    return build_hook_response(True, "", status_msg, decision="block", reason=status_msg)
                else:
                    # Recovery mechanism: Inject full task context if AI stops working
                    return inject_continue_prompt(state)
            else:
                # KEEP CLAUDE WORKING: Reveal stage 3 instructions and continue
                stage3_instructions = f"STAGE 3: {CONFIG['stage3_instruction']}. Output **{CONFIG['stage3_message']}** to complete."
                return build_hook_response(True, "", stage3_instructions, decision="block", reason=stage3_instructions)

        # Fallback: unknown stage or state
        log_info(f"Unknown state in three-stage stop_handler: stage={current_stage}, session={session_id}")
        return inject_continue_prompt(state)

# Default handler
def default_handler(ctx): return build_hook_response()

def main():
    """Entry point - unified with efficient dispatch"""
    operation_mode = os.getenv("AGENT_MODE", "HOOK_INTEGRATION").upper()

    if operation_mode == "HOOK_INTEGRATION":
        # Run as Claude Code hook - handle all hook events
        try:
            payload = json.loads(sys.stdin.read())
            event = payload.get("hook_event_name", "?")
            _session_id = payload.get("session_id", "?")

            # DEBUG: Log all hook calls to track when script is called
            # Security: Sanitize prompt to prevent log injection
            debug_log = STATE_DIR / "hook_debug.log"
            safe_prompt = sanitize_log_message(payload.get('prompt', '')[:100], max_length=100)
            with open(debug_log, "a") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] HOOK_CALLED: {event} | session: {_session_id} | prompt: {safe_prompt}...\n")

            # Context object for event handling
            class Ctx:
                def __init__(self, p):
                    self.hook_event_name = p.get("hook_event_name", "")
                    self.session_id = p.get("session_id", "")
                    self.prompt = p.get("prompt", "")
                    self.tool_name = p.get("tool_name", "")
                    self.tool_input = p.get("tool_input", {})
                    self.session_transcript = p.get("session_transcript", [])

            ctx = Ctx(payload)
            handler = HANDLERS.get(event, default_handler)
            response = handler(ctx)

            print(json.dumps(response, sort_keys=True))
            sys.stdout.flush()

        except Exception:
            print(json.dumps(build_hook_response()))
            sys.exit(1)

    else:
        # Run as standalone Agent SDK - Interactive mode
        run_interactive_sdk(operation_mode)

def run_interactive_sdk(operation_mode: str):
    """Run interactive Agent SDK with clean async/sync separation"""
    print("🚀 Agent SDK Command Interceptor - Interactive Mode")
    print("=" * 55)
    print("Commands handled locally (no AI tokens):")

    for cmd, action in CONFIG["command_mappings"].items():
        policy_info = ""
        if action in CONFIG["policies"]:
            policy_name, policy_desc = CONFIG["policies"][action]
            policy_info = f" - {policy_desc}"
        print(f"  {cmd} → {action}{policy_info}")

    print(f"\nEnvironment: AGENT_MODE={operation_mode}")
    print("Type commands (e.g., '/afs', '/afa', '/afj', '/afst') or 'quit' to exit")
    print("Non-commands will be processed by Claude Code via Agent SDK\n")

    # Initialize session state for interactive mode
    session_id = "interactive_session"
    with session_state(session_id) as state:
        state["file_policy"] = "ALLOW"

    print("✅ Ready for commands...")
    print("💡 One Ctrl+C = interrupt, two Ctrl+C = goodbye\n")

    # Track Ctrl+C count for double-press detection
    ctrl_c_count = 0
    last_ctrl_c_time = 0

    # Main interactive loop - sync input, async processing
    while True:
        try:
            # Get user input synchronously
            user_input = input("❓ ").strip()

            # Reset Ctrl+C count on successful input
            ctrl_c_count = 0

            # Exit conditions
            if user_input.lower() in ('quit', 'exit', 'q'):
                print("👋 Goodbye!")
                break

            if not user_input:
                continue

            # Efficient command detection - O(1) lookup via command_mappings
            command = next((v for k, v in CONFIG["command_mappings"].items() if k == user_input), None)
            if not command:
                # Check for commands that support arguments (autorun)
                command = next((v for k, v in CONFIG["command_mappings"].items() if user_input.startswith(k)), None)

            if command and command in COMMAND_HANDLERS:
                # Handle locally using dispatch table lookup
                with session_state(session_id) as state:
                    if command == "activate":
                        # Pass the full prompt for activation
                        response = COMMAND_HANDLERS[command](state, user_input)
                    else:
                        response = COMMAND_HANDLERS[command](state)
                    print(f"✅ {response}")

            else:
                # Send to Claude Code via Agent SDK - async operation
                print("🤖 Processing with Claude Code...")
                try:
                    # Run async operation in sync context
                    asyncio.run(process_with_claude_sdk(user_input, session_id))
                except Exception as e:
                    print(f"❌ Claude Code error: {e}")
                    print("💡 Make sure Claude Code is running and accessible")

        except KeyboardInterrupt:
            import time
            current_time = time.time()

            # Check if this is a rapid second Ctrl+C (within 1 second)
            if current_time - last_ctrl_c_time < 1.0:
                ctrl_c_count += 1
            else:
                ctrl_c_count = 1

            last_ctrl_c_time = current_time

            if ctrl_c_count >= 2:
                print("\n👋 Goodbye!")
                break
            else:
                print("\n⚠️ Interrupted. One more Ctrl+C to exit, or continue with a command.")

        except EOFError:
            print("\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"❌ Error: {e}")

async def process_with_claude_sdk(prompt: str, session_id: str):
    """Process non-command prompts with Claude Code via Agent SDK"""
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

    try:
        # Use ClaudeSDKClient for better control
        options = ClaudeAgentOptions()
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt, session_id=session_id)

            async for message in client.receive_response():
                if hasattr(message, 'content'):
                    for block in message.content:
                        if hasattr(block, 'text'):
                            print(block.text, end='', flush=True)
                elif hasattr(message, 'total_cost_usd'):
                    print(f"\n💰 Cost: ${message.total_cost_usd:.4f}")
                    break  # End of response
            print()  # New line at end

    except Exception as e:
        print(f"❌ Agent SDK error: {e}")
        # Fallback - suggest using Claude Code directly
        print("💡 You can ask this question directly in Claude Code")

if __name__ == "__main__":
    main()