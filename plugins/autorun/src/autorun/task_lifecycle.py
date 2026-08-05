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

from typing import Optional, Dict, List, Callable, Literal
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
from .core import AI_ECHO_CHANNEL, EventContext, format_command_for_cli, logger
from .session_manager import (
    DEFAULT_SESSION_TIMEOUT,
    MISSING,
    get_session_manager,
    session_state,
)
from .config import (
    CONFIG,
    LOG_SNIPPET_MAX_LEN,
    TASK_PAUSE_DEFAULT_TTL_SECONDS,
)
from .platforms import (
    SessionIdentityResolutionError,
    agent_spawn_tools_for,
    platform_for,
    resolve_standalone_session_identity,
    task_tool_role,
)
from .task_pause import (
    task_pause_allows_stop,
    task_pause_guidance,
)
from .task_status import (
    COMPLETED_TASK_STATUSES,
    NON_BLOCKING_TASK_STATUSES,
    PRUNABLE_TASK_STATUSES,
    task_status_policy,
)


_SECONDS_PER_DAY: int = 24 * 3600  # pure unit conversion (not a config value)
_STAGE3_OVERFLOW_NAME_COUNT: int = 3  # task names shown in stage-3 overflow message
_MAX_CONSUMED_DELEGATION_MARKERS: int = 256
_SESSION_START_CLAIM_DIGEST_HEX_CHARS: int = 16


# === Stop / Resume action fragments (assembled at call site) ===
_ACT_REVIEW = "{task_list}"
_ACT_COMPLETE = '{task_update}({task_id_param}="X", status="completed")'
_ACT_DELEGATE = '{task_update}({task_id_param}="X", status="delegated")'
_ACT_DISCARD = '{task_update}({task_id_param}="X", status="deleted")'
# AI-callable escape route — emitted on EVERY Stop block so the AI does
# not give up on block #1 when a task is provably stale. The actual marker
# only takes effect after ghost_clear_min_consecutive_blocks identical blocks
# (anti-abuse: prevents instant single-block clears), but discoverability
# must not depend on the threshold being met.
# The marker literal lives in CONFIG["ghost_clear_marker_template"]
# (single source of truth, enforced by test_marker_literal_single_source_of_truth).
# {task_list}/{task_update}/{task_progress} are resolved per harness by
# core.py's placeholder substitution. Spelling either tool literally here sent
# Claude's tool names to every harness — a Codex transcript showed "Claude's
# TaskList ... TaskUpdate returns" inside a message that otherwise correctly
# said update_plan, naming two tools Codex does not have.
_ACT_STALE_AI_ESCAPE_TASK_TOOLS = (
    "If a task above is stale ({task_list} does not show it or "
    '{task_update} returns "Task not found"), retry — after '
    "{threshold} identical Stop blocks an AI-callable stale-clear marker "
    "({marker}) becomes printable to mark "
    "those ids ignored without user intervention."
)
_ACT_STALE_AI_ESCAPE_CHECKLIST = (
    "If an item above is stale (it no longer appears in {task_progress}), "
    "retry — after {threshold} identical Stop blocks an AI-callable "
    "stale-clear marker ({marker}) becomes printable to mark those ids "
    "ignored without user intervention."
)


def _stale_escape_sentence(cli_type: str | None, *, threshold: int, marker: str) -> str:
    """Stale-task escape wording in the harness's own task vocabulary."""
    template = _ACT_STALE_AI_ESCAPE_CHECKLIST if platform_for(cli_type).task_management_style == "plan_checklist" else _ACT_STALE_AI_ESCAPE_TASK_TOOLS
    # Only threshold/marker are substituted here; the {task_*} placeholders stay
    # for core.py to resolve against the running harness.
    return template.replace("{threshold}", str(threshold)).replace("{marker}", marker)


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


def _task_actions_fragment(cli_type: str | None, *, staleness_reminders_disabled: bool = False) -> str:
    """Return stop/resume actions in the platform's native task vocabulary."""
    sos = format_command_for_cli("/ar:sos", cli_type)
    task_pause = format_command_for_cli("/ar:task pause <reason>", cli_type)
    task_ignore = format_command_for_cli("/ar:task ignore <id>", cli_type)
    user_actions = f"{task_pause} (discussion; tasks unchanged), {sos}, or {task_ignore}"
    if staleness_reminders_disabled:
        tasks_off = format_command_for_cli("/ar:tasks off", cli_type)
        user_actions += f"; {tasks_off} only disables reminders"
    if platform_for(cli_type).task_management_style == "plan_checklist":
        return (
            "Actions: 1. You must complete or remove each checklist item before stopping "
            "2. Review/update: {task_progress} with the current plan list "
            '3. Finish work: {task_progress} with finished items status="completed" '
            "4. Defer/delegate: keep a concrete follow-up item pending "
            "5. Discard obsolete work: remove it from the current plan list "
            f"6. User only: {user_actions} "
        )
    return (
        f"Actions: 1. You must complete or discard each task before stopping "
        f"2. Review: {_ACT_REVIEW} "
        f"3. Do the work, then: {_ACT_COMPLETE} "
        f"4. Delegate to subagent first: {_delegate_action(cli_type)} (marks task non-blocking while subagent runs) "
        f"5. Or discard: {_ACT_DISCARD} "
        f"6. User only: {user_actions} "
    )


