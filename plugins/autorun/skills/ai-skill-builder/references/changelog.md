# ai-skill-builder changelog

The revision history of the ai-skill-builder package itself. Nothing here describes the skill
you are building. Release history first, then the detail behind each release, newest first.

## Release history

### v1.2.1 - 2026-08-13
- Added "Growth Needs Evidence" to `references/refining-skills.md`: keep the smallest
  known-good core, add a guard only for a reproduced failure, and record the version to
  return to before a rewrite. Written after a deep-superset skill grew 167 → 715 lines
  while `audit-skill.sh` stayed clean and the repair was a rollback.

### v1.2.0 - 2026-08-03
- Audited every file against every other file and against `scripts/audit-skill.sh`; fixed the
  contradictions found. Detail below.
- Split `audit-skill.sh` output into decided, proxy, and needs-a-reader checks, and scored only
  the decided ones.
- Replaced wall-clock phase estimates with a Definition of Done throughout.

### v1.1.0 - 2026-03-05
- Added YAML frontmatter (enables Claude auto-detection)
- Rewrote body to imperative form throughout
- Integrated IMPROVEMENTS.md self-critique (moved to this file)
- Corrected description templates: trigger-phrase format for SKILL.md, outcome-focused for README
- Trimmed SKILL.md from 932 → ~350 lines; moved detail to references/
- Corrected directory taxonomy: references/examples/ for standalone skills (Anthropic's
  recommended structure)
- Corrected README.md rule: enforce "no README inside skill folder" with distribution exception
- Added description field constraints: 1024-char hard limit, no angle brackets, kebab-case names
- Added Additional Resources section linking all references/ files and scripts/
- Created references/patterns.md with 5 advanced patterns + troubleshooting from Anthropic's guide
- Confirmed skill categories are from Anthropic's official guide — no "unofficial" label

### v1.0.0 - Initial release based on Anthropic's Complete Guide
- Complete 4-phase methodology
- Progressive disclosure templates
- Testing framework
- Distribution strategies
- Common pitfalls guide

## v1.2.0 detail — internal-contradiction audit

Every file in the package was read in full and checked against the other files and against
`scripts/audit-skill.sh`.

**All line numbers below are as of v1.1.0, before these edits.** They locate each defect in the
version that carried it; `git show` the v1.1.0 tree to follow them. This edit shifted every file,
so the same numbers do not point at the same text in v1.2.0.

### Guidance that contradicted the validator or the rest of the package

1. Outcome-focused `description` prescribed at 8 sites, which `audit-skill.sh` fails for having
   no quoted trigger phrases, and which `SKILL.md:238`, `best-practices.md:62`, and
   `troubleshooting.md:51` already call wrong: `best-practices.md:405`,
   `refining-skills.md:127`, `:137`, `:240`, `:260`, `:282`, `:565`, and `changelog.md:352`.
   `refining-skills.md:137` offered as its ✅ result the exact string `best-practices.md:64`
   labels ❌.
2. Top-level `version` field prescribed at 3 sites, which `audit-skill.sh` fails:
   `troubleshooting.md:18`, `distribution.md:40`, `:45`. Four other sites already said
   `metadata.version`.

### Broken pointers and commands

3. A `SKILL.md` bullet pointed at a file that is not part of the package. The pointer was
   removed; the package cites only files it ships or public sources.
4. `refining-skills.md:494` pointed at `../templates/SKILL-template.md`. Corrected to
   `examples/SKILL-template.md`.
5. `discovery.md:55` pointed at `references/skill-categories.md`. Corrected to `categories.md`.
6. `troubleshooting.md:288` called `open('~/.claude/skills/…')`. Python does not expand `~`,
   so the diagnostic raised `FileNotFoundError` on every run. Rewritten to take the path as an
   argument.

### Numbers without a source

7. `testing.md:204` and `:225` required "at least two of ≥50% time reduction, ≥10 percentage
   points quality improvement…". No primary source establishes cross-skill thresholds.
   Replaced with criteria relative to the skill's own measured baseline.
8. Invented before/after figures removed from `refining-skills.md:224`, `:365`, `:369`, `:582`,
   `:627` and from the ROI, Projected Impact, Impact Measurements, and Success Metrics sections
   of this file. The v1.1 file inventory is now labelled a dated snapshot.

