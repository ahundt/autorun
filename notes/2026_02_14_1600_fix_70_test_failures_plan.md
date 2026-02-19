# Plan: Fix 70 Test Failures — PreToolUse `continue` Regression + Gemini cli_type Detection

## Context

After Phases 1-4, 6, 9, 13 of the Gemini CLI integration, the test suite shows **70 failures** (1803 pass, 11 skip). Analysis reveals two categories:

1. **Pre-existing failures from committed code (b48f6c6)** — ~50 failures where `continue: True` was hardcoded for PreToolUse deny in both `respond()` and `build_pretooluse_response()`. These tests existed before our uncommitted diff and document `continue: True` for deny as a known regression (commit 662d789 reverted the fix from commit 89030a9).

2. **Failures from our uncommitted diff** — ~20 failures from cli_type auto-detection, `command_response()` changes, and Gemini E2E test setup.

The plan addresses ALL 70 failures by fixing the root causes in code and updating tests where semantics intentionally changed.

---

## CORRECTED: `continue: True` for PreToolUse Deny is CORRECT

### Official Documentation

**Source**: [Claude Code Hooks Reference](https://code.claude.com/docs/en/hooks) (official Anthropic docs)

> **`continue`** | Default: `true` | If `false`, Claude stops processing entirely after the hook runs. Takes precedence over any event-specific decision fields.

And separately for PreToolUse:
> **`permissionDecision`**: `"deny"` prevents the tool call from executing. `permissionDecisionReason` is shown to Claude.

**Key insight: `continue` and `permissionDecision` are INDEPENDENT controls:**
- `continue: false` = **stops the AI entirely** (kills the whole session/agent)
- `permissionDecision: "deny"` = **blocks just the tool call** (AI continues, sees reason, suggests alternatives)
- For tool blocking: `continue: true` + `permissionDecision: "deny"` is the CORRECT pattern

**The code at `core.py:696` and `main.py:1102` with `continue: True` is CORRECT.**
**The tests expecting `continue: False` for PreToolUse deny are WRONG.**

Also confirmed by local `notes/hooks_api_reference.md:69-70`:
> | **`continue: false`** | Stops AI entirely | Stops AI entirely |
> | **Blocking Requires** | Exit code 2 + stderr (workaround) | `decision: "deny"` + `continue: true` |

And at `hooks_api_reference.md:322-324`:
> Both CLIs treat `continue: false` as "stop the AI entirely", NOT "stop just this tool". To block a tool while allowing the AI to suggest alternatives, you need special handling.

### Bug #4669 — Critical Impact on Claude Code Tool Blocking and Code Edits

**Reference**: [GitHub Issue #4669](https://github.com/anthropics/claude-code/issues/4669)
— `[BUG] permissionDecision: "deny" in PreToolUse hooks is ignored - tools execute anyway`
— auto-closed for inactivity (NOT_PLANNED, 2026-01-05), NOT actually fixed.
Bug likely still present in current Claude Code versions.

**Related issues** (broader pattern of PreToolUse hook bugs in Claude Code):
- [Issue #4362](https://github.com/anthropics/claude-code/issues/4362) — `approve: false` ignored (closed: user had wrong syntax)
- [Issue #19298](https://github.com/anthropics/claude-code/issues/19298) — PermissionRequest hook deny also ignored
- [Issue #13339](https://github.com/anthropics/claude-code/issues/13339) — VS Code ignores `permissionDecision: "ask"`
- [Issue #21988](https://github.com/anthropics/claude-code/issues/21988) — PreToolUse hook exit codes ignored, operations proceed

**Source**: `notes/hooks_api_reference.md:72-78, 326-340`

#### Bug #4669 — Full Report (from GitHub)

> **Title**: `[BUG] permissionDecision: "deny" in PreToolUse hooks is ignored - tools execute anyway`
> **Reporter**: `a-c-m`, **Opened**: 2025-07-29, **Environment**: Claude Code v1.0.62, macOS
>
> **Description**: PreToolUse hooks that return `"permissionDecision": "deny"` are not
> preventing tool execution as documented. The hook executes correctly and returns
> the proper JSON response, but Claude Code ignores the denial and executes the tool anyway.
>
> **Steps to Reproduce**: Create hook returning `{"permissionDecision": "deny",
> "permissionDecisionReason": "..."}` at exit 0. Run any bash command. Expected: blocked.
> Actual: executes normally.
>
> **Debug Output**: Hook completes with status 0, returns correct JSON with
> `"permissionDecision": "deny"`, but `[DEBUG] Bash tool invoked with command: ls -la`
> shows tool still executes.
>
> **Documentation Reference**: According to https://docs.anthropic.com/en/docs/claude-code/hooks,
> `"permissionDecision": "deny"` should "prevent the tool call from executing" and the
> `"permissionDecisionReason"` should be "shown to Claude".

#### Bug #4669 — Impact Summary

| Aspect | Detail |
|--------|--------|
| **JSON Key** | `hookSpecificOutput.permissionDecision` |
| **Value** | `"deny"` at exit 0 |
| **Should** | Tool blocked, reason fed to AI |
| **Does** | Tool **executes anyway** (denial ignored) |
| **Versions** | v1.0.62+ through v2.1.39 (current as of 2026-02-12) |
| **Workaround** | Exit code 2 + reason on stderr |

#### Bug #4669 — Impact on Code Edits

**This bug is why the exit-2 workaround exists and must be preserved.**

Without the workaround, `permissionDecision: "deny"` alone does NOT block tools in
Claude Code. This means:
1. **`rm`, `git reset --hard`, `git clean -f`** and other dangerous commands would
   execute despite our hooks returning "deny"
2. **AutoFile policy enforcement** would be completely ineffective — files would be
   created/modified even when policy says "deny"
3. **All safety guards** in clautorun would be silently bypassed

**How we address it in our code (the exit-2 workaround)**:
- `client.py:output_hook_response()` checks `should_use_exit2_workaround()`
- When denying on Claude Code: prints reason to stderr + exits with code 2
- When denying on Gemini CLI: prints JSON with `decision: "deny"` + exits with code 0
- The JSON response (`permissionDecision: "deny"`, `continue: true`) is STILL included
  for correctness — if/when Claude Code fixes Bug #4669, the JSON will work without
  the exit-2 workaround

**What this means for `continue` field**: The `continue: true` in our deny responses
is CORRECT — it keeps the AI session running so it can see the denial reason and suggest
alternatives. Tool blocking is done by exit code 2 (Bug #4669 workaround), NOT by
`continue: false`. Setting `continue: false` would kill the entire AI session, which
is far worse than the tool executing.

**Gemini CLI is NOT affected**: Gemini CLI correctly respects `decision: "deny"` at exit 0.
No exit-2 workaround needed.

### Blocking Behavior Matrix

From `hooks_api_reference.md:431-438`:

| CLI | JSON Field | Exit Code | `continue` | Result |
|-----|------------|-----------|------------|--------|
| Claude | `permissionDecision: "deny"` | 0 | `true` | ❌ TOOL EXECUTES (BUG #4669 - denial ignored) |
| Claude | `permissionDecision: "deny"` | 2 | `true` | ✅ **TOOL BLOCKED**, AI continues (workaround) |
| Claude | - | 2 | - | ✅ **TOOL BLOCKED**, stderr to AI |
| Gemini | `decision: "deny"` | 0 | `true` | ✅ **TOOL BLOCKED**, AI continues |
| Gemini | `decision: "deny"` | 2 | `true` | ✅ **TOOL BLOCKED**, AI continues |
| Gemini | `decision: "block"` | 0 | `false` | ⚠️ **AI STOPS** entirely |

### Exit-2 Workaround Parameterization

The workaround is configurable via environment variable and CLI argument:

**Environment variable** (`config.py:should_use_exit2_workaround()` at line 466):
```bash
export CLAUTORUN_EXIT2_WORKAROUND=auto    # Default: workaround ONLY for Claude Code
export CLAUTORUN_EXIT2_WORKAROUND=always  # Force for all CLIs (testing)
export CLAUTORUN_EXIT2_WORKAROUND=never   # Disable for all CLIs (future bug fix)
```

**CLI argument** (`__main__.py:213`):
```bash
clautorun --exit2-mode auto    # Auto-detect CLI type
clautorun --exit2-mode always  # Force exit-2 for all
clautorun --exit2-mode never   # Disable workaround
```

**Implementation** (`client.py:output_hook_response()` at line 129-135):
- When `decision == "deny"` AND `should_use_exit2_workaround()` returns True:
  - Prints reason to stderr (AI sees this as feedback)
  - Exits with code 2 (triggers actual blocking)
- When workaround is not needed (Gemini, or workaround disabled):
  - Prints JSON to stdout with `decision: "deny"`
  - Exits with code 0 (Gemini respects this correctly)

### Historical Context

**File**: `test_regression_pretooluse_blocking.py:1-16` documents the test history:
> "core.py EventContext.respond("deny") returned continue=True for PreToolUse
>  (daemon path) — commit 662d789 introduced, this session fixed"

These tests were written under the INCORRECT assumption that `continue: False` blocks
tool execution. In reality, `continue: False` stops the AI entirely, and tool blocking
is controlled by `permissionDecision: "deny"` + exit code 2 (Bug #4669 workaround).
The committed code with `continue: True` was correct all along.

### Stop/SubagentStop Events are DIFFERENT

For Stop hooks, `decision: "block"` prevents Claude from stopping (forces it to keep working).
For emergency stop (`/cr:sos`), `continue: false` via `command_response(continue_loop=False)`
is correct to halt the AI entirely — but this is in the UserPromptSubmit path, NOT Stop events.

---

## Failure Categorization (70 total)

### Group A: Tests incorrectly expect `continue: False` for PreToolUse deny (40 failures) — TEST FIX needed

Per [official Claude Code hooks docs](https://code.claude.com/docs/en/hooks), `continue: True` is CORRECT
for PreToolUse deny. Tool blocking is done by `permissionDecision: "deny"`, NOT by `continue: false`.
`continue: false` stops the AI entirely, which is NOT what we want when blocking a tool.

| Test File | Failures | Root Cause |
|-----------|----------|------------|
| `test_regression_pretooluse_blocking.py` | 16 | Tests wrongly assert `continue: False` for PreToolUse deny |
| `test_pretooluse_blocking_fix.py` | 2 | Tests wrongly assert `continue: False` for policy deny |
| `test_pretooluse_policy_enforcement.py` | 4 | Tests wrongly assert `continue: False` for policy deny |
| `test_hook_entry.py` (blocking tests) | 3 | Tests wrongly assert `continue: False` for rm block |
| `test_dual_platform_hooks_install.py` | 2 | Tests wrongly assert `continue: False` for rm block |
| `test_integration_comprehensive.py` | 2 | Tests wrongly assert `continue: False` |
| `test_plugin.py` (rm/policy tests) | ~8 | Tests wrongly assert `continue: False` for blocked commands |

**Fix**: Update ALL tests to expect `continue: True` for PreToolUse deny.
The code (`core.py:696`, `main.py:1102`) with `continue: True` is CORRECT.
Tool blocking relies on `permissionDecision: "deny"` + exit code 2 (Bug #4669 workaround).

**Stop/SubagentStop events (lines 421-441)**: These tests ALSO expect `continue: False`
for Stop deny/block, but this is ALSO wrong per the docs:
- Stop `decision: "block"` = "prevent Claude from stopping, keep working" → `continue: True` is correct
- Emergency stop (`/cr:sos`) uses `command_response(continue_loop=False)` in the
  UserPromptSubmit path, NOT Stop event `respond("deny")`
- The current code at `core.py:742` correctly uses `continue: True` for Stop block
- These test assertions should also be updated to `continue: True`

### Group B: Gemini cli_type detection (8 failures) — CODE FIX needed

| Test File | Failures | Root Cause |
|-----------|----------|------------|
| `test_gemini_e2e_improved.py::TestGeminiExtensionInstalledHook` | 8 | cli_type auto-detects "claude" from env, ignores `source: "gemini"` in payload |

**Fix**: In the legacy handler path (`pretooluse_handler` in main.py), detect cli_type from the payload and pass it to EventContext.

### Group C: command_response() semantics (4 failures) — TEST UPDATE needed

| Test File | Failures | Root Cause |
|-----------|----------|------------|
| `test_plugin.py::test_plugin_handles_autorun_command` | 1 | Expects `continue: False` for `/autorun` — old semantics |
| `test_plugin.py::test_plugin_handles_control_commands` | 2 | Stop commands — should work via `_halt_ai` but legacy path may not use it |
| `test_ai_monitor_integration.py` | 3 | Empty `systemMessage` for stop/continue handlers |

**Fix**: Need to verify whether the legacy `main()` path uses `dispatch()` or its own handler chain for UserPromptSubmit commands.

### Group D: Decision field consistency (4 failures) — TEST UPDATE needed

| Test File | Failures | Root Cause |
|-----------|----------|------------|
| `test_regression_pretooluse_blocking.py::TestContinueDecisionInvariant` | 2 | Expects `decision == "deny"` but Claude mapping returns `"block"` |
| `test_regression_pretooluse_blocking.py::TestDaemonLegacyConsistency` | 2 | Same — top-level `decision` uses Claude format |

**Fix**: Update tests to expect `decision == "block"` for Claude cli_type (Claude uses approve/block, not allow/deny).

### Group E: Pre-existing / infrastructure (10+ failures) — SEPARATE ISSUE

| Test File | Failures | Root Cause |
|-----------|----------|------------|
| `test_install_pathways.py` | 10 | Not modified by our diff — pre-existing |
| `test_hook_entry.py::TestAllLocationsSync` | 2 | Cache sync / daemon process — infrastructure |
| `test_session_lifecycle_edge_cases.py` | 1 | Memory leak test |
| `test_task_cli_commands.py` | 1 | CLI gc dry run |

**Action**: Skip for now — not caused by our changes, not blocking Gemini integration.

### Group F: Hardcoded capture sizes (0 failures currently, fragile)

| Test File | Issue |
|-----------|-------|
| `test_dual_platform_hooks_install.py:637-655` | Uses `6000`/`4000` char windows — breaks on code growth |

**Fix**: Replace with function-boundary detection.

---

## Execution Plan

### Step 1: Fix tests that wrongly assert `continue: False` for PreToolUse deny (Group A — 40 failures)

**NO CODE CHANGES to core.py or main.py** — `continue: True` is CORRECT per
[official hooks docs](https://code.claude.com/docs/en/hooks).

**Fix the tests**, not the code. All PreToolUse deny tests must assert `continue: True`.
Tool blocking is controlled by `permissionDecision: "deny"`, NOT by `continue`.

Tests to update:
- `test_regression_pretooluse_blocking.py:53,89,124,397,414` — change `False` to `True`
- `test_regression_pretooluse_blocking.py:387,405` — parametrize: `("deny", True)` not `("deny", False)`
- `test_regression_pretooluse_blocking.py:479,552` — rm blocking: change `False` to `True`
- `test_pretooluse_blocking_fix.py` — update deny assertions
- `test_pretooluse_policy_enforcement.py` — update deny assertions
- `test_hook_entry.py` — update rm block assertions
- `test_dual_platform_hooks_install.py` — update rm block assertions
- `test_plugin.py` — update blocked command assertions

**Also update test docstrings** to reflect correct semantics:
```python
# BEFORE (wrong):
"""EventContext.respond('deny') for PreToolUse must return continue=False."""

# AFTER (correct):
"""EventContext.respond('deny') for PreToolUse must return continue=True.
Tool blocking uses permissionDecision='deny', not continue=False.
continue=False stops the AI entirely (per hooks docs)."""
```

**The comments in core.py:686-691 and main.py:1089-1093 are CORRECT** — they accurately
describe that `continue: True` keeps the AI running so it can see feedback.

**Stop/SubagentStop event tests (lines 421-441)**: These also assert `continue: False`
for Stop deny/block. Per the docs, Stop `decision: "block"` prevents Claude from stopping
(keeps AI working), so `continue: True` is correct here too. The current code at
`core.py:742` correctly uses `continue: True` for Stop block. Update these test assertions
to `continue: True` as well.

**Emergency stop** (`/cr:sos`): Uses `command_response(continue_loop=False)` in the
UserPromptSubmit path — this is the ONLY place where `continue: False` is correct
(to halt the AI entirely). This does NOT go through `respond()` or Stop events.

### Step 2: Fix Gemini cli_type in legacy handler (Group B — 8 failures)

The legacy `pretooluse_handler()` creates `EventContext` without passing cli_type. The payload may contain `source: "gemini"` but `EventContext.cli_type` auto-detects from env vars only.

**File**: `plugins/clautorun/src/clautorun/main.py` — in `pretooluse_handler()`, detect cli_type from the raw payload and pass it to EventContext:

```python
from .config import detect_cli_type
# Before creating EventContext:
cli_type = detect_cli_type(payload)  # Detects from source/cli_type/env
ctx = EventContext(..., cli_type=cli_type)
```

### Step 3: Fix decision field consistency tests (Group D — 4 failures)

**File**: `plugins/clautorun/tests/test_regression_pretooluse_blocking.py:401,418`

The tests assert `response["decision"] == decision` (e.g., `"deny"`), but Claude mapping returns `"block"` for deny and `"approve"` for allow. Since these tests create EventContext without Gemini env vars, cli_type is "claude".

Update assertions to use mapped values:
```python
# Line 401 — daemon invariant
expected_decision = "block" if decision == "deny" else "approve"
assert response["decision"] == expected_decision

# Line 418 — legacy invariant
expected_decision = "block" if decision == "deny" else "approve"
assert response["decision"] == expected_decision
```

### Step 4: Fix command_response() for legacy path (Group C — 4 failures)

Investigate how `main.py:main()` handles UserPromptSubmit commands:
- If legacy path uses `build_hook_response()`, verify `_halt_ai` is checked
- If legacy path uses `command_response()`, verify stop commands set `_halt_ai`
- Update `test_plugin.py` tests if autorun `continue: True` is correct behavior
- Fix `test_ai_monitor_integration.py` systemMessage assertions

### Step 5: Fix hardcoded capture sizes (Group F)

**File**: `plugins/clautorun/tests/test_dual_platform_hooks_install.py:637-655`

Replace fixed-window capture with function-boundary detection:
```python
def _extract_function(content, func_name):
    """Extract function body from source by finding next def at same indent."""
    import re
    func_idx = content.find(f"def {func_name}(")
    if func_idx < 0:
        return ""
    # Find the indentation of this function
    line_start = content.rfind("\n", 0, func_idx) + 1
    indent = func_idx - line_start
    # Find next function at same or lesser indent
    pattern = re.compile(rf'\n.{{{0},{indent}}}def \w+\(', re.MULTILINE)
    match = pattern.search(content, func_idx + 1)
    end = match.start() if match else len(content)
    return content[func_idx:end]
```

### Step 6: Run full test suite and verify

```bash
uv run pytest plugins/clautorun/tests/ --tb=short -q --timeout=15
```

Target: 0 failures from Groups A-D, F. Group E failures are pre-existing and tracked separately.

---

## Verification

1. `uv run pytest plugins/clautorun/tests/test_regression_pretooluse_blocking.py -v` — all 16 pass
2. `uv run pytest plugins/clautorun/tests/test_gemini_e2e_improved.py -v` — all pass including installed hook tests
3. `uv run pytest plugins/clautorun/tests/test_plugin.py -v` — all pass
4. `uv run pytest plugins/clautorun/tests/test_pretooluse_blocking_fix.py test_pretooluse_policy_enforcement.py -v` — all pass
5. `uv run pytest plugins/clautorun/tests/ --tb=short -q --timeout=15` — only Group E pre-existing failures remain

## Key Files

| File | Changes |
|------|---------|
| `core.py:692-696` | NO CHANGE — `continue: True` is correct (Bug [#4669](https://github.com/anthropics/claude-code/issues/4669) workaround in client.py handles blocking) |
| `core.py:686-691` | NO CHANGE — comment correctly describes `continue: True` semantics |
| `main.py:1099-1103` | NO CHANGE — `continue: True` is correct; optionally restore JSON-escaping |
| `main.py:1089-1093` | NO CHANGE — comment correctly describes `continue: True` semantics |
| `main.py:pretooluse_handler` | Pass cli_type from payload to EventContext |
| `test_regression_pretooluse_blocking.py:401,418` | Fix decision field assertions for Claude mapping |
| `test_dual_platform_hooks_install.py:637-655` | Replace hardcoded capture sizes |

---

# APPENDIX: Stable vs Current Block-by-Block Comparison

## Investigation Date: 2026-02-14

Stable worktree: `/Users/athundt/.claude/clautorun/.worktrees/claude-stable-pre-v0.8.0`
Current branch: `feature/gemini-cli-integration`

---

## A. `build_pretooluse_response()` — main.py

### STABLE (main.py:1021-1077)
```python
def build_pretooluse_response(decision="allow", reason=""):
    safe_reason = json.dumps(reason)[1:-1] if reason else ""
    should_continue = decision != "deny"    # ← WRONG: stops AI entirely on deny (Bug #4669 unrelated)
    return {
        "decision": decision,               # ← Raw value (no CLI mapping)
        "reason": safe_reason,
        "continue": should_continue,        # ← WRONG: False kills AI, doesn't block tool
        "stopReason": safe_reason if not should_continue else "",  # ← Only matters if continue=false
        "suppressOutput": False,
        "systemMessage": safe_reason,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,     # ← Tool blocking: this + exit 2 (Bug #4669 workaround)
            "permissionDecisionReason": safe_reason,
        },
    }
```

### CURRENT (main.py:1086-1113)
```python
def build_pretooluse_response(decision="allow", reason="", ctx=None):
    cli_type = ctx.cli_type if ctx else detect_cli_type()
    top_decision = mapped_value(decision, cli_type)   # ← IMPROVEMENT: per-CLI mapping
    response = {
        "decision": top_decision,
        "reason": reason,
        "continue": True,       # ← CORRECT: AI continues; tool blocked by permissionDecision + exit 2 (Bug #4669)
        "stopReason": "",       # ← CORRECT: stopReason only for continue=false
        ...
    }
    return validate_hook_response(...)  # ← IMPROVEMENT: schema validation
```

### VERDICT: CORRECT BEHAVIOR + IMPROVEMENTS (NOT a regression)
- **CORRECT**: `continue: True` is correct per [official hooks docs](https://code.claude.com/docs/en/hooks)
  — `continue` controls the AI session lifecycle, NOT tool blocking
- **CORRECT**: `stopReason: ""` is fine — `stopReason` is only for when `continue: false`
- **NOTE**: Lost `json.dumps(reason)[1:-1]` JSON-escaping of reason — minor, may want to restore
- **IMPROVEMENT**: cli_type detection and per-CLI decision mapping
- **IMPROVEMENT**: `validate_hook_response()` for schema compliance
- **IMPROVEMENT**: `ctx` parameter for explicit CLI type propagation

The stable version had `should_continue = decision != "deny"` which was WRONG —
it would stop the AI entirely on any tool deny, instead of just blocking the tool.
The current code correctly keeps `continue: True` always for PreToolUse.

### FIX: Only restore JSON-escaping if needed, no continue/stopReason changes
```python
def build_pretooluse_response(decision="allow", reason="", ctx=None):
    from .config import detect_cli_type
    cli_type = ctx.cli_type if ctx and hasattr(ctx, 'cli_type') else detect_cli_type()

    if cli_type == "gemini":
        top_decision = "allow" if decision == "allow" else "deny"
    else:
        top_decision = "approve" if decision == "allow" else "block"

    safe_reason = json.dumps(reason)[1:-1] if reason else ""  # Restore JSON-escaping

    response = {
        "decision": top_decision,
        "reason": safe_reason,
        "continue": True,              # ← CORRECT: AI continues, tool blocked by permissionDecision
        "stopReason": "",              # ← CORRECT: only used when continue=false
        "suppressOutput": False,
        "systemMessage": safe_reason,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": safe_reason,
        },
    }
    return validate_hook_response("PreToolUse", response, cli_type=cli_type)
```

---

## B. `respond()` PreToolUse path — core.py

### STABLE (core.py:631-649)
```python
if self._event == "PreToolUse":
    top_decision = "block" if decision == "deny" else "approve"
    is_deny = decision == "deny"
    return {
        "decision": top_decision,
        "permissionDecision": decision,
        "reason": "" if is_deny else msg_reason,
        "continue": True,           # ← CORRECT: AI continues; tool blocked by exit 2 (Bug #4669)
        "stopReason": "",            # ← CORRECT: only matters when continue=false
        ...
    }
```

### CURRENT (core.py:686-707)
```python
if self._event == "PreToolUse":
    is_deny = decision == "deny"
    resp = {
        "decision": top_decision,    # ← IMPROVEMENT: per-CLI mapping
        "permissionDecision": decision,  # ← Tool blocking: this + exit 2 (Bug #4669 workaround)
        "reason": msg_reason if cli_type == "gemini" else ("" if is_deny else msg_reason),
        "continue": True,            # ← CORRECT: same as stable, AI continues
        "stopReason": "",            # ← CORRECT: same as stable
        ...
    }
    return validate_hook_response(...)  # ← IMPROVEMENT: schema validation
```

### VERDICT: CORRECT BEHAVIOR + IMPROVEMENTS (NOT a bug)
- **CORRECT**: `continue: True` is correct per [official hooks docs](https://code.claude.com/docs/en/hooks)
- **CORRECT**: `stopReason: ""` is fine for `continue: true` responses
- **IMPROVEMENT**: Gemini CLI detection and per-CLI decision mapping
- **IMPROVEMENT**: `validate_hook_response()` for schema compliance
- **IMPROVEMENT**: Logging for debugging

### NO CODE FIX NEEDED — current code is correct (Bug #4669 workaround in client.py handles blocking)
```python
# Current code is correct. Tool blocking works via:
# - permissionDecision: "deny" (JSON field, ignored by Claude Code due to Bug #4669)
# - exit code 2 + stderr reason (Bug #4669 workaround, applied in client.py:output_hook_response())
# - continue: True keeps AI running so it sees feedback and suggests alternatives
resp = {
    "decision": top_decision,
    "permissionDecision": decision,  # Tool decision (+ exit 2 workaround for Claude Code Bug #4669)
    "reason": msg_reason if cli_type == "gemini" else ("" if is_deny else msg_reason),
    "continue": True,        # ← CORRECT: AI continues; tool blocked by exit 2, not continue=false
    "stopReason": "",         # ← CORRECT: only used when continue=false (AI halt)
    ...
}
```

---

## C. `command_response()` — core.py

### STABLE (core.py:727-748)
```python
def command_response(self, response_text: str) -> Dict:
    """Commands handled locally should NOT continue to AI."""
    return {
        "continue": False,           # ← BUG: stops AI for ALL commands
        "stopReason": "",
        "suppressOutput": False,
        "systemMessage": response_text,
        "response": response_text    # ← Legacy compatibility
    }
```

### CURRENT (core.py:786-804)
```python
def command_response(self, response_text: str, continue_loop: bool = True) -> Dict:
    resp = {
        "continue": continue_loop,    # ← FIX: parameterized
        "stopReason": "",
        "suppressOutput": False,
        "systemMessage": response_text,
    }
    return validate_hook_response(self._event, resp, cli_type=self.cli_type)
```

### VERDICT: BUG FIX (current is correct)
- **FIX**: `continue_loop` parameter defaults True (AI continues after commands)
- **FIX**: stop/estop use `continue_loop=False` via `_halt_ai` flag in `dispatch()`
- This fixes the "Operation stopped by hook" bug the user experienced live
- Removed `"response"` legacy field (covered by `validate_hook_response`)

### No changes needed — current is correct.

---

## D. `detect_cli_type()` — config.py

### STABLE (config.py — ~140 lines, env-only detection)
- Only checks `GEMINI_SESSION_ID` and `GEMINI_PROJECT_DIR` env vars
- No payload detection
- No event-based detection

### CURRENT (config.py:420-481 — ~35 lines, 3-tier detection)
```python
_GEMINI_EVENTS = frozenset({"BeforeTool", "AfterTool", ...})

def detect_cli_type(payload: dict = None) -> str:
    if payload:
        if payload.get("cli_type") in ("gemini", "claude"):
            return payload["cli_type"]
        if payload.get("source") in ("gemini", "claude"):
            return payload["source"]
        if payload.get("GEMINI_SESSION_ID") or payload.get("sessionId"):
            return "gemini"
        if payload.get("hook_event_name") in _GEMINI_EVENTS:
            return "gemini"
    if os.environ.get("GEMINI_SESSION_ID") or os.environ.get("GEMINI_PROJECT_DIR"):
        return "gemini"
    return "claude"
```

### VERDICT: IMPROVEMENT (current is correct)
- Adds payload-based detection (fixes Gemini E2E tests when env vars absent)
- Adds event-name-based detection (Gemini uses different event names)
- Dramatically simplified from ~140 to ~35 lines (DRY)
- No regressions

### No changes needed — current is correct.

---

## E. `dispatch()` and stop/estop — core.py + plugins.py

### STABLE: No `_halt_ai` mechanism
- `command_response()` always returned `continue: False` (broke all commands)

### CURRENT (core.py:899-928, plugins.py:592-603)
```python
# plugins.py — stop/estop handlers set _halt_ai
@app.command("/cr:x", "/cr:stop", "/autostop", "stop")
def handle_stop(ctx):
    ctx._halt_ai = True
    return _deactivate(ctx, "✅ Stopped")

# core.py — dispatch() checks _halt_ai
halt = getattr(ctx, '_halt_ai', False)
return ctx.command_response(response_text, continue_loop=not halt)
```

### VERDICT: IMPROVEMENT (current is correct)
- Correct design: most commands allow AI to continue, only stop/estop halt
- No regressions

---

## F. `validate_hook_response()` — core.py (NEW)

### STABLE: Does not exist

### CURRENT: Filters response fields per event and CLI type
- Ensures only valid fields per Claude Code / Gemini CLI schema
- Prevents "unknown field" errors from either CLI
- Applied to all response paths

### VERDICT: IMPROVEMENT (current is correct)

---

## G. Plan Export — plan_export.py

### Issue: Plans aren't exporting (user-reported)

**Root cause investigation:**
1. Plan export uses `ThreadSafeDB` → `session_state()` → shelve
2. `session_manager.py:397` uses `shelve.open(..., writeback=True)`
3. Previous session encountered 50MB shelve bloat (trashed in that session)
4. `writeback=True` keeps ALL data in memory and rewrites entire file on `.sync()`/`.close()`
5. Concurrent sessions can corrupt the shelve or cause deadlocks

**Current status:** The shelve file was trashed but the root cause (`writeback=True`)
was not fixed. New shelve files will eventually bloat again.

**Fix needed:**
1. Change `writeback=True` to `writeback=False` in `session_manager.py`
2. When mutating values, explicitly reassign: `state[key] = modified_value`
3. Add shelve size monitoring (warn if > 1MB)
4. Add periodic cleanup of old tracking entries in plan_export.py

---

## H. Test Failures — Decision Field Mapping

### Issue: Tests expect raw `decision` values but current code maps them

Tests like `test_regression_pretooluse_blocking.py:401` assert:
```python
assert response["decision"] == decision  # e.g., "deny"
```

But current code maps for Claude cli_type:
- `"deny"` → `"block"`
- `"allow"` → `"approve"`

And for Gemini cli_type:
- `"deny"` → `"deny"` (unchanged)
- `"allow"` → `"allow"` (unchanged)

**Options:**
1. Update tests to expect mapped values per cli_type
2. Add `permissionDecision` field with raw value (already present)
3. Tests should assert `response["permissionDecision"]` for raw value

**Recommended**: Tests should check `response["permissionDecision"]` for the raw
decision AND `response["decision"]` for the CLI-mapped value. This validates
both the mapping logic and the raw value preservation.

---

## I. Legacy Handler cli_type Propagation

### Issue: `pretooluse_handler()` in main.py doesn't pass cli_type from payload

The legacy `pretooluse_handler()` creates `EventContext` without extracting cli_type
from the payload. When tests set `source: "gemini"` in the payload, `EventContext`
auto-detects "claude" from env vars (no Gemini env vars set in test).

**Fix**: Extract cli_type from payload before creating EventContext:
```python
def pretooluse_handler(hook_input):
    cli_type = detect_cli_type(hook_input)  # Checks payload first
    ctx = EventContext(..., cli_type=cli_type)
```

---

# UPDATED EXECUTION PLAN (Supersedes Steps 1-6 above)

## Step 1: Fix tests that wrongly assert `continue: False` for PreToolUse deny

**NO CODE CHANGES** — `continue: True` for PreToolUse deny is CORRECT per
[official hooks docs](https://code.claude.com/docs/en/hooks).

### 1a. Update all PreToolUse deny test assertions
Change `assert response["continue"] is False` → `assert response["continue"] is True`
in all PreToolUse-related test files (see Group A list above for full file list).

### 1b. Update test docstrings and header comments
```python
# BEFORE (test_regression_pretooluse_blocking.py:8-9):
# 1. core.py EventContext.respond("deny") returned continue=True for PreToolUse
#    (daemon path) — commit 662d789 introduced, this session fixed

# AFTER:
# 1. core.py EventContext.respond("deny") correctly returns continue=True for PreToolUse
#    (per official hooks docs: continue controls AI lifecycle, not tool blocking)
#    Tool blocking uses permissionDecision="deny" + exit code 2 (Bug #4669 workaround)
```

### 1c. Optionally restore JSON-escaping of reason in main.py
The stable `build_pretooluse_response()` used `json.dumps(reason)[1:-1]` to escape
special characters. Current version lost this. Minor improvement to restore:
```python
safe_reason = json.dumps(reason)[1:-1] if reason else ""
```

### 1d. Comments in core.py:686-691 and main.py:1089-1093 are CORRECT
The existing comments accurately describe that `continue: True` keeps the AI loop
running so it sees feedback. No changes needed.

## Step 2: Fix Gemini cli_type in legacy handler
In `main.py:pretooluse_handler()`, add:
```python
cli_type = detect_cli_type(hook_input)
ctx = EventContext(..., cli_type=cli_type)
```

## Step 3: Fix test decision field assertions
Update tests to check both:
- `response["permissionDecision"]` for raw value (e.g., "deny")
- `response["decision"]` for CLI-mapped value (e.g., "block" for Claude)

## Step 4: Verify command_response() (no code change needed)
Current implementation is correct. Run tests to confirm:
- `/cr:status` → `continue: True` (AI continues)
- `/cr:stop` → `continue: False` via `_halt_ai` (AI stops)

## Step 5: Fix hardcoded capture sizes in test_dual_platform_hooks_install.py
Replace 6000/4000 char windows with function-boundary extraction.

## Step 6: Fix plan export shelve bloat
- Change `writeback=True` to `writeback=False` in `session_manager.py:397,399`
- Change `writeback=True` to `writeback=False` in `ai_monitor.py:52`
- Explicitly reassign values after mutation: `state[key] = modified_value`

## Step 7: Run full test suite
```bash
uv run pytest plugins/clautorun/tests/ --tb=short -q --timeout=15
```
Target: Only pre-existing Group E failures remain.

---

# ISSUES TRACKER

| ID | Issue | Status | Root Cause | Fix Location |
|----|-------|--------|------------|--------------|
| I1 | Tests wrongly assert `continue: False` for PreToolUse deny | TESTS WRONG | Tests misunderstand `continue` semantics; per [official docs](https://code.claude.com/docs/en/hooks), `continue` controls AI lifecycle, NOT tool blocking. Tool blocking uses `permissionDecision: "deny"` + exit 2 (Bug [#4669](https://github.com/anthropics/claude-code/issues/4669) workaround) | ~40 test assertions across 7+ test files |
| I2 | `continue: True` in `respond()` and `build_pretooluse_response()` | CODE IS CORRECT | `continue: True` + `permissionDecision: "deny"` is correct per docs. Actual tool blocking handled by exit code 2 workaround for Bug [#4669](https://github.com/anthropics/claude-code/issues/4669) in `client.py:output_hook_response()` | No code change needed |
| I3 | `stopReason: ""` for PreToolUse deny | CODE IS CORRECT | `stopReason` is only used when `continue: false`; for tool blocking it's irrelevant. Bug [#4669](https://github.com/anthropics/claude-code/issues/4669) workaround uses stderr, not stopReason | No code change needed |
| I4 | `/cr:` commands cause "Operation stopped by hook" | FIXED IN DIFF | Stable `continue: False`, current parameterized | core.py:786-804 |
| I5 | Gemini E2E tests: cli_type not from payload | BUG IN DIFF | Legacy handler ignores payload source | main.py:pretooluse_handler |
| I6 | Decision field tests: expect raw, get mapped | TEST UPDATE | Tests predate cli_type mapping | test_regression*.py:401,418 |
| I7 | Plans not exporting | SHELVE BLOAT | `writeback=True` in session_manager.py | session_manager.py:397,399 |
| I8 | Hardcoded capture sizes in tests | FRAGILE | Fixed char windows break on code growth | test_dual_platform*.py:637-655 |
| I9 | Lost JSON-escaping of reason | MINOR REGRESSION | Stable used `json.dumps()[1:-1]` | main.py:build_pretooluse_response |
| I10 | test_install_pathways.py failures | PRE-EXISTING | Not in our diff | SKIP |
