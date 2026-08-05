---
description: List every autorun command with what it does, in this harness's spelling
argument-hint: "[command]"
---

# autorun help

Lists the autorun commands this harness understands, grouped into the ones
autorun answers itself and the ones the model reads as a document. Each entry
shows the recommended spelling first and any shorter spelling after it.

`/ar:help <command>` shows one command's arguments and alternative spellings.

The header line states how commands reach autorun here, because that differs
per harness: Claude Code autocompletes them, Codex keeps its slash menu closed
so the no-slash form is the one that arrives, and ForgeCode and OpenCode run
only the command files the installer copied.

Typing `ar` on its own also opens this list.
