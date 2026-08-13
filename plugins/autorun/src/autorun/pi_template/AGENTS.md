# autorun safety guidance (Pi)

autorun's Pi extension sends tool calls to the autorun daemon before execution.
A denied tool returns the daemon's reason to the agent and does not run. The
extension also routes `/ar <command>` and compatible `ar:`/`ar-` spellings to
the same command dispatcher.

## Common commands

- `/ar go <task>` — start autonomous execution with three-stage verification.
- `/ar st` — show the current AutoFile policy.
- `/ar allow`, `/ar justify`, `/ar find` — select file-creation policy.
- `/ar task` — show or control task lifecycle enforcement.
- `/ar sos` — emergency stop.
- `/ar help` — list the complete command surface.

## Safety guardrails

1. Prefer `trash` over `rm` for paths you did not just create.
2. Never run `git reset --hard`, `git push --force`, or `git clean -f` without
   explicit user authorization in the current turn.
3. Preserve uncommitted work you did not produce.
4. Follow the commit skill before staging or committing.

## Lifecycle

Pi's `agent_settled` event is autorun's Stop boundary. If required work remains,
autorun sends the concrete continuation reason as the next user message. Do not
claim completion until that gate allows the session to settle.
