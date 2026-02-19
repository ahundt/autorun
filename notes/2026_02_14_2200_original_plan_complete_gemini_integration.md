# Plan: Complete Gemini CLI Integration + Fix Hook Stop Bug + Thread Safety

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

### KEEP: main.py build_pretooluse_response() continue True and Gemini top-level decision
The change from should_continue to continue True is correct. Tool denial should keep the AI loop running. The Gemini top_decision mapping is also correct. (Only revert the deny-to-ask remapping and debug fields.)

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
- [ ] 1.1 TDD: Write `test_command_response_continues_by_default` in `test_core.py`
- [ ] 1.2 TDD: Write `test_command_response_can_halt` in `test_core.py`
- [ ] 1.3 TDD: Write `test_command_response_no_response_key` in `test_core.py`
- [ ] 1.4 Fix `command_response()` in `core.py:797-818`: add `continue_loop=True` param, default True
- [ ] 1.5 Remove `"response"` key (non-spec backward compat)
- [ ] 1.6 Route through `validate_hook_response()` for schema compliance
- [ ] 1.7 Update estop/stop handlers in `plugins.py` to pass `continue_loop=False`

### Phase 2 Checklist: Remove Debug Pollution
- [ ] 2.1 Remove raw `open("~/.clautorun/daemon.log")` from `core.py:respond()` (3 blocks)
- [ ] 2.2 Remove raw debug log from `core.py:handle_client()` (1 block)
- [ ] 2.3 Remove `_cli_type`, `_cli_type_detected`, `_decision_input` from HOOK_SCHEMAS `core.py:366-370`
- [ ] 2.4 Remove same debug fields from `allowed_gemini` in `core.py:429-434`
- [ ] 2.5 Remove `raw_event`, `type`, `sessionId`, `cli_type` from `normalize_hook_payload()` `core.py:185-190`
- [ ] 2.6 Remove `_cli_type_detected`, `_decision_input` from `build_pretooluse_response()` `main.py:1115-1116`

### Phase 3 Checklist: Simplify detect_cli_type()
- [ ] 3.1 TDD: Write 6 tests for `detect_cli_type()` (payload, source, env, default, gemini events, claude events)
- [ ] 3.2 Rewrite `detect_cli_type()` in `config.py:420-562` to ~35 lines (3-tier: payload, env, default)
- [ ] 3.3 Simplify `should_use_exit2_workaround()` — remove debug logging

### Phase 4 Checklist: Fix Decision Mapping (Spec Compliance)
- [ ] 4.1 Fix `build_pretooluse_response()` `main.py:1076-1129`: remove deny-to-ask, debug fields, double-encoding
- [x] 4.2 ~~Restore `ctx.ask()`~~ REMOVED — `ctx.deny()` is correct (see Regression 1 above). Keep current code.
- [ ] 4.3 Restore `commands`/`commands_description` in `should_block_command()` `main.py:952-958`
- [ ] 4.4 Fix test assertion: `"deny" or "ask"` back to strict `== "deny"` (only 1 location found, not 10+)

### Phase 5 Checklist: ~~Fix Daemon Thread Safety~~ REMOVED (see Regression 2)
- [x] 5.1-5.4 REMOVED — asyncio cooperative concurrency makes threading.Lock unnecessary and harmful. Add code comment explaining why no lock is needed.

### Phase 6 Checklist: Fix Gemini Manifest and Tests
- [ ] 6.1 Fix `gemini-extension.json:22` hooks path to `./hooks/gemini-hooks.json`
- [ ] 6.2 Fix duplicate `source` key in `test_gemini_e2e_improved.py:578`
- [ ] 6.3 Remove no-op ternaries in `core.py:758,780`

### Phase 7 Checklist: Full Test Suite
- [ ] 7.1 `uv run pytest plugins/clautorun/tests/test_core.py -v`
- [ ] 7.2 `uv run pytest plugins/clautorun/tests/test_dual_platform_response.py -v`
- [ ] 7.3 `uv run pytest plugins/clautorun/tests/test_actual_command_blocking.py -v`
- [ ] 7.4 `uv run pytest plugins/clautorun/tests/test_gemini_e2e_improved.py -v`
- [ ] 7.5 `uv run pytest plugins/clautorun/tests/test_e2e_policy_lifecycle.py -v`
- [ ] 7.6 `uv run pytest plugins/clautorun/tests/test_blocking_integration.py -v`
- [ ] 7.7 `uv run pytest plugins/clautorun/tests/ -v --tb=short` (full suite)

### Phase 8 Checklist: Legacy/Daemon Alignment
- [ ] 8.1 Verify legacy `build_hook_response(True, ...)` matches `command_response(continue_loop=True)`
- [ ] 8.2 Verify legacy `build_hook_response(False, ...)` matches `command_response(continue_loop=False)`
- [ ] 8.3 Verify autorun activate uses `continue_loop=False`

### Phase 9 Checklist: Debug Logging Sweep
- [ ] 9.1 Check `should_use_exit2_workaround()` for raw file logging
- [ ] 9.2 Search entire diff for `open(os.path.expanduser` and replace all with `logger`

### Phase 10 Checklist: ai_monitor Shelve Fix
- [ ] 10.1 Wrap `ai_monitor.py:52` `shelve.open()` with `SessionLock` RAII

### Phase 11 Checklist: Gemini Hook Event Coverage
- [ ] 11.1 Verify tool name matchers in gemini-hooks.json (Gemini names not Claude names)
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
- [ ] 13.1 Run aix_manifest generate and verify outputs
- [ ] 13.2 Fix aix_manifest.py to generate gemini-hooks.json for Gemini manifest

### Execution Order

