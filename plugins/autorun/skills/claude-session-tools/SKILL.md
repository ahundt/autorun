---
name: claude-session-tools
description: This skill should be used when the user asks to "find Claude sessions", "explore session history", "search Claude sessions", "analyze session", "list sessions", "extract session content", "export Claude sessions", "find tool usage in sessions", "session timeline", "recover a file", "find a file from a previous session", "recover code from a session", mentions "Claude Code session.jsonl files", or needs to explore, search, analyze, export, or recover files and code from Claude Code session histories stored in ~/.claude/projects/.
version: "0.9.0"

# VISIBILITY & TRIGGERING
user-invocable: true
disable-model-invocation: false

# EXECUTION CONTEXT - Tools this skill can use
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
---

# Claude Session Tools

Use `aise` to explore, search, analyze, export, and recover files and code from Claude Code session histories in `~/.claude/projects/`. All commands below are `aise` subcommands.

**When you don't know the session ID**, always start with `aise list` to identify it.
**When you need full content**, add `--format json` and pipe to `jq` or read raw output.
**Prefer the most targeted command** — `aise tools search Edit` is faster than searching all messages.

---

## Command Reference

| Command | When to use |
|---------|-------------|
| `aise list` | Orient: find sessions by project, date, branch |
| `aise files search` | Find which files Claude wrote or edited across sessions |
| `aise files history <name>` | See all recorded versions of a specific file |
| `aise files extract <name>` | Get the content of a file version (pipe to restore it) |
| `aise files cross-ref <path>` | Check which session edits are present in the current file |
| `aise messages search <query>` | Full-text search across all user+assistant messages |
| `aise messages get <session-id>` | Read all messages from one session chronologically |
| `aise messages extract <session-id> pbcopy` | Get clipboard content piped via heredoc in that session |
| `aise messages analyze <session-id>` | Stats: message counts, tool usage counts, files touched |
| `aise messages timeline <session-id>` | Chronological event view with content previews |
| `aise messages corrections` | Find messages where the user corrected Claude |
| `aise messages planning` | Count planning command usage (`/ar:plannew` etc.) |
| `aise tools search <Tool> [query]` | Find specific tool invocations (Edit, Bash, Write, Read…) |
| `aise export session <session-id>` | Render one session as a markdown document |
| `aise export recent [days]` | Bulk export last N days of sessions to markdown |
| `aise search` | Cross-domain: files + messages + tools in one command |
| `aise stats` | Aggregate counts: sessions, files, versions, most-edited |

**Output formats** (all commands): `--format table` (default) | `--format json` | `--format csv` | `--format plain`

---

## Goal: Recover a lost or overwritten file

Use when the user says "I lost a file", "Claude deleted something", or "find the old version of X".

```bash
# 1. Find the file — see all sessions that touched it, with edit counts
aise files search --pattern "filename.py"

# 2. See every saved version with line counts and timestamps
aise files history filename.py

# 3. Preview the content of a specific version
aise files extract filename.py --version 3

# 4. Restore the latest version to disk
aise files extract filename.py > filename.py

# 5. If you know it was in a specific session, filter to confirm
aise files search --pattern "filename.py" --include-sessions SESSION_ID
```

---

## Goal: Find what was done in a specific session

Use when the user wants to understand what happened in a session — what was discussed, what was built, what tools were used.

```bash
# 1. Identify the session (if you don't have the ID)
aise list --project myproject
aise list --after 2026-01-20

# 2. See high-level stats: how many messages, which tools, which files
aise messages analyze SESSION_ID

# 3. Read the full conversation in order
aise messages get SESSION_ID

# 4. View a chronological timeline with content previews
aise messages timeline SESSION_ID

# 5. See every Edit and Write call to understand what was changed
aise tools search Edit --limit 20
aise tools search Write --limit 20

# 6. Get what was copied to the clipboard
aise messages extract SESSION_ID pbcopy
```

---

## Goal: Verify that a change was applied to a file

Use when the user asks "did that edit actually get applied?" or "is the session content in the current file?".

```bash
# 1. Run cross-reference — shows each Edit/Write call as ✓ (found) or ✗ (missing)
aise files cross-ref ./path/to/file.py

# 2. Narrow to a specific session if needed
aise files cross-ref ./path/to/file.py --session SESSION_ID

# 3. Get full detail in JSON to inspect specific snippets
aise files cross-ref ./path/to/file.py --format json

# 4. If edits are missing, extract the session version of the file to compare
aise files history filename.py
aise files extract filename.py --version N
```