### Claims, links, counts

9. `distribution.md:15` claimed "any Claude Code installation can use any skill". Discovery
   roots, `allowed-tools` handling, plugin versus standalone placement, and reload behavior
   differ by host. Narrowed to tested hosts and versions.
10. `best-practices.md:442` cited `agent-skills.dev` for the standard while `SKILL.md:22` and
    `sources.md:18` cite `agentskills.io`. Unified. Bare `docs.anthropic.com` replaced with
    the Claude Code skills page; a dead Discord invite removed.
11. Word limits appeared as 2,000, 3,000, and 5,000 in different files. Reconciled to the
    2,000-word guideline and 5,000-word hard limit, with the specification's 5,000-token and
    500-line recommendation recorded in `sources.md`.
12. `refining-skills.md:113` instructed `rm`. Replaced with `trash`, which preserves the
    deletion record.
13. `discovery.md` and `SKILL.md` described a "20-question" guide containing 22 questions.

### Conformance of ai-skill-builder to the rules it teaches

14. Added `metadata.version`, required by `SKILL.md:101` and absent from ai-skill-builder's own
    frontmatter since v1.1.
15. Dropped `Glob` and `Grep` from `allowed-tools`. `Glob` appeared nowhere in the package
    except that line; `Grep` only inside a hypothetical example in `patterns.md`.
16. Brought `SKILL.md` back under the 2,000-word guideline by moving the optional-frontmatter
    field list, the trigger-fix detail, and the GitHub setup steps into the references that
    already covered them.
17. Rewrote the reference index so each entry states when to read the file and what it
    produces, rather than summarising its contents.

### Added

18. Target-host discovery in Step 1: host, version, discovery root, invocation form, and reload
    behavior, with unknowns marked as unknown.
19. Compatibility tests as a fourth test type in Step 4.
20. A Common Pitfalls row for unverified compatibility and performance claims.
21. A pointer to the `engineer-agent-skills` skill for cross-host claim matrices, per-host
    validation receipts, and install ownership, rather than duplicating that material here.

### Second pass

Found by re-reading the whole package after the edits above, so these are defects the first
pass introduced or walked past.

22. `scripts/audit-skill.sh` still repeated an unpublished local measurement claim after item 3
    removed the package pointer. The useful checks stayed; the unsupported claim was removed
    from distributable references.
23. Four markdown files mis-rendered because a same-length code fence was nested inside
    another: `troubleshooting.md` State Tracking, `distribution.md` README template, and a stray
    unclosed fence in the extracted guide. Everything after the break rendered as the wrong kind
    of content. `audit-skill.sh` now checks fence balance and same-length nesting.
24. `references/examples/SKILL-template.md` — the file every new skill is copied from — still
    carried `(X minutes)` per step and a `**Total Time**` line, so item 6's Definition of Done
    policy would have been undone by the first skill built from the template.
25. Two checks classified as PASS were heading and regex matches: the Level 3 detail heading and
    the description's technical identifiers. Both are now proxies.
26. The `SKILL.md` reference index used bare filenames (`research.md`), which the link checker
    does not resolve and which leave the directory to be inferred. Restored to `references/`
    paths.
27. `sources.md` cited Format A and Format B by line number into `best-practices.md`; both
    numbers were already stale. Replaced with the section name.
28. `scripts/scaffold-skill.sh` generated a skill that failed `scripts/audit-skill.sh`. Its
    frontmatter carried an outcome-focused TODO description with no quoted trigger phrases (a
    FAIL) and no `metadata.version` beside the `## Version History` heading it also generated
    (a second FAIL). It emitted `(X minutes)` per step, a `**Total Time**` line, and an `[X%]
    faster` metric — everything items 6 and 7 removed elsewhere. Running the scaffolder and
    auditing the result is now the check that keeps the two scripts agreeing; a fresh scaffold
    reports zero FAILs and two warnings for its own unfilled TODOs.
29. `scaffold-skill.sh` used `${SKILL_NAME^}` for the generated H1. That form needs bash 4;
    macOS ships bash 3.2.57 as `/bin/bash`, where it is `bad substitution` and the script exits
    1. It ran here only because Homebrew bash 5.3 precedes `/bin/bash` on PATH. Rewritten with
    `tr` and `awk`, and verified with `/bin/bash` directly.