| Step | Depends On | Phase | Description |
|---|---|---|---|
| 1 | - | Phase 1 | Fix command_response() continue True default |
| 2 | - | Phase 2 | Remove debug pollution |
| 3 | - | Phase 3 | Simplify detect_cli_type() |
| 4 | 1,3 | Phase 4 | Fix decision mapping (4.2 REMOVED, 4.4 reduced to 1 fix) |
| ~~5~~ | ~~-~~ | ~~Phase 5~~ | ~~REMOVED — asyncio makes this unnecessary~~ |
| 6 | 4 | Phase 6 | Fix Gemini manifest and tests |
| 7 | All above | Phase 7 | Full test suite |
| 8 | 1 | Phase 8 | Verify legacy/daemon alignment |
| 9 | 2 | Phase 9 | Debug logging sweep |
| 10 | - | Phase 10 | Fix ai_monitor shelve |
| 11 | 6 | Phase 11 | Verify hook event coverage |
| 12 | 4 | Phase 12 | Verify cli_type E2E |
| 13 | 6 | Phase 13 | AIX manifest sync |

---

## Phase 1: Fix `command_response()` Bug — The Root Cause of "Operation stopped by hook"

### 1.1 Write tests first

**File**: `plugins/clautorun/tests/test_core.py`

Add after existing `test_command_response` tests:

```python
# File: plugins/clautorun/tests/test_core.py
# Purpose: TDD for command_response fix

def test_command_response_continues_by_default(self):
    """command_response must return continue=True so AI processes output."""
    ctx = EventContext(session_id="test", event="UserPromptSubmit")
    response = ctx.command_response("Policy: allow-all")
    assert response["continue"] is True, "AI loop must continue after command"
    assert response["systemMessage"] == "Policy: allow-all"

def test_command_response_can_halt(self):
    """estop/stop commands can opt into continue=False."""
    ctx = EventContext(session_id="test", event="UserPromptSubmit")
    response = ctx.command_response("Emergency stop!", continue_loop=False)
    assert response["continue"] is False
    assert response["systemMessage"] == "Emergency stop!"

def test_command_response_no_response_key(self):
    """command_response must not include non-spec 'response' key."""
    ctx = EventContext(session_id="test", event="UserPromptSubmit")
    response = ctx.command_response("test")
    assert "response" not in response
```

### 1.2 Fix `command_response()`

**File**: `plugins/clautorun/src/clautorun/core.py:797-818`

BEFORE:
```python
    def command_response(self, response_text: str) -> Dict:
        """
        Response for locally-handled commands (UserPromptSubmit).

        Commands handled locally should NOT continue to AI.

        Args:
            response_text: The command output message

        Returns:
            dict: Hook response with continue=False and response text

        Usage:
            return ctx.command_response("✅ AutoFile policy: strict-search")
        """
        return {
            "continue": False,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": response_text,
            "response": response_text  # Backward compatibility with tests
        }
```

AFTER:
```python
    def command_response(self, response_text: str, continue_loop: bool = True) -> Dict:
        """
        Response for locally-handled commands (UserPromptSubmit).

        Args:
            response_text: The command output message
            continue_loop: True (default) keeps AI running. False for estop/stop.

        Usage:
            return ctx.command_response("✅ AutoFile policy: strict-search")
            return ctx.command_response("Emergency stop!", continue_loop=False)
        """
        resp = {
            "continue": continue_loop,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": response_text,
        }
        return validate_hook_response(self._event, resp, cli_type=self.cli_type)
```

### 1.3 Update estop/stop to use `continue_loop=False`

**File**: `plugins/clautorun/src/clautorun/plugins.py` — search for estop/stop command handlers that call `command_response`. Those that intentionally halt the AI must pass `continue_loop=False`.

Search pattern: `command_response` in plugins.py. Only emergency stop and graceful stop should use `False`.

---

## Phase 2: Remove Debug Pollution

### 2.1 Remove raw file logging from `core.py:respond()`

**File**: `plugins/clautorun/src/clautorun/core.py:650-655`

BEFORE:
```python
        cli_type = self.cli_type
        try:
            with open(os.path.expanduser("~/.clautorun/daemon.log"), "a") as f:
                import time
                f.write(f"[{time.time()}] respond: START event={self._event} cli_type={cli_type} decision={decision}\n")
        except Exception: pass
        # This keeps both CLIs as first-class citizens by using the best available
```

AFTER:
```python
        cli_type = self.cli_type
        logger.debug(f"respond: event={self._event} cli_type={cli_type} decision={decision}")
        # This keeps both CLIs as first-class citizens by using the best available
```

### 2.2 Remove raw file logging from `core.py:respond()` PreToolUse Gemini branch

**File**: `plugins/clautorun/src/clautorun/core.py:684-688`

BEFORE:
```python
                top_decision = "allow" if decision == "allow" else "deny"
                try:
                    with open(os.path.expanduser("~/.clautorun/daemon.log"), "a") as f:
                        import time
                        f.write(f"[{time.time()}] respond: event={self._event} cli_type={cli_type} decision={decision} -> top_decision={top_decision}\n")
                except Exception: pass
```

AFTER:
```python
                top_decision = "allow" if decision == "allow" else "deny"
                logger.debug(f"respond: gemini PreToolUse decision={decision} -> top_decision={top_decision}")
```

### 2.3 Remove raw file logging from `core.py:handle_client()`

**File**: `plugins/clautorun/src/clautorun/core.py:1031-1036`

BEFORE:
```python
            cli_type = detect_cli_type(payload)
            # Log for debugging
            try:
                with open(os.path.expanduser("~/.clautorun/daemon.log"), "a") as f:
                    import time
                    f.write(f"[{time.time()}] handle_client: detected cli_type={cli_type} for event={event} tool={tool}\n")
            except Exception: pass
            logger.info(f"handle_client: detected cli_type={cli_type} for event={event}")
```

AFTER:
```python
            cli_type = detect_cli_type(payload)
            logger.info(f"handle_client: cli_type={cli_type} event={event} tool={tool}")
```

### 2.4 Remove debug fields from `HOOK_SCHEMAS`

**File**: `plugins/clautorun/src/clautorun/core.py:366-370`

BEFORE:
```python
    "PreToolUse": {
        "root": {"continue", "stopReason", "suppressOutput", "systemMessage",
                 "decision", "permissionDecision", "reason", "hookSpecificOutput",
                 "_cli_type", "_cli_type_detected", "_decision_input"},
        "hso": {"hookEventName", "permissionDecision", "permissionDecisionReason", "updatedInput"}
    },
```

