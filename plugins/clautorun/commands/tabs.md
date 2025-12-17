---
description: Discover and manage Claude sessions across tmux windows
allowed-tools: Bash(tmux *), Bash(python3 *), Bash(uv *)
argument-hint: [action] [sessions]
---

# Claude Session Manager

Discover, analyze, and interact with Claude Code sessions across tmux windows.

## Context

Current tmux sessions and Claude windows:
!`tmux list-sessions -F "#{session_name}: #{session_windows} windows" 2>/dev/null || echo "No tmux sessions"`

## Your Task

$ARGUMENTS

If no arguments provided, discover all Claude sessions and present them to the user.

## Workflow

1. **Discover** - Run the discovery script to get session data
2. **Present** - Show sessions as table with letter labels (A, B, C...)
3. **Ask** - Get user selection for which sessions to interact with
4. **Execute** - Run commands on selected sessions

## Discovery

```bash
"${CLAUDE_PLUGIN_ROOT}/commands/tabs-exec"
```

## User Selection Syntax

| Syntax | Meaning |
|--------|---------|
| `A,C` or `AC` | Select sessions A and C |
| `A:git status` | Run custom command on session A |
| `B:pwd, C:git log` | Different commands per session |
| `all:continue` | Execute on all discovered sessions |
| `awaiting:continue` | Execute only on sessions awaiting input |

## Execution

After user confirms, execute via:

```bash
# Using tabs-exec
echo '{"selections": "B,C", "command": "continue"}' | "${CLAUDE_PLUGIN_ROOT}/commands/tabs-exec" --execute

# Direct tmux (for simple commands)
tmux send-keys -t "session:window" "command" C-m
```

## Prompt Types

When discovering sessions, detect what state each Claude session is in:

| Type | Visual Indicator |
|------|------------------|
| `input` | Standalone `>` prompt - ready for input |
| `plan_approval` | "Would you like to proceed?" |
| `tool_permission` | `[Y/n]` or numbered options `[1] [2]` |
| `question` | Multi-choice with ❯ selector |
| `clarification` | Question ending with `?` |
| `working` | No prompt detected - Claude is active |

## Session Table Format

Present discovered sessions as:

```
| # | Session | Purpose | Status |
|---|---------|---------|--------|
| A | main:3  | Feature dev | awaiting input |
| B | main:5  | Testing     | working |
| C | work:1  | Bug fix     | plan approval |
```

Then ask: "Which sessions would you like to interact with?"