30. `scaffold-skill.sh` ran `rm -rf` on an existing skill directory after a single y/N prompt,
    in a package that tells authors to use `trash` and whose audit fails a skill for showing
    `rm` on a real path. Now uses `trash`, or a timestamped `mv` when `trash` is absent.
31. `scaffold-skill.sh` wrote a `README.md` into `scripts/`, `references/`, and `assets/`, in a
    package whose stated rule is that a skill folder carries no `README.md`, and where every
    file under `references/` is loadable context. The one in `references/` cost tokens to say
    "add documentation here". The guidance now prints to the terminal.
32. `scaffold-skill.sh` hardcoded `~/.claude/skills` as the only output root, and pointed at
    the same dead `templates/SKILL-template.md` path item 4 corrected elsewhere. `SKILLS_DIR`
    is now overridable, matching the target-host discovery step item 18 added.
33. Two Common Pitfalls rows contradicted the row item 20 added, from two lines away. The
    ✅ column for missing success criteria read "Reduce API test writing time by 75%", a bare
    percentage with no baseline; and the ✅ column for no testing still listed three test types
    after item 19 made it four. Both corrected.
34. `SKILL.md` described `audit-skill.sh` as producing "scored output (0-100%)" after item 25
    made the number a decided-check score. It now says what the number covers and that the gate
    is zero FAILs.
35. `SKILL.md`'s opening claimed Antigravity support. `sources.md` cites host documentation for
    Claude Code, Codex, and Qwen Code and nothing for Antigravity, so the sentence was an
    instance of the pitfall item 20 added three sections below it. It now names the three
    sourced hosts and tells the reader to verify any other.

## v1.1.0 development notes (written 2026-02-15, released 2026-03-05)

A critique of v1.0 and a record of what v1.1 changed in response. Kept for provenance. Where it
disagrees with the guidance in the package today, the package is current and this section is not.

---

## Critical Analysis of Initial Version (v1.0)

### ❌ Major Gaps Identified

**1. No Refinement Pathway** (CRITICAL GAP)
- **Problem**: Only covered creating NEW skills, ignored improving existing ones
- **Impact**: Users with old skills had no migration path
- **Real-world scenario**: Someone with `my_api_test/README.md` has no way to upgrade
- **Severity**: High - Most users refine more than they create

**2. Missing Source Links** (DOCUMENTATION GAP)
- **Problem**: No attribution to Anthropic's guide, no reference links
- **Impact**: Users can't verify methodology or dive deeper
- **Missing links**: PDF guide, Anthropic docs, MCP, community resources
- **Severity**: Medium - Reduces credibility and learning ability

**3. No Validation Tools** (AUTOMATION GAP)
- **Problem**: No way to audit if skills follow best practices
- **Impact**: Users don't know if their skills are correct
- **Missing**: Automated checker for file structure, naming, frontmatter
- **Severity**: High - Manual validation is error-prone

**4. Inappropriate Tool Usage** (IMPLEMENTATION ERROR)
- **Problem**: Examples used `nano`, `vim` (human text editors)
- **Impact**: Claude can't use these - uses Read/Edit/Write tools instead
- **Context**: Skills are for Claude to use, not humans
- **Severity**: Medium - Confusing and technically incorrect

**5. No Migration Guide** (USABILITY GAP)
- **Problem**: No path from old skill standards to new ones
- **Impact**: Existing skill authors stuck with old patterns
- **Missing**: Before/after examples, step-by-step upgrade process
- **Severity**: Medium - Blocks adoption of new standards

**6. Limited Real Examples** (LEARNING GAP)
- **Problem**: Hypothetical examples only, no real skill references
- **Impact**: Users can't see actual working implementations
- **Missing**: Links to community skills, real-world patterns
- **Severity**: Low - Learning is slower but possible

**7. No Performance Tracking** (MEASUREMENT GAP)
- **Problem**: No framework for measuring refinement impact
- **Impact**: Can't validate improvements worked
- **Missing**: Before/after metrics, success measurement
- **Severity**: Medium - Can't prove value of changes

---

## Improvements Implemented (v1.1)

### ✅ Major Additions

