---
name: gemini
description: Gemini CLI reference - visual analysis, code review, multi-model workflows, and cross-referencing patterns
---

# Gemini CLI Reference

Use `gemini` for visual analysis (screenshots, diagrams) and code reviews with cross-referencing.

## Requirements

**Three hard constraints** - violating any causes silent failures or poor results:

### 1. Files Must Be in Current Directory

Gemini cannot access `/tmp/`, parent directories, or absolute paths outside the project.

```bash
# BAD - files in /tmp won't be accessible
./myapp --screenshot /tmp/screenshot.png
gemini -m gemini-3-pro-preview -o json "analyze screenshot.png" 2>/dev/null | jq -r '.response'
# Result: "File not found" error

# GOOD - files in current directory
./myapp --screenshot ./screenshot.png
gemini -m gemini-3-pro-preview -o json "analyze screenshot.png" 2>/dev/null | jq -r '.response'
# Result: Works correctly

# WORKAROUND - include additional directories
gemini --include-directories /tmp -o json "analyze /tmp/screenshot.png" 2>/dev/null | jq -r '.response'
```

### 2. Files Must Not Match .gitignore

Gemini silently skips files matching ignore patterns. If your file is being ignored:

1. Rename to a filename not matching ignore patterns
2. Move to a non-ignored directory
3. Use a different extension (e.g., `.png` instead of `.log`)

```bash
# Check if file is ignored
git check-ignore screenshot.png && echo "IGNORED - gemini won't see it"
```

### 3. Use Numbered Lists in Prompts

Gemini produces better structured output when prompts use numbered lists:

1. Number all check items (not bullets)
2. Request numbered output format
3. Specify file_path:function_name:line_start-line_end for citations

## Quick Start

```bash
# Setup (once per project)
mkdir -p notes

# Basic: ask a question, get clean response
gemini -m gemini-3-pro-preview -o json "What are the top 3 code review checks?" 2>/dev/null | jq -r '.response'

# Save raw JSON to notes, display extracted response
gemini -m gemini-3-pro-preview -o json "Analyze screenshot.png for accessibility issues" 2>/dev/null | tee notes/$(date +"%Y_%m_%d_%H%M")_accessibility_review.json | jq -r '.response'

# Long response: save raw JSON, show last 30 lines of response
gemini -m gemini-3-pro-preview -o json "Review src/main.py for bugs and edge cases" 2>/dev/null | tee notes/$(date +"%Y_%m_%d_%H%M")_main_py_review.json | jq -r '.response' | tail -30

# Read saved response later
jq -r '.response' notes/2024_01_15_1430_accessibility_review.json
```

**Why `-o json`**: Debug noise goes to stderr only. Raw JSON saved to notes, extract response with `jq -r '.response'`.

**Example raw JSON** (saved to notes):
```json
{
  "session_id": "e491c6ae-7d6b-43fb-8f6a-4d60a93cb813",
  "response": "1. **Logic & Correctness**: Verifying the code...\n2. **Readability**: Ensuring variable names...",
  "stats": {
    "models": {
      "gemini-2.5-flash-lite": {
        "tokens": { "input": 4565, "candidates": 62, "total": 4926 }
      }
    },
    "tools": { "totalCalls": 0 }
  }
}
```

**Fallback** (no jq):
```bash
gemini "your prompt" 2>&1 | tee notes/$(date +"%Y_%m_%d_%H%M")_review.txt | tail -50
# Response is after "ClearcutLogger:" line in output
```

### Timestamp Pattern for File References

When saving output to a timestamped file that you'll reference later in a **separate command**, store the timestamp in a variable first:

```bash
# CORRECT: Timestamp stored, can reference file in later commands
TS=$(date +"%Y_%m_%d_%H%M")
gemini -m gemini-3-pro-preview -o json "prompt" 2>/dev/null | tee "notes/${TS}_review.json" | jq -r '.response'
# Read the same file later
jq -r '.response' "notes/${TS}_review.json"

# ALSO CORRECT: Single command with fallback (both $(date) expand at parse time)
gemini -m gemini-3-pro-preview -o json "prompt" 2>/dev/null | tee notes/$(date +"%Y_%m_%d_%H%M")_review.json | jq -r '.response' || cat notes/$(date +"%Y_%m_%d_%H%M")_review.json

# WRONG: Separate commands = different timestamps
gemini -m gemini-3-pro-preview -o json "prompt" 2>/dev/null | tee notes/$(date +"%Y_%m_%d_%H%M")_review.json | jq -r '.response'
# This creates a NEW file (different timestamp) - won't find the original
jq -r '.response' notes/$(date +"%Y_%m_%d_%H%M")_review.json
```

