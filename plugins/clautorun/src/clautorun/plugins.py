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
Clautorun v0.7 Plugins - Magic State + DRY Factories

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

from .core import app, EventContext, logger
from .config import CONFIG, DEFAULT_INTEGRATIONS
from .session_manager import session_state
from .command_detection import command_matches_pattern
from .integrations import load_all_integrations, invalidate_caches, check_when_predicate, check_conditions

# Import plan_export to register its @app.on() handlers with daemon
from . import plan_export  # noqa: F401


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
    "ALLOW":   ("/cr:a", "/cr:allow", "/afa"),
    "JUSTIFY": ("/cr:j", "/cr:justify", "/afj"),
    "SEARCH":  ("/cr:f", "/cr:find", "/afs"),
}

for policy, aliases in _POLICY_ALIASES.items():
    app.command(*aliases, policy)(_make_policy_handler(policy))


@app.command("/cr:st", "/cr:status", "/afst", "STATUS")
def handle_status(ctx: EventContext) -> str:
    """
    Unified status: Shows file policy + session/global blocks.
    """
    lines = []

    # File policy status
    current_policy = ctx.file_policy
    name, desc = CONFIG["policies"].get(current_policy, ("unknown", ""))
    lines.append(f"✅ AutoFile policy: {name}\n\n{desc}\n")

    # Block status (session + global unified view)
    session_blocks = ScopeAccessor(ctx, "session").get()
    global_blocks = ScopeAccessor(ctx, "global").get()

    if session_blocks or global_blocks:
        lines.append("\n📊 Command Blocking Status\n")
        if session_blocks:
            lines.append(f"Session blocks ({len(session_blocks)}):")
            for b in session_blocks:
                ptype = f" ({b.get('pattern_type', 'literal')})" if b.get('pattern_type') != 'literal' else ""
                lines.append(f"  • {b['pattern']}{ptype}")
        if global_blocks:
            lines.append(f"\nGlobal blocks ({len(global_blocks)}):")
            for b in global_blocks:
                ptype = f" ({b.get('pattern_type', 'literal')})" if b.get('pattern_type') != 'literal' else ""
                lines.append(f"  • {b['pattern']}{ptype}")

    return "\n".join(lines)


@app.on("PreToolUse")
def enforce_file_policy(ctx: EventContext) -> Optional[Dict]:
    """Enforce file creation policy on Write tool."""
    if ctx.tool_name != "Write":
        return None

    policy = ctx.file_policy  # Magic: auto-loads from Shelve!

    if policy == "ALLOW":
        return None

    if policy == "SEARCH":
        if ctx.file_exists:
            return None
        return ctx.deny(CONFIG["policy_blocked"]["SEARCH"])

    if policy == "JUSTIFY":
        if ctx.file_exists or ctx.has_justification:
            return None
        return ctx.deny(CONFIG["policy_blocked"]["JUSTIFY"])

    return None


@app.on("PreToolUse")
def gate_exit_plan_mode(ctx: EventContext) -> Optional[Dict]:
    """Only allow ExitPlanMode after planning Stage 3 is complete (when autorun active)."""
    if ctx.tool_name != "ExitPlanMode":
        return None

    # REGRESSION PROTECTION: Only gate when autorun is active
    # If autorun NOT active (normal /cr:plannew without /cr:go), allow ExitPlanMode as before
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
    return f"Blocked: {pattern}\n\nTo allow: /cr:ok {pattern}"