**1. Complete Refinement Workflow**
- **File**: `references/refining-skills.md` (1,902 words)
- **Content**:
  - 5-step refinement process (Audit → Prioritize → Fix → Validate → Document)
  - Common scenarios (migration, enhancement, automation)
  - Real before/after examples with impact metrics
  - Tool usage guide (Read/Edit/Write, not nano)
  - Continuous improvement framework
- **Impact**: Fills critical gap in Anthropic's guide

**2. Automated Audit Script**
- **File**: `scripts/audit-skill.sh` (executable)
- **Capabilities**:
  - File structure validation (SKILL.md, kebab-case)
  - YAML frontmatter checking (name, description)
  - Progressive disclosure detection (3 levels)
  - Content quality analysis (examples, metrics)
  - Scoring system (0-100%)
  - Actionable fix recommendations
- **Impact**: automates the structural checks that were being done by eye

**3. Comprehensive Source Documentation**
- **File**: `references/sources.md` (1,115 words)
- **Content**:
  - Primary source: Anthropic's PDF with full citation
  - Official docs: Claude, MCP, Agent Skills Standard
  - Community resources: Discord, GitHub
  - Related tools and technologies
  - Learning resources (prompt engineering, markdown)
  - Testing tools (ShellCheck, markdownlint)
  - Progressive disclosure theory (Nielsen Norman Group)
  - Outcome-focused design (Jobs to Be Done)
  - Complete URL reference table
  - Citation formats (APA, Chicago)
- **Impact**: Full transparency and verifiability

**4. Fixed Tool Usage Throughout**
- **Changed**: All examples from `nano/vim` → Claude tools
- **Examples now show**:
  - `Read:` for examining files
  - `Edit:` for precise changes
  - `Write:` for creating files
  - `Bash:` for file operations
- **Impact**: Technically accurate for Claude's use

**5. Enhanced Main Documentation**
- **File**: `SKILL.md` updated (3,281 words)
- **Additions**:
  - Major "Refining Existing Skills" section
  - Source attribution at top
  - Links to refinement guide
  - Tool usage examples (Claude-appropriate)
  - Migration scenarios
  - Performance tracking examples
- **Impact**: Now covers full lifecycle (create + refine)

**6. Improved README**
- **File**: `README.md` updated (1,398 words)
- **Additions**:
  - Refinement capabilities highlighted
  - Source links section
  - Audit script documentation
  - Example 3: Refining existing skill
  - Fixed tool usage in examples
  - Version history with improvements listed
- **Impact**: Clear discovery of new capabilities

---

## Files Created/Updated Summary

### New Files (v1.1)
1. `scripts/audit-skill.sh` - Automated validation
2. `references/refining-skills.md` - Complete refinement guide
3. `references/sources.md` - All source links
4. `IMPROVEMENTS.md` - This document

### Updated Files (v1.1)
1. `SKILL.md` - Added refinement section, source links
2. `README.md` - Added refinement capabilities, sources (later removed; see v1.2.0)
3. `references/examples/SKILL-template.md` - Already good (no changes needed)
4. `references/best-practices.md` - Already comprehensive (no changes needed)
5. `scripts/scaffold-skill.sh` - Already functional (no changes needed)

### Total documentation as of v1.1 (2026-03-05)
- **Word count**: 9,510 words
- **Files**: 8 (3 markdown docs, 3 reference docs, 2 scripts)
- **Coverage**: Create + Refine + Sources + Tools

This inventory is a v1.1 snapshot and is not maintained. For the current file list run
`bash scripts/audit-skill.sh <skill>`.

---

## Gap Analysis: What Was Missing

### From Anthropic's Guide

**Guide Covered**:
- ✅ 4-phase creation methodology
- ✅ Progressive disclosure structure
- ✅ Skill categories
- ✅ Testing framework
- ✅ Distribution strategies

**Guide Missed**:
- ❌ Refining existing skills
- ❌ Migration from old standards
- ❌ Automated validation tools
- ❌ Performance tracking
- ❌ Continuous improvement

### Our Implementation

**Now Includes**:
- ✅ All content from guide
- ✅ Refinement workflow (original)
- ✅ Audit automation (original)
- ✅ Migration guides (original)
- ✅ Source attribution (original)
- ✅ Performance tracking (original)

