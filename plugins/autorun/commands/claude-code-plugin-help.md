---
description: Reference the supported Claude Code command, skill, plugin, and hook surfaces
---

# Claude Code Plugin Reference

Use Claude Code's current documented interfaces rather than treating this file
as a substitute runtime specification.

## Commands And Skills

- Files under `commands/` remain supported as legacy custom commands.
- Skills live in a directory containing `SKILL.md` and may include supporting
  files. Skills are preferred for new reusable workflows.
- Plugin command names are namespaced by the installed plugin. Autorun's
  command prefix is `/ar:` in Claude Code.
- Skill invocation is harness-specific. Do not document a skill as an
  `/ar:*` command unless a matching file exists under `commands/`.

## Dynamic Context

Claude Code uses this form to run a shell command before sending command or
skill content to the model:

```markdown
!`command`
```

The command output replaces the placeholder. A bare `! command` line is not
the documented dynamic-context syntax.

Keep executable snippets narrow, quote paths and arguments, and use `uv run`
for autorun's Python entry points. Hook executables must reserve stdout for the
harness response protocol; diagnostics belong on stderr or in logs.

## Authoritative References

- Skills, frontmatter, legacy commands, and dynamic context:
  https://code.claude.com/docs/en/skills
- Plugin manifest and component layout:
  https://code.claude.com/docs/en/plugins-reference
- Hook events and JSON input/output:
  https://code.claude.com/docs/en/hooks

For autorun's installed interface inventory, run
`autorun --capability-snapshot`. It reports platforms, runtime command aliases,
command documents, skills, and hook chains without changing user configuration.
