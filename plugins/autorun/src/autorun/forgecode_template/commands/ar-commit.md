---
name: ar-commit
description: Refresh git commit guidelines before staging changes
---

Before authoring a git commit, follow these rules:

1. Run `git status` and `git diff --staged` first; describe what you
   actually see, not what you think you wrote.
2. Subject line ≤ 70 chars, imperative mood, concrete verbs
   (fix, add, remove, refactor) — no internal jargon like "Bug 1" or
   "the issue".
3. Body explains the *why* and the user-visible change, not the *how*.
   Reference exact file paths and function names where useful.
4. Never include transient internal session details (e.g. AI tool
   sequences, agent IDs, transcript timestamps).
5. Never amend a published commit; always create a new commit.
6. Never push, force-push, or run destructive git operations without
   an explicit user instruction in the current turn.
