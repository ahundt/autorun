# Fix clautorun Marketplace Hooks Installation - Architecture Analysis

## Complete Handler Architecture from Git History

### Original Design (Oct 19, 2025 - Initial Commit)

**Three separate files with distinct purposes:**

| File | Original Purpose | Entry Point |
|------|-----------------|-------------|
| **main.py** | Interactive CLI command interceptor | Direct execution |
| **agent_sdk_hook.py** | Agent SDK hook integration (drop-in autorun5.py replacement) | Agent SDK hooks |
| **claude_code_plugin.py** | Claude Code slash command integration | Claude Code `/clautorun` command |

**Evidence:** Initial commit d5b109d: "Core files: main.py (Interactive), agent_sdk_hook.py (Agent SDK hooks), claude_code_plugin.py (Claude Code slash commands)"

### Evolution (Oct 2025 - Jan 2026)

**Dec 16, 2025** (commit f16a910): claude_code_plugin.py became a hook handler
- Added hook event routing based on `hook_event_name`
- Implemented three-stage verification
- Changed from slash command to hook handler

**Oct 22, 2025** (commit 00e95b1): main.py became a hook handler
- Added `claude_code_handler()` function
- Integrated with Agent SDK
- Two modes: HOOK_INTEGRATION and INTERACTIVE

**Result:** Both main.py AND claude_code_plugin.py now handle hooks (DRY violation)

### Current Architecture (Jan 2026)

| File | Lines | Actual Capabilities | Hooks? | Agent SDK Client? | Used By |
|------|-------|---------------------|--------|-------------------|---------|
| **main.py** | 1827 | Hook handler + Agent SDK client | ✅ | ✅ Can send TO Claude | `clautorun-interactive` |
| **claude_code_plugin.py** | 488 | Hook handler only (lightweight) | ✅ | ❌ No Agent SDK | `clautorun` command |
| **agent_sdk_hook.py** | 83 | Delegation to main.py | ✅ | ❌ Delegates to main.py | Not used |

### Functional Differentiation

**main.py can do what claude_code_plugin.py CANNOT:**

1. **Agent SDK Client Integration** (lines 1728-1847):
   - Import `ClaudeSDKClient` (line 55)
   - Send queries TO Claude Code (line 1831: `client.query()`)
   - Run interactive mode with bidirectional communication
   - Process user commands and forward to Claude

2. **Enhanced Verification** (lines 50-59, 1350-1433):
   - `RequirementVerificationEngine` integration
   - Evidence-based verification with requirement tracking
   - Enhanced transcript analysis

3. **AI Monitor Integration** (lines 38-46):
   - `ai_monitor` module for monitoring AI sessions
   - Process management and lifecycle tracking

4. **Two Operation Modes** (line 1689):
   - HOOK_INTEGRATION: Receive hooks from Claude Code
   - INTERACTIVE: Send queries to Claude Code via Agent SDK

**claude_code_plugin.py capabilities:**

1. **Hook Handler Only** (lines 446-493):
   - Receives hook events from Claude Code
   - Routes to inline handlers (UserPromptSubmit, PreToolUse, Stop)
   - Simpler, no Agent SDK dependency
   - 488 lines vs 1827 lines

2. **File Policy Enforcement** (lines 357-395):
   - Basic SEARCH/JUSTIFY/ALLOW policy enforcement
   - Inline implementation (duplicates main.py logic)

3. **Three-Stage Verification** (lines 248-354):
   - Simplified three-stage logic
   - Less sophisticated than main.py (no verification engine)

**agent_sdk_hook.py capabilities:**

1. **Clean Delegation Pattern** (83 lines total):
   - Imports handlers from main.py
   - Thin wrapper with no duplication
   - Shows best practice architecture

## Official Claude Agent SDK Documentation Findings

