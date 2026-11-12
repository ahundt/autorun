---
description: Set this session's new-file policy - allow, justify or existing-only
argument-hint: "allow|justify|existing-only"
---

# New-file policy

Sets what the agent may do when it wants to create a file in this session.
Editing existing files is always allowed.

**Usage**: `/ar:file allow|justify|existing-only`

- `allow` (allow-all): new files may be created freely. Every session starts here.
- `justify` (justify-create): a new file is allowed only when the agent includes
  `<AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION>` explaining why no
  existing file can hold the change.
- `existing-only` (strict-search): no new files; only existing files may be
  modified. `search` and `find` are accepted for the same policy.

`/ar:status` shows the current policy. From a shell:
`autorun file allow|justify|existing-only --session SESSION_ID`.

UserPromptSubmit hook has updated the session policy.