AFTER:
```python
    "PreToolUse": {
        "root": {"continue", "stopReason", "suppressOutput", "systemMessage",
                 "decision", "permissionDecision", "reason", "hookSpecificOutput"},
        "hso": {"hookEventName", "permissionDecision", "permissionDecisionReason", "updatedInput"}
    },
```

### 2.5 Remove debug fields from `allowed_gemini`

**File**: `plugins/clautorun/src/clautorun/core.py:429-434`

BEFORE:
```python
        allowed_gemini = {
            "continue", "decision", "reason", "systemMessage", "stopReason",
            "hookSpecificOutput", "permissionDecision", "suppressOutput",
            "_cli_type", "_cli_type_detected", "_decision_input"
        }
```

AFTER:
```python
        allowed_gemini = {
            "continue", "decision", "reason", "systemMessage", "stopReason",
            "hookSpecificOutput", "permissionDecision", "suppressOutput"
        }
```

### 2.6 Remove leaked fields from `normalize_hook_payload()`

**File**: `plugins/clautorun/src/clautorun/core.py:177-190`

BEFORE:
```python
    return {
        "hook_event_name": event,
        "session_id": session_id,
        "prompt": payload.get("prompt", ""),
        "tool_name": payload.get("tool_name") or payload.get("toolName", ""),
        "tool_input": payload.get("tool_input") or payload.get("toolInput", {}),
        "tool_result": payload.get("tool_result") or payload.get("toolResult"),
        "session_transcript": transcript,
        # Preserve original event names and markers for Gemini detection
        "raw_event": raw_event,
        "type": payload.get("type"),
        "sessionId": session_id,
        "cli_type": payload.get("cli_type"),
    }
```

AFTER:
```python
    return {
        "hook_event_name": event,
        "session_id": session_id,
        "prompt": payload.get("prompt", ""),
        "tool_name": payload.get("tool_name") or payload.get("toolName", ""),
        "tool_input": payload.get("tool_input") or payload.get("toolInput", {}),
        "tool_result": payload.get("tool_result") or payload.get("toolResult"),
        "session_transcript": transcript,
    }
```

Note: `detect_cli_type()` must be called on the ORIGINAL payload (before normalization) since normalization strips the Gemini-specific fields it needs. This is already done correctly in `handle_client()` at line 1029.

### 2.7 Remove debug fields from `build_pretooluse_response()`

**File**: `plugins/clautorun/src/clautorun/main.py:1115-1116`

BEFORE:
```python
        "_cli_type_detected": cli_type,  # DEBUG
        "_decision_input": decision,     # DEBUG
```

AFTER: Delete these two lines entirely.

---

## Phase 3: Simplify `detect_cli_type()`

### 3.1 Write tests first

**File**: `plugins/clautorun/tests/test_core.py` (or new `test_config.py`)

```python
# File: plugins/clautorun/tests/test_core.py
# Purpose: TDD for simplified detect_cli_type

class TestDetectCliType:
    def test_explicit_cli_type_in_payload(self):
        from clautorun.config import detect_cli_type
        assert detect_cli_type({"cli_type": "gemini"}) == "gemini"
        assert detect_cli_type({"cli_type": "claude"}) == "claude"

    def test_source_field(self):
        from clautorun.config import detect_cli_type
        assert detect_cli_type({"source": "gemini"}) == "gemini"
        assert detect_cli_type({"source": "claude"}) == "claude"

    def test_gemini_env_var(self):
        from clautorun.config import detect_cli_type
        import os
        with mock.patch.dict(os.environ, {"GEMINI_SESSION_ID": "abc"}):
            assert detect_cli_type({}) == "gemini"
            assert detect_cli_type() == "gemini"

    def test_default_is_claude(self):
        from clautorun.config import detect_cli_type
        import os
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("GEMINI_")}
        with mock.patch.dict(os.environ, env, clear=True):
            assert detect_cli_type() == "claude"
            assert detect_cli_type({}) == "claude"

    def test_gemini_event_names_in_raw_payload(self):
        from clautorun.config import detect_cli_type
        assert detect_cli_type({"hook_event_name": "BeforeTool"}) == "gemini"
        assert detect_cli_type({"hook_event_name": "AfterTool"}) == "gemini"

    def test_claude_event_names(self):
        from clautorun.config import detect_cli_type
        import os
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("GEMINI_")}
        with mock.patch.dict(os.environ, env, clear=True):
            assert detect_cli_type({"hook_event_name": "PreToolUse"}) == "claude"
```

### 3.2 Rewrite `detect_cli_type()`

**File**: `plugins/clautorun/src/clautorun/config.py:420-562`

BEFORE: ~140 lines with 15+ raw `open()` debug writes, redundant checks

AFTER:
```python
# Gemini-only event names (pre-normalization)
_GEMINI_EVENTS = frozenset({"BeforeTool", "AfterTool", "BeforeAgent", "AfterAgent",
                             "BeforeModel", "AfterModel", "BeforeToolSelection"})

def detect_cli_type(payload: dict = None) -> str:
    """Detect which CLI is calling (Claude Code vs Gemini CLI).

    Detection order (most reliable first):
    1. Explicit cli_type or source in payload
    2. Gemini-specific event names in payload (pre-normalization only)
    3. GEMINI_SESSION_ID or GEMINI_PROJECT_DIR env vars
    4. Default to "claude" (safer for bug #4669 workaround)

    Returns:
        "claude" or "gemini"
    """
    import os

    if payload:
        # Tier 1: Explicit markers
        if payload.get("cli_type") in ("gemini", "claude"):
            return payload["cli_type"]
        if payload.get("source") in ("gemini", "claude"):
            return payload["source"]

        # Tier 2: Gemini-specific signals
        if payload.get("GEMINI_SESSION_ID") or payload.get("sessionId"):
            return "gemini"
        if payload.get("hook_event_name") in _GEMINI_EVENTS:
            return "gemini"
        transcript_path = str(payload.get("transcript_path", ""))
        if ".gemini" in transcript_path:
            return "gemini"

    # Tier 3: Environment variables
    if os.environ.get("GEMINI_SESSION_ID") or os.environ.get("GEMINI_PROJECT_DIR"):
        return "gemini"

    # Default: Claude (safer - applies exit-2 workaround)
    return "claude"
```

### 3.3 Simplify `should_use_exit2_workaround()`

**File**: `plugins/clautorun/src/clautorun/config.py`

BEFORE: ~30 lines with debug file logging

AFTER:
```python
def should_use_exit2_workaround(payload: dict = None) -> bool:
    """Check if exit-2 workaround should be applied for bug #4669.

    Modes (CLAUTORUN_EXIT2_WORKAROUND env var):
    - "auto" (default): Workaround ONLY for Claude Code
    - "always": Force for all CLIs (testing)
    - "never": Disable for all CLIs (testing/future)
    """
    import os
    mode = os.environ.get('CLAUTORUN_EXIT2_WORKAROUND', 'auto').lower()
    if mode == "always":
        return True
    if mode == "never":
        return False
    return detect_cli_type(payload) == "claude"
```

---

## Phase 4: Fix Decision Mapping (Spec Compliance)

### 4.1 Fix `build_pretooluse_response()` — remove `deny→ask` remapping

**File**: `plugins/clautorun/src/clautorun/main.py:1076-1129`

The `deny→ask` remapping is wrong for non-interactive operation. Bug #4669 workaround is exit code 2, not decision remapping. `permissionDecision: "deny"` must stay `"deny"`.

BEFORE:
```python
    safe_reason = json.dumps(reason)[1:-1] if reason else ""

    # Pathway selection for Claude bug #4669 workaround (deny -> ask)
    claude_decision = decision

    if cli_type == "gemini":
        top_decision = "allow" if decision == "allow" else "deny"
    else:
        top_decision = "approve" if decision == "allow" else "block"
        if decision == "deny":
            claude_decision = "ask"

    gemini_reason = reason if cli_type == "gemini" else safe_reason

    response = {
        "decision": top_decision,
        "reason": gemini_reason,
        "_cli_type_detected": cli_type,  # DEBUG
        "_decision_input": decision,     # DEBUG
        "continue": True,
        "stopReason": "",
        "suppressOutput": False,
        "systemMessage": gemini_reason,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": claude_decision,
            "permissionDecisionReason": reason
        },
    }

    return validate_hook_response("PreToolUse", response, cli_type=cli_type)
```

AFTER:
```python
    if cli_type == "gemini":
        top_decision = "allow" if decision == "allow" else "deny"
    else:
        top_decision = "approve" if decision == "allow" else "block"

    response = {
        "decision": top_decision,
        "reason": reason,
        "continue": True,
        "stopReason": "",
        "suppressOutput": False,
        "systemMessage": reason,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason
        },
    }

    return validate_hook_response("PreToolUse", response, cli_type=cli_type)
```

Key changes:
- Remove `deny→ask` remapping (`claude_decision` variable deleted)
- Remove `safe_reason = json.dumps(reason)[1:-1]` double-encoding
- Remove `_cli_type_detected` and `_decision_input` debug fields
- `permissionDecision` stays as the original `decision` value

### 4.2 REMOVED — Keep `ctx.deny()` in ExitPlanMode gate

**NO CHANGE NEEDED.** The current `ctx.deny()` at `plugins.py:161,173` is correct.

Rationale (see Regression 1 in Critical Assessment):
- `ctx.deny()` → `permissionDecision: "deny"` → exit 2 → Claude blocks tool + AI continues
- `ctx.ask()` → `permissionDecision: "ask"` → exit 0 → Claude prompts user (breaks autonomous mode)
- For Gemini: `respond()` already maps "deny" correctly at `core.py:661-662`

### 4.3 Restore `commands` and `commands_description` in `should_block_command()`

**File**: `plugins/clautorun/src/clautorun/main.py:952-958`

BEFORE (current diff):
```python
            return {
                "pattern": pattern,
                "suggestion": config["suggestion"],
                "pattern_type": "literal",
                "decision": "deny"
            }
```

AFTER:
```python
            result = {
                "pattern": pattern,
                "suggestion": config["suggestion"],
                "pattern_type": "literal",
                "decision": "deny"
            }
            if config.get("commands"):
                result["commands"] = config["commands"]
            if config.get("commands_description"):
                result["commands_description"] = config["commands_description"]
            return result
```

### 4.4 Fix weakened test assertion

The diff weakened 1 test assertion (not 10+ as initially claimed):
- `test_cli_integration.py` — `assert any(d in result2.stdout for d in ["DECISION:deny", "DECISION:ask"])` → change to `assert "DECISION:deny" in result2.stdout`

The `deny→ask` remapping is being removed, so `deny` is the only correct value.

---

## Phase 5: REMOVED — No Thread Safety Fix Needed

**Reason**: `ClautorunDaemon` uses `asyncio.start_unix_server()`. All coroutines (`handle_client()`, `_watchdog()`) run cooperatively on the **same event loop thread**. Between `await` points, no preemption occurs, so `active_pids` set operations and `last_activity` assignments are atomic.

Using `threading.Lock()` in an async context would **block the entire event loop thread**, potentially causing deadlocks. `asyncio.Lock()` would work but adds complexity for zero benefit since there are no actual race conditions.

**Action**: Add a brief code comment at `core.py:964` explaining this design choice:
```python
        # Note: active_pids and last_activity are safe without locks because
        # asyncio coroutines don't preempt between await points (cooperative concurrency).
        self.active_pids: Set[int] = set()
```

---

## Phase 6: Fix Gemini Manifest and Tests

### 6.1 Fix `gemini-extension.json` hooks path

**File**: `plugins/clautorun/gemini-extension.json:22`

BEFORE:
```json
  "hooks": "./hooks/hooks.json",
```

AFTER:
```json
  "hooks": "./hooks/gemini-hooks.json",
```

### 6.2 Fix duplicate key in test

**File**: `plugins/clautorun/tests/test_gemini_e2e_improved.py:578`

BEFORE:
```python
        payload = {
            "hook_event_name": "BeforeTool",
            "source": "gemini",
            "source": "gemini",
```

AFTER:
```python
        payload = {
            "hook_event_name": "BeforeTool",
            "source": "gemini",
```

### 6.3 Remove no-op ternary in `respond()` Stop/SessionStart paths

**File**: `plugins/clautorun/src/clautorun/core.py:758,780`

BEFORE:
```python
                    "systemMessage": msg_reason if cli_type == "gemini" else msg_reason,
```

AFTER:
```python
                    "systemMessage": msg_reason,
```

Same no-op ternary at line 780.

---

## Phase 7: Full Test Suite Verification

```bash
# Run in order
uv run pytest plugins/clautorun/tests/test_core.py -v
uv run pytest plugins/clautorun/tests/test_dual_platform_response.py -v
uv run pytest plugins/clautorun/tests/test_actual_command_blocking.py -v
uv run pytest plugins/clautorun/tests/test_gemini_e2e_improved.py -v
uv run pytest plugins/clautorun/tests/test_e2e_policy_lifecycle.py -v
uv run pytest plugins/clautorun/tests/test_blocking_integration.py -v
uv run pytest plugins/clautorun/tests/ -v --tb=short  # Full suite
```

---

## Key Files Summary

| File | Changes |
|------|---------|
| `core.py:797-818` | Fix `command_response()` — `continue: True` default, `continue_loop` param |
| `core.py:650-695` | Remove 3 raw debug log blocks, use `logger` |
| `core.py:1031-1036` | Remove 1 raw debug log block |
| `core.py:366-370` | Remove `_cli_type*` from HOOK_SCHEMAS |
| `core.py:429-434` | Remove `_cli_type*` from allowed_gemini |
| `core.py:177-190` | Remove leaked fields from normalize_hook_payload |
| `core.py:758,780` | Remove no-op ternaries |
| `core.py:964-981` | Add `_pid_lock` for thread safety |
| `core.py:999-1000,1018-1022` | Lock `active_pids` and `last_activity` mutations |
| `core.py:1113-1125` | Lock watchdog PID cleanup and idle check |
| `config.py:420-562` | Rewrite `detect_cli_type()` to ~35 clean lines |
| `config.py:should_use_exit2_workaround` | Remove debug logging |
| `main.py:1076-1129` | Fix `build_pretooluse_response()` — remove deny→ask, debug fields |
| `main.py:952-958` | Restore `commands`/`commands_description` fields |
| `plugins.py:161,173` | Restore `ctx.ask()` for ExitPlanMode gate |
| `gemini-extension.json:22` | Fix hooks path to `gemini-hooks.json` |
| `test_gemini_e2e_improved.py:578` | Fix duplicate key |
| `test_*.py` (6 files) | Restore strict `== "deny"` assertions |

## Phase 8: Verify Legacy main.py UserPromptSubmit Handler Alignment

The legacy `main.py:claude_code_handler()` at line 1660-1679 uses `build_hook_response()` for UserPromptSubmit commands. This is the legacy path (CLAUTORUN_USE_DAEMON=0). The daemon path uses `core.py:command_response()`.

These two paths must produce equivalent responses. Currently:
- Legacy: `build_hook_response(False, "", response)` for stop, `build_hook_response(True, "", response)` for status
- Daemon: `ctx.command_response(text)` which returns `continue: False` (the bug)

After our fix, `command_response()` defaults to `continue: True`. Verify:

- [ ] 8.1 Legacy `build_hook_response(True, "", response)` for policy/status commands matches `command_response("text", continue_loop=True)` — both produce `continue: True`
- [ ] 8.2 Legacy `build_hook_response(False, "", response)` for stop commands matches `command_response("text", continue_loop=False)` — both produce `continue: False`
- [ ] 8.3 Legacy `build_hook_response(False, "", response)` for autorun activate command should use `continue_loop=False` (injection template is complete, AI should NOT continue processing)

**Note**: The legacy main.py path already uses `build_hook_response(True, ...)` for normal commands and `build_hook_response(False, ...)` for stop/activate. This is correct. Only the daemon path via `command_response()` was broken.

---

## Phase 9: Verify config.py Debug Logging Cleanup Throughout

The `detect_cli_type()` rewrite in Phase 3 addresses the biggest instance, but check for remaining raw debug logging in config.py:

- [ ] 9.1 Check `should_use_exit2_workaround()` for raw file logging — replace with logger
- [ ] 9.2 Check any other functions in config.py added in this diff for raw `open()` patterns
- [ ] 9.3 Search entire diff for `open(os.path.expanduser` pattern and replace all with `logger`

---

## Phase 10: Fix ai_monitor.py Shelve Race Condition

**Severity**: HIGH — `shelve.open()` without RAII locking can corrupt data under concurrent access.

**File**: `plugins/clautorun/src/clautorun/ai_monitor.py:49-57`

BEFORE:
```python
@contextmanager
def monitor_state(session_id):
    s = shelve.open(str(STATE_DIR / f"monitor-{session_id}.db"), writeback=True)
    try:
        yield s
    finally:
        s.sync()
        s.close()
```

AFTER:
```python
@contextmanager
def monitor_state(session_id):
    from .session_manager import session_state
    with session_state(session_id, prefix="monitor") as state:
        yield state
```

If `session_state()` does not support a prefix parameter, use `SessionLock` directly:
```python
@contextmanager
def monitor_state(session_id):
    from .session_manager import SessionLock
    lock = SessionLock(f"monitor-{session_id}", timeout=5.0)
    with lock:
        s = shelve.open(str(STATE_DIR / f"monitor-{session_id}.db"), writeback=True)
        try:
            yield s
        finally:
            s.sync()
            s.close()
```

---

## Phase 11: Verify Gemini Hook Event Coverage

