# autorun Marketplace - Claude Code

## Critical Runtime Isolation

- Tests must set both `AUTORUN_HOME` and `AUTORUN_TEST_STATE_DIR` before any
  autorun import; they must never touch the live daemon socket, PID, locks, logs,
  or user session history.
- Daemon hook paths must use `EventContext.state_get/state_set/state_update`;
  wrap legacy direct persistence with `state_synchronize` so concurrent threads,
  processes, sessions, and harnesses cannot observe stale state.
- Never hide persistent-state I/O or lock failures by raising hook timeouts, and
  never weaken concurrency, protocol, or isolation assertions to pass tests.
- Before committing, read `plugins/autorun/commands/commit.md`; use a concrete
  `<files>:` or `type(scope):` subject and a body covering previous behavior,
  exact changes, rationale, affected files, and verification evidence.
- Full invariants, regression checks, and recovery guidance:
  [`plugins/autorun/docs/RUNTIME_STATE_ISOLATION.md`](plugins/autorun/docs/RUNTIME_STATE_ISOLATION.md).

UV workspace containing 2 Claude Code plugins: **autorun**, **pdf-extractor**.

**For Gemini CLI:** See [GEMINI.md](GEMINI.md) for Gemini-specific installation and configuration.

## Installation (Claude Code)

### From GitHub (Production - Recommended)

```bash
# Install directly via Claude Code plugin system
claude plugin install https://github.com/ahundt/autorun.git

# Verify
claude plugin list  # Should show: ar, pdf-extractor
```

### From Local Clone (Development)

```bash
git clone https://github.com/ahundt/autorun.git && cd autorun

# Option 1: UV (recommended - faster, better dependency management)
uv run python -m plugins.autorun.src.autorun.install --install --force

# Option 2: pip fallback (if UV not available)
pip install -e . && python -m plugins.autorun.src.autorun.install --install --force

# REQUIRED: Install as UV tool for global CLI availability
# This makes 'autorun' and 'claude-session-tools' commands globally available
# which are needed for proper daemon operation and session management
cd plugins/autorun && uv tool install --force --editable .

# Verify installation
claude plugin list  # Should show: ar, pdf-extractor
autorun --status  # Verifies UV tool installation works
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
/ar:st  # Expected: "AutoFile policy: allow-all"
```

## Quick Start

```bash
/ar:go <task>     # Start autonomous execution with three-stage verification
/ar:sos           # Emergency stop
/ar:st            # Show current status
```

## Plugins Overview

| Plugin | Prefix | Purpose |
|--------|--------|---------|
| **autorun** | `/ar:` | Autonomous execution, file policies, safety guards, plan export |
| **pdf-extractor** | `/pdf-extractor:` | Extract text from PDFs (9 backends, GPU support) |

---

## autorun Plugin (v0.12.0)

### Three-Stage Verification System

Ensures thorough task completion through mandatory stages:

| Stage | Purpose | Completion Marker |
|-------|---------|-------------------|
| **Stage 1** | Initial implementation | `AUTORUN_INITIAL_TASKS_COMPLETED` |
| **Stage 2** | Critical evaluation - identify gaps, fix issues | `CRITICALLY_EVALUATING_PREVIOUS_WORK_AND_CONTINUING_TASKS_AS_NEEDED` |
| **Stage 3** | Final verification - all requirements met | `AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY` |

**Concrete Example:**
```
User: /ar:go Add login form with validation and tests

Stage 1: Implements login form → outputs AUTORUN_INITIAL_TASKS_COMPLETED
Stage 2: Reviews work, finds missing error handling, adds it → CRITICALLY_EVALUATING_PREVIOUS_WORK_AND_CONTINUING_TASKS_AS_NEEDED
Stage 3: Verifies form works, tests pass, error handling complete → AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY → Session ends
```

Without three-stage: Claude might stop after Stage 1 with incomplete work.

### All Commands

**AutoFile Policy** (controls file creation via PreToolUse hooks):

