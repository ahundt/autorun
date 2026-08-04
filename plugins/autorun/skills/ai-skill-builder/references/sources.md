# Sources

Checked: 2026-08

## Primary

- [Anthropic: The Complete Guide to Building Skills for Claude](https://resources.anthropic.com/hubfs/The-Complete-Guide-to-Building-Skill-for-Claude.pdf)
  (January 2026) — authoritative source for the 4-phase methodology, skill categories,
  progressive disclosure structure, SKILL.md/kebab-case naming rules, YAML frontmatter
  requirements, 5,000-word hard limit, description field constraints (1024 chars, no angle
  brackets, trigger-phrase format), folder taxonomy (references/, scripts/, assets/),
  distribution channels, testing framework. Local copy: `../ai-skill-builder-guide.pdf`

- [Claude Code skills documentation](https://code.claude.com/docs/en/skills)
  — confirmed allowed-tools frontmatter field format, skill loading behavior, plugin
  structure vs standalone skill structure, skill auto-detection via YAML frontmatter.
  Claude Code specific: do not generalize this behavior to other hosts.

- [Anthropic prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)
  — confirmed descriptive XML tags help separate mixed instructions, context, and examples
  in complex prompts, and that examples improve output consistency. Supports body-structure
  guidance as a clarity technique; establishes no cross-model percentage and no security
  property.

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

- plugin-dev:skill-development `SKILL.md` — Anthropic's official skill-creation plugin.
  Confirmed trigger-phrase description format, imperative writing style requirement, and the
  ideally-under-2,000-word guideline (plugin-dev origin, distinct from the PDF's 5,000-word
  hard limit). Installed locally at
  `~/.claude/plugins/cache/claude-code-plugins/plugin-dev/<version>/skills/skill-development/`;
  the version segment changes on every plugin release, so resolve it rather than copying a path.

- [YAML specification](https://yaml.org/spec/)
  — confirmed YAML frontmatter syntax requirements

- [CommonMark specification](https://spec.commonmark.org/current/#fenced-code-blocks)
  — confirmed that a fenced code block ends at the first fence line with no info string and at
  least as many backticks as its opener. A ```` ```bash ```` line written inside a ``` block is
  literal text, and the next bare ``` ends the outer block early. Basis for the code-fence check
  in `scripts/audit-skill.sh` and for using a four-backtick outer fence when nesting.

- [Semantic Versioning](https://semver.org/)
  — confirmed MAJOR.MINOR.PATCH format used when a skill records a version in metadata

## Discrepancies

- **Word count target**: three separate limits from three sources, in different units.
  The Anthropic PDF states a 5,000-**word** hard limit. The 1,500-2,000 word "target" comes
  from plugin-dev:skill-development, not the PDF. The Agent Skills specification recommends
  instructions under 5,000 **tokens** and `SKILL.md` under 500 lines. `scripts/audit-skill.sh`
  warns above 2,000 words and fails above 5,000. Treat all of them as heuristics unless a
  named target runtime makes one normative.

- **examples/ directory placement**: Anthropic PDF anatomy shows `references/examples/` as
  a subdirectory; plugin-dev:skill-development shows top-level `examples/`. Resolved by
  context: plugin-dev convention applies to plugin skills; PDF/skill-creator anatomy applies
  to standalone `~/.claude/skills/` skills. Both are documented with their context.

- **Description field format**: PDF uses capability-first format ("Generates X from Y. Use
  when user asks..."); plugin-dev uses third-person trigger-only format ("This skill should
  be used when the user wants to..."). Both formats are valid and both satisfy
  `scripts/audit-skill.sh`, which requires quoted trigger phrases rather than a particular
  opening clause. Both are shown as Format A and Format B under "Description Writing Formula"
  in `best-practices.md`. Outcome-only descriptions ("87% faster") satisfy neither and fail
  the audit.

## Claims recorded without a retrievable source

Listed here rather than asserted in the guidance, so that nobody repeats them as measured.

- **Skill activation rates by description shape.** Through v1.1, `SKILL.md` and
  `scripts/audit-skill.sh` cited `notes/2026_03_reliable_skill_usage_and_design.md` for
  "250 sandboxed evals", "specific technical terms trigger 100% activation", and "conceptual
  queries fail 60-80% of the time". That file was never present in the package and no
  publication matching it was located, so none of the figures can be checked. The underlying
  advice — quote the literal phrases a user would type — is independently supported by the
  Anthropic PDF and plugin-dev, and is what the script now checks. The percentages are not.
