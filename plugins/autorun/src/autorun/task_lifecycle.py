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
- DRY: Reuses session_manager.py patterns, no custom shelve code
"""

from typing import Optional, Dict, List, Callable
from pathlib import Path
from dataclasses import dataclass
import json
import os
import time
import re
from datetime import datetime

from . import ipc
from .core import EventContext, app, logger
from .session_manager import session_state  # REUSE - no custom shelve code
from .config import (
    CONFIG,
    PLAN_TOOLS, TASK_CREATE_TOOLS, TASK_UPDATE_TOOLS,
    TASK_LIST_TOOLS, TASK_GET_TOOLS
)


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
    debug_logging: bool = False

    @classmethod
    def load(cls) -> "TaskLifecycleConfig":
        """Load from config file with defaults."""
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text())
                if "storage_dir" in data and isinstance(data["storage_dir"], str):
                    data["storage_dir"] = Path(data["storage_dir"])
                return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})
            except (json.JSONDecodeError, TypeError):
                pass
        return cls()

    def save(self) -> None:
        """Save config to file."""
        CONFIG_PATH.parent.mkdir(exist_ok=True)
        data = {
            "enabled": self.enabled,
            "storage_dir": str(self.storage_dir),
            "max_resume_tasks": self.max_resume_tasks,
            "stop_block_max_count": self.stop_block_max_count,
            "task_ttl_days": self.task_ttl_days,
            "debug_logging": self.debug_logging,
        }
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


# === TaskLifecycle Class ===


class TaskLifecycle:
    """Task lifecycle manager (follows PlanExport pattern).

    DRY REUSE:
    - session_state() for persistence (no custom shelve code)
    - @property + atomic_update_*() pattern from PlanExport
    - Simple append logging (no RotatingFileHandler)
    - Frozenset constants for status checks (single source of truth)

    Per-Session Isolation:
    - Each AI session uses unique global key: "__task_lifecycle__{session_id}"
    - State stored in shared shelve but keyed per session
    - Audit logs are per-session files
    """

    # Schema version for task lifecycle data stored in shelve files
    # (~/.claude/sessions/plugin___task_lifecycle__{session_id}.db).
    # Bump when the task dict structure or status transition rules change.
    # Migration runs automatically on first access via _migrate_if_needed().
    #
    # Version history:
    #   v1: Initial task schema. No version field stored in shelve. Ghost tasks
    #       (tasks first seen via TaskUpdate, not TaskCreate) could transition
    #       to in_progress/pending, causing them to block Stop hook permanently
    #       if the completed update was lost (e.g., session end, context compaction).
    #   v2: Ghost task protection. Ghost tasks (metadata.ghost_task=True) can only
    #       transition to terminal statuses (completed/deleted/ignored). Non-terminal
    #       status requests (in_progress/pending) are silently skipped. Existing v1
    #       ghost tasks with blocking statuses are reset to "ignored" on migration.
    SCHEMA_VERSION = 2

    # Status constants (single source of truth - DRY)
    COMPLETED_STATUSES = frozenset(["completed", "deleted"])
    # Statuses that don't block stopping (task is "done" or "parked")
    NON_BLOCKING_STATUSES = frozenset(["completed", "deleted", "paused", "ignored"])

    # Statuses safe to prune after TTL (truly terminal, no resume expected)
    PRUNABLE_STATUSES = frozenset(["completed", "deleted", "ignored"])

    def __init__(self, session_id: str | None = None, ctx: EventContext | None = None,
                 config: TaskLifecycleConfig | None = None):
        """Initialize task lifecycle manager.

        Args:
            session_id: Explicit session ID (for CLI commands)
            ctx: EventContext (for hook handlers - has session_id)
            config: Config override (uses TaskLifecycleConfig.load() if None)

        Raises:
            ValueError: If session_id cannot be determined
        """
        # Session ID resolution (3 sources - explicit > ctx > env)
        if session_id:
            self.session_id = session_id
        elif ctx:
            self.session_id = ctx.session_id
        elif os.environ.get('CLAUDE_SESSION_ID'):
            self.session_id = os.environ['CLAUDE_SESSION_ID']
        else:
            raise ValueError("session_id required: pass explicitly, via ctx, or set CLAUDE_SESSION_ID env var")

        # Config
        self.config = config or TaskLifecycleConfig.load()

        # Global key for session state (per-session isolation)
        self.global_key = f"__task_lifecycle__{self.session_id}"

        # Audit log path (per-session, append-only)
        self.audit_log = self.config.storage_dir / self.session_id / "audit.log"
        self.audit_log.parent.mkdir(parents=True, exist_ok=True)

    # === State Access (REUSES session_state() - DRY) ===

    def _migrate_if_needed(self, state: Dict) -> None:
        """Migrate shelve state to current schema version (lazy self-healing).

        LIFECYCLE: Called automatically on every .tasks access and atomic_update_tasks().
        This ensures old data gets fixed when accessed, no manual intervention needed.

        CRITICAL INVARIANTS:
        1. Migrations are idempotent (safe to run multiple times)
        2. Runs inside session_state() context (SessionLock protection)
        3. Updates both tasks AND schema_version atomically
        4. Each version bump preserves backward compatibility

        WHY LAZY MIGRATION:
        - Daemon restarts don't trigger migration (shelve just sits on disk)
        - Session resume/continuation triggers migration via first .tasks access
        - Failed sessions get fixed when next session accesses their data
        - No need for batch migration scripts or manual database edits
        """
        stored_version = state.get("schema_version", 1)
        if stored_version >= self.SCHEMA_VERSION:
            return  # Already current - no work needed

        tasks = state.get("tasks", {})

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
                self.log_event("MIGRATION", "v1->v2", f"Fixed {fixed_count} ghost tasks",
                              "schema_update", {"fixed_count": fixed_count})

        # Atomic update: tasks + version together
        state["tasks"] = tasks
        state["schema_version"] = self.SCHEMA_VERSION

    @property
    def tasks(self) -> Dict[str, Dict]:
        """Get tasks dict. For modifications, use atomic_update_tasks()."""
        with session_state(self.global_key) as state:
            self._migrate_if_needed(state)
            return dict(state.get("tasks", {}))

    def atomic_update_tasks(self, updater: Callable[[Dict], None]) -> None:
        """Atomically update tasks. updater(tasks) modifies in-place."""
        with session_state(self.global_key) as state:
            self._migrate_if_needed(state)
            tasks = state.get("tasks", {})
            updater(tasks)
            state["tasks"] = tasks

    @property
    def plan_tasks_map(self) -> Dict[str, List[str]]:
        """Get plan->tasks mapping."""
        with session_state(self.global_key) as state:
            return dict(state.get("plan_tasks_map", {}))

    def atomic_update_plan_tasks_map(self, updater: Callable[[Dict], None]) -> None:
        """Atomically update plan_tasks_map."""
        with session_state(self.global_key) as state:
            plan_map = state.get("plan_tasks_map", {})
            updater(plan_map)
            state["plan_tasks_map"] = plan_map

    @property
    def session_metadata(self) -> Dict:
        """Get session metadata."""
        with session_state(self.global_key) as state:
            if "session_metadata" not in state:
                state["session_metadata"] = {
                    'session_id': self.session_id,
                    'created_at': time.time(),
                    'last_activity': time.time(),
                    'stop_block_count': 0,
                }
            return dict(state["session_metadata"])

    def atomic_update_metadata(self, updater: Callable[[Dict], None]) -> None:
        """Atomically update session_metadata."""
        with session_state(self.global_key) as state:
            metadata = state.get("session_metadata", {
                'session_id': self.session_id,
                'created_at': time.time(),
                'last_activity': time.time(),
                'stop_block_count': 0,
            })
            updater(metadata)
            state["session_metadata"] = metadata

    # === Logging (Simple append - DRY) ===

    def log_event(self, event_type: str, task_id: str, subject: str, status: str,
                  extra: Dict = None) -> None:
        """Log event to audit file (simple append - follows plan_export.py pattern)."""
        if not self.config.debug_logging:
            return

        try:
            with open(self.audit_log, "a") as f:
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
        tasks = self.tasks
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

        ready = []    # No blockers
        waiting = []  # All blockers completed
        blocked = []  # Some blockers incomplete

        for task in incomplete:
            blockers = task.get('blockedBy', [])

            if not blockers:
                ready.append(task)
            else:
                all_done = all(
                    tasks_dict.get(blocker_id, {}).get('status') in self.COMPLETED_STATUSES
                    for blocker_id in blockers
                )
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

            tasks[task_id] = {
                # Core identification
                "id": task_id,
                "subject": input_data.get("subject", ""),
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
                "tool_outputs": [result]
            }

        self.atomic_update_tasks(updater)
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
                    "tool_outputs": []
                }

            task = tasks[task_id]

            # Update metadata fields (merge semantics)
            for key in ["subject", "description", "activeForm", "owner"]:
                if key in updates:
                    task[key] = updates[key]

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
                terminal_statuses = {"completed", "deleted", "ignored"}

                if is_ghost and new_status not in terminal_statuses:
                    # Ghost task protection triggered - log for debugging
                    self.log_event("GHOST_SKIP", task_id, task["subject"], new_status,
                                  {"old_status": old_status,
                                   "reason": "ghost task cannot become blocking",
                                   "requested_status": new_status,
                                   "maintained_status": "ignored"})
                    # Status stays "ignored" - do NOT update to blocking status
                    ghost_state[0] = True
                else:
                    # Normal status transition (non-ghost or terminal status)
                    task["status"] = new_status

                    event_type = {
                        "completed": "COMPLETE",
                        "in_progress": "START",
                        "deleted": "DELETE",
                        "paused": "PAUSE",
                        "ignored": "IGNORE"
                    }.get(new_status, "UPDATE")

                    self.log_event(event_type, task_id, task["subject"], new_status,
                                  {"old_status": old_status})

            task["updated_at"] = time.time()
            task["tool_outputs"].append(result)

        self.atomic_update_tasks(updater)

        # Fix 4: Reset stop_block_count when task reaches terminal status.
        # Placed OUTSIDE updater closure to avoid nonlocal scoping issues.
        if ("status" in updates
                and updates["status"] in self.NON_BLOCKING_STATUSES
                and not ghost_state[0]):
            def reset_block_count(metadata):
                metadata['stop_block_count'] = 0
            self.atomic_update_metadata(reset_block_count)

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
            old_status = task['status']
            task['status'] = 'ignored'
            task['updated_at'] = time.time()
            task['metadata']['ignore_reason'] = reason
            task['tool_outputs'].append(f'User ignored task: {reason}')

            self.log_event('IGNORE', task_id, task['subject'], 'ignored',
                         {'old_status': old_status, 'reason': reason})

        self.atomic_update_tasks(updater)
        return task_id in self.tasks

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
        ttl_seconds = self.config.task_ttl_days * 86400
        now = time.time()
        pruned_count = 0

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

        self.atomic_update_tasks(updater)

        if pruned_count > 0:
            self.log_event("PRUNE", "session", f"Pruned {pruned_count} old completed tasks",
                          "maintenance")

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
        result_text = ctx.tool_result or ""

        # Multiple regex patterns with fallbacks (Problem 3 solution)
        patterns = [
            r'Task #(\d+) created successfully',
            r'Created task #(\d+) successfully',
            r'Task (\d+) created',
            r'#(\d+)',  # Last resort
        ]

        task_id = None
        for pattern in patterns:
            match = re.search(pattern, result_text)
            if match:
                task_id = match.group(1)
                break

        if not task_id:
            self.log_event("ERROR", "unknown", "Failed to extract task ID", "error")
            return  # Fail-open

        # Create task with full metadata
        self.create_task(task_id, ctx.tool_input, result_text)

        # If active plan, link this task to the plan for context injection
        if hasattr(ctx, 'plan_active') and ctx.plan_active:
            plan_key = getattr(ctx, 'plan_arguments', '')
            if plan_key:
                self.link_task_to_plan(task_id, plan_key)

    def handle_task_update(self, ctx: EventContext) -> Optional[str]:
        """Handle TaskUpdate tool (called from PostToolUse hook).

        Tracks status transitions AND updates full metadata.

        Returns:
            'ghost_skip' if ghost task protection triggered, None otherwise.
        """
        task_id = ctx.tool_input.get("taskId")
        if not task_id:
            return None  # Skip if no task ID

        # Update task with all metadata
        return self.update_task(task_id, ctx.tool_input, ctx.tool_result or "")

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
        # Find incomplete tasks (exclude paused/ignored - they're explicitly parked)
        # REMOVED: self.prune_old_tasks() - manual pruning only
        incomplete = self.get_incomplete_tasks(exclude_blocking=True)

        if not incomplete:
            return None  # No incomplete tasks - session proceeds normally

        # Get prioritized tasks for better AI guidance
        prioritized = self.get_prioritized_tasks()

        # Separate by status for better visibility
        now = time.time()
        recent_incomplete = [t for t in prioritized if now - t["created_at"] < 86400]  # 24 hours
        older_incomplete = [t for t in prioritized if now - t["created_at"] >= 86400]

        in_progress_tasks = [t for t in recent_incomplete if t["status"] == "in_progress"]
        pending_tasks = [t for t in recent_incomplete if t["status"] == "pending"]

        # Build resume prompt with cap (Problem 1 solution)
        lines = []
        total_shown = 0
        max_tasks = self.config.max_resume_tasks

        if in_progress_tasks and total_shown < max_tasks:
            lines.append("**In Progress:**")
            for t in in_progress_tasks[:max_tasks - total_shown]:
                lines.append(f"  - Task #{t['id']}: {t['subject']}")
                total_shown += 1

        if pending_tasks and total_shown < max_tasks:
            lines.append("\n**Pending:**")
            for t in pending_tasks[:max_tasks - total_shown]:
                blockers = t.get("blockedBy", [])
                if blockers:
                    lines.append(f"  - Task #{t['id']}: {t['subject']} (⚠️ blocked by {blockers})")
                else:
                    lines.append(f"  - Task #{t['id']}: {t['subject']} (✅ ready)")
                total_shown += 1

        task_list = "\n".join(lines)
        total = len(incomplete)
        older_count = len(older_incomplete)

        injection = f"""
