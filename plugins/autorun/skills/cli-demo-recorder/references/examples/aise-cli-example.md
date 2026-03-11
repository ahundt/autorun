# Concrete Example: aise CLI Demo

aise (`ai_session_tools`) is a pure CLI tool — no AI session, no tmux. The harness runs as the recorded subprocess.

**Source**: `~/.claude/ai_session_tools/tests/test_demo.py`

## Generalizable Patterns (use in any CLI demo)

- `DEMO_DATA_DIR` + `TOOL_ISOLATION_VAR` env var — isolate from real user data
- `create_dated_demo_dir()` — shift timestamps for `--since Nd` acts
- Deterministic IDs (`uuid.UUID("cafe0001-cafe-cafe-cafe-000000000001")`) — recognizable in recordings
- `_run(cmd)` + `section(title)` + `pause(N)` harness pattern
- `--run-acts` flag — harness IS the recorded process

## aise-Specific Patterns (do not generalize)

- `--provider claude` scoping flag — only synthetic DEMO_DATA_DIR data appears
- JSONL session format, recovery dirs

## Acts (7 total, self-explanatory)

1. `aise stats` — session/file/version counts
2. `aise messages search '' --since DATE` — recent user prompts (empty = "show recent")
3. `aise list` — all sessions table
4. `aise messages search "keyword" --context 1` — targeted message search
5. `aise files search --pattern '*.py' --min-edits 2` — most-edited files
6. `aise messages corrections` — AI correction patterns (auto-detected)
7. `aise messages get SESSION_ID` — full session recovery

## Key Design Decisions

- Act 2 empty string: intentional "show recent" semantics, not a placeholder
- `--since DATE` computed from shifted fixtures, not hardcoded
- SESSION_ID in Act 7 is `_S6` constant from fixture definition, not a queried value
- Acts written first; `TestDemoFree` runs the same exact command strings

## Recording Parameters

| Parameter | Value |
|-----------|-------|
| Terminal size | 160x48 |
| asciinema format | v2 |
| agg font-size | 16 |
| agg speed | 0.75 |
| ffmpeg codec | libx264, CRF 24 |
| Output resolution | 1560x1098 |
