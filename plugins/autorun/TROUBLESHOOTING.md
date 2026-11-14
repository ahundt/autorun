# Troubleshooting autorun

- [Every tool call is blocked](#every-tool-call-is-blocked)
- [Commands or guards do nothing](#commands-or-guards-do-nothing)
- [A dangerous command was not blocked](#a-dangerous-command-was-not-blocked)
- [Claude task tools are missing or vanished](#claude-task-tools-are-missing-or-vanished)
- [Task tools on other harnesses](#task-tools-on-other-harnesses)
- [Errors from the hook itself](#errors-from-the-hook-itself)
- [Reporting a bug](#reporting-a-bug)

## Every tool call is blocked

Every tool call is denied with a message like:

```
[autorun] Import error: No module named 'autorun'. Cannot bootstrap:
previous bootstrap failed: .../.venv/bin/python: No module named autorun.
Blocking tool use to avoid fail-open.
```

autorun cannot load its own code, so it cannot check any command. It blocks
every tool call, because a broken guard that let calls through would let `rm -rf`
through too, unnoticed.

This does not clear on its own, and the agent cannot fix it, because the repair
command is blocked too. `/ar:estop` cannot help either; the failure happens before
autorun reads any session state.

**Fix it in a terminal, outside the agent.** Run `autorun --install --force`.
It reinstalls autorun's hook environment, including the `.venv` in Claude's
plugin cache when you install from a source checkout. If the `autorun` command
itself fails, or the install reports that the Claude cache runtime step failed,
run the `uv pip install --python ...` command the denial names. For Claude's
plugin cache it looks like:

```bash
uv pip install --python ~/.claude/plugins/cache/autorun/ar/<version>/.venv/bin/python \
  --reinstall ~/.claude/plugins/cache/autorun/ar/<version>
```

Then run `autorun --status`; it reports any file that still differs from this
version and makes one test run of the hook.

**To keep working before you repair it**, turn autorun off and start the agent
from the same shell, since hooks inherit the agent's environment:

```bash
export AUTORUN_DISABLE=1
claude          # or codex, qwen, pi, ...
```

autorun reads this before importing anything, so it works in exactly this state.
It turns off every protection (command guards, file policies, task enforcement)
until you unset it; it is not a fix. `AUTORUN_NO_BOOTSTRAP=1` only stops autorun
reinstalling itself in the background, and calls stay blocked. Use it when the
repeated reinstall attempts are the problem.

## Commands or guards do nothing

If a notice says autorun is degraded, keep working and let the daemon recover;
do not restart it under load. The local default guards still deny destructive
shell commands. Ordinary shell commands and non-shell tools proceed. `/ar:no`
rules, `/ar:ok` grants, the file policy, and task and plan gating are not checked
until autorun answers again. A guarded command cannot use a session grant.
If a local guard errors or runs out of budget, that shell call is denied with
wait-and-retry guidance; non-shell tools continue.

`overload_policy` in `~/.autorun/autorun.config.json` has three values:
`critical-guards` (default), `block` (blanket tool-gate denial on daemon
failure), and `allow` (no local guards). `AUTORUN_OVERLOAD_POLICY` overrides
the file; an unknown value falls back to `critical-guards` and is reported by
`autorun --status`. These choices cover load denials, client response timeouts,
an unavailable daemon, and session-state evaluation failures. They do not
change Stop or non-gate events. If the Python package itself cannot import,
the stdlib hook still denies tool calls and gives the repair guidance above:
there are no guard predicates to run.

1. `autorun --status` checks every detected agent and runs the hook command
   once; a working hook reports `ok hook command`.
2. Restart the agent session. Each agent reads its hooks when the session
   starts, so an install or upgrade reaches only new sessions.
3. Read the logs: every hook call goes to `~/.autorun/hook_entry_debug.log`,
   and the daemon's view to `~/.autorun/daemon.log`.
4. If hooks still run old code after an upgrade, restart the daemon:
   `autorun --restart-daemon`, or `/ar:restart-daemon` in a session.
5. Reinstall one agent: `autorun --install --claude --force` (or `--codex`,
   `--qwen`, `--antigravity`, `--pi`, `--prime`, `--gemini`).

**Claude Code.** `claude plugin list` should show `ar@autorun`. The installed
copy is `~/.claude/plugins/cache/autorun/ar/<version>/`; its `hooks/hooks.json`
commands start with `${CLAUDE_PLUGIN_ROOT}`. `claude --debug` shows plugin
loading.

### Gemini CLI checklist

Standalone Gemini CLI (legacy, installed only with `--gemini`):

1. `gemini --version` must be 0.28.0 or later. Update with `npm install -g
   @google/gemini-cli@latest` (or `bun install -g`).
2. `~/.gemini/settings.json` must enable hooks:

   ```json
   {
     "tools": {
       "enableHooks": true,
       "enableMessageBusIntegration": true
     }
   }
   ```

3. `~/.gemini/extensions/ar/hooks/hooks.json` must exist, using
   `${extensionPath}` and Gemini's events (`SessionStart`, `BeforeAgent`,
   `BeforeTool`, `AfterAgent`, `AfterTool`, `PreCompress`). If not:
   `autorun --install --gemini --force`.
4. At startup Gemini prints `Hook registry initialized with N hook entries`;
   `N` must be above 0, and each hook run prints `Created execution plan for
   BeforeTool: ...`. If neither appears, recheck steps 2 and 3.
5. Gemini's trust prompt for a new folder restarts the session; trust the
   folder first when testing.

## A dangerous command was not blocked

1. Check for an allow you gave earlier. `/ar:status` lists the session and
   global blocks and allows, and `/ar:clear` / `/ar:clear --global` remove them.
   `/ar:ok` with a count or duration stays active until it runs out.
2. Check that the agent's harness can block at all. ForgeCode receives advice
   only, and Pi's guard checks only the model's tool calls, so a `!` line you
   type runs unchecked (see the [README](../../README.md#pi-and-prime-agent)).
3. Test: ask the agent to run `rm /tmp/autorun-test` and expect a block that
   suggests `trash`. If nothing happens, work through
   [Commands or guards do nothing](#commands-or-guards-do-nothing).

## Claude task tools are missing or vanished

**Symptoms:** `TaskCreate`, `TaskUpdate`, `TaskList` or `TaskGet` is absent;
`ToolSearch` finds no match; the task panel freezes; or autorun blocks Stop
naming a task tool the agent cannot call.

Claude Code 2.1.233 took these tools away from Opus 4.8, Sonnet 5, Fable 5 and
newer models, and 2.1.268 offers them only to Claude 3.x, Opus 4.0-4.7, Sonnet
4.0-4.6 and Haiku 4.5 unless `CLAUDE_CODE_ENABLE_TODO_TOOLS=1` is set
([tool availability](https://code.claude.com/docs/en/tools-reference)). The
same tools can also vanish mid-session
([#80401](https://github.com/anthropics/claude-code/issues/80401)).

`autorun --install` sets the variable in the `env` object of
`~/.claude/settings.json` unless you already set it. Start a new session
afterwards, because Claude Code reads settings at startup and a hook cannot
change its environment. Without the variable, autorun enforces task tracking
only after the session has made a task call (older models have the tools
anyway), and its session-start notice says tracking is not enforced yet.

If the tools are still missing, load them once in the session:

```text
ToolSearch query: select:TaskCreate,TaskUpdate,TaskList,TaskGet
```

If that finds nothing, check the `env` object in `~/.claude/settings.json` and
the project's `.claude/settings.json`, then start a new session:

```json
{
  "env": {
    "CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"
  }
}
```

Remove any `CLAUDE_CODE_ENABLE_TASKS` set to `0`. It switches Claude Code to the
older `TodoWrite`, which autorun does not track. To discuss without enforcement
while keeping the tasks, run `/ar:task pause <reason>`.

To keep the tools off, set `CLAUDE_CODE_ENABLE_TODO_TOOLS` to `"0"` yourself.
`autorun --install` leaves a value you set alone, and `--uninstall` removes only
the value it added. Deleting the line does not opt out, because the next install
adds it back. `autorun --status` shows each setting as `current`, `would set`,
or kept. To turn off autorun's recovery text for #80305 and #80401:

```bash
export AUTORUN_BUG_CLAUDE_CODE_TASK_TOOLS_GATED_OFF_BUG_80305_WORKAROUND_ENABLED=false
export AUTORUN_BUG_CLAUDE_CODE_TASK_TOOLS_VANISH_MID_SESSION_BUG_80401_WORKAROUND_ENABLED=false
```

## Task tools on other harnesses

autorun enforces task tracking only where the harness's own task tool exists.
When it cannot tell, it does not block and only records what the agent tracks.

| Harness | Tool | Off by default since | What turns it on |
|---|---|---|---|
| Qwen Code | `todo_write` | 0.24.0 | `"tools": {"todoWrite": {"enabled": true}}` in `~/.qwen/settings.json` (a project's `.qwen/settings.json` overrides it); `autorun --install` sets it unless you did. autorun enforces while the setting is `true` or once the session has called `todo_write`. If the file has comments, autorun leaves it alone and its install output names the setting to add. |
| Codex | `update_plan` | 0.152.0 | `[tools.update_plan]` `enabled = true` in `~/.codex/config.toml`; `autorun --install` sets it unless you did, keeping your comments. Plan mode still rejects the tool and hooks cannot see the mode, so autorun records Codex checklists but never requires them. |
| Gemini CLI | `write_todos` | 0.36.0 (default model) | Only an explicit Gemini 2.x model gets it; no setting guarantees it, so autorun requires it only once the session has called it. |

Pi and Prime Agent report their tools to autorun directly; OpenCode's primary
agent always has `todowrite`.

## Errors from the hook itself

Run the hook by hand and read what it prints. It should print one JSON object,
such as `{"continue": true}`:

```bash
echo '{"hook_event_name":"SessionStart"}' | \
  ~/.claude/plugins/cache/autorun/ar/<version>/.venv/bin/python \
  ~/.claude/plugins/cache/autorun/ar/<version>/hooks/hook_entry.py
```

For the legacy Gemini CLI the hook is
`~/.gemini/extensions/ar/hooks/hook_entry.py`. autorun needs Python 3.10 or
later. `ModuleNotFoundError: No module named 'autorun'` is the
[blocked state above](#every-tool-call-is-blocked); a `PermissionError`,
`SyntaxError` or `IndentationError` means a damaged install, so reinstall with
`autorun --install --force`. Claude Code discards a hook's response when the
hook writes anything to stderr, so `uv` warnings printed by
`uv run --project ~/.claude/plugins/cache/autorun/ar/<version> python -c pass`
are enough to switch every guard off; report them as a bug.

Hook payload formats: [docs/hooks_api_reference.md](../../docs/hooks_api_reference.md).

## Reporting a bug

Open an issue at <https://github.com/ahundt/autorun/issues> with `autorun
--version`, the output of `autorun --status`, your agent and its version
(`claude --version`, `codex --version`, ...), the relevant lines of
`~/.autorun/hook_entry_debug.log` and `~/.autorun/daemon.log`, and the steps
that reproduce it.

Related: [README.md](../../README.md) for usage, [AGENTS.md](AGENTS.md) for
plugin development.
