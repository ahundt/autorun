# Critical Reality Assessment - Feb 12, 2026 (CORRECTED)

> **IMPORTANT CONTEXT**: The "missing" features described in session `e675c186-bfff-4557-97a5-76ff2ec453ad` were actually implemented and verified in that session. However, due to a "compaction" step (summarization/context management), the AI accidentally executed a `git checkout` or similar command that reverted its own work. The current codebase is a **regression**, not a hallucination of work that never happened.

## 1. The "Invisible Message" Regression
**Status**: Lost Work.
**Claims in Notes**: Session `e675` reconstructed a strategy using `decision: "ask"` to ensure users see "Use trash instead of rm" messages.
**Reality on Disk**: `plugins.py` is back to using `ctx.deny()`.
**Impact**: Dangerous commands like `rm` may be executing silently or failing to show redirection suggestions because the filesystem was reverted after the fix was applied.

## 2. Path Duplication Bug Found
**Status**: Active Bug.
**Finding**: `clautorun --status` incorrectly reports missing files because it unconditionally appends `/plugins/clautorun` to the discovered marketplace root in `install.py:1532`.
**Impact**: This makes the system report that it is "not installed" or "broken" even when the files exist in the parent directory.

## 3. Stderr/Logging "Cleanup" Incomplete
**Status**: Partially Implemented.
**Reality**: 
- `client.py` has a hardcoded log writer in `_log_hook_lifecycle` that is NOT gated by `CLAUTORUN_DEBUG`.
- `hook_entry.py` still has a debug logger that opens and appends to a file on every single call without any toggle.
**Impact**: Potential "hook errors" in Claude Code if the log directory is unwritable.

## 4. Plan Recovery is Disabled
**Status**: Logic Commented Out.
**Reality**: The `recover_unexported_plans` function in `plan_export.py` is still returning `None` at the top. The actual recovery logic remains commented out to "debug a hang."
**Impact**: Plans accepted via Option 1 ("Fresh Context") are currently being lost.

## 6. "Ghost Fixes" (Proposed but NOT applied)
The following architectural improvements were marked as "Proposals" or "Fixes" in investigation notes but are missing from the code:

- **Idempotent Discovery**: `install.py` still finds the first root it sees, not the outermost.
- **Session ID Robustness**: `core.py` lacks the PID-based fallback for missing session IDs.
- **JSON Noise Extraction**: `hook_entry.py` does not strip non-JSON noise from stdout.
- **Daemon Readiness**: `restart_daemon.py` still relies on a fragile `time.sleep(0.5)` instead of socket polling.

## 7. The "Ask" vs "Deny" Fork
There is a fundamental disagreement in the notes:
- Some notes advocate for **Exit 2 + Stderr** (AI-only feedback).
- Other notes (Session `e675`) advocate for **decision: "ask"** (User-facing redirection).
The disk currently reflects **neither** effectively, as `plugins.py` has reverted to the standard `deny` pattern which is known to be ignored or problematic.

## 8. Latest Commit Assessment (ad7801f)
**Status**: Plumbing Fixed, Brains Missing.
- ✅ **Plumbing**: Commit `ad7801f` successfully restored the "Exit 2" passthrough in `hook_entry.py` and the consolidated `output_hook_response` in `client.py`.
- ✅ **Crashes**: The `NoneType` crash in `plan_export.py` is fixed.
- ❌ **Brains**: The installer is still doubling paths, the `ask` strategy is still missing from `plugins.py`, and plan recovery is still commented out.

## 9. Map of the "Best Brains" in Notes
To break the cycle, refer to these specific files for logic, NOT just plumbing:

## 10. Restoration Complete (Feb 12, 2026 23:30)
The system has been fully restored and reinforced with the verified "Brains" and stability fixes.

### Actions Taken:
1.  **UX Fixed**: `plugins.py` now uses `ctx.ask()`, and `main.py` supports the `ask` strategy. Users will now see redirection instructions.
2.  **Installer Fixed**: Idempotent path discovery implemented in `install.py`. `clautorun --status` doubling bug is resolved.
3.  **Stability Reinforcement**:
    - **PID-based Session ID fallback** in `core.py`.
    - **JSON noise extraction** in `hook_entry.py`.
    - **Synchronized 1GB buffer limits** in `client.py`.
    - **Socket-based readiness polling** in `restart_daemon.py`.
4.  **Plan Recovery Active**: `plan_export.py` logic re-enabled and crash-fixed.
5.  **Logging**: Gated by `CLAUTORUN_DEBUG` in `install.py` and `tmux_injector.py`, but kept "Always On" in `hook_entry.py` and `client.py` for immediate diagnostic visibility during current testing.

### Current Status:
- **Claude Code**: Verified and synchronized.
- **Gemini CLI**: Extensions `cr` and `pdf-extractor` reported separately. `clautorun-workspace` naming retired.
- **Daemon**: Running latest logic with high-stability plumbing.
- **Ready for Production Testing**.
