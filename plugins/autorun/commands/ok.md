---
description: Allow a blocked command pattern in this session, or for every session with --global
---

# Allow a blocked pattern

When this message's hook context holds a `<held-prompt>`, answer that
prompt as the user's message for this turn; this command needs no reply.

Allows patterns that a guard or an `/ar:no` rule would block. Default: 1 use
per pattern (auto-revokes after its first match).

**Usage**: `/ar:ok [--global] <pattern> ['<literal>' ...] [count] [duration] [permanent|perm|p]`

With a quoted or typed first pattern, each additional pattern must be a
quoted literal, and a bare count, duration or perm word (such as `'5m'`)
needs its own `/ar:ok` command to be used as a pattern. So does a literal that
contains an escaped quote (`"say \"hi\""`).

`--global` writes the allow to the global rules, which apply to every session
until they run out or `/ar:clear --global` removes them. Without it the allow
applies to this session only.

**Scope options** (applied to each pattern):
- No args: **1 use** (safe default, auto-revokes)
- Integer: use count (e.g. `3` = allow 3 times)
- Duration: time limit (e.g. `5m`, `1h`, `30s`, `2h30m`, `2d`, `1d12h`)
- Both: whichever expires first (e.g. `3 5m`)
- `permanent` / `perm` / `p`: unlimited for the rest of this session (with
  `--global`: in every session until cleared)

**Examples**:
- `/ar:ok rm` — allow rm once (default), then auto-revoke
- `/ar:ok rm 3` — allow rm 3 times
- `/ar:ok rm 5m` — allow rm for 5 minutes
- `/ar:ok rm 3 5m` — allow rm 3 times or 5 minutes (whichever first)
- `/ar:ok rm permanent` — allow rm for the rest of this session
- `/ar:ok rm p` — same (shortest form)
- `/ar:ok --global 'git push' 6h` — allow git push in every session for 6 hours
- `/ar:ok 'git push' 'gh pr create' 'gh pr edit' 72h` — allow all three for 72 hours

`/ar:status` lists the current allows.

This command takes effect only when the user types it. Sent by the model or
a subagent, it changes nothing and autorun answers that only the user can run it.
