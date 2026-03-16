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
Utility shim module for autorun.

Provides shared utility functions (sanitize_log_message, log_info,
is_safe_regex_pattern, command_matches_pattern, parse_pattern_and_description,
build_hook_response, has_valid_justification) and a thin main() shim that
delegates to __main__.run_hook_handler().

Production hook dispatch path:
  hook_entry.py → __main__.run_hook_handler() → client.py → daemon → plugins.py
                                               ↑
  (AUTORUN_USE_DAEMON=0) → __main__.run_direct() → EventContext + app.dispatch()

Canonical v0.9 implementations:
- core.py        — EventContext, AutorunApp dispatcher, AutorunDaemon server
- plugins.py     — All handlers via DRY factories (_make_policy_handler,
                   _make_block_op, check_blocked_commands, autorun_injection, …)
- integrations.py — Command-matching predicates (_WHEN_PREDICATES, check_when_predicate)
- session_manager.py — filelock+JSON session state backend

Removed (with canonical replacements):
- handle_block_pattern/allow/clear/status → plugins._make_block_op("session", op)
- handle_global_* → plugins._make_block_op("global", op)
- handle_search/allow/justify/status → plugins._make_policy_handler(policy)
- handle_stop/emergency_stop → plugins.handle_stop / handle_sos
- handle_activate → plugins.handle_activate
- stop_handler → plugins.autorun_injection (@app.on("Stop"))
- claude_code_handler → __main__.run_direct() via app.dispatch()
- inject_continue_prompt/inject_verification_prompt → plugins.build_injection_prompt
- is_premature_stop(ctx, state) → plugins.is_premature_stop(ctx: EventContext)
- get_stage3_instructions(state) → plugins.get_stage3_instructions(ctx: EventContext)
- get_session_blocks/add/remove/clear → ScopeAccessor(ctx, "session") in plugins.py
- get_global_blocks/add/remove → ScopeAccessor(ctx, "global") in plugins.py
- HANDLERS dict, COMMAND_HANDLERS dict → plugins.app.commands + app.dispatch()
- _manage_monitor, get_injection_monitor, update_injection_outcome → removed
- get_global_tmux_utils → tmux_utils.get_tmux_utilities() directly
"""

import os
import sys
import time
from pathlib import Path
from typing import Optional, List, Dict
import re as regex_module

# Import centralized tmux utilities (TMUX_UTILS_AVAILABLE exported for tests)
try:
    from .tmux_utils import get_tmux_utilities
    TMUX_UTILS_AVAILABLE = True
except ImportError:
    TMUX_UTILS_AVAILABLE = False
    get_tmux_utilities = None

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

# Import schema validation for dual-platform compatibility
try:
    from .core import validate_hook_response, get_cli_event_name
except ImportError:
    # Fallback if core.py not available
    def validate_hook_response(event, response, cli_type="claude"):
        return response
    def get_cli_event_name(internal_event, cli_type):
        return internal_event

# get_global_tmux_utils — REMOVED: use tmux_utils.get_tmux_utilities() directly
# get_injection_monitor — REMOVED: ai_monitor injection no longer in production path
# update_injection_outcome — REMOVED: ai_monitor injection no longer in production path

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
        with open(STATE_DIR / "autorun.log", "a", encoding="utf-8") as f:
            log_time = time.strftime('%Y-%m-%d %H:%M:%S')
            pid = os.getpid()
            f.write(f"[{log_time}] {pid}: {safe_message}\n")
            f.flush()

        # Separate log for PreToolUse debugging
        if "PreToolUse" in message:
            with open(STATE_DIR / "pretooluse_debug.log", "a", encoding="utf-8") as debug_f:
                debug_f.write(f"[{log_time}] {pid}: {safe_message}\n")
                debug_f.flush()

    except Exception as e:
        # Fallback logging - silently skip to avoid breaking hooks
        # If logging fails, it's better to continue than to break hooks with stderr output
        pass

# Import robust session state management from session_manager module
# This provides RAII-based session state with proper file locking and backend selection
# Fixes Issue #29 (process-local _session_backends) and Issue #28 (filename extensions)
from autorun.session_manager import session_state

# NOTE: _not_in_pipe import removed (fallback path only).
# Modern replacement: integrations.py — _not_in_pipe(ctx) in _WHEN_PREDICATES,
# used via integrations.check_when_predicate() from plugins.check_blocked_commands().

# =============================================================================
# Command Blocking State Management — REMOVED
# =============================================================================
# All session/global block management is now handled by plugins.py via:
#   ScopeAccessor(ctx, "session") — session-scoped blocks/allows
#   ScopeAccessor(ctx, "global")  — global blocks/allows
#   _make_block_op(scope, op)     — factory for /ar:no, /ar:ok, /ar:clear, etc.
#
# Removed functions and their canonical replacements:
#   get_session_blocks(session_id)   → ScopeAccessor(ctx, "session").get()
#   add_session_block(session_id, …) → ScopeAccessor.set(blocks) via _make_block_op
#   remove_session_block(sid, …)     → ScopeAccessor.set(filtered)
#   clear_session_blocks(sid, …)     → ScopeAccessor.set([]) + set_allowed([])
#   get_global_blocks()              → ScopeAccessor(ctx, "global").get()
#   initialize_default_blocks()      → daemon loads defaults via load_all_integrations()
#   add_global_block(pattern, …)     → ScopeAccessor(ctx, "global").set() via _make_block_op
#   remove_global_block(pattern)     → ScopeAccessor(ctx, "global").set(filtered)
#   GLOBAL_CONFIG_FILE constant      → daemon uses session_state("__global__") backend
# =============================================================================


def parse_pattern_and_description(args: str) -> tuple[str, str | None, str]:
    """
    Parse pattern and optional description from command arguments.

    Supports multiple formats:
    1. /ar:no pattern                    # No description, uses DEFAULT_INTEGRATIONS
    2. /ar:no "pattern with spaces"      # Quoted pattern, no description
    3. /ar:no "pattern" description      # Quoted pattern with custom description
    4. /ar:no pattern description text   # Unquoted pattern with description

    Pattern type prefixes (opt-in):
    5. /ar:no regex:pattern              # Regex pattern matching
    6. /ar:no glob:pattern               # Glob pattern matching
    7. /ar:no "/regex.*$/"               # Auto-detect regex using /pattern/ syntax

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
    # - "/ar:plannew" does NOT match "rm" (substring in "plannew")
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
# Predicate Functions — MOVED to integrations.py (fallback copies removed)
# =============================================================================
# The fallback-path copies of these predicates (taking cmd: str) were removed.
#
# CANONICAL implementations now live in:
#   integrations.py — _has_unstaged_changes(ctx), _file_has_unstaged_changes(ctx)
#   integrations.py — _stash_exists(ctx), _not_in_pipe(ctx)
#   integrations.py — _checkout_targets_file_with_changes(ctx), _restore_is_destructive(ctx)
#   integrations.py — _WHEN_PREDICATES dict: maps name string → predicate function
#   integrations.py — check_when_predicate(when, ctx): calls _WHEN_PREDICATES[when](ctx)
#   plugins.py      — check_blocked_commands(ctx): calls check_when_predicate per integration
#
# Key difference: daemon-path predicates take EventContext (not a raw cmd string)
# and can access full hook context (session state, tool input, transcript, etc.)
# =============================================================================