## 🔄 INCOMPLETE TASKS DETECTED - AI MUST CONTINUE

Your previous session ended with {total} incomplete task(s):

{task_list}
"""
        if total > total_shown:
            injection += f"\n... and {total - total_shown} more tasks (use /task-status to see all)\n"

        if older_count > 0:
            injection += f"\n📅 Note: {older_count} older task(s) from previous days also incomplete\n"

        injection += """
**PRIMARY GOAL**: Continue working until ALL tasks are completed.

**Resume Options:**
1. **Continue**: Use TaskUpdate(taskId="X", status="in_progress") to start working
2. **Reassess**: Review with TaskList, mark completed if already done
3. **Pause**: Use TaskUpdate(taskId="X", status="paused") for tasks blocked externally
4. **Abandon**: Use TaskUpdate(taskId="X", status="deleted") for irrelevant tasks

⚠️ You CANNOT stop until all tasks are marked completed, paused, or deleted.

Use /task-status to see full task list and plan linkage.
"""

        # Log resume event
        self.log_event("RESUME", "session", f"{total} incomplete tasks", "multiple")

        # Return block with injected prompt - AI sees this immediately
        return ctx.block(injection)

    def handle_stop(self, ctx: EventContext) -> Optional[Dict]:
        """Handle Stop (block if incomplete tasks - PRIMARY GOAL).

        This is the core mechanism that ensures AI continues while tasks are outstanding.

        Escape hatches (user-driven only, never automatic):
        - User runs /ar:sos to trigger emergency stop (AI outputs AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP)
        - User runs /ar:task-ignore <id> to mark specific tasks as ignored
        - User marks tasks as completed/deleted/paused via TaskUpdate

        No automatic override after N attempts - that caused premature stoppage.
        """
        # Find incomplete tasks (exclude paused/ignored - they're explicitly parked)
        incomplete_tasks = self.get_incomplete_tasks(exclude_blocking=True)

        if not incomplete_tasks:
            # Reset stop block counter
            def reset_counter(metadata):
                metadata['stop_block_count'] = 0
            self.atomic_update_metadata(reset_counter)
            return None  # Allow stop - all tasks completed

        # Increment stop block counter for diagnostics
        block_count = self.session_metadata.get('stop_block_count', 0) + 1

        def increment_counter(metadata):
            metadata['stop_block_count'] = block_count
        self.atomic_update_metadata(increment_counter)

        # Build task list with status indicators (cap at max_resume_tasks)
        max_tasks = self.config.max_resume_tasks
        task_lines = []

        for t in incomplete_tasks[:max_tasks]:
            tid = t["id"]
            subject = t["subject"]
            status = t["status"]
            status_icon = {"in_progress": "🔄", "pending": "⏸️"}.get(status, "❓")
            task_lines.append(f"  - Task #{tid}: {subject} ({status_icon} {status})")

        task_list = "\n".join(task_lines)
        total = len(incomplete_tasks)

        injection = f"""
