# Testing a Terminal Program on a Private tmux Server

`tmux -L <name>` starts or reaches a tmux server with its own socket. Its
sessions are invisible to the user's server, and `kill-server` on it cannot
touch the user's sessions. `-f /dev/null` makes it ignore the user's tmux
configuration, so results do not depend on it.

Every command below goes through the function `t`, so none can reach the
user's server by accident.

```bash
S="autorun-test-$$"                         # server and session name, unique per run
t() { tmux -L "$S" -f /dev/null "$@"; }
trap 't kill-server 2>/dev/null' EXIT       # removes only this private server
```

## Run a Custom Command in Isolation

Replace `COMMAND_HERE` with the command to test.

```bash
S="autorun-test-$$"
t() { tmux -L "$S" -f /dev/null "$@"; }
trap 't kill-server 2>/dev/null' EXIT

t new-session -d -s "$S" -x 120 -y 40
t send-keys -t "$S" -l -- 'COMMAND_HERE'
t send-keys -t "$S" Enter

# Wait until the screen stops changing, at most 15 seconds.
prev=""
for _ in $(seq 1 30); do
  sleep 0.5
  cur=$(t capture-pane -p -t "$S")
  [ "$cur" = "$prev" ] && break
  prev=$cur
done
t capture-pane -p -t "$S" -S -200
```

## Basic Test Sequence

Checks that the private server runs commands and returns their output.

```bash
S="autorun-test-$$"
t() { tmux -L "$S" -f /dev/null "$@"; }
trap 't kill-server 2>/dev/null' EXIT

t new-session -d -s "$S" -x 120 -y 40
for cmd in 'echo hello' 'pwd' 'command -v python3'; do
  t send-keys -t "$S" -l -- "$cmd"
  t send-keys -t "$S" Enter
  sleep 1
done
t capture-pane -p -t "$S"
```

## Testing a Terminal UI

AI coding harnesses and other full-screen programs hide the cursor, draw a
bordered input box and redraw the screen.

- Give the session a size (`-x 120 -y 40`); some programs refuse a tiny one.
- Wait for the program's input prompt in `capture-pane` before typing.
- After `Enter`, read the input line, not the whole screen: the row is
  `t display-message -p -t "$S" '#{cursor_y}'` even while the cursor is
  hidden. Text still on that row was typed but not submitted.
- Start the program as the pane's own process to watch it alone:
  `t new-session -d -s "$S" -x 120 -y 40 'PROGRAM ARGS'`.

## Safety

- Every command passes `-L "$S"`; `kill-server` without it would end the
  user's tmux server.
- The `trap` removes the private server when the script exits, including on
  an error.
- Nothing is written outside the program's own effects.
- A program that never exits keeps its pane; the `trap` still removes it.
