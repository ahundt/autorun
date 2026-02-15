# Progress Report: Unified CLI Support & Gemini Blocking Fixes
**Date:** Friday, February 13, 2026
**Session:** Gemini CLI Integration Refinement

## Context
We are establishing `clautorun` as a first-class citizen on both Claude Code and Gemini CLI. This requires unified event handling, strict schema validation, and CLI-specific decision mapping (e.g., mapping Claude's `deny` to Gemini's `deny` while using the `#4669` workaround for Claude).

## Current Progress

### Core Architecture Improvements
- **Signature Updates:** Updated `build_pretooluse_response` and `build_hook_response` in `main.py` and `__init__.py` to accept a `ctx` object. This ensures the detected `cli_type` is available during response construction.
- **Testability:** Refactored `main.py`'s `main()` and `client.py`'s `run_client`/`output_hook_response` to return integer exit codes (0, 1, 2) instead of calling `sys.exit()` directly. This allows tests to verify behavior without terminating the test process.
- **CLI Detection:** Refined `detect_cli_type()` in `config.py` to aggressively check for Gemini markers, including a new `raw_event` field in normalized payloads.

### Bug Fixes
- **Indentation Error:** Fixed a duplicate `hookSpecificOutput` and unexpected indent in `main.py` that was breaking imports.
- **Decision Mapping:** Implemented logic in `respond()` and `build_pretooluse_response` to map internal `deny` to `deny` for Gemini (top-level `decision`) and `ask` for Claude (Bug #4669 workaround).
- **Schema Validation:** Updated `HOOK_SCHEMAS` and `validate_hook_response` to be more permissive for Gemini while maintaining strictness for Claude.

## Outstanding Tasks

### 1. Fix `test_gemini_e2e_improved.py` Failures
The test `test_installed_hook_blocks_cat` is still failing with:
`E   AssertionError: INSTALLED hook did NOT block 'cat'! Response: {"decision": "block", ...}`
Expected `"decision": "deny"`.

**Investigation Strategy:**
- Verify if `ctx.cli_type` is correctly reaching `respond()`.
- Check if the daemon is using a cached version of `cli_type` or re-detecting incorrectly.
- Confirm all `build_hook_response` and `build_pretooluse_response` calls in `main.py` are passing the `ctx` object.

### 2. Finalize `main.py` Updates
- Complete the update of all 27+ `build_hook_response` calls to pass `ctx=ctx`.
- Ensure all handlers in `HANDLERS` dictionary are passing the `ctx` object down.

### 3. Verification
- Run `uv run pytest plugins/clautorun/tests/test_gemini_e2e_improved.py` and ensure all 30 tests pass.
- Run `uv run pytest plugins/clautorun/tests/test_hook.py` to ensure no Claude regressions.
- Perform a manual test in a Gemini CLI session to verify real-world blocking.

## Critical Context Info
- **Daemon vs Standalone:** `clautorun` defaults to `CLAUTORUN_USE_DAEMON=1`. The daemon handles requests from both CLIs. Per-request detection is vital.
- **Bug #4669:** Claude Code ignores `permissionDecision: "deny"` if exit code is 0. We must use `permissionDecision: "ask"` OR exit 2 + stderr. Gemini CLI *requires* `decision: "deny"` at exit 0 to block.
- **Token Efficiency:** We are using `rtk` to optimize shell outputs and minimize token usage during these complex refactors.
