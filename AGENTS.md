# autorun Marketplace - Claude Code

`CLAUDE.md` and `GEMINI.md` here are symlinks to this file. Edit `AGENTS.md`.

## Development isolation is MANDATORY

This machine runs many harness sessions at once, and they all share the live
daemon, `~/.autorun`, and the installed trees under `~/.agents`, `~/.claude`,
`~/.codex`, `~/.gemini`, `~/.qwen`, `~/.pi`, `~/.prime`, `~/.config/opencode`.
An install, uninstall, or daemon restart against the live machine reaches every
one of those sessions. It has happened: a self-check that redirected only
`$HOME` uninstalled 16 skills from the live machine, and a live `--install
--force` in the middle of other sessions left them looping and burned about
12% of a week's token budget (2026-08-15).

**Rules, for every AI and human working in this repository:**

1. **Every install, uninstall, dry run, status probe, self-check, test, and
   dogfood run happens in a sandbox** — `HOME`/`USERPROFILE`, `AUTORUN_HOME`,
   and `AUTORUN_TEST_STATE_DIR` redirected to a short scratch path — or in
   Docker. `pytest` does this itself through `plugins/autorun/conftest.py`;
   everything else you must do by hand, every time. Keep the sandbox working:
   a change that breaks it blocks the change, not the sandbox.
2. **NEVER touch the live installation without explicit written instruction
   from the user in the current conversation naming that action.** That
   covers `autorun --install` (with or without `--force`), `--uninstall`,
   `--restart-daemon`, `--restart-all-daemons`, `claude plugin install/update`,
   `uv tool install` of autorun, and any hand edit, link, move, or deletion
   under the live harness config directories. A task you wrote for yourself,
   a `/ar:ok` grant for some other command, "so live copies match", or "to
   verify the fix" is not that instruction. Report what a live install would
   change (a sandboxed `--install-dry-run` shows it) and stop there.
3. **Prove isolation instead of assuming it**: snapshot the live trees before
   and after (recipe in the isolation doc), and if a sandboxed hook reports
   `autorun CLI timed out`, check the sandbox socket path length before
   anything else.

Recipe (short path — the daemon socket has almost no `sun_path` headroom):

```bash
SB=/tmp/arsb; mkdir -p "$SB/home" "$SB/ar-home" "$SB/state"
env HOME="$SB/home" USERPROFILE="$SB/home" PI_CODING_AGENT_DIR="$SB/home/.pi/agent" \
    AUTORUN_HOME="$SB/ar-home" AUTORUN_TEST_STATE_DIR="$SB/state" \
    UV_CACHE_DIR="$(uv cache dir)" \
    uv run --project plugins/autorun python -m autorun --install --force
```

Isolated tests, probes, and Docker for Linux contention runs use the same
three variables: [`plugins/autorun/docs/RUNTIME_STATE_ISOLATION.md`](plugins/autorun/docs/RUNTIME_STATE_ISOLATION.md).
Installer-specific traps (a `Context.home` that disagrees with `$HOME`, an
in-process self-check whose `$HOME` redirect does not move the daemon):
[`plugins/autorun/src/autorun/installer/AGENTS.md`](plugins/autorun/src/autorun/installer/AGENTS.md).

## Critical Runtime Isolation

- Tests must set both `AUTORUN_HOME` and `AUTORUN_TEST_STATE_DIR` before any
  autorun import; they must never touch the live daemon socket, PID, locks, logs,
  or user session history.
- Daemon hook paths must use `EventContext.state_get/state_set/state_update`;
  wrap legacy direct persistence with `state_synchronize` so concurrent threads,
  processes, sessions, and harnesses cannot observe stale state.
- Never hide persistent-state I/O or lock failures by raising hook timeouts, and
  never weaken concurrency, protocol, or isolation assertions to pass tests.
- Before committing, read `plugins/autorun/skills/commit/SKILL.md`; use a concrete
  `<files>:` subject for few/grouped files or `type(scope):` for many files.
  Cover previous behavior, exact changes, rationale, files, and verification.
  Read the full staged diff before every commit.

UV workspace containing 2 Claude Code plugins: **autorun**, **pdf-extractor**.

gemini-cli is retired, 'gemini' represents the qwen code agy harness families.

