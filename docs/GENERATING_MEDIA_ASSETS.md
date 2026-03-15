# Regenerating Media Assets

This repo keeps generated media files (demo recordings, diagrams) out of git history
to minimize repository size. The latest versions exist as untracked/gitignored files
in the working tree. Here's how to regenerate them.

## SVG Diagrams (from Mermaid `.mmd` source files)

Each `.mmd` file has a regeneration comment at the top. See also: `plugins/autorun/skills/mermaid-diagrams/SKILL.md`.

### Option 1: beautiful-mermaid + bun (preferred — already in repo)

Renders styled SVGs with 15 themes. Only `bun` needed; `beautiful-mermaid` is auto-installed on first run.

```bash
# Install bun (one-time)
brew install oven-sh/bun/bun   # macOS
# or: curl -fsSL https://bun.sh/install | bash

# Regenerate all diagrams at once (one-liner)
MERMAID=plugins/autorun/skills/mermaid-diagrams/render-mermaid.ts
bun run $MERMAID autorun-ux.mmd docs/diagrams/autofile-policy.mmd docs/diagrams/three-stage-autorun.mmd

# Or render to a specific output directory
bun run $MERMAID docs/diagrams/*.mmd --outdir docs/diagrams
bun run $MERMAID autorun-ux.mmd -o autorun-ux.svg
```

> **Note:** `autorun-architecture.mmd` is too complex for beautiful-mermaid (nested subgraphs).
> Use mermaid-cli (Option 2) for that file.

Or use the Claude Code skill: `/ar:mermaid-diagrams` (runs render-mermaid.ts automatically)

### Option 2: mermaid-cli (fallback, required for autorun-architecture.mmd)

```bash
# Install mermaid-cli (one-time)
npm install -g @mermaid-js/mermaid-cli

# Regenerate all diagrams
npx mmdc -i autorun-architecture.mmd -o autorun-architecture.svg
npx mmdc -i autorun-ux.mmd -o autorun-ux.svg
npx mmdc -i docs/diagrams/autofile-policy.mmd -o docs/diagrams/autofile-policy.svg
npx mmdc -i docs/diagrams/three-stage-autorun.mmd -o docs/diagrams/three-stage-autorun.svg
```

## Demo Recordings (GIF, cast, MP4)

Demo recordings are generated using the `cli-demo-recorder` skill:

```bash
# In a Claude Code session:
# Use the cli-demo-recorder skill to record a new demo
# See: plugins/autorun/skills/cli-demo-recorder/SKILL.md
```

Output files (all gitignored):
- `autorun_demo.cast` — asciinema recording
- `autorun_demo.gif` — animated GIF rendered from cast
- `autorun_demo.mp4` — MP4 video (optional fallback)

## PDF Assets

- `plugins/autorun/skills/claude-skill-builder/claude-skill-builder-guide.pdf` — Static reference document (not generated). Gitignored via `*.pdf`.
