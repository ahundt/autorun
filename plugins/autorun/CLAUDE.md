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
2. Installs UV tool globally (`autorun`, `aise` commands)
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

## Feature Implementation Lessons (from `/ar:cache`, v0.10)

**Critical:** Set `AUTORUN_HOME` and `AUTORUN_TEST_STATE_DIR` before imports so
tests cannot touch the live daemon. In daemon paths use `state_get`, `state_set`,
and atomic `state_update`; wrap legacy persistence with `state_synchronize` so
threads, processes, sessions, and harnesses cannot observe stale state. Never
raise timeouts or weaken isolation/concurrency tests to hide persistent I/O.
Full specification: [`docs/RUNTIME_STATE_ISOLATION.md`](docs/RUNTIME_STATE_ISOLATION.md).

Rules extracted from the `/ar:cache` build and earlier fixes. Follow them when adding any new gated feature.

1. **Reuse `ScopedAllow` + `parse_scope_args` for every override grant.** Do not invent a new TTL/count parser — the `5m | 5 | perm | 2h30m` grammar lives in `scoped_allow.py:42-76` and its `_PARALLEL_GRACE_SECONDS = 1.0` fingerprint-matched grace window already mitigates rtk's double-hook. See `cache_guard.grant_override`.
2. **Use the scoped `EventContext` state APIs in daemon paths.** `state_get`, `state_set`, and `state_update` keep `ThreadSafeDB` coherent; wrap legacy direct-persistence helpers with `state_synchronize`. Reserve `session_state()` for standalone administration and persistence internals.
3. **When a feature adds a new Claude event, add the Gemini analog at the same time.** `PreCompact` on Claude → `PreCompress` on Gemini (advisory, cannot block, no PostCompress exists). Wire both at `plugins.py:@app.on(...)`, add mapping in `core.py:GEMINI_EVENT_MAP`, and declare the event in BOTH `hooks/hooks.json` (Claude) AND `src/autorun/gemini_template/hooks/hooks.json` (Gemini).
4. **When you need data from hook stdin beyond what `EventContext` already exposes, add a slot + property.** `transcript_path` was missing before `/ar:cache`. Pattern: add the slot, property, kwarg to `__init__`, and update every `EventContext(...)` call site (daemon, plan_export, anywhere else). Do not `getattr(ctx, "field", None)` — that silently returns None when the plumbing is broken.
5. **Features that may block tools must slot AFTER TIER 1 (`/ar:ok` allows) and BEFORE TIER 2 (pattern blocks).** Otherwise an explicit allow cannot bypass the new gate. See `plugins.check_blocked_commands` → `CacheGuard.from_session().on_pretooluse(...)` site.
6. **Keep full persistent-state reads off warm hooks.** Hydrate through `ThreadSafeDB` once per session and use atomic updates for shared fields. Coalescing file locks alone still reparses the full durable state.
7. **Fail open when data is unknown.** A gate that errors or denies on missing fields is worse than a gate that allows. CacheGuard returns `HookDecision.allow()` whenever the configured axis's data is None. Cross-CLI robustness falls out of this rule for free.
8. **Default off.** Any new gate must default `False` in its `FeatureToggle`. Users opt in via `/ar:<feature> on`.
9. **Anchor `.gitignore` directory patterns with a leading `/` when you mean the repo root.** Unanchored `cache/` matches `plugins/autorun/skills/cache/` too — the `/ar:cache` skill was invisible to git until this was fixed. Use `/cache/` + explicit `!plugins/autorun/skills/cache/` when you must name the dir `cache` for UX reasons.
10. **When editing a Gemini hook schema validator test, update the event list in ALL three tests:** `test_split_layout.py`, `test_hooks_format.py`, `test_dual_cli_pathways.py` — they each maintain a canonical event list independently.

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

## Bug Workaround Policy

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

**CONFIG template** (`config.py` `# ─── Bug Workarounds ───`):

```
# BUG #NNNNN: What's broken. https://github.com/anthropics/claude-code/issues/NNNNN
# Workaround: what changes. Override: env var same name (true|false|always|never).
# Evidence: notes/YYYY_MM_DD_*.md — Set to False when fixed.
"AUTORUN_BUG_<NAME>_BUG_<NUMBER>_WORKAROUND_ENABLED": True,
```