# === DRY SCOPE ACCESSOR (eliminates session vs global duplication) ===
class ScopeAccessor:
    """
    DRY: Unified accessor for session and global state.
    Replaces duplicate if/else blocks with single pattern.
    """

    def __init__(self, ctx: EventContext, scope: str):
        self.ctx = ctx
        self.scope = scope
        self._key = "session_blocked_patterns" if scope == "session" else "global_blocked_patterns"

    def get(self) -> list:
        if self.scope == "session":
            return list(self.ctx.session_blocked_patterns or [])
        with session_state("__global__") as st:
            return list(st.get(self._key, []))

    def set(self, blocks: list):
        if self.scope == "session":
            self.ctx.session_blocked_patterns = blocks
        else:
            with session_state("__global__") as st:
                st[self._key] = blocks


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
                return f"❌ Usage: /cr:{prefix}no <pattern> [description]"
            try:
                pattern, desc, ptype = _parse_args(args)
            except ValueError as e:
                return f"❌ Error: {e}"

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
                return f"❌ Usage: /cr:{prefix}ok <pattern>"
            before = len(blocks)
            blocks = [b for b in blocks if b["pattern"] != args.strip()]
            accessor.set(blocks)
            return f"✅ Allowed: {args}" if len(blocks) < before else f"⚠️ Not found: {args}"

        if op == "clear":
            accessor.set([])
            return f"✅ Cleared {scope} blocks"

        if op == "status":
            if not blocks:
                return f"ℹ️ No {scope} blocks"
            lines = [f"📋 {scope.title()} Blocks:"]
            for b in blocks:
                ptype = f" ({b.get('pattern_type', 'literal')})" if b.get('pattern_type') != 'literal' else ""
                lines.append(f"  • {b['pattern']}{ptype}")
            return "\n".join(lines)

        return ""

    return handler


# DRY: Data-driven registration
_BLOCK_COMMANDS = [
    ("session", "/cr:no", "block"),
    ("session", "/cr:ok", "allow"),
    ("session", "/cr:clear", "clear"),
    ("session", "/cr:blocks", "status"),
    ("global", "/cr:globalno", "block"),
    ("global", "/cr:globalok", "allow"),
    ("global", "/cr:globalclear", "clear"),
    ("global", "/cr:globalstatus", "status"),
]

for scope, cmd, op in _BLOCK_COMMANDS:
    app.command(cmd)(_make_block_op(scope, op))


@app.command("/cr:reload")
def handle_reload(ctx: EventContext) -> str:
    """Force reload of integration files."""
    invalidate_caches()
    count = len(load_all_integrations())
    return f"✅ Reloaded {count} integrations from Python defaults + user files"


@app.on("PreToolUse")
def check_blocked_commands(ctx: EventContext) -> Optional[Dict]:
    """
    Unified command integrations (superset of hookify).

    Priority: Session blocks > Global blocks > User files > Python defaults
    Features: action (block/warn), redirect with {args}, when predicates, conditions
    Supports: Bash commands (event: bash), Write/Edit operations (event: file)
    """
    # Determine event type and command/path
    if ctx.tool_name == "Bash":
        event_type = "bash"
        cmd = ctx.tool_input.get("command", "")
    elif ctx.tool_name in ("Write", "Edit"):
        event_type = "file"
        cmd = ctx.tool_input.get("file_path", "")
    else:
        return None

    if not cmd:
        return None

    # Check session blocks first (highest priority)
    for b in ScopeAccessor(ctx, "session").get():
        if _match(cmd, b["pattern"], b.get("pattern_type", "literal")):
            return ctx.deny(f"{b['suggestion']}\n\nTo allow: /cr:ok {b['pattern']}")

    # Then global blocks
    for b in ScopeAccessor(ctx, "global").get():
        if _match(cmd, b["pattern"], b.get("pattern_type", "literal")):
            return ctx.deny(f"{b['suggestion']}\n\nTo allow: /cr:globalok {b['pattern']}")

    # User files + Python defaults (sorted by specificity) - FIX Bug 2
    try:
        for intg in load_all_integrations():  # O(1) cached
            # Check event type (bash/file/stop/all)
            if intg.event not in ("all", event_type):
                continue

            # Check tool matcher (hookify compat)
            if intg.tool_matcher != "*":
                allowed_tools = intg.tool_matcher.split("|")
                if ctx.tool_name not in allowed_tools:
                    continue

            # Check when predicate - FIX Bug 1
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

            # Check patterns (OR-ed)
            for pattern in intg.patterns:
                try:
                    if command_matches_pattern(cmd, pattern):  # O(1) cached
                        # Build message (add redirect if present)
                        msg = intg.message
                        if intg.redirect:
                            # Substitute {args} with actual args
                            args = cmd.split(maxsplit=1)[1] if " " in cmd else ""
                            redirect_cmd = intg.redirect.replace("{args}", args)
                            msg += f"\n\nUse instead: `{redirect_cmd}`"

                        # Apply action (warn = allow + message, block = deny)
                        if intg.action == "warn":
                            # Log warning to AI but allow command
                            logger.info(f"Integration warning for '{pattern}': {intg.name}")
                            return ctx.respond("allow", msg)
                        else:
                            # Block command
                            return ctx.deny(msg)
                except Exception as e:
                    logger.warning(f"Pattern match error for '{pattern}': {e}")
                    continue
    except Exception as e:
        logger.error(f"Error in check_blocked_commands: {e}")
        # Fail-open: allow command on error (never crash Claude Code)

    return None


