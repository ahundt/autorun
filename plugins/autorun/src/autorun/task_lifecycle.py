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

"""Task lifecycle tracking for AI continuation - DRY implementation.

**PRIMARY GOAL**: Ensure AI continues working while tasks are outstanding.

REUSES from autorun:
- session_state() for thread-safe persistence (plan_export.py pattern)
- logger for warnings (core.py)
- @dataclass config pattern (plan_export.py:348-385)

ISOLATED from other plugins:
- Uses own global key: "__task_lifecycle__{session_id}"
- Own config file: ~/.autorun/task-lifecycle.config.json
- Own audit logs: ~/.autorun/task-tracking/{session_id}/audit.log

Architecture:
- Dict-based storage: {task_id: TaskState} prevents duplicates
- Per-session isolation: Each AI session tracks own tasks
- Class-based design: Follows PlanExport pattern for consistency
- Thread-safe: filelock locks via session_state(), atomic operations
- DRY: Reuses session_manager.py patterns, no custom persistence code
"""

from typing import Any, Optional, Dict, List, Callable, Literal
from pathlib import Path
from dataclasses import dataclass, asdict
import contextlib
import copy
import hashlib
import json
import math
import time
import re
import uuid
from collections.abc import Iterable
from datetime import datetime
from functools import cache

from . import ipc
from .core import (
    AI_ECHO_CHANNEL,
    EventContext,
    format_command_for_cli,
    logger,
    record_conversation_fork,
    resolve_prompt_author,
)
from .session_manager import (
    DEFAULT_SESSION_TIMEOUT,
    MISSING,
    TASK_DELEGATION_RECEIPT_SESSION,
    TASK_DELEGATION_RECEIPT_SESSION_PREFIX,
    TASK_LIFECYCLE_SESSION_PREFIX,
    get_session_manager,
    session_state,
    state_lock_timeout,
)
from .config import (
    CONFIG,
    LOG_SNIPPET_MAX_LEN,
    detect_cli_type,
    TASK_PAUSE_DEFAULT_TTL_SECONDS,
)
from .platforms import (
    TASK_UPDATE_CAPABILITY_ROLES,
    SessionIdentityResolutionError,
    agent_spawn_tools_for,
    platform_for,
    registered_task_tools,
    resolve_standalone_session_identity,
    task_enforcement_capability_available,
    task_tool_role,
    writes_whole_task_list,
)
from .scoped_allow import fingerprint_call
from .task_pause import (
    task_pause_allows_stop,
    task_pause_guidance,
)
from .task_status import (
    AUTORUN_OWNED_TASK_STATUSES,
    AWAITING_USER_STATUS,
    BLOCKING_TASK_STATUSES,
    COMPLETED_TASK_STATUSES,
    FINISHED_TASK_STATUSES,
    NON_BLOCKING_TASK_STATUSES,
    PRUNABLE_TASK_STATUSES,
    SETTLED_TASK_STATUSES,
    STATUS_POLICY,
    WAITING_TASK_STATUSES,
    task_status_is_unfinished,
    task_status_policy,
)


_SECONDS_PER_DAY: int = 24 * 3600  # pure unit conversion (not a config value)
_STAGE3_OVERFLOW_NAME_COUNT: int = 3  # task names shown in stage-3 overflow message
_MAX_CONSUMED_DELEGATION_MARKERS: int = 256
_SESSION_START_CLAIM_DIGEST_HEX_CHARS: int = 16
_DELEGATION_RECEIPTS_FIELD = "receipts"
_DELEGATION_RECEIPTS_KEYED_MIGRATION = "keyed_migration_complete"
_MAX_RESOLVED_DELEGATION_RECEIPTS = 256
_MAX_AWAITING_USER_DECISION_CHARS = 300
# Session metadata: ids of tasks waiting on the user, so a prompt restores
# them without reading every task row. The task row is the truth; an id whose
# row moved on is dropped when the user next writes.
_AWAITING_USER_INDEX = "awaiting_user_task_ids"
# Volatile flag set by a blocked Stop: the next main-agent tool call restarts
# the loop guard's count (plugins.reset_stop_loop_guard_on_tool_activity).
STOP_LOOP_GUARD_ARMED = "stop_loop_guard_armed"


def task_progress_state_session_id(ctx: EventContext) -> str:
    """Scope enforcement cadence to one agent while tasks remain session-wide."""
    if not ctx.agent_id:
        return ctx.session_id
    return f"{ctx.session_id}:task-staleness-agent:{ctx.agent_id}"


def task_progress_state_get(ctx: EventContext, name: str, default=None):
    return ctx.state_get(name, default, session_id=task_progress_state_session_id(ctx))


def task_progress_state_set(ctx: EventContext, name: str, value) -> None:
    ctx.state_set(name, value, session_id=task_progress_state_session_id(ctx))


def clear_task_staleness_enforcement(ctx: EventContext) -> None:
    """Clear the one-shot staleness deny flag, writing only when it is set.

    Many paths reset it on every tool call. An unconditional reset was a
    durable write transaction per call, serialised behind every session's.
    The check reads the same cached value enforce_task_staleness reads, so
    the two never disagree about whether the flag is armed.
    """
    if task_progress_state_get(ctx, "task_staleness_enforce_next", False):
        task_progress_state_set(ctx, "task_staleness_enforce_next", False)


def task_progress_state_update(ctx: EventContext, name: str, updater, default=None):
    return ctx.state_update_volatile(
        name,
        updater,
        default,
        session_id=task_progress_state_session_id(ctx),
    )


def task_progress_state_update_durable(
    ctx: EventContext,
    name: str,
    updater,
    default=None,
):
    return ctx.state_update(
        name,
        updater,
        default,
        session_id=task_progress_state_session_id(ctx),
    )


def _safe_path_component(value: str) -> str:
    """Keep a logical session ID usable as one audit-log directory name.

    Session IDs are protocol data and may contain ``:`` or ``/``. Those are
    valid identity characters but are separators or reserved characters on
    Windows, so only the filesystem presentation is normalized; the original
    ID remains in state, events, and responses.
    """
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" .")
    return cleaned or "_"


# === Stop / Resume action fragments (assembled at call site) ===
_ACT_REVIEW = "{task_list}"
_ACT_COMPLETE = '{task_update}({task_id_param}="X", status="completed")'
_ACT_DELEGATE = '{task_update}({task_id_param}="X", status="delegated")'
_ACT_DISCARD = '{task_update}({task_id_param}="X", status="deleted")'
# AI-callable stale-task clear, offered on every Stop block. It works from the
# first block: the fact it asserts is that the harness's own task list no
# longer has the task, and counting identical blocks proved nothing about that.
# The marker literal lives in CONFIG["ghost_clear_marker_template"]
# (single source of truth, enforced by test_marker_literal_single_source_of_truth).
# {task_list}/{task_update}/{task_progress} are resolved per harness by
# core.py's placeholder substitution. Spelling either tool literally here sent
# Claude's tool names to every harness — a Codex transcript showed "Claude's
# TaskList ... TaskUpdate returns" inside a message that otherwise correctly
# said update_plan, naming two tools Codex does not have.
_ACT_STALE_AI_ESCAPE_TASK_TOOLS = (
    "If a task above is stale ({task_list} does not show it or "
    '{task_update} returns "Task not found"), print {marker} on its own line '
    "to mark those ids ignored."
)
_ACT_STALE_AI_ESCAPE_CHECKLIST = (
    "If an item above is stale (it no longer appears in {task_progress}), "
    "print {marker} on its own line to mark those ids ignored."
)


def _stale_escape_sentence(cli_type: str | None, *, marker: str) -> str:
    """Stale-task escape wording in the harness's own task vocabulary."""
    template = _ACT_STALE_AI_ESCAPE_CHECKLIST if writes_whole_task_list(cli_type) else _ACT_STALE_AI_ESCAPE_TASK_TOOLS
    # Only the marker is substituted here; the {task_*} placeholders stay for
    # core.py to resolve against the running harness.
    return template.replace("{marker}", marker)


def _delegate_action(cli_type: str | None) -> str:
    """Return a delegation action the running harness can actually perform.

    Prefers the harness's own task-update tool when it accepts "delegated".
    No supported harness does today — Claude Code's TaskUpdate rejects the
    value with InputValidationError — so this normally yields autorun's
    marker, which works everywhere because autorun parses it out of the AI's
    own output rather than asking the harness's tool to accept a status it
    does not define.
    """
    if "delegated" in platform_for(cli_type).native_task_statuses:
        return _ACT_DELEGATE
    return f"print {_delegate_marker_example()}"


def _claude_task_tool_names() -> list[str]:
    """Claude's task tools from the registry, in a stable order."""
    return sorted(registered_task_tools("claude"))


# --- BUG #80305/#80401 WORKAROUND START --- DELETE WHEN FIXED ---
# Claude Code can gate TaskCreate/Get/Update/List off at process start (#80305)
# or unregister the same deferred-tool bundle mid-session (#80401). A Stop or
# staleness block that names only those tools is otherwise impossible to obey.
# Disable with either matching AUTORUN_BUG_*_WORKAROUND_ENABLED=false flag.
# Removal: delete this block, its two CONFIG keys, the two recovery call sites
# in plugins.py, and the bracketed recovery tests after both bugs are fixed.
# Keep the SessionStart deferred-tool load instruction: deferral is designed
# Claude behavior, not either upstream bug.
BUG_80305_FLAG = (
    "AUTORUN_BUG_CLAUDE_CODE_TASK_TOOLS_GATED_OFF_BUG_80305_WORKAROUND_ENABLED"
)
BUG_80401_FLAG = (
    "AUTORUN_BUG_CLAUDE_CODE_TASK_TOOLS_VANISH_MID_SESSION_BUG_80401_WORKAROUND_ENABLED"
)


def _workaround_flag_applies(flag: str, cli_type: str | None) -> bool:
    """Resolve one bug flag as off, affected-platform auto, or always.

    REQUIREMENT: applicability comes from the Platform registry, not a harness
    name. This previously read ``detect_cli_type(...) == "claude"``, which
    config.py's own comment forbids ("Applicability is
    Platform.has_exit2_workaround, never a hardcoded name") and which core.py
    had already replaced for #18534 — so a new harness in an affected family
    would have silently missed the workaround here alone.
    """
    from .config import workaround_applies
    from .platforms import get_platform

    platform = get_platform(detect_cli_type({"cli_type": cli_type}))
    return workaround_applies(
        flag, affected=bool(platform and platform.gates_mutable_task_tools)
    )


def task_tool_recovery_sentence(cli_type: str | None) -> str:
    """Reachable recovery when Claude's four mutable task tools are absent."""
    if not (
        _workaround_flag_applies(BUG_80305_FLAG, cli_type)
        or _workaround_flag_applies(BUG_80401_FLAG, cli_type)
    ):
        return ""
    from .platforms import get_platform

    # The settings come from the registry's switch, so this sentence and the
    # installer name the same variables: the switch on, and each variable that
    # would otherwise swap the Task tools for TodoWrite set back to 1.
    switch = get_platform("claude").task_tool_switch
    settings = " and ".join(
        [f"`{switch.env}={switch.value}`", *(f"`{name}=1`" for name in switch.env_off)]
    )
    names = _claude_task_tool_names()
    return (
        f" If {', '.join(names[:-1])}, or {names[-1]} is unavailable or "
        "vanished, use ToolSearch once with "
        f"`select:{','.join(names)}`. If it returns no "
        "match, do not retry unavailable Task tools in this session: tell the "
        f"user to set {settings}, then restart Claude Code."
    )


# --- BUG #80305/#80401 WORKAROUND END --- DELETE WHEN FIXED ---


def _deferred_task_tool_instruction(ctx: EventContext) -> str:
    """The SessionStart load instruction, with one recovery path, not two."""
    load = (
        "Before tracked work, use ToolSearch once with "
        f"`select:{','.join(_claude_task_tool_names())}` to load Claude Code's "
        "deferred task tools. If it returns no match, do not loop"
    )
    unconfirmed = _unconfirmed_task_tools_notice(ctx)
    if unconfirmed:
        return load + "." + unconfirmed
    return load + "; follow the unavailable-tool recovery in autorun's task guidance."


def _unconfirmed_task_tools_notice(ctx: EventContext) -> str:
    """What a session whose task tools autorun cannot confirm needs to know.

    Task gates stand down without evidence, so the recovery text they carry
    never reaches this session. Say it here instead, once per fresh context:
    that autorun is not enforcing task tracking, and the one change that makes
    the tools present. It names the setting rather than a command: a
    marketplace-only install may have no ``autorun`` command at all. The
    setting comes from the registry's switch, the one ``autorun --install``
    writes.
    """
    from .platforms import platform_for, task_capability_is_known

    switch = platform_for(ctx.cli_type).task_tool_switch
    if switch is None or not switch.env or task_capability_is_known(ctx.task_tool_evidence):
        return ""
    return (
        " autorun is not enforcing task tracking yet: it cannot confirm the "
        "task tools exist, and starts once this session makes a task call. "
        "Claude Code 2.1.268 and later offer them to newer models only with "
        f"`{switch.env}={switch.value}`. If the search finds no match, tell the "
        "user once, then continue: add "
        f'`"{switch.env}": {json.dumps(switch.value)}` to the `env` object of '
        "Claude Code's settings.json (`autorun --install` does this), then "
        "restart Claude Code."
    )


def _task_actions_fragment(cli_type: str | None, *, staleness_reminders_disabled: bool = False) -> str:
    """Return stop/resume actions in the platform's native task vocabulary."""
    estop = format_command_for_cli("/ar:estop", cli_type)
    task_pause = format_command_for_cli("/ar:task pause <reason>", cli_type)
    task_ignore = format_command_for_cli("/ar:task ignore <id>", cli_type)
    user_actions = f"{task_pause} (discussion; tasks unchanged), {estop}, or {task_ignore}"
    if staleness_reminders_disabled:
        tasks_off = format_command_for_cli("/ar:tasks off", cli_type)
        user_actions += f"; {tasks_off} only disables reminders"
    recovery = task_tool_recovery_sentence(cli_type)
    awaiting = (
        "4b. Waiting on a decision only the user can make (approval to merge or push, "
        "a credential, a choice between options)? Print this line on its own, naming "
        f"that decision: {_awaiting_user_marker_example()} — the task waits on the user "
        "until the user's next message, and stops blocking until then"
    )
    if writes_whole_task_list(cli_type):
        return (
            "Actions: 1. You must complete or remove each checklist item before stopping "
            "2. Review/update: {task_progress} with the full current list "
            '3. Finish work: {task_progress} with finished items status="completed" '
            "4. Defer/delegate: keep a concrete follow-up item pending "
            f"{awaiting} "
            "5. Truly blocked? re-check that first (reread the error, try another "
            "approach); then, as a permitted last resort, remove the item from the "
            "list you send and state why "
            f"6. User only: {user_actions} "
            f"{recovery}"
        )
    return (
        f"Actions: 1. You must complete or discard each task before stopping "
        f"2. Review: {_ACT_REVIEW} "
        f"3. Do the work, then: {_ACT_COMPLETE} "
        f"4. Delegate to subagent first: {_delegate_action(cli_type)} (marks task non-blocking while subagent runs) "
        f"{awaiting} "
        f"5. Truly blocked? re-check that first (reread the error, try another "
        f"approach); then, as a permitted last resort, discard: {_ACT_DISCARD} "
        f"and state why "
        f"6. User only: {user_actions} "
        f"{recovery}"
    )


def _loop_guard_message(cli_type: str | None, *, blocked_count: int, task_count: int) -> str:
    """Say what the loop guard is when it lets a Stop through: an anomaly, not an exit."""
    task_label = "task" if task_count == 1 else "tasks"
    task_status = format_command_for_cli("/ar:task status", cli_type)
    return (
        "⚠️ autorun's Stop loop guard let this Stop through after the configured "
        f"stop_block_max_count: {blocked_count} consecutive Stops arrived with no "
        "main-agent tool call between them, which means "
        "the block reason is not reaching the agent. It is not a way to finish "
        f"work: retained {task_count} incomplete {task_label}, unchanged, and the "
        "next main-agent tool call, user-authored prompt or session start restores "
        "enforcement. Review "
        f"them with {task_status}."
    )


def _fork_notice(cli_type: str | None) -> str:
    """What autorun says once when a conversation turns out to be a fork."""
    goal_clear = platform_for(cli_type).goal_clear_command
    goal = (
        f" A goal the harness set in the parent conversation continues here until you run {goal_clear}."
        if goal_clear
        else ""
    )
    return (
        "This conversation is a fork of another. Tasks it inherited are listed "
        "and do not block it from stopping until it updates one to take it on, "
        "and it is not asked to create tasks of its own." + goal
    )


def _task_cli_hint(ctx: EventContext) -> str | None:
    """Return explicit CLI hint for task-tool classification, if one was supplied."""
    if hasattr(ctx, "_cli_type") and not getattr(ctx, "_cli_type_explicit", False):
        return None
    raw_cli_type = getattr(ctx, "_cli_type", None)
    if raw_cli_type is not None:
        return raw_cli_type
    return getattr(ctx, "cli_type", None)


def _stale_clear_marker_example() -> str:
    """Render the stale-clear marker template with a placeholder id.

    Derives from CONFIG to avoid duplicating the marker literal — keeps
    test_marker_literal_single_source_of_truth's invariant intact.

    Shows the list form, because clearing several stale tasks one marker at a
    time is the common case and repeating the line is pure noise.
    """
    return CONFIG["ghost_clear_marker_template"].replace("{id}", "<id>[,<id>...]")


def can_prompt() -> bool:
    """Whether asking the user a question can actually succeed.

    ``sys.stdin.isatty()`` alone is not the answer on Windows. The CRT reports
    every character device as a tty, and ``subprocess.DEVNULL`` is ``NUL``,
    which is one -- so a deliberately non-interactive run looked interactive,
    the CLI prompted, and the first ``input()`` raised EOFError. ``autorun task
    --configure`` exited 1 with "EOF when reading a line" instead of printing
    the settings it was asked for, and the clear guards prompted instead of
    refusing.

    Never probe by reading: an idle terminal has no buffered byte and the probe
    blocks. On Windows, ``GetConsoleMode`` distinguishes a console input handle
    from NUL without consuming input.
    """
    import sys

    stream = sys.stdin
    if stream is None or not stream.isatty():
        return False
    if sys.platform != "win32":
        return True
    return windows_tty_is_a_console(stream)


def windows_tty_is_a_console(stream) -> bool:
    """Whether a Windows stream that claims to be a tty is a real console.

    Separate from :func:`can_prompt` so it can be exercised off Windows. Inside
    ``can_prompt`` this branch runs only when ``sys.platform == "win32"``, so
    every test of it passed vacuously on macOS and Linux and the one platform
    that runs it had no coverage until Windows CI did.

    Two outcomes are not the same and were once conflated:

    * ``GetConsoleMode`` ran and said no -- ``NUL`` (what ``subprocess.DEVNULL``
      supplies) always yields a real handle, so this is the authoritative "not
      interactive" answer and the reason this function exists.
    * No handle could be obtained at all -- a captured, wrapped, or synthetic
      stream that still reports a tty. That is missing evidence, not evidence
      of ``NUL``, and returning False there refuses to prompt on a genuine
      terminal because the stream lacks an optional API. ``isatty()`` stands.
    """
    try:
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(stream.fileno())
    except (AttributeError, ImportError, OSError, ValueError):
        # ImportError covers msvcrt being absent, which is every non-Windows
        # platform: without it this helper cannot be exercised off Windows,
        # and the branch would keep the zero coverage that let it regress.
        return True

    try:
        mode = ctypes.c_ulong()
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    except (AttributeError, OSError, ValueError):
        return True


# === Configuration (dataclass pattern from PlanExportConfig) ===

CONFIG_PATH = ipc.AUTORUN_CONFIG_DIR / "task-lifecycle.config.json"


def _positive_seconds(value: object, default: float) -> float:
    """``value`` as a positive finite float, else ``default``."""
    if isinstance(value, bool):
        return default
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return default
    return seconds if math.isfinite(seconds) and seconds > 0 else default


@dataclass
class TaskLifecycleConfig:
    """Task lifecycle configuration (follows PlanExportConfig pattern)."""

    enabled: bool = True
    storage_dir: Path = ipc.AUTORUN_CONFIG_DIR / "task-tracking"
    max_resume_tasks: int = 20
    # Loop guard only: consecutive main-agent Stops with no main-agent tool
    # call between them. Reaching it means the harness is not delivering the
    # block reason; it never decides whether work is done.
    stop_block_max_count: int = 3
    task_ttl_days: int = 30
    recent_task_days: int = 1  # tasks created within this window shown as "recent"
    debug_logging: bool = False
    ghost_clear_enabled: bool = True
    task_pause_default_ttl_seconds: float = TASK_PAUSE_DEFAULT_TTL_SECONDS
    state_lock_timeout_seconds: float = DEFAULT_SESSION_TIMEOUT
    hook_state_lock_timeout_seconds: float = float(CONFIG.get("hook_state_lock_timeout_seconds", 0.5))

    @classmethod
    def load(cls) -> "TaskLifecycleConfig":
        """Load from config file with defaults."""
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if "storage_dir" in data and isinstance(data["storage_dir"], str):
                    data["storage_dir"] = Path(data["storage_dir"])
                # A lock timeout that is not a positive finite number reaches
                # the Stop gate as an exception, which it treats as "allow".
                data["state_lock_timeout_seconds"] = max(
                    _positive_seconds(data.get("state_lock_timeout_seconds"), DEFAULT_SESSION_TIMEOUT),
                    DEFAULT_SESSION_TIMEOUT,
                )
                data["hook_state_lock_timeout_seconds"] = _positive_seconds(
                    data.get("hook_state_lock_timeout_seconds"),
                    cls.hook_state_lock_timeout_seconds,
                )
                pause_ttl = data.get(
                    "task_pause_default_ttl_seconds",
                    cls.task_pause_default_ttl_seconds,
                )
                if isinstance(pause_ttl, bool) or not isinstance(pause_ttl, (int, float)) or not math.isfinite(pause_ttl) or pause_ttl <= 0:
                    raise TypeError(f"task_pause_default_ttl_seconds must be a finite number greater than 0, got {pause_ttl!r}; pass positive seconds")
                return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})
            except (ValueError, TypeError):
                pass
        return cls()

    def save(self) -> None:
        """Save config to file."""
        CONFIG_PATH.parent.mkdir(exist_ok=True)
        data = asdict(self)
        data["storage_dir"] = str(data["storage_dir"])
        CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


# === Plan Acceptance Notification Config ===

PLAN_NOTIFY_CONFIG_PATH = ipc.AUTORUN_CONFIG_DIR / "plan-notify.config.json"


@dataclass
class PlanNotifyConfig:
    """Plan acceptance notification config (follows TaskLifecycleConfig pattern)."""

    tdd_scaffolding: bool = True
    task_update_enforcement: bool = True
    dependency_wiring: bool = True

    @classmethod
    def load(cls) -> "PlanNotifyConfig":
        """Load from config file with defaults."""
        if PLAN_NOTIFY_CONFIG_PATH.exists():
            try:
                data = json.loads(PLAN_NOTIFY_CONFIG_PATH.read_text(encoding="utf-8"))
                return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})
            except (ValueError, TypeError):
                pass
        return cls()

    def save(self) -> None:
        """Save config to file."""
        PLAN_NOTIFY_CONFIG_PATH.parent.mkdir(exist_ok=True)
        data = {
            "tdd_scaffolding": self.tdd_scaffolding,
            "task_update_enforcement": self.task_update_enforcement,
            "dependency_wiring": self.dependency_wiring,
        }
        PLAN_NOTIFY_CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


# === Stop loop guard ===


def _reset_stop_block_sequence(metadata: dict) -> None:
    """Start a fresh consecutive Stop sequence.

    Also drops the identical-block counter older builds kept, so a session
    carried across the upgrade stops storing it.
    """
    metadata["stop_block_count"] = 0
    metadata.pop("last_delivered_stop_block_generation", None)
    metadata.pop("consecutive_identical_stop_block_count", None)
    metadata.pop("last_stop_block_id_hash", None)


def _stop_sequence_is_reset(metadata: dict) -> bool:
    """Whether ``_reset_stop_block_sequence`` would change nothing."""
    return not metadata.get("stop_block_count") and not any(
        key in metadata
        for key in (
            "last_delivered_stop_block_generation",
            "consecutive_identical_stop_block_count",
            "last_stop_block_id_hash",
        )
    )


@cache
def _stale_clear_marker_regex() -> re.Pattern:
    """Regex derived from the configured stale-clear marker template."""
    return _marker_regex(CONFIG["ghost_clear_marker_template"])


