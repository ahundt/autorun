# Refining Existing Skills - Complete Guide

**Source**: Anthropic's "The Complete Guide to Building Skills for Claude" (January 2026)
**PDF**: `../ai-skill-builder-guide.pdf`

This guide covers improving and modernizing existing Claude skills to match current best practices.

---

## When to Refine a Skill

### Signs Your Skill Needs Refinement

**Critical Issues** (fix immediately):
- ❌ File named `README.md` instead of `SKILL.md`
- ❌ Folder uses underscores (`my_skill`) or camelCase (`mySkill`)
- ❌ Missing YAML frontmatter
- ❌ Feature-focused description ("Uses X to Y")
- ❌ No progressive disclosure structure

**Quality Issues** (improve soon):
- ⚠️ Description too short to carry 4-8 quoted trigger phrases, or over the 1024-character limit
- ⚠️ No examples section
- ⚠️ Missing trigger phrases
- ⚠️ No success metrics
- ⚠️ Wall of text (no structure)

**Enhancement Opportunities** (nice to have):
- 💡 Could add automation scripts
- 💡 Missing reference documentation
- 💡 Could benefit from templates/assets
- 💡 Performance not measured

---

## Refinement Workflow

### Phase 1: Audit

**Step 1: Run Automated Audit**
```bash
bash ../scripts/audit-skill.sh ~/.claude/skills/YOUR-SKILL
```

**Step 2: Document Findings**

Create audit report:
```markdown
# Skill Audit Report - [Skill Name]

Date: [Current date]
Auditor: Claude
Skill Path: ~/.claude/skills/[skill-name]

## Critical Issues
- [ ] Issue 1: [Description]
- [ ] Issue 2: [Description]

## Quality Issues
- [ ] Issue 1: [Description]

## Enhancement Opportunities
- [ ] Opportunity 1: [Description]

## Score: X/100

## Next Steps
1. [Priority 1 fix]
2. [Priority 2 fix]
```

### Phase 2: Prioritize

**Priority Framework:**

**P0 - Critical (must fix)**:
- Incorrect file naming
- Missing required fields
- Broken skill detection

**P1 - High (should fix)**:
- Poor progressive disclosure
- Missing examples
- Vague descriptions

**P2 - Medium (nice to have)**:
- Additional documentation
- Automation scripts
- Performance optimizations

**P3 - Low (future enhancement)**:
- Visual assets
- Advanced features
- Edge case handling

### Phase 3: Implement Fixes

**For Critical Issues:**

**Issue**: Wrong filename
```bash
# If file is README.md or wrong case
# Claude uses Read + Write tools:

# 1. Read current content
Read: ~/.claude/skills/my-skill/README.md

# 2. Write to correct filename
Write: ~/.claude/skills/my-skill/SKILL.md
[Copy content]

# 3. Remove old file — use trash, not rm: rm destroys the only copy and its deletion time
Bash: trash ~/.claude/skills/my-skill/README.md
```

**Issue**: Wrong folder name
```bash
# Rename folder to kebab-case
mv ~/.claude/skills/my_old_skill ~/.claude/skills/my-old-skill
```

**Issue**: Missing YAML frontmatter
```markdown
# Add to top of SKILL.md:
---
name: skill-name
description: Generates X from Y. Use when user asks to "trigger phrase 1", "trigger phrase 2".
metadata:
  version: 0.1.0
---
```

**Issue**: Feature-focused description
```yaml
# ❌ Before:
description: Uses OpenAPI parser and Jinja2 to generate Jest tests

# ✅ After:
description: Generates Jest test suites from OpenAPI specs. Use when user asks to
  "generate API tests", "create a test suite from my spec", "write tests for my endpoints".
```

**For Quality Issues:**

**Issue**: No progressive disclosure

Claude will use Edit tool:
```markdown
# 1. Read current SKILL.md
Read: ~/.claude/skills/skill-name/SKILL.md

# 2. Edit to add structure using Edit tool
Edit: ~/.claude/skills/skill-name/SKILL.md
old_string: [current unstructured content]
new_string:
# Skill Name

[Level 1: Hook - 50-100 words]

---

## How It Works

[Level 2: Workflow - 200-400 words]

---

## Detailed Guide

[Level 3: Comprehensive]
```

**Issue**: No examples

Add examples section:
```markdown
## Examples

### Example 1: [Common Use Case]

**Scenario**: [Specific situation]

**Input**:
\`\`\`
[Actual input]
\`\`\`

**Output**:
\`\`\`
[Actual output]
\`\`\`

**Result**: [Outcome achieved]
```

### Phase 4: Test & Validate

**Re-run Audit**:
```bash
bash ../scripts/audit-skill.sh ~/.claude/skills/YOUR-SKILL
```

**Test Triggering**:
```
# Try exact trigger:
/your-skill-name

# Try natural language:
"Help me with [skill purpose]"

# Verify it doesn't trigger on unrelated:
"Something completely different"
```

**Functional Testing**:
1. Run through complete workflow
2. Verify outputs match documentation
3. Check error handling works
4. Validate edge cases