| Short | Long | Legacy | Description |
|-------|------|--------|-------------|
| `/ar:a` | `/ar:allow` | `/afa` | Allow all file creation |
| `/ar:j` | `/ar:justify` | `/afj` | Require `<AUTOFILE_JUSTIFICATION>` for new files |
| `/ar:f` | `/ar:find` | `/afs` | Modify existing files only (strictest) |
| `/ar:st` | `/ar:status` | `/afst` | Show current policy |

**Autorun Control**:

| Short | Long | Legacy | Description |
|-------|------|--------|-------------|
| `/ar:go <task>` | `/ar:run` | `/autorun` | Start autonomous execution |
| `/ar:gp <task>` | `/ar:proc` | `/autoproc` | Procedural mode with Wait Process |
| `/ar:x` | `/ar:stop` | `/autostop` | Graceful stop |
| `/ar:sos` | `/ar:estop` | `/estop` | Emergency stop |

**Plan Management**:

| Short | Long | Description |
|-------|------|-------------|
| `/ar:pn` | `/ar:plannew` | Create structured plan |
| `/ar:pr` | `/ar:planrefine` | Critique and improve plan |
| `/ar:pu` | `/ar:planupdate` | Update plan with new info |
| `/ar:pp` | `/ar:planprocess` | Execute plan with methodology |

**Documentation**:

| Short | Long | Description |
|-------|------|-------------|
| `/ar:gc` | `/ar:commit` | Git commit requirements (17 steps) |
| `/ar:ph` | `/ar:philosophy` | System design philosophy (17 principles) |

**Safety Guards** (v0.6.0+) - Blocks dangerous commands and suggests safe alternatives:

Built-in protections for: `rm` → `trash`, `git reset --hard` → `git stash`, `git clean -f` → `git clean -n`, etc.

| Command | Description |
|---------|-------------|
| `/ar:no <pattern>` | Block command pattern in this session |
| `/ar:ok <pattern> [N\|5m\|perm]` | Allow pattern — `3` uses, `5m` duration, or `perm` (rest of session); default 1 use then auto-revokes |
| `/ar:clear` | Clear all session blocks and allows |
| `/ar:blocks` | Show active session-level blocks and allows |
| `/ar:globalno <pattern>` | Block command pattern globally (persists across sessions) |
| `/ar:globalok <pattern> [N\|5m\|perm]` | Allow pattern globally — `3` uses, `5m` duration, or `perm` (until cleared); default 1 use then auto-revokes |
| `/ar:globalstatus` | Show global blocks and allows |
| `/ar:globalclear` | Clear all global blocks and allows |

See `plugins/autorun/src/autorun/config.py:63-276` for DEFAULT_INTEGRATIONS list.

**Hook Error Prevention**: See `plugins/autorun/CLAUDE.md` "Hook Error Prevention" section. Key rule: NEVER add deprecated fields to `[tool.uv]` in pyproject.toml — UV stderr warnings silently disable ALL hooks.

**Tmux/Session Tools**:

| Short | Long | Description |
|-------|------|-------------|
| `/ar:tm` | `/ar:tmux` | Tmux session management |
| `/ar:tt` | `/ar:ttest` | CLI testing in isolated sessions |
| `/ar:tabs` | - | Discover Claude sessions across tmux windows |

**Plan Export** — Auto-exports plans to `notes/` on ExitPlanMode, recovers unexported plans on SessionStart:

| Short | Long | Description |
|-------|------|-------------|
| `/ar:pe` | `/ar:planexport` | Show plan export status |
| `/ar:pe-on` | `/ar:planexport-enable` | Enable auto-export |
| `/ar:pe-off` | `/ar:planexport-disable` | Disable auto-export |
| `/ar:pe-cfg` | `/ar:planexport-configure` | Interactive configuration |
| `/ar:pe-dir` | `/ar:planexport-dir` | Set output directory |
| `/ar:pe-fmt` | `/ar:planexport-pattern` | Set filename pattern |
| `/ar:pe-reset` | `/ar:planexport-reset` | Reset to defaults |
| `/ar:pe-rej` | `/ar:planexport-rejected` | Toggle rejected plan export |
| `/ar:pe-rdir` | `/ar:planexport-rejected-dir` | Set rejected plan output directory |