## Prompt Structure

Seven elements for effective prompts:

1. **Context**: What you're reviewing (file, algorithm, UI)
2. **Instructions**: What to check (correctness, edge cases, contrast)
3. **Locations**: Exact file paths, line numbers
4. **Cross-references**: Multiple code versions if applicable
5. **Critical Review**: Be harshly critical, constructive, and propose concrete and actionable solutions with justification.
6. **Output format**: Severity levels (CRITICAL/IMPORTANT/OPTIMIZATION)
7. **Code snippets**: Always request **BEFORE/AFTER** code for each issue with citation as `file_path:function_name:line_start-line_end` (include function if applicable)

## Cross-Model Critical Review

When Gemini reviews Claude's work (or vice versa):

1. **Reviewer MUST be skeptical** - verify claims against actual code
2. **Disagreements documented** with justification from both sides
3. **Common Gemini mistakes to watch for**:
   - Fabricated line numbers (always verify)
   - Math errors in timing estimates (off by 10-100x)
   - Overconfident "approved" without showing work
   - Missed edge cases or pathological inputs

### BEFORE/AFTER Code Requirement (MANDATORY)

Every code review finding MUST include:
- **BEFORE**: The current code with the problem (with `file_path:function_name:line_start-line_end`)
- **AFTER**: The proposed fix with explanation

**Enforcement**: Add to every Gemini prompt:
```
For each issue provide: (1) BEFORE code with problem, (2) AFTER code with fix, (3) concrete technical justification (4) citation as file_path:function_name:line_start-line_end (include function if applicable).
```

This is already in the template above but is repeated here for emphasis: **without BEFORE/AFTER code with exact line citations, reviews are not actionable.**

**Template** (copy and fill in `<placeholders>`):
```bash
gemini -m gemini-3-pro-preview -o json "Review <FILE_PATH>. **Check for**: <WHAT_TO_CHECK>. **Cross-reference**: <BASELINE_PATH if comparing versions>. **Output format**: CRITICAL/IMPORTANT/OPTIMIZATION. For each issue provide: (1) BEFORE code with problem, (2) AFTER code with fix, (3) concrete technical justification (4) citation as file_path:function_name:line_start-line_end (include function if applicable). Be specific with concrete examples." 2>/dev/null | tee notes/$(date +"%Y_%m_%d_%H%M")_<DESCRIPTION>_review.json | jq -r '.response'
```

**Concrete example**:
```bash
gemini -m gemini-3-pro-preview -o json "Review src/auth/login.py. **Check for**: SQL injection, password handling, session management. **Output format**: CRITICAL/IMPORTANT/OPTIMIZATION. For each issue provide: (1) BEFORE code showing the problem, (2) AFTER code with the fix, (3) citation as src/auth/login.py:function_name:line_start-line_end." 2>/dev/null | tee notes/$(date +"%Y_%m_%d_%H%M")_login_security_review.json | jq -r '.response'
```

**Example output format**:
```
CRITICAL: SQL injection

Location: src/auth/login.py:authenticate_user:45-52

BEFORE:
```python
query = f"SELECT * FROM users WHERE username='{username}'"
```

AFTER:
```python
query = "SELECT * FROM users WHERE username=?"
cursor.execute(query, (username,))
```

Explanation: User input passed directly to query string. Fix uses parameterized query.
```

## Iterative Review Process

Gemini review is NOT one-shot. Follow this cycle:

1. **Run review** → Save to `notes/${TS}_review_round1.json`
2. **Claude critically reviews** each Gemini finding for validity
3. **Create tasks** for ALL valid findings with `[GEMINI-CRITICAL/HIGH/MEDIUM/LOW]` prefix
4. **Implement fixes**
5. **Re-run Gemini** on same code → Save to `notes/${TS}_review_round2.json`
6. **Compare** Round 2 vs Round 1 - are previous issues resolved? New ones found?
7. **Repeat** until Gemini finds no more issues
8. **Document**: "Gemini review complete after N iterations"