**Total Coverage**: Guide + 6 major additions

---

## What changed between v1.0 and v1.1

### Countable

| Thing counted | v1.0 | v1.1 |
|---|---|---|
| Files in the package | 5 | 8 |
| Scripts | 1 (scaffold) | 2 (scaffold, audit) |
| Reference documents | 1 | 3 |

Percentage deltas were reported here through v1.1. They divided counts of different things by
each other and are gone; the counts themselves are above, as a dated v1.1 snapshot.

### Qualitative Improvements

**Coverage**:
- v1.0: Creation workflow only (~50% of skill lifecycle)
- v1.1: Complete lifecycle (create + refine + validate)

**Usability**:
- v1.0: Manual validation required
- v1.1: Automated audit with scoring

**Accuracy**:
- v1.0: Mixed tool usage (nano/vim inappropriate for Claude)
- v1.1: All examples use Claude tools (Read/Edit/Write)

**Verifiability**:
- v1.0: No source links
- v1.1: Complete source attribution with URLs

---

## Lessons Learned

### What Worked Well Initially
1. **Progressive disclosure structure** - Correctly implemented from guide
2. **Scaffolding automation** - Good use of bash scripting
3. **Template provision** - Helpful starting point
4. **File naming rules** - Comprehensive and accurate

### What Needed Improvement
1. **Lifecycle coverage** - Too focused on creation, ignored refinement
2. **Source attribution** - No links to verify methodology
3. **Automation** - Manual validation is error-prone
4. **Tool usage** - Confused human and AI tool usage
5. **Real examples** - Hypothetical only, no real references

### Design Decisions Made

**Decision 1: Separate refinement guide**
- **Rationale**: Refinement is complex enough for dedicated doc
- **Alternative**: Could have embedded in SKILL.md
- **Chose**: Separate file for clarity
- **Impact**: Better organization, easier to find

**Decision 2: Bash audit script**
- **Rationale**: Fast, portable, no dependencies
- **Alternative**: Could use Python for richer checks
- **Chose**: Bash for simplicity
- **Impact**: Works immediately, easy to understand

**Decision 3: Comprehensive source documentation**
- **Rationale**: Transparency and verifiability
- **Alternative**: Could just link to Anthropic guide
- **Chose**: Complete source catalog
- **Impact**: Users can verify and dive deeper

---

## Self-Critique Summary

### v1.0

**Strengths**:
- Accurate methodology from Anthropic guide
- Good template structure
- Useful scaffolding automation

**Weaknesses**:
- Covered creating a skill but not improving one
- No source attribution
- No validation tools
- Inappropriate tool examples

### v1.1

**Strengths**:
- Complete lifecycle (create + refine)
- Full source attribution
- Automated validation
- Technically accurate tool usage
- Comprehensive documentation

**Remaining Gaps**:
- Could add more real-world skill examples
- Could integrate with Claude marketplace (when available)
- Could add skill performance analytics
- Could add collaborative refinement features

---

## Comparison to Anthropic Guide

### What We Preserved
- ✅ 4-phase methodology
- ✅ Progressive disclosure (3 levels)
- ✅ Skill categories (3 types)
- ✅ Testing framework
- ✅ Phase estimates (v1.1 kept the guide's wall-clock figures; v1.2.0 replaced them with a
  Definition of Done per phase)
- ✅ Success criteria patterns
- ✅ Common pitfalls
- ✅ Distribution strategies

### What We Enhanced
- ➕ Complete refinement workflow
- ➕ Automated audit tooling
- ➕ Migration guides
- ➕ Source attribution
- ➕ Performance tracking
- ➕ Tool usage corrections
- ➕ Before/after examples
- ➕ Continuous improvement framework

### Why Enhancements Were Needed

**Anthropic's guide** (excellent for creation):
- Target: Creating new skills from scratch
- Audience: Developers starting fresh
- Scope: Design → Implementation → Testing → Distribution

**Real-world needs** (include refinement):
- Reality: Most skills need improvement over time
- Audience: Developers maintaining existing skills
- Scope: Full lifecycle including evolution

**Our additions** address the gap between "how to build" and "how to maintain."

---

## Testing This Skill Itself

### Applied Own Methodology

