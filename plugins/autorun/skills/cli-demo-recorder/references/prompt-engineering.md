# TUI Prompt Engineering

When a feature does not fire, **the cause is always the prompt or context — not the tool's behavior or the AI's "safety training."** Fix the prompt.

## Root Cause Framework

1. Prompt is ambiguous — AI answered with a native tool substitute
2. Context is wrong — AI does not have the state that makes the tool call obvious
3. Framing is alarming — AI refuses pre-emptively without making any tool call
4. Command is conditional — block only fires in specific git/file states

## Verified Failure-Fix Sequence

| Prompt that failed | Why it failed | Fixed prompt |
|---|---|---|
| `"delete important data"` | "important" — AI refuses before calling Bash | `"Delete project_data.csv — it's test output. Run the bash rm command."` |
| `"find TODOs using grep in bash"` | AI used native Grep tool, hook never fired | `"Using the Bash tool, run: grep -n 'TODO' main.py"` |
| `"Run: git reset --hard HEAD~2"` | Alarming framing — AI pauses to confirm | `"The last two commits broke auth. Run the Bash tool: git reset --hard HEAD~2"` |
| `"Show me what sed would match"` | AI uses Read + pattern match, no Bash call | `"Using the Bash tool, run: sed -n '/TODO/p' main.py"` |
| `"Clean up temp files: git clean -f"` | Claude self-overrides via /ar:ok | Add: `"do not override any safety blocks, just report what the hook says"` |

## Forcing Specific Tool Calls

**Method 1: Explicit tool naming (most reliable)**
```python
session.send_prompt("Using the Bash tool, run: grep -n 'TODO' main.py")
```

**Method 2: Command with no native substitute**
```python
# sed -n cannot be done with Edit tool — AI must use Bash:
session.send_prompt("Using the Bash tool, run: sed -n '/TODO/p' main.py")
```

**Method 3: Use unconditionally-blocked commands (most reliable for demos)**

| Conditional (unreliable) | Unconditional (reliable) |
|---|---|
| `git reset --hard` (only if unstaged changes) | `git clean -f` |
| `grep` (only if not piped) | `sed` |
| `find` (only if not piped) | `rm` |

## Preventing Self-Override

```python
session.send_prompt(
    "Using the Bash tool, run: git clean -f — "
    "do not override any safety blocks, just report what the hook says."
)
```

## Bash Exec Does Not Work in TUI

Some tools use `! bash` or `exec` lines. In a TUI the `!` line is passed as raw text to the AI rather than executed:

```python
# Wrong: Command uses ! bash exec — appears as literal text in TUI:
session.send_prompt("/mytool:export-status")
# AI receives "! uv run export_status.py" as text, produces useless output

# Correct: Use hook-based commands instead (UserPromptSubmit, PreToolUse):
session.send_prompt("/mytool:export-on")
```

## Dynamic UI String Detection

Never hardcode exact UI strings from dialog boxes or menus:

```python
# Wrong: Breaks when dialog wording changes:
if "Yes, I trust this folder" in content:
    send_key("Enter")

# Correct: Keyword detection:
if any(kw in content.lower() for kw in ["trust", "safe", "quick safety check"]):
    send_key("Enter")
```

## Plan Approval Menu Parsing

```python
# Wrong: hardcoded — breaks when menu reorders
session._send_key("2\n")  # "2" may mean "clear context" in some versions

# Correct: parse actual menu; use exact word sets
_ACCEPT_WORDS = ("yes", "proceed", "accept", "bypass")
_CLEAR_WORDS = ("clear context", "new conversation", "fresh context", "clear history")
# Regex handles cursor prefix:
m = re.match(r'[>\s]*(\d+)\.\s+(.+)', stripped_line)
# Select line with accept word AND no clear word; fallback to "1"
```

## Shell Command Overlap Prevention

```python
# Wrong: Fixed sleep may not be enough if previous command runs longer:
session.send_command("python3 banner.py")
time.sleep(1.0)
session.send_command("claude")  # overlap!

# Correct: Wait for shell prompt after each command:
session.send_command("python3 banner.py")
wait_for_shell_prompt(pane, timeout=10)
session.send_command("claude")
```

## Verification

Parse the session JSONL to confirm every act fired the correct tool call:

```python
def verify_session(jsonl_path: str) -> None:
    with open(jsonl_path) as f:
        for i, line in enumerate(f):
            obj = json.loads(line)
            for block in obj.get("message", {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block["name"]
                    cmd = block.get("input", {}).get("command", "")[:60]
                    print(f"L{i:3d}  {name}({cmd})")
```

| What is seen | Meaning | Fix |
|---|---|---|
| `Bash(rm project_data.csv)` | Hook fires | — |
| `Grep(TODO)` | Native tool, hook skipped | Add "Using the Bash tool, run:" |
| `[no tools]` | Pre-emptive refusal | Switch to unconditional command |
| `Bash(/ar:ok ... && cmd)` | AI self-overrode | Add "do not override any safety blocks" |
