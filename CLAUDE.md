# clautorun Marketplace

UV workspace containing 3 Claude Code plugins: **clautorun**, **plan-export**, **pdf-extractor**.

## Installation

```bash
# From GitHub (recommended)
uv pip install git+https://github.com/ahundt/clautorun.git
uv run clautorun-marketplace

# From local clone
git clone https://github.com/ahundt/clautorun.git && cd clautorun
uv pip install . && uv run clautorun-marketplace

# Verify plugins installed
claude plugin list  # Should show: cr, plan-export, pdf-extractor

# In a Claude Code session, test:
# /cr:st  → "AutoFile policy: allow-all"
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
| **clautorun** | `/cr:` | Autonomous execution, file policies, safety guards |
| **plan-export** | `/plan-export:` | Auto-export plans to `notes/` on ExitPlanMode |
| **pdf-extractor** | `/pdf-extractor:` | Extract text from PDFs (9 backends, GPU support) |

---

## clautorun Plugin (v0.7.0)

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

**Tmux/Session Tools**:

| Short | Long | Description |
|-------|------|-------------|
| `/cr:tm` | `/cr:tmux` | Tmux session management |
| `/cr:tt` | `/cr:ttest` | CLI testing in isolated sessions |
| `/cr:tabs` | - | Discover Claude sessions across tmux windows |

### Key Files

| File | Purpose |
|------|---------|
| `plugins/clautorun/src/clautorun/config.py` | Single source of truth for CONFIG (stages, policies, templates) |
| `plugins/clautorun/src/clautorun/main.py` | Hook handler and CLI entry point |
| `plugins/clautorun/src/clautorun/plugins.py` | Command handlers and dispatch logic |
| `plugins/clautorun/commands/clautorun` | Plugin command script |
| `plugins/clautorun/.claude-plugin/plugin.json` | Plugin manifest |

---

## plan-export Plugin (v0.7.0)

Auto-exports plan files to `notes/YYYY_MM_DD_<name>.md` when exiting plan mode.

### Commands

| Command | Description |
|---------|-------------|
| `/plan-export:enable` | Enable auto-export |
| `/plan-export:disable` | Disable auto-export |
| `/plan-export:status` | Show export status |
| `/plan-export:configure` | Interactive configuration |
| `/plan-export:dir` | Set output directory |
| `/plan-export:pattern` | Set filename pattern |
| `/plan-export:preset` | Apply preset configuration |
| `/plan-export:presets` | List available presets |
| `/plan-export:reset` | Reset to defaults |

### Key Files

| File | Purpose |
|------|---------|
| `plugins/plan-export/hooks/` | PostToolUse hook for ExitPlanMode detection |
| `plugins/plan-export/README.md` | Full documentation |

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
│   ├── plan-export/                # Plan export plugin
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
