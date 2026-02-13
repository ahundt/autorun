# Clautorun Plugin Hook Failure Investigation - Feb 12, 2026

## Investigation Overview

The user reported a critical bug where clautorun plugins fail during initial calls, specifically noting "PreToolUse:Bash hook error" in Claude Code. This investigation maps the end-to-end architecture, identifies root causes in recent regressions (via `git diff main`), and proposes project-compliant fixes.

### Core Findings & Regressions (Current vs Main)

1.  **Missing Session ID on Startup**: Claude Code's `SessionStart` hook often omits the `session_id`. In `main`, this was handled gracefully. Current branch refactored `resolve_session_key` and `normalize_hook_payload`, which now return an empty string, triggering `SessionStateError("Session ID must be a non-empty string")` in `ThreadSafeDB`.
2.  **Hook Command Transformation**: `hooks.json` was changed from `python3 ${ROOT}/hooks/hook_entry.py` to `uv run --quiet --project ${ROOT} python ${ROOT}/hooks/hook_entry.py`. While more robust, this adds overhead and potential for `uv` warnings to leak into `stdout`.
3.  **Stdin Re-consumption Bug**: `hook_entry.py` was refactored to read `stdin` once in `main()` and pass it to `try_cli()`. If `try_cli()` fails, it restores `sys.stdin` via `io.StringIO`. Any bug in this restoration or the subsequent `run_fallback()` would result in empty payloads reaching the client.
4.  **Output Pollution (The "Hook Error" Trigger)**: Claude Code treats **any** `stderr` as a "hook error" and silently ignores the hook's JSON response. Regressions included `logging` defaults and `uv run` warnings.
5.  **Exit Code 2 Complexity**: The Bug #4669 fix requires exit code 2 for `deny` decisions. Combined with `uv run`, this increases the risk of "hook error" UI messages if `uv` outputs anything to `stdout` before the JSON.
6.  **Incomplete Response Validation**: `Stop` hooks were failing validation because `hookSpecificOutput` was missing. Claude Code requires this for all events. (Fixed in `008e08f`).
7.  **Client-Side Buffer Risk**: While the daemon was recently patched to handle 1GB payloads (fixing `LimitOverrunError` seen on `2026-02-10`), the client (`client.py`) still defaults to 64KB. There is currently no log evidence of the client crashing, but hooks that inject large templates (e.g. `procedural_injection_template`) alongside a long list of tasks remain a potential risk.

---

## End-to-End Workflow Trace (Round-Trip)

To understand how a hook call (e.g., `rm /tmp/test.txt`) is processed:

1.  **CLI Trigger**: Claude Code detect tool use -> executes command in `hooks.json`.
2.  **Hook Entry** (`hook_entry.py`):
    *   `main()`: Reads `stdin` once (`L455`).
    *   `try_cli()`: Spawns `clautorun` binary (`L189-247`).
3.  **Client Layer** (`client.py`):
    *   `run_client()`: Reads payload, connects to Unix socket (`L116-213`).
    *   `forward()`: Sends JSON to daemon, waits for response (`L138-152`).
4.  **Daemon Layer** (`core.py`):
    *   `handle_client()`: Receives payload via socket (`L799-847`).
    *   `normalize_hook_payload()`: Maps Gemini/Claude fields (`L81-180`).
    *   `resolve_session_key()`: Determines stable session identity (`L240-270`).
5.  **Logic Engine** (`plugins.py` / `integrations.py`):
    *   `check_blocked_commands()`: Matches against `DEFAULT_INTEGRATIONS` (`plugins.py:446-544`).
6.  **Response Return**:
    *   **Daemon → Client**: Clean JSON via socket.
    *   **Client → Hook Entry**: `output_hook_response()` (`L54-113`) selects exit code (0 or 2).
    *   **Hook Entry → CLI**: `try_cli()` passes through stdout, stderr, and exit code.

---

## Key Files, Functions & Line Ranges

| File | Function | Line Range | Purpose |
| :--- | :--- | :--- | :--- |
| `hooks/hook_entry.py` | `try_cli` | 189-247 | Binary fast-path and exit code passthrough. |
| `hooks/hooks.json` | N/A | 1-40 | Hook event mapping configuration. |
| `src/clautorun/client.py`| `output_hook_response`| 54-113 | Unified output handler (DRY). |
| `src/clautorun/core.py` | `resolve_session_key` | 240-270 | Tri-layer identity resolution. |
| `src/clautorun/core.py` | `handle_client` | 799-847 | Daemon socket server handler. |
| `src/clautorun/install.py`| `find_marketplace_root`| 280-320 | Outermost root discovery. |
| `src/clautorun/install.py`| `show_status` | 1505-1600| Health and environment reporter. |
| `src/clautorun/restart_daemon.py`| `restart_daemon` | 300-350 | Graceful restart controller. |

