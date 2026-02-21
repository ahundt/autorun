---
description: Show current AutoFile policy status (short for /cr:status)
---

# AutoFile Status

Displays the current AutoFile policy for this session.

**Policy Levels**:
- **allow-all** - No restrictions on file creation (default)
- **justify-create** - Requires justification tag for new files
- **strict-search** - Only modify existing files, no new file creation

UserPromptSubmit hook will display the current policy.
