# Refining Existing Skills - Complete Guide

**Source**: Anthropic's "The Complete Guide to Building Skills for Claude" (January 2026)
**PDF**: `../claude-skill-builder-guide.pdf`

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
- ⚠️ Description over 50 words
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

### Phase 1: Audit (5-10 minutes)

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

### Phase 2: Prioritize (2-5 minutes)

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

### Phase 3: Implement Fixes (15-45 minutes)

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

# 3. Remove old file
Bash: rm ~/.claude/skills/my-skill/README.md
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
description: Outcome-focused one-sentence description
---
```

**Issue**: Feature-focused description
```yaml
# ❌ Before:
description: Uses OpenAPI parser and Jinja2 to generate Jest tests

# ✅ After:
description: Generate API test suites 75% faster than manual writing
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

### Phase 4: Test & Validate (10-20 minutes)

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

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Audit Score | 45% | 92% | +47pts |
| Trigger Accuracy | 60% | 95% | +35% |
| User Satisfaction | 3.2/5 | 4.7/5 | +47% |
| Time to Complete | 45min | 12min | -73% |
```

### Phase 5: Document Changes (5-10 minutes)

**Update Version History**:
```markdown
## Version History

**v2.0.0** - 2026-02-15
- BREAKING: Renamed from my_old_skill to my-new-skill
- BREAKING: Changed trigger from /old to /new
- Added progressive disclosure structure
- Improved description (outcome-focused)
- Added 3 concrete examples
- Audit score improved from 45% to 92%

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
- [ ] Convert description to outcome-focused
- [ ] Add progressive disclosure (3 levels)
- [ ] Add examples section
- [ ] Add success metrics
- [ ] Add version history
- [ ] Run audit script
- [ ] Test triggering and functionality

**Time**: 30-60 minutes

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
description: Generate API test suites 75% faster
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

**Time**: 15-30 minutes

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

**Time**: 45-90 minutes

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

**Audit Scores**:
```bash
# Before refinement
bash audit-skill.sh my-skill
Score: 45%

# After refinement
bash audit-skill.sh my-skill
Score: 92%
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
- PDF: `../claude-skill-builder-guide.pdf`
- Extracted content in SKILL.md

**Templates**:
- `../templates/SKILL-template.md`

**Best Practices**:
- `../references/best-practices.md`

**This Guide**:
- `../references/refining-skills.md`

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
description: Generate test suites 80% faster
---

# Test Generator

Generate comprehensive test suites from code analysis...

## How It Works

1. Analyze code (2 min)
2. Generate tests (3 min)
3. Validate (1 min)

Total: ~6 minutes vs 30 minutes manual
```

**Impact**:
- Audit score: 35% → 95%
- Usage: 2x increase
- Time saved: 80%

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

### Step 1: Pre-flight Checks (2 min)
- Validates prerequisites
- Checks environment health

### Step 2: Build & Push (5 min)
- Builds Docker image
- Pushes to registry

### Step 3: Deploy & Validate (3 min)
- Deploys to Kubernetes
- Validates health checks

**Total**: ~10 minutes with automated rollback

---

## Detailed Guide
[Comprehensive documentation...]
```

**Impact**:
- Comprehension: 40% → 95%
- Time to understand: 5min → 30sec
- Adoption: 3x increase

---

## Summary

**Refining existing skills is often more valuable than creating new ones.**

**Key Principles**:
1. **Audit first** - Know what to improve
2. **Prioritize ruthlessly** - Fix critical issues first
3. **Test thoroughly** - Measure impact
4. **Document changes** - Help future maintainers
5. **Iterate continuously** - Skills are never "done"

**Remember**: A well-refined skill following best practices is worth 10 poorly-structured ones.

**Source**: Adapted from Anthropic's "The Complete Guide to Building Skills for Claude" (January 2026)