# One argument inside a marker: a bare task id, or a name=value option.
_MARKER_ARGUMENT = r"[A-Za-z0-9_.=-]+"

# The keywords that name where delegated work went. ``session=`` remains
# accepted for compatibility; the explicit name prevents confusion with the
# parent hook session when a marker is copied into a child-agent prompt.
_MARKER_SESSION_KEYWORDS = ("agent_session_id", "session")

# Agent-spawn identity capture. "Agent" is the spawn tool's name captured
# from a live Claude Code tool result (2026-08-05); "Task" is the same tool's
# name in other Claude Code builds. Only these tools' results are parsed —
# a Bash result merely QUOTING a spawn payload must never seed the ledger,
# the same forgery discipline the marker parser applies. Parallel subagents
# share the parent session id (https://github.com/anthropics/claude-code/issues/7881), so this ledger
# is the only reliable identity source for the returned gate.
# Spawn tool names are per-harness and live on Platform.agent_spawn_tools, the
# same registry that owns task_create_tools and friends, so a new harness
# declares its name once instead of extending a set buried here.
# The id arrives in two shapes and both must parse. A live claude -p fan-out
# on 2026-08-05 sent PostToolUse tool_response as the structured launch record
#   {"isAsync": true, "status": "async_launched", "agentId": "<id>", ...}
# which reaches here JSON-encoded as '"agentId": "<id>"', while the transcript
# and older builds carry the prose line 'agentId: <id>'. One pattern with
# optional quotes covers both, and covers a nested id too, instead of a
# dict-shape reader that would miss the prose form.
# To re-derive this fixture after a harness change, read the parent session's
# ~/.claude/projects/<project>/<session>.jsonl, keep the lines carrying a
# 'toolUseResult' field (that object is what arrives as tool_response; the
# rendered tool_result content block is a different, prose shape), then
# generalize ids, paths, and model names before committing it.
# Codex spells the same field `agent_id` and its ids carry dashes
# (codex-rs/core/src/tools/handlers/multi_agents_tests.rs:257), so accept both
# spellings and both id alphabets rather than one harness's.
_AGENT_SPAWN_ID_RE = re.compile(r'\bagent(?:Id|_id)"?\s*:\s*"?([A-Za-z0-9][A-Za-z0-9-]{7,})')
# A SubagentStop's transcript_path names the child: .../agent-<id>.jsonl.
_AGENT_TRANSCRIPT_RE = re.compile(r"agent-([A-Za-z0-9-]{8,})\.jsonl$")
# A storage bound on returned history only. A spawn that has not returned,
# claimed or not, is a child still running that a marker may name, so no count
# drops it; it leaves the ledger by evidence (its SubagentStop, or its
# harness process gone).
_MAX_TRACKED_SPAWNS = 16
_STATUS_FIELD_RE = re.compile(r'"status"\s*:\s*"([^"]+)"')


def _bounded_spawn_ledger(entries: list[dict]) -> list[dict]:
    """Every unreturned spawn, plus bounded returned history."""
    running = [entry for entry in entries if not entry.get("returned")]
    returned = [entry for entry in entries if entry.get("returned")]
    return running + returned[-_MAX_TRACKED_SPAWNS:]


def _spawn_mode(cli_type: str | None, tool_result: str) -> str:
    """``async``, ``sync`` or ``unrecognized`` from a spawn result's status.

    The meaning of each value is the platform's data. ``unrecognized`` is
    treated as running: ending a delegation needs evidence.
    """
    match = _STATUS_FIELD_RE.search(tool_result or "")
    status = match.group(1) if match else ""
    platform = platform_for(cli_type)
    if status in platform.agent_spawn_finished_results:
        return "sync"
    if status in platform.agent_spawn_running_results:
        return "async"
    return "unrecognized"


def _agent_receipt_key(cli_type: str, agent_id: str) -> str:
    """Namespace globally unique child ids by the harness that issued them."""
    return f"{cli_type}:{agent_id}"


def _delegation_receipt_session_id(cli_type: str, agent_id: str) -> str:
    """The keyed state root for one child-return authority record."""
    return f"{TASK_DELEGATION_RECEIPT_SESSION_PREFIX}{_agent_receipt_key(cli_type, agent_id)}"


def _delegation_receipt_sessions_for_parents(
    parent_global_keys: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Find keyed receipt roots for explicit maintenance operations only."""
    wanted = None if parent_global_keys is None else frozenset(parent_global_keys)
    manager = get_session_manager()
    matches = []
    for receipt_session in manager.list_sessions(namespace="task_delegation_receipts"):
        if receipt_session == TASK_DELEGATION_RECEIPT_SESSION:
            continue
        receipt = manager.read_field(
            receipt_session,
            "receipt",
            MISSING,
            timeout=DEFAULT_SESSION_TIMEOUT,
        )
        if not isinstance(receipt, dict):
            continue
        if wanted is None or receipt.get("parent_global_key") in wanted:
            matches.append(receipt_session)
    return tuple(matches)


def _recorded_harness_is_live(
    record: dict,
    *,
    starts_by_pid: dict[int, int],
    legacy_alive_by_pid: dict[int, bool],
) -> bool:
    """Check the one process-identity scheme shared with daemon records.

    New records pair the PID with ``client.get_stable_process_identity``'s
    microsecond birth stamp and compare it through ``ipc.process_start_units``.
    PID-only records predate that field and retain their original
    ``psutil.pid_exists`` behavior.
    """
    pid = record.get("harness_pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return True
    started_at_units = record.get("harness_started_at_units")
    if isinstance(started_at_units, int) and not isinstance(started_at_units, bool) and started_at_units > 0:
        if pid not in starts_by_pid:
            starts_by_pid[pid] = ipc.process_start_units(pid)
        return starts_by_pid[pid] == started_at_units
    if pid not in legacy_alive_by_pid:
        import psutil

        legacy_alive_by_pid[pid] = psutil.pid_exists(pid)
    return legacy_alive_by_pid[pid]


def _bounded_delegation_receipts(receipts: dict[str, dict], *, reclaim: bool = False) -> dict[str, dict]:
    """Every open receipt, plus bounded returned history.

    An open receipt is the only way a child that reports in its own session
    (Codex) reaches its parent, so no count drops it. With ``reclaim``, which
    only SubagentStop passes, one whose recorded parent harness process is
    gone is dropped: the child ran inside that process and cannot report.
    One process-identity check per distinct recorded pid; a receipt without one
    stays. PID-only receipts keep their legacy existence check.
    """
    starts_by_pid: dict[int, int] = {}
    legacy_alive_by_pid: dict[int, bool] = {}

    def harness_gone(receipt: dict) -> bool:
        pid = receipt.get("harness_pid")
        if not reclaim or not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return False
        return not _recorded_harness_is_live(
            receipt,
            starts_by_pid=starts_by_pid,
            legacy_alive_by_pid=legacy_alive_by_pid,
        )

    unresolved = {
        key: value
        for key, value in receipts.items()
        if not value.get("returned") and not harness_gone(value)
    }
    resolved = sorted(
        (
            (key, value)
            for key, value in receipts.items()
            if value.get("returned")
        ),
        key=lambda item: float(item[1].get("returned_at") or item[1].get("at") or 0),
    )[-_MAX_RESOLVED_DELEGATION_RECEIPTS:]
    unresolved.update(resolved)
    return unresolved


@cache
def _marker_regex(template: str) -> re.Pattern:
    """Regex matching one AI-printable marker template's argument slot.

    The slot accepts a comma-delimited list rather than a single id, so one
    marker can name every task it applies to. Writing one marker per task was
    the only option before, which made handing three tasks to a subagent
    three near-identical lines.
    """
    prefix, suffix = template.split("{id}")
    # At least one argument is required, so an empty marker does not match at
    # all rather than matching and naming nothing.
    arguments = rf"{_MARKER_ARGUMENT}(?:\s*,\s*{_MARKER_ARGUMENT})*\s*,?"
    return re.compile(re.escape(prefix) + rf"\s*({arguments})\s*" + re.escape(suffix))


# One argument, tokenized: an optional ``name=`` followed by a value.
# Whitespace, separators, and a trailing comma all fall out of the pattern,
# so there is no hand-rolled splitting to get wrong.
_MARKER_ARGUMENT_TOKEN = re.compile(r"(?:(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*)?(?P<value>[A-Za-z0-9_.-]+)")


def _parse_marker_arguments(raw: str) -> tuple[list[str], "str | None"]:
    """Split one marker's arguments into task ids and an optional agent session.

    A bare argument is a task id. ``name=value`` is an option. An option
    nobody recognizes is dropped rather than treated as an id, so a typo does
    nothing instead of delegating a task called ``priority=high``.
    """
    ids: list[str] = []
    options: dict[str, str] = {}
    for token in _MARKER_ARGUMENT_TOKEN.finditer(raw or ""):
        key, value = token.group("key"), token.group("value")
        if key is None:
            ids.append(value)
        else:
            options[key.lower()] = value
    return ids, next(
        (options[key] for key in _MARKER_SESSION_KEYWORDS if key in options),
        None,
    )


@cache
def _marker_literal_prefix(template: str) -> str:
    """The fixed text before a marker's arguments, e.g. ``MARKER_NAME(``."""
    return template.split("{id}")[0]


def _standalone_non_code_lines(text: str) -> Iterable[str]:
    """Yield stripped lines outside fenced Markdown code blocks."""
    fence: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        marker = next(
            (candidate for candidate in ("```", "~~~") if stripped.startswith(candidate)),
            None,
        )
        if marker is not None:
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is None and stripped:
            yield stripped


def _extract_marker_entries(
    template: str,
    *texts: object,
    placement: Literal["anywhere", "standalone-line"] = "anywhere",
) -> list:
    """Every occurrence of one marker, as (task ids, session) pairs.

    This runs on every Stop and every PostToolUse, and almost no hook carries
    a marker, so the cost that matters is the cost of finding nothing. Two
    things keep that cheap, both following what LazyTranscript already does:

    Each text is tested for the marker's fixed prefix before the pattern is
    applied. That test is CPython's own substring search — a single pass with
    no backtracking — and it rejects the common case outright. The compiled
    pattern and the prefix are both cached, so neither is rebuilt per call.

    The texts are also scanned separately rather than concatenated. Joining
    them copied every byte on every call, which for a transcript at its 64 KiB
    ingest cap (see normalize_hook_payload) was a wasted copy per marker per
    hook — twice over, since two markers are scanned.
    """
    prefix = _marker_literal_prefix(template)
    pattern = None
    entries = []
    for text in texts:
        if not isinstance(text, str) or prefix not in text:
            continue
        if pattern is None:
            pattern = _marker_regex(template)
        if placement == "standalone-line":
            raw_entries = (match.group(1) for line in _standalone_non_code_lines(text) if (match := pattern.fullmatch(line)) is not None)
        else:
            raw_entries = pattern.findall(text)
        for raw in raw_entries:
            ids, session = _parse_marker_arguments(raw)
            if ids or session:
                entries.append((ids, session))
    return entries


def _extract_marker_task_ids(
    template: str,
    *texts: object,
    placement: Literal["anywhere", "standalone-line"] = "anywhere",
) -> list[str]:
    """Return unique task ids matching one marker template across texts."""
    ids = [
        task_id
        for entry_ids, _session in _extract_marker_entries(
            template,
            *texts,
            placement=placement,
        )
        for task_id in entry_ids
    ]
    return list(dict.fromkeys(ids))


def latest_assistant_turn(ctx) -> tuple[str, str]:
    """Identity and text of the newest assistant message, or ``("", "")``.

    The only text a marker reader may treat as the assistant speaking. A
    harness that sends its transcript (Pi, Prime) includes autorun's own Stop
    block messages, which list the stale-clear marker with real task ids, so a
    reader scanning the whole transcript obeys autorun itself. Claude sends no
    transcript on Stop; its final reply is only in ``last_assistant_message``.
    """
    transcript = getattr(ctx, "transcript", None)
    latest = transcript.latest_assistant_message() if transcript else None
    if isinstance(latest, tuple) and len(latest) == 2 and all(isinstance(value, str) for value in latest):
        return latest
    message = getattr(ctx, "last_assistant_message", "") or ""
    if not message:
        return "", ""
    return fingerprint_call(
        getattr(ctx, "session_id", ""),
        "assistant-message",
        message,
    ), message


def extract_stale_clear_task_ids(*texts: object) -> list[str]:
    """Return unique task ids from configured stale-clear markers."""
    return _extract_marker_task_ids(CONFIG["ghost_clear_marker_template"], *texts)


def stale_clear_marker_ids(ctx) -> list[str]:
    """Stale-clear marker ids in this event's tool result or newest assistant message."""
    return extract_stale_clear_task_ids(ctx.tool_result_str, latest_assistant_turn(ctx)[1])


def extract_delegate_task_ids(*texts: object) -> list[str]:
    """Return unique task ids from configured delegation markers."""
    return _extract_marker_task_ids(CONFIG["delegate_marker_template"], *texts)


def extract_task_pause_resume_generations(*texts: object) -> list[str]:
    """Return exact standalone generation markers outside code fences."""
    return _extract_marker_task_ids(
        CONFIG["task_pause_resume_marker_template"],
        *texts,
        placement="standalone-line",
    )


def extract_delegate_markers(*texts: object) -> list:
    """Delegation markers as (task ids, agent session) pairs.

    Callers that only need the ids use ``extract_delegate_task_ids``; this
    keeps the agent session each group of tasks was handed to, which is what
    makes an unreturned delegation traceable.
    """
    return _extract_marker_entries(CONFIG["delegate_marker_template"], *texts)


def extract_awaiting_user_markers(text: object) -> list[tuple[list[str], str]]:
    """(task ids, stated decision) for each awaiting-user marker line.

    Only a marker that starts its own line outside a code fence counts, so a
    quoted or discussed marker does nothing. The decision is the text after
    the marker, with an optional leading colon; it is "" when nothing
    follows, and the caller rejects that.
    """
    template = CONFIG["awaiting_user_marker_template"]
    if not isinstance(text, str) or _marker_literal_prefix(template) not in text:
        return []
    pattern = _marker_regex(template)
    entries = []
    for line in _standalone_non_code_lines(text):
        match = pattern.match(line)
        if match is None:
            continue
        ids, _session = _parse_marker_arguments(match.group(1))
        if ids:
            entries.append((ids, line[match.end():].strip().lstrip(":").strip()))
    return entries


def _awaiting_user_marker_example() -> str:
    return CONFIG["awaiting_user_marker_template"].replace("{id}", "<id>[,<id>...]") + ": <the decision you need>"


def _task_refs(task_ids: Iterable[object]) -> str:
    """``#1, #2`` for display."""
    return ", ".join(f"#{task_id}" for task_id in task_ids)


def _delegation_agent(task: Dict) -> "str | None":
    """The subagent a delegated task was handed to, if one was recorded.

    ``metadata.delegation`` is the record; ``delegated_to_session`` is what
    builds before it wrote, still read for rows they delegated.
    """
    metadata = task.get("metadata") or {}
    record = metadata.get("delegation")
    agent = record.get("agent_id") if isinstance(record, dict) else None
    return agent or metadata.get("delegated_to_session") or None


def _cycle_members(graph: Dict[str, List[str]]) -> set:
    """Nodes on a cycle of ``graph`` (node -> nodes it depends on).

    Tarjan's strongly connected components, iterative so a long blockedBy
    chain cannot reach the recursion limit. Linear in nodes plus edges.
    """
    index: Dict[str, int] = {}
    low: Dict[str, int] = {}
    on_stack: set = set()
    stack: list = []
    members: set = set()
    for root in graph:
        if root in index:
            continue
        index[root] = low[root] = len(index)
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter(graph[root]))]
        while work:
            node, edges = work[-1]
            descended = False
            for nxt in edges:
                if nxt not in graph:
                    continue
                if nxt not in index:
                    index[nxt] = low[nxt] = len(index)
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(graph[nxt])))
                    descended = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if descended:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1 or node in graph[node]:
                    members.update(component)
    return members


def _delegate_marker_example() -> str:
    """Delegation marker with placeholder arguments, for guidance text.

    Shows both extras the grammar accepts: several ids in one marker, and an
    optional ``agent_session_id=`` naming where the work went. The legacy
    ``session=`` spelling remains accepted.
    """
    return CONFIG["delegate_marker_template"].replace("{id}", "<id>[,<id>...][,agent_session_id=<agent-session-id>]")


# === TaskLifecycle Class ===


