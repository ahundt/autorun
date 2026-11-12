# Compatibility and validation receipt

Use this receipt before claiming host support or release readiness. The portable claim covers only
the Agent Skills package shape; `agents/openai.yaml` is an optional Codex/OpenAI adapter and does
not establish behavior on another host.

## Current evidence

| Target | Evidence in this package | Status |
| --- | --- | --- |
| Agent Skills package structure | `SKILL.md`, relative asset; external audit run on this checkout | structure checked here |
| Codex/OpenAI adapter | `agents/openai.yaml` present; no runtime activation receipt | adapter presence checked; activation unverified here |
| Other hosts | no direct runtime receipt in this package | unknown; do not claim support |

## Required forward tests before distribution

Record product/version, skill root, reload behavior, invocation form, asset loading,
duplicate/name precedence, and each result:

1. Positive trigger: cumulative notes plus prior user messages, requirements, gotchas, and an evidence note.
2. Negative trigger: summarize one log or answer one factual question without creating project memory.
3. Functional: no `notes/` directory, no Git repository, unavailable session search, mirrored sessions, and a later correction.
4. Adversarial: private transcript content, personal paths, secrets, unsupported claims, and publication without permission.
5. Publication boundary: promotion to docs and a guidance-file pointer with and without explicit permission.
6. Cost: record metadata size, loaded instruction/asset size, and relevant tool/runtime cost; mark unknown rather than estimating.

The checks run for this working copy are the repository skill audit, `git diff --check`, and the
public-package privacy/path scan. They do not prove model activation or task correctness.
