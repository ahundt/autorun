# Advanced Skill Patterns

Five advanced patterns from Anthropic's "The Complete Guide to Building Skills for Claude"
(January 2026, Chapter 5). Apply these when the basic 4-phase workflow is insufficient.

For fixing broken skills, see `references/troubleshooting.md`.

---

## Pattern 1: Sequential Workflow Orchestration

**When to use**: Tasks with 3+ phases where each phase's output feeds the next, and failures
at any phase must halt the entire workflow.

### Structure

```
Phase 1 → validate output → Phase 2 → validate output → Phase 3 → final report
               ↓                           ↓
           halt + error               halt + error
```

### Implementation

```markdown
## Workflow Phases

### Phase 1: [Name]
**Input**: [What this phase receives]
**Process**: [What it does]
**Output**: [What it produces]
**Validation gate**: [What must be true before proceeding to Phase 2]
**On failure**: Stop. Report: "[Phase 1 failed: specific reason]. Fix [X] and retry."

### Phase 2: [Name]
**Input**: Phase 1 output ([specific artifact])
**Process**: [What it does]
**Output**: [What it produces]
**Validation gate**: [What must be true before proceeding to Phase 3]
**On failure**: Stop. Report: "[Phase 2 failed]. Phase 1 output preserved at [location]."

### Final Report Format
- ✅ Phase 1: [Result summary]
- ✅ Phase 2: [Result summary]
- ✅ Phase 3: [Result summary]
- Total: [Completion metric]
```

### Example: Database Migration Skill

```
Parse migration file → validate schema → apply changes → verify row counts → generate report
         ↓                    ↓                ↓
     syntax error         type mismatch    rollback + error
```

Each phase reports its status before proceeding. If Phase 3 fails, Phase 1 and 2 results
are preserved and a rollback path is provided.

---

## Pattern 2: Multi-MCP Coordination

**When to use**: Skills that need data or operations from 2+ MCP servers, where the servers
have different availability, latency, or reliability profiles.

### Design Principles

1. **Declare dependencies explicitly**: List required vs optional MCP servers in SKILL.md
2. **Degrade gracefully**: If an optional server is unavailable, continue without it
3. **Cache aggressively**: MCP calls can be slow; cache results that are stable across requests
4. **Handle partial failures**: One server failing should not fail the entire workflow

### Implementation in SKILL.md

```markdown
## MCP Server Requirements

### Required (skill cannot function without these)
- **[server-name]**: [What it provides]
  Install: [installation command]
  Test: [verification command]

### Optional (skill degrades gracefully without these)
- **[server-name]**: [What it adds when available]
  Without it: [what the skill does instead]

## Coordination Logic

To execute [operation]:
1. Check which servers are available (use list_tools or equivalent)
2. If [required-server] unavailable: report error and stop
3. If [optional-server] unavailable: note in output, continue without [feature]
4. Execute [primary operation] via [required-server]
5. If [optional-server] available: enrich result with [additional data]
6. Merge results and respond
```

### Example: Smart File Search Skill

```markdown
## MCP Server Requirements

### Required
- **filesystem**: Read file contents and directory structure

### Optional
- **semantic-search**: Find conceptually related files (not just text matches)
  Without it: falls back to recursive text search with Grep

## Coordination Logic

To search for [query]:
1. Always: use filesystem to get directory tree
2. Always: use Grep for exact text matches
3. If semantic-search available: augment with semantic matches, rank by relevance
4. If not: return exact matches with helpful note about semantic search
```

---

## Pattern 3: Iterative Refinement

**When to use**: Skills that produce output requiring validation and revision cycles, where the
first attempt is rarely the final result.

### Structure

```
Generate draft → validate against criteria → if pass: done
                        ↓
                    if fail: identify gaps → refine → re-validate (max N iterations)
                                                              ↓
                                              if still failing after N: report with partial result
```

### Implementation

```markdown
## Refinement Loop

To generate [output]:

### Attempt 1: Initial generation
Generate [output] from [input].

### Validation checkpoint
Check [output] against:
- [ ] Criterion 1: [specific, measurable check]
- [ ] Criterion 2: [specific, measurable check]
- [ ] Criterion 3: [specific, measurable check]

If all pass: deliver output.
If any fail: identify which criteria failed, proceed to refinement.

### Refinement (up to 2 iterations)
For each failed criterion:
- Explain what specifically failed
- Generate targeted fix for that criterion only
- Re-validate the fixed criterion

### Final delivery
If all criteria pass after refinement: deliver with note about iterations taken.
If criteria still fail after 2 iterations: deliver best result with explicit gaps noted.
Never silently deliver output that failed validation.
```

---

## Pattern 4: Context-Aware Tool Selection

**When to use**: Skills that need to choose between multiple approaches based on the user's
environment, preferences, or constraints detected at runtime.

### Design

Rather than one rigid workflow, provide conditional branches based on detected context:

```markdown
## Context Detection

Before starting, determine:
1. **Language/framework**: Detect from package.json, requirements.txt, go.mod, etc.
2. **Project structure**: Detect from directory layout
3. **Existing conventions**: Read existing files in the output directory to match style
4. **User preferences**: Check if user specified any in their request

## Workflow Selection

Based on detected context, choose the appropriate path:

| Detected context | Workflow to use |
|-----------------|-----------------|
| package.json with Jest | `references/examples/jest-workflow.md` |
| requirements.txt with pytest | `references/examples/pytest-workflow.md` |
| go.mod present | `references/examples/go-test-workflow.md` |
| No detection possible | Ask user: "What test framework do you prefer?" |

Never assume context. If detection is ambiguous, ask rather than guess.
```

---

## Pattern 5: Domain Intelligence Layer

**When to use**: Skills operating in a specific domain (finance, healthcare, legal, security)
where domain rules, terminology, and compliance requirements affect every decision.

### Structure

Domain knowledge lives in `references/` files, not in SKILL.md:

```
SKILL.md              → workflow steps (domain-agnostic procedure)
references/domain.md  → domain rules, terminology, compliance requirements
references/schema.md  → domain-specific data structures
references/examples/  → validated domain-specific examples
```

### Implementation

```markdown
## Domain Context

Before executing [operation], load domain context from `references/domain.md`.

Key domain rules that affect this workflow:
- [Rule 1]: [How it affects the output]
- [Rule 2]: [How it affects the output]
- [Compliance requirement]: [What must always be true in the output]

Apply these rules at [specific step in workflow]. If a rule conflicts with the user's request,
explain the conflict and ask for clarification rather than silently applying or ignoring the rule.
```

### Example: Finance Skill

```markdown
## Domain Context

Before generating any financial analysis, load `references/finance-rules.md`.

Domain constraints:
- Currency: Always specify ISO 4217 code (USD, EUR, not $ or €)
- Dates: Always ISO 8601 (2026-03-05, not March 5, 2026)
- Amounts: Always use integer cents internally, format as decimals for display
- Disclosures: Any forward-looking statements must include disclaimer from `references/disclosures.md`
```

---

## Combining Patterns

Patterns compose. A single skill can use multiple patterns:

**Example: Enterprise Deployment Skill**
- Pattern 1 (Sequential): Deploy → smoke test → notify team → update runbook
- Pattern 4 (Context-aware): Different steps for AWS vs GCP vs on-prem
- Pattern 5 (Domain): Follow company-specific deployment policy from `references/policy.md`

Add each pattern's section to SKILL.md separately, clearly labeled, so Claude can load only
the relevant patterns for a given request.

---

## Source

Anthropic, "The Complete Guide to Building Skills for Claude," January 2026, Chapter 5 (pages 21-26).
