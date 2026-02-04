# Plan Export: tool_response.filePath Bug Fix

**Date:** 2026-02-03
**Status:** Fixed (commit `905b584`)
**Test File:** `plugins/plan-export/tests/test_tool_response_filepath.py`

---

## Bug Description

When exiting plan mode, the plan-export plugin exported the **wrong plan file**.

### Observed Behavior

1. User exits plan mode for `keen-napping-sparkle.md` (clautorun hook bugs fix)
2. Plugin exports `stateless-wondering-storm.md` instead (spotlight fix plan)
3. Exported file has wrong content with metadata showing wrong source:
   ```
   original_path: /Users/athundt/.claude/plans/stateless-wondering-storm.md
   ```

### Expected Behavior

Plugin should export the exact plan file that was exited (`keen-napping-sparkle.md`).

---

## Root Cause Analysis

### The Problem

The `tool_response` from ExitPlanMode contains the correct plan file path:
```json
{
  "filePath": "/Users/athundt/.claude/plans/keen-napping-sparkle.md",
  "plan": "# Plan: Fix Two Clautorun Hook Bugs\n\n..."
}
```

**But the plugin ignored this authoritative source** and tried indirect methods:

1. `get_plan_from_transcript(transcript_path)` → FAILED
2. `find_plan_by_session_id(session_id)` → FAILED
3. `get_most_recent_plan()` → Returned WRONG file

### Why Fallbacks Failed

- **Transcript parsing failed**: The plan file path wasn't in the expected format in the transcript
- **Session ID metadata failed**: No matching session_id in plan file metadata
- **Most recent plan was wrong**: Another plan file (`stateless-wondering-storm.md`) was modified more recently

### Debug Log Evidence

```
[2026-02-03 18:37:34.807511] WARNING: Session fe28abb4-...: transcript parsing failed, trying metadata fallback
[2026-02-03 18:37:34.844275] WARNING: Session fe28abb4-...: metadata search failed, using most recent plan
```

---

## The Fix

**Use `tool_response.filePath` as the primary source** since it's authoritative from Claude Code.

### Code Change (plugins/plan-export/scripts/plan_export.py)

```python
# Step 1: Get plan path from tool_response (most reliable - direct from Claude Code)
# ExitPlanMode returns {filePath: "/path/to/plan.md", plan: "content..."}
plan_path = None
if isinstance(tool_response, dict):
    file_path = tool_response.get("filePath")
    if file_path:
        candidate = Path(file_path)
        if candidate.exists():
            plan_path = candidate
```

### Updated Fallback Chain

1. **`tool_response.filePath`** - Direct from Claude Code (NEW - primary)
2. Transcript parsing - file-history-snapshot entries
3. Session ID metadata - embedded in exported plan files
4. Most recent plan - **last resort only**

---

## How to Reproduce

### Prerequisites
- Claude Code with plan-export plugin installed
- Multiple plan files in `~/.claude/plans/`

### Steps to Trigger Bug (with OLD code)

