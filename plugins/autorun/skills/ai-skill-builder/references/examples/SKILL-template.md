---
name: your-skill-name-here
description: This skill should be used when the user wants to "trigger phrase 1",
  "trigger phrase 2", or needs guidance on [domain]. Brief capability summary.
metadata:
  version: 0.1.0
---

# Skill Name

<purpose>

[LEVEL 1: THE HOOK - 50-100 words]

Brief introduction that includes:
- Clear value proposition (what outcome does this achieve?)
- Who it's for (target users)
- When to use it (triggering scenarios)

To start: [first action to take]
**Invoke with:** `/your-skill-name` or ask about [trigger scenario]

</purpose>

<workflow>

## How It Works

[LEVEL 2: THE WORKFLOW - 200-400 words]

Brief overview of the process in 3-5 clear steps:

### Step 1: [Phase Name]
- What happens in this step
- What inputs are needed
- What outputs are produced
- Definition of Done: [what must be true before Step 2 starts]

### Step 2: [Phase Name]
- What happens in this step
- What inputs are needed
- What outputs are produced
- Definition of Done: [what must be true before Step 3 starts]

### Step 3: [Phase Name]
- What happens in this step
- What inputs are needed
- What outputs are produced
- Definition of Done: [what must be true before the skill reports success]

**Definition of Done for the whole workflow**: [the checkable end state]

---

## Detailed Workflow

[LEVEL 3: COMPREHENSIVE DOCUMENTATION - No word limit]

### Prerequisites
- Required tools/dependencies
- Required knowledge/skills
- Required access/permissions

### Complete Step-by-Step Guide

#### Step 1: [Detailed Phase Name]

**Purpose**: [Why this step matters]

**Process**:
1. [Detailed sub-step 1]
2. [Detailed sub-step 2]
3. [Detailed sub-step 3]

**Inputs**:
- Input 1: [Description, format, example]
- Input 2: [Description, format, example]

**Outputs**:
- Output 1: [Description, format, example]
- Output 2: [Description, format, example]

**Common Issues**:
- Issue 1: [Problem and solution]
- Issue 2: [Problem and solution]

#### Step 2: [Continue for all steps...]

### Configuration Options

**Option 1: [Name]**
- Description: [What it does]
- Default: [Default value]
- Valid values: [Acceptable inputs]
- Example: `[Example usage]`

### Error Handling

**Error 1: [Error name/message]**
- Cause: [Why this happens]
- Solution: [How to fix it]
- Prevention: [How to avoid it]

### Advanced Usage

[Optional advanced features, edge cases, power user tips]

</workflow>

<examples>

## Examples

### Example 1: [Common Use Case]

**Scenario**: [Describe the situation]

**Input**:
```
[Actual input example]
```

**Process**:
1. [What happens step by step]
2. [With actual values/outputs]

**Output**:
```
[Actual output example]
```

**Result**: [Outcome achieved]

### Example 2: [Edge Case]

[Repeat structure]

</examples>

<success_criteria>

## Success Metrics

### Quantitative
- [Metric 1]: [Baseline] → [With skill] ([Improvement %])
- [Metric 2]: [Baseline] → [With skill] ([Improvement %])

### Qualitative
- [Quality aspect 1]: [How it improves]
- [Quality aspect 2]: [How it improves]

</success_criteria>

<troubleshooting>

## Troubleshooting

### Common Issues

**Issue**: [Problem description]
- **Symptoms**: [How you know this is happening]
- **Cause**: [Root cause]
- **Solution**: [Step-by-step fix]
- **Prevention**: [How to avoid in future]

</troubleshooting>

<resources>

## Resources

### Documentation
- [Link to relevant docs]
- [Link to API references]

### Examples
- [Link to example projects]
- [Link to sample outputs]

### Community
- [Link to support channels]
- [Link to issue tracker]

---

## Changelog

Record the version in `metadata.version` above and the release history in
`references/changelog.md`. Readers using the skill do not need it; readers changing it do.

---

## License & Attribution

[License information if applicable]
[Attribution to sources, tools, or inspirations]

</resources>

<!-- Every major section above sits inside a balanced, descriptive XML region
     (purpose, workflow, examples, success_criteria, troubleshooting, resources).
     Rename regions to fit the task; a `## ` heading outside every region fails
     scripts/audit-skill.sh. -->