# =============================================================================
# Command Blocking Functions — REMOVED (daemon path)
# =============================================================================
# should_block_command(session_id, cmd) and get_command_warning(session_id, cmd)
# were only used by pretooluse_handler (fallback path, AUTORUN_USE_DAEMON=0).
#
# Modern replacement (daemon path, default):
#   plugins.py — check_blocked_commands(ctx) — stacks all matching rules,
#                deny-wins over warn, deduplicates, returns combined message.
#   plugins.py — ScopeAccessor — session/global scope block management.
#
# build_pretooluse_response(decision, reason, ctx) was the response builder
# for the fallback path.
#
# Modern replacement:
#   core.py — EventContext.deny(msg) / .allow() / .respond("allow", msg)
# =============================================================================

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
                        decision=None, reason=None, event_name="unknown", ctx=None):
    """Build standardized JSON hook response.

    For Stop/SubagentStop hooks that need to keep Claude working:
    - Set continue_execution=True (default)
    - Set decision="block" to prevent the stop
    - Set reason="..." with instructions for Claude

    See documentation block above for full semantics.
    """
    from .config import detect_cli_type
    
    # Priority: explicit ctx cli_type > global detection
    if ctx and hasattr(ctx, 'cli_type'):
        cli_type = ctx.cli_type
    else:
        cli_type = detect_cli_type()

    response = {"continue": continue_execution, "stopReason": stop_reason,
                "suppressOutput": False, "systemMessage": system_message}
    # Stop-hook-specific fields for blocking stops
    if decision is not None:
        actual_decision = decision
        if cli_type == "gemini" and decision == "block":
            actual_decision = "deny"
        response["decision"] = actual_decision
    if reason is not None:
        response["reason"] = reason
        
    return validate_hook_response(event_name, response, cli_type=cli_type)

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