| Bug | Platform | Key | Default | Effect |
|-----|----------|-----|---------|--------|
| [#4669](https://github.com/anthropics/claude-code/issues/4669): deny ignored at exit 0 | Claude Code | `AUTORUN_EXIT2_WORKAROUND` (legacy) | `auto` | stderr + exit 2 |
| [#18534](https://github.com/anthropics/claude-code/issues/18534): additionalContext dropped | Claude Code | `AUTORUN_BUG_CLAUDE_CODE_IGNORES_ADDITIONAL_CONTEXT_JSON_ENTRY_BUG_18534_WORKAROUND_ENABLED` | `True` | channel="ai" → "both" |
| [#24115](https://github.com/anthropics/claude-code/issues/24115): plugin loader scans marketplace-source hooks/ AND cache; strict Zod rejects Gemini event names with `invalid_key` | Claude Code | `AUTORUN_BUG_CLAUDE_CODE_MARKETPLACE_SOURCE_SCAN_BUG_24115_WORKAROUND_ENABLED` | `True` | Claude events ONLY in `plugins/autorun/hooks/`; Gemini events live under `src/autorun/gemini_template/` (outside Claude's scan path) |
| [#14449](https://github.com/google-gemini/gemini-cli/issues/14449) ([PR #14460](https://github.com/google-gemini/gemini-cli/pull/14460)): Gemini hardcodes extension hooks at `<ext>/hooks/hooks.json`; manifest `hooks` field ignored | Gemini CLI | `AUTORUN_BUG_GEMINI_CLI_HOOKS_JSON_HARDCODED_BUG_14449_WORKAROUND_ENABLED` | `True` | Installer materializes `~/.gemini/extensions/<name>/` from template dir; `hook_entry.py` copied into `<ext>/hooks/` so `${extensionPath}/hooks/hook_entry.py` resolves |

### Bug #24115 & #14449 in depth

These two bugs co-motivate the split repo layout implemented in install.py
(`# --- BUG #24115 & #14449 WORKAROUND START ---`).

**Root causes (each bug independent of the other):**

1. **Claude Code bug #24115**: When `claude plugin list` runs, Claude scans
   `plugins/autorun/hooks/*.json` in the marketplace source directory (git
   checkout) in addition to the versioned cache. The plugin we ship is
   registered as a `directory`-type marketplace source pointing at the git
   repo itself (`~/.claude/plugins/known_marketplaces.json`), so Claude
   reads the source hooks/ directly. Its Zod schema rejects any unknown
   event name with `invalid_key`. Gemini event names (`BeforeTool`,
   `BeforeAgent`, etc.) present in that path silently disable ALL plugin
   hooks and show `ar@autorun: ✘ failed to load`.

2. **Gemini CLI bug #14449**: Gemini's extension hook loader hardcodes
   `<extension_root>/hooks/hooks.json`. The `hooks` field in
   `gemini-extension.json` is documented but ignored at runtime. PR #14460
   landed in Dec 2025 and should ship in a future release — until then we
   cannot redirect Gemini to look at a different file path.

**Why one workaround solves both:** Keep Claude's hooks at
`plugins/autorun/hooks/hooks.json` (default path, Claude-valid events only),
and stage Gemini's hooks at `plugins/autorun/src/autorun/gemini_template/hooks/hooks.json`
(outside Claude's scan surface). At install time, point `gemini extensions install`
at the template dir; it materializes `~/.gemini/extensions/<name>/` with the
hardcoded layout Gemini expects. Copy `hook_entry.py` into
`<ext>/hooks/` so `${extensionPath}/hooks/hook_entry.py` resolves.

**Pathway 2 & 6 (`gemini extensions install <github-url>` or `.` from
repo root):** committed symlinks at repo root (`./gemini-extension.json`,
`./hooks/`) redirect into the template so Gemini sees the required layout
when installing from the repo as a whole. The symlinks are outside Claude's
scan path.

**Configuration:** either environment variable or CONFIG entry controls
each workaround independently. Values: `true`/`1`/`auto` (on, default),
`false`/`0`/`never` (off — likely to produce a broken install until the
upstream bug is actually fixed).

```bash
# Disable Claude marketplace-scan workaround (requires #24115 to be fixed)
export AUTORUN_BUG_CLAUDE_CODE_MARKETPLACE_SOURCE_SCAN_BUG_24115_WORKAROUND_ENABLED=false

# Disable Gemini hardcoded-hooks-path workaround (requires #14449 to be fixed)
export AUTORUN_BUG_GEMINI_CLI_HOOKS_JSON_HARDCODED_BUG_14449_WORKAROUND_ENABLED=false
```

**When both bugs are fixed:** follow the deletion instructions in the
bracketed block in `plugins/autorun/src/autorun/install.py`. Short
summary: remove the helpers, move Gemini assets back to plugin root,
rename `hooks/hooks.json` to declare both CLI event sets (or keep them
separated at a finer granularity), delete the `BUG #24115 & #14449 TESTS`
block in `test_split_layout.py`, remove the two CONFIG keys, and drop the
repo-root shim symlinks.
