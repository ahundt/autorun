# State storage and locking

Every hook from every running agent session reads and writes one shared state
store through the daemon, so a change here can corrupt state for all of them.

## Where state lives

| What | Where | Owner |
|---|---|---|
| Session state (file policy, guards, stages, tasks) | `~/.claude/sessions/daemon_state.json` | `session_manager.state_directory()`; `AUTORUN_TEST_STATE_DIR` moves it, and tests always set it |
| Task audit logs, config files, daemon socket and logs | `~/.autorun/` | `ipc.AUTORUN_CONFIG_DIR`; `AUTORUN_HOME` moves it |

`state_backend` in `config.py` is `json` by default. `autorun --state-migrate`
converts to the SQLite store (`session_manager.SQLiteStore`) while this
install's daemon is stopped, `--state-rollback` exports back to
`daemon_state.json`, and `--state-status` / `--state-maintenance` report on
either. `autorun --state-rebuild-tasks` is only for the case where startup
reports that SQLite refused the in-place task-definition rewrite. Stop every
daemon using that store before running it; the command checks that the store's
directory has enough free space before copying the tasks table. Moving the
default directory needs the daemon quiesced and the migrator's receipts; see
the comment above `_DEFAULT_STATE_DIR_PARTS`.

## How it is locked

The JSON store (`session_manager._JSONStore`) takes a `filelock` lock across
processes and a `threading.RLock` within one, re-reads the file on every
acquisition to pick up other processes' writes, and writes through
`durable_io.atomic_write_json` (temporary file, then rename) so a crash leaves
the old or the new file, never half of one. The daemon's single-instance lock is
a separate `fcntl.flock` at `ipc.daemon_flock_path()`.

## Which API to use

On daemon paths, read and write through `EventContext.state_get`, `state_set`
and `state_update`; wrap legacy helpers in `state_synchronize`. Calling
`session_manager.session_state()` from a handler bypasses the daemon's
per-session hydration and reads the whole file on a warm hook, which
`plugins/autorun/AGENTS.md` rule 6 forbids. `SessionLock` remains only as a
no-op shim for old callers.

Never hide a lock or I/O failure by raising a hook timeout; the root
[AGENTS.md](../AGENTS.md) and [RUNTIME_STATE_ISOLATION.md](../plugins/autorun/docs/RUNTIME_STATE_ISOLATION.md)
cover isolation for tests and sandboxes.

## Python versions

`requires-python = ">=3.10"`. CI runs 3.10 to 3.14 on Ubuntu and 3.13 on macOS
and Windows. `python_check.check_python_version()` rejects older interpreters
before any runtime import, and `error_handling.show_comprehensive_uv_error()`
prints the setup guidance when an import fails.