1. Create/modify plan A, exit plan mode (don't export)
2. Create/modify plan B, exit plan mode (don't export)
3. Enter plan mode for plan A
4. Exit plan mode for plan A
5. **Bug**: Plan B gets exported instead of plan A (if B was modified more recently)

### Conditions That Trigger Bug

- Transcript parsing fails (common with session resumes, context compaction)
- Session ID not embedded in plan file metadata
- Another plan file was modified more recently than the target

---

## Test Coverage

### Test File Location
`plugins/plan-export/tests/test_tool_response_filepath.py`

### Test Categories (13 tests)

#### 1. Code Structure Verification
- `test_script_checks_tool_response_first` - Verifies filePath extraction exists
- `test_script_has_correct_fallback_order` - Verifies tool_response is checked BEFORE transcript
- `test_script_validates_file_exists` - Verifies existence check before using
- `test_script_syntax_is_valid` - Basic syntax validation

#### 2. Runtime Behavior
- `test_tool_response_with_valid_filepath` - Valid path is used correctly
- `test_tool_response_with_nonexistent_filepath` - Missing file falls through
- `test_tool_response_without_filepath` - Missing key falls through
- `test_tool_response_not_dict` - Non-dict response handled
- `test_tool_response_empty_dict` - Empty dict handled

#### 3. Regression Prevention
- `test_most_recent_plan_is_last_resort` - Verifies it's Fallback 3
- `test_tool_response_checked_before_transcript` - **Critical**: Prevents the bug

#### 4. ExitPlanMode Format
- `test_exitplanmode_response_format_documented` - Documents expected format
- `test_handles_camelcase_filepath` - Uses "filePath" not "file_path"

### Running Tests

```bash
uv run pytest plugins/plan-export/tests/test_tool_response_filepath.py -v
```

---

## Other Plan Export Failure Modes

### 1. Race Condition (Fixed in v1.0.0)
- **Bug**: Concurrent exports from same session could corrupt output
- **Fix**: SessionLock with RAII pattern
- **Tests**: `test_race_condition_fix.py`, `test_same_session_multi_process.py`

### 2. Stale Lock Recovery (Fixed)
- **Bug**: Crashed process leaves orphaned lock file
- **Fix**: PID-based stale lock detection and cleanup
- **Tests**: `test_stale_lock_recovery.py`

### 3. Bootstrap Fallback (Fixed)
- **Bug**: Script crashed if clautorun not installed (SessionLock import fails)
- **Fix**: try/except with nullcontext fallback
- **Tests**: `test_bootstrap_fallback.py`

### 4. Cross-Session Export (Fixed in 2025-12)
- **Bug**: Plan from different session exported
- **Fix**: Session ID validation in transcript parsing
- **Docs**: `notes/2025_12_20_fix_plan_export_cross-session_bug.md`

### 5. Approval Detection (Fixed in 2025-12)
- **Bug**: Export triggered before user approved plan
- **Fix**: Improved ExitPlanMode detection
- **Docs**: `notes/2025_12_20_1811_fix_plan_export_approval_detection_bug.md`

---

## Synthetic Test Pattern

To create tests for similar bugs:

```python
def test_correct_source_used_first(self):
    """Verify the most authoritative source is checked first."""
    script_path = get_script_path()
    content = script_path.read_text()

    # Find the main function section
    main_start = content.find("def main():")
    main_section = content[main_start:]

    # Find positions of each source check
    authoritative_pos = main_section.find("authoritative_source")
    fallback_pos = main_section.find("fallback_source")

    assert authoritative_pos < fallback_pos, (
        "Authoritative source must be checked BEFORE fallback"
    )
```

---

## Comprehensive Edge Case Tests

**Test File:** `plugins/plan-export/tests/test_edge_cases.py` (48 tests)

### Edge Cases Discovered During Testing

| Function | Edge Case | Behavior |
|----------|-----------|----------|
| `get_plan_from_transcript` | Uses `snapshot.trackedFileBackups` | Not `files` key |
| `embed_plan_metadata` | Modifies export destination | Not source plan file |
| `embed_plan_metadata` | Skips if frontmatter exists | Content starts with `---` |
| `extract_useful_name` | Processes whole file | Doesn't skip YAML frontmatter |
| `expand_template` | Uses `{original}` | Not `{original_name}` |
| `export_plan` | Returns `destination` key | Not `exported_to` |
| `load_config` | Uses `Path.home()` | Must patch at module level |

### Test Categories (48 tests total)

| Category | Tests | Description |
|----------|-------|-------------|
| TestLoadConfig | 3 | Config file missing, invalid JSON, valid JSON |
| TestIsEnabled | 3 | True, False, missing defaults to True |
| TestGetMostRecentPlan | 4 | Dir missing, empty, single file, multiple files |
| TestGetPlanFromTranscript | 5 | Missing, no entries, with entry, file missing, invalid JSON |
| TestGetPlanFromMetadata | 4 | No frontmatter, no session_id, with session_id, quoted |
| TestFindPlanBySessionId | 3 | Dir missing, no match, match found |
| TestEmbedPlanMetadata | 2 | No metadata, existing metadata |
| TestExtractUsefulName | 3 | With heading, no heading, empty |
| TestSanitizeFilename | 3 | Special chars, spaces, alphanumeric |
| TestExpandTemplate | 3 | Date, name, original placeholders |
| TestExportPlan | 2 | Creates file, embeds metadata |
| TestExportRejectedPlan | 1 | Creates in rejected dir |
| TestMainApprovalDetection | 4 | acceptEdits, bypassPermissions, plan, unknown |
| TestMainDisabledExport | 1 | Disabled returns early |
| TestMainNoSessionId | 1 | Unknown session_id warning |
| TestMainNoPlanFound | 1 | All fallbacks fail |
| TestErrorHandling | 3 | IOError handling |
| TestFullExportFlow | 2 | Approved and rejected flows |

### Running Edge Case Tests

```bash
uv run pytest plugins/plan-export/tests/test_edge_cases.py -v
```

---

## References

- **Commit**: `905b584` - fix(plan-export): use tool_response.filePath for correct plan file export
- **Claude Code Hooks Docs**: https://code.claude.com/docs/en/hooks
- **Test Files**:
  - `plugins/plan-export/tests/test_tool_response_filepath.py` (13 tests)
  - `plugins/plan-export/tests/test_edge_cases.py` (48 tests)
- **Debug Log**: `~/.claude/clautorun/logs/plan-export-debug.log` (when debug_logging enabled)
