---
description: Block a command pattern in this session, or for every session with --global
---

# Block a pattern

Adds a command pattern to the block list. A blocked command is denied with a
warning and a safe alternative.

**Usage**: `/ar:no [--global] <pattern>` or `/ar:no [--global] '<pattern>' [description]`

`--global` writes the block to the global rules, which apply to every session
until `/ar:clear --global` removes them. Without it the block applies to this
session only.

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
- `/ar:no --global "kubectl delete"` — block kubectl delete in every session

`/ar:status` lists the current blocks.

UserPromptSubmit hook processes this command and updates session or global state.
