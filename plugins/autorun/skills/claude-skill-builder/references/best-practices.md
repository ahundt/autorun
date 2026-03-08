# Claude Skill Best Practices

Quick reference guide for building effective Claude skills based on Anthropic's official methodology.

## File Naming Rules (CRITICAL)

### ✅ Correct
- **File name**: `SKILL.md` (exactly, case-sensitive)
- **Folder name**: `api-test-generator` (kebab-case)
- **Location**: `~/.claude/skills/api-test-generator/SKILL.md`

### ❌ Incorrect
- ❌ `README.md` (Claude doesn't read this)
- ❌ `skill.md` (wrong case)
- ❌ `SKILL.MD` (wrong extension case on some systems)
- ❌ `api_test_generator` (underscores)
- ❌ `apiTestGenerator` (camelCase)
- ❌ `API-Test-Generator` (capitals in folder)

## YAML Frontmatter Requirements

### Minimal Required
```yaml
---
name: skill-name
description: This skill should be used when the user wants to "trigger phrase 1",
  "trigger phrase 2", or needs help with [domain].
version: 0.1.0
---
```

### Extended (Optional)
```yaml
---
name: skill-name
description: Does X for Y inputs. Use when user asks for "trigger phrase 1",
  "trigger phrase 2", or needs help with [domain].
version: 1.0.0
author: Your Name
tags: [category1, category2]
dependencies:
  - tool-name
  - mcp-server-name
---
```

### Description Field Constraints
- **Under 1024 characters** (hard limit — longer descriptions are truncated)
- **No XML angle brackets** (`<` or `>`) in any frontmatter field
- **Skill name must be kebab-case** (e.g., `my-skill`) — no spaces, no capitals

## Description Writing Formula

### Two Audiences, Two Formats

The `description` field in YAML frontmatter and a GitHub README.md serve different audiences
and require different language:

| Location | Audience | Purpose | Language style |
|----------|----------|---------|---------------|
| `description` field in SKILL.md | Claude (AI) | Auto-activation: pattern-match user queries | Trigger phrases |
| `README.md` at repo root | Humans | Installation decision: "should I install this?" | Outcome-focused |

### ❌ Wrong for description field — outcome-focused (misses trigger matching)
```yaml
description: Generate API test suites 87% faster than manual writing
```

### ✅ Correct for description field — trigger-phrase format
```yaml
# Format A (plugin-dev style):
description: This skill should be used when the user wants to "generate API tests",
  "create a test suite", "write tests for my endpoints", or needs help with API test generation.

# Format B (Anthropic PDF style — capability + triggers):
description: Generates API test suites from OpenAPI specs. Use when user asks for
  "generate API tests", "create a test suite from my spec", or "automate endpoint testing".
```

### ✅ Correct for README.md — outcome-focused (for human readers)
```markdown
## Why Use This Skill?
Generate production-ready API tests 87% faster than writing them manually.
```

### ❌ Wrong for description field — feature-focused
```yaml
description: Uses OpenAPI parser with Jinja2 templates to generate Jest tests
```

**Rule**: `description` field → what users SAY → trigger phrases. GitHub README → what users ACHIEVE → outcome-focused.

## Progressive Disclosure Levels

### Level 1: The Hook (50-100 words)
**Purpose**: Quick decision - "Is this for me?"

**Include**:
- Clear value proposition
- Target user
- Triggering scenario
- Exact trigger phrase

**Omit**:
- Technical details
- How it works internally
- Configuration options
- Edge cases

### Level 2: The Workflow (200-400 words)
**Purpose**: Understanding - "How does this work?"

**Include**:
- 3-5 numbered steps
- Time estimates per step
- Input → Output per step
- Total time comparison

**Omit**:
- Implementation details
- Error handling
- Advanced configuration
- Troubleshooting

### Level 3: Comprehensive (No limit)
**Purpose**: Reference - "How do I handle X?"

**Include**:
- Complete technical details
- All configuration options
- Error messages and solutions
- Edge cases and examples
- Advanced usage patterns

**Structure**:
1. Prerequisites
2. Detailed steps
3. Configuration
4. Error handling
5. Examples
6. Troubleshooting

## Skill Categories

### Category 1: Document & Asset Creation
**Pattern**: Input → Analysis → Generation → Output

**Examples**:
- Generate documentation from code
- Create test suites from specs
- Build diagrams from descriptions
- Generate reports from data

**Structure**:
```
Input Requirements → Analysis Phase → Generation Phase → Validation → Output
```

### Category 2: Workflow Automation
**Pattern**: Task → Orchestration → Execution → Validation

**Examples**:
- Deploy to production
- Run data pipelines
- Execute health checks
- Coordinate multi-step processes

**Structure**:
```
Pre-flight Checks → Sequential/Parallel Steps → Error Handling → Status Report
```

### Category 3: MCP Enhancement
**Pattern**: MCP Tools → Composition → Intelligence Layer → Enhanced Output

**Examples**:
- Combine database + API tools
- Add semantic search over filesystem
- Cache slow MCP operations
- Create composite MCP operations

**Structure**:
```
MCP Tool Discovery → Tool Composition → Add AI Layer → Return Results
```

## Folder Structure Patterns

### Minimal (Document Creation)
```
skill-name/
└── SKILL.md
```

### Standard (With Scripts)
```
skill-name/
├── SKILL.md
└── scripts/
    ├── generate.py
    └── validate.sh
```

### Complete (Full Featured)
```
skill-name/
├── SKILL.md
├── scripts/
│   ├── deploy.sh
│   └── rollback.sh
├── references/
│   ├── api-docs.md
│   └── examples.md
└── assets/
    ├── config.json
    └── template.yaml
```

## Testing Framework

### 1. Triggering Tests
**Purpose**: Verify Claude detects the skill correctly

```markdown
Test 1: Exact trigger
Input: "/skill-name"
Expected: Skill activates

Test 2: Natural language
Input: "Help me [task description]"
Expected: Skill activates

Test 3: Similar but wrong
Input: "[Related but different task]"
Expected: Skill does NOT activate
```

### 2. Functional Tests
**Purpose**: Verify skill works correctly

```markdown
Test 1: Happy path
Input: [Standard valid input]
Expected Output: [Correct result]
Success Criteria: [Measurable outcome]

Test 2: Edge case
Input: [Minimal/maximal/unusual input]
Expected Output: [Handled gracefully]
Success Criteria: [No errors, sensible result]

Test 3: Error case
Input: [Invalid input]
Expected Output: [Clear error message]
Success Criteria: [Helpful guidance provided]
```

### 3. Performance Tests
**Purpose**: Verify skill provides value

```markdown
Metric 1: Time
Baseline: [Manual time]
With Skill: [Automated time]
Improvement: [Percentage reduction]

Metric 2: Quality
Baseline: [Manual quality metric]
With Skill: [Automated quality metric]
Improvement: [Improvement description]

Metric 3: Consistency
Baseline: [Variation in manual process]
With Skill: [Standardization achieved]
Improvement: [Consistency improvement]
```

## Common Antipatterns

### Antipattern 1: The Wall of Text
**Problem**: Everything in one giant block

**Solution**: Use progressive disclosure
```
Level 1 (Hook) → Level 2 (Workflow) → Level 3 (Details)
```

### Antipattern 2: The Feature List
**Problem**: Describing what it has, not what it achieves

**Solution**: Focus on outcomes
```
❌ "Has integration with 5 APIs"
✅ "Sync data across 5 platforms automatically"
```

### Antipattern 3: The Assumption Trap
**Problem**: Assuming user knows context

**Solution**: State prerequisites explicitly
```
Prerequisites:
- Docker installed
- AWS credentials configured
- Node.js 18+
```

### Antipattern 4: The Mystery Box
**Problem**: No examples of actual usage

**Solution**: Include concrete examples
```
Example Input: [Actual input]
Example Output: [Actual output]
Result: [Outcome achieved]
```

### Antipattern 5: The Untested Skill
**Problem**: Publishing without validation

**Solution**: Test before releasing
```
1. Triggering tests
2. Functional tests
3. Performance tests
4. User feedback
```

## Success Criteria Patterns

### Quantitative Metrics
- **Time Reduction**: "75% faster than manual process"
- **Error Reduction**: "90% fewer deployment failures"
- **Cost Savings**: "Save $5K/month in manual work"
- **Scale Improvement**: "Handle 10x more requests"
- **Quality Increase**: "85% test coverage vs 60% manual"

### Qualitative Metrics
- **Consistency**: "Standardized across 12 teams"
- **Best Practices**: "Follows industry standards automatically"
- **Accessibility**: "Non-experts can use effectively"
- **Maintainability**: "Reduced code complexity by 40%"
- **Reliability**: "Zero-downtime deployments"

## Distribution Checklist

### GitHub Repository
- [ ] Clear README with installation steps
- [ ] LICENSE file (MIT recommended)
- [ ] Example use cases documented
- [ ] Screenshots/GIFs if applicable
- [ ] CHANGELOG for version tracking

### Documentation
- [ ] Installation guide tested on clean system
- [ ] Prerequisites clearly listed
- [ ] Common issues documented
- [ ] Example usage included
- [ ] Support channel identified

### Community
- [ ] Announcement in Claude Discord
- [ ] Post in relevant forums/communities
- [ ] Blog post or tutorial (optional)
- [ ] Response plan for issues/questions

### Maintenance
- [ ] Issue tracking enabled
- [ ] Update plan defined
- [ ] Support commitment stated
- [ ] Deprecation path considered

## Quick Reference Commands

### Create New Skill
```bash
mkdir -p ~/.claude/skills/my-new-skill
cd ~/.claude/skills/my-new-skill
touch SKILL.md
```

### Validate Structure
```bash
# Check file exists
ls ~/.claude/skills/my-skill/SKILL.md

# Check frontmatter
head -5 ~/.claude/skills/my-skill/SKILL.md
```

### Test Skill Discovery
```bash
# Restart Claude Code
# Then try trigger phrase
/my-skill
```

## Word Count Targets

### Skill Sections
- **Level 1 Hook**: 50-100 words
- **Level 2 Workflow**: 200-400 words
- **Level 3 Details**: No limit (comprehensive)

### Description Field
- **YAML description**: 1-2 sentences (15-30 words)
- Focus: Outcome achieved
- Avoid: Technical implementation details

### Step Descriptions
- **Step title**: 3-5 words
- **Step description**: 20-50 words
- **Step time**: Estimate in minutes

## Version Control Tips

### Semantic Versioning
- **v1.0.0**: Initial release
- **v1.1.0**: New features (backward compatible)
- **v1.0.1**: Bug fixes
- **v2.0.0**: Breaking changes

### Changelog Format
```markdown
## v1.1.0 - 2026-02-15

### Added
- New configuration option for custom templates
- Support for Python 3.12

### Fixed
- Error handling for missing dependencies
- Typo in step 3 instructions

### Changed
- Improved performance by 25%
```

## Resources

### Official
- [Anthropic Claude Docs](https://docs.anthropic.com)
- [MCP Protocol](https://modelcontextprotocol.io)
- [Agent Skills Standard](https://agent-skills.dev)

### Community
- [Claude Discord](https://discord.gg/claude)
- [GitHub Discussions](https://github.com/anthropics)

### Tools
- Claude Code CLI
- MCP Inspector
- skill-creator (built-in)
