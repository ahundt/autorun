---
description: Inspect tasks or configure pause, prompts, recovery, and ignore behavior
argument-hint: "[status|pause [N] [duration] [reason]|resume|ignore <id> [reason]|prompts ...|recovery ...|on|off|N]"
---

# Task Control

`/ar:task` and `/ar:tasks` are equivalent command roots. Autorun parses the
subcommand from the exact user-authored prompt; this file documents the shared
surface without implementing a second parser.

```text
/ar:task
/ar:task pause
/ar:task pause Discuss the release decision
/ar:task pause 5m Discuss the release decision
/ar:task pause 3 10m Compare architecture options
/ar:task pause perm Keep discussing until explicitly resumed
/ar:task resume
/ar:task ignore 7 No longer required
/ar:task prompts on
/ar:task off
/ar:task prompts 25
/ar:task prompts initial 25
/ar:task prompts subsequent 50
/ar:task prompts scope all
/ar:task recovery min 3
```

`pause` suppresses task reminders and task-based Stop continuation only. It
does not disable command safety, change task status, or advance autorun stages.
Bare `pause` uses the configured default duration (five minutes by default).
A reason with no count or duration creates an indefinite discussion pause
until the AI returns the generation-bound recovery marker or the user resumes.
An explicit count means logical Stop attempts, a duration uses `s`, `m`, or
`h`, and count plus duration expires at whichever limit is reached first. An
explicit scope remains authoritative when followed by a multiword reason.

Only a user command can activate or extend a pause. Autorun supplies the AI a
generation-bound recovery marker; the exact marker on its own non-code line
may end that pause. While discussion remains paused, autorun repeats the
reason and recovery guidance at the configured task-progress cadence when no
task update occurs. `/ar:task resume`, `/ar:go`, and `/ar:proc` also resume
enforcement.

Task-staleness prompting uses two phases. A fresh primary agent or subagent gets
one initial checkpoint at the configured initial interval. The first checkpoint
or any genuine task/plan update enters the subsequent phase, resets the counter,
and uses the configured subsequent interval thereafter. Primary and subagent
counters are independent. `scope all|user|subagent` controls which kinds are
eligible; `all` is the default. A bare positive number sets both intervals to
the same value for backward-compatible fixed cadence.

Legacy `/ar:task on`, `/ar:task off`, and `/ar:task N` are aliases for
`prompts on`, `prompts off`, and `prompts N`. They change reminder prompting
only; task-based Stop enforcement remains active. Use `pause` when discussion
should temporarily suspend both.
