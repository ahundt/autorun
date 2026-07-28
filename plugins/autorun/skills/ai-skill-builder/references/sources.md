# Sources

Checked: 2026-07

## Primary

- [Anthropic: The Complete Guide to Building Skills for Claude](https://resources.anthropic.com/hubfs/The-Complete-Guide-to-Building-Skill-for-Claude.pdf)
  (January 2026) — authoritative source for the 4-phase methodology, skill categories,
  progressive disclosure structure, SKILL.md/kebab-case naming rules, YAML frontmatter
  requirements, 5,000-word hard limit, description field constraints (1024 chars, no angle
  brackets, trigger-phrase format), folder taxonomy (references/, scripts/, assets/),
  distribution channels, testing framework. Local copy: `../ai-skill-builder-guide.pdf`

- [Claude Code documentation](https://docs.anthropic.com)
  — confirmed allowed-tools frontmatter field format, skill loading behavior, plugin
  structure vs standalone skill structure, skill auto-detection via YAML frontmatter

- [Agent Skills specification](https://agentskills.io/specification)
  — confirmed the portable `SKILL.md`, `scripts/`, `references/`, and `assets/`
  package shared across compatible agent hosts

- [OpenAI: Build skills](https://learn.chatgpt.com/docs/build-skills)
  — confirmed Codex/ChatGPT skill discovery, explicit invocation, progressive
  disclosure, and the shared Agent Skills standard

- [Qwen Code: Agent Skills](https://qwenlm.github.io/qwen-code-docs/en/users/features/skills/)
  — confirmed Qwen personal, project, and extension skill locations plus
  model- and user-invocation behavior

- [Model Context Protocol specification](https://modelcontextprotocol.io)
  — confirmed MCP server tool list/parameter schema behavior referenced in Category 3
  skill guidance; basis for MCP Enhancement skill category research targets

## Secondary

- [Nielsen Norman Group: Progressive Disclosure](https://www.nngroup.com/articles/progressive-disclosure/)
  — confirmed 3-level progressive disclosure pattern (hook → workflow → detail);
  basis for SKILL.md structure guidance

- [plugin-dev:skill-development SKILL.md](~/.claude/plugins/cache/claude-code-plugins/plugin-dev/0.1.0/skills/skill-development/SKILL.md)
  — Anthropic's official skill-creation plugin; confirmed trigger-phrase description
  format, imperative writing style requirement, ideally-under-2,000-word guideline
  (plugin-dev origin, distinct from PDF's 5,000-word hard limit)

- [YAML specification](https://yaml.org/spec/)
  — confirmed YAML frontmatter syntax requirements

- [CommonMark specification](https://commonmark.org/)
  — confirmed Markdown rendering behavior for SKILL.md content

- [Semantic Versioning](https://semver.org/)
  — confirmed MAJOR.MINOR.PATCH format used when a skill records a version in metadata

## Discrepancies

- **Word count target**: Anthropic PDF states only a 5,000-word hard limit; the 1,500-2,000
  word "target" appears in plugin-dev:skill-development but not the PDF. Both are documented
  separately — PDF limit as hard rule, plugin-dev guideline as a quality target.

- **examples/ directory placement**: Anthropic PDF anatomy shows `references/examples/` as
  a subdirectory; plugin-dev:skill-development shows top-level `examples/`. Resolved by
  context: plugin-dev convention applies to plugin skills; PDF/skill-creator anatomy applies
  to standalone `~/.claude/skills/` skills. Both are documented with their context.

- **Description field format**: PDF uses capability-first format ("Generates X from Y. Use
  when user asks..."); plugin-dev uses third-person trigger-only format ("This skill should
  be used when the user wants to..."). Both formats are valid; documented as Format A and
  Format B in SKILL.md.
