# Task tracking details

The [README](../README.md#task-tracking) covers the commands and the four things that block a Stop. This page holds the rules behind them: reminder timing, each waiting state, the loop guard, forks, and settings.

1. [Staleness reminders](#staleness-reminders)
2. [What blocks a Stop](#what-blocks-a-stop)
3. [Stale tasks](#stale-tasks)
4. [Loop guard](#loop-guard)
5. [Forked conversations](#forked-conversations)
6. [Settings and state](#settings-and-state)

## Staleness reminders

If the agent stops updating its tasks, autorun reminds it after 25 tool calls in a new session and every 50 calls after that.

1. Any real task or plan update (`TaskCreate`, `TaskUpdate`, `TodoWrite`, Codex `update_plan`, or the agent's equivalent) restarts the count.
2. Main agents and subagents count separately.
3. The defaults are the `task_staleness_initial_threshold`, `task_staleness_subsequent_threshold` and `task_staleness_agent_scope` config keys.
4. Resume and compaction keep the current phase; a fresh start or clear begins again at 25.

## What blocks a Stop

autorun decides from recorded task state. A `pending`, `in_progress` or `delegation-returned` task blocks Stop unless it waits on someone else. The Stop message names the tasks that block, then lists each waiting task on its own line.

### Waiting on you

Line shown: `⏳ Waiting on you: #7 Push release-1.0: approve pushing to origin/main`

1. The agent printed `AUTORUN_TASK_AWAITING_USER(7): <the decision it needs>` on its own line.
2. The task becomes `awaiting-user`, keeps the decision, and stops blocking.
3. The next prompt you type puts it back to the status it had, and so does the agent's own update of it.
4. A prompt from a subagent, a harness notification or automation changes nothing.
5. A marker with no decision text is refused and the block says so.

### Waiting on a subagent

Line shown: `🤝 Waiting on subagent a1b2c3d4: #3 Sweep the fixtures`

1. The agent printed `AUTORUN_TASK_DELEGATED(3)`. autorun records the subagent and the harness process that ran it.
2. The task blocks again when the subagent's SubagentStop arrives (`delegation-returned`, so the agent checks the result).
3. It goes back to `pending` if that harness process has exited by the next Stop or session start. Nothing expires on a timer.
4. A task delegated by marker with no recorded process returns at its first Stop as `delegation-returned` for the agent to verify.

### Waiting on another task

Line shown: `⛓️ #2 waits on #3`

#2 is blocked by an open task. Its blockers block Stop themselves if the agent can act on them. A blocker id that does not exist, or a `blockedBy` cycle, makes the task block with a note saying what to fix.

### Inherited from a fork

Line shown: `↪️ #1 ... is inherited from the conversation this one was forked from`

A Pi or Prime Agent `/fork` carries the parent's tasks over. The fork lists them and blocks on one only after it updates it.

When nothing blocks, autorun's task check steps aside and the waiting lines are shown to you. During `/ar:go` the stage check still answers that Stop.

## Stale tasks

A harness can lose a task that autorun still tracks. Every block tells the agent that, for a task its harness no longer lists, it may print `AUTORUN_TASKS_CLEAR_STALE_TASK(<id>)` on its own line. autorun marks the task ignored, logs `GHOST_CLEAR`, and shows you which. This works from the first block.

For a real task that needs discussion, use `/ar:tasks pause <reason>`.

## Loop guard

The loop guard is the one count that can pass a Stop. After `stop_block_max_count` consecutive main-agent Stops are blocked with no main-agent tool call between them, the next Stop passes through with a notice naming `/ar:task status`. Later Stop handlers, including stage verification, still run.

1. The guard means the block reason is not reaching the agent, not that work is finished, and it changes no task.
2. A subagent's tool calls do not count as the main agent acting.
3. A main-agent tool call, a user-authored prompt, or a session start restarts the sequence and restores enforcement.

## Forked conversations

A conversation the harness reports as a fork (Claude Code `--fork-session`, Pi and Prime Agent `/fork`) is told so once, and may work without creating tasks. A goal Claude Code set in the parent conversation continues in the fork until you run `/goal clear` there.

## Settings and state

Settings live in `~/.autorun/task-lifecycle.config.json`:

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | task tracking on |
| `max_resume_tasks` | `20` | tasks listed in a resume or Stop message |
| `stop_block_max_count` | `3` | the loop guard's count: blocked consecutive main-agent Stops with no main-agent tool call before the next Stop passes through with a notice |
| `task_ttl_days` | `30` | completed tasks pruned after this many days |
| `debug_logging` | `false` | audit logging |
| `ghost_clear_enabled` | `true` | the stale-task marker is honored |

Task state is in `~/.claude/sessions/daemon_state.json` (or its SQLite store after `autorun --state-migrate`). Per-session audit logs are in `~/.autorun/task-tracking/<session_id>/audit.log`. The `autorun task` commands in the [CLI Reference](../README.md#cli-reference) inspect, export and clean them.
