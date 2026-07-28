# Guided Discovery — Interactive Skill Requirements Q&A

Use this 20-question guide when starting a new skill from scratch or when requirements are unclear.
Work through each phase in order. Skip a question only when it is clearly not applicable.

---

## Phase 1: Discovery (Questions 1–5)

**Purpose**: Understand the problem and who benefits.

**1. What problem does this skill solve?**

Ask for a concrete description of the pain point. Avoid abstract answers.

- ✅ "Writing API test suites takes 2-3 hours per endpoint and our team does 50 new endpoints per sprint"
- ❌ "Testing is hard and takes time"

Follow up: "What do you do today instead of using this skill?"

**2. Who will use it?**

Be specific about the user role and technical level.

- Developers (what language/stack?)
- Designers (what tools?)
- Data scientists (what workflow?)
- Non-technical users (what context?)

Follow up: "What would a typical user already know before using this skill?"

**3. What are 2-3 concrete use cases?**

Concrete = you could watch someone do it. Gather actual examples.

For each use case, capture:
- What triggers this task
- What inputs the user provides
- What outputs they receive
- How they use the output

**4. What does success look like?**

Set measurable success criteria before writing a line of SKILL.md.

| Metric type | Example |
|-------------|---------|
| Time reduction | "From 2 hours to 15 minutes per API" |
| Error reduction | "90% fewer missing test cases" |
| Consistency | "Same structure across all 12 teams" |
| Accessibility | "Junior devs can do it without help" |

**5. Which skill category best fits?**

Review `references/skill-categories.md` and choose:

- **Category 1**: Primary output is a document or code artifact
- **Category 2**: Primary output is a completed multi-step process
- **Category 3**: Primary output enhances or orchestrates MCP tools

---

## Phase 2: Design (Questions 6–10)

**Purpose**: Translate use cases into skill structure.

**6. What is the skill name?**

Rules:
- kebab-case only: `api-test-generator` not `apiTestGenerator` or `api_test_generator`
- Describes function, not features: `meeting-notes-summarizer` not `gpt4-enhanced-transcriber`
- No brand names unless truly required

**7. What trigger phrases will users say?**

The `description` field in YAML frontmatter is what Claude pattern-matches against user queries.
Collect 4-8 trigger phrases users would naturally say when they need this skill.

Examples for an API test generator:
- "Generate API tests from my OpenAPI spec"
- "Create a test suite for my REST API"
- "Write tests for all my endpoints"
- "Help me generate test coverage for my API"

**8. What are the main workflow steps?**

Break the skill into 3-5 phases. For each phase:
- Name (action verb + noun): "Parse Spec", "Generate Tests", "Validate Output"
- Time estimate: 1-2 min, 5-10 min, etc.
- What goes in, what comes out

**9. What inputs does the skill need from the user?**

List every piece of information the skill needs:

| Input | Required? | Format | Example |
|-------|-----------|--------|---------|
| OpenAPI spec | Yes | YAML/JSON file path | `./api-spec.yaml` |
| Test framework | No (default: Jest) | String | `pytest` |
| Output directory | No (default: `./tests/`) | Path | `./src/__tests__/` |

**10. What outputs does the skill produce?**

Describe the output artifacts:
- File type and location
- Content structure
- How the user will use each output

---

## Phase 3: Implementation (Questions 11–14)

**Purpose**: Decide what goes in each directory.

**11. What scripts should be bundled?**

Scripts are appropriate when:
- The same code would be rewritten every time the skill is used
- The operation is deterministic and can be tested independently
- The script runs faster via CLI than via Claude-generated code

Examples: validators, generators, format converters, test runners.

**12. What reference documentation should be bundled?**

References are appropriate when:
- There is a schema, API spec, or policy that Claude needs to consult
- The documentation is large enough to justify keeping out of SKILL.md
- The information changes independently of the workflow

Examples: database schemas, API documentation, company policies, domain knowledge.

**13. What examples should be bundled?**

Examples go in `references/examples/` and are appropriate when:
- There is a template file users will copy and adapt
- A complete working example helps Claude understand the expected output format
- Real-world samples prevent ambiguity about what "correct" looks like

