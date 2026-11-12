# autorun safety guidance (OpenCode)

autorun's OpenCode plugin intercepts every tool call in-process and asks the
autorun daemon before it runs: a blocked command fails with the daemon's
reason instead of executing — the same enforcement Claude Code and Codex get
from hooks. The guidance below still matters: follow it before destructive
or irreversible actions rather than waiting to be blocked.

## Use these autorun commands when relevant

- `/ar-go <task>` — start an autonomous run with the three-stage
  verification cycle (initial → critical review → final verification).
- `/ar-status` — show the file policy and the session and global command rules.
- `/ar-file allow|justify|existing-only` — set the new-file policy for this session.
- `/ar-commit` — refresh git commit guidelines before staging.
- `/ar-philosophy` — refresh the universal system design philosophy.

Other autorun commands reach the daemon through the registered `autorun`
tool — invoke it with the command's name and arguments (status, task,
planexport, help) whenever the user asks about autorun state. `help` lists
everything. Commands that change permissions (ok, no, clear, file, cache,
task pause, task ignore) take effect only when the user types them, and
OpenCode hands autorun no typed prompt, so through this tool they answer
that only the user can run them: tell the user, do not retry.

## Safety guardrails

1. Prefer `trash` over `rm` for any path you did not just create.
2. Never run `git reset --hard`, `git push --force`, or `git clean -f`
   without an explicit user instruction in the current turn.
3. Never modify uncommitted work that you did not produce in this run.
4. When in doubt about a destructive command, propose it as a question
   instead of executing it.
5. Commits use the structure documented in `/ar-commit` — concrete
   summary, no internal jargon, no transient session details.

## Stop semantics

OpenCode delivers no stop event to autorun, so autorun holds nothing open
when you finish a turn — never say autorun blocked or cleared a stop. The
emergency stop still has teeth here: after an emergency stop the plugin
refuses further tool calls, so honor it immediately. When you stop with
work outstanding, name what is left and let the user decide.
