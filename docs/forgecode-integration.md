# ForgeCode Integration

[ForgeCode](https://forgecode.dev) by antinomyhq is a Rust-based AI
coding assistant. Unlike Claude Code, Gemini CLI, and Codex CLI, it
does **not** expose an external hook system — its lifecycle is entirely
internal (`crates/forge_domain/src/hook.rs`).

Autorun therefore integrates with ForgeCode via two surface paths it
DOES support: custom commands and `AGENTS.md`.

## Custom commands

Location: `<base>/commands/*.md`.

Per `crates/forge_services/src/command.rs:56-74`, ForgeCode loads
commands from `<cwd>/.forge/commands/` (project-level) or
`<base>/commands/` (global). Frontmatter parsing is in
`crates/forge_domain/src/command.rs:11-21` — only `name` and
`description` are read; everything else is silently dropped.

Autorun ships these commands in `forgecode_template/commands/`:

| Command       | Purpose                                                      |
|---------------|--------------------------------------------------------------|
| `ar-go`       | Start an autorun task with three-stage verification          |
| `ar-st`       | Show current AutoFile policy and tasks                       |
| `ar-allow`    | Set AutoFile policy to ALLOW                                 |
| `ar-find`     | Set AutoFile policy to FIND (existing files only)            |
| `ar-commit`   | Refresh git commit guidelines                                |
| `ar-ph`       | Refresh system design philosophy                             |

Invocation: `forge cmd execute <name>` (CLI) or `:<name>` (zsh plugin).

## AGENTS.md

Location: `<base>/AGENTS.md` (also `<git_root>/AGENTS.md` and
`<cwd>/AGENTS.md` — ForgeCode concatenates all three per
`crates/forge_services/src/instructions.rs:22-47`).

ForgeCode injects AGENTS.md content into the agent context as "custom
instructions" (`crates/forge_services/src/instructions.rs:73-79`). The
autorun template provides advisory safety guidance covering:

- Prefer `trash` over `rm` for paths you did not just create
- No `git reset --hard`, `git push --force`, `git clean -f` without
  explicit user instruction in the current turn
- Stop semantics: only the user can type `/ar:sos` or
  `/ar:task-ignore <id>` to unblock incomplete-task stops

## Base path resolution

Per `crates/forge_config/src/reader.rs:58-84`:

1. `FORGE_CONFIG` env var (explicit override)
2. `~/forge/` (legacy, only if it already exists)
3. `~/.forge/` (default)

The autorun installer (`_install_for_forgecode`) honors this precedence.

## What autorun cannot do for ForgeCode

Without external hooks, autorun cannot enforce:

- Pre-tool blocking (no `PreToolUse` equivalent)
- Stop-block enforcement (no `Stop` event)
- File-policy validation (no way to reject a tool call from outside the agent)

The guards therefore run **advisory** — the AGENTS.md guidance tells the
agent what NOT to do, but does not stop a misbehaving agent. For
enforcement, use Claude, Gemini, or Codex.

## References

- [ForgeCode custom commands docs](https://forgecode.dev/docs/commands/)
- [ForgeCode GitHub](https://github.com/antinomyhq/forgecode)
- [ForgeCode DeepWiki: agent/workflow config](https://deepwiki.com/antinomyhq/forgecode/2.1-agent-and-workflow-configuration)
