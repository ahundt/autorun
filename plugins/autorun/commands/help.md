---
description: List every autorun command with what it does, in this harness's spelling
argument-hint: "[command]"
---

# autorun help

Lists the autorun commands this harness understands, grouped into the ones
autorun answers itself and the ones the model reads as a document, followed by
the skills this harness loads. Each command has one name; `/ar:task` and
`/ar:tasks` are the one pair of names for the same command.

`/ar:help <command>` shows one command's arguments.

The header line states how commands reach autorun here, because that differs
per harness: Claude Code autocompletes them, Codex keeps its slash menu closed
so the no-slash form is the one that arrives, and ForgeCode and OpenCode run
only the command files the installer copied.

Typing `ar` on its own also opens this list.

Shell reads are not blocked: `cat`, `head`, `tail`, `grep` and `find` outside
a pipe, and `echo >`, run without a guard. To block one again, use
`/ar:no <command>` for this session or `/ar:no --global <command>` for every
session.
