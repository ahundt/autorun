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
Autorun v0.7 Core - Click/Typer-style Decorator Framework

Provides:
- LazyTranscript: Deferred string conversion for performance
- ThreadSafeDB: In-memory cache layer for daemon performance
- EventContext: Rich context with magic __getattr__/__setattr__ state access
- AutorunApp: Click-style decorator registration
- AutorunDaemon: AsyncIO Unix socket server

Reuses session_manager.py entirely (421 lines of battle-tested RAII code).
"""
import os
import re
import json
import asyncio
import signal
import subprocess
import time
import copy
import logging
import threading
from pathlib import Path
from typing import Any, Optional, Dict, List, Callable, Set, Union
from functools import lru_cache

# Reuse existing session_manager (CRITICAL: preserves RAII, locks, backends)
from .session_manager import session_state
from .config import CONFIG
from . import ipc

# === CONFIGURATION ===
ipc.ensure_config_dir()
LOCK_PATH = ipc.AUTORUN_LOCK_PATH
LOG_FILE = ipc.AUTORUN_LOG_FILE
IDLE_TIMEOUT = 1800  # 30 minutes

# Buffer size for reading hook payloads (asyncio default is 64KB = 2^16)
# Need larger than default to accept full payloads before truncating
# Client sends full transcript (can be 200MB+), server truncates to 64KB after reading
_DEFAULT_LIMIT = asyncio.streams._DEFAULT_LIMIT  # 64KB (2^16 = 65536)

# Allow override via AUTORUN_BUFFER_LIMIT env var (in bytes)
# Default: 1GB (2^30) handles sessions up to 1GB
# Found actual sessions: 511MB, so need more than 512MB headroom
_env_limit = os.environ.get("AUTORUN_BUFFER_LIMIT")
if _env_limit:
    try:
        READ_BUFFER_LIMIT = int(_env_limit)
    except ValueError:
        # Logger not available yet at module load time
        # CRITICAL: Don't print to stderr - breaks hooks!
        # Invalid buffer limit - silently use default 1GB
        READ_BUFFER_LIMIT = _DEFAULT_LIMIT * (2 ** 14)  # 1GB (2^30)
else:
    READ_BUFFER_LIMIT = _DEFAULT_LIMIT * (2 ** 14)  # 1GB (2^30)

# === LOGGING ===
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger("autorun")


# === GEMINI CLI PAYLOAD NORMALIZATION ===
# Gemini CLI uses different event names and camelCase keys vs Claude Code's snake_case.
# This mapping normalizes both formats to a single internal representation.

GEMINI_EVENT_MAP = {
    "BeforeTool": "PreToolUse",
    "AfterTool": "PostToolUse",
    "BeforeAgent": "UserPromptSubmit",
    "AfterAgent": "Stop",
    "SessionStart": "SessionStart",
    "SessionEnd": "SessionEnd",
}

# Reverse mapping: Internal event name → CLI-specific name
# For converting normalized internal events back to original CLI format
INTERNAL_TO_CLAUDE = {
    "PreToolUse": "PreToolUse",
    "PostToolUse": "PostToolUse",
    "UserPromptSubmit": "UserPromptSubmit",
    "Stop": "Stop",
    "SessionStart": "SessionStart",
    "SessionEnd": "SessionEnd",
}

INTERNAL_TO_GEMINI = {
    "PreToolUse": "BeforeTool",
    "PostToolUse": "AfterTool",
    "UserPromptSubmit": "BeforeAgent",
    "Stop": "AfterAgent",
    "SessionStart": "SessionStart",
    "SessionEnd": "SessionEnd",
}


# === CLI TOOL NAME DISPATCH TABLE ===
# Maps CLI type → {template_key: tool_name} for suggestion string substitution.
# Template keys ({grep}, {read}, etc.) are used in DEFAULT_INTEGRATIONS suggestion
# strings, parallel to the {args} substitution in Integration.redirect (plugins.py:531).
# Adding a new CLI: add one entry here. No other changes required.
#
# THREE NAMING LAYERS (as of Claude Code CLI v2.1.47):
#
#   1. API tool_name  — what the AI uses in its tool call (hooks key off this)
#                       Claude Code: PascalCase  e.g. "Glob", "Grep", "Read"
#                       Gemini CLI:  snake_case  e.g. "glob", "grep_search", "read_file"
#                       Confirmed by hook matchers:
#                         claude-hooks.json  PreToolUse  "Write|Edit|Bash|ExitPlanMode"
#                         hooks.json         BeforeTool  "write_file|run_shell_command|replace|read_file|glob|grep_search"
#
#   2. CLI display name — what the user sees rendered in the terminal (cosmetic only)
#                         Claude Code renders "Glob" as "Search" in terminal output.
#                         Gemini CLI matches display name to API name.
#                         This is a UI rendering choice; hooks and AI calls use API names.
#
#   3. Bash command     — the shell command being BLOCKED in DEFAULT_INTEGRATIONS
#                         e.g. blocking "grep" (bash) and suggesting {grep} (AI tool)
#                         These are different namespaces — "grep" bash ≠ "Grep" AI tool.
#
# The values in this table are API tool_names (layer 1), NOT CLI display names (layer 2).
# Suggestions addressed to the AI must use API names so the AI calls the right tool.
# The UX mismatch (Claude "Glob" displayed as "Search") is cosmetic, not functional.
CLI_TOOL_NAMES: dict[str, dict[str, str]] = {
    "claude": {
        # API tool_names — PascalCase (Claude Code CLI v2.1.47)
        # Note: Claude Code terminal renders Glob→"Search" but hook/API name is "Glob"
        "grep": "Grep",
        "glob": "Glob",
        "read": "Read",
        "write": "Write",
        "edit": "Edit",
        "bash": "Bash",
        "ls": "LS",
    },
    "gemini": {
        # API tool_names — snake_case, confirmed by hooks.json BeforeTool matchers
        "grep": "grep_search",
        "glob": "glob",
        "read": "read_file",
        "write": "write_file",
        "edit": "replace",
        "bash": "run_shell_command",
        "ls": "list_directory",
    },
}


def get_tool_names(cli_type: str) -> dict[str, str]:
    """Get tool name dict for a CLI. Returns empty dict for unknown CLIs.

    Unknown CLI → all placeholders pass through unchanged (safe, generic).
    """
    return CLI_TOOL_NAMES.get(cli_type, {})


@lru_cache(maxsize=64)
def format_suggestion(msg: str, cli_type: str) -> str:
    """Resolve {tool_key} placeholders in safety-guard suggestion strings to the
    correct CLI-specific tool name, so the AI receives actionable instructions.

    WHY THIS EXISTS:
        Safety guards block bash commands (grep, find, cat, etc.) and suggest the
        equivalent AI tool instead. Without this, Claude would be told "use Grep"
        while Gemini (which calls it grep_search) would receive wrong instructions.
        The daemon handles both CLIs on the same socket, so cli_type determines
        which tool names to use per request.

    WHY lru_cache:
        The daemon is shared across all concurrent AI sessions (multiple Claude and
        Gemini instances). AIs retry blocked commands, and multiple sessions trigger
        the same integrations independently. Input space is bounded: ~14 integrations
        × 2 CLIs + 2 policy_blocked × 2 CLIs ≈ 32 unique (msg, cli_type) pairs.
        maxsize=64 comfortably covers this with room for growth.

    WHY str.replace() NOT str.format_map():
        format_map() raises ValueError on positional fields like `xargs -I{} mv {}`,
        which appear in shell examples in the git clean suggestion string. str.replace()
        is safe for arbitrary text and only substitutes exact dispatch table keys.

    Only replaces {grep}, {glob}, {read}, {write}, {edit}, {bash}, {ls}.
    Everything else — {args}, {}, {0}, shell syntax — passes through unchanged.

    Args:
        msg: Suggestion string with optional {tool_key} placeholders.
        cli_type: CLI identifier ("claude", "gemini", or any future CLI).

    Returns:
        msg with tool names resolved for cli_type. Unknown CLI or unknown
        placeholder leaves msg unchanged.
    """
    for key, value in get_tool_names(cli_type).items():
        msg = msg.replace(f"{{{key}}}", value)
    return msg


def get_cli_event_name(internal_event: str, cli_type: str) -> str:
    """Convert internal event name to CLI-specific name for responses.

    Args:
        internal_event: Normalized internal event name (e.g., "PreToolUse")
        cli_type: Target CLI ("claude" or "gemini")

    Returns:
        CLI-specific event name (e.g., "BeforeTool" for Gemini, "PreToolUse" for Claude)
    """
    if cli_type == "gemini":
        return INTERNAL_TO_GEMINI.get(internal_event, internal_event)
    else:
        return INTERNAL_TO_CLAUDE.get(internal_event, internal_event)


def normalize_hook_payload(payload: dict, truncate_transcript: bool = True) -> dict:
    """Normalize hook payload from any CLI format and optionally truncate transcript.

    Normalization:
    - Claude Code: hook_event_name, session_id, tool_name (snake_case)
    - Gemini CLI: type, sessionId, toolName (camelCase)

    Transcript Truncation (configurable):
    - session_transcript can be 200MB+ in long sessions
    - Hooks only search recent patterns (stage markers, justification tags)
    - Truncate to last ~64KB by default (saves memory, speeds pattern search)
    - Can disable via truncate_transcript=False or AUTORUN_NO_TRUNCATE=1 env var

    When to disable truncation:
    - CLI commands needing full session history (export, analysis, debugging)
    - Custom hooks that need access to old messages
    - Diagnostic/troubleshooting scenarios

    Args:
        payload: Raw hook payload from Claude Code or Gemini CLI
        truncate_transcript: If True, truncate to ~64KB (default: True for hooks)

    Returns:
        dict: Normalized payload with optionally truncated transcript
    """
    # Map event name: Gemini "BeforeTool" → internal "PreToolUse", etc.
    raw_event = payload.get("hook_event_name") or payload.get("type", "")
    event = GEMINI_EVENT_MAP.get(raw_event, raw_event)

    # Get session ID (Gemini uses sessionId)
    session_id = payload.get("sessionId") or payload.get("session_id", "unknown")
    
    logger.debug(f"normalize_hook_payload: raw_event={raw_event}, event={event}, session_id={session_id}")

    # Get transcript
    transcript = payload.get("session_transcript", [])

    # Check if truncation disabled globally via env var
    if os.environ.get("AUTORUN_NO_TRUNCATE") == "1":
        truncate_transcript = False

    # Truncate to recent messages if enabled (memory optimization)
    # Default: enabled for hooks (saves memory, they only need recent patterns)
    # Disable for: CLI commands, debugging, full history analysis
    #
    # STRATEGY: Prioritize size limit (64KB hard cap) over message count.
    # This prevents memory bloat from sessions with huge individual messages.
    if truncate_transcript and transcript and len(transcript) > 20:
        # Try last 20 messages first (fast path for normal-sized messages)
        recent_20 = transcript[-20:]
        size_20 = len(json.dumps(recent_20))

        if size_20 <= 64 * 1024:
            # Last 20 fit in 64KB - use them (common case)
            transcript = recent_20
            logger.debug(f"Truncated transcript: {len(payload['session_transcript'])} → 20 messages ({size_20//1024}KB)")
        else:
            # Last 20 too large - accumulate from end with STRICT 64KB limit
            # Prioritize size over count (some messages are huge)
            truncated = []
            size_estimate = 0
            for msg in reversed(transcript):
                msg_size = len(json.dumps(msg))
                # STRICT: Stop if adding would exceed 64KB (even if < 5 messages)
                # We need size limit more than message count - one message is enough
                # if it contains the patterns we search for
                if size_estimate + msg_size > 64 * 1024:
                    if len(truncated) == 0:
                        # Edge case: First message itself > 64KB
                        # Keep it anyway (we need at least one message)
                        truncated.insert(0, msg)
                    break
                truncated.insert(0, msg)
                size_estimate += msg_size

            transcript = truncated
            logger.debug(f"Truncated huge messages: {len(payload.get('session_transcript', []))} → "
                        f"{len(truncated)} messages ({size_estimate//1024}KB)")

    return {
        "hook_event_name": event,
        "session_id": session_id,
        "prompt": payload.get("prompt", ""),
        "tool_name": payload.get("tool_name") or payload.get("toolName", ""),
        "tool_input": payload.get("tool_input") or payload.get("toolInput", {}),
        "tool_result": payload.get("tool_result") or payload.get("toolResult"),
        "session_transcript": transcript,
        "permission_mode": payload.get("permission_mode", "default"),
        "source": payload.get("source", "startup"),
    }


# === THREAD-SAFE DB WRAPPER (In-memory cache layer for daemon performance) ===
class ThreadSafeDB:
    """
    Thread-safe in-memory cache on top of session_manager.py's persistent storage.

    Architecture (3 layers for optimal performance):
        1. EventContext: Magic syntax (ctx.file_policy)
        2. ThreadSafeDB: In-memory cache (daemon lifetime, fast reads)
        3. session_state(): Persistent shelve with RAII locks (survives daemon restart)

    Attributes:
        _lock: RLock for thread-safe access
        _cache: In-memory dict cache

    Benefits:
        - First access: Reads from shelve (~7-17ms)
        - Subsequent: Reads from memory cache (<1ms)
        - Daemon restart: Cache rebuilds from persistent shelve
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._cache: Dict[str, Any] = {}

    def get(self, key: str, default=None) -> Any:
        """Get value with two-tier lookup: memory cache -> persistent shelve."""
        with self._lock:
            # Fast path: Check memory cache first
            if key in self._cache:
                return self._cache[key]

            # Slow path: Load from persistent shelve
            try:
                # Use rsplit to handle session_ids containing ":"
                parts = key.rsplit(":", 1)
                session_id = parts[0] if len(parts) > 1 else "__default__"
                field = parts[-1]
                with session_state(session_id) as state:
                    value = state.get(field, default)
                    # Cache for next access
                    if value is not None:
                        self._cache[key] = value
                    return value
            except Exception as e:
                logger.error(f"ThreadSafeDB.get error: {e}")
                return default

    def set(self, key: str, value: Any):
        """Set value in both memory cache and persistent shelve."""
        # Deep copy only mutable types for clean serialization
        if isinstance(value, (list, dict, set)):
            value = copy.deepcopy(value)

        with self._lock:
            # Update memory cache
            self._cache[key] = value

            # Persist to shelve via session_state() RAII wrapper
            try:
                # Use rsplit to handle session_ids containing ":"
                parts = key.rsplit(":", 1)
                session_id = parts[0] if len(parts) > 1 else "__default__"
                field = parts[-1]
                with session_state(session_id) as state:
                    state[field] = value
            except Exception as e:
                logger.error(f"ThreadSafeDB.set error: {e}")


