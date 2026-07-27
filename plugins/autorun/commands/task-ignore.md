---
name: task-ignore
description: Compatibility alias for `/ar:task ignore <id> [reason]`
aliases: [ti, ignore-task]
argument-hint: <id> [reason]
---

# Ignore Task

Use `/ar:task ignore $ARGUMENTS`.

The canonical task command handler performs lookup, reason handling, state
locking, persistence, and platform-specific error rendering. This alias does
not implement a second task-state mutation path.
