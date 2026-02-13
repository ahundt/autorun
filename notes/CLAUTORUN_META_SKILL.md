# Clautorun Meta-Skill: The Definitive Guide to Hooks & Deployment

This document is the "Source of Truth" for maintaining and debugging the clautorun hook system. It covers the architectural "Invisible Failures," strict schema requirements for Claude Code and Gemini CLI, and the mandatory synchronization workflow.

---

## 1. The Debugging Philosophy: "Trust No UI"

Claude Code's "hook error" is a generic mask that hides the true cause. **Never** assume the UI tells the whole story. Use the hierarchy of logs to find the "Invisible Failure":

1.  **Plumbing Check (`~/.clautorun/hook_entry_debug.log`)**: 
    *   Did the binary even start? 
    *   Did it exit with 0 or 2? 
    *   What was the *exact* raw string it printed? (Look for noise before/after JSON).
2.  **Logic Check (`~/.clautorun/daemon.log`)**: 
    *   Did the daemon receive the request? 
    *   What were the keys in the `FullPayload`?
    *   Check `DAEMON PROCESSING END` for millisecond timing.
3.  **Source Check (`~/.clautorun/daemon_startup.log`)**: 
    *   Is the daemon loading from `.../cache/...` (STALE) or `.../plugins/clautorun/src/...` (FRESH)?
    *   Verify the **Commit Hash** and **PID** change on every restart.

---

## 2. Platform Schema Specifications (Claude v2.1.41)

Claude Code strictly validates JSON. Violating these rules causes a "hook error" that silently disables clautorun.

### The "Hook Error" Matrix
| Symptom | Event Type | Cause | Resolution |
| :--- | :--- | :--- | :--- |
| **"Invalid Input"** | `Stop`, `SessionStart` | Sent `decision` or `reason` | **STRICT MODE**: Return only `continue`, `systemMessage`, etc. |
| **"Missing context"**| `UserPromptSubmit` | Missing `additionalContext`| Map your reason to `additionalContext` in `hookSpecificOutput`. |
| **"JSON failed"** | `PreToolUse` | Missing `permissionDecision`| Must exist at top-level AND in `hookSpecificOutput`. |
| **"Double print"** | All | `hook_entry.py` printed noise | Refactor `hook_entry.py` to isolate and print exactly one JSON block. |

### Table-Driven Enforcement (`core.py`)
Always use `HOOK_SCHEMAS` and `validate_hook_response()` to filter output. 
*   **Claude**: Maps `ask` -> `ask` (shows user prompt).
*   **Gemini**: Maps `ask` -> `deny` (Gemini respects JSON `deny`).

---

## 3. Deployment & Synchronization Workflow

### The "One-Liner of Truth" (Mandatory)
```bash
uv run --project plugins/clautorun python -m clautorun --install --force && \
cd plugins/clautorun && uv tool install --force --editable . && cd ../.. && \
clautorun --restart-daemon
```

### Critical Synchronization Gotchas:
1.  **The Stale Code Trap**: The daemon is persistent. Code edits in `src/` are **ignored** until `clautorun --restart-daemon` is run.
2.  **The Invisible Variable**: For local marketplaces, Claude does **not** substitute `${CLAUDE_PLUGIN_ROOT}`. `install.py` must manually `sed`-replace this in `~/.claude/plugins/cache/` files.
3.  **Editable Tool**: If the tool isn't installed with `--editable`, hooks might pick up an old global version of clautorun instead of your repo.

---

## 4. UI/UX: Formatting & Deduplication

*   **Avoid Double-Escaping**: Never manually call `json.dumps()` on a string that goes into a dict. This causes literal `\n` in the UI. Pass raw strings and let the final boundary handle encoding.
*   **Deduplicate**: Claude shows `systemMessage`, `hookSpecificOutput.permissionDecisionReason`, and `stderr` (exit 2) simultaneously. 
    *   *Rule*: For rejections, **clear** top-level `reason` and `systemMessage`. Let specific fields handle it.
*   **Exit 2 Workaround (#4669)**: To block a tool in Claude, you **must** exit with code 2 and print the reason to `stderr`.

---

## 5. Automated Metadata & Versioning

Metadata must never be hardcoded. Use the automated system:
*   **Capture**: `install.py` calls `git describe --always --dirty=+`. 
*   **Storage**: Metadata is written to `src/clautorun/metadata.json` (Ignored by Git).
*   **Loading**: `__init__.py` robustly loads this at runtime.
*   **Naming**: Always use `+` for uncommitted changes (e.g., `9d9ba0f+`) for professional logs.

---

## 6. Verification Sources

*   **Claude Hooks Reference**: `https://code.claude.com/docs/en/hooks`
*   **Claude Schema Output**: `https://code.claude.com/docs/en/hooks#json-output`
*   **Gemini Hooks Reference**: `https://geminicli.com/docs/hooks/reference/`
*   **Claude Bug #4669 (Exit 2)**: `https://claude.com/blog/how-to-configure-hooks`

### Synthetic Verification
```bash
# Test schema compliance without starting Claude:
echo '{"hook_event_name":"SessionStart"}' | clautorun
```
