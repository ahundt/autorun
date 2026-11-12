# Rust Demo Harness Patterns

Patterns from the canal project (`~/source/canal/canal/src/bin/`), which implements both CLI and TUI demo harnesses in Rust.

## Architecture

Canal uses two separate binaries for demo recording:

- `can-cli-demo.rs` (~970 lines) — CLI pathway: runs `can suggest`, `can explain`, `can fix` with typing animation
- `can-demo.rs` (~900 lines) — TUI pathway: launches real shell sessions via tmux, demonstrates ghost text, typo correction, command_not_found

Both are defined as `[[bin]]` entries in `Cargo.toml`:

```toml
[[bin]]
name = "can-cli-demo"
path = "src/bin/can-cli-demo.rs"

[[bin]]
name = "can-demo"
path = "src/bin/can-demo.rs"
```

## Run Mode Pattern (AtomicBool)

Control timing globally with a static atomic flag:

```rust
use std::sync::atomic::{AtomicBool, Ordering};

static RUN_MODE: AtomicBool = AtomicBool::new(false);

fn is_run_mode() -> bool {
    RUN_MODE.load(Ordering::Relaxed)
}

// In act functions, conditionally sleep:
if is_run_mode() {
    std::thread::sleep(Duration::from_millis(pause_ms));
}
```

This pattern enables dual-purpose execution:
- `cargo run --bin can-demo -- --run` — animated demo with pauses
- `cargo run --bin can-demo -- --test` — CI-safe, no timing delays
- `cargo run --bin can-demo -- --record` — animated + asciinema wrapping

## Multi-Shell Support

Canal supports 5 shells with per-shell shim paths resolved at compile time:

```rust
// can-demo.rs:26-31
let shim_dir = format!("{}/shims", env!("CARGO_MANIFEST_DIR"));
let shells = vec![
    ("zsh",   format!("{shim_dir}/zsh/canal-zsh-shim")),
    ("bash",  format!("{shim_dir}/bash/canal-bash-shim")),
    ("fish",  format!("{shim_dir}/fish/canal-fish-shim")),
    ("nu",    format!("{shim_dir}/nu/canal-nu-shim")),
    ("xonsh", format!("{shim_dir}/xonsh/canal-xonsh-shim")),
];
```

Each shell produces a separate `.cast` file:
```
demos/canal-interactive.cast          # default shell
demos/canal-interactive-zsh.cast      # zsh-specific
demos/canal-interactive-offline.cast  # offline mode
```

## CLI Flags

```rust
use clap::Parser;

#[derive(Parser)]
struct Args {
    #[arg(long)] run: bool,       // Animated with timing
    #[arg(long)] test: bool,      // CI-safe, no delays
    #[arg(long)] record: bool,    // Wrap with asciinema
    #[arg(long)] gif_only: bool,  // Convert existing .cast to GIF/MP4
    #[arg(long)] shell: Option<String>,  // zsh|bash|fish|nu|xonsh
}
```

## Typing Animation

```rust
fn type_text(text: &str, delay_ms: u64) {
    for ch in text.chars() {
        print!("{ch}");
        std::io::stdout().flush().unwrap();
        std::thread::sleep(Duration::from_millis(delay_ms));
    }
    println!();
}
```

## Recording and Conversion (asciinema + agg + ffmpeg)

```rust
// asciinema recording
Command::new("asciinema")
    .args(["rec", &cast_path, "--idle-time-limit", "5",
           "--overwrite", "--command", &demo_cmd])
    .status()?;

// agg conversion (cast -> GIF)
Command::new("agg")
    .args([&cast_path, &gif_path,
           "--font-size", "14",  // NOTE: should be 16-18 for Full HD
           "--renderer", "fontdue",
           "--speed", "0.75",
           "--idle-time-limit", "10",
           "--theme", "dracula"])
    .status()?;

// ffmpeg conversion (GIF -> MP4) — 4-strategy fallback
let strategies = [
    // Strategy 1: HEVC (smallest, best quality)
    vec!["-c:v", "libx265", "-crf", "28", "-preset", "medium",
         "-tune", "animation", "-tag:v", "hvc1",
         "-vf", "fps=24,format=yuv420p"],
    // Strategy 2: H.264 (broadest compatibility)
    vec!["-c:v", "libx264", "-crf", "28", "-preset", "medium",
         "-tune", "animation",
         "-vf", "fps=24,format=yuv420p"],
    // Strategy 3: macOS hardware encoder
    vec!["-c:v", "h264_videotoolbox", "-q:v", "65",
         "-vf", "fps=24,format=yuv420p"],
    // Strategy 4: ffmpeg default
    vec!["-vf", "fps=24,format=yuv420p"],
];
```

