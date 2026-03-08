# Skill Troubleshooting Guide

From Anthropic's "The Complete Guide to Building Skills for Claude" (January 2026, pages 24-26).

Use this guide when a skill is built but not behaving as expected.

---

## Problem 1: Skill Doesn't Trigger

**Symptom**: Claude does not activate the skill when users type phrases that should trigger it.

### Diagnosis

```bash
# 1. Check the frontmatter exists
head -10 ~/.claude/skills/your-skill-name/SKILL.md
# Expected: --- block at line 1 with name, description, version

# 2. Check the description has trigger phrases
head -15 ~/.claude/skills/your-skill-name/SKILL.md | grep -i "when\|wants to\|asks"
# Expected: phrases like "when the user wants to", "when user asks for"

# 3. Check folder naming
ls -d ~/.claude/skills/your-skill-name
# Expected: kebab-case, no underscores, no capitals
```

### Common Causes and Fixes

**Cause A: No YAML frontmatter**

```yaml
# ❌ Wrong — no frontmatter, Claude never sees the skill's description
# Claude Skill Builder
Build Claude Code skills...

# ✅ Fix — add frontmatter as the VERY FIRST thing in the file
---
name: your-skill-name
description: This skill should be used when the user wants to "trigger phrase 1",
  "trigger phrase 2", or needs help with [domain].
version: 0.1.0
---

# Claude Skill Builder
Build Claude Code skills...
```

**Cause B: Description is outcome-focused instead of trigger-phrase format**

```yaml
# ❌ Wrong — outcome-focused language doesn't match user queries
description: Generate API tests 87% faster than manual writing

# ✅ Fix — trigger-phrase format matches what users actually say
description: This skill should be used when the user wants to "generate API tests",
  "create a test suite", "write tests for my API", or needs help with API test generation.
```

**Cause C: Trigger phrases are too technical or uncommon**

```yaml
# ❌ Wrong — no user says "synthesize REST endpoint coverage matrices"
description: Use when user wants to "synthesize REST endpoint coverage matrices"

# ✅ Fix — use natural language users actually type
description: Use when user wants to "write tests for my API", "generate test coverage",
  "create test files from my spec", or asks about API testing.
```

**Cause D: Folder name has underscores or capitals**

```bash
# ❌ Wrong folder names
~/.claude/skills/My_Skill/
~/.claude/skills/mySkill/
~/.claude/skills/my skill/

# ✅ Fix — rename to kebab-case
mv ~/.claude/skills/My_Skill ~/.claude/skills/my-skill
```

**Cause E: File is named README.md instead of SKILL.md**

```bash
# ❌ Wrong — Claude ignores README.md
~/.claude/skills/my-skill/README.md

# ✅ Fix — rename to SKILL.md
mv ~/.claude/skills/my-skill/README.md ~/.claude/skills/my-skill/SKILL.md
```

### Prevention

Run the audit script before every distribution:
```bash
bash ~/.claude/skills/claude-skill-builder/scripts/audit-skill.sh ~/.claude/skills/your-skill
```

---

## Problem 2: Skill Triggers Too Often

**Symptom**: The skill activates for requests it shouldn't handle — unrelated tasks, similar
but different domains, or requests meant for other skills.

### Diagnosis

Write negative test cases — phrases that should NOT trigger the skill — and test them:

```markdown
Negative test: "[Related phrase that SHOULD NOT trigger]"
Expected: Skill does NOT activate
Actual: [What actually happens]
```

### Common Causes and Fixes

**Cause A: Description too broad — matches too many topics**

```yaml
# ❌ Wrong — "help with code" matches almost everything
description: Use when user needs help with code.

# ✅ Fix — be specific about what type of code and what kind of help
description: Use when user wants to "generate API tests", "create test suites from OpenAPI specs",
  or needs help specifically with REST API test generation.
  Do NOT use for general code help, unit tests, or non-API testing.
```

**Cause B: Trigger phrases overlap with another skill's domain**

If two skills cover overlapping territory, the description must explicitly exclude the other skill's domain:

```yaml
description: Use when user wants to "generate API tests from OpenAPI spec", "create REST API tests",
  or needs help with endpoint test generation.
  Do NOT use for: unit tests, integration tests without an API spec, or frontend testing.
```

**Cause C: Single broad phrase instead of specific phrases**

```yaml
# ❌ Wrong — "test" matches test framework setup, test debugging, etc.
description: Use when user needs to test something.

# ✅ Fix — precise phrases
description: Use when user wants to "generate API test suite", "create tests from OpenAPI spec",
  or "automate API endpoint testing".
```

