# Autorun Workspace - Gemini CLI

**autorun works identically in both Claude Code and Gemini CLI**, providing unified safety features, commands, and autonomous execution capabilities across both platforms.

**For Claude Code:** See [CLAUDE.md](CLAUDE.md) for Claude Code-specific installation (uses `claude plugin install`).

## Installation (Gemini CLI)

### From GitHub (Production - Recommended)

```bash
# Canonical installer (recommended) — detects Claude Code and Gemini CLI and
# installs for whichever are present.
autorun --install

# Alternative: Gemini-only install direct from GitHub. Because the autorun
# Gemini extension template lives under plugins/autorun/src/autorun/gemini_template/
# (see File Structure below), installing the repo root as a single Gemini
# extension does not succeed — finish the install with `autorun --install`
# which materializes ~/.gemini/extensions/ar/ from the template programmatically.
gemini extensions install https://github.com/ahundt/autorun.git/plugins/pdf-extractor  # pdf-extractor still uses legacy layout
autorun --install  # completes the autorun Gemini extension setup

# Verify
gemini extensions list  # Should show: ar@0.12.0, pdf-extractor@0.12.0
```

### From Local Clone (Development)

```bash
git clone https://github.com/ahundt/autorun.git && cd autorun

# Option 1: UV (recommended - faster, better dependency management)
uv run python -m plugins.autorun.src.autorun.install --install --force

# Option 2: pip fallback (if UV not available)
pip install -e . && python -m plugins.autorun.src.autorun.install --install --force

# REQUIRED: Install as UV tool for global CLI availability (works with both Gemini and Claude)
# This makes 'autorun' and 'claude-session-tools' commands globally available
# which are needed for proper daemon operation and session management
# Useful for: autorun --restart-daemon, autorun --install, autorun --status, etc.
cd plugins/autorun && uv tool install --force --editable .

# Verify installation
gemini extensions list    # Should show: ar@0.12.0, pdf-extractor@0.12.0
autorun --status        # Verifies UV tool installation works
```

**Install UV (if needed):**
```bash
# macOS/Linux:
curl -LsSf https://astral.sh/uv/install.sh | sh

# Homebrew:
brew install uv

# Windows:
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Test Installation

```bash
# Start new Gemini session
gemini

# Test
/ar:st  # Expected: "AutoFile policy: allow-all"
```

### Gemini-Specific Configuration

**IMPORTANT**: Hooks require explicit enablement in Gemini CLI settings (not required for Claude Code).

Edit `~/.gemini/settings.json` and add:

```json
{
  "tools": {
    "enableHooks": true,
    "enableMessageBusIntegration": true
  }
}
```

**Why Required**: Without these settings, autorun hooks will not execute in Gemini CLI. The safety features (command blocking, file policies) depend on hooks.

**Version Requirement**: Gemini CLI v0.28.0 or later recommended.

Update Gemini CLI:
```bash
# Using Bun (faster)
bun install -g @google/gemini-cli@latest

