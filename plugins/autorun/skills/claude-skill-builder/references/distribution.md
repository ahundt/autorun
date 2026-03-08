# Skill Distribution Guide

How to package, document, and publish Claude Code skills for others to install.

---

## Current Distribution Landscape (2026)

Skills are distributed directly via GitHub — no central marketplace exists yet. Installation is a
simple git clone into `~/.claude/skills/`. This means:

- **No approval process**: Publish when you're ready
- **Version control**: GitHub handles versioning and changelogs
- **Discovery**: Word of mouth, Claude Discord, social sharing
- **Compatibility**: Any Claude Code installation can use any skill

---

## Step 1: Prepare the Skill for Distribution

Before creating a GitHub repo, verify:

```bash
# 1. Correct file naming
ls ~/.claude/skills/your-skill-name/SKILL.md   # must exist
ls ~/.claude/skills/your-skill-name/README.md  # must NOT exist (inside skill folder)

# 2. Correct folder naming (kebab-case)
ls -d ~/.claude/skills/your-skill-name         # no underscores, no capitals

# 3. Run the audit script
bash ~/.claude/skills/claude-skill-builder/scripts/audit-skill.sh \
    ~/.claude/skills/your-skill-name

# Target score: 85%+ before distributing
```

### Distribution Checklist

- [ ] `SKILL.md` has YAML frontmatter with `name`, `description`, `version`
- [ ] `description` uses trigger-phrase format (what users SAY to activate)
- [ ] Folder is kebab-case, no README.md inside the skill folder
- [ ] Triggering tests pass (skill activates on expected phrases)
- [ ] Functional tests pass (at least happy path + 1 edge case)
- [ ] `version` field is set to `0.1.0` or higher

---

## Step 2: Create the GitHub Repository

The GitHub repo structure should follow this layout:

```
your-skill-repo/                   ← GitHub repo root
├── README.md                      ← Human-facing docs (outcome-focused, installation, examples)
└── your-skill-name/               ← actual skill folder (no README.md inside)
    ├── SKILL.md
    ├── references/
    │   └── ...
    └── scripts/
        └── ...
```

**Why this layout?**
- `README.md` at repo root serves as the GitHub landing page for humans
- The skill folder inside contains only what Claude needs
- Users clone the repo root, getting the skill folder at the right nesting

**Alternative for single-skill repos** (when the repo IS the skill):
```
your-skill-repo/                   ← GitHub repo root = skill folder
├── README.md                      ← OK here: repo root is outside the skill's context
├── SKILL.md
├── references/
└── scripts/
```

This works but the README.md rule applies inside the skill folder — since here they coincide, it's the exception documented in the Anthropic PDF.

---

## Step 3: Write the README.md

The repo-level README.md is for **human readers deciding whether to install**. Use outcome-focused language here — this is where "generate tests 87% faster" belongs.

### README.md Template

```markdown
# [Skill Name]

> [One outcome-focused sentence: "Generate X in Y% less time"]

[2-3 sentences describing the problem this skill solves and who benefits most.]

## Installation

```bash
cd ~/.claude/skills
git clone https://github.com/username/your-skill-name
```

Verify installation:
```bash
ls ~/.claude/skills/your-skill-name/SKILL.md
```

Restart Claude Code, then activate with: `/your-skill-name`

## Requirements

- Claude Code [version or "latest"]
- [Any required MCP servers with install links]
- [Any required CLI tools]

## What It Does

[2-3 bullet points with concrete outcomes]
- ✅ [Outcome 1 with metric]
- ✅ [Outcome 2 with metric]
- ✅ [Outcome 3 with metric]

## Usage Examples

**[Example 1 scenario]:**
```
/your-skill-name [typical usage]
```

**[Example 2 scenario]:**
```
[Natural language trigger phrase]
```

## Changelog

**v0.1.0** - [Date]
- Initial release

## License

[MIT / Apache-2.0 / etc.]
```

### README.md Content Rules

| Include | Exclude |
|---------|---------|
| Outcome-focused description | Feature lists ("uses GPT-4, Jinja2, OpenAPI parser") |
| Specific improvement metrics | Vague claims ("saves time", "improves quality") |
| Prerequisites with install links | Implementation details |
| Working install commands | Marketing language without evidence |
| Real usage examples | Changelog entries (put in CHANGELOG.md) |

---

## Step 4: Publishing and Positioning

### Positioning Language (for README and community posts)

Outcome-focused language drives adoption. Show what users achieve, not what the skill uses.

**Pattern**: `[Action verb] [what] [quantifiable improvement]`

```markdown
✅ "Generate API test suites 87% faster than writing them manually"
✅ "Deploy to production in 10 minutes instead of 2 hours"
✅ "Reduce missing test cases by 90% with spec-driven generation"

❌ "Uses OpenAPI parser and Jinja2 templates"
❌ "AI-powered test generation"
❌ "Smart and efficient API testing"
```

### Community Sharing

Current best channels (2026):
1. **Claude Discord** — `#skills` or relevant domain channel; share install command + outcome
2. **GitHub** — Star and fork encourage discovery; good README drives organic sharing
3. **Reddit** — r/ClaudeAI, r/artificial, domain-specific subreddits
4. **Twitter/X** — Demo GIF + install command gets traction
5. **Domain communities** — Dev forums, Slack communities in the skill's target domain

### Support Commitment

State your support policy in the README:

```markdown
## Support

Issues: https://github.com/username/your-skill-name/issues
Response time: [Best effort / within 1 week / actively maintained through YYYY]
```

Be honest. An unsupported skill that works reliably is better than a maintained skill that breaks.

---

## Step 5: Versioning and Updates

### Semantic Versioning

```
v1.0.0  ← stable, ready for production
 │ │ └─ patch: bug fixes, typo corrections
 │ └─── minor: new features, backward compatible (new workflow steps, new references/)
 └───── major: breaking changes (renamed triggers, removed workflow steps, changed output format)
```

```
v0.x.0  ← experimental, API may change
```

### Changelog

Keep a `CHANGELOG.md` or update `references/changelog.md` with each release:

```markdown
## v1.1.0 — 2026-03-15

### Added
- Support for Python test frameworks (pytest, unittest)
- `references/python-examples/` with working test files

### Fixed
- Triggering phrase "write API tests" now activates correctly
- Auth test generation works with Bearer tokens

### Changed
- Output directory default changed from `./tests/` to `./src/__tests__/`
  (upgrade note: update your `.gitignore` if needed)
```

### Notifying Users of Updates

Users who installed via git clone can update with:
```bash
cd ~/.claude/skills/your-skill-name
git pull
```

Add this to your README's "Updating" section.

---

## GitHub Repository Setup Checklist

- [ ] Repo created with correct name (matches skill folder name)
- [ ] `README.md` at repo root with outcome-focused description
- [ ] Installation instructions tested on a fresh machine (or fresh `~/.claude/skills/`)
- [ ] `LICENSE` file added (MIT is common for skills)
- [ ] GitHub Issues enabled for support
- [ ] `CHANGELOG.md` or `references/changelog.md` started
- [ ] Initial tag created: `git tag v0.1.0 && git push --tags`