### Task Creation Template

After each Gemini review, create tasks for valid findings:
```bash
# Claude should create tasks like:
TaskCreate({
    "subject": "[GEMINI-HIGH] SQL injection in login.py:authenticate_user:45-52",
    "description": "Gemini finding: User input in f-string query\nFile: src/auth/login.py:45-52\nFix: Use parameterized queries"
})

# Also create a task for future review to verify fixes
TaskCreate({
    "subject": "[GEMINI-REVIEW] Re-run Gemini review after fixes implemented",
    "description": "After implementing all Gemini findings, re-run: gemini -m gemini-3-pro-preview -o json \"Review src/auth/login.py. **Check for**: Remaining security issues, regressions from fixes, edge cases. **Output format**: CRITICAL/IMPORTANT/OPTIMIZATION. For each issue provide: (1) BEFORE code with problem, (2) AFTER code with fix, (3) concrete technical justification (4) citation as src/auth/login.py:function_name:line_start-line_end.\" 2>/dev/null | tee notes/$(date +\"%Y_%m_%d_%H%M\")_login_round2_review.json | jq -r '.response'"
})
```

## Gotchas

### Error Reference

| Error | Cause | Fix |
|-------|-------|-----|
| "File path is ignored by configured ignore patterns" | .gitignore match | Rename or move file |
| "File not found" | Outside project directory | Copy to cwd |
| "Tool not found: run_shell_command" | Gemini internal | Ignore - retries automatically |
| Output truncated | Exceeds terminal buffer | Use `-o json` with tee |
| Empty output (no error) | Invalid model name with `2>/dev/null` | Verify model name from CLI Options table. Common mistakes: `gemini-3-pro` (use `gemini-3-pro-preview`), `gemini-3-flash` (use `gemini-3-flash-preview`), `gemini-2.0-pro` (use `gemini-2.5-pro`) |
| "Unknown argument: file" | `--file` flag doesn't exist | Reference files by name in prompt (Gemini reads project files). For files outside project use `--include-directories` |

### Long Prompts

Piping long prompts via heredoc (`cat << 'EOF' | gemini`) can timeout. For prompts longer than a few lines:

```bash
# Store timestamp for consistent file naming
TS=$(date +"%Y_%m_%d_%H%M")

# Write prompt to file first
cat > "notes/${TS}_gemini_prompt.txt" << 'EOF'
<your long prompt here>
EOF

# Pass via -p flag or command substitution
gemini -m gemini-3-pro-preview -o json "$(cat "notes/${TS}_gemini_prompt.txt")" 2>/dev/null | jq -r '.response'
```

This pattern is already documented in the "Complex Prompts" section below, but is repeated here because heredoc piping is a common first instinct that fails for long prompts.

### Critically Assess All Output

Gemini is a useful second opinion, not a source of truth. **Always verify claims before acting on them.**

**Common issues**:
1. Math errors (timing estimates off by 10-100x)
2. Missed edge cases or pathological inputs
3. Fabricated line numbers or function names
4. Overconfident "approved" without showing work

**Mitigation**:
1. Cross-check claims against actual code
2. Run 2-3 reviews from different angles
3. Compare results for contradictions
4. Test suggested fixes before applying

### Complex Prompts

For prompts with special characters or multi-line structure, write to file first:
```bash
# Store timestamp to reference the saved files later
TS=$(date +"%Y_%m_%d_%H%M")
cat > "notes/${TS}_gemini_prompt.txt" << 'EOF'
Review src/api/handlers.py for security issues.

**Check for**:
1. SQL injection in query building
2. XSS in response rendering
3. Authentication bypass

**Cross-reference**: Compare with src/api/validators.py

**Output**: CRITICAL/IMPORTANT. For each issue cite: file_path:function_name:line_start-line_end with fix suggestions.
EOF

gemini -m gemini-3-pro-preview -o json "$(cat "notes/${TS}_gemini_prompt.txt")" 2>/dev/null | tee "notes/${TS}_api_security_review.json" | jq -r '.response'

# Read the saved response later
jq -r '.response' "notes/${TS}_api_security_review.json"

# Cleanup: trash "notes/${TS}_gemini_prompt.txt" (or delete manually)
```

## Examples

### Visual Analysis

