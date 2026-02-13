# Bugs and Issues Identified - clautorun Hooks System

**Generated:** 2026-02-12
**Purpose:** Document bugs and missed requirements identified during API documentation review, with concrete locations and proposed fixes.

**Constraint:** No code edits - documentation and proposed fixes only.

---

## Table of Contents

1. [Critical Bugs](#critical-bugs)
2. [Medium Priority Issues](#medium-priority-issues)
3. [Low Priority / Enhancements](#low-priority--enhancements)
4. [Documentation Gaps](#documentation-gaps)
5. [Architectural Observations](#architectural-observations)

---

## Critical Bugs

### BUG-1: hooks.json Contains Gemini Format in Source Repository

**Location:** `plugins/clautorun/hooks/hooks.json`

**Evidence:**
```bash
$ head -5 plugins/clautorun/hooks/hooks.json
{
  "description": "clautorun v0.8 - unified daemon-based hook handler",
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "/afs|/afa|/afj|/afst|/autorun|/autostop|/estop|/cr:",
```

**Current Status:** CORRECT - The file now shows Claude Code format (`PreToolUse`, `UserPromptSubmit`)

**Historical Issue (Fixed):**
- Previous commits had `BeforeTool` (Gemini format) instead of `PreToolUse` (Claude format)
- Installer backup/restore logic failed, corrupting source file
- Fixed by: Test in `test_hook_entry.py::test_source_hooks_json_is_claude_format`

**Verification:**
```bash
grep -c "PreToolUse" plugins/clautorun/hooks/hooks.json  # Should be > 0
grep -c "BeforeTool" plugins/clautorun/hooks/hooks.json  # Should be 0
```

---

### BUG-2: gemini-hooks.json Uses `python3` Instead of UV

**Location:** `plugins/clautorun/hooks/gemini-hooks.json:9`

**Current Code:**
```json
"command": "uv run --quiet --project ${extensionPath} python ${extensionPath}/hooks/hook_entry.py"
```

**Status:** CORRECT - The file correctly uses `uv run --quiet --project`

**Historical Issue (Fixed):**
- Previous version used bare `python3` without UV wrapper
- This violates UV workspace best practices
- Fixed in commit that added UV commands to gemini-hooks.json

---

### BUG-3: try_cli() Returns True on Subprocess Failure

**Location:** `plugins/clautorun/hooks/hook_entry.py:189-250`

**Current Code (CORRECT):**
```python
def try_cli(bin_path: Path, stdin_data: str = "") -> bool:
    result = subprocess.run([str(bin_path)], input=stdin_data, ...)

    # Exit code 2: Blocking error
    if result.returncode == 2:
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        if result.stdout:
            print(result.stdout, end="")
        sys.exit(2)

    # Must check return code
    if result.returncode != 0:
        return False

    # Must have stdout output
    if result.stdout:
        print(result.stdout, end="")
        return True

    return False  # No output = failure
```

**Status:** CORRECT - Fixed in commit 4c5fce9

**Historical Bug (Fixed):**
- Old code returned `True` unconditionally even when subprocess failed
- This caused hook_entry.py to exit without printing JSON
- Claude Code saw empty response → fail-open → rm executed

**Reference:** `notes/2026_02_11_lessons_learned_hook_failure_loop_prevention.md` Bug 1

---

### BUG-4: continue=True Hardcoded in Response Builder

**Location:** `plugins/clautorun/src/clautorun/core.py:509-589`

**Current Code (CORRECT):**
```python
def respond(self, decision: str = "allow", reason: str = "") -> dict:
    if self._event == "PreToolUse":
        return {
            "decision": decision,
            "reason": reason_escaped,
            "continue": True,  # Correct: continue=true for tool blocking
            ...
            "_exit_code_2": decision == "deny",
        }
    ...
    return {
        "continue": decision != "deny",
        ...
    }
```

**Status:** CORRECT - Properly handles `continue` based on context

**Key Insight from API Docs:**
- `continue: false` stops the AI entirely (NOT just the tool)
- For tool blocking, use `continue: true` + `decision: "deny"` + exit code 2
- This is CORRECT in the current implementation

**Historical Bug (Fixed):**
- Old code had `continue: True` hardcoded unconditionally
- Fixed in commit de24440

---

## Medium Priority Issues

### ISSUE-1: Stdin Consumed Before Fallback

**Location:** `plugins/clautorun/hooks/hook_entry.py:420-451`

**Current Code (CORRECT):**
```python
def main() -> None:
    import io

    # Read stdin once
    stdin_data = "" if sys.stdin.isatty() else sys.stdin.read()

    clautorun_bin = get_clautorun_bin()

    if clautorun_bin and try_cli(clautorun_bin, stdin_data):
        return

    # Restore stdin for fallback path
    sys.stdin = io.StringIO(stdin_data)

    run_fallback()
```

**Status:** CORRECT - Fixed in commit 4c5fce9

**Historical Bug (Fixed):**
- Old code read stdin inside `try_cli()`, consuming it
- Fallback path `run_fallback()` then got empty stdin
- Fixed by reading once at entry and passing as parameter

---

### ISSUE-2: Multiple Daemon Instances

**Location:** Multiple code locations spawn daemons

**Symptom:** 14+ daemons running simultaneously

**Root Cause:** Each code location can spawn its own daemon:
- Daemon from dev venv
- Daemon from UV tool
- Daemon from Gemini extension venv
- Daemon from Gemini workspace venv

**Proposed Fix (Already Implemented):**
```bash
# Kill all daemons after code changes
pkill -f "clautorun.daemon"
```

**Reference:** `notes/2026_02_11_lessons_learned_hook_failure_loop_prevention.md` Bug 9

---

### ISSUE-3: UV Tool Install --force Cache Bug

**Location:** `plugins/clautorun/src/clautorun/install.py:1418`

**Current Code:**
```python
result = run_cmd(["uv", "tool", "install", ".", "--force"], timeout=120)
```

**Problem:** UV bug #9492 - `--force` doesn't invalidate import cache

**Proposed Fix:**
```python
# For development (creates symlink):
result = run_cmd(["uv", "tool", "install", "--editable", "."], timeout=120)

# For production (invalidates cache):
result = run_cmd(["uv", "tool", "install", "--reinstall", "."], timeout=120)
```

**Reference:** `notes/2026_02_11_lessons_learned_hook_failure_loop_prevention.md` Bug 5

---

### ISSUE-4: Gemini Extensions Install Creates Copies

**Location:** `plugins/clautorun/src/clautorun/install.py:970`

**Current Code:**
```python
result = run_cmd(["gemini", "extensions", "install", str(temp_plugin), "--consent"])
```

**Problem:** Gemini `install` COPIES files instead of symlinking

**Proposed Fix:**
```python
# Use link instead of install for development
result = run_cmd(["gemini", "extensions", "link", str(plugin_dir)])
```

**Reference:** `notes/2026_02_09_2330_clautorun_install_paths_reference.md` "gemini extensions link" section

---

### ISSUE-5: Session Restart Required for Hook Changes

**Location:** Architectural - Claude Code design

**Problem:** Hook configuration cached at session start

**No Code Fix Available** - This is Claude Code's design

**Mitigation:**
1. Document that hook changes require session restart
2. Add warning in `--install` output
3. Provide `/cr:restart-daemon` command for code changes

**Current Mitigation in install.py:**
```python
def _restart_daemon_if_running() -> None:
    """Restart the clautorun daemon if it's currently running."""
    ...
    print("Restarting daemon to pick up changes...")
```

---

## Low Priority / Enhancements

### ENH-1: Missing GEMINI.md Documentation for Hook Requirements

**Location:** `plugins/clautorun/GEMINI.md` (if exists) or needs creation

**Missing Content:**
- Required settings (`enableHooks`, `enableMessageBusIntegration`)
- Hook event naming differences
- Environment variable availability

**Proposed Addition:**
```markdown
## Gemini CLI Hook Requirements

### Required Settings (~/.gemini/settings.json)
{
  "tools": {
    "enableHooks": true,
    "enableMessageBusIntegration": true
  }
}

### Environment Variables
- GEMINI_SESSION_ID: Session identifier
- GEMINI_PROJECT_DIR: Project directory
- ${extensionPath}: Extension directory in hooks.json
```

---

### ENH-2: No Automated Sync Validation

**Location:** Missing test coverage

**Problem:** No automated check that all code locations are synchronized

**Proposed Fix:**
```python
# In test_hook_entry.py
class TestAllLocationsSync:
    def test_cache_matches_source(self):
        """Verify cache has same hook_entry.py as source."""
        source = Path("plugins/clautorun/hooks/hook_entry.py")
        cache = Path.home() / ".claude/plugins/cache/clautorun/clautorun/0.8.0/hooks/hook_entry.py"
        if cache.exists():
            assert source.read_text() == cache.read_text()

    def test_only_one_daemon_running(self):
        """Verify single daemon process."""
        result = subprocess.run(["pgrep", "-f", "clautorun.daemon"], capture_output=True)
        count = len(result.stdout.strip().split('\n')) if result.stdout.strip() else 0
        assert count <= 1, f"Multiple daemons running: {count}"
```

**Status:** Tests added in commit 4c5fce9

---

### ENH-3: Build Artifacts Not Cleaned

**Location:** `plugins/clautorun/build/` and `~/.gemini/extensions/clautorun-workspace/plugins/clautorun/build/`

**Problem:** Build artifacts may be imported instead of source

**Proposed Fix:**
```bash
# Add to .gitignore
build/
*.egg-info/
dist/

# Add cleanup to install.py
def _cleanup_build_artifacts(plugin_dir: Path) -> None:
    build_dir = plugin_dir / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir)
```

---

## Documentation Gaps

### DOC-1: Missing Troubleshooting Guide for Hook Issues

**Proposed Location:** `plugins/clautorun/TROUBLESHOOTING.md` or section in `CLAUDE.md`

**Missing Content:**
- How to diagnose hook not firing
- How to verify hook configuration
- Common error messages and solutions
- Session restart requirement

**Partially Addressed In:**
- `notes/2026_02_10_1948_gemini_hooks_integration_complete_notes.md` Section: "Troubleshooting Guide"
- `notes/2026_02_11_lessons_learned_hook_failure_loop_prevention.md` Section: "Quick Reference"

---

### DOC-2: No Feature Matrix for Installation Pathways

**Missing:** Comprehensive matrix showing which features work in which installation pathway

**Reference Exists:** `notes/2026_02_09_2300_plan_robust_installation_pathways_comprehensive.md`

**Proposed Format:**
| Feature | GitHub Plugin | Local Clone | Pip Install | UV Tool | Gemini Extension |
|---------|---------------|-------------|-------------|---------|------------------|
| Hooks work | ✅ | ✅ | ❌ | ✅ | ✅ |
| Commands work | ✅ | ✅ | ❌ | ✅ | ✅ |
| Self-update | ✅ | ⚠️ | ❌ | ⚠️ | ✅ |
| Resources included | ✅ | ✅ | ❌ | ✅ | ✅ |

---

## Architectural Observations

### ARCH-1: 9 Code Locations Cause Synchronization Issues

**Locations Identified:**
1. Dev repo source: `~/.claude/clautorun/plugins/clautorun/src/clautorun/`
2. Dev venv: `~/.claude/clautorun/plugins/clautorun/.venv/.../clautorun/`
3. Dev build artifacts: `~/.claude/clautorun/plugins/clautorun/build/lib/clautorun/`
4. Claude cache: `~/.claude/plugins/cache/clautorun/clautorun/0.8.0/`
5. UV global tool: `~/.local/share/uv/tools/clautorun/.../clautorun/`
6. Gemini extension source: `~/.gemini/extensions/clautorun-workspace/plugins/clautorun/src/clautorun/`
7. Gemini plugin venv: `~/.gemini/extensions/clautorun-workspace/plugins/clautorun/.venv/.../clautorun/`
8. Gemini workspace venv: `~/.gemini/extensions/clautorun-workspace/.venv/.../clautorun/`
9. Gemini build artifacts: `~/.gemini/extensions/clautorun-workspace/plugins/clautorun/build/`

**Proposed Solution (Symlink Architecture):**
- 9 locations → 3 real + 2 caches = 5 total
- Use `uv tool install --editable .` for UV tool
- Use `gemini extensions link` for Gemini
- Delete `build/` directories

**Reference:** `notes/2026_02_11_lessons_learned_hook_failure_loop_prevention.md` "Migration Checklist"

---

### ARCH-2: Fail-Open Design Can Mask Errors

**Location:** `plugins/clautorun/hooks/hook_entry.py:137-153`

**Current Code:**
```python
def fail_open(message: str = "") -> NoReturn:
    response = {
        "continue": True,
        ...
    }
    print(json.dumps(response))
    sys.exit(0)
```

**Observation:** While correct for never crashing Claude, this can mask real errors

**Mitigation:** Include clear error messages in `systemMessage` field

---

### ARCH-3: Daemon-Based Architecture Adds Complexity

**Benefits:**
- Fast response time (1-5ms vs 50-150ms process startup)
- Persistent state across hooks
- Shared ThreadSafeDB cache

**Drawbacks:**
- Multiple daemons can run simultaneously
- Code changes require daemon restart
- Session-based lifecycle can cause issues

**Current Mitigation:**
- `_restart_daemon_if_running()` in install.py
- `/cr:restart-daemon` command available
- Watchdog cleans up dead PIDs

---

## Summary

### Critical Bugs: 0 Open (All Fixed)
- BUG-1: hooks.json format - FIXED
- BUG-2: gemini-hooks.json python3 - FIXED
- BUG-3: try_cli() return handling - FIXED
- BUG-4: continue field handling - FIXED

### Medium Priority Issues: 5
- ISSUE-1: Stdin consumption - FIXED
- ISSUE-2: Multiple daemons - Mitigation exists
- ISSUE-3: UV --force cache bug - Needs `--reinstall`
- ISSUE-4: Gemini copies vs links - Needs `gemini extensions link`
- ISSUE-5: Session restart required - Architectural, documented

### Low Priority Enhancements: 3
- ENH-1: GEMINI.md documentation
- ENH-2: Sync validation tests - PARTIALLY DONE
- ENH-3: Build artifact cleanup

### Documentation Gaps: 2
- DOC-1: Troubleshooting guide
- DOC-2: Feature matrix

### Architectural Observations: 3
- ARCH-1: 9 code locations → symlink architecture
- ARCH-2: Fail-open masking
- ARCH-3: Daemon complexity

---

## Action Items

1. **Immediate:** None (critical bugs fixed)
2. **Short-term:**
   - Change `uv tool install --force` to `--reinstall`
   - Add GEMINI.md hook requirements section
   - Document session restart requirement prominently
3. **Long-term:**
   - Migrate to symlink architecture
   - Add comprehensive troubleshooting guide
   - Implement automated sync validation in CI

---

**Document Version:** 1.0
**Status:** Complete
**Based On:** Review of hook_entry.py, core.py, install.py, hooks.json, gemini-hooks.json, and notes from Feb 9-12, 2026
