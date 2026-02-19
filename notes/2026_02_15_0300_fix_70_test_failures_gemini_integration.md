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

---

## Execution Plan

### Step 1: Fix tests that wrongly assert `continue: False` for PreToolUse deny (Group A — 40 failures)

**NO CODE CHANGES to core.py or main.py** — `continue: True` is CORRECT per
[official hooks docs](https://code.claude.com/docs/en/hooks).

**Fix the tests**, not the code. All PreToolUse deny tests must assert `continue: True`.

### Step 2: Fix Gemini cli_type in legacy handler (Group B — 8 failures)

File: `plugins/clautorun/src/clautorun/main.py`
In `pretooluse_handler()`, detect cli_type from the raw payload and pass it to EventContext.

### Step 3: Fix decision field consistency tests (Group D — 4 failures)

File: `plugins/clautorun/tests/test_regression_pretooluse_blocking.py:401,418`
Update assertions to use mapped values.

### Step 4: Run full test suite and verify

```bash
uv run pytest plugins/clautorun/tests/ --tb=short -q --timeout=15
```

Target: 0 failures.

---

## Verification

All 1872 tests pass currently with the hooks rename work completed (2 commits: one for hooks rename, one for git_reset_hard_blocked test fix with real temp git repo).
