---
name: ai-skill-builder
description: Guides creation and improvement of portable Agent Skills using the shared SKILL.md
  format and Anthropic's established methodology. Use when the user wants to "create a skill",
  "build a skill", "make a new skill", "write a new skill", "improve an existing skill",
  "audit my skill", "refine my skill", "test a skill", "package a skill for distribution",
  or needs guidance on skill structure, progressive disclosure, description quality, testing,
  cross-harness compatibility, or distribution.
allowed-tools: Read Write Edit Bash WebSearch WebFetch
metadata:
  version: 1.2.1
---

# AI Skill Builder

Build Agent Skills using the shared `SKILL.md` format and Anthropic's methodology.
`references/sources.md` cites host documentation for Claude Code, Codex, and Qwen Code; every
other host is untested here, so verify the one you target (Step 1).

**Primary methodology source**: "The Complete Guide to Building Skills for Claude"
(Anthropic, January 2026)
- PDF: https://resources.anthropic.com/hubfs/The-Complete-Guide-to-Building-Skill-for-Claude.pdf
- Extracted text: `references/ai-skill-builder-guide.md`

**Portable format source**: https://agentskills.io/specification

---

## Quick Start

To create a skill from scratch, follow the 4-phase process below.
To improve an existing skill, read `references/refining-skills.md`.
To validate structure, run `scripts/audit-skill.sh`.

**Invoke with:** ask for `ai-skill-builder`; hosts may expose it as
`/ai-skill-builder`, `$ai-skill-builder`, or through their skills picker.

---

## How It Works

Four-phase methodology from the Anthropic guide. Each phase carries a **Definition of Done**:
the condition that must hold before the next phase starts.

| Phase | Activity | Definition of Done |
|-------|----------|--------------------|
| 1: Planning & Design | Define target hosts, use cases, category, success criteria | Criteria are measurable and the category is chosen |
| 2: Implementation | Create folder, write SKILL.md, add resources | `audit-skill.sh` reports no failures |
| 3: Testing | Triggering, functional, performance, compatibility tests | Every named host passes; baseline recorded |
| 4: Distribution | Package, document, publish | Install verified from a clean root |

Phase 1 covers Steps 1–2 below; Phases 2–4 correspond to Steps 3–5.

How long a phase takes depends on who or what runs it, so this skill states no wall-clock
estimates and the skills you build should not either.

---

## Skill Creation Workflow

### Step 1: Discover Requirements

**Research the domain first** — use the host's web tools to verify current best practices
before writing instructions. Outdated guidance is worse than none: an agent follows it
confidently.

**Retain all sources**: record every URL consulted with a note of what it confirmed.
Unsourced guidance cannot be verified or updated.

For research strategies, source quality standards, and the required Sources section
format: `references/research.md`

1. Identify the problem and target users
2. Identify every target host and version, its discovery root, invocation form, and reload
   behavior. Mark unknowns as unknown rather than guessing.
3. Define 2-3 concrete use cases
4. Set measurable success criteria (time saved, errors reduced, quality improved)
5. Choose a skill category:

| Category | INPUT → OUTPUT | Examples |
|----------|---------------|---------|
| **1: Document & Asset Creation** | Data/specs → document, code, report | API test generator, meeting notes summarizer |
| **2: Workflow Automation** | Task params → completed multi-step process | Deploy pipeline, code review workflow |
| **3: MCP Enhancement** | MCP tool outputs → smarter orchestration | Smart file search, BigQuery assistant |

For in-depth category guidance: `references/categories.md`
For interactive discovery questions: `references/discovery.md`

### Step 2: Design Structure

Design using progressive disclosure — 3 loading levels:

| Level | When loaded | Target length | Content |
|-------|------------|---------------|---------|
| 1: Metadata | Always (~100 words) | name + description | Trigger conditions |
| 2: SKILL.md body | When skill triggers | under 5,000 words (ideally under 2,000) | Core workflow + pointers |
| 3: references/ files | As needed | Unlimited | Deep detail, schemas, examples |

For progressive disclosure writing tips and success metrics: `references/best-practices.md`

### Step 3: Implement