**Compare Metrics**:
```markdown
## Before vs After

Fill this table from your own measurements. The values below are placeholders, not results
from any measured skill.

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Audit score | [measured] | [measured] | [delta] |
| Trigger accuracy (passes / triggering tests run) | [measured] | [measured] | [delta] |
| Time to complete (median of 3 runs) | [measured] | [measured] | [delta] |
```

### Phase 5: Document Changes

**Update Version History**:
```markdown
## Version History

**v2.0.0** - 2026-02-15
- BREAKING: Renamed from my_old_skill to my-new-skill
- BREAKING: Changed trigger from /old to /new
- Added progressive disclosure structure
- Rewrote description to capability-plus-quoted-trigger-phrase format
- Added 3 concrete examples
- Audit score improved from [before] to [after]

**v1.0.0** - 2025-12-01
- Initial release
```

---

## Common Refinement Scenarios

### Scenario 1: Migrating Old Skill to New Standard

**Starting Point**: Skill created before Anthropic's guide (pre-2026)

**Migration Checklist**:
- [ ] Rename README.md → SKILL.md
- [ ] Fix folder name to kebab-case
- [ ] Add YAML frontmatter
- [ ] Convert description to capability-plus-quoted-trigger-phrase format
- [ ] Add progressive disclosure (3 levels)
- [ ] Add examples section
- [ ] Add success metrics
- [ ] Add version history
- [ ] Run audit script
- [ ] Test triggering and functionality

**Definition of Done**: the audit script reports no failures and triggering tests pass

**Example**:
```bash
# Before:
~/.claude/skills/API_Test_Gen/README.md
# No frontmatter
# Feature-focused description
# No structure

# After:
~/.claude/skills/api-test-generator/SKILL.md
---
name: api-test-generator
description: Generates Jest test suites from OpenAPI specs. Use when user asks to
  "generate API tests", "create a test suite from my spec".
---
# API Test Generator
[Progressive disclosure structure]
```

### Scenario 2: Improving Existing Good Skill

**Starting Point**: Skill follows basics but could be better

**Enhancement Checklist**:
- [ ] Audit with script
- [ ] Add concrete examples (if missing)
- [ ] Add automation scripts
- [ ] Add reference documentation
- [ ] Improve success metrics measurement
- [ ] Add troubleshooting section
- [ ] Enhance error messages
- [ ] Add related skills links

**Definition of Done**: every checklist item above is satisfied

### Scenario 3: Adding Automation to Manual Skill

**Starting Point**: Skill works but requires manual steps

**Automation Checklist**:
- [ ] Identify repetitive manual steps
- [ ] Create automation script(s)
- [ ] Add to scripts/ directory
- [ ] Update SKILL.md workflow
- [ ] Test automation end-to-end
- [ ] Document automation requirements
- [ ] Add rollback procedures

**Definition of Done**: the automation runs end to end and its failure path is documented

**Example**:
```bash
# Add automation script
Write: ~/.claude/skills/api-test-generator/scripts/generate.py

# Update SKILL.md to reference script
Edit: ~/.claude/skills/api-test-generator/SKILL.md
old_string: "3. Manually create test files"
new_string: "3. Run: python scripts/generate.py --spec openapi.yaml"
```

### Scenario 4: Splitting Overly Complex Skill

**Starting Point**: One skill doing too many things

**Splitting Strategy**:
1. **Identify distinct capabilities** (should be separate skills)
2. **Create new skills** for each capability
3. **Keep original as orchestrator** (if needed)
4. **Update documentation** with links to related skills

**Example**:
```markdown
# Original: api-automation (does everything)
→ Split into:
  - api-test-generator (testing)
  - api-docs-generator (documentation)
  - api-client-generator (client code)
  - api-automation (orchestrator - optional)
```

---

## Measuring Improvement

### Before/After Metrics

**Audit Scores**: run the script before and after, and keep both outputs. What moves is the
decided-check score and the FAIL count; proxy checks are heuristics and are reported separately,
not scored.

```bash
bash ../scripts/audit-skill.sh ~/.claude/skills/my-skill | tee before.txt
# ...apply fixes...
bash ../scripts/audit-skill.sh ~/.claude/skills/my-skill | tee after.txt
diff before.txt after.txt
```

**User Satisfaction** (gather feedback):
```markdown
Survey questions:
1. How easy was it to understand when to use this skill? (1-5)
2. How clear was the workflow? (1-5)
3. How helpful were the examples? (1-5)
4. Would you recommend this skill? (Yes/No)
5. What could be improved?
```

**Performance Metrics**:
- Time to complete task (before vs after)
- Error rate (failures/attempts)
- Adoption rate (usage growth)
- Support requests (reduction)

