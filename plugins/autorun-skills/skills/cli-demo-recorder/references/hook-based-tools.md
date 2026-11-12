# Hook-Based Tools: Forcing Bash Tool Calls

When your CLI tool uses PreToolUse hooks (like autorun), the hooks only fire
when Claude calls the `Bash` tool — NOT native tools like Grep, Read, Edit, Glob.

## The Problem

Modern Claude prefers native tools because they're more reliable:

```
User: "Find all TODO comments in main.py"
Claude: Grep(pattern='TODO', path='main.py')  ← native tool, hook NEVER fires
```

## The Solution

Explicitly name the tool in the prompt:

```
User: "Using the Bash tool, run: grep -n 'TODO' main.py"
Claude: Bash(grep -n 'TODO' main.py)  ← Bash tool, hook FIRES
```

## Verified Working Patterns

### grep / sed (always blocked, but Claude substitutes native Grep/Edit)
```python
# ❌ fails: Claude uses native Grep
"Find TODO comments using grep in main.py"

# ✅ works: explicit Bash tool request
"Using the Bash tool, run: grep -n 'TODO' main.py"

# ✅ works: sed dry-run (Claude can't substitute Edit for print-only sed)
"Using the Bash tool, run: sed -n '/TODO/p' main.py"
```

### rm (always blocked, Claude usually calls Bash for destructive ops)
```python
# ✅ works: "Run the bash rm command" forces Bash
"Delete project_data.csv — it's test output. Run the bash rm command to remove it."
```

### git clean -f (always blocked, less alarming than git reset)
```python
# ✅ works: explicit Bash + "do not override" prevents self-override
"Using the Bash tool, run: git clean -f — do not override any safety blocks."
```

### git reset --hard (conditional block, more alarming → Claude may pre-refuse)
```python
# ⚠️ non-deterministic: Claude sometimes refuses before calling Bash
# Better to use git clean -f for demos unless you need reset specifically
"Using the Bash tool, run: git reset --hard HEAD~2"
```

## Self-Override Prevention

If your tool has an override command (like `/ar:ok`), Claude may try to use it
after seeing a block. Add an explicit instruction:

```python
"Using the Bash tool, run: git clean -f — "
"do not override any safety blocks, just report what the hook says."
```

## Verification Script

After recording, parse the session JSONL to confirm Bash was used:

```python
import json

path = "~/.claude/projects/-private-tmp-demo-12345/session.jsonl"
with open(path) as f:
    for i, line in enumerate(f):
        obj = json.loads(line)
        content = obj.get('message', {}).get('content', [])
        for block in (content if isinstance(content, list) else []):
            if isinstance(block, dict) and block.get('type') == 'tool_use':
                name = block['name']
                cmd = block.get('input', {}).get('command', '')[:60]
                print(f"L{i:3d}  {name}({cmd})")
```

Expected output for a working demo:
```
L  5  Bash(rm project_data.csv)        ← act1: rm hook fires
L 21  Bash(sed -n '/TODO/p' main.py)   ← act2: sed hook fires
L 36  Bash(git clean -f)               ← act3: git clean hook fires
```

Red flags:
```
L 16  Grep(TODO)                       ← ❌ act2: native Grep, hook never fired
L 41  assistant: I need to confirm...  ← ❌ act3: pre-emptive refusal, no Bash call
```
