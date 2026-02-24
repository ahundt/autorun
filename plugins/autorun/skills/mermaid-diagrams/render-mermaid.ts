#!/usr/bin/env bun
/**
 * render-mermaid — Render Mermaid .mmd files to themed SVG.
 *
 * Usage:
 *   bun run render-mermaid.ts <input.mmd...> [-o output.svg] [-d outdir] [-t theme]
 *   bun run render-mermaid.ts --list-themes
 *   echo 'graph TD; A-->B' | bun run render-mermaid.ts -o diagram.svg
 *
 * Dependencies (beautiful-mermaid) are auto-installed by bun on first run.
 */
import { renderMermaidSVGAsync, THEMES } from 'beautiful-mermaid'
import { writeFileSync, readFileSync, mkdirSync, existsSync } from 'fs'
import { resolve, basename, dirname, extname } from 'path'

const HELP = `render-mermaid — Render Mermaid .mmd files to themed SVG

Usage:
  bun run render-mermaid.ts [options] <input.mmd...>
  echo 'graph TD; A-->B' | bun run render-mermaid.ts -o out.svg

Options:
  -o, --output <file>   Output file (single input only)
  --outdir <dir>        Output directory for multiple files
  -t, --theme <name>    Theme (default: tokyo-night)
  --list-themes         List available themes
  --help                Show this help

Note: -d and -h are intercepted by "bun run". Use --outdir and --help instead.

Output defaults:
  Single file without -o: writes <name>.svg next to the input
  Multiple files: writes .svg next to each input
  With --outdir: writes all .svg files into that directory

Examples:
  bun run render-mermaid.ts diagram.mmd
  bun run render-mermaid.ts diagram.mmd -o pretty.svg
  bun run render-mermaid.ts *.mmd --outdir docs/diagrams
  bun run render-mermaid.ts -t dracula diagram.mmd`

// --- Arg parsing ---
const args = process.argv.slice(2)
let themeName = 'tokyo-night'
let outDir: string | null = null
let outputFile: string | null = null
const inputFiles: string[] = []

for (let i = 0; i < args.length; i++) {
  const a = args[i]
  const next = () => {
    if (i + 1 >= args.length) {
      console.error(`Error: ${a} requires a value`)
      process.exit(1)
    }
    return args[++i]
  }

  switch (a) {
    case '-t': case '--theme':  themeName = next(); break
    case '--outdir': outDir = next(); break
    case '-o': case '--output': outputFile = next(); break
    case '--list-themes':
      console.log(Object.keys(THEMES).join('\n'))
      process.exit(0)
    case '--help':
      console.log(HELP)
      process.exit(0)
    default:
      if (a.startsWith('-')) {
        console.error(`Error: Unknown option "${a}". Use --help for usage.`)
        process.exit(1)
      }
      inputFiles.push(a)
  }
}

// --- Validate ---
if (outputFile && outDir) {
  console.error('Error: Cannot use both -o and -d together.')
  process.exit(1)
}
if (outputFile && inputFiles.length > 1) {
  console.error('Error: -o can only be used with a single input file.')
  process.exit(1)
}

const theme = THEMES[themeName]
if (!theme) {
  console.error(`Error: Unknown theme "${themeName}". Use --list-themes.`)
  process.exit(1)
}

// --- Counters ---
let ok = 0, failed = 0

// --- Collect render jobs: [{name, source, dest}] ---
type Job = { name: string; source: string; dest: string }
const jobs: Job[] = []

if (inputFiles.length === 0) {
  // Read from stdin
  if (process.stdin.isTTY) {
    console.error('Error: No input files specified. Use --help for usage.')
    process.exit(1)
  }
  const stdinData = await new Promise<string>((res) => {
    let d = ''
    process.stdin.on('data', (c: Buffer) => { d += c.toString() })
    process.stdin.on('end', () => res(d))
  })
  if (!stdinData.trim()) {
    console.error('Error: No input files and stdin is empty. Use --help.')
    process.exit(1)
  }
  if (!outputFile) {
    console.error('Error: Reading from stdin requires -o <output.svg>.')
    process.exit(1)
  }
  jobs.push({ name: 'stdin', source: stdinData, dest: outputFile })
} else {
  for (const inputPath of inputFiles) {
    const ext = extname(inputPath).toLowerCase()
    const name = basename(inputPath, ext)

    // Catch common mistakes: passing .svg (output) or non-mermaid files as input
    if (ext === '.svg') {
      console.error(`  SKIP  ${inputPath}: this is an SVG (output), not a .mmd source. Did you mean the .mmd file?`)
      failed++
      continue
    }
    if (ext && ext !== '.mmd' && ext !== '.mermaid') {
      console.error(`  WARN  ${inputPath}: unexpected extension "${ext}" (expected .mmd or .mermaid)`)
    }

    if (!existsSync(inputPath)) {
      console.error(`  FAIL  ${name}: file not found: ${inputPath}`)
      failed++
      continue
    }
    const source = readFileSync(inputPath, 'utf-8')
    let dest: string
    if (outputFile) {
      dest = outputFile
    } else if (outDir) {
      dest = resolve(outDir, `${name}.svg`)
    } else {
      dest = resolve(dirname(inputPath), `${name}.svg`)
    }
    // Prevent overwriting input with output
    if (resolve(inputPath) === resolve(dest)) {
      console.error(`  FAIL  ${name}: output would overwrite input file. Use -o or -d.`)
      failed++
      continue
    }
    jobs.push({ name, source, dest })
  }
}

if (jobs.length === 0) {
  console.error('Error: No valid input files found.')
  process.exit(1)
}

if (outDir) mkdirSync(outDir, { recursive: true })

// --- Render ---
for (const { name, source, dest } of jobs) {
  try {
    // Strip %%{init:...}%% front matter — beautiful-mermaid handles theming
    const cleaned = source.replace(/^%%\{init:.*?\}%%\n?/s, '')
    const svg = await renderMermaidSVGAsync(cleaned, theme)
    mkdirSync(dirname(dest), { recursive: true })
    writeFileSync(dest, svg)
    console.log(`  ok  ${dest}`)
    ok++
  } catch (e: any) {
    console.error(`  FAIL  ${name}: ${e.message?.split('\n')[0]}`)
    failed++
  }
}

if (ok + failed > 1 || failed > 0) {
  console.log(`\n${ok} rendered, ${failed} failed`)
}
if (failed > 0) process.exit(1)
