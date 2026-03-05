---
name: ai-session-tools
description: Search, recover, and analyze sessions from Claude Code, AI Studio, and Gemini CLI using the aise CLI — find files Claude wrote, restore context after compaction, search conversation history, or understand what happened in any past session.
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

# AI Session Tools

Search, analyze, and recover anything from AI session histories (Claude Code, AI Studio, Gemini CLI). Use when the user needs to find past work, understand what Claude did in a session, recover a file, search conversation history, or detect patterns in how Claude is being used.

**Invoke with:** `/ar:ai-session-tools` or natural language like "find that file from last week's session", "what did Claude do in session ab841016", "search my Claude sessions for authentication"

**Tool:** `aise` -- run `aise --help` to see all commands

---

## What You Can Do

After a context compaction, a lost file, or a confusing session — `aise` finds it.
In under a minute you can recover a file Claude wrote, search every conversation you've
ever had, or export a full session to markdown. Works across Claude Code, AI Studio,
and Gemini CLI sessions simultaneously.

---

## Goal -> Command Quick Reference

| Goal | Command |
|------|---------|
| Show session, file, and version counts | `aise stats` |
| Find all sessions | `aise list` |
| Find sessions for a project | `aise list --project myproject` |
| Filter by source | `aise list --provider claude` |
| Read all messages from a session | `aise messages get SESSION_ID` |
| Read only your (user) messages | `aise messages get SESSION_ID --type user` |
| See recent prompts you sent to Claude | `aise messages search "" --type user --since 7d` |
| Search text across all sessions | `aise messages search "query"` |
| See what tools Claude used | `aise messages inspect SESSION_ID` |
| See chronological session events | `aise messages timeline SESSION_ID` |
| Find a file Claude wrote or edited | `aise files search --pattern "name.py"` |
| Find heavily-edited files | `aise files search --min-edits 2` |
| See version history of a file | `aise files history filename.py` |
| Recover a file to stdout | `aise files extract filename.py` |
| Find all Write/Edit calls to a file | `aise tools search Write "filename"` |
| Check if edits appear in current file | `aise files cross-ref ./path/to/file` |
| Find user corrections to Claude | `aise messages corrections` |
| Count planning command usage | `aise messages planning` |
| Get clipboard content from a session | `aise messages extract SESSION_ID pbcopy` |
| Export session to markdown | `aise export session SESSION_ID` |
| Export last N days of sessions | `aise export recent 7 --output week.md` |
| Search files + messages together | `aise search --pattern "*.py" --query "error"` |
| Find specific tool invocations | `aise tools search Bash "git commit"` |
| List configured session sources | `aise source list` |
| Scan for new sources (dry-run) | `aise source scan` |
| Scan and save found sources | `aise source scan --save` |
| Add an AI Studio source directory | `aise source add aistudio /path/to/dir` |
| Remove a source by path | `aise source remove /path/to/dir` |
| Run full analysis pipeline | `aise analyze` |
| Show date format reference | `aise dates` |

---

## Goal-Oriented Sequences

### "Recover context after context compaction"

This is the most common use of `aise` inside Claude Code. When context was compacted
and you need to restore what you were working on:

```bash
# 1. Find recent sessions (last 7 days, newest first)
aise list --since 7d

# 2. Read the conversation to restore context
aise messages get SESSION_ID

# 2b. Get only your (user) messages for a quick overview of what you asked
aise messages get SESSION_ID --type user

# 3. Find files Claude was working on in that session
aise files search --include-sessions SESSION_ID

# 4. Recover a specific file back to its original location
aise files extract filename.py --restore

# 5. Export session to markdown for persistent reference
aise export session SESSION_ID --output session-context.md
```

Full context restored in under 2 minutes.

---

### "See what you've been asking Claude recently"

```bash
# Your prompts from the last 3 days (across all projects)
aise messages search "" --type user --since 3d

# Narrow to a specific project
aise messages search "" --type user --since 7d --project myproject

# Search for a specific topic in your past prompts
aise messages search "authentication" --type user
```

---

### "Find and recover a file from a past session"

```bash
# 1. Find which sessions touched the file
aise files search --pattern "filename.py"

# 2. See version history (how many times it was edited, which sessions)
aise files history filename.py

# 3. Preview a specific version
aise files extract filename.py --version 3

# 4. Recover latest version to disk
aise files extract filename.py > filename.py
```

---

### "Understand what happened in session X"

```bash
# 1. Get session list to find the right ID
aise list --project myproject

# 2. Read the conversation
aise messages get ab841016

# 3. See statistics: tool counts, files touched
aise messages inspect ab841016

# 4. See chronological event timeline
aise messages timeline ab841016
```

---

### "Search for when something was discussed"

```bash
# Search all sessions for a phrase
aise messages search "authentication bug"

# Search only user messages
aise messages search "authentication" --type user

# Search with surrounding context
aise messages search "authentication" --context 3

# Search across files AND messages
aise search --query "authentication" --pattern "*.py"
```

