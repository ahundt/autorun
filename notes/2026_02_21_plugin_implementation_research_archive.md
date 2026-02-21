# Plugin Implementation Approaches - Research Findings (Archived from README)

> This content was removed from README.md on 2026-02-21 as it documents resolved design research from early development. Kept here for historical reference.

## Official Plugin Pattern

Sources: [Agent SDK Overview](https://docs.claude.com/en/api/agent-sdk/overview), [Plugins](https://github.com/anthropics/claude-code/tree/main/plugins)

Official documentation states: *"Slash Commands: Use custom commands defined as Markdown files in `./.claude/commands/`"*

- Plugins use **markdown files** in `commands/` directory
- Example: `/new-sdk-app` is implemented as `new-sdk-app.md`
- Markdown files contain prompts that tell Claude what to do
- No executable scripts found in official plugins

### Example: agent-sdk-dev Plugin

[Source](https://github.com/anthropics/claude-code/blob/main/plugins/agent-sdk-dev/README.md)

Plugin Structure:
```
agent-sdk-dev/
├── .claude-plugin/
│   └── plugin.json
├── commands/
│   └── new-sdk-app.md          # Main command - interactive project setup
├── agents/
│   ├── agent-sdk-verifier-py   # Python verification agent
│   └── agent-sdk-verifier-ts   # TypeScript verification agent
└── README.md
```

How It Works:
1. **Command File** ([new-sdk-app.md](https://github.com/anthropics/claude-code/blob/main/plugins/agent-sdk-dev/commands/new-sdk-app.md)) contains detailed prompt with requirements gathering questions, setup instructions, verification procedures, and best practices.

2. **Command Execution**: User runs `/new-sdk-app` → Claude reads the markdown prompt → Interactively asks questions → Creates project files → Runs verification agent.

3. **Key Principles from Official Plugin**: "ALWAYS USE LATEST VERSIONS", "VERIFY CODE RUNS CORRECTLY", ask questions one at a time, use modern syntax, include proper error handling.

This shows the official pattern: **Markdown files define prompts that guide Claude's behavior**, not executable code that processes commands.

## Bash Integration in Slash Commands

[Documentation](https://docs.claude.com/en/docs/claude-code/slash-commands)

Commands can execute bash scripts using the `!` prefix:

```markdown
---
allowed-tools: Bash(git add:*), Bash(git status:*)
description: Create a git commit
---

## Context
- Current git status: !`git status`
- Current branch: !`git branch --show-current`

## Your task
Based on the above changes, create a single git commit.
```

**How Bash Integration Works**:
- Use `!` prefix before bash command in markdown
- Must declare `allowed-tools` in frontmatter
- Command output is included in context for Claude
- Can call external scripts: `!`./scripts/my-script.sh``

## Python Agent SDK

[Documentation](https://docs.claude.com/en/api/agent-sdk/python), [README](https://github.com/anthropics/claude-agent-sdk-python), [client.py](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/client.py)

The SDK provides direct communication with Claude Code:
- `query()` - Async function for querying Claude Code directly
- `ClaudeSDKClient()` - Advanced client for interactive conversations
- `@tool` decorator - Define custom tools (in-process MCP servers)
- Hooks support via `ClaudeAgentOptions`
- `get_server_info()` - Can retrieve available commands from server

*"In-process MCP servers for custom tools - No subprocess management - Direct Python function calls with type safety"*

Python code CAN communicate with Claude Code directly, but the documented pattern for slash commands is still markdown files that can call bash scripts.

## Our Implementation (Non-standard JSON Protocol)

- Executable `commands/autorun` script using JSON stdin/stdout protocol
- Works when symlinked to `~/.claude/commands/autorun`
- Not recognized by plugin system's command discovery
- Uses Agent SDK-style JSON communication pattern

**Status at time of research:**
- Plugin system doesn't auto-discover executable commands
- Executable works via manual symlink in `~/.claude/commands/`
- No documentation found for executable-based plugin commands

## Possible Solutions (as evaluated)

1. **Bash Integration in Markdown** (RECOMMENDED - Official Pattern)
2. **Use Hooks Instead** (UserPromptSubmit hooks)
3. **Python SDK Direct Integration** (ClaudeSDKClient)
4. **Direct Symlink** (Current Working Solution at time of research)
5. **Pure Markdown** (Simplest, loses programmatic state management)