# === TRI-LAYER IDENTITY RESOLUTION (Optional, for resume robustness) ===
def resolve_session_key(pid: int, cwd: str, fallback_id: str) -> str:
    """
    Resolve stable session key for resume robustness.

    Priority:
    1. AUTORUN_SESSION_ID env var (explicit override)
    2. JSONL history file scan (survives claude --resume)
    3. session_id from payload (fallback)

    Usage: Enable with AUTORUN_USE_IDENTITY=1
    """
    import platform

    # Layer 1: Explicit env var
    if env_id := os.environ.get("AUTORUN_SESSION_ID"):
        return f"explicit:{env_id}"

    # Layer 2: JSONL file (for resume robustness)
    if os.environ.get("AUTORUN_USE_IDENTITY") == "1":
        if platform.system() == "Linux":
            try:
                fd_dir = Path(f"/proc/{pid}/fd")
                for fd in fd_dir.iterdir():
                    target = fd.readlink()
                    if ".jsonl" in str(target) and ".claude" in str(target):
                        return f"history:{Path(target).name}"
            except (PermissionError, FileNotFoundError, OSError):
                pass
        elif platform.system() == "Darwin":
            try:
                result = subprocess.run(
                    ["lsof", "-p", str(pid), "-Fn"],
                    capture_output=True, text=True, timeout=2.0
                )
                for line in result.stdout.splitlines():
                    if line.startswith("n") and ".jsonl" in line and ".claude" in line:
                        return f"history:{Path(line[1:]).name}"
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

    # Layer 3: Fallback to session_id from payload
    if not fallback_id or fallback_id == "unknown":
        # Use stable PID-based identity if session_id is missing (startup hooks)
        # pid is the Claude session PID (parent of the hook process)
        return f"pid:{pid}" if pid else "default_session"
    return fallback_id


