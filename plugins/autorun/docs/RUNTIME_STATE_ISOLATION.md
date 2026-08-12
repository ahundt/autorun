# Runtime State and Test Isolation

This document is the maintainer specification for autorun daemon state,
concurrency, and test isolation. It applies to every harness because Claude,
Codex, Gemini, Qwen, Antigravity, and compatible custom flavors can share one
daemon while running multiple sessions and working directories concurrently.

## Required Invariants

1. Production and test runtimes never share state files, sockets, PID files,
   locks, or logs.
2. Session fields are isolated by `session_id`; global fields use only the
   `__global__` session.
3. A successful state mutation is visible to the next daemon request before
   the mutation lock is released.
4. Security-sensitive read-modify-write operations are atomic across daemon
   threads and external processes.
5. Warm hook reads do not parse the complete persistent state file.
6. Direct CLI callers without a daemon store retain locked persistence.
7. Tests may not stop, restart, replace, or remove artifacts owned by the live
   production daemon.
8. Errors preserve the original failure and leave the cache reloadable; they
   must not silently create split cached and persisted state.

## State Ownership

`ThreadSafeDB` in `src/autorun/core.py` owns the daemon's in-memory view of
persistent state. `session_state()` in `src/autorun/session_manager.py` owns
cross-process file locking and durable JSON persistence.

Use the highest-level API that satisfies the operation:

| Operation | API | Guarantee |
| --- | --- | --- |
| Read one field | `ctx.state_get(...)` | Warm daemon cache; locked fallback without daemon |
| Write one field | `ctx.state_set(...)` | Cache and persistence updated together |
| Read-modify-write | `ctx.state_update(...)` | Atomic daemon-thread and process update |
| Existing multi-field helper that calls `session_state()` | `ctx.state_synchronize(operation, ...)` | Holds daemon lock and rehydrates before unlock |
| Standalone administrative code | `session_state(...)` | Durable cross-process lock, no daemon-cache guarantee |

Do not call `session_state()` directly from a warm hook path. Do not mutate a
file-backed field in daemon code and then merely clear the cache: another hook
can observe stale data between those operations. Wrap legacy helpers with
`state_synchronize()` or convert them to the scoped state APIs.

Mutable values are copied at cache boundaries. A missing field is negatively
cached per session, but each `get` still returns its caller-provided default.
Stored `None` remains distinct from a missing key.

## Concurrency Model

The daemon accepts concurrent socket clients and dispatches hook work through
executor threads. Separate CLI processes and direct administrative commands can
also access the same persistent file.

- `ThreadSafeDB._lock` serializes cache hydration and mutation inside one
  daemon process.
- `session_state()` supplies cross-process exclusion.
- `state_update()` holds both layers across a read-modify-write operation.
- `state_synchronize()` holds the daemon layer while a legacy persistence
  operation runs, then replaces that session's cached fields before unlock.
- Different session IDs prevent logical collisions but still share the durable
  JSON file, so full-file I/O must stay off warm paths.
- Global scoped grants and blocks require atomic updates because all sessions
  and harnesses can consume them.

Advisory counters may remain last-writer-wins only when a missed increment
cannot affect safety or correctness. Stop events are normally serial within one
session, but this is not a substitute for atomic APIs in code shared with other
events.

### Lock contention is a normal condition, not a defect

`HOOK_STATE_LOCK_TIMEOUT` is 0.5s per attempt. Measured on Linux pinned to two
CPUs, 8 and 16 concurrent hook processes exceed that budget regularly, raising
`SessionTimeoutError`. The lock itself is sound under the same load: 8
processes x 200 increments committed 1600 of 1600 with no lost update once
timeouts were retried.

Two consequences worth knowing before changing any of this:

- **A timeout is not data loss.** `state_update()` reports both through
  `SessionPersistenceError` so every caller keeps failing open, so the exception
  type cannot tell them apart. `core.state_failure_is_contention()` can: it
  inspects the error and its `__cause__` for `SessionTimeoutError`. A timeout
  means the read-modify-write never began, so nothing was read, written, or
  accepted; only a genuine persistence failure means a value was accepted in
  memory and never stored.
- **Do not count notifications to test a "happens once" property.**
  `report_state_persistence_failure()` attaches a warning to
  `ctx._chain_notifications`, so under contention a test asserting
  `len(ctx._chain_notifications) == 1` counts that warning as the thing it is
  measuring. Assert on a marker in the message instead. This produced an
  intermittent failure whose own diagnosis blamed an unserialised counter that
  had in fact been serialised correctly.

