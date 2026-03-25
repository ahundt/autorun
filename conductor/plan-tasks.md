# Plan: Unified Task Lifecycle for Gemini CLI and Claude Code

This plan ensures `autorun` provides seamless task tracking across both CLIs by normalizing tool names, input parameters, and bulk operations.

## 1. Objectives
- **Tool Normalization**: Support Gemini native task tools ("Create Task", "Update Task", "List Tasks") and bulk operations (`WriteTodos`).
- **Input Normalization**: Support both Claude-style (`subject`) and Gemini-style (`title`) task descriptions.
- **Bulk Task Support**: Natively support Gemini Planner bulk updates (`WriteTodos`) to ensure the staleness counter resets correctly.
- **TDD-Driven**: Add failing tests for Gemini-specific task operations before implementing fixes.

## 2. Proposed Changes

### A. Expand Tool Identification Sets (`plugins/autorun/src/autorun/config.py`)
Update sets to include Gemini-native tool names.

**Code Change:**
```python
TASK_CREATE_TOOLS = {"TaskCreate", "task_create", "Create Task"}
TASK_UPDATE_TOOLS = {"TaskUpdate", "task_update", "Update Task"}
TASK_LIST_TOOLS = {"TaskList", "task_list", "List Tasks"}
TASK_GET_TOOLS = {"TaskGet", "task_get", "Get Task"}
TASK_COMBINED_TOOLS = {"write_todos"} # Note: keep lowercase for normalization
```

### B. Map Task Tools in Dispatch Table (`plugins/autorun/src/autorun/core.py`)
Add task tools to `CLI_TOOL_NAMES` for correct AI suggestions.

**Code Change:**
```python
"gemini": {
    # ...
    "task_create": "Create Task",
    "task_update": "Update Task",
    "task_list": "List Tasks",
}
```

### C. Unified Input Parsing (`plugins/autorun/src/autorun/task_lifecycle.py`)
Update `create_task` and `update_task` to handle `title` as a fallback for `subject`.

**Code Change:**
```python
# In create_task
subject = input_data.get("subject") or input_data.get("title") or "Untitled Task"

# In update_task metadata loop
for key in ["subject", "title", "description", "activeForm", "owner"]:
    if key in updates:
        actual_key = "subject" if key == "title" else key
        task[actual_key] = updates[key]
```

### D. Bulk Task Support for `WriteTodos` (`plugins/autorun/src/autorun/task_lifecycle.py`)
Add `handle_bulk_todos` method to process Gemini Planner output.

**Logic:**
1.  Extract `todos` array from `WriteTodos` input.
2.  Clear current session tasks (or mark old ones as cancelled).
3.  Create new tasks for each todo item.

## 3. Implementation Strategy (TDD)
1.  **Red (Fail)**: Create `plugins/autorun/tests/test_gemini_native_tasks.py`.
2.  **Green (Pass)**: Sequential application of fixes.
3.  **Refactor**: Ensure Claude Code `TaskCreate` still works (100 0.000000e+00st pass rate).
4.  **Redeploy**: Restart daemon and reinstall.