From exploration: gemini-hooks.json currently covers 7 events. Verify against Gemini CLI spec (10 available events):

**File**: `plugins/clautorun/hooks/gemini-hooks.json`

| Gemini Event | Mapped Internal | Covered | Purpose |
|---|---|---|---|
| SessionStart | SessionStart | YES | Init hook |
| BeforeAgent | (slash command detection) | YES | Catches `/cr:` patterns |
| BeforeTool | PreToolUse | YES | Blocks dangerous tools |
| AfterTool | PostToolUse | YES | Plan export, task tracking |
| AfterAgent | (autorun detection) | YES | AUTORUN pattern matching |
| AfterModel | (post-model) | YES | Post-model processing |
| SessionEnd | Stop | YES | Cleanup |
| BeforeModel | N/A | NO | Not needed currently |
| BeforeToolSelection | N/A | NO | Not needed currently |
| PreCompress | N/A | NO | Not needed currently |

- [ ] 11.1 Verify all 7 covered events have correct tool name matchers (Gemini uses `write_file`, `run_shell_command`, `read_file`, `glob`, `grep_search` not `Write`, `Bash`, `Read`, `Glob`, `Grep`)
- [ ] 11.2 Verify command paths use `${extensionPath}` (not env var prefix)
- [ ] 11.3 Verify all hooks complete within 5s timeout (hook_entry.py uses 4s internal)
- [ ] 11.4 Verify `enableHooks` and `enableMessageBusIntegration` documented in GEMINI.md

---

## Phase 12: Verify cli_type Propagation Through Daemon Path (E2E)

From investigation: the daemon creates `EventContext` objects per-request. `cli_type` must be detected from the request payload, not cached from daemon startup.

- [ ] 12.1 Verify `handle_client()` at `core.py:1029` calls `detect_cli_type(payload)` on the ORIGINAL payload (before normalization) — CONFIRMED in current diff
- [ ] 12.2 Verify `EventContext.__init__` at `core.py:457-510` accepts and stores `cli_type` — CONFIRMED in current diff
- [ ] 12.3 Verify `respond()` uses `self.cli_type` (not calling `detect_cli_type()` again) — CONFIRMED in current diff
- [ ] 12.4 Write E2E test: simulate daemon receiving Gemini payload, verify response has `decision: "deny"` (not `"block"`)
- [ ] 12.5 Write E2E test: simulate daemon receiving Claude payload, verify response has `decision: "block"` for PreToolUse deny

---

## Phase 13: Verify AIX Manifest Sync

The AIX manifest generator (`aix_manifest.py`) produces both manifests from a single source.

- [ ] 13.1 Run `uv run --project plugins/clautorun python -c "from clautorun.aix_manifest import generate_manifests; from pathlib import Path; generate_manifests(Path('plugins/clautorun'))"` and verify outputs match current `plugin.json` and `gemini-extension.json` (except the hooks path fix from Phase 6.1)
- [ ] 13.2 After Phase 6.1 hooks path fix, update `aix_manifest.py` to generate `"hooks": "./hooks/gemini-hooks.json"` for Gemini manifest

**File**: `plugins/clautorun/src/clautorun/aix_manifest.py:49-52`

Currently both manifests get `"hooks": "./hooks/hooks.json"`. The Gemini manifest should get `"hooks": "./hooks/gemini-hooks.json"`.

BEFORE:
```python
        "skills": "./skills/",
        "hooks": "./hooks/hooks.json"
    }
```

AFTER (Claude manifest keeps hooks.json, Gemini manifest section should differ):
```python
        "skills": "./skills/",
        "hooks": "./hooks/hooks.json"
    }
    # ... later in Gemini manifest generation ...
    gemini_manifest["hooks"] = "./hooks/gemini-hooks.json"
```

---

## Exploration Findings: Outstanding Tasks (from notes/2026_02_13)

### Regression Investigation (from notes/2026_02_13_1445)
Two regressions reported in Claude Code sessions:
- `SessionStart: resume hook error` — Recent changes to `respond()` in `core.py` may send forbidden fields (decision, reason, hookSpecificOutput) in SessionStart responses. Claude Code SessionStart schema MUST NOT contain these.
- `UserPromptSubmit hook error` — Same schema violation issue.
- **Root cause**: `respond()` Pathway 4 (SessionStart) at `core.py:773-782` now correctly strips forbidden fields via `validate_hook_response()`. Verify this is working.
- [ ] Run real Claude Code session and confirm no "hook error" on SessionStart
- [ ] Run real Claude Code session and confirm no "hook error" on UserPromptSubmit
- [ ] Verify `validate_hook_response()` strips `decision`, `reason`, `hookSpecificOutput` from SessionStart responses for Claude

### Progress Notes (from notes/2026_02_13_1535)
- Test `test_installed_hook_blocks_cat` failing: Expected `"decision": "deny"`, Actual: `"decision": "block"`
- Root cause: `ctx.cli_type` not correctly reaching `respond()` in some test paths
- 27+ `build_hook_response` calls in main.py needed `ctx=ctx` — NOW DONE in current diff (38 call sites updated)
- All HANDLERS dictionary entries needed context passing — NOW DONE
- [ ] Verify `test_installed_hook_blocks_cat` passes after Phase 12 E2E fixes
- [ ] Verify all 38 ctx propagation sites produce correct cli_type-aware responses

### Architectural Gaps Identified (from exploration)

1. **Manifest Drift**: `gemini-extension.json` and `plugin.json` can drift without code-gen
   - **Status**: AIX manifest system (`aix_manifest.py`) created but not integrated into CI/CD
   - **Covered by**: Phase 13 (AIX manifest sync verification)

2. **Tool Name Mismatches**: Gemini tool names differ from Claude
   - Gemini: `write_file`, `run_shell_command`, `read_file`, `glob`, `grep_search`
   - Claude: `Write`, `Bash`, `Read`, `Glob`, `Grep`
   - **Status**: Addressed in `gemini-hooks.json` matchers
   - **Covered by**: Phase 11.1

