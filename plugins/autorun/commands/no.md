---
description: Block a command pattern in this session
---

# Session Block Pattern

Add a command pattern to the session block list. Blocked commands show a warning and safe alternative.

**Usage**: `/ar:no <pattern>` or `/ar:no '<pattern>' [description]`

Unquoted words are all one pattern, the same pattern `/ar:ok` would use, and
`/ar:no` also removes the matching `/ar:ok` allow. A custom description needs a
quoted, `regex:` or `glob:` pattern. Regex backslashes are kept as typed; an
invalid or unsafe regex is refused.

**Pattern types**:
- Literal: `/ar:no rm` (blocks commands containing "rm")
- Regex: `/ar:no regex:eval\(` (blocks regex match)
- Glob: `/ar:no glob:*.tmp` (blocks glob match)

**Examples**:
- `/ar:no rm` — block all rm commands
- `/ar:no git reset` — block git reset (same as `/ar:no "git reset"`)
- `/ar:no "rm" use trash instead` — block rm with a custom message
- `/ar:no regex:eval\(` — block eval() calls

UserPromptSubmit hook processes this command and updates session state.