**Sources:**
- [Agent SDK Hooks Documentation](https://platform.claude.com/docs/en/agent-sdk/hooks)
- [Agent SDK Plugins Documentation](https://platform.claude.com/docs/en/agent-sdk/plugins)
- [Claude Code Hooks Reference](https://code.claude.com/docs/en/hooks)

### Python SDK Limitations (Official)

**Hooks NOT supported in Python SDK:**
- `SessionStart` - Only TypeScript (Python limitation)
- `SessionEnd` - Only TypeScript (Python limitation)
- `Notification` - Only TypeScript (Python limitation)
- `PostToolUseFailure` - Only TypeScript
- `PermissionRequest` - Only TypeScript
- `SubagentStart` - Only TypeScript

**Hooks SUPPORTED in Python SDK:**
- ✅ `PreToolUse` - Can block or modify tool inputs
- ✅ `PostToolUse` - Can log results and add context
- ✅ `UserPromptSubmit` - Can add additionalContext
- ✅ `Stop` - Can prevent session exit
- ✅ `SubagentStop` - Can track subagent completion
- ✅ `PreCompact` - Can archive before compaction

### systemMessage Visibility Issue (Confirmed)

**From official docs:**
> "The systemMessage field adds context to the conversation that the model sees, but it may not appear in all SDK output modes."

This confirms why plan-export's `systemMessage` isn't visible to user/Claude.

**Solution:** Use `additionalContext` field instead:
- Documented as working in: PreToolUse, PostToolUse, UserPromptSubmit
- Explicitly injects into conversation (visible to both Claude and user)

### No Restrictions on Agent SDK Client in Hooks

**Finding:** Documentation does NOT prohibit using `ClaudeSDKClient` from within hooks. Hooks can:
- Make async HTTP requests
- Call external APIs
- Perform complex operations
- **[Inference] Use ClaudeSDKClient** (no restriction mentioned)

**Conclusion:** main.py's Agent SDK client integration is allowed and not restricted by SDK limitations.

## Recommendation: Consolidate to main.py

### Should agent_sdk_hook.py be removed?

**Keep it** - It demonstrates best practice delegation pattern and is only 83 lines. Could be useful if we ever need a thin wrapper again.

### WOLOG Cleanup: Remove Both Duplicate Files

**Analysis:** Both agent_sdk_hook.py (83 lines) and claude_code_plugin.py (488 lines) can be removed WOLOG:

1. **All functionality already in main.py:**
   - Hook handlers: claude_code_handler, pretooluse_handler, stop_handler
   - Session management: session_state (via session_manager.py)
   - Response builders: build_hook_response, build_pretooluse_response
   - All exported via __init__.py

2. **Tests import from deleted files but equivalents exist:**
   - `from clautorun.claude_code_plugin import session_state` → `from clautorun import session_state`
   - `from clautorun.agent_sdk_hook import HOOK_HANDLERS` → `from clautorun.main import HANDLERS`
   - `from clautorun.agent_sdk_hook import main` → `from clautorun.main import main`

3. **Entry points need simple update:**
   ```toml
   [project.scripts]
   clautorun = "clautorun.main:main"              # Change from claude_code_plugin:main
   clautorun-interactive = "clautorun.main:main"  # Already correct
   ```

**Result:**
- ✅ Remove 571 lines of duplicated code (488 + 83)
- ✅ Single source of truth (main.py)
- ✅ No loss of functionality
- ✅ No thin wrapper needed
- ✅ Both UV commands still work

## Complete Clautorun Marketplace Inventory

### Plugin Overview Table

| Plugin | Version | Commands | Agents | Skills | Hooks | Install Status |
|--------|---------|----------|--------|--------|-------|----------------|
| **clautorun** | 0.6.0 | 39 | 2 | 2 | 4 events | ✅ Installed, ❌ Hooks broken |
| **plan-export** | 1.0.0 | 9 | 0 | 0 | 1 event | ✅ Installed, ⚠️ Working but silent |
| **pdf-extractor** | 0.1.0 | 1 | 0 | 1 | 0 | ❌ Not installed |

### Clautorun Plugin Components (39 commands, 2 agents, 2 skills, 4 hook events)

**AutoFile Policy Commands (12):**
`/cr:a` `/cr:allow` `/afa` `/cr:j` `/cr:justify` `/afj` `/cr:f` `/cr:find` `/afs` `/cr:st` `/cr:status` `/afst`

**Autorun Execution Commands (6):**
`/cr:go` `/cr:run` `/autorun` `/cr:gp` `/cr:proc` `/autoproc`

**Control Commands (6):**
`/cr:x` `/cr:stop` `/autostop` `/cr:sos` `/cr:estop` `/estop`

**Plan Management Commands (8):**
`/cr:pn` `/cr:plannew` `/cr:pr` `/cr:planrefine` `/cr:pu` `/cr:planupdate` `/cr:pp` `/cr:planprocess`

**Tmux Automation Commands (7):**
`/cr:tm` `/cr:tmux` `/tmux-session-management` `/cr:tt` `/cr:ttest` `/tmux-test-workflow` `/cr:tabs` `/cr:tabw` `/test`

**Agents (2):**
1. `cli-test-automation.md` - CLI testing automation with byobu
2. `tmux-session-automation.md` - Tmux session lifecycle management

**Skills (2):**
1. `CLI_USAGE_AND_TEST_AUTOMATION_WITH_BYOBU_TMUX_SESSIONS.md` - BYOBU/tmux patterns
2. `export-claude-sessions/SKILL.md` - Export Claude session transcripts

**Hooks (4 event types):**
- UserPromptSubmit: Matcher `/afs|/afa|/afj|/afst|/autorun|/autostop|/estop` + `/cr:` prefix
- PreToolUse: Matcher `Write|Bash` - File policy + command blocking
- Stop: Three-stage verification (AUTORUN_STAGE1/2/3_COMPLETE)
- SubagentStop: Three-stage verification for subagents

### Plan-Export Plugin Components (9 commands, 1 hook event)

**Commands:** `/plan-export:configure` `/plan-export:dir` `/plan-export:disable` `/plan-export:enable` `/plan-export:pattern` `/plan-export:preset` `/plan-export:presets` `/plan-export:reset` `/plan-export:status`

**Hooks:** PostToolUse (matcher: ExitPlanMode) - Exports plans to notes/

### PDF-Extractor Plugin Components (1 command, 1 skill)

**Commands:** `/pdf-extractor:extract`
**Skills:** pdf-extraction (9 backends)

## Capability Matrix

| Capability | main.py | claude_code_plugin.py | agent_sdk_hook.py |
|-----------|---------|----------------------|-------------------|
| **Hook Events** | ✅ All 4 | ✅ All 4 (duplicated) | ✅ All 4 (delegates) |
| **Agent SDK Client** | ✅ ClaudeSDKClient | ❌ None | ❌ Delegates to main.py |
| **Send TO Claude** | ✅ query(), client.query() | ❌ Cannot | ❌ Delegates to main.py |
| **Verification Engine** | ✅ RequirementVerificationEngine | ❌ None | ✅ Via main.py |
| **AI Monitor** | ✅ ai_monitor module | ❌ None | ✅ Via main.py |
| **Enhanced Transcript Analysis** | ✅ _enhance_evidence_with_analysis | ❌ Basic read_transcript | ✅ Via main.py |
| **Interactive Mode** | ✅ run_interactive_sdk | ❌ None | ❌ None |
| **Lines of Code** | 1827 | 488 | 83 |
| **Entry Point** | `clautorun-interactive` | `clautorun` | Not exposed |
| **Maintenance Status** | ✅ Actively maintained | ⚠️ Duplicates main.py | ✅ Clean pattern |

## Solution: Fix Hooks + Consolidate Architecture

### Fix 1: Update hooks.json to call main.py

File: `/Users/athundt/.claude/clautorun/plugins/clautorun/hooks/hooks.json`

**Change all occurrences of:**
```json
"command": "python3 ${CLAUDE_PLUGIN_ROOT}/src/clautorun/claude_code_plugin.py"
```

**To:**
```json
"command": "python3 ${CLAUDE_PLUGIN_ROOT}/src/clautorun/main.py"
```

**Why:** main.py is the source of truth with full verification engine, ai_monitor, and enhanced transcript analysis.

### Fix 2: Move sys.path setup from claude_code_plugin.py to main.py

**Remove from claude_code_plugin.py** (lines 22-27):
```python
# CRITICAL: Add plugin source to Python path for imports when called as hook
# Claude Code sets CLAUDE_PLUGIN_ROOT before executing hook commands
PLUGIN_ROOT = os.environ.get('CLAUDE_PLUGIN_ROOT')
if PLUGIN_ROOT:
    src_dir = os.path.join(PLUGIN_ROOT, 'src')
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
```

**Add to main.py** at line 29 (after standard library imports, before relative imports):
```python
# CRITICAL: Add plugin source to Python path for imports when called as hook
# Claude Code sets CLAUDE_PLUGIN_ROOT before executing hook commands
PLUGIN_ROOT = os.environ.get('CLAUDE_PLUGIN_ROOT')
if PLUGIN_ROOT:
    src_dir = os.path.join(PLUGIN_ROOT, 'src')
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
```

### Fix 3: Convert claude_code_plugin.py to thin wrapper (Future cleanup)

**Current:** 488 lines with duplicated hook handlers
**After:** ~30 lines wrapper that delegates to main.py

```python
#!/usr/bin/env python3
"""Clautorun CLI Command - Lightweight Hook Handler

BACKWARD COMPATIBILITY WRAPPER for UV 'clautorun' command.
Delegates all hook handling to main.py (source of truth).

Entry point: clautorun command (UV-installed at ~/.local/bin/clautorun)
Calls: This file via pyproject.toml [project.scripts] entry
Purpose: Provides 'clautorun' command for backward compatibility

For hook handling logic, see: main.py (1827 lines, source of truth)
For thin delegation pattern, see: agent_sdk_hook.py (83 lines, best practice)
"""
import sys
from clautorun.main import main

if __name__ == "__main__":
    sys.exit(main())
```

**Note:** This cleanup can be done after hooks are verified working.

### Fix 4: Add additionalContext to plan-export

File: `/Users/athundt/.claude/clautorun/plugins/plan-export/scripts/export-plan.py`

At line 474, add `"additionalContext"` field:

```python
if config.get("notify_claude", True):
    result = {
        "continue": True,
        "systemMessage": export_result["message"],
        "additionalContext": f"\n\n📋 {export_result['message']}\n"
    }
```

## Files to Modify

### Immediate Fixes (Hook Execution)

| File | Change | Lines |
|------|--------|-------|
| `plugins/clautorun/hooks/hooks.json` | Change command from claude_code_plugin.py to main.py | Replace 47 lines |
| `plugins/clautorun/src/clautorun/main.py` | Add sys.path setup at line 29 (already done in commit c4302ca) | +0 |
| `plugins/plan-export/scripts/export-plan.py` | Add additionalContext field at line 474 | +1 |

### WOLOG Cleanup (Remove Duplicate Files)

| File | Change | Lines |
|------|--------|-------|
| `plugins/clautorun/src/clautorun/claude_code_plugin.py` | DELETE entire file | -488 |
| `plugins/clautorun/src/clautorun/agent_sdk_hook.py` | DELETE entire file | -83 |
| `plugins/clautorun/pyproject.toml` | Update clautorun entry point + remove from coverage omit | 3 lines |
| `README.md` | Update 7 references to use main.py | ~7 lines |
| `plugins/clautorun/docs/INTEGRATION_GUIDE.md` | Update references to main.py | ~3 lines |
| `plugins/clautorun/agents/cli-test-automation.md` | Update test function reference | ~1 line |
| `plugins/clautorun/src/clautorun/error_handling.py` | Update example command | ~1 line |
| `plugins/clautorun/src/clautorun/main.py` | Remove references to deleted files from docstring | ~2 lines |
| `plugins/clautorun/tests/*.py` (7 test files) | Update imports to use main.py/__init__.py | ~15 lines |
| `plugins/clautorun/examples/example_of_importing_and_using_clautorun.py` | Update imports to use main.py | ~2 lines |

**Total: 15 files modified (2 deleted, 13 references updated), net -537 lines**

**install.py status:** ✅ No changes needed - only validates main.py exists (line 628), not deleted files

## Implementation Steps

### Step 1: Update hooks.json to call main.py

**Current state:** Commit c4302ca already has sys.path in main.py but hooks.json still calls claude_code_plugin.py

File: `/Users/athundt/.claude/clautorun/plugins/clautorun/hooks/hooks.json`

Change all 4 occurrences from:
```json
"command": "python3 ${CLAUDE_PLUGIN_ROOT}/src/clautorun/claude_code_plugin.py"
```

To:
```json
"command": "python3 ${CLAUDE_PLUGIN_ROOT}/src/clautorun/main.py"
```

**2a. Complete updated hooks.json:**

File: `/Users/athundt/.claude/clautorun/plugins/clautorun/hooks/hooks.json`

```json
{
  "description": "clautorun command interceptor and AutoFile policy enforcement hooks",
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/src/clautorun/main.py",
            "timeout": 10
          }
        ],
        "matcher": "/afs|/afa|/afj|/afst|/autorun|/autostop|/estop|/cr:"
      }
    ],
    "PreToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/src/clautorun/main.py",
            "timeout": 10
          }
        ],
        "matcher": "Write|Bash"
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/src/clautorun/main.py",
            "timeout": 10
          }
        ]
      }
    ],
    "SubagentStop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/src/clautorun/main.py",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

### Step 2: WOLOG Cleanup - Delete Duplicate Files

**2a. Delete claude_code_plugin.py:**
```bash
git rm plugins/clautorun/src/clautorun/claude_code_plugin.py
```

**2b. Delete agent_sdk_hook.py:**
```bash
git rm plugins/clautorun/src/clautorun/agent_sdk_hook.py
```

**2c. Update pyproject.toml:**

File: `/Users/athundt/.claude/clautorun/plugins/clautorun/pyproject.toml`

**At line 47, change entry point:**
```toml
clautorun = "clautorun.claude_code_plugin:main"
```

To:
```toml
clautorun = "clautorun.main:main"
```

**At lines 100-101, remove from coverage omit list:**

Delete these lines:
```toml
    "src/clautorun/agent_sdk_hook.py",
    "src/clautorun/claude_code_plugin.py",
```

**2d. Update README.md references (7 locations):**

File: `/Users/athundt/.claude/clautorun/README.md`

- Line 267: Change `agent_sdk_hook.py` → `main.py`
- Line 270: Change `claude_code_plugin.py` → `main.py`
- Line 894: Change `agent_sdk_hook.py` → `main.py`
- Line 1039: Remove line mentioning `agent_sdk_hook.py`
- Line 1042: Remove line mentioning `claude_code_plugin.py`
- Line 1062: Change "Hooks (`agent_sdk_hook.py`)" → "Hooks (`main.py`)"
- Line 1273: Change `claude_code_plugin:main` → `main:main`

**2e. Update docs/INTEGRATION_GUIDE.md:**

File: `/Users/athundt/.claude/clautorun/plugins/clautorun/docs/INTEGRATION_GUIDE.md`

- Line 35: Change `claude_code_plugin.py` → `main.py`
- Line 91-93: Change `agent_sdk_hook.py` → `main.py`

**2f. Update agents/cli-test-automation.md:**

File: `/Users/athundt/.claude/clautorun/plugins/clautorun/agents/cli-test-automation.md`

- Line 230: Change function name `test_claude_code_plugin` → `test_clautorun_main`

**2g. Update error_handling.py:**

File: `/Users/athundt/.claude/clautorun/plugins/clautorun/src/clautorun/error_handling.py`

- Line 99: Change `claude_code_plugin.py` → `main.py`

**2h. Update main.py docstring:**

File: `/Users/athundt/.claude/clautorun/plugins/clautorun/src/clautorun/main.py`

Remove lines 27-28 (references to deleted files):
```python
- claude_code_plugin.py: Legacy CLI command tool (duplicates logic for backward compatibility)
- agent_sdk_hook.py: Alternative delegation pattern (thin wrapper)
```

**2i. Update test imports (7 files):**

Replace all occurrences:
- `from clautorun.claude_code_plugin import session_state` → `from clautorun import session_state`
- `from clautorun.claude_code_plugin import main` → `from clautorun.main import main`
- `from clautorun.agent_sdk_hook import HOOK_HANDLERS` → `from clautorun.main import HANDLERS`
- `from clautorun.agent_sdk_hook import main` → `from clautorun.main import main`
- `from clautorun.agent_sdk_hook import build_hook_response` → `from clautorun import build_hook_response`
- `from clautorun.agent_sdk_hook import agent_sdk_*` → Direct imports from `clautorun` or `clautorun.main`
- `import clautorun.claude_code_plugin as plugin_module` → `import clautorun.main as plugin_module`

**Files to update:**
- tests/test_integration_comprehensive.py (4 import statements)
- tests/test_edge_cases_comprehensive.py (1 import statement)
- tests/test_thread_process_safety.py (4 import statements)
- tests/test_hook.py (1 import statement)
- tests/test_plugin.py (1 import statement)
- tests/test_session_lifecycle_edge_cases.py (6 import statements)
- examples/example_of_importing_and_using_clautorun.py (1 import statement)

**2j. Update documentation files:**

**README.md** (7 occurrences):
- Line 267: `agent_sdk_hook.py` → `main.py`
- Line 270: `claude_code_plugin.py` → `main.py`
- Line 894: `agent_sdk_hook.py` → `main.py`
- Line 1039: Delete line with `agent_sdk_hook.py` reference
- Line 1042: Delete line with `claude_code_plugin.py` reference
- Line 1062: `agent_sdk_hook.py` → `main.py`
- Line 1273: `claude_code_plugin:main` → `main:main`

**docs/INTEGRATION_GUIDE.md** (3 occurrences):
- Line 35: `claude_code_plugin.py` → `main.py`
- Line 91: Comment references `agent_sdk_hook.py` → `main.py`
- Line 93: `agent_sdk_hook.py` → `main.py`

**agents/cli-test-automation.md** (1 occurrence):
- Line 230: `test_claude_code_plugin` → `test_clautorun_main`

**error_handling.py** (1 occurrence):
- Line 99: `claude_code_plugin.py` → `main.py`

**main.py docstring** (2 lines):
- Lines 27-28: Delete references to deleted files

### Step 3: Fix plan-export visibility

File: `/Users/athundt/.claude/clautorun/plugins/plan-export/scripts/export-plan.py`

At line 474, change:
```python
result = {
    "continue": True,
    "systemMessage": export_result["message"]
}
```

To:
```python
result = {
    "continue": True,
    "systemMessage": export_result["message"],
    "additionalContext": f"\n\n📋 {export_result['message']}\n"
}
```

### Step 4: Commit WOLOG cleanup

```bash
cd ~/.claude/clautorun

git add -A  # Includes deletions and updates

git commit -m "refactor: WOLOG cleanup - remove duplicate hook handlers, consolidate to main.py

Clautorun plugin (v0.6.0):
- Delete claude_code_plugin.py (488 lines) - duplicates main.py hook logic
- Delete agent_sdk_hook.py (83 lines) - unused delegation wrapper
- Update hooks.json to call main.py directly for all 4 hook events
  Changed: python3 \${CLAUDE_PLUGIN_ROOT}/src/clautorun/main.py
- Update pyproject.toml clautorun entry point to main:main
- Update test imports (7 files) to use main.py/__init__.py exports
- Update example imports to use main.py exports

Plan-export plugin (v1.0.0):
- Add additionalContext field to export-plan.py at line 474
  Makes export location visible to both user and Claude

Why WOLOG (Without Loss of Generality):
- main.py (1827 lines) is source of truth with ALL capabilities
- claude_code_plugin.py duplicated 488 lines of main.py logic without:
  * verification_engine integration (lines 1350-1433)
  * ai_monitor integration (lines 38-46)
  * Agent SDK client for bidirectional communication (lines 1728-1847)
  * Enhanced transcript analysis
- agent_sdk_hook.py (83 lines) was unused demonstration of delegation pattern
- All exports already available via __init__.py (session_state, handlers, etc.)
- Both UV commands (clautorun, clautorun-interactive) now point to same main()
- Tests updated to import from canonical locations
- No functionality lost, 571 lines of duplication removed

Files modified:
- plugins/clautorun/hooks/hooks.json (all 4 hooks call main.py)
- plugins/clautorun/src/clautorun/claude_code_plugin.py (DELETED)
- plugins/clautorun/src/clautorun/agent_sdk_hook.py (DELETED)
- plugins/clautorun/pyproject.toml (clautorun entry point → main:main)
- plugins/clautorun/tests/*.py (7 files, updated imports)
- plugins/clautorun/examples/*.py (1 file, updated imports)
- plugins/plan-export/scripts/export-plan.py (additionalContext added)"

# Reinstall to update cached versions
uv run python plugins/clautorun/src/clautorun/install.py install --force
```

## Verification Tests

### Test 1: AutoFile Policy (UserPromptSubmit hook)
```bash
# Fresh Claude Code session (hooks use cached version, must restart)
/cr:f
/cr:st
# Expected: "AutoFile policy: strict-search - STRICT SEARCH..."
```

### Test 2: rm Blocker (PreToolUse hook on Bash)
```bash
# Ask Claude: "run rm test.txt"
# Expected: Block message with trash CLI suggestion
```

### Test 3: File Creation Block (PreToolUse hook on Write)
```bash
/cr:f
# Ask Claude: "create new file test.py"
# Expected: Block message forcing search
```

### Test 4: Three-Stage Verification with Verification Engine (Stop hook)
```bash
/cr:go Write hello world Python script with tests
# Expected:
# - Stage 1: AUTORUN_STAGE1_COMPLETE
# - Stage 2: AUTORUN_STAGE2_COMPLETE (with verification engine checking requirements)
# - Stage 3: AUTORUN_STAGE3_COMPLETE
```

### Test 5: Plan Export Visibility (PostToolUse hook)
```bash
# Exit plan mode in this session
# Expected: "📋 Exported plan to: notes/2026_01_28_HHMM_fix_clautorun_marketplace_hooks.md"
# Verify: ls -lt notes/
```

### Test 6: Pytest Suite
```bash
cd ~/.claude/clautorun/plugins/clautorun
uv run pytest tests/test_hook.py tests/test_pretooluse_policy_enforcement.py tests/test_three_stage_completion.py -v
```

## Why main.py Must Be Used (Not claude_code_plugin.py)

**Features Only in main.py:**

1. **Verification Engine** (lines 50-59, 1350-1433):
   - `RequirementVerificationEngine` with requirement tracking
   - Evidence-based verification with `RequirementEvidence`
   - Task completion analysis with concrete verification

2. **AI Monitor** (lines 38-46):
   - Session lifecycle management
   - Process monitoring and recovery
   - Crash detection and handling

3. **Enhanced Transcript Analysis** (lines 1435-1464):
   - `_enhance_evidence_with_analysis()` merges verification engine with transcript analyzer
   - Requirement extraction from user prompts
   - Evidence collection from conversation

4. **Agent SDK Client** (lines 1728-1847):
   - Can send queries TO Claude Code
   - Bidirectional communication
   - Interactive mode for testing

5. **More Sophisticated Stage Logic** (lines 1516-1660):
   - Plan acceptance detection (lines 1543-1550)
   - Countdown mechanism for stage 3
   - Alternating status/recovery injections

**claude_code_plugin.py has:**
- Basic hook handling (lines 248-437)
- Simple three-stage logic without verification engine
- File policy enforcement (basic)
- 488 lines of duplicated code

**Evidence:**
- pyproject.toml:45: `clautorun-interactive = "clautorun.main:main"` (designed for interactive/hook use)
- pyproject.toml:44: `clautorun = "clautorun.claude_code_plugin:main"` (lightweight CLI tool)

## Edge Cases Covered

1. ✅ CLAUDE_PLUGIN_ROOT not set - main.py falls back to INTERACTIVE mode (line 1689)
2. ✅ Import errors - main.py has comprehensive fallbacks (lines 31-59)
3. ✅ Malformed JSON - Caught at line 1694
4. ✅ Empty hook_event_name - Defaults to "?" and uses default_handler (line 1695, 1714)
5. ✅ Timeout - 10s for clautorun, 30s for plan-export
6. ✅ Concurrent sessions - session_manager uses file locking
7. ✅ Agent SDK not installed - main.py exits with clear error (line 57)
8. ✅ Verification engine not available - Fallback mode (line 59)

## Post-Implementation Verification

### Verify Entry Points Work

```bash
# Test clautorun command (should work after pyproject.toml update)
echo '{"hook_event_name":"UserPromptSubmit","prompt":"/cr:st","session_id":"test"}' | clautorun
# Expected: JSON response with AutoFile policy status

# Test clautorun-interactive command (already pointed to main.py)
echo '{"hook_event_name":"UserPromptSubmit","prompt":"/cr:st","session_id":"test"}' | clautorun-interactive
# Expected: Same JSON response

# Verify both commands are equivalent
which clautorun clautorun-interactive
# Expected: Both in ~/.local/bin/
```

### Verify Deleted Files Not Referenced

```bash
cd ~/.claude/clautorun/plugins/clautorun

# Check no remaining imports of deleted files
grep -r "from.*agent_sdk_hook import\|from.*claude_code_plugin import" src/ tests/ examples/ || echo "All clean"
# Expected: "All clean"

# Run full test suite
uv run pytest tests/ -v
# Expected: All tests pass with updated imports
```
