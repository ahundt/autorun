---
description: Git worktree lifecycle management — create, list, remove, merge parallel Claude/Gemini sessions in isolated branches
allowed-tools: Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python *)
---

# Git Worktree Management (/cr:wt)

Creates and manages isolated git worktrees with paired tmux windows and AI sessions.
Enables parallel AI agents (Claude or Gemini) working on separate branches simultaneously.

## Subcommands

| Command | Description |
|---------|-------------|
| `/cr:wt new <branch> [-p "prompt"]` | Create git worktree + tmux window + AI session |
| `/cr:wt start [branch]` | Reopen tmux window for existing worktree (resume mode) |
| `/cr:wt ls` | List all git worktrees with HEAD, tmux window, dirty status |
| `/cr:wt rm [branch] [--force]` | Remove git worktree + branch (auto-detects from cwd) |
| `/cr:wt rm --all` | Remove all git worktrees (two-step token confirmation) |
| `/cr:wt merge [branch] [--squash]` | Merge worktree branch into primary |
| `/cr:wt cd [branch]` | Print cd command (no arg = repo root; branch = worktree path) |
| `/cr:wt init [--replace]` | Generate .cmux/setup hook for this repository |
| `/cr:wt config` | Show current git worktree layout |
| `/cr:wt config set layout <preset>` | Set layout: nested, outer-nested, sibling |

## Examples

```
# Create worktree and delegate a task to a new Claude session
/cr:wt new feature-auth -p "Implement JWT auth. See SPEC.md section 3."

# List all git worktrees with status
/cr:wt ls

# Auto-detect current worktree from cwd and merge it
/cr:wt merge

# Remove a worktree (dirty check + clean Claude exit + teardown hook)
/cr:wt rm feature-auth

# Resume an existing worktree session
/cr:wt start feature-auth

# Get the worktree path for scripting
/cr:wt cd feature-auth
```

## Layout Modes

| Layout | Worktree path | Use case |
|--------|--------------|----------|
| `nested` (default) | `{repo}/.worktrees/{branch}` | Keeps worktrees inside repo |
| `outer-nested` | `{parent}/{repo}.worktrees/{branch}` | Keeps repo dir clean |
| `sibling` | `{parent}/{repo}-{branch}` | Sibling directories |

Set layout: `/cr:wt config set layout outer-nested`

## AI→AI Delegation (-p flag)

The `-p` flag sends a task directly to the new AI session:

```
/cr:wt new feature-payments -p "Implement Stripe checkout. Use the existing PaymentService."
```

Sequence:
1. Creates git worktree + tmux window
2. Launches Claude or Gemini in the new window
3. Polls until AI session detected (up to 10s)
4. Sends the task prompt to the AI

The orchestrating AI continues working while the worker AI implements the feature.

## Setup/Teardown Hooks

Create `.cmux/setup` and `.cmux/teardown` in your repo root or worktree directory:

```bash
# .cmux/setup (executable bash script)
#!/bin/bash
npm install           # or: pip install -e . / uv sync / etc.
cp .env.example .env  # copy environment
echo "Setup complete"
```

Run `/cr:wt init` to auto-generate a setup hook based on your project structure.

## Failure Recovery

After a context reset (`/compact` or session restart), the worker AI automatically
receives its task context via the SessionStart hook — no manual re-briefing needed.

## Configuration (Gemini vs Claude)

By default, uses `claude` CLI. To use Gemini:

```json
// .cmux/config.json in your repo root
{"ai_cli": "gemini"}

// or ~/.cmux/config.json for global default
{"ai_cli": "gemini"}
```

## Notes

- **`cd` subcommand**: Prints the path; run `cd` yourself — plugin cannot change shell cwd.
- **Auto-detection**: `rm`, `merge`, `start` auto-detect branch from current directory.
- **Locked worktrees**: `rm` checks for git-locked worktrees and gives clear instructions.
- **Dirty guard**: `rm` refuses to remove worktrees with uncommitted changes (use `--force` to override).
- **`rm --all` safety**: Generates a one-time token (5-min TTL); second call must include `--token`.