### Reproducing a contention failure

These failures do not appear on a developer machine with free cores: the
processes run in parallel and nobody waits on the lock long enough to time out.
Reproduction needs Linux and genuine CPU scarcity.

`--cpus=2` does not work, because it throttles CFS quota while `nproc` still
reports every host core. Use `--cpuset-cpus`, which pins the run to two real
cores so the processes time-slice the way a two-core CI runner does:

```bash
docker run --rm --cpuset-cpus=0,1 --memory=4g \
  -v "$(git rev-parse --show-toplevel)":/repo:ro python:3.13-slim bash -lc '
apt-get update -qq && apt-get install -y -qq git
mkdir -p /tmp/src && cp -a /repo/. /tmp/src/
git config --global --add safe.directory /tmp/src
cd /tmp/src/plugins/autorun
pip install -e . && pip install pytest pytest-asyncio pytest-timeout
for i in $(seq 1 25); do python -m pytest <target> -q -p no:randomly; done'
```

Raising the process count matters more than the iteration count: a case that
never failed at 8 concurrent processes failed 18 times in 25 at 16.

The image has no `uv` and no `claude` binary, so `test_demo.py`,
`test_plan_export_hook_e2e.py` and `test_gemini_e2e_improved.py` fail there for
environmental reasons rather than code ones. Read the failure names before
concluding anything from a full-suite run in this container.

Two traps specific to probing this area:

- A `spawn` child re-imports `__main__`, so a `tempfile.mkdtemp()` at module
  scope gives every process its own state directory and the probe measures
  nothing. Pass the root through the environment.
- A probe that abandons its work on `SessionTimeoutError` reports the shortfall
  as lost updates. Retry the operation instead, or contention and data loss are
  indistinguishable in the result.

## Pytest Isolation

`plugins/autorun/conftest.py` creates one temporary runtime root before any
autorun package import. It sets:

- `AUTORUN_TEST_RUNTIME_DIR=<temporary root>`
- `AUTORUN_TEST_STATE_DIR=<temporary root>/sessions`
- `AUTORUN_HOME=<temporary root>/autorun-home`

Import-time setup is required because `ipc.py` resolves daemon paths when it is
imported. Setting only `AUTORUN_TEST_STATE_DIR`, or setting `AUTORUN_HOME` in a
later fixture, can still point tests at the production socket and PID files.
`tests/conftest.py` removes the complete temporary root after the suite unless
debug artifact retention is explicitly enabled.

### The isolated socket path has almost no headroom

`AUTORUN_HOME` under the temporary root produces this daemon socket path:

```
<TMPDIR>/autorun_test_runtime_XXXXXXXX/autorun-home/daemon.sock
```

Everything after `<TMPDIR>` is 55 characters. `sun_path` holds 104 bytes on
macOS and BSD, 108 on Linux, so a macOS `TMPDIR` longer than 48 characters
makes the socket unbindable. Measured on macOS 25.5 (2026-08-05): the
per-user `TMPDIR` is 48 characters, the socket path is 103, and a probe that
binds progressively longer paths first fails at exactly 104.

That is zero bytes of headroom. Renaming the runtime prefix, adding one
directory level, or running under a longer `TMPDIR` breaks every
daemon-backed test at once.

The failure does not announce itself as a path problem. With a 124-byte
socket path a real `PostToolUse` hook returns

```json
{"continue": true, "systemMessage": "[autorun] autorun CLI timed out after 5s"}
```

at exit 0, and writes no session state at all. A test that asserts on
persisted state then fails with an empty result while the harness reports a
clean run. Check the path length before hunting for a persistence bug:

```bash
python3 -c "import os;p=os.environ['AUTORUN_HOME']+'/daemon.sock';print(len(p),p)"
```

Tests that replace persistence must patch the owner lookup:

```python
with patch("autorun.core.session_state", isolated_session_state):
    ...
```

Patching `autorun.plugins.session_state` does not isolate `ThreadSafeDB` and can
leak global blocks or allows into later tests. Every fake context manager must
accept persistence keyword arguments such as `timeout`.

## Running a real install without touching the live machine

Unit tests are not the only thing that must be isolated. `--install` writes to
every harness config directory, replaces plugin caches, and restarts the daemon,
so dogfooding it against a developer's own home directory risks the user's
skills, commands and hook configuration. Redirect `HOME` along with the two
runtime variables:

```bash
SB=/tmp/arsb                      # short: see the socket headroom section
mkdir -p "$SB/home" "$SB/ar-home" "$SB/state"
env HOME="$SB/home" \
    AUTORUN_HOME="$SB/ar-home" \
    AUTORUN_TEST_STATE_DIR="$SB/state" \
    UV_CACHE_DIR="$(uv cache dir)" \
    uv run --project plugins/autorun python -m autorun --install --force
```

`HOME` is the seam every path resolves through, which is why the install tests
use `monkeypatch.setenv("HOME", ...)`. Keep the sandbox path short for the same
`sun_path` reason as above: a scratch directory nested a few levels deep already
exceeds the budget. Sharing `UV_CACHE_DIR` with the real cache avoids
re-downloading the dependency set into the sandbox; it is a read-mostly cache,
not install state.

Harnesses whose config directory does not exist in the sandbox fail loudly and
are skipped, which is correct — `Qwen Code install failed: <sandbox>/.qwen/ not
found`. Create the directory first if that harness is the one under test.

Prove the isolation rather than assuming it. Record the full listing, not just
a digest, so a difference can be *diffed* rather than merely detected:

```bash
snapshot() {
  for p in ~/.agents/skills ~/.claude/plugins ~/.qwen/extensions \
           ~/.codex ~/forge ~/.config/opencode; do
    [ -e "$p" ] && find "$p" -print0 | sort -z | xargs -0 stat -f "%N %m %z"
  done > "$1"
}
snapshot /tmp/live-before.txt
# ... run the sandboxed install ...
snapshot /tmp/live-after.txt
diff /tmp/live-before.txt /tmp/live-after.txt && echo "live tree untouched"
```

A digest alone answers "did anything change?" but not "what?", and these trees
also hold session logs a harness writes on its own schedule, so an unexplained
digest change is common and uninformative. When a listing is unavailable, the
decisive check is a modification-time window covering the run:

```bash
find ~/.agents/skills ~/.claude/plugins ~/.codex -newermt "-90 minutes"
```

Empty output over a window that contains the sandboxed run proves nothing in
those trees was written, regardless of what a digest taken at some other
moment says.

One caveat this check catches: install output abbreviates the sandbox home back
to `~`, so a line reading `written to ~/.agents/plugins/marketplace.json` is not
evidence of a leak on its own. Compare the fingerprint, not the message.

## Regression Specification

Changes to state, daemon lifecycle, hooks, or cache-backed features must retain
or strengthen these checks:

1. Present, missing, and stored-`None` hydration behavior.
2. Session and `__global__` cache separation.
3. Atomic update persistence plus immediate warm-cache visibility.
4. Failed external mutation rehydration and exception propagation.
5. Session and global command mutation after a prewarmed negative cache.
6. Cross-fixture global policy isolation and order independence.
7. Multiprocess lock contention with deterministic event handshakes, not
   timing-only sleeps.
8. Production daemon PID, socket, and lock metadata unchanged across pytest.
9. Valid, silent hook protocol output for successful pass-through and correct
   platform-specific denial output.
10. Full-suite execution after focused tests, because global leaks are often
    visible only across test modules.

Do not fix a failure by increasing a hook timeout, weakening an assertion,
removing a concurrency case, or changing fail-closed behavior without evidence
that the contract itself is wrong.

## Verification

From the repository root:

```bash
uv run --project plugins/autorun pytest plugins/autorun/tests/test_database_functionality.py \
  plugins/autorun/tests/test_cache_guard.py \
  plugins/autorun/tests/test_scoped_permissions.py \
  plugins/autorun/tests/test_daemon_restart_safety.py -q

uv run --project plugins/autorun pytest plugins/autorun/tests/ -q
```

Before and after the full suite, compare the production daemon PID and the
inode/mtime of its lock and socket. Tests must not change them. Then restart the
daemon only when intentionally loading new source and verify `autorun --status`
plus one allowed and one denied hook request for each installed harness schema.

## Failure Guidance

- Repeated hook timeout: check daemon responsiveness and logs, then run
  `autorun --restart-daemon`; do not mask persistent I/O with a larger timeout.
- Test removed the live socket or PID file: confirm import-time `AUTORUN_HOME`
  isolation and run the daemon restart command once after fixing the test root.
- Command reports success but the next hook sees old configuration: find the
  direct `session_state()` mutation and route it through `state_set`,
  `state_update`, or `state_synchronize`.
- State lock timeout: report the session, event, configured lock budget, and
  recovery command. Keep timeout values in the existing configuration system.
- Large persistent state: preserve history, measure the hot path, and design a
  migration or retention policy separately. Never delete user history as a
  performance shortcut.
