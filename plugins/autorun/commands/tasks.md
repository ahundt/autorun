---
description: Plural alias for task status, pause, prompts, recovery, and ignore
argument-hint: [status|pause [N] [duration] [reason]|resume|ignore <id> [reason]|prompts ...|recovery ...]
---

# Task Control (`/ar:tasks`)

$ARGUMENTS

This is the plural alias of `/ar:task`; both route through the same parser,
operation registry, validation, state transitions, and platform-specific
command renderer. See `/ar:task` for the complete command surface.