class TaskLifecycle:
    """Task lifecycle manager (follows PlanExport pattern).

    DRY REUSE:
    - session_state() for persistence (no custom persistence code)
    - @property + atomic_update_*() pattern from PlanExport
    - Simple append logging (no RotatingFileHandler)
    - Frozenset constants for status checks (single source of truth)

    Per-Session Isolation:
    - Each AI session uses unique global key: "__task_lifecycle__{session_id}"
    - State stored in the effective backend and keyed per session
    - Audit logs are per-session files
    """

    # Schema version for task lifecycle data stored in JSON files
    # (via session_state() in daemon_state.json).
    # Bump when the task dict structure or status transition rules change.
    # Migration runs automatically on first access via _migrate_if_needed().
    #
    # Version history:
    #   v1: Initial task schema. No version field stored in JSON. Ghost tasks
    #       (tasks first seen via TaskUpdate, not TaskCreate) could transition
    #       to in_progress/pending, causing them to block Stop hook permanently
    #       if the completed update was lost (e.g., session end, context compaction).
    #   v2: Ghost task protection. Ghost tasks (metadata.ghost_task=True) can only
    #       transition to terminal statuses (completed/deleted/ignored). Non-terminal
    #       status requests (in_progress/pending) are silently skipped. Existing v1
    #       ghost tasks with blocking statuses are reset to "ignored" on migration.
    #   v3: "delegated" status added to NON_BLOCKING_STATUSES. Allows AI to explicitly
    #       mark tasks as delegated to a subagent, unblocking Stop while preserving
    #       task visibility (delegated tasks appear in SessionStart for follow-up).
    SCHEMA_VERSION = 3

    # Status constants (single source of truth - DRY)
    COMPLETED_STATUSES = COMPLETED_TASK_STATUSES
    # Statuses that don't block stopping.
    # "ignored" is set by /ar:task ignore (autorun code), not via TaskUpdate tool.
    # "delegated" is set explicitly by AI before spawning a subagent for a task.
    NON_BLOCKING_STATUSES = NON_BLOCKING_TASK_STATUSES

    # Statuses safe to prune after TTL (truly terminal, no resume expected)
    PRUNABLE_STATUSES = PRUNABLE_TASK_STATUSES
    TASK_OUTPUT_RECENT_LIMIT = int(CONFIG.get("task_output_recent_limit", 64))

    def __init__(self, session_id: str | None = None, ctx: EventContext | None = None, config: TaskLifecycleConfig | None = None):
        """Initialize task lifecycle manager.

        Args:
            session_id: Explicit session ID (for CLI commands)
            ctx: EventContext (for hook handlers - has session_id)
            config: Config override (uses TaskLifecycleConfig.load() if None)

        Raises:
            ValueError: If session_id cannot be determined
        """
        self.ctx = ctx
        # Session ID resolution (explicit > hook context > shared registry).
        if session_id:
            self.session_id = session_id
        elif ctx:
            self.session_id = ctx.session_id
        else:
            self.session_id = (
                resolve_standalone_session_identity().session_id
            )

        # CLI Type resolution (explicit > ctx > auto-detect)
        if ctx:
            self._cli_type = ctx.cli_type
        else:
            from .config import detect_cli_type

            self._cli_type = detect_cli_type()

        # Config
        self.config = config or TaskLifecycleConfig.load()

        # Global key for session state (per-session isolation)
        self.global_key = f"{TASK_LIFECYCLE_SESSION_PREFIX}{self.session_id}"

        # Audit log path (per-session, append-only)
        self.audit_log = (
            self.config.storage_dir / _safe_path_component(self.session_id) / "audit.log"
        )

    # === State Access (REUSES session_state() - DRY) ===

    @property
    def _state_lock_timeout(self) -> float:
        """How long this operation may wait for the shared state lock.

        Answered per operation, not once in ``__init__``: the deadline moves as
        a handler spends its allowance, and a value frozen at construction is
        the same constant advisory counters use. That is how a Pi task receipt
        came to give up after 0.5s inside a handler still holding most of a 2s
        budget — the write was not late, it was discarded.

        Outside a hook there is no caller waiting, so the standalone budget
        (30s) stands; ``state_lock_timeout`` owns the rule for the hook case and
        is given this manager's own configured budget as the floor, so lowering
        it in configuration still lowers it here.
        """
        if self.ctx is None:
            return self.config.state_lock_timeout_seconds
        return state_lock_timeout(
            self.ctx, floor=self.config.hook_state_lock_timeout_seconds
        )

    def _daemon_serialized(self):
        """Take turns with other daemon threads; a no-op for standalone use."""
        if self.ctx is None:
            return contextlib.nullcontext()
        return self.ctx.state_synchronized(session_id=self.global_key)

    @contextlib.contextmanager
    def _session_state(self):
        """Open this task lifecycle session with the configured hook lock budget.

        Serialized against the daemon's other state access whenever a hook
        supplies a context. Parallel tool calls in one assistant turn make the
        harness fire several PostToolUse hooks at once, and the daemon runs
        them on separate threads of a shared pool. Racing them for this
        session's file lock on a sub-second budget loses the losers' writes,
        which strands a task the AI already completed at its previous status:
        Stop then blocks on it forever, and the harness — which did complete
        it — answers "Task not found" to every repair attempt. Every other
        daemon write path already holds this lock across the file lock.
        """
        with self._daemon_serialized():
            with session_state(self.global_key, timeout=self._state_lock_timeout) as state:
                yield state

    @contextlib.contextmanager
    def _delegation_receipt_state(
        self,
        cli_type: str,
        agent_id: str,
        ctx: EventContext | None = None,
    ):
        """Open one keyed child-to-parent authority record safely."""
        receipt_session = _delegation_receipt_session_id(cli_type, agent_id)
        hook_ctx = ctx or self.ctx
        synchronization = (
            hook_ctx.state_synchronized(session_id=receipt_session)
            if hook_ctx is not None
            else contextlib.nullcontext()
        )
        with synchronization:
            with session_state(
                receipt_session,
                timeout=self._state_lock_timeout,
            ) as state:
                yield state

    @contextlib.contextmanager
    def _legacy_delegation_receipt_state(self, ctx: EventContext | None = None):
        """Open the pre-keyed whole-registry record for compatibility only."""
        hook_ctx = ctx or self.ctx
        synchronization = (
            hook_ctx.state_synchronized(session_id=TASK_DELEGATION_RECEIPT_SESSION)
            if hook_ctx is not None
            else contextlib.nullcontext()
        )
        with synchronization:
            with session_state(
                TASK_DELEGATION_RECEIPT_SESSION,
                timeout=self._state_lock_timeout,
            ) as state:
                yield state

    def _promote_legacy_delegation_receipts_once(
        self, ctx: EventContext | None = None
    ) -> None:
        """Move the bounded legacy receipt map to keyed roots, then retire it.

        Each keyed write commits before the completion marker and legacy-map
        deletion commit. A crash therefore leaves the marker absent and the
        next attempt safely replays identical keyed writes; conflicting keyed
        data is refused rather than overwritten.
        """
        manager = get_session_manager()
        done = manager.read_field(
            TASK_DELEGATION_RECEIPT_SESSION,
            _DELEGATION_RECEIPTS_KEYED_MIGRATION,
            False,
            timeout=self._state_lock_timeout,
        )
        if done is True:
            return
        with self._legacy_delegation_receipt_state(ctx) as legacy_state:
            if legacy_state.get(_DELEGATION_RECEIPTS_KEYED_MIGRATION) is True:
                return
            receipts = _bounded_delegation_receipts(
                copy.deepcopy(legacy_state.get(_DELEGATION_RECEIPTS_FIELD, {}))
            )
            for key, receipt in receipts.items():
                if not isinstance(receipt, dict):
                    continue
                cli_type = str(receipt.get("cli_type") or "")
                agent_id = str(receipt.get("agent_id") or "")
                if not cli_type or not agent_id or _agent_receipt_key(cli_type, agent_id) != key:
                    continue
                with self._delegation_receipt_state(cli_type, agent_id, ctx) as keyed_state:
                    existing = copy.deepcopy(keyed_state.get("receipt"))
                    if isinstance(existing, dict) and existing != receipt:
                        raise RuntimeError(
                            f"delegation receipt {key!r} exists in legacy and keyed storage "
                            "with different contents; refusing to choose one"
                        )
                    if not isinstance(existing, dict):
                        keyed_state["receipt"] = copy.deepcopy(receipt)
            if _DELEGATION_RECEIPTS_FIELD in legacy_state:
                del legacy_state[_DELEGATION_RECEIPTS_FIELD]
            legacy_state[_DELEGATION_RECEIPTS_KEYED_MIGRATION] = True

    def _receipt_parent_still_owns_child(self, receipt: dict) -> bool:
        """Whether recorded parent state still authorizes this child receipt."""
        parent_global_key = receipt.get("parent_global_key")
        parent_session_id = receipt.get("parent_session_id")
        if not isinstance(parent_global_key, str) or not parent_global_key:
            return True
        metadata = get_session_manager().read_field(
            parent_global_key,
            "session_metadata",
            MISSING,
            timeout=self._state_lock_timeout,
        )
        if metadata is MISSING:
            return False
        claimed = bool(receipt.get("claimed"))
        if isinstance(metadata, dict):
            agent_id = str(receipt.get("agent_id") or "")
            claimed = claimed or any(
                str(entry.get("id")) == agent_id and entry.get("claimed")
                for entry in metadata.get("agent_spawns", [])
                if isinstance(entry, dict)
            )
        if not claimed:
            return True
        if not isinstance(parent_session_id, str) or not parent_session_id:
            return False
        agent_id = str(receipt.get("agent_id") or "")
        parent = TaskLifecycle(
            session_id=parent_session_id,
            config=self.config,
        )
        return any(
            task.get("status") == "delegated"
            and _delegation_agent(task) == agent_id
            for task in parent.get_incomplete_tasks(exclude_blocking=False)
        )

    def _receipt_is_reclaimable(self, receipt: dict) -> bool:
        starts_by_pid: dict[int, int] = {}
        legacy_alive_by_pid: dict[int, bool] = {}
        pid = receipt.get("harness_pid")
        if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
            if not _recorded_harness_is_live(
                receipt,
                starts_by_pid=starts_by_pid,
                legacy_alive_by_pid=legacy_alive_by_pid,
            ):
                return True
        return not self._receipt_parent_still_owns_child(receipt)

    def _delegation_receipt(self, cli_type: str, agent_id: str) -> dict | None:
        """Read one parent authority record and retire evidence-ended records."""
        self._promote_legacy_delegation_receipts_once()
        with self._delegation_receipt_state(cli_type, agent_id) as state:
            receipt = copy.deepcopy(state.get("receipt"))
        if not isinstance(receipt, dict):
            return None
        if not self._receipt_is_reclaimable(receipt):
            return receipt

        get_session_manager().clear_session_if_field_matches(
            _delegation_receipt_session_id(cli_type, agent_id),
            "receipt",
            receipt,
            timeout=self._state_lock_timeout,
        )
        return None

    def _mark_delegation_receipt_claimed(
        self,
        cli_type: str,
        agent_id: str,
        task_ids: Iterable[str],
    ) -> None:
        """Record that task state now depends on this exact child receipt."""
        self._promote_legacy_delegation_receipts_once()
        with self._delegation_receipt_state(cli_type, agent_id) as state:
            receipt = copy.deepcopy(state.get("receipt"))
            if isinstance(receipt, dict) and receipt.get("parent_session_id") == self.session_id:
                receipt["claimed"] = True
                receipt["delegated_task_ids"] = list(dict.fromkeys(str(value) for value in task_ids))
                state["receipt"] = receipt
                return

    def _finish_delegation_receipts(
        self,
        cli_type: str,
        agent_ids: Iterable[str],
        now: float,
    ) -> None:
        """Retire keyed receipts after a child return."""
        self._promote_legacy_delegation_receipts_once(self.ctx)
        manager = get_session_manager()
        for value in agent_ids:
            agent_id = str(value)
            receipt_session = _delegation_receipt_session_id(cli_type, agent_id)
            receipt = manager.read_field(
                receipt_session,
                "receipt",
                MISSING,
                timeout=self._state_lock_timeout,
            )
            if isinstance(receipt, dict) and receipt.get("parent_session_id") == self.session_id:
                manager.clear_session_if_field_matches(
                    receipt_session,
                    "receipt",
                    receipt,
                    timeout=self._state_lock_timeout,
                )


    def _migrate_if_needed(self, state: Dict) -> None:
        """Migrate stored state to current schema version (lazy self-healing).

        LIFECYCLE: Called automatically on every .tasks access and atomic_update_tasks().
        This ensures old data gets fixed when accessed, no manual intervention needed.

        CRITICAL INVARIANTS:
        1. Migrations are idempotent (safe to run multiple times)
        2. Runs inside the ``session_state()`` backend transaction/lock scope
        3. Updates both tasks AND schema_version atomically
        4. Each version bump preserves backward compatibility

        WHY LAZY MIGRATION:
        - Daemon restarts don't trigger migration (JSON just sits on disk)
        - Session resume/continuation triggers migration via first .tasks access
        - Failed sessions get fixed when next session accesses their data
        - No need for batch migration scripts or manual database edits
        """
        stored_version = state.get("schema_version", 1)
        if stored_version >= self.SCHEMA_VERSION:
            return  # Already current - no work needed

        row_backed = state.get("task_rows_migrated") is True
        tasks = {} if row_backed else state.get("tasks", {})

        # === v1 → v2 Migration: Fix Ghost Task Blocking Bug ===
        # PROBLEM (v1): Ghost tasks (created via TaskUpdate on unknown ID) could
        # transition from initial "ignored" to "in_progress". If session ended
        # before the "completed" update persisted, ghost stayed "in_progress"
        # forever, blocking all Stop hooks in that session.
        #
        # FIX (v2): Reset any ghost task with non-terminal status back to "ignored".
        # Going forward, ghost tasks can't transition to blocking statuses (see
        # status transition code at line ~395), but existing v1 data needs fixing.
        if stored_version < 2:
            fixed_count = 0
            for task in tasks.values():
                is_ghost = task.get("metadata", {}).get("ghost_task", False)
                if is_ghost and task.get("status") not in self.NON_BLOCKING_STATUSES:
                    # Reset blocking ghost to ignored
                    task["status"] = "ignored"
                    fixed_count += 1

            if fixed_count > 0 and self.config.debug_logging:
                self.log_event("MIGRATION", "v1->v2", f"Fixed {fixed_count} ghost tasks", "schema_update", {"fixed_count": fixed_count})

        # === v2 → v3 Migration: "delegated" status added to NON_BLOCKING_STATUSES ===
        # No data migration needed — "delegated" is a new status AI can set going
        # forward. Existing tasks are unaffected. Version bump records the schema
        # so future migrations have a clean baseline.
        if stored_version < 3:
            pass  # v2→v3: pure addition, no data changes needed

        # Atomic update: blob tasks (when still used) + version together.
        # A future lifecycle version must not resurrect an empty legacy blob
        # after this session has moved to row storage.
        if not row_backed:
            state["tasks"] = tasks
        state["schema_version"] = self.SCHEMA_VERSION

    def _prepare_task_storage(self, state):
        """Return the row repository after one atomic blob-to-row conversion.

        The enclosing ``session_state`` context already owns SQLite's
        connection and write transaction. Repository calls are nested RAII
        scopes, so copying all rows, removing the blob, and recording the
        marker either commit together or roll back together.
        """
        self._migrate_if_needed(state)
        repository = get_session_manager().task_repository()
        if repository is None or state.get("task_rows_migrated") is True:
            return repository

        blob_tasks = state.get("tasks", {})
        if not isinstance(blob_tasks, dict):
            raise TypeError("task lifecycle field 'tasks' must be a mapping")
        row_tasks = repository.list_tasks(self.global_key, self._state_lock_timeout)
        if row_tasks and blob_tasks and row_tasks != blob_tasks:
            raise RuntimeError("task state exists in both legacy blob and SQLite rows but differs; refusing to choose one and discard the other")
        if not row_tasks:
            for task_id, record in blob_tasks.items():
                self._persist_task_record(
                    repository,
                    str(task_id),
                    MISSING,
                    record,
                    event_source="legacy-migration",
                )
        if "tasks" in state:
            del state["tasks"]
        state["task_rows_migrated"] = True
        return repository

    def _event_source_identity(self) -> str:
        """Stable identity for retries of one hook, unique for standalone calls."""
        if self.ctx is None:
            return f"standalone:{uuid.uuid4().hex}"
        transcript = object.__getattribute__(self.ctx, "_session_transcript")
        latest = self.ctx.transcript.latest_assistant_message() if transcript else None
        latest_identity = latest[0] if latest else f"messages:{len(transcript)}"
        tool_input = json.dumps(
            self.ctx.tool_input,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(tool_input.encode("utf-8")).hexdigest()
        return f"{self.ctx.event}:{self.ctx.tool_name}:{latest_identity}:{len(transcript)}:{digest}"

    def _persist_task_record(
        self,
        repository,
        task_id: str,
        original,
        updated: Dict,
        *,
        event_source: str | None = None,
    ) -> None:
        """Store one bounded task row and every newly appended output event."""
        previous_outputs = list(original.get("tool_outputs", [])) if original is not MISSING else []
        candidate_outputs = list(updated.get("tool_outputs", []))
        if candidate_outputs[: len(previous_outputs)] == previous_outputs:
            appended = candidate_outputs[len(previous_outputs) :]
            recent = previous_outputs[-self.TASK_OUTPUT_RECENT_LIMIT :]
        else:
            # A bulk synchronizer replaced the compatibility view rather than
            # appending to it. Retain the bounded view; only true append
            # operations become immutable output events.
            appended = []
            recent = candidate_outputs[-self.TASK_OUTPUT_RECENT_LIMIT :]

        record = copy.deepcopy(updated)
        record["tool_outputs"] = list(recent)
        # Events reference the task row. A new task is staged first; the outer
        # lifecycle transaction still makes the preliminary row, events, and
        # final row one commit.
        if original is MISSING:
            repository.put_task(self.global_key, task_id, record, self._state_lock_timeout)

        source = event_source or self._event_source_identity()
        created_at = float(updated.get("updated_at", time.time()))
        for ordinal, output in enumerate(appended):
            identity = hashlib.sha256(f"{source}\0{self.global_key}\0{task_id}\0{ordinal}\0{output}".encode("utf-8")).hexdigest()
            inserted = repository.append_event(
                self.global_key,
                task_id,
                event_id=f"output-{identity}",
                idempotency_key=f"task-output:{identity}",
                event_type="tool_output",
                payload={"output": output},
                created_at=created_at + ordinal * 1e-6,
                requires_projection=False,
                timeout=self._state_lock_timeout,
            )
            if inserted:
                recent.append(output)
                recent = recent[-self.TASK_OUTPUT_RECENT_LIMIT :]

        record["tool_outputs"] = recent
        repository.put_task(self.global_key, task_id, record, self._state_lock_timeout)

    def _atomic_update_task(self, task_id: str, updater: Callable[[Dict], None]) -> bool:
        """Update at most one task without loading any unrelated task rows."""
        with self._session_state() as state:
            repository = self._prepare_task_storage(state)
            if repository is None:
                tasks = state.get("tasks", {})
                existed = task_id in tasks
                updater(tasks)
                state["tasks"] = tasks
                return existed or task_id in tasks

            current = repository.get_task(self.global_key, task_id, self._state_lock_timeout)
            original = copy.deepcopy(current) if current is not MISSING else MISSING
            tasks = {} if current is MISSING else {task_id: current}
            existed = current is not MISSING
            updater(tasks)
            updated = tasks.get(task_id, MISSING)
            if updated is MISSING:
                if existed:
                    repository.delete_task(self.global_key, task_id, self._state_lock_timeout)
            elif original is MISSING or updated != original:
                self._persist_task_record(repository, task_id, original, updated)
            return existed or updated is not MISSING

    @property
    def tasks(self) -> Dict[str, Dict]:
        """Get tasks dict aggregated from internal store and Conductor (Gemini)."""
        with self._session_state() as state:
            repository = self._prepare_task_storage(state)
            tasks = repository.list_tasks(self.global_key, self._state_lock_timeout) if repository is not None else dict(state.get("tasks", {}))

        # Superset Capability: Aggregation with Conductor (Gemini-native)
        # If in Gemini session and Conductor plan exists, parse and merge its tasks.
        # This ensures /ar:status and /ar:tasks show Conductor tasks.
        if platform_for(self._cli_type).aggregates_conductor_tasks:
            try:
                conductor_dir = Path.cwd() / "conductor" / "tracks"
                if conductor_dir.is_dir():
                    conductor_tasks = self._parse_conductor_tasks(conductor_dir)
                    # Merge: internal tasks take precedence for status updates,
                    # but Conductor tasks are added if not present.
                    for tid, task in conductor_tasks.items():
                        if tid not in tasks:
                            tasks[tid] = task
            except Exception as e:
                logger.debug(f"Failed to aggregate Conductor tasks: {e}")

        return tasks

    def _parse_conductor_tasks(self, tracks_dir: Path) -> Dict[str, Dict]:
        """Parse tasks from latest Conductor plan.md."""
        tasks = {}
        try:
            # Find latest modified track directory
            tracks = [d for d in tracks_dir.iterdir() if d.is_dir()]
            if not tracks:
                return {}
            latest_track = max(tracks, key=lambda d: d.stat().st_mtime)
            plan_file = latest_track / "plan.md"
            if not plan_file.exists():
                return {}

            content = plan_file.read_text(encoding="utf-8")
            # Simple regex parser for markdown task lists: - [ ] Task name
            # Maps to minimal internal Task schema
            task_idx = 1
            for line in content.splitlines():
                match = re.match(r"^\s*-\s*\[([ xX])\]\s*(.*)", line)
                if match:
                    status_char = match.group(1).lower()
                    subject = match.group(2).strip()
                    tid = f"c{task_idx}"  # Conductor-prefixed ID
                    tasks[tid] = {
                        "id": tid,
                        "subject": subject,
                        "status": "completed" if status_char == "x" else "pending",
                        "created_at": latest_track.stat().st_mtime,
                        "updated_at": latest_track.stat().st_mtime,
                        "metadata": {"source": "conductor", "track": latest_track.name},
                    }
                    task_idx += 1
        except Exception:
            pass
        return tasks

    def atomic_update_tasks(self, updater: Callable[[Dict], None]) -> None:
        """Bulk compatibility path; prefer ``_atomic_update_task`` on hot paths."""
        with self._session_state() as state:
            repository = self._prepare_task_storage(state)
            tasks = repository.list_tasks(self.global_key, self._state_lock_timeout) if repository is not None else state.get("tasks", {})
            original = copy.deepcopy(tasks)
            updater(tasks)
            if repository is None:
                state["tasks"] = tasks
                return
            for task_id in original.keys() - tasks.keys():
                repository.delete_task(self.global_key, task_id, self._state_lock_timeout)
            for task_id, record in tasks.items():
                if original.get(task_id, MISSING) != record:
                    self._persist_task_record(
                        repository,
                        task_id,
                        original.get(task_id, MISSING),
                        record,
                    )

    def replace_task_projection(
        self,
        records: List[Dict],
        *,
        source: str,
        inherited_from: "str | None" = None,
        inherited_ids: Iterable[str] = (),
    ) -> None:
        """Atomically replace only rows owned by one native task source.

        ``inherited_from`` is set when this conversation is a fork, and
        ``inherited_ids`` are the ids that crossed the fork boundary
        (core.CONVERSATION_INHERITED_TASK_IDS). Only those rows are marked as
        inherited from that conversation, so the Stop gate lists them as
        waiting instead of blocking on them, unless this conversation already
        took one on by updating it (``inherited_claimed``). A task the fork
        created itself is never marked.
        """
        inherited_ids = {str(task_id) for task_id in inherited_ids}
        if not source:
            raise ValueError("Task projection source must be non-empty")
        normalized: Dict[str, Dict] = {}
        now = time.time()
        for raw in records:
            if not isinstance(raw, dict):
                raise TypeError("Pi task projection entries must be mappings")
            task_id = str(raw.get("id") or "").strip()
            if not task_id:
                raise ValueError("Pi task projection entry is missing id")
            status = str(raw.get("status") or "pending")
            task_status_policy(status)
            metadata = dict(raw.get("metadata") or {})
            metadata["source"] = source
            normalized[task_id] = {
                "id": task_id,
                "subject": str(raw.get("subject") or ""),
                "description": str(raw.get("description") or ""),
                "activeForm": str(raw.get("activeForm") or ""),
                "status": status,
                "created_at": float(raw.get("created_at") or now),
                "updated_at": float(raw.get("updated_at") or now),
                "session_id": self.session_id,
                "owner": raw.get("owner"),
                "blockedBy": [str(value) for value in raw.get("blockedBy") or []],
                "blocks": [str(value) for value in raw.get("blocks") or []],
                "metadata": metadata,
                "tool_outputs": list(raw.get("tool_outputs") or []),
            }

        def replace(tasks):
            # A status only autorun sets (awaiting-user, delegated, ...) was
            # recorded after the receipt it came from, so an older receipt
            # does not undo it; a native update newer than it does.
            for task_id, record in normalized.items():
                stored = tasks.get(task_id)
                if (
                    isinstance(stored, dict)
                    and stored.get("status") in AUTORUN_OWNED_TASK_STATUSES
                    and float(stored.get("updated_at") or 0) > record["updated_at"]
                ):
                    normalized[task_id] = copy.deepcopy(stored)
            claimed = set()
            for task_id in list(tasks):
                metadata = tasks[task_id].get("metadata", {})
                if metadata.get("inherited_claimed"):
                    claimed.add(task_id)
                if metadata.get("source") == source:
                    tasks.pop(task_id)
            for task_id, record in normalized.items():
                if task_id in claimed:
                    record["metadata"]["inherited_claimed"] = True
                elif inherited_from and task_id in inherited_ids:
                    record["metadata"]["inherited_from"] = inherited_from
            tasks.update(normalized)

        self.atomic_update_tasks(replace)

    @property
    def plan_tasks_map(self) -> Dict[str, List[str]]:
        """Get plan->tasks mapping."""
        with self._session_state() as state:
            return dict(state.get("plan_tasks_map", {}))

    def atomic_update_plan_tasks_map(self, updater: Callable[[Dict], None]) -> None:
        """Atomically update plan_tasks_map."""
        with self._session_state() as state:
            plan_map = state.get("plan_tasks_map", {})
            updater(plan_map)
            state["plan_tasks_map"] = plan_map

    @property
    def session_metadata(self) -> Dict:
        """Get session metadata."""
        with self._session_state() as state:
            if "session_metadata" not in state:
                state["session_metadata"] = {
                    "session_id": self.session_id,
                    "created_at": time.time(),
                    "last_activity": time.time(),
                    "stop_block_count": 0,
                }
            return dict(state["session_metadata"])

    def atomic_update_metadata(self, updater: Callable[[Dict], None]) -> None:
        """Atomically update session_metadata without persisting semantic no-ops."""
        with self._session_state() as state:
            metadata = copy.deepcopy(
                state.get(
                    "session_metadata",
                    {
                        "session_id": self.session_id,
                        "created_at": time.time(),
                        "last_activity": time.time(),
                        "stop_block_count": 0,
                    },
                )
            )
            original = copy.deepcopy(metadata)
            updater(metadata)
            if metadata != original:
                state["session_metadata"] = metadata

    # === Logging (Simple append - DRY) ===

    def log_event(self, event_type: str, task_id: str, subject: str, status: str, extra: Dict = None) -> None:
        """Log event to audit file (simple append - follows plan_export.py pattern)."""
        if not self.config.debug_logging:
            return

        try:
            self.audit_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.audit_log, "a", encoding="utf-8") as f:
                timestamp = datetime.now().isoformat()
                extra_str = f" {json.dumps(extra)}" if extra else ""
                f.write(f"{timestamp} [{event_type}] Task #{task_id} ({status}): {subject}{extra_str}\n")
        except IOError as e:
            logger.warning(f"Failed to log task event: {e}")

    # === Task Operations (DRY - reusable methods) ===

    def get_unfinished_tasks(self) -> List[Dict]:
        """Return tracked work that was not completed, deleted, or ignored.

        This is independent of whether a row blocks Stop. On SQLite the
        repository narrows through ``tasks_retention(session, prunable)`` before
        decoding payloads, so completed history does not make this hot read grow.
        """
        with self._session_state() as state:
            repository = self._prepare_task_storage(state)
            if repository is not None:
                return repository.list_excluding_statuses(
                    self.global_key,
                    FINISHED_TASK_STATUSES,
                    self._state_lock_timeout,
                )
            tasks = dict(state.get("tasks", {}))
        return [
            task
            for task in tasks.values()
            if task_status_is_unfinished(task["status"])
        ]

    def get_incomplete_tasks(self, exclude_blocking: bool = True) -> List[Dict]:
        """Get incomplete tasks.

        Args:
            exclude_blocking: If True, exclude paused/ignored (don't block stop).
                             If False, only exclude completed/deleted.
        """
        with self._session_state() as state:
            repository = self._prepare_task_storage(state)
            if repository is not None:
                if exclude_blocking:
                    return repository.list_incomplete(self.global_key, timeout=self._state_lock_timeout)
                return repository.list_excluding_statuses(
                    self.global_key,
                    self.COMPLETED_STATUSES,
                    self._state_lock_timeout,
                )
            tasks = dict(state.get("tasks", {}))
        if exclude_blocking:
            return [t for t in tasks.values() if t["status"] not in self.NON_BLOCKING_STATUSES]
        else:
            return [t for t in tasks.values() if t["status"] not in self.COMPLETED_STATUSES]

    def task_staleness_state(self, *, summary_limit: int = 5) -> tuple[List[Dict], str]:
        """Return unfinished rows and a summary when none is actionable now.

        The one indexed snapshot excludes finished history. ``_stop_view`` then
        applies the Stop gate's existing dependency/waiting policy in memory,
        while paused rows are added explicitly because they are parked rather
        than waiting on another actor.
        """
        tasks, _metadata, unknown = self._open_task_snapshot()
        unfinished = list(tasks.values())
        actionable, waiting = self._stop_view(tasks, unknown)
        if actionable or not unfinished:
            return unfinished, ""
        paused = [
            f"⏸ Paused: #{task['id']} {task.get('subject', '')}"
            for task in sorted(unfinished, key=self._creation_order_key)
            if task.get("status") == "paused"
        ]
        all_lines = [*waiting, *paused]
        shown = all_lines[:summary_limit]
        overflow = len(all_lines) - len(shown)
        if overflow > 0:
            shown.append(f"and {overflow} more")
        return unfinished, "; ".join(shown)

    @staticmethod
    def _creation_order_key(task: Dict) -> tuple:
        """Sort key: creation time, then the id read numerically when it is one.

        Ids are stored as strings, and the SQLite repository lists them in string
        order, so without this "10" sorts before "9". Sequential ids from Claude
        Code and the daemon-minted Pi-family ids are plain integers; ids from
        other sources ("plan-2", OpenCode todo ids) sort after them by text.
        """
        raw = str(task.get("id", ""))
        return (
            float(task.get("created_at") or 0),
            (0, int(raw)) if raw.isdigit() else (1, raw),
        )

    def next_task_id(self) -> str:
        """Mint the session's next task id under the session lock.

        The id is one above both the persisted sequence and every numeric id
        recorded, so a number is never reused: deleted and completed tasks stay
        in the snapshot, a mint whose create is never recorded still consumes
        its number, and two mints racing from parallel tool calls each get
        their own. Non-numeric ids ("plan-2", OpenCode todo ids) do not move
        the sequence. O(T) over the session task count, one locked write.

        This is the one owner of id shape for adapters whose task tools autorun
        implements (the Pi-family extension asks through ``task_next_id_v1``);
        harnesses with native task tools mint their own ids.
        """
        with self._session_state() as state:
            repository = self._prepare_task_storage(state)
            tasks = (
                repository.list_tasks(self.global_key, self._state_lock_timeout)
                if repository is not None
                else dict(state.get("tasks", {}))
            )
            numeric = [int(task_id) for task_id in tasks if str(task_id).isdigit()]
            floor = state.get("task_id_sequence", 0)
            floor = int(floor) if isinstance(floor, (int, float)) or str(floor).isdigit() else 0
            minted = max([floor, *numeric]) + 1
            state["task_id_sequence"] = minted
        return str(minted)

    def _prioritize_task_snapshot(self, tasks: Dict[str, Dict]) -> List[Dict]:
        """Order blocking tasks from one authoritative task snapshot."""
        incomplete = sorted(
            (
                task for task in tasks.values()
                if task["status"] not in self.NON_BLOCKING_STATUSES
            ),
            key=self._creation_order_key,
        )
        ready = []
        waiting = []
        blocked = []

        for task in incomplete:
            blockers = task.get("blockedBy", [])
            if not blockers:
                ready.append(task)
            elif all(
                tasks.get(blocker_id, {}).get("status") in FINISHED_TASK_STATUSES
                for blocker_id in blockers
            ):
                waiting.append(task)
            else:
                blocked.append(task)

        return ready + waiting + blocked

    def get_prioritized_tasks(self) -> List[Dict]:
        """Get blocking tasks in dependency-aware priority order."""
        return self._prioritize_task_snapshot(self.tasks)

    def task_projection(self, *, limit: int = 100) -> Dict[str, object]:
        """Return one bounded, dependency-aware view from one task snapshot.

        Worst-case time is O(T log T) for terminal-history ordering and peak
        memory is O(T), where T is the session task count. Output is
        O(min(T, limit)); persisted task history is never mutated or pruned.
        """
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("Task projection limit must be a positive integer")
        tasks = self.tasks
        prioritized = self._prioritize_task_snapshot(tasks)
        prioritized_ids = {str(task.get("id", "")) for task in prioritized}
        history = sorted(
            (
                task for task in tasks.values()
                if str(task.get("id", "")) not in prioritized_ids
            ),
            key=self._creation_order_key,
        )
        rows = [*prioritized, *history]
        return {
            "tasks": rows[:limit],
            "total": len(rows),
            "truncated": len(rows) > limit,
        }

    def create_task(self, task_id: str, input_data: Dict, result: str) -> None:
        """Create task with full metadata (handles duplicates)."""

        def updater(tasks):
            # Deduplication check (Problem 5 solution)
            if task_id in tasks:
                self.log_event("WARNING", task_id, "Duplicate task creation ignored", "duplicate")
                return

            # Superset Compatibility: support both Claude (subject) and Gemini (title)
            subject = input_data.get("subject") or input_data.get("title") or ""

            tasks[task_id] = {
                # Core identification
                "id": task_id,
                "subject": subject,
                "description": input_data.get("description", ""),
                "activeForm": input_data.get("activeForm", ""),
                # Status (explicit field)
                "status": "pending",
                # Timestamps
                "created_at": time.time(),
                "updated_at": time.time(),
                # Session tracking
                "session_id": self.session_id,
                # Ownership and dependencies (initialized empty, updated via TaskUpdate)
                "owner": None,
                "blockedBy": [],
                "blocks": [],
                # Custom tracking
                "metadata": input_data.get("metadata", {}),
                # Audit trail
                "tool_outputs": [result],
            }

        self._atomic_update_task(task_id, updater)
        self.log_event("CREATE", task_id, input_data.get("subject", ""), "pending")

    def update_task(
        self,
        task_id: str,
        updates: Dict,
        result: str,
        *,
        _tasks: Dict[str, Dict] | None = None,
        _reset_metadata: bool = True,
    ) -> Optional[str]:
        """Update task metadata (handles all fields).

        Returns:
            'ghost_skip' if ghost task protection triggered, None otherwise.
        """
        ghost_state = [False]  # Mutable container -- avoids nonlocal in updater closure

        def updater(tasks):
            # Get or create task entry
            if task_id not in tasks:
                # Task created before tracking started - initialize as ignored
                # so ghost tasks don't block stopping. Ghost tasks can only
                # transition to terminal statuses (completed/deleted/ignored),
                # never to in_progress/pending (which would block stopping).
                tasks[task_id] = {
                    "id": task_id,
                    "subject": "(unknown - created before tracking)",
                    "description": "",
                    "activeForm": "",
                    "status": "ignored",
                    "created_at": time.time(),
                    "updated_at": time.time(),
                    "session_id": self.session_id,
                    "owner": None,
                    "blockedBy": [],
                    "blocks": [],
                    "metadata": {"ghost_task": True},
                    "tool_outputs": [],
                }

            task = tasks[task_id]

            # Update metadata fields (merge semantics)
            # Superset Compatibility: handle 'title' (Gemini) mapping to 'subject'
            for key in ["subject", "title", "description", "activeForm", "owner"]:
                if key in updates:
                    target_key = "subject" if key == "title" else key
                    task[target_key] = updates[key]

            if "addBlockedBy" in updates:
                task["blockedBy"].extend(updates["addBlockedBy"])
            if "addBlocks" in updates:
                task["blocks"].extend(updates["addBlocks"])
            if "metadata" in updates:
                # Merge metadata (null values delete keys)
                for k, v in updates["metadata"].items():
                    if v is None:
                        task["metadata"].pop(k, None)
                    else:
                        task["metadata"][k] = v

            # This conversation's own update of a task it inherited from the
            # conversation it was forked from takes the task on: it blocks
            # from now on, and a later reprojection keeps it taken.
            task_metadata = task.setdefault("metadata", {})
            if task_metadata.pop("inherited_from", None):
                task_metadata["inherited_claimed"] = True

            # Status transition (with ghost task protection)
            if "status" in updates:
                old_status = task["status"]
                if old_status == AWAITING_USER_STATUS:
                    # The agent resumed or settled it: no longer waiting on the user.
                    task_metadata.pop("awaiting_user", None)
                new_status = updates["status"]
                task_status_policy(new_status)

                # CRITICAL GHOST TASK PROTECTION:
                # Ghost tasks are created when AI calls TaskUpdate(id=X) for unknown X
                # (task created before daemon tracking started). In v1, these could
                # transition to in_progress/pending, then if the session ended before
                # TaskUpdate(id=X, status=completed), they'd block Stop hooks forever.
                #
                # V2 FIX: Ghost tasks can ONLY accept statuses that do not block
                # Stop (STATUS_POLICY blocks_stop=False: completed, deleted,
                # ignored, delegated, paused). Requests for a blocking status are
                # logged as GHOST_SKIP but status stays "ignored". This prevents
                # permanent blocking.
                #
                # WHY THIS WORKS:
                # - get_incomplete_tasks() filters by NON_BLOCKING_STATUSES (includes "ignored")
                # - Ghost tasks with "ignored" never appear in incomplete list
                # - Stop hook only blocks if incomplete tasks exist
                # - Migration (v1→v2) automatically fixes old ghost tasks on first access
                is_ghost = task.get("metadata", {}).get("ghost_task", False)

                if is_ghost and task_status_policy(new_status).blocks_stop:
                    # Ghost task protection triggered - log for debugging
                    logger.warning(
                        "GHOST_SKIP: task_id=%s requested status=%s but ghost tasks can only transition to non-blocking statuses (%s). Keeping 'ignored'.",
                        task_id,
                        new_status,
                        ", ".join(sorted(NON_BLOCKING_TASK_STATUSES)),
                    )
                    self.log_event(
                        "GHOST_SKIP",
                        task_id,
                        task["subject"],
                        new_status,
                        {
                            "old_status": old_status,
                            "reason": "ghost task cannot become blocking",
                            "requested_status": new_status,
                            "maintained_status": "ignored",
                        },
                    )
                    # Status stays "ignored" - do NOT update to blocking status
                    ghost_state[0] = True
                else:
                    # Normal status transition (non-ghost or terminal status)
                    task["status"] = new_status

                    event_type = {"completed": "COMPLETE", "in_progress": "START", "deleted": "DELETE", "paused": "PAUSE", "ignored": "IGNORE"}.get(
                        new_status, "UPDATE"
                    )

                    self.log_event(event_type, task_id, task["subject"], new_status, {"old_status": old_status})

            task["updated_at"] = time.time()
            task["tool_outputs"].append(result)

        if _tasks is None:
            self._atomic_update_task(task_id, updater)
        else:
            updater(_tasks)

        # Fix 4: Reset stop_block_count when task reaches terminal status.
        # Placed OUTSIDE updater closure to avoid nonlocal scoping issues.
        if _reset_metadata and "status" in updates and updates["status"] in self.NON_BLOCKING_STATUSES and not ghost_state[0]:
            self.atomic_update_metadata(_reset_stop_block_sequence)

        return "ghost_skip" if ghost_state[0] else None

    def update_tasks(self, updates: List[Dict], result: str) -> Optional[str]:
        """Atomically update multiple tasks with one shared tool result."""
        if not isinstance(updates, list) or not updates:
            raise ValueError("taskUpdates must be a non-empty list")

        normalized: list[tuple[str, Dict]] = []
        seen: set[str] = set()
        for raw in updates:
            if not isinstance(raw, dict):
                raise TypeError("taskUpdates entries must be mappings")
            task_id = str(raw.get("taskId") or raw.get("id") or "").strip()
            if not task_id:
                raise ValueError("taskUpdates entries require taskId")
            if task_id in seen:
                raise ValueError(f"taskUpdates contains duplicate taskId: {task_id}")
            seen.add(task_id)
            normalized.append((task_id, dict(raw)))

        ghost_skipped = False
        terminal_update = False

        def updater(tasks):
            nonlocal ghost_skipped, terminal_update
            for task_id, task_update in normalized:
                result_code = self.update_task(
                    task_id,
                    task_update,
                    result,
                    _tasks=tasks,
                    _reset_metadata=False,
                )
                ghost_skipped = ghost_skipped or result_code == "ghost_skip"
                terminal_update = terminal_update or (
                    task_update.get("status") in self.NON_BLOCKING_STATUSES
                    and result_code != "ghost_skip"
                )

        self.atomic_update_tasks(updater)
        if terminal_update:
            self.atomic_update_metadata(_reset_stop_block_sequence)
        return "ghost_skip" if ghost_skipped else None

    def ignore_task(self, task_id: str, reason: str = "User ignored") -> bool:
        """Mark task as ignored (user override to unblock stop).

        Use case: Task is stuck, no longer relevant, or paused indefinitely.
        User can explicitly ignore it to allow AI to stop without completing it.

        Args:
            task_id: Task ID to ignore
            reason: Reason for ignoring

        Returns:
            True if task was ignored, False if task not found
        """

        def updater(tasks):
            if task_id not in tasks:
                return

            task = tasks[task_id]
            old_status = task["status"]
            task["status"] = "ignored"
            task["updated_at"] = time.time()
            task["metadata"]["ignore_reason"] = reason
            task["tool_outputs"].append(f"User ignored task: {reason}")

            self.log_event("IGNORE", task_id, task["subject"], "ignored", {"old_status": old_status, "reason": reason})

        return self._atomic_update_task(task_id, updater)

    def prune_old_tasks(self) -> int:
        """Prune truly terminal tasks older than TTL.

        CRITICAL: Only prunes PRUNABLE_STATUSES (completed, deleted, ignored).
        NEVER prunes "paused" tasks - users pause for later resume, pruning would
        violate that contract and lose their work intent.

        Prunable statuses:
        - completed: Work finished, safe to remove after TTL
        - deleted: User explicitly removed, safe to purge after TTL
        - ignored: Ghost tasks we can't track, safe to clean after TTL

        NOT prunable:
        - paused: User explicitly paused for later resume - must preserve
        - in_progress: Active work - protected by different mechanism
        - pending: Queued work - protected by different mechanism

        Returns:
            Number of tasks pruned
        """
        ttl_seconds = self.config.task_ttl_days * _SECONDS_PER_DAY
        now = time.time()
        pruned_count = 0

        with self._session_state() as state:
            repository = self._prepare_task_storage(state)
            if repository is not None:
                expired = repository.list_terminal_before(
                    self.global_key,
                    self.PRUNABLE_STATUSES,
                    now - ttl_seconds,
                    self._state_lock_timeout,
                )
                for task in expired:
                    repository.delete_task(
                        self.global_key,
                        str(task["id"]),
                        self._state_lock_timeout,
                    )
                pruned_count = len(expired)

        def updater(tasks):
            nonlocal pruned_count
            for task_id in list(tasks.keys()):
                task = tasks[task_id]
                # Only prune truly terminal statuses (NOT paused - users may resume)
                if task["status"] in self.PRUNABLE_STATUSES:
                    age = now - task["updated_at"]
                    if age > ttl_seconds:
                        del tasks[task_id]
                        pruned_count += 1

        if repository is None:
            self.atomic_update_tasks(updater)

        if pruned_count > 0:
            self.log_event("PRUNE", "session", f"Pruned {pruned_count} old completed tasks", "maintenance")

        return pruned_count

    # === Plan Integration ===

    def link_task_to_plan(self, task_id: str, plan_key: str) -> None:
        """Link task to plan for context injection."""

        def updater(plan_map):
            if plan_key not in plan_map:
                plan_map[plan_key] = []
            if task_id not in plan_map[plan_key]:
                plan_map[plan_key].append(task_id)

        self.atomic_update_plan_tasks_map(updater)

    def get_plan_tasks(self, plan_key: str, incomplete_only: bool = True) -> List[Dict]:
        """Get tasks linked to plan.

        Args:
            plan_key: Plan identifier
            incomplete_only: If True, only return incomplete tasks

        Returns:
            List of task dicts
        """
        plan_map = self.plan_tasks_map
        task_ids = plan_map.get(plan_key, [])

        all_tasks = self.tasks
        plan_tasks = [all_tasks[tid] for tid in task_ids if tid in all_tasks]

        if incomplete_only:
            return [t for t in plan_tasks if t["status"] not in FINISHED_TASK_STATUSES]

        return plan_tasks

    # === Hook Handlers (called from register_hooks) ===

    def handle_task_create(self, ctx: EventContext) -> None:
        """Handle TaskCreate tool (called from PostToolUse hook).

        Enhanced from stash@{1} with:
        - Multiple regex fallback patterns (Problem 3 solution)
        - Full TaskState schema (all fields populated)
        - Plan linkage (if active plan)
        - Deduplication check
        """
        # Extract task ID: try structured dict first (Claude Code tool_response),
        # then regex fallback (Gemini CLI string responses, legacy formats).
        raw_result = ctx.tool_result
        task_id = None

        # Primary: extract task ID from dict (Claude Code tool_response format).
        # Observed formats (from daemon log evidence 2026-03-23):
        #   {"task": {"id": "57", "subject": "..."}}  — TaskCreate
        #   {"success": true, "taskId": "18", ...}    — TaskUpdate
        if isinstance(raw_result, dict):
            nested = raw_result.get("task", {})
            if isinstance(nested, dict):
                task_id = str(nested.get("id") or "")
            if not task_id:
                task_id = str(raw_result.get("taskId") or raw_result.get("id") or "")

        # Fallback: regex on string/JSON representation
        if not task_id:
            # Use raw_result directly if it's already a string (e.g. Gemini CLI or test mocks),
            # otherwise fall back to tool_result_str (JSON-serialized Claude Code response).
            if isinstance(raw_result, str):
                result_text = raw_result
            else:
                result_text = ctx.tool_result_str
            patterns = [
                r'"taskId"\s*:\s*"([^"]+)"',  # JSON taskId field
                r"Task #?([a-zA-Z0-9_\-\.]+)\s+created successfully",
                r"Created task #?([a-zA-Z0-9_\-\.]+)\s+successfully",
                r"Created task ([a-zA-Z0-9_\-\.]+):",  # Gemini CLI tracker_create_task format
                r"Task ([a-zA-Z0-9_\-\.]+)\s+created",
                r"#([a-zA-Z0-9_\-\.]+)",  # Last resort
            ]
            for pattern in patterns:
                match = re.search(pattern, result_text, re.IGNORECASE)
                if match:
                    task_id = match.group(1)
                    break

        if not task_id:
            self.log_event("ERROR", "unknown", "Failed to extract task ID", "error")
            return  # Fail-open

        # Create task with full metadata. Pi task receipts are tagged at the
        # Python authority boundary so the adapter cannot impersonate another
        # source or grow its own transition policy.
        input_data = dict(ctx.tool_input)
        task_source = platform_for(self._cli_type).task_record_source
        if task_source:
            metadata = dict(input_data.get("metadata") or {})
            metadata["source"] = task_source
            input_data["metadata"] = metadata
        # Use raw_result directly if string (Gemini/test mocks), else tool_result_str
        result_str = raw_result if isinstance(raw_result, str) else ctx.tool_result_str
        self.create_task(task_id, input_data, result_str)

        # If active plan, link this task to the plan for context injection
        if hasattr(ctx, "plan_active") and ctx.plan_active:
            plan_key = getattr(ctx, "plan_arguments", "")
            if plan_key:
                self.link_task_to_plan(task_id, plan_key)

    def handle_task_update(self, ctx: EventContext) -> Optional[str]:
        """Handle TaskUpdate tool (called from PostToolUse hook).

        Tracks status transitions AND updates full metadata.

        Returns:
            'ghost_skip' if ghost task protection triggered, None otherwise.
        """
        # Attach native provenance at the Python authority boundary. This also
        # tags ghost-protected unknown updates so branch reprojection can remove
        # them when their Pi receipt is no longer on the active branch.
        task_source = platform_for(self._cli_type).task_record_source
        # Update task with all metadata
        # Use raw tool_result directly if string (Gemini/test mocks), else tool_result_str
        raw_result = ctx.tool_result
        result_str = raw_result if isinstance(raw_result, str) else ctx.tool_result_str

        if "taskUpdates" in ctx.tool_input and (
            "taskId" in ctx.tool_input or "id" in ctx.tool_input
        ):
            raise ValueError("TaskUpdate requires exactly one of taskId or taskUpdates")

        bulk_updates = ctx.tool_input.get("taskUpdates")
        if bulk_updates is not None:
            if not isinstance(bulk_updates, list) or not bulk_updates:
                raise ValueError("taskUpdates must be a non-empty list")
            updates = []
            for raw_update in bulk_updates:
                if not isinstance(raw_update, dict):
                    raise TypeError("taskUpdates entries must be mappings")
                update = dict(raw_update)
                if task_source:
                    metadata = dict(update.get("metadata") or {})
                    metadata["source"] = task_source
                    update["metadata"] = metadata
                updates.append(update)
            return self.update_tasks(updates, result_str)

        task_id = ctx.tool_input.get("taskId") or ctx.tool_input.get("id")
        if not task_id:
            return None  # Skip if no task ID

        updates = dict(ctx.tool_input)
        if task_source:
            metadata = dict(updates.get("metadata") or {})
            metadata["source"] = task_source
            updates["metadata"] = metadata
        return self.update_task(task_id, updates, result_str)

    def handle_bulk_todos(self, ctx: EventContext) -> None:
        """Handle WriteTodos tool (Gemini Planner).

        Replaces the current session's tasks with the provided todos list.
        """
        # Defensive check for tool_input structure (Pre-mortem fix)
        if not isinstance(ctx.tool_input, dict):
            logger.warning(f"handle_bulk_todos: tool_input is not a dict: {type(ctx.tool_input)}")
            return

        todos = ctx.tool_input.get("todos")
        if not isinstance(todos, list):
            logger.debug("handle_bulk_todos: no todos list found in input")
            return

        now = time.time()
        task_source = platform_for(self._cli_type).task_record_source or "planner"

        def updater(tasks):
            # Clear only previous bulk-sourced tasks for this session. The
            # daemon may also track explicit TaskCreate records in the same
            # session; a bulk refresh must not erase them.
            to_remove = [
                tid
                for tid, t in tasks.items()
                if (
                    t.get("session_id") == self.session_id
                    and t.get("metadata", {}).get("source") == task_source
                )
            ]
            previous = {tid: tasks.pop(tid) for tid in to_remove}

            # Explicit ids win; index fallbacks (Gemini todos carry none) skip
            # any id an explicit todo already claims, so two todos never fold
            # into one record.
            explicit_ids = {
                str(todo.get("id"))
                for todo in todos
                if isinstance(todo, dict) and todo.get("id")
            }
            next_index = 1
            for i, todo in enumerate(todos, 1):
                if not isinstance(todo, dict):
                    continue

                # OpenCode uses 'content'; Gemini uses 'description'.
                subject = (
                    todo.get("content")
                    or todo.get("description")
                    or todo.get("subject")
                    or f"Task {i}"
                )
                if todo.get("id"):
                    task_id = str(todo["id"])
                else:
                    while str(next_index) in explicit_ids or str(next_index) in tasks:
                        next_index += 1
                    task_id = str(next_index)
                    next_index += 1
                status = str(todo.get("status") or "pending").strip().lower()
                # Both spellings of the harness's "cancelled" mean deleted;
                # anything outside the shared status policy falls back to
                # pending rather than raising out of persistence and dropping
                # the whole sync (SQLite) or blocking Stop on a status no
                # policy knows (JSON).
                if status in ("cancelled", "canceled"):
                    status = "deleted"
                elif status not in STATUS_POLICY:
                    status = "pending"
                metadata = {"source": task_source}
                prior = previous.get(task_id) or {}
                if not todo.get("id") and prior.get("subject") != subject:
                    prior = {}
                if prior.get("status") in AUTORUN_OWNED_TASK_STATUSES and status not in COMPLETED_TASK_STATUSES:
                    status = prior["status"]
                    metadata = {**prior.get("metadata", {}), "source": task_source}
                tasks[task_id] = {
                    "id": task_id,
                    "subject": subject,
                    "description": "",
                    "activeForm": "",
                    "status": status,
                    "created_at": now,
                    "updated_at": now,
                    "session_id": self.session_id,
                    "owner": None,
                    "blockedBy": [],
                    "blocks": [],
                    "metadata": metadata,
                    "tool_outputs": [],
                }

        self.atomic_update_tasks(updater)
        self.log_event("BULK_CREATE", "multiple", f"Created {len(todos)} tasks from planner", "multiple")

    def handle_plan_checklist(self, ctx: EventContext) -> None:
        """Handle native plan/checklist tools such as Codex update_plan.

        update_plan is a current-state checklist, not an append-only task event.
        Sync only tasks previously sourced from this checklist so unrelated
        Claude/Gemini task records are not overwritten.
        """
        if not isinstance(ctx.tool_input, dict):
            logger.warning(f"handle_plan_checklist: tool_input is not a dict: {type(ctx.tool_input)}")
            return

        if "plan" not in ctx.tool_input:
            logger.debug("handle_plan_checklist: no plan field found in input")
            return
        plan = ctx.tool_input["plan"]
        if not isinstance(plan, list):
            logger.debug("handle_plan_checklist: plan field is not a list")
            return

        raw_result = ctx.tool_result
        result_str = raw_result if isinstance(raw_result, str) else ctx.tool_result_str
        result_snippet = result_str[:LOG_SNIPPET_MAX_LEN] if result_str else ""
        explanation = ctx.tool_input.get("explanation")
        now = time.time()
        valid_statuses = {"pending", "in_progress", "completed"}
        synced_count = 0
        removed_count = 0

        def updater(tasks):
            nonlocal synced_count, removed_count
            current_ids: set[str] = set()

            for i, item in enumerate(plan, 1):
                if not isinstance(item, dict):
                    continue

                subject = str(item.get("step") or item.get("subject") or item.get("title") or "").strip()
                if not subject:
                    continue

                status = str(item.get("status") or "pending").strip().lower()
                if status not in valid_statuses:
                    status = "pending"

                task_id = f"plan-{i}"
                current_ids.add(task_id)
                existing = tasks.get(task_id, {})
                if (
                    existing.get("subject") != subject
                    or existing.get("session_id") != self.session_id
                    or existing.get("metadata", {}).get("source") != "plan_checklist"
                ):
                    existing = {}
                if existing.get("status") in AUTORUN_OWNED_TASK_STATUSES and status not in COMPLETED_TASK_STATUSES:
                    status = existing["status"]
                metadata = dict(existing.get("metadata", {}))
                metadata.update(
                    {
                        "source": "plan_checklist",
                        "platform": ctx.cli_type,
                        "position": i,
                        "tool_name": ctx.tool_name,
                    }
                )
                if explanation:
                    metadata["explanation"] = str(explanation)

                outputs = list(existing.get("tool_outputs", []))
                if result_snippet:
                    outputs.append(result_snippet)

                tasks[task_id] = {
                    "id": task_id,
                    "subject": subject,
                    "description": str(item.get("description") or ""),
                    "activeForm": existing.get("activeForm", ""),
                    "status": status,
                    "created_at": existing.get("created_at", now),
                    "updated_at": now,
                    "session_id": self.session_id,
                    "owner": existing.get("owner"),
                    "blockedBy": list(existing.get("blockedBy", [])),
                    "blocks": list(existing.get("blocks", [])),
                    "metadata": metadata,
                    "tool_outputs": outputs,
                }
                synced_count += 1

            for task_id, task in list(tasks.items()):
                metadata = task.get("metadata", {})
                if (
                    metadata.get("source") == "plan_checklist"
                    and task.get("session_id") == self.session_id
                    and task_id not in current_ids
                    and task.get("status") not in self.NON_BLOCKING_STATUSES
                ):
                    task["status"] = "deleted"
                    task["updated_at"] = now
                    task.setdefault("tool_outputs", []).append("Removed from native plan checklist")
                    removed_count += 1

        self.atomic_update_tasks(updater)
        self.log_event(
            "PLAN_SYNC",
            "multiple",
            f"Synced {synced_count} native checklist task(s); removed {removed_count}",
            "multiple",
        )

    def _claim_session_start_injection(
        self,
        ctx: EventContext,
        injection: str,
    ) -> bool:
        """Claim one source+content injection under the task metadata lock."""
        digest = hashlib.sha256(injection.encode()).hexdigest()[:_SESSION_START_CLAIM_DIGEST_HEX_CHARS]
        claim_key = f"{getattr(ctx, 'source', '') or ''}:{digest}"
        claimed = False

        def claim(metadata):
            nonlocal claimed
            if metadata.get("last_session_start_injection") == claim_key:
                return
            metadata["last_session_start_injection"] = claim_key
            claimed = True

        self.atomic_update_metadata(claim)
        return claimed

    def handle_session_start(self, ctx: EventContext) -> Optional[Dict]:
        """Handle SessionStart (return injection if incomplete tasks).

        CRITICAL LIFECYCLE: Does NOT auto-prune tasks.
        Completed tasks are HISTORICAL EVIDENCE, not garbage. Users expect
        persistent task history in sessions. Automatic deletion without consent
        violates this expectation and loses valuable context.

        Pruning is manual-only:
        - Via CLI: autorun --task-clear
        - Via GC: TaskLifecycle.cli_gc()
        - User controls when to delete history

        Strategy:
        1. Find incomplete tasks (status = in_progress or pending)
        2. Inject resume prompt with prioritized task details
        3. Cap injection size (avoid overwhelming AI)

        REMOVED (lifecycle violation): Automatic pruning on SessionStart.
        Old behavior deleted completed tasks > 30 days on EVERY resume,
        losing user's work history without consent.
        """
        pause_injection = task_pause_guidance(ctx)
        if pause_injection is not None:
            if not self._claim_session_start_injection(ctx, pause_injection):
                return None
            return ctx.continue_running(pause_injection)

        if not task_enforcement_capability_available(
            ctx.cli_type,
            ctx.task_tool_evidence,
            TASK_UPDATE_CAPABILITY_ROLES,
        ):
            clear_task_staleness_enforcement(ctx)
            return None

        # A resumed harness is the moment a dead one is noticed: a delegation
        # whose recorded harness process is gone goes back to pending here.
        try:
            open_tasks, _metadata, _unknown = self._open_task_snapshot()
            self._settle_delegations(ctx, open_tasks)
        except Exception as exc:  # noqa: BLE001 - liveness must not break resume
            logger.warning("Could not check delegation liveness at SessionStart: %s", exc)

        # Read only unfinished rows. Whether a row blocks Stop is a separate
        # policy: parked and delegated work is retained and shown below without
        # being mistaken for an empty session.
        # REMOVED: self.prune_old_tasks() - manual pruning only
        unfinished = self.get_unfinished_tasks()
        if not unfinished:
            return None
        unfinished_by_id = {str(task["id"]): task for task in unfinished}
        incomplete = [
            task
            for task in unfinished
            if task.get("status") not in self.NON_BLOCKING_STATUSES
        ]
        delegated_all = [t for t in unfinished if t.get("status") == "delegated"]
        paused_all = [t for t in unfinished if t.get("status") == "paused"]
        awaiting_all = [t for t in unfinished if t.get("status") == AWAITING_USER_STATUS]

        # Get prioritized tasks from the same unfinished snapshot.
        prioritized = self._prioritize_task_snapshot(unfinished_by_id)

        # Separate by status for better visibility
        now = time.time()
        recent_threshold = self.config.recent_task_days * _SECONDS_PER_DAY
        recent_incomplete = [t for t in prioritized if now - t["created_at"] < recent_threshold]
        older_incomplete = [t for t in prioritized if now - t["created_at"] >= recent_threshold]

        # Every blocking status, in_progress first; the stable sort keeps the
        # dependency-aware order within each group.
        recent_ordered = sorted(recent_incomplete, key=lambda t: t["status"] != "in_progress")

        # Build numbered task list (inline, minimal newlines)
        task_items = []
        total_shown = 0
        max_tasks = self.config.max_resume_tasks

        for t in recent_ordered[: max_tasks - total_shown]:
            blockers = t.get("blockedBy", [])
            if t["status"] == "in_progress":
                icon = "🔄"
            elif t["status"] == "delegation-returned":
                icon = "📥 delegation returned — verify the results"
            else:
                icon = f"⚠️ blocked by {blockers}" if blockers else "✅ ready"
            task_items.append(f"{len(task_items) + 1}. #{t['id']}: {t['subject']} ({icon})")
            total_shown += 1

        # Every incomplete task can be older than recent_task_days — a session
        # resumed after a break. Listing only "recent" tasks then produced a
        # message that named nothing and counted the same tasks twice, once as
        # "and N more" and again as "N older". Naming them is the whole point of
        # the message, so fall back to the older ones rather than emitting a
        # bare count.
        named_older: list[dict] = []
        if not task_items and older_incomplete:
            for t in older_incomplete[: max_tasks - total_shown]:
                task_items.append(f"{len(task_items) + 1}. #{t['id']}: {t['subject']} (📅 older)")
                named_older.append(t)
                total_shown += 1

        # Show delegated tasks (non-blocking but need follow-up if child failed)
        delegated_tasks = delegated_all
        for t in delegated_tasks[: max_tasks - total_shown]:
            task_items.append(f"{len(task_items) + 1}. #{t['id']}: {t['subject']} (🤝 delegated — check if complete)")
            total_shown += 1

        for t in paused_all[: max_tasks - total_shown]:
            task_items.append(f"{len(task_items) + 1}. #{t['id']}: {t['subject']} (⏯ paused — resume when ready)")
            total_shown += 1

        for t in awaiting_all[: max_tasks - total_shown]:
            decision = ((t.get("metadata") or {}).get("awaiting_user") or {}).get("decision", "")
            task_items.append(f"{len(task_items) + 1}. #{t['id']}: {t['subject']} (⏳ waiting on the user: {decision})")
            total_shown += 1

        task_list = " ".join(task_items)
        total = len(incomplete) + len(delegated_all) + len(paused_all) + len(awaiting_all)
        # Subtract the older tasks already named above: counting them again in
        # the "older" suffix reported one set of tasks as two.
        older_count = len(older_incomplete) - len(named_older)
        task_status = format_command_for_cli("/ar:task status", ctx.cli_type)
        overflow = f" [... and {total - total_shown} more: {task_status} lists them]" if total > total_shown else ""
        older = f" [📅 {older_count} older task(s) from previous days also incomplete]" if older_count > 0 else ""

        injection = (
            f"🔄 outstanding incomplete tasks from previous session: "
            f"{task_list}{overflow}{older}\n"
            f"{_task_actions_fragment(_task_cli_hint(ctx), staleness_reminders_disabled=not ctx.task_staleness_enabled)}"
            f"7. {_stale_escape_sentence(_task_cli_hint(ctx), marker=_stale_clear_marker_example())}\n"
        )

        # SessionStart can fire repeatedly within one session: harnesses emit it
        # for startup, resume, and post-compaction, and a Codex transcript showed
        # six consecutive fires rendering the identical block six times. Claim
        # each (source, injection) pair once, inside atomic_update_metadata's
        # lock so concurrent fires cannot both claim it.
        #
        # Deliberately keyed on the injection text and not on the session alone:
        # a changed task list still re-injects (the AI needs the new task), and a
        # different source still re-injects (compaction wipes the context that
        # held the previous copy). Only an identical repeat is suppressed.
        if not self._claim_session_start_injection(ctx, injection):
            return None

        # Log resume event
        self.log_event("RESUME", "session", f"{total} outstanding tasks", "multiple")

        # Only blocking work arms PreToolUse enforcement. Paused/delegated work
        # is surfaced for recovery but remains genuinely non-blocking.
        if incomplete:
            task_progress_state_set(ctx, "task_staleness_enforce_next", True)
            # Skip allow and go straight to deny on the first unrelated tool.
            task_progress_state_set(ctx, "task_staleness_reminder_count", 1)

        # Keep AI running with injected prompt — AI sees this immediately
        return ctx.continue_running(injection)

    def handle_stop(self, ctx: EventContext) -> Optional[Dict]:
        """Handle Stop: block only while some task is the agent's to act on now.

        The decision is made from recorded task state, never from a clock or a
        block count (``_stop_decision``). A task that waits — delegated to a
        subagent with no evidence that it ended, waiting on a decision the
        user must make, blocked by a task that is itself waiting, or inherited
        from another conversation — is listed and does not block. When nothing
        is actionable the gate returns None, so later Stop handlers (autorun's
        stage verification) still answer.

        User-driven exits: /ar:estop, /ar:task ignore <id>, /ar:task pause.
        """
        # SubagentStop may share the parent's session (Claude) or carry the
        # child's session plus a parent transcript (Codex). Blocking it creates
        # a deadlock: the parent cannot receive the child result. The spawn-time
        # receipt below resolves either payload back to the authoritative parent;
        # that parent's own Stop still enforces verification.
        if ctx.event == "SubagentStop":
            # Never blocks (blocking here deadlocks the parent waiting for the
            # child), but a finishing child ends its delegation: matching
            # tasks flip to the blocking delegation-returned status so the
            # parent's own next Stop asks for verification.
            try:
                self._return_delegations(ctx)
            except Exception as exc:  # noqa: BLE001 - the gate must stay non-blocking
                logger.warning("Could not process returned delegations: %s", exc)
            return None
        if not task_enforcement_capability_available(
            ctx.cli_type,
            ctx.task_tool_evidence,
            TASK_UPDATE_CAPABILITY_ROLES,
        ):
            # Skip only this Task-specific gate. Later Stop handlers may still
            # enforce autorun stages that require no task mutation tool.
            return None
        if task_pause_allows_stop(ctx):
            return ctx.allow()
        return self._stop_decision(ctx)

    def _return_delegations(self, ctx) -> list[str]:
        """Flip delegated tasks whose subagent finished to delegation-returned.

        Codex supplies an exact agent_id while firing this hook in the child
        session. Claude may instead encode the id in a child transcript path
        while sharing the parent session. The immutable spawn receipt routes
        either shape to the parent task state; a legacy local ledger fallback
        remains for older payloads. One state commit is O(tasks) and idempotent.
        """
        finished = str(ctx.agent_id) if ctx.agent_id else None
        child_transcript = ctx.agent_transcript_path or ctx.transcript_path
        match = _AGENT_TRANSCRIPT_RE.search(str(child_transcript or ""))
        if finished is None and match:
            finished = match.group(1)

        if finished:
            receipt = self._delegation_receipt(ctx.cli_type, finished)
            parent_session_id = (
                str(receipt.get("parent_session_id"))
                if isinstance(receipt, dict) and receipt.get("parent_session_id")
                else None
            )
            if parent_session_id and parent_session_id != self.session_id:
                parent = TaskLifecycle(
                    session_id=parent_session_id,
                    ctx=ctx,
                    config=self.config,
                )
                return parent._return_delegations_for_agent(ctx, finished)

        return self._return_delegations_for_agent(ctx, finished)

    def _return_delegations_for_agent(
        self,
        ctx: EventContext,
        finished: str | None,
    ) -> list[str]:
        """Apply one child return inside the already-resolved parent session."""

        returned: list[str] = []
        with self._session_state() as state:
            repository = self._prepare_task_storage(state)
            metadata = copy.deepcopy(state.get("session_metadata", {}))
            ledger = list(metadata.get("agent_spawns", []))
            live_ids = {
                str(entry.get("id"))
                for entry in ledger
                if entry.get("claimed") and not entry.get("returned")
            }
            if finished is None and len(live_ids) == 1:
                finished = next(iter(live_ids))
            target_ids = {finished} if finished else live_ids
            if not target_ids:
                return []

            tasks = (
                repository.list_tasks(self.global_key, self._state_lock_timeout)
                if repository is not None
                else state.get("tasks", {})
            )
            now = time.time()
            for task_id, task in tasks.items():
                if not isinstance(task, dict) or task.get("status") != "delegated":
                    continue
                if _delegation_agent(task) not in target_ids:
                    continue
                task["status"] = "delegation-returned"
                task["updated_at"] = now
                returned.append(str(task_id))
                if repository is not None:
                    repository.put_task(self.global_key, str(task_id), task, self._state_lock_timeout)

            matched_ledger = False
            if target_ids:
                for entry in ledger:
                    if (
                        str(entry.get("id")) in target_ids
                        and not entry.get("returned")
                    ):
                        entry["returned"] = True
                        entry["returned_at"] = now
                        matched_ledger = True
            if returned or matched_ledger:
                metadata["agent_spawns"] = _bounded_spawn_ledger(ledger)
                if repository is None:
                    state["tasks"] = tasks
                state["session_metadata"] = metadata
            self._finish_delegation_receipts(
                ctx.cli_type,
                target_ids,
                now,
            )

        for task_id in returned:
            self.log_event(
                "DELEGATION_RETURNED",
                f"task#{task_id}",
                "subagent finished; verification required",
                "delegation-returned",
            )
        if returned:
            ctx.add_chain_notification(
                "Subagent returned — critically assess its results before "
                "accepting them; subagents make mistakes. Then TaskUpdate("
                'status="completed") with the evidence, or re-delegate, or '
                "ignore if no longer needed: "
                + ", ".join(f"#{task_id}" for task_id in returned),
                channel="both",
            )
        return returned

    # === The Stop gate's view of the session ===

    def _open_task_snapshot(self) -> tuple[Dict[str, Dict], Dict, set]:
        """One read for the Stop gate: open tasks, session metadata, unknown blockers.

        Rows whose status is settled (completed, deleted, ignored) are never
        read. A blocker id that is not among the rows read is either settled
        or unknown; only such ids are looked up, in the same transaction, so a
        finished blocker can be told from a dangling one. One transaction;
        cost linear in the open tasks.
        """
        with self._session_state() as state:
            repository = self._prepare_task_storage(state)
            metadata = copy.deepcopy(state.get("session_metadata", {}))
            if repository is None:
                stored = state.get("tasks", {})
                rows = [copy.deepcopy(task) for task in stored.values() if task.get("status") not in SETTLED_TASK_STATUSES]
            else:
                rows = repository.list_excluding_statuses(self.global_key, SETTLED_TASK_STATUSES, self._state_lock_timeout)
            tasks = {str(task["id"]): task for task in rows}
            absent = {
                str(blocker)
                for task in tasks.values()
                if task.get("status") not in NON_BLOCKING_TASK_STATUSES
                for blocker in task.get("blockedBy") or []
                if str(blocker) not in tasks
            }
            if not absent:
                unknown: set = set()
            elif repository is None:
                unknown = {task_id for task_id in absent if task_id not in stored}
            else:
                unknown = absent - repository.existing_task_ids(self.global_key, absent, self._state_lock_timeout)
        return tasks, metadata, unknown

    def _update_task_rows(
        self,
        updaters: Dict[str, Callable[[Dict], bool]],
        metadata_update: Callable[[Dict], None] | None = None,
    ) -> list[str]:
        """Apply per-task updaters to fresh rows in one transaction.

        Each updater sees the row as stored now (not as an earlier snapshot
        saw it) and returns whether it changed it; only changed rows are
        written. Returns the changed ids.
        """
        changed: list[str] = []
        now = time.time()
        with self._session_state() as state:
            repository = self._prepare_task_storage(state)
            stored = state.get("tasks", {}) if repository is None else None
            for task_id, update in updaters.items():
                current = stored.get(task_id) if repository is None else repository.get_task(self.global_key, task_id, self._state_lock_timeout)
                if not isinstance(current, dict):
                    continue
                task = copy.deepcopy(current)
                if not update(task):
                    continue
                task["updated_at"] = now
                if repository is None:
                    stored[task_id] = task
                else:
                    repository.put_task(self.global_key, task_id, task, self._state_lock_timeout)
                changed.append(task_id)
            if repository is None and changed:
                state["tasks"] = stored
            if metadata_update is not None:
                metadata = copy.deepcopy(state.get("session_metadata", {}))
                original = copy.deepcopy(metadata)
                metadata_update(metadata)
                if metadata != original:
                    state["session_metadata"] = metadata
        return changed

    def _settle_delegations(self, ctx: EventContext, tasks: Dict[str, Dict]) -> bool:
        """End delegations whose evidence says they ended. True if a task changed.

        No clock is involved. Evidence read here:
        - A delegation an older build recorded by marker (``delegated_at``
          with no ``delegation`` record) has no liveness to check, so it
          returns to the agent for verification (``delegation-returned``).
          A task set to ``delegated`` through a task tool carries neither and
          stays delegated until it is updated: there is nothing to check.
        - The recorded parent harness process is gone, so the subagent it ran
          died with it: the task goes back to ``pending``. The hook's own
          harness identity is alive by definition and is not checked; every
          other recorded pid and birth stamp is checked once. PID-only records
          from older builds fall back to existence checks.

        A SubagentStop (``_return_delegations``) and the agent's or user's own
        update of the task are the other evidence, handled where they arrive.
        """
        running_signal = platform_for(ctx.cli_type).stop_payload_lists_running_agents
        if running_signal is not None and running_signal.reports_running(ctx):
            return False

        legacy: list[str] = []
        exited: Dict[str, str] = {}
        # The exact record each pid check was about: the check runs outside
        # the state lock, so the write applies only to a task still carrying it.
        checked: Dict[str, Dict] = {}
        starts_by_pid: Dict[int, int] = {}
        legacy_alive_by_pid: Dict[int, bool] = {}
        for task_id, task in tasks.items():
            if task.get("status") != "delegated":
                continue
            metadata = task.get("metadata") or {}
            record = metadata.get("delegation")
            if not isinstance(record, dict):
                if "delegated_at" in metadata:
                    legacy.append(task_id)
                continue
            pid = record.get("harness_pid")
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                continue
            recorded_start = record.get("harness_started_at_units")
            same_current_identity = pid == ctx.harness_pid and (
                recorded_start is None
                or recorded_start == ctx.harness_started_at_units
            )
            if same_current_identity:
                continue
            if not _recorded_harness_is_live(
                record,
                starts_by_pid=starts_by_pid,
                legacy_alive_by_pid=legacy_alive_by_pid,
            ):
                exited[task_id] = str(record.get("agent_id") or "with no recorded id")
                checked[task_id] = copy.deepcopy(record)
        if not legacy and not exited:
            return False

        def returned_for_verification(task: Dict) -> bool:
            metadata = task.get("metadata") or {}
            if task.get("status") != "delegated" or isinstance(metadata.get("delegation"), dict) or "delegated_at" not in metadata:
                return False
            task["status"] = "delegation-returned"
            task.setdefault("tool_outputs", []).append("Delegation liveness not recorded before upgrade; verify the subagent's results")
            return True

        applied: set = set()

        def back_to_pending(task_id: str) -> Callable[[Dict], bool]:
            def update(task: Dict) -> bool:
                if task.get("status") != "delegated" or (task.get("metadata") or {}).get("delegation") != checked[task_id]:
                    return False
                task["status"] = "pending"
                task.setdefault("tool_outputs", []).append("The harness process that ran the subagent exited before it reported")
                applied.add(exited[task_id])
                return True

            return update

        def close_ledger_entries(metadata: Dict) -> None:
            ledger = list(metadata.get("agent_spawns", []))
            gone = applied
            for entry in ledger:
                if str(entry.get("id")) in gone and not entry.get("returned"):
                    entry["returned"] = True
                    entry["harness_exited"] = True
            if ledger:
                metadata["agent_spawns"] = _bounded_spawn_ledger(ledger)

        updaters: Dict[str, Callable[[Dict], bool]] = {task_id: returned_for_verification for task_id in legacy}
        updaters.update({task_id: back_to_pending(task_id) for task_id in exited})
        changed = set(self._update_task_rows(updaters, close_ledger_entries if exited else None))
        verify = [task_id for task_id in legacy if task_id in changed]
        pending = [task_id for task_id in exited if task_id in changed]
        for task_id in verify:
            self.log_event("DELEGATION_UNVERIFIED", f"task#{task_id}", "delegated before liveness was recorded; verification required", "delegation-returned")
        for task_id in pending:
            self.log_event("DELEGATION_HARNESS_EXITED", f"task#{task_id}", f"harness of subagent {exited[task_id]} exited", "pending")
        if verify:
            ctx.add_chain_notification(
                f"📥 {_task_refs(verify)}: delegated before autorun recorded the subagent's harness process "
                "(liveness not recorded before upgrade); verify the subagent's results, then complete, "
                "re-delegate or ignore each.",
                channel="both",
            )
        for task_id in pending:
            ctx.add_chain_notification(
                f"↩️ The harness process that ran subagent {exited[task_id]} exited before it reported; "
                f"#{task_id} is back to pending.",
                channel="both",
            )
        return bool(changed)

    def apply_awaiting_user_markers(self, ctx: EventContext, blocking_tasks: List[Dict]) -> tuple[list[str], list[str]]:
        """Record the awaiting-user markers of the agent's latest turn as task state.

        Returns (ids entered, ids whose marker named no decision). Only a task
        blocking now can enter, so a marker cannot touch a finished, unknown,
        delegated or already waiting task. The decision and the status the
        task had are stored on it; the session keeps the ids so the next user
        prompt restores them without scanning every task.
        """
        entries = extract_awaiting_user_markers(latest_assistant_turn(ctx)[1])
        if not entries:
            return [], []
        by_id = {str(task["id"]): task for task in blocking_tasks}
        decisions: Dict[str, str] = {}
        unstated: list[str] = []
        for ids, decision in entries:
            for task_id in ids:
                if task_id not in by_id:
                    continue
                if decision:
                    decisions[task_id] = decision[:_MAX_AWAITING_USER_DECISION_CHARS]
                elif task_id not in unstated:
                    unstated.append(task_id)
        unstated = [task_id for task_id in unstated if task_id not in decisions]
        if not decisions:
            return [], unstated

        prompt_marker = None
        if platform_for(ctx.cli_type).policy_commands_arrive_in_transcript:
            from .transcript_commands import latest_transcript_prompt

            prompt = latest_transcript_prompt(ctx.transcript_path)
            if prompt is None:
                ctx.add_chain_notification(
                    "Awaiting-user marker not recorded: no user prompt can be identified in the transcript. "
                    "This harness may omit prompt hooks; the named tasks remain actionable until updated.",
                    channel="both",
                )
                return [], unstated
            prompt_marker = prompt.marker

        def entering(decision: str) -> Callable[[Dict], bool]:
            def update(task: Dict) -> bool:
                status = task.get("status")
                if status not in BLOCKING_TASK_STATUSES:
                    return False
                task.setdefault("metadata", {})["awaiting_user"] = {"decision": decision, "previous_status": status}
                if prompt_marker:
                    task["metadata"]["awaiting_user"]["transcript_prompt_marker"] = prompt_marker
                task["status"] = AWAITING_USER_STATUS
                task.setdefault("tool_outputs", []).append(f"Waiting on the user: {decision}")
                return True

            return update

        def index(metadata: Dict) -> None:
            waiting = list(metadata.get(_AWAITING_USER_INDEX, []))
            waiting.extend(task_id for task_id in decisions if task_id not in waiting)
            metadata[_AWAITING_USER_INDEX] = waiting
            _reset_stop_block_sequence(metadata)

        entered = self._update_task_rows({task_id: entering(decision) for task_id, decision in decisions.items()}, index)
        for task_id in entered:
            self.log_event("AWAITING_USER", task_id, by_id[task_id].get("subject", ""), AWAITING_USER_STATUS, {"decision": decisions[task_id]})
        return entered, unstated

    def restore_awaiting_user(self, ctx: EventContext, *, expected_waits: Optional[Dict] = None) -> bool:
        """A prompt the user wrote: restart the loop guard and end awaiting-user.

        Only a prompt ``resolve_prompt_author`` credits to the user does
        either. A subagent, harness or automation prompt changes nothing: the
        Pi-family bridge answers every blocked Stop with an automation-authored
        continuation turn, and letting that restart the guard kept a model that
        stops without acting blocked forever. Reads the session metadata
        without the writer slot first, so a prompt with nothing to reset and
        nothing waiting on the user writes nothing and reads no task row.
        """
        if resolve_prompt_author(ctx) != "user":
            return False
        metadata = get_session_manager().read_field(self.global_key, "session_metadata", {}, timeout=self._state_lock_timeout)
        metadata = metadata if isinstance(metadata, dict) else {}
        waiting = [str(task_id) for task_id in metadata.get(_AWAITING_USER_INDEX, [])]
        if expected_waits is not None:
            waiting = [task_id for task_id in waiting if task_id in expected_waits]
        if not waiting:
            if expected_waits is None and not _stop_sequence_is_reset(metadata):
                self.atomic_update_metadata(_reset_stop_block_sequence)
            return False

        applied: set = set()

        def restored(task: Dict) -> bool:
            if task.get("status") != AWAITING_USER_STATUS:
                return False
            if expected_waits is not None and (task.get("metadata") or {}).get("awaiting_user") != expected_waits.get(str(task["id"])):
                return False
            record = (task.get("metadata") or {}).pop("awaiting_user", None) or {}
            previous = record.get("previous_status")
            task["status"] = previous if previous in BLOCKING_TASK_STATUSES else "pending"
            task.setdefault("tool_outputs", []).append("The user replied; enforcement restored")
            applied.add(str(task["id"]))
            return True

        def clear_index(metadata: Dict) -> None:
            _reset_stop_block_sequence(metadata)
            if expected_waits is None:
                metadata.pop(_AWAITING_USER_INDEX, None)
            else:
                metadata[_AWAITING_USER_INDEX] = [task_id for task_id in metadata.get(_AWAITING_USER_INDEX, []) if str(task_id) not in applied]

        changed = self._update_task_rows({task_id: restored for task_id in waiting}, clear_index)
        for task_id in changed:
            self.log_event("AWAITING_USER_ANSWERED", task_id, "the user replied", "restored")
        if changed:
            ctx.add_chain_notification(
                f"The user replied; {_task_refs(changed)} no longer waiting on the user and "
                "blocking again until done.",
                channel="ai",
            )
        return bool(changed)

    def _restore_awaiting_user_from_transcript(self, ctx: EventContext, tasks: Dict) -> tuple[bool, list[str]]:
        """Restore on a newer user entry; missing evidence keeps tasks actionable."""
        if not platform_for(ctx.cli_type).policy_commands_arrive_in_transcript:
            return False, []
        waiting = {task_id: task for task_id, task in tasks.items() if task.get("status") == AWAITING_USER_STATUS}
        if not waiting:
            return False, []
        from .plugins import _transcript_policy_context
        from .transcript_commands import latest_transcript_prompt

        prompt = latest_transcript_prompt(ctx.transcript_path)
        fallback = _transcript_policy_context(ctx, prompt.text) if prompt else None
        if fallback is not None and resolve_prompt_author(fallback) != "user":
            return False, []
        answered = {}
        unverified = []
        for task_id, task in waiting.items():
            record = (task.get("metadata") or {}).get("awaiting_user") or {}
            baseline = record.get("transcript_prompt_marker")
            if prompt and baseline == prompt.marker:
                continue
            # Decide safely even if recording the restoration subsequently fails.
            previous = record.get("previous_status")
            task["status"] = previous if previous in BLOCKING_TASK_STATUSES else "pending"
            if prompt and baseline:
                answered[task_id] = copy.deepcopy(record)
            else:
                unverified.append(task_id)
        notes = []
        if unverified:
            notes.append(
                f"Cannot verify user replies from the transcript for {_task_refs(unverified)}. "
                "Those tasks are actionable for this Stop; update their status explicitly."
            )
        restored = self.restore_awaiting_user(fallback, expected_waits=answered) if answered else False
        return restored, notes

    def _apply_stop_markers(self, ctx: EventContext, tasks: Dict[str, Dict]) -> tuple[bool, list[str]]:
        """Apply the agent's markers against the tasks blocking now.

        Returns (whether any task changed, notes for the reply). Each marker
        reader first tests its literal prefix, so a reply with no marker costs
        three substring searches.
        """
        blocking = [task for task in tasks.values() if task.get("status") not in NON_BLOCKING_TASK_STATUSES]
        if not blocking:
            return False, []
        changed = bool(self.apply_delegation_markers(ctx, blocking))
        notes = []
        try:
            entered, unstated = self.apply_awaiting_user_markers(ctx, blocking)
        except Exception as exc:  # noqa: BLE001 - the tasks keep the state they had
            logger.warning("Could not record awaiting-user markers: %s", exc)
            entered, unstated = [], []
            notes.append(
                f"⚠️ autorun could not record the awaiting-user marker: {exc}. "
                "The tasks it named still block this Stop."
            )
        changed = changed or bool(entered)
        if unstated:
            notes.append(
                f"Awaiting-user marker ignored for {_task_refs(unstated)}: no decision stated. "
                f"Name the decision on the same line: {_awaiting_user_marker_example()}"
            )
        if self.config.ghost_clear_enabled:
            stale = stale_clear_marker_ids(ctx)
            if stale:
                cleared = self.clear_stale_task_markers(stale, allowed_task_ids={str(task["id"]) for task in blocking})
                if cleared:
                    changed = True
                    ctx.add_chain_notification(f"Cleared stale task(s): {_task_refs(cleared)}", channel="both")
        return changed, notes

    def _waiting_line(self, task: Dict) -> str:
        """One line for a task whose own status means it waits on someone else."""
        task_id, subject = task["id"], task.get("subject", "")
        if task.get("status") == AWAITING_USER_STATUS:
            decision = ((task.get("metadata") or {}).get("awaiting_user") or {}).get("decision", "")
            return f"⏳ Waiting on you: #{task_id} {subject}: {decision}"
        agent = _delegation_agent(task)
        if agent:
            return f"🤝 Waiting on subagent {agent}: #{task_id} {subject}"
        return (
            f"🤝 Delegated with no subagent id recorded: #{task_id} {subject}; it stays "
            "delegated until you or the agent update it"
        )

    def _stop_view(self, tasks: Dict[str, Dict], unknown: set) -> tuple[list[tuple[Dict, str]], list[str]]:
        """(actionable tasks with a note, waiting lines) from one snapshot.

        In memory, linear in the open tasks. A blocking-status task is
        actionable when it has no open blocker, when a blocker id is unknown
        (a dangling dependency the agent must fix), or when it sits in a
        blockedBy cycle (one the agent must break). Otherwise it waits on its
        open blockers, which block Stop themselves if they are actionable.
        A task a fork inherited waits until this conversation updates it.
        """
        blocking = {task_id: task for task_id, task in tasks.items() if task.get("status") not in NON_BLOCKING_TASK_STATUSES}
        graph = {
            task_id: [str(blocker) for blocker in task.get("blockedBy") or [] if str(blocker) in blocking]
            for task_id, task in blocking.items()
        }
        cyclic = _cycle_members(graph)
        actionable: list[tuple[Dict, str]] = []
        waiting: list[str] = []
        for task in sorted(blocking.values(), key=self._creation_order_key):
            task_id = str(task["id"])
            inherited = (task.get("metadata") or {}).get("inherited_from")
            if inherited:
                waiting.append(f"↪️ #{task_id} {task.get('subject', '')} is inherited from {inherited}; update it here to take it on")
                continue
            blockers = list(dict.fromkeys(str(blocker) for blocker in task.get("blockedBy") or []))
            dangling = [blocker for blocker in blockers if blocker in unknown]
            if dangling:
                actionable.append((task, f"blocked by unknown task {_task_refs(dangling)}: fix its blockedBy"))
                continue
            if task_id in cyclic:
                partners = [blocker for blocker in graph[task_id] if blocker in cyclic]
                actionable.append((task, f"in a blockedBy cycle with {_task_refs(partners)}: break the cycle"))
                continue
            holding = [blocker for blocker in blockers if blocker in tasks]
            if holding:
                waiting.append(f"⛓️ #{task_id} waits on {_task_refs(holding)}")
                continue
            actionable.append((task, ""))
        waiting.extend(
            self._waiting_line(task)
            for task in sorted(tasks.values(), key=self._creation_order_key)
            if task.get("status") in WAITING_TASK_STATUSES
        )
        return actionable, waiting

    def waiting_summary(self) -> list[str]:
        """The waiting lines the Stop gate would print now, for status commands."""
        tasks, _metadata, unknown = self._open_task_snapshot()
        return self._stop_view(tasks, unknown)[1]

    def _stop_decision(self, ctx: EventContext) -> Optional[Dict]:
        """The Stop gate proper: block on actionable tasks, list the waiting ones.

        1. One snapshot of the open tasks.
        2. Markers in the agent's latest turn (delegation, awaiting-user,
           stale clear) and delegation liveness evidence, each a state write
           that happens only when it changes a task; the snapshot is re-read
           only then.
        3. Actionable tasks block, followed by one line per waiting task.
           With none, return None (never allow) so stage verification runs,
           and tell the user who waits on whom.
        4. The loop guard: a block counts one consecutive Stop; past
           ``stop_block_max_count`` with no tool call between them the gate
           passes through instead, saying so.
        """
        tasks, metadata, unknown = self._open_task_snapshot()
        notes: list[str] = []

        def recorded(what: str, write: Callable[[], Any], unchanged: Any) -> Any:
            """Run one of this Stop's state writes; on any failure, say so and decide
            from the task state as it was read, never by letting the Stop through."""
            try:
                return write()
            except Exception as exc:  # noqa: BLE001 - a failed write must not decide the Stop
                logger.warning("Stop gate could not record %s: %s", what, exc)
                notes.append(f"⚠️ autorun could not record {what}: {exc}. This Stop is decided from the task state as it was.")
                return unchanged

        restored, transcript_notes = recorded(
            "the user's transcript reply",
            lambda: self._restore_awaiting_user_from_transcript(ctx, tasks),
            (False, []),
        )
        notes.extend(transcript_notes)
        if restored:
            tasks, metadata, unknown = self._open_task_snapshot()
        changed, marker_notes = recorded("the markers in your last reply", lambda: self._apply_stop_markers(ctx, tasks), (False, []))
        notes[:0] = marker_notes
        if changed:
            tasks, metadata, unknown = self._open_task_snapshot()
        if recorded("the end of a delegation", lambda: self._settle_delegations(ctx, tasks), False):
            tasks, metadata, unknown = self._open_task_snapshot()
        actionable, waiting = self._stop_view(tasks, unknown)
        waiting_text = "Not blocking this Stop:\n" + "\n".join(waiting) if waiting else ""

        if not actionable:
            if not _stop_sequence_is_reset(metadata):
                recorded("the Stop loop guard's restart", lambda: self.atomic_update_metadata(_reset_stop_block_sequence), None)
            for note in [*notes, waiting_text]:
                if note:
                    ctx.add_chain_notification(note, channel="both")
            return None

        computed: dict = {}

        def count_block(metadata: Dict) -> None:
            metadata["stop_block_count"] = metadata.get("stop_block_count", 0) + 1
            metadata.pop("consecutive_identical_stop_block_count", None)
            metadata.pop("last_stop_block_id_hash", None)
            computed["block_count"] = metadata["stop_block_count"]

        recorded("this block in the Stop loop guard", lambda: self.atomic_update_metadata(count_block), None)
        ctx.state_set_volatile(STOP_LOOP_GUARD_ARMED, True)
        # If the counter update failed, block (count 1) rather than release.
        block_count = computed.get("block_count", 1)
        total = len(actionable)

        # This loop guard is the one count allowed to pass a Stop. It does not
        # decide task state: the tasks remain actionable, and returning None
        # preserves every later Stop handler, including stage verification.
        if block_count > self.config.stop_block_max_count:
            ctx.pending_stop_injection = None
            if block_count == self.config.stop_block_max_count + 1:
                message = _loop_guard_message(_task_cli_hint(ctx), blocked_count=block_count - 1, task_count=total)
                self.log_event("STOP_LOOP_GUARD", "session", message, "released")
                ctx.add_chain_notification(message, channel="both")
            return None

        max_tasks = self.config.max_resume_tasks
        task_lines = []
        for task, note in actionable[:max_tasks]:
            status = task["status"]
            status_icon = {
                "in_progress": "🔄",
                "pending": "⏸️",
                "delegation-returned": "📥",
            }.get(status, "❓")
            if status not in STATUS_POLICY:
                # Written by a newer build: it blocks (fail closed), so say why.
                status_icon += f" unknown status {status!r}: update this task, or install the release that wrote it"
            if note:
                status_icon += f" {note}"
            task_lines.append(f"{len(task_lines) + 1}. #{task['id']}: {task.get('subject', '')} ({status_icon})")

        task_status = format_command_for_cli("/ar:task status", ctx.cli_type)
        overflow = f" [... and {total - max_tasks} more: {task_status} lists them]" if total > max_tasks else ""
        injection = (
            f"🛑 CANNOT STOP — incomplete tasks: {' '.join(task_lines)}{overflow}\n"
            f"{_task_actions_fragment(_task_cli_hint(ctx), staleness_reminders_disabled=not ctx.task_staleness_enabled)}"
            f"7. {_stale_escape_sentence(_task_cli_hint(ctx), marker=_stale_clear_marker_example())}\n"
        )

        returned_tasks = [task for task, _note in actionable if task.get("status") == "delegation-returned"]
        if returned_tasks:
            task_ignore = format_command_for_cli("/ar:task ignore <id>", ctx.cli_type)
            injection += (
                "Delegation returned (📥): critically assess each returned "
                "task's results — subagents make mistakes — and verify before "
                'accepting. Then TaskUpdate(status="completed") with the '
                "evidence, or re-delegate by printing the delegation marker "
                f"again, or ignore it with {task_ignore} if no longer needed: "
                + _task_refs(task["id"] for task in returned_tasks[:max_tasks])
                + "\n"
            )
        for note in notes:
            injection += note + "\n"
        if waiting_text:
            injection += waiting_text + "\n"

        # Block count is diagnostic only, not shown to the agent.
        self.log_event("STOP_WARNING", "session", f"Block #{block_count}: {total} incomplete tasks", "blocked")

        # Reset three-stage system if at STAGE_2_COMPLETED — tasks must resolve first.
        if ctx.autorun_stage == EventContext.STAGE_2_COMPLETED:
            ctx.autorun_stage = EventContext.STAGE_2
            injection += CONFIG.get("task_outstanding_stage3_message", "").format(
                count=total,
                names=", ".join(task.get("subject", f"#{task.get('id', '?')}") for task, _note in actionable[:_STAGE3_OVERFLOW_NAME_COUNT])
                + ("..." if total > _STAGE3_OVERFLOW_NAME_COUNT else ""),
            )

        # Deferred AI delivery: the Stop block uses decision+reason, and the
        # next PostToolUse replays the same text through the context pathway
        # (deliver_pending_stop_injection). Re-armed on every block so the
        # agent always sees the override actions again; PostToolUse delivery
        # clears it, so each arm fires at most once.
        ctx.pending_stop_injection = injection
        # Reset staleness counter — AI just learned about tasks, give full countdown.
        ctx.tool_calls_since_task_update = 0
        return ctx.continue_running(injection)

    def record_agent_spawn(self, ctx) -> None:
        """Remember which child agent took work — Python-side, no transcription.

        Runs on every PostToolUse, so the tool-name allowlist gates BEFORE any
        state I/O: non-spawn events cost one frozenset lookup. The recorded id
        is what lets a later delegation marker attach automatically and the
        SubagentStop gate know which delegation returned.
        """
        if ctx.tool_name not in agent_spawn_tools_for(ctx.cli_type):
            return
        match = _AGENT_SPAWN_ID_RE.search(ctx.tool_result_str)
        if not match:
            return
        agent_id = match.group(1)
        try:
            self._promote_legacy_delegation_receipts_once(ctx)
            with self._session_state() as state:
                with self._delegation_receipt_state(ctx.cli_type, agent_id, ctx) as receipt_state:
                    now = time.time()
                    existing = copy.deepcopy(receipt_state.get("receipt"))
                    if isinstance(existing, dict):
                        if existing.get("parent_session_id") != self.session_id:
                            logger.warning(
                                "Agent id %s is already owned by parent session %s",
                                agent_id,
                                existing.get("parent_session_id"),
                            )
                            return
                    metadata = copy.deepcopy(state.get("session_metadata", {}))
                    ledger = list(metadata.get("agent_spawns", []))
                    ledger_entry = next(
                        (entry for entry in ledger if entry.get("id") == agent_id),
                        None,
                    )
                    if ledger_entry is None:
                        mode = _spawn_mode(ctx.cli_type, ctx.tool_result_str)
                        ledger_entry = {
                            "id": agent_id,
                            "at": float(existing.get("at") or now)
                            if isinstance(existing, dict)
                            else now,
                            "claimed": False,
                            "mode": mode,
                            # A child that had already finished when this
                            # result arrived cannot be waited on.
                            "returned": mode == "sync"
                            or (bool(existing.get("returned")) if isinstance(existing, dict) else False),
                        }
                        ledger.append(ledger_entry)
                    metadata["agent_spawns"] = _bounded_spawn_ledger(ledger)
                    state["session_metadata"] = metadata
                    if not isinstance(existing, dict):
                        receipt_state["receipt"] = {
                            "agent_id": agent_id,
                            "cli_type": ctx.cli_type,
                            "parent_session_id": self.session_id,
                            "parent_global_key": self.global_key,
                            "at": float(ledger_entry.get("at") or now),
                            "returned": bool(ledger_entry.get("returned")),
                            "claimed": False,
                            "harness_pid": ctx.harness_pid,
                            "harness_started_at_units": ctx.harness_started_at_units,
                        }
        except Exception as exc:  # noqa: BLE001 - capture must never break the hook
            logger.warning("Could not record agent spawn: %s", exc)

    def apply_delegation_markers(self, ctx, blocking_tasks: "List[Dict] | None" = None):
        """Honor any delegation markers in what the AI just said.

        Called from both pathways that can see a marker — PostToolUse, which
        fires only after a tool call, and the stop gate, which fires when the
        AI has stopped talking. A marker printed in a plain-text reply reaches
        only the second, and that is the common case, since "print this
        marker" invites exactly that.

        Restricted to tasks that are blocking right now, so a marker cannot
        resurrect or alter one that is already finished, ignored, or unrelated
        to the decision at hand.

        Returns the delegated ids. Never raises: a marker that cannot be
        applied must leave the caller's decision exactly as it would have
        been, not replace it with an error.
        """
        try:
            sources: list[tuple[str, str]] = []
            tool_text = ctx.tool_result_str
            marker_prefix = _marker_literal_prefix(CONFIG["delegate_marker_template"])
            if marker_prefix in tool_text:
                digest = hashlib.sha256(tool_text.encode("utf-8")).hexdigest()
                transcript_size = len(object.__getattribute__(ctx, "_session_transcript"))
                sources.append((f"tool:{ctx.event}:{ctx.tool_name}:{transcript_size}:{digest}", tool_text))
            latest_identity, latest_text = latest_assistant_turn(ctx)
            if marker_prefix in latest_text:
                sources.append((f"assistant:{latest_identity}", latest_text))
            if not sources:
                return []

            if blocking_tasks is None:
                blocking_tasks = self.get_incomplete_tasks(exclude_blocking=True)
            blocking_ids = {str(task["id"]) for task in blocking_tasks}

            delegated: list[str] = []
            for source_identity, text in sources:
                for ordinal, (marker_ids, session) in enumerate(extract_delegate_markers(text)):
                    marker_payload = json.dumps(
                        [marker_ids, session],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    identity = hashlib.sha256(f"{source_identity}:{ordinal}:{marker_payload}".encode("utf-8")).hexdigest()
                    delegated.extend(
                        self.delegate_tasks_from_markers(
                            marker_ids,
                            allowed_task_ids=blocking_ids,
                            session=session,
                            marker_identity=identity,
                        )
                    )
            delegated = list(dict.fromkeys(delegated))
            if delegated:
                ctx.add_chain_notification(
                    f"Delegation recorded: {', '.join(f'#{d}' for d in delegated)}. Returned work requires verification.",
                    channel="both",
                )
            return delegated
        except Exception as exc:  # noqa: BLE001 - a marker must not break the caller
            logger.warning("Could not apply delegation markers: %s", exc)
            return []

    def clear_stale_task_markers(
        self,
        task_ids: Iterable[str],
        *,
        allowed_task_ids: Iterable[str] | None = None,
    ) -> list[str]:
        """Mark stale task ids ignored through the configured escape hatch.

        The marker is the agent's statement that the harness's own task list
        no longer has the task. It is honored from the first block, logged as
        GHOST_CLEAR and shown to the user; no count of identical blocks gates
        it, since such a count says nothing about the harness's list.

        Args:
            task_ids: Task ids extracted from the configured marker template.
            allowed_task_ids: Optional current blocking task-id set. When
                provided, markers for unrelated or already non-blocking tasks are
                ignored.
        """
        if not self.config.ghost_clear_enabled:
            return []

        reason = CONFIG["ghost_clear_reason"]
        cleared: list[str] = []
        allowed = {str(task_id) for task_id in allowed_task_ids} if allowed_task_ids is not None else None
        for tid in list(dict.fromkeys(str(task_id) for task_id in task_ids)):
            if allowed is not None and tid not in allowed:
                continue
            try:
                if self.ignore_task(tid, reason=reason):
                    cleared.append(tid)
                    self.log_event(
                        "GHOST_CLEAR",
                        f"task#{tid}",
                        "Cleared via ghost-clear marker",
                        "cleared",
                    )
            except Exception:
                continue
        return cleared

    def delegate_tasks_from_markers(
        self,
        task_ids: Iterable[str],
        *,
        marker_identity: str,
        allowed_task_ids: Iterable[str] | None = None,
        session: "str | None" = None,
    ) -> list[str]:
        """Mark task ids delegated from AI-printed delegation markers.

        "delegated" is in NON_BLOCKING_STATUSES, so this releases a Stop block
        while keeping the task visible for follow-up if the subagent fails.
        Unlike the stale-clear marker there is no armed threshold: delegation
        is a normal, immediate action the AI takes before spawning a subagent,
        not an escape hatch guarded against abuse — and it is strictly safer
        than the alternatives already offered (completed/deleted), since a
        delegated task stays listed and resurfaces at SessionStart.

        Args:
            task_ids: Task ids extracted from the delegation marker template.
            marker_identity: Stable identity of the marker occurrence. Required:
                it is what keeps a replayed marker from delegating twice, and
                the one path through _consume_delegation_marker also checks
                that each task is blocking, records delegated_at, and claims
                the spawn ledger.
            allowed_task_ids: Optional current blocking task-id set. When
                provided, markers naming unrelated or already non-blocking
                tasks are ignored, matching clear_stale_task_markers().
            session: Optional id of the session the work was handed to,
                recorded on each task. A delegation that never reports back
                is otherwise untraceable — the task says only that it went
                somewhere.
        """
        return self._consume_delegation_marker(
            marker_identity,
            task_ids,
            allowed_task_ids=allowed_task_ids,
            session=session,
        )

    def _consume_delegation_marker(
        self,
        marker_identity: str,
        task_ids: Iterable[str],
        *,
        allowed_task_ids: Iterable[str] | None,
        session: "str | None",
    ) -> list[str]:
        """Apply task transitions and consume one marker in one state commit."""
        allowed = {str(task_id) for task_id in allowed_task_ids} if allowed_task_ids is not None else None
        requested = list(dict.fromkeys(str(task_id) for task_id in task_ids))
        delegated: list[str] = []
        now = time.time()

        with self._session_state() as state:
            repository = self._prepare_task_storage(state)
            metadata = copy.deepcopy(state.get("session_metadata", {}))
            consumed = dict(metadata.get("consumed_delegation_markers", {}))
            if marker_identity in consumed:
                return []

            # A marker with no explicit id claims the latest unclaimed spawn:
            # the ledger is the reliable identity source, since the AI rarely
            # has the child id to transcribe (https://github.com/anthropics/claude-code/issues/7881 bars session
            # identity too). No spawn recorded → today's manual semantics.
            ledger = list(metadata.get("agent_spawns", []))
            claimed_entry = None
            if session is None:
                unclaimed = [entry for entry in ledger if not entry.get("claimed")]
                if unclaimed:
                    claimed_entry = unclaimed[-1]
                    claimed_entry["claimed"] = True
                    session = str(claimed_entry["id"])
                    metadata["agent_spawns"] = ledger
            else:
                for entry in ledger:
                    if str(entry.get("id")) == str(session):
                        entry["claimed"] = True
                        claimed_entry = entry
                        metadata["agent_spawns"] = ledger
                        break
            # What ends this delegation later is evidence about this record:
            # a SubagentStop for agent_id, or harness_pid no longer running.
            delegation = {
                "agent_id": str(session) if session else None,
                "cli_type": self._cli_type,
                "parent_session_id": self.session_id,
                "harness_pid": self.ctx.harness_pid if self.ctx is not None else None,
                "harness_started_at_units": (
                    self.ctx.harness_started_at_units if self.ctx is not None else None
                ),
                "mode": claimed_entry.get("mode", "unrecognized") if claimed_entry else "manual",
                "delegated_at": now,
            }

            tasks = state.get("tasks", {}) if repository is None else None
            for task_id in requested:
                if allowed is not None and task_id not in allowed:
                    continue
                task = tasks.get(task_id) if repository is None else repository.get_task(self.global_key, task_id, self._state_lock_timeout)
                if task is MISSING:
                    continue
                if not isinstance(task, dict):
                    continue
                if not task_status_policy(task.get("status", "pending")).blocks_stop:
                    continue
                task["status"] = "delegation-returned" if claimed_entry and claimed_entry.get("returned") else "delegated"
                task["updated_at"] = now
                task_metadata = task.setdefault("metadata", {})
                task_metadata["delegation"] = dict(delegation)
                # Kept beside the record for builds released before it, which
                # match a returning subagent on delegated_to_session.
                task_metadata["delegated_at"] = now
                if session:
                    task_metadata["delegated_to_session"] = str(session)
                else:
                    task_metadata.pop("delegated_to_session", None)
                task.setdefault("tool_outputs", []).append(CONFIG["delegate_reason"])
                if repository is not None:
                    repository.put_task(
                        self.global_key,
                        task_id,
                        task,
                        self._state_lock_timeout,
                    )
                delegated.append(task_id)

            # Consume only when at least one currently blocking task changed.
            # An early marker naming an unknown task may become meaningful
            # later and must not be burned pre-emptively.
            if delegated:
                consumed[marker_identity] = now
                while len(consumed) > _MAX_CONSUMED_DELEGATION_MARKERS:
                    consumed.pop(next(iter(consumed)))
                metadata["consumed_delegation_markers"] = consumed
                _reset_stop_block_sequence(metadata)
                if repository is None:
                    state["tasks"] = tasks
                state["session_metadata"] = metadata

        if delegated and session and self._cli_type:
            try:
                self._mark_delegation_receipt_claimed(
                    self._cli_type,
                    str(session),
                    delegated,
                )
            except Exception as exc:  # noqa: BLE001 - task state already committed
                logger.warning("Could not mark delegation receipt claimed: %s", exc)

        for task_id in delegated:
            self.log_event(
                "DELEGATE",
                f"task#{task_id}",
                CONFIG["delegate_reason"],
                "delegated",
                {"session": session} if session else None,
            )
        return delegated

    def get_plan_approval_injection(self, ctx) -> Optional[str]:
        """Get plan task context as injection string for plan acceptance.

        Returns string for caller to embed in ctx.respond(), avoiding
        abstraction inversion (building dict then unpacking it).

        Returns:
            Injection string with task context, or None if no tasks linked.
        """
        plan_key = getattr(ctx, "plan_arguments", "")
        if not plan_key:
            return None

        plan_tasks = self.get_plan_tasks(plan_key, incomplete_only=True)
        if not plan_tasks:
            return None

        task_lines = []
        for task in plan_tasks[: self.config.max_resume_tasks]:
            status_icon = {"in_progress": "...", "pending": "o", "paused": "||"}.get(task["status"], "?")
            task_lines.append(f"  - Task #{task['id']}: {task['subject']} ({status_icon} {task['status']})")

        if not task_lines:
            return None

        remaining = len(plan_tasks) - len(task_lines)
        if remaining > 0:
            task_lines.append(f"\n  ... and {remaining} more tasks")

        task_context = "\n".join(task_lines)
        return (
            f"\n## Plan Accepted - Task Context\n\n"
            f"{len(plan_tasks)} task(s):\n\n{task_context}\n\n"
            f"Use {{task_list}} to see all tasks. You CANNOT stop until all tasks "
            f"are completed or deleted.\n"
        )

    # === CLI Interface (Typer-like patterns - class methods) ===

    @classmethod
    def cli_status(cls, session_id: str | None = None, verbose: bool = False, output_format: str = "text") -> int:
        """Show task status for session (CLI command).

        Args:
            session_id: Session ID to show (None = current/latest)
            verbose: Show full task details including metadata
            output_format: Output output_format ('text', 'json', 'table')

        Returns:
            Exit code (0 = success, 1 = error)
        """
        try:
            # Auto-detect session ID if not provided
            if not session_id:
                session_id = (
                    resolve_standalone_session_identity().session_id
                )

            manager = cls(session_id=session_id)
            tasks = manager.tasks

            if output_format == "json":
                # Wrap tasks in metadata for CLI usability
                incomplete_count = len([
                    task for task in tasks.values()
                    if task["status"] not in FINISHED_TASK_STATUSES
                ])
                output = {"session_id": session_id, "total_tasks": len(tasks), "incomplete_tasks": incomplete_count, "tasks": tasks}
                print(json.dumps(output, indent=2))
                return 0

            elif output_format == "table":
                # Simple text table
                prioritized = manager.get_prioritized_tasks()
                print(f"Task Status - Session {session_id[:8]}...")
                print()
                for task in prioritized:
                    status_icon = {"in_progress": "🔄", "pending": "⏸️", "paused": "⏯️", "completed": "✅", "deleted": "🗑️", "ignored": "🚫"}.get(
                        task["status"], "❓"
                    )
                    print(f"  {task['id']}: {status_icon} {task['subject']} ({task['status']})")
                return 0

            else:  # output_format == 'text'
                # Simple text output (default)
                incomplete = manager.get_incomplete_tasks(exclude_blocking=True)

                print(f"Session: {session_id}")
                print(f"Total tasks: {len(tasks)}")
                print(f"Incomplete: {len(incomplete)}")

                if verbose and incomplete:
                    print("\nIncomplete Tasks:")
                    for task in manager.get_prioritized_tasks():
                        if task["status"] not in cls.NON_BLOCKING_STATUSES:
                            print(f"\n  Task #{task['id']}: {task['subject']}")
                            print(f"    Status: {task['status']}")
                            print(f"    Created: {datetime.fromtimestamp(task['created_at']).isoformat()}")
                            if task["blockedBy"]:
                                print(f"    Blocked by: {task['blockedBy']}")

                return 0

        except Exception as e:
            print(f"Error showing task status: {e}")
            return 1

    @classmethod
    def cli_export(cls, session_id: str, output_path: str, output_format: str = "json", include_completed: bool = False) -> int:
        """Export task data to file (CLI command).

        Args:
            session_id: Session ID to export
            output_path: Output file path
            output_format: Export output_format ('json', 'csv', 'markdown')
            include_completed: Include completed/deleted tasks

        Returns:
            Exit code (0 = success, 1 = error)
        """
        try:
            manager = cls(session_id=session_id)
            tasks = manager.tasks

            if not include_completed:
                tasks = {k: v for k, v in tasks.items() if v["status"] not in cls.COMPLETED_STATUSES}

            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)

            if output_format == "json":
                output_file.write_text(
                    json.dumps({"session_id": session_id, "exported_at": time.time(), "tasks": tasks, "plan_tasks_map": manager.plan_tasks_map}, indent=2),
                    encoding="utf-8",
                )

            elif output_format == "csv":
                import csv

                with open(output_file, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=["id", "subject", "status", "created_at", "blockedBy"])
                    writer.writeheader()
                    for task in tasks.values():
                        writer.writerow(
                            {
                                "id": task["id"],
                                "subject": task["subject"],
                                "status": task["status"],
                                "created_at": datetime.fromtimestamp(task["created_at"]).isoformat(),
                                "blockedBy": ",".join(task.get("blockedBy", [])),
                            }
                        )

            elif output_format == "markdown":
                md_lines = [
                    f"# Task Export - Session {session_id}",
                    f"\nExported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    f"\nTotal tasks: {len(tasks)}",
                    "\n## Tasks\n",
                ]
                for task in tasks.values():
                    md_lines.append(f"### Task #{task['id']}: {task['subject']}")
                    md_lines.append(f"- **Status**: {task['status']}")
                    md_lines.append(f"- **Description**: {task['description']}")
                    if task.get("blockedBy"):
                        md_lines.append(f"- **Blocked by**: {', '.join(task['blockedBy'])}")
                    md_lines.append("")

                output_file.write_text("\n".join(md_lines), encoding="utf-8")

            print(f"Exported {len(tasks)} tasks to {output_path}")
            return 0

        except Exception as e:
            print(f"Error exporting tasks: {e}")
            return 1

    @classmethod
    def cli_clear(cls, session_id: str | None = None, all_sessions: bool = False, confirm: bool = True) -> int:
        """Clear task data (CLI command with confirmation).

        Args:
            session_id: Session ID to clear (None = current)
            all_sessions: Clear all sessions (ignores session_id)
            confirm: Prompt for confirmation before clearing

        Returns:
            Exit code (0 = success, 1 = error, 2 = cancelled)
        """
        try:
            config = TaskLifecycleConfig.load()

            if all_sessions:
                if confirm and not can_prompt():
                    print("⚠️ Refusing to clear all sessions in non-interactive mode")
                    print("Use --no-confirm flag to proceed")
                    return 2

                state_manager = get_session_manager()
                session_keys = state_manager.list_sessions(
                    namespace="task_lifecycle"
                )
                receipt_present = (
                    TASK_DELEGATION_RECEIPT_SESSION
                    in state_manager.list_sessions()
                )
                receipt_sessions = _delegation_receipt_sessions_for_parents()
                sessions_dir = config.storage_dir
                session_dirs = (
                    [d for d in sessions_dir.iterdir() if d.is_dir()]
                    if sessions_dir.exists()
                    else []
                )
                if not session_keys and not session_dirs and not receipt_present and not receipt_sessions:
                    print("No task data found.")
                    return 0

                if confirm:
                    print(
                        "⚠️  WARNING: About to clear "
                        f"{len(session_keys) + len(session_dirs)} task session(s)"
                    )
                    response = input("Type 'yes' to confirm: ")
                    if response.lower() != "yes":
                        print("Cancelled.")
                        return 2

                import shutil

                state_manager.clear_sessions_atomically(
                    (
                        *session_keys,
                        *receipt_sessions,
                        *((TASK_DELEGATION_RECEIPT_SESSION,) if receipt_present else ()),
                    )
                )
                for session_dir in session_dirs:
                    shutil.rmtree(session_dir)

                print(
                    f"Cleared {len(session_keys) + len(session_dirs)} session(s)"
                )
                return 0

            else:
                if not session_id:
                    session_id = (
                        resolve_standalone_session_identity().session_id
                    )

                manager = cls(session_id=session_id)
                tasks = manager.tasks
                task_count = len(tasks)

                if confirm:
                    if not can_prompt():
                        print("⚠️ Refusing to clear session in non-interactive mode")
                        print("Use --no-confirm flag to proceed")
                        return 2
                    print(f"⚠️  WARNING: About to clear {task_count} task(s) from session {session_id[:8]}...")
                    response = input("Type 'yes' to confirm: ")
                    if response.lower() != "yes":
                        print("Cancelled.")
                        return 2

                import shutil

                storage_dir = config.storage_dir / session_id
                # SessionStateManager owns the backend-specific clear scope:
                # one cascading transaction for SQLite, one locked save for
                # JSON. The CLI does not manually clear persistence internals.
                state_manager = get_session_manager()
                def remove_parent_receipts(receipt_state):
                    receipts = copy.deepcopy(
                        receipt_state.get(_DELEGATION_RECEIPTS_FIELD, {})
                    )
                    kept = {
                        key: receipt
                        for key, receipt in receipts.items()
                        if not isinstance(receipt, dict)
                        or (
                            receipt.get("parent_session_id") != manager.session_id
                            and receipt.get("parent_global_key") != manager.global_key
                        )
                    }
                    if kept != receipts:
                        if kept:
                            receipt_state[_DELEGATION_RECEIPTS_FIELD] = kept
                        else:
                            receipt_state.clear()

                receipt_sessions = _delegation_receipt_sessions_for_parents(
                    (manager.global_key,)
                )
                state_manager.clear_sessions_atomically(
                    (manager.global_key, *receipt_sessions),
                    related_session=TASK_DELEGATION_RECEIPT_SESSION,
                    related_updater=remove_parent_receipts,
                    timeout=manager._state_lock_timeout,
                )

                if storage_dir.exists():
                    shutil.rmtree(storage_dir)

                print(f"Cleared {task_count} task(s) from session")
                return 0

        except Exception as e:
            print(f"Error clearing tasks: {e}")
            return 1

    @classmethod
    def cli_gc(
        cls,
        archive: bool = True,
        dry_run: bool = False,
        pattern: str = "*",
        ttl_days: int | None = None,
        config: TaskLifecycleConfig | None = None,
        confirm: bool = True,
        current_session_id: str | None = None,
    ) -> int:
        """Garbage-collect stale task lifecycle data (archive-then-purge).

        JSON-ONLY COMPATIBILITY PATH - NOT DEPRECATED while JSON remains a
        supported fresh-install and rollback backend. Replacement for SQLite:
        StateRetention in ``session_manager.py``; its docstring links back to
        this method. On SQLite this method hands the request to
        ``_sqlite_gc`` (StateRetention.sweep_sessions) before any JSON
        discovery, because the code below scans legacy flat JSON keys.

        Retire when JSON authority is no longer supported and every destructive
        caller uses ``StateRetention`` with explicit age, archive, confirmation,
        and deletion policy. Until then this is the JSON maintenance path.

        ⚠️  DESTRUCTIVE OPERATION - PERMANENTLY DELETES SESSION DATA ⚠️

        SAFETY GUARANTEES (fail-safe design):
        1. Protects the resolved current harness session - NEVER deleted
        2. Skips sessions with any task not in PRUNABLE_STATUSES (pending,
           in_progress, paused, delegated, delegation-returned)
        3. Respects TTL - only cleans sessions older than ttl_days
        4. Uses session_state() for lock protection (never bypasses locking)
        5. Archives non-empty data to JSON before deletion (restorable backup)
        6. dry_run=True preview mode - reports without modifications
        7. Archive-then-clear under one ``session_state()`` backend scope
        8. Requires confirmation by default (confirm=True) - must type 'yes' to proceed

        LIFECYCLE & USAGE:
        - GC is manual-only (never automatic) - user controls when to clean
        - Daemon doesn't auto-GC - state files persist until user runs this
        - Recommended: Run with dry_run=True first to preview
        - Safe to run anytime - protections prevent active session damage
        - ALWAYS archives before deletion unless archive=False (NOT recommended)

        CRITICAL ORDERING (prevents corruption):
        1. Find session IDs matching pattern
        2. For each session:
           a. Check if current session → skip (protected)
           b. Acquire the backend scope via session_state(global_key, timeout=2s)
           c. Read tasks, check incomplete → skip if found
           d. Check age against TTL → skip if too recent
           e. Archive to JSON (if archive=True) - within lock
           f. Clear state content (state.clear()) - within lock
           g. Release lock (exit session_state context)
           h. Delete state files from disk
           i. Clean empty audit directories
        3. Report summary with skip reasons and error guidance

        Archive location: {config.storage_dir}/archive/{session_id}.json
        Archive format: JSON with session_id, schema_version, tasks, metadata

        Args:
            archive: Export data to JSON before deleting (default: True for safety)
            dry_run: Report what would be cleaned without modifying (default: False)
            pattern: Glob pattern for session IDs (default: "*" = all sessions)
            ttl_days: Only GC sessions older than this (default: config.task_ttl_days)
            config: Config override for testing (default: load from ~/.autorun/)
            confirm: Require confirmation before deletion (default: True for safety)
            current_session_id: Explicit current session to protect. Standalone
                CLI dispatch resolves this from shared platform metadata.

        Returns:
            0 on success, 1 on fatal error, 2 on user cancellation

        Examples:
            # Preview before cleaning (RECOMMENDED)
            TaskLifecycle.cli_gc(dry_run=True)

            # Clean only test sessions immediately (ignore TTL)
            TaskLifecycle.cli_gc(pattern="test-*", ttl_days=0)

            # Clean without archiving (DANGEROUS - permanent data loss)
            TaskLifecycle.cli_gc(archive=False)

            # Clean old sessions, keep last 7 days
            TaskLifecycle.cli_gc(ttl_days=7)
        """
        import fnmatch
        import shutil
        from .session_manager import get_session_manager

        try:
            config = config or cls._get_config()
            ttl = ttl_days if ttl_days is not None else config.task_ttl_days
            if ttl < 0:
                print(f"Error: --ttl must be 0 or more days (got {ttl}). Nothing was deleted.")
                return 1
            ttl_seconds = ttl * _SECONDS_PER_DAY
            mgr = get_session_manager()
            sessions_dir = mgr.state_dir
            # JSON key prefix for task lifecycle sessions (matches global_key format below)
            json_key_prefix = TASK_LIFECYCLE_SESSION_PREFIX
            if current_session_id is None:
                try:
                    current_session_id = (
                        resolve_standalone_session_identity().session_id
                    )
                except SessionIdentityResolutionError as exc:
                    if exc.reason != "missing":
                        print(
                            "Task GC refused because the current session is "
                            f"not unambiguous: {exc}"
                        )
                        return 1
            current = current_session_id or ""
            if not current:
                print("No current harness session found: no session is protected as current.")
            archive_dir = config.storage_dir / "archive"

            # The code below scans and rewrites daemon_state.json prefixes
            # directly; on SQLite it would find nothing. StateRetention is the
            # SQLite archive-before-delete authority.
            if get_session_manager().task_repository() is not None:
                return cls._sqlite_gc(
                    archive=archive, dry_run=dry_run, pattern=pattern,
                    ttl_seconds=ttl_seconds, archive_dir=archive_dir,
                    confirm=confirm, current=current,
                )

            # Show prominent warning banner (unless dry-run)
            if not dry_run:
                print("\n" + "=" * 70)
                print("⚠️  TASK LIFECYCLE GARBAGE COLLECTION - DESTRUCTIVE OPERATION  ⚠️")
                print("=" * 70)
                print()
                print("This will PERMANENTLY DELETE task data from old sessions.")
                print()
                print("Safety protections:")
                print("  ✓ Resolved current harness session will NOT be deleted")
                print("  ✓ Sessions with incomplete tasks will be SKIPPED")
                print("  ✓ Sessions newer than TTL will be SKIPPED")
                if archive:
                    print(f"  ✓ Data will be ARCHIVED to: {archive_dir}/")
                else:
                    print("  ✗ NO ARCHIVING - Data will be PERMANENTLY LOST")
                print()
                print(f"Pattern: {pattern}")
                print(f"TTL: {ttl} days")
                print()

            # Find session IDs from daemon_state.json (filelock+JSON backend).
            # Keys in the JSON are "{global_key}/{subkey}" where
            # global_key = "__task_lifecycle__{sid}".
            sids = set()
            state_file = sessions_dir / "daemon_state.json"
            if state_file.exists():
                try:
                    raw = json.loads(state_file.read_text(encoding="utf-8"))
                    for json_key in raw:
                        # json_key = "__task_lifecycle__{sid}/{subkey}"
                        if json_key.startswith(json_key_prefix) and "/" in json_key:
                            rest = json_key[len(json_key_prefix) :]
                            sid = rest.split("/")[0]
                            if sid and fnmatch.fnmatch(sid, pattern):
                                sids.add(sid)
                except (ValueError, OSError):
                    pass

            if not sids:
                print(f"No task lifecycle sessions matching '{pattern}'.")
                return 0

            # Preview sessions that will be processed
            print(f"Found {len(sids)} session(s) matching pattern '{pattern}':")
            for sid in sorted(sids):
                is_current = sid == current
                marker = " (CURRENT - will skip)" if is_current else ""
                print(f"  • {sid[:24]}...{marker}")
            print()

            # Confirmation prompt (unless dry-run or confirm=False)
            if not dry_run and confirm:
                if not can_prompt():
                    print("⚠️  ERROR: Cannot prompt for confirmation in non-interactive mode")
                    print("Use --dry-run to preview, or --no-confirm to proceed")
                    return 2

                print("=" * 70)
                print("FINAL CONFIRMATION")
                print("=" * 70)
                print(f"About to garbage-collect up to {len(sids)} session(s).")
                print()
                if archive:
                    print(f"Archived data will be saved to: {archive_dir}/")
                    print("You can restore from archives if needed.")
                else:
                    print("⚠️  NO ARCHIVING - Data will be PERMANENTLY LOST")
                print()
                response = input("Type 'yes' to proceed, anything else to cancel: ")
                print()

                if response.lower() != "yes":
                    print("Cancelled. No changes made.")
                    return 2

            archived = cleared = skip_active = skip_incomplete = skip_young = errors = 0

            # Step 2: Process sessions from one shared-state snapshot, then
            # bulk-delete eligible prefixes in one atomic save. The old
            # per-session loop rewrote daemon_state.json once per session; with
            # thousands of historical task sessions that made GC itself slow
            # enough to contend with live hooks.
            error_details = []
            candidates: dict[str, dict] = {}
            clear_sids: list[str] = []

            from .session_manager import all_session_state

            try:
                with all_session_state(timeout=2.0, write=False) as raw:
                    for sid in sorted(sids):
                        if sid == current:
                            skip_active += 1
                            if dry_run:
                                print(f"  PROTECT {sid[:12]}... (current session - never GC active)")
                            continue

                        prefix = f"{TASK_LIFECYCLE_SESSION_PREFIX}{sid}/"
                        state_snapshot = {key[len(prefix) :]: value for key, value in raw.items() if key.startswith(prefix)}
                        tasks = state_snapshot.get("tasks", {})

                        incomplete = [t for t in tasks.values() if isinstance(t, dict) and t.get("status") not in cls.PRUNABLE_STATUSES]
                        if incomplete:
                            skip_incomplete += 1
                            if dry_run:
                                print(f"  SKIP    {sid[:12]}... ({len(incomplete)} incomplete)")
                            continue

                        if tasks:
                            newest = max(
                                (t.get("updated_at", 0) for t in tasks.values() if isinstance(t, dict)),
                                default=0,
                            )
                            age = time.time() - newest
                            if age < ttl_seconds:
                                skip_young += 1
                                if dry_run:
                                    print(f"  SKIP    {sid[:12]}... ({age / _SECONDS_PER_DAY:.1f}d old)")
                                continue

                        if dry_run:
                            label = "ARCHIVE+" if archive and tasks else ""
                            print(f"  {label}CLEAR  {sid[:12]}... ({len(tasks)} tasks)")
                            if archive and tasks:
                                archived += 1
                            cleared += 1
                            continue

                        candidates[sid] = state_snapshot
                        clear_sids.append(sid)

                if not dry_run:
                    if archive:
                        archive_dir.mkdir(parents=True, exist_ok=True)
                    for sid, state_snapshot in candidates.items():
                        tasks = state_snapshot.get("tasks", {})
                        try:
                            if archive and tasks:
                                (archive_dir / f"{sid}.json").write_text(
                                    json.dumps(
                                        {
                                            "session_id": sid,
                                            "archived_at": time.time(),
                                            "schema_version": state_snapshot.get("schema_version", 1),
                                            "session_metadata": state_snapshot.get("session_metadata", {}),
                                            "tasks": tasks,
                                        },
                                        indent=2,
                                        default=str,
                                    ),
                                    encoding="utf-8",
                                )
                                archived += 1
                        except PermissionError as e:
                            errors += 1
                            error_details.append((sid, "Permission", str(e)[:LOG_SNIPPET_MAX_LEN]))
                            print(f"  ERROR   {sid[:12]}... Permission denied")
                            clear_sids.remove(sid)
                        except Exception as e:
                            errors += 1
                            error_details.append((sid, type(e).__name__, str(e)[:LOG_SNIPPET_MAX_LEN]))
                            print(f"  ERROR   {sid[:12]}... {type(e).__name__}: {e}")
                            clear_sids.remove(sid)

                    if clear_sids:
                        clear_set = set(clear_sids)
                        with all_session_state(timeout=10.0, write=True) as raw:
                            cleared_parent_keys = {
                                f"{TASK_LIFECYCLE_SESSION_PREFIX}{sid}"
                                for sid in clear_set
                            }
                            for sid in sorted(clear_set):
                                prefix = f"{TASK_LIFECYCLE_SESSION_PREFIX}{sid}/"
                                for key in [k for k in raw if k.startswith(prefix)]:
                                    del raw[key]
                                audit_dir = config.storage_dir / sid
                                if audit_dir.exists():
                                    shutil.rmtree(audit_dir, ignore_errors=True)
                                cleared += 1
                            receipt_roots = {
                                key.split("/", 1)[0]
                                for key, value in raw.items()
                                if key.startswith(TASK_DELEGATION_RECEIPT_SESSION_PREFIX)
                                and key.endswith("/receipt")
                                and isinstance(value, dict)
                                and value.get("parent_global_key") in cleared_parent_keys
                            }
                            for receipt_root in receipt_roots:
                                for key in [k for k in raw if k.startswith(receipt_root + "/")]:
                                    del raw[key]
                            legacy_key = f"{TASK_DELEGATION_RECEIPT_SESSION}/{_DELEGATION_RECEIPTS_FIELD}"
                            legacy = raw.get(legacy_key)
                            if isinstance(legacy, dict):
                                kept = {
                                    key: receipt
                                    for key, receipt in legacy.items()
                                    if not isinstance(receipt, dict)
                                    or receipt.get("parent_global_key") not in cleared_parent_keys
                                }
                                if kept:
                                    raw[legacy_key] = kept
                                else:
                                    raw.pop(legacy_key, None)

            except PermissionError as e:
                errors += 1
                error_details.append(("*", "Permission", str(e)[:LOG_SNIPPET_MAX_LEN]))
                print("  ERROR   bulk GC... Permission denied")
            except Exception as e:
                errors += 1
                error_details.append(("*", type(e).__name__, str(e)[:LOG_SNIPPET_MAX_LEN]))
                print(f"  ERROR   bulk GC... {type(e).__name__}: {e}")

            # Step 8: Summary with actionable guidance
            verb = "Would" if dry_run else "Did"
            print(f"\n=== Task Lifecycle GC {'(dry run) ' if dry_run else ''}===")
            print(f"Scanned: {len(sids)} matching pattern '{pattern}'")

            # Skipped sessions (grouped for clarity)
            if skip_active or skip_incomplete or skip_young:
                print("\nProtected/Skipped:")
                if skip_active:
                    print(f"  • Current session: {skip_active} (resolved harness session - never GC)")
                if skip_incomplete:
                    print(f"  • Incomplete tasks: {skip_incomplete} (active work in progress)")
                if skip_young:
                    print(f"  • Too recent: {skip_young} (age < {ttl}d TTL)")

            # Actions taken
            print("\nActions:")
            print(f"  • {verb} archive: {archived} sessions")
            print(f"  • {verb} clear: {cleared} sessions")

            # Error reporting with actionable guidance
            if errors:
                print(f"\n⚠️  Errors: {errors} sessions failed to GC")
                print("\nFailed sessions:")
                for sid, err_type, msg in error_details[:5]:
                    print(f"  • {sid[:16]}... {err_type}: {msg}")
                if len(error_details) > 5:
                    print(f"  • ... and {len(error_details) - 5} more errors")

                print("\n━━━ TROUBLESHOOTING ━━━")
                print("Common Issues:")
                print("  1. Permission denied:")
                print(f"     → Check file ownership: ls -la {sessions_dir}")
                # Name the directory that actually failed, not the default. The
                # line above already resolved it, and a session with the state
                # directory redirected would otherwise be told to chown a path
                # unrelated to its error.
                print(f"     → Fix: sudo chown -R $USER {sessions_dir}")
                print("  2. Lock timeout:")
                print("     → Daemon actively using session (wait or refine pattern)")
                print("     → Check: ps aux | grep autorun")
                print("  3. db type errors:")
                print("     → Backend detection failed (corrupted state)")
                print("     → Try: pattern='*' to see all, or delete manually")

            # Archive location
            if archived and not dry_run:
                print(f"\n📦 Archive saved to: {archive_dir}/")
                print("To restore archived session:")
                print("  1. Load {session_id}.json")
                print("  2. Use TaskLifecycle API to recreate tasks")
                print("  3. Or manually inspect archived task data")

            return 0

        except Exception as e:
            print(f"\n❌ FATAL GC ERROR: {type(e).__name__}")
            print(f"\n{e}")
            print("\n━━━ BUG REPORT INFO ━━━")
            print(f"Pattern: '{pattern if 'pattern' in locals() else 'unknown'}'")
            print(f"TTL: {ttl_days}d")
            if "sessions_dir" in locals():
                print(f"Sessions dir: {sessions_dir}")
            print("\nPlease report at: https://github.com/ahundt/autorun/issues")
            import traceback

            traceback.print_exc()  # CLI only - defaults to stderr but not in hook path
            return 1

    @staticmethod
    def _sqlite_gc(*, archive: bool, dry_run: bool, pattern: str,
                   ttl_seconds: float, archive_dir: Path, confirm: bool,
                   current: str) -> int:
        """``autorun task gc`` on SQLite: StateRetention.sweep_sessions.

        The sweep expires whole sessions in its sweepable namespaces, keeps
        any with unfinished tasks, and writes its archive before it deletes.
        ``--pattern`` and ``--no-archive`` have no counterpart there, so they
        are refused rather than silently ignored. Both spellings of the
        current session (the harness id and its task-lifecycle key) are
        protected.
        """
        import os

        from .session_manager import (
            RetentionPolicy,
            SQLiteStore,
            StateRetention,
            _state_dir_key,
        )

        if pattern != "*":
            print(
                f"Task GC on the SQLite backend cannot honor --pattern {pattern!r}: "
                "it sweeps every expired session. Nothing was deleted."
            )
            return 1
        if not archive:
            print(
                "Task GC on the SQLite backend cannot honor --no-archive: it "
                "archives every session before deleting it. Nothing was deleted."
            )
            return 1

        store = SQLiteStore(os.path.join(_state_dir_key(), "daemon_state.sqlite3"))
        store.initialize()
        protected = (
            {current, f"{TASK_LIFECYCLE_SESSION_PREFIX}{current}"} if current else set()
        )

        def sweep(delete: bool) -> dict:
            return StateRetention(store, RetentionPolicy(
                session_max_age_seconds=ttl_seconds,
                archive_dir=archive_dir,
                delete=delete,
                protected_sessions=protected,
            )).sweep_sessions()

        eligible = sweep(False)["eligible"]
        days = ttl_seconds / _SECONDS_PER_DAY
        print(f"{len(eligible)} finished session(s) older than {days:g} days"
              + (":" if eligible else "."))
        for session_id in eligible:
            print(f"  {session_id}")
        if dry_run:
            print("Dry run: nothing archived or deleted.")
            return 0
        if not eligible:
            return 0

        if confirm:
            if not can_prompt():
                print("⚠️  ERROR: Cannot prompt for confirmation in non-interactive mode")
                print("Use --dry-run to preview, or --no-confirm to proceed")
                return 2
            response = input(
                f"Archive to {archive_dir}/ and delete {len(eligible)} session(s)? "
                "Type 'yes' to proceed: "
            )
            if response.lower() != "yes":
                print("Cancelled. No changes made.")
                return 2

        report = sweep(True)
        if report["archive"]:
            print(f"Archived to: {report['archive']}")
        print(f"Deleted: {len(report['deleted'])} session(s)")
        if report["changed_after_archive"]:
            print("Kept, because they changed after archiving: "
                  + ", ".join(report["changed_after_archive"]))
        return 0

    @classmethod
    def _get_config(cls) -> TaskLifecycleConfig:
        """Get config (DRY helper for CLI methods)."""
        return TaskLifecycleConfig.load()

    @classmethod
    def cli_configure(cls, interactive: bool = False) -> int:
        """Show configuration (interactive if TTY or forced).

        Args:
            interactive: Force interactive mode even in non-TTY

        Returns:
            Exit code (0 = success, 1 = error, 2 = non-interactive)
        """
        try:
            config = TaskLifecycleConfig.load()

            # Always show current settings
            print("Task Lifecycle Configuration")
            print("============================")
            print()
            print("Current settings:")
            print(f"  Enabled: {config.enabled}")
            print(f"  Storage directory: {config.storage_dir}")
            print(f"  Max resume tasks: {config.max_resume_tasks}")
            print(f"  Stop block max count: {config.stop_block_max_count}")
            print(f"  Task TTL (days): {config.task_ttl_days}")
            print(f"  Debug logging: {config.debug_logging}")
            print()

            # Check if interactive mode possible
            if not interactive and not can_prompt():
                print("(Non-interactive mode - showing current settings only)")
                print("Use --interactive flag to modify settings")
                return 0

            # Prompt to modify
            response = input("Modify settings? (y/n): ")
            if response.lower() != "y":
                return 0

            # Interactive prompts
            enabled = input(f"Enable task lifecycle? (y/n) [current: {'y' if config.enabled else 'n'}]: ")
            if enabled.lower() in ("y", "n"):
                config.enabled = enabled.lower() == "y"

            max_tasks = input(f"Max resume tasks [current: {config.max_resume_tasks}]: ")
            if max_tasks.strip():
                config.max_resume_tasks = int(max_tasks)

            max_blocks = input(f"Stop block max count [current: {config.stop_block_max_count}]: ")
            if max_blocks.strip():
                config.stop_block_max_count = int(max_blocks)

            ttl = input(f"Task TTL (days) [current: {config.task_ttl_days}]: ")
            if ttl.strip():
                config.task_ttl_days = int(ttl)

            debug = input(f"Enable debug logging? (y/n) [current: {'y' if config.debug_logging else 'n'}]: ")
            if debug.lower() in ("y", "n"):
                config.debug_logging = debug.lower() == "y"

            # Save
            config.save()
            print()
            print("✅ Configuration saved to:", CONFIG_PATH)
            return 0

        except Exception as e:
            print(f"Error configuring: {e}")
            return 1

    @classmethod
    def cli_enable(cls) -> int:
        """Enable task lifecycle (CLI command).

        Returns:
            Exit code (0 = success, 1 = error)
        """
        try:
            config = TaskLifecycleConfig.load()
            config.enabled = True
            config.save()
            print("✅ Task lifecycle tracking enabled")
            return 0
        except Exception as e:
            print(f"Error enabling: {e}")
            return 1

    @classmethod
    def cli_disable(cls) -> int:
        """Disable task lifecycle (CLI command).

        Returns:
            Exit code (0 = success, 1 = error)
        """
        try:
            config = TaskLifecycleConfig.load()
            config.enabled = False
            config.save()
            print("✅ Task lifecycle tracking disabled")
            return 0
        except Exception as e:
            print(f"Error disabling: {e}")
            return 1