**14. What assets should be bundled?**

Assets go in `assets/` and are appropriate when:
- The skill produces output that includes non-text files (images, fonts, icons)
- There is an HTML/React boilerplate the skill pastes into output
- There is a binary template (PowerPoint, PDF) the skill modifies

---

## Phase 4: Testing (Questions 15–18)

**Purpose**: Define testable criteria before writing SKILL.md.

**15. What phrases should trigger the skill?**

Write the exact triggering test cases:

```markdown
Test 1: Slash command trigger
Phrase: "/your-skill-name"
Expected: Skill activates

Test 2: Natural language trigger (most common form)
Phrase: "[most natural way users will ask]"
Expected: Skill activates

Test 3: Closely related but different request
Phrase: "[similar but out-of-scope request]"
Expected: Skill does NOT activate
```

**16. What defines a passing functional test?**

For each use case from Question 3, write:

```markdown
Test: [Use Case Name]
Input: [Exact input provided]
Expected output: [Specific, verifiable result]
Success criteria: [How to confirm it worked]
```

**17. What performance baseline are you improving on?**

Measure before building:

| Metric | Manual baseline | Target with skill |
|--------|----------------|-------------------|
| Time | [How long does it take today?] | [Target time] |
| Quality | [Current error rate/coverage?] | [Target metric] |
| Consistency | [How variable is manual process?] | [Standardized metric] |

**18. Who will provide user feedback before release?**

Identify 1-2 people who represent the target user:
- Who will test the skill in a real scenario
- What format feedback will be collected (written, meeting, async)
- What criteria must pass before release

---

## Phase 5: Distribution (Questions 19–22)

**Purpose**: Prepare for sharing and maintenance.

**19. Where will the skill live for distribution?**

- GitHub repository (recommended): `github.com/username/skill-name`
- Internal company repository
- Local only (no distribution planned)

**20. What does the GitHub README need?**

The repo-level README.md (OUTSIDE the skill folder) should include:
- Outcome-focused positioning: "Generate tests 87% faster" (this is marketing, not SKILL.md)
- Installation instructions (clone-to-install pattern)
- Compatibility requirements (Claude Code version, MCP servers, etc.)
- Example use cases with screenshots if applicable

**21. What is the initial version number?**

Use semantic versioning:
- `0.1.0`: Early/experimental skill, API may change
- `1.0.0`: Stable skill, ready for broad use
- `1.x.0`: New features, backward compatible
- `2.0.0`: Breaking changes to inputs or outputs

**22. What is the support plan?**

Before releasing publicly:
- GitHub issues enabled?
- Response time expectation (days, weeks, best-effort)?
- Who owns updates if requirements change?

---

## Discovery Summary Template

After completing all 22 questions, fill in this template to confirm you have everything needed:

```yaml
skill_plan:
  name: "your-skill-name"                    # kebab-case, from Q6
  category: "Category N: [Name]"             # from Q5

  trigger_phrases:                           # from Q7 (4-8 phrases)
    - "natural language phrase 1"
    - "natural language phrase 2"

  use_cases:                                 # from Q3 (2-3 cases)
    - "Case 1: [concrete scenario]"
    - "Case 2: [concrete scenario]"

  success_criteria:                          # from Q4
    quantitative: "Reduce X from Y to Z"
    qualitative: "[Quality improvement]"

  inputs:                                    # from Q9
    required: ["input1", "input2"]
    optional: ["input3 (default: value)"]

  outputs:                                   # from Q10
    - "output1: [file/format/location]"

  bundled_resources:                         # from Q11-14
    scripts: ["name: purpose"]
    references: ["name: content description"]
    examples: ["name: what it shows"]
    assets: ["name: what it is"]

  testing:                                   # from Q15-17
    trigger_test: "Exact phrase that must activate skill"
    negative_test: "Phrase that must NOT activate skill"
    functional_test: "Concrete input → expected output"
    performance_baseline: "Manual: X min → Target: Y min"

  distribution:
    repo: "github.com/username/skill-name"
    version: "0.1.0"
```