# Or using npm
npm install -g @google/gemini-cli@latest
```

Verify version:
```bash
gemini --version  # Should show 0.28.0 or later
```

## Autorun Integration Benefits

### Safety Features (Works in Both CLIs)

- **File Policies**: Control file creation (`/ar:a`, `/ar:j`, `/ar:f`)
- **Command Blocking**: Prevent dangerous operations (rm, git reset --hard, etc.)
- **Plan Export**: Auto-save plans to notes/ directory
- **Three-Stage Verification**: Ensures thorough task completion
- **Task Tracking**: Monitor task completion across sessions

**Commands work identically in both CLIs:**

```bash
/ar:st              # Show current AutoFile policy
/ar:a               # Allow all file creation
/ar:j               # Justify new files
/ar:f               # Find and modify existing files only (strictest)
/ar:go <task>       # Start autonomous execution
/ar:sos             # Emergency stop
/ar:tasks           # Toggle task staleness reminders on/off or set threshold
/ar:task-status     # Show task lifecycle status and incomplete tasks
/ar:pn              # Create new structured plan
/ar:pr              # Refine existing plan
/ar:pe              # Show plan export status
/ar:no <pattern>    # Block command pattern in session
/ar:ok <pattern>    # Allow blocked command in session
/ar:cache           # Cache-miss / compaction protection gate (off by default)
```

See [README.md](README.md) for the complete command reference.

#### Cache-Miss / Compaction Protection on Gemini CLI

`/ar:cache` works on Gemini CLI with reduced signal fidelity. Gemini does not surface `cache_read_input_tokens` or `cache_creation_input_tokens` to its hook stdin or statusline, and Gemini's JSONL transcript schema does not consistently expose per-message cache tokens. Consequences when enabled on Gemini:

- **Works**: `cache_age_max_seconds` axis (time since last assistant message proxies cache warmth), `compaction_used_max` axis (total-token proxy vs. inferred 2M / 1M context window).
- **Fail-open**: `cache_hit_ratio_min` and `cache_read_tokens_min` axes — Gemini's transcript usually lacks the required fields, so cache_guard returns ALLOW on those axes. `/ar:cache` will print which axes are inactive on your current CLI.
- **PreCompress hook**: autorun wires Gemini's `PreCompress` event (advisory — cannot block) to invalidate the cached usage memo, so the next `BeforeTool` re-reads the transcript after compression.

#### Task Staleness Reminders (v0.9)

Task staleness reminders work identically in both CLIs. When 25+ tool calls pass without TaskCreate/TaskUpdate, autorun injects a reminder. Use `/ar:tasks` to configure.

### Gemini Vision + Autorun Safety

**Use Gemini's superior vision capabilities with autorun's safety guards active:**

#### Image Analysis with File Policy Control

```bash
# Set strict policy: only modify existing files
/ar:f

# Analyze UI mockup and generate code (respects policy)
gemini -i notes/ui_mockup.png -c "Convert this mockup to React components"

# Autorun ensures:
# - No new files created (policy: SEARCH mode)
# - Suggests modifying existing components
# - Blocks dangerous bash commands
```

#### Architecture Diagram Analysis

```bash
# Analyze architecture diagram and implement
gemini -i notes/architecture.png -c "Implement the service layer shown in this diagram"

# Autorun provides:
# - File creation control via /ar:j (requires justification)
# - Command blocking (prevents accidental destructive operations)
# - Plan export (auto-saves implementation plan)
```

#### Video & Audio Analysis

Gemini supports video (1 FPS sampling) and audio (transcription + diarization) with autorun safety:

```bash
# Video UX analysis with safety
gemini -m gemini-3-pro-preview "Analyze notes/demo_video.mp4 for UX issues"
# Safety: File policies and command blocking remain active

# Audio transcription with safety
gemini -m gemini-3-pro-preview "Transcribe notes/meeting.wav and extract action items"
# Safety: Generated code/commands respect policy settings
```

### Multi-Model Workflows

**Use both CLIs together with shared safety features:**

#### Pattern 1: Claude Implements, Gemini Reviews

```bash
# Step 1: Claude implements feature
claude
/ar:go "Implement user authentication system with tests"

# Step 2: Gemini reviews with vision (analyze docs + code)
gemini
"Review the authentication code in src/auth.py for security issues.
Also analyze docs/auth_flow.png to verify implementation matches design."

# Both sessions use autorun safety:
# - Same file policies
# - Same command blocking rules
# - Isolated session state (no interference)
```

#### Pattern 2: Gemini Researches, Claude Implements

```bash
# Step 1: Gemini researches with web search
gemini
"Search for best practices in OAuth 2.0 implementation and security patterns"

# Step 2: Claude implements with research context
claude
/ar:go "Implement OAuth 2.0 flow following the patterns from Gemini's research"

# Safety: Both CLIs enforce same policies throughout workflow
```

#### Pattern 3: Iterative Cross-Review

```bash
# Round 1: Claude creates implementation
claude
/ar:go "Build REST API with authentication"

# Round 2: Gemini reviews
gemini
"Review API endpoints for security, provide BEFORE/AFTER code for issues"
# Save output to notes/round1_review.json

# Round 3: Claude fixes issues
claude
"Fix the security issues found by Gemini in round1_review.json"

# Round 4: Gemini re-reviews
gemini -r "$SESSION_ID"  # Resume session for context
"Re-check API after fixes. Any remaining issues?"
```

### Session Isolation

**Claude and Gemini sessions don't interfere with each other:**

- Separate session IDs and state
- Independent policy settings (can run `/ar:f` in one, `/ar:a` in other)
- Shared hook daemon (efficient, same enforcement logic)
- No state leakage between CLIs

### PDF Extraction (Both CLIs)

The pdf-extractor plugin works identically in both:

```bash
# Extract PDF to markdown (same command in both CLIs)
/pdf-extractor:extract document.pdf

