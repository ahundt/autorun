# Gemini CLI Integration — Full Plan (Phases 1-13) + Git Diff Critique

**Date**: 2026-02-14 22:00
**Branch**: `feature/gemini-cli-integration`
**Source**: Preserved from plan file before overwrite. Contains complete phase definitions, critiques, regressions, and architecture context.

---

## CRITICAL ASSESSMENT: 3 Regressions This Plan Would Introduce

### REGRESSION 1: Phase 4.2 MUST BE REMOVED — `ctx.ask()` breaks autonomous blocking

**Plan says**: Restore `ctx.ask()` in `plugins.py:161,173` for ExitPlanMode gate.
**This is wrong.** The current diff correctly uses `ctx.deny()`. Here's why:

- `ctx.ask("reason")` → `respond("ask", ...)` → `permissionDecision: "ask"` → exit code 0 (not 2)
- Claude Code at exit 0 with `permissionDecision: "ask"` **prompts the user** instead of blocking
- In autonomous operation, there's no user to click "allow/deny" → the AI stalls
- Even in interactive mode, "ask" lets the user click "allow" to bypass the gate — defeating its purpose

**`ctx.deny("reason")` is correct:**
- `ctx.deny("reason")` → `respond("deny", ...)` → `permissionDecision: "deny"` → exit code 2 + stderr
- Claude Code at exit 2 **blocks the tool** and shows the reason
- `continue: true` keeps the AI running → AI sees the reason and adjusts
- Gemini: `respond()` already maps `deny` correctly at `core.py:661-662`

**Action**: DELETE Phase 4.2 entirely. Keep `ctx.deny()` in plugins.py:161,173.

### REGRESSION 2: Phase 5 uses `threading.Lock()` in asyncio context — potential deadlock

**Plan says**: Add `threading.Lock()` to protect `active_pids` and `last_activity` in daemon.
**This is wrong.** `ClautorunDaemon` is asyncio-based:

- `handle_client()` is a coroutine on `asyncio.start_unix_server()`
- `_watchdog()` is a coroutine on the same event loop
- All coroutines run cooperatively in **one thread** — no preemption between `await` points
- Simple attribute assignments (`self.last_activity = time.time()`) and set operations (`self.active_pids.add(pid)`) are **atomic** between yield points
- `threading.Lock()` in an async context **blocks the entire event loop thread** → potential deadlock if any code path holds the lock across an `await`

**Action**: Either:
- (a) Remove Phase 5 entirely — asyncio cooperative concurrency makes these races impossible, OR
- (b) Replace `threading.Lock()` with `asyncio.Lock()` and use `async with self._pid_lock:` — but this adds complexity for no real benefit since there are no actual await points between the read and write of these variables

**Recommendation**: Remove Phase 5. The daemon's asyncio architecture already prevents these races. Add a code comment explaining why no locking is needed.

### REGRESSION 3: Phase 1 `command_response()` continue:True — tradeoff needs acknowledgment

**Plan says**: Change `command_response()` default from `continue: False` to `continue: True`.
**This is a tradeoff, not a pure fix:**