def _bounded_stop_message(
    cli_type: str | None,
    *,
    blocked_count: int,
    task_count: int,
) -> str:
    """Describe one bounded Stop yield without implying task completion."""
    task_label = "task" if task_count == 1 else "tasks"
    task_status = format_command_for_cli("/ar:task status", cli_type)
    return (
        f"Autorun allowed this interaction to end after {blocked_count} "
        f"consecutive Stop blocks without completed activity; retained "
        f"{task_count} incomplete {task_label}. No task was completed, ignored, "
        f"or cleared. The next user prompt or session resume restores task "
        f"enforcement; use {task_status} to review the retained work."
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


# === Configuration (dataclass pattern from PlanExportConfig) ===

CONFIG_PATH = ipc.AUTORUN_CONFIG_DIR / "task-lifecycle.config.json"


@dataclass
class TaskLifecycleConfig:
    """Task lifecycle configuration (follows PlanExportConfig pattern)."""

    enabled: bool = True
    storage_dir: Path = ipc.AUTORUN_CONFIG_DIR / "task-tracking"
    max_resume_tasks: int = 20
    stop_block_max_count: int = 3
    task_ttl_days: int = 30
    recent_task_days: int = 1  # tasks created within this window shown as "recent"
    debug_logging: bool = False
    ghost_clear_enabled: bool = True
    ghost_clear_min_consecutive_blocks: int = 2
    ghost_clear_hash_length: int = 12
    task_pause_default_ttl_seconds: float = TASK_PAUSE_DEFAULT_TTL_SECONDS
    state_lock_timeout_seconds: float = DEFAULT_SESSION_TIMEOUT
    hook_state_lock_timeout_seconds: float = float(CONFIG.get("hook_state_lock_timeout_seconds", 0.25))

    @classmethod
    def load(cls) -> "TaskLifecycleConfig":
        """Load from config file with defaults."""
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text())
                if "storage_dir" in data and isinstance(data["storage_dir"], str):
                    data["storage_dir"] = Path(data["storage_dir"])
                try:
                    state_timeout = float(data.get("state_lock_timeout_seconds", DEFAULT_SESSION_TIMEOUT))
                except (TypeError, ValueError):
                    state_timeout = DEFAULT_SESSION_TIMEOUT
                if state_timeout < DEFAULT_SESSION_TIMEOUT:
                    data["state_lock_timeout_seconds"] = DEFAULT_SESSION_TIMEOUT
                pause_ttl = data.get(
                    "task_pause_default_ttl_seconds",
                    cls.task_pause_default_ttl_seconds,
                )
                if isinstance(pause_ttl, bool) or not isinstance(pause_ttl, (int, float)) or not math.isfinite(pause_ttl) or pause_ttl <= 0:
                    raise TypeError(f"task_pause_default_ttl_seconds must be a finite number greater than 0, got {pause_ttl!r}; pass positive seconds")
                return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})
            except (json.JSONDecodeError, TypeError):
                pass
        return cls()

    def save(self) -> None:
        """Save config to file."""
        CONFIG_PATH.parent.mkdir(exist_ok=True)
        data = asdict(self)
        data["storage_dir"] = str(data["storage_dir"])
        CONFIG_PATH.write_text(json.dumps(data, indent=2))


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
                data = json.loads(PLAN_NOTIFY_CONFIG_PATH.read_text())
                return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})
            except (json.JSONDecodeError, TypeError):
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
        PLAN_NOTIFY_CONFIG_PATH.write_text(json.dumps(data, indent=2))


# === Ghost-Task Helpers (v0.10.2) ===


