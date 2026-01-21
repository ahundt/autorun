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
