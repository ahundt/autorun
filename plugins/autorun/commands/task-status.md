---
name: task-status
description: Show task lifecycle tracking status and incomplete tasks
aliases: [ts, task-state, tasks]
---

# Task Lifecycle Status

!`uv run --project ${CLAUDE_PLUGIN_ROOT} python -c "
import sys
import os

from autorun.task_lifecycle import TaskLifecycle, is_enabled

# Check if enabled
if not is_enabled():
    print('Task lifecycle tracking is DISABLED.')
    print('Run: uv run --project ${CLAUDE_PLUGIN_ROOT} python ${CLAUDE_PLUGIN_ROOT}/scripts/task_lifecycle_cli.py --enable')
    sys.exit(0)

# Get current session ID from environment
session_id = os.environ.get('CLAUDE_SESSION_ID', 'unknown')

try:
    # Create manager instance
    manager = TaskLifecycle(session_id=session_id)
    tasks = manager.tasks

    if not tasks:
        print('No tasks tracked in this session.')
        print(f'Storage: ~/.autorun/task-tracking/{session_id}/')
        sys.exit(0)

    # Count by status
    by_status = {}
    for t in tasks.values():
        status = t['status']
        by_status[status] = by_status.get(status, 0) + 1

    print('## Task Summary')
    print(f'Total tasks: {len(tasks)}')
    for status in ['completed', 'in_progress', 'pending', 'paused', 'deleted', 'ignored']:
        count = by_status.get(status, 0)
        if count > 0:
            icon = {'completed': '✅', 'in_progress': '🔄', 'pending': '⏸️', 'paused': '⏯️', 'deleted': '🗑️', 'ignored': '🚫'}.get(status, '❓')
            print(f'  {icon} {status.replace(\"_\", \" \").title()}: {count}')

    # Show incomplete tasks by status
    incomplete = manager.get_incomplete_tasks(exclude_blocking=True)
    if incomplete:
        print('\\n## Incomplete Tasks (Prioritized)')

        prioritized = manager.get_prioritized_tasks()
        for i, task in enumerate(prioritized, 1):
            status = task['status']
            icon = {'in_progress': '🔄', 'pending': '⏸️'}.get(status, '❓')
            blockers = task.get('blockedBy', [])
            blocker_str = f' (⚠️ blocked by {blockers})' if blockers else ' (✅ ready)'
            print(f'  {i}. Task #{task[\"id\"]}: {task[\"subject\"]} ({icon} {status}){blocker_str}')
    else:
        print('\\n✅ All tasks completed!')

    # Show plan linkage if any
    plan_map = manager.plan_tasks_map
    if plan_map:
        print('\\n## Tasks by Plan')
        for plan_key, task_ids in plan_map.items():
            print(f'\\n**{plan_key}:**')
            for tid in task_ids:
                task = tasks.get(tid)
                if task:
                    icon = {'completed': '✅', 'deleted': '🗑️', 'in_progress': '🔄', 'pending': '⏸️'}.get(task['status'], '❓')
                    print(f'  {icon} Task #{tid}: {task[\"subject\"]} ({task[\"status\"]})')

    # Show storage info
    print('\\n## CLI Commands')
    print(f'uv run --project \\${{CLAUDE_PLUGIN_ROOT}} python \\${{CLAUDE_PLUGIN_ROOT}}/scripts/task_lifecycle_cli.py --status {session_id}  # View from command line')
    print(f'uv run --project \\${{CLAUDE_PLUGIN_ROOT}} python \\${{CLAUDE_PLUGIN_ROOT}}/scripts/task_lifecycle_cli.py --export {session_id} ./tasks.json  # Export to JSON')
    print(f'uv run --project \\${{CLAUDE_PLUGIN_ROOT}} python \\${{CLAUDE_PLUGIN_ROOT}}/scripts/task_lifecycle_cli.py --clear {session_id}  # Clear this session\\'s tasks')

except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)
"`

**Usage:**
- `/task-status` or `/ts` - View current task state (in-session)
- Use CLI commands above for external access
