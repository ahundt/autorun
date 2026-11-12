---
name: ar
description: "autorun control commands, stated as prose after $ar: status; file allow/justify/existing-only new-file policy; ok/no/clear command guards, each with --global; go/goprocess/stop/estop; task tracking and pause; cache-miss gate; planexport settings; reload; help lists everything."
---

# autorun commands (ar)

<purpose>

autorun's daemon dispatches these from plain prompt text; no slash menu or
catalog entry is required for them to work. Type `ar:<command> [args]`
(`ar <command>` and `/ar:<command>` are also accepted). Type `ar:help` for
the live list in this harness's spelling, or `ar:help <command>` for one
command.

</purpose>

<commands>

## New-file policy (AutoFile)

- `ar:status` — show the file policy, this session's and the global command
  rules, and whether task enforcement is paused.
- `ar:file allow` — allow all file creation.
- `ar:file justify` — require a justification for each new file.
- `ar:file existing-only` — modify existing files only (strictest).

## Command guards

- `ar:no <pattern>` — block a command pattern in this session.
- `ar:ok <pattern> [N|5m|perm]` — allow a blocked pattern: N uses, a
  duration such as `5m`, `2h30m`, or `2d`, or `perm` for the rest of the session.
  Default is one use, then auto-revoke.
- `ar:clear` — remove this session's blocks and allows.
- `--global` as the first argument applies to every session instead:
  `ar:no --global <pattern>`, `ar:ok --global <pattern> [N|5m|perm]`,
  `ar:clear --global`. `ar:status` lists both.

## Run control

- `ar:go <task>` — start autonomous execution with three-stage
  verification.
- `ar:goprocess <task>` — the same with the plan, critique and wait process
  at every step.
- `ar:stop` — graceful stop after the current task.
- `ar:estop` — emergency stop, cancels the current autorun.

## Task tracking

- `ar:task` / `ar:tasks` — task, pause, prompting, and recovery status.
- `ar:task pause [N] [duration] [reason]` — bare pause defaults to five
  minutes; a reason-only pause holds until AI recovery; an explicit count
  and duration may be combined.
- `ar:task resume` — resume task enforcement.
- `ar:task ignore <id> [reason]` — mark one task ignored.
- `ar:task prompts on|off|<N>` — configure task-staleness prompting.
- `ar:task recovery on|off|min <N>` — configure repeated-Stop stale-task
  recovery.

## Cache-miss / compaction gate (off by default)

- `ar:cache` — show status. `ar:cache on|off [5m|1h|perm]` — toggle,
  optionally for a window.
- `ar:cache set ratio|read|age|full <value>` — configure threshold axes.
  Tokens `50k` or `.5M`, percents `85%`, durations `5m`, `2h30m`, or `2d`.
- `ar:cache ok [N|5m|perm]` — override the gate; `ar:cache no` — cancel
  outstanding overrides.

## Plan export

- `ar:planexport [on|off|globalon|globaloff]` — status or toggles; a
  per-project pin beats the global default.
- `ar:planexport dir <path>` — set the export directory.
- `ar:planexport pattern <template>` — set the filename pattern.
- `ar:planexport rejected [on|off|dir <path>]` — rejected-plan export.
- `ar:planexport reset` — restore defaults and clear project pins.

## Admin

- `ar:reload` — reload the guard rules from autorun's defaults and your
  integration files.
- `ar:help` — list every command with what it does.

</commands>
