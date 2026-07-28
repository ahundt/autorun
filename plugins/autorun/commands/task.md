---
description: Inspect tasks or configure pause, prompts, recovery, and ignore behavior
argument-hint: [status|pause [N] [duration] [reason]|resume|ignore <id> [reason]|prompts ...|recovery ...]
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
/ar:task prompts 25
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
