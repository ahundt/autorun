# Controlling AI sessions in tmux

`autorun tmux` lists the AI coding sessions running in your tmux panes and types text into the ones you name. In an agent session, `/ar:tmux` does the same. The [README](../README.md#cli-reference) has the command lines; this page has the rules.

## List sessions

`autorun tmux list` only reads. It finds a pane running any of the nine supported agents and shows each session's status as `idle`, `active`, `dialog`, `error`, `completed` or `unknown`.

1. `dialog` is a permission, plan or question prompt. Enter there answers it, so it needs you in that pane.
2. Only Claude Code's prompts can be read, so every other agent shows `unknown`.

A pane counts as an AI session only while the agent is the pane's foreground process.

## Send text

```bash
autorun tmux send --to <%3 | %3,%7 | all | awaiting-input> -- <text>
```

1. `send` prints each target's session, window, pane, directory and agent, then types the text and presses Enter in a second call.
2. It reports text still on the input line as `typed, not submitted`, and a pane it could not read back as `sent, not confirmed`. It resends neither.
3. It reads each pane just before typing. It refuses a pane in a dialog, mid-turn or with text already typed: nothing is typed, the pane is named by letter with the reason, and the exit code is 1.
4. `awaiting-input` selects only sessions at their plain input prompt. It and `all` never type into an `unknown` session, which only its pane id reaches.

Name panes by the pane id `list` prints (`%3`), which tmux never reuses. A letter is refused, because letters shift when a window opens or closes between `list` and `send`.