---

### "Verify that Claude's edits are in the current file"

```bash
# Show each Edit/Write call Claude made to the file
# and mark (found in current file) or (missing)
aise files cross-ref ./path/to/file.md

# Limit to one session
aise files cross-ref ./cli.py --session ab841016

# JSON output to process programmatically
aise files cross-ref ./engine.py --format json
```

---

### "Find a specific tool use (Bash, Edit, Write, Read...)"

```bash
# All Write calls across all sessions
aise tools search Write

# Bash calls containing a pattern
aise tools search Bash "git commit"

# Edit calls targeting a file
aise tools search Edit "cli.py"

# Via root search with --tool flag
aise search --tool Read --query "engine.py"
```

---

### "Export sessions for documentation or review"

```bash
# Export one session to stdout, redirect to file
aise export session ab841016 > session.md

# Export to an explicit file
aise export session ab841016 --output notes/session.md

# Preview without writing
aise export session ab841016 --dry-run

# Export last 7 days of sessions
aise export recent 7 --output weekly.md

# Filtered by project
aise export recent 14 --project myproject --output sprint.md
```

---

### "Understand patterns in Claude usage"

```bash
# Find messages where you corrected Claude
# Categories: regression, skip_step, misunderstanding, incomplete, other
aise messages corrections

# Filter by project or date
aise messages corrections --project myproject --since 2026-01-01

# Count planning command usage (/ar:plannew, /ar:planrefine, etc.)
aise messages planning

# Get clipboard content Claude prepared in a session
aise messages extract ab841016 pbcopy
```

---

### "Run the full analysis pipeline"

```bash
# Run all stages: qualitative coding -> graph -> taxonomy symlinks
aise analyze

# Check which stages are stale without running
aise analyze --status

# Force re-run all stages
aise analyze --force

# Analyze only one provider
aise analyze --provider aistudio
aise analyze --provider gemini

# Run a specific stage
aise analyze --step analyze
aise analyze --step graph
```

---

## All Commands Reference

### Global Options
```bash
aise --version                               # show version
aise --provider claude COMMAND               # filter to one source: claude | aistudio | gemini | all
aise --claude-dir /path COMMAND              # override ~/.claude location
aise --config /path/config.json COMMAND      # override config file
```

### Session Discovery
```bash
aise list                                    # all sessions, newest first
aise list --project myproject                # filter by project
aise list --provider claude                  # filter by source
aise list --since 2026-01-01                 # sessions on or after date
aise list --until 2026-02-01                 # sessions on or before date
aise list --limit 20                         # cap results
aise list --format json                      # machine-readable
aise list --full-uuid                        # show complete 36-char session UUIDs
```

### Message Search & Reading
```bash
aise messages search "query"                 # search all sessions
aise messages search "query" --type user     # user messages only
aise messages search "query" --type assistant
aise messages search "query" --context 3     # N messages before/after
aise messages search "query" --limit 20
aise messages search "query" --format json
aise messages search "query" --full-uuid     # full UUIDs in output
aise messages get SESSION_ID                 # read full session
aise messages get SESSION_ID --type user
aise messages get SESSION_ID --limit 10
```

### Session Analysis
```bash
aise messages inspect SESSION_ID             # tool counts, files touched
aise messages inspect SESSION_ID --format json
aise messages timeline SESSION_ID            # chronological events
aise messages timeline SESSION_ID --preview-chars 80
aise messages timeline SESSION_ID --since 14:00  # events from 2pm onwards (time-of-day)
aise messages corrections                    # user corrections to Claude
aise messages corrections --project myproject
aise messages corrections --limit 50
aise messages corrections --full-uuid        # full UUIDs in output
aise messages corrections --pattern 'LABEL:REGEX'  # custom correction category
aise messages planning                       # planning command frequency
aise messages planning --commands '/custom,/cmd'   # count custom command patterns
aise messages extract SESSION_ID pbcopy      # clipboard content
aise messages extract SESSION_ID pbcopy --format json
```

### File Recovery
```bash
aise files search                            # all files Claude touched
aise files search --pattern "*.py"           # by glob pattern
aise files search --include-extensions py ts # by extension
aise files search --min-edits 5              # heavily-edited files
aise files search --include-sessions ID      # files in one session
aise files history filename.py               # version history
aise files history filename.py --export      # export all versions as cli_v1.py, cli_v2.py, ...
aise files history filename.py --stdout      # all versions to stdout (pipe-friendly)
aise files extract filename.py               # latest version -> stdout
aise files extract filename.py --version 2   # specific version
aise files extract filename.py --restore     # write back to original path on disk
aise files extract filename.py --output-dir ./backup  # write to backup directory
aise files cross-ref ./file.py               # verify edits in current file
aise files cross-ref ./file.py --session ID

# Top-level shortcuts (same as files subcommands):
aise extract filename.py                     # latest version -> stdout
aise history filename.py                     # version history
```