---

## Bug-Specific References

| Bug Description | File | Function | Lines | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Empty Session ID** | `core.py` | `resolve_session_key` | 268 | **REPT**: Causes `SessionStateError`. |
| **Path Duplication** | `install.py` | `find_marketplace_root` | 290 | **REPT**: Duplicate `plugins/clautorun`. |
| **Readiness Race** | `restart_daemon.py`| `restart_daemon` | 305 | **REPT**: 0.5s wait too short for lock. |
| **Stop Hook Error** | `core.py` | `respond` | 587 | **FIXED**: Commit `008e08f`. |
| **Stderr Leak** | `ai_monitor.py" | `setup_clautorun_logging`| 45 | **FIXED**: Commit `8f778e8`. |
| **Client Buffer** | `client.py` | `forward` | 138 | **REPT**: 64KB default too small. |

---

## Unified System-Wide Buffer Configuration

A critical architectural inconsistency exists between the daemon and client buffer configurations. While the daemon is large-payload safe, the client is vulnerable to `LimitOverrunError` when receiving expanded templates.

### Proposed Unification: `READ_BUFFER_LIMIT` (1GB)

**Before (`core.py` local constant)**:
```python
_DEFAULT_LIMIT = asyncio.streams._DEFAULT_LIMIT  # 64KB
READ_BUFFER_LIMIT = _DEFAULT_LIMIT * (2 ** 14)  # 1GB (Local to core.py)
```

**After (Proposed `config.py` single source of truth)**:
```python
# config.py
import asyncio
DEFAULT_ASYNC_LIMIT = 65536  # 64KB
# Shared 1GB limit for all system endpoints
READ_BUFFER_LIMIT = int(os.environ.get("CLAUTORUN_BUFFER_LIMIT", DEFAULT_ASYNC_LIMIT * (2 ** 14)))
```

**Before (`client.py` L138)**:
```python
reader, writer = await asyncio.open_unix_connection(path=str(SOCKET_PATH))
```

**After (Proposed `client.py` Fix)**:
```python
# Proposed: Use the unified 1GB limit
from .config import READ_BUFFER_LIMIT
reader, writer = await asyncio.open_unix_connection(
    path=str(SOCKET_PATH),
    limit=READ_BUFFER_LIMIT
)
```

---

## Dual-Path Handling (Claude vs Gemini)

The system must handle fundamental differences in how Claude Code and Gemini CLI resolve plugin paths and execute hooks.

### Path Variable Substitution
- **Claude Code**: Uses `${CLAUDE_PLUGIN_ROOT}` in `hooks.json`. Substituted by Claude at runtime.
- **Gemini CLI**: Uses `${extensionPath}` in `gemini-hooks.json`. Substituted by Gemini at runtime.

### Root Detection Strategy (`hook_entry.py`)
To remain "first-class" on both platforms, `hook_entry.py` implements a hierarchical discovery logic:

1.  **Explicit Override**: `CLAUTORUN_PLUGIN_ROOT` env var.
2.  **Claude Native**: `CLAUDE_PLUGIN_ROOT` env var.
3.  **Gemini/Portable**: Inference via `__file__`. Since `hook_entry.py` is always at `<root>/hooks/hook_entry.py`, the root is deterministically `dirname(dirname(__file__))`.

### Handling Differences in `install.py`
The installer now uses a **Temporary-Copy Strategy** for Gemini to avoid corrupting the source repository's Claude-specific `hooks.json`.

**Before** (Direct Modification):
```python
# Contaminated source with ${extensionPath}, breaking Claude Code
shutil.copy2(gemini_hooks_file, hooks_file)
run_cmd(["gemini", "extensions", "install", ...])
```

**After** (Proposed Temp Strategy):
```python
import tempfile
with tempfile.TemporaryDirectory() as temp_dir:
    temp_plugin = Path(temp_dir) / plugin_name
    shutil.copytree(plugin_dir, temp_plugin)
    # Only swap hooks in the isolated temp directory
    shutil.copy2(temp_plugin / "hooks/gemini-hooks.json", temp_plugin / "hooks/hooks.json")
    run_cmd(["gemini", "extensions", "install", str(temp_plugin), "--consent"])