# === LAZY TRANSCRIPT ===
class LazyTranscript:
    """
    Lazy string conversion for session_transcript.
    Only converts to string when searched, saving 10-50ms per hook call.

    Usage:
        transcript = LazyTranscript(ctx.session_transcript)
        if transcript.contains("AUTOFILE_JUSTIFICATION"):
            ...
    """
    __slots__ = ('_raw', '_text', '_converted')

    def __init__(self, raw_transcript: List[Dict[str, Any]]):
        self._raw = raw_transcript
        self._text: Optional[str] = None
        self._converted = False

    @property
    def text(self) -> str:
        if not self._converted:
            self._text = json.dumps(self._raw) if self._raw else ""
            self._converted = True
        return self._text or ""

    def contains(self, pattern: str) -> bool:
        """Case-insensitive substring search."""
        return pattern.lower() in self.text.lower()

    @lru_cache(maxsize=16)
    def search_regex(self, pattern: str) -> Optional[re.Match]:
        """Cached regex search."""
        return re.search(pattern, self.text, re.DOTALL | re.IGNORECASE)

    def has_justification(self) -> bool:
        """Check for valid AUTOFILE_JUSTIFICATION tag."""
        match = self.search_regex(r'<AUTOFILE_JUSTIFICATION>(.*?)</AUTOFILE_JUSTIFICATION>')
        if match:
            content = match.group(1).strip().lower()
            return content not in {"", "reason"}  # Exclude placeholders
        return False


# === MAGIC STATE CONTEXT ===
# =============================================================================
# HOOK SCHEMAS & VALIDATION
# =============================================================================
# Documentation References:
# - Claude Code Hooks: https://code.claude.com/docs/en/hooks
# - Gemini CLI Hooks:   https://geminicli.com/docs/hooks/reference/
# - Claude Schema:     https://code.claude.com/docs/en/hooks#json-output
# =============================================================================
# Strict filtering prevents "Invalid input" errors in Claude Code.
# Gemini CLI is more lenient but we maintain its fields for compatibility.
# =============================================================================
HOOK_SCHEMAS = {
    "PreToolUse": {
        "root": {"continue", "stopReason", "suppressOutput", "systemMessage",
                 "decision", "permissionDecision", "reason", "hookSpecificOutput"},
        "hso": {"hookEventName", "permissionDecision", "permissionDecisionReason", "updatedInput"}
    },
    "UserPromptSubmit": {
        "root": {"continue", "stopReason", "suppressOutput", "systemMessage", 
                 "decision", "reason", "hookSpecificOutput"},
        "hso": {"hookEventName", "additionalContext"}
    },
    "PostToolUse": {
        "root": {"continue", "stopReason", "suppressOutput", "systemMessage", 
                 "decision", "reason", "hookSpecificOutput"},
        "hso": {"hookEventName", "additionalContext"}
    },
    "SessionStart": {
        "root": {"continue", "stopReason", "suppressOutput", "systemMessage"},
        "hso": {}
    },
    "Stop": {
        "root": {"continue", "stopReason", "suppressOutput", "systemMessage", "decision", "reason"},
        "hso": {}
    },
    "SubagentStop": {
        "root": {"continue", "stopReason", "suppressOutput", "systemMessage", "decision", "reason"},
        "hso": {}
    }
}

def validate_hook_response(event: str, response: dict, cli_type: str = "claude") -> dict:
    """
    Perform strict code-based enforcement of hook schemas.
    Filters the response dictionary to contain ONLY allowed fields for the target CLI.
    
    Args:
        event: Normalized event name (e.g. PreToolUse, Stop)
        response: Dictionary to validate and filter
        cli_type: Target CLI ("claude" or "gemini")
        
    Returns:
        Filtered dictionary containing only schema-compliant fields.
    """
    # Debug logging
    logger.debug(f"validate_hook_response(event={event}, cli_type={cli_type}) input decision={response.get('decision')}")

    if cli_type == "gemini":
        # Gemini CLI is lenient - it ignores unknown fields.
        # We ensure it gets the standard decision/reason fields it expects.
        # We also INCLUDE hookSpecificOutput for universal test compatibility
        # and because it doesn't hurt Gemini.
        
        # Mapping for Gemini top-level decision/reason if they're missing but in HSO
        if "hookSpecificOutput" in response:
            hso = response["hookSpecificOutput"]
            if "decision" not in response and "permissionDecision" in hso:
                # Map 'deny' or 'block' to Gemini 'deny'
                d = hso["permissionDecision"]
                response["decision"] = "allow" if d == "allow" else "deny"
            if "reason" not in response and "permissionDecisionReason" in hso:
                response["reason"] = hso["permissionDecisionReason"]

        allowed_gemini = {
            "continue", "decision", "reason", "systemMessage", "stopReason",
            "hookSpecificOutput", "permissionDecision", "suppressOutput"
        }
        return {k: v for k, v in response.items() if k in allowed_gemini}

    # Strict validation for Claude Code (fails on unknown fields)
    schema = HOOK_SCHEMAS.get(event)
    if not schema:
        # Fallback for unknown events: allow universal common fields
        allowed_root = {"continue", "stopReason", "suppressOutput", "systemMessage"}
        return {k: v for k, v in response.items() if k in allowed_root}

    # 1. Filter root fields
    filtered = {k: v for k, v in response.items() if k in schema["root"]}
    
    # 2. Filter hookSpecificOutput if present and supported
    if "hookSpecificOutput" in filtered and schema["hso"]:
        hso = filtered["hookSpecificOutput"]
        filtered["hookSpecificOutput"] = {k: v for k, v in hso.items() if k in schema["hso"]}
    elif "hookSpecificOutput" in filtered:
        # Event does not support hookSpecificOutput (e.g. Stop, SessionStart)
        del filtered["hookSpecificOutput"]
        
    return filtered


