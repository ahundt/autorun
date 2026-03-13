---
description: Allow a blocked pattern globally (persists across sessions)
---

# Global Allow Pattern

Allow a pattern globally (persists across sessions). Default: 1 use (auto-revokes after first match).

**Usage**: `/ar:globalok <pattern> [count] [duration] [permanent|perm|p]`

**Scope options** (trailing args after pattern):
- No args: **1 use** (safe default, auto-revokes)
- Integer: use count (e.g. `3` = allow 3 times)
- Duration: time limit (e.g. `5m`, `1h`, `30s`, `2h30m`)
- Both: whichever expires first (e.g. `3 5m`)
- `permanent` / `perm` / `p`: no limit (global, all sessions)

**Examples**:
- `/ar:globalok rm` — globally allow rm once, then auto-revoke
- `/ar:globalok rm 3` — globally allow rm 3 times
- `/ar:globalok rm 5m` — globally allow rm for 5 minutes
- `/ar:globalok rm perm` — globally allow rm permanently

See also: `/ar:globalstatus` to view current global state.

UserPromptSubmit hook processes this command and updates global state.
