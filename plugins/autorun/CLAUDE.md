# ⚠️ CRITICAL: Read from Git Repository, NOT Plugin Cache

## You Are Reading the WRONG Location If:

- Path contains: `~/.claude/plugins/cache/`
- Path contains: `/.claude/plugins/cache/`
- You see version numbers like `0.5.0/` in the path

## CORRECT Location: Git Repository

**Always read from this location (relative to git root):**
```
plugins/autorun/
```

**Why:**
- ✅ Git repository with version control
- ✅ Uncommitted changes are visible
- ✅ Editable source files (changes take effect)
- ✅ Active development location
- ✅ Test files can be run and modified
- ✅ Has latest bug fixes and improvements

## Quick Install/Update Command

**Primary installation command** (run from repository root):
```bash
(uv run --project plugins/autorun python -m autorun --install --force && \
  cd plugins/autorun && \
  uv tool install --force --editable . && \
  cd ../.. && \
  autorun --restart-daemon) 2>&1 | tee "install-$(date +%Y%m%d-%H%M%S).log"
```

**IMPORTANT:** Use a **3-minute timeout** when running via Bash tool - the UV tool
install step can take 1-2 minutes on first run or when dependencies change.

**What this does:**
1. Syncs plugin to cache (both Claude Code and Gemini CLI)
2. Installs UV tool globally (`autorun`, `claude-session-tools` commands)
3. Restarts daemon to pick up code changes
4. Logs output to timestamped file: `install-YYYYMMDD-HHMMSS.log`

**When to run:**
- After editing Python source files in `src/autorun/`
- After modifying hook files in `hooks/`
- After changing plugin configuration
- When testing fixes or new features

**Log file:** Check `install-*.log` for installation details and troubleshooting

## WRONG Location: Plugin Cache (READ-ONLY)

**DO NOT read from:**
```
~/.claude/plugins/cache/autorun/autorun/0.5.0/
```

**Why NOT:**
- ❌ Cached copy installed by plugin system
- ❌ Changes here don't persist (reinstalled on update)
- ❌ Not a git repository
- ❌ No version control or git history
- ❌ Not the development location
- ❌ May be outdated (missing bug fixes)

## How This Happens

Claude Code plugin installation process:
1. `/plugin install https://github.com/ahundt/autorun.git`
2. Claude copies repository to: `~/.claude/plugins/cache/autorun/autorun/0.5.0/`
3. Plugin loads from cache location
4. **Problem**: AI may read cached code instead of git repository
5. **Issue**: Changes in dev repo may not be reflected in cache until reinstalled

## Directory Structure

```
autorun/                             # Git repository root
├── plugins/autorun/                 # <-- DEVELOPMENT LOCATION (READ THIS)
│   ├── src/autorun/                 # Source code to edit
│   ├── tests/                         # Tests to run
│   ├── commands/                      # Plugin commands
│   ├── agents/                        # Agent definitions
│   ├── CLAUDE.md                      # <-- This file
│   └── .claude-plugin/                # Plugin manifest
└── ... (other files)

~/.claude/plugins/cache/autorun/     # Plugin cache (DO NOT EDIT)
└── autorun/
    └── 0.5.0/                         # Cached copy (READ-ONLY)
        ├── src/autorun/             # May be outdated!
        ├── tests/
        └── ...
```

## Entry Points

- **Commands**: `commands/autorun` — executable called by Claude Code plugin system (JSON stdin/stdout)
- **Hooks**: `hooks/hook_entry.py` — event handler for UserPromptSubmit, PreToolUse, Stop, SubagentStop (configured via `hooks/claude-hooks.json`)
- **CLI**: `autorun` command → `src/autorun/__main__.py:main` (installed globally via `uv tool install --editable .`)
- **Config**: `src/autorun/config.py` — single source of truth for all CONFIG values

## Verification Commands

**Check if you're in the right location:**
```bash
# Should show git repository
git status

# Should show: "On branch fix-v0.4.1-opentelemetry-import"
# If error: "not a git repository", you're in the WRONG location

# Check current working directory
pwd
# Should end with: plugins/autorun/
```

## Hook Error Prevention (CRITICAL)

Claude Code treats ANY stderr output from hooks as "hook error" and ignores the hook's JSON response. This silently disables ALL hook protections (rm blocking, git safety, etc.) while appearing to work.

