# Common Pitfalls

Labels: **[CLI]** = CLI pathway, **[TUI]** = TUI pathway, **[Both]** = either

| Problem | Root cause | Fix | Scope |
|---------|-----------|-----|-------|
| asciinema records nothing / blank | `capture_output=True` swallows stdout | `capture_output=False`; harness IS the recording | [CLI] |
| Timing changed when only spacing was requested | `pause()` and `section()` conflated | Independent: `pause()` = reading time; `section()` `\n\n\n` = visual gap | [CLI] |
| Empty-string query changed assuming placeholder | Didn't verify query semantics | `''` often means "show recent/all" — may be intentional | [CLI] |
| Section bar shorter than title | Hardcoded bar width | `bar_len = max(68, len(title) + 6)` | [CLI] |
| `--since Nd` shows 0 results on re-record | Committed fixtures have fixed old timestamps | `create_dated_demo_dir()` shifts timestamps to near today | [CLI] |
| Banner rows misaligned | ANSI codes inflate `len()` | Pad on plain text only, wrap ANSI outside | [CLI] |
| Wrong install command in closing message | Guessed install command | Read pyproject.toml/README; never guess | [Both] |
| Test uses different flags than demo act | Wrote tests independently | Write acts first; tests run SAME command strings verbatim | [Both] |
| Test assertion impossible with fixture data | Assertion assumes fixture triggers behavior | Dry-run every act command against fixtures BEFORE writing assertions | [Both] |
| Fix thought complete but problem persists | Assumed fix was complete without verifying | Run actual demo commands; tests catch regressions | [Both] |
| Real user data appears in recording | Subprocess uses raw `os.environ` | Use `DEMO_ENV` with `TOOL_ISOLATION_VAR` set to synthetic data dir | [Both] |
| `git commit` fails in mock repo | GPG signing configured globally | Add `git config commit.gpgsign=false` + `GIT_CONFIG_NOSYSTEM=1` | [Both] |
| Re-recording produces corrupt cast | Stale asciinema from previous run writes to same file | Kill stale asciinema processes before creating new session | [TUI] |
| Slash commands / special chars misfire | `tmux send-keys` interprets `/`, `{`, `}` | Use `send-keys -l` (literal flag) for all special-char input | [TUI] |
| Demo shows nothing / raw text | Harness prints to Python stdout, not tmux pane | All visible output must go through the tmux pane shell | [TUI] |
| Hook never fires | Claude used native Grep/Read/Edit instead of Bash | Add "Using the Bash tool, run:" to prompt | [TUI] |
| Claude pre-emptively refuses | Alarming framing | Reframe neutrally OR switch to unconditionally-blocked command | [TUI] |
| Claude self-overrides block | Override command visible in context | Add "do not override any safety blocks, just report what happened" | [TUI] |
| Trust dialog hangs | Exact string mismatch | Keyword detection: `["trust", "safe", "quick safety check", "allow"]` | [TUI] |
| Acts overlap / garbled output | `wait_for_response` returned on first idle | Require 3 consecutive idle checks; sleep 1.5s before polling | [TUI] |
| Plan approval step hangs | Used `wait_for_response()` instead of `wait_for_plan_approval()` | Separate wait that detects plan approval prompt type | [TUI] |
| Plan accepted but context wiped | Hardcoded option "2" means "clear context" | Parse menu dynamically with `_ACCEPT_WORDS` / `_CLEAR_WORDS` | [TUI] |
| Mid-session tool dialogs interrupt acts | Missing `--dangerously-skip-permissions` | Add flag when starting the CLI | [TUI] |
| Bash exec `!cmd` doesn't work | `!` lines passed as literal text to AI | Replace with hook-based slash commands | [TUI] |
| GIF too fast to read | Default speed / idle-time-limit | `--speed 0.75 --idle-time-limit 10` (agg) | [Both] |
| Long paths in every tool call | `tempfile.TemporaryDirectory()` → `/private/var/...` | Use `/tmp/mytool-demo-{os.getpid()}` | [TUI] |
| Recording tool missing silently | Hard-coded dependency | `shutil.which()` + skip gracefully with install instructions | [Both] |
| GIF/MP4 accidentally committed | No .gitignore rule for output files | Add `*.gif *.cast *.mp4 *.webm` to .gitignore | [Both] |
| Can't debug failed act | tmux session destroyed immediately | Add `--no-cleanup` flag to keep session alive | [TUI] |
| GIF text blurry | Default agg renderer (bitmap) | Add `--renderer fontdue` for vector-quality text | [Both] |
| MP4 files larger than necessary | Using h264_videotoolbox or crf=18 | Use `libx265 crf=28 tune=animation` as strategy 1 | [Both] |
| Resolution too low (sub-HD) | Terminal size 80x24 at any font size | Use minimum 160x48 cols/rows; see Resolution Targets in SKILL.md | [Both] |
| First frame shows shell init junk | asciinema records RC/prompt before script starts | `trim_cast_to_banner()` — find banner marker, drop prior events, rebase timestamps to t=0 | [Both] |