**Task Tracking** (v0.9+):

| Command | Description |
|---------|-------------|
| `/ar:tasks` | Toggle task staleness reminders on/off or set threshold |
| `/ar:tasks stale on\|off\|min <N>` | Enable/disable stale-task escape hatch or set consecutive-block threshold (v0.10.2) |
| `/ar:task-status` | Show task lifecycle status and incomplete tasks |
| `/ar:task-ignore <id>` | Mark task as ignored (user override to unblock stop) |

**Cache-Miss / Compaction Protection** (off by default):

| Command | Description |
|---------|-------------|
| `/ar:cache` | Show status (enabled/disabled, thresholds, active overrides). |
| `/ar:cache on [5m\|1h\|perm]` | Enable the gate (optionally for a window). |
| `/ar:cache off [5m\|1h\|perm]` | Disable the gate. |
| `/ar:cache set ratio\|read\|age\|full <value>` | Configure threshold axes. Tokens `50k \| .5M`, percents `85%`, durations `5m \| 2h30m`. |
| `/ar:cache ok [5m\|N\|perm]` | Override the gate (same grammar as `/ar:ok`). |
| `/ar:cache no` | Cancel outstanding overrides. |

Feature lives in `plugins/autorun/src/autorun/cache_guard.py`. Reuses `ScopedAllow`, `parse_scope_args`, `session_state`, `check_blocked_commands`, `detect_cli_type`. See plan at `~/.claude/plans/make-a-plan-to-sunny-sparkle.md`.

**Developer/Admin**:

| Command | Description |
|---------|-------------|
| `/ar:reload` | Force-reload all integration rules from config files |
| `/ar:restart-daemon` | Restart the daemon for the current autorun install/source tree |
| `autorun --restart-all-daemons` | Risky recovery command for stale or mixed-version daemons; can interrupt active autorun-backed sessions in other installs |
| `/ar:marketplace-test` | Run tests across installed marketplace plugins |
| `/ar:test` | Test command guidelines |
| `/ar:gemini` | Gemini CLI reference guide |
| `/ar:tabw` | Cross-window session actions |

### Key Files

| File | Purpose |
|------|---------|
| `plugins/autorun/src/autorun/config.py` | Single source of truth for CONFIG (stages, policies, templates) |
| `plugins/autorun/src/autorun/main.py` | Hook handler and CLI entry point |
| `plugins/autorun/src/autorun/plugins.py` | Command handlers and dispatch logic |
| `plugins/autorun/src/autorun/plan_export.py` | Plan export logic, PlanExport class, daemon handlers |
| `plugins/autorun/src/autorun/integrations.py` | Unified command integrations (superset of hookify) |
| `plugins/autorun/src/autorun/task_lifecycle.py` | Task lifecycle tracking and stop-hook enforcement |
| `plugins/autorun/src/autorun/session_manager.py` | filelock+JSON session state backend |
| `plugins/autorun/src/autorun/client.py` | Hook response output and CLI detection |
| `plugins/autorun/scripts/plan_export_config.py` | Plan export configuration CLI |
| `plugins/autorun/.claude-plugin/plugin.json` | Plugin manifest |

---

## pdf-extractor Plugin (v0.12.0)

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
autorun/                          # Git repository root
├── plugins/
│   ├── autorun/                  # Main plugin
│   │   ├── src/autorun/          # Python source
│   │   ├── commands/               # Slash commands (77 files)
│   │   ├── agents/                 # Tmux automation agents
│   │   ├── skills/                 # Claude Code skills
│   │   └── hooks/                  # Event hooks
│   └── pdf-extractor/              # PDF extraction plugin
├── src/autorun_marketplace/      # Marketplace registration
├── pyproject.toml                  # UV workspace config
└── README.md                       # Full documentation (1800+ lines)
```

## Testing

```bash
# Quick tests (from repo root)
uv run pytest plugins/autorun/tests/test_unit_simple.py -v

# Full suite with coverage
uv run pytest plugins/autorun/tests/ --cov=plugins/autorun/src/autorun --cov-report=term-missing
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