**Quality Indicators**:
- Progressive disclosure compliance (Yes/No)
- Example coverage (# of examples)
- Success metrics defined (Yes/No)
- Automated tests passing (%)

---

## Continuous Improvement

### Regular Maintenance Schedule

**Monthly**:
- Review skill usage analytics
- Collect user feedback
- Check for broken examples
- Update dependencies

**Quarterly**:
- Run audit script
- Review and update examples
- Improve documentation
- Add requested features

**Yearly**:
- Major version upgrade
- Align with latest best practices
- Comprehensive testing
- Performance optimization

### Feedback Loop

**Collect Feedback**:
1. User survey after skill usage
2. GitHub issues
3. Discord discussions
4. Support tickets

**Prioritize Improvements**:
```markdown
Impact vs Effort Matrix:

High Impact, Low Effort:
- Do immediately

High Impact, High Effort:
- Plan for next quarter

Low Impact, Low Effort:
- Do when time permits

Low Impact, High Effort:
- Defer or reject
```

**Implement & Measure**:
1. Make changes
2. Re-run audit
3. Test with users
4. Measure impact
5. Document learning

---

## Refinement Tools & Resources

### Automated Tools

**Audit Script**:
```bash
../scripts/audit-skill.sh
```

**Scaffolding** (for new structure):
```bash
../scripts/scaffold-skill.sh
```

### Manual Tools (Claude uses these)

**Read Tool**: Review current state
```
Read: ~/.claude/skills/skill-name/SKILL.md
```

**Edit Tool**: Make precise changes
```
Edit: ~/.claude/skills/skill-name/SKILL.md
old_string: [exact text to replace]
new_string: [new text]
```

**Write Tool**: Create new files
```
Write: ~/.claude/skills/skill-name/new-file.md
[content]
```

**Bash Tool**: File operations
```
Bash: mv old-name new-name
Bash: chmod +x script.sh
```

### Reference Materials

**Official Guide**:
- PDF: `../ai-skill-builder-guide.pdf`
- Extracted text: `ai-skill-builder-guide.md`

**Templates**:
- `examples/SKILL-template.md`

**Best Practices**:
- `best-practices.md`

---

## Quick Reference: Refinement Workflow

```
┌─────────────────────────────────────────────┐
│ 1. AUDIT                                    │
│    bash audit-skill.sh my-skill             │
│    Document findings                        │
└─────────────────────┬───────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────┐
│ 2. PRIORITIZE                               │
│    P0: Critical (file naming, etc)          │
│    P1: Quality (structure, examples)        │
│    P2: Enhancement (scripts, docs)          │
└─────────────────────┬───────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────┐
│ 3. FIX                                      │
│    Critical → Quality → Enhancements        │
│    Use Read/Edit/Write tools                │
└─────────────────────┬───────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────┐
│ 4. TEST                                     │
│    Re-run audit                             │
│    Test triggering                          │
│    Functional testing                       │
│    Measure metrics                          │
└─────────────────────┬───────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────┐
│ 5. DOCUMENT                                 │
│    Update version history                   │
│    Document improvements                    │
│    Share learnings                          │
└─────────────────────────────────────────────┘
```

---

## Examples of Real Refinements

### Example 1: README.md → SKILL.md Migration

**Before** (v1.0):
```
~/.claude/skills/test_gen/README.md
No frontmatter
One big paragraph description
No structure
```

**After** (v2.0):
```
~/.claude/skills/test-generator/SKILL.md
---
name: test-generator
description: Generates test suites from code analysis. Use when user asks to
  "generate tests", "write tests for this module".
---

# Test Generator

Generate test suites from code analysis...

## How It Works

1. Analyze code — done when every exported symbol is listed
2. Generate tests — done when each listed symbol has a test file
3. Validate — done when the suite parses and runs
```

**What changed**: file renamed so the host can find it, folder renamed to kebab-case,
frontmatter added with trigger phrases, workflow split into three steps that each state
when they are finished.

### Example 2: Adding Progressive Disclosure

**Before** (wall of text):
```markdown
This skill helps you deploy applications to production by first checking prerequisites then building docker images then pushing to registry then deploying to kubernetes then validating deployment then monitoring for issues and rolling back if needed. It supports multiple environments including dev staging and production. You can configure timeouts health checks and rollback thresholds...
```

**After** (structured):
```markdown
# Deploy to Production

Deploy containerized applications with automated validation and rollback.

**Use when:** Ready to ship to production
**Invoke with:** `/deploy-to-production`

---

## How It Works

### Step 1: Pre-flight Checks
- Validates prerequisites
- Checks environment health
- Done when: every prerequisite reports healthy

### Step 2: Build & Push
- Builds Docker image
- Pushes to registry
- Done when: the registry returns the pushed digest

### Step 3: Deploy & Validate
- Deploys to Kubernetes
- Validates health checks
- Done when: all pods are Ready, or rollback has completed

---

## Detailed Guide
[Comprehensive documentation...]
```

**What changed**: one 60-word paragraph became a 20-word hook, three named steps each stating
what must be true before the next one runs, and a pointer to the detailed guide. A reader can
now decide whether the skill applies without reading past the hook.

---

## Summary

**Key Principles**:
1. **Audit first** - Know what to improve
2. **Prioritize ruthlessly** - Fix critical issues first
3. **Test thoroughly** - Measure impact against the baseline you recorded
4. **Document changes** - Help future maintainers
5. **Iterate continuously** - Skills are never "done"

**Source**: Adapted from Anthropic's "The Complete Guide to Building Skills for Claude" (January 2026)
