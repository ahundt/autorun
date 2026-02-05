---
description: Gemini CLI for visual feedback, code review, and comprehensive analysis - includes output management, critical assessment, and multi-version review patterns
---

# Gemini CLI Usage Guide

Use the `gemini` CLI tool for:
- Visual analysis (screenshots, UI designs, diagrams)
- Comprehensive code reviews with cross-referencing
- Algorithm verification and performance analysis
- Plan fact-checking against multiple code versions

**Key principle**: Always save output to files using `tee` - Gemini produces large outputs that exceed terminal buffers.

## Critical Constraints

### Directory Requirement
**Files MUST be in the directory where you run gemini.** Gemini cannot access files outside the project directory or in `/tmp/`.

**Solution:** Copy screenshots to the current working directory before running gemini:
```bash
# Bad - files in /tmp won't be accessible
./target/release/myapp --screenshot /tmp/screenshot.png
gemini "analyze screenshot.png"  # FAILS - file not found

# Good - files in current directory
./target/release/myapp --screenshot ./screenshot.png
gemini "analyze screenshot.png"  # Works
```

### Ignore Patterns
Gemini respects `.gitignore` patterns. If your screenshot is being ignored:
1. Copy to a different filename not matching ignore patterns
2. Or temporarily add to a non-ignored directory
3. Or use a filename without common ignored extensions

## Critical: Managing Large Output

Gemini can produce very large outputs (100KB+) that exceed terminal buffers. **Always save output to files**:

```bash
# Save output to file with tee (see output live AND save it)
mkdir -p gemini_reviews
gemini "your detailed prompt" 2>&1 | tee gemini_reviews/$(date +"%Y_%m_%d_%H%M")_review.txt

# Background execution for long reviews
gemini "your detailed prompt" 2>&1 | tee gemini_reviews/review.txt &
# Then use TaskOutput or tail to check progress

# Read saved output later (handles large files)
grep -A10 "CRITICAL\|APPROVED\|BUG" gemini_reviews/review.txt
tail -200 gemini_reviews/review.txt  # Get conclusions
```

**Why this matters**:
- Gemini outputs can be 100-300KB with debug logs
- Terminal output gets truncated
- Saved files enable searching for specific findings
- Multiple reviews can be compared side-by-side

## CLI Usage

### Basic Usage
```bash
# Single image analysis
gemini "Describe what you see in screenshot.png"

# Specific questions
gemini "Does screenshot.png show a light or dark theme?"

# Multiple files at once (must be in same directory)
gemini "Compare dark.png and light.png - are both themes consistent?"
```

### Prompt Patterns for UI Feedback

**Theme Verification:**
```bash
gemini "Analyze these two theme screenshots: dark-theme.png (dark mode) and light-theme.png (light mode). For each theme check: 1) Are backgrounds appropriate? 2) Is text readable? 3) Do severity badges have proper contrast? 4) Are consumer cards readable? 5) Is each theme internally consistent?"
```

**Layout Review:**
```bash
gemini "Review the UI layout in screenshot.png: 1) Is spacing consistent? 2) Is alignment correct? 3) Are interactive elements clearly visible?"
```

**Accessibility Check:**
```bash
gemini "Check accessibility in screenshot.png: 1) Is contrast sufficient? 2) Are fonts readable? 3) Are clickable areas large enough?"
```

### Complex Prompts: Use Files

For long, detailed prompts with special characters, use a file:

```bash
# 1. Write prompt to file (avoids shell escaping issues)
cat > /tmp/gemini_prompt.txt << 'EOF'
Comprehensive review of plan.md:

**Cross-reference**:
- File A: src/module.rs
- File B: .worktrees/baseline/src/module.rs

Check for bugs, complexity issues, and edge cases.
Use specific line numbers in your response.
EOF

# 2. Run gemini with prompt from file
gemini "$(cat /tmp/gemini_prompt.txt)" 2>&1 | tee gemini_reviews/review.txt

# 3. Cleanup
rm /tmp/gemini_prompt.txt
```

**Advantages**:
- No shell escaping headaches (quotes, backslashes, special chars)
- Easy to edit and refine prompts
- Prompts can be version-controlled
- Reusable prompt templates

### Filtering Debug Output

Gemini outputs verbose debug logs. Filter with:
```bash
# Filter out debug messages
gemini "your prompt" 2>&1 | grep -v "^\[" | grep -v "^Loaded" | grep -v "^Experiments" | grep -v "^Hook" | grep -v "^Session" | grep -v "^Flushing" | grep -v "^Error flushing" | grep -v "^ClearcutLogger" | grep -v "^  " | grep -v "^$" | grep -v "^{$" | grep -v "^}$"

# Or just get the last N lines of actual response
gemini "your prompt" 2>&1 | tail -50
```

## Code Review and Analysis

Gemini excels at comprehensive code reviews when given detailed context and specific instructions.

### Effective Code Review Prompts