**Critical Rules:**
- ✅ File MUST be named `SKILL.md` (not README.md)
- ✅ Folder name MUST be **kebab-case** — no spaces, no capitals (`my-skill` not `My Skill`)
- ✅ YAML frontmatter MUST include `name` and `description`; put a version under `metadata`
- ✅ Description MUST include specific trigger phrases — what users SAY to activate the skill
- ✅ Description must be **under 1024 characters** (hard limit — longer descriptions are truncated)
- ✅ No XML angle brackets (`<` or `>`) in any frontmatter field
- ❌ NO README.md inside the skill folder — all docs go in SKILL.md or references/
  (Exception: a README.md at the GitHub repo ROOT, outside the skill folder, is fine for GitHub.)
- ❌ NO spaces or underscores in folder names (`my_skill` → `my-skill`)

**Frontmatter: required fields**:
```yaml
---
name: your-skill-name          # kebab-case only; no spaces, capitals, or underscores
description: What it does. Use when user asks to "specific phrase", "another phrase".
             # MUST include: what it does + when to use it (trigger conditions)
             # Under 1024 characters. No XML angle brackets.
             # Do NOT start with "claude" or "anthropic" (reserved namespaces).
---
```

For every optional field (`license`, `compatibility`, `allowed-tools`, `metadata`) with
host-portability notes: `references/best-practices.md`

**Positioning language** ("generate tests 87% faster") belongs in the repo README, never in
`description`. See `references/best-practices.md`.

**Folder structure (Claude Code standalone example):**
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

| Directory | Load into context? | Use for |
|-----------|-------------------|---------|
| `references/` | Yes, as needed | Schemas, API docs, policies, workflow guides |
| `references/examples/` | As needed | Working code users copy and adapt |
| `scripts/` | No (run directly) | Validators, scaffolders, utilities |
| `assets/` | No (used in output) | Images, fonts, HTML templates the skill pastes into output |

Note: Plugin skills (in `plugin-name/skills/`) may place `examples/` at the top level — that is
plugin-dev convention. For standalone `~/.claude/skills/` skills, put examples inside `references/`.

