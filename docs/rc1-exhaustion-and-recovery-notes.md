# Resource exhaustion and recovery paths — working notes

Started 2026-08-19. Scope: make every path that fires in exceptional
circumstances behave per the philosophy — especially out-of-disk,
out-of-memory, and state-backend failure — and give duplicated owners one home.

Status legend: `OPEN` · `IN PROGRESS` · `FIXED` · `WONTFIX (reason)`

## How this started

A live session on 2026-08-18 had every tool blocked. `~/.autorun/daemon.log`
holds 67 occurrences of

```
SessionBackendError: Could not configure state connection for
~/.claude/sessions/daemon_state.sqlite3: unable to open database file
```

between `01:40:51` and `01:50:42`. Disk was not full (123 GB free), the
directory was intact (`700`, owned by the running user), the DB was intact
(107 MB), and
`ulimit -n` was 1048576. The incident cleared on its own after ~10 minutes.

The interesting part is not the SQLite error. It is that the session could not
recover from it, and that the message telling it how to recover named a command
the same gate was blocking.

## Verified failure chain

1. `session_manager.py:1586` raises `SessionBackendError`.
2. `core.py:3164` — a bare `except Exception` in the daemon request handler —
   catches it and calls `build_daemon_failure_response(event, cli_type,
   f"Daemon error: {e}")`.
3. `client.py:244` `is_tool_gate_event("PreToolUse")` is True, so it fails
   closed. Correct.
4. `client.py:252-253` builds its own reason ending
   ``Run `autorun --restart-daemon`, then retry.``

No `fail_closed_tool_gate` string appears anywhere in
`~/.autorun/hook_entry_debug.log`, which confirms the `hook_entry.py` path did
not run. The path that ran was `client.py`.

## Findings

### F-1 · P0 · Fail-closed guidance has two owners and only one learned the lesson

`hook_entry.py:459-496` documents this exact deadlock, names its cost, and
deliberately rejects allowlisting the repair commands because
`uv tool install autorun --with <package>` would pass such a check and execute
arbitrary build code. It settles on `AUTORUN_DISABLE=1` as the human's escape
hatch and encodes two named constants, `_RETRY_GUIDANCE` and
`_INTERVENTION_GUIDANCE`.

`client.py:253` is a second implementation that never received any of it:

- ends in `then retry`, which `hook_entry.py:473-477` records as the advice that
  "turned every attached session into a loop against a hook that could not
  succeed";
- never names `AUTORUN_DISABLE`, so the only working exit is invisible;
- names a Bash command, which is itself a `PreToolUse` tool call and therefore
  blocked by the gate emitting the message.

`test_hook_bootstrap_deadlock.py:103` asserts
`assert "AUTORUN_DISABLE" in reason, "the way out must be named in the reason"`
— against `hook_entry` only. `test_client_fail_closed.py` covers event
recognition, deadlines, budgets and PID liveness, and makes **no assertion about
the reason text**. The property was tested on the module that did not fire.

Status: OPEN → task #215

### F-2 · P0 · Recovery messages name paths that do not exist

`core.py:3157` tells the user to run
`uv run python plugins/autorun/scripts/restart_daemon.py`.
`plugins/autorun/scripts/` does not exist — the module is
`src/autorun/restart_daemon.py`, and the supported command is
`autorun --restart-daemon`. A PyPI install has no checkout at all, so a
repo-relative path can never be right in a runtime message.

Status: OPEN → task #216

### F-3 · P0 · A full disk makes the logging system write to stderr, disabling every hook

