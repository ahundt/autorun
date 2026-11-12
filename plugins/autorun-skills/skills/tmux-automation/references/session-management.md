# Test Session Management on the User's tmux Server

Use a private server (`references/isolated-testing.md`) when you can. When a
test session has to live on the user's server, for example so the user can
attach to it, name it `autorun-test-<suffix>`. Cleanup only ever removes
sessions whose names start with `autorun-test`.

Each command works the same with `byobu` in place of `tmux`.

## Actions

### Create

```bash
name="autorun-test-$(date +%s)"
if tmux has-session -t "=$name" 2>/dev/null; then
  echo "$name already exists; choose another name"
else
  tmux new-session -d -s "$name" -x 120 -y 40 && echo "created $name"
fi
```

`=` makes the target match the session name exactly; without it `autorun-test`
would also match `autorun-test-old`.

### List

```bash
tmux list-sessions -F '#{session_name}: #{session_windows} windows, created #{t:session_created}'
```

### Clean Up Test Sessions Older Than One Hour

```bash
now=$(date +%s)
tmux list-sessions -F '#{session_name} #{session_created}' 2>/dev/null |
while read -r name created; do
  case "$name" in
    autorun-test*)
      if [ $((now - created)) -gt 3600 ]; then
        tmux kill-session -t "=$name" && echo "removed $name"
      fi ;;
  esac
done
```

Session names that contain a space are skipped by `read`; the names created
above have none.

### Remove One Test Session

```bash
tmux kill-session -t "=autorun-test-1731300000"
```

## Safety

- Only sessions whose names start with `autorun-test` are removed. The user's
  own sessions are never removed, and never to restart a program running in
  them.
- Ask the user before removing a test session this run did not create.
- Never run `tmux kill-server` on the user's server.
