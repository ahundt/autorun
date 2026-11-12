---
name: tmux-automation
description: Run, watch and type into terminal programs in tmux or byobu panes without disturbing the user's other sessions. Use when asked to "test this CLI in tmux", "run it in a tmux session", "send keystrokes to a pane", "capture a pane's output", "restart the agent in that window", "resume Claude Code, Codex or Pi in its pane", or to test a terminal app on a private tmux server. Covers read-only discovery, checking the target, sending text and Enter in two calls, verifying by capture, and the resume command of each AI coding harness.
---

# tmux and byobu Automation

<purpose>

How to drive programs in tmux panes so that the intended pane receives the
input and the user's other work is left alone.

byobu is a front end to tmux. Its sessions live on the tmux server, and
`byobu <tmux-command> ...` passes the command to tmux, so every `tmux`
command below also works on byobu sessions, written either way.

To test a program on a private tmux server, read
`references/isolated-testing.md`. To create, list and remove test sessions on
the user's server, read `references/session-management.md`.

</purpose>

<safety_rules>

## Rules

1. Discover read-only first. `list-sessions`, `list-windows`, `list-panes`,
   `capture-pane -p` and `display-message -p` change nothing.
2. Check the target before acting. Tell the user the session, window, pane,
   working directory and the program running there.
3. Ask the user before changing state in a session you did not create:
   typing, pressing keys, opening or closing windows and panes.
4. Never kill a tmux session or the tmux server to restart one program. The
   other windows hold other work. Exit or interrupt the program in its own
   pane and start it again there.
5. Send text and Enter as two separate `send-keys` calls, and send text with
   `-l` so it is typed literally.
6. Verify every send by capturing the pane. Text echoed on screen is not
   proof it was submitted. If the text still sits on the input line, report
   it; do not press Enter again, because a second Enter can submit a
   half-edited line.
7. Test on a private tmux server (`tmux -L <name>`), never on the user's.

</safety_rules>

<commands>

## Discover (read-only)

```bash
tmux list-sessions -F '#{session_name}: #{session_windows} windows'
tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index} active=#{pane_active} pid=#{pane_pid} #{pane_current_command} #{pane_current_path}'
tmux capture-pane -p -t work:3.1 -S -50          # last 50 lines of that pane
tmux display-message -p -t work:3.1 '#{cursor_y}' # row of the cursor (the input line)
```

A target is `session:window.pane`, for example `work:3.1`. Without `.pane`
it means the window's active pane.

An AI coding harness started from a shell shows the shell, or `node`, as
`pane_current_command`. To see what runs in the pane, list the children of
the pane's process:

```bash
pgrep -l -P <pane_pid>
```

## Send text, then Enter

```bash
tmux send-keys -t work:3.1 -l -- 'git status'
tmux send-keys -t work:3.1 Enter
```

Without `-l`, text that is exactly a key name (`Enter`, `Up`, `Escape`) is
sent as that key. Other keys, each in its own call: `Enter` (also `C-m`),
`C-c`, `Escape`, `C-d`, `Tab`, `BTab` (Shift+Tab), `Up`, `Down`.

## byobu

`byobu list-sessions`, `byobu send-keys ...` and `byobu capture-pane ...`
are the same commands. byobu's function keys (F2 new window, F3 and F4
previous and next window, F6 detach, F8 rename) are for a person at the
keyboard; scripts use the commands.

</commands>

<workflow>

## Send and verify

1. Capture the pane. Confirm the program is at its input prompt and not in
   the middle of a task.
2. Send the text with `-l`, wait briefly, send `Enter` in a second call.
3. Read the cursor line again. If it still ends with the text, the text was
   typed but not submitted: tell the user.

```bash
T=work:3.1
tmux send-keys -t "$T" -l -- 'continue'
sleep 0.2
tmux send-keys -t "$T" Enter
sleep 1
row=$(tmux display-message -p -t "$T" '#{cursor_y}')
tmux capture-pane -p -t "$T" | sed -n "$((row + 1))p"   # the input line now
```

## Restart one AI coding harness in its own pane

1. Ask the user, naming the session, window, pane and directory.
2. Note the directory, model and session the harness shows.
3. Exit the harness in its pane with its own exit command, or `C-c`.
   Capture the pane until the shell prompt is back.
4. Type the resume command (text, then `Enter` in a second call).
5. Capture the pane and check the directory, model and session match what
   step 2 noted.

| Harness | Continue the latest session | Resume a named session |
|---------|-----------------------------|------------------------|
| Claude Code | `claude --continue` | `claude --resume <id>` |
| Codex | `codex resume --last` | `codex resume <id>` |
| Pi | `pi -c` | `pi --session <path or id>` |
| Prime Agent | `prime-agent -c` | `prime-agent -r <path or id>` |
| Qwen Code | `qwen -c` | `qwen -r <id>` |
| Gemini CLI | `gemini -r latest` | `gemini -r <index>` |
| Antigravity | `agy -c` | `agy --conversation <id>` |
| OpenCode | `opencode -c` | `opencode -s <id>` |
| ForgeCode | none | `forge --conversation-id <id>` |

The commands come from each tool's `--help`; check it if a flag is refused.

</workflow>

<pitfalls>

## Common Mistakes

- **Text and Enter in one call** (the text and `C-m` as two arguments of
  one `send-keys`). A terminal UI can receive the Enter before it has
  processed the text, or treat the burst as a paste, and the text is left
  unsubmitted. Send them in two calls.
- **A key name sent as text.** `send-keys -t T Up` recalls history. Use `-l`
  for text.
- **Killing the session to restart a program.** It closes every window in
  that session. Restart in the pane.
- **A fixed session name** such as `test` can already exist and belong to the
  user. Use a unique name, for example `autorun-test-$$`, and check with
  `tmux has-session -t "=<name>"` first (`=` matches the name exactly).
- **The wrong server.** Inside tmux, `$TMUX` picks the server; a script
  started elsewhere talks to the default server. `-L <name>` picks a named
  server explicitly.
- **Fixed sleeps.** A slow start makes `sleep 2` too short. Poll
  `capture-pane` until the expected prompt or output appears, with a limit.

</pitfalls>

<reference>

## References

- tmux manual: https://man.openbsd.org/tmux.1 (targets, `send-keys -l`,
  formats such as `#{cursor_y}`)
- tmux wiki: https://github.com/tmux/tmux/wiki
- byobu: https://www.byobu.org/

</reference>
