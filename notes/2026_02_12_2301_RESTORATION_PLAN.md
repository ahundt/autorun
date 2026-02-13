# Restoration Plan: Breaking the Hook Failure Loop

**Date**: Feb 12, 2026
**Derived From**: Session `e675c186-bfff-4557-97a5-76ff2ec453ad` (Reconstructed)

## 1. Fix "Invisible Redirection" (The Ask Strategy)
The current `deny` strategy hides "Use trash instead" messages from the user. We must switch to the `ask` strategy verified in session `e675`.

### A. Update `plugins/clautorun/src/clautorun/plugins.py`
Replace all `ctx.deny(msg)` with `ctx.respond("ask", msg)`.
- **Lines 132, 137, 471, 476, 537**: Change `deny` to `respond("ask", ...)` or `ask(...)`.

### B. Update `plugins/clautorun/src/clautorun/main.py`
Update `build_pretooluse_response` (Line 1021+) to properly support the `ask` decision and include the `_exit_code_2` marker for the client.

```python
def build_pretooluse_response(decision="allow", reason=""):
    safe_reason = json.dumps(reason)[1:-1] if reason else ""
    should_continue = decision != "deny"
    return {
        "decision": decision,
        "reason": safe_reason,
        "continue": should_continue,
        "stopReason": safe_reason if not should_continue else "",
        "suppressOutput": False,
        "systemMessage": safe_reason,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": safe_reason,
        },
        "_exit_code_2": decision == "deny",
    }
```

## 2. Fix Installer Path Duplication
### File: `plugins/clautorun/src/clautorun/install.py` (Line 1532)
**Change**:
```python
# From:
plugin_dir = marketplace_root / "plugins" / "clautorun"
# To:
plugin_dir = marketplace_root if (marketplace_root / "pyproject.toml").exists() else marketplace_root / "plugins" / "clautorun"
```

## 3. Finalize Stderr Cleanup (Gating)
### File: `plugins/clautorun/src/clautorun/client.py` (Line 52)
**Change**:
```python
def _log_hook_lifecycle(message: str, **kwargs) -> None:
    if os.environ.get('CLAUTORUN_DEBUG') != '1':
        return
    # ... existing write logic ...
```

### File: `plugins/clautorun/hooks/hook_entry.py` (Lines 170, 310)
Add `if os.environ.get('CLAUTORUN_DEBUG') == '1':` around the `with open(debug_log, 'a') as f:` blocks.

## 4. Re-enable Plan Recovery
### File: `plugins/clautorun/src/clautorun/plan_export.py` (Line 913)
- Remove `return None` at line 939.
- Uncomment logic from line 942 to 965.
- Ensure `ctx.event` is used for logging instead of `ctx.payload`.

## 5. Architectural Alignment
1. **UV Tool**: `uv tool install --force --editable plugins/clautorun`
2. **Gemini**: `gemini extensions link .` (from workspace root)
3. **Daemon**: `clautorun --restart-daemon`
4. **Claude**: Restart Claude Code completely.
