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

Tests that replace persistence must patch the owner lookup:

```python
with patch("autorun.core.session_state", isolated_session_state):
    ...
```

Patching `autorun.plugins.session_state` does not isolate `ThreadSafeDB` and can
leak global blocks or allows into later tests. Every fake context manager must
accept persistence keyword arguments such as `timeout`.

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
uv run pytest plugins/autorun/tests/test_database_functionality.py \
  plugins/autorun/tests/test_cache_guard.py \
  plugins/autorun/tests/test_scoped_permissions.py \
  plugins/autorun/tests/test_daemon_restart_safety.py -q

uv run pytest plugins/autorun/tests/ -q
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