# ============================================================================
# AUTORUN PLUGIN - Commands + Stop Hook Injection
# ============================================================================

def _is_procedural_mode(prompt: str) -> bool:
    """Check if command indicates procedural mode."""
    return any(x in prompt for x in ["/cr:gp", "/cr:proc", "/autoproc"])


@app.command("/cr:go", "/cr:run", "/cr:gp", "/cr:proc", "/autorun", "/autoproc", "activate")
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


@app.command("/cr:x", "/cr:stop", "/autostop", "stop")
def handle_stop(ctx: EventContext) -> str:
    """Graceful stop."""
    return _deactivate(ctx, "✅ Stopped")


@app.command("/cr:sos", "/cr:estop", "/estop", "emergency_stop")
def handle_sos(ctx: EventContext) -> str:
    """Emergency stop."""
    return _deactivate(ctx, f"⚠️ EMERGENCY STOP\n{CONFIG['emergency_stop']}")


# === AUTORUN HELPERS ===

def is_premature_stop(ctx: EventContext) -> bool:
    """Check if this is a premature stop without completion markers."""
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

@app.on("PostToolUse")
def detect_plan_approval(ctx: EventContext) -> Optional[Dict]:
    """Detect plan approval via ExitPlanMode tool PostToolUse event.

    ExitPlanMode tool result contains approval message like:
    "User has approved your plan. You can now start coding..."

    This is more reliable than text matching for "PLAN ACCEPTED" because:
    1. Hooks into the actual approval mechanism (ExitPlanMode tool)
    2. Tool result confirms user interaction occurred
    3. Avoids false positives from text in discussion

    Sources:
    - https://github.com/Piebald-AI/claude-code-system-prompts
    - https://github.com/anthropics/claude-code/issues/9701
    """
    # Only handle ExitPlanMode tool
    if ctx.tool_name != "ExitPlanMode":
        return None

    # Skip if already in autorun
    if ctx.autorun_active:
        return None

    # Check tool result for approval indicators
    tool_result = ctx.tool_result or ""

    # ExitPlanMode returns "User has approved your plan..." on success
    approval_indicators = ["approved your plan", "can now start coding"]
    if not any(ind in tool_result.lower() for ind in approval_indicators):
        return None  # Not approved or rejected

    # User approved - activate autorun with preserved context
    original_request = ctx.plan_arguments or "Execute the accepted plan"

    # Same state setup as existing autorun_plan_acceptance()
    ctx.autorun_active = True
    ctx.autorun_task = original_request
    ctx.autorun_stage = EventContext.STAGE_1
    ctx.autorun_mode = "standard"
    ctx.recheck_count = 0
    ctx.hook_call_count = 0

    injection = build_injection_prompt(ctx)
    return ctx.allow(injection)  # FIX: allow, not block - AI continues with injected instructions


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

        if not md_path.exists():
            return f"❌ Error: Plan command file not found: {md_filename}"

        try:
            return md_path.read_text(encoding='utf-8')
        except Exception as e:
            return f"❌ Error reading plan command: {e}"

    return handler


# Data-driven registration: symlink aliases for plan commands
_PLAN_ALIASES = {
    "NEW_PLAN":     ("/cr:pn", "/cr:plannew", "plannew.md"),
    "REFINE_PLAN":  ("/cr:pr", "/cr:planrefine", "planrefine.md"),
    "UPDATE_PLAN":  ("/cr:pu", "/cr:planupdate", "planupdate.md"),
    "PROCESS_PLAN": ("/cr:pp", "/cr:planprocess", "planprocess.md"),
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
