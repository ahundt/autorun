---
description: Toggle task staleness reminders on/off or set threshold
argument-hint: [on|off|<number>]
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
