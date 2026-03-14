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
Autorun v0.7 Plugins - Magic State + DRY Factories

ZERO boilerplate per state field thanks to magic __getattr__/__setattr__.
Just write ctx.file_policy = "SEARCH" and it persists automatically!

Plugins:
- File Policy: ALLOW/JUSTIFY/SEARCH with PreToolUse enforcement
- Command Blocking: Session/global block/allow/clear/status with ReDoS protection
- Autorun: Three-stage workflow with sophisticated state machine
- Plan Management: New/refine/update/process plan commands
- AI Monitor Integration: External tmux observer (optional)
"""
import re
import fnmatch
import shlex
from functools import lru_cache
from pathlib import Path
from typing import Optional, Dict

from .core import app, EventContext, logger, format_suggestion
from .config import (
    CONFIG, DEFAULT_INTEGRATIONS,
    BASH_TOOLS, WRITE_TOOLS, EDIT_TOOLS, FILE_TOOLS, PLAN_TOOLS,
    TASK_CREATE_TOOLS, TASK_UPDATE_TOOLS
)
from .session_manager import session_state
from .scoped_allow import ScopedAllow, parse_scope_args
from .command_detection import command_matches_pattern
from .integrations import load_all_integrations, invalidate_caches, check_when_predicate, check_conditions

# Import plan_export to register its @app.on() handlers with daemon
from . import plan_export  # noqa: F401

# Import task_lifecycle and register hooks (if enabled)
from . import task_lifecycle  # noqa: F401
task_lifecycle.register_hooks(app)  # Register task lifecycle hooks


# ============================================================================
# FILE POLICY PLUGIN (DRY Factory Pattern)
# ============================================================================

def _make_policy_handler(policy_name: str):
    """
    Factory: Generate policy handler for given policy name.

    Args:
        policy_name: One of "ALLOW", "JUSTIFY", "SEARCH"

    Returns:
        Callable: Handler function that sets policy and returns status message
    """
    def handler(ctx: EventContext) -> str:
        ctx.file_policy = policy_name  # Magic: auto-persists!
        name, desc = CONFIG["policies"][policy_name]
        return f"✅ AutoFile policy: {name}\n\n{desc}"
    return handler


# Data-driven registration: easy to add new policies
_POLICY_ALIASES = {
    "ALLOW":   ("/ar:a", "/ar:allow", "/afa"),
    "JUSTIFY": ("/ar:j", "/ar:justify", "/afj"),
    "SEARCH":  ("/ar:f", "/ar:find", "/afs"),
}

for policy, aliases in _POLICY_ALIASES.items():
    app.command(*aliases, policy)(_make_policy_handler(policy))


@app.command("/ar:st", "/ar:status", "/afst", "STATUS")
def handle_status(ctx: EventContext) -> str:
    """
    Unified status: Shows file policy + session/global blocks and allows.
    """
    lines = []

    # File policy status
    current_policy = ctx.file_policy
    name, desc = CONFIG["policies"].get(current_policy, ("unknown", ""))
    lines.append(f"✅ AutoFile policy: {name}\n\n{desc}\n")

    # Block/Allow status — loop over scopes, DRY via _format_pattern_list
    control_lines = []
    for scope in ("session", "global"):
        accessor = ScopeAccessor(ctx, scope)
        scope_title = scope.title()
        section = (
            _format_pattern_list(accessor.get(), f"{scope_title} blocks", "🚫") +
            _format_pattern_list(accessor.get_allowed(), f"{scope_title} allows", "✅", show_scope=True)
        )
        if section:
            if control_lines:
                control_lines.append("")
            control_lines.extend(section)

    if control_lines:
        lines.append("\n📊 Command Control Status\n")
        lines.extend(control_lines)

    return "\n".join(lines)


@app.on("PreToolUse")
def enforce_file_policy(ctx: EventContext) -> Optional[Dict]:
    """Enforce file creation policy on Write tool."""
    if ctx.tool_name not in WRITE_TOOLS:
        return None

    policy = ctx.file_policy  # Magic: auto-loads from Shelve!

    if policy == "ALLOW":
        return None

    if policy == "SEARCH":
        if ctx.file_exists:
            return None
        return ctx.deny(format_suggestion(CONFIG["policy_blocked"]["SEARCH"], ctx.cli_type))

    if policy == "JUSTIFY":
        if ctx.file_exists or ctx.has_justification:
            return None
        return ctx.deny(format_suggestion(CONFIG["policy_blocked"]["JUSTIFY"], ctx.cli_type))

    return None


@app.on("PreToolUse")
def gate_exit_plan_mode(ctx: EventContext) -> Optional[Dict]:
    """Only allow ExitPlanMode after planning Stage 3 is complete (when autorun active)."""
    if ctx.tool_name not in PLAN_TOOLS:
        return None

    # REGRESSION PROTECTION: Only gate when autorun is active
    # If autorun NOT active (normal /ar:plannew without /ar:go), allow ExitPlanMode as before
    if not ctx.autorun_active:
        return None  # No gating - existing behavior preserved

    # When autorun IS active, check BOTH transcript AND current stage
    # Bug #9 Fix: Verify we're actually in Stage 3, not just that string appears in transcript
    # from previous session or user input
    transcript = ctx.transcript.text
    stage = ctx.autorun_stage

    # First check: Stage 3 message must be in transcript
    if CONFIG["stage3_message"] not in transcript:
        return ctx.deny(
            f"Cannot exit plan mode yet. Complete plan verification first.\n\n"
            f"Current requirement: Output **{CONFIG['stage3_message']}** when Stage 3 is complete.\n\n"
            f"Continue with plan verification using the three-stage system:\n"
            f"1. Stage 1: {CONFIG['stage1_instruction']}\n"
            f"2. Stage 2: {CONFIG['stage2_instruction']}\n"
            f"3. Stage 3: {CONFIG['stage3_instruction']}"
        )

    # Second check: Current autorun session must have actually completed Stage 2
    # STAGE_2_COMPLETED means AI output stage2_message and countdown is complete
    if stage != EventContext.STAGE_2_COMPLETED:
        return ctx.deny(
            f"Stage 3 not reached in current autorun session.\n\n"
            f"Current stage: {stage} (expected: {EventContext.STAGE_2_COMPLETED})\n\n"
            f"The stage3_message was found in transcript, but current session hasn't progressed to Stage 3.\n"
            f"Complete Stage 1 and Stage 2 in this session before exiting plan mode."
        )

    return None  # Allow ExitPlanMode - both checks pass


# ============================================================================
# COMMAND BLOCKING PLUGIN (DRY Mega-Factory + Security)
# ============================================================================

def _is_safe_regex(pattern: str, max_len: int = 200) -> bool:
    """
    Validate regex for ReDoS protection.

    Args:
        pattern: Regex pattern to validate
        max_len: Maximum allowed pattern length

    Returns:
        bool: True if pattern is safe, False if potentially dangerous
    """
    if len(pattern) > max_len:
        return False

    # Detect nested quantifiers (catastrophic backtracking)
    dangerous = [
        r'\([^)]*[+*?]\)[+*?]',
        r'\([^)]*[+*?]\)\{',
        r'\[[^\]]*\][+*?][+*?]'
    ]
    for d in dangerous:
        if re.search(d, pattern):
            return False

    try:
        re.compile(pattern)
        return True
    except re.error:
        return False


@lru_cache(maxsize=128)
def _compile_pattern(pattern: str, ptype: str):
    """
    Compile and cache regex patterns with security validation.

    Args:
        pattern: Pattern string to compile
        ptype: Pattern type ("literal", "regex", "glob")

    Returns:
        Compiled regex Pattern for "regex" type, or original string

    Raises:
        ValueError: If regex is unsafe (ReDoS) or invalid - NOT cached
    """
    if ptype == "regex":
        if not _is_safe_regex(pattern):
            raise ValueError(f"Unsafe regex (ReDoS): {pattern[:50]}")
        try:
            return re.compile(pattern)
        except re.error as e:
            raise ValueError(f"Invalid regex: {e}")
    return pattern


def _match(cmd: str, pattern: str, ptype: str = "literal") -> bool:
    """
    Unified pattern matching with security.

    Args:
        cmd: Command string to match against
        pattern: Pattern to match
        ptype: Pattern type - "literal", "regex", or "glob"

    Returns:
        bool: True if pattern matches command
    """
    if not cmd or not pattern:
        return False

    if ptype == "literal":
        return command_matches_pattern(cmd, pattern)

    if ptype == "regex":
        try:
            compiled = _compile_pattern(pattern, "regex")
            return bool(compiled.search(cmd))
        except ValueError:
            # Unsafe/invalid regex - fallback to literal (not cached)
            return pattern in cmd

    if ptype == "glob":
        return fnmatch.fnmatch(cmd, pattern)

    return False


def _parse_args(args: str) -> tuple:
    """
    Parse pattern with shlex quoted string support and type detection.

    Args:
        args: Raw argument string

    Returns:
        tuple: (pattern, description, pattern_type)

    Raises:
        ValueError: If pattern is empty or too long
    """
    MAX_PATTERN_LENGTH = 10 * 1024  # 10KB limit

    args = args.strip()
    if not args:
        raise ValueError("No pattern provided")

    if len(args) > MAX_PATTERN_LENGTH:
        raise ValueError(f"Pattern too long (max {MAX_PATTERN_LENGTH})")

    ptype = "literal"

    # Type prefix detection
    if args.startswith("regex:"):
        ptype, args = "regex", args[6:].lstrip()
    elif args.startswith("glob:"):
        ptype, args = "glob", args[5:].lstrip()

    # Shlex parsing for quoted strings
    try:
        parts = shlex.split(args)
    except ValueError:
        parts = args.split(None, 1)

    if not parts:
        raise ValueError("No pattern provided")

    pattern = parts[0]

    # Auto-detect /pattern/ regex
    if ptype == "literal" and pattern.startswith("/") and pattern.endswith("/") and len(pattern) > 2:
        if any(c in pattern[1:-1] for c in r"[]{}()*+?|^$.\ "):
            ptype, pattern = "regex", pattern[1:-1]

    desc = " ".join(parts[1:]) if len(parts) > 1 else None
    return pattern, desc, ptype


def _get_suggestion(pattern: str) -> str:
    """Get suggestion from default integrations or generate default."""
    for k, v in DEFAULT_INTEGRATIONS.items():
        if k in pattern:
            return v["suggestion"]
    return f"Blocked: {pattern}\n\nTo allow: /ar:ok {pattern}"


def _format_pattern_list(patterns: list, label: str, icon: str, show_scope: bool = False) -> list:
    """Format a list of pattern dicts into display lines.

    Args:
        patterns: List of dicts with 'pattern' and optional 'pattern_type' keys.
        label:    Section label, e.g. "Session blocks".
        icon:     Emoji prefix for the header line, e.g. "🚫" or "✅".
        show_scope: If True, show ScopedAllow status_label for allow entries.

    Returns:
        Empty list when patterns is empty; otherwise [header_line, *item_lines].
    """
    if not patterns:
        return []
    lines = [f"{icon} {label} ({len(patterns)}):"]
    for p in patterns:
        ptype = f" ({p.get('pattern_type', 'literal')})" if p.get('pattern_type') != 'literal' else ""
        scope_info = ""
        if show_scope:
            sa = ScopedAllow.from_dict(p)
            scope_info = f" ({sa.status_label()})"
        lines.append(f"  • {p['pattern']}{ptype}{scope_info}")
    return lines


# === DRY SCOPE ACCESSOR (eliminates session vs global duplication) ===
class ScopeAccessor:
    """
    DRY: Unified accessor for session and global state.
    Replaces duplicate if/else blocks with single pattern.
    """

    def __init__(self, ctx: EventContext, scope: str):
        self.ctx = ctx
        self.scope = scope
        self._blocked_key = "session_blocked_patterns" if scope == "session" else "global_blocked_patterns"
        self._allowed_key = "session_allowed_patterns" if scope == "session" else "global_allowed_patterns"

    def get(self) -> list:
        """Get blocked patterns."""
        if self.scope == "session":
            return list(self.ctx.session_blocked_patterns or [])
        with session_state("__global__") as st:
            return list(st.get(self._blocked_key, []))

    def set(self, blocks: list):
        """Set blocked patterns."""
        if self.scope == "session":
            self.ctx.session_blocked_patterns = blocks
        else:
            with session_state("__global__") as st:
                st[self._blocked_key] = blocks

    def get_allowed(self) -> list:
        """Get allowed patterns."""
        if self.scope == "session":
            return list(self.ctx.session_allowed_patterns or [])
        with session_state("__global__") as st:
            return list(st.get(self._allowed_key, []))

    def set_allowed(self, allows: list):
        """Set allowed patterns."""
        if self.scope == "session":
            self.ctx.session_allowed_patterns = allows
        else:
            with session_state("__global__") as st:
                st[self._allowed_key] = allows

    def consume_allowed(self, index: int, consumed_dict: dict) -> None:
        """Atomic update of a single allow entry (safe for global scope)."""
        if self.scope == "session":
            allows = list(self.ctx.session_allowed_patterns or [])
            if index < len(allows):
                allows[index] = consumed_dict
                self.ctx.session_allowed_patterns = allows
        else:
            with session_state("__global__") as st:
                allows = list(st.get(self._allowed_key, []))
                if index < len(allows):
                    allows[index] = consumed_dict
                    st[self._allowed_key] = allows


# MEGA DRY: Single factory for ALL block operations
def _make_block_op(scope: str, op: str):
    """
    Factory: Generate block operation handler.

    Args:
        scope: "session" or "global"
        op: "block", "allow", "clear", or "status"

    Returns:
        Callable: Handler function for the specified scope/operation combination
    """
    def handler(ctx: EventContext) -> str:
        prompt = ctx.activation_prompt or ctx.prompt
        args = prompt.split(maxsplit=1)[1] if " " in prompt else ""

        accessor = ScopeAccessor(ctx, scope)
        blocks = accessor.get()

        if op == "block":
            if not args:
                prefix = "global" if scope == "global" else ""
                return f"❌ Usage: /ar:{prefix}no <pattern> [description]"
            try:
                pattern, desc, ptype = _parse_args(args)
            except ValueError as e:
                return f"❌ Error: {e}"

            # Remove from allows first (so /ar:no is the true inverse of /ar:ok)
            allows = accessor.get_allowed()
            new_allows = [a for a in allows if a["pattern"] != pattern]
            if len(new_allows) != len(allows):
                accessor.set_allowed(new_allows)

            blocks.append({
                "pattern": pattern,
                "suggestion": desc or _get_suggestion(pattern),
                "pattern_type": ptype
            })
            accessor.set(blocks)
            return f"✅ Blocked ({scope}): {pattern}"

        if op == "allow":
            if not args:
                prefix = "global" if scope == "global" else ""
                return f"❌ Usage: /ar:{prefix}ok <pattern> [count] [duration] [permanent|perm|p]"
            try:
                pattern, desc, ptype = _parse_args(args)
            except ValueError as e:
                return f"❌ Error: {e}"
            import time as _time
            ttl, uses, explicit_permanent = parse_scope_args(desc)
            if not explicit_permanent and ttl is None and uses is None:
                uses = 1  # Safe default: 1 use
            sa = ScopedAllow(
                pattern=pattern, pattern_type=ptype,
                granted_at=_time.time(), ttl_seconds=ttl, remaining_uses=uses,
            )
            allows = accessor.get_allowed()
            # Replace existing entry for same pattern (update scope)
            allows = [a for a in allows if a["pattern"] != pattern]
            allows.append(sa.to_dict())
            accessor.set_allowed(allows)
            return f"✅ Allowed: '{pattern}' ({sa.status_label()})"

        if op == "clear":
            accessor.set([])
            accessor.set_allowed([])
            return f"✅ Cleared {scope} blocks and allows"

        if op == "status":
            allows = accessor.get_allowed()
            lines = (
                _format_pattern_list(blocks, f"{scope.title()} Blocks", "🚫") +
                _format_pattern_list(allows, f"{scope.title()} Allows", "✅", show_scope=True)
            )
            return "\n".join(lines) if lines else f"ℹ️ No {scope} blocks or allows"

        return ""

    return handler


# DRY: Data-driven registration
_BLOCK_COMMANDS = [
    ("session", "/ar:no", "block"),
    ("session", "/ar:ok", "allow"),
    ("session", "/ar:clear", "clear"),
    ("session", "/ar:blocks", "status"),
    ("global", "/ar:globalno", "block"),
    ("global", "/ar:globalok", "allow"),
    ("global", "/ar:globalclear", "clear"),
    ("global", "/ar:globalstatus", "status"),
]

for scope, cmd, op in _BLOCK_COMMANDS:
    app.command(cmd)(_make_block_op(scope, op))


@app.command("/ar:reload")
def handle_reload(ctx: EventContext) -> str:
    """Force reload of integration files."""
    invalidate_caches()
    count = len(load_all_integrations())
    return f"✅ Reloaded {count} integrations from Python defaults + user files"


@app.on("PreToolUse")
def check_blocked_commands(ctx: EventContext) -> Optional[Dict]:
    """
    Unified command integrations (superset of hookify).

    Priority:
      TIER 1 — Session allows > Global allows (short-circuit, first match wins)
        - /ar:ok <pattern>       → adds to session_allowed_patterns
        - /ar:globalok <pattern> → adds to global_allowed_patterns
        - Any TIER 1 match immediately returns ctx.allow(), skipping ALL blocks/warns.
        - This is how users override a default deny rule for their current work.

      TIER 2 — ALL of these collected together, deny-wins over warn:
        - Session blocks   (/ar:no <pattern>       → session_blocked_patterns, always deny)
        - Global blocks    (/ar:globalno <pattern>  → global config JSON, always deny)
        - User integration files (hookify *.md)     (action: block or warn)
        - Python defaults  (config.py DEFAULT_INTEGRATIONS, action: block or warn)

      Within TIER 2:
        - ALL matching rules have their messages collected (stacking).
        - Deduplication: same (pattern, decision) pair across tiers shows only once.
        - If ANY deny matches → final decision is deny; all deny msgs first, then warns.
        - If ONLY warns match → final decision is allow with combined warning message.

    Note on /ar:no and DEFAULT_INTEGRATIONS overlap:
      If a user runs /ar:no rm AND the default rm integration also fires, BOTH messages
      appear in the combined deny response. This gives additional context (the user's
      custom reason plus the built-in trash suggestion). To silence the default and show
      ONLY the custom message, the user should also /ar:ok rm to move rm to TIER 1 allows,
      then create a separate session block with a different pattern scope.

    Note on response fields (PreToolUse deny responses):
      - Claude Code deny: reason="" and systemMessage="" (message sent via stderr+exit 2,
        anti-triple-print). The message is in hookSpecificOutput.permissionDecisionReason.
      - Gemini deny:      reason=msg, systemMessage=msg, and permissionDecisionReason=msg.
      - Both CLIs:        hookSpecificOutput.permissionDecisionReason ALWAYS has the message.
      Tests should use hookSpecificOutput.permissionDecisionReason for portable assertions.

    Features: action (block/warn), redirect with {args}, when predicates, conditions
    Supports: Bash commands (event: bash), Write/Edit operations (event: file)
    """
    # Determine event type and command/path
    if ctx.tool_name in BASH_TOOLS:
        event_type = "bash"
        cmd = ctx.tool_input.get("command", "")
    elif ctx.tool_name in FILE_TOOLS:
        event_type = "file"
        cmd = ctx.tool_input.get("file_path", "")
    else:
        return None

    if not cmd:
        return None

    # Centralized prefix check (DRY): Internal autorun commands are always allowed
    if cmd.strip().startswith("/ar:"):
        return ctx.allow()

    # TIER 1: Allows (short-circuit, first match wins — explicit allow overrides everything)
    for scope_name in ("session", "global"):
        accessor = ScopeAccessor(ctx, scope_name)
        allows = accessor.get_allowed()
        for i, a in enumerate(allows):
            sa = ScopedAllow.from_dict(a)
            if not sa.is_valid():
                continue  # Expired/exhausted — skip (lazy cleanup)
            if _match(cmd, sa.pattern, sa.pattern_type):
                consumed = sa.consume()
                accessor.consume_allowed(i, consumed.to_dict())
                label = consumed.status_label()
                if label != "permanent":
                    return ctx.respond("allow", f"Allowed '{sa.pattern}' ({label})")
                return ctx.allow()
        # Lazy cleanup: remove expired entries on pass-through
        cleaned = [a for a in allows if ScopedAllow.from_dict(a).is_valid()]
        if len(cleaned) != len(allows):
            accessor.set_allowed(cleaned)

    # TIER 2: Collect ALL matching blocks + warns (stacking: deny wins over warn)
    deny_parts: list = []
    warn_parts: list = []
    # Dedup by pattern string only (not by decision). This ensures that if the user adds
    # /ar:no git (deny), it suppresses the DEFAULT "git" (warn) integration for the same
    # pattern — user's explicit block replaces the default regardless of action type.
    # Different patterns (e.g. "rm" vs "rm -rf") are distinct and both shown if they match.
    seen: set = set()  # dedup by pattern string — same pattern from ANY tier shows once

    # Session blocks (always deny)
    for b in ScopeAccessor(ctx, "session").get():
        if _match(cmd, b["pattern"], b.get("pattern_type", "literal")):
            key = b["pattern"]
            if key not in seen:
                seen.add(key)
                suggestion = b.get("suggestion", f"Pattern '{b['pattern']}' is blocked")
                deny_parts.append(f"{suggestion}\n\nTo allow: /ar:ok {b['pattern']}")

    # Global blocks (always deny)
    for b in ScopeAccessor(ctx, "global").get():
        if _match(cmd, b["pattern"], b.get("pattern_type", "literal")):
            key = b["pattern"]
            if key not in seen:
                seen.add(key)
                suggestion = b.get("suggestion", f"Pattern '{b['pattern']}' is blocked")
                deny_parts.append(f"{suggestion}\n\nTo allow: /ar:globalok {b['pattern']}")

    # User files + Python defaults (deny or warn per action field)
    try:
        for intg in load_all_integrations():  # O(1) cached
            # Check event type (bash/file/stop/all)
            if intg.event not in ("all", event_type):
                continue

            # Check tool matcher (hookify compat) - expand to all CLI aliases
            if intg.tool_matcher != "*":
                # Expand tool_matcher to include all aliases from the same tool family.
                # e.g., "Bash" expands to {"Bash", "bash_command", "run_shell_command"}
                allowed_tools = set(intg.tool_matcher.split("|"))
                expanded = set()
                for tool in allowed_tools:
                    for family in (BASH_TOOLS, FILE_TOOLS, PLAN_TOOLS):
                        if tool in family:
                            expanded |= family
                            break
                    else:
                        expanded.add(tool)
                if ctx.tool_name not in expanded:
                    continue

            # Check when predicate
            try:
                if not check_when_predicate(intg.when, ctx):
                    continue
            except Exception as e:
                logger.warning(f"When predicate '{intg.when}' failed: {e}")
                continue  # Skip on error

            # Check hookify conditions (AND-ed)
            if intg.conditions:
                try:
                    if not check_conditions(intg.conditions, ctx):
                        continue
                except Exception as e:
                    logger.warning(f"Conditions check failed: {e}")
                    continue

            # Check patterns (OR-ed): first match within this intg adds its message once
            for pattern in intg.patterns:
                try:
                    if command_matches_pattern(cmd, pattern):  # O(1) cached
                        decision = "warn" if intg.action == "warn" else "deny"
                        key = pattern  # dedup by pattern string only (see TIER 2 comment above)
                        if key not in seen:
                            seen.add(key)
                            msg = format_suggestion(intg.message, ctx.cli_type)
                            if intg.redirect:
                                # Substitute {args} with actual args, {file} with target file
                                args = cmd.split(maxsplit=1)[1] if " " in cmd else ""
                                redirect_cmd = intg.redirect.replace("{args}", args)
                                if "{file}" in redirect_cmd:
                                    parts = cmd.split()
                                    file_args = [p for p in parts[2:] if p != "--" and not p.startswith("-")]
                                    file_val = file_args[-1] if file_args else args
                                    redirect_cmd = redirect_cmd.replace("{file}", file_val)
                                msg += f"\n\nUse instead: `{redirect_cmd}`"
                            if decision == "warn":
                                logger.info(f"Integration warning for '{pattern}': {intg.name}")
                                warn_parts.append(msg)
                            else:
                                deny_parts.append(msg)
                        break  # First matching pattern in this intg is enough
                except Exception as e:
                    logger.warning(f"Pattern match error for '{pattern}': {e}")
                    continue
    except Exception as e:
        logger.error(f"Error in check_blocked_commands: {e}")
        # Fail-open: allow command on error (never crash Claude Code)

    # Apply deny-wins: combine all messages, deny takes precedence over warn
    if deny_parts or warn_parts:
        combined = "\n\n".join(p for p in (deny_parts + warn_parts) if p)
        if deny_parts:
            return ctx.deny(combined)
        else:
            return ctx.respond("allow", combined)

    return None


# ============================================================================
# AUTORUN PLUGIN - Commands + Stop Hook Injection
# ============================================================================

def _is_procedural_mode(prompt: str) -> bool:
    """Check if command indicates procedural mode."""
    return any(x in prompt for x in ["/ar:gp", "/ar:proc", "/autoproc"])


@app.command("/ar:go", "/ar:run", "/ar:gp", "/ar:proc", "/autorun", "/autoproc", "activate")
def handle_activate(ctx: EventContext) -> str:
    """Activate autorun with task description."""
    # Bug #10 Fix: Ensure prompt is string to avoid TypeError on None
    prompt = ctx.activation_prompt or ctx.prompt or ""
    task = prompt.split(maxsplit=1)[1] if " " in prompt else ""

    is_procedural = _is_procedural_mode(prompt)

    # Magic state - all persist automatically!
    ctx.autorun_active = True
    ctx.autorun_stage = EventContext.STAGE_1
    ctx.autorun_task = task
    ctx.autorun_mode = "procedural" if is_procedural else "standard"
    ctx.recheck_count = 0
    ctx.hook_call_count = 0

    name, _ = CONFIG["policies"].get(ctx.file_policy, ("allow-all", ""))
    mode_str = "🧠 Procedural (Wait Process)" if is_procedural else "🔄 Stages: 1→2→3"
    return f"✅ Autorun: {task or '(auto)'}\n📁 {name}\n{mode_str}\n⚠️ {CONFIG['emergency_stop']}"


def _deactivate(ctx: EventContext, msg: str) -> str:
    """Shared deactivation logic."""
    ctx.autorun_active = False
    ctx.autorun_stage = EventContext.STAGE_INACTIVE
    stop_external_monitor(ctx)
    return msg


@app.command("/ar:x", "/ar:stop", "/autostop", "stop")
def handle_stop(ctx: EventContext) -> str:
    """Graceful stop."""
    ctx._halt_ai = True  # Signal dispatch to use continue_loop=False
    return _deactivate(ctx, "✅ Stopped")


@app.command("/ar:sos", "/ar:estop", "/estop", "emergency_stop")
def handle_sos(ctx: EventContext) -> str:
    """Emergency stop."""
    ctx._halt_ai = True  # Signal dispatch to use continue_loop=False
    return _deactivate(ctx, f"⚠️ EMERGENCY STOP\n{CONFIG['emergency_stop']}")


# === AUTORUN HELPERS ===

def is_premature_stop(ctx: EventContext) -> bool:
    """Check if this is a premature stop without completion markers.

    NOTE: Task checking is NOT done here -- it's handled by
    prevent_premature_stop() in task_lifecycle.py, which fires earlier
    in the Stop hook chain (first-non-None wins, core.py:1097-1107).
    Adding task checks here would create a circular dependency.
    """
    if not ctx.autorun_active:
        return False

    transcript = ctx.transcript.text
    result = ctx.tool_result or ""
    combined = result + transcript

    return not any(marker in combined for marker in [
        CONFIG["stage1_message"],
        CONFIG["stage2_message"],
        CONFIG["stage3_message"],
        CONFIG.get("completion_marker", ""),
        CONFIG["emergency_stop"]
    ])


def get_stage3_instructions(ctx: EventContext) -> str:
    """Get stage 3 instructions with countdown messaging."""
    if ctx.autorun_stage != EventContext.STAGE_2_COMPLETED:
        return "Complete Stage 1 and Stage 2 before proceeding to Stage 3."

    remaining = CONFIG.get("stage3_countdown_calls", 3) - ctx.hook_call_count
    if remaining > 0:
        return f"After {remaining} more hook calls, Stage 3 instructions will be revealed. Continue with evaluation."

    return f"STAGE 3: {CONFIG['stage3_instruction']}. Output **{CONFIG['stage3_message']}** to complete."


def _build_progressive_stage_section(ctx: EventContext) -> str:
    """
    Build stage section with PROGRESSIVE DISCLOSURE.

    Only reveals the CURRENT stage instruction + completion string.
    This prevents AI from prematurely outputting Stage 2/3 strings.

    Args:
        ctx: Event context with autorun_stage

    Returns:
        str: Stage section showing only current stage
    """
    stage = getattr(ctx, 'autorun_stage', EventContext.STAGE_1)

    if stage in (EventContext.STAGE_INACTIVE, EventContext.STAGE_1):
        # Stage 1: Show only Stage 1
        return f"""5.  **STAGE 1 - INITIAL IMPLEMENTATION:** {CONFIG['stage1_instruction']}
    * When Stage 1 is complete, output **{CONFIG['stage1_message']}** to advance to Stage 2
    * You will receive Stage 2 instructions after outputting this confirmation"""

    elif stage == EventContext.STAGE_2:
        # Stage 2: Show Stage 2 (Stage 1 already revealed)
        return f"""5.  **STAGE 2 - CRITICAL EVALUATION:** {CONFIG['stage2_instruction']}
    * When Stage 2 is complete, output **{CONFIG['stage2_message']}** to advance to Stage 3
    * You will receive Stage 3 instructions after outputting this confirmation"""

    elif stage in (EventContext.STAGE_2_COMPLETED, EventContext.STAGE_3):
        # Stage 3: Show Stage 3 (countdown complete or in Stage 3)
        stage3_instructions = get_stage3_instructions(ctx)
        return f"""5.  **STAGE 3 - FINAL VERIFICATION:** {CONFIG['stage3_instruction']}
    * {stage3_instructions}
    * When Stage 3 is complete, output **{CONFIG['stage3_message']}** for final completion"""

    else:
        # Fallback: Show Stage 1
        return f"""5.  **STAGE 1 - INITIAL IMPLEMENTATION:** {CONFIG['stage1_instruction']}
    * When Stage 1 is complete, output **{CONFIG['stage1_message']}** to advance to Stage 2"""


def build_injection_prompt(ctx: EventContext, use_progressive_disclosure: bool = True) -> str:
    """
    Build injection prompt with optional progressive disclosure.

    Args:
        ctx: Event context
        use_progressive_disclosure: If True, only show current stage (default: True)

    Returns:
        str: Formatted injection prompt
    """
    # Force compliance if over limit
    if ctx.recheck_count > CONFIG["max_recheck_count"]:
        return CONFIG["forced_compliance_template"].format(
            activation_prompt=ctx.autorun_task or "",
            verification_requirements="",
            stage3_message=CONFIG["stage3_message"]
        )

    _, desc = CONFIG["policies"].get(ctx.file_policy, ("", ""))
    template_key = "procedural_injection_template" if ctx.autorun_mode == "procedural" else "injection_template"
    template = CONFIG.get(template_key, CONFIG["injection_template"])

    if use_progressive_disclosure:
        # Build base template (sections 1-4: safety protocol)
        base_template = """Your primary objective is to continue the **UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION** of your current tasks and goals.

