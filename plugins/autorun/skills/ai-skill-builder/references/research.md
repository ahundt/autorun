# Research Strategies for Skill Building

Skills encode procedures at scale — every user who triggers a skill follows its
instructions, confidently. Outdated or incorrect guidance is worse than none.
Research cost is paid once; error cost is paid on every invocation.

---

## Tools

| Tool | When to use |
|------|------------|
| `WebSearch` | Find current best practices, compare approaches, check community consensus |
| `WebFetch` | Read official documentation, RFCs, API references, changelogs |
| `Read` | Read local docs, existing code, config files in the user's project |
| `Bash` | Run the tool being documented to observe actual behavior directly |

---

## Research Strategies by Domain

Skills can cover any domain. The research approach follows what you're investigating.

### Tool or API

The tool's own behavior is ground truth; everything else is secondary.

- Current version's docs and changelog — behavior changes between versions
- The actual installed version (`Bash: tool --version`, then test it)
- Known failure modes: what does the community get wrong most often?

```
WebSearch: "jest 29 snapshot testing pitfalls 2025"
WebFetch:  https://jestjs.io/docs/29.x/snapshot-testing
Bash:      jest --version
```

### Domain Practice (security, accessibility, data science, law, writing…)

Trace to authoritative bodies — not tutorials summarizing other tutorials.

- Authoritative body for the domain (OWASP for security, W3C for web, NIST for crypto)
- Primary literature: RFCs, published standards, peer-reviewed papers if they exist
- Current-year community consensus — practices shift; old tutorials are actively harmful

```
WebSearch: "OWASP SQL injection prevention 2025"
WebFetch:  https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html
WebSearch: "parameterized queries python psycopg3"
```

### Workflow or Process

Processes have order-of-operations constraints and failure modes that documentation
understates. Research both explicitly.

- Official runbook or checklist from the authoritative source
- Failure modes and recovery paths — what goes wrong, how to detect and reverse it
- Prerequisites and postconditions for each step

```
WebSearch: "zero-downtime postgres ALTER TABLE production 2025"
WebFetch:  https://www.postgresql.org/docs/current/sql-altertable.html
WebSearch: "postgres ALTER TABLE lock timeout production"
```

### Creative or Subjective Domain (writing, design, UX…)

Even subjective domains have evidence-based principles.

- Style guides or canonical references for the domain
- Empirical research where it exists (readability studies, accessibility audits)
- Where experts disagree, document both positions rather than picking one arbitrarily

```
WebSearch: "plain language writing guidelines"
WebFetch:  https://www.plainlanguage.gov/guidelines/
WebSearch: "sentence length readability studies"
```

### External Integrations and MCP Servers

Schemas and rate limits change without notice — never assume from memory.

- Current parameter schema for each tool or MCP server
- Rate limits, quotas, and latency that affect orchestration order
- Failure modes in tool chaining: partial success, stale cache, auth expiry

```
WebFetch: https://cloud.google.com/bigquery/docs/reference/rest
WebSearch: "bigquery quota limits per project 2026"
Read:      ~/.claude/mcp-servers/bigquery/SCHEMA.md  (if local config exists)
```

---

## Source Quality (Academic Standards)

Every claim must trace to a primary source. Use the tier hierarchy to select sources;
apply the red-flag list before accepting any claim.

| Tier | Source type | Examples |
|------|------------|---------|
| 1 | Specification or RFC | IETF RFC, W3C spec, ISO standard, language spec |
| 2 | Official documentation | Tool's own docs, API reference, official changelog |
| 3 | Primary author writing | Maintainer blog, conference talk by author, design doc |
| 4 | Peer-reviewed or editorial | ACM/IEEE paper, major publication with editorial review |
| 5 | Community consensus | Stack Overflow accepted answer, high votes, recent date |
| 6 | Third-party tutorial | Useful for examples — verify every claim against Tier 1-2 |

**Red flags — reject or verify independently:**
- AI-generated content with no primary source links
- Undated content for any version-specific claim
- Tutorial citing another tutorial (no primary source in the chain)
- "As of this writing" with no date
- Stack Overflow answer with no accepted mark and under 10 votes
- Content that contradicts official docs (cite the docs, note the discrepancy)

**Corroborate** any claim with significant consequences using a second independent
Tier 1-3 source before encoding it in a skill.

---

## Translating Research into Skill Instructions

Research produces facts; skills must contain actionable instructions.

```
Research: "PostgreSQL ADD COLUMN is non-blocking since PG 11 for simple
           additions (no DEFAULT requiring table rewrite)"

Skill instruction:
  To add a nullable column without locking the table:
    ALTER TABLE orders ADD COLUMN notes TEXT;
  Avoid DEFAULT with NOT NULL on large tables — triggers a full table
  rewrite on Postgres versions before 11.
```

Before writing instructions: verify the behavior holds for the version range the skill
targets, pin the version, and add a warning for the most common mistake.

---

## Retaining Sources (Required)

Create `references/sources.md` — do not embed the full sources list in SKILL.md.
Add only a one-line pointer in SKILL.md: `For sources: references/sources.md`.

**Format:**

```markdown
# Sources

Checked: 2026-03

## Primary

- [PostgreSQL 14 ALTER TABLE](https://www.postgresql.org/docs/14/sql-altertable.html)
  — confirmed non-blocking ADD COLUMN since PG 11 (no DEFAULT rewrite required)
- [PG 11 release notes](https://www.postgresql.org/docs/11/release-11.html)
  — confirmed version that introduced non-blocking column add

## Secondary

- [Django migration rollback](https://docs.djangoproject.com/en/4.2/topics/migrations/#reversing-migrations)
  — confirmed --fake flag behavior as of Django 4.2

## Discrepancies

- DigitalOcean tutorial (undated) claimed DEFAULT with NOT NULL is safe — contradicts
  PG official docs; tutorial discarded, official docs followed.
```

Use the full URL (not just a domain). Note what each source confirmed. Document
discrepancies and which source was followed. Separate primary (Tier 1-3) from
secondary (Tier 4-6).

---

## Sources

Checked: 2026-03

### Primary

- [ACRL Framework for Information Literacy](https://www.ala.org/acrl/standards/ilframework)
  — source evaluation principles: authority, accuracy, currency, purpose; basis for
  the "never cite AI as primary source" and corroboration requirements
- [Cornell University Library: Evaluating Web Sources](https://guides.library.cornell.edu/evaluate_websites)
  — CRAAP test criteria mapped to the 6-tier hierarchy (currency, relevance, authority,
  accuracy, purpose)
- [Anthropic: The Complete Guide to Building Skills for Claude](https://resources.anthropic.com/hubfs/The-Complete-Guide-to-Building-Skill-for-Claude.pdf)
  (January 2026) — confirmed that skill content quality depends on instruction accuracy;
  source of the requirement that skills contain verifiable, current guidance

### Secondary

- [Nielsen Norman Group: Progressive Disclosure](https://www.nngroup.com/articles/progressive-disclosure/)
  — confirmed the pattern of deferring detail to reduce cognitive load; basis for
  keeping sources in references/sources.md rather than SKILL.md body
- [OWASP: SQL Injection Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html)
  — used as a concrete example of Tier 1 authoritative domain source in the Domain
  Practice research strategy section
- [plainlanguage.gov Guidelines](https://www.plainlanguage.gov/guidelines/)
  — used as a concrete example of a canonical reference for a subjective/creative domain
