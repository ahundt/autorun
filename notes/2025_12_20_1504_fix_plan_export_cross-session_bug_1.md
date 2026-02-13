# Fix Plan Export Cross-Session Bug

## Problem
When two Claude Code sessions are simultaneously in plan mode, the `export-plan.py` script exports the wrong session's plan file. This happens because `get_most_recent_plan()` selects the globally most recent plan file by modification time, ignoring which session is exiting plan mode.

## Root Cause
**File**: `plugins/plan-export/scripts/export-plan.py`

**Vulnerable Code** (lines 59-71):
```python
def get_most_recent_plan() -> Path | None:
    plans_dir = Path.home() / ".claude" / "plans"
    plan_files = list(plans_dir.glob("*.md"))
    plan_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return plan_files[0]  # BUG: Returns globally most recent, ignores session
```

**Available but unused**: The hook provides `transcript_path` which contains session-specific plan file references in `file-history-snapshot` entries.

## Solution
Parse the session transcript to find the plan file that was actually edited in that session, rather than relying on global modification time.

### Implementation Steps

1. **Add transcript parsing function** to extract plan file path from session transcript
   - Read JSONL transcript from `transcript_path`
   - Search for `file-history-snapshot` entries
   - Extract paths matching `~/.claude/plans/*.md` from `trackedFileBackups`
   - Return the most recent plan file found in session history

2. **Update `main()` function** to use session-aware plan selection
   - Get `transcript_path` from hook input
   - Call new function to find session's plan file
   - Fall back to `get_most_recent_plan()` only if transcript parsing fails

### Code Changes

**File**: `plugins/plan-export/scripts/export-plan.py`

Add new function after line 71:
```python
def get_plan_from_transcript(transcript_path: str) -> Path | None:
    """Extract plan file path from session transcript.

    Parses the JSONL transcript to find file-history-snapshot entries
    that track which plan file was edited in this session.
    """
    plans_dir = Path.home() / ".claude" / "plans"
    transcript = Path(transcript_path)

    if not transcript.exists():
        return None

    found_plans = set()
    try:
        with open(transcript, 'r') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "file-history-snapshot":
                        tracked = entry.get("snapshot", {}).get("trackedFileBackups", {})
                        for file_path in tracked.keys():
                            if str(plans_dir) in file_path and file_path.endswith(".md"):
                                found_plans.add(file_path)
                except json.JSONDecodeError:
                    continue
    except IOError:
        return None

    if not found_plans:
        return None

    # Return the plan file (usually only one per session)
    # If multiple, use modification time as tiebreaker
    valid_plans = [Path(p) for p in found_plans if Path(p).exists()]
    if not valid_plans:
        return None

    valid_plans.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return valid_plans[0]
```

Modify `main()` function (around line 215-218):
```python
# Get transcript path for session-aware plan selection
transcript_path = hook_input.get("transcript_path")

# Try to get plan from session transcript first
plan_path = None
if transcript_path:
    plan_path = get_plan_from_transcript(transcript_path)

# Fall back to most recent plan if transcript parsing failed
if not plan_path:
    plan_path = get_most_recent_plan()
```

## Files to Modify
- `plugins/plan-export/scripts/export-plan.py` - Add transcript parsing, update main()

## Testing
1. Start two Claude Code sessions in different projects
2. Enter plan mode in both
3. Exit plan mode in the older session
4. Verify the correct plan file is exported to the correct project
