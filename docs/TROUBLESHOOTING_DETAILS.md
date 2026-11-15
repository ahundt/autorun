# Troubleshooting details

The [README's Troubleshooting section](../README.md#troubleshooting) lists the common fixes. This page holds the long explanations: how hooks behave when the daemon is unavailable, and how the Claude plugin cache is installed and removed. The full guide is [TROUBLESHOOTING.md](../plugins/autorun/TROUBLESHOOTING.md).

1. [When the daemon is busy or down](#when-the-daemon-is-busy-or-down)
2. [Claude cache differs from this build](#claude-cache-differs-from-this-build)
3. [Cache install lock](#cache-install-lock)

## When the daemon is busy or down

When the daemon is busy, unreachable, misses a deadline, or cannot evaluate session state, tool hooks use a local, stateless check.

1. Ordinary shell commands and non-shell tools proceed with a notice. Built-in destructive-command guards still deny dangerous shell commands.
2. `/ar:no` rules, `/ar:ok` grants, the file policy, and task and plan gating are not checked until autorun answers again. A grant cannot bypass a guard during this period.
3. A cheap literal screen lets ordinary commands such as `git status` proceed even when no check budget remains.
4. If the full check fails or is late, only a command that names a guarded command as a whole word, or uses shell syntax that can spell one indirectly (`$'...'`, `eval`, `printf`, process substitution), is denied with wait-and-retry guidance.
5. Do not restart the daemon under load.

Set `overload_policy` in `~/.autorun/autorun.config.json`, or override it with `AUTORUN_OVERLOAD_POLICY`:

| Value | Effect |
|---|---|
| `critical-guards` (default) | the behavior above |
| `block` | deny every tool-gate call |
| `allow` | permit every call without local guards |

Unknown values use `critical-guards` and are reported by `autorun --status`. Hooks read this setting without the daemon. Non-gate events and Stop are unchanged.

If the Python package itself cannot be imported, the stdlib hook still denies tool calls and gives repair guidance; the guard code is unavailable in that case.

## Claude cache differs from this build

Run `autorun --status` first. Then:

1. A plain `autorun --install` changes only hash-proven files. Edited files keep their last recorded hash in `.autorun-owned` and are never re-recorded. Unproven files are never added.
2. `autorun --install --force` backs up files detected as edited or unproven when the refresh selects them, prints each backup path, and refreshes the same version directory in place. It never moves or deletes that directory or its `.venv`.
3. Explicit uninstall removes proven payload and metadata only from regular version directories. It keeps and reports edited files, unproven files, symlinked or Windows reparse-point versions, `.venv` when a version remains, and the cache `.autorun-install.lock`.
4. The lock and its parent directories stay in place after install, refresh, and uninstall, so concurrent installers keep one lock authority.
5. Delete a kept version folder by hand only when no running session needs it.
6. Start a new Claude session for hook-manifest changes.

## Cache install lock

If another install holds the Claude cache lock, autorun prints the cache path and waits up to `cache_install_lock_timeout_seconds` (default 600). On timeout, wait for the other install to finish and rerun.

Values must be greater than zero and at most 86400 seconds. Invalid CLI, environment, or config-file values report the key, the rejected value, and the accepted range.
