# Autorun Workspace - Gemini CLI

**autorun works identically in both Claude Code and Gemini CLI**, providing unified safety features, commands, and autonomous execution capabilities across both platforms.

**For Claude Code:** See [CLAUDE.md](CLAUDE.md) for Claude Code-specific installation (uses `claude plugin install`).

## Installation (Gemini CLI)

### From GitHub (Production - Recommended)

```bash
# Install directly via Gemini extension system
gemini extensions install https://github.com/ahundt/autorun.git

# Verify
gemini extensions list  # Should show: ar@0.9.0, pdf-extractor@0.9.0
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
gemini extensions list    # Should show: ar@0.9.0, pdf-extractor@0.9.0
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
```

See `/ar:help` or [README.md](README.md) for complete command reference.

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
├── gemini-extension.json          # Workspace-level Gemini manifest
├── GEMINI.md                       # This file
└── plugins/
    ├── autorun/
    │   ├── gemini-extension.json          # Plugin manifest for Gemini
    │   ├── .claude-plugin/plugin.json     # Plugin manifest for Claude
    │   ├── hooks/
    │   │   ├── gemini-hooks.json          # Gemini event hooks
    │   │   ├── hooks.json                 # Claude event hooks
    │   │   └── hook_entry.py              # Shared hook handler (both CLIs)
    │   └── commands/                      # Shared commands (both CLIs)
    └── pdf-extractor/
        ├── gemini-extension.json          # Plugin manifest for Gemini
        ├── .claude-plugin/plugin.json     # Plugin manifest for Claude
        └── commands/                      # Shared commands (both CLIs)
```

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
claude plugin list  # Verify: ar@0.9.0, pdf-extractor@0.9.0
```

**Gemini CLI:**
```bash
cd /path/to/autorun
gemini extensions install .
gemini extensions list # Verify: ar@0.9.0, pdf-extractor@0.9.0

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
