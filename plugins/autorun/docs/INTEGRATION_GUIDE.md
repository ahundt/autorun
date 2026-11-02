# Extending autorun

How a hook event reaches autorun's code, and the three places to add behavior.

## The path of a hook event

```
agent hook event
  → hooks/hook_entry.py      stdlib only; finds the agent, starts or reaches the daemon
  → src/autorun/client.py    socket client; starts the daemon if it is not running
  → src/autorun/core.py      AutorunDaemon; dispatches the event
  → src/autorun/plugins.py   handlers: file policy, command guards, tasks, plan export
```

Claude Code reads `hooks/hooks.json`; the installer generates each Gemini-family
extension's own `hooks/hooks.json`. Details: [HOOK_ARCHITECTURE.md](../HOOK_ARCHITECTURE.md).

The daemon starts on the first hook event and keeps running Python code from
when it started, so changed code takes effect only after
`uv run --project plugins/autorun python -m autorun --restart-daemon`, which
restarts the daemon of whichever `AUTORUN_HOME` it runs under. Run it, like
everything here, under the root [AGENTS.md](../../../AGENTS.md) sandbox, never
against your live state.

## Run a hook without the daemon

`AUTORUN_USE_DAEMON=0` runs the handlers in-process, which isolates a daemon
problem from a handler problem:

```bash
SB=$(mktemp -d /tmp/arsb.XXXXXX)
echo '{"hook_event_name": "UserPromptSubmit", "prompt": "/ar:st", "session_id": "test", "cwd": "/tmp"}' \
  | env HOME="$SB" AUTORUN_HOME="$SB/ar" AUTORUN_TEST_STATE_DIR="$SB/state" AUTORUN_USE_DAEMON=0 \
    uv run --project plugins/autorun python -m autorun
```

It prints the hook response, here with a `systemMessage` containing
`AutoFile policy: allow-all`.

## Add behavior

**A slash command with no Python:** add `commands/<name>.md` with a
`description:` in its frontmatter (`test_command_help.py` requires one). Its
body tells the agent what to do; after a (sandboxed) `autorun --install --force`
it is `/ar:<name>` in the next session.

**A handler:** register it in `src/autorun/plugins.py` and return `None` to let
the event pass:

```python
@app.on("PostToolUse")
def note_large_writes(ctx: EventContext):
    if ctx.tool_name == "Write" and len(ctx.tool_input.get("content", "")) > 100_000:
        ctx.add_chain_notification("Wrote a file over 100 KB", channel="human")
    return None
```

A rule that blocks tools does not get its own `PreToolUse` handler: it belongs
in the guard chain (`plugins.check_blocked_commands`), after `/ar:ok` allows and
before pattern blocks, or an allow can never override it. Most such rules are a
guard entry (below).

Related commands share a factory rather than copies: `_make_policy_handler()`
for file policies, `_make_block_op()` for block and allow commands. A new event
needs its Gemini analog in the same change; the rules are in
[AGENTS.md](../AGENTS.md).

**A guard:** add an entry to `DEFAULT_INTEGRATIONS` in `src/autorun/config.py`
with a `suggestion` naming the safer command.

## Test

```bash
uv run --project plugins/autorun pytest plugins/autorun/tests/ -q
uv run --project plugins/autorun pytest plugins/autorun/tests/test_core.py plugins/autorun/tests/test_integrations.py -q
```

A deny reaches Claude Code as exit code 2 with the reason on stderr, because it
ignored a JSON deny at exit 0
([anthropics/claude-code#4669](https://github.com/anthropics/claude-code/issues/4669));
`client.py:output_hook_response()` handles that, and the workaround policy is in
[AGENTS.md](../AGENTS.md).
