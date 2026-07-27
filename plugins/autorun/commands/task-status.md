---
name: task-status
description: Compatibility alias for `/ar:task status`
aliases: [ts, task-state]
---

# Task Lifecycle Status

Use `/ar:task status`.

The canonical handler reads pause, prompt, recovery, and tracked-task state
through the same session backend used by hooks. This alias does not open a
separate task manager or derive a session ID from a harness-specific
environment variable.