# HANDLERS dict — REMOVED: use plugins.app.commands + app.dispatch() (canonical)
# handler() decorator — REMOVED: use @app.on() from core.py
# COMMAND_HANDLERS dict — REMOVED: use plugins.app.commands + app.dispatch()
# All handle_* functions — REMOVED: see module docstring for canonical replacements
# claude_code_handler — REMOVED: __main__.run_direct() dispatches via app.dispatch()
# stop_handler — REMOVED: plugins.autorun_injection (@app.on("Stop")) is canonical
# inject_continue_prompt — REMOVED: plugins.build_injection_prompt(ctx) is canonical
# inject_verification_prompt — REMOVED: plugins.build_injection_prompt(ctx) is canonical
# is_premature_stop(ctx, state) — REMOVED: plugins.is_premature_stop(ctx: EventContext)
# get_stage3_instructions(state) — REMOVED: plugins.get_stage3_instructions(ctx: EventContext)
# default_handler — REMOVED: app.dispatch() returns None for unmatched events

# handle_search, handle_allow, handle_justify, handle_status — REMOVED
# Canonical: plugins._make_policy_handler(policy) at plugins.py:63
#
# handle_stop, handle_emergency_stop — REMOVED
# Canonical: plugins.handle_stop(ctx), plugins.handle_sos(ctx) at plugins.py:719,725

# handle_activate — REMOVED
# Canonical: plugins.handle_activate(ctx) at plugins.py:689
#
# COMMAND_HANDLERS dict — REMOVED (already noted above at line 511)
# All handle_block_pattern, handle_*_pattern, handle_global_* — REMOVED
# Canonical: plugins._make_block_op(scope, op) factory at plugins.py:399-492


# claude_code_handler — REMOVED: __main__.run_direct() → app.dispatch() → plugins.py
# stop_handler, inject_continue_prompt, inject_verification_prompt — REMOVED
# is_premature_stop, get_stage3_instructions — REMOVED
# Canonical replacements: plugins.autorun_injection (Stop), plugins.build_injection_prompt, plugins.is_premature_stop
# default_handler — REMOVED: app.dispatch() returns None for unmatched events

def main(_exit=True):
    """Thin shim — delegates to __main__.run_hook_handler().

    All hook-handling logic lives in __main__.run_hook_handler() which routes to:
    - Daemon path (default): client.py → daemon socket → core.py → plugins.py
    - Direct path (AUTORUN_USE_DAEMON=0): run_direct() → EventContext + app.dispatch()

    Retained for backward compatibility with callers:
    - autorun.py (calls main())
    - test_hook.py (imports main)
    - test_edge_cases_comprehensive.py (imports main)
    """
    from autorun.__main__ import run_hook_handler
    result = run_hook_handler()
    if _exit:
        sys.exit(result)
    return result

if __name__ == "__main__":
    main()