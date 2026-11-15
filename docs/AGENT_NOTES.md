# Agent notes

Per-agent install details that the [README](../README.md#supported-agents) keeps to a table and a few sentences. Each section covers one agent: what `autorun --install` writes, what differs from Claude Code, and the limits to know.

1. [Codex CLI](#codex-cli)
2. [Pi and Prime Agent](#pi-and-prime-agent)
3. [OpenCode](#opencode)
4. [Qwen Code](#qwen-code)

## Codex CLI

Where the install writes:

1. Hooks go in `~/.codex/hooks.json`.
2. A local plugin, `ar@personal`, keeps its files in `~/plugins/ar`, where Codex keeps personal plugins, and its entry in `~/.agents/plugins/marketplace.json`.
3. To use the folder Codex's documentation names instead, set the `codex_plugin_source_dir` config key to `~/.codex/plugins`.

If Codex asks, run `/hooks` once so it trusts the hooks.

### Choosing where hooks live

Codex runs the same hook twice if two places declare it, so pick one with `--codex-hook-source`:

| Value | Hooks live in |
|---|---|
| `user` (default) | `~/.codex/hooks.json` only |
| `plugin` | the `ar@personal` plugin only |
| `both` | both places, on purpose |
| `none` | nowhere; skills and guidance still install |

```bash
autorun --install --codex --codex-hook-source plugin
autorun --install --codex --codex-plugin-marketplace github   # ar@autorun from ahundt/autorun instead of ar@personal
```

`AUTORUN_CODEX_HOOK_SOURCE` sets the source for unattended installs. A reinstall after switching leaves no duplicate hooks behind.

### How autorun maps onto Codex

1. The generated manifest includes `PreCompact` and `PostCompact`, so autorun invalidates its cached usage reading around manual and automatic compaction.
2. Task progress maps to `update_plan`, search to `rg -n` and `rg --files`, and edits to `apply_patch`.
3. Skills appear through `/skills`, a `$mermaid-diagrams` mention, or the `@autorun` plugin. Codex does not turn skills into slash commands such as `/mermaid`.

Hook schema: [codex-cli-hooks-api.md](codex-cli-hooks-api.md).

## Pi and Prime Agent

```bash
autorun --install --pi --force    # or --prime for Prime Agent
pi
/ar status
```

The extension lives in `~/.pi/agent/extensions/ar/` (Prime Agent: `~/.prime/agent/extensions/ar/` before version 0.10). It passes tool calls, prompts, results and session events to the same autorun daemon the other agents use.

1. A blocked tool call comes back as Pi's `{ block: true, reason }`.
2. When autorun keeps the agent from finishing (`agent_settled`), it starts the next turn with a hidden `autorun-continuation` message, so the transcript never shows it as something you typed.
3. A cancelled (`aborted`) turn is not continued.
4. Pi loads skills with `/skill:<name>`. Its accepted command spellings are in the [README's spelling table](../README.md#command-spelling).
5. Pi gets `TaskCreate`, `TaskUpdate`, `TaskList` and `TaskGet` tools backed by the same task tracking as Claude. `TaskUpdate` takes one `taskId` or an atomic `taskUpdates` array, including `addBlockedBy` and `addBlocks`; `status="deleted"` deletes.

### Prime Agent 0.10 and newer

Prime releases below 0.10.0 use the Pi-compatible extension contract above. Prime 0.10.x uses a Rust runtime without the Pi extension API, but it reads `~/.prime/agent/AGENTS.md`, `~/.prime/agent/skills`, and `~/.agents/skills`.

`autorun --install --prime` on that runtime installs guidance and skills and reports that external enforcement is unavailable.

Prime also supports MCP through `mcpServers` and `~/.prime/agent/mcp-services.json`. autorun currently ships no Prime-specific MCP server, so there is no MCP artifact to add.

### What autorun does not check

autorun checks the tool calls the model makes. It does not check a `!` shell line you type, or a child `pi --no-extensions`.

`pi-subagents` starts a child that way when an agent definition lists its own `extensions:` (or a capability ceiling denies extensions). Add `~/.pi/agent/extensions/ar/index.ts` (or the `~/.prime` path) to that list. Agents without an `extensions:` key inherit the guard.

## OpenCode

### Stop enforcement

OpenCode 0.6.0 and newer expose `client.session.prompt`, so a blocked Stop can start a new assistant turn. OpenCode 1.0.111 added the non-blocking `client.session.promptAsync`; autorun uses it first and falls back to `prompt`.

A client with neither method cannot enforce the continuation. It shows the reason in a warning toast when that API exists; otherwise it only records the reason through OpenCode's structured log API when available. Upgrade OpenCode. Until then the Stop block is a warning only and you must continue manually.

### Who may run `ar:ok`

OpenCode exposes the session's `parentID` and marks injected parts as `synthetic`, but it does not distinguish a typed root-session prompt from the same prompt sent by another plugin or API client. autorun therefore:

1. accepts a root-session prompt only when no part is synthetic;
2. classifies a child session as a subagent;
3. classifies synthetic or unresolvable prompts as automation.

OpenCode's built-in task tool creates a child session with `parentID`. Its built-in shell route writes a synthetic user record without invoking `chat.message`. Neither gives the model a root-session user prompt.

The README's [permissions table](../README.md#only-you-can-change-permissions) lists the result per agent.

## Qwen Code

```bash
brew install qwen-code
autorun --install --qwen --force
qwen extensions list
```

Qwen gets the Gemini extension with its hooks set to `--cli qwen`, so Qwen sessions get Qwen's response format.
