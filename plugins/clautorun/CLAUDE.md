# ⚠️ CRITICAL: Read from Git Repository, NOT Plugin Cache

## You Are Reading the WRONG Location If:

- Path contains: `~/.claude/plugins/cache/`
- Path contains: `/Users/athundt/.claude/plugins/cache/`
- You see version numbers like `0.5.0/` in the path

## CORRECT Location: Git Repository

**Always read from this location:**
```
/Users/athundt/.claude/clautorun/plugins/clautorun/
```

**Why:**
- ✅ Git repository with version control
- ✅ Uncommitted changes are visible
- ✅ Editable source files (changes take effect)
- ✅ Active development location
- ✅ Test files can be run and modified
- ✅ Has latest bug fixes and improvements

## WRONG Location: Plugin Cache (READ-ONLY)

**DO NOT read from:**
```
~/.claude/plugins/cache/clautorun/clautorun/0.5.0/
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
1. `/plugin install https://github.com/ahundt/clautorun.git`
2. Claude copies repository to: `~/.claude/plugins/cache/clautorun/clautorun/0.5.0/`
3. Plugin loads from cache location
4. **Problem**: AI may read cached code instead of git repository
5. **Issue**: Changes in dev repo may not be reflected in cache until reinstalled

## Directory Structure

```
clautorun/                             # Git repository root
├── plugins/clautorun/                 # <-- DEVELOPMENT LOCATION (READ THIS)
│   ├── src/clautorun/                 # Source code to edit
│   ├── tests/                         # Tests to run
│   ├── commands/                      # Plugin commands
│   ├── agents/                        # Agent definitions
│   ├── CLAUDE.md                      # <-- This file
│   └── .claude-plugin/                # Plugin manifest
└── ... (other files)

~/.claude/plugins/cache/clautorun/     # Plugin cache (DO NOT EDIT)
└── clautorun/
    └── 0.5.0/                         # Cached copy (READ-ONLY)
        ├── src/clautorun/             # May be outdated!
        ├── tests/
        └── ...
```

## Verification Commands

**Check if you're in the right location:**
```bash
# Should show git repository
git status

# Should show: "On branch fix-v0.4.1-opentelemetry-import"
# If error: "not a git repository", you're in the WRONG location

# Check current working directory
pwd
# Should be: /Users/athundt/.claude/clautorun/plugins/clautorun/
```

## Hook Error Prevention (CRITICAL)

Claude Code treats ANY stderr output from hooks as "hook error" and ignores the hook's JSON response. This silently disables ALL hook protections (rm blocking, git safety, etc.) while appearing to work.

**Rules to prevent hook errors:**

1. **pyproject.toml [tool.uv]**: NEVER add deprecated UV fields. UV versions remove fields silently. When UV encounters an unknown field, it prints a warning to stderr, which breaks ALL hooks. The `default-extras` field was removed in UV 0.9+. If you need default extras, put them in `[project] dependencies` instead.

2. **Slash commands**: ALL bash commands in `.md` files MUST use `uv run --project ${CLAUDE_PLUGIN_ROOT} python` — never bare `python3`. The `allowed-tools` frontmatter must use `Bash(uv *)` not `Bash(python3:*)`.

3. **Hook stderr**: hook_entry.py must NEVER write to stderr. All error handling must go through `fail_open()` which writes JSON to stdout.

4. **Cache sync**: After fixing pyproject.toml or hooks.json in the source, run the installer to sync to cache:
   ```bash
   uv run --project plugins/clautorun python -m clautorun --install --force
   ```
   Manual file copies to `~/.claude/plugins/cache/` are fragile and will be overwritten on next install. Always use the installer.
   NOTE: `clautorun --sync` is broken (bug: `find_marketplace_root()` returns plugin root, not workspace root). Use `--install --force` instead.

5. **Session restart**: Hook configuration is cached at session start. Fixes to hooks.json or pyproject.toml only take effect on the NEXT Claude Code session.

**Regression tests**: `test_hook_entry.py::TestUVCompatibility` and `test_hook_entry.py::TestCacheSync`

**Diagnosis**: Run `uv run --project <plugin_root> python -c "pass" 2>&1` — any output beyond "Building/Installed" lines is a problem.

## Dynamic Content in Slash Commands

Markdown commands can include dynamic bash output using `!` prefix ([docs](https://docs.anthropic.com/en/docs/claude-code/slash-commands)). To access CONFIG:

```bash
!`uv run --project ${CLAUDE_PLUGIN_ROOT} python -c "from clautorun.config import CONFIG; print(CONFIG['key'])"`
```

## If You See This File in Cache Location

1. Navigate to git repository: `cd /Users/athundt/.claude/clautorun/plugins/clautorun/`
2. Read CLAUDE.md from that location
3. Edit source files in that location
4. Run tests from that location
5. Commit changes to git repository
6. Reinstall plugin: `/plugin update clautorun`

## Summary

- **READ**: `/Users/athundt/.claude/clautorun/plugins/clautorun/`
- **EDIT**: `/Users/athundt/.claude/clautorun/plugins/clautorun/`
- **TEST**: `/Users/athundt/.claude/clautorun/plugins/clautorun/`
- **COMMIT**: `/Users/athundt/.claude/clautorun/` (git root)

**NEVER**: `~/.claude/plugins/cache/...` (wrong location, may be outdated)
