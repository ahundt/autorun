# Hook architecture

Each agent sends hook events in its own format and expects answers in its own
format. autorun turns them into one internal event, runs its handlers once, and
answers in the caller's format. To add a handler, start with
[docs/INTEGRATION_GUIDE.md](docs/INTEGRATION_GUIDE.md).

## The path of one event

```
agent hook manifest  (Claude: hooks/hooks.json; Gemini family: generated <ext>/hooks/hooks.json;
                      Pi, Prime Agent, OpenCode: in-process JS bridge)
  → hooks/hook_entry.py   stdlib only
  → src/autorun/client.py socket client; starts the daemon if needed
  → src/autorun/core.py   AutorunDaemon; normalize_hook_payload(); EventContext
  → src/autorun/plugins.py handlers registered with @app.on(event) / @app.command(...)
  → response shaped for the calling agent by platforms.HookProtocol
```

**`hooks/hook_entry.py`** imports only the standard library, because it must
run before autorun's dependencies exist. `main()` reads stdin once, then:

1. `AUTORUN_DISABLE=1` answers with the agent's no-op and stops, before any
   autorun import.
2. `try_daemon()` talks to the running daemon directly over its socket.
3. `try_cli()` runs the installed `autorun` command when the daemon path is
   unavailable.
4. `run_fallback()` imports autorun from the plugin source; on `ImportError`
   it starts a background install (`spawn_background_bootstrap()`, unless
   `AUTORUN_NO_BOOTSTRAP=1` or `--no-bootstrap`) and the next hook finds the
   dependencies.

When it cannot decide, a lifecycle event fails open (`fail_open_for_cli()`) and
a tool-permission event fails closed (`fail_closed_tool_gate()`), because a gate
that fails open blocks nothing. On an exit-0 path it never writes to stderr,
because Claude Code discards the JSON response of any hook that does;
diagnostics go to `~/.autorun/hook_entry_debug.log`. `detect_cli_type()` here
and in `config.py` decide which agent is calling; the Gemini-family manifests
also pass `--cli <name>`.

**`core.normalize_hook_payload()`** maps the agent's event name to autorun's
(`to_autorun_event`, from the registry), reads the session id from
`session_id`, Gemini's `sessionId`, or Antigravity's `conversationId`, and
trims the transcript to about the last 64 KB unless `AUTORUN_NO_TRUNCATE=1`.

**`platforms.py`** is the registry and the one owner of per-agent facts: event
names in both directions (`harness_cli_to_autorun_events`,
`autorun_to_harness_cli_events`; `core.GEMINI_EVENT_MAP` is derived from it),
tool names (Claude `Write`/`Bash`/`Edit`, Gemini `write_file`/`run_shell_command`/`replace`),
and the response protocol each agent reads. Nothing else should hard-code a
harness name.

## Two manifest roots

| | Claude Code | Gemini CLI, Qwen Code, Antigravity |
|---|---|---|
| Manifest source | `plugins/autorun/hooks/hooks.json` | `plugins/autorun/src/autorun/gemini_template/hooks/hooks.json`, copied into each extension by `installer/extension.py` |
| Path variable | `${CLAUDE_PLUGIN_ROOT}` | `${extensionPath}` |
| Events | `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `SessionStart`, `Stop`, `SubagentStop`, `PreCompact`, `PostCompact` | `SessionStart`, `BeforeAgent`, `BeforeTool`, `AfterAgent`, `AfterTool`, `PreCompress` |
| `timeout` unit | seconds (`10`) | milliseconds (`5000`) |

They live apart because Claude's plugin loader also scans the marketplace
source's `hooks/` folder and rejects Gemini event names with `invalid_key`,
which disables every hook in the file
([anthropics/claude-code#24115](https://github.com/anthropics/claude-code/issues/24115),
closed as not planned), while Gemini reads only `<ext>/hooks/hooks.json`
([google-gemini/gemini-cli#14449](https://github.com/google-gemini/gemini-cli/issues/14449)).
The template under `src/` is outside Claude's scan. The repository root's
`gemini-extension.json` and `hooks/` are symlinks into the template, so `gemini
extensions install` from a clone or a GitHub URL finds a manifest. The two
agents' native layouts still conflict, so the split stays.

Pinned by `tests/test_split_layout.py` (which events go where),
`tests/harness_hook_events.py` (the one allowlist of event names per agent),
`tests/test_hooks_format.py::test_claude_hooks_timeout_is_seconds` and
`test_dual_cli_pathways.py::test_gemini_hook_timeouts_are_milliseconds`.

## Rules that bite

1. **Edit the repository, never an installed copy.**
   `~/.claude/plugins/cache/autorun/ar/<version>/` and
   `~/.gemini/extensions/ar/` are overwritten by the next
   `autorun --install --force`, which reaches the live machine only in a
   sandbox or on the user's instruction (root [AGENTS.md](../../AGENTS.md)).
2. **A new Claude event needs its Gemini analog in the same change**, in
   `plugins.py`, the registry, and both manifests ([AGENTS.md](AGENTS.md)).
3. **Timeouts:** seconds for Claude, milliseconds for the Gemini family. A
   Gemini `"timeout": 10` gives the hook 10 ms.
4. **Gemini hook entries need `"type": "command"` and a command with no
   leading `VAR=value`**, which Gemini CLI does not support. Claude's command
   starts with `P=...`, so do not copy it across.
5. **Test Gemini interactively in tmux**, not with `subprocess.run(["gemini",
   ...])`, which waits for a terminal. The `tmux-automation` skill has the tmux
   recipe; `tests/test_gemini_before_tool_hooks.py` is a worked Gemini example
   (a paid test).

## Tests

```bash
uv run --project plugins/autorun pytest plugins/autorun/tests/test_hook_entry.py \
  plugins/autorun/tests/test_hooks_format.py plugins/autorun/tests/test_split_layout.py \
  plugins/autorun/tests/test_dual_cli_pathways.py -q
```

References: [Claude Code hooks](https://docs.claude.com/en/docs/claude-code/hooks),
[docs/hooks_api_reference.md](../../docs/hooks_api_reference.md),
[TROUBLESHOOTING.md](TROUBLESHOOTING.md).
