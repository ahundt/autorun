# Plan Export Plugin Enhancements

## Features to Add

1. **Minute-precision timestamps** - Add `{datetime}` template variable (YYYY_MM_DD_HHmm)
2. **Export rejected plans** - Copy unapproved plans to `rejected/` subdirectory
3. **Configuration options** - Document and expose all config options
4. **Remove `enabled` config** - Plugin installed = enabled; uninstall to disable

## Implementation

### File: `plugins/plan-export/scripts/export-plan.py`

#### 1. Update DEFAULT_CONFIG (line 31-37)

```python
DEFAULT_CONFIG = {
    "output_dir": "notes",
    "filename_pattern": "{datetime}_{name}",  # Changed default to include time
    "extension": ".md",
    "export_rejected": True,  # New option
    "rejected_subdir": "rejected"  # New option
}
```

Remove `"enabled": True` since installed = enabled.

#### 2. Update expand_template() to add {datetime} (line 194-220)

Add new template variables:
```python
replacements = {
    "{YYYY}": now.strftime("%Y"),
    "{YY}": now.strftime("%y"),
    "{MM}": now.strftime("%m"),
    "{DD}": now.strftime("%d"),
    "{HH}": now.strftime("%H"),      # NEW: Hour 00-23
    "{mm}": now.strftime("%M"),      # NEW: Minute 00-59
    "{date}": now.strftime("%Y_%m_%d"),
    "{datetime}": now.strftime("%Y_%m_%d_%H%M"),  # NEW: Full datetime
    "{name}": plan_name,
    "{original}": plan_path.stem,
}
```

#### 3. Update docstring to document new variables (line 13-21)

```python
Template Variables:
  {YYYY}     - 4-digit year (2025)
  {YY}       - 2-digit year (25)
  {MM}       - Month 01-12
  {DD}       - Day 01-31
  {HH}       - Hour 00-23
  {mm}       - Minute 00-59
  {date}     - Full date YYYY_MM_DD
  {datetime} - Full datetime YYYY_MM_DD_HHmm
  {name}     - Extracted plan name from heading
  {original} - Original plan filename (without .md)
```

#### 4. Remove is_enabled() function (line 59-62)

Delete the function entirely - plugin installed means enabled.

#### 5. Add export_rejected_plan() function

New function to export rejected plans to subdirectory:
```python
def export_rejected_plan(plan_path: Path, project_dir: Path) -> dict:
    """Export a rejected plan to the rejected subdirectory."""
    config = load_config()
    output_dir = config.get("output_dir", "notes")
    rejected_subdir = config.get("rejected_subdir", "rejected")
    filename_pattern = config.get("filename_pattern", "{datetime}_{name}")
    extension = config.get("extension", ".md")

    useful_name = extract_useful_name(plan_path)

    # Build path: notes/rejected/
    expanded_dir = expand_template(output_dir, plan_path, useful_name)
    note_dir = project_dir / expanded_dir / rejected_subdir
    note_dir.mkdir(parents=True, exist_ok=True)

    base_filename = expand_template(filename_pattern, plan_path, useful_name)
    base_filename = sanitize_filename(base_filename)
    dest_filename = f"{base_filename}{extension}"
    dest_path = note_dir / dest_filename

    # Handle collision
    counter = 1
    while dest_path.exists():
        dest_filename = f"{base_filename}_{counter}{extension}"
        dest_path = note_dir / dest_filename
        counter += 1

    shutil.copy2(plan_path, dest_path)

    return {
        "success": True,
        "source": str(plan_path),
        "destination": str(dest_path),
        "message": f"Rejected plan saved to {dest_path.relative_to(project_dir)}"
    }
```

#### 6. Update main() to handle approved vs rejected

Check `tool_response` for approval status:
```python
def main():
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        hook_input = {}

    project_dir = Path(hook_input.get("cwd", os.getcwd()))
    transcript_path = hook_input.get("transcript_path")
    tool_response = hook_input.get("tool_response", {})

    # Detect if plan was approved
    # tool_response contains "User has approved your plan" when approved
    is_approved = "approved" in str(tool_response).lower()

    # Get plan file
    plan_path = None
    if transcript_path:
        plan_path = get_plan_from_transcript(transcript_path)
    if not plan_path:
        plan_path = get_most_recent_plan()

    if not plan_path:
        result = {"continue": True, "systemMessage": "No plan files found."}
        print(json.dumps(result))
        return

    config = load_config()

    try:
        if is_approved:
            export_result = export_plan(plan_path, project_dir)
        elif config.get("export_rejected", True):
            export_result = export_rejected_plan(plan_path, project_dir)
        else:
            result = {"continue": True, "suppressOutput": True}
            print(json.dumps(result))
            return

        result = {"continue": True, "systemMessage": export_result["message"]}
    except Exception as e:
        result = {"continue": True, "systemMessage": f"Plan export failed: {e}"}

    print(json.dumps(result))
```

## Files to Modify

- `plugins/plan-export/scripts/export-plan.py` - All changes above

## Configuration Options Summary

Config file: `~/.claude/plan-export.config.json`

```json
{
  "output_dir": "notes",
  "filename_pattern": "{datetime}_{name}",
  "extension": ".md",
  "export_rejected": true,
  "rejected_subdir": "rejected"
}
```

| Option | Default | Description |
|--------|---------|-------------|
| `output_dir` | `"notes"` | Directory for exported plans |
| `filename_pattern` | `"{datetime}_{name}"` | Filename template |
| `extension` | `".md"` | File extension |
| `export_rejected` | `true` | Whether to save rejected plans |
| `rejected_subdir` | `"rejected"` | Subdirectory for rejected plans |

## Testing

1. Exit plan mode with approval → plan saved to `notes/`
2. Exit plan mode without approval → plan saved to `notes/rejected/`
3. Set `export_rejected: false` → rejected plans not saved
4. Verify `{datetime}` produces `2025_12_20_1523` format
5. Uninstall plugin → no plans exported (confirms installed=enabled)
