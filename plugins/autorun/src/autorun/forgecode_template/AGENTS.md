# autorun safety guidance (ForgeCode)

ForgeCode does not expose external hook events, so autorun cannot
intercept tool calls the way it does in Claude Code or Gemini CLI.
The guidance below is therefore advisory — you (the AI agent) are
expected to read and follow it before taking destructive or
irreversible actions.

## Use these autorun commands when relevant

- `/ar-go <task>` — start an autonomous run with the three-stage
  verification cycle (initial → critical review → final verification).
- `/ar-st` — show the current AutoFile policy (allow / justify / find).
- `/ar-allow` — allow all file creation.
- `/ar-find` — restrict edits to existing files only.
- `/ar-commit` — refresh git commit guidelines before staging.
- `/ar-ph` — refresh the universal system design philosophy.

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

If the user wants you to stop and you have incomplete tasks, surface
the override path explicitly: only the user can type `/ar:sos` or
`/ar:task-ignore <id>` to unblock the stop.