# === Module-Level Functions (for registration and CLI) ===


def is_enabled() -> bool:
    """Check if task lifecycle tracking is enabled."""
    return TaskLifecycleConfig.load().enabled


def _report_tracking_failure(ctx: EventContext, error: Exception) -> None:
    """Say out loud that a task operation never reached autorun's mirror.

    Sent to the AI as well as the user because only the AI can repair it: it
    still holds the intent and can re-issue the call, while the harness's own
    task list already shows the operation as done.
    """
    tool_input = ctx.tool_input or {}
    task_id = tool_input.get("taskId") or tool_input.get("id")
    status = tool_input.get("status")
    target = f"task #{task_id}" if task_id else f"the {ctx.tool_name} call"
    if status:
        target += f" (status={status})"
    try:
        ctx.add_chain_notification(
            f"⚠️ autorun did not record {target}: {error}. The tool call itself "
            "succeeded, so autorun's task mirror is now out of date — re-issue "
            "the same update to resync it, otherwise a later Stop can block on "
            "a task that is already finished.",
            channel="both",
        )
    except Exception as notify_error:  # noqa: BLE001 - reporting must not block the tool
        logger.warning(f"Could not report task tracking failure: {notify_error}")


def register_hooks(app_instance) -> None:
    """Register all task lifecycle hooks (if enabled).

    Uses class-based handlers for DRY code organization.
    Follows plan_export.py pattern for consistency.
    """
    lifecycle_hooks_registered = is_enabled()

    @app_instance.on("Stop")
    def keep_subagent_stop_terminal_when_tasks_unregistered(
        ctx: EventContext,
    ) -> dict | None:
        """Preserve child liveness when this app has no lifecycle Stop gate."""
        if ctx.event == "SubagentStop" and not lifecycle_hooks_registered:
            return ctx.allow()
        return None

    @app_instance.on("SessionStart")
    def record_fork_at_session_start(ctx: EventContext) -> None:
        """Record a fork once and say what it means, before any handler answers."""
        try:
            if record_conversation_fork(ctx):
                ctx.add_chain_notification(_fork_notice(ctx.cli_type), channel="both")
        except Exception as error:  # noqa: BLE001 - unknown stays "main"
            logger.warning(f"Could not record a forked conversation: {error}")
        return None

    if not lifecycle_hooks_registered:
        return

    @app_instance.on("UserPromptSubmit")
    def reset_stop_sequence_on_user_prompt(ctx: EventContext) -> Optional[Dict]:
        """A prompt the user wrote: restart the Stop loop guard and end awaiting-user.

        Registered first on UserPromptSubmit and always returns None: the
        chain is first-wins, so a handler that answers the prompt (the
        prompt-time cache check) must never run before this state change.
        """
        if not is_enabled():
            return None
        try:
            TaskLifecycle(ctx=ctx).restore_awaiting_user(ctx)
        except Exception as error:
            logger.warning(f"Task Stop-sequence prompt reset error: {error}")
            # Tasks awaiting the user keep waiting until a later prompt
            # restores them; say so rather than leave them exempt silently.
            ctx.add_chain_notification(
                f"⚠️ autorun could not record your reply for tasks waiting on you: {error}. "
                "They stay waiting until your next message is recorded.",
                channel="both",
            )
        return None

    @app_instance.on("PostToolUse")
    def record_agent_spawn_identity(ctx: EventContext) -> Optional[Dict]:
        """Capture the child id from an agent-spawn tool result (ledger)."""
        if not is_enabled():
            return None
        TaskLifecycle(ctx=ctx).record_agent_spawn(ctx)
        return None

    @app_instance.on("PostToolUse")
    def deliver_pending_stop_injection(ctx: EventContext) -> Optional[Dict]:
        """Deliver stop-block message to AI on next PostToolUse (one-shot).

        WHY THIS EXISTS — the established Stop block response uses
        decision+reason, while this PostToolUse replay supplies the same
        actionable text through the context pathway. Current Claude documents
        Stop additionalContext, but switching this safety path requires a live
        visibility and repeated-generation proof. BUG #18534 affects the
        PostToolUse fallback on Claude Code:
             https://github.com/anthropics/claude-code/issues/18534
             Workaround: respond() PATHWAY 2 upgrades channel="ai" → "both" on
             Claude Code so messages reach AI via systemMessage (same-turn only).
             Controlled by CONFIG["AUTORUN_BUG_CLAUDE_CODE_IGNORES_ADDITIONAL_CONTEXT_JSON_ENTRY_BUG_18534_WORKAROUND_ENABLED"].

        HOW IT WORKS:
          handle_stop() sets ctx.pending_stop_injection on EVERY blocked Stop
          (re-arms every time, not just block_count==1 — see the comment
          above ctx.pending_stop_injection in handle_stop() for why). This
          handler fires on the AI's next PostToolUse, delivers the message
          via add_chain_notification(channel="ai"), then clears the flag —
          one-shot per arm.

          Duplicate suppression is scoped to ONE Stop-block generation
          (session_metadata["stop_block_count"]), never to message text.
          Parallel tool calls fire concurrent PostToolUse hooks that can all
          read the armed slot before any of them clears it, printing the same
          block text several times; the atomic claim below lets exactly one
          win per generation.

          Do NOT extend this to skip byte-identical text across generations.
          An AI stuck repeating the same mistake produces an unchanged task
          list and therefore identical text on every block — suppressing that
          would stop feeding it the override actions (/ar:estop,
          /ar:task ignore) from the second block onward, recreating the
          "infinite non-overridable stop failure" the every-block re-arm in
          handle_stop() exists to prevent. Every new Stop block re-delivers.

        HISTORY: enforce_stop_injection (PreToolUse deny) was removed because it
          caused a deadlock — handle_stop re-armed pending_stop_injection on every
          Stop event, and AI text output triggers Stop in Claude Code. See
          plugins.py "DEADLOCK BUG" comment for full details.

        ONGOING ENFORCEMENT after this one-shot delivery:
          check_task_staleness (plugins.py) counts tool calls → reminds at threshold.
          enforce_task_staleness (plugins.py) escalates to warn-then-deny.
        """
        # Only the main agent's Stop arms this (handle_stop returns before arming
        # on SubagentStop), so a subagent's tool call leaves it for the parent:
        # the subagent cannot act on the parent's tasks, and consuming the
        # one-shot here hid it from the parent (2026-11-06).
        if ctx.agent_id:
            return None
        injection = ctx.pending_stop_injection
        if not injection:
            return None
        ctx.pending_stop_injection = None  # Clear so it fires only once per Stop

        manager = TaskLifecycle(ctx=ctx)

        # The injection is text rendered at Stop time, so it names the tasks that
        # blocked THEN. The PostToolUse that delivers it is frequently the very
        # TaskUpdate that resolved them, and replaying it there tells an AI its
        # own fix failed. Observed consequence (Codex prompt-fix session,
        # 2026-08-05): the agent completed its last plan item, saw the identical
        # block echoed by the PostToolUse running on that update, and DELETED the
        # finished item rather than completing it — the plan ended with three
        # entries where it had four. Re-read before speaking.
        #
        # This does not weaken the gate. Stop itself still blocks on the live set
        # (handle_stop), and while anything remains blocking the text is
        # delivered unchanged, override actions included. It only suppresses a
        # message whose own subject is already resolved. The every-generation
        # re-delivery that prevents "infinite non-overridable stop failure" is
        # untouched: that guarantees a NEW block always re-delivers, not that a
        # satisfied one keeps echoing.
        if not manager.get_incomplete_tasks(exclude_blocking=True):
            manager.log_event(
                "STOP_REPLAY_DROPPED",
                "session",
                "Tasks resolved before delivery; stale Stop block not replayed",
                "skipped",
            )
            return None

        claimed = False

        def claim_this_generation(metadata):
            """Compare-and-set inside atomic_update_metadata's lock."""
            nonlocal claimed
            generation = metadata.get("stop_block_count", 0)
            if metadata.get("last_delivered_stop_block_generation") == generation:
                return  # A concurrent PostToolUse already delivered this block.
            metadata["last_delivered_stop_block_generation"] = generation
            claimed = True

        manager.atomic_update_metadata(claim_this_generation)
        if not claimed:
            return None

        # AI_ECHO_CHANNEL, not "ai": the Stop hook already printed this exact
        # text to the user. Under the #18534 workaround channel="ai" is upgraded
        # into systemMessage so it actually reaches the AI, which would print the
        # block a second time. This channel keeps the AI delivery and drops only
        # the duplicate human copy — every generation still re-delivers.
        ctx.add_chain_notification(injection, channel=AI_ECHO_CHANNEL)
        return None  # Flushed by _run_chain → ctx.respond("allow","") → additionalContext

    @app_instance.on("PostToolUse")
    def track_task_operations(ctx: EventContext) -> Optional[Dict]:
        """Track Task tool usage for AI continuation (PostToolUse hook)."""
        role = task_tool_role(_task_cli_hint(ctx), ctx.tool_name)
        if role is None:
            return None
        if not ctx.agent_id and platform_for(ctx.cli_type).task_evidence_from_task_calls:
            # PostToolUse follows a call that ran, so the task tools exist for
            # the model that made it; task gates may enforce while that model
            # answers (EventContext.task_tool_evidence).
            stored = ctx.state_get(EventContext.TASK_TOOLS_OBSERVED, False)
            # A tail that names no model (one huge line, another harness's
            # transcript) keeps a model already recorded rather than erasing it.
            observed = ctx.current_model() or stored or True
            if stored != observed:
                ctx.state_set(EventContext.TASK_TOOLS_OBSERVED, observed)

        try:
            # Instantiate class with auto-detected session ID
            manager = TaskLifecycle(ctx=ctx)

            if role == "plan":
                manager.handle_plan_checklist(ctx)
            elif role == "bulk":
                # Gemini CLI uses write_todos for all task operations.
                # Route based on content: todos in input → bulk create, else taskId → update.
                tool_input = ctx.tool_input or {}
                # An empty list is a full-state write ("no todos left") and
                # must clear this source's records, so route on presence of
                # a list, not on truthiness.
                if isinstance(tool_input.get("todos"), list):
                    manager.handle_bulk_todos(ctx)
                elif tool_input.get("taskId"):
                    manager.handle_task_update(ctx)
                elif "created" in ctx.tool_result_str.lower():
                    manager.handle_task_create(ctx)
                # else: list/get — just update activity below
            elif role == "create":
                manager.handle_task_create(ctx)
            elif role == "update":
                ghost_result = manager.handle_task_update(ctx)
                if ghost_result == "ghost_skip":
                    if manager.config.debug_logging:
                        task_id = ctx.tool_input.get("taskId", "?")
                        status = ctx.tool_input.get("status", "?")
                        tool_result_snippet = ctx.tool_result_str
                        manager.log_event(
                            "GHOST_SKIP_HOOK",
                            task_id,
                            f"requested_status={status} tool_result={tool_result_snippet!r}",
                            status="ignored",
                        )
            elif role == "review":
                # Update last activity timestamp
                def update_activity(metadata):
                    metadata["last_activity"] = time.time()

                manager.atomic_update_metadata(update_activity)

        except Exception as e:
            logger.warning(f"Task tracking error: {e}")
            # Fail open so tracking never blocks a tool — but never silently.
            # The tool call itself succeeded, so nothing else tells the AI that
            # autorun's mirror now disagrees with the harness. Unreported, the
            # mismatch resurfaces much later as a Stop block naming a task that
            # is already finished and can no longer be updated.
            _report_tracking_failure(ctx, e)

        return None  # Always allow tool to complete

    @app_instance.on("SessionStart")
    def request_claude_deferred_task_tools(ctx: EventContext) -> None:
        """Ask Claude to load its deferred task tools once per fresh context.

        REQUIREMENT: gate on ``Platform.gates_mutable_task_tools``, not on a
        harness name. This is the same condition ``_workaround_flag_applies``
        resolves for
        https://github.com/anthropics/claude-code/issues/80305 and
        https://github.com/anthropics/claude-code/issues/80401, so a name
        comparison here would put the two out of step the moment another
        harness in the family deferred the same tools.
        """
        if (
            not is_enabled()
            or not platform_for(ctx.cli_type).gates_mutable_task_tools
            or ctx.source not in {"startup", "clear"}
        ):
            return None

        claimed = False
        claim_value = f"{ctx.source}:v1"

        def claim(metadata):
            nonlocal claimed
            if metadata.get("claude_task_tool_load_instruction") == claim_value:
                return
            metadata["claude_task_tool_load_instruction"] = claim_value
            claimed = True

        try:
            TaskLifecycle(ctx=ctx).atomic_update_metadata(claim)
        except Exception as exc:
            # Loading advice is advisory. A state-store failure must not break
            # Claude's SessionStart hook or suppress later lifecycle handlers.
            logger.warning("Deferred task-tool load instruction was not claimed: %s", exc)
            return None
        if claimed:
            ctx.add_chain_notification(
                _deferred_task_tool_instruction(ctx),
                channel="ai",
            )
        return None

    @app_instance.on("SessionStart")
    def resume_incomplete_tasks(ctx: EventContext) -> Optional[Dict]:
        """Reset consecutive Stop state, then resume incomplete tasks."""
        if not is_enabled():
            return None

        try:
            manager = TaskLifecycle(ctx=ctx)
            manager.atomic_update_metadata(_reset_stop_block_sequence)
            return manager.handle_session_start(ctx)
        except Exception as e:
            logger.warning(f"Task resume detection error: {e}")
            return None  # Fail-open

    @app_instance.on("Stop")
    def prevent_premature_stop(ctx: EventContext) -> Optional[Dict]:
        """Prevent AI from stopping if tasks are incomplete (PRIMARY GOAL)."""
        if not is_enabled():
            # Subagent settlement is a whole-chain liveness invariant, not a
            # task-enforcement feature toggle.
            return ctx.allow() if ctx.event == "SubagentStop" else None

        try:
            manager = TaskLifecycle(ctx=ctx)
            result = manager.handle_stop(ctx)
            # The manager keeps SubagentStop pass-through semantics for direct
            # callers, but the registered app chain must be terminal here so no
            # later Stop policy can deadlock a parent waiting on this child.
            if ctx.event == "SubagentStop" and result is None:
                return ctx.allow()
            return result
        except Exception as e:
            logger.warning(
                f"Stop hook error (fail-open: allowing stop): {e}. "
                f"Session: {getattr(ctx, 'session_id', '?')}. "
                f"If tasks exist but stop was allowed, this exception is the cause."
            )
            # For SubagentStop, pass-through is not enough because later Stop
            # handlers may block; terminate the chain with an explicit allow.
            return ctx.allow() if ctx.event == "SubagentStop" else None

    # NOTE: inject_plan_tasks was removed -- plan task injection is now merged into
    # detect_plan_approval() in plugins.py to avoid first-non-None chain ordering
    # issues (core.py:1097-1107). See Fixes 6-8.