```bash
# 1. Take screenshot IN current directory (not /tmp)
./myapp --screenshot ./screenshot.png

# 2. Run analysis (raw JSON saved to notes)
gemini -m gemini-3-pro-preview -o json "Analyze screenshot.png: 1) Is contrast sufficient for accessibility? 2) Is text readable? 3) Are interactive elements clearly visible?" 2>/dev/null | tee notes/$(date +"%Y_%m_%d_%H%M")_screenshot_accessibility.json | jq -r '.response'

# 3. Cleanup (optional)
trash screenshot.png  # or delete manually
```

### Code Review with Worktree

```bash
# 1. Create baseline worktree for comparison
git worktree add .worktrees/baseline <commit-hash>
echo ".worktrees/" >> .gitignore

# 2. Run comparative review (raw JSON saved to notes)
gemini -m gemini-3-pro-preview -o json "Review src/module.rs against baseline. **Current**: src/module.rs **Baseline**: .worktrees/baseline/src/module.rs Check: 1. Logic errors, off-by-one, wrong operators 2. Edge cases: empty input, boundary conditions 3. Which version is correct where they differ. For each issue cite: file_path:function_name:line_start-line_end from BOTH versions." 2>/dev/null | tee notes/$(date +"%Y_%m_%d_%H%M")_module_rs_worktree_review.json | jq -r '.response' | tail -50

# 3. Read full response if needed
jq -r '.response' notes/2024_01_15_1430_module_rs_worktree_review.json

# 4. Cleanup
git worktree remove .worktrees/baseline
```

## Reference

### CLI Options

```bash
# Output format (required for clean extraction)
gemini -m gemini-3-pro-preview -o json "prompt"                    # JSON output, use with jq
gemini -o text "prompt"                    # Plain text (default, has debug noise)

# Model selection (omit -m for auto-selection)
gemini "prompt"                            # Auto: uses gemini-2.5-flash-lite + gemini-3-flash-preview
gemini -m gemini-3-pro-preview "prompt"    # Quality: best, Speed: slow, Cost: $$$, Status: preview
gemini -m gemini-3-flash-preview "prompt"  # Quality: high, Speed: fast, Cost: $$, Status: preview
gemini -m gemini-2.5-pro "prompt"          # Quality: high, Speed: slow, Cost: $$$, Status: released
gemini -m gemini-2.5-flash "prompt"        # Quality: good, Speed: fast, Cost: $$, Status: released
gemini -m gemini-2.5-flash-lite "prompt"   # Quality: good, Speed: fastest, Cost: $, Status: released

# Approval modes
gemini --approval-mode plan "prompt"       # Read-only mode: no file changes (SAFE for reviews)
gemini --approval-mode auto_edit "prompt"  # Auto-approve edits only
# NOTE: YOLO mode exists but is BANNED - it auto-accepts destructive actions without confirmation

# Include additional directories (workaround for directory constraint)
gemini --include-directories /path/to/other/project "analyze file.py"
gemini --include-directories ../sibling-repo,/tmp "compare files"

# Session management
gemini --list-sessions                     # List previous sessions
gemini -r latest "continue previous task"  # Resume most recent session
gemini -r 3 "continue"                     # Resume session #3

# Interactive mode
gemini                                     # Start interactive session
gemini -i "start with this prompt"         # Interactive, starting with prompt
```

### Reading Saved Output

```bash
# Full response from a saved review
jq -r '.response' notes/2024_01_15_1430_login_security_review.json

# Last 50 lines of response
jq -r '.response' notes/2024_01_15_1430_login_security_review.json | tail -50

# Search for issues in response
jq -r '.response' notes/2024_01_15_1430_login_security_review.json | grep -iE "bug|error|critical|wrong|fail"

# Check token usage and tool calls
jq '{tokens: .tokens, tools: .tools.totalCalls}' notes/2024_01_15_1430_login_security_review.json

# List all saved reviews
ls -la notes/*_review.json
```

### Summary

| Requirement | Reason |
|-------------|--------|
| Files in cwd | Can't access /tmp or parent dirs |
| Not gitignored | Silently skipped |
| `-o json` + `jq` | Clean extraction, no debug noise |
| `2>/dev/null` | Suppress stderr debug output |
| Critically assess | Gemini is second opinion, not truth |
| Multiple rounds | Each pass finds different issues, repeat until all parties approve. |