To scaffold a new skill: `bash scripts/scaffold-skill.sh my-skill-name`
(set `SKILLS_DIR` to target another host's root)
To start from template: copy `references/examples/SKILL-template.md`

### Step 4: Test

Four testing approaches (run in order):

1. **Triggering tests** — verify the agent activates on expected phrases and not on unrelated requests
2. **Functional tests** — validate the skill's core workflow produces correct output for known inputs
3. **Performance tests** — measure improvement over baseline (time saved, error reduction, consistency)
4. **Compatibility tests** — verify discovery, explicit invocation, reference loading, script
   execution, and duplicate/name resolution on every named host and version

**Debugging trigger issues**:
```
Ask the agent: "When would you use the [skill name] skill?"
```
The agent should quote its description back. Adjust based on what's missing or too vague.

**Fixing undertriggering**: Add more specific trigger phrases and relevant technical terms.

**Fixing overtriggering**: Add negative triggers to the description:
```yaml
description: Processes PDF legal documents for contract review. Use for "review this contract",
  "analyze legal document", "extract contract clauses". Do NOT use for general PDF viewing,
  image extraction, or non-legal documents (use doc-converter skill instead).
```

For the full diagnosis-and-fix guide covering all four failure modes: `references/troubleshooting.md`

To validate structure:
```bash
bash scripts/audit-skill.sh /path/to/YOUR-SKILL
```

For automated quality review, use the built-in `skill-creator` skill:
```
"Use the skill-creator skill to review the skill I just built and suggest improvements"
```
Also available: the `skill-reviewer` agent from the plugin-dev plugin checks description quality.

For detailed test case templates and the Testing Triangle methodology: `references/testing.md`

### Step 5: Distribute

**Three distribution channels** (choose one or all):

**A. Claude.ai / Claude Code (individual install)**
```bash
# Clone into Claude Code skills directory:
cd ~/.claude/skills && git clone https://github.com/username/your-skill-name
# Or: download ZIP → upload in Claude.ai Settings > Capabilities > Skills
```

**B. Organization-wide deployment** (admins only, shipped Dec 2025)
Admins can deploy skills workspace-wide via Claude.ai admin console — automatic updates,
centralized management. Users get the skill without any install step.

**C. Programmatic / API**
Add skills to Messages API requests via `container.skills` parameter. Use the `/v1/skills`
endpoint to manage skills. Works with the Claude Agent SDK for building custom agents.

For GitHub repo layout, the README template, versioning, and community channels:
`references/distribution.md`

---

## Common Pitfalls

| Pitfall | ❌ Wrong | ✅ Correct |
|---------|---------|---------|
| File naming | `my_skill/README.md` | `my-skill/SKILL.md` |
| Description field | Outcome-focused: `"generates tests 87% faster"` | Trigger phrases: `"create a skill", "improve my skill"` |
| README positioning | Trigger phrases in GitHub README | Outcome-focused: `"generate tests 87% faster"` |
| No progressive disclosure | Monolithic wall of text | 3-level: hook (50-100w) → workflow (200-400w) → detail |
| No testing | Write → publish immediately | Triggering + functional + performance + compatibility tests |
| Missing success criteria | "Build a skill that helps with APIs" | "Cut API test writing from 3 h to 45 min, median of 3 runs" |
| Feature-focused description | "Uses OpenAPI parser and Jinja2 templates" | Trigger phrases + concise capability summary |
| Unmeasured claim | "Works with any agent host", a bare "40% faster" | The hosts and versions you tested; the workload, baseline, and unit behind the number. The 87% in the rows above is correct placement only if it was measured |

---

## Refining Existing Skills

Signs a skill needs refinement:
- File named README.md or folder has underscores/capitals (P0 — Claude cannot find it)
- Missing YAML frontmatter (P0 — Claude cannot auto-activate it)
- Description is outcome-focused instead of trigger-phrase format (P1)
- SKILL.md is over 5,000 words (P0 — hard limit; Claude reports degraded quality above this)
- SKILL.md is over 2,000 words with no references/ files (P1 — detail belongs in references/)
- Wall of text with no progressive disclosure structure (P1)

For the 5-step refinement process (audit → prioritize → fix → validate → document), migration
scenarios, and before/after examples: `references/refining-skills.md`

---

## Additional Resources

### Reference Files (loaded as needed by the agent — when to open it → what it gives back)

- **`references/research.md`** — before writing any domain instruction → source-tier judgement, `sources.md` entries
- **`references/discovery.md`** — when requirements are unclear → 22-question plan: name, triggers, use cases, inputs, outputs, tests
- **`references/categories.md`** — when Step 1's category is not obvious → category, structure, Level 2 template
- **`references/best-practices.md`** — while writing body and frontmatter → description format, every optional field, level targets, 5 anti-patterns
- **`references/patterns.md`** — when 4 phases are not enough → orchestration, multi-MCP, refinement loops, runtime branching, domain rules
- **`references/testing.md`** — at Step 4 → T1-T6 triggering cases, F1-F4 functional cases, baseline comparison
- **`references/troubleshooting.md`** — when a built skill misbehaves → fixes for no trigger, over-trigger, skipped instructions, context overload
- **`references/refining-skills.md`** — when improving an existing skill → P0-P3 audit, migration checklist
- **`references/distribution.md`** — at Step 5 → repo layout, README, versioning
- **`references/sources.md`** — when checking what a claim rests on, or adding one → citations, and claims with no retrievable source
- **`references/changelog.md`** — when changing ai-skill-builder itself → its release history, not the history of the skill you are building

### Related skills (separate packages — do not duplicate their content here)
- **`engineer-agent-skills`** — Read when a skill targets more than one host, or makes a
  portability, security, or performance claim. Supplies the portable-standard vs
  runtime-extension claim matrix, per-host validation receipts, install/uninstall ownership
  rules, and semantic XML body regions.

### Scripts (run directly — do not load into context)
- **`scripts/audit-skill.sh`** — structure smoke test. Gate on zero FAILs; the percentage
  scores only the mechanically decidable checks, and proxy checks are listed unscored.
  ```bash
  bash scripts/audit-skill.sh /path/to/YOUR-SKILL
  ```
- **`scripts/scaffold-skill.sh`** — create a skill directory that already passes the audit
  ```bash
  SKILLS_DIR=~/.claude/skills bash scripts/scaffold-skill.sh my-skill-name
  ```