```

---

## Proposed Fixes & Code Blocks

### Fix 1: Idempotent Root Discovery (`install.py`)
Prevents duplicated path segments in health status by finding the *outermost* marker.

**Before** (`L290`):
```python
    for parent in [current, *current.parents]:
        marker = parent / ".claude-plugin" / "marketplace.json"
        if marker.exists():
            return parent
```

**After** (Proposed):
```python
    # Proposed: Find the highest parent containing the marker (outermost root)
    root = None
    for parent in [current, *current.parents]:
        if (parent / ".claude-plugin" / "marketplace.json").exists():
            root = parent
    if root:
        return root
```

### Fix 2: Session ID Fallback (`core.py`)
Prevents daemon crashes on initial `SessionStart` calls where Claude Code omits the ID.

**Before** (`L268`):
```python
    # Layer 3: Fallback to session_id from payload
    return fallback_id
```

**After** (Proposed):
```python
    # Layer 3: Fallback to session_id from payload
    if not fallback_id:
        # Use stable PID-based identity if session_id is missing (startup hooks)
        return f"pid:{pid}" if pid else "default_session"
    return fallback_id
```

### Fix 3: Socket-Based Readiness (`restart_daemon.py`)
Replaces fragile `sleep(0.5)` with designs-intended deterministic check.

**Before** (`L305`):
```python
        time.sleep(0.5)  # Let daemon initialize
        new_pid = get_daemon_pid()
```

**After** (Proposed):
```python
        # Poll for socket readiness (designs-intended indicator)
        start = time.time()
        while time.time() - start < 3.0:
            if is_daemon_responding():
                return 0
            time.sleep(0.1)
```

### Fix 4: JSON-Safe Output Extraction (`hook_entry.py`)
Prevents "JSON validation failed" by stripping any non-JSON noise (warnings, logs) from `stdout`.

**Before** (`L263`):
```python
        # Print JSON to stdout (required for Claude Code)
        print(result.stdout, end="")
```

**After** (Proposed):
```python
        # Proposed: Extract only the JSON block to strip any noise/warnings
        import re
        if result.stdout:
            # Look for the last JSON-like block in the output
            json_match = re.findall(r'(\{.*?\})', result.stdout, re.DOTALL)
            if json_match:
                # Use the last match (allows for leading logs but only one response)
                print(json_match[-1], end="")
            else:
                # Fallback to raw output if no JSON found
                print(result.stdout, end="")
```

### Fix 5: Synchronized Buffer Limits (`client.py`)
Ensures the client can receive large responses from the daemon.

**Before** (`L138`):
```python
            reader, writer = await asyncio.open_unix_connection(path=str(SOCKET_PATH))
```

**After** (Proposed):
```python
            # Proposed: Use the same 1GB limit as the daemon
            from .core import READ_BUFFER_LIMIT
            reader, writer = await asyncio.open_unix_connection(
                path=str(SOCKET_PATH),
                limit=READ_BUFFER_LIMIT
            )
```

### Fix 6: Allow-list Clautorun Commands (`/cr:planrefine`)
Addresses the "Operation stopped by hook" bug where internal clautorun commands are accidentally blocked.

**User Instruction (Quoted)**:
> "❯ /cr:planrefine keep the plan intact and cross reference against teh code and make sure it uses the proper syntax features and decorators where appropriate to minimize boilerplate and maximize reuse adn ensure the code is DRY (particularly the typer and click based decorators and factory functions and other patterns but don't actually add typer or click those are conceptual examples) and again keep the plan intact and do micro edits throughought to improve it dramatically WOLOG"

**Proposed DRY Fix (DRY/WOLOG Pattern)**:
Refactor command recognition to use a decorator-based registration system or a centralized allow-list for internal `/cr:` commands. This ensures that any command prefixed with `/cr:` (internal to this plugin) bypasses standard blocking logic, preventing the "Operation stopped by hook" regression.

**Before** (`plugins.py` L447+):
```python
    if ctx.tool_name in BASH_TOOLS:
        event_type = "bash"
        cmd = ctx.tool_input.get("command", "")