---

## Goal: Search for when something was discussed

Use when the user asks "when did we talk about X", "find the session where we decided Y", or "what did I tell Claude about Z".

```bash
# 1. Search all messages for the topic
aise messages search "authentication"

# 2. See surrounding conversation for context
aise messages search "authentication" --context 3

# 3. Narrow to user messages only (what YOU said)
aise messages search "authentication" --type user

# 4. Search for a specific tool invocation with that topic
aise tools search Bash "authentication"
aise tools search Edit "auth"

# 5. Once you have a session ID, read the full surrounding conversation
aise messages get SESSION_ID --limit 30
```

---

## Goal: Find specific tool usage across sessions

Use when the user wants to see every time Claude ran a particular command, edited a particular file, or used a particular tool.

```bash
# Find all Bash calls with a pattern
aise tools search Bash "git commit"
aise tools search Bash "pytest"

# Find all edits to files matching a name
aise tools search Edit "cli.py"
aise tools search Write "config"

# Find all Read calls — useful to see what Claude was reading
aise tools search Read

# Get JSON for deeper inspection
aise tools search Edit "engine.py" --format json

# Cross-domain: search messages AND filter to tool calls
aise messages search "login" --tool Edit
```

---

## Goal: Analyze patterns in your AI usage

Use when the user wants to understand their own habits: what they correct, how they plan, how heavily they use sessions.

```bash
# Find messages where you corrected Claude's behavior
aise messages corrections
aise messages corrections --project myproject --limit 50

# See which correction categories appear most (regression/skip_step/misunderstanding/incomplete/other)
aise messages corrections --format json | jq 'group_by(.category) | map({category: .[0].category, count: length})'

# Count how often you use planning commands
aise messages planning
aise messages planning --project myproject

# See overall session and file statistics
aise stats
```

---

## Goal: Export session history for documentation or handoff

Use when the user wants a markdown document of a session, or to archive recent work.

```bash
# Export one session to stdout (redirect to save)
aise export session SESSION_ID > notes/session-2026-01-24.md

# Export to an explicit file
aise export session SESSION_ID --output notes/session.md

# Preview what would be written without actually writing
aise export session SESSION_ID --dry-run

# Export all sessions from the last 7 days
aise export recent 7 --output docs/weekly-work.md

# Export recent sessions for one project only
aise export recent 14 --project myproject --output docs/sprint.md

# Append to an existing notes file
aise export session SESSION_ID >> notes/investigation.md
```

Export content: session metadata (date, branch, cwd), all user+assistant messages, compact summaries — system noise filtered out.

---

## Goal: Find sessions by project, date, or branch

```bash
# All sessions, newest first
aise list

# Filter by project name (substring match on encoded dir)
aise list --project autorun
aise list --project ai_session_tools

# Filter by date range
aise list --after 2026-01-15
aise list --before 2026-02-01
aise list --after 2026-01-15 --before 2026-02-01

# JSON for scripting (session IDs, timestamps, branch names)
aise list --format json

# Get just the session ID of the most recent session for a project
aise list --project myproject --format json --limit 1 | jq -r '.[0].session_id'
```

---

## Arguments

This command accepts arguments for automated operations:

```
$ARGUMENTS
```

---

## Session File Structure

Sessions are stored as `.jsonl` files at `~/.claude/projects/<ENCODED-PATH>/<SESSION-ID>.jsonl`.

The `<ENCODED-PATH>` is the project directory path with non-alphanumeric/non-hyphen characters replaced by `-`:
- macOS: `/Users/<user>/project` → `-Users-<user>-project`
- macOS: `/Users/<user>/.claude` → `-Users-<user>--claude` (`.` → `-`)
- Linux: `/home/<user>/project` → `-home-<user>-project`

Each JSONL line is a JSON object with `type` (`user`/`assistant`/`system`), `timestamp`, and `message.content` (array of `text`, `tool_use`, `tool_result` blocks).

Common tool names in `tool_use` blocks: `Bash`, `Read`, `Edit`, `Write`, `Grep`, `Glob`, `TodoWrite`, `Task`.

---

## Configuration

Override the projects directory (default: `~/.claude/projects`):

```bash
export AI_SESSION_TOOLS_PROJECTS=/path/to/projects
```
