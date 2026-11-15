<!-- autorun:antigravity-agents-md:start -->
# autorun safety guidance (Antigravity)

Antigravity runs autorun's hooks from the `ar` plugin's `hooks.json`, so
blocked commands and the file policy are enforced, not advisory. This file
tells you what overrides exist so a block can always be resolved.

## Override commands (user types these)

- `/ar:ok <pattern> [N|5m|perm]` — allow a blocked command in this session;
  default is one use, then it locks again.
- `/ar:ok --global <pattern> [N|5m|perm]` — same allow, for every session.
- `/ar:status` — show the file policy and the current allows.
- `/ar:help` — list every autorun command.

Only the user can type `/ar:ok`. If you type it yourself it changes nothing.

## Safety guardrails

1. Prefer `trash` over `rm` for any path you did not just create.
2. Never run `git reset --hard`, `git push --force`, or `git clean -f`
   without an explicit user instruction in the current turn.
3. Never modify uncommitted work that you did not produce in this run.
4. When in doubt about a destructive command, propose it as a question
   instead of executing it.
<!-- autorun:antigravity-agents-md:end -->