### Tool Call Search
```bash
aise tools search Write                      # all Write calls
aise tools search Bash "git"                 # Bash calls matching pattern
aise tools search Edit "filename"            # Edit calls
aise tools search Read                       # all Read calls
aise tools search Write --format json
aise tools search Write --limit 20
```

### Export
```bash
aise export session SESSION_ID               # -> stdout
aise export session SESSION_ID --output f.md # -> file
aise export session SESSION_ID --dry-run     # preview
aise export recent 7                         # last 7 days -> stdout
aise export recent 7 --output week.md
aise export recent 14 --project myproject --output sprint.md
```

### Combined Search
```bash
aise search                                  # default: all files
aise search --query "error"                  # auto-routes to messages
aise search --pattern "*.py"                 # auto-routes to files
aise search --pattern "*.py" --query "error" # both: files + messages
aise search --tool Write --query "login"     # tool search with query
aise search tools --tool Bash --query "git"  # explicit tools domain
aise find messages --query "error"           # find = alias for search
aise find files --pattern "*.py"
```

### Statistics
```bash
aise stats                                   # session + file counts
aise stats --since 7d                        # filtered to last 7 days
aise stats --since 2026-01-01 --until 2026-03-31
aise stats --provider claude                 # one source only
```

### Analysis Pipeline
```bash
aise analyze                                 # full pipeline: coding -> graph -> taxonomy
aise analyze --status                        # show which stages are stale/current
aise analyze --force                         # force re-run all stages
aise analyze --provider aistudio             # one source only
aise analyze --step analyze                  # run one stage
aise analyze --step graph
aise analyze --window N                      # rolling window size for era detection
aise analyze --org-dir /path                 # custom output directory for analysis artifacts
aise analyze --format json                   # machine-readable analysis output
```

### Source Management
```bash
aise source list                             # show configured sources
aise source scan                             # dry-run: show discoverable sources not yet in config
aise source scan --save                      # scan and write found paths to config
aise source add aistudio /path/to/dir        # add AI Studio source directory
aise source add gemini /path/to/dir          # add Gemini CLI source directory
aise source remove /path/to/dir             # remove a source by path
aise source disable claude                   # disable auto-discovery for a type
aise source enable claude                    # re-enable auto-discovery
```

### Configuration
```bash
aise config show                             # view full config + resolved path
aise config path                             # print config file path
aise config init                             # create default config file
aise dates                                   # show full date format reference
```

---

## Date Filtering

All commands that accept `--since`/`--until` support multiple formats:

```bash
# ISO dates
aise list --since 2026-01-01
aise list --since 2026-01-01 --until 2026-03-31

# Duration shorthands
aise list --since 7d                         # last 7 days
aise list --since 2w                         # last 2 weeks
aise list --since 1m                         # last month

# EDTF intervals (single --since sets both bounds)
aise list --since 2026-01/2026-03            # Q1 2026
aise list --when 202X                        # entire decade

# --after/--before are accepted as hidden aliases for --since/--until

# Show full reference
aise dates
```

---

## Output Formats

All commands support `--format` / `-f`:

| Format | Use When |
|--------|----------|
| `table` | Default -- human-readable in terminal |
| `json` | Scripting, piping to `jq`, programmatic use |
| `csv` | Spreadsheet import |
| `plain` | Raw text, minimal formatting |

---

## Multi-Source Support

`aise` reads from Claude Code, AI Studio, and Gemini CLI sessions simultaneously.

```bash
# Show all sessions across all configured sources
aise list

# Filter to one source
aise list --provider claude
aise list --provider aistudio
aise list --provider gemini

# Add additional source directories
aise source add aistudio ~/Downloads/ai-studio-exports
aise source scan --save                      # auto-detect and save new sources
aise source list
```

Claude Code sessions are auto-detected from `~/.claude/projects/`. AI Studio and Gemini CLI paths must be configured with `aise source add` or in the config file.

---

## Session File Structure

Sessions live at `~/.claude/projects/<ENCODED-PATH>/<SESSION-ID>.jsonl`

Path encoding: non-alphanumeric characters -> `-`
- `/Users/alice/myproject` -> `-Users-alice-myproject`
- `/Users/alice/.claude` -> `-Users-alice--claude` (dot -> dash)
- `/home/alice/project` -> `-home-alice-project`

Each JSONL line is a JSON object with `type` (`user`/`assistant`/`system`), `timestamp`, and `message` containing tool calls and text content.

---

## Configuration

Config file location (priority order):
1. `--config` CLI flag
2. `AI_SESSION_TOOLS_CONFIG` env var
3. OS default: `~/Library/Application Support/ai_session_tools/config.json` (macOS) or `~/.config/ai_session_tools/config.json` (Linux)

```bash
aise config show                             # view current config
aise config path                             # print path (even if file doesn't exist)
aise config init                             # create starter config
```
