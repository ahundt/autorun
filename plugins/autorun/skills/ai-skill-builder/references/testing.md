# Skill Testing Guide

Three-phase testing approach from Anthropic's Complete Guide. Run all three phases before publishing.

---

## The Testing Triangle

```
             Manual Testing
             (quick feedback)
                   /\
                  /  \
                 /    \
                /      \
    Scripted ──────────── Programmatic
     Testing              Testing
  (repeatable)        (comprehensive)
```

Start manual for concept validation, add scripted for automation, build programmatic for coverage.

---

## Phase 1: Triggering Tests

**Goal**: Confirm Claude activates the skill when it should and ignores it when it shouldn't.

### Test Structure

```markdown
## Triggering Tests for [skill-name]

### Tests that MUST activate the skill
Test T1: Slash command
  Phrase: "/skill-name"
  Expected: Skill activates and begins its workflow

Test T2: Primary natural language trigger
  Phrase: "[most common way users will ask]"
  Expected: Skill activates

Test T3: Alternate phrasing
  Phrase: "[different way to express same intent]"
  Expected: Skill activates

Test T4: Partial match (critical edge case)
  Phrase: "[phrase with one trigger word but different intent]"
  Expected: Skill DOES activate (if in scope) or DOES NOT (if out of scope)

### Tests that MUST NOT activate the skill
Test T5: Related but out-of-scope
  Phrase: "[task related to the domain but not what this skill covers]"
  Expected: Skill does NOT activate

Test T6: Completely unrelated
  Phrase: "Help me write a poem"
  Expected: Skill does NOT activate
```

### How to Run Triggering Tests

1. Start a fresh Claude Code session
2. Type each phrase exactly as written
3. Observe whether the skill loads and responds as expected
4. Document any unexpected behavior

### When Triggering Fails

**Skill doesn't activate on expected phrases:**
- Trigger phrases in `description` field are too vague or too technical
- Users' natural language doesn't match the phrases in `description`
- Fix: Add more natural phrasing to the `description` field; see `references/troubleshooting.md`

**Skill activates on unexpected phrases:**
- `description` field is too broad; captures requests meant for other skills
- Fix: Add "Do NOT use for..." guidance in `description`; see `references/troubleshooting.md`

---

## Phase 2: Functional Tests

**Goal**: Validate the skill's core workflow produces correct, complete output for known inputs.

### Test Structure

```markdown
## Functional Tests for [skill-name]

### Test F1: Happy path (typical case)
  Input: [Standard, valid input — describe exactly]
  Steps triggered:
    1. [What Claude should do first]
    2. [What Claude should do second]
    3. [etc.]
  Expected output:
    - [Specific artifact or response expected]
    - [Any files created or modified]
  Success criteria:
    - [ ] [Specific verifiable criterion 1]
    - [ ] [Specific verifiable criterion 2]

### Test F2: Minimal input (edge case — least possible input)
  Input: [Smallest valid input — e.g., 1 endpoint, empty spec, no options]
  Expected output: [Correctly handled minimal case]
  Success criteria:
    - [ ] No errors or crashes
    - [ ] Output is valid even if minimal

### Test F3: Maximal input (stress case)
  Input: [Largest realistic input — e.g., 50 endpoints, full config, all options set]
  Expected output: [Complete output for full input]
  Success criteria:
    - [ ] All inputs processed (none silently dropped)
    - [ ] Performance remains acceptable

### Test F4: Error input (invalid/missing data)
  Input: [Invalid or malformed input]
  Expected output: [Clear error message with actionable guidance]
  Success criteria:
    - [ ] No cryptic failure or silent error
    - [ ] User knows what to fix and how
```

### Concrete Example: API Test Generator

```markdown
Test F1: Complete spec with 10 endpoints
  Input: OpenAPI spec with 10 GET/POST/PUT/DELETE endpoints, JWT auth
  Expected output:
    - 10 test files in ./tests/ directory
    - Each file has tests for happy path + at least 1 error case
    - Authentication setup and teardown included
  Success criteria:
    - [ ] All 10 endpoints have test files
    - [ ] Tests are syntactically valid (run: jest --listTests)
    - [ ] Auth configuration is correct

Test F2: Single endpoint, no auth
  Input: OpenAPI spec with 1 GET endpoint, no authentication
  Expected output:
    - 1 test file with basic request/response tests
    - No auth-related code generated
  Success criteria:
    - [ ] Test file created without auth blocks
    - [ ] No missing-auth errors

Test F3: Invalid OpenAPI spec
  Input: Malformed YAML (missing required 'paths' key)
  Expected output: Error message identifying the problem
  Success criteria:
    - [ ] Error message mentions the specific missing field
    - [ ] Suggestions provided for how to fix
```

---

## Phase 3: Performance Tests

**Goal**: Measure whether the skill provides concrete value compared to the manual baseline.

### Measurement Framework

Before testing, establish the manual baseline:

| Metric | How to measure | Baseline value |
|--------|---------------|----------------|
| Time | Stopwatch; average 3 runs of the manual process | [X] minutes |
| Quality | Count errors, coverage %, or specific quality metric | [X]% |
| Consistency | Run 3 people through same task; count variations | [X] variations |

After testing with the skill:

| Metric | Manual baseline | With skill | Improvement |
|--------|----------------|------------|-------------|
| Time | X minutes | Y minutes | (X-Y)/X × 100% reduction |
| Quality | X% | Y% | Y-X percentage points |
| Consistency | X variations | Y variations | (X-Y)/X × 100% reduction |

### Concrete Example: API Test Generator

```markdown
Performance Test P1: Time efficiency
  Task: Generate tests for a 20-endpoint REST API with JWT auth
  Manual baseline: 3 hours (developer writes tests from scratch)
  With skill: 20 minutes (parse spec → generate → validate)
  Improvement: 89% time reduction

Performance Test P2: Test coverage quality
  Task: Same 20-endpoint API
  Manual baseline: 65% path coverage (developers miss edge cases)
  With skill: 85% path coverage (spec-driven, systematic)
  Improvement: +20 percentage points

Performance Test P3: Consistency across team
  Task: 3 developers generate tests for same 5-endpoint API
  Manual baseline: 3 different test structures, 2 missing auth tests
  With skill: Identical structure, all auth tests present
  Improvement: 100% standardization
```

### When to Accept Performance Results

A skill is ready to distribute when it achieves at least **two** of:
- ≥50% time reduction compared to manual baseline
- ≥10 percentage points quality improvement
- Substantially higher consistency than manual process
- Enables work that was previously infeasible manually

---

## Collecting User Feedback

After passing triggering and functional tests, test with 1-2 representative users:

**Feedback session structure (30-45 minutes):**
1. Give the user a real task (not a test scenario) — 20 minutes
2. Observe without helping — note where they hesitate or struggle
3. Ask: "What was unclear?", "What did you expect that didn't happen?", "What would make this more useful?" — 10 minutes
4. Document and prioritize feedback

**Do not publish until:**
- [ ] Triggering tests: all pass
- [ ] Functional tests: happy path + edge cases pass
- [ ] Performance: measurable improvement in at least 2 metrics
- [ ] User feedback: no P0 or P1 usability issues remaining