3. **Timeout Risk**: Gemini enforces 5s timeout, `uv run` can add 2-3s overhead
   - **Status**: Optimized to 4s internal timeout and direct venv binary in `hook_entry.py:572-577`
   - **Covered by**: Phase 11.3
   - [ ] Verify slow-system behavior does not exceed 5s

4. **Session Identity**: PID tracking may be incorrect (daemon tracking wrong process)
   - **Status**: Noted in earlier notes
   - **Covered by**: Phase 5 (thread safety for active_pids)
   - [ ] Verify stable parent process traversal for PID detection

5. **Broken Symlinks**: Installation from /tmp causes broken symlinks
   - **Status**: Partially addressed in `install.py`
   - **Current workaround**: install.py uses persistent repo path, not /tmp
   - [ ] Verify `_install_for_gemini()` in `install.py:1021+` uses stable paths

### Feature Gaps Identified (from exploration)

1. **Skills Directory Structure**: Gemini requires `skills/<name>/SKILL.md` but repo uses flat meaningful names
   - **Solution documented**: Dual-layout symlink strategy
   - **Status**: Not yet implemented
   - [ ] Evaluate if skills directory migration is needed for this milestone or deferred

2. **Limited BeforeTool Matchers**: Only 6 tool names matched in gemini-hooks.json
   - Current: `write_file`, `run_shell_command`, `replace`, `read_file`, `glob`, `grep_search`
   - **Gap**: May miss new Gemini tools if specs change
   - [ ] Review Gemini CLI tool list for any missing matchers

3. **MCP Server Integration**: Both CLIs support MCP, potential for cross-platform tool sharing
   - **Status**: Not in scope for this plan
   - **Deferred**: Future milestone

### Key Differences: Claude Code vs Gemini CLI Hooks (from research)

