# autorun safety guidance (Prime Agent)

Prime Agent 0.10 and newer do not expose an external lifecycle hook API to
autorun. The installed guidance and skills are available, but autorun cannot
intercept tool calls, enforce a Stop decision, or dispatch prompt commands in
this runtime. Treat the rules below as advisory.

## Available skills

Use Prime Agent's skill interface to load the installed autorun skills when a
task needs their procedures. In particular, load the commit skill before
staging or writing a commit message, and the philosophy skill before major
feature or architecture work.

## Safety guardrails

1. Prefer `trash` over `rm` for paths you did not just create.
2. Never run `git reset --hard`, `git push --force`, or `git clean -f` without
   explicit user authorization in the current turn.
3. Preserve uncommitted work you did not produce.
4. Do not claim that autorun blocked a tool or kept the session running on this
   runtime; no enforcement hook is installed.

## Completion

Before stopping, check the task requirements and verification evidence
explicitly. If work remains, name the remaining work rather than implying that
a Stop gate will resume the session.
