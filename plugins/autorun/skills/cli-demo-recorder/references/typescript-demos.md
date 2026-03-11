# TypeScript/Node Demo Harness Patterns

Guide for implementing CLI demo harnesses in TypeScript/Node.js, following the same dual-purpose (demo + test) pattern used in the Python and Rust harnesses.

## Project Setup

```json
{
  "scripts": {
    "demo": "tsx tests/demo.ts --run",
    "demo:record": "tsx tests/demo.ts --record",
    "demo:gif": "tsx tests/demo.ts --gif-only",
    "test:demo": "vitest run tests/demo.test.ts"
  },
  "devDependencies": {
    "tsx": "^4.0.0",
    "vitest": "^2.0.0"
  }
}
```

Use `tsx` for TypeScript execution without a separate compilation step.

## CLI Pathway Harness

```typescript
// tests/demo.ts
import { spawnSync } from "node:child_process";
import { parseArgs } from "node:util";

const { values: args } = parseArgs({
  options: {
    run: { type: "boolean", default: false },
    record: { type: "boolean", default: false },
    "gif-only": { type: "boolean", default: false },
    test: { type: "boolean", default: false },
  },
});

// Timing control — equivalent to Python's _TIMED or Rust's AtomicBool
const TIMED = args.run || args.record;

function pause(seconds: number): Promise<void> {
  if (!TIMED) return Promise.resolve();
  return new Promise((resolve) => setTimeout(resolve, seconds * 1000));
}
```

## Typing Animation

```typescript
function typeText(text: string, delayMs = 50): Promise<void> {
  return new Promise((resolve) => {
    let i = 0;
    const interval = setInterval(() => {
      process.stdout.write(text[i]);
      i++;
      if (i >= text.length) {
        clearInterval(interval);
        process.stdout.write("\n");
        resolve();
      }
    }, delayMs);
  });
}
```

## Subprocess Execution (stdio: inherit is critical)

```typescript
import { spawnSync } from "node:child_process";

// CORRECT: stdout flows to terminal, asciinema captures it
spawnSync("mytool", ["stats"], { stdio: "inherit", env: DEMO_ENV });

// WRONG: stdout captured by Node, asciinema records nothing
const result = spawnSync("mytool", ["stats"], { env: DEMO_ENV });
console.log(result.stdout.toString());  // too late — asciinema missed it
```

For async commands, use `spawn` with `inherit`:

```typescript
import { spawn } from "node:child_process";

function runCommand(cmd: string, cmdArgs: string[]): Promise<number> {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, cmdArgs, {
      stdio: "inherit",
      env: DEMO_ENV,
    });
    child.on("close", (code) => resolve(code ?? 1));
    child.on("error", reject);
  });
}
```

**Security note**: Always use `spawnSync`/`spawn` with explicit argument arrays instead of shell string execution. This prevents command injection and is the correct pattern for demo harnesses.

## Privacy Isolation

```typescript
const DEMO_ENV: Record<string, string> = {
  ...process.env as Record<string, string>,
  MYTOOL_DATA_DIR: "/tmp/mytool-demo",
  HOME: "/tmp/mytool-demo-home",
  GIT_CONFIG_NOSYSTEM: "1",
  MYTOOL_API_KEY: "",
};
```

## Section Dividers

```typescript
function section(title: string): void {
  const barLen = Math.max(68, title.length + 6);
  const bar = "\u2500".repeat(barLen);
  process.stdout.write(`\n\n\n  ${bar}\n  \u2500\u2500 ${title} \u2500\u2500\n  ${bar}\n\n`);
}
```

## Recording and Conversion