| Aspect | Claude Code | Gemini CLI |
|---|---|---|
| Hook Events | PreToolUse, PostToolUse, SessionStart, Stop, UserPromptSubmit | BeforeTool, AfterTool, SessionStart, SessionEnd, BeforeAgent, AfterAgent, BeforeModel, AfterModel, BeforeToolSelection, PreCompress |
| Tool Blocking | Exit 2 + stderr required (bug #4669) | JSON `decision: "deny"` at exit 0 |
| Env Var in Commands | `VAR=value command` works | Not supported, use `${extensionPath}` |
| Context Window | ~100k tokens | 2M tokens |
| Stdout Parsing | JSON only | JSON only (no plain text allowed) |
| Tool Names | Write, Read, Bash, Glob, Grep | write_file, read_file, run_shell_command, glob, grep_search |
| Required Settings | None special | enableHooks + enableMessageBusIntegration |
| Hook Timeout | None documented | 5 seconds |
| Notification Hook | Notification | Notification |

### Key Files Status Table (from exploration)

| File | Purpose | Status |
|---|---|---|
| `hooks/hook_entry.py` | Dual-CLI entry point with fast/fallback paths | GOOD (optimized) |
| `src/clautorun/core.py` | CLI-aware response mapping and schema handling | HAS ISSUES (debug pollution, command_response bug, race conditions) |
| `src/clautorun/main.py` | Hook handler dispatch (legacy + current) | HAS ISSUES (deny-to-ask, missing commands fields) |
| `src/clautorun/config.py` | CLI type detection and schema management | HAS ISSUES (140-line spaghetti detect_cli_type) |
| `src/clautorun/client.py` | Unified hook output with exit-2 workaround | GOOD (refactored to return int) |
| `src/clautorun/plugins.py` | Command handlers and dispatch logic | MINOR (ctx.deny should be ctx.ask) |
| `src/clautorun/aix_manifest.py` | Manifest generation from single source | NEEDS FIX (same hooks path for both) |
| `src/clautorun/ai_monitor.py` | AI session monitoring | NEEDS FIX (shelve without RAII lock) |
| `src/clautorun/plan_export.py` | Plan export logic | GOOD (all validate_hook_response calls added) |
| `src/clautorun/install.py` | Installation with hook swap logic | GOOD (backup/restore pattern added) |
| `hooks/gemini-hooks.json` | Gemini event definitions | GOOD (AfterAgent, AfterModel added) |
| `gemini-extension.json` | Gemini manifest | NEEDS FIX (hooks path wrong) |
| `plugin.json` | Claude manifest | GOOD (hooks field added) |
| `GEMINI.md` | Installation, configuration, multi-CLI workflows | GOOD (comprehensive) |

### Philosophy Checklist (from CLAUDE.md principles)

Each phase should satisfy:
- [ ] **TDD**: Tests written FIRST before implementation changes
- [ ] **DRY**: No code duplication; reuse `validate_hook_response()`, `ThreadSafeDB`, `SessionLock`
- [ ] **OODA**: Observe (read code) -> Orient (understand schema) -> Decide (choose fix) -> Act (implement)
- [ ] **KISS**: Simplest fix that works; `detect_cli_type()` from 140 lines to 35
- [ ] **YAGNI**: No premature features; skills directory migration deferred
- [ ] **SOLID**: Single responsibility (each function does one thing); `command_response()` only builds response
- [ ] **RAII**: Resources properly managed; `SessionLock` wraps all shelve access; `_pid_lock` protects daemon state
- [ ] **WOLOG**: Solution generalizes; same `validate_hook_response()` works for both CLIs

---

## Complete Issue Tracker

### Critical (Blocks completion)
- [ ] C1: `command_response()` returns `continue: False` killing AI loop (Phase 1)
- [ ] C2: `deny-to-ask` remapping breaks non-interactive blocking (Phase 4.1)
- [ ] C3: Debug fields in HOOK_SCHEMAS violate spec (Phase 2.4-2.5)

### High (Causes failures)
- [ ] H1: Gemini E2E test expects `deny` gets `block` (Phase 12.4-12.5)
- [x] ~~H2: `active_pids` race condition~~ NOT A BUG — asyncio cooperative concurrency (Phase 5 REMOVED)
- [ ] H3: `ai_monitor.py` shelve without RAII lock (Phase 10)
- [ ] H4: `gemini-extension.json` hooks path wrong (Phase 6.1)
- [ ] H5: `commands`/`commands_description` silently dropped (Phase 4.3)

### Medium (Code quality)
- [ ] M1: Raw debug file I/O in core.py and config.py (Phase 2, 9)
- [ ] M2: `detect_cli_type()` grew from 20 to 140 lines (Phase 3)
- [ ] M3: `normalize_hook_payload()` leaks internal fields (Phase 2.6)
- [x] ~~M4: `ctx.deny()` replaced `ctx.ask()`~~ CORRECT — deny is right for autonomous blocking (Phase 4.2 REMOVED)
- [ ] M5: Test assertion weakened to `"deny" or "ask"` — 1 location (Phase 4.4)
- [ ] M6: Duplicate `source` key in test (Phase 6.2)
- [ ] M7: No-op ternaries in respond() (Phase 6.3)
- [ ] M8: `safe_reason` double-encodes special characters (Phase 4.1)

### Low (Verification only)
- [ ] L1: Legacy main.py/daemon core.py alignment (Phase 8)
- [ ] L2: AIX manifest sync (Phase 13)
- [ ] L3: Gemini hook event coverage (Phase 11)
- [ ] L4: config.py remaining debug logging sweep (Phase 9)

---

## Architecture Context

### Dual CLI Hook Flow (Current)
```
Claude Code / Gemini CLI
        |
  hooks.json / gemini-hooks.json  (event matching)
        |
  hook_entry.py                   (subprocess launcher)
        |
  __main__.py:run_hook_handler()  (daemon or legacy routing)
        |
  +-- DAEMON (default): client.py -> Unix socket -> ClautorunDaemon.handle_client()
  |     |
  |     detect_cli_type(payload)  <-- on ORIGINAL payload
  |     normalize_hook_payload()  <-- strips Gemini fields
  |     EventContext(cli_type=)   <-- carries CLI type
  |     app.dispatch(ctx)         <-- handler lookup
  |     ctx.respond()             <-- CLI-aware response building
  |     validate_hook_response()  <-- schema filtering per CLI
  |     client.py:output_hook_response() <-- exit code 0 or 2
  |
  +-- LEGACY (CLAUTORUN_USE_DAEMON=0): main.py:main()
        |
        payload -> handler(ctx)
        build_pretooluse_response(ctx=ctx)  <-- now passes ctx
        validate_hook_response()
        sys.exit(0 or 2)
```

### Response Schema Differences
| Field | Claude Code | Gemini CLI |
|---|---|---|
| Tool blocking decision | `hookSpecificOutput.permissionDecision: "deny"` | `decision: "deny"` |
| Tool blocking exit code | Exit 2 + stderr (bug #4669) | Exit 0 + JSON |
| Stop prevention | `decision: "block"` | `decision: "deny"` |
| Allowed top-level | continue, stopReason, suppressOutput, systemMessage, hookSpecificOutput | continue, decision, reason, systemMessage, stopReason |
| Forbidden top-level | decision, reason (for SessionStart/Stop) | hookSpecificOutput internals |

### ThreadSafeDB Architecture (3 layers)
```
1. EventContext   ctx.file_policy = "ALLOW"    (magic __setattr__)
       |
2. ThreadSafeDB  self._cache[key] = value     (RLock-protected in-memory)
       |                                         First read: ~7-17ms
       |                                         Cached: <1ms
3. session_state  shelve + fcntl.flock         (cross-process RAII)
       |                                         Survives daemon restart
   SessionLock    fcntl.LOCK_EX | LOCK_NB      (file-based mutual exclusion)
```

### Key Bug References
- **Bug #4669**: Claude Code ignores `permissionDecision: "deny"` at exit 0. Workaround: exit 2 + stderr.
- **Bug #10964**: Exit code 2 stderr goes to Claude AI, not to user. Documented in main.py:1052-1056.
- **GitHub issue #13155**: Gemini CLI requires both `enableHooks` and `enableMessageBusIntegration` settings.

---

## Risks

| Risk | Mitigation |
|------|-----------|
| `command_response()` change affects all `/cr:` commands | estop/stop opt into `continue_loop=False` |
| `command_response()` continue:True sends slash text to AI | Claude sees systemMessage and responds to that; verify in real session |
| Removing `deny-to-ask` may re-expose bug #4669 | Exit code 2 workaround is the real fix, not decision remapping |
| Simplifying `detect_cli_type()` may miss edge cases | 3-tier approach covers: payload, env, default |
| ~~Adding `_pid_lock`~~ | REMOVED — asyncio makes this unnecessary |
| Restoring strict test assertions may reveal broken paths | That is the point, tests should expose bugs |
| Legacy main.py and daemon core.py diverge on command semantics | Phase 8 verifies alignment |
| Gemini extension.json hooks path mismatch | Phase 6.1 fixes; install.py backup/restore handles install-time swap |
| AIX manifest generates wrong hooks for Gemini | Phase 13 fixes generator |
| ai_monitor shelve corruption under concurrent access | Phase 10 wraps with SessionLock |

## Gemini CLI Spec References
- Hooks reference: https://geminicli.com/docs/hooks/reference/
- Writing hooks: https://geminicli.com/docs/hooks/writing-hooks/
- Best practices: https://geminicli.com/docs/hooks/best-practices/
- Extensions: https://geminicli.com/docs/extensions/
- Extensions catalog: https://geminicli.com/extensions/
- Getting started codelab: https://codelabs.developers.google.com/getting-started-gemini-cli-extensions
- Required settings: enableHooks true, enableMessageBusIntegration true (GitHub issue #13155)
- Exit code 0 with decision deny blocks tools (unlike Claude bug 4669)
- Hook timeout: 5 seconds (hook_entry.py uses 4s internal timeout for safety)
- 10 available events: SessionStart, SessionEnd, BeforeAgent, AfterAgent, BeforeModel, AfterModel, BeforeTool, AfterTool, BeforeToolSelection, PreCompress