**Rules to prevent hook errors:**

1. **pyproject.toml [tool.uv]**: NEVER add deprecated UV fields. UV versions remove fields silently. When UV encounters an unknown field, it prints a warning to stderr, which breaks ALL hooks. The `default-extras` field was removed in UV 0.9+. If you need default extras, put them in `[project] dependencies` instead.

2. **Slash commands**: ALL bash commands in `.md` files MUST use `uv run --project ${CLAUDE_PLUGIN_ROOT} python` — never bare `python3`. The `allowed-tools` frontmatter must use `Bash(uv *)` not `Bash(python3:*)`.

3. **Hook stderr**: hook_entry.py must NEVER write to stderr. All error handling must go through `fail_open()` which writes JSON to stdout.

4. **Cache sync**: After fixing pyproject.toml or hooks.json in the source, run the installer to sync to cache:
   ```bash
   uv run --project plugins/autorun python -m autorun --install --force
   ```
   Manual file copies to `~/.claude/plugins/cache/` are fragile and will be overwritten on next install. Always use the installer.

5. **Session restart**: Hook configuration is cached at session start. Fixes to hooks.json or pyproject.toml only take effect on the NEXT Claude Code session.

**Regression tests**: `test_hook_entry.py::TestUVCompatibility` and `test_hook_entry.py::TestCacheSync`

**Diagnosis**: Run `uv run --project <plugin_root> python -c "pass" 2>&1` — any output beyond "Building/Installed" lines is a problem.

## Bug #4669 Workaround (Claude Code v1.0.62+)

**Problem**: Claude Code ignores `permissionDecision:"deny"` at exit 0. Tool executes anyway despite JSON deny decision.

**Solution**: Auto-detect CLI and apply exit-2 workaround for Claude Code only. Gemini CLI respects JSON decision field correctly.

**Behavior**:
- **Claude Code**: Uses exit 2 + stderr (only way blocking works due to bug #4669)
- **Gemini CLI**: Uses JSON `decision` field (works correctly per spec)
- **Auto-detect**: Based on `GEMINI_SESSION_ID` and `GEMINI_PROJECT_DIR` environment variables

**Configuration**:

Environment variable (set before running Claude Code/Gemini):
```bash
# Auto-detect (default - recommended)
export AUTORUN_EXIT2_WORKAROUND=auto

# Force enable for testing
export AUTORUN_EXIT2_WORKAROUND=always

# Disable for testing/future
export AUTORUN_EXIT2_WORKAROUND=never
```

CLI argument (applies to current execution):
```bash
autorun --exit2-mode auto    # Default - auto-detect CLI
autorun --exit2-mode always  # Force exit-2 for all CLIs
autorun --exit2-mode never   # Disable workaround for all CLIs
```

**Technical Details**:
- Detection: `plugins/autorun/src/autorun/config.py:detect_cli_type()`
- Unified output: `plugins/autorun/src/autorun/client.py:output_hook_response()`
- Response format: Both `decision` (Gemini) and `hookSpecificOutput.permissionDecision` (Claude) fields included
- Exit codes: 0 for allow/Gemini-deny, 2 for Claude-deny (stderr contains reason)

**Reference**: `notes/hooks_api_reference.md` lines 326-440 (workaround details), lines 1187-1221 (outcome matrices)

## Dynamic Content in Slash Commands

Markdown commands can include dynamic bash output using `!` prefix ([docs](https://docs.anthropic.com/en/docs/claude-code/slash-commands)). To access CONFIG:

```bash
!`uv run --project ${CLAUDE_PLUGIN_ROOT} python -c "from autorun.config import CONFIG; print(CONFIG['key'])"`
```

## If You See This File in Cache Location

1. Navigate to git repository: `cd <git-root>/plugins/autorun/`
2. Read CLAUDE.md from that location
3. Edit source files in that location
4. Run tests from that location
5. Commit changes to git repository
6. Reinstall plugin: `/plugin update autorun`

## Summary

- **READ**: `<git-root>/plugins/autorun/`
- **EDIT**: `<git-root>/plugins/autorun/`
- **TEST**: `<git-root>/plugins/autorun/`
- **COMMIT**: `<git-root>/` (git root)

**NEVER**: `~/.claude/plugins/cache/...` (wrong location, may be outdated)
