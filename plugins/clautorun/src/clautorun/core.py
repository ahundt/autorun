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
Clautorun v0.7 Core - Click/Typer-style Decorator Framework

Provides:
- LazyTranscript: Deferred string conversion for performance
- ThreadSafeDB: In-memory cache layer for daemon performance
- EventContext: Rich context with magic __getattr__/__setattr__ state access
- ClautorunApp: Click-style decorator registration
- ClautorunDaemon: AsyncIO Unix socket server

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
from typing import Any, Optional, Dict, List, Callable, Set
from functools import lru_cache

# Reuse existing session_manager (CRITICAL: preserves RAII, locks, backends)
from .session_manager import session_state
from .config import CONFIG

# === CONFIGURATION ===
HOME_DIR = Path.home() / ".clautorun"
HOME_DIR.mkdir(mode=0o700, exist_ok=True)
SOCKET_PATH = HOME_DIR / "daemon.sock"
LOCK_PATH = HOME_DIR / "daemon.lock"
LOG_FILE = HOME_DIR / "daemon.log"
IDLE_TIMEOUT = 1800  # 30 minutes

# Buffer size for reading hook payloads (asyncio default is 64KB = 2^16)
# Need larger than default to accept full payloads before truncating
# Client sends full transcript (can be 200MB+), server truncates to 64KB after reading
_DEFAULT_LIMIT = asyncio.streams._DEFAULT_LIMIT  # 64KB (2^16 = 65536)

# Allow override via CLAUTORUN_BUFFER_LIMIT env var (in bytes)
# Default: 1GB (2^30) handles sessions up to 1GB
# Found actual sessions: 511MB, so need more than 512MB headroom
_env_limit = os.environ.get("CLAUTORUN_BUFFER_LIMIT")
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
logger = logging.getLogger("clautorun")


# === GEMINI CLI PAYLOAD NORMALIZATION ===
# Gemini CLI uses different event names and camelCase keys vs Claude Code's snake_case.
# This mapping normalizes both formats to a single internal representation.

GEMINI_EVENT_MAP = {
    "BeforeTool": "PreToolUse",
    "AfterTool": "PostToolUse",
    "BeforeAgent": "UserPromptSubmit",
    "SessionStart": "SessionStart",
    "SessionEnd": "SessionEnd",
}


