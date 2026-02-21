---
description: Block a command pattern in this session (short for /cr:block)
---

# Session Block Pattern

Add a command pattern to the session block list. Blocked commands show a warning and safe alternative.

**Usage**: `/cr:no <pattern>`

**Pattern types**:
- Literal: `/cr:no rm` (blocks commands containing "rm")
- Regex: `/cr:no regex:eval\(` (blocks regex match)
- Glob: `/cr:no glob:*.tmp` (blocks glob match)

**Examples**:
- `/cr:no rm` — block all rm commands
- `/cr:no "git reset"` — block git reset
- `/cr:no regex:eval\(` — block eval() calls

UserPromptSubmit hook processes this command and updates session state.