**Structure your prompts**:
1. **Context**: What you're reviewing (file, plan, algorithm)
2. **Specific instructions**: What to check (complexity, correctness, edge cases)
3. **Code locations**: Exact file paths and line numbers
4. **Cross-reference sources**: Multiple code versions if applicable
5. **Output requirements**: Format, severity levels, actionable fixes

**Example: Comprehensive Algorithm Review**
```bash
gemini "Review the algorithm in plan_file.md (lines 100-200).

**Code to cross-reference**:
- Current implementation: src/module.rs
- Clean baseline: .worktrees/commit-hash/src/module.rs
- Related logic: src/helpers.rs lines 50-75

**Check for**:
1. Correctness: Logic errors, off-by-one, wrong operators
2. Performance: Complexity claims accurate? O(N²) pathological cases?
3. Edge cases: Empty input, single element, boundary conditions
4. Memory: Unnecessary allocations? Vec reuse opportunities?

**Requirements** (from user guidance):
- Must be O(N) or better
- Low memory overhead
- Easy to use correctly, hard to use incorrectly

**Output format**:
- CRITICAL: Show-stopper bugs
- IMPORTANT: Correctness issues
- OPTIMIZATION: Performance improvements
- VERIFIED: Items confirmed correct

Be specific with line numbers and concrete examples." 2>&1 | tee gemini_reviews/algorithm_review.txt
```

### Critical Assessment of Gemini's Output

**IMPORTANT**: Gemini makes mistakes. Always critically review its findings:

1. **Verify Gemini's math**: Complexity claims, performance calculations, timing estimates
2. **Check Gemini's logic**: Does the suggested fix actually solve the problem?
3. **Test Gemini's examples**: Run the test scenarios it proposes
4. **Cross-reference**: Verify Gemini actually read the code (not hallucinating)
5. **Look for contradictions**: Compare multiple Gemini reviews for consistency

**Red flags** (Gemini might be wrong):
- Performance numbers that seem unrealistic (too fast or too slow)
- Suggestions that contradict language best practices
- Claims without specific line number references
- "Approved" without showing actual verification work

**Best practice**: Run Gemini 2-3 times with different angles, compare results, and critically assess each finding before applying changes.

## Workflow Examples

### Visual Feedback Workflow

```bash
# 1. Build your app
cargo build --release

# 2. Take screenshots IN THE CURRENT DIRECTORY (not /tmp!)
./target/release/myapp --screenshot ./dark-theme.png
./target/release/myapp --screenshot ./light-theme.png

# 3. Run gemini analysis (save output)
gemini "Compare dark-theme.png and light-theme.png. Check:
1) Proper contrast 2) Readable text 3) Theme consistency" 2>&1 | tee gemini_reviews/theme_review.txt

# 4. Read results
grep -v "^\[" gemini_reviews/theme_review.txt | tail -50

# 5. Clean up screenshots
rm dark-theme.png light-theme.png
```

### Code Review Workflow

```bash
# 1. Copy plan/document to notes (gemini can access)
cp /path/to/plan.md notes/plan_for_review.md

# 2. Create baseline worktree if comparing versions
git worktree add .worktrees/baseline <commit-hash>

# 3. Run comprehensive review (save to file!)
gemini "Comprehensive review of notes/plan_for_review.md.

Cross-reference:
- Baseline code: .worktrees/baseline/src/file.rs
- Current code: src/file.rs

Check complexity, correctness, edge cases, test coverage.
Be specific with line numbers." 2>&1 | tee gemini_reviews/code_review.txt

# 4. Extract key findings
grep -A5 "CRITICAL\|BUG\|OPTIMIZATION" gemini_reviews/code_review.txt

# 5. Cleanup
git worktree remove .worktrees/baseline
```

## Advanced Patterns

### Multi-Version Code Review

When reviewing changes against multiple code versions:

```bash
# 1. Create clean worktree for baseline
git worktree add .worktrees/baseline-commit <commit-hash>

# 2. Add worktree to .gitignore
echo ".worktrees/" >> .gitignore

# 3. Run Gemini with cross-referencing
gemini "Review plan_file.md against BOTH code versions:

**Clean baseline**: .worktrees/baseline-commit/src/file.rs
**Current code**: src/file.rs (may have bugs from previous attempts)

Compare the plan's proposed algorithm with:
1. How the baseline implements it
2. Any changes in current code
3. Which version is correct

Report discrepancies with line numbers from BOTH versions." 2>&1 | tee gemini_reviews/multi_version_review.txt

# 4. Cleanup when done
git worktree remove .worktrees/baseline-commit
```

### Iterative Review Pattern

For complex analysis, run multiple focused reviews:

```bash
# Round 1: Algorithm correctness
gemini "Focus on algorithm correctness in plan.md lines 100-200.
Check: logic errors, edge cases, off-by-one errors." | tee gemini_reviews/round1_correctness.txt

# Round 2: Performance analysis
gemini "Focus on performance claims in plan.md lines 300-400.
Verify: complexity analysis, timing estimates, optimization opportunities.
Cross-reference: Round 1 findings in gemini_reviews/round1_correctness.txt" | tee gemini_reviews/round2_performance.txt

# Round 3: Test coverage
gemini "Focus on test coverage in plan.md lines 500-700.
Check: Do tests catch the bugs found in Rounds 1 and 2?
Reference: gemini_reviews/round1_correctness.txt, gemini_reviews/round2_performance.txt" | tee gemini_reviews/round3_tests.txt

# Compare all rounds
diff <(grep "CRITICAL" gemini_reviews/round1_correctness.txt) \
     <(grep "CRITICAL" gemini_reviews/round2_performance.txt)
```

**Benefits**:
- Each review is focused and thorough
- Later reviews can reference earlier findings
- Contradictions between rounds reveal uncertainties
- Saved files enable comparison and tracking

## Prompt Design Best Practices

### Comprehensive Review Template

**For thorough code/plan reviews**, structure prompts with:

```bash
gemini "I need comprehensive review of <target>.

**CRITICAL INSTRUCTIONS**:
1. Read ALL context: <list files/sections to read>
2. Fact-check EVERY claim: <what to verify>
3. Cross-reference: <list all code versions/files>
4. Be specific: Line numbers, concrete examples, actionable fixes

**Review Checklist**:
- Section 1: <What to verify in this section>
- Section 2: <What to verify in this section>
- Section 3: <What to verify in this section>

**Output Requirements**:
- CRITICAL: Show-stopper bugs
- IMPORTANT: Correctness issues
- OPTIMIZATION: Performance improvements
- VERIFIED: Items confirmed correct

Be thorough. I will critically assess YOUR output as well." 2>&1 | tee reviews/comprehensive.txt
```

### Key Elements for Effective Prompts

1. **Explicit instructions**: Tell Gemini exactly what to do ("READ ALL", "FACT-CHECK EVERY", "CROSS-REFERENCE")
2. **Specific targets**: File paths, line numbers, function names
3. **Multiple sources**: Baseline code, current code, related files
4. **Output structure**: How you want results formatted (severity levels, line numbers)
5. **Critical tone**: "I will critically assess your output" → Makes Gemini more thorough
6. **Concrete verification**: "Show your work" → Forces Gemini to provide evidence

### What Makes Gemini Most Effective

**DO**:
- Provide file paths and line numbers (Gemini can read files in current directory)
- Ask for specific checks with clear criteria
- Request concrete examples and test scenarios
- Save output to files for later analysis
- Run multiple rounds from different angles

**DON'T**:
- Trust Gemini blindly (verify its claims)
- Give vague prompts ("review this code")
- Expect Gemini to find everything in one pass
- Assume Gemini's math is correct (double-check calculations)
- Use files outside project directory (Gemini can't access them)

## Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| "File path is ignored by configured ignore patterns" | File matches .gitignore | Rename file or move to non-ignored location |
| "File not found" | File is outside project directory | Copy file to current working directory |
| "Tool not found: run_shell_command" | Gemini trying to use unavailable tool | Ignore - gemini will retry with available tools |
| Output truncated in terminal | Output exceeds terminal buffer | Use `| tee file.txt` to save output |

## Quick Reference

### Essential Commands

```bash
# Visual feedback (save output)
gemini "analyze screenshot.png" 2>&1 | tee gemini_reviews/visual.txt

# Code review (with cross-referencing)
gemini "Review notes/plan.md. Cross-ref: src/code.rs and .worktrees/baseline/src/code.rs" 2>&1 | tee gemini_reviews/review.txt

# Complex prompt from file (avoids escaping)
gemini "$(cat /tmp/prompt.txt)" 2>&1 | tee gemini_reviews/output.txt

# Extract key findings
grep -A5 "CRITICAL\|BUG" gemini_reviews/output.txt

# Filter debug noise
tail -100 gemini_reviews/output.txt | grep -v "^\[" | grep -v "^Loaded"
```

### Critical Success Factors

1. **Always use `| tee file.txt`** - Output can be 100KB+
2. **Files in current directory** - Gemini can't access /tmp or parent dirs
3. **Critically assess output** - Gemini makes mistakes, verify its claims
4. **Multiple rounds** - Run 2-3 reviews from different angles
5. **Specific prompts** - Provide file paths, line numbers, exact requirements
6. **Save everything** - Keep reviews for comparison and tracking

### Lessons Learned

- **Performance claims**: Always verify Gemini's timing estimates (can be off by 10-100×)
- **Complexity analysis**: Check Gemini's Big-O claims (it missed O(N²) pathological cases before)
- **Test coverage**: Gemini is good at spotting missing tests, use those insights
- **Cross-referencing**: Providing multiple code versions makes Gemini more thorough
- **Iterative refinement**: Each Gemini review finds different issues - use multiple rounds

