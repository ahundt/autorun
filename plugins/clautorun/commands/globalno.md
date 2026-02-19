---
description: Block a command pattern globally (persists across sessions)
---

# Global Block Pattern

Add a command pattern to the global block list. Persists across all Claude Code sessions.

**Usage**: `/cr:globalno <pattern>`

**Pattern types**:
- Literal: `/cr:globalno rm` (blocks commands containing "rm")
- Regex: `/cr:globalno regex:eval\(` (blocks regex match)
- Glob: `/cr:globalno glob:*.tmp` (blocks glob match)

**Examples**:
- `/cr:globalno "docker rm"` — globally block docker rm
- `/cr:globalno "kubectl delete"` — globally block kubectl delete

See also: `/cr:globalstatus` to view global blocks, `/cr:globalclear` to remove all.

UserPromptSubmit hook processes this command and updates global state (shelve DB).
