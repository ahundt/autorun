# Plan: Unified Gemini CLI and Claude Code Integration for Autorun

This plan outlines the technical changes required to ensure `autorun` provides a seamless, high-performance experience across both Gemini CLI and Claude Code, following the WOLOG (Work Out Loud, On Git) principle with maximal code reuse.

## 1. Objectives
- **Fix TOML Command Generation**: Ensure `no.toml` and `globalno.toml` are parsed correctly by Gemini CLI by properly escaping backslashes in regex patterns.
- **Strict Hook Schema Enforcement**: Eliminate hook failures in Gemini CLI (`SessionStart`, `AfterTool`) by strictly filtering JSON responses to match Gemini-specific expectations.
- **Unified Task Lifecycle**: Resolve the "Zombie State" in Gemini CLI by gracefully bypassing Claude-specific task tracking in favor of the Conductor extension, while maintaining full safety guards for both platforms.
- **Interoperability**: Ensure a single codebase supports both CLIs without duplication, using `detect_cli_type()` for environment-aware behavior.

## 2. Proposed Changes

### A. Fix TOML Command Generation (`plugins/autorun/src/autorun/install.py`)
Gemini CLI fails to parse TOML files if backslashes in the prompt are not escaped. We must escape backslashes and triple quotes in the multi-line prompt string.

**Proposed Code Change:**
```python
<<<<
            # Escape triple quotes in body if present
            safe_body = body.replace(x22x22x22, '\\"\\"\\"')
====
            # Escape backslashes and triple quotes in body for TOML multi-line strings
            # Gemini CLI parser fails on unescaped backslashes in regex (e.g. \()
            safe_body = body.replace('\\', '\\\\').replace('"""', '\\"\\"\\"')
>>>>
```

### B. Strict Hook Schema Validation (`plugins/autorun/src/autorun/core.py`)
Gemini CLI is stricter about extra JSON fields in lifecycle events than previously assumed. We must filter responses based on the event type for Gemini, just as we do for Claude.

**Proposed Code Change:**
Update `validate_hook_response` to implement a per-event whitelist for Gemini.

```python
<<<<
        allowed_gemini = {
            "continue", "decision", "reason", "systemMessage", "stopReason",
            "hookSpecificOutput", "permissionDecision", "suppressOutput"
        }
        return {k: v for k, v in response.items() if k in allowed_gemini}
====
        # Gemini-specific event schema validation
        # Lifecycle events (SessionStart, AfterTool/PostToolUse, etc.) MUST NOT have 'decision'
        gemini_lifecycle_events = {"SessionStart", "SessionEnd", "AfterTool", "AfterAgent", "PostToolUse", "Stop"}
        
        allowed_base = {"continue", "systemMessage", "stopReason", "suppressOutput"}
        if event in gemini_lifecycle_events:
            return {k: v for k, v in response.items() if k in allowed_base}
            
        allowed_tool = allowed_base | {"decision", "reason", "hookSpecificOutput", "permissionDecision"}
        return {k: v for k, v in response.items() if k in allowed_tool}
>>>>
```

### C. Graceful Task Tracking Bypass for Gemini (`plugins/autorun/src/autorun/plugins.py`)
The `check_task_staleness` and `enforce_task_staleness` hooks rely on Claude-specific MCP tools (`TaskCreate`, `TaskUpdate`). These do not exist in Gemini CLI, which uses the Conductor extension (markdown-based tracking).

**Proposed Code Change:**
Insert a CLI-type check at the entry of task enforcement.

```python
<<<<
def check_task_staleness(ctx: EventContext) -> Optional[Dict]:
    """Inject reminder when AI hasn't updated tasks recently (v0.9).
====
def check_task_staleness(ctx: EventContext) -> Optional[Dict]:
    """Inject reminder when AI hasn't updated tasks recently (v0.9).
    
    Bypass for Gemini CLI: Gemini uses Conductor (markdown-based) rather than
    native tool calls for task tracking.
    """
    if ctx.cli_type == "gemini":
        return None
>>>>
```