🛑 **CANNOT STOP - INCOMPLETE TASKS** (Block #{block_count})

**PRIMARY GOAL**: You must continue working until ALL tasks are completed.

You have {total} incomplete task(s):

{task_list}
"""
        if total > max_tasks:
            injection += f"\n... and {total - max_tasks} more tasks (use /task-status to see all)\n"

        injection += """
**Required actions:**
1. Use TaskUpdate(taskId="X", status="in_progress") to start working on a task
2. Complete the work
3. Use TaskUpdate(taskId="X", status="completed") when done
4. Repeat for all tasks
5. Only stop when ALL tasks are marked completed

**Alternatives:**
- Task no longer needed: TaskUpdate(taskId="X", status="deleted")
- Task blocked externally: TaskUpdate(taskId="X", status="paused")

Use TaskList or /task-status to see current state of all tasks.

**User escape hatches** (only the user can trigger these):
- /ar:sos - Emergency stop (outputs AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP)
- /ar:task-ignore <id> - Mark a specific task as ignored to unblock stopping
"""

        # Log warning
        self.log_event("STOP_WARNING", "session",
                      f"Block #{block_count}: {total} incomplete tasks", "blocked")

        # Reset three-stage system if at STAGE_2_COMPLETED — tasks must resolve first.
        # Chain ordering: prevent_premature_stop fires BEFORE autorun_injection
        # (core.py:1094-1103 first-non-None wins). autorun_injection never runs when
        # tasks are outstanding, so the stage reset must happen here.
        if ctx.autorun_stage == EventContext.STAGE_2_COMPLETED:
            ctx.autorun_stage = EventContext.STAGE_2
            ctx.tool_calls_since_task_update = 0  # Prevent immediate re-trigger on re-entry
            injection += CONFIG.get("task_outstanding_stage3_message", "").format(
                count=total,
                names=", ".join(
                    t.get("subject", f"#{t.get('id', '?')}") for t in incomplete_tasks[:3]
                ) + ("..." if total > 3 else ""),
            )

        # BLOCK the stop - force AI to continue
        return ctx.block(injection)

    def get_plan_approval_injection(self, ctx) -> Optional[str]:
        """Get plan task context as injection string for plan acceptance.

        Returns string for caller to embed in ctx.respond(), avoiding
        abstraction inversion (building dict then unpacking it).

        Returns:
            Injection string with task context, or None if no tasks linked.
        """
        plan_key = getattr(ctx, 'plan_arguments', '')
        if not plan_key:
            return None

        plan_tasks = self.get_plan_tasks(plan_key, incomplete_only=True)
        if not plan_tasks:
            return None

        task_lines = []
        for task in plan_tasks[:self.config.max_resume_tasks]:
            status_icon = {
                "in_progress": "...", "pending": "o", "paused": "||"
            }.get(task["status"], "?")
            task_lines.append(
                f"  - Task #{task['id']}: {task['subject']} ({status_icon} {task['status']})")

        if not task_lines:
            return None

        remaining = len(plan_tasks) - len(task_lines)
        if remaining > 0:
            task_lines.append(f"\n  ... and {remaining} more tasks")

        task_context = "\n".join(task_lines)
        return (
            f"\n## Plan Accepted - Task Context\n\n"
            f"{len(plan_tasks)} task(s):\n\n{task_context}\n\n"
            f"Use TaskList to see all tasks. You CANNOT stop until all tasks "
            f"are completed or deleted.\n"
        )

    def handle_plan_approval(self, ctx) -> Optional[Dict]:
        """Handle plan approval. Delegates to get_plan_approval_injection()."""
        injection = self.get_plan_approval_injection(ctx)
        return ctx.allow(injection) if injection else None

    # === CLI Interface (Typer-like patterns - class methods) ===

    @classmethod
    def cli_status(cls, session_id: str | None = None, verbose: bool = False,
                   format: str = 'text') -> int:
        """Show task status for session (CLI command).

        Args:
            session_id: Session ID to show (None = current/latest)
            verbose: Show full task details including metadata
            format: Output format ('text', 'json', 'table')

        Returns:
            Exit code (0 = success, 1 = error)
        """
        import sys
        try:
            # Auto-detect session ID if not provided
            if not session_id:
                session_id = os.environ.get('CLAUDE_SESSION_ID')
                if not session_id:
                    print("Error: No session ID provided and CLAUDE_SESSION_ID not set")
                    return 1

            manager = cls(session_id=session_id)
            tasks = manager.tasks

            if format == 'json':
                # Wrap tasks in metadata for CLI usability
                incomplete_count = len([t for t in tasks.values()
                                        if t['status'] not in cls.COMPLETED_STATUSES])
                output = {
                    'session_id': session_id,
                    'total_tasks': len(tasks),
                    'incomplete_tasks': incomplete_count,
                    'tasks': tasks
                }
                print(json.dumps(output, indent=2))
                return 0

            elif format == 'table':
                # Simple text table
                prioritized = manager.get_prioritized_tasks()
                print(f"Task Status - Session {session_id[:8]}...")
                print()
                for task in prioritized:
                    status_icon = {'in_progress': '🔄', 'pending': '⏸️', 'paused': '⏯️',
                                  'completed': '✅', 'deleted': '🗑️', 'ignored': '🚫'}.get(task['status'], '❓')
                    print(f"  {task['id']}: {status_icon} {task['subject']} ({task['status']})")
                return 0

            else:  # format == 'text'
                # Simple text output (default)
                incomplete = manager.get_incomplete_tasks(exclude_blocking=True)

                print(f"Session: {session_id}")
                print(f"Total tasks: {len(tasks)}")
                print(f"Incomplete: {len(incomplete)}")

                if verbose and incomplete:
                    print("\nIncomplete Tasks:")
                    for task in manager.get_prioritized_tasks():
                        if task['status'] not in cls.NON_BLOCKING_STATUSES:
                            print(f"\n  Task #{task['id']}: {task['subject']}")
                            print(f"    Status: {task['status']}")
                            print(f"    Created: {datetime.fromtimestamp(task['created_at']).isoformat()}")
                            if task['blockedBy']:
                                print(f"    Blocked by: {task['blockedBy']}")

                return 0

        except Exception as e:
            print(f"Error showing task status: {e}")
            return 1

    @classmethod
    def cli_export(cls, session_id: str, output_path: str, format: str = 'json',
                   include_completed: bool = False) -> int:
        """Export task data to file (CLI command).

        Args:
            session_id: Session ID to export
            output_path: Output file path
            format: Export format ('json', 'csv', 'markdown')
            include_completed: Include completed/deleted tasks

        Returns:
            Exit code (0 = success, 1 = error)
        """
        import sys
        try:
            manager = cls(session_id=session_id)
            tasks = manager.tasks

            if not include_completed:
                tasks = {k: v for k, v in tasks.items()
                        if v['status'] not in cls.COMPLETED_STATUSES}

            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)

            if format == 'json':
                output_file.write_text(json.dumps({
                    'session_id': session_id,
                    'exported_at': time.time(),
                    'tasks': tasks,
                    'plan_tasks_map': manager.plan_tasks_map
                }, indent=2))

            elif format == 'csv':
                import csv
                with open(output_file, 'w', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=['id', 'subject', 'status', 'created_at', 'blockedBy'])
                    writer.writeheader()
                    for task in tasks.values():
                        writer.writerow({
                            'id': task['id'],
                            'subject': task['subject'],
                            'status': task['status'],
                            'created_at': datetime.fromtimestamp(task['created_at']).isoformat(),
                            'blockedBy': ','.join(task.get('blockedBy', []))
                        })

            elif format == 'markdown':
                md_lines = [
                    f"# Task Export - Session {session_id}",
                    f"\nExported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    f"\nTotal tasks: {len(tasks)}",
                    "\n## Tasks\n"
                ]
                for task in tasks.values():
                    md_lines.append(f"### Task #{task['id']}: {task['subject']}")
                    md_lines.append(f"- **Status**: {task['status']}")
                    md_lines.append(f"- **Description**: {task['description']}")
                    if task.get('blockedBy'):
                        md_lines.append(f"- **Blocked by**: {', '.join(task['blockedBy'])}")
                    md_lines.append("")

                output_file.write_text('\n'.join(md_lines))

            print(f"Exported {len(tasks)} tasks to {output_path}")
            return 0

        except Exception as e:
            print(f"Error exporting tasks: {e}")
            return 1

    @classmethod
    def cli_clear(cls, session_id: str | None = None, all_sessions: bool = False,
                  confirm: bool = True) -> int:
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
                sessions_dir = config.storage_dir
                if not sessions_dir.exists():
                    print("No task data found.")
                    return 0

                session_dirs = [d for d in sessions_dir.iterdir() if d.is_dir()]

                if confirm:
                    if not sys.stdin.isatty():
                        print("⚠️ Refusing to clear all sessions in non-interactive mode")
                        print("Use --no-confirm flag to proceed")
                        return 2
                    print(f"⚠️  WARNING: About to clear {len(session_dirs)} session(s)")
                    response = input("Type 'yes' to confirm: ")
                    if response.lower() != 'yes':
                        print("Cancelled.")
                        return 2

                import shutil
                for session_dir in session_dirs:
                    shutil.rmtree(session_dir)

                print(f"Cleared {len(session_dirs)} session(s)")
                return 0

            else:
                if not session_id:
                    session_id = os.environ.get('CLAUDE_SESSION_ID')
                    if not session_id:
                        print("Error: No session ID provided and CLAUDE_SESSION_ID not set")
                        return 1

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
                    if response.lower() != 'yes':
                        print("Cancelled.")
                        return 2

                import shutil
                storage_dir = config.storage_dir / session_id
                if storage_dir.exists():
                    shutil.rmtree(storage_dir)

                # Also clear from session_state
                with session_state(manager.global_key) as state:
                    state.clear()

                print(f"Cleared {task_count} task(s) from session")
                return 0

        except Exception as e:
            print(f"Error clearing tasks: {e}")
            return 1


    @classmethod
    def cli_gc(cls, archive: bool = True, dry_run: bool = False,
               pattern: str = "*", ttl_days: int | None = None,
               config: TaskLifecycleConfig | None = None,
               confirm: bool = True) -> int:
        """Garbage-collect stale task lifecycle data (archive-then-purge).

        ⚠️  DESTRUCTIVE OPERATION - PERMANENTLY DELETES SESSION DATA ⚠️

        SAFETY GUARANTEES (fail-safe design):
        1. Protects current active session (CLAUDE_SESSION_ID) - NEVER deleted
        2. Skips sessions with incomplete tasks (in_progress/pending work)
        3. Respects TTL - only cleans sessions older than ttl_days
        4. Uses session_state() for SessionLock protection (never direct shelve.open())
        5. Archives non-empty data to JSON before deletion (restorable backup)
        6. dry_run=True preview mode - reports without modifications
        7. Atomic archive-then-clear within single SessionLock (no data loss window)
        8. Requires confirmation by default (confirm=True) - must type 'yes' to proceed

        LIFECYCLE & USAGE:
        - GC is manual-only (never automatic) - user controls when to clean
        - Daemon doesn't auto-GC - shelves persist until user runs this
        - Recommended: Run with dry_run=True first to preview
        - Safe to run anytime - protections prevent active session damage
        - ALWAYS archives before deletion unless archive=False (NOT recommended)

        CRITICAL ORDERING (prevents corruption):
        1. Find session IDs matching pattern
        2. For each session:
           a. Check if current session → skip (protected)
           b. Acquire SessionLock via session_state(global_key, timeout=2s)
           c. Read tasks, check incomplete → skip if found
           d. Check age against TTL → skip if too recent
           e. Archive to JSON (if archive=True) - within lock
           f. Clear shelve content (state.clear()) - within lock
           g. Release lock (exit session_state context)
           h. Delete shelve files from disk
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
        import sys, fnmatch, shutil
        from .session_manager import get_session_manager

        try:
            config = config or cls._get_config()
            ttl = ttl_days if ttl_days is not None else config.task_ttl_days
            ttl_seconds = ttl * 86400
            mgr = get_session_manager()
            sessions_dir = mgr.state_dir
            # JSON key prefix for task lifecycle sessions (matches global_key format below)
            json_key_prefix = "__task_lifecycle__"
            current = os.environ.get("CLAUDE_SESSION_ID", "")
            archive_dir = config.storage_dir / "archive"

            # Show prominent warning banner (unless dry-run)
            if not dry_run:
                print("\n" + "="*70)
                print("⚠️  TASK LIFECYCLE GARBAGE COLLECTION - DESTRUCTIVE OPERATION  ⚠️")
                print("="*70)
                print()
                print("This will PERMANENTLY DELETE task data from old sessions.")
                print()
                print("Safety protections:")
                print("  ✓ Current session will NOT be deleted (if CLAUDE_SESSION_ID set)")
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
                            rest = json_key[len(json_key_prefix):]
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

                if response.lower() != 'yes':
                    print("Cancelled. No changes made.")
                    return 2

            archived = cleared = skip_active = skip_incomplete = skip_young = errors = 0

            # Step 2: Process each session with safety checks
            error_details = []

            for sid in sorted(sids):
                # Safety check 1: Never GC current active session
                # Even if it has zero tasks or all completed, active session is OFF LIMITS
                if sid == current:
                    skip_active += 1
                    if dry_run:
                        print(f"  PROTECT {sid[:12]}... (current session - never GC active)")
                    continue

                global_key = f"__task_lifecycle__{sid}"

                try:
                    # CRITICAL LOCKING: Use session_state() for SessionLock coordination.
                    # This prevents race conditions with daemon writing to same shelve.
                    # Direct shelve.open() would bypass locking and corrupt data.
                    #
                    # Timeout 2s = fail fast if daemon holds lock (indicates session active).
                    with session_state(global_key, timeout=2.0) as state:
                        tasks = state.get("tasks", {})

                        # Skip incomplete
                        incomplete = [t for t in tasks.values()
                                      if t.get("status") not in cls.NON_BLOCKING_STATUSES]
                        if incomplete:
                            skip_incomplete += 1
                            if dry_run:
                                print(f"  SKIP    {sid[:12]}... ({len(incomplete)} incomplete)")
                            continue

                        # Skip young (respect TTL)
                        if tasks:
                            newest = max(t.get("updated_at", 0) for t in tasks.values())
                            age = time.time() - newest
                            if age < ttl_seconds:
                                skip_young += 1
                                if dry_run:
                                    print(f"  SKIP    {sid[:12]}... ({age/86400:.1f}d old)")
                                continue

                        if dry_run:
                            label = "ARCHIVE+" if archive and tasks else ""
                            print(f"  {label}CLEAR  {sid[:12]}... ({len(tasks)} tasks)")
                            cleared += 1
                            continue

                        # Archive to JSON (within lock)
                        if archive and tasks:
                            archive_dir.mkdir(parents=True, exist_ok=True)
                            (archive_dir / f"{sid}.json").write_text(
                                json.dumps({
                                    "session_id": sid,
                                    "archived_at": time.time(),
                                    "schema_version": state.get("schema_version", 1),
                                    "session_metadata": state.get("session_metadata", {}),
                                    "tasks": tasks,
                                }, indent=2, default=str))
                            archived += 1

                        # Clear state (within lock)
                        state.clear()

                    # Clean audit dir
                    audit_dir = config.storage_dir / sid
                    if audit_dir.exists():
                        shutil.rmtree(audit_dir, ignore_errors=True)

                    cleared += 1

                except PermissionError as e:
                    errors += 1
                    error_details.append((sid, "Permission", str(e)[:60]))
                    print(f"  ERROR   {sid[:12]}... Permission denied")
                except Exception as e:
                    errors += 1
                    error_details.append((sid, type(e).__name__, str(e)[:60]))
                    print(f"  ERROR   {sid[:12]}... {type(e).__name__}: {e}")

            # Step 8: Summary with actionable guidance
            verb = "Would" if dry_run else "Did"
            print(f"\n=== Task Lifecycle GC {'(dry run) ' if dry_run else ''}===")
            print(f"Scanned: {len(sids)} matching pattern '{pattern}'")

            # Skipped sessions (grouped for clarity)
            if skip_active or skip_incomplete or skip_young:
                print("\nProtected/Skipped:")
                if skip_active:
                    print(f"  • Current session: {skip_active} (CLAUDE_SESSION_ID - never GC)")
                if skip_incomplete:
                    print(f"  • Incomplete tasks: {skip_incomplete} (active work in progress)")
                if skip_young:
                    print(f"  • Too recent: {skip_young} (age < {ttl}d TTL)")

            # Actions taken
            print(f"\nActions:")
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
                print("     → Backend detection failed (corrupted shelve)")
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
            if 'sessions_dir' in locals():
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
            print(f"Current settings:")
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
            if response.lower() != 'y':
                return 0

            # Interactive prompts
            enabled = input(f"Enable task lifecycle? (y/n) [current: {'y' if config.enabled else 'n'}]: ")
            if enabled.lower() in ('y', 'n'):
                config.enabled = (enabled.lower() == 'y')

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
            if debug.lower() in ('y', 'n'):
                config.debug_logging = (debug.lower() == 'y')

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
        import sys
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
        import sys
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


def register_hooks(app_instance) -> None:
    """Register all task lifecycle hooks (if enabled).

    Uses class-based handlers for DRY code organization.
    Follows plan_export.py pattern for consistency.
    """
    if not is_enabled():
        return

    @app_instance.on("PostToolUse")
    def track_task_operations(ctx: EventContext) -> Optional[Dict]:
        """Track Task tool usage for AI continuation (PostToolUse hook)."""
        if ctx.tool_name not in (TASK_CREATE_TOOLS | TASK_UPDATE_TOOLS | TASK_LIST_TOOLS | TASK_GET_TOOLS):
            return None

        try:
            # Instantiate class with auto-detected session ID
            manager = TaskLifecycle(ctx=ctx)

            if ctx.tool_name in TASK_CREATE_TOOLS:
                manager.handle_task_create(ctx)
            elif ctx.tool_name in TASK_UPDATE_TOOLS:
                ghost_result = manager.handle_task_update(ctx)
                if ghost_result == "ghost_skip":
                    if manager.config.debug_logging:
                        task_id = ctx.tool_input.get('taskId', '?')
                        status = ctx.tool_input.get('status', '?')
                        tool_result_snippet = (ctx.tool_result or '')[:80]
                        manager.log_event(
                            "GHOST_SKIP_HOOK", task_id,
                            f"requested_status={status} tool_result={tool_result_snippet!r}",
                            status="ignored",
                        )
            elif ctx.tool_name in TASK_LIST_TOOLS:
                # Update last activity timestamp
                def update_activity(metadata):
                    metadata['last_activity'] = time.time()
                manager.atomic_update_metadata(update_activity)

        except Exception as e:
            logger.warning(f"Task tracking error: {e}")
            # Fail-open: don't break hook chain on tracking errors

        return None  # Always allow tool to complete

    @app_instance.on("SessionStart")
    def resume_incomplete_tasks(ctx: EventContext) -> Optional[Dict]:
        """Resume incomplete tasks on session start."""
        if not is_enabled():
            return None

        try:
            manager = TaskLifecycle(ctx=ctx)
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