```

**After** (Proposed DRY Pattern):
```python
    # Proposed: Centralized prefix check (DRY)
    if cmd.startswith("/cr:"):
        # Internal clautorun commands are always allowed
        return ctx.respond("allow")
```

---

## Log Evidence (Timestamps)

- **Stop Hook validation fail**: `2026-02-12 21:14:27.591466` - "JSON validation failed: Invalid input" (Fixed: `hookSpecificOutput` added).
- **Session ID error**: `2026-02-12 21:18:29,121` - "ThreadSafeDB.get error: Session ID must be a non-empty string".
- **Socket connection loss**: `2026-02-12 21:18:41,118` - "Handler error: 0 bytes read on a total of undefined expected bytes".

---

## Sources & Documentation

### Official Specifications
- **Gemini CLI Hook Reference**: [geminicli.com/docs/hooks/reference/](https://geminicli.com/docs/hooks/reference/)
- **Claude Code Hook Reference**: [code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks)
- **Claude Exit Code Semantics**: [claude.com/blog/how-to-configure-hooks](https://claude.com/blog/how-to-configure-hooks)

### Internal Project Standards
- **CLAUDE.md**: Definitions of "concrete" and zero-stderr rules.
- **Code Quality Tests**: `plugins/clautorun/tests/test_unit_simple.py:TestCodeQuality`.
- **Bug #4669 Fix**: Commit `aad23fe`.
- **Daemon Lifecycle Analysis**: `notes/2026-02-09-daemon-lifecycle-complete-analysis.md`.

---

## 14-Day Git History Detail

| SHA | Date | Author | Summary |
| :--- | :--- | :--- | :--- |
| `df15a91` | 2026-02-12 | Andrew Hundt | fix(plan_export): temporarily disable SessionStart plan recovery to debug hang |
| `008e08f` | 2026-02-12 | Andrew Hundt | fix(core): add hookSpecificOutput to all hook responses |
| `1ea8904` | 2026-02-12 | Andrew Hundt | feat(client): add DRY hook lifecycle logging to daemon.log |
| `81c168e` | 2026-02-12 | Andrew Hundt | docs(CLAUDE.md): add primary install command with timestamped logging |
| `8735689` | 2026-02-12 | Andrew Hundt | fix(hooks): return True for exit 0 to prevent hook errors |
| `aad23fe` | 2026-02-12 | Andrew Hundt | fix(hooks): pass through exit code 2 and stderr for Bug #4669 workaround |
| `6205bd5` | 2026-02-12 | Andrew Hundt | fix(hooks): implement Bug #4669 workaround with DRY unified handler |
| `fad5005` | 2026-02-12 | Andrew Hundt | test: add code quality tests for stderr/logging requirements |
| `8f778e8` | 2026-02-12 | Andrew Hundt | fix(hooks): eliminate stderr output causing hook errors |
| `e558d83` | 2026-02-12 | Andrew Hundt | fix(restart_daemon): kill ALL daemons before restart, prevent spawn skip |
| `4c5fce9` | 2026-02-11 | Andrew Hundt | fix(hook_entry,tests): fix try_cli bugs and add 11 multi-location sync tests |
| `de24440` | 2026-02-11 | Andrew Hundt | fix(hooks): UV-centric commands and continue=false for deny decisions |
| `e0b857b` | 2026-02-10 | Andrew Hundt | fix(install): copy gemini-hooks.json during Gemini installation |
| `904a277` | 2026-02-10 | Andrew Hundt | fix(hooks): fix get_plugin_root() to work without environment variables |

---

## Main Branch Status

| Detail | Value |
| :--- | :--- |
| **Latest Commit** | `3953382` |
| **Commit Date** | 2026-02-08 23:08:58 -0500 |
| **Summary** | fix(pipe-detection,cli) fix pipe blocking and add daemon restart |
| **Key Changes** | Piped commands allowed, TTY checks for non-interactive CLI, restart script created. |

**Current Divergence**: The active feature branch (`feature/gemini-cli-integration`) is approximately **15 commits ahead** of `main`, containing critical architectural changes for Bug #4669 (Exit 2), unified hook responses, and zero-stderr enforcement.

---

## False Assertions & Misleading Results

1.  **"Daemon did not start"**: Reported when daemon is up but hasn't written its lock file yet.
2.  **"Workspace not installed"**: Reported by `show_status` because it doesn't recognize the `cr` alias.
3.  **"Hook Error" label**: UI bug in Claude Code showing "error" for successful Exit 2 blocking responses.
