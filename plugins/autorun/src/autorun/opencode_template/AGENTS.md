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
task pause, task ignore) take effect only from a non-synthetic prompt in a
root session. The model-callable `autorun` tool, child sessions, compaction
continuations and plugin-injected synthetic parts are refused. OpenCode cannot
distinguish a typed root prompt from the same prompt sent by another plugin or
API client; that residual exposure is part of the harness contract.

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

OpenCode's `session.idle` event reaches autorun's Stop gate. When the gate
blocks, the plugin submits one synthetic continuation through
`session.promptAsync`, or through `session.prompt` on clients without the async
method. A client with neither method can only show a warning when its toast API
exists; upgrade OpenCode, and continue manually until then. The emergency stop
also refuses further tool calls, so honor it immediately.