**THREE-STAGE COMPLETION SYSTEM:**
This system ensures thorough, high-quality work through a structured three-stage process. You will receive stage instructions progressively.

1.  **MANDATORY PROCESS TO CONTINUE EXECUTION:** Cautiously and deliberately continue working **carefully, patiently, concretely, and safely**, non-stop, autonomously, and non-interactively per your instructions and CLAUDE.md philosophy and definition of concrete.
    * **Permissions Granted:** You have full permission to use all tools (Bash, Edit, Write, etc.) without requiring any further permission prompts.
2.  **SYSTEM STOP SIGNAL RULE:** The exact string **{emergency_stop}** is a **SYSTEM STOP SIGNAL**. You MUST **NEVER** output this string unless catastrophic/irreversible risk exists. Accidental output will immediately halt the entire system.
3.  **Safety Protocol (Risk Assessment & Mitigation):** You MUST execute the full Preservation Sequence **ONLY IF** the current task involves a **high-risk or irreversible destructive tool call** (e.g., initial modification, database interaction, or action following a prior failure).
    * **Assess Risk:** Implicitly evaluate the potential for irreversible state misalignment or system integrity breach.
    * **Mitigation Action (CONDITIONAL):** If a **high-risk condition is met**, you **MUST immediately execute the following Preservation Sequence** and explicitly state your actions:
        1.  **INITIATE SAFETY PROTOCOL:** Announce 'INITIATE SAFETY PROTOCOL' to begin structured assessment.
        2.  **Secure State:** Execute the recovery command(s) to create an **environment backup** or **state checkpoint** (using available systems and tools) *before* proceeding.
        3.  **Verify Integrity:** Run a quick non-destructive check to ensure the state checkpoint was successful.
        4.  **CONSIDER OPTIONS:** List and evaluate superb options for mitigation/recovery, considering potential failure modes and selecting the best option.
    * **CRITICAL ESCAPE PRE-CHECK:** If, after executing the Mitigation Action, the risk remains irreversible, proceed directly to **Step 4: CRITICAL ESCAPE TO STOP SYSTEM**.
