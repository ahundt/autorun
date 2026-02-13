# Bug #4669 Workaround: DRY Unified Output Handler

**Plan File**: `/Users/athundt/.claude/plans/staged-snacking-dewdrop.md`

**Export Instructions**: After implementation, copy this plan to notes:
```bash
cp /Users/athundt/.claude/plans/staged-snacking-dewdrop.md \
   notes/$(date +"%Y_%m_%d_%H%M")_bug_4669_hook_entry_exit_code_fix.md
```

## Context

**The Problem**: Claude Code ignores `permissionDecision: "deny"` at exit 0 (bug #4669). Tool executes anyway.

**Root Cause Analysis** (Round-Trip Trace):
```
Claude Code → hook_entry.py → clautorun binary → client.py → daemon (processes, returns decision)
                                                                 ↓
                                                    client.py receives response
                                                                 ↓
                                                    ← CRITICAL OUTPUT POINT (output_hook_response)
                                                                 ↓
                                                    client.py exits with code 0 or 2
                                                                 ↓
                                                    hook_entry.py (passes through exit code)
                                                                 ↓
                                                    Claude Code
```

**Key Point**: client.py's `output_hook_response()` runs AFTER daemon responds, not before.

**Current Issues**:

**client.py**:
1. ❌ **4 different output paths** (lines 90, 93, 105-110, 152-157)
2. ❌ **No exit code logic** - all paths exit 0
3. ❌ **Not DRY** - `print(json.dumps({...}))` duplicated 3 times
4. ❌ **No workaround** - never exits with code 2

**hook_entry.py** (CRITICAL BUG - blocks workaround):
5. ❌ **Rejects exit 2** - line 251: `if result.returncode != 0: return False`
6. ❌ **Always exits 0** - line 153: `fail_open()` → `sys.exit(0)`
7. ❌ **Breaks workaround** - client.py returns exit 2 + stderr, hook_entry.py converts to exit 0 + stderr = "hook error" = fail-open

## Solution: Two Clear Pathways with Single Flag Check (DRY + Pythonic)

### Design Principles

1. **DRY**: Single function, single flag check, shared decision logic
2. **Two pathways**: Primary (exit 0) and Workaround (exit 2), clearly separated
3. **Pythonic**: Clean conditionals, explicit control flow
4. **Flag-driven**: One check determines entire pathway
5. **Complete**: Handles all 4 input paths through unified output

### Architecture: Two Pathways, One Flag Check

```python
def output_hook_response(response, source):
    # Shared: Parse response and extract decision (DRY)
    decision = extract_decision(response)
    print(json.dumps(response))  # Always print JSON

    # Single flag check determines pathway
    if decision == "deny" and should_use_exit2_workaround():
        # ╔═══════════════════════════════════════════════╗
        # ║ PATHWAY A: Bug #4669 Workaround (Claude Code)║
        # ╚═══════════════════════════════════════════════╝
        reason = extract_reason(response)
        print(reason, file=sys.stderr)  # AI feedback
        sys.exit(2)  # Actual blocking
    else:
        # ╔═══════════════════════════════════════════════╗
        # ║ PATHWAY B: Standard Behavior (Gemini/Allow)  ║
        # ╚═══════════════════════════════════════════════╝
        sys.exit(0)  # Normal success
```

**Key Properties**:
- **DRY**: JSON printing, decision extraction shared
- **Clear**: Two pathways explicitly separated by if/else
- **Single check**: `should_use_exit2_workaround()` called once
- **Pythonic**: Simple conditional, no complex nesting
- **Complete**: All 4 input sources converge here

## Implementation

### 0. Copy Plan to Notes Folder

**Action**: After completing all steps, copy this plan file to notes with timestamp:

```bash
cp /Users/athundt/.claude/plans/staged-snacking-dewdrop.md \
   notes/$(date +"%Y_%m_%d_%H%M")_bug_4669_hook_entry_exit_code_fix.md
```

**Purpose**: Archive the plan for future reference

---

### 1. Fix hook_entry.py to Pass Through Exit Codes (CRITICAL)

**File**: `plugins/clautorun/hooks/hook_entry.py`
**Lines**: 246-259

**Problem**: Line 251 rejects exit 2, line 256 always exits with 0 (implicit via return True → caller exits 0)

**BEFORE** (lines 246-259):
```python
        # Exit code 0 = CLI succeeded (even when denying tool access)
        # Exit code 2 would be a blocking ERROR causing "hook error"
        # The JSON permissionDecision: "deny" blocks the tool, not exit code

        # Must check return code — stale CLI installs fail with argparse errors
        if result.returncode != 0:
            return False

        # Must have stdout output — hook response is JSON on stdout
        if result.stdout:
            print(result.stdout, end="")
            return True

        return False  # No output = something went wrong
```

**AFTER** (lines 246-265):
```python
        # ═══════════════════════════════════════════════════════════════
        # TWO PATHWAYS: Primary (exit 0) and Workaround (exit 2)
        # ═══════════════════════════════════════════════════════════════
        # Exit 0: Normal (allow OR Gemini deny)
        # Exit 2: Claude Code Bug #4669 workaround (deny + stderr → AI)
        # Other: Error (stale install, import failure, etc.)
        # ═══════════════════════════════════════════════════════════════

        # Must check return code — only 0 and 2 are valid
        if result.returncode not in (0, 2):
            return False

        # Must have stdout output — hook response is JSON on stdout
        if not result.stdout:
            return False

        # Print JSON to stdout (required for Claude Code)
        print(result.stdout, end="")

        # Pass through stderr if present (Bug #4669: stderr → AI for exit 2)
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)

        # Pass through exit code to Claude Code (DRY: client.py decides)
        sys.exit(result.returncode)
```

**Key Changes**:
1. **Line 251**: `if result.returncode not in (0, 2)` — accept BOTH exit codes
2. **Line 260**: `print(result.stderr, ...)` — pass through stderr for workaround pathway
3. **Line 263**: `sys.exit(result.returncode)` — pass through exit code (NOT hardcoded 0)

**Why DRY**: Client.py makes the pathway decision (flag check), hook_entry.py just passes it through

### 2. Add Flag Check Functions to config.py

**File**: `plugins/clautorun/src/clautorun/config.py`
**Lines**: 416+ (after CONFIG dict closes on line 415)

**BEFORE**: (nothing, adding new code)

**AFTER** (lines 418-472):
```python
# =============================================================================
# CLI Detection and Bug #4669 Workaround (v0.8.0+)
# =============================================================================


def detect_cli_type() -> str:
    """Detect which CLI is calling (Claude Code vs Gemini CLI).

    Detection order (most reliable first):
    1. GEMINI_SESSION_ID - Gemini-specific variable
    2. GEMINI_PROJECT_DIR without CLAUDE_PROJECT_DIR - Gemini only
    3. Default to "claude" (safer to apply workaround when uncertain)

    Returns:
        "claude": Claude Code (needs exit-2 workaround)
        "gemini": Gemini CLI (respects JSON decision)

    Reference: notes/hooks_api_reference.md lines 249-272
    """
    import os

    if os.environ.get("GEMINI_SESSION_ID"):
        return "gemini"

    if os.environ.get("GEMINI_PROJECT_DIR") and not os.environ.get("CLAUDE_PROJECT_DIR"):
        return "gemini"

    # Default to Claude (safer to apply workaround if uncertain)
    return "claude"


def should_use_exit2_workaround() -> bool:
    """Check if exit-2 workaround should be applied for bug #4669.

    SINGLE FLAG CHECK for pathway selection.

    Modes (CLAUTORUN_EXIT2_WORKAROUND env var):
    - "auto" (default): Use workaround ONLY for Claude Code
    - "always": Force workaround for all CLIs (testing)
    - "never": Disable workaround for all CLIs (testing/future)

    Returns:
        bool: True → Pathway A (exit 2 + stderr)
              False → Pathway B (exit 0 only)

    Reference: notes/hooks_api_reference.md lines 326-440
    """
    import os

    mode = os.environ.get('CLAUTORUN_EXIT2_WORKAROUND', 'auto').lower()

    if mode == "always":
        return True
    elif mode == "never":
        return False
    else:  # "auto" or any other value
        cli_type = detect_cli_type()
        return cli_type == "claude"
```

**Purpose**: Single source of truth for pathway selection flag

### 3. Create Unified Output Handler in client.py

**File**: `plugins/clautorun/src/clautorun/client.py`
**Lines**: 54-113 (new function before run_client())

**BEFORE**: (nothing, adding new function)

**AFTER** (lines 54-113):
```python
def output_hook_response(response: dict | str, source: str = "daemon") -> None:
    """Unified hook response output handler with two clear pathways (DRY).

    Single consolidation point for ALL 4 input paths:
    - Path 1: Normal daemon response (success)
    - Path 2: JSON decode error (fallback)
    - Path 3: Buffer overflow error (fail-open)
    - Path 4: Exception (fail-open)

    TWO OUTPUT PATHWAYS selected by single flag check:
    - Pathway A (Bug #4669 Workaround): JSON + stderr + exit 2
    - Pathway B (Standard): JSON + exit 0

    Args:
        response: Response dict OR raw string (for fallback cases)
        source: Source ("daemon", "daemon-raw", "buffer-error", "exception")

    Exits:
        0: Pathway B (standard - allow OR Gemini deny)
        2: Pathway A (workaround - Claude Code deny)

    Reference: notes/hooks_api_reference.md lines 395-427
    """
    from .config import should_use_exit2_workaround

    # ═══════════════════════════════════════════════════════════════
    # SHARED: Handle raw string fallback (JSON decode error)
    # ═══════════════════════════════════════════════════════════════
    if isinstance(response, str):
        logger.debug(f"Outputting raw response from {source}")
        print(response)
        sys.exit(0)
        return

    # ═══════════════════════════════════════════════════════════════
    # SHARED: Extract decision (DRY - works for Claude and Gemini)
    # ═══════════════════════════════════════════════════════════════
    decision = response.get('hookSpecificOutput', {}).get('permissionDecision',
                                                          response.get('decision', 'allow'))

    logger.info(f"Hook response: source={source}, decision={decision}")

    # ═══════════════════════════════════════════════════════════════
    # SHARED: Always print JSON to stdout first
    # ═══════════════════════════════════════════════════════════════
    print(json.dumps(response))

    # ═══════════════════════════════════════════════════════════════
    # SINGLE FLAG CHECK: Select pathway
    # ═══════════════════════════════════════════════════════════════
    if decision == "deny" and should_use_exit2_workaround():
        # ╔═══════════════════════════════════════════════════════════╗
        # ║ PATHWAY A: Bug #4669 Workaround (Claude Code)           ║
        # ║ - Print reason to stderr (AI sees this)                 ║
        # ║ - Exit code 2 (ONLY way blocking works in Claude Code)  ║
        # ╚═══════════════════════════════════════════════════════════╝
        reason = response.get('hookSpecificOutput', {}).get('permissionDecisionReason',
                                                            response.get('reason', 'Tool blocked'))

        logger.info("Applying exit-2 workaround (Claude Code bug #4669)")
        print(reason, file=sys.stderr)
        sys.exit(2)
    else:
        # ╔═══════════════════════════════════════════════════════════╗
        # ║ PATHWAY B: Standard Behavior                             ║
        # ║ - Gemini respects JSON decision field                    ║
        # ║ - Allow decisions in Claude Code                         ║
        # ║ - Exit code 0 (normal success)                           ║
        # ╚═══════════════════════════════════════════════════════════╝
        sys.exit(0)
```

**Properties**:
- **DRY**: Decision extraction and JSON printing shared
- **Clear pathways**: Explicit if/else for two pathways
- **Single flag**: `should_use_exit2_workaround()` called once
- **Pythonic**: Simple conditionals, clear comments

### 4. Replace ALL Output Paths in client.py

**File**: `plugins/clautorun/src/clautorun/client.py`

**Path 1 & 2: Normal response + JSON decode error**
**Lines**: 143-149

**BEFORE**:
```python
            # Parse response to strip internal markers
            try:
                resp_json = json.loads(resp_text)
                # Remove internal marker (not part of hook response)
                resp_json.pop("_exit_code_2", None)
                # Log decision for diagnostics (file-only, never stdout/stderr)
                decision = resp_json.get('hookSpecificOutput', {}).get('permissionDecision', resp_json.get('decision', 'allow'))
                logger.info(f"Hook response: decision={decision}")
                # Re-serialize without the internal marker
                print(json.dumps(resp_json))
            except json.JSONDecodeError:
                # Not valid JSON, just print as-is
                print(resp_text)
```

**AFTER**:
```python
            # Parse response and route through unified output handler
            try:
                resp_json = json.loads(resp_text)
                output_hook_response(resp_json, source="daemon")
            except json.JSONDecodeError:
                # Not valid JSON, output as-is
                output_hook_response(resp_text, source="daemon-raw")
```

**Path 3: Buffer overflow error**
**Lines**: 154-164

**BEFORE**:
```python
        except asyncio.LimitOverrunError as e:
            # Response from daemon exceeded buffer (shouldn't happen - response is tiny)
            logger.error(f"Client buffer error: {e}")
            print(json.dumps({
                "continue": True,
                "stopReason": "",
                "suppressOutput": False,
                "systemMessage": f"Client buffer error: Daemon response too large. This is a bug. {e}"
            }))
```

**AFTER**:
```python
        except asyncio.LimitOverrunError as e:
            # Response from daemon exceeded buffer (shouldn't happen - response is tiny)
            logger.error(f"Client buffer error: {e}")
            output_hook_response({
                "continue": True,
                "stopReason": "",
                "suppressOutput": False,
                "systemMessage": f"Client buffer error: Daemon response too large. {e}",
                "decision": "allow",
                "hookSpecificOutput": {"permissionDecision": "allow"}
            }, source="buffer-error")
```

**Path 4: Exception fail-open**
**Lines**: 205-213

**BEFORE**:
```python
    except Exception as e:
        # Fail open
        logger.error(f"Client exception (fail-open): {e}", exc_info=True)
        print(json.dumps({
            "continue": True,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": ""
        }))
```

**AFTER**:
```python
    except Exception as e:
        # Fail open
        logger.error(f"Client exception (fail-open): {e}", exc_info=True)
        output_hook_response({
            "continue": True,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": "",
            "decision": "allow",
            "hookSpecificOutput": {"permissionDecision": "allow"}
        }, source="exception")
```

**Result**: All 4 paths now route through single `output_hook_response()` function (DRY)

### 5. Unified Response in core.py (No Markers)

**File**: `plugins/clautorun/src/clautorun/core.py`
**Lines**: 587-595 (default response section)

**BEFORE** (old version with markers):
```python
        # Default hook response
        return {
            "continue": decision != "deny",
            "stopReason": reason if decision == "deny" else "",
            "suppressOutput": False,
            "systemMessage": reason if decision != "deny" else ""
        }
```

**AFTER** (lines 587-595):
```python
        # Default hook response (unified format for both Claude and Gemini)
        return {
            "continue": True,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": "",
            "decision": decision,
            "reason": reason_escaped
        }
```

**Key changes**:
- Added `decision` field (Gemini format)
- Added `reason` field (Gemini format)
- Always `continue: True` (let pathway selection happen in client.py)
- Removed conditional logic (DRY - client.py decides pathway)

```python
def _format_response(self, decision: str, reason: str) -> dict:
    """Build unified hook response for both Claude Code and Gemini CLI.

    Returns response with BOTH formats (no markers, no CLI-specific logic):
    - Claude Code: hookSpecificOutput.permissionDecision
    - Gemini CLI: decision (top-level)

    Exit code logic handled by client.py output_hook_response().

    Args:
        decision: "allow", "deny", "ask", "block"
        reason: Human-readable reason

    Returns:
        dict: Unified response (clean, no internal markers)
    """
    reason_escaped = reason.replace('"', '\\"').replace('\n', '\\n')

    # PreToolUse/BeforeTool: Tool blocking while AI continues
    if self._event == "PreToolUse":
        return {
            # === Universal fields ===
            "continue": True,  # Let AI continue (don't stop entirely)
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": reason_escaped,

            # === Gemini CLI format (top-level) ===
            "decision": decision,
            "reason": reason_escaped,

            # === Claude Code format (nested) ===
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": reason_escaped
            }
        }

    # Stop/SubagentStop with "block" decision
    if decision == "block":
        return {
            "continue": False,
            "stopReason": reason,
            "suppressOutput": False,
            "systemMessage": reason,
            "decision": "block",
            "reason": reason
        }

    # Default response (allow)
    return {
        "continue": True,
        "stopReason": "",
        "suppressOutput": False,
        "systemMessage": "",
        "decision": decision,
        "reason": reason
    }
```

### 6. CLI Override Support

**File**: `plugins/clautorun/src/clautorun/__main__.py`

**Part A: Add argument definition**
**Lines**: 212-219 (in create_parser function)

**BEFORE** (nothing, adding new argument):
```python
    install_group.add_argument(
        "--enable-bootstrap",
        action="store_true",
        help="Re-enable automatic bootstrap (removes --no-bootstrap from hooks.json commands)",
    )
    install_group.add_argument(
        "--claude",
```

**AFTER** (lines 212-219):
```python
    install_group.add_argument(
        "--enable-bootstrap",
        action="store_true",
        help="Re-enable automatic bootstrap (removes --no-bootstrap from hooks.json commands)",
    )
    install_group.add_argument(
        "--exit2-mode",
        choices=['auto', 'always', 'never'],
        default=None,
        help="Bug #4669 workaround mode: 'auto' (detect CLI - default), 'always' (force exit-2), 'never' (disable). "
             "Controls whether deny decisions use exit code 2 + stderr (Claude Code) or JSON decision field (Gemini CLI). "
             "Can also be set via CLAUTORUN_EXIT2_WORKAROUND environment variable.",
    )
    install_group.add_argument(
        "--claude",
```

**Part B: Set environment variable from argument**
**Lines**: 632-635 (in main function)

**BEFORE** (nothing, adding new code):
```python
        return 0

    # Bootstrap config
    if args.no_bootstrap:
```

**AFTER** (lines 632-635):
```python
        return 0

    # Bug #4669 workaround configuration (set env var from CLI arg)
    if hasattr(args, 'exit2_mode') and args.exit2_mode is not None:
        import os
        os.environ['CLAUTORUN_EXIT2_WORKAROUND'] = args.exit2_mode

    # Bootstrap config
    if args.no_bootstrap:
```

**Purpose**: Allows CLI override of auto-detection mode for testing

### 7. Documentation

**File**: `plugins/clautorun/CLAUDE.md`
**Lines**: 108-146 (new section after "Hook Error Prevention")

**BEFORE** (nothing, adding new section):
```markdown
**Diagnosis**: Run `uv run --project <plugin_root> python -c "pass" 2>&1` — any output beyond "Building/Installed" lines is a problem.

## Dynamic Content in Slash Commands
```

**AFTER** (lines 108-146):
```markdown
**Diagnosis**: Run `uv run --project <plugin_root> python -c "pass" 2>&1` — any output beyond "Building/Installed" lines is a problem.

## Bug #4669 Workaround (Claude Code v1.0.62+)

**Problem**: Claude Code ignores `permissionDecision:"deny"` at exit 0. Tool executes anyway despite JSON deny decision.

**Solution**: Auto-detect CLI and apply exit-2 workaround for Claude Code only. Gemini CLI respects JSON decision field correctly.

**Behavior**:
- **Claude Code**: Uses exit 2 + stderr (only way blocking works due to bug #4669)
- **Gemini CLI**: Uses JSON `decision` field (works correctly per spec)
- **Auto-detect**: Based on `GEMINI_SESSION_ID` and `GEMINI_PROJECT_DIR` environment variables

**Configuration**:

Environment variable (set before running Claude Code/Gemini):
```bash
# Auto-detect (default - recommended)
export CLAUTORUN_EXIT2_WORKAROUND=auto

# Force enable for testing
export CLAUTORUN_EXIT2_WORKAROUND=always

# Disable for testing/future
export CLAUTORUN_EXIT2_WORKAROUND=never
```

CLI argument (applies to current execution):
```bash
clautorun --exit2-mode auto    # Default - auto-detect CLI
clautorun --exit2-mode always  # Force exit-2 for all CLIs
clautorun --exit2-mode never   # Disable workaround for all CLIs
```

**Technical Details**:
- Detection: `plugins/clautorun/src/clautorun/config.py:detect_cli_type()`
- Unified output: `plugins/clautorun/src/clautorun/client.py:output_hook_response()`
- Response format: Both `decision` (Gemini) and `hookSpecificOutput.permissionDecision` (Claude) fields included
- Exit codes: 0 for allow/Gemini-deny, 2 for Claude-deny (stderr contains reason)

**Reference**: `notes/hooks_api_reference.md` lines 326-440 (workaround details), lines 1187-1221 (outcome matrices)

## Dynamic Content in Slash Commands
```

**Purpose**: Document the two-pathway system and configuration options

## Critical Files to Modify

1. **plugins/clautorun/hooks/hook_entry.py** (CRITICAL - blocks workaround if not fixed)
   - Fix `try_cli()` to accept exit 0 OR exit 2
   - Pass through stderr to Claude Code
   - Pass through exit code (NOT hardcoded 0)
   - **Net change**: ~10 lines modified

2. **plugins/clautorun/src/clautorun/config.py**
   - Add `detect_cli_type()` - 15 lines
   - Add `should_use_exit2_workaround()` - 12 lines

3. **plugins/clautorun/src/clautorun/client.py**
   - Add `output_hook_response()` - 40 lines (DRY consolidation)
   - Replace 4 output paths with calls to `output_hook_response()`
   - **Net change**: +40 lines, simplify 30 lines

4. **plugins/clautorun/src/clautorun/core.py**
   - Replace `_format_response()` - simpler (no markers)
   - **Net change**: -5 lines (remove marker logic)

5. **plugins/clautorun/src/clautorun/__main__.py**
   - Add `--exit2-mode` argument - 5 lines
   - Set env var from args - 2 lines

6. **plugins/clautorun/CLAUDE.md**
   - Add documentation section - 30 lines

## Complete Round Trip (End-to-End Workaround)

### Deny Decision in Claude Code (Bug #4669 Workaround)

```
Claude Code detects `rm /tmp/test.txt`
         ↓
hook_entry.py: Calls try_cli() → forwards to client.py
         ↓
client.py: Calls daemon via Unix socket
         ↓
daemon: Detects "rm" pattern, returns deny decision
         ↓
client.py: output_hook_response()
         - Detects CLI type: "claude"
         - Decision: "deny"
         - Workaround needed: True
         - Prints JSON to stdout
         - Prints reason to stderr
         - sys.exit(2)  ← EXIT CODE 2
         ↓
hook_entry.py: try_cli() receives result
         - result.returncode = 2  ✅ ACCEPTED (was rejected before fix)
         - Prints result.stdout (JSON)
         - Prints result.stderr (reason)
         - sys.exit(2)  ← PASSES THROUGH EXIT CODE 2
         ↓
Claude Code receives:
         - Exit code: 2
         - stdout: JSON with permissionDecision="deny"
         - stderr: "Use trash instead of rm..."
         ↓
Claude Code behavior:
         - Exit 2 triggers ACTUAL BLOCKING (only way that works)
         - stderr fed back to AI as suggestion
         - NO "hook error" (exit 2 is valid)
         - Tool BLOCKED ✅
```

### Allow Decision (Normal Path)

```
Claude Code detects allowed command
         ↓
hook_entry.py → client.py → daemon
         ↓
daemon: Returns allow decision
         ↓
client.py: output_hook_response()
         - Decision: "allow"
         - Prints JSON to stdout
         - sys.exit(0)  ← EXIT CODE 0
         ↓
hook_entry.py:
         - result.returncode = 0  ✅ ACCEPTED
         - Prints result.stdout (JSON)
         - sys.exit(0)  ← PASSES THROUGH EXIT CODE 0
         ↓
Claude Code:
         - Exit 0: Normal success
         - Tool ALLOWED ✅
```

**KEY FIX**: hook_entry.py now accepts BOTH exit 0 and exit 2, passes through to Claude Code.

**Before Fix**: hook_entry.py rejected exit 2 → returned False → fail_open() → exit 0 → "hook error" → tool executed

**After Fix**: hook_entry.py passes through exit 2 → Claude Code receives exit 2 → tool blocked ✅

## Complete Code Pathways (ALL CONSOLIDATED)

### Normal Path (Daemon Success)
```
daemon → client.py:forward() → resp_json
                              ↓
               output_hook_response(resp_json, "daemon")
                              ↓
               detect CLI → check decision → exit code
```

### Error Path 1 (JSON Decode Error)
```
daemon → client.py:forward() → JSONDecodeError
                              ↓
               output_hook_response(resp_text, "daemon-raw")
                              ↓
               print raw → exit(0)
```

### Error Path 2 (Buffer Overflow)
```
daemon → client.py:forward() → LimitOverrunError
                              ↓
               output_hook_response({...fail-open...}, "buffer-error")
                              ↓
               print JSON → exit(0)
```

### Error Path 3 (Exception)
```
client.py:forward() → Exception
                    ↓
     output_hook_response({...fail-open...}, "exception")
                    ↓
     print JSON → exit(0)
```

**ALL 4 PATHS** converge to `output_hook_response()` → **DRY + WOLOG**

## Testing Strategy

### Test 1: Verify Consolidation (All Paths Use Same Function)

```python
# Unit test
def test_all_paths_use_output_hook_response():
    """Verify all 4 output paths call output_hook_response."""
    import ast
    import inspect
    from plugins.clautorun.src.clautorun import client

    source = inspect.getsource(client.run_client)
    tree = ast.parse(source)

    # Find all print() calls
    prints = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Name) and node.func.id == 'print']

    # Should be ZERO direct print() calls (all go through output_hook_response)
    assert len(prints) == 0, f"Found {len(prints)} direct print() calls - all should use output_hook_response()"
```

### Test 2: Auto-Detection Works

```bash
# Test Claude detection
unset GEMINI_SESSION_ID
unset GEMINI_PROJECT_DIR
python3 -c "from plugins.clautorun.src.clautorun.config import detect_cli_type, should_use_exit2_workaround; print(f'CLI: {detect_cli_type()}, Workaround: {should_use_exit2_workaround()}')"
# Expected: CLI: claude, Workaround: True

# Test Gemini detection
export GEMINI_SESSION_ID="test-123"
python3 -c "from plugins.clautorun.src.clautorun.config import detect_cli_type, should_use_exit2_workaround; print(f'CLI: {detect_cli_type()}, Workaround: {should_use_exit2_workaround()}')"
# Expected: CLI: gemini, Workaround: False
```

### Test 3: hook_entry.py Passes Through Exit Code 2 (CRITICAL)

```bash
# Enable auto mode (default)
export CLAUTORUN_EXIT2_WORKAROUND=auto
clautorun --install clautorun --force

# Test rm blocking - this is the FULL round trip test
echo "test" > /tmp/test-exit2.txt
rm /tmp/test-exit2.txt

# EXPECTED BEHAVIOR:
# 1. Tool BLOCKED (file still exists)
# 2. Trash suggestion shown to AI
# 3. NO "hook error" message
# 4. Exit code 2 passed through all layers

# Verify file still exists (tool was actually blocked)
ls -la /tmp/test-exit2.txt
# Expected: File exists (rm was blocked)

# Verify debug log shows exit 2 at ALL layers
grep "CLI exit code: 2" ~/.clautorun/hook_entry_debug.log
grep "CLI stderr.*trash" ~/.clautorun/hook_entry_debug.log
# Expected: Both found (client.py returned exit 2, hook_entry.py logged it)

# Verify hook_entry.py passed through exit 2 (NOT converted to 0)
# If converted to 0: "hook error" + tool executes (WRONG)
# If passed through 2: tool blocked, no "hook error" (CORRECT)
```

### Test 4: Exit Code Logic (Gemini Mock)

```bash
# Mock Gemini environment
export GEMINI_SESSION_ID="test-session"
export CLAUTORUN_EXIT2_WORKAROUND=auto

# Test response
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"rm test.txt"}}' | \
  python3 ~/.claude/clautorun/plugins/clautorun/src/clautorun/client.py 2>/tmp/gemini_stderr.txt
# Expected: exit 0, stderr 0 bytes, JSON has decision="deny"

wc -c /tmp/gemini_stderr.txt
# Expected: 0 bytes (no stderr for Gemini)
```

### Test 5: Response Format (Unified)

```bash
# Verify response has BOTH formats
export CLAUTORUN_DEBUG=1
rm /tmp/test.txt

# Check for both Claude and Gemini fields
grep '"decision":' ~/.clautorun/hook_entry_debug.log
grep '"hookSpecificOutput":' ~/.clautorun/hook_entry_debug.log
grep '"permissionDecision":' ~/.clautorun/hook_entry_debug.log
# Expected: All three found
```

## Success Criteria

1. ✅ **hook_entry.py passes through exit codes**: Accepts exit 0 AND exit 2, passes through to Claude Code
2. ✅ **DRY**: Single `output_hook_response()` handles ALL 4 paths
3. ✅ **WOLOG**: Works for normal, error, and fallback cases without loss of generality
4. ✅ **Auto-detection**: Correctly identifies Claude vs Gemini
5. ✅ **Claude Code**: exit 2 + stderr for deny (workaround)
6. ✅ **Gemini CLI**: exit 0 + JSON for deny (standard)
7. ✅ **Unified response**: Both `decision` and `hookSpecificOutput` fields
8. ✅ **No markers**: Clean JSON, no internal fields to pop
9. ✅ **Testable**: Can unit test `output_hook_response()` in isolation
10. ✅ **Configurable**: "auto"/"always"/"never" modes work
11. ✅ **Complete round trip**: Claude Code → hook_entry.py → client.py → daemon → client.py → hook_entry.py → Claude Code (exit 2 preserved throughout)
12. ✅ **Actual blocking**: `rm` test shows file still exists, NO "hook error" message

## Architecture Benefits

### Before (NOT DRY)
```
Path 1: daemon success    → print(json.dumps(resp_json))      → exit(0)
Path 2: JSON decode error → print(resp_text)                  → exit(0)
Path 3: buffer error      → print(json.dumps({...}))          → exit(0)
Path 4: exception         → print(json.dumps({...}))          → exit(0)
```
**Issues**: 4 separate print statements, no exit-2 logic, not DRY

### After (DRY + WOLOG)
```
Path 1: daemon success    ↘
Path 2: JSON decode error  ↘
Path 3: buffer error        → output_hook_response(...) → auto-detect → exit(0 or 2)
Path 4: exception          ↗
```
**Benefits**: Single function, DRY, auto-detect, workaround, testable

## References

- **Bug #4669**: https://github.com/anthropics/claude-code/issues/4669
- **CLI Detection**: `notes/hooks_api_reference.md` lines 249-272
- **Unified Blocking**: `notes/hooks_api_reference.md` lines 395-427
- **Claude Outcome Matrix**: `notes/hooks_api_reference.md` lines 1187-1209
- **Gemini Outcome Matrix**: `notes/hooks_api_reference.md` lines 1209-1221