```typescript
import { spawnSync } from "node:child_process";

function record(): void {
  const castFile = "demo.cast";
  const gifFile = "demo.gif";
  const mp4File = "demo.mp4";

  // Record with asciinema
  spawnSync("asciinema", [
    "rec", castFile, "--overwrite",
    "--command", "tsx tests/demo.ts --run",
    "--idle-time-limit", "3",
    "--cols", "160", "--rows", "48",
  ], { stdio: "inherit" });

  // Convert to GIF with agg
  spawnSync("agg", [
    castFile, gifFile,
    "--font-size", "18", "--renderer", "fontdue",
    "--speed", "0.75", "--idle-time-limit", "10",
    "--theme", "dracula",
  ], { stdio: "inherit" });

  // Convert to MP4 with ffmpeg (try HEVC first, fall back to H.264)
  const strategies = [
    ["-c:v", "libx265", "-crf", "28", "-preset", "medium",
     "-tune", "animation", "-tag:v", "hvc1"],
    ["-c:v", "libx264", "-crf", "24", "-preset", "medium",
     "-tune", "animation"],
  ];
  for (const strategy of strategies) {
    const result = spawnSync("ffmpeg", [
      "-y", "-i", gifFile, ...strategy,
      "-vf", "fps=24,format=yuv420p", mp4File,
    ], { stdio: "inherit" });
    if (result.status === 0) break;
  }
}
```

## Initial Frame Cleanup (TypeScript)

Equivalent of Python's `trim_cast_to_banner()` — removes shell init artifacts from the first frame of a .cast recording so the demo starts cleanly with the banner.

```typescript
import { readFileSync, writeFileSync } from "node:fs";

function trimCastToBanner(castFile: string, bannerMarker: string): void {
  const content = readFileSync(castFile, "utf-8");
  const lines = content.split("\n").filter((l) => l.length > 0);
  if (lines.length < 2) return;

  const header = lines[0]; // JSON header — always line 1
  const events = lines.slice(1);

  // Find the first event containing the banner marker.
  const bannerIdx = events.findIndex((line) => line.includes(bannerMarker));
  if (bannerIdx === -1) {
    console.error(`[trim] marker ${JSON.stringify(bannerMarker)} not found — skipping trim`);
    return;
  }

  // Walk back to the clear-screen escape just before the banner.
  let clearIdx = bannerIdx;
  for (let i = bannerIdx - 1; i >= 0; i--) {
    if (events[i].includes("\\u001b[H\\u001b[2J") || events[i].includes("\\033[H\\033[2J")) {
      clearIdx = i;
      break;
    }
  }

  const kept = events.slice(clearIdx);
  if (kept.length === 0) return;

  // Rebase timestamps: first kept event becomes t=0.
  const firstTs: number = JSON.parse(kept[0])[0];
  const rebased = kept.map((line) => {
    const evt = JSON.parse(line);
    evt[0] = Math.round((evt[0] - firstTs) * 1e6) / 1e6;
    return JSON.stringify(evt);
  });

  const trimmed = events.length - kept.length;
  writeFileSync(castFile, header + "\n" + rebased.join("\n") + "\n");
  console.error(`[trim] Removed ${trimmed} events before banner (kept ${kept.length})`);
}
```

Call after recording, before conversion:
```typescript
trimCastToBanner("demo.cast", "mytool_name");
```

## Test Integration with Vitest

```typescript
// tests/demo.test.ts
import { describe, it, expect } from "vitest";
import { spawnSync } from "node:child_process";

const DEMO_ENV = { /* same as above */ };

describe("Demo acts", () => {
  it("act1: stats output includes session count", () => {
    const result = spawnSync("mytool", ["stats"], {
      env: DEMO_ENV,
      encoding: "utf-8",
    });
    expect(result.stdout).toContain("Sessions:");
  });

  it("act2: search returns results", () => {
    const result = spawnSync("mytool", ["search", "", "--since", "7d"], {
      env: DEMO_ENV,
      encoding: "utf-8",
    });
    expect(result.stdout).toContain("Found");
  });
});
```

## Key Differences from Python Harness

| Aspect | Python | TypeScript |
|--------|--------|------------|
| Execution | `python tests/test_demo.py --record` | `tsx tests/demo.ts --record` |
| Subprocess | `subprocess.run(capture_output=False)` | `spawnSync({stdio: "inherit"})` |
| Test runner | pytest | Vitest |
| Typing | `sys.stdout.write(ch); sys.stdout.flush()` | `process.stdout.write(ch)` |
| Async | `time.sleep()` | `setTimeout` / `await pause()` |
| CLI parsing | `argparse` | `util.parseArgs` |
| Privacy env | `dict(os.environ, **overrides)` | `{...process.env, ...overrides}` |