- `continue: False` on UserPromptSubmit means "don't send this prompt to the AI" — **correct semantic for slash commands** (Claude shouldn't try to process "/cr:st" as a user question)
- `continue: True` means the raw prompt text "/cr:st" ALSO gets sent to Claude, along with the systemMessage output — Claude might respond to the slash command text
- The "Operation stopped by hook" message may be a Claude Code UI issue with `continue: False`

**However**: `continue: True` is acceptable because:
- Claude sees the systemMessage with command output and likely responds to that instead
- It eliminates the "Operation stopped by hook" error message
- For Gemini, the behavior is similar

**Action**: Keep Phase 1 fix but:
- Add `suppressOutput: True` consideration — may prevent the raw slash command from showing
- Verify in real session that Claude doesn't try to process "/cr:st" text
- Document this tradeoff in the code comment

---

## Context

The clautorun plugin is ~60-70% through Gemini CLI integration. Critical issues:
1. **`/cr:` commands kill AI loop** — `command_response()` returns `continue: False`
2. **Gemini E2E test failures** — `cli_type` not propagating correctly
3. **Debug pollution** — raw file I/O scattered through hot paths
4. **Race conditions** — asyncio daemon, not actually racy (see Regression 2 above)
5. **Spec violations** — debug fields in schemas, `deny→ask` remapping breaks non-interactive

No install/restart steps (live sessions). TDD throughout.

---

## Good Diff Changes to PRESERVE (Do NOT revert these)

The current diff contains many valuable improvements. The plan only modifies the problematic parts. Everything listed here must be kept as-is:

### KEEP: main.py ctx propagation (38 call sites)
All 38 `ctx=ctx` and `event_name=` additions to `build_hook_response()` and `build_pretooluse_response()` calls are correct. They route CLI type through the handler chain.

### KEEP: main.py main(_exit=True) parameter
Allows tests to call `main(_exit=False)` and get return codes without sys.exit().

### KEEP: main.py should_use_exit2_workaround(payload)
Passing payload enables per-request CLI detection instead of ambient env-only detection.

### KEEP: main.py validate_hook_response import and usage
All calls to validate_hook_response() ensure schema compliance per CLI type.

### KEEP: main.py should_block_command() adds decision deny to all block results
Adding explicit decision field to session blocks, global blocks, and default integrations ensures consistent handling downstream. (But restore the commands/commands_description fields in Phase 4.3.)

### KEEP: main.py build_hook_response() Gemini decision mapping
block to deny mapping for Gemini in build_hook_response() is correct. Gemini uses deny, Claude uses block for Stop hooks.

### KEEP: main.py build_pretooluse_response() Gemini top-level decision
The Gemini top_decision mapping is correct. (Only revert the deny-to-ask remapping and debug fields.)

### KEEP: core.py EventContext.cli_type property
The cli_type property on EventContext and its use in respond() is the correct architecture.

### KEEP: core.py Gemini decision mapping in respond() PreToolUse/Stop pathways
The pathway-specific decision mapping (deny for Gemini, block for Claude) in respond() is correct per both specs.

### KEEP: core.py validate_hook_response() Gemini allowlist and HSO mapping
The Gemini path in validate_hook_response() that maps hookSpecificOutput.permissionDecision to top-level decision is correct.

### KEEP: core.py handle_client() passes cli_type to EventContext
Detecting CLI type from the raw payload and passing it through to EventContext is correct.

### KEEP: client.py refactor from sys.exit() to return int
output_hook_response() returning int instead of calling sys.exit() is correct for testability. Exit code chain verified complete.

### KEEP: client.py event and cli_type parameters on output_hook_response()
These enable per-request schema validation.

### KEEP: __main__.py return run_client() and return app_main()
Propagating exit codes correctly through run_hook_handler().

### KEEP: __init__.py updated fallback signatures
Adding event_name, ctx, and updating build_pretooluse_response fallback to match main.py.

### KEEP: install.py backup/restore Claude hooks during Gemini install
The hooks.json.claude-backup pattern is correct.

### KEEP: plan_export.py all responses through validate_hook_response()
All 12+ validate_hook_response() calls in handle_session_start() ensure schema compliance.

### KEEP: plugin.json and aix_manifest.py added hooks field
Adding hooks field to Claude manifest and AIX generator.

### KEEP: gemini-hooks.json added AfterAgent and AfterModel hooks
New Gemini hook events for autonomous loop detection and post-model processing.

### KEEP: hook_entry.py EOFError handling and event name parsing
Better stdin error handling and hook event name extraction for logging.

### KEEP: tmux_injector.py import os addition
Required for os.environ access.

### KEEP: test_hook.py main(_exit=False) usage
Tests legacy main() with _exit=False for proper exit code testing.

### KEEP: test_dual_platform_hooks_install.py import improvements
Using import hook_entry instead of exec(open()) and allowing .venv/bin/python in Gemini commands.

### KEEP: test_bootstrap_config.py --force flag update
Correct CLI argument.

### KEEP: test_command_blocking_comprehensive.py continue True for blocked commands
Aligns with the continue True semantics (tool blocked, AI continues).

### KEEP: test_core.py updated test assertions for continue True
Tests updated to match new continue True semantics.

### KEEP: test_dual_platform_response.py direct cli_type testing
Tests now pass cli_type directly instead of mocking env vars.

---

## Harsh Critique of Current Git Diff (26 files, +641 -322)

Summary of 12 critiques driving each phase. Full BEFORE/AFTER code follows in each phase.

### CRITIQUE 1: core.py Debug Logging Pollution (SEVERE)
`core.py:650-655`, `core.py:677-688`, `core.py:1024-1034` — 6+ instances of raw `open("~/.clautorun/daemon.log", "a")` with `import time` inside try blocks, bare `except Exception: pass`. Not using existing `logger` infrastructure. Performance drag on every hook call. **Drives Phase 2.**

### CRITIQUE 2: core.py Debug Fields in HOOK_SCHEMAS (SPEC VIOLATION)
`core.py:369-370` — `_cli_type`, `_cli_type_detected`, `_decision_input` added to allowed schema fields. Claude Code may reject unknown fields. These are internal debugging state leaking to the wire. Also in `allowed_gemini` at `core.py:432`. **Drives Phase 2.4-2.5.**

### CRITIQUE 3: core.py normalize_hook_payload() Leaks Internal Fields
`core.py:185-190` — `raw_event`, `type`, `sessionId`, `cli_type` stuffed into normalized payload. Mixes routing metadata with canonical data. `sessionId` duplicates `session_id`. **Drives Phase 2.6.**

### CRITIQUE 4: config.py detect_cli_type() is 120+ Lines of Spaghetti (SEVERE)
`config.py:420-562` — Was 20 clean lines with 3 checks. Now 140+ lines with 15+ raw debug log writes, redundant `sessionId` checks at 3 depths, Gemini event names checked AFTER normalization already mapped them. **Drives Phase 3.**

### CRITIQUE 5: client.py Good Refactoring, Verify Exit Code Chain
`client.py:86-151` — Refactor from `sys.exit()` to `return int` is correct. Exit code chain verified: `output_hook_response() -> run_client() -> run_hook_handler() -> main() -> sys.exit()` at `__main__.py:850`. No gap. **Status: VERIFIED OK.**

### CRITIQUE 6: main.py Removed commands and commands_description from Block Results
`main.py:949-958` — `commands` and `commands_description` fields carry safer alternative commands (e.g., `trash` instead of `rm`). Removing them means AI cannot suggest safe alternatives. Silent feature regression. **Drives Phase 4.3.**

### CRITIQUE 7: main.py build_pretooluse_response() Has Contradictory Decision Mapping
`main.py:1073-1113` — `deny -> ask` remapping changes UX from "blocked" to "Claude asks permission". Debug fields `_cli_type_detected`, `_decision_input` pollute response. `safe_reason = json.dumps(reason)[1:-1]` double-encodes special characters. **Drives Phase 4.1.**

### CRITIQUE 8: Tests Weakened Assertions (MINOR — 1 location, not 10+)
1 test location changed to accept `"deny" or "ask"`. Masks the real question of which value is correct. **Drives Phase 4.4.**

### CRITIQUE 9: gemini-extension.json Hooks Points to Claude Hooks
`gemini-extension.json:22` — `"hooks": "./hooks/hooks.json"` points to Claude hooks file, not `gemini-hooks.json`. install.py swaps at install time, but manifest at rest references wrong file. **Drives Phase 6.1.**

### CRITIQUE 10: test_gemini_e2e_improved.py Duplicate Key
`test_gemini_e2e_improved.py:578` — `"source": "gemini"` appears twice. Python silently takes last value. Copy-paste error. **Drives Phase 6.2.**

### ~~CRITIQUE 11~~: INVALID — `ctx.deny()` is CORRECT for ExitPlanMode Gate
`plugins.py:161,173` — `ctx.deny()` is correct. `deny` blocks the tool + AI continues with feedback. `ask` would pause AI and prompt user, breaking autonomous operation. See Regression 1 in Critical Assessment. **Phase 4.2 REMOVED.**

### CRITIQUE 12: command_response() Still Returns continue False (THE ORIGINAL BUG)
`core.py:797-818` — The diff does NOT fix the root cause of "Operation stopped by hook". `command_response()` still returns `continue: False` killing the AI loop for all `/cr:` commands. **Drives Phase 1.**

---

## Concise Execution Checklist (cross-references detailed phases below)

### Phase 1 Checklist: Fix command_response() (Critical)
- [x] 1.1 TDD: Write `test_command_response_continues_by_default` in `test_core.py`
- [x] 1.2 TDD: Write `test_command_response_can_halt` in `test_core.py`
- [x] 1.3 TDD: Write `test_command_response_no_response_key` in `test_core.py`
- [x] 1.4 Fix `command_response()` in `core.py:797-818`: add `continue_loop=True` param, default True
- [x] 1.5 Remove `"response"` key (non-spec backward compat)
- [x] 1.6 Route through `validate_hook_response()` for schema compliance
- [x] 1.7 Update estop/stop handlers in `plugins.py` to pass `continue_loop=False`

### Phase 2 Checklist: Remove Debug Pollution
- [x] 2.1 Remove raw `open("~/.clautorun/daemon.log")` from `core.py:respond()` (3 blocks)
- [x] 2.2 Remove raw debug log from `core.py:handle_client()` (1 block)
- [x] 2.3 Remove `_cli_type`, `_cli_type_detected`, `_decision_input` from HOOK_SCHEMAS `core.py:366-370`
- [x] 2.4 Remove same debug fields from `allowed_gemini` in `core.py:429-434`
- [x] 2.5 Remove `raw_event`, `type`, `sessionId`, `cli_type` from `normalize_hook_payload()` `core.py:185-190`
- [x] 2.6 Remove `_cli_type_detected`, `_decision_input` from `build_pretooluse_response()` `main.py:1115-1116`

### Phase 3 Checklist: Simplify detect_cli_type()
- [x] 3.1 TDD: Write 6 tests for `detect_cli_type()`
- [x] 3.2 Rewrite `detect_cli_type()` in `config.py:420-562` to ~35 lines (3-tier: payload, env, default)
- [x] 3.3 Simplify `should_use_exit2_workaround()` — remove debug logging

### Phase 4 Checklist: Fix Decision Mapping (Spec Compliance)
- [x] 4.1 Fix `build_pretooluse_response()` `main.py:1076-1129`: remove deny-to-ask, debug fields, double-encoding
- [x] 4.2 ~~Restore `ctx.ask()`~~ REMOVED — `ctx.deny()` is correct (see Regression 1 above). Keep current code.
- [x] 4.3 Restore `commands`/`commands_description` in `should_block_command()` `main.py:952-958`
- [x] 4.4 Fix test assertion: `"deny" or "ask"` back to strict `== "deny"` (only 1 location found, not 10+)

### Phase 5 Checklist: ~~Fix Daemon Thread Safety~~ REMOVED (see Regression 2)
- [x] 5.1-5.4 REMOVED — asyncio cooperative concurrency makes threading.Lock unnecessary and harmful.

### Phase 6 Checklist: Fix Gemini Manifest and Tests
- [x] 6.1 Fix `gemini-extension.json:22` hooks path to `./hooks/gemini-hooks.json`
- [x] 6.2 Fix duplicate `source` key in `test_gemini_e2e_improved.py:578`
- [x] 6.3 Remove no-op ternaries in `core.py:758,780`

### Phase 7 Checklist: Full Test Suite
- [ ] 7.1-7.7 Run full test suite — **70 FAILURES FOUND** (see section below)

### Phase 8 Checklist: Legacy/Daemon Alignment
- [ ] 8.1 Verify legacy `build_hook_response(True, ...)` matches `command_response(continue_loop=True)`
- [ ] 8.2 Verify legacy `build_hook_response(False, ...)` matches `command_response(continue_loop=False)`
- [ ] 8.3 Verify autorun activate uses `continue_loop=False`

### Phase 9 Checklist: Debug Logging Sweep
- [x] 9.1 Check `should_use_exit2_workaround()` for raw file logging
- [x] 9.2 Search entire diff for `open(os.path.expanduser` and replace all with `logger`

### Phase 10 Checklist: ai_monitor Shelve Fix
- [ ] 10.1 Wrap `ai_monitor.py:52` `shelve.open()` with `SessionLock` RAII

### Phase 11 Checklist: Gemini Hook Event Coverage
- [ ] 11.1 Verify tool name matchers in gemini-hooks.json
- [ ] 11.2 Verify command paths use `${extensionPath}`
- [ ] 11.3 Verify hooks complete within 5s timeout
- [ ] 11.4 Verify enableHooks and enableMessageBusIntegration documented

### Phase 12 Checklist: cli_type E2E Propagation
- [ ] 12.1 Verify handle_client() detects cli_type from ORIGINAL payload
- [ ] 12.2 Verify EventContext stores cli_type
- [ ] 12.3 Verify respond() uses self.cli_type
- [ ] 12.4 TDD: Write E2E test for Gemini payload -> decision deny
- [ ] 12.5 TDD: Write E2E test for Claude payload -> decision block

### Phase 13 Checklist: AIX Manifest Sync
- [x] 13.1 Run aix_manifest generate and verify outputs
- [x] 13.2 Fix aix_manifest.py to generate gemini-hooks.json for Gemini manifest

---

## CRITICAL BUG DISCOVERED IN PHASE 7: PreToolUse `continue: True` Regression

### 70 Test Failures — Root Cause Analysis

**Root cause**: Both `respond()` in core.py:696 and `build_pretooluse_response()` in main.py:1102 hardcode `"continue": True` for ALL PreToolUse responses. This was documented as a regression in `test_regression_pretooluse_blocking.py`:

> "core.py EventContext.respond("deny") returned continue=True for PreToolUse — commit 662d789 introduced, this session fixed"

**The tests are correct.** For PreToolUse:
- `continue: False` = "don't execute this tool" (tool blocked, AI sees reason)
- `continue: True` = "proceed with tool execution" (defeats blocking)

**This is DIFFERENT from UserPromptSubmit** where:
- `continue: True` = AI continues and sees systemMessage (correct for slash commands)
- `continue: False` = "Operation stopped by hook" message (the Phase 1 bug)

### Failure Categorization (70 total)

**Group A: PreToolUse `continue` regression (40 failures) — CODE FIX needed**
- `test_regression_pretooluse_blocking.py` — 16 failures
- `test_pretooluse_blocking_fix.py` — 2 failures
- `test_pretooluse_policy_enforcement.py` — 4 failures
- `test_hook_entry.py` (blocking tests) — 3 failures
- `test_plugin.py` (various) — ~8 failures
- `test_integration_comprehensive.py` — 2 failures
- Others — ~5 failures

**Fix**: Change `"continue": True` to `"continue": not is_deny` in core.py:696 and `"continue": decision == "allow"` in main.py:1102.

**Group B: Gemini cli_type detection (8 failures)**
- `test_gemini_e2e_improved.py::TestGeminiExtensionInstalledHook` — 8 failures
- Tests set `source: "gemini"` but EventContext auto-detects "claude" from env

**Fix**: Pass cli_type from payload to EventContext in pretooluse_handler.

**Group C: command_response() semantics (4 failures)**
- `test_plugin.py` autorun/stop tests expect `continue: False`
- `test_ai_monitor_integration.py` empty systemMessage

**Group D: Decision field consistency (4 failures)**
- Tests expect `decision == "deny"` but Claude mapping returns `"block"`

**Group E: Pre-existing / infrastructure (14 failures)**
- `test_install_pathways.py` — 10 failures (not in our diff)
- `test_hook_entry.py::TestAllLocationsSync` — 2 failures
- `test_session_lifecycle_edge_cases.py` — 1 failure
- `test_task_cli_commands.py` — 1 failure

### Fix Plan for Phase 7 Failures

See `notes/2026_02_14_2035_gemini_integration_phase7_status.md` for detailed status of completed phases and the newer plan file for the fix approach.

---

## Phase Details (Full BEFORE/AFTER Code)

### Phase 1: Fix `command_response()` Bug (DONE)

**File**: `plugins/clautorun/src/clautorun/core.py:797-818`

BEFORE:
```python
    def command_response(self, response_text: str) -> Dict:
        return {
            "continue": False,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": response_text,
            "response": response_text
        }
```

AFTER:
```python
    def command_response(self, response_text: str, continue_loop: bool = True) -> Dict:
        resp = {
            "continue": continue_loop,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": response_text,
        }
        return validate_hook_response(self._event, resp, cli_type=self.cli_type)
```

### Phase 2: Remove Debug Pollution (DONE)

Removed 4 raw `open("~/.clautorun/daemon.log")` blocks in core.py, debug fields from HOOK_SCHEMAS and allowed_gemini, leaked fields from normalize_hook_payload, debug fields from build_pretooluse_response.

### Phase 3: Simplify detect_cli_type() (DONE)

Rewrote from ~140 lines to ~35 lines with 3-tier detection (payload → env → default).

### Phase 4: Fix Decision Mapping (DONE)

Removed deny→ask remapping, double-encoding, restored commands/commands_description.

### Phase 6: Fix Gemini Manifest and Tests (DONE)

Fixed hooks path, duplicate key, no-op ternaries.

### Phase 9: Debug Logging Sweep (DONE)

Replaced all raw file I/O with logger.

### Phase 13: AIX Manifest Sync (DONE)

Added `gemini_manifest["hooks"] = "./hooks/gemini-hooks.json"`.

---

## EventContext `cli_type` Auto-Detection Fix

**File**: `core.py:495` — Changed `cli_type` default from `"claude"` to `None`
**File**: `core.py:518-522` — Added lazy auto-detection via `detect_cli_type()` in property
**File**: `test_blocking_integration.py:60` — Added `cli_type = "claude"` to MockContext
**Result**: Fixed 13 test failures where tests expected Gemini `"deny"` but got Claude `"block"`

---

## CRITICAL BUG: Plan Export Deadlock (RESOLVED)

### Symptoms
- `test_export_creates_file` hangs indefinitely (ignores pytest timeout)
- Production plan export also broken (user confirmed)
- Blocks at `atomic_update_tracking()` call in `PlanExport.export()` at `plan_export.py:655`

### Root Cause
50MB corrupted shelve file at `~/.claude/sessions/plugin___plan_export__.db.db` with 0 useful keys. Caused by `shelve.open(..., writeback=True)` in `session_manager.py:397` which caches ALL accessed objects and writes them all back on sync/close. Over many daemon sessions, this caused exponential growth.

### Fix Applied
Trashed the bloated file. Export works now.

### Prevention Needed
1. Add size check before `shelve.open()` — if > 5MB with few keys, recreate
2. Consider `writeback=False` for plan_export tracking
3. Add periodic compaction or use JSON file instead of shelve
4. Add timeout wrapper around `shelve.open()` to fail-open instead of deadlock

---

## Code Quality Issues Found

### Hardcoded capture sizes in test_dual_platform_hooks_install.py (FRAGILE)
- **File**: `plugins/clautorun/tests/test_dual_platform_hooks_install.py:637-655`
- **Problem**: Tests use `content[respond_idx:respond_idx + 6000]` and `content[func_idx:func_idx + 4000]`
- **Already broke once**: Had to increase from 4000 to 6000
- **Fix**: Use function-boundary detection instead of fixed window

---

## Architecture Context

### Dual CLI Hook Flow
```
Claude Code / Gemini CLI
        |
  hooks.json / gemini-hooks.json
        |
  hook_entry.py
        |
  __main__.py:run_hook_handler()
        |
  +-- DAEMON: client.py -> Unix socket -> ClautorunDaemon.handle_client()
  |     detect_cli_type(payload) → EventContext(cli_type=) → dispatch → respond → validate
  |
  +-- LEGACY: main.py:main()
        payload → handler(ctx) → build_pretooluse_response(ctx=ctx) → validate → sys.exit
```

### Response Schema Differences
| Field | Claude Code | Gemini CLI |
|---|---|---|
| Tool blocking decision | `hookSpecificOutput.permissionDecision: "deny"` | `decision: "deny"` |
| Tool blocking exit code | Exit 2 + stderr (bug #4669) | Exit 0 + JSON |
| Stop prevention | `decision: "block"` | `decision: "deny"` |

### Key Bug References
- **Bug #4669**: Claude Code ignores `permissionDecision: "deny"` at exit 0. Workaround: exit 2 + stderr.
- **Bug #10964**: Exit code 2 stderr goes to Claude AI, not to user.
- **GitHub issue #13155**: Gemini CLI requires both `enableHooks` and `enableMessageBusIntegration` settings.

---

## Remaining Plan Phases (Not Yet Completed)

### Phase 8: Verify Legacy/Daemon Alignment
- Verify `build_hook_response(True, ...)` matches `command_response(continue_loop=True)`
- Verify `build_hook_response(False, ...)` matches `command_response(continue_loop=False)`

### Phase 10: Fix ai_monitor.py Shelve Race Condition
- `ai_monitor.py:49-57` - Wrap `shelve.open()` with `SessionLock` RAII

### Phase 11: Verify Gemini Hook Event Coverage
- Verify tool name matchers, `${extensionPath}`, 5s timeout

### Phase 12: Verify cli_type E2E Propagation
- Write E2E tests for Gemini payload -> decision deny
- Write E2E tests for Claude payload -> decision block

---

## Key Files Modified

| File | Changes |
|------|---------|
| `core.py:495,518-522` | EventContext cli_type auto-detection |
| `core.py:781-799` | command_response() continue:True default |
| `core.py:366-370,429-434` | Removed debug fields from schemas |
| `core.py:177-190` | Cleaned normalize_hook_payload |
| `core.py:650-695,1031-1036` | Removed raw debug logging |
| `core.py:758,780` | Removed no-op ternaries |
| `config.py:420-562` | Rewrote detect_cli_type() (~140->~35 lines) |
| `main.py:1076-1113` | Fixed build_pretooluse_response() |
| `main.py:952-958` | Restored commands/commands_description |
| `plugins.py` | Added ctx._halt_ai for stop/estop |
| `aix_manifest.py` | Fixed Gemini hooks path |
| `gemini-extension.json` | Fixed hooks path |
| `test_blocking_integration.py:60` | Added cli_type to MockContext |
| `test_core.py` | 3 new TDD tests for command_response |
| `test_gemini_e2e_improved.py:578` | Fixed duplicate key |
| `test_e2e_policy_lifecycle.py` | Strict deny assertion |