**For explicit legacy Gemini CLI support:** See
[README.md — Legacy Gemini CLI Installation](README.md#legacy-gemini-cli-installation).

## Installation (Claude Code)

These are end-user instructions for installing autorun on a machine you own.
Inside a development session they fall under the isolation rules above: run
them in the sandbox, or on the live machine only when the user has told you to.

### From GitHub (Production - Recommended)

```bash
# Add the marketplace, then install its registered plugin identity
claude plugin marketplace add https://github.com/ahundt/autorun.git
claude plugin install ar@autorun

# Verify
claude plugin list  # Should show: ar
# Optional companion: claude plugin install pdf-extractor@autorun
```

### From Local Clone (Development)

```bash
git clone https://github.com/ahundt/autorun.git && cd autorun

# Option 1: UV (recommended - faster, better dependency management)
uv run --project plugins/autorun python -m autorun --install --force

# Option 2: pip fallback (if UV not available)
python -m pip install -e plugins/autorun && autorun --install --force

# REQUIRED: Install as UV tool for global CLI availability
# This makes the 'autorun' and 'autorun-install' commands globally available
# which are needed for proper daemon operation and session management
cd plugins/autorun && uv tool install --force --editable .

# Verify installation
claude plugin list  # Shows ar; source-checkout installs may also show pdf-extractor
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

## autorun Plugin (v1.0.0rc1)

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

`/ar:help` lists these live; `/ar:help <cmd>` shows one. Dispatch takes `/ar:st`,
`ar:st`, `ar st`, `/ar-st`, `ar-st`, and the retired `ar:task-status`/`ar:task-ignore`;
only what autorun prints is per-harness. Codex never delivers the leading slash;
ForgeCode and OpenCode run only their installed command files.

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
| `/ar:task` | `/ar:tasks` | - | Show task status or dispatch pause, resume, ignore, prompts, and recovery |
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
| - | `/ar:help` | List every command in this harness's spelling |
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

See `DEFAULT_INTEGRATIONS` in `plugins/autorun/src/autorun/config.py` for the list.

**Hook Error Prevention**: See `plugins/autorun/AGENTS.md` "Hook error prevention" section. Key rule: NEVER add deprecated fields to `[tool.uv]` in pyproject.toml — UV stderr warnings silently disable ALL hooks.

**Never print outside CLI entry points**: stdout is the hook response and stderr reads as a hook failure that disables every hook — log via `logging_utils.get_logger()`.

**Tmux/Session Tools**:

| Short | Long | Description |
|-------|------|-------------|
| `/ar:tm` | `/ar:tmux` | Tmux session management |
| `/ar:tt` | `/ar:ttest` | CLI testing in isolated sessions |
| `/ar:tabs` | - | Discover Claude sessions across tmux windows |

**Plan Export** — Auto-exports plans to `notes/` on ExitPlanMode, recovers unexported plans on SessionStart:

| Short | Long | Description |
|-------|------|-------------|
| `/ar:pe [on\|off\|globalon\|globaloff]` | `/ar:planexport […]` | Status, per-project pin, or global default; a pin beats the global default |
| `/ar:pe dir <path>` | `/ar:planexport dir <path>` | Set the export directory |
| `/ar:pe pattern <template>` | `/ar:planexport pattern <template>` | Set the filename pattern |
| `/ar:pe <component> [on\|off\|dir <path>]` | `/ar:planexport <component> […]` | Per-component switch and destination. Components are `accepted` and `rejected`; a bare name toggles it. A component writes only when both plan export and that component are on |
| `/ar:pe reset` | `/ar:planexport reset` | Restore defaults (also clears project pins) |

**Task Tracking** (v0.9+):

| Command | Description |
|---------|-------------|
| `/ar:task`, `/ar:tasks` | Show task, pause, prompting, and recovery status |
| `/ar:task pause [N] [duration] [reason]` | Bare pause defaults to five minutes; reason-only pause continues until AI recovery; explicit count and duration may be combined |
| `/ar:task resume` | Resume task enforcement |
| `/ar:task ignore <id> [reason]` | Mark one task ignored |
| `/ar:task prompts on\|off\|<N>` | Configure task-staleness prompting |
| `/ar:task recovery on\|off\|min <N>` | Configure repeated-Stop stale-task recovery |

**Cache-Miss / Compaction Protection** (off by default):

| Command | Description |
|---------|-------------|
| `/ar:cache` | Show status (enabled/disabled, thresholds, active overrides). |
| `/ar:cache on [5m\|1h\|perm]` | Enable the gate (optionally for a window). |
| `/ar:cache off [5m\|1h\|perm]` | Disable the gate. |
| `/ar:cache set ratio\|read\|age\|full <value>` | Configure threshold axes. Tokens `50k \| .5M`, percents `85%`, durations `5m \| 2h30m \| 2d`. |
| `/ar:cache ok [5m\|N\|perm]` | Override the gate (same grammar as `/ar:ok`). |
| `/ar:cache no` | Cancel outstanding overrides. |

Feature lives in `plugins/autorun/src/autorun/cache_guard.py`. Reuses `ScopedAllow`, `parse_scope_args`, `session_state`, `check_blocked_commands`, `detect_cli_type`. 

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
| `plugins/autorun/src/autorun/__main__.py` | CLI entry point (`autorun`) and hook handler routing (`run_hook_handler`) |
| `plugins/autorun/src/autorun/main.py` | Pattern matching, hook response building, and the legacy `main()` shim |
| `plugins/autorun/src/autorun/plugins.py` | Command handlers and dispatch logic |
| `plugins/autorun/src/autorun/plan_export.py` | Plan export logic, PlanExport class, daemon handlers |
| `plugins/autorun/src/autorun/integrations.py` | Unified command integrations (superset of hookify) |
| `plugins/autorun/src/autorun/task_lifecycle.py` | Task lifecycle tracking and stop-hook enforcement |
| `plugins/autorun/src/autorun/session_manager.py` | filelock+JSON session state backend |
| `plugins/autorun/src/autorun/client.py` | Hook response output and CLI detection |
| `plugins/autorun/.claude-plugin/plugin.json` | Plugin manifest |

---

## pdf-extractor Plugin (v1.0.0rc1)

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

This is a harness plugin, not a Python distribution: `plugins/pdf-extractor/`
holds the manifests, commands, skill, and the `src/pdf_extraction` source, and
`plugins/autorun/src/pdf_extraction` is a symlink to that source so
`extract-pdfs` and `pdf_extraction` ship inside the `autorun` distribution
(every backend beyond `pdftotext` sits behind the `pdf` extra).

---

## Architecture

```
autorun/                          # Git repository root
├── plugins/
│   ├── autorun/                  # Main plugin
│   │   ├── src/autorun/          # Python source
│   │   ├── commands/               # Slash commands
│   │   ├── agents/                 # Tmux automation agents
│   │   ├── skills/                 # Claude Code skills
│   │   └── hooks/                  # Event hooks
│   └── pdf-extractor/              # PDF extraction plugin
├── src/autorun_workspace/        # Workspace-root package (UV workspace member)
├── pyproject.toml                  # UV workspace config
└── README.md                       # Full documentation
```

## Testing

```bash
# Quick tests (from repo root)
uv run --project plugins/autorun pytest plugins/autorun/tests/test_unit_simple.py -v

# Full suite with coverage
uv run --project plugins/autorun pytest plugins/autorun/tests/ --cov=plugins/autorun/src/autorun --cov-report=term-missing
```

## Integration References

- **Claude Code Plugins**: [docs.claude.com/en/docs/claude-code/plugins](https://docs.claude.com/en/docs/claude-code/plugins)
- **Plugin Reference**: [docs.claude.com/en/docs/claude-code/plugins-reference](https://docs.claude.com/en/docs/claude-code/plugins-reference)
- **Slash Commands**: [docs.claude.com/en/docs/claude-code/slash-commands](https://docs.claude.com/en/docs/claude-code/slash-commands)
- **Hooks**: [docs.claude.com/en/docs/claude-code/hooks](https://docs.claude.com/en/docs/claude-code/hooks)
- **Byobu/Tmux**: [byobu.org](https://www.byobu.org/) - Terminal multiplexer for crash-safe sessions
- **Mosh**: [mosh.org](https://mosh.org/) - Mobile shell for unreliable connections

## Full Documentation

See `README.md` for complete details:
- Installation options: "Quick Start" and "UV Installation" sections
- Three-stage verification internals: "Three-Stage Autorun System" section
- Safety guards with defaults: the command-blocking sections
- Tmux/byobu integration: "Tmux Integration" section
- Plugin architecture: "Plugin Architecture and Integration Guide" section
- Troubleshooting: "Troubleshooting" section
