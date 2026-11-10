# Compatibility and validation

Read this before claiming host support or replacing an installed copy. Keep one canonical `task-lifecycle-workflow` installation; overlapping copies can produce duplicate or ambiguous activation.

## Support boundary

| Target | Required evidence | Claim boundary |
| --- | --- | --- |
| portable Agent Skills package | valid `SKILL.md`, relative package-local references, public-safe contents | static package checks only |
| Codex/OpenAI display adapter | valid `agents/openai.yaml`; canonical `$task-lifecycle-workflow` prompt | adapter presence only until runtime testing |
| dynamic subagent route | host exposes delegation; complete role packet is sent; result returns to the named join/orchestrator | behavior on that tested host/version only |
| named Codex agent | standalone TOML at user `~/.codex/agents/` or project `.codex/agents/`; discovery and spawn test | host configuration, not a portable skill resource |
| named Claude Code agent | Markdown at user/project `.claude/agents/` or plugin-root `agents/`; discovery and spawn test | Claude-specific adapter, not a portable skill resource |
| any host | discovery, positive/negative activation, reference loading, behavior, reload, duplicate handling | unknown until tested on that product/version |

The portable package does not define persistent background agents or a cross-runtime named-agent file format. Roles are logical; a host may receive the directly linked role packets through dynamic subagents, named host agents, sequential specialists, or one orchestrator running separate passes. `agents/openai.yaml` is skill metadata, not the trackkeeper. A portable trackkeeper finding is advisory data until a named runtime or harness proves that it can enforce the affected-action gate independently; the orchestrator-only fallback must disclose its weaker independence. AI Session Search is optional and has an explicit local-evidence fallback.

## Development-only static checker

When developing or validating this skill package, explicitly run
[scripts/check_workflow.py](../scripts/check_workflow.py). It is development tooling,
not a lifecycle step for ordinary tasks; do not load its source as workflow instructions.

From the skill directory:

```bash
uv run --no-project python scripts/check_workflow.py
```

The checker requires only Python 3's standard library; `uv` is optional. It accepts no
required arguments, locates the package relative to its own file, and works from any
working directory when invoked by path. It reads files without modifying them, prints
PASS/FAIL results, and exits nonzero on a failed check or unreadable input. Checks cover
specific graph paths, terminology, and inline/source parity; they do not execute a model,
validate Mermaid syntax, or establish host enforcement. No hook runs it automatically.

## Required checks

For skill development, package evaluation, or installation validation, retain commands/results in the task's evidence log or final receipt, not in this skill package:

1. Package audit, frontmatter validation, and balanced semantic XML regions and assignment/correction envelopes.
2. Relative-link and public-package privacy scan.
3. Verify exact parity between all four package `.mmd` sources and their inline graphs. Do not generate SVG or PNG validation artifacts for this AI-facing package.
4. Positive activation: evolving multi-stage task with research, agents, correction, cold read, and a readiness gate.
5. Negative activation: direct factual answer, typo, and small reversible one-owner task.
6. Functional cases: missing required branch, failed check, correction during work, correction acknowledgement, stale in-flight result, pause/resume, orchestrator finding disposition, complete role-packet delivery, and sequential-host fallback.
7. Adversarial cases: coupled writes offered as parallel, unsafe mutation without permission, conflicting worker reports, fabricated citation, private evidence requested in public output, an off-rails worker, an off-rails orchestrator, a trackkeeper attempting to command a worker, an evidence-free finding rejection, and a premature user question resolvable from the outcome, precedent, or a safe default.
8. Scale/cost: compare the fast, standard, and full routes; record instruction/reference size and relevant tool/runtime use instead of estimating.
9. Replacement rehearsal: install under one canonical name in an isolated or recoverable host state, reload, invoke, confirm conditional reference loading, and verify no duplicate activation.
10. When prose or workflow text changed, verify the recovery point, atomic fact index, restore decisions, whole-file reread, and every removed diff segment; repeat after fixes.

## Evidence receipt

Record the product and version, canonical install root, reload behavior, invocation form, activated description, references loaded, commands or interactions, outputs, side effects, result, limitations, and duplicate-resolution behavior. Record instruction/reference size and relevant runtime or tool cost when measured; mark unmeasured values unknown.

Static structure, link, privacy, and Mermaid checks do not prove model activation or task correctness. A runtime support claim requires direct behavior evidence from that named runtime. Preserve unrelated work during replacement, and remove an obsolete copy only when its exact path is known and removal is authorized.