class EventContext:
    """
    Rich context with MAGIC STATE PERSISTENCE.

    Any attribute access transparently reads/writes to Shelve via session_manager.py.
    ZERO boilerplate per field - just use ctx.file_policy = "SEARCH" and it persists!

    Usage:
        ctx.file_policy = "SEARCH"  # Saves automatically
        if ctx.autorun_active:      # Loads automatically
            ...
    """
    # Reserved attributes (not persisted)
    __slots__ = ('_session_id', '_event', '_prompt', '_tool_name', '_tool_input',
                 '_tool_result', '_session_transcript', '_state', '_transcript',
                 '_store', '_cli_type', '_cwd', '_permission_mode', '_source')

    # Stage constants for type consistency
    STAGE_INACTIVE = 0
    STAGE_1 = 1
    STAGE_2 = 2
    STAGE_2_COMPLETED = 3
    STAGE_3 = 4

    # Default values for magic state (used when key not in shelve)
    _DEFAULTS = {
        'file_policy': 'ALLOW',
        'session_status': '',
        'autorun_active': False,
        'autorun_stage': 0,
        'autorun_task': '',
        'autorun_mode': 'standard',
        'activation_prompt': '',
        'session_blocked_patterns': [],
        'recheck_count': 0,
        'hook_call_count': 0,
        'ai_monitor_pid': None,
        'plan_active': False,
        'plan_type': '',
        'plan_arguments': '',       # v0.7: Store original user request from $ARGUMENTS
        'tool_calls_since_task_update': 0,   # v0.9: Counter for task staleness reminder
        'task_staleness_enabled': True,      # v0.9: Enable/disable reminder injection
        'task_staleness_threshold': None,    # v0.9: Session override (None = use CONFIG default)
    }

    def __init__(self, session_id: str, event: str, prompt: str = "",
                 tool_name: str = None, tool_input: Dict = None,
                 tool_result: str = None, session_transcript: List = None,
                 store: 'ThreadSafeDB' = None, cli_type: str = None, cwd: str = None,
                 permission_mode: str = "default", source: str = "startup"):
        object.__setattr__(self, '_session_id', session_id)
        object.__setattr__(self, '_event', event)
        object.__setattr__(self, '_prompt', prompt)
        object.__setattr__(self, '_tool_name', tool_name)
        object.__setattr__(self, '_tool_input', tool_input or {})
        object.__setattr__(self, '_tool_result', tool_result)
        object.__setattr__(self, '_session_transcript', session_transcript or [])
        object.__setattr__(self, '_state', {})
        object.__setattr__(self, '_transcript', None)
        object.__setattr__(self, '_store', store)
        # Auto-detect CLI type from environment if not explicitly provided
        object.__setattr__(self, '_cli_type', cli_type)
        # Working directory injected by client.py (_cwd field) for plan tracking
        object.__setattr__(self, '_cwd', cwd)
        # Permission mode from hook payload (plan/bypassPermissions/acceptEdits/default)
        object.__setattr__(self, '_permission_mode', permission_mode)
        # Session start source from hook payload (startup/resume/clear/compact)
        object.__setattr__(self, '_source', source)

    # === Read-only accessors for payload data ===
    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def event(self) -> str:
        return self._event

    @property
    def cli_type(self) -> str:
        if self._cli_type is None:
            from .config import detect_cli_type
            detected = detect_cli_type()
            object.__setattr__(self, '_cli_type', detected)
        return self._cli_type

    @property
    def prompt(self) -> str:
        return self._prompt

    @property
    def tool_name(self) -> str:
        return self._tool_name

    @property
    def tool_input(self) -> Dict:
        return self._tool_input

    @property
    def tool_result(self) -> str:
        return self._tool_result

    @property
    def cwd(self) -> Optional[str]:
        """Working directory injected by client.py via _cwd payload field.

        Used by plan_export.py:project_dir to scope plan tracking to the correct
        project. Returns None if not available (hooks fired without client injection).
        """
        return self._cwd

    @property
    def permission_mode(self) -> str:
        """Permission mode from hook payload (e.g. 'plan', 'bypassPermissions', 'acceptEdits', 'default').

        Used by plan_export.py:recover_unexported_plans to route plans to notes/ vs
        notes/rejected/ at SessionStart recovery time.
        """
        return self._permission_mode

    @property
    def source(self) -> str:
        """Session start source from hook payload: 'startup', 'resume', 'clear', or 'compact'.

        Claude Code sends this in SessionStart payloads to identify how the session was initiated.
        'clear' indicates Option 1 (clear context + bypass permissions) or /clear command.
        Used by plan_export.py:recover_unexported_plans as the primary Option 1 detection signal
        (permission_mode is 'default' at hook time due to a 2ms timing race — applied after hook).
        """
        return self._source

    # === MAGIC STATE: __getattr__ / __setattr__ ===
    def __getattr__(self, name: str):
        """
        Magic getter - reads from Shelve automatically.

        ctx.file_policy  ->  shelve[session_id:file_policy] or default
        """
        # Check local cache first (for this request)
        state = object.__getattribute__(self, '_state')
        if name in state:
            return state[name]

        # Load from Shelve
        store = object.__getattribute__(self, '_store')
        session_id = object.__getattribute__(self, '_session_id')
        if store:
            key = f"{session_id}:{name}"
            value = store.get(key)
            if value is not None:
                state[name] = value
                return value

        # Return default
        defaults = object.__getattribute__(self, '_DEFAULTS')
        return defaults.get(name)

    def __setattr__(self, name: str, value):
        """
        Magic setter - writes to Shelve automatically.

        ctx.file_policy = "SEARCH"  ->  shelve[session_id:file_policy] = "SEARCH"

        Handles list/dict with deep copy for clean serialization.
        """
        # Deep copy lists/dicts to ensure clean serialization
        if isinstance(value, (list, dict)):
            value = copy.deepcopy(value)

        # Update local cache
        state = object.__getattribute__(self, '_state')
        state[name] = value

        # Persist to Shelve
        store = object.__getattribute__(self, '_store')
        session_id = object.__getattribute__(self, '_session_id')
        if store:
            key = f"{session_id}:{name}"
            store.set(key, value)

    # === Computed Properties (not persisted) ===
    @property
    def transcript(self) -> LazyTranscript:
        t = object.__getattribute__(self, '_transcript')
        if t is None:
            t = LazyTranscript(self._session_transcript)
            object.__setattr__(self, '_transcript', t)
        return t

    @property
    def has_justification(self) -> bool:
        return self.transcript.has_justification()

    @property
    def file_exists(self) -> bool:
        file_path = self._tool_input.get("file_path", "")
        if not file_path:
            return False
        try:
            return Path(file_path).resolve().exists()
        except (OSError, ValueError):
            return False

    @staticmethod
    def _resolve_channel(param: Union[bool, str], default_text: str) -> Optional[str]:
        """Resolve a to_human/to_ai channel parameter to message text or None.

        Args:
            param: True → use default_text, False → skip channel, str → use as message
            default_text: The reason text to use when param is True
        Returns:
            Message text for the channel, or None to skip it.
        """
        if param is True:
            return default_text
        if isinstance(param, str) and param:
            return param
        return None  # False, empty string, or unsupported type → skip

    # === UNIFIED RESPONSE BUILDER (DRY: single method handles all events) ===
    def respond(self, decision: str = "allow", reason: str = "", *,
                to_human: Union[bool, str] = True, to_ai: Union[bool, str] = True) -> dict:
        """
        Unified response builder - automatically formats for event type.

        Returns a response compatible with BOTH Claude Code and Gemini CLI:
        - Claude Code reads: hookSpecificOutput.permissionDecision
        - Gemini CLI reads: top-level decision field

        Ensures both CLIs are first-class citizens by mapping decisions
        to their respective capabilities (e.g., 'ask' for Claude, 'deny' for Gemini).

        Args:
            decision: One of "allow", "deny", "ask", "block"
            reason: Message to include in response
            to_human: Controls systemMessage (user terminal). Only meaningful for PATHWAY 2
                (PostToolUse and UserPromptSubmit events):
                  True (default) — systemMessage = reason
                  False          — no systemMessage
                  "custom msg"   — systemMessage = custom string
            to_ai: Controls hookSpecificOutput.additionalContext (Claude AI context).
                Only meaningful for PATHWAY 2:
                  True (default) — additionalContext = reason
                  False          — no hookSpecificOutput
                  "custom msg"   — additionalContext = custom string
                All other pathways (PreToolUse, Stop, SessionStart, etc.) silently ignore
                to_human/to_ai to preserve their existing semantics and safety invariants.

        Returns:
            dict: Hook response compatible with Claude Code and Gemini CLI

        Usage:
            return ctx.respond("allow")
            return ctx.respond("deny", "File creation blocked")
            return ctx.respond("block", injection_prompt)  # For Stop events

        Exit Code Semantics (CORRECTED):
        --------------------------------
        Exit code 0 = hook succeeded (even when denying tool access)
        Exit code 2 = blocking ERROR causing "hook error" in UI (Claude Code only)
        JSON permissionDecision: "deny" blocks the tool
        JSON systemMessage shows suggestion
        JSON continue: true lets AI continue

        References:
        - GitHub Issues: #4669, #18312, #13744, #20946, #10964
        - Hook docs: https://code.claude.com/docs/en/hooks
        - CLAUDE.md: Hook Error Prevention section
        """
        cli_type = self.cli_type
        logger.debug(f"respond: event={self._event} decision={decision} to_human={to_human!r} to_ai={to_ai!r} cli_type={cli_type}")
        # This keeps both CLIs as first-class citizens by using the best available
        # blocking mechanism for each.
        if decision == "ask" and cli_type == "gemini":
            decision = "deny"

        # Use the raw reason - final json.dumps() in the daemon/client will handle escaping.
        msg_reason = reason or ""

        # =====================================================================
        # PATHWAY 1: PreToolUse (Permission Decisions)
        # =====================================================================
        if self._event == "PreToolUse":
            # PATHWAY 1: to_human/to_ai silently ignored — hookSpecificOutput always kept for security.
            if to_human is not True or to_ai is not True:
                logger.debug("respond: to_human/to_ai ignored for PreToolUse — hookSpecificOutput always kept")
            # Claude Code PreToolUse Schema:
            # - top-level 'decision': "approve" | "block"
            # - top-level 'permissionDecision': "allow" | "deny" | "ask"
            # - hookSpecificOutput: { permissionDecision, permissionDecisionReason }
            top_decision = "block" if decision == "deny" else "approve"
            
            if cli_type == "gemini":
                # Gemini CLI uses standard allow/deny top-level decision.
                # Map any blocking decision (deny, block, ask) to "deny".
                top_decision = "allow" if decision == "allow" else "deny"
                logger.debug(f"respond: gemini PreToolUse decision={decision} -> top_decision={top_decision}")

            # To avoid triple-printing in the UI, we only provide the reason 
            # in hookSpecificOutput. Claude will also show stderr for exit 2.
            is_deny = decision == "deny"

            # CRITICAL SEMANTICS:
            # 1. 'continue: True' means the AI loop keeps running. We ALWAYS want this
            #    on tool denial so the AI can see the feedback and suggest alternatives.
            #    Setting this to False would stop the entire agent session.
            # 2. 'decision' / 'permissionDecision' controls the TOOL, not the AI.
            #    Denying the tool (block/deny) prevents execution while the AI continues.
            # === PreToolUse response field semantics (ANTI-TRIPLE-PRINT DESIGN) ===
            # Claude Code deny:  reason="" and systemMessage="" intentionally.
            #   The message goes to the user via stderr (exit 2 workaround for bug #4669).
            #   Putting reason/systemMessage would cause triple-printing in the Claude UI.
            # Claude Code allow: reason=msg and systemMessage=msg (normal display).
            # Gemini deny/allow: reason=msg and systemMessage=msg (Gemini has no exit-2 quirk).
            # ALL paths:         hookSpecificOutput.permissionDecisionReason=msg ALWAYS set.
            #   This is the CANONICAL, PORTABLE location for the blocking/warning message.
            #   Tests should assert on hookSpecificOutput.permissionDecisionReason, not reason.
            resp = {
                "decision": top_decision,
                "permissionDecision": decision,
                "reason": msg_reason if cli_type == "gemini" else ("" if is_deny else msg_reason),
                "continue": True,
                "stopReason": "",
                "suppressOutput": False,
                "systemMessage": msg_reason if cli_type == "gemini" else ("" if is_deny else msg_reason),
                # Claude Code hookSpecificOutput (REQUIRED for PreToolUse)
                # Gemini CLI hookSpecificOutput (BeforeTool expects hookEventName: "BeforeTool")
                "hookSpecificOutput": {
                    "hookEventName": get_cli_event_name(self._event, cli_type),
                    "permissionDecision": decision,
                    "permissionDecisionReason": msg_reason  # ALWAYS populated — use this in tests
                },
            }
            return validate_hook_response(self._event, resp, cli_type=cli_type)

        # =====================================================================
        # PATHWAY 2: UserPromptSubmit & PostToolUse (Context Injection)
        # =====================================================================
        if self._event in ("UserPromptSubmit", "PostToolUse"):
            human_text = self._resolve_channel(to_human, msg_reason)
            ai_text = self._resolve_channel(to_ai, msg_reason)

            # systemMessage: prefer human_text, fall back to ai_text for backwards compat.
            # reason="": prevents double-print when systemMessage is set
            #   (claude-code-hooks-api.md:202-210).
            # NOTE: reason="" keyed on human_text (not sys_msg) to preserve backwards compat:
            #   to_human=False → human_text=None → reason=msg_reason (old AI-injection path kept)
            sys_msg = human_text or ai_text or ""
            resp = {
                "decision": "approve",
                "reason": "" if human_text else msg_reason,
                "continue": True,
                "stopReason": "",
                "suppressOutput": False,
                "systemMessage": sys_msg,
            }

            if ai_text is not None:
                resp["hookSpecificOutput"] = {
                    "hookEventName": self._event,
                    "additionalContext": ai_text,
                }

            return validate_hook_response(self._event, resp, cli_type=cli_type)

        # =====================================================================
        # PATHWAY 3: Stop & SubagentStop (Stop Prevention)
        # =====================================================================
        if self._event in ("Stop", "SubagentStop"):
            # PATHWAY 3: to_human/to_ai silently ignored — systemMessage already human+AI visible.
            if to_human is not True or to_ai is not True:
                logger.debug(f"respond: to_human/to_ai ignored for {self._event} — systemMessage already human-visible")
            # Claude Code Stop Schema:
            # - MUST NOT contain 'decision' or 'reason' for standard allow
            # - ONLY supports 'continue', 'stopReason', 'suppressOutput', 'systemMessage'
            if decision == "block":
                # For Gemini, 'block' decision must be 'deny' to trigger retry
                actual_decision = "deny" if cli_type == "gemini" else "block"
                resp = {
                    "continue": True,  # Keep AI working
                    "decision": actual_decision,
                    "reason": msg_reason,
                    "stopReason": "",
                    "suppressOutput": False,
                    "systemMessage": msg_reason,
                }
                return validate_hook_response(self._event, resp, cli_type=cli_type)
            
            resp = {
                "continue": True,
                "stopReason": "",
                "suppressOutput": False,
                "systemMessage": "",
            }
            return validate_hook_response(self._event, resp, cli_type=cli_type)

        # =====================================================================
        # PATHWAY 4: SessionStart (Startup Injections)
        # =====================================================================
        if self._event == "SessionStart":
            # PATHWAY 4: to_human/to_ai silently ignored — SessionStart systemMessage is always the
            # only notification channel (hookSpecificOutput impossible per HOOK_SCHEMAS).
            if to_human is not True or to_ai is not True:
                logger.debug("respond: to_human/to_ai ignored for SessionStart — systemMessage always human-visible")
            # Claude Code SessionStart Schema:
            # - MUST NOT contain 'decision', 'reason', or 'hookSpecificOutput'
            resp = {
                "continue": True,
                "stopReason": "",
                "suppressOutput": False,
                "systemMessage": msg_reason,
            }
            return validate_hook_response(self._event, resp, cli_type=cli_type)

        # =====================================================================
        # FALLBACK: Universal Default
        # =====================================================================
        final_response = {
            "continue": True,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": msg_reason,
        }
        
        # Enforce strict schema validation before returning
        return validate_hook_response(self._event, final_response, cli_type=cli_type)

    def command_response(self, response_text: str, continue_loop: bool = True) -> Dict:
        """
        Response for locally-handled commands (UserPromptSubmit).

        Args:
            response_text: The command output message
            continue_loop: True (default) keeps AI running. False for estop/stop.

        Usage:
            return ctx.command_response("✅ AutoFile policy: strict-search")
            return ctx.command_response("Emergency stop!", continue_loop=False)
        """
        resp = {
            "continue": continue_loop,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": response_text,
        }
        return validate_hook_response(self._event, resp, cli_type=self.cli_type)

    # === Convenience aliases ===
    def allow(self, reason: str = "") -> Dict:
        return self.respond("allow", reason)

    def deny(self, reason: str) -> Dict:
        return self.respond("deny", reason)

    def ask(self, reason: str) -> Dict:
        return self.respond("ask", reason)

    def block(self, reason: str) -> Dict:
        return self.respond("block", reason)


