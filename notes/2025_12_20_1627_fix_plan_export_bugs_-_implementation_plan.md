# Fix Plan Export Bugs - Implementation Plan

## Goal
Add debug logging to diagnose approval detection bug, then fix based on actual tool_response data.

## Bugs to Fix

1. **Missing timestamp precision** - User's config has `{date}_{name}` instead of `{datetime}_{name}`
2. **Wrong folder for manual approval** - Manual edit acceptance goes to `rejected/` instead of `notes/`
3. **Approval detection** - Current detection `"approved" in str(tool_response).lower()` may be too narrow

## Implementation Steps

### Step 1: Add Debug Logging

**File**: `plugins/plan-export/scripts/export-plan.py`

Add logging after line 359 (after `is_approved` is calculated):

```python
# Debug logging to diagnose approval detection
try:
    debug_log = Path.home() / ".claude" / "plan-export-debug.log"
    with open(debug_log, "a") as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"Timestamp: {datetime.now()}\n")
        f.write(f"Plan: {plan_path}\n")
        f.write(f"is_approved: {is_approved}\n")
        f.write(f"\ntool_response type: {type(tool_response)}\n")
        f.write(f"tool_response: {json.dumps(tool_response, indent=2)}\n")
        f.write(f"\ntool_input: {json.dumps(hook_input.get('tool_input', {}), indent=2)}\n")
        f.write(f"{'='*60}\n")
except Exception as e:
    # Don't let logging errors break the export
    pass
```

This will capture:
- What `tool_response` actually contains
- Whether our current detection logic works
- The type of data structure we're working with

### Step 2: Update User Config

**File**: `~/.claude/plan-export.config.json`

Change from:
```json
{
  "enabled": true,
  "output_dir": "notes",
  "filename_pattern": "{date}_{name}",
  "extension": ".md"
}
```

To:
```json
{
  "enabled": true,
  "output_dir": "notes",
  "filename_pattern": "{datetime}_{name}",
  "extension": ".md",
  "export_rejected": true,
  "rejected_subdir": "rejected"
}
```

### Step 3: Exit Plan Mode and Observe

When I exit plan mode:
1. The PostToolUse hook will fire
2. Debug log will be written to `~/.claude/plan-export-debug.log`
3. I can read the log to see actual `tool_response` content
4. Then fix the approval detection logic based on real data

## Files to Modify

1. `plugins/plan-export/scripts/export-plan.py` - Add debug logging (lines after 359)
2. `~/.claude/plan-export.config.json` - Update pattern to `{datetime}_{name}`

## Testing Strategy

1. Implement debug logging
2. Exit plan mode (triggers hook, writes debug log)
3. Read `~/.claude/plan-export-debug.log`
4. Analyze actual `tool_response` structure
5. Fix approval detection based on findings
6. Test again with corrected logic

## Success Criteria

- Debug log shows actual `tool_response` content
- Approved plans go to `notes/` with `{datetime}` format
- Rejected plans go to `notes/rejected/`
- Manual approval is detected correctly
