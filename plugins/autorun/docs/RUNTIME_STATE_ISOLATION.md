# Runtime State and Test Isolation

This document is the maintainer specification for autorun daemon state,
concurrency, and test isolation. It applies to every harness because Claude,
Codex, Gemini, Qwen, Antigravity, Pi, and compatible custom flavors can share one
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

### A lock timeout under load is contention

`HOOK_STATE_LOCK_TIMEOUT` bounds one acquisition attempt. Enough concurrent hook
processes on a CPU-starved machine will exceed it and raise
`SessionTimeoutError`. This is contention. The lock still serializes every
transaction it grants, and an operation that retries after a timeout commits
normally.

Two rules follow, and both are easy to get wrong:

- **A timeout is not data loss.** `state_update()` reports both through
  `SessionPersistenceError` so every caller keeps failing open, so the exception
  type cannot tell them apart. `core.state_failure_is_contention()` can: it
  inspects the error and its `__cause__` for `SessionTimeoutError`. A timeout
  means the read-modify-write never began, so nothing was read, written, or
  accepted; only a genuine persistence failure means a value was accepted in
  memory and never stored. Do not report contention to a user as a discarded
  value, and do not treat it as evidence that a counter lost an update.
- **Do not count notifications to test a "happens once" property.**
  `report_state_persistence_failure()` attaches a warning to
  `ctx._chain_notifications`, so under contention a test asserting
  `len(ctx._chain_notifications) == 1` counts that warning as the thing it is
  measuring, and fails intermittently while blaming the wrong mechanism. Assert
  on a marker in the message instead.

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

Raise the concurrent process count before raising the iteration count. A
contention window that never opens at one process count can open readily at
twice that, while more iterations of an insufficiently contended run only cost
time.

The image has no `uv` and no `claude` binary, so tests that drive a real CLI
(the demo, plan-export hook, and Gemini end-to-end modules) fail there because
those tools are missing, whatever the code does. Read the failure names before
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
- `HOME` and `USERPROFILE` both point to `<temporary root>/home`, and
  `PI_CODING_AGENT_DIR` points to its `.pi/agent` directory.

Inherited harness configuration, detection, session and task-tool switches are
cleared using the platform registry, including its Claude project alias.
Runtime `AUTORUN_*` overrides, uv project/tool/Python relocation settings and
`XDG_DATA_HOME` are cleared too; the shared uv cache and explicit test controls
are retained. Tests opt into
an override after collection. `isolated_environ.isolation_only()` preserves
the redirected home as well as the daemon and state paths when clearing the
rest of the environment.

The install canaries in `tests/conftest.py` therefore observe the sandbox,
including nested tests of canary failures. They do not inspect the developer's
live trees. For an audit, take the explicitly permitted live-file fingerprints
outside pytest before and after the run; a sandbox-only canary cannot prove
that those files stayed unchanged.

Import-time setup is required because `ipc.py` resolves daemon paths when it is
imported. Setting only `AUTORUN_TEST_STATE_DIR`, or setting `AUTORUN_HOME` in a
later fixture, can still point tests at the production socket and PID files.
`tests/conftest.py` removes the complete temporary root after the suite unless
debug artifact retention is explicitly enabled.

### Keep the isolated socket path short

`AUTORUN_HOME` under the temporary root produces this daemon socket path:

```
/tmp/ar_test_XXXXXXXX/autorun-home/daemon.sock
```

The parent conftest prefers `/tmp` and falls back to the platform temporary
directory if it is unavailable. Each process, including each xdist worker,
gets its own root. `sun_path` holds 104 bytes on macOS and BSD, 108 on Linux.
Before importing autorun, conftest measures the encoded, resolved socket path
and stops with `test daemon socket path is N bytes, limit L` if it cannot fit.
Non-ASCII characters can take more than one byte.

The old platform-temp layout left almost no headroom: measured on macOS 25.5
(2026-08-05), its socket path was 103 bytes and binding first failed at 104.
A long caller `TMPDIR` can still affect test fixtures that create their own
sockets; those fixtures need a short temporary directory too.

Outside pytest (a sandboxed install, a hook run by hand) the failure does not
announce itself as a path problem. With a 124-byte
socket path a real `PostToolUse` hook returns

```json
{"continue": true, "systemMessage": "[autorun] autorun CLI timed out after 5s"}
```

at exit 0, and writes no session state at all. A test that asserts on
persisted state then fails with an empty result while the harness reports a
clean run. Check the path length before hunting for a persistence bug:

```bash
uv run python -c "import os;p=os.path.realpath(os.environ['AUTORUN_HOME']+'/daemon.sock');print(len(os.fsencode(p)),p)"
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
SB=$(mktemp -d /tmp/arsb.XXXXXX)  # short (socket headroom above) and unique per run
mkdir -p "$SB/home" "$SB/ar-home" "$SB/state"
env -i PATH="$PATH" HOME="$SB/home" \
    USERPROFILE="$SB/home" \
    PI_CODING_AGENT_DIR="$SB/home/.pi/agent" \
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
not install state. `env -i` is required: an inherited `CODEX_HOME`,
`CLAUDE_CONFIG_DIR`, `QWEN_HOME`, or another harness relocation variable can
otherwise bypass the redirected home. Add any deliberate sandbox-specific
override after `env -i`; never forward one from the live session.

The install output names each harness's outcome. A harness the sandbox lacks
can be skipped, so create its config directory in the sandbox first when it is
the one under test.

Prove the isolation with a full listing of the live trees before and after the
run, so any difference can be diffed:

```bash
snapshot() {
  for p in ~/.agents ~/.claude/skills ~/.claude/plugins ~/.claude/settings.json \
           ~/.codex ~/.gemini ~/.qwen ~/.pi/agent ~/.prime/agent \
           ~/.config/opencode ~/plugins ~/forge ~/.forge; do
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

Install output abbreviates the sandbox home to `~`, so `written to
~/.agents/plugins/marketplace.json` says nothing about the live tree. Compare
the listings.

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
- Command reports success but the next hook reads old configuration: find the
  direct `session_state()` mutation and route it through `state_set`,
  `state_update`, or `state_synchronize`.
- State lock timeout: report the session, event, configured lock budget, and
  recovery command. Keep timeout values in the existing configuration system.
- Large persistent state: preserve history, measure the hot path, and design a
  migration or retention policy separately. Never delete user history as a
  performance shortcut.