def normalize_hook_payload(payload: dict, truncate_transcript: bool = True) -> dict:
    """Normalize hook payload from any CLI format and optionally truncate transcript.

    Normalization:
    - Claude Code: hook_event_name, session_id, tool_name (snake_case)
    - Gemini CLI: type, sessionId, toolName (camelCase)

    Transcript Truncation (configurable):
    - session_transcript can be 200MB+ in long sessions
    - Hooks only search recent patterns (stage markers, justification tags)
    - Truncate to last ~64KB by default (saves memory, speeds pattern search)
    - Can disable via truncate_transcript=False or CLAUTORUN_NO_TRUNCATE=1 env var

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

    # Get transcript
    transcript = payload.get("session_transcript", [])

    # Check if truncation disabled globally via env var
    if os.environ.get("CLAUTORUN_NO_TRUNCATE") == "1":
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
        "session_id": payload.get("session_id") or payload.get("sessionId", ""),
        "prompt": payload.get("prompt", ""),
        "tool_name": payload.get("tool_name") or payload.get("toolName", ""),
        "tool_input": payload.get("tool_input") or payload.get("toolInput", {}),
        "tool_result": payload.get("tool_result") or payload.get("toolResult"),
        "session_transcript": transcript,
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
    1. CLAUTORUN_SESSION_ID env var (explicit override)
    2. JSONL history file scan (survives claude --resume)
    3. session_id from payload (fallback)

    Usage: Enable with CLAUTORUN_USE_IDENTITY=1
    """
    import platform

    # Layer 1: Explicit env var
    if env_id := os.environ.get("CLAUTORUN_SESSION_ID"):
        return f"explicit:{env_id}"

    # Layer 2: JSONL file (for resume robustness)
    if os.environ.get("CLAUTORUN_USE_IDENTITY") == "1":
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
                 '_tool_result', '_session_transcript', '_state', '_transcript', '_store')

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
        'plan_arguments': '',  # v0.7: Store original user request from $ARGUMENTS
    }

    def __init__(self, session_id: str, event: str, prompt: str = "",
                 tool_name: str = None, tool_input: Dict = None,
                 tool_result: str = None, session_transcript: List = None,
                 store: 'ThreadSafeDB' = None):
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

    # === Read-only accessors for payload data ===
    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def event(self) -> str:
        return self._event

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

    # === UNIFIED RESPONSE BUILDER (DRY: single method handles all events) ===
    @staticmethod
    def _escape_for_json(s: str) -> str:
        """
        Escape string for safe JSON embedding.

        Args:
            s: String to escape (will be converted if not string)

        Returns:
            str: JSON-escaped string without surrounding quotes
        """
        if not isinstance(s, str):
            s = str(s)
        return json.dumps(s)[1:-1]

    def respond(self, decision: str = "allow", reason: str = "") -> dict:
        """
        Unified response builder - automatically formats for event type.

        Returns a response compatible with BOTH Claude Code and Gemini CLI:
        - Claude Code reads: hookSpecificOutput.permissionDecision
        - Gemini CLI reads: top-level decision field

        Args:
            decision: One of "allow", "deny", "block"
            reason: Message to include in response

        Returns:
            dict: Hook response compatible with Claude Code and Gemini CLI

        Usage:
            return ctx.respond("allow")
            return ctx.respond("deny", "File creation blocked")
            return ctx.respond("block", injection_prompt)  # For Stop events

        Exit Code Semantics (CORRECTED):
        --------------------------------
        Exit code 0 = hook succeeded (even when denying tool access)
        Exit code 2 = blocking ERROR causing "hook error" in UI
        JSON permissionDecision: "deny" blocks the tool
        JSON systemMessage shows suggestion
        JSON continue: true lets Claude continue

        References:
        - GitHub Issues: #4669, #18312, #13744, #20946
        - Hook docs: https://code.claude.com/docs/en/hooks
        - CLAUDE.md: Hook Error Prevention section
        """
        reason_escaped = self._escape_for_json(reason) if reason else ""

        # PreToolUse needs hookSpecificOutput + top-level decision
        if self._event == "PreToolUse":
            # PreToolUse deny must NOT set continue=false — that stops the AI entirely.
            # Blocking is handled by:
            #   - permissionDecision:"deny" (Claude Code - correct behavior)
            #   - decision:"deny" (Gemini CLI BeforeTool)
            # Per docs: continue=false "stops processing entirely, takes precedence
            # over any event-specific decision fields" — NOT what we want.
            return {
                # Top-level decision for Gemini CLI
                "decision": decision,
                "reason": reason_escaped,
                # Universal fields - always continue=true, blocking handled elsewhere
                # continue=true is correct because:
                #   - Claude Code: "continue:false stops processing entirely"
                #     https://code.claude.com/docs/en/hooks#json-output
                #   - Gemini CLI: "continue:false stops agent loop"
                #     https://geminicli.com/docs/hooks/reference/
                # We want to block the TOOL (via permissionDecision:"deny")
                # but let the AI continue running to suggest alternatives.
                "continue": True,
                "stopReason": "",
                "suppressOutput": False,
                "systemMessage": reason_escaped,
                # Claude Code hookSpecificOutput
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": reason_escaped
                },
            }

        # Stop/SubagentStop with "block" decision
        if decision == "block":
            return {
                "continue": False,
                "stopReason": "",
                "suppressOutput": False,
                "systemMessage": reason,
                "decision": "block",
                "reason": reason
            }

        # Default hook response (unified format for both Claude and Gemini)
        return {
            "continue": True,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": "",
            "decision": decision,
            "reason": reason_escaped
        }

    def command_response(self, response_text: str) -> Dict:
        """
        Response for locally-handled commands (UserPromptSubmit).

        Commands handled locally should NOT continue to AI.

        Args:
            response_text: The command output message

        Returns:
            dict: Hook response with continue=False and response text

        Usage:
            return ctx.command_response("✅ AutoFile policy: strict-search")
        """
        return {
            "continue": False,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": response_text,
            "response": response_text  # Backward compatibility with tests
        }

    # === Convenience aliases ===
    def allow(self, reason: str = "") -> Dict:
        return self.respond("allow", reason)

    def deny(self, reason: str) -> Dict:
        return self.respond("deny", reason)

    def block(self, reason: str) -> Dict:
        return self.respond("block", reason)


