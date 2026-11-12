# Agent Skills portability and claim audit

Use this reference when a skill targets more than one runtime, when third-party guidance makes
universal claims, or when a claim about frontmatter, XML, skill roots, symlinks, arguments, or
security needs checking. Recheck linked primary documentation because harness behavior evolves.
It backs SKILL-REQ002, SKILL-REQ004, SKILL-REQ009, and SKILL-REQ010 in `SKILL.md`.

## Portable specification core

The current Agent Skills specification requires a directory containing `SKILL.md`, YAML
frontmatter, and a Markdown body. The standard fields are:

| Field | Standard status | Important constraint |
| --- | --- | --- |
| `name` | Required | 1–64 lowercase alphanumeric/hyphen characters; no edge or consecutive hyphens; match parent directory |
| `description` | Required | 1–1024 characters; state what the skill does and when to use it |
| `license` | Optional | Short license name or bundled license reference |
| `compatibility` | Optional | 1–500 characters; only when environment requirements matter |
| `metadata` | Optional | String-to-string extension metadata |
| `allowed-tools` | Optional, experimental | Space-separated string; support varies by implementation |

The portable body has no mandated XML or section taxonomy. This methodology nevertheless mandates
consistent semantic XML regions as a higher-quality authoring policy (SKILL-REQ004). Keep those
two facts separate: portable parsers need only accept Markdown, while packages produced by this
methodology must also pass balanced-tag validation (`scripts/audit-skill.sh`, section 4) and real
target-runtime task evaluation. `scripts/`, `references/`, and `assets/` are optional
conventions. The specification recommends instructions below 5,000 tokens, `SKILL.md` below 500
lines, focused one-hop references, and validation with `skills-ref`; those are recommendations,
not evidence that every runtime has the same loader or execution policy.

Primary source: <https://agentskills.io/specification>

## Runtime differences that must remain explicit

