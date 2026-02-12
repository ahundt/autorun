# clautorun Marketplace

UV workspace containing 2 Claude Code plugins: **clautorun**, **pdf-extractor**.

## Installation

### From GitHub (Production - Recommended)

```bash
# Install directly via Claude Code plugin system
claude plugin install https://github.com/ahundt/clautorun.git

# Verify
claude plugin list  # Should show: cr, pdf-extractor
```

### From Local Clone (Development)

```bash
git clone https://github.com/ahundt/clautorun.git && cd clautorun

# Option 1: UV (recommended - faster, better dependency management)
uv run python -m plugins.clautorun.src.clautorun.install --install --force-install

# Option 2: pip fallback (if UV not available)
pip install -e . && python -m plugins.clautorun.src.clautorun.install --install --force-install

# Optional: Install as UV tool for global CLI availability
cd plugins/clautorun && uv tool install --force --editable .
# This makes 'clautorun', 'clautorun-install', 'claude-session-tools' globally available

# Verify
claude plugin list  # Should show: cr, pdf-extractor
clautorun --status  # If installed as UV tool
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
# In Claude Code session:
/cr:st  # Expected: "AutoFile policy: allow-all"
```

## Quick Start

```bash
/cr:go <task>     # Start autonomous execution with three-stage verification
/cr:sos           # Emergency stop
/cr:st            # Show current status
```

## Plugins Overview

| Plugin | Prefix | Purpose |
|--------|--------|---------|
| **clautorun** | `/cr:` | Autonomous execution, file policies, safety guards, plan export |
| **pdf-extractor** | `/pdf-extractor:` | Extract text from PDFs (9 backends, GPU support) |

---

## clautorun Plugin (v0.8.0)

### Three-Stage Verification System

Ensures thorough task completion through mandatory stages:

| Stage | Purpose | Completion Marker |
|-------|---------|-------------------|
| **Stage 1** | Initial implementation | `AUTORUN_STAGE1_COMPLETE` |
| **Stage 2** | Critical evaluation - identify gaps, fix issues | `AUTORUN_STAGE2_COMPLETE` |
| **Stage 3** | Final verification - all requirements met | `AUTORUN_STAGE3_COMPLETE` |

**Concrete Example:**
```
User: /cr:go Add login form with validation and tests

Stage 1: Implements login form → outputs AUTORUN_STAGE1_COMPLETE
Stage 2: Reviews work, finds missing error handling, adds it → AUTORUN_STAGE2_COMPLETE
Stage 3: Verifies form works, tests pass, error handling complete → AUTORUN_STAGE3_COMPLETE → Session ends
```

Without three-stage: Claude might stop after Stage 1 with incomplete work.

### All Commands

**AutoFile Policy** (controls file creation via PreToolUse hooks):

| Short | Long | Legacy | Description |
|-------|------|--------|-------------|
| `/cr:a` | `/cr:allow` | `/afa` | Allow all file creation |
| `/cr:j` | `/cr:justify` | `/afj` | Require `<AUTOFILE_JUSTIFICATION>` for new files |
| `/cr:f` | `/cr:find` | `/afs` | Modify existing files only (strictest) |
| `/cr:st` | `/cr:status` | `/afst` | Show current policy |

**Autorun Control**:

| Short | Long | Legacy | Description |
|-------|------|--------|-------------|
| `/cr:go <task>` | `/cr:run` | `/autorun` | Start autonomous execution |
| `/cr:gp <task>` | `/cr:proc` | `/autoproc` | Procedural mode with Wait Process |
| `/cr:x` | `/cr:stop` | `/autostop` | Graceful stop |
| `/cr:sos` | `/cr:estop` | `/estop` | Emergency stop |

**Plan Management**:

| Short | Long | Description |
|-------|------|-------------|
| `/cr:pn` | `/cr:plannew` | Create structured plan |
| `/cr:pr` | `/cr:planrefine` | Critique and improve plan |
| `/cr:pu` | `/cr:planupdate` | Update plan with new info |
| `/cr:pp` | `/cr:planprocess` | Execute plan with methodology |

**Documentation**:

| Short | Long | Description |
|-------|------|-------------|
| `/cr:gc` | `/cr:commit` | Git commit requirements (17 steps) |
| `/cr:ph` | `/cr:philosophy` | System design philosophy (17 principles) |

**Safety Guards** (v0.6.0+) - Blocks dangerous commands and suggests safe alternatives:

Built-in protections for: `rm` → `trash`, `git reset --hard` → `git stash`, `git clean -f` → `git clean -n`, etc.

| Command | Description |
|---------|-------------|
| `/cr:no <pattern>` | Add custom block (shows safer alternative) |
| `/cr:ok <pattern>` | Allow blocked command in session |
| `/cr:clear` | Clear session overrides |
| `/cr:globalno <pattern>` | Block pattern globally |
| `/cr:globalok <pattern>` | Allow pattern globally |

See `plugins/clautorun/src/clautorun/config.py:38-93` for DEFAULT_INTEGRATIONS list.

**Hook Error Prevention**: See `plugins/clautorun/CLAUDE.md` "Hook Error Prevention" section. Key rule: NEVER add deprecated fields to `[tool.uv]` in pyproject.toml — UV stderr warnings silently disable ALL hooks.

**Tmux/Session Tools**:

| Short | Long | Description |
|-------|------|-------------|
| `/cr:tm` | `/cr:tmux` | Tmux session management |
| `/cr:tt` | `/cr:ttest` | CLI testing in isolated sessions |
| `/cr:tabs` | - | Discover Claude sessions across tmux windows |

**Plan Export** — Auto-exports plans to `notes/` on ExitPlanMode, recovers unexported plans on SessionStart:

| Short | Long | Description |
|-------|------|-------------|
| `/cr:pe` | `/cr:planexport` | Show plan export status |
| `/cr:pe-on` | `/cr:planexport-enable` | Enable auto-export |
| `/cr:pe-off` | `/cr:planexport-disable` | Disable auto-export |
| `/cr:pe-cfg` | `/cr:planexport-configure` | Interactive configuration |
| `/cr:pe-dir` | `/cr:planexport-dir` | Set output directory |
| `/cr:pe-fmt` | `/cr:planexport-pattern` | Set filename pattern |
| `/cr:pe-reset` | `/cr:planexport-reset` | Reset to defaults |
| `/cr:pe-rej` | `/cr:planexport-rejected` | Toggle rejected plan export |
| `/cr:pe-rdir` | `/cr:planexport-rejected-dir` | Set rejected plan output directory |

### Key Files

| File | Purpose |
|------|---------|
| `plugins/clautorun/src/clautorun/config.py` | Single source of truth for CONFIG (stages, policies, templates) |
| `plugins/clautorun/src/clautorun/main.py` | Hook handler and CLI entry point |
| `plugins/clautorun/src/clautorun/plugins.py` | Command handlers and dispatch logic |
| `plugins/clautorun/src/clautorun/plan_export.py` | Plan export logic, PlanExport class, daemon handlers |
| `plugins/clautorun/src/clautorun/integrations.py` | Unified command integrations (superset of hookify) |
| `plugins/clautorun/scripts/plan_export_config.py` | Plan export configuration CLI |
| `plugins/clautorun/.claude-plugin/plugin.json` | Plugin manifest |

---

## pdf-extractor Plugin (v0.1.0)

Extract text from PDFs with 9 backends (markitdown, pdfplumber, docling, marker, etc.).

### Commands

| Command | Description |
|---------|-------------|
| `/pdf-extractor:extract <file>` | Extract PDF to markdown |

### CLI Usage

```bash
extract-pdfs document.pdf              # Single file
extract-pdfs ./pdfs/ ./output/         # Batch extraction
extract-pdfs --list-backends           # Show available backends
extract-pdfs doc.pdf --backends marker # Use specific backend (GPU OCR)
```

### Key Files

| File | Purpose |
|------|---------|
| `plugins/pdf-extractor/src/pdf_extraction/backends.py` | 9 extraction backends |
| `plugins/pdf-extractor/src/pdf_extraction/cli.py` | CLI entry point |
| `plugins/pdf-extractor/CLAUDE.md` | Full documentation |

---

## Architecture

```
clautorun/                          # Git repository root
├── plugins/
│   ├── clautorun/                  # Main plugin
│   │   ├── src/clautorun/          # Python source
│   │   ├── commands/               # Slash commands (50+ files)
│   │   ├── agents/                 # Tmux automation agents
│   │   ├── skills/                 # Claude Code skills
│   │   └── hooks/                  # Event hooks
│   └── pdf-extractor/              # PDF extraction plugin
├── src/clautorun_marketplace/      # Marketplace registration
├── pyproject.toml                  # UV workspace config
└── README.md                       # Full documentation (1800+ lines)
```

## Testing

```bash
# Quick tests (from repo root)
uv run pytest plugins/clautorun/tests/test_unit_simple.py -v

# Full suite with coverage
uv run pytest plugins/clautorun/tests/ --cov=plugins/clautorun/src/clautorun --cov-report=term-missing
```

## Integration References

- **Claude Code Plugins**: [docs.claude.com/en/docs/claude-code/plugins](https://docs.claude.com/en/docs/claude-code/plugins)
- **Plugin Reference**: [docs.claude.com/en/docs/claude-code/plugins-reference](https://docs.claude.com/en/docs/claude-code/plugins-reference)
- **Slash Commands**: [docs.claude.com/en/docs/claude-code/slash-commands](https://docs.claude.com/en/docs/claude-code/slash-commands)
- **Hooks**: [docs.claude.com/en/docs/claude-code/hooks](https://docs.claude.com/en/docs/claude-code/hooks)
- **Agent SDK**: [docs.claude.com/en/api/agent-sdk/overview](https://docs.claude.com/en/api/agent-sdk/overview)
- **Byobu/Tmux**: [byobu.org](https://www.byobu.org/) - Terminal multiplexer for crash-safe sessions
- **Mosh**: [mosh.org](https://mosh.org/) - Mobile shell for unreliable connections

## Full Documentation

See `README.md` (1800+ lines) for complete details:
- Installation options: "Quick Start" and "UV Installation" sections
- Three-stage verification internals: "Three-Stage Autorun System" section (~line 430)
- Safety guards with defaults: "Command Blocking Commands" section (~line 683)
- Tmux/byobu integration: "Tmux Integration" section (~line 478)
- Plugin architecture: "Plugin Architecture and Integration Guide" section (~line 924)
- Troubleshooting: "Troubleshooting" section (~line 1709)
