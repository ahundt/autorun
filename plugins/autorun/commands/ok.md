---
description: Allow a blocked pattern in this session (short for /ar:unblock)
---

# Session Allow Pattern

Allow a pattern in the current session. Default: 1 use (auto-revokes after first match).

**Usage**: `/ar:ok <pattern> [count] [duration] [permanent|perm|p]`

**Scope options** (trailing args after pattern):
- No args: **1 use** (safe default, auto-revokes)
- Integer: use count (e.g. `3` = allow 3 times)
- Duration: time limit (e.g. `5m`, `1h`, `30s`, `2h30m`)
- Both: whichever expires first (e.g. `3 5m`)
- `permanent` / `perm` / `p`: no limit (session-wide)

**Examples**:
- `/ar:ok rm` — allow rm once (default), then auto-revoke
- `/ar:ok rm 3` — allow rm 3 times
- `/ar:ok rm 5m` — allow rm for 5 minutes
- `/ar:ok rm 3 5m` — allow rm 3 times or 5 minutes (whichever first)
- `/ar:ok rm permanent` — allow rm permanently in this session
- `/ar:ok rm p` — same (shortest form)

UserPromptSubmit hook processes this command and updates session state.
