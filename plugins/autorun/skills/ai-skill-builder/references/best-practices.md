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
---
```

### Extended — every optional field
```yaml
---
name: your-skill-name
description: What it does and when. Use when user says "trigger phrase 1", "trigger phrase 2".
license: MIT                   # Optional: open-source license (MIT, Apache-2.0, etc.)
allowed-tools: "Bash(python:*) WebFetch"  # Optional: restrict which tools the skill can use
compatibility: Claude Code     # Optional: 1-500 chars; environment requirements
metadata:                      # Optional: custom key-value pairs
  author: Your Name
  version: 1.0.0               # Version belongs inside metadata, never at the top level
  mcp-server: your-server      # If skill requires a specific MCP server
  category: productivity
  tags: [automation, workflow]
  dependencies: tool-name,mcp-server-name
  documentation: https://example.com/docs
---
```

`license`, `compatibility`, and `allowed-tools` are optional in the portable specification and
accepted differently by different hosts. `allowed-tools` is experimental and its accepted shape
varies. Name the host and version you tested rather than assuming portability.

### Description Field Constraints
- **Under 1024 characters** (hard limit — longer descriptions are truncated)
- **No XML angle brackets** (`<` or `>`) in any frontmatter field — a host restriction from
  Anthropic's skill guide and the Claude Code docs, not a YAML rule; the body must carry balanced
  semantic XML regions (SKILL-REQ004 in `SKILL.md`)
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
- Numbered items wherever order matters or a reader must cite one; bullets only for unordered sets
- Input → Output per step
- A Definition of Done per step: the checkable condition that must hold before the next one
  starts. Not a wall-clock estimate — how long a step takes depends on who or what runs it.

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

### Annotated (Claude Code standalone example)
```
~/.claude/skills/your-skill-name/
├── SKILL.md                         # Required — loaded when skill triggers (<5k words)
├── references/                      # Docs Claude loads into context as needed
│   ├── detailed-guide.md            #   schemas, API docs, policies, detailed workflows
│   └── examples/                    #   working code users copy (subdirectory of references/)
│       └── working-example.sh
├── scripts/                         # Executables (run without loading into context)
│   └── validate.sh
└── assets/                          # Files used IN skill output (not loaded to context)
    └── template.html
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

**Solution**: Name the outcome — in the repo README and in the SKILL.md body. The `description`
field is the one place this does not apply: there, outcome language displaces the trigger phrases
a host matches on. See "Two Audiences, Two Formats" above.
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

**Solution**: Test before releasing, in the order SKILL.md's Step 4 gives
```
1. Triggering tests
2. Functional tests
3. Performance tests
4. Compatibility tests (every named host and version)
5. Forward tests (realistic positive, negative, ambiguous, and adversarial prompts)
```
Then collect user feedback.

### Antipattern 6: The Unmeasured Rewrite
**Problem**: A revision adds material and no task outcome improves

**Solution**: Run the same tasks against the version being replaced; keep the revision only if it does them better
```
❌ "The audit passes and it reads better now"
✅ "Same 5 tasks, 4 correct before, 5 correct after"
```

## Success Criteria Patterns

The strings below are shapes to fill from your own measurements, not results anyone recorded.
A number earns its place once it names four things: the measured event, the workload and
conditions, the number with its unit, and which direction counts as better. Record the manual
baseline before building, so the improvement has something to be relative to —
`references/testing.md` covers the measurement framework.

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
- **YAML description**: 1-3 sentences, long enough to carry 4-8 quoted trigger phrases
- Focus: what the skill does, then the phrases users say. Use Format A or B above.
- Avoid: outcome-only positioning ("87% faster"). That belongs in the repo README.
- Hard limit 1024 characters. `scripts/audit-skill.sh` fails a description with no quoted phrases.

### Step Descriptions
- **Step title**: 3-5 words
- **Step description**: 20-50 words
- **Definition of Done**: one checkable condition

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
- [Claude Code skills documentation](https://code.claude.com/docs/en/skills)
- [MCP Protocol](https://modelcontextprotocol.io)
- [Agent Skills specification](https://agentskills.io/specification)

### Community
- [Anthropic on GitHub](https://github.com/anthropics)

### Tools
- Claude Code CLI
- MCP Inspector
- skill-creator (built-in)