# === AUTORUN APP (Click-style Registration + Unified Dispatch) ===
class AutorunApp:
    """
    Click/Typer-style decorator framework with unified dispatch.

    DRY Features:
    - Single @app.command() decorator for all command types
    - Single @app.on() decorator for all event chains
    - Single _run_chain() method for all chain execution
    - Automatic command matching via CONFIG + aliases

    Usage:
        @app.command("/ar:a", "/ar:allow", "/afa", "ALLOW")
        def handle_allow(ctx): ctx.file_policy = "ALLOW"; return "Done"

        @app.on("PreToolUse")
        def enforce_policy(ctx): return ctx.deny("Blocked") if blocked else None
    """

    def __init__(self):
        self.command_handlers: Dict[str, Callable] = {}
        self.chains: Dict[str, List[Callable]] = {
            "PreToolUse": [],
            "Stop": [],
            "SessionStart": [],
            "PostToolUse": [],
        }

    def command(self, *aliases: str):
        """Register command handler with multiple aliases (DRY: single decorator)."""
        def decorator(func: Callable):
            for alias in aliases:
                self.command_handlers[alias] = func
            return func
        return decorator

    # Semantic aliases - all point to same decorator
    policy = block = workflow = command

    def on(self, event: str):
        """Register chain handler for event (DRY: single decorator)."""
        def decorator(func: Callable):
            # SubagentStop shares Stop chain
            target = "Stop" if event == "SubagentStop" else event
            if target in self.chains:
                self.chains[target].append(func)
            return func
        return decorator

    def _run_chain(self, ctx: EventContext, chain_name: str) -> Optional[Dict]:
        """
        Run a handler chain, return first non-None result (DRY: single chain runner).

        Used by all event types - eliminates duplicate chain execution code.
        """
        for handler in self.chains.get(chain_name, []):
            result = handler(ctx)
            if result is not None:
                return result
        return None

    def _find_command(self, prompt: str) -> Optional[tuple]:
        """
        Find matching command handler (DRY: single lookup logic).

        Returns (handler, matched_alias) or None.
        """
        # Check CONFIG command_mappings
        command = CONFIG["command_mappings"].get(prompt)
        if command and command in self.command_handlers:
            return (self.command_handlers[command], command)

        # Check direct aliases
        for alias, handler in self.command_handlers.items():
            if prompt == alias or prompt.startswith(f"{alias} "):
                return (handler, alias)

        return None

    def dispatch(self, ctx: EventContext) -> Dict:
        """
        Unified dispatch - routes all events through consistent pattern.

        DRY: Single method handles all event types with minimal branching.
        """
        event = ctx.event

        # UserPromptSubmit: Check commands first
        if event == "UserPromptSubmit":
            match = self._find_command(ctx.prompt.strip())
            if match:
                handler, alias = match
                ctx.activation_prompt = ctx.prompt
                response_text = handler(ctx)
                # Stop/estop handlers set _halt_ai=True to kill AI loop
                halt = getattr(ctx, '_halt_ai', False)
                return ctx.command_response(response_text, continue_loop=not halt)
            # Non-commands continue to AI
            return ctx.respond("allow")

        # Chain events: Run appropriate chain
        chain_name = "Stop" if event in ("Stop", "SubagentStop") else event
        if chain_name in self.chains:
            result = self._run_chain(ctx, chain_name)
            if result is not None:
                return result

        # Pass-through: return None when nothing fired.
        # Autorun subprocess exits 0 with NO stdout → Claude Code ignores it entirely.
        # This allows parallel hooks (e.g. RTK) to apply updatedInput without conflict.
        # RTK substitutes "ls -alh" → "rtk ls -alh" for 60-90% token savings on common cmds.
        # Contrast with UserPromptSubmit non-commands (line 1144) which still return explicit
        # allow — needed to signal the prompt was handled by the AI, not a command.
        return None


