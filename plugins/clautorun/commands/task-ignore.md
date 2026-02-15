---
name: task-ignore
description: Mark task as ignored (user override to unblock stop)
aliases: [ti, ignore-task]
---

# Ignore Task

Mark a task as ignored - allows AI to stop without completing it.

**Syntax:**
```
/task-ignore <task_id> [reason]
```

**Arguments:**
- `task_id`: Task ID to ignore (required)
- `reason`: Optional reason for ignoring (stored in metadata)

**Examples:**
```
/task-ignore 5
/task-ignore 3 "Blocked by external API issue"
/task-ignore 7 "No longer relevant after design change"
```

!`uv run --project /Users/athundt/.claude/clautorun/plugins/clautorun python -c "
import sys
import os

from clautorun.task_lifecycle import TaskLifecycle, is_enabled

# Check if enabled
if not is_enabled():
    print('Error: Task lifecycle tracking is disabled')
    print('Run: uv run --project /Users/athundt/.claude/clautorun/plugins/clautorun python /Users/athundt/.claude/clautorun/plugins/clautorun/scripts/task_lifecycle_cli.py --enable')
    sys.exit(1)

# Parse arguments
args = '${ARGS}'.strip().split(None, 1)
if not args:
    print('Error: Task ID required')
    print('Usage: /task-ignore <task_id> [reason]')
    sys.exit(1)

task_id = args[0]
reason = args[1] if len(args) > 1 else 'User ignored'

# Get current session
session_id = os.environ.get('CLAUDE_SESSION_ID')
if not session_id:
    print('Error: CLAUDE_SESSION_ID not set')
    sys.exit(1)

try:
    manager = TaskLifecycle(session_id=session_id)

    # Check if task exists
    tasks = manager.tasks
    if task_id not in tasks:
        print(f'Error: Task #{task_id} not found')
        print(f'Available tasks: {list(tasks.keys())}')
        sys.exit(1)

    task = tasks[task_id]
    old_status = task['status']

    # Ignore task
    success = manager.ignore_task(task_id, reason)

    if success:
        print(f'✅ Task #{task_id} marked as ignored')
        print(f'   Subject: {task[\"subject\"]}')
        print(f'   Previous status: {old_status}')
        print(f'   Reason: {reason}')
        print()
        print('⚠️  This task will no longer block the AI from stopping.')
        print('   Use /task-status to see remaining incomplete tasks.')
    else:
        print(f'Error: Failed to ignore task #{task_id}')
        sys.exit(1)

except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)
"`

**When to use:**
- Task is stuck or blocked indefinitely
- Task is no longer relevant
- You want to remove this task from blocking AI stop

**Alternatives:**
- Use `paused` status for temporary parking:
  ```
  TaskUpdate(taskId="5", status="paused")
  ```
- Use `deleted` status if task should be removed:
  ```
  TaskUpdate(taskId="5", status="deleted")
  ```

**Difference from paused:**
- `paused`: Task is temporarily parked (doesn't block stop)
- `ignored`: User explicitly overrode to unblock (logged in audit trail)
- Both prevent stop hook from blocking