4.  **CRITICAL ESCAPE TO STOP SYSTEM (Final Decision):** Only if the risk is irreversible, catastrophic, or cannot be fully mitigated, you **MUST initiate the Preservation Protocol** by immediately outputting the following exact string to immediately halt all actions: **{emergency_stop}**

{progressive_stage_section}

6.  **FILE CREATION POLICY:** {policy_instructions}"""

        progressive_stage_section = _build_progressive_stage_section(ctx)
        return base_template.format(
            emergency_stop=CONFIG["emergency_stop"],
            progressive_stage_section=progressive_stage_section,
            policy_instructions=desc
        )
    else:
        # Original full template showing all three stages
        return template.format(
            emergency_stop=CONFIG["emergency_stop"],
            stage1_message=CONFIG["stage1_message"],
            stage2_message=CONFIG["stage2_message"],
            stage3_message=CONFIG["stage3_message"],
            stage1_instruction=CONFIG["stage1_instruction"],
            stage2_instruction=CONFIG["stage2_instruction"],
            stage3_instruction=CONFIG["stage3_instruction"],
            stage3_instructions=get_stage3_instructions(ctx),
            policy_instructions=desc
        )


# === POSTTOOLUSE HOOK HANDLERS ===


def _get_task_creation_reminder(ctx: EventContext) -> Optional[str]:
    """Return task creation reminder message if flags are set, else None (DRY helper).

    Used by both detect_plan_approval (inline append) and
    remind_until_tasks_created (chain notification stacking).
    """
    if ctx.plan_awaiting_execution_tasks:
        return CONFIG["plan_execution_task_reminder"]
    elif ctx.plan_awaiting_planning_tasks:
        return CONFIG["plan_planning_task_reminder"]
    return None

@app.on("PostToolUse")
def detect_plan_approval(ctx: EventContext) -> Optional[Dict]:
    """Detect plan approval via ExitPlanMode PostToolUse event.

    Merges three concerns into one handler (Fixes 6+7+8):
    - Plan task injection (was lost to first-non-None chain ordering)
    - User notification via systemMessage (PATHWAY 2)
    - Configurable TDD scaffolding injection

    Sources:
    - https://github.com/Piebald-AI/claude-code-system-prompts
    - https://github.com/anthropics/claude-code/issues/9701
    """
    if ctx.tool_name not in PLAN_TOOLS:
        return None

    tool_result = ctx.tool_result or ""
    approval_indicators = ["approved your plan", "can now start coding"]
    if not any(ind in tool_result.lower() for ind in approval_indicators):
        return None

    if ctx.autorun_active:
        # Autorun already running (e.g. re-entering plan mode mid-session).
        # Still set execution task reminder and notify, but don't re-initialize.
        ctx.plan_awaiting_planning_tasks = False
        ctx.plan_awaiting_execution_tasks = True
        reminder = _get_task_creation_reminder(ctx)
        if reminder:
            ctx.add_chain_notification(reminder, channel="ai")
        ctx.add_chain_notification("Plan accepted (autorun already active)", channel="human")
        return None

    # Activate autorun
    original_request = ctx.plan_arguments or "Execute the accepted plan"
    ctx.autorun_active = True
    ctx.autorun_task = original_request
    ctx.autorun_stage = EventContext.STAGE_1
    ctx.autorun_mode = "standard"
    ctx.recheck_count = 0
    ctx.hook_call_count = 0
    ctx.plan_awaiting_planning_tasks = False   # Planning phase done
    ctx.plan_awaiting_execution_tasks = True   # Now need [TDD]/[EXEC] tasks

    injection = build_injection_prompt(ctx)

    # Fix 6: Merge plan task injection (ONE TaskLifecycle instance)
    task_count = 0
    if task_lifecycle.is_enabled():
        try:
            manager = task_lifecycle.TaskLifecycle(ctx=ctx)
            task_injection = manager.get_plan_approval_injection(ctx)
            if task_injection:
                injection += "\n" + task_injection
            plan_key = getattr(ctx, 'plan_arguments', '') or ''
            if plan_key:
                tasks = manager.get_plan_tasks(plan_key, incomplete_only=True)
                task_count = len(tasks) if tasks else 0
        except Exception as e:
            logger.warning(f"Plan task injection error: {e}")

    # Fix 8: Configurable TDD scaffolding
    plan_cfg = task_lifecycle.PlanNotifyConfig.load()
    if plan_cfg.tdd_scaffolding:
        injection += CONFIG.get("tdd_scaffolding_message", "")
    if plan_cfg.task_update_enforcement:
        threshold = ctx.task_staleness_threshold or CONFIG.get("task_staleness_threshold", 25)
        ctx.tool_calls_since_task_update = max(0, threshold - 2)

    # v0.10: Append execution task reminder (DRY helper shared with remind_until_tasks_created)
    reminder = _get_task_creation_reminder(ctx)
    if reminder:
        injection += reminder

    # Fix 7: Chain notifications (PATHWAY 2) — don't stop chain
    user_lines = [f"Plan accepted - {task_count} task(s) linked"]
    if plan_cfg.tdd_scaffolding:
        user_lines.append("  TDD scaffolding: enabled")
    if plan_cfg.task_update_enforcement:
        user_lines.append("  Task update enforcement: enabled")
    user_msg = "\n".join(user_lines)

    ctx.add_chain_notification(user_msg, channel="human")
    ctx.add_chain_notification(injection, channel="ai")
    return None  # Don't stop chain — let all PostToolUse handlers contribute


@app.on("PostToolUse")
def detect_plan_shrinkage(ctx: EventContext) -> Optional[Dict]:
    """Warn if a plan file loses substantial content during Write/Edit.

    For Edit: detects when old_string has many more lines than new_string (content deletion).
    For Write: detects when written content is suspiciously short for a plan file.

    Does NOT block the tool — injects a warning message into AI context.
    Only checks .md files under ~/.claude/plans/.
    """
    if ctx.tool_name not in ("Write", "Edit"):
        return None

    file_path = ctx.tool_input.get("file_path", "")
    if not (file_path.endswith(".md") and "plans" in file_path):
        return None

    if ctx.tool_name == "Edit":
        old_str = ctx.tool_input.get("old_string", "")
        new_str = ctx.tool_input.get("new_string", "")
        old_lines = len(old_str.splitlines())
        new_lines = len(new_str.splitlines())
        # Warn if: replacement removed >5 lines AND shrank by >60%
        if old_lines > 5 and new_lines < old_lines * 0.4:
            removed = old_lines - new_lines
            ctx.add_chain_notification(
                f"\n⚠️ PLAN CONTENT WARNING: Edit removed {removed} lines from "
                f"{file_path} (old_string was {old_lines} lines, new_string is {new_lines} lines). "
                f"Read the full plan file NOW and verify no content was accidentally deleted. "
                f"Restore any missing content before continuing.",
                channel="ai"
            )

    elif ctx.tool_name == "Write":
        content = ctx.tool_input.get("content", "")
        content_lines = len(content.splitlines())
        if content_lines < 15:
            ctx.add_chain_notification(
                f"\n⚠️ PLAN CONTENT WARNING: Write produced only {content_lines} lines "
                f"in {file_path}. Plan files should have substantial content. "
                f"Verify this was intentional and no content was accidentally deleted.",
                channel="ai"
            )

    return None


# === TASK STALENESS REMINDER (v0.9) ===

@app.on("PostToolUse")
def check_task_staleness(ctx: EventContext) -> Optional[Dict]:
    """Inject reminder when AI hasn't updated tasks recently (v0.9).

    Counts tool calls per session. Resets on TaskCreate/TaskUpdate.
    Injects reminder when count reaches threshold AND incomplete tasks exist.

    Fires in any session (not just autorun). Disable with /ar:tasks off.
    """
    if not ctx.task_staleness_enabled:
        return None

    # Reset counter when AI actively manages tasks; skip increment.
    if ctx.tool_name in (TASK_CREATE_TOOLS | TASK_UPDATE_TOOLS):
        ctx.tool_calls_since_task_update = 0
        return None

    count = (ctx.tool_calls_since_task_update or 0) + 1
    ctx.tool_calls_since_task_update = count

    threshold = ctx.task_staleness_threshold or CONFIG.get("task_staleness_threshold", 25)
    if count < threshold:
        return None

    # Only fire reminder if there are actually incomplete tasks
    if task_lifecycle.is_enabled():
        try:
            manager = task_lifecycle.TaskLifecycle(ctx=ctx)
            if not manager.get_incomplete_tasks(exclude_blocking=True):
                ctx.tool_calls_since_task_update = 0
                return None  # No incomplete tasks — nothing to remind about
        except Exception:
            pass  # Fail-open — skip reminder on error

    ctx.tool_calls_since_task_update = 0
    msg = CONFIG["task_staleness_message"].format(threshold=threshold)
    ctx.add_chain_notification(msg, channel="ai")
    return None


# === TASK CREATION REMINDER (v0.10) ===

@app.on("PostToolUse")
def remind_until_tasks_created(ctx: EventContext) -> Optional[Dict]:
    """Inject reminder until AI creates tasks after plan start or acceptance (v0.10).

    Two independent flags (set by plan commands and detect_plan_approval):
    - plan_awaiting_planning_tasks: cleared on first TaskCreate
    - plan_awaiting_execution_tasks: cleared on first TaskCreate

    Fires on EVERY PostToolUse until TaskCreate is detected.
    """
    awaiting_planning = ctx.plan_awaiting_planning_tasks
    awaiting_execution = ctx.plan_awaiting_execution_tasks

    if not awaiting_planning and not awaiting_execution:
        return None

    # TaskCreate clears the active flag
    if ctx.tool_name in TASK_CREATE_TOOLS:
        if awaiting_planning:
            ctx.plan_awaiting_planning_tasks = False
        if awaiting_execution:
            ctx.plan_awaiting_execution_tasks = False
        return None

    # DRY helper selects message based on flags (execution priority over planning)
    msg = _get_task_creation_reminder(ctx)
    if msg:
        ctx.add_chain_notification(msg, channel="ai")
    return None


@app.command("/ar:tasks")
def toggle_task_staleness(ctx: EventContext) -> str:
    """Toggle task staleness reminder on/off or set threshold.

    Usage:
      /ar:tasks          — show status (enabled/disabled, count, threshold)
      /ar:tasks on       — enable reminders
      /ar:tasks off      — disable reminders
      /ar:tasks <number> — set threshold (e.g. /ar:tasks 10)
    """
    parts = (ctx.activation_prompt or ctx.prompt or "").split()
    arg = parts[1].strip().lower() if len(parts) > 1 else ""

    if arg == "on":
        ctx.task_staleness_enabled = True
        ctx.tool_calls_since_task_update = 0
        threshold = ctx.task_staleness_threshold or CONFIG.get("task_staleness_threshold", 25)
        return f"Task staleness reminders enabled (threshold: {threshold} tool calls)."
    elif arg == "off":
        ctx.task_staleness_enabled = False
        return "Task staleness reminders disabled."
    elif arg.isdigit() and int(arg) >= 1:
        ctx.task_staleness_threshold = int(arg)
        ctx.tool_calls_since_task_update = 0
        return f"Task staleness threshold set to {arg} tool calls."
    elif arg:
        # Catches: "0", negative numbers like "-5", non-numeric strings
        return f"Invalid threshold '{arg}'. Use a positive integer (e.g. /ar:tasks 10)."
    else:
        enabled = ctx.task_staleness_enabled
        count = ctx.tool_calls_since_task_update or 0
        threshold = ctx.task_staleness_threshold or CONFIG.get("task_staleness_threshold", 25)

        lines = []

        # Show actual task summary if task lifecycle is enabled
        if task_lifecycle.is_enabled():
            try:
                manager = task_lifecycle.TaskLifecycle(ctx=ctx)
                tasks = manager.tasks
                if tasks:
                    by_status = {}
                    for t in tasks.values():
                        s = t["status"]
                        by_status[s] = by_status.get(s, 0) + 1

                    status_icons = {
                        "completed": "done", "in_progress": "active",
                        "pending": "pending", "paused": "paused",
                        "deleted": "deleted", "ignored": "ignored",
                    }
                    status_parts = []
                    for s in ["in_progress", "pending", "completed", "paused", "deleted", "ignored"]:
                        c = by_status.get(s, 0)
                        if c > 0:
                            status_parts.append(f"{c} {status_icons.get(s, s)}")

                    lines.append(f"Tasks: {len(tasks)} total ({', '.join(status_parts)})")

                    incomplete = manager.get_incomplete_tasks(exclude_blocking=True)
                    if incomplete:
                        for t in incomplete[:5]:
                            tid = t["id"]
                            subj = t["subject"]
                            st = t["status"]
                            icon = {"in_progress": ">>", "pending": ".."}.get(st, "??")
                            lines.append(f"  {icon} #{tid}: {subj} ({st})")
                        if len(incomplete) > 5:
                            lines.append(f"  ... and {len(incomplete) - 5} more (/ar:task-status for full list)")
                    else:
                        lines.append("  All tasks completed!")
                else:
                    lines.append("Tasks: none tracked")
            except Exception:
                lines.append("Tasks: unavailable (lifecycle error)")

        lines.append(f"Staleness reminders: {'on' if enabled else 'off'} ({count}/{threshold} tool calls)")
        lines.append("Usage: /ar:tasks on|off|<number> | /ar:task-status for full details")
        return "\n".join(lines)


# === STOP HOOK HANDLERS ===

@app.on("Stop")
def autorun_injection(ctx: EventContext) -> Optional[Dict]:
    """
    Sophisticated three-stage autorun with alternating recovery behavior.

    Stage Flow:
    1. STAGE_1 -> stage1_message -> STAGE_2
    2. STAGE_2 -> stage2_message -> STAGE_2_COMPLETED
    3. STAGE_2_COMPLETED -> countdown -> stage3_message -> Complete
    """
    if not ctx.autorun_active:
        return None

    result = ctx.tool_result or ""
    transcript = ctx.transcript.text
    combined = result + transcript
    stage = ctx.autorun_stage

    # === EMERGENCY STOP CHECK (Bug #6 Fix) ===
    # CRITICAL: Emergency stop must immediately halt autorun
    # Without this check, emergency_stop string is recognized by is_premature_stop()
    # but doesn't actually stop the system - it falls through to fallback injection.
    if CONFIG["emergency_stop"] in combined:
        ctx.autorun_active = False
        ctx.autorun_stage = EventContext.STAGE_INACTIVE
        stop_external_monitor(ctx)
        return None  # Allow Claude to stop - emergency protocol triggered

    # Track hook calls for countdown
    ctx.hook_call_count = (ctx.hook_call_count or 0) + 1

    # Helper: Return via injection method
    def inject(msg: str) -> Optional[Dict]:
        return ctx.block(msg) if get_injection_method(ctx) == "HOOK_INTEGRATION" else None

    # === STAGE 1: Initial work ===
    if stage == EventContext.STAGE_1:
        if CONFIG["stage1_message"] in combined:
            ctx.autorun_stage = EventContext.STAGE_2
            msg = f"STAGE 2: {CONFIG['stage2_instruction']}. Output **{CONFIG['stage2_message']}** when complete."
            return inject(msg)

        # Bug #7 Fix: Warn about premature stage2_message
        if CONFIG["stage2_message"] in combined:
            return inject(f"Complete Stage 1 first. Output **{CONFIG['stage1_message']}** when Stage 1 is done.")

        if CONFIG["stage3_message"] in combined:
            return inject(f"Complete Stage 1 first. Output **{CONFIG['stage1_message']}** when done.")

        if is_premature_stop(ctx):
            ctx.recheck_count = (ctx.recheck_count or 0) + 1
            return inject(build_injection_prompt(ctx))

    # === STAGE 2: Critical evaluation ===
    elif stage == EventContext.STAGE_2:
        if CONFIG["stage2_message"] in combined:
            ctx.autorun_stage = EventContext.STAGE_2_COMPLETED
            ctx.hook_call_count = -1  # Bug #8 Fix: Reset to -1 so next increment makes it 0
            remaining = CONFIG.get("stage3_countdown_calls", 3)
            return inject(f"Stage 2 complete. Continue for {remaining} more cycles before Stage 3.")

        # Bug #7 Fix: Warn about regression to stage1_message
        if CONFIG["stage1_message"] in combined:
            return inject(f"Already in Stage 2. Continue with critical evaluation. Output **{CONFIG['stage2_message']}** when complete.")

        if CONFIG["stage3_message"] in combined:
            return inject(f"Complete Stage 2 first. Output **{CONFIG['stage2_message']}** when done.")

        if is_premature_stop(ctx):
            return inject(f"Continue Stage 2: {CONFIG['stage2_instruction']}. Output **{CONFIG['stage2_message']}** when complete.")

    # === STAGE 2 COMPLETED: Countdown to stage 3 ===
    elif stage == EventContext.STAGE_2_COMPLETED:
        countdown_max = CONFIG.get("stage3_countdown_calls", 3)
        remaining = countdown_max - ctx.hook_call_count

        if CONFIG["stage3_message"] in combined:
            if remaining > 0:
                ctx.autorun_stage = EventContext.STAGE_2
                return inject(f"Too early for Stage 3. Continue Stage 2: {CONFIG['stage2_instruction']}")
            else:
                ctx.autorun_active = False
                ctx.autorun_stage = EventContext.STAGE_INACTIVE
                stop_external_monitor(ctx)
                return None  # Allow Claude to stop

        if remaining > 0:
            if ctx.hook_call_count % 2 == 0:
                return inject(f"Stage 3 countdown: {remaining} calls remaining. Continue evaluation.")
            else:
                ctx.recheck_count = (ctx.recheck_count or 0) + 1
                return inject(build_injection_prompt(ctx))
        else:
            return inject(get_stage3_instructions(ctx))

    # Fallback
    ctx.recheck_count = (ctx.recheck_count or 0) + 1
    return inject(build_injection_prompt(ctx))


# === SCOPED ALLOW CLEANUP (SessionStart) ===

@app.on("SessionStart")
def cleanup_expired_allows(ctx: EventContext) -> None:
    """Purge expired scoped allows on session start (lazy GC)."""
    for scope_name in ("session", "global"):
        accessor = ScopeAccessor(ctx, scope_name)
        allows = accessor.get_allowed()
        cleaned = [a for a in allows if ScopedAllow.from_dict(a).is_valid()]
        if len(cleaned) != len(allows):
            accessor.set_allowed(cleaned)
    return None


# ============================================================================
# PLAN MANAGEMENT PLUGIN
# ============================================================================

def _make_plan_handler(md_filename: str):
    """
    Factory: Generate plan command handler that reads and returns markdown content.

    This provides a Python-level workaround for symlink discovery issues.
    Claude Code doesn't always discover symlinked commands properly, so we
    create explicit handlers that read the markdown files.

    Args:
        md_filename: Name of markdown file in commands/ directory (e.g., "plannew.md")

    Returns:
        Callable: Handler function that returns markdown file content
    """
    def handler(ctx: EventContext) -> str:
        from pathlib import Path
        commands_dir = Path(__file__).parent.parent.parent / "commands"
        md_path = commands_dir / md_filename

        # Set plan_active and task creation nag for all plan commands
        ctx.plan_active = True
        ctx.plan_awaiting_planning_tasks = True

        if not md_path.exists():
            return f"❌ Error: Plan command file not found: {md_filename}"

        try:
            return md_path.read_text(encoding='utf-8')
        except Exception as e:
            return f"❌ Error reading plan command: {e}"

    return handler


# Data-driven registration: symlink aliases for plan commands
_PLAN_ALIASES = {
    "NEW_PLAN":     ("/ar:pn", "/ar:plannew", "plannew.md"),
    "REFINE_PLAN":  ("/ar:pr", "/ar:planrefine", "planrefine.md"),
    "UPDATE_PLAN":  ("/ar:pu", "/ar:planupdate", "planupdate.md"),
    "PROCESS_PLAN": ("/ar:pp", "/ar:planprocess", "planprocess.md"),
}

for plan_type, (short_cmd, long_cmd, md_file) in _PLAN_ALIASES.items():
    app.command(short_cmd, long_cmd, plan_type)(_make_plan_handler(md_file))


# ============================================================================
# AI MONITOR INTEGRATION (External Tmux Observer)
# ============================================================================

# Import ai_monitor with fallback
try:
    from . import ai_monitor
except ImportError:
    ai_monitor = None


def _manage_monitor(ctx: EventContext, action: str) -> Optional[int]:
    """
    Centralized helper for ai-monitor process management.

    Args:
        ctx: EventContext with session state
        action: "start" or "stop"

    Returns:
        int | None: PID of started monitor, or None
    """
    if ai_monitor is None:
        return None

    session_id = ctx.session_id

    if action == 'start':
        if ctx.ai_monitor_pid:
            ai_monitor.stop_monitor(session_id)

        pid = ai_monitor.start_monitor(
            session_id=session_id,
            prompt="continue working",
            stop_marker=CONFIG["stage3_message"],
            max_cycles=20,
            prompt_on_start=True
        )
        ctx.ai_monitor_pid = pid
        return pid

    elif action == 'stop':
        if ctx.ai_monitor_pid:
            ai_monitor.stop_monitor(session_id)
            ctx.ai_monitor_pid = None

    return None


def start_external_monitor(ctx: EventContext) -> Optional[int]:
    """Start AI monitor for external tmux-based observation."""
    return _manage_monitor(ctx, 'start')


def stop_external_monitor(ctx: EventContext):
    """Stop AI monitor subprocess if running."""
    _manage_monitor(ctx, 'stop')


def get_injection_method(ctx: EventContext) -> str:
    """
    Determine injection method based on ai_monitor state.

    Returns:
    - "TMUX_INJECTION": ai_monitor subprocess will send keys via tmux
    - "HOOK_INTEGRATION": daemon hook will return injection prompt
    """
    if ai_monitor and ctx.ai_monitor_pid:
        return "TMUX_INJECTION"
    return "HOOK_INTEGRATION"