## Initial Frame Cleanup (Rust)

Equivalent of Python's `trim_cast_to_banner()` — removes shell init artifacts from the first frame of a .cast recording so the demo starts cleanly with the banner.

```rust
use std::path::Path;
use std::fs;

fn trim_cast_to_banner(cast_file: &Path, banner_marker: &str) -> std::io::Result<()> {
    let content = fs::read_to_string(cast_file)?;
    let mut lines: Vec<&str> = content.lines().collect();
    if lines.len() < 2 {
        return Ok(());
    }

    let header = lines[0]; // JSON header — always line 1
    let events = &lines[1..];

    // Find the first event containing the banner marker.
    let banner_idx = match events.iter().position(|line| line.contains(banner_marker)) {
        Some(idx) => idx,
        None => {
            eprintln!("[trim] marker {banner_marker:?} not found — skipping trim");
            return Ok(());
        }
    };

    // Walk back to the clear-screen escape just before the banner.
    let mut clear_idx = banner_idx;
    for i in (0..banner_idx).rev() {
        if events[i].contains(r"\u001b[H\u001b[2J") || events[i].contains(r"\033[H\033[2J") {
            clear_idx = i;
            break;
        }
    }

    let kept = &events[clear_idx..];
    if kept.is_empty() {
        return Ok(());
    }

    // Rebase timestamps: first kept event becomes t=0.
    let first_ts: f64 = serde_json::from_str::<serde_json::Value>(kept[0])
        .map(|v| v[0].as_f64().unwrap_or(0.0))
        .unwrap_or(0.0);

    let mut rebased = Vec::with_capacity(kept.len());
    for line in kept {
        let mut evt: serde_json::Value = serde_json::from_str(line)?;
        if let Some(ts) = evt[0].as_f64() {
            evt[0] = serde_json::json!((ts - first_ts * 1e6).round() / 1e6);
        }
        rebased.push(serde_json::to_string(&evt)?);
    }

    let trimmed = events.len() - kept.len();
    let output = format!("{}\n{}\n", header, rebased.join("\n"));
    fs::write(cast_file, output)?;
    eprintln!("[trim] Removed {trimmed} events before banner (kept {})", kept.len());
    Ok(())
}
```

Call after `asciinema rec`, before `agg` conversion:
```rust
trim_cast_to_banner(&cast_path, "mytool_name")?;
```

## Testing Integration

```rust
// In tests/demo_integration.rs:
#[test]
fn test_cli_demo_runs() {
    let status = Command::new("cargo")
        .args(["run", "--bin", "can-cli-demo", "--", "--test"])
        .status()
        .expect("failed to run demo");
    assert!(status.success());
}
```

## Key Differences from Python Harness

| Aspect | Python | Rust |
|--------|--------|------|
| Timing control | `_TIMED` global bool | `AtomicBool` static |
| Subprocess | `subprocess.run()` | `std::process::Command` |
| Shell paths | Runtime `shutil.which()` | Compile-time `env!("CARGO_MANIFEST_DIR")` |
| Test runner | pytest | `cargo test` |
| Multi-shell | Not supported | 5 shells via shim paths |
| CLI parsing | `argparse` | `clap::Parser` derive macro |

## Resolution Note

Canal demos currently use 80x24 terminal at font-size 14, producing 690x490px MP4 output.
This is below the minimum 1080p target. Update to 160x48 at font-size 16-18 for Full HD output.
