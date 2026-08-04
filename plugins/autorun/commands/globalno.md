---
description: Block a command pattern globally (persists across sessions)
# Manual-only: reachable by typing the command, not by model choice.
# Writes a block that persists across every future session, so it must be a human decision.
disable-model-invocation: true
---

# Global Block Pattern

Add a command pattern to the global block list. Persists across all Claude Code sessions.

**Usage**: `/ar:globalno <pattern>`

**Pattern types**:
- Literal: `/ar:globalno rm` (blocks commands containing "rm")
- Regex: `/ar:globalno regex:eval\(` (blocks regex match)
- Glob: `/ar:globalno glob:*.tmp` (blocks glob match)

**Examples**:
- `/ar:globalno "docker rm"` — globally block docker rm
- `/ar:globalno "kubectl delete"` — globally block kubectl delete

See also: `/ar:globalstatus` to view global blocks, `/ar:globalclear` to remove all.

UserPromptSubmit hook processes this command and updates global state (daemon_state.json).
