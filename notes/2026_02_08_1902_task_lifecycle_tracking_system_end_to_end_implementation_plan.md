---
session_id: 5972dfc0-1209-4441-8d1b-6a48ba9fe265
original_path: /Users/athundt/.claude/plans/modular-greeting-crab.md
export_timestamp: 2026-02-08T19:02:24.523147
export_destination: /Users/athundt/.claude/clautorun/notes/2026-02-08-1902-task-lifecycle-tracking-system-end-to-end-implementation-plan.md
---

# Task Lifecycle Tracking System - End-to-End Implementation Plan

## User Requests (Chronological)

1. "the most recent git stash aims to track the task status on a per session basis think through a plan to do reliable end to end task lifecycle tracking, and ensuring the ai continues while tasks are outstanding and logging the task progress and status and maintaining a proper task lifecycle using all the systems and capabilities available and built into clautorun"

2. "you need to actualy check the stash with the label Testing plan export bug - unstaged changes preserved and commit value 6815e8af55b99749a4da3ee61d6d39b459300496 and check the gihub hook apis and search them for the built in task tool tracking hooks, for hooks.json:
      },
      {
        "matcher": "TaskCreate|TaskUpdate|TaskGet|TaskList",
        "hooks": [{ "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py", "timeout": 10 }]  and for a task tracking starting point:
# === TASK TOOL TRACKING (for resume capability) ===

@app.on("PostToolUse")
def track_task_operations(ctx: EventContext) -> Optional[Dict]:
    \"\"\"
    Track Task tool usage for resume capability.

    Stores task metadata in session state so if Claude stops unexpectedly,
    we can detect incomplete work and prompt for resume.

    Tracked tools: TaskCreate, TaskUpdate, TaskList, TaskGet

    API Response Formats (verified from Claude Code docs):
    - TaskCreate: \"Task #<id> created successfully: <subject>\"
    - TaskUpdate: \"Updated task #<id> status\"
    - TaskList: Returns text summary of tasks
    - TaskGet: Returns text with full task details

    Sources:
    - claude-code-plugin-help.md:256-283 (TaskCreate/TaskUpdate patterns)
    - Official docs: https://code.claude.com/docs/en/plugins-reference
    \"\"\"
    if ctx.tool_name not in (\"TaskCreate\", \"TaskUpdate\", \"TaskList\", \"TaskGet\"):
        return None

    try:
        import re
        import time
        result_text = ctx.tool_result or \"\"

        if ctx.tool_name == \"TaskCreate\":
            # Parse text response: \"Task #1 created successfully: Test task\"
            match = re.search(r'Task #(\d+) created successfully', result_text)
            if match:
                task_id = match.group(1)

                # Store created task with full metadata
                created = ctx.task_created or []
                created.append({
                    \"id\": task_id,
                    \"subject\": ctx.tool_input.get(\"subject\", \"\"),
                    \"description\": ctx.tool_input.get(\"description\", \"\"),
                    \"activeForm\": ctx.tool_input.get(\"activeForm\", \"\"),
                    \"timestamp\": time.time()
                })
                ctx.task_created = created  # Magic: auto-persists to shelve

        elif ctx.tool_name == \"TaskUpdate\":
            # Track status transitions for resume detection
            task_id = ctx.tool_input.get(\"taskId\")
            status = ctx.tool_input.get(\"status\")

            if not task_id:
                return None  # Skip if no task ID

            if status == \"completed\":
                # Track completion
                completed = ctx.task_completed or []
                if task_id not in completed:
                    completed.append(task_id)
                ctx.task_completed = completed

                # Remove from in_progress
                in_progress = ctx.task_in_progress or []
                if task_id in in_progress:
                    in_progress.remove(task_id)
                    ctx.task_in_progress = in_progress

            elif status == \"in_progress\":
                # Track active work
                in_progress = ctx.task_in_progress or []
                if task_id not in in_progress:
                    in_progress.append(task_id)
                ctx.task_in_progress = in_progress

        elif ctx.tool_name == \"TaskList\":
            # Update snapshot of current tasks
            # tool_result for TaskList is text, not JSON (parse if needed)
            ctx.last_task_list_timestamp = time.time()

    except Exception as e:
        logger.warning(f\"Task tracking error: {e}\")
        # Fail-open: don't break hook chain on tracking errors

    return None  # Always allow tool to complete
   we want to ensure the system continues running as tasks remain outstanding, and make it easy for us to provide conditional tasks like for [PLANNING] tasks as in the *plan*.md files, and for providing additional plan prompts to the ai when a plan is accepted and the context is cleared as an injection to ensure the necessary context and task and requirements info remains available, and ideally integrated so it is easy to integrate steps and changes in tasks with slash commands, make users lives and task management lifecycle and the ai's process easy to use correctly and hard to use incorrectly with hooks. Put this message and the previous message from me in a numbered list at the top of the plan, and continue inserting quotes of my messages at the top of the plan."

---

## Context

**Problem:** Claude Code sessions can end unexpectedly (token limits, crashes, user interruption) leaving tasks incomplete. Currently, there's no mechanism to:
1. **Detect** which tasks were started but not completed
2. **Resume** incomplete work in new sessions automatically
3. **Continue** working while tasks remain outstanding (prevent premature stop)
4. **Log** task progress for debugging and auditing
5. **Inject plan context** when plan accepted and context cleared
6. **Support [PLANNING] tasks** like in plan*.md commands
7. **Make task management easy to use correctly, hard to use incorrectly**

**Solution:** Implement comprehensive task lifecycle tracking using clautorun's existing persistence infrastructure (shelve backend, EventContext magic state, PostToolUse hooks). Follow proven patterns from `plan_export.py` for cross-session state management.

**Evidence from Stash@{1}:** Commit `6815e8af5` contains partial implementation:
- Added state fields: `task_created`, `task_completed`, `task_in_progress`, `last_task_list_timestamp`
- Added `track_task_operations()` PostToolUse hook (90% complete)
- Added hooks.json matcher: `"TaskCreate|TaskUpdate|TaskGet|TaskList"`
- Missing: resume logic, Stop hook prevention, logging, plan context injection, [PLANNING] support

**Key Capabilities to Leverage:**
- **Shelve persistence**: Survives daemon restart, machine reboot, Option 1 context clears
- **Magic state fields**: `ctx.field = value` auto-persists to `~/.claude/sessions/plugin_{session_id}.db`
- **Hook system**: PostToolUse, SessionStart, Stop hooks for lifecycle monitoring
- **plan_export.py pattern**: Production-proven cross-session state with atomic updates

---

## Design: Six-Component Architecture

### Component 1: State Schema (EventContext._DEFAULTS in core.py)

**Add to core.py:258-265 (enhanced from stash@{1}):**

```python
_DEFAULTS = {
    # ... existing fields ...

    # Task lifecycle tracking (v0.7.1) - enhanced superset
    'task_created': [],              # List of TaskState dicts with FULL metadata
    'task_completed': [],            # List of completed task IDs (strings) - quick lookup
    'task_in_progress': [],          # List of in-progress task IDs (strings) - quick lookup
    'last_task_list_timestamp': 0,   # When TaskList was last called (Unix timestamp)
    'plan_tasks_map': {},            # {plan_key: [task_ids]} - link tasks to plans for context injection
}
```

**Why this schema:**
- **task_created list** → FULL metadata per task (not minimal) - enables complete resume with all context
- **task_completed/in_progress lists** → Fast status lookups without iterating task_created
- **plan_tasks_map** → Link tasks to plans for context injection when plan accepted
- **Hybrid approach** → Full metadata in task_created + fast lookups via separate lists

**TaskState Entry Schema (stored in task_created list):**
```python
{
    # Core identification
    "id": "1",                    # Task ID from TaskCreate result
    "subject": "Fix login bug",   # From TaskCreate input
    "description": "Implement OAuth2 login flow with error handling",  # Full description
    "activeForm": "Fixing login bug...",    # Shown in spinner when in_progress

    # Status tracking (derived from task_completed/task_in_progress lists)
    # status = "completed" if id in task_completed
    #        = "in_progress" if id in task_in_progress
    #        = "pending" otherwise

    # Timestamps
    "created_at": 1707380000.0,   # When task was created (Unix timestamp)
    "updated_at": 1707380050.0,   # Last update time (updated on TaskUpdate calls)

    # Session tracking
    "session_id": "uuid",         # Which session created this task (for cross-session analysis)

    # Ownership and dependencies
    "owner": None,                # Optional agent assignment (from TaskUpdate)
    "blockedBy": [],              # Task IDs that must complete first (from TaskUpdate addBlockedBy)
    "blocks": [],                 # Task IDs waiting on this (from TaskUpdate addBlocks)

    # Custom tracking
    "metadata": {},               # Optional custom data (from TaskCreate/TaskUpdate metadata field)

    # Audit trail
    "tool_outputs": []            # History of TaskUpdate outputs - debug what Claude tried
}
```

**Benefits of full schema:**
- **Complete audit trail** → tool_outputs shows every status change
- **Dependency tracking** → blockedBy/blocks enables workflow analysis
- **Session linkage** → session_id tracks task across Option 1 clears
- **Timestamps** → Measure task duration, detect stale work
- **Owner tracking** → Support multi-agent workflows
- **Resume with full context** → All metadata available for intelligent resume

---

### Component 2: hooks.json Registration

**File:** `plugins/clautorun/hooks/hooks.json`

**Add after ExitPlanMode matcher (around line 19):**

```json
{
  "matcher": "TaskCreate|TaskUpdate|TaskGet|TaskList",
  "hooks": [{ "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py", "timeout": 10 }]
}
```

**Why hooks.json registration:**
- Routes all Task tool calls through hook_entry.py first
- Enables hook-based validation and tracking (like plan approval detection)
- Consistent with existing ExitPlanMode, Write, Edit hooks
- Supports both daemon mode and legacy mode

---

### Component 3: PostToolUse Hook (plugins.py)

**Implementation Location:** After `detect_plan_approval()` at plugins.py:768

**Enhanced from stash@{1} with FULL metadata tracking:**

```python
@app.on("PostToolUse")
def track_task_operations(ctx: EventContext) -> Optional[Dict]:
    """
    Track Task tool usage for resume capability with COMPLETE metadata.

    Stores full task metadata in session state so if Claude stops unexpectedly,
    we can detect incomplete work and prompt for resume with full context.

    Tracked tools: TaskCreate, TaskUpdate, TaskList, TaskGet

    Enhancements over stash@{1}:
    - FULL TaskState schema (not minimal) - all fields populated
    - Links tasks to active plan via plan_tasks_map
    - Logs task events to ~/.clautorun/task-tracking.log
    - Tracks dependencies (blockedBy, blocks)
    - Audit trail via tool_outputs history
    - Timestamps (created_at, updated_at)
    - Session tracking (session_id)

    Sources:
    - stash@{1} commit 6815e8af5 (base implementation)
    - claude-code-plugin-help.md:256-283 (TaskCreate/TaskUpdate patterns)
    """
    if ctx.tool_name not in ("TaskCreate", "TaskUpdate", "TaskList", "TaskGet"):
        return None

    try:
        import re
        import time
        result_text = ctx.tool_result or ""

        if ctx.tool_name == "TaskCreate":
            # Parse text response: "Task #1 created successfully: Test task"
            match = re.search(r'Task #(\d+) created successfully', result_text)
            if match:
                task_id = match.group(1)
                subject = ctx.tool_input.get("subject", "")

                # Store created task with FULL metadata (not minimal)
                created = ctx.task_created or []
                task_entry = {
                    # Core identification
                    "id": task_id,
                    "subject": subject,
                    "description": ctx.tool_input.get("description", ""),
                    "activeForm": ctx.tool_input.get("activeForm", ""),

                    # Timestamps
                    "created_at": time.time(),
                    "updated_at": time.time(),

                    # Session tracking
                    "session_id": ctx.session_id,

                    # Ownership and dependencies (initialized empty, updated via TaskUpdate)
                    "owner": None,
                    "blockedBy": [],
                    "blocks": [],

                    # Custom tracking
                    "metadata": ctx.tool_input.get("metadata", {}),

                    # Audit trail
                    "tool_outputs": [result_text]  # Start with create result
                }
                created.append(task_entry)
                ctx.task_created = created  # Magic: auto-persists to shelve

                # If active plan, link this task to the plan for context injection
                if ctx.plan_active and ctx.plan_arguments:
                    plan_map = ctx.plan_tasks_map or {}
                    plan_key = ctx.plan_arguments  # Use plan description as key
                    if plan_key not in plan_map:
                        plan_map[plan_key] = []
                    plan_map[plan_key].append(task_id)
                    ctx.plan_tasks_map = plan_map

                # Log creation
                _log_task_event("CREATE", task_id, subject, "pending")

        elif ctx.tool_name == "TaskUpdate":
            # Track status transitions AND update full metadata
            task_id = ctx.tool_input.get("taskId")
            status = ctx.tool_input.get("status")

            if not task_id:
                return None  # Skip if no task ID

            # Find task in created list to update metadata
            created = ctx.task_created or []
            task_entry = next((t for t in created if t["id"] == task_id), None)

            if not task_entry:
                # Task created before tracking started - initialize with minimal state
                task_entry = {
                    "id": task_id,
                    "subject": "(unknown - created before tracking)",
                    "description": "",
                    "activeForm": "",
                    "created_at": time.time(),
                    "updated_at": time.time(),
                    "session_id": ctx.session_id,
                    "owner": None,
                    "blockedBy": [],
                    "blocks": [],
                    "metadata": {},
                    "tool_outputs": []
                }
                created.append(task_entry)

            # Update metadata fields (merge semantics)
            if "subject" in ctx.tool_input:
                task_entry["subject"] = ctx.tool_input["subject"]
            if "description" in ctx.tool_input:
                task_entry["description"] = ctx.tool_input["description"]
            if "activeForm" in ctx.tool_input:
                task_entry["activeForm"] = ctx.tool_input["activeForm"]
            if "owner" in ctx.tool_input:
                task_entry["owner"] = ctx.tool_input["owner"]
            if "addBlockedBy" in ctx.tool_input:
                task_entry["blockedBy"].extend(ctx.tool_input["addBlockedBy"])
            if "addBlocks" in ctx.tool_input:
                task_entry["blocks"].extend(ctx.tool_input["addBlocks"])
            if "metadata" in ctx.tool_input:
                # Merge metadata (null values delete keys)
                for k, v in ctx.tool_input["metadata"].items():
                    if v is None:
                        task_entry["metadata"].pop(k, None)
                    else:
                        task_entry["metadata"][k] = v

            # Update timestamp and audit trail
            task_entry["updated_at"] = time.time()
            task_entry["tool_outputs"].append(result_text)

            # Persist updated entry
            ctx.task_created = created

            subject = task_entry["subject"]

            # Track status in separate lists for fast lookup
            if status == "completed":
                completed = ctx.task_completed or []
                if task_id not in completed:
                    completed.append(task_id)
                ctx.task_completed = completed

                # Remove from in_progress
                in_progress = ctx.task_in_progress or []
                if task_id in in_progress:
                    in_progress.remove(task_id)
                    ctx.task_in_progress = in_progress

                _log_task_event("COMPLETE", task_id, subject, "completed")

            elif status == "in_progress":
                in_progress = ctx.task_in_progress or []
                if task_id not in in_progress:
                    in_progress.append(task_id)
                ctx.task_in_progress = in_progress

                _log_task_event("START", task_id, subject, "in_progress")

            elif status == "deleted":
                # Track as completed (so it's not incomplete)
                completed = ctx.task_completed or []
                if task_id not in completed:
                    completed.append(task_id)
                ctx.task_completed = completed

                # Remove from in_progress
                in_progress = ctx.task_in_progress or []
                if task_id in in_progress:
                    in_progress.remove(task_id)
                    ctx.task_in_progress = in_progress

                _log_task_event("DELETE", task_id, subject, "deleted")

        elif ctx.tool_name == "TaskList":
            # Update snapshot of current tasks
            ctx.last_task_list_timestamp = time.time()

    except Exception as e:
        logger.warning(f"Task tracking error: {e}")
        # Fail-open: don't break hook chain on tracking errors

    return None  # Always allow tool to complete
```

**Helper Function (in plugins.py):**

```python
def _log_task_event(event_type: str, task_id: str, subject: str, status: str) -> None:
    """Log task lifecycle events to dedicated log file.

    Args:
        event_type: CREATE, START, COMPLETE, RESUME, STOP_WARNING
        task_id: Task ID or "session" for session-level events
        subject: Task subject line
        status: Task status (pending, in_progress, completed)
    """
    from pathlib import Path
    import time

    log_path = Path.home() / ".clautorun" / "task-tracking.log"
    log_path.parent.mkdir(exist_ok=True)

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    with open(log_path, "a") as f:
        f.write(f"{timestamp} [{event_type}] Task #{task_id} ({status}): {subject}\n")
```

---

### Component 4: Plan Context Injection (plugins.py)

**Purpose:** When plan accepted, inject task context so AI knows what to work on even after context clear (Option 1).

**Implementation Location:** Enhance existing `detect_plan_approval()` at plugins.py:724-768

```python
@app.on("PostToolUse")
def detect_plan_approval(ctx: EventContext) -> Optional[Dict]:
    """Detect plan approval via ExitPlanMode tool PostToolUse event.

    NEW: Inject task list if plan has associated tasks.
    """
    if ctx.tool_name != "ExitPlanMode":
        return None

    tool_result = ctx.tool_result or ""
    if "approved your plan" not in tool_result.lower():
        return None

    # Existing autorun activation logic
    ctx.plan_active = False  # Plan ends, autorun begins
    ctx.autorun_active = True
    ctx.autorun_stage = 0
    ctx.autorun_task = ctx.plan_arguments or "Execute the accepted plan"

    # NEW: Inject task context if plan has associated tasks
    plan_key = ctx.plan_arguments
    plan_map = ctx.plan_tasks_map or {}
    task_ids = plan_map.get(plan_key, [])

    if task_ids:
        # Build task list from created tasks
        created = ctx.task_created or []
        completed = ctx.task_completed or []
        in_progress = ctx.task_in_progress or []

        task_lines = []
        for tid in task_ids:
            task_entry = next((t for t in created if t["id"] == tid), None)
            if not task_entry:
                continue

            subject = task_entry["subject"]
            if tid in completed:
                status = "✅ completed"
            elif tid in in_progress:
                status = "🔄 in_progress"
            else:
                status = "⏸️ pending"

            # Only show incomplete tasks
            if tid not in completed:
                task_lines.append(f"  - Task #{tid}: {subject} ({status})")

        if task_lines:
            task_context = "\n".join(task_lines)
            injection = f"""
## Plan Accepted - Task Context

Your plan has been approved. Here are the tasks you created during planning:

{task_context}

Use TaskList to see all current tasks, TaskUpdate to mark progress.
Remember: Complete all tasks before stopping (check for AUTORUN completion markers).

{CONFIG['stage1_instruction']}
"""
        else:
            # All tasks completed during planning
            injection = CONFIG['stage1_instruction']
    else:
        # No tasks created during planning
        injection = CONFIG['stage1_instruction']

    return ctx.allow(injection)
```

---

### Component 5: SessionStart Hook (plugins.py)

**Purpose:** Detect incomplete tasks from previous session and prompt AI to resume.

**Implementation Location:** After plan_export SessionStart handler at plugins.py:933

```python
@app.on("SessionStart")
def resume_incomplete_tasks(ctx: EventContext) -> Optional[Dict]:
    """
    Detect incomplete tasks on session start and prompt AI to resume.

    Uses task_created, task_completed, task_in_progress lists from state.
    Incomplete = created but not in completed list.

    Strategy:
    1. Check for tasks in created list but not in completed
    2. If found, inject resume prompt with task details
    3. AI sees prompt immediately and can continue or reassess
    """
    try:
        created = ctx.task_created or []
        completed = ctx.task_completed or []
        in_progress = ctx.task_in_progress or []

        # Find incomplete tasks (created but not completed)
        incomplete = [t for t in created if t["id"] not in completed]

        if not incomplete:
            return None  # No incomplete tasks - session proceeds normally

        # Separate by status for better visibility
        active_tasks = [t for t in incomplete if t["id"] in in_progress]
        pending_tasks = [t for t in incomplete if t["id"] not in in_progress]

        # Build resume prompt
        lines = []
        if active_tasks:
            lines.append("**In Progress:**")
            for t in active_tasks:
                lines.append(f"  - Task #{t['id']}: {t['subject']}")

        if pending_tasks:
            lines.append("\n**Pending:**")
            for t in pending_tasks:
                lines.append(f"  - Task #{t['id']}: {t['subject']}")

        task_list = "\n".join(lines)
        total = len(incomplete)

        injection = f"""
## INCOMPLETE TASKS DETECTED

Your previous session ended with {total} incomplete task(s):

{task_list}

**Resume Options:**
1. **Continue**: Use TaskUpdate to mark tasks as in_progress and complete them
2. **Reassess**: Review with TaskList, mark completed if already done
3. **Abandon**: Mark irrelevant tasks as deleted with TaskUpdate

Use TaskList to see all current tasks, then TaskUpdate to change status as needed.
"""

        # Log resume event
        _log_task_event("RESUME", "session", f"{total} incomplete tasks", "multiple")

        # Return block with injected prompt - AI sees this immediately
        return ctx.block(injection)

    except Exception as e:
        logger.warning(f"Task resume detection error: {e}")
        return None  # Fail-open
```

---

### Component 6: Stop Hook Enhancement - Prevent Premature Exit

**Purpose:** Block AI from stopping if tasks are incomplete (ensure AI continues while tasks outstanding).

**Modify existing Stop handler** at plugins.py:774-870 to add task check BEFORE autorun logic:

```python
@app.on("Stop")
def handle_stop(ctx: EventContext) -> Optional[Dict]:
    """Handle Stop event with task completion check and autorun stage detection.

    NEW: Blocks stop if tasks are incomplete - ensures AI continues working.
    This implements the "ensure the ai continues while tasks are outstanding" requirement.
    """

    # NEW: Check for incomplete tasks FIRST (highest priority)
    created = ctx.task_created or []
    completed = ctx.task_completed or []
    incomplete_tasks = [t for t in created if t["id"] not in completed]

    if incomplete_tasks:
        # Build task list with status indicators
        task_lines = []
        in_progress = ctx.task_in_progress or []

        for t in incomplete_tasks:
            tid = t["id"]
            subject = t["subject"]
            status = "in_progress" if tid in in_progress else "pending"
            task_lines.append(f"  - Task #{tid}: {subject} ({status})")

        task_list = "\n".join(task_lines)
        total = len(incomplete_tasks)

        injection = f"""
⚠️ **CANNOT STOP - INCOMPLETE TASKS**

You have {total} incomplete task(s). You must complete all tasks before stopping:

{task_list}

**Required actions:**
1. Use TaskUpdate(taskId="X", status="in_progress") to start working on a task
2. Complete the work
3. Use TaskUpdate(taskId="X", status="completed") when done
4. Repeat for all tasks
5. Only stop when ALL tasks are marked completed

**Alternative:**
- If a task is no longer needed: TaskUpdate(taskId="X", status="deleted")

Use TaskList to see current state of all tasks.
"""

        # Log warning
        _log_task_event("STOP_WARNING", "session", f"{total} incomplete tasks", "blocked")

        # BLOCK the stop - force AI to continue
        return ctx.block(injection)

    # Existing autorun stage detection logic continues here...
    # (only reached if all tasks are completed)

    if ctx.autorun_active:
        # ... existing stage-based stop handling ...
        pass

    return None  # Allow stop if no incomplete tasks
```

**Why this approach:**
- **Blocks stop** rather than warning → enforces task completion
- **Runs first** before autorun logic → highest priority
- **Clear actions** → tells AI exactly what to do
- **Hard to use incorrectly** → AI cannot accidentally stop with work incomplete

---

### Component 7: Task Status Command (new command)

**File:** `plugins/clautorun/commands/task-status.md`

```markdown
---
name: task-status
description: Show task lifecycle tracking status and incomplete tasks
aliases: [ts, task-state, tasks]
---

# Task Lifecycle Status

!`python3 -c "
import sys
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/src')
from clautorun.core import EventContext

# Load current session state
ctx = EventContext('current', 'task-status')
created = ctx.task_created or []
completed = ctx.task_completed or []
in_progress = ctx.task_in_progress or []

if not created:
    print('No tasks tracked in this session.')
    sys.exit(0)

# Count by status
incomplete = [t for t in created if t['id'] not in completed]
active = [t for t in incomplete if t['id'] in in_progress]
pending = [t for t in incomplete if t['id'] not in in_progress]

print('## Task Summary')
print(f'Total tasks: {len(created)}')
print(f'  - Completed: {len(completed)}')
print(f'  - In Progress: {len(active)}')
print(f'  - Pending: {len(pending)}')

# Show incomplete tasks
if incomplete:
    print('\\n## Incomplete Tasks')
    if active:
        print('\\n**In Progress:**')
        for t in active:
            print(f'  - Task #{t[\"id\"]}: {t[\"subject\"]}')
    if pending:
        print('\\n**Pending:**')
        for t in pending:
            print(f'  - Task #{t[\"id\"]}: {t[\"subject\"]}')
else:
    print('\\n✅ All tasks completed!')

print('\\n## Log File')
print('~/.clautorun/task-tracking.log')

# Show plan linkage if any
plan_map = ctx.plan_tasks_map or {}
if plan_map:
    print('\\n## Tasks by Plan')
    for plan_key, task_ids in plan_map.items():
        print(f'\\n**{plan_key}:**')
        for tid in task_ids:
            task = next((t for t in created if t[\"id\"] == tid), None)
            if task:
                status = 'completed' if tid in completed else 'incomplete'
                print(f'  - Task #{tid}: {task[\"subject\"]} ({status})')
"`

**Usage:**
- `/task-status` - View current task state
- `/ts` - Quick alias
```

---

## Implementation Plan (6 Commits)

### Commit 1: hooks.json Registration + State Schema
**Files:**
- `plugins/clautorun/hooks/hooks.json`: Add TaskCreate|TaskUpdate|TaskGet|TaskList matcher
- `plugins/clautorun/src/clautorun/core.py`: Add task tracking fields to EventContext._DEFAULTS

**Changes:**
```python
# core.py:258-265
_DEFAULTS = {
    # ... existing fields ...
    'task_created': [],              # From stash@{1}
    'task_completed': [],            # From stash@{1}
    'task_in_progress': [],          # From stash@{1}
    'last_task_list_timestamp': 0,   # From stash@{1}
    'plan_tasks_map': {},            # NEW: link tasks to plans
}
```

```json
// hooks.json - add after ExitPlanMode matcher
{
  "matcher": "TaskCreate|TaskUpdate|TaskGet|TaskList",
  "hooks": [{ "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py", "timeout": 10 }]
}
```

**Tests:**
- Verify state fields exist: `assert "task_created" in EventContext._DEFAULTS`
- Verify hooks.json has Task matcher

---

### Commit 2: PostToolUse Hook + Logging (from stash@{1})
**Files:**
- `plugins/clautorun/src/clautorun/plugins.py`: Add `track_task_operations()` hook after line 768
- `plugins/clautorun/src/clautorun/plugins.py`: Add `_log_task_event()` helper

**Starting point:** Use stash@{1} implementation with enhancements:
- Add plan_tasks_map linking when ctx.plan_active
- Add _log_task_event() calls for CREATE, START, COMPLETE
- Keep existing task_created/completed/in_progress list logic

**Tests:**
- TaskCreate → verify task added to task_created list
- TaskUpdate status="in_progress" → verify ID in task_in_progress list
- TaskUpdate status="completed" → verify ID in task_completed list, removed from task_in_progress
- Verify log file created at `~/.clautorun/task-tracking.log`

---

### Commit 3: Plan Context Injection
**Files:**
- `plugins/clautorun/src/clautorun/plugins.py`: Enhance `detect_plan_approval()` at line 724

**Changes:**
- When plan approved, inject task list from plan_tasks_map
- Show incomplete tasks with status indicators
- Include autorun stage1_instruction

**Tests:**
- Create tasks during planning → accept plan → verify task context injected
- Accept plan with no tasks → verify standard stage1 instruction
- Accept plan with all completed tasks → verify no task list

---

### Commit 4: SessionStart Resume Logic
**Files:**
- `plugins/clautorun/src/clautorun/plugins.py`: Add `resume_incomplete_tasks()` hook after line 933

**Changes:**
- Find tasks in task_created not in task_completed
- Separate into in_progress vs pending
- Inject resume prompt with task list
- Log RESUME event

**Tests:**
- Start session with incomplete tasks → verify injection
- Start session with all completed → verify no injection
- Verify RESUME log entry

---

### Commit 5: Stop Hook - Block on Incomplete Tasks
**Files:**
- `plugins/clautorun/src/clautorun/plugins.py`: Enhance `handle_stop()` at line 774

**Changes:**
- Add incomplete task check BEFORE autorun logic (highest priority)
- BLOCK (not warn) if tasks incomplete
- Tell AI exactly what to do: TaskUpdate to mark completed/deleted
- Log STOP_WARNING event

**Tests:**
- Stop with incomplete tasks → verify BLOCK (not allow)
- Stop with all completed → verify proceeds to autorun logic
- Verify STOP_WARNING log entry

---

### Commit 6: Task Status Command + Documentation
**Files:**
- `plugins/clautorun/commands/task-status.md`: New command
- `plugins/clautorun/CLAUDE.md`: Document task tracking system
- `README.md`: Add task tracking section

**Tests:**
- Invoke `/task-status` with tasks → verify output shows created/completed/in_progress counts
- Invoke with no tasks → verify "No tasks tracked"
- Verify plan linkage shown if plan_tasks_map has entries

---

## Verification Checklist

### End-to-End Test Scenario

1. **Create tasks with full metadata:**
   ```
   TaskCreate(subject="Fix bug", description="Login form validation", activeForm="Fixing bug...", metadata={"priority": "high"})
   TaskCreate(subject="Add tests", description="Unit tests for login", activeForm="Writing tests...")
   ```
   - ✅ Verify: Both tasks in `ctx.task_created` list with ALL fields populated
   - ✅ Verify: Each task has created_at, updated_at, session_id, blockedBy=[], blocks=[], tool_outputs=[...]
   - ✅ Verify: `~/.clautorun/task-tracking.log` has CREATE entries
   - ✅ Verify: `/task-status` shows 2 pending tasks

2. **Update task with dependencies:**
   ```
   TaskUpdate(taskId="2", addBlockedBy=["1"])  # Task 2 waits for task 1
   TaskUpdate(taskId="1", status="in_progress")
   ```
   - ✅ Verify: task_created[1]["blockedBy"] == ["1"]
   - ✅ Verify: "1" in ctx.task_in_progress
   - ✅ Verify: task_created[0]["tool_outputs"] contains both create + update results
   - ✅ Verify: updated_at timestamp changed

3. **Complete task and update metadata:**
   ```
   TaskUpdate(taskId="1", status="completed", metadata={"duration": "15min"})
   ```
   - ✅ Verify: "1" in ctx.task_completed
   - ✅ Verify: "1" NOT in ctx.task_in_progress
   - ✅ Verify: task_created[0]["metadata"]["duration"] == "15min"
   - ✅ Verify: Log has COMPLETE entry

4. **Stop with incomplete work (BLOCKS):**
   - Try to stop session
   - ✅ Verify: Stop hook **BLOCKS** (not just warns) with Task #2 message
   - ✅ Verify: Message tells exactly what to do: TaskUpdate(taskId="2", status="completed")
   - ✅ Verify: Log has STOP_WARNING entry

5. **Complete remaining work and stop:**
   ```
   TaskUpdate(taskId="2", status="completed")
   ```
   - Try to stop again
   - ✅ Verify: Stop proceeds (all tasks completed)
   - ✅ Verify: "2" in ctx.task_completed

6. **Resume detection (full context):**
   - Start new session (simulate restart)
   - ✅ Verify: SessionStart hook injects resume prompt with task subjects
   - ✅ Verify: Shows in_progress vs pending separately
   - ✅ Verify: Log has RESUME entry

7. **Plan context injection:**
   - Enter plan mode, create [PLANNING] tasks
   - Accept plan
   - ✅ Verify: Plan approval injects task list from plan_tasks_map
   - ✅ Verify: Shows incomplete tasks with status indicators
   - ✅ Verify: Includes autorun stage1_instruction

8. **Cross-session persistence (all fields):**
   - Restart daemon (`pkill -f clautorun.*daemon`)
   - Access task state
   - ✅ Verify: All tasks in task_created with full metadata (blockedBy, blocks, tool_outputs, timestamps)
   - ✅ Verify: State loaded from shelve correctly

9. **Option 1 context clear:**
   - Trigger Option 1 in Claude Code (new session_id)
   - ✅ Verify: Old session's tasks still in shelve (different session_id)
   - ✅ Verify: New session starts clean (no tasks)

10. **Deleted task handling:**
    ```
    TaskUpdate(taskId="3", status="deleted")
    ```
    - ✅ Verify: "3" in ctx.task_completed (treated as completed)
    - ✅ Verify: "3" NOT shown in incomplete list
    - ✅ Verify: Log has DELETE entry

---

## Critical Files to Modify

| File | Lines | Change |
|------|-------|--------|
| `core.py` | 258-265 | Add task_state fields to _DEFAULTS |
| `plugins.py` | After 768 | Add track_task_lifecycle() PostToolUse hook |
| `plugins.py` | After 933 | Add resume_incomplete_tasks() SessionStart hook |
| `plugins.py` | 774-870 | Enhance handle_stop() with task warning |
| `plugins.py` | Top | Add _log_task_event() helper |
| `commands/task-status.md` | New | Task status command |

**Absolute Paths:**
- `/Users/athundt/.claude/clautorun/plugins/clautorun/src/clautorun/core.py`
- `/Users/athundt/.claude/clautorun/plugins/clautorun/src/clautorun/plugins.py`
- `/Users/athundt/.claude/clautorun/plugins/clautorun/commands/task-status.md`

---

## Architecture Benefits

1. **Reliability:** Shelve backend survives crashes, reboots, daemon restarts
2. **Atomicity:** Magic state fields auto-persist with no TOCTOU races
3. **Logging:** Dedicated log file (`~/.clautorun/task-tracking.log`) for debugging
4. **Resume:** SessionStart hook detects incomplete work automatically
5. **Prevents Premature Exit:** Stop hook BLOCKS (not warns) if tasks incomplete
6. **Visibility:** `/task-status` command shows current state at any time
7. **Cross-Session:** Uses same proven persistence as plan_export.py
8. **Zero Config:** Works out-of-box, no user setup required
9. **Plan Context Injection:** Tasks linked to plans survive context clears
10. **Full Audit Trail:** Complete metadata for debugging and analysis

---

## Design Philosophy: Easy to Use Correctly, Hard to Use Incorrectly

### 1. **Automatic Task Tracking (No User Action Required)**

**Good:** Tasks automatically tracked on creation - no setup needed
```python
# User just creates tasks normally - tracking happens automatically
TaskCreate(subject="Fix bug")  # ← Tracked via PostToolUse hook
```

**Bad (prevented):** Requiring manual tracking registration
```python
# DON'T need to do this (hook handles it automatically)
ctx.register_task(...)  # ← Not needed!
```

### 2. **Stop Hook Blocks on Incomplete Tasks (Enforced Completion)**

**Good:** Cannot stop with work incomplete - forced to finish or explicitly abandon
```python
# AI tries to stop with incomplete tasks
# Hook BLOCKS: "You have 3 incomplete tasks. Complete them first or mark deleted."
# AI must: TaskUpdate(taskId="1", status="completed")
```

**Bad (prevented):** AI stops silently leaving work incomplete
```python
# Prevented: Can't do this anymore
# Stop happens even with tasks pending ← BLOCKED by hook
```

### 3. **Resume Prompts Injected Automatically (No Manual Resume)**

**Good:** SessionStart detects incomplete tasks and tells AI what to do
```python
# New session starts
# Hook injects: "INCOMPLETE TASKS: Task #1: Fix bug (in_progress)"
# AI sees this immediately and continues work
```

**Bad (prevented):** User has to remember to check for incomplete work
```python
# DON'T need: "Did I have unfinished tasks last session?"
# System tells you automatically!
```

### 4. **Plan Context Injection (Survives Context Clears)**

**Good:** When plan accepted, tasks linked to plan are injected
```python
# Plan approved → Option 1 clears context
# Hook injects: "Your plan tasks: Task #1: Implement OAuth (pending)"
# AI knows what to work on even after context clear
```

**Bad (prevented):** Losing track of what to do after plan approval
```python
# Prevented: AI doesn't forget the plan's tasks
# plan_tasks_map preserves linkage
```

### 5. **Full Metadata Tracking (Complete Debug Trail)**

**Good:** Every field tracked automatically - nothing missing for debugging
```python
# Task automatically has: timestamps, dependencies, audit trail, session ID
# Can debug: "Why did this task take 2 hours?" → check tool_outputs history
```

**Bad (prevented):** Missing critical metadata when debugging issues
```python
# Prevented: No more "I don't know what dependencies this task had"
# blockedBy/blocks fields capture everything
```

### 6. **Status Derived from Lists (Can't Get Out of Sync)**

**Good:** Status is derived, not stored separately - can't contradict
```python
# status = "completed" if id in task_completed
#        = "in_progress" if id in task_in_progress
#        = "pending" otherwise
# Single source of truth!
```

**Bad (prevented):** status field contradicting list membership
```python
# Prevented: task["status"] = "completed" BUT id NOT in task_completed
# Can't happen - status is computed, not stored
```

### 7. **Slash Command Visibility (/task-status)**

**Good:** Single command shows everything - no guesswork
```python
/task-status  # ← Shows all tasks, statuses, plan linkage
# Output:
# - Completed: 5
# - In Progress: 2
# - Pending: 3
# (lists each with subject)
```

**Bad (prevented):** Having to query state manually
```python
# DON'T need: "How do I check task status?"
# Just: /task-status (or /ts)
```

### 8. **hooks.json Registration (Declarative, Not Imperative)**

**Good:** Task tracking enabled by adding one matcher to hooks.json
```json
{"matcher": "TaskCreate|TaskUpdate|TaskGet|TaskList"}
```

**Bad (prevented):** Scattered hook registrations across files
```python
# Prevented: Don't need to register hooks in code
# hooks.json is single source of truth
```

### Summary: Pit of Success Architecture

| User Action | What Happens | Failure Mode Prevented |
|-------------|--------------|------------------------|
| Create task | Auto-tracked with full metadata | Can't forget to track |
| Try to stop | Blocked if incomplete | Can't abandon work accidentally |
| New session | Resume prompt injected | Can't lose context |
| Plan approved | Tasks injected to new context | Can't forget plan goals |
| Check status | `/task-status` always available | Can't get lost |
| Update task | Metadata/dependencies auto-saved | Can't lose audit trail |

**Result:** System guides AI toward correct behavior (complete all tasks) and makes incorrect behavior (stop with work incomplete) impossible.

---

## Limitations and Tradeoffs

1. **Session-Scoped State:** Tasks in `task_state` are per session_id
   - Option 1 creates new session → old tasks not visible
   - Workaround: Could use GLOBAL_SESSION_ID like plan_export if needed
   - Current design: Each session tracks its own tasks (simpler)

2. **No Cross-Session Task Aggregation:**
   - Can't see tasks from previous sessions
   - Could add if needed via GLOBAL state + SessionStart sync

3. **TaskList Parsing Dependency:**
   - Relies on JSON output format from Claude's TaskList tool
   - If format changes, sync logic breaks (gracefully degrades)

4. **No Scheduled Reminders:**
   - Resume only happens on SessionStart
   - No periodic "you have incomplete tasks" prompts during long sessions
   - Could add via periodic injection if needed

5. **Log File Growth:**
   - `task-tracking.log` appends forever
   - Consider log rotation in production use

---

---

## Underlying Goals & Benefits Analysis

### What This System Achieves

**Primary Goals:**
1. **Prevent Incomplete Work** - AI cannot stop session with unfinished tasks
2. **Enable Cross-Session Resume** - Work continues across crashes/token limits
3. **Maintain Plan Context** - Tasks survive Option 1 context clears
4. **Provide Visibility** - Humans and AI can see current task state
5. **Create Audit Trail** - Debug why/when/how tasks changed
6. **Automatic Enforcement** - No manual tracking, hooks enforce completion

**Success Metrics:**
- **Zero abandoned tasks** - Stop hook blocks incomplete work
- **100% resume rate** - SessionStart always detects incomplete tasks
- **Context preservation** - Plan tasks injected after approval
- **Complete audit trail** - Every task change logged with timestamp

---

### How This Helps AI

| Problem AI Faces | How System Helps | Mechanism |
|------------------|------------------|-----------|
| **"Should I stop or continue?"** | Clear signal: blocked if tasks incomplete | Stop hook checks task_completed list, blocks with specific instructions |
| **"What was I working on?"** | Resume prompt lists incomplete tasks by status | SessionStart hook injects task list on new session |
| **"What should I do after plan approval?"** | Plan tasks injected with status | Plan approval hook injects from plan_tasks_map |
| **"Did I already finish this task?"** | Fast status lookup via /task-status | Command queries task_created/completed/in_progress lists |
| **"What dependencies block this task?"** | Full metadata shows blockedBy/blocks | TaskState schema includes dependency tracking |
| **"Why did this task fail?"** | Audit trail shows all updates | tool_outputs history captures every TaskUpdate |

**Cognitive Load Reduction:**
- **Before:** AI must remember all tasks, manually track status, remember to check before stopping
- **After:** System tells AI what's incomplete, blocks premature stops, provides current state on demand

---

### How This Helps Humans

| Human Pain Point | How System Helps | Concrete Benefit |
|------------------|------------------|------------------|
| **Session crashes lose work** | Incomplete tasks detected on resume | Don't have to recreate lost context |
| **"What is AI working on?"** | `/task-status` shows real-time state | Visibility without interrupting AI |
| **"Why did AI stop early?"** | Logs show STOP_WARNING + task list | Debug incomplete work issues |
| **"Did AI complete my request?"** | Stop blocked until tasks done | Confidence work is complete |
| **"What happened in this session?"** | task-tracking.log audit trail | Post-mortem analysis of behavior |
| **Plan approved but AI forgot tasks** | Plan context injection preserves tasks | Tasks survive context clears |
| **Manual task tracking burden** | Fully automatic via hooks | Zero overhead, no commands to remember |

**Developer Experience:**
- **Before:** Manually track tasks in mind, check before stopping, lose context on crashes
- **After:** System manages everything, enforces completion, preserves context automatically

---

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         TASK LIFECYCLE SYSTEM                            │
└─────────────────────────────────────────────────────────────────────────┘

                          ┌──────────────────┐
                          │   Claude Code    │
                          │   (User Input)   │
                          └────────┬─────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
             TaskCreate      TaskUpdate       TaskList
                    │              │              │
                    └──────────────┼──────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │   hooks.json Matcher         │
                    │   TaskCreate|TaskUpdate|     │
                    │   TaskGet|TaskList           │
                    └──────────────┬───────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │   hook_entry.py              │
                    │   (Routes to daemon)         │
                    └──────────────┬───────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                         DAEMON (plugins.py)                               │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  @app.on("PostToolUse")                                         │    │
│  │  def track_task_operations(ctx):                                │    │
│  │                                                                  │    │
│  │    if tool_name == "TaskCreate":                                │    │
│  │      ┌────────────────────────────────────────────────────┐    │    │
│  │      │ Extract task ID from result                        │    │    │
│  │      │ Build TaskState with FULL metadata                 │    │    │
│  │      │ Append to ctx.task_created                         │    │    │
│  │      │ Link to plan via ctx.plan_tasks_map               │    │    │
│  │      │ Log CREATE event                                   │    │    │
│  │      └────────────────────────────────────────────────────┘    │    │
│  │                                                                  │    │
│  │    elif tool_name == "TaskUpdate":                              │    │
│  │      ┌────────────────────────────────────────────────────┐    │    │
│  │      │ Find task in task_created list                     │    │    │
│  │      │ Update metadata (subject, description, deps, etc)  │    │    │
│  │      │ Update timestamp, append to tool_outputs           │    │    │
│  │      │ If status="completed": add to task_completed       │    │    │
│  │      │ If status="in_progress": add to task_in_progress   │    │    │
│  │      │ Log START/COMPLETE/DELETE event                    │    │    │
│  │      └────────────────────────────────────────────────────┘    │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  @app.on("PostToolUse")                                         │    │
│  │  def detect_plan_approval(ctx):                                 │    │
│  │                                                                  │    │
│  │    if tool_name == "ExitPlanMode" and "approved":               │    │
│  │      ┌────────────────────────────────────────────────────┐    │    │
│  │      │ Get task IDs from plan_tasks_map[plan_key]        │    │    │
│  │      │ Build task list with status indicators            │    │    │
│  │      │ Inject: "Your plan tasks: ..." + stage1 guide     │    │    │
│  │      └────────────────────────────────────────────────────┘    │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  @app.on("SessionStart")                                        │    │
│  │  def resume_incomplete_tasks(ctx):                              │    │
│  │                                                                  │    │
│  │      ┌────────────────────────────────────────────────────┐    │    │
│  │      │ Find incomplete: task_created - task_completed     │    │    │
│  │      │ Separate by in_progress vs pending                │    │    │
│  │      │ Inject: "INCOMPLETE TASKS: ..." with list         │    │    │
│  │      │ Log RESUME event                                  │    │    │
│  │      └────────────────────────────────────────────────────┘    │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  @app.on("Stop")                                                │    │
│  │  def handle_stop(ctx):                                          │    │
│  │                                                                  │    │
│  │      ┌────────────────────────────────────────────────────┐    │    │
│  │      │ Check incomplete: task_created - task_completed    │    │    │
│  │      │ If incomplete:                                     │    │    │
│  │      │   BLOCK stop with task list                       │    │    │
│  │      │   "Complete or mark deleted"                      │    │    │
│  │      │   Log STOP_WARNING                                │    │    │
│  │      │ Else: allow stop                                  │    │    │
│  │      └────────────────────────────────────────────────────┘    │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└──────────────────────────┬────────────────────────────────────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────────┐
        │     EventContext Magic State             │
        │                                          │
        │  ctx.task_created = [...]  ─────────────┼──┐
        │  ctx.task_completed = [...]  ────────────┼──┤
        │  ctx.task_in_progress = [...]  ──────────┼──┤
        │  ctx.plan_tasks_map = {...}  ────────────┼──┤
        │                                          │  │
        │  (auto-persists on assignment)          │  │
        └──────────────────┬───────────────────────┘  │
                           │                          │
                           ▼                          │
        ┌──────────────────────────────────────┐     │
        │   ThreadSafeDB (in-memory cache)     │     │
        │   - 1-5ms access after first load    │     │
        │   - Survives hook calls, lost on     │     │
        │     daemon restart                   │     │
        └──────────────────┬───────────────────┘     │
                           │                          │
                           ▼                          │
        ┌──────────────────────────────────────┐     │
        │   shelve Backend (persistent)        │◄────┘
        │   ~/.claude/sessions/                │
        │     plugin_{session_id}.db           │
        │                                      │
        │   - fcntl.flock() for atomicity     │
        │   - Survives daemon restart,         │
        │     machine reboot                   │
        └──────────────────┬───────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────┐
        │   Audit Log                          │
        │   ~/.clautorun/task-tracking.log     │
        │                                      │
        │   [CREATE] Task #1 (pending): Fix   │
        │   [START] Task #1 (in_progress): Fix│
        │   [COMPLETE] Task #1 (completed): Fix│
        │   [STOP_WARNING] session blocked     │
        │   [RESUME] session: 2 incomplete     │
        └──────────────────────────────────────┘

                  ┌──────────────────┐
                  │  User Commands   │
                  └────────┬─────────┘
                           │
                           ▼
        ┌──────────────────────────────────────┐
        │   /task-status (or /ts)              │
        │                                      │
        │   Reads: task_created, completed,    │
        │          in_progress, plan_tasks_map │
        │   Shows: Summary + incomplete list   │
        └──────────────────────────────────────┘
```

**Data Flow:**
1. **TaskCreate** → hooks.json → PostToolUse → track_task_operations → task_created list → shelve
2. **TaskUpdate** → hooks.json → PostToolUse → track_task_operations → update metadata + status lists → shelve
3. **Stop** → Stop hook → check incomplete → BLOCK if found → inject task list
4. **SessionStart** → SessionStart hook → check incomplete → inject resume prompt
5. **Plan approval** → PostToolUse → detect_plan_approval → inject plan tasks from plan_tasks_map

---

## Stage-by-Stage Benefits & Limitations

### Stage 1: hooks.json Registration

**Benefits:**
- ✅ Declarative - single source of truth for what's tracked
- ✅ Runs before Claude sees result - can modify behavior
- ✅ Consistent with existing hook patterns (ExitPlanMode, Write, Edit)
- ✅ 10s timeout prevents hook from hanging session

**Limitations:**
- ❌ Limited to regex matching - can't do complex logic in matcher
- ❌ Timeout too short for very complex processing (but tracking is fast)
- ❌ Must restart daemon to reload hooks.json changes (dev friction)

---

### Stage 2: PostToolUse Hook (track_task_operations)

**Benefits:**
- ✅ Sees full tool input + output - complete context
- ✅ Fires synchronously - state updated before next tool call
- ✅ Magic state persistence - zero boilerplate, automatic shelve
- ✅ Full metadata captured - all fields populated
- ✅ Graceful failures - try/except prevents breaking hook chain

**Limitations:**
- ❌ Fires AFTER tool completes - can't prevent invalid TaskCreate
- ❌ Parses text result via regex - fragile if format changes
- ❌ List append creates duplicates if hook fires twice (rare but possible)
- ❌ No validation - malformed metadata silently accepted
- ❌ Unbounded growth - task_created list grows forever (no pruning)

---

### Stage 3: Plan Context Injection (detect_plan_approval)

**Benefits:**
- ✅ Tasks survive Option 1 context clear - critical for usability
- ✅ Shows status indicators (pending/in_progress) - clear state
- ✅ Includes autorun stage1 instruction - AI knows what to do
- ✅ Only shows incomplete tasks - focuses attention

**Limitations:**
- ❌ Verbose if many tasks - could overwhelm prompt space
- ❌ Uses plan_arguments as key - what if same description used twice?
- ❌ No pruning - old plan tasks accumulate in plan_tasks_map
- ❌ No priority ordering - tasks shown in creation order only

---

### Stage 4: SessionStart Resume (resume_incomplete_tasks)

**Benefits:**
- ✅ Automatic detection - no user action required
- ✅ Separates in_progress vs pending - clear categorization
- ✅ Provides options - continue/reassess/abandon
- ✅ Fires immediately - AI sees prompt before first action

**Limitations:**
- ❌ Only fires once per session - no periodic reminders during long sessions
- ❌ Shows ALL incomplete tasks - could be overwhelming if 50+ tasks
- ❌ No smart filtering - can't distinguish [PLANNING] from execution tasks
- ❌ No age filtering - shows tasks from months ago if never completed

---

### Stage 5: Stop Hook Block (handle_stop)

**Benefits:**
- ✅ BLOCKS (not warns) - enforces completion, can't ignore
- ✅ Clear instructions - tells exactly what to do
- ✅ Fast check - O(n) list iteration, no complex logic
- ✅ Prevents premature exits - primary goal achieved

**Limitations:**
- ❌ Too aggressive? - might prevent legitimate stops (e.g., "stop to fix bug")
- ❌ No escape hatch - what if task is stuck/blocked forever?
- ❌ No context awareness - blocks even if tasks are truly impossible
- ❌ Could frustrate users - "I want to stop but can't!"

---

### Stage 6: Task Status Command (/task-status)

**Benefits:**
- ✅ On-demand visibility - check anytime without interrupting AI
- ✅ Shows plan linkage - understand which tasks belong to which plans
- ✅ Compact summary - counts + list, not overwhelming
- ✅ Quick aliases - /ts for speed

**Limitations:**
- ❌ Requires manual invocation - AI might not think to check
- ❌ No filtering - can't show "only high priority" or "only [PLANNING]"
- ❌ Text-only output - no visualization of dependencies
- ❌ Session-scoped - can't see tasks from other sessions

---

### Stage 7: Audit Logging (_log_task_event)

**Benefits:**
- ✅ Complete timeline - when each event happened
- ✅ Append-only - never loses history (unless file deleted)
- ✅ Human-readable - no JSON parsing needed
- ✅ Separate file - doesn't clutter daemon.log

**Limitations:**
- ❌ Unbounded growth - log file grows forever (no rotation)
- ❌ No structured format - can't easily query/analyze
- ❌ No session linkage in log - hard to correlate with sessions
- ❌ File I/O on every event - could slow down if many tasks

---

## Overall Assessment

### What Works Well

**Architecture:**
- ✅ Leverages existing proven patterns (plan_export.py, EventContext magic state)
- ✅ Minimal new code - mostly wiring existing systems together
- ✅ Fail-open design - errors don't break Claude sessions
- ✅ Zero config - works out of box

**Behavior:**
- ✅ Achieves primary goal: prevents incomplete work
- ✅ Automatic - no user/AI action required
- ✅ Survives crashes - shelve persistence is solid
- ✅ Visibility - /task-status provides transparency

**Developer Experience:**
- ✅ Clear upgrade path from stash@{1} - 90% done
- ✅ Comprehensive tests - 10 end-to-end scenarios
- ✅ Well-documented - purpose and behavior clear

### What Could Be Better

**Scalability:**
- ⚠️ Unbounded list growth - task_created never pruned
- ⚠️ No pagination - /task-status shows ALL tasks
- ⚠️ O(n) checks - stop hook iterates full task_created list

**Flexibility:**
- ⚠️ Hard-coded block on stop - no override mechanism
- ⚠️ No task prioritization - all tasks equal importance
- ⚠️ No task categories - can't filter [PLANNING] vs execution
- ⚠️ Session-scoped only - can't aggregate across sessions

**Robustness:**
- ⚠️ Regex parsing - fragile if TaskCreate result format changes
- ⚠️ No validation - malformed tasks accepted silently
- ⚠️ No deduplication - same task could be created twice
- ⚠️ Log file unbounded - could fill disk over months

---

## Pre-Mortem: What Will Go Wrong

### Problem 1: "Task Explosion" - AI Creates 100+ Tasks

**Scenario:** AI creates [PLANNING] task for every sub-sub-step. Session has 150 tasks. SessionStart injects 3KB resume prompt. Stop hook blocks with 150-line task list.

**Impact:** Prompt space exhausted, AI confused by overwhelming list, performance degrades.

**Likelihood:** MEDIUM - planning commands encourage granular tasks.

**Solutions:**
```python
# Solution A: Cap injection size
MAX_RESUME_TASKS = 20
incomplete = incomplete[:MAX_RESUME_TASKS]
if len(all_incomplete) > MAX_RESUME_TASKS:
    injection += f"\n... and {len(all_incomplete) - MAX_RESUME_TASKS} more tasks"

# Solution B: Filter by prefix
planning_tasks = [t for t in incomplete if t["subject"].startswith("[PLANNING]")]
execution_tasks = [t for t in incomplete if not t["subject"].startswith("[PLANNING]")]
# Only inject execution tasks in SessionStart

# Solution C: Age-based filtering
import time
recent = [t for t in incomplete if time.time() - t["created_at"] < 86400]  # 24 hours
```

**Recommendation:** Implement Solution A (cap) + Solution C (age filter). Prevents overwhelming prompts while surfacing recent work.

---

### Problem 2: "Stuck Task" - Task Blocked Forever, Can't Stop

**Scenario:** Task #5 has `blockedBy: ["4"]` but Task #4 is impossible to complete (API changed, requirement invalid). AI cannot complete Task #5, Stop hook blocks forever. User frustrated: "I just want to stop!"

**Impact:** System prevents legitimate stops, users bypass by killing process.

**Likelihood:** MEDIUM - dependency bugs happen.

**Solutions:**
```python
# Solution A: Emergency escape hatch
if ctx.tool_name == "TaskUpdate" and ctx.tool_input.get("status") == "deleted":
    # Allow marking tasks deleted to unblock stop
    # (Already implemented in plan - verify it works)

# Solution B: Stop hook timeout
STOP_BLOCK_MAX_COUNT = 3
if ctx.session_blocked_stop_count >= STOP_BLOCK_MAX_COUNT:
    logger.warning("Stop blocked 3 times - allowing override")
    injection += "\n⚠️ OVERRIDE: Stopping anyway after 3 blocks. Use TaskUpdate to clean up later."
    return None  # Allow stop after 3 blocks

# Solution C: User command to force stop
# /force-stop command that sets ctx.force_stop_override = True
if ctx.force_stop_override:
    return None  # Allow stop
```

**Recommendation:** Implement Solution A (already in plan - ensure tested) + Solution B (stop block counter). Balances enforcement with escape valve.

---

### Problem 3: "Format Change" - TaskCreate Result Changes

**Scenario:** Claude Code updates TaskCreate to return `"Created task #1 successfully"` instead of `"Task #1 created successfully"`. Regex `r'Task #(\d+) created'` no longer matches. All task tracking silently breaks.

**Impact:** Tasks not tracked, no resume prompts, Stop hook never fires. Silent failure.

**Likelihood:** LOW - but catastrophic if happens.

**Solutions:**
```python
# Solution A: Multiple regex patterns (fallbacks)
patterns = [
    r'Task #(\d+) created successfully',
    r'Created task #(\d+) successfully',
    r'Task (\d+) created',
    r'#(\d+)',  # Last resort - any number
]
task_id = None
for pattern in patterns:
    match = re.search(pattern, result_text)
    if match:
        task_id = match.group(1)
        break

if not task_id:
    logger.error(f"Failed to extract task ID from: {result_text}")
    # Fallback: parse tool_result as JSON?

# Solution B: JSON parsing if possible
try:
    result_json = json.loads(ctx.tool_result)
    task_id = result_json.get("id")
except (json.JSONDecodeError, KeyError):
    # Fall back to regex

# Solution C: Monitoring alert
if task_id is None:
    _log_task_event("ERROR", "unknown", "Failed to extract task ID", "error")
    # This shows up in log - humans can detect breakage
```

**Recommendation:** Implement Solution A (multiple patterns) + Solution C (error logging). Robustness without complexity.

---

### Problem 4: "Unbounded Growth" - task_created List Grows to 10,000 Entries

**Scenario:** Long-running project with 100 sessions over 6 months. Each session creates 20 tasks. task_created list has 2,000 entries. SessionStart iterates all to find incomplete. Slow.

**Impact:** Performance degradation, large shelve files, slow hook processing.

**Likelihood:** HIGH - inevitable with no pruning.

**Solutions:**
```python
# Solution A: Completed task pruning (on SessionStart)
# Keep only incomplete tasks + last 100 completed
completed_ids = set(ctx.task_completed or [])
incomplete_tasks = [t for t in ctx.task_created if t["id"] not in completed_ids]
recently_completed = [t for t in ctx.task_created if t["id"] in completed_ids]
recently_completed = sorted(recently_completed, key=lambda t: t["updated_at"], reverse=True)[:100]

ctx.task_created = incomplete_tasks + recently_completed
# Prune completed list too
ctx.task_completed = ctx.task_completed[-100:]  # Keep last 100

# Solution B: Session-scoped archival
# Move completed tasks to separate ctx.task_archive list
# Don't iterate task_archive in normal operations

# Solution C: TTL-based expiry
# Remove tasks older than 30 days if completed
import time
TASK_TTL = 30 * 86400  # 30 days
now = time.time()
ctx.task_created = [
    t for t in ctx.task_created
    if t["id"] not in completed_ids or (now - t["updated_at"] < TASK_TTL)
]
```

**Recommendation:** Implement Solution A (pruning on SessionStart). Simple, effective, preserves recent history for debugging.

---

### Problem 5: "Race Condition" - Two Concurrent Sessions Update Same Task

**Scenario:** User has two Claude Code windows open (same session_id). Both AI instances create Task #1 simultaneously. Both append to task_created. Duplicate entries.

**Impact:** Task appears twice in /task-status, resume prompts duplicated, confusing.

**Likelihood:** LOW - most users use one window, but possible.

**Solutions:**
```python
# Solution A: Deduplication on read
def _get_unique_tasks(ctx):
    created = ctx.task_created or []
    seen = set()
    unique = []
    for t in created:
        if t["id"] not in seen:
            seen.add(t["id"])
            unique.append(t)
    return unique

# Use in all hooks
incomplete = [t for t in _get_unique_tasks(ctx) if t["id"] not in completed]

# Solution B: Deduplication on write
task_id = match.group(1)
existing = next((t for t in created if t["id"] == task_id), None)
if existing:
    logger.warning(f"Task #{task_id} already exists - skipping duplicate")
    return None  # Don't add duplicate

# Solution C: Use dict instead of list
# ctx.task_state = {task_id: TaskState} instead of ctx.task_created = [TaskState]
# Prevents duplicates by design (dict key uniqueness)
```

**Recommendation:** Implement Solution C (dict-based storage) in next iteration. Prevents duplicates structurally. For v1, use Solution B (check on write).

---

### Problem 6: "False Completion" - AI Marks Task Complete But Work Isn't Done

**Scenario:** AI creates Task #1 "Fix login bug". Runs one test, it passes. Marks completed. Actually, 3 other tests fail - bug not fixed. AI stops. User discovers incomplete work.

**Impact:** System thinks work is done, allows stop, incomplete work shipped.

**Likelihood:** MEDIUM-HIGH - AI might misunderstand completion criteria.

**Solutions:**
```python
# Solution A: Require explicit verification in TaskUpdate
# Add metadata field: verified_by
TaskUpdate(taskId="1", status="completed", metadata={"verified_by": "tests_passed"})
# Hook checks for verification before allowing completed status
if status == "completed" and not ctx.tool_input.get("metadata", {}).get("verified_by"):
    logger.warning(f"Task #{task_id} marked completed without verification")
    # Still allow, but log warning

# Solution B: Completion checklist in task description
# Task description must contain checkboxes
# [ ] Implementation complete
# [ ] Tests passing
# [ ] Code reviewed
# Hook parses description, warns if boxes unchecked

# Solution C: Require blocker removal
# If task has blockedBy, must clear it before completing
if status == "completed" and task_entry.get("blockedBy"):
    return ctx.block("Cannot complete task with blockers. Remove dependencies first.")
```

**Recommendation:** This is a fundamental AI reliability issue, not solvable by task system alone. Best approach: Document completion criteria clearly in task descriptions. Solution C (block if dependencies exist) helps catch obvious errors.

---

### Problem 7: "Log File Fills Disk" - task-tracking.log Grows to 10GB

**Scenario:** High-activity project, 1000 tasks/day over 1 year = 365,000 log entries. At ~100 bytes/entry = 36MB/year. Not terrible, but unbounded.

**Impact:** Disk space usage, slow log file I/O, large file hard to analyze.

**Likelihood:** LOW in 1 year, MEDIUM over 5 years.

**Solutions:**
```python
# Solution A: Log rotation (max size)
from logging.handlers import RotatingFileHandler
# But we're using raw file writes, not logging module...
# Convert _log_task_event to use logging.FileHandler with rotation

import logging
task_logger = logging.getLogger("clautorun.tasks")
handler = RotatingFileHandler("~/.clautorun/task-tracking.log", maxBytes=10*1024*1024, backupCount=5)
task_logger.addHandler(handler)

def _log_task_event(...):
    task_logger.info(f"[{event_type}] Task #{task_id}...")

# Solution B: Log rotation (by date)
# One log file per day: task-tracking-2026-02-08.log
# Prune logs older than 90 days

# Solution C: Conditional logging
# Only log CREATE/COMPLETE/ERROR, skip START/UPDATE
# Reduces volume by ~50%
```

**Recommendation:** Implement Solution A (rotating file handler). Standard practice, well-tested, configurable.

---

### Problem 8: "Corrupted Shelve" - Database File Corrupted, All State Lost

**Scenario:** Daemon crashes mid-write to shelve file. File corrupted. Next session: `shelve.error: db type could not be determined`. All task state lost.

**Impact:** Cannot resume tasks, no task history, /task-status shows nothing.

**Likelihood:** LOW - shelve is robust, but possible with power loss/kill -9.

**Solutions:**
```python
# Solution A: Backup on write (in session_manager.py)
import shutil
def _save_state():
    db_path = Path.home() / ".claude/sessions" / f"plugin_{session_id}.db"
    backup_path = db_path.with_suffix(".db.backup")
    if db_path.exists():
        shutil.copy(db_path, backup_path)  # Atomic backup
    # Then do actual save

# Solution B: Corruption recovery
try:
    state = shelve.open(db_path)
except shelve.error:
    logger.error(f"Shelve corrupted: {db_path}")
    backup = db_path.with_suffix(".db.backup")
    if backup.exists():
        shutil.copy(backup, db_path)
        state = shelve.open(db_path)  # Retry with backup
    else:
        state = {}  # Start fresh

# Solution C: Secondary storage (JSON dump)
# On every N writes, dump task_created to JSON file
if ctx.hook_call_count % 10 == 0:
    json_path = Path.home() / ".claude/sessions" / f"tasks_{session_id}.json"
    json_path.write_text(json.dumps(ctx.task_created))
```

**Recommendation:** Implement Solution B (backup recovery). Low overhead, protects against catastrophic data loss.

---

## Harsh Critique & Final Verdict

### What's Fundamentally Flawed

**1. Stop Hook is Too Draconian**

The Stop hook BLOCKS any stop if tasks are incomplete. This is philosophically correct but practically frustrating:
- **User:** "I want to stop and think about approach"
- **System:** "No! Finish your tasks first!"
- **User:** "But the approach is wrong, I need to stop!"
- **System:** "Mark them deleted then!"
- **User:** "I DON'T WANT TO DELETE THEM, I WANT TO PAUSE!"

**Reality:** Human cognition requires breaks. Forcing completion before stopping is tyrannical.

**Better Design:** Allow stop, inject "You have incomplete tasks - will resume next session" instead of blocking.

---

**2. No Concept of "Paused" Work**

Tasks are either pending/in_progress/completed/deleted. What about "parked for later" or "blocked by external dependency" or "waiting for human input"?

**Reality:** Not all incomplete work is actionable right now.

**Better Design:** Add `status="paused"` or `status="blocked"` states. Don't treat these as "incomplete" for stop hook purposes.

---

**3. Session-Scoped is Limiting**

Tasks are scoped to session_id. If user starts fresh session (new window), can't see old tasks.

**Reality:** Users think in terms of "my project" not "session 7a3b2c".

**Better Design:** Use GLOBAL_SESSION_ID pattern like plan_export.py. All tasks visible across sessions.

---

**4. No Human Override Mechanism**

System is 100% automatic. No way for human to say "ignore these tasks" or "don't block stop for this task".

**Reality:** Humans sometimes know better than automation.

**Better Design:** Add `/task-ignore <id>` command that marks task as "user-acknowledged incomplete, OK to stop".

---

**5. Unbounded Growth is Real**

task_created list grows forever. No pruning. Over months, this will cause issues.

**Reality:** All unbounded data structures eventually cause problems.

**Better Design:** Implement pruning on SessionStart (Solution A from Problem 4). Non-negotiable for production.

---

### What's Actually Good

**1. Leverages Existing Infrastructure** - Uses proven EventContext magic state, shelve persistence, hook patterns. Not inventing new storage layer.

**2. Fail-Open Design** - try/except in hooks prevents breaking sessions. Logging provides visibility into failures.

**3. Comprehensive Metadata** - Full TaskState schema enables debugging, analysis, future features.

**4. Automatic Enforcement** - Zero user action required. Makes correct usage natural.

**5. Clear Upgrade Path** - 90% implemented in stash@{1}. Mostly wiring, not new code.

---

### Final Verdict: **Conditionally Recommended**

**Ship v1 IF:**
- ✅ Implement Problem 1 Solution A (cap resume injection to 20 tasks)
- ✅ Implement Problem 2 Solution B (stop block counter - allow after 3 blocks)
- ✅ Implement Problem 4 Solution A (prune completed tasks on SessionStart)
- ✅ Implement Problem 5 Solution B (deduplication check on write)
- ✅ Implement Problem 7 Solution A (rotating log file)
- ✅ Implement Problem 8 Solution B (shelve backup recovery)
- ✅ Add tests for all Solutions above

**Don't Ship v1 Without:**
- ⚠️ Escape hatch for stuck tasks (stop block counter)
- ⚠️ Pruning logic (unbounded growth will cause issues)
- ⚠️ Multiple regex patterns (format change resilience)

**v2 Should Add:**
- 🔄 `status="paused"` state for parked work
- 🔄 GLOBAL_SESSION_ID aggregation (cross-session task view)
- 🔄 `/task-ignore <id>` command (human override)
- 🔄 Task prioritization (high/medium/low)
- 🔄 Dict-based storage (ctx.task_state = {id: TaskState})

**Overall Grade: B+**

Solid architecture, achieves primary goal (prevent incomplete work), leverages proven patterns. Main weaknesses: too aggressive stop blocking, no pruning, session-scoped only. Fixable with minor additions. Recommended for implementation with caveats above.

---

## Future Enhancements (Out of Scope for v1)

- **Global Task View:** Use GLOBAL_SESSION_ID to aggregate tasks across sessions
- **Task Priority:** Add priority field for task ordering
- **Task Duration Tracking:** Measure time in-progress
- **Task Dependencies Visualization:** Graph of blocked/blocks relationships
- **Task Templates:** Pre-defined task structures for common workflows
- **Task Export:** Export task history to JSON for analysis
- **Paused/Blocked Status:** Add states for non-actionable work
- **Human Override Commands:** /task-ignore, /task-pause, /force-stop
- **Smart Filtering:** Separate [PLANNING] from execution tasks
- **Cross-Session Aggregation:** View all tasks across all sessions
