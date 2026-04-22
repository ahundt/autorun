---
description: Toggle task staleness reminders on/off or set threshold
argument-hint: [on|off|<number>|stale on|off|min <N>]
---

# Task Staleness Reminders (/ar:tasks)

$ARGUMENTS

Toggle task list staleness reminders or adjust the threshold.

When enabled (default), a reminder is injected after **25 tool calls** without
a `TaskCreate` or `TaskUpdate`, prompting the AI to keep its task list current.

**Usage:**
- `/ar:tasks` — show status (on/off, current count, threshold)
- `/ar:tasks on` — enable reminders
- `/ar:tasks off` — disable reminders
- `/ar:tasks 10` — set threshold to 10 tool calls

## Stale-Task Escape Hatch (/ar:tasks stale)

When autorun's task store diverges from Claude's Task DB (a "ghost task"), the
Stop hook can block forever even though Claude has no record of the task.

After **N identical consecutive Stop blocks** with no non-task tool call between
them (default N=2), the Stop injection is augmented with an escape hatch: the AI
is told to emit `AUTORUN_TASKS_CLEAR_STALE_TASK(<id>)` for each stale task id.
A PostToolUse hook detects the marker and marks the task `ignored` (non-blocking).

**Usage:**
- `/ar:tasks stale` — show status (enabled/disabled, consecutive threshold)
- `/ar:tasks stale on` — enable stale-task escape hatch (default-on)
- `/ar:tasks stale off` — disable (persists across sessions)
- `/ar:tasks stale min 3` — require 3 identical consecutive blocks (this session only)

Also accessible as `/ar:tasks ghost` (legacy alias).