# === CLAUTORUN APP (Click-style Registration + Unified Dispatch) ===
class ClautorunApp:
    """
    Click/Typer-style decorator framework with unified dispatch.

    DRY Features:
    - Single @app.command() decorator for all command types
    - Single @app.on() decorator for all event chains
    - Single _run_chain() method for all chain execution
    - Automatic command matching via CONFIG + aliases

    Usage:
        @app.command("/cr:a", "/cr:allow", "/afa", "ALLOW")
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
                # Commands handled locally should NOT continue to AI
                return ctx.command_response(response_text)
            # Non-commands continue to AI
            return ctx.respond("allow")

        # Chain events: Run appropriate chain
        chain_name = "Stop" if event in ("Stop", "SubagentStop") else event
        if chain_name in self.chains:
            result = self._run_chain(ctx, chain_name)
            if result is not None:
                return result

        # Default: allow
        return ctx.respond("allow")


# === DAEMON ===
class ClautorunDaemon:
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

    def __init__(self, app: ClautorunApp):
        """
        Initialize daemon with app and shared state.

        Args:
            app: ClautorunApp instance with registered handlers
        """
        self.app = app
        self.running = False
        self.last_activity = time.time()
        self.active_pids: Set[int] = set()
        self._server = None
        self._lock_fd = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._shutdown_event: Optional[asyncio.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.store = ThreadSafeDB()
        self._cleanup_registered = False

    def _pid_exists(self, pid: int) -> bool:
        """Check if process with given PID exists."""
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    async def handle_client(self, reader: asyncio.StreamReader,
                           writer: asyncio.StreamWriter):
        """Handle single hook request.

        Note: Server uses READ_BUFFER_LIMIT (1GB) to accept large payloads,
        then truncates transcript to ~64KB after reading (see normalize_hook_payload).
        """
        self.last_activity = time.time()
        response = {"continue": True, "stopReason": "", "suppressOutput": False, "systemMessage": ""}

        try:
            # Read payload (READ_BUFFER_LIMIT set on server to accept large payloads)
            # Truncates transcript to ~64KB AFTER reading (see normalize_hook_payload below)
            data = await reader.readuntil(b'\n')
            payload = json.loads(data.decode())

            # Debug logging (ALWAYS enabled)
            logger.debug(f"Daemon received payload ({len(data)} bytes): {str(payload)}")

            # Track the Claude session PID (injected by client)
            pid = payload.get("_pid")
            if pid and pid not in self.active_pids:
                self.active_pids.add(pid)
                logger.info(f"New session PID: {pid} (active: {len(self.active_pids)})")

            # Normalize payload (includes transcript truncation to ~64KB)
            normalized = normalize_hook_payload(payload)


            # Resolve session identity (supports tri-layer for resume robustness)
            raw_session_id = normalized["session_id"]
            session_id = resolve_session_key(pid, payload.get("_cwd", ""), raw_session_id)

            # Build context with shared store for magic state persistence
            ctx = EventContext(
                session_id=session_id,
                event=normalized["hook_event_name"],
                prompt=normalized["prompt"],
                tool_name=normalized["tool_name"],
                tool_input=normalized["tool_input"],
                tool_result=normalized["tool_result"],
                session_transcript=normalized["session_transcript"],
                store=self.store
            )

            # Dispatch
            response = self.app.dispatch(ctx)

        except asyncio.LimitOverrunError as e:
            # Buffer size exceeded - provide actionable guidance
            current_mb = READ_BUFFER_LIMIT // (1024 * 1024)
            logger.error(f"Buffer overflow: Session transcript exceeded {current_mb}MB limit", exc_info=True)
            response["systemMessage"] = (
                f"Daemon buffer overflow (fail-open): Session transcript exceeded {current_mb}MB.\n\n"
                f"SOLUTION: Increase buffer size with environment variable:\n"
                f"  export CLAUTORUN_BUFFER_LIMIT={READ_BUFFER_LIMIT * 2}  # {current_mb * 2}MB\n"
                f"  # Then restart daemon: uv run python plugins/clautorun/scripts/restart_daemon.py\n\n"
                f"Current limit: {current_mb}MB (READ_BUFFER_LIMIT={READ_BUFFER_LIMIT:,} bytes)\n"
                f"Details: {e}"
            )
        except Exception as e:
            logger.error(f"Handler error: {e}", exc_info=True)
            response["systemMessage"] = f"Daemon error (fail-open): {e}"

        finally:
            # Debug logging (ALWAYS enabled)
            response_json = json.dumps(response)
            logger.debug(f"Daemon sending response ({len(response_json)} bytes): {response_json}")

            writer.write(response_json.encode() + b'\n')
            await writer.drain()
            writer.close()
            await writer.wait_closed()

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
        Cleanup socket and lock files.

        Safe to call multiple times.
        """
        # Remove socket file
        try:
            if SOCKET_PATH.exists():
                SOCKET_PATH.unlink()
                logger.debug(f"Removed socket: {SOCKET_PATH}")
        except OSError as e:
            logger.warning(f"Failed to remove socket: {e}")

        # Release and remove lockfile
        if self._lock_fd:
            import fcntl
            try:
                fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_UN)
                self._lock_fd.close()
            except (IOError, OSError):
                pass
            self._lock_fd = None

        # Remove lock file
        try:
            if LOCK_PATH.exists():
                LOCK_PATH.unlink()
                logger.debug(f"Removed lock: {LOCK_PATH}")
        except OSError as e:
            logger.warning(f"Failed to remove lock file: {e}")

    def _acquire_daemon_lock(self) -> bool:
        """
        Acquire exclusive daemon lock using lockfile.

        Returns:
            bool: True if lock acquired, False if another daemon running

        Uses fcntl.flock for atomic cross-process locking.
        Falls back to socket connect test if flock unavailable (NFS).
        """
        import fcntl
        import errno
        try:
            self._lock_fd = open(LOCK_PATH, 'w')
            fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fd.write(str(os.getpid()))
            self._lock_fd.flush()
            return True
        except (IOError, OSError) as e:
            if self._lock_fd:
                self._lock_fd.close()
                self._lock_fd = None
            if getattr(e, 'errno', None) == errno.ENOLCK:
                return self._socket_connect_test()
            return False

    def _socket_connect_test(self) -> bool:
        """
        Fallback daemon check via socket connection test.

        Returns:
            bool: True if no daemon running (can proceed), False if daemon running
        """
        if not SOCKET_PATH.exists():
            return True
        import socket
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            sock.connect(str(SOCKET_PATH))
            sock.close()
            return False
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            return True

    def _cleanup_stale_socket(self):
        """
        Remove stale socket from crashed daemon.

        Uses lockfile for atomic check - if we can acquire lock,
        any existing socket is stale. Falls back to socket test if needed.
        """
        if not self._acquire_daemon_lock():
            raise RuntimeError("Another daemon is already running")
        if SOCKET_PATH.exists():
            logger.info("Removing stale socket")
            SOCKET_PATH.unlink()

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
        self._server = await asyncio.start_unix_server(
            self.handle_client, str(SOCKET_PATH),
            limit=READ_BUFFER_LIMIT
        )
        self.running = True

        # Start watchdog as tracked task
        self._watchdog_task = asyncio.create_task(self.watchdog())

        logger.info(f"Daemon started on {SOCKET_PATH}")

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
app = ClautorunApp()
