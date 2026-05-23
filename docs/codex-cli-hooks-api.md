# Codex CLI hook surface (v0.11.0 reference)

OpenAI's [Codex CLI](https://developers.openai.com/codex/) (v0.133 at
the time of writing) ships a hook system that is binary-compatible with
Claude Code's hooks. Autorun's integration uses this surface directly —
the same `hook_entry.py` script handles Claude, Codex, and Gemini
events; the `--cli codex` flag selects the right code paths.

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

Strict JSON schema (`additionalProperties:false`); unknown fields are
rejected. Same shape as Claude Code's HOOK_SCHEMAS. Exit code 0 plus a
JSON `permissionDecision:"deny"` is honored — no exit-2 workaround is
required (Claude bug #4669 does not apply).

## Where autorun installs hooks

User-level: `~/.codex/hooks.json` (always active, no marketplace required).

Plugin-bundled hooks would live under `~/.codex/plugins/<name>/.codex-plugin/plugin.json`
but require `features.plugin_hooks = true` in the Codex config, which is
**off by default in v0.133** ("Plugin hooks are off by default in this
release."). Autorun chose user-level for consistency with the global
install model for Claude (plugin manifest) and Gemini (extension manifest).

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