def _ghost_id_set_hash(tasks: Iterable[dict], hex_chars: int) -> str:
    """Stable hash of the sorted task-id set — detects identical consecutive stop blocks."""
    ids = ",".join(sorted(str(t["id"]) for t in tasks))
    byte_size = max(4, min(32, hex_chars // 2))
    return hashlib.blake2s(ids.encode(), digest_size=byte_size).hexdigest()


def _reset_stop_block_sequence(metadata: dict) -> None:
    """Start a fresh consecutive Stop sequence."""
    metadata["stop_block_count"] = 0
    metadata.pop("last_delivered_stop_block_generation", None)


def _reset_ghost_counter(metadata: dict) -> None:
    """Reset consecutive identical stop-block counter (called on clean exit or tool activity)."""
    metadata["consecutive_identical_stop_block_count"] = 0
    metadata.pop("last_stop_block_id_hash", None)


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
# share the parent session id (anthropics/claude-code#7881), so this ledger
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
_AGENT_TRANSCRIPT_RE = re.compile(r"agent-([A-Za-z0-9]{8,})\.jsonl$")
# Ring-buffer bound on remembered spawns; fan-outs beyond this simply lose
# the oldest identities, which degrades to the AMBIGUOUS (return-everything)
# path rather than to wrong completions.
_MAX_TRACKED_SPAWNS = 16


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


def extract_stale_clear_task_ids(*texts: object) -> list[str]:
    """Return unique task ids from configured stale-clear markers."""
    return _extract_marker_task_ids(CONFIG["ghost_clear_marker_template"], *texts)


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
        self._state_lock_timeout = self.config.hook_state_lock_timeout_seconds if ctx is not None else self.config.state_lock_timeout_seconds

        # Global key for session state (per-session isolation)
        self.global_key = f"__task_lifecycle__{self.session_id}"

        # Audit log path (per-session, append-only)
        self.audit_log = self.config.storage_dir / self.session_id / "audit.log"
        self.audit_log.parent.mkdir(parents=True, exist_ok=True)

    # === State Access (REUSES session_state() - DRY) ===

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
            with open(self.audit_log, "a", encoding="utf-8") as f:
                timestamp = datetime.now().isoformat()
                extra_str = f" {json.dumps(extra)}" if extra else ""
                f.write(f"{timestamp} [{event_type}] Task #{task_id} ({status}): {subject}{extra_str}\n")
        except IOError as e:
            logger.warning(f"Failed to log task event: {e}")

    # === Task Operations (DRY - reusable methods) ===

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

    def get_prioritized_tasks(self) -> List[Dict]:
        """Get tasks in priority order using blockedBy/blocks.

        Hard prioritization (uses EXISTING fields - no schema changes):
        1. Ready: Tasks with no blockers (can start now)
        2. Waiting: Tasks with all blockers completed (unblocked, can start)
        3. Blocked: Tasks with incomplete blockers (must wait)

        Returns:
            List of tasks ordered by priority
        """
        incomplete = self.get_incomplete_tasks(exclude_blocking=True)
        tasks_dict = self.tasks

        ready = []  # No blockers
        waiting = []  # All blockers completed
        blocked = []  # Some blockers incomplete

        for task in incomplete:
            blockers = task.get("blockedBy", [])

            if not blockers:
                ready.append(task)
            else:
                all_done = all(tasks_dict.get(blocker_id, {}).get("status") in self.COMPLETED_STATUSES for blocker_id in blockers)
                if all_done:
                    waiting.append(task)
                else:
                    blocked.append(task)

        return ready + waiting + blocked

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

    def update_task(self, task_id: str, updates: Dict, result: str) -> Optional[str]:
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

            # Status transition (with ghost task protection)
            if "status" in updates:
                old_status = task["status"]
                new_status = updates["status"]
                task_status_policy(new_status)

                # CRITICAL GHOST TASK PROTECTION:
                # Ghost tasks are created when AI calls TaskUpdate(id=X) for unknown X
                # (task created before daemon tracking started). In v1, these could
                # transition to in_progress/pending, then if the session ended before
                # TaskUpdate(id=X, status=completed), they'd block Stop hooks forever.
                #
                # V2 FIX: Ghost tasks can ONLY accept terminal statuses (completed,
                # deleted, ignored). Requests for in_progress/pending are logged as
                # GHOST_SKIP but status stays "ignored". This prevents permanent blocking.
                #
                # WHY THIS WORKS:
                # - get_incomplete_tasks() filters by NON_BLOCKING_STATUSES (includes "ignored")
                # - Ghost tasks with "ignored" never appear in incomplete list
                # - Stop hook only blocks if incomplete tasks exist
                # - Migration (v1→v2) automatically fixes old ghost tasks on first access
                is_ghost = task.get("metadata", {}).get("ghost_task", False)
                terminal_statuses = {"completed", "deleted", "ignored", "delegated"}

                if is_ghost and new_status not in terminal_statuses:
                    # Ghost task protection triggered - log for debugging
                    logger.warning(
                        "GHOST_SKIP: task_id=%s requested status=%s but ghost tasks can only transition to terminal statuses (%s). Keeping 'ignored'.",
                        task_id,
                        new_status,
                        ", ".join(sorted(terminal_statuses)),
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

        self._atomic_update_task(task_id, updater)

        # Fix 4: Reset stop_block_count when task reaches terminal status.
        # Placed OUTSIDE updater closure to avoid nonlocal scoping issues.
        if "status" in updates and updates["status"] in self.NON_BLOCKING_STATUSES and not ghost_state[0]:
            self.atomic_update_metadata(_reset_stop_block_sequence)

        return "ghost_skip" if ghost_state[0] else None

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
            return [t for t in plan_tasks if t["status"] not in self.COMPLETED_STATUSES]

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

        # Create task with full metadata
        # Use raw_result directly if string (Gemini/test mocks), else tool_result_str
        result_str = raw_result if isinstance(raw_result, str) else ctx.tool_result_str
        self.create_task(task_id, ctx.tool_input, result_str)

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
        task_id = ctx.tool_input.get("taskId") or ctx.tool_input.get("id")
        if not task_id:
            return None  # Skip if no task ID

        # Update task with all metadata
        # Use raw tool_result directly if string (Gemini/test mocks), else tool_result_str
        raw_result = ctx.tool_result
        result_str = raw_result if isinstance(raw_result, str) else ctx.tool_result_str
        return self.update_task(task_id, ctx.tool_input, result_str)

    def handle_bulk_todos(self, ctx: EventContext) -> None:
        """Handle WriteTodos tool (Gemini Planner).

        Replaces the current session's tasks with the provided todos list.
        """
        # Defensive check for tool_input structure (Pre-mortem fix)
        if not isinstance(ctx.tool_input, dict):
            logger.warning(f"handle_bulk_todos: tool_input is not a dict: {type(ctx.tool_input)}")
            return

        todos = ctx.tool_input.get("todos", [])
        if not isinstance(todos, list) or not todos:
            logger.debug("handle_bulk_todos: no todos list found in input")
            return

        now = time.time()

        def updater(tasks):
            # Clear only previous planner-sourced tasks for this session. The
            # daemon may also track explicit TaskCreate records in the same
            # session; a bulk planner refresh must not erase them.
            to_remove = [tid for tid, t in tasks.items() if (t.get("session_id") == self.session_id and t.get("metadata", {}).get("source") == "planner")]
            for tid in to_remove:
                tasks.pop(tid)

            for i, todo in enumerate(todos, 1):
                if not isinstance(todo, dict):
                    continue

                # Gemini tool uses 'description', fall back to 'subject' for cross-platform robustness
                subject = todo.get("description") or todo.get("subject") or f"Task {i}"
                task_id = str(i)
                tasks[task_id] = {
                    "id": task_id,
                    "subject": subject,
                    "description": "",
                    "activeForm": "",
                    "status": todo.get("status", "pending"),
                    "created_at": now,
                    "updated_at": now,
                    "session_id": self.session_id,
                    "owner": None,
                    "blockedBy": [],
                    "blocks": [],
                    "metadata": {"source": "planner"},
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

        plan = ctx.tool_input.get("plan", [])
        if not isinstance(plan, list) or not plan:
            logger.debug("handle_plan_checklist: no plan list found in input")
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

        # Find blocking tasks. Parked and delegated work is retained and shown
        # separately below without making Stop block on it.
        # REMOVED: self.prune_old_tasks() - manual pruning only
        incomplete = self.get_incomplete_tasks(exclude_blocking=True)

        # Also find delegated tasks — non-blocking but need follow-up if child failed
        all_tasks = self.tasks
        delegated_all = [t for t in all_tasks.values() if t.get("status") == "delegated"]
        paused_all = [t for t in all_tasks.values() if t.get("status") == "paused"]

        if not incomplete and not delegated_all and not paused_all:
            return None

        # Get prioritized tasks for better AI guidance
        prioritized = self.get_prioritized_tasks()

        # Separate by status for better visibility
        now = time.time()
        recent_threshold = self.config.recent_task_days * _SECONDS_PER_DAY
        recent_incomplete = [t for t in prioritized if now - t["created_at"] < recent_threshold]
        older_incomplete = [t for t in prioritized if now - t["created_at"] >= recent_threshold]

        in_progress_tasks = [t for t in recent_incomplete if t["status"] == "in_progress"]
        pending_tasks = [t for t in recent_incomplete if t["status"] == "pending"]

        # Build numbered task list (inline, minimal newlines)
        task_items = []
        total_shown = 0
        max_tasks = self.config.max_resume_tasks

        for t in in_progress_tasks[: max_tasks - total_shown]:
            task_items.append(f"{len(task_items) + 1}. #{t['id']}: {t['subject']} (🔄)")
            total_shown += 1

        for t in pending_tasks[: max_tasks - total_shown]:
            blockers = t.get("blockedBy", [])
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

        task_list = " ".join(task_items)
        total = len(incomplete) + len(delegated_all) + len(paused_all)
        # Subtract the older tasks already named above: counting them again in
        # the "older" suffix reported one set of tasks as two.
        older_count = len(older_incomplete) - len(named_older)
        overflow = f" [... and {total - total_shown} more: use /ar:task to see all]" if total > total_shown else ""
        older = f" [📅 {older_count} older task(s) from previous days also incomplete]" if older_count > 0 else ""

        injection = (
            f"🔄 outstanding incomplete tasks from previous session: "
            f"{task_list}{overflow}{older}\n"
            f"{_task_actions_fragment(_task_cli_hint(ctx), staleness_reminders_disabled=not ctx.task_staleness_enabled)}"
            f"7. {_stale_escape_sentence(_task_cli_hint(ctx), threshold=self.config.ghost_clear_min_consecutive_blocks, marker=_stale_clear_marker_example())}\n"
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
            ctx.task_staleness_enforce_next = True
            ctx.task_staleness_reminder_count = 1  # Skip allow, go straight to deny

        # Keep AI running with injected prompt — AI sees this immediately
        return ctx.continue_running(injection)

    def handle_stop(self, ctx: EventContext) -> Optional[Dict]:
        """Handle Stop (block if incomplete tasks - PRIMARY GOAL).

        This is the core mechanism that ensures AI continues while tasks are outstanding.

        Task-state escape hatches (user-driven only, never automatic):
        - User runs /ar:sos to trigger emergency stop (AI outputs AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP)
        - User runs /ar:task ignore <id> to mark specific tasks as ignored
        - User marks tasks as completed/deleted via TaskUpdate

        A configured consecutive-Stop bound may yield one stuck interaction,
        but it retains every task and re-enables enforcement on the next
        completed activity, user prompt, or session start.
        """
        # SubagentStop fires in the PARENT's context when a child agent (spawned via
        # Agent tool) completes and returns results. Blocking SubagentStop creates a
        # deadlock: parent waits for child results, but SubagentStop is blocked so
        # results never arrive → parent loops forever. The parent's own Stop events
        # (separate event name) still enforce task completion, so auto-resume is
        # completely unaffected by this early return.
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
        if task_pause_allows_stop(ctx):
            return ctx.allow()

        try:
            self._revert_expired_delegations(ctx)
        except Exception as exc:  # noqa: BLE001 - liveness fallback must not break Stop
            logger.warning("Could not check delegation TTLs: %s", exc)
        return self._check_stop_with_delegation(ctx)

    def _return_delegations(self, ctx) -> list[str]:
        """Flip delegated tasks whose subagent finished to delegation-returned.

        Identity, in order (claude-code#7881 bars session-id identity): the
        SubagentStop transcript_path's ``agent-<id>.jsonl`` name; else the
        single live ledger entry; else AMBIGUOUS — flip EVERY ledger-linked
        delegation, because over-asking for verification is safe and
        wrong-completing is not. One state commit, O(tasks), idempotent, so
        fan-outs firing SubagentStop repeatedly re-flip nothing.
        """
        finished = None
        match = _AGENT_TRANSCRIPT_RE.search(str(ctx.transcript_path or ""))
        if match:
            finished = match.group(1)

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

            tasks = state.get("tasks", {})
            now = time.time()
            for task_id, task in tasks.items():
                if not isinstance(task, dict) or task.get("status") != "delegated":
                    continue
                agent = (task.get("metadata") or {}).get("delegated_to_session")
                if agent not in target_ids:
                    continue
                task["status"] = "delegation-returned"
                task["updated_at"] = now
                returned.append(str(task_id))
                if repository is not None:
                    repository.put_task(self.global_key, str(task_id), task, self._state_lock_timeout)

            if returned:
                for entry in ledger:
                    if str(entry.get("id")) in target_ids:
                        entry["returned"] = True
                metadata["agent_spawns"] = ledger
                state["tasks"] = tasks
                state["session_metadata"] = metadata

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

    def _revert_expired_delegations(self, ctx) -> list[str]:
        """Liveness fallback: a dead subagent cannot exempt its task forever.

        A ledger-linked delegation whose spawn produced no SubagentStop within
        the TTL reverts to pending with a notice. Manual delegations (no
        recorded spawn) keep their open-ended semantics.
        """
        ttl = float(CONFIG.get("delegation_ttl_seconds", 0) or 0)
        if ttl <= 0:
            return []
        now = time.time()
        reverted: list[str] = []
        with self._session_state() as state:
            repository = self._prepare_task_storage(state)
            metadata = copy.deepcopy(state.get("session_metadata", {}))
            ledger = {
                str(entry.get("id")): entry
                for entry in metadata.get("agent_spawns", [])
            }
            tasks = state.get("tasks", {})
            for task_id, task in tasks.items():
                if not isinstance(task, dict) or task.get("status") != "delegated":
                    continue
                task_metadata = task.get("metadata") or {}
                agent = task_metadata.get("delegated_to_session")
                entry = ledger.get(str(agent)) if agent else None
                if entry is None or entry.get("returned"):
                    continue
                delegated_at = task_metadata.get("delegated_at") or entry.get("at") or now
                if now - float(delegated_at) < ttl:
                    continue
                task["status"] = "pending"
                task["updated_at"] = now
                reverted.append(str(task_id))
                if repository is not None:
                    repository.put_task(self.global_key, str(task_id), task, self._state_lock_timeout)
            if reverted:
                state["tasks"] = tasks
        for task_id in reverted:
            self.log_event(
                "DELEGATION_EXPIRED",
                f"task#{task_id}",
                "no SubagentStop within delegation_ttl_seconds; reverted to pending",
                "pending",
            )
        if reverted:
            ctx.add_chain_notification(
                "Delegated subagent produced no completion within the TTL; "
                "reverted to pending: " + ", ".join(f"#{task_id}" for task_id in reverted),
                channel="both",
            )
        return reverted

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
            with self._session_state() as state:
                metadata = copy.deepcopy(state.get("session_metadata", {}))
                ledger = list(metadata.get("agent_spawns", []))
                if any(entry.get("id") == agent_id for entry in ledger):
                    return
                ledger.append(
                    {"id": agent_id, "at": time.time(), "claimed": False, "returned": False}
                )
                del ledger[:-_MAX_TRACKED_SPAWNS]
                metadata["agent_spawns"] = ledger
                state["session_metadata"] = metadata
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
            structured = ctx.last_assistant_message
            if structured and marker_prefix in structured:
                digest = hashlib.sha256(structured.encode("utf-8")).hexdigest()
                sources.append((f"assistant-structured:{digest}", structured))
            latest = ctx.transcript.latest_assistant_message() if ctx.transcript else None
            if latest and marker_prefix in latest[1]:
                sources.append((f"assistant:{latest[0]}", latest[1]))
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
                    f"Marked delegated (non-blocking until it reports back): {', '.join(f'#{d}' for d in delegated)}",
                    channel="both",
                )
            return delegated
        except Exception as exc:  # noqa: BLE001 - a marker must not break the caller
            logger.warning("Could not apply delegation markers: %s", exc)
            return []

    def _apply_delegate_markers(self, ctx, incomplete_tasks: List[Dict]) -> List[Dict]:
        """Apply markers and report what still blocks the stop."""
        if not incomplete_tasks:
            return incomplete_tasks
        if not self.apply_delegation_markers(ctx, incomplete_tasks):
            return incomplete_tasks
        return self.get_incomplete_tasks(exclude_blocking=True)

    def _check_stop_with_delegation(self, ctx) -> Optional[str]:
        """The stop gate proper, with delegation markers already honored."""

        # Find incomplete tasks (exclude paused/ignored - they're explicitly parked)
        incomplete_tasks = self.get_incomplete_tasks(exclude_blocking=True)

        # Apply delegation markers here as well as on PostToolUse.
        #
        # The block message tells the AI to print AUTORUN_TASK_DELEGATED(N) to
        # hand a task to a subagent. It usually does so in a plain-text reply
        # with no tool call — and PostToolUse only fires after a tool call, so
        # on that path the marker is never read. This hook then blocks again on
        # the very task the AI just delegated, prints the same instruction, and
        # the AI complies again: a loop with no exit that the AI cannot escape
        # by doing what it was told.
        #
        # Both pathways are kept. PostToolUse applies the marker as soon as it
        # appears; this applies it at the only moment it has to be right.
        incomplete_tasks = self._apply_delegate_markers(ctx, incomplete_tasks)

        if not incomplete_tasks:

            def reset_counter(metadata):
                _reset_stop_block_sequence(metadata)
                _reset_ghost_counter(metadata)

            self.atomic_update_metadata(reset_counter)
            return None

        override = getattr(ctx, "ghost_clear_min_consecutive_blocks_override", None)
        min_consecutive = override if isinstance(override, int) else self.config.ghost_clear_min_consecutive_blocks
        ghost_enabled = self.config.ghost_clear_enabled

        id_hash = _ghost_id_set_hash(incomplete_tasks, self.config.ghost_clear_hash_length)

        # Read AND write inside one atomic lock — eliminates race between
        # two concurrent Stop hooks reading stale session_metadata snapshots.
        computed: dict = {}

        def update_counters(metadata):
            block_count = metadata.get("stop_block_count", 0) + 1
            prev_hash = metadata.get("last_stop_block_id_hash")
            prev_count = metadata.get("consecutive_identical_stop_block_count", 0)
            consecutive = prev_count + 1 if id_hash == prev_hash else 1
            metadata["stop_block_count"] = block_count
            metadata["last_stop_block_id_hash"] = id_hash
            metadata["consecutive_identical_stop_block_count"] = consecutive
            computed["block_count"] = block_count
            computed["consecutive"] = consecutive

        self.atomic_update_metadata(update_counters)
        ctx.state_set_volatile("ghost_counter_armed_in_daemon", True)
        # Use .get() with safe defaults: if atomic update failed (exception in
        # callback), computed is empty. Default to block_count=1/consecutive=1
        # so we show the standard block message but do NOT trigger the escape
        # hatch (consecutive=1 < min_consecutive). Prefer a correct block over
        # a fail-open allow-stop.
        block_count = computed.get("block_count", 1)
        consecutive = computed.get("consecutive", 1)

        if ghost_enabled and consecutive >= min_consecutive:
            transcript_text = ctx.transcript.text if ctx.transcript else ""
            marker_ids = extract_stale_clear_task_ids(ctx.tool_result_str, transcript_text)
            if marker_ids:
                blocking_ids = {str(task["id"]) for task in incomplete_tasks}
                cleared = self.clear_stale_task_markers(
                    marker_ids,
                    require_armed=False,
                    allowed_task_ids=blocking_ids,
                )
                if cleared:
                    incomplete_tasks = self.get_incomplete_tasks(exclude_blocking=True)
                if cleared and not incomplete_tasks:
                    ctx.add_chain_notification(
                        f"Cleared stale task(s): {', '.join(f'#{c}' for c in cleared)}",
                        channel="both",
                    )
                    return None

        if block_count > self.config.stop_block_max_count:
            ctx.pending_stop_injection = None
            if block_count == self.config.stop_block_max_count + 1:
                yield_message = _bounded_stop_message(
                    _task_cli_hint(ctx),
                    blocked_count=block_count - 1,
                    task_count=len(incomplete_tasks),
                )
                self.log_event(
                    "STOP_SEQUENCE_YIELD",
                    "session",
                    yield_message,
                    "yielded",
                )
                return ctx.allow(yield_message)
            return ctx.allow()

        # Build task list with status indicators (cap at max_resume_tasks)
        max_tasks = self.config.max_resume_tasks
        task_lines = []

        for t in incomplete_tasks[:max_tasks]:
            tid = t["id"]
            subject = t["subject"]
            status = t["status"]
            status_icon = {
                "in_progress": "🔄",
                "pending": "⏸️",
                "delegated": "🤝",
                "delegation-returned": "📥",
            }.get(status, "❓")
            task_lines.append(f"{len(task_lines) + 1}. #{tid}: {subject} ({status_icon})")

        task_list = " ".join(task_lines)
        total = len(incomplete_tasks)
        overflow = f" [... and {total - max_tasks} more: use /ar:task to see all]" if total > max_tasks else ""

        injection = (
            f"🛑 CANNOT STOP — incomplete tasks: {task_list}{overflow}\n"
            f"{_task_actions_fragment(_task_cli_hint(ctx), staleness_reminders_disabled=not ctx.task_staleness_enabled)}"
            f"7. {_stale_escape_sentence(_task_cli_hint(ctx), threshold=min_consecutive, marker=_stale_clear_marker_example())}\n"
        )

        returned_tasks = [t for t in incomplete_tasks if t.get("status") == "delegation-returned"]
        if returned_tasks:
            task_ignore = format_command_for_cli("/ar:task ignore <id>", ctx.cli_type)
            injection += (
                "Delegation returned (📥): critically assess each returned "
                "task's results — subagents make mistakes — and verify before "
                'accepting. Then TaskUpdate(status="completed") with the '
                "evidence, or re-delegate by printing the delegation marker "
                f"again, or ignore it with {task_ignore} if no longer needed: "
                + ", ".join(f"#{t['id']}" for t in returned_tasks[:max_tasks])
                + "\n"
            )

        if ghost_enabled and consecutive >= min_consecutive:
            marker_template = CONFIG["ghost_clear_marker_template"]
            marker_lines = "\n".join(f"   {marker_template.format(id=t['id'])}" for t in incomplete_tasks[:max_tasks])
            injection += CONFIG["ghost_clear_injection_template"].format(
                threshold=min_consecutive,
                marker_lines=marker_lines,
            )
            task_pause = format_command_for_cli("/ar:tasks pause <reason>", ctx.cli_type)
            task_stale_off = format_command_for_cli("/ar:tasks stale off", ctx.cli_type)
            injection += f"Real task: {task_pause}. Disable recovery: {task_stale_off}\n"

        # Log warning (block count is diagnostic-only, not shown to AI)
        self.log_event("STOP_WARNING", "session", f"Block #{block_count}: {total} incomplete tasks", "blocked")

        # Reset three-stage system if at STAGE_2_COMPLETED — tasks must resolve first.
        if ctx.autorun_stage == EventContext.STAGE_2_COMPLETED:
            ctx.autorun_stage = EventContext.STAGE_2
            injection += CONFIG.get("task_outstanding_stage3_message", "").format(
                count=total,
                names=", ".join(t.get("subject", f"#{t.get('id', '?')}") for t in incomplete_tasks[:_STAGE3_OVERFLOW_NAME_COUNT])
                + ("..." if total > _STAGE3_OVERFLOW_NAME_COUNT else ""),
            )

        # Deferred AI delivery — the established Stop block pathway uses
        # decision+reason and the PostToolUse replay keeps override actions
        # visible to the AI across supported harnesses. Current Claude also
        # documents Stop additionalContext, but changing the delivery pathway
        # requires a live harness proof that user/AI visibility and repeated
        # generation semantics remain intact.
        #
        # Re-arm on EVERY Stop block so the AI reliably sees the override
        # actions (/ar:sos, /ar:task ignore) on its next PostToolUse. Earlier
        # logic only re-armed on block_count==1 or consecutive==min_consecutive,
        # which left "infinite non-overridable stop failure" cases: e.g. the
        # AI churning task subjects between stops drove consecutive=1 forever,
        # so it never re-saw the override instructions. Frequency is naturally
        # low because Stop events themselves are infrequent (one per AI
        # stop-attempt). Safety: PostToolUse delivery clears pending_stop_injection,
        # so each Stop re-arm fires AT MOST once. The historical deadlock
        # (re-arm + PreToolUse deny + AI text → Stop → re-arm) is impossible
        # now because enforce_stop_injection (PreToolUse deny) was removed.
        ctx.pending_stop_injection = injection
        # Reset staleness counter — AI just learned about tasks, give full countdown.
        ctx.tool_calls_since_task_update = 0
        return ctx.continue_running(injection)

    def clear_stale_task_markers(
        self,
        task_ids: Iterable[str],
        *,
        require_armed: bool = True,
        allowed_task_ids: Iterable[str] | None = None,
    ) -> list[str]:
        """Mark stale task ids ignored through the configured escape hatch.

        Args:
            task_ids: Task ids extracted from the configured marker template.
            require_armed: If true, require the identical-stop threshold to be
                armed in session metadata before clearing. Stop handling checks
                the threshold before calling this method, so it passes false to
                avoid re-reading counter state after it already holds the
                relevant decision.
            allowed_task_ids: Optional current blocking task-id set. When
                provided, markers for unrelated or already non-blocking tasks are
                ignored.
        """
        if not self.config.ghost_clear_enabled:
            return []
        if require_armed:
            consecutive = self.session_metadata.get("consecutive_identical_stop_block_count", 0)
            if consecutive < self.config.ghost_clear_min_consecutive_blocks:
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
        allowed_task_ids: Iterable[str] | None = None,
        session: "str | None" = None,
        marker_identity: "str | None" = None,
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
            allowed_task_ids: Optional current blocking task-id set. When
                provided, markers naming unrelated or already non-blocking
                tasks are ignored, matching clear_stale_task_markers().
            session: Optional id of the session the work was handed to,
                recorded on each task. A delegation that never reports back
                is otherwise untraceable — the task says only that it went
                somewhere.
        """
        if marker_identity:
            return self._consume_delegation_marker(
                marker_identity,
                task_ids,
                allowed_task_ids=allowed_task_ids,
                session=session,
            )

        reason = CONFIG["delegate_reason"]
        delegated: list[str] = []
        allowed = {str(task_id) for task_id in allowed_task_ids} if allowed_task_ids is not None else None
        updates: Dict[str, object] = {"status": "delegated"}
        if session:
            updates["metadata"] = {"delegated_to_session": str(session)}
        for tid in list(dict.fromkeys(str(task_id) for task_id in task_ids)):
            if allowed is not None and tid not in allowed:
                continue
            try:
                # update_task returns None on success and "ghost_skip" when
                # ghost-task protection refused the write.
                if self.update_task(tid, dict(updates), reason) != "ghost_skip":
                    delegated.append(tid)
                    self.log_event(
                        "DELEGATE",
                        f"task#{tid}",
                        reason,
                        "delegated",
                        {"session": session} if session else None,
                    )
            except Exception:
                continue
        return delegated

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
            # has the child id to transcribe (claude-code#7881 bars session
            # identity too). No spawn recorded → today's manual semantics.
            ledger = list(metadata.get("agent_spawns", []))
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
                        metadata["agent_spawns"] = ledger
                        break

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
                task["status"] = "delegated"
                task["updated_at"] = now
                task_metadata = task.setdefault("metadata", {})
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

    def handle_plan_approval(self, ctx) -> Optional[Dict]:
        """Handle plan approval. Delegates to get_plan_approval_injection()."""
        injection = self.get_plan_approval_injection(ctx)
        return ctx.allow(injection) if injection else None

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
                incomplete_count = len([t for t in tasks.values() if t["status"] not in cls.COMPLETED_STATUSES])
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
                    json.dumps({"session_id": session_id, "exported_at": time.time(), "tasks": tasks, "plan_tasks_map": manager.plan_tasks_map}, indent=2)
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

                output_file.write_text("\n".join(md_lines))

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
        import sys

        try:
            config = TaskLifecycleConfig.load()

            if all_sessions:
                if confirm and not sys.stdin.isatty():
                    print("⚠️ Refusing to clear all sessions in non-interactive mode")
                    print("Use --no-confirm flag to proceed")
                    return 2

                sessions_dir = config.storage_dir
                if not sessions_dir.exists():
                    print("No task data found.")
                    return 0

                session_dirs = [d for d in sessions_dir.iterdir() if d.is_dir()]

                if confirm:
                    print(f"⚠️  WARNING: About to clear {len(session_dirs)} session(s)")
                    response = input("Type 'yes' to confirm: ")
                    if response.lower() != "yes":
                        print("Cancelled.")
                        return 2

                import shutil

                for session_dir in session_dirs:
                    shutil.rmtree(session_dir)

                print(f"Cleared {len(session_dirs)} session(s)")
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
                    if not sys.stdin.isatty():
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
                state_manager.clear_session(manager.global_key)

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
        this method. SQLite is refused before discovery or deletion because
        this implementation scans legacy flat JSON keys.

        Retire when JSON authority is no longer supported and every destructive
        caller uses ``StateRetention`` with explicit age, archive, confirmation,
        and deletion policy. Until then this is the JSON maintenance path.

        ⚠️  DESTRUCTIVE OPERATION - PERMANENTLY DELETES SESSION DATA ⚠️

        SAFETY GUARANTEES (fail-safe design):
        1. Protects the resolved current harness session - NEVER deleted
        2. Skips sessions with incomplete tasks (in_progress/pending work)
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
        import sys
        import fnmatch
        import shutil
        from .session_manager import get_session_manager

        try:
            config = config or cls._get_config()
            # The legacy collector scans and rewrites daemon_state.json
            # prefixes directly. On SQLite that would falsely report no data
            # and, if partially adapted, could bypass row/event retention
            # invariants. Refuse explicitly until this file's
            # TaskLifecycle.cli_gc archive/confirmation/delete workflow is
            # implemented through session_manager.py:StateRetention, the
            # SQLite archive-before-delete authority.
            if get_session_manager().task_repository() is not None:
                print(
                    "Task GC is not available for the SQLite backend. "
                    "Use 'autorun --state-maintenance' for a report-only "
                    "retention assessment; no SQLite rows were deleted."
                )
                return 1
            ttl = ttl_days if ttl_days is not None else config.task_ttl_days
            ttl_seconds = ttl * _SECONDS_PER_DAY
            mgr = get_session_manager()
            sessions_dir = mgr.state_dir
            # JSON key prefix for task lifecycle sessions (matches global_key format below)
            json_key_prefix = "__task_lifecycle__"
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
            archive_dir = config.storage_dir / "archive"

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
                    raw = json.loads(state_file.read_text())
                    for json_key in raw:
                        # json_key = "__task_lifecycle__{sid}/{subkey}"
                        if json_key.startswith(json_key_prefix) and "/" in json_key:
                            rest = json_key[len(json_key_prefix) :]
                            sid = rest.split("/")[0]
                            if sid and fnmatch.fnmatch(sid, pattern):
                                sids.add(sid)
                except (json.JSONDecodeError, OSError):
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
                if not sys.stdin.isatty():
                    print("⚠️  ERROR: Cannot prompt for confirmation in non-interactive mode")
                    print("Use --task-dry-run to preview, or --task-no-confirm to force")
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

                        prefix = f"__task_lifecycle__{sid}/"
                        state_snapshot = {key[len(prefix) :]: value for key, value in raw.items() if key.startswith(prefix)}
                        tasks = state_snapshot.get("tasks", {})

                        incomplete = [t for t in tasks.values() if isinstance(t, dict) and t.get("status") not in cls.NON_BLOCKING_STATUSES]
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
                            for sid in sorted(clear_set):
                                prefix = f"__task_lifecycle__{sid}/"
                                for key in [k for k in raw if k.startswith(prefix)]:
                                    del raw[key]
                                audit_dir = config.storage_dir / sid
                                if audit_dir.exists():
                                    shutil.rmtree(audit_dir, ignore_errors=True)
                                cleared += 1

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
                print("     → Fix: sudo chown -R $USER ~/.claude/sessions/")
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
        import sys

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
            if not interactive and not sys.stdin.isatty():
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
    if not is_enabled():
        return

    def reset_stop_sequence(ctx: EventContext) -> None:
        manager = TaskLifecycle(ctx=ctx)
        manager.atomic_update_metadata(_reset_stop_block_sequence)

    @app_instance.on("UserPromptSubmit")
    def reset_stop_sequence_on_user_prompt(ctx: EventContext) -> Optional[Dict]:
        """Start a fresh consecutive Stop sequence for a new user turn."""
        if not is_enabled():
            return None
        try:
            reset_stop_sequence(ctx)
        except Exception as error:
            logger.warning(f"Task Stop-sequence prompt reset error: {error}")
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
          would stop feeding it the override actions (/ar:sos,
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
        injection = ctx.pending_stop_injection
        if not injection:
            return None
        ctx.pending_stop_injection = None  # Clear so it fires only once per Stop

        manager = TaskLifecycle(ctx=ctx)
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

        try:
            # Instantiate class with auto-detected session ID
            manager = TaskLifecycle(ctx=ctx)

            if role == "plan":
                manager.handle_plan_checklist(ctx)
            elif role == "bulk":
                # Gemini CLI uses write_todos for all task operations.
                # Route based on content: todos in input → bulk create, else taskId → update.
                tool_input = ctx.tool_input or {}
                if tool_input.get("todos"):
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
            return None

        try:
            manager = TaskLifecycle(ctx=ctx)
            return manager.handle_stop(ctx)
        except Exception as e:
            logger.warning(
                f"Stop hook error (fail-open: allowing stop): {e}. "
                f"Session: {getattr(ctx, 'session_id', '?')}. "
                f"If tasks exist but stop was allowed, this exception is the cause."
            )
            return None  # Fail-open - allow stop on errors

    # NOTE: inject_plan_tasks was removed -- plan task injection is now merged into
    # detect_plan_approval() in plugins.py to avoid first-non-None chain ordering
    # issues (core.py:1097-1107). See Fixes 6-8.