| Capability | Portable standard | Claude Code | GitHub Copilot | Local Codex authoring policy, not runtime-support evidence |
| --- | --- | --- | --- | --- |
| `name`, `description` | Required | More permissive parser; description recommended | Required in documented simple form | Requires only these fields in `SKILL.md` frontmatter |
| `license`, `compatibility`, `metadata` | Optional | Runtime-dependent acceptance | `license` documented; check other fields per surface | Put product UI data in `agents/openai.yaml`, not extra `SKILL.md` frontmatter |
| `allowed-tools` | Experimental string | Supported with Claude-specific permission semantics and extra tool fields | Supported; dangerous shell approval warning | Do not assume portable behavior |
| `when_to_use` | Not standard | Claude extension | Not established by the cited GitHub guide | Put triggers in `description` |
| `arguments` and argument hints | Not standard | Claude extensions with `$ARGUMENTS`, `$N`, and named positions | Invocation syntax differs; verify current product | Not a portable frontmatter schema; `quick_validate.py` rejects `argument-hint` and `aliases` |
| `context: fork`, `agent`, `background`, `effort`, `model` | Not standard | Claude extensions | Not established by the cited GitHub guide | Do not put in portable core |
| Top-level `version` | Not standard | Ignored | Not established | Rejected by `quick_validate.py`; use `metadata.version` |
| Project roots | Implementation-defined | `.claude/skills` | `.github/skills`, `.claude/skills`, `.agents/skills` | `.agents/skills` is the project convention here |
| Personal roots | Implementation-defined | `~/.claude/skills` | `~/.copilot/skills`, `~/.agents/skills` | Harness-specific install system |
| Symlinks | Implementation-defined | Supported for current personal/project skill entries, with version-specific behavior; the `skills/` directory itself must not be a symlink (anthropics/claude-code#38051) | Verify per product and OS | Test rather than infer |

### Evidence status

| Target | Evidence status | What is and is not established |
| --- | --- | --- |
| Agent Skills portable format | Primary specification checked 2026-07-27 | Format fields, body freedom, directories, recommendations, and validation command |
| Claude Code | Official current docs checked 2026-07-27 | Locations, symlink behavior, frontmatter extensions, arguments, forked context, and lifecycle; no cross-version guarantee |
| GitHub Copilot surfaces | Official current docs checked 2026-07-27 | Documented project/personal roots and basic/allowed-tool behavior; no blanket claim for every Copilot host/version |
| Microsoft Agent Framework | Official current docs checked 2026-07-27 | Provider-driven progressive disclosure and experimental MCP skill source; not a filesystem-root claim for unrelated harnesses |
| Local Codex skill creator | Installed authoring policy and validator checked 2026-07-27 and 2026-08-15 | This package's accepted structure; `quick_validate.py` allows only `allowed-tools`, `description`, `license`, `metadata`, `name` at top level; not proof of discovery/invocation in every Codex product |
| Qwen Code | Official docs checked 2026-08 (`references/sources.md`) | Personal, project, and extension skill locations; model- and user-invocation behavior; no cross-version guarantee |
| Codex/ChatGPT | Official docs checked 2026-08 (`references/sources.md`) | Discovery, explicit invocation, progressive disclosure; validator behavior above |
| Pi, Prime Agent, OpenCode, ForgeCode, Antigravity, Gemini CLI, OpenClaw, GLM, and other named runtimes | Unknown/not assessed here | Do not claim compatibility until official documentation and direct forward tests fill the validation receipt |

Primary sources:

- Claude Code skills: <https://code.claude.com/docs/en/skills>
- GitHub Copilot skills:
  <https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/add-skills>
- Microsoft Agent Framework skills: <https://learn.microsoft.com/en-us/agent-framework/agents/skills>
- Current installed Codex skill-creator instructions and `scripts/quick_validate.py`
  (environment-specific; reread them from the active harness before authoring)

## Claim audit

Claims met in third-party "master protocols" and marketing, with the assessment this methodology
adopts. Where a claim is rejected, the corrected guidance is what `SKILL.md` teaches.

| Supplied claim | Assessment | Corrected guidance |
| --- | --- | --- |
| Agent Skills is an open portable format | Retain | Portability applies to the common format; every runtime still needs direct compatibility tests |
| Discovery loads name/description, then instructions and resources on demand | Retain as the standard model | Do not promise identical startup, caching, script, or context behavior in every implementation |
| The portable Agent Skills specification requires XML | Reject | The specification requires Markdown and imposes no body format restrictions |
| This methodology requires semantic XML regions | Retain as an explicit quality policy | Apply balanced, descriptive tags to major instruction regions and forward-test task outcomes in every target runtime |
| Visual HTML is ignored by transformers | Reject | Models process HTML tokens; usefulness depends on semantics and task, not a universal ignore rule |
| Custom XML tags are hard instruction/security boundaries | Reject | XML can improve clarity for complex prompts but is not an authorization or injection boundary |
| XML reduces failures by 28–40 percent | Reject without a reproducible primary benchmark | Do not import precise performance claims from secondary marketing/blog sources |
| Every methodology XML region must be balanced and clearly delimited | Retain as a methodology rule | This makes the selected XML structure mechanically auditable; it remains separate from portable parser requirements |
| Raw angle brackets break YAML scalars | Reject as a universal claim | YAML parses `<` and `>` in a plain scalar. The frontmatter ban is a host restriction: Anthropic's skill guide lists "XML angle brackets" as forbidden in frontmatter and Claude Code documents that `description` must not contain XML tags. Enforce it for those hosts; do not explain it as YAML |
| "No XML tags anywhere" (Anthropic guide checklist wording) | Conditional | The enumerated restriction in the same guide is frontmatter-only (Reference B). Body regions rest on Anthropic's prompting guidance and are this methodology's policy. A host that rejects body tags on upload is recorded as unsupported in the validation receipt rather than assumed |
| `when_to_use`, `context`, `effort`, and `arguments` are universal frontmatter | Reject | These are runtime extensions, notably in current Claude Code, not Agent Skills core fields |
| `allowed-tools` is an array | Reject for the portable spec | The standard defines an experimental space-separated string; some runtimes accept other shapes |
| Directory and `name` must match | Retain for the portable spec | Enforce it in portable packages even if a permissive runtime allows otherwise |
| Keep `SKILL.md` below 500 lines and move details to references | Retain as a recommendation | Optimize for task success and loaded tokens; do not split cohesive instructions merely to satisfy a number |
| `.github/skills`, `.claude/skills`, and `.agents/skills` are universally discovered | Reject | These are product-specific locations; GitHub documents all three, Claude documents `.claude/skills` |
| Scripts need executable permissions | Conditional | Depends on how the runtime invokes them; still test permissions, interpreter, dependencies, and errors |
| All major named frontier and open-source runtimes are compatible | Unsupported | Claim only products and versions directly documented and forward-tested |

Anthropic's official prompting guidance says XML tags can help Claude distinguish mixed prompt
components. That supports this methodology's quality choice, but it does not make XML mandatory in
the portable Agent Skills specification or establish XML as prompt-injection prevention:
<https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices#structure-prompts-with-xml-tags>.

The supplied 28–40 percent improvement claim is not adopted. It was not backed by a reproducible
primary benchmark spanning the target runtimes and actual skill tasks. Measure automatic
activation, instruction adherence, task correctness, and regression rate on representative tasks;
the methodology mandate stands as a selected engineering policy and should still be improved when
better direct evidence becomes available.

## Validation receipt

For every claimed target, record:

```text
product and version:
skill root:
portable fields accepted:
runtime extensions used:
automatic activation:
explicit invocation:
arguments:
references:
scripts:
symlink behavior:
duplicate/name precedence:
live reload or restart:
install/update/uninstall:
negative and adversarial prompts:
measured context/runtime cost:
unsupported or unknown:
```

Attach the receipts to the skill's changelog or release notes. A target without a receipt is
"unknown", never "supported".
