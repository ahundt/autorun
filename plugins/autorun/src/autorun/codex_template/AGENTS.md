<!-- autorun:codex-agents-md:start -->
# autorun safety guidance (Codex)

Codex CLI runs autorun's hooks from `~/.codex/hooks.json`, so the
enforcement path (blocked commands, autofile policy, stop blocker) is
real and binding. This file is informational — it tells the model what
overrides exist so a stop or a block can always be resolved.

## Override commands (user types these)

- `/ar:sos` — emergency stop, cancels the current autorun.
- `/ar:task-ignore <id>` — mark a tracked task as ignored so a Stop
  block can clear.
- `/ar:ok <pattern> [N|5m|perm]` — allow a blocked command in this
  session; default is one use then auto-revoke.
- `/ar:globalok <pattern> [N|5m|perm]` — same allow, persisted globally.

## Safety guardrails

1. Prefer `trash` over `rm` for any path you did not just create.
2. Never run `git reset --hard`, `git push --force`, or `git clean -f`
   without an explicit user instruction in the current turn.
3. Never modify uncommitted work that you did not produce in this run.
4. When in doubt about a destructive command, propose it as a question
   instead of executing it.

## Stop semantics

If you have incomplete tracked tasks and the user asks you to stop,
surface the override path: only the user can type `/ar:sos` or
`/ar:task-ignore <id>` to unblock the Stop.
<!-- autorun:codex-agents-md:end -->