`logging_utils.py:5` opens with "CRITICAL: Never writes to stdout/stderr to
avoid breaking Claude Code hooks." But nothing sets `logging.raiseExceptions =
False` anywhere in the package (verified: no match in `src/` or `hooks/`;
Python's default is `True`).

On `ENOSPC` during emit, `RotatingFileHandler.emit` calls
`Handler.handleError`, which prints a traceback to `sys.stderr` when
`raiseExceptions` is true. Claude Code treats any hook stderr as a hook error
and discards that hook's response — silently disabling every protection while
the session still looks healthy.

So the out-of-disk scenario does not merely degrade logging; it turns the
safety system off.

Status: OPEN → task #219

### F-4 · P1 · `get_logger` constructs its handler with no error handling

`configure_file_logging` wraps handler construction in `try/except OSError` and
falls back to `NullHandler` (lines 81-89), with a docstring explaining why.
`get_logger` line 123 constructs the same `RotatingFileHandler` with no guard.
`get_logger(__name__)` is called at module scope across the package, so a full
or unwritable disk raises during import — inside a hook, that is the
"autorun cannot be imported" state that `hook_entry.py` treats as unrecoverable.

Status: OPEN → task #219

### F-5 · P1 · Rotation limits are duplicated magic values outside CONFIG

`5 * 1024 * 1024` and `3` appear as signature defaults in
`configure_file_logging` and again as literals in `get_logger` line 123.
Neither is a CONFIG key, so the two copies can drift and neither is tunable
alongside the existing `state_journal_size_limit_bytes` /
`volatile_state_max_bytes` block.

Status: OPEN → task #220

### F-6 · P1 · State-directory resolution is duplicated across four modules

| Site | Expression |
|------|-----------|
| `session_manager.py:447` | `state_dir or env AUTORUN_TEST_STATE_DIR or ~/.claude/sessions` |
| `ai_monitor.py:30` | `env AUTORUN_TEST_STATE_DIR or ~/.claude/sessions` |
| `main.py:109` | character-identical to `ai_monitor.py:30` |
| `plan_export.py:278` | reads `AUTORUN_TEST_STATE_DIR` |

`ai_monitor.py:30-32` and `main.py:109-111` are the same three lines twice,
including the `AUTORUN_CREATE_LEGACY_STATE_DIR_ON_IMPORT` block.

Two consequences. The production default is decided in four places, so
answering "should the state DB live under `~/.autorun`?" is currently a
four-site change instead of one. And the only override is an environment
variable named `AUTORUN_TEST_STATE_DIR` — a test name carrying production duty.

Compare `ipc.py:35-44`, which does own its directory:
`AUTORUN_HOME` env → `~/.autorun` default.

Status: OPEN → tasks #222, #223

### F-7 · P1 · Config precedence is at most env → default

`ipc._get_autorun_config_dir` resolves env → default. The bug-workaround policy
documents env → CONFIG → default. Neither provides a CLI-parameter layer or a
user config file, so the required
`CLI param > env var > config file > default` chain does not exist as a single
owner anywhere; each setting reimplements whatever subset it needs.

Status: OPEN → task #223

### F-8 · P2 · Guard remediation names tools that are absent from the session

The `grep` guard says "Use the Grep tool instead"; the `find` guard says "Use
the Glob tool instead". Neither tool exists in this session's tool set, so both
messages send the reader to something unavailable. Same defect class as F-1 and
F-2: remediation naming an unreachable remedy. Two independent instances
observed in one session, so this is not a one-off.

Status: OPEN → task #216

### F-9 · P2 · Autorun's own artifacts grow without bound

Observed on this machine:

| Path | Size |
|------|------|
| `~/.autorun/hook_entry_debug.log.20260701-incident.bak` | 741.4 MB |
| `~/.claude/sessions/autorun.log` | 30.4 MB |
| `~/.autorun/daemon.log` + 3 rotations | ~19 MB |
| `~/.claude/sessions/` entries | 12,691 |

`daemon.log` is bounded by the 5 MB × 3 rotation. `autorun.log` at 30 MB is
over that ceiling, so it is not going through the same handler. The 741 MB
incident backup has no retention at all.

Also present: `daemon_state.sqlite3.stage.<uuid>-shm` and `-wal` whose parent
`.stage.<uuid>` file is gone. `_discard_stage_sidecars` documents that failure
paths deliberately keep the stage as evidence, but nothing ever sweeps them, and
here the main stage file is already missing — so the retained evidence is
incomplete anyway.

Growth control is what keeps the full-disk scenario rare rather than routine.

Status: OPEN → task #220

## Design decision: do not move the state DB default in this change

The user asked whether the state DB and config dirs should live under
`~/.autorun`. Consistency argues yes: `~/.autorun` already owns the socket,
lock, logs and installer backups, while session state sits in
`~/.claude/sessions/`, a directory Claude Code creates, writes session
heartbeats into, and unlinks from.

But relocating the *default* is a breaking change to running instances. The
live daemon holds a 107 MB SQLite file open, shared by every attached session,
and the root `AGENTS.md` records that one careless live change cost roughly 12%
of a week's token budget. Changing where state lives while sessions are
attached is exactly that class of change.

Plan, in order:

1. Give state-directory resolution **one owner** with the full precedence
   chain, default unchanged. Nothing running is affected. (#222)
2. Offer relocation as an explicit, daemon-quiesced migration reusing the
   existing `StateMigrator` receipt machinery
   (`_build_stage` → `_verify_stage` → `_publish` → `_retire_source`), never an
   implicit move. (#222)

Principle 12, Preserve User Intent, and the user's own instruction to take
extreme care not to break running instances both point the same way here.

## Verification baseline before any change

| Gate | Result |
|------|--------|
| `pytest -m "not real_money"` | 6299 passed, 16 skipped, 26 deselected, 665.53s, exit 0 |
| `ruff check --select E9,F63,F7,F82 .` | All checks passed |
| CI on `ea85a0ba` | success |
| Working tree | clean, nothing unpushed |

All development runs sandboxed per the root `AGENTS.md`. The live installation
is not touched without written instruction in the conversation.

### Isolation practice used here

Sandbox at `/tmp/arsb-s1` (short path: the socket would be 32 characters, well
under the limit that makes a sandboxed hook report `autorun CLI timed out`).
Every command runs under:

```bash
SB=/tmp/arsb-s1; env HOME="$SB/home" USERPROFILE="$SB/home" \
  AUTORUN_HOME="$SB/ar-home" AUTORUN_TEST_STATE_DIR="$SB/state" \
  UV_CACHE_DIR="$(uv cache dir)" <command>
