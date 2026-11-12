---
name: mermaid-diagrams
description: Render Mermaid diagrams to SVG using beautiful-mermaid and bun. Use when the user asks to "render mermaid", "generate diagram", "create flowchart", "update diagrams", "render SVG from mermaid", "beautiful-mermaid", "regenerate diagrams", or needs to convert Mermaid diagram syntax into styled SVG files. Supports all Mermaid diagram types with 15 themes including tokyo-night.
---

# Mermaid Diagram Rendering with beautiful-mermaid

<purpose>

Render Mermaid `.mmd` files into styled SVG using [beautiful-mermaid](https://github.com/lukilabs/beautiful-mermaid) v1.0.2 and [bun](https://bun.sh).

## Skill Contents

| File | Purpose |
|------|---------|
| `render-mermaid.ts` | CLI tool: renders `.mmd` files to SVG |

`render-mermaid.ts` sits in this skill's own directory. Every command below
runs it by full path through `$MERMAID`, so it works from any directory.

</purpose>

<setup>

## Installation

Only **bun** is required. The `beautiful-mermaid` dependency is auto-installed by bun on first run.

```bash
# macOS
brew install oven-sh/bun/bun

# Or universal installer
curl -fsSL https://bun.sh/install | bash
```

</setup>

<reference>

## CLI Reference

```
render-mermaid — Render Mermaid .mmd files to themed SVG

Usage:
  bun run render-mermaid.ts [options] <input.mmd...>
  echo 'graph TD; A --> B' | bun run render-mermaid.ts -o out.svg

Options:
  -o, --output <file>   Output file (single input only)
  --outdir <dir>        Output directory for multiple files
  -t, --theme <name>    Theme (default: tokyo-night)
  --list-themes         List available themes
  --help                Show this help
```

**Output defaults:** Without `-o` or `--outdir`, writes `<name>.svg` next to each input file (`-d` is swallowed by `bun run`; use `--outdir`).

**Set `MERMAID` once** to the script's full path: the directory this skill is
installed in, plus `/render-mermaid.ts`. For example
`MERMAID=~/.agents/skills/mermaid-diagrams/render-mermaid.ts` for the shared
skills root, or `MERMAID=~/.config/opencode/skills/mermaid-diagrams/render-mermaid.ts`
on OpenCode. In a checkout of the autorun repository it is
`plugins/autorun-skills/skills/mermaid-diagrams/render-mermaid.ts`.

</reference>

<examples>

## Examples

```bash
MERMAID=~/.agents/skills/mermaid-diagrams/render-mermaid.ts   # see "Set MERMAID once"

# Render single file (output next to input)
bun run "$MERMAID" diagram.mmd

# Render to specific output
bun run "$MERMAID" diagram.mmd -o pretty.svg

# Render all .mmd files to output directory
bun run "$MERMAID" docs/diagrams/*.mmd --outdir docs/diagrams

# Different theme
bun run "$MERMAID" -t dracula diagram.mmd

# Pipe from stdin
echo 'graph TD; A --> B --> C' | bun run "$MERMAID" -o quick.svg

# List themes
bun run "$MERMAID" --list-themes
```

</examples>

<limitations>

## Known Limitations

- Complex diagrams with deeply nested subgraphs may hit dagre layout bugs. Failed diagrams are reported but don't block other renders.
- `%%{init:...}%%` front matter is stripped automatically (beautiful-mermaid applies its own theming).
- Write arrows with spaces around them (`A --> B`). beautiful-mermaid reads `A-->B` as one node labelled `A--` and still exits 0, so check the SVG's `data-label` values when a diagram looks wrong.

</limitations>
