---
description: Cache-miss / compaction protection gate (disabled by default)
argument-hint: [on|off|set|ok|no|status] [args…]
---

# Cache Protection Gate (/ar:cache)

$ARGUMENTS

Blocks tool use when the Claude Code prompt cache is cold (cache hit ratio low,
cache-read tokens below floor, or cache age above ceiling), using the same
`5m | 5 | perm` override grammar as `/ar:ok`. **Off by default** — enable with
`/ar:cache on`.

## Usage

```
/ar:cache                    # show status (enabled? thresholds? active overrides?)
/ar:cache on [5m|1h|perm]    # enable (optionally for a window)
/ar:cache off [5m|1h|perm]   # disable

/ar:cache set ratio 0.3      # block when cache_read / total_input < 30%
/ar:cache set read 50k       # block when cache_read_tokens < 50,000
/ar:cache set age 10m        # block when time since last cache hit > 10 min
/ar:cache set full 0.9       # block when total_input / context_window > 90%

/ar:cache ok 5m              # allow for 5 minutes
/ar:cache ok 3               # allow next 3 tool uses
/ar:cache ok perm            # allow until axis clears or session end
/ar:cache no                 # cancel all outstanding overrides
```

Token units: `50000`, `50,000`, `50_000`, `50k`, `.5M`, `1.5M`, `2M`.
Percent units: `85%`, `0.85`.
Durations: `5s`, `5m`, `1h`, `2h30m`, `perm` (reuses `parse_scope_args`).

## How it decides

On every `PreToolUse` (Claude Code) / `BeforeTool` (Gemini CLI), when enabled:

1. Read usage snapshot from `session_state['cache/last_usage']` (2 s memo) or
   tail the transcript JSONL at `transcript_path` (bounded 64 KB reverse scan,
   locates the most recent `message.usage`).
2. Check each configured axis; collect those that trip.
3. If any axis tripped and no active `/ar:cache ok` grant, render a block
   message and deny the tool call.

On Gemini CLI, cache-token fields are not surfaced to hooks — ratio and
cache-read axes fail-open; age and compaction-proximity axes remain active.

## Optional fast path (opt-in, one line in your statusline)

`/ar:cache` never touches your `statusLine` setting. If you want the rich
Claude statusline JSON (exposes `context_window.current_usage.cache_read_input_tokens`
and `rate_limits`) to feed the gate directly, add one line to your **existing**
statusline script:

```bash
INPUT=$(cat)
printf '%s' "$INPUT" | autorun --cache-snapshot >/dev/null 2>&1 &
# ... your existing logic continues on $INPUT as before ...
```

## See also

- Plan: `~/.claude/plans/make-a-plan-to-sunny-sparkle.md`
- Grammar rationale: plan §6.5.1 (`/ar:cache ok` vs `/ar:ok cache`).
- Magic classes reused: `ScopedAllow`, `parse_scope_args`, `session_state`,
  `check_blocked_commands`, `detect_cli_type`.