```

`pytest` additionally isolates itself — `conftest.py:75-76` sets
`AUTORUN_TEST_STATE_DIR` and `AUTORUN_HOME` per worker — so suite runs are
covered twice over.

Live daemon before this work: PID 99917, socket `~/.autorun/daemon.sock`,
state DB 107.5 MB. Nothing in this batch restarts it, installs, or writes to
the live trees.

One lapse, recorded rather than hidden: an early unsandboxed
`python -c "... setup_autorun_logging()"` smoke check created an empty
`~/.claude/sessions/autorun_ai_monitor.log`. Zero bytes, a file autorun creates
in normal operation, no daemon or install touched — but it should have gone
through the sandbox, and everything after it did.

## Progress

### F-1 · FIXED · one owner for fail-closed recovery guidance

`client.UNRECOVERABLE_GUIDANCE` now carries the sentence, and the reason reads
"Repair with `autorun --restart-daemon` in a terminal" followed by it — naming
`AUTORUN_DISABLE=1` and no longer advising a retry.
`hooks/hook_entry.py:_INTERVENTION_GUIDANCE` keeps its own copy because it runs
when the package cannot be imported; the two are pinned equal by
`test_the_wrapper_and_the_client_agree_on_the_unrecoverable_guidance`,
following the existing `DEADLINE_ENV_VAR` precedent. The shared sentence was
generalized from "the same install fails the same way" to "the same failure
repeats", since the client reaches it for a daemon state failure rather than a
broken install.

Also pinned: a lifecycle event still fails open and carries no gate guidance.

Wording note — the phrase "repair command" was avoided because
`test_client_fail_closed.py:398` asserts `"command" not in rendered.lower()` as
a privacy guard. That predicate is weaker than the property it stands for (the
message is echoed by design, so a real command would leak while it passed), but
strengthening it is its own change: task #228, not a side effect of this one.

### F-2 · FIXED · dead paths in runtime messages, plus a spec check

`core.py` now names `autorun --restart-daemon` instead of
`plugins/autorun/scripts/restart_daemon.py`, which never existed.

The generalized check is
`test_recovery_messages_are_reachable.py::test_every_repo_relative_path_named_in_source_exists`:
it AST-walks every string constant in `src/` and `hooks/` and fails if a
repo-relative path does not resolve. It found **seven** sites, not the one
known instance — of which three were URL false positives (`docs/en/hooks`
inside an anthropic docs link, now stripped before matching, because a check
that cries wolf gets switched off) and two were real:
`plan_export.py:272,579` cited `docs/RUNTIME_STATE_ISOLATION.md`, which only
resolves from the plugin directory, now corrected to the repo-root path.

### F-3, F-4, F-5 · FIXED · logging survives a full disk

`_ExhaustionTolerantRotatingFileHandler` overrides `handleError` to return
without writing, so `ENOSPC` during emit or rollover no longer prints a
traceback to stderr. Narrower than setting `logging.raiseExceptions = False`,
which is process-global.

`build_rotating_handler` is now the single construction site: it returns a
`NullHandler` when the path is unavailable rather than raising, which matters
because `get_logger(__name__)` runs at module scope across the package — a
raise there is a failed `import autorun...` inside a hook.

Ceilings moved to CONFIG as `log_file_max_bytes` / `log_file_backup_count`;
`configure_file_logging`'s unused `max_bytes`/`backup_count` parameters were
deleted (its one caller, `daemon.py:27`, passed neither).

The spec check
`test_no_source_file_builds_a_rotating_handler_outside_the_one_builder`
found a **third** raw handler that reading had missed: `ai_monitor.py:48`,
whose own neighbouring comment reads "CRITICAL: No stderr handler - breaks
Claude Code hooks" while installing one that would. Now routed through the
shared builder.

`hooks/hook_entry.py:_append_debug_log` was checked and is already safe: it has
its own size cap and a bare `except Exception: pass`.

Portability: the fix needs no platform branch and is worth more on Windows than
on POSIX. Windows refuses to rename a file another process holds open
(WinError 32), and several autorun processes share `daemon.log`, so rollover
fails there routinely rather than only on a full disk — and every one of those
failures previously printed to stderr.

### D-1, D-2 · FIXED · one resolver for the bug-workaround flag grammar

`config.workaround_applies(flag, *, affected, legacy_flags=())` is now the only
implementation. `affected` is passed in by the caller from a `Platform` field
so `config.py` needs no import of `platforms`; `legacy_flags` preserves
`AUTORUN_EXIT2_WORKAROUND` outranking the newer #4669 key, so an explicit
`--exit2-mode never` still wins.

Both drifts are closed:

* `task_lifecycle.py` asked `detect_cli_type(...) == "claude"`. Applicability
  now comes from a new registry field, `Platform.gates_mutable_task_tools`,
  declared beside `has_exit2_workaround` and `drops_additional_context` and
  true only for Claude. A future harness in an affected family now inherits
  the workaround by adding a registry row, which is how every other capability
  already works.
* `core.py` called `.lower()` without `.strip()`, so ` always ` silently did
  nothing for #18534 while working for the other two flags.

`test_workaround_flag_grammar.py` runs the whole value matrix against all four
flags (67 cases), and its spec check asserts the disable-set literal appears in
exactly one file, so a fifth copy fails the build.

Adding the registry field was caught mid-change by an *existing* spec check,
`test_capability_snapshot_covers_every_platform_field`, which requires every
`Platform` field to be serialized in `capability_snapshot._jsonable_platform`.
Worth recording as evidence that this style of check earns its keep: it turned
a silently incomplete change into a failing test.

This is also the prerequisite for version-ranged workarounds (#224): ranges get
added to one resolver rather than to four copies.

### #224 · DONE (grammar) · version ranges in the same flag values

`workaround_applies(..., version=...)` accepts open and closed ranges as flag
values: `>=2.1.233`, `>=2.1.233,<2.2`, `<2.2`, `==2.1.234`, `!=2.1.5`, comma
separated. Semantics, each pinned by a test:

| Case | Result |
|------|--------|
| range satisfied, platform affected | workaround applies |
| range not satisfied | does not apply |
| platform **not** affected | never applies — a range narrows, never widens |
| version unknown or unparseable | today's behavior (`affected`) |
| malformed spec (`>=abc`, `=>2.1`) | falls through to the next tier, like a typo |

The last two are the load-bearing ones. Dropping a workaround because a harness
did not report its version would be a regression wearing precision as a
disguise, and a typo in a spec must not silently switch a safety workaround off
— the same rule the word grammar already followed.

**No new dependency.** `packaging` is importable in a development environment
but is *not* in `[project] dependencies`, and the hook venv installs only those
— so importing it would pass every test and fail in the hook, the worst
possible split. Harness versions are dotted integers, which a zero-padded tuple
comparison orders correctly; a build suffix (`2.1.240-beta.1`) compares on its
leading numeric components rather than failing.

### #217 · FIXED · storage failures say what to do, and nothing latches

Refused deliberately, and recorded here so it is not "fixed" later: durable
state does **not** fall back to an in-memory copy when the disk is unavailable.
That sounds like resilience and is the opposite. One daemon serves many
concurrent sessions and several processes share the database, so a memory copy
outliving a failed write would make those processes disagree about a permission
decision, and the disagreement would survive into the next restart as silent
divergence. `test_state_persistence_failures.py` already states the rule —
"memory must not outvote storage" — and it is right. Advisory counters are a
separate matter and already live in memory under `volatile_state_max_*`.

What exhaustion handling means here is narrower and honest:

* a failed write still raises and still names what was lost (unchanged);
* the failure now explains the likely cause and says recovery is automatic;
* nothing latches — the first call after space returns succeeds.

`SQLiteStore._storage_failure_advice` maps SQLite's wording to a remedy. The
phrases overlap — a full disk, a missing directory and a permission problem can
all surface as "unable to open database file" — so specific phrases match first
and the ambiguous one names every plausible cause rather than asserting a wrong
single one. Every entry says recovery is automatic, because the 2026-08-18
incident cleared itself in ten minutes and nothing told the operator that was
expected, while the restart they would otherwise reach for was itself a tool
call the gate was denying.

The no-latch property turned out to already hold; the test now pins it.

### #218 · FIXED · the advisory-state sweep no longer scans what it keeps

The bounds themselves were already enforced correctly, and eviction already
refused to discard durable values. What was wrong was the cost:
`_evict_volatile_over_limits` materialized a list over **every** volatile entry
on every advisory write — up to 4096 — and advisory writes happen on the hook
path, so one write's cost grew with how busy the daemon had been.

`_track_volatile` pops a key before reinserting it, so the OrderedDict is in
ascending order of last write and expired entries are always a prefix. The
sweep now stops at the first live entry, making its cost proportional to what
it removes. Measured by instrumenting `items()`: 10 entries visited before, 2
after, for a write that expires nothing.

### #220 · code scope FIXED, operational items left to the owner

Bounded through CONFIG: `log_file_max_bytes` / `log_file_backup_count`, shared
by all three logging call sites.

Swept automatically: orphaned `daemon_state.sqlite3.stage.<generation>-wal` /
`-shm` pairs whose stage database is gone. A *failed* migration's stage is
still kept as evidence — only pairs that prove nothing are removed, since each
migration picks a fresh generation suffix and no later run would recognize an
old orphan.

Not touched, because it is the owner's data and deleting it is their call:

| Path | Size |
|------|------|
| `~/.autorun/hook_entry_debug.log.20260701-incident.bak` | 741.4 MB |
| `~/.claude/sessions/autorun.log` | 30.4 MB |
| `~/.claude/sessions/` entries | 12,691 |

The 741 MB file is a hand-made incident backup with no retention policy; the
30 MB `autorun.log` exceeds the 5 MB rotation ceiling, which means it is not
going through the shared handler and is worth tracing separately.

### #222 · FIXED · one owner for the state directory, default unchanged

`session_manager.state_directory(explicit=None)` now decides where session
state lives: explicit value, then `STATE_DIR_ENV_VAR`, then the default. The
three other derivations are gone, and a spec check fails if a fifth appears —
matching the literal path segments rather than a variable name, because the
duplicates spelled it three different ways.

The default is deliberately unchanged. Relocating it under `AUTORUN_HOME` would
be more consistent — `~/.claude/sessions` is a directory Claude Code creates
and prunes for its own purposes — but a live daemon holds that database open
for every attached session, so it is a quiesced migration through the existing
`StateMigrator` receipt machinery, not an edit to a default. What this change
buys is that the migration is now a one-line change instead of a four-site one.

The sweep also found a real bug rather than just duplication:
`task_lifecycle.py:3619` printed `sudo chown -R $USER ~/.claude/sessions/` as a
remedy while the line above it already used the resolved directory, so a
session with the state directory redirected was told to fix a path unrelated to
its error.

### #223 · audit done, file tier scoped separately

Measured across `src/autorun`: 50 `CONFIG.get` sites, 68 `os.environ.get`
sites, 5 modules reading both.

The important distinction the audit produced: of the environment variables
read, 25 are **harness-supplied facts** — `CLAUDE_SESSION_ID`,
`CODEX_PROJECT_DIR`, `PI_SESSION_ID` and so on. Those are inputs, not settings,
and correctly have no CONFIG or file tier; giving them one would invite a user
to "configure" something the harness dictates.

That leaves roughly eight real settings (`AUTORUN_HOME`, `AUTORUN_DEBUG`,
`AUTORUN_BUFFER_LIMIT`, `AUTORUN_DISABLE`, `AUTORUN_USE_DAEMON`,
`AUTORUN_NO_TRUNCATE`, and the two guard-enabled flags). Several have no CONFIG
entry at all and are env-only, so they resolve through two tiers rather than
four.

**The file tier does not exist as a general facility.** Three per-feature config
files exist with three separate loaders —
`~/.autorun/plan-export.config.json`, `plan-notify.config.json`,
`task-lifecycle.config.json` — and none backs the CONFIG dict.

**Update — built, after reconsidering the risk.** The argument above was
weaker than it read. "Changes how every setting resolves" is true of the code
path and false of the behavior: with no config file present, which is every
existing install, resolution is byte-identical. Declining to build something
explicitly asked for, on a risk that evaporates under inspection, is worse than
building it carefully.

`USER_CONFIG_FILENAME` (`autorun.config.json` under `AUTORUN_HOME`) is overlaid
onto the defaults once, at import, rather than consulted at each of the ~50
`CONFIG.get` sites. That is what makes the tier arrive everywhere at once
instead of wherever someone remembered to add it. The environment stays above
the file because the resolvers read env vars before CONFIG, which is the point
of the ordering: a value exported for one session should not be overridden by a
file written for the whole machine.

Unknown keys and mismatched types are declined. Bools are excluded from the
numeric check on purpose, since `isinstance(True, int)` is true and a flag
written as `1` should not satisfy a numeric setting.

One thing this cost, worth recording: the first version of the import-time test
used `importlib.reload`, which rebinds `config.CONFIG` to a new dict while every
module that already did `from .config import CONFIG` keeps the old one. It split
the process into two configurations and broke six unrelated tests. The test runs
in a subprocess now.

### #225 · DONE in a scope that does not guess

`config.harness_version(cli_type)` resolves `AUTORUN_HARNESS_VERSION` first,
then whatever the platform registry declares in the new
`Platform.version_env_vars`. That tuple is **empty for every harness today**,
and a spec check refuses any entry whose name contains "SDK".

That refusal is the point. `CLAUDE_AGENT_SDK_VERSION` is sitting in the
environment, looks usable, and shares its trailing component with the CLI
build — which is exactly the sort of near-miss that gets registered by someone
in a hurry. It reports the Agent SDK's version, not the CLI build a workaround
is described against, and a permission gate must not turn on a resemblance.

So ranges are usable today through the explicit override, auto-detection waits
for a documented source, and "unknown" resolves to the pre-range behavior.

### #226 · surveyed against the running build

Installed here: **Claude Code 2.1.235**, above the 2.1.233 threshold in
https://github.com/anthropics/claude-code/issues/80305.

The deferred-task-tool behavior is not merely reported, it was observed in this
session: `TaskCreate`/`TaskUpdate` arrived in the deferred-tool list and had to
be loaded through `ToolSearch` before use. The existing workarounds cover it,
and the version machinery can now express the bound rather than only describing
it in prose.

autorun registers eight Claude hook events (`PreToolUse`, `PostToolUse`,
`SessionStart`, `Stop`, `SubagentStop`, `UserPromptSubmit`, `PreCompact`,
`PostCompact`); `tests/harness_hook_events.py` owns that allowlist and passes.
No further divergence was observable from this build; a wider survey needs
upstream release notes rather than inference.

The survey did surface a gap in the range feature itself: values were honored
from the environment tier but a range written into CONFIG was only tested for
truthiness, so it silently stayed on for every version — the opposite of what
its author asked for, with no sign of being ignored. Both tiers now share one
vocabulary.

### Cost of the new hot-path code, measured

`workaround_applies` runs on the PreToolUse path, so its cost was measured
rather than asserted (100k iterations, this machine):

| Path | Cost | `packaging` imported |
|------|------|----------------------|
| default, no env var set | 3.96 µs/call | no |
| explicit `always` | 1.68 µs/call | no |
| version range | 28.4 µs/call | yes, only here |

Against the 3-second PreToolUse dispatch budget in
`daemon_dispatch_timeouts_seconds`, the default path costs about 0.0001% of the
allowance. The lazy import is confirmed working: `packaging` is absent from
`sys.modules` after 200k calls through the word grammar and appears only once a
flag actually holds a range.

Deliberately not memoized: caching `SpecifierSet` per spec string would save
~28 µs on a path that runs at most once per hook event. Note the ceiling rather
than build the cache — if ranges ever move into a per-tool loop, revisit.

The three new spec-check tests read and AST-parse the source tree. That cost is
CI-only; no runtime module imports them.

### #225 · IN PROGRESS · what version each harness actually reports

Measured on this machine rather than assumed:

* There is **no `CLAUDE_CODE_VERSION`** in the environment.
* The only version-bearing variable present is
  `CLAUDE_AGENT_SDK_VERSION=0.3.234`.
* `docs/claude-code-hooks-api.md` documents no version field in the hook
  payload, and no captured payload in `~/.autorun/hook_entry_debug.log` carries
  one.

[Inference] `CLAUDE_AGENT_SDK_VERSION=0.3.234` shares its trailing component
with Claude Code 2.1.234, so the SDK patch number may track the CLI build. That
is a pattern in two observations, not a documented contract, and a permission
workaround must not be gated on a guessed correspondence.

### D-3 · FIXED · the same hardcoded-name defect at a second site

Found by auditing the rest of the 2026-08-19 changes rather than by a failing
test. `task_lifecycle.py:3991`, the SessionStart handler that asks Claude to
load its deferred Task tools, gated on
`platform_for(ctx.cli_type).name != "claude"` — the same condition
`_workaround_flag_applies` resolves, and the same defect, at a site the first
fix did not touch.

This is the lesson worth keeping: fixing only the copy that a failing test
happened to cover leaves the pair able to disagree. Both now read
`Platform.gates_mutable_task_tools`, and a spec check fails on any harness-name
comparison in that module.

The sweep classified every other `== "claude"` in the tree as correct:
`hooks/hook_entry.py` is stdlib-only by design and cannot ask the registry
anything, and the `installer/` sites compare a *config flavor* (ForgeCode uses
Claude's settings format), which is a different concept that happens to share
the word.

Consequence for the design: the version source is per-harness data on the
`Platform` registry plus an explicit `AUTORUN_HARNESS_VERSION` override at the
top of the precedence chain, and "unknown" stays a first-class value that
resolves to today's behavior. That way a session whose harness reports nothing
usable behaves exactly as it does now, and the ranges only ever *narrow* a
workaround where the version is actually known.

### F-10 · FIXED · a probe imported autorun through the working directory

Found by CI going red on `6d8f50d3`, the commit that added F-7's config file
tier. All 11 matrix jobs failed on one test, `test_config_precedence_chain.py::
test_the_live_config_reflects_the_file_at_import`, and the whole local suite
passed.

The test spawned `sys.executable -c "from autorun.config import CONFIG; ..."`
with no `cwd`. `python -c` puts the *parent's* working directory at
`sys.path[0]`, and CI runs pytest from `plugins/autorun/`, which holds
`autorun.py` — the bootstrap launcher. A module shadows a package of the same
name, so the probe imported the launcher, failed on `autorun.python_check`, and
exited 1. The documented local command runs from the repository root, where
nothing shadows the package, so the defect was invisible to every local run.

Two things made it cost more than it should have:

- The assertion was `assert result.returncode == 0, result.stderr`, and the
  launcher prints its diagnostic to **stdout**. The CI failure therefore read
  `AssertionError:` with an empty message and `assert 1 == 0`. The assertion now
  reports returncode, stdout and stderr together.
- The hazard was already known here. `test_task_14_cli_non_interactive.py:39`
  defines `spawn_kwargs`, whose docstring is "Subprocess options that keep
  `import autorun` off the launcher shim" and which supplies `cwd`. Two other
  modules solve it the other way, passing `SRC_DIR` and inserting it at
  `sys.path[0]`. The fix follows the second pattern; nothing needed inventing.

The spec check is `test_cross_platform_spec.py::
test_no_probe_imports_autorun_through_the_working_directory`, added as item 5 in
that module because it shares the shape of the four Windows defects already
there: correct where it was written, wrong in the environment that runs it, and
silent at the point of the mistake. It resolves probe bodies held in a name,
requires a real `from|import autorun` rather than the substring (which would
have flagged `shutil.which('autorun')` in `error_handling.py`), and skips calls
that spread `**kwargs`, since a shared helper supplying `cwd` is invisible to a
source scan. Run against the tree it reported five sites; all five were
false positives of those two kinds, which is why both exclusions exist.

### F-11 · FIXED · the Pi bridge read hook silence as a malfunction

Reported live: Pi began answering ordinary commands with "[autorun] hook entry
returned an invalid response. Blocking tool use to avoid fail-open. ... then
retry".

`bridge_template/daemon-client.mjs:askHookEntry` spawns `hooks/hook_entry.py`
whenever the daemon is unreachable, then ran `JSON.parse(stdout)` and denied on
any throw. Measured against the live hook before changing anything: an allow
exits 0 and writes **zero bytes**, because silence is how the hook protocol
spells "no decision" — only a decision puts JSON on stdout. `JSON.parse("")`
raises SyntaxError, so the catch converted every allow taken through the
fallback into a deny.

The trigger was this session's own install: each `autorun --install` restarts
the daemon, and the cache-venv repair left it unreachable for a stretch. Any
daemon restart, install, or crash produced the same wall of blocks.

Empty stdout is now a non-decision. Two things were deliberately *not* widened:
output that is present but unparseable still denies, because a permission gate
that cannot read its own verdict must not fail open; and a silent exit 2 still
denies, because the issue #4669 workaround puts the denial reason on stderr,
which this bridge does not capture, so the exit code is the surviving signal.

Verified end to end through the installed extension with the daemon
deliberately unreachable: allow → no denial, `cat /etc/hosts` → still blocked
with the real reason. One fix covers Pi, Prime and OpenCode, which share the
transport; all three installed copies hash-match the source.

Spec guard: `test_pi_bridge.py::spawned_output_parsed_without_an_empty_guard`
extracts every `child.on("close", ...)` body by paren balance and fails on one
that parses spawned output with no emptiness test. Its third self-check — that
the extracted body is whole rather than truncated — caught a real bug in the
first scanner, which counted parens from the wrong index and would have passed
by not looking.

### F-12 · FIXED · a hung harness CLI cost one timeout per command

`autorun --install --force` on 2026-08-20 printed four consecutive
`TimeoutExpired ... after 120 seconds` failures for four `codex plugin remove`
calls: eight minutes. `codex --version` also times out with zero output on this
machine, so the binary answers nothing at all.

`registration.py`'s own module docstring already promised "a hung CLI still
costs one timeout rather than several". The removal path did not honour it: it
runs with `stop=False` so that withdrawing from a harness that no longer has
the plugin is not an error, and a `TimeoutExpired` arrived as an ordinary
failure. "Never stop at a failure" is about an absent plugin; it was never
about a binary that does not respond.

`Outcome` now carries `timed_out`, `_perform` sets it for `TimeoutExpired`
only, and `_sequence` stops on it even when `stop` is False, appending an
outcome that names how many commands were skipped. Measured after the fix: the
same install takes 164 seconds and reports one timeout plus "3 further codex
command(s) — skipped, codex did not respond within the timeout".

### F-13 · OPEN · Windows CI trips the dispatch circuit breaker under load

Two tests failed on the Windows job of run 32316628597 and pass everywhere
else: `test_claude_e2e.py::TestClaudeHookEntryPoint::
test_staleness_reminder_in_system_message_posttooluse` (got
`[AR_EVENT_V1:daemon_dispatch_contained]` in place of the reminder) and
`::test_staleness_pretooluse_warn_then_deny` (got no response at all).

Mechanism, from `core.py:_dispatch_with_timeout`: when a handler exceeds
`daemon_dispatch_timeouts_seconds` (PostToolUse 2.0, PreToolUse 3.0) the daemon
opens a circuit for `daemon_dispatch_timeout_cooldown_seconds` (5.0) and later
dispatches of that event return a contained response immediately. That Windows
job ran the suite in 1618 seconds against roughly 330 on Linux, so a two-second
handler budget is routinely exceeded for reasons unrelated to the behaviour
under test. This is the breaker working, not a defect in the staleness feature.

Not fixed, and deliberately so. The two candidate fixes both have costs worth a
decision rather than a guess:

- Raise the budgets under test. `test_client_fail_closed.py` and
  `test_core.py` assert the *relationships* client > max dispatch and wrapper >
  client, so raising one means moving all three of an interlocking ladder that
  many tests depend on.
- Wait out the cooldown and ask once more. Bounded and honest for the
  containment case — a slow machine then passes, a genuinely broken handler is
  contained again and fails — but the second failure is "no response at all",
  which a containment-shaped retry does not cover, and a blanket retry would
  mask real timeouts.

Neither can be validated from this machine: the failure needs Windows-runner
load, which does not reproduce on an idle Mac. Choosing between them is the
maintainer's call.

### F-14 · ROOT-CAUSED · a forced install replaces Claude's plugin cache, venv and all

The symptom that started this: after every `autorun --install --force`, the
hook interpreter at `~/.claude/plugins/cache/autorun/ar/1.0.0rc1/.venv` could no
longer `import autorun`, so the documented repair (`uv venv --clear` plus
`uv pip install --reinstall`) had to be run again by hand each time.

The first guess — "the installer resets the venv and should repopulate it" —
was wrong, and two sandbox runs settled it. Preparing a cache directory with a
sentinel file and recording its inode, then running a sandboxed
`--install --force --claude`:

    inode before        : 1352731716
    inode after         : 1352732273
    sentinel survives   : False
    entries afterwards  : 41
    dev detritus present: .coverage .pytest_cache .ruff_cache .venv
                          __pycache__ demo examples htmlcov

The directory is *replaced*, not filled, and what replaces it is a copy of the
developer's working tree. The live cache shows the same contents, `.DS_Store`
and `build/` included.

Two facts rule autorun's own fallback out. `orchestrate.py:597` only calls
`claude.cache_fallback` when `not cached.is_dir()`, and `fs.fill_tree` returns
`None` the moment the target exists. Both were designed for exactly this:
`claude.py:21` states "An existing cache is never replaced: Claude may have
added its managed `.venv` there", and the installer's own trap list says a
versioned harness cache belongs to the harness.

[Inference, from those two guards plus the observed replacement] the actor is
Claude's own CLI: `autorun --install` drives `claude plugin install`, and for a
locally-sourced marketplace that copies the plugin directory — here the dev
checkout — over its versioned cache. Autorun asks for the install; Claude
performs the copy. That also explains why a user installing from a published
wheel would see the venv reset but not the `htmlcov`/`.coverage` clutter, since
a wheel carries none of it.

Consequence, and it is a real one: any `--force` install leaves the Claude hook
venv unable to import autorun. That is survivable — `hooks/hook_entry.py` is
stdlib-only by design and the daemon does the policy work, and a probe taken in
exactly that state still returned exit 2 with `permissionDecision: deny` for
`cat` and exit 0 with zero stdout and zero stderr for `echo hi` — but it costs
the package-import fallback for when the daemon is unreachable, which is the
path F-11 was about.

Open design decision, for the maintainer rather than a guess from here:

- Stop re-running `claude plugin install` when the cache already matches what
  is shipped, so the copy never happens. Smallest blast radius, but it means
  autorun deciding when Claude's own registration is unnecessary.
- Keep the copy and have the installer restore the venv afterwards. Contradicts
  `claude.py`'s stated ownership rule, which exists to stop autorun writing
  into a tree Claude prunes.
- Leave the behaviour and make the docs say plainly that `--force` always
  requires the venv repair. Cheapest, and it leaves a manual step in the
  documented workflow that is easy to forget — it was forgotten twice today.

Not attempted here: each candidate changes a live install path shared by every
session on the machine, and the sandbox can prove the defect but not that a fix
leaves Claude's own plugin bookkeeping intact.
