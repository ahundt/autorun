---
description: Block a command pattern in this session (short for /ar:block)
---

# Session Block Pattern

Add a command pattern to the session block list. Blocked commands show a warning and safe alternative.

**Usage**: `/ar:no <pattern>`

**Pattern types**:
- Literal: `/ar:no rm` (blocks commands containing "rm")
- Regex: `/ar:no regex:eval\(` (blocks regex match)
- Glob: `/ar:no glob:*.tmp` (blocks glob match)

**Examples**:
- `/ar:no rm` — block all rm commands
- `/ar:no "git reset"` — block git reset
- `/ar:no regex:eval\(` — block eval() calls

UserPromptSubmit hook processes this command and updates session state.