**Audit Results**:
```bash
bash ./scripts/audit-skill.sh \
     ~/.claude/skills/ai-skill-builder
```

**Gate**: zero FAILs. See `references/distribution.md` on why the printed percentage is not a
quality verdict.

**Checks**:
- ✅ SKILL.md exists (correct name)
- ✅ kebab-case folder name
- ✅ YAML frontmatter present
- ✅ Progressive disclosure structure
- ✅ Examples included
- ✅ Success metrics documented
- ✅ No TODOs or placeholders
- ✅ Scripts directory with tools
- ✅ References directory with guides

### Dogfooding Results

**This skill follows its own guidance**:
- Progressive disclosure: 3 levels ✅
- Description in capability-plus-quoted-trigger-phrase format: ✅
- Source attribution: ✅
- Examples: ✅
- Testing framework: ✅
- Continuous improvement: ✅

---

## Recommendations for Future Versions

### v1.2 (Next Minor Release)
- Add real-world skill examples (links to quality community skills)
- Create skill performance analytics tool
- Add collaborative refinement guide (team workflows)
- Integrate with emerging Claude marketplace

### v2.0 (Next Major Release)
- Interactive web-based skill builder
- AI-powered skill suggestion based on use case
- Skill dependency management
- Automated testing harness
- Skill version migration tool

---

## Key Learnings

### On Critique Process
1. **First implementation is never complete** - Critique reveals gaps
2. **User perspective matters** - "Help me improve" is as important as "Help me build"
3. **Source attribution is essential** - Verifiability builds trust
4. **Automation reduces errors** - Manual validation misses issues
5. **Tool accuracy matters** - Claude uses different tools than humans

### On Skill Development
1. **Progressive disclosure works** - Users find what they need quickly
2. **Outcome-focus resonates** - Users care about results, not features
3. **Examples are essential** - Abstract descriptions don't teach
4. **Testing catches issues** - Untested skills create bad experiences
5. **Iteration improves quality** - V1.1 >> V1.0 with focused critique

### On Documentation
1. **Complete source attribution** - Always link to original sources
2. **Separate concerns** - Refinement deserves own guide
3. **Tool-appropriate examples** - Match tool usage to audience (Claude vs humans)
4. **Comprehensive coverage** - Better to be thorough than brief
5. **Self-critique documents** - Show your thinking and improvements

---

## Conclusion

### What We Built
A comprehensive skill that:
- Teaches Anthropic's official methodology (v1.0)
- Adds refinement workflow for existing skills (v1.1 NEW)
- Provides automation tools (scaffolding, audit) (v1.1 ENHANCED)
- Includes complete source attribution (v1.1 NEW)
- Uses technically accurate tool examples (v1.1 FIXED)

### Why It Matters
**Before**: Users could create skills but not improve them
**After**: Users can create, refine, validate, and continuously improve skills

**Before**: No way to know if skills follow best practices
**After**: Automated audit with scoring and actionable fixes

**Before**: No source links for verification
**After**: Complete source catalog with URLs and citations

### What v1.1 added

**Lifecycle**: creation only → creation plus refinement
**Automation**: 1 script (scaffold) → 2 (scaffold, audit)
**Sourcing**: no citations → every methodology claim attributed in `sources.md`
**Tool usage in examples**: `nano`/`vim` → Read, Edit, Write, Bash

The self-assigned grades and coverage percentages that stood here were not measurements of
anything and have been removed.

---

## Sources for This Critique

**Methodology**:
- Anthropic's Guide: Original best practices
- User feedback: "critique your work and improve it"
- Challenge Mode (CLAUDE.md): "Never assume, always verify"
- Concrete spec (CLAUDE.md): Specific, measurable, testable

**Tools Used**:
- Read: Examined existing implementation
- Edit: Made precise improvements
- Write: Created new documentation
- Bash: Tested scripts and structure
- Critical thinking: Identified gaps and solutions

**References**:
- Anthropic's Complete Guide to Building Skills for Claude (Jan 2026)
- User's CLAUDE.md development guidelines
- Real-world skill development experience
- Software engineering best practices

---

The v1.1 development notes above were last edited 2026-02-15 and are not maintained. The current
package version is the `metadata.version` field in `SKILL.md`.
