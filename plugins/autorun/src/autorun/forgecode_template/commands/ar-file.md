---
name: ar-file
description: Set the new-file policy for this session - allow, justify or existing-only
---

Set the AutoFile new-file policy named below for the remainder of this
session, acknowledge it in one line, and follow it:

- `allow` (allow-all): new files may be created freely.
- `justify` (justify-create): before creating a new file, search existing
  files; create one only when you state why no existing file can hold the
  change.
- `existing-only` (strict-search): do not create any new file; modify or
  delete existing files only. If the task needs a new file, tell the user
  before proceeding.

Editing existing files is always allowed. Continue to follow the safety
guardrails in AGENTS.md for destructive commands.

{{parameters}}