# === DAEMON ===
class AutorunDaemon:
    """
    AsyncIO Unix socket daemon for fast hook handling.

    Replaces per-invocation process startup (50-150ms) with
    persistent daemon (1-5ms response time).

    Lifecycle:
    - Tracks active Claude session PIDs via active_pids set
    - Watchdog runs every 60s: cleans dead PIDs, idle shutdown after 30min
    - Signal handlers (SIGTERM/SIGINT) trigger graceful async_stop()
    - atexit registration ensures cleanup on unexpected exit

    Shutdown Mechanism:
    - Signals (SIGTERM/SIGINT): Handled via loop.add_signal_handler() for async safety
    - Watchdog timeout: Calls async_stop() directly from async context
    - atexit: Fallback cleanup for unexpected termination
    """

    def __init__(self, app: AutorunApp):
        """
        Initialize daemon with app and shared state.

        Args:
            app: AutorunApp instance with registered handlers
        """
        self.app = app
        self.running = False
        self.last_activity = time.time()
        self.active_pids: Set[int] = set()
        self._server = None
        self._daemon_lock = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._shutdown_event: Optional[asyncio.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.store = ThreadSafeDB()
        self._cleanup_registered = False

    def _pid_exists(self, pid: int) -> bool:
        """Check if process with given PID exists.

        Uses psutil for cross-platform correctness. On Windows, os.kill(pid, 0)
        sends CTRL_C_EVENT (signal 0 = CTRL_C_EVENT) instead of checking
        existence, which causes a spurious KeyboardInterrupt.
        """
        import psutil
        return psutil.pid_exists(pid)

    async def handle_client(self, reader: asyncio.StreamReader,
                           writer: asyncio.StreamWriter):
        """Handle single hook request.

        Note: Server uses READ_BUFFER_LIMIT (1GB) to accept large payloads,
        then truncates transcript to ~64KB after reading (see normalize_hook_payload).
        """
        import time
        start_time = time.time()
        self.last_activity = start_time
        # None = pass-through (dispatch returned None = nothing fired = output nothing)
        # _fallback = used only for error cases (timeout, buffer overflow, exception)
        response = None
        _fallback = {"continue": True, "stopReason": "", "suppressOutput": False, "systemMessage": ""}

        try:
            # Read payload (READ_BUFFER_LIMIT set on server to accept large payloads)
            # Truncates transcript to ~64KB AFTER reading (see normalize_hook_payload below)
            data = await reader.readuntil(b'\n')
            payload = json.loads(data.decode())

            event = payload.get("hook_event_name", "unknown")
            tool = payload.get("tool_name", "")

            from .client import _log_hook_lifecycle
            _log_hook_lifecycle("DAEMON PROCESSING START", Event=event, Tool=tool)

            # Debug logging (lazy % formatting avoids str(payload) when debug is off)
            logger.debug("Daemon received payload (%d bytes): %s", len(data), payload)

            # Track the Claude session PID (injected by client)
            pid = payload.get("_pid")
            if pid and pid not in self.active_pids:
                self.active_pids.add(pid)
                logger.info(f"New session PID: {pid} (active: {len(self.active_pids)})")

            # Normalize payload (includes transcript truncation to ~64KB)
            normalized = normalize_hook_payload(payload)

            # Detect CLI type from payload (ensures correct schema for shared daemon)
            from .config import detect_cli_type
            cli_type = detect_cli_type(payload)
            logger.info(f"handle_client: cli_type={cli_type} event={event} tool={tool}")

            # Resolve session identity (supports tri-layer for resume robustness)
            raw_session_id = normalized["session_id"]
            session_id = resolve_session_key(pid, payload.get("_cwd", ""), raw_session_id)

            # Build context with shared store for magic state persistence
            # _cwd is injected by client.py:197 and used by plan_export.py:project_dir
            ctx = EventContext(
                session_id=session_id,
                event=normalized["hook_event_name"],
                prompt=normalized["prompt"],
                tool_name=normalized["tool_name"],
                tool_input=normalized["tool_input"],
                tool_result=normalized["tool_result"],
                session_transcript=normalized["session_transcript"],
                store=self.store,
                cli_type=cli_type,
                cwd=payload.get("_cwd"),
                permission_mode=normalized["permission_mode"],
                source=normalized["source"],
            )

            # Dispatch — run in thread pool to avoid blocking the asyncio event loop.
            # Synchronous/blocking work in handlers (file I/O, locks) runs in a thread.
            loop = asyncio.get_running_loop()
            try:
                response = await asyncio.wait_for(
                    loop.run_in_executor(None, self.app.dispatch, ctx),
                    timeout=15.0,
                )
            except asyncio.TimeoutError:
                logger.error(
                    f"Handler for '{ctx.event}' timed out after 15s (fail-open)"
                )
                _fallback["systemMessage"] = f"Daemon handler for '{ctx.event}' timed out after 15s"
                response = _fallback

        except asyncio.LimitOverrunError as e:
            # Buffer size exceeded - provide actionable guidance
            current_mb = READ_BUFFER_LIMIT // (1024 * 1024)
            logger.error(f"Buffer overflow: Session transcript exceeded {current_mb}MB limit", exc_info=True)
            _fallback["systemMessage"] = (
                f"Daemon buffer overflow (fail-open): Session transcript exceeded {current_mb}MB.\n\n"
                f"SOLUTION: Increase buffer size with environment variable:\n"
                f"  export AUTORUN_BUFFER_LIMIT={READ_BUFFER_LIMIT * 2}  # {current_mb * 2}MB\n"
                f"  # Then restart daemon: uv run python plugins/autorun/scripts/restart_daemon.py\n\n"
                f"Current limit: {current_mb}MB (READ_BUFFER_LIMIT={READ_BUFFER_LIMIT:,} bytes)\n"
                f"Details: {e}"
            )
            response = _fallback
        except Exception as e:
            logger.error(f"Handler error: {e}", exc_info=True)
            _fallback["systemMessage"] = f"Daemon error (fail-open): {e}"
            response = _fallback

        finally:
            # None = pass-through: send {} so client exits 0 with no stdout output
            final = response if response is not None else {}
            # Debug logging (ALWAYS enabled)
            response_json = json.dumps(final)
            logger.debug(f"Daemon sending response ({len(response_json)} bytes): {response_json}")

            writer.write(response_json.encode() + b'\n')
            await writer.drain()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass  # Ignore errors on close (connection may already be gone)

            duration = (time.time() - start_time) * 1000
            from .client import _log_hook_lifecycle
            _log_hook_lifecycle("DAEMON PROCESSING END", Event=event, Duration=f"{duration:.2f}ms")

    async def watchdog(self):
        """
        PID-aware lifecycle management.

        - Cleans up dead PIDs (crashed Claude sessions)
        - Shuts down when all sessions exit AND idle timeout reached
        - Checks shutdown event for graceful termination
        """
        try:
            while self.running:
                # Use wait_for with shutdown event for responsive shutdown
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait() if self._shutdown_event else asyncio.sleep(60),
                        timeout=60.0
                    )
                    # If shutdown event was set, exit loop
                    if self._shutdown_event and self._shutdown_event.is_set():
                        break
                except asyncio.TimeoutError:
                    pass  # Normal timeout, continue with cleanup

                if not self.running:
                    break

                now = time.time()

                # Clean dead PIDs
                dead = {pid for pid in self.active_pids if not self._pid_exists(pid)}
                if dead:
                    logger.info(f"Cleaned {len(dead)} dead PIDs: {dead}")
                self.active_pids -= dead

                # Shutdown when no active sessions AND idle timeout
                if not self.active_pids and (now - self.last_activity > IDLE_TIMEOUT):
                    logger.info("Idle timeout reached, shutting down")
                    await self.async_stop()
                    break
        except asyncio.CancelledError:
            logger.info("Watchdog task cancelled")
            raise

    async def async_stop(self):
        """
        Async graceful shutdown with proper cleanup ordering.

        Cleans up in order:
        1. Set shutdown flag and event
        2. Cancel watchdog task
        3. Close server and wait for connections to drain
        4. Remove socket file
        5. Release and remove lockfile
        """
        if not self.running:
            return  # Already stopping/stopped

        logger.info("Daemon stopping (async)")
        self.running = False

        # Signal shutdown event
        if self._shutdown_event:
            self._shutdown_event.set()

        # Cancel watchdog task
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass

        # Close server gracefully
        if self._server:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Server close timed out")

        # Cleanup files
        self._cleanup_files()

    def stop(self):
        """
        Synchronous stop - schedules async_stop on the event loop.

        Use this from signal handlers or non-async contexts.
        For async contexts, use async_stop() directly.
        """
        logger.info("Daemon stopping (sync)")
        self.running = False

        # Signal shutdown event
        if self._shutdown_event:
            self._shutdown_event.set()

        # Schedule async cleanup if we have a loop
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self.async_stop())
            )
        else:
            # Fallback to sync cleanup if no loop
            self._cleanup_files()

    def _cleanup_files(self):
        """
        Cleanup socket/port and lock files.

        Safe to call multiple times.
        """
        # Remove IPC socket/port file
        ipc.cleanup_socket()

        # Release daemon lock
        if self._daemon_lock:
            try:
                self._daemon_lock.release()
            except Exception:
                pass
            self._daemon_lock = None

        # Remove lock and flock files
        for lock_file in [LOCK_PATH, LOCK_PATH.with_suffix('.flock')]:
            try:
                if lock_file.exists():
                    lock_file.unlink()
                    logger.debug(f"Removed lock: {lock_file}")
            except OSError as e:
                logger.warning(f"Failed to remove lock file {lock_file}: {e}")

    def _acquire_daemon_lock(self) -> bool:
        """
        Acquire exclusive daemon lock using filelock (cross-platform).

        Returns:
            bool: True if lock acquired, False if another daemon running

        Uses filelock on a dedicated lock file (daemon.flock) for cross-platform
        atomic locking. PID is written to daemon.lock for consumer discovery
        (restart_daemon.py, client.py, install.py all read daemon.lock for PID).
        Falls back to socket connect test if lock unavailable (NFS).
        """
        from filelock import FileLock, Timeout
        flock_path = LOCK_PATH.with_suffix('.flock')
        try:
            self._daemon_lock = FileLock(str(flock_path), timeout=0)
            self._daemon_lock.acquire()
            # Write PID to daemon.lock for discovery by other processes
            LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")
            return True
        except Timeout:
            self._daemon_lock = None
            return False
        except OSError:
            self._daemon_lock = None
            return self._socket_connect_test()

    def _socket_connect_test(self) -> bool:
        """
        Fallback daemon check via socket connection test.

        Returns:
            bool: True if no daemon running (can proceed), False if daemon running
        """
        return ipc.socket_connect_test()

    def _cleanup_stale_socket(self):
        """
        Remove stale socket from crashed daemon.

        Uses lockfile for atomic check - if we can acquire lock,
        any existing socket is stale. Falls back to socket test if needed.
        """
        if not self._acquire_daemon_lock():
            raise RuntimeError("Another daemon is already running")
        # Clean up any stale IPC socket/port file from a previous crash
        ipc.cleanup_socket()

    def _register_atexit_cleanup(self):
        """Register atexit handler for cleanup on unexpected exit."""
        if self._cleanup_registered:
            return
        import atexit
        atexit.register(self._cleanup_files)
        self._cleanup_registered = True
        logger.debug("Registered atexit cleanup handler")

    def _setup_signal_handlers(self):
        """
        Set up async-safe signal handlers.

        Uses loop.add_signal_handler() for proper asyncio integration.
        Handles SIGTERM, SIGINT, and SIGHUP.
        """
        if not self._loop:
            return

        def handle_signal(sig_name: str):
            """Signal handler that schedules async shutdown."""
            logger.info(f"Received {sig_name}")
            asyncio.create_task(self.async_stop())

        try:
            self._loop.add_signal_handler(
                signal.SIGTERM,
                lambda: handle_signal("SIGTERM")
            )
            self._loop.add_signal_handler(
                signal.SIGINT,
                lambda: handle_signal("SIGINT")
            )
            # SIGHUP for graceful restart (just log for now)
            self._loop.add_signal_handler(
                signal.SIGHUP,
                lambda: logger.info("Received SIGHUP (ignored)")
            )
            logger.debug("Signal handlers registered")
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            logger.warning("Async signal handlers not supported on this platform")

    def _self_heal_cache_hooks(self) -> None:
        """One-time: remove Gemini hooks.json from Claude Code plugin cache.

        Catches external installs (claude plugin install from GitHub) that
        bypass our installer entirely. Runs once per daemon lifetime at startup.
        Also cleans stale old version directories.
        """
        try:
            from .install import _clean_cross_cli_hooks, MARKETPLACE
            cache_root = Path.home() / ".claude" / "plugins" / "cache" / MARKETPLACE
            if not cache_root.exists():
                return
            for plugin_dir in cache_root.iterdir():
                if not plugin_dir.is_dir():
                    continue
                for version_dir in plugin_dir.iterdir():
                    if version_dir.is_dir():
                        _clean_cross_cli_hooks(version_dir, target_cli="claude")
            logger.debug("Cache self-healing complete")
        except Exception as e:
            logger.debug(f"Cache self-healing skipped: {e}")

    async def start(self):
        """
        Start daemon server with proper lifecycle management.

        Sets up:
        - Shutdown event for coordinated termination
        - Signal handlers for SIGTERM/SIGINT
        - atexit cleanup registration
        - Watchdog task for PID cleanup and idle shutdown
        """
        self._cleanup_stale_socket()

        # Get event loop reference for signal handling
        self._loop = asyncio.get_running_loop()

        # Create shutdown coordination event
        self._shutdown_event = asyncio.Event()

        # Register cleanup handlers
        self._register_atexit_cleanup()
        self._setup_signal_handlers()

        # Start server with large buffer to accept full payloads before truncation
        # Default 64KB too small - client sends full transcript, we truncate after reading
        self._server = await ipc.start_server(
            self.handle_client, limit=READ_BUFFER_LIMIT
        )
        self.running = True

        # One-time: clean cross-CLI hooks from Claude Code cache.
        # Catches external installs (claude plugin install) that bypass our installer.
        self._self_heal_cache_hooks()

        # Start watchdog as tracked task
        self._watchdog_task = asyncio.create_task(self.watchdog())

        logger.info(f"Daemon started on {ipc.get_address()}")

        try:
            async with self._server:
                # Wait for shutdown event instead of serve_forever
                # This allows graceful termination
                await self._shutdown_event.wait()
        except asyncio.CancelledError:
            logger.info("Server task cancelled")
        finally:
            # Ensure cleanup happens
            await self.async_stop()


# Global app instance
app = AutorunApp()