# Use with Gemini vision for comparison
gemini -i notes/scanned_doc.png "Extract text from this image"
# Then compare Gemini OCR vs pdf-extractor output
```

## Technical Details

### Entry Points

- **Hooks**: `hooks/hook_entry.py` — shared handler for both CLIs (configured via `hooks/gemini-hooks.json` for Gemini)
- **CLI**: `autorun` command — UV tool entry point at `src/autorun/__main__.py:main`
- **Config**: `src/autorun/config.py` — single source of truth for all CONFIG values

### Hook System

Autorun uses the same hook scripts for both CLIs:

- **Claude Code**: Uses `hooks.json` with PreToolUse, PostToolUse, SessionStart, Stop events
- **Gemini CLI**: Uses `gemini-hooks.json` with BeforeTool, AfterTool, SessionStart, SessionEnd events
- **Same Python script**: `hook_entry.py` detects which CLI is calling (backwards compatible)
- **Event normalization**: Gemini events mapped to Claude equivalents internally

### Environment Variables

The hook system works because Gemini CLI provides:

- `CLAUDE_PROJECT_DIR`: Aliased from `GEMINI_PROJECT_DIR` (per Gemini docs)
- `GEMINI_SESSION_ID`: Used to detect Gemini CLI context
- Compatible JSON-over-STDIN protocol

### File Structure

```
autorun/
├── GEMINI.md                              # This file
└── plugins/
    ├── autorun/
    │   ├── .claude-plugin/plugin.json     # Plugin manifest for Claude
    │   ├── hooks/
    │   │   ├── hooks.json                 # Claude event hooks (default path)
    │   │   └── hook_entry.py              # Shared hook handler (both CLIs)
    │   ├── src/autorun/
    │   │   └── gemini_template/           # Gemini extension template
    │   │       ├── gemini-extension.json  # Gemini manifest (materialized at install)
    │   │       └── hooks/hooks.json       # Gemini event hooks (Gemini-only events)
    │   └── commands/                      # Shared commands (both CLIs)
    └── pdf-extractor/
        ├── gemini-extension.json          # Plugin manifest for Gemini (legacy layout)
        ├── .claude-plugin/plugin.json     # Plugin manifest for Claude
        └── commands/                      # Shared commands (both CLIs)
```

**Why the template lives under `src/autorun/`:** Claude Code's plugin loader
([bug #24115](https://github.com/anthropics/claude-code/issues/24115)) scans
`plugins/autorun/hooks/` from BOTH the installed cache AND the marketplace
source directory. Gemini-only event names (`BeforeTool`, `BeforeAgent`, etc.)
in that path fail Claude's strict Zod schema (`invalid_key`). Putting Gemini
assets under `src/autorun/gemini_template/` keeps them out of Claude's scan
path while still shipping them in the repo. The installer programmatically
materializes `~/.gemini/extensions/ar/` from the template and copies
`hook_entry.py` into place so `${extensionPath}/hook_entry.py` resolves.

## Gemini-Specific Command Reference

For comprehensive Gemini CLI usage (models, session management, output formats, etc.), see:

```bash
/ar:gemini  # Display full Gemini CLI reference guide
```

Key Gemini capabilities that complement autorun:

- **Vision**: Superior image/diagram/screenshot analysis
- **Video**: 1 FPS sampling for UX analysis (use slow-motion for high-speed bugs)
- **Audio**: Transcription with speaker diarization and sentiment
- **Web Search**: Google search integration for research
- **Context**: 2M tokens (hours of video or massive codebases)
- **Session Resume**: Continue sessions with `gemini -r "$SESSION_ID"` (preserves context, avoids re-uploading)

## Installation Notes

### Single Install Command

```bash
# Automatic detection and installation
autorun --install

# Detects both CLIs and installs for whichever are present:
# ✅ Claude Code: Installs via `claude plugin install`
# ✅ Gemini CLI: Installs via `gemini extensions install`
```

### Manual Installation

**Claude Code:**
```bash
cd /path/to/autorun
claude plugin install .
claude plugin list  # Verify: ar@0.12.0, pdf-extractor@0.12.0
```

**Gemini CLI:**
```bash
cd /path/to/autorun
gemini extensions install .
gemini extensions list # Verify: ar@0.12.0, pdf-extractor@0.12.0

