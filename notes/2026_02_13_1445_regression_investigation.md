# Regression Investigation: Hook Errors (SessionStart & UserPromptSubmit)

**Date:** 2026-02-13 14:45
**Objective:** Resolve "hook error" regressions in Claude Code for `SessionStart` and `UserPromptSubmit` events while maintaining Gemini compatibility.

## Current Tasks & Progress

### 1. Investigation [COMPLETE]
- [x] Analyze `~/.clautorun/hook_entry_debug.log` for raw hook output.
- [x] Analyze `~/.clautorun/daemon.log` for payload and processing details.
- [x] Cross-reference `respond()` logic in `core.py` with strict Claude schemas.

### 2. Regression Fixes [COMPLETE]
- [x] Fix `SessionStart` schema (Verify no `decision`, `reason`, or `hookSpecificOutput`).
- [x] Fix `UserPromptSubmit` schema (Verify `additionalContext` in `hookSpecificOutput`).
- [x] Verify `detect_cli_type()` reliability (Ensure Claude doesn't get Gemini responses).
- [x] Update `client.py` to use `validate_hook_response` for fail-open paths.
- [x] Update `main.py` builders to use `validate_hook_response`.
- [x] Update `plan_export.py` legacy handlers to use `validate_hook_response`.

### 3. Verification [IN PROGRESS]
- [ ] Implement synthetic hook tests for Claude schemas.
- [ ] Run full test suite.
- [ ] Verify in real Claude/Gemini sessions.

## Reported Regressions
- `SessionStart:resume hook error`
- `UserPromptSubmit hook error`

## Potential Root Causes (Pre-mortem)
1. **Schema Violation:** My recent changes to `respond()` in `core.py` might be sending forbidden fields (like `decision` or `reason`) to Claude for lifecycle events.
2. **Detection Failure:** If `detect_cli_type()` incorrectly identifies Claude as Gemini, the strict filtering is bypassed.
3. **Stderr Noise:** Any stray print or warning on `stderr` during these hooks will trigger a "hook error" in Claude.
