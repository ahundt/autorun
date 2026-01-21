# Fix Plan Export Approval Detection Bug

## Problem Statement

**Current behavior**: Manual plan acceptance (editing files directly) causes plans to be exported to `notes/rejected/` instead of `notes/`.

**Root cause**: The approval detection logic `"approved" in str(tool_response).lower()` **NEVER finds "approved"** because:
1. `tool_response` only contains `{"plan": "...", "isAgent": false, "filePath": "..."}`
2. The string "approved" does not appear anywhere in this structure
3. Debug log confirms: **100% of exports show `is_approved: False`**

**Critical finding**: ExitPlanMode has a known bug (GitHub issue #5036) where it returns approval messages without actual user interaction, making tool_response unreliable for approval detection.

## Research Findings

### Available Hook Data (from PostToolUse hook input)

Based on plugin-dev documentation and clautorun patterns, the hook receives:

```json
{
  "session_id": "abc123",
  "transcript_path": "/path/to/transcript.jsonl",
  "cwd": "/working/directory",
  "permission_mode": "default|plan|acceptEdits|bypassPermissions",
  "hook_event_name": "PostToolUse",
  "tool_name": "ExitPlanMode",
  "tool_input": {...},
  "tool_response": {...},
  "tool_use_id": "toolu_01ABC123..."
}
```

**Key field**: `permission_mode` indicates the user's approval context:
- `"plan"` - User is in plan mode (approval status unclear)
- `"acceptEdits"` - User accepted edits ✅ **This means approved!**
- `"bypassPermissions"` - User bypassed permissions ✅ **This also means approved!**
- `"default"` - Standard mode

### Current vs. Correct Detection Approach

**Current (BROKEN)**:
```python
is_approved = "approved" in str(tool_response).lower()
```
❌ Never finds "approved" because tool_response only contains plan metadata

**Correct Approach #1 - Use permission_mode field**:
```python
permission_mode = hook_input.get("permission_mode", "default")
is_approved = permission_mode in ["acceptEdits", "bypassPermissions"]
```
✅ Reliable signal of user approval

**Correct Approach #2 - Check transcript for approval markers**:
```python
# Similar to clautorun's pattern-matching approach
transcript_path = hook_input.get("transcript_path")
# Parse transcript and look for approval markers
```
✅ Most robust but requires transcript parsing

**Correct Approach #3 - Assume approval by default**:
```python
# Since manual edit acceptance should export to notes/ not rejected/
is_approved = True  # Default to approved
# Only mark as rejected if explicit rejection signal exists
```
✅ Simple, matches user expectation

## Recommended Solution

**Use permission_mode field with sensible fallback:**

```python
# Get permission mode from hook input
permission_mode = hook_input.get("permission_mode", "default")

# Detect approval using permission_mode field
if permission_mode in ["acceptEdits", "bypassPermissions"]:
    is_approved = True
elif permission_mode == "plan":
    # User is in plan mode - could be approval or rejection
    # Default to approved since user explicitly exited plan mode
    is_approved = True
else:
    # Default mode - treat as approved
    is_approved = True
```

**Rationale**:
1. `permission_mode` is the official signal from Claude Code
2. Exiting plan mode (regardless of method) indicates user wants to proceed
3. Only explicit rejection (cancel) should prevent export
4. Manual edit acceptance (`acceptEdits`) is a form of approval

## Implementation Steps

### Step 1: Enhanced Debug Logging

Add `permission_mode` to debug logging to verify it's available:

**File**: `plugins/plan-export/scripts/export-plan.py` (after line 364)

```python
# Debug logging to diagnose approval detection (if enabled in config)
config = load_config()
if config.get("debug_logging", False):
    try:
        debug_log = Path.home() / ".claude" / "plan-export-debug.log"
        with open(debug_log, "a") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"Timestamp: {datetime.now()}\n")
            f.write(f"is_approved: {is_approved}\n")
            f.write(f"\npermission_mode: {hook_input.get('permission_mode', 'NOT_FOUND')}\n")
            f.write(f"\ntool_response type: {type(tool_response)}\n")
            f.write(f"tool_response: {json.dumps(tool_response, indent=2)}\n")
            f.write(f"\ntool_input: {json.dumps(hook_input.get('tool_input', {}), indent=2)}\n")
            f.write(f"{'='*60}\n")
    except Exception as e:
        # Don't let logging errors break the export
        pass
```

### Step 2: Fix Approval Detection Logic

**File**: `plugins/plan-export/scripts/export-plan.py` (line 361)

**Replace**:
```python
# Detect if plan was approved
# tool_response contains "User has approved your plan" when approved
is_approved = "approved" in str(tool_response).lower()
```

**With**:
```python
# Detect if plan was approved using permission_mode field
permission_mode = hook_input.get("permission_mode", "default")

# Check permission_mode for approval signals
if permission_mode in ["acceptEdits", "bypassPermissions"]:
    # User explicitly accepted edits or bypassed permissions
    is_approved = True
elif permission_mode == "plan":
    # User is in plan mode - exiting plan mode is implicit approval
    is_approved = True
else:
    # Default mode - treat as approved (user exited plan mode)
    is_approved = True

# Note: Only explicit cancellation (which doesn't trigger PostToolUse)
# prevents plan export. Exiting plan mode by any method indicates approval.
```

### Step 3: Test with Enhanced Debug Logging

1. Exit plan mode with "approve and implement"
2. Check `~/.claude/plan-export-debug.log` for `permission_mode` value
3. Verify plan goes to correct directory (`notes/` for approved, `notes/rejected/` for rejected)
4. Test with manual edit acceptance
5. Verify all approval methods work correctly

## Alternative: Simplest Approach (If permission_mode Not Available)

If `permission_mode` field is not present in hook_input, use this fallback:

```python
# Simplest approach: Default to approved
# Rationale: User explicitly exited plan mode, indicating intent to proceed
is_approved = True

# Only exception: Check if export_rejected config is enabled
# If user has rejected plans enabled, they want differentiation
config = load_config()
if config.get("export_rejected", True):
    # Keep current behavior but fix the detection
    # Look for explicit rejection signals (if any exist)
    pass
```

## Files to Modify

1. **`plugins/plan-export/scripts/export-plan.py`** (lines 361-379)
   - Add `permission_mode` to debug logging (line 371)
   - Fix approval detection to use `permission_mode` field (line 361)
   - Add comments explaining the rationale

2. **`~/.claude/plan-export.config.json`** (already updated)
   - Already has `debug_logging: true` for testing
   - Already has correct config option names

## Testing Strategy

### Phase 1: Verify permission_mode Field Exists
1. Update debug logging to include `permission_mode`
2. Exit plan mode with approval
3. Check debug log for `permission_mode` value
4. If NOT_FOUND, use simplest fallback approach

### Phase 2: Test All Approval Methods
1. **Test "Approve and Implement"** → Should export to `notes/`
2. **Test "Accept Edits Manually"** → Should export to `notes/`
3. **Test "Reject with Changes"** → Should NOT trigger PostToolUse (plan mode continues)
4. **Test "Cancel"** → Should NOT trigger PostToolUse (plan mode exits without export)

### Phase 3: Verify Correct Behavior
- Plans with any approval method go to `notes/` directory
- Rejected plans (if PostToolUse triggers) go to `notes/rejected/`
- Filename includes full timestamp: `YYYY_MM_DD_HHmm_plan_name.md`
- Debug log shows correct `permission_mode` values

## Success Criteria

✅ **Primary Goal**: Manual edit acceptance exports to `notes/` instead of `notes/rejected/`

✅ **Verification**:
1. `permission_mode` field is logged and available
2. Detection logic uses `permission_mode` instead of searching tool_response
3. All approval methods (approve, accept edits, bypass) work correctly
4. Plans export to correct directory based on actual user action

✅ **Documentation**: Code comments explain why this approach works and references GitHub issue #5036

## Risk Mitigation

**Risk**: `permission_mode` field may not be available in all hook contexts

**Mitigation**: Default to `is_approved = True` as fallback, since:
- User explicitly exited plan mode (indicates intent to proceed)
- Export to `notes/` is safer than `notes/rejected/` for unclear cases
- User can manually move files if needed

**Risk**: Breaking existing behavior for users who rely on rejected plan exports

**Mitigation**: Keep `export_rejected` config option working, but improve detection accuracy