```

### Verification

Test in both CLIs:

```bash
# Claude Code
claude
/ar:st  # Expected: "AutoFile policy: allow-all"
/ar:f   # Set strict policy
"Create test.txt"  # Expected: Blocked

# Gemini CLI
gemini
/ar:st  # Expected: Same policy display
/ar:a   # Allow files (independent of Claude session)
"Create test2.txt"  # Expected: Created
```

## Troubleshooting

### Hooks Not Firing in Gemini

```bash
# Check extension enabled
cat ~/.gemini/settings.json
# Should include: {"extensions": {"ar": {"enabled": true}}}

# Check hook registration
gemini --verbose  # Shows hook execution in logs
```

### Tool Name Mismatches

If hooks don't fire on specific tools, tool names may differ:

```bash
# List actual Gemini tool names
gemini --list-tools

# Update matchers in: plugins/autorun/hooks/gemini-hooks.json
# Example: "write_file" vs "Write" in Claude Code
```

### State Conflicts

If sessions interfere unexpectedly:

```bash
# Check daemon logs
cat ~/.autorun/daemon.log | tail -50

# Verify CLI detection
grep "detect_cli_type" ~/.autorun/daemon.log

# Should show correct CLI type for each session
```

## See Also

- [README.md](README.md) - Full autorun documentation
- [plugins/autorun/commands/gemini.md](plugins/autorun/commands/gemini.md) - Comprehensive Gemini CLI reference
- [Gemini CLI Docs](https://geminicli.com/docs/) - Official documentation
- [Gemini Hooks Reference](https://geminicli.com/docs/hooks/reference/) - Hook system technical details

## Bug Workaround Policy

> These workarounds are auto-detected and only activate for Claude Code. Gemini CLI is unaffected.

All SDK bug workarounds (Claude Code, Gemini CLI, future CLIs) **MUST** follow all of the following:

**Flag** — MUST use ONE key as both env var and CONFIG dict entry:
1. Format: `AUTORUN_BUG_<DESCRIPTIVE_NAME>_BUG_<NUMBER>_WORKAROUND_ENABLED`
2. Lookup: env var → CONFIG dict → default `True`
3. Values: `true`/`1`/`auto` (affected platform) · `always` (all) · `false`/`0`/`never` (off)

**Code** — MUST be a self-contained removable unit, invisible to callers:
1. One bracketed helper function (`# --- BUG #N WORKAROUND START/END --- DELETE WHEN FIXED ---`) with one call site (one-line)
2. Helper checks env → CONFIG → `cli_type` (via `detect_cli_type()`, never hardcoded); no-op on unaffected platforms
3. Sets both workaround AND designed output (e.g. `systemMessage` AND `additionalContext`) so designed field is ready when bug is fixed
4. Preserves `respond()` print guards: `reason=""` when `systemMessage` set (anti-double-print); `reason=""`+`systemMessage=""` on PreToolUse deny (anti-triple-print with stderr)
5. Only uses fields in `HOOK_SCHEMAS` for the event type (`validate_hook_response()` strips others)
6. Every affected site has: bug number, full issue link, description, disable key, deletion instruction
7. Removal: delete helper (START→END) + replace call with designed-behavior literal

**Tests** — MUST have a self-contained removable test block:
1. Bracketed `# --- BUG #N TESTS START/END ---` with shared `_BUG_FLAG` constant
2. Pass with flag True AND False; cover: affected+enabled, affected+disabled, unaffected, env=always, env=never
3. No non-bug test depends on these — delete block when fixed

**When fixed**: set `False` (quick) or delete helper, replace call with literal, delete CONFIG key + test block (cleanup). Defense-in-depth handlers remain.

| Bug | Platform | Key | Default | Effect |
|-----|----------|-----|---------|--------|
| [#4669](https://github.com/anthropics/claude-code/issues/4669): deny ignored at exit 0 | Claude Code | `AUTORUN_EXIT2_WORKAROUND` (legacy) | `auto` | stderr + exit 2 |
| [#18534](https://github.com/anthropics/claude-code/issues/18534): additionalContext dropped | Claude Code | `AUTORUN_BUG_CLAUDE_CODE_IGNORES_ADDITIONAL_CONTEXT_JSON_ENTRY_BUG_18534_WORKAROUND_ENABLED` | `True` | channel="ai" → "both" |
