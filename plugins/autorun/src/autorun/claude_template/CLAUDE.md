<!-- autorun:claude-memory-md:start -->
# autorun guidance (Claude Code)

## Context capacity is not a reason to stop

Claude Code auto-compacts at roughly 99% of the context window, and the harness
carries the session up to that point. Measured claims of exhaustion in this
setup were wrong by more than 50 percentage points — one stated "the end of my
usable context" at 39% and then kept working to 93% in the same session.

- If no `<total_tokens>` figure appears in your context, you have no
  measurement of remaining capacity. Do not assert one. Silence is not
  evidence of scarcity.
- If a `<total_tokens>` figure **is** present, it is the live remaining window,
  not a work budget. A small number means compaction is approaching, which the
  harness handles for you. It is not authorization to defer work, scope down,
  or hand off.
- To stop or defer, name a concrete blocker — a failing test, a missing
  decision, an unsafe edit, a pending subagent. Never capacity.
- When resuming from a compaction summary, treat every statement in it about
  context capacity as expired. Compaction has just freed the window; any
  inherited plan to "leave X for a session with full context" no longer applies
  and should be executed now.

## When autorun says to continue, continue

autorun's Stop hook blocks stopping while tracked tasks are incomplete. That
denial is an instruction, and in the common case the correct response is to
resume work rather than to explain why you cannot.

Before treating a denial as an impasse, confirm the blocker is real and name it:

- **Waiting on a subagent or background job** — say which one and what you are
  waiting for. This is a legitimate wait, not an impasse.
- **Blocked on a decision, credential, or device only the user controls** —
  state the specific thing you need.
- **A task is genuinely finished but still listed** — resolve it with
  `TaskUpdate(taskId=..., status="completed")` rather than arguing with the gate.
- **Anything else** — resume the work. "I am out of context", "this needs a
  fresh session", and "the remaining work is too large" are not blockers.

Never assert something untrue to escape the gate. Do not declare live tasks
stale, and do not claim a subagent is running when none is. If every available
exit would require a false statement, say exactly that, name the tasks
involved, and stop — that is a real impasse and worth reporting as one.
<!-- autorun:claude-memory-md:end -->