## 3. Implementation Strategy
1. **Apply `install.py` fix**: Fix the generator first to ensure subsequent installs produce valid TOML.
2. **Apply `core.py` and `plugins.py` fixes**: Fix the runtime logic to prevent hook errors and task blocking.
3. **Redeploy**:
   - Run `autorun --restart-daemon` to load fresh logic.
   - Run `autorun --install --force` to regenerate TOML commands.
4. **Validation**:
   - Start a new Gemini session; verify no `SessionStart` error.
   - Run a tool (e.g., `ls`); verify no `AfterTool` error and no task blockage.
   - Verify Claude Code still functions correctly with task tracking.

## 4. Why this is WOLOG and Interoperable
- **Maximal Reuse**: The `validate_hook_response` function remains the single point of truth for both CLIs, simply gaining a Gemini-specific branch that mirrors the Claude logic's rigor.
- **Zero Configuration**: No user action is required to switch modes; `detect_cli_type()` handles the differentiation automatically.
- **Future Proof**: By aligning Gemini hooks with their strict schema, we prevent regressions as the Gemini CLI SDK matures.
- **Platform Native**: We respect Gemini's ecosystem (Conductor) instead of forcing a Claude-centric tool workflow where it isn't supported.
# Plan: Task Interoperability Support for Gemini CLI via Conductor

This plan extension describes how autorun will explicitly support the Conductor-based task lifecycle in Gemini CLI, ensuring that task status is visible and maintainable across platforms.

## 1. Conductor Integration Goals
- **Unified Status**: The `/ar:status` and `/ar:task-status` commands should reflect Conductor tracks when running in Gemini CLI.
- **Plan Tracking**: Leverage Conductor's `conductor/tracks/<track_id>/plan.md` as the source of truth for tasks in Gemini, matching Claude Code's internal task DB behavior.
- **WOLOG Verification**: Ensure that when a user approves a Conductor plan, autorun recognizes it as an active "Feature Phase" or "Execution Phase".

## 2. Technical Enhancements

### A. Conductor Awareness in `task_lifecycle.py`
Modify the `TaskLifecycle` class to detect and parse Conductor plans when in Gemini CLI.

**Proposed Logic:**
1.  Check if `conductor/tracks/` exists.
2.  Find the "active" track (referenced in `conductor/index.md` or the latest directory).
3.  Parse the Markdown task list (`- [ ] task`) from `plan.md`.
4.  Map these to internal `Task` objects for consistent display in `/ar:task-status`.

### B. Task Completion Sync
When Conductor marks a task as complete in `plan.md`, `autorun` should detect the file change and update the session state accordingly.

## 3. Benefits
- **Platform Native**: Gemini users get the benefit of Conductor's repository-persistent plans while still receiving autorun's safety and verification guards.
- **Cross-CLI Visibility**: A developer can start a feature in Claude Code (using `TaskCreate`), then switch to Gemini CLI (using `/conductor:implement`), and autorun will bridge the visibility gap.
# Unified Gemini CLI and Claude Code Integration Plan: Final Summary

- **TOML Generation Fix**: Ensure regex patterns like `regex:eval\(` in `no.md` are escaped as `regex:eval\\(` in the generated `.toml` files.
- **Strict Schema Enforcement**: Filter JSON responses per Gemini hook type to prevent schema validation errors.
- **Task Interoperability**: Bypass Claude-specific `TaskCreate` enforcement in Gemini, replacing it with a first-class Conductor plan parser that reflects active tracks in `/ar:task-status`.

### D. Command Adjustment for `/ar:tasks` (`plugins/autorun/src/autorun/plugins.py`)

In Gemini CLI, `/ar:tasks` should reflect the task status derived from Conductor plans. If `TaskLifecycle` is enhanced to parse Conductor files, the existing `/ar:tasks` logic should naturally report those tasks.

**Proposed Logic Enhancement:**
- Update `/ar:tasks` status output to clarify that staleness enforcement is handled by the Conductor plan for Gemini CLI.
- Ensure that if no Conductor track is active, the command provides a helpful suggestion (e.g., "Use `/conductor:newTrack` to start tracking tasks").
