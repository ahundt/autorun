# Regenerating Media Assets

This repo keeps generated media files (demo recordings, diagrams) out of git history
to minimize repository size. The latest versions exist as untracked/gitignored files
in the working tree. Here's how to regenerate them.

## SVG Diagrams (from Mermaid `.mmd` source files)

Each `.mmd` file has a regeneration comment at the top. Install mermaid-cli and run:

```bash
# Install mermaid-cli (one-time)
npm install -g @mermaid-js/mermaid-cli

# Regenerate individual diagrams
npx @mermaid-js/mermaid-cli mmdc -i autorun-architecture.mmd -o autorun-architecture.svg
npx @mermaid-js/mermaid-cli mmdc -i autorun-ux.mmd -o autorun-ux.svg
npx @mermaid-js/mermaid-cli mmdc -i docs/diagrams/autofile-policy.mmd -o docs/diagrams/autofile-policy.svg
npx @mermaid-js/mermaid-cli mmdc -i docs/diagrams/three-stage-autorun.mmd -o docs/diagrams/three-stage-autorun.svg
```

Or use the `mermaid-diagrams` skill in Claude Code: `/ar:mermaid-diagrams`

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
