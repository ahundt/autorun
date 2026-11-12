---
name: tmux
description: List the AI coding sessions running in your tmux windows and send text to the ones you choose. Use when asked "which sessions are waiting", "list my agent sessions", "what is running in my tmux windows", "continue the waiting sessions", "send this to session B", or "/ar:tmux". Finds Claude Code, Codex, Pi, Prime Agent, Antigravity, Gemini CLI, Qwen Code, OpenCode and ForgeCode panes.
allowed-tools: Bash(autorun tmux list*), Bash(tmux list-*), Bash(tmux capture-pane *), Bash(tmux display-message *)
argument-hint: "[list] or [send --to A -- TEXT]"
---

# AI Sessions in tmux

<purpose>

`autorun tmux list` prints each AI coding session running in a tmux window,
lettered A, B, C, with its target (`session:window.pane`), harness, directory,
purpose and state. It only reads.

`autorun tmux send --to <targets> -- <text>` types the text into the sessions
named, then presses Enter in a second call, then reads each pane back.

The user's request is the task. With no request beyond the skill name, list
the sessions and ask what to do.

</purpose>

<safety_rules>

## Rules

1. Listing is read-only: `autorun tmux list`, `tmux list-panes`,
   `tmux capture-pane -p` and `tmux display-message -p` change nothing.
2. Before acting on a session, tell the user its tmux session, window, pane,
   working directory and harness. `autorun tmux send` prints these for every
   target before it types; read them.
3. Ask the user before any action that changes state: typing text, pressing a
   key, interrupting, restarting, closing a window or pane. An approval covers
   the targets and text it named, nothing more.
4. Never kill the tmux session (`tmux kill-session`, `tmux kill-server`) to
   restart one harness: the other windows in it are other work. Exit or
   interrupt that harness in its own pane, then start it again there.
5. Send text and Enter as two separate calls. `autorun tmux send` does this.
   By hand: `tmux send-keys -t <target> -l -- '<text>'`, then
   `tmux send-keys -t <target> Enter`.
6. Verify by capturing the pane afterwards. Text echoed on screen is not proof
   it was submitted. `autorun tmux send` reports text still sitting on the
   input line as `typed, not submitted` and does not resend, because a second
   Enter can submit a half-edited line. Report it to the user and ask.

</safety_rules>

<workflow>

## Workflow

1. List:

   ```bash
   autorun tmux list           # table
   autorun tmux list --json    # the same data as JSON
   ```

2. Show the user the letter, target, harness, directory and status of each
   session, and ask which sessions should receive what text.
3. After the user confirms, list again (letters follow the current windows,
   so they shift when a window opens or closes) and send:

   ```bash
   autorun tmux send --to A -- continue
   autorun tmux send --to A,C -- git status
   autorun tmux send --to awaiting-input -- continue
   ```

4. Read the output: one line per target naming its session, window, pane,
   directory and harness, then `[ok]` or `[FAIL]` per target. The exit status
   is 1 when any send failed or nothing was sent.
5. Capture each pane (its target from the list) and tell the user what it shows:

   ```bash
   tmux capture-pane -p -t <target> | tail -20
   ```

## Targets

| `--to` | Types into |
|--------|------------|
| `A` | session A of the list |
| `A,C` | sessions A and C |
| `all` | every session listed |
| `awaiting-input` (`awaiting` is accepted) | Claude Code sessions waiting at a prompt |

autorun reads only Claude Code's prompts. Every other harness shows status
`unknown`, and `awaiting-input` skips it, so a batch send never types into a
session in the middle of a turn. Name such a session by its letter instead.

With no `--to`, or no text after `--`, `send` types nothing and exits 1.

## Which pane receives the text

Each pane that runs an AI coding harness is its own session in the list,
with target `session:window.pane`. A window split into a shell and a
harness lists only the harness pane; a window with two harness panes lists
both, each with its own letter. `send` types into the pane the target
names, whichever pane of the window is active. Letters follow tmux's order:
sessions by name, windows by index, panes by index.

## Keys other than text

`autorun tmux send` types text and Enter only. To interrupt a Claude Code
turn, ask first, then send `tmux send-keys -t <target> Escape` and
capture the pane. Commands to resume each harness in place after it exits are
in the `tmux-automation` skill.

## When `autorun` is not on PATH

A Claude Code install made through the marketplace alone has no `autorun`
command. In every command above, replace `autorun tmux` with
`"${CLAUDE_PLUGIN_ROOT}/commands/tmux-exec"`, which takes the same arguments.
Only Claude Code expands that variable.

</workflow>

<output_contract>

## What to show the user

Before sending: a table of the sessions with letter, target, harness,
directory and status, then the question "Which sessions, and what text?"

After sending: the target lines `send` printed, the `[ok]` or `[FAIL]` result
for each, and what the pane capture shows.

</output_contract>
