# Codex CLI hook surface (v0.11.0 reference)

OpenAI's [Codex CLI](https://developers.openai.com/codex/) (v0.133 at
the time of writing, rechecked against local `codex-cli 0.137.0`) ships a
hook system with Claude-like event names but Codex-specific response schemas.
Autorun's integration uses the shared `hook_entry.py` script, but the
`--cli codex` flag must select Codex-specific response filtering. Treating
Codex as "Claude strict schema" is not safe: Codex rejects unsupported output
fields and reports hook failures.

## Hook events

Codex emits identical event names to Claude Code:

| Event              | Fires when                                                       |
|--------------------|------------------------------------------------------------------|
| `PreToolUse`       | Before any tool call (shell/Bash reliably; `apply_patch` + MCP gap — see [openai/codex#16732](https://github.com/openai/codex/issues/16732)) |
| `PostToolUse`      | After a tool call returns                                        |
| `UserPromptSubmit` | When the user submits a prompt                                   |
| `SessionStart`     | At session start                                                 |
| `SessionEnd`       | At session end                                                   |
| `Stop`             | When the agent attempts to stop                                  |
| `SubagentStart`    | When a sub-agent is invoked                                      |
| `SubagentStop`     | When a sub-agent finishes                                        |
| `PreCompact`       | Before context compaction                                        |
| `PostCompact`      | After context compaction                                         |
| `PermissionRequest`| When the agent requests elevated permissions                     |

Verified against the `HookEventNameWire` enum in the v0.133 binary.

## Hook payload

Stdin JSON contains: `session_id`, `transcript_path`, `cwd`,
`hook_event_name`, `model`, `permission_mode`. Same shape as Claude Code.

## Hook response

Codex performs strict JSON validation (`additionalProperties:false`);
unknown or unsupported fields are rejected and surfaced as hook failures.
The response shape is not identical to Claude Code's legacy schema.

Important autorun rules:

- Normal `UserPromptSubmit` and command responses MUST NOT emit
  `decision: "approve"`. Codex accepts `decision: "block"` for prompt-blocking
  responses; normal allow/context responses should omit `decision`.
- `UserPromptSubmit` command output should use common fields such as
  `continue`, `stopReason`, `suppressOutput`, `systemMessage`, plus
  `hookSpecificOutput.additionalContext` when the model should see the text.
- `PreToolUse` must omit Codex-unsupported common fields such as `continue`,
  `stopReason`, and `suppressOutput`. Use
  `hookSpecificOutput.permissionDecision: "deny"` with
  `permissionDecisionReason` for blocking, or a top-level
  `decision: "block"` + `reason` legacy block. Use
  `hookSpecificOutput.permissionDecision: "allow"` only when also returning
  `updatedInput`; ordinary non-blocking warnings/context should use
  `systemMessage` and/or `hookSpecificOutput.additionalContext`.
- Exit code 0 plus valid JSON is the standard path. The Claude exit-2
  workaround does not apply to Codex.

The observed failure signature for stale/incorrect output is:

```text
UserPromptSubmit hook (failed)
error: hook returned invalid user prompt submit JSON output
```

## Where autorun installs hooks

User-level: `~/.codex/hooks.json` (default, always active, no marketplace
required).

Codex plugin packaging: `~/.agents/plugins/marketplace.json` lists an
`autorun` plugin with local source `./plugins/autorun`, which resolves to
`~/plugins/autorun`. That plugin source contains `.codex-plugin/plugin.json`
and `skills/`, so Codex can discover autorun's skills through its native
plugin marketplace path.

Autorun makes the Codex hook source explicit with
`--codex-hook-source user|plugin|both|none`. The default is `user`, which keeps
enforcement in `~/.codex/hooks.json` and packages the Codex plugin for skills
and metadata only. `plugin` mode generates a Codex-specific
`hooks/hooks.json` with `--cli codex` and removes autorun's user-level hooks.
`both` intentionally installs both sources. Avoid accidental user+plugin
duplicates: Codex merges hooks from all active sources, so duplicate
`PreToolUse`, `UserPromptSubmit`, `Stop`, and task lifecycle hooks all run.

The local development plugin identity is `autorun@personal`: `autorun` is the
plugin name and `personal` is the generated local marketplace name. Repo-backed
installs use `.agents/plugins/marketplace.json` from `ahundt/autorun`, whose
marketplace name is `autorun`, so the plugin identity is
`autorun@autorun`.

Path variables available inside hook commands:

| Variable                   | Value                             |
|----------------------------|-----------------------------------|
| `${PLUGIN_ROOT}`           | plugin root (Codex's primary var) |
| `${CLAUDE_PLUGIN_ROOT}`    | compat alias (also set by Codex)  |
| `${PLUGIN_DATA}`           | plugin data dir                   |
| `${CLAUDE_PLUGIN_DATA}`    | compat alias                      |
| `${CODEX_HOME}`            | `~/.codex` (or override)          |

No `CODEX_SESSION_ID` env var is exported to hook subprocesses today —
the session id arrives only through stdin JSON.

## Autorun behavior

Autorun uses the same command handlers and safety policies for Codex as for
Claude Code and Gemini CLI, but adapts model-facing guidance to Codex:

- Task progress maps to Codex's native `update_plan` checklist tool.
- Search guidance uses shell `rg -n` for text search and `rg --files` for file discovery instead of Claude/Gemini search tool names.
- File guidance uses shell file inspection for reads and `apply_patch` for edits.
- Plain bounded `cat`, `head`, and `tail` reads are allowed for Codex; redirects,
  follow-mode `tail`, and prefixed forms such as `sudo cat` remain blocked.

Claude/Gemini examples usually show `/ar:*`. In Codex, prefer `ar:*` or
`ar <command>` (for example `ar:st` or `ar:ok git push`) because unknown slash
commands can be intercepted before `UserPromptSubmit` hooks see them.
When Codex does not deliver `UserPromptSubmit`, autorun's transcript fallback
processes exact `ar:*` policy commands on the first later `PreToolUse` and
returns a `hookSpecificOutput.additionalContext` notice there. This is the
earliest reliable notification autorun can emit without blocking the tool, and
it avoids duplicating the same status text as both a Codex `warning` and hook
context.

## Trust model

Codex hashes each hook command and stores the approved hashes in a TOML
state file. New or modified hashes are **silently skipped** until the
user runs `/hooks` inside Codex and approves them. The autorun installer
prints this reminder at the end of `_install_for_codex`:

> ✓ Codex hooks installed at ~/.codex/hooks.json
> Next: run `/hooks` inside Codex CLI to trust the new hook hashes.

## Bug-workaround applicability

| Bug                              | Affects Codex? |
|----------------------------------|----------------|
| Claude #4669 (exit-2 quirk)      | No             |
| Claude #18534 (additionalContext drop) | No       |
| Gemini #14449 (hardcoded hooks path)   | No       |

## References

- [Codex CLI hooks documentation](https://developers.openai.com/codex/hooks)
- [Codex plugin authoring](https://developers.openai.com/codex/plugins/build)
- [Codex advanced configuration](https://developers.openai.com/codex/config-advanced)
- [Agentic Control Plane: Codex hooks reference](https://agenticcontrolplane.com/blog/codex-cli-hooks-reference)
- [openai/codex#16732 — apply_patch coverage gap](https://github.com/openai/codex/issues/16732)