---

## Problem 3: Instructions Not Followed

**Symptom**: Claude activates the skill but ignores parts of SKILL.md — skipping steps,
using wrong output format, or omitting required elements.

### Diagnosis

1. Check if SKILL.md exceeds 3,000 words — long files cause attention drift
2. Check if the relevant instruction is buried deep in SKILL.md
3. Check if the instruction conflicts with another instruction

```bash
wc -w ~/.claude/skills/your-skill-name/SKILL.md
# If over 3,000 words, the file is too long — move content to references/
```

### Common Causes and Fixes

**Cause A: SKILL.md too long — content buried and skipped**

Move detailed content to `references/` files:
- Keep SKILL.md under 5,000 words (hard limit); ideally under 2,000 for good performance
- Put detailed schemas, policies, examples in `references/` files
- Add explicit pointers in SKILL.md: "For validation rules, see `references/validation.md`"

**Cause B: Critical instructions not prominent enough**

Move critical instructions to the top of SKILL.md, immediately after the Quick Start:

```markdown
## Critical Requirements (always check these)
- Output MUST use ISO 8601 dates (2026-03-05, not March 5)
- NEVER skip the validation step
- Always include rollback instructions when making destructive changes
```

**Cause C: Ambiguous instructions**

Replace abstract guidance with concrete imperatives:

```markdown
# ❌ Vague
Follow best practices for error handling.

# ✅ Concrete
On any error:
1. Print: "Error: [specific error message]"
2. Print: "Fix: [specific actionable step]"
3. Stop — do NOT continue to the next step
```

---

## Problem 4: Context Overload

**Symptom**: Claude becomes confused, contradicts itself, or loses track of earlier steps
during complex skill workflows. Most common in long sessions or skills with many steps.

### Causes

- SKILL.md is too long (entire file loaded into context at once)
- Multiple conflicting instructions in the same file
- No clear "current state" tracking for multi-step workflows

### Fixes

**Fix A: Aggressive progressive disclosure**

Move everything non-essential out of SKILL.md:

```markdown
# SKILL.md — only the essential workflow (short and complete is fine; hard limit: 5,000 words)

## Step 1: [What to do]
[3-5 lines max. If more is needed, add:] For details, see `references/step-1-details.md`.

## Step 2: [What to do]
...
```

**Fix B: Explicit state tracking in multi-step workflows**

For workflows where Claude must track what's been done:

```markdown
## State Tracking

After completing each step, output:
```
STEP [N] COMPLETE: [brief summary of what was done]
```

Before starting each step, output:
```
STARTING STEP [N]: [step name]
Previous: [reference to what Step N-1 produced]
```

This creates an explicit checkpoint log that stays visible in context.

**Fix C: Break into sub-skills**

If a skill has 8+ steps, consider splitting it into 2-3 focused sub-skills that chain:

```
Skill A: Generate tests (steps 1-3) → outputs test files
Skill B: Validate tests (steps 4-6) → takes test files as input
Skill C: Package and document (steps 7-8) → produces final distribution
```

---

## Diagnostic Checklist

When any of the above problems occur, work through this checklist in order:

```bash
# Step 1: Structural validation
bash ~/.claude/skills/claude-skill-builder/scripts/audit-skill.sh ~/.claude/skills/your-skill
# Fix all P0 (critical) issues before moving on

# Step 2: File size check
wc -w ~/.claude/skills/your-skill-name/SKILL.md
# Over 3,000 words? → Move content to references/

# Step 3: Frontmatter check
head -10 ~/.claude/skills/your-skill-name/SKILL.md
# Missing ---? → Add YAML frontmatter

# Step 4: Description check
python3 -c "
import re
content = open('~/.claude/skills/your-skill-name/SKILL.md').read()
m = re.search(r'^---\n(.*?)\n---', content, re.DOTALL)
if m:
    desc = re.search(r'description: (.*?)(\n\w|\Z)', m.group(1), re.DOTALL)
    if desc:
        print('Description length:', len(desc.group(1)))
        print('Has trigger phrases:', '\"' in desc.group(1))
"
# Description > 1024 chars? → Shorten it
# No quotes? → Add trigger phrases

# Step 5: Re-run triggering tests
# Fresh Claude Code session → type each trigger phrase → verify behavior
```

---

## Source

Anthropic, "The Complete Guide to Building Skills for Claude," January 2026, pages 24-26.
