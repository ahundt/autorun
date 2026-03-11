# Alternative Recording Tools

Comparison of recording tools beyond the primary asciinema + agg + ffmpeg pipeline.

## Tool Comparison

| Feature | asciinema + agg | VHS (charmbracelet) | t-rec |
|---------|----------------|---------------------|-------|
| **Input format** | Terminal recording → `.cast` | `.tape` script | Screen capture |
| **Output formats** | `.cast` → GIF → MP4 | GIF, MP4, WebM directly | GIF directly |
| **Intermediate file** | `.cast` (editable, mergeable) | None | None |
| **Install** | `brew install asciinema agg` | `brew install vhs` | `brew install t-rec` or `cargo install t-rec` |
| **Language** | Python + Rust | Go | Rust |
| **Scriptable** | Via harness code (Python/Rust/TS) | `.tape` DSL | CLI flags |
| **Terminal size control** | `--cols --rows` or `--window-size` | `Set Width`, `Set Height` | Captures actual window |
| **Font control** | `agg --font-size` | `Set FontSize` | Inherits terminal font |
| **Theme** | `agg --theme dracula` | `Set Theme` | Inherits terminal theme |
| **Speed control** | `agg --speed` | `Set PlaybackSpeed` | Post-processing only |
| **Best for** | Flexible pipeline, cast editing, merging | Simple scripted demos | Screen-level capture |

## VHS (charmbracelet)

VHS uses a `.tape` DSL for declarative demo scripts:

```tape
# demo.tape
Output demo.gif
Set FontSize 20
Set Width 1200
Set Height 700
Set Theme "Dracula"

Type "mytool --help"
Enter
Sleep 3s

Type "mytool stats"
Enter
Sleep 5s
```

**Run**: `vhs demo.tape`

**Advantages**:
- Single file defines the entire demo
- Built-in timing control (`Sleep`, `Set PlaybackSpeed`)
- Produces GIF/MP4 directly (no intermediate `.cast`)
- Declarative — easy to read and modify

**Disadvantages**:
- No `.cast` intermediate — cannot merge recordings
- No dual-purpose (demo + test) pattern — `.tape` is declarative only
- Cannot drive TUI applications interactively
- Limited control over subprocess environment

**Real-world example** (`~/.claude/autorun/plugins/autorun/demo/autorun_demo.tape`):
```tape
Set FontSize 20
Set Width 1200
Set Height 700
Set Theme "Dracula"
Type "uv run python plugins/autorun/tests/test_demo.py --play"
Enter
Sleep 60s
```

## t-rec (Rust screen recorder)

t-rec captures the actual terminal window as frames:

```bash
# Record terminal window to GIF
t-rec -m demo.gif

# With optimized settings
t-rec -m --natural --decor none demo.gif
```

**Advantages**:
- Captures exactly what the user sees (WYSIWYG)
- Works with any terminal application (not limited to text)
- Single binary, no dependencies

**Disadvantages**:
- No intermediate format — cannot edit or merge recordings
- No programmatic control of terminal size or content
- Output is raster (not vector like agg)
- macOS-focused (uses screen capture APIs)
- Requires actual terminal window (not headless)

**Real-world example** (`~/source/pyuvstarter/create_demo.sh`):
```bash
if [ "$RECORD_DEMO" = true ]; then
    t-rec -m "$DEMO_OUTPUT_DIR/demo.gif"
fi
```

## When to Use Each Tool

| Scenario | Recommended Tool |
|----------|-----------------|
| CLI demo with test suite | asciinema + agg + ffmpeg |
| TUI demo with tmux | asciinema + agg + ffmpeg |
| Quick one-off demo | VHS |
| Merging multiple recordings | asciinema (only tool with editable intermediate) |
| Screen-level capture | t-rec |
| CI/CD demo generation | asciinema (headless) or VHS |
| Multi-shell demo | asciinema (per-shell `.cast` files, then merge) |

## Migration: VHS to asciinema

If outgrowing VHS, convert the `.tape` to a Python/Rust harness:

1. Each `Type` + `Enter` + `Sleep` block becomes an act function
2. `Set FontSize/Width/Height` become asciinema `--cols`/`--rows` + agg `--font-size`
3. `Set Theme` becomes agg `--theme`
4. Add `--test` flag for CI integration (VHS has no equivalent)
5. Add privacy isolation (VHS inherits raw environment)
