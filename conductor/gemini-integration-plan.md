# Objective
Fix autorun integration issues with Gemini CLI, specifically TOML parsing errors for commands, hook failures (`SessionStart`, `AfterTool`), and inappropriate task staleness blocking.

# Background & Motivation
When using autorun with Gemini CLI, three main issues occur:
1. `Failed to parse TOML file` for `no.toml` and `globalno.toml`: The `_generate_gemini_toml_commands` function in `install.py` fails to escape backslashes when creating TOML multi-line strings, causing TOML parsing errors on markdown containing `\(` or similar regex escapes.
2. `Hook(s) [autorun-init] failed for event SessionStart` and `AfterTool`: `validate_hook_response` in `core.py` permits fields like `decision` or `hookSpecificOutput` for Gemini's `SessionStart` and `AfterTool` events. Gemini CLI's strict JSON schema validation rejects these extra fields.
3. Task Staleness Tracker Blocking: Autorun enforces a task staleness check that counts tool uses and blocks execution until `TaskCreate` is called. However, Gemini CLI lacks `TaskCreate` and instead uses Conductor for task management, leading to an inescapable "Zombie State" where tools are permanently blocked.

# Proposed Solution

### 1. Fix TOML Parsing for Gemini Commands
**File:** `plugins/autorun/src/autorun/install.py`
*   In `_generate_gemini_toml_commands`, properly escape backslashes before generating the TOML literal.
*   **Change:**
    ```python
    # Convert $ARGUMENTS to {{args}} (Gemini convention)
    body = body.replace("$ARGUMENTS", "{{args}}")

    # Escape backslashes and triple quotes
    safe_body = body.replace('\\', '\\\\').replace('"""', '\\"\\"\\"')

    # Write TOML file
    toml_content = f'description = "{description}"\n'
    toml_content += f'prompt = """\n{safe_body}\n"""\n'
    ```

### 2. Enforce Strict Hook Schema for Gemini CLI
**File:** `plugins/autorun/src/autorun/core.py`
*   Modify `validate_hook_response` to apply event-specific schema filtering for Gemini CLI.
*   Ensure that lifecycle events (`SessionStart`, `SessionEnd`, `AfterAgent`, etc.) do not include `decision`, `reason`, or `hookSpecificOutput`, matching Gemini's strict JSON expectations.

### 3. Disable Task Tracking for Gemini CLI
**File:** `plugins/autorun/src/autorun/plugins.py` (or where `check_task_staleness` and `enforce_task_staleness` are defined)
*   Bypass the task staleness enforcement mechanism when `cli_type == "gemini"`.
*   **Change:** Add a fast return at the beginning of task staleness checks if running in Gemini CLI.

# Implementation Steps
1. Edit `install.py` to fix the backslash escaping in TOML.
2. Edit `core.py` to correctly filter Gemini hook responses strictly per event.
3. Edit `plugins.py` to bypass staleness checks for Gemini CLI since Gemini handles tasks via the Conductor extension.
4. Run `autorun --restart-daemon` to pick up changes.
5. Re-run `autorun --install --force` in Gemini to regenerate `.toml` files.

# Verification & Testing
*   Verify that `no.toml` parses correctly in Gemini CLI.
*   Start a new Gemini session and ensure no `SessionStart` hook error appears.
*   Execute a tool in Gemini and ensure neither `AfterTool` schema errors nor Task Staleness blockers appear.