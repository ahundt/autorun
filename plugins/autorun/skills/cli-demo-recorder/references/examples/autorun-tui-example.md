# Concrete Example: autorun TUI Demo

autorun is a Claude Code plugin with hooks — requires a live TUI session via tmux.

**Source**: `~/.claude/autorun/plugins/autorun/tests/test_demo.py`

## Acts (7 total)

1. `rm project_data.csv` — blocked, redirected to `trash`
2. `sed -n '/TODO/p' main.py` — blocked (always), redirected to Edit
3. `git clean -f` — blocked (always), suggests `git clean -n` preview
4. `/ar:f` (strict-search policy) — new file creation blocked
5. `/ar:no git push` — custom block; `/ar:ok git push` — unblock
6. `/ar:plannew` — structured plan created, auto-saved to `notes/`
7. `/ar:go` — autonomous 3-stage task execution

## Acts Skipped

`/ar:pe` (uses `! bash exec` which does not work in TUI); daemon status (invisible); byobu/tmux (requires multiplexer setup).

## Prompt Evolution for Act 2

Shows the debugging process — 6 attempts to get the hook to fire:

```
Attempt 1: "find TODOs using grep in bash"
  -> Claude used native Grep tool. Hook never fired.
Attempt 2: "Use the Bash tool to run: grep 'TODO' main.py"
  -> Claude still used native Grep.
Attempt 3-5: Various grep / sed phrasings
  -> Claude used Read + pattern match. No Bash call.
Attempt 6: Explicit Bash tool + sed -n
  -> "Using the Bash tool, run: sed -n '/TODO/p' main.py"
  -> L21 Bash(sed -n '/TODO/p' main.py) -- Hook fired. Blocked. Redirected.
```

## Prompt Evolution for Act 3

```
Attempt 1-3: Various "git reset --hard HEAD~2" phrasings
  -> Non-deterministic. Sometimes refused pre-emptively.
Attempt 4: Switch to git clean -f (unconditionally blocked, less alarming)
  -> "Using the Bash tool, run: git clean -f -- do not override any safety blocks"
  -> L36 Bash(git clean -f) -- Consistent across all recordings.
```

## Verified Final Session (cba8b698)

```
L  5  Bash(rm project_data.csv)        act1: rm blocked
L 21  Bash(sed -n '/TODO/p' main.py)   act2: sed blocked -> Grep redirect
L 36  Bash(git clean -f)               act3: git clean blocked
L 82  Bash(git push)                   act5: custom block fires
L 94  Bash(git push)                   (after /ar:ok, now allowed)
L239  ExitPlanMode()                   act6: plan auto-saved to notes/
```

## Recording Parameters

| Parameter | Value |
|-----------|-------|
| Terminal size | 160x48 |
| asciinema format | v3 |
| agg font-size | 16 |
| agg speed | 0.75 |
| ffmpeg codec | libx265, CRF 28 |
| Output resolution | 1560x1098 |
