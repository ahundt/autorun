# Plan Addendum: DRY, Schema, and Version Source-of-Truth Audit

This addendum supersedes the DRY/schema/version parts of
`2026_07_08_1921_plan_autorun_major_bugs_multi_harness_skill_consolidation.md`
for the active worktree implementation.

## Executive Correction

Before each implementation checkpoint, inspect the existing code and tests for
the source of truth. Do not add a parallel registry, schema constant, version
constant, manifest parser, command table, platform table, hook schema table, or
installer capability table unless the audit proves no reusable source exists.

The implementation must preserve the consolidated superset of useful autorun
functionality across all supported harnesses. The default behavior should be
easy to use correctly, hard to use incorrectly, automatic when the safe action is
known, and explicit when user choice is required. When autorun cannot safely
solve a problem itself, it must report the exact state, the reason automation did
not proceed, and the smallest reliable user action that can resolve it.

## Existing Sources of Truth Found

- Package/release version:
  - `plugins/autorun/pyproject.toml`
  - root `pyproject.toml`
  - `plugins/autorun/src/autorun/__init__.py`
  - `plugins/autorun/src/autorun/metadata.json`
  - `.claude-plugin/plugin.json`
  - `.codex-plugin/plugin.json`
  - `gemini_template/gemini-extension.json`
  - `.claude-plugin/marketplace.json`
  - Guarded by `plugins/autorun/tests/test_dual_platform_hooks_install.py::TestManifestFiles::test_version_consistency`
- Platform capabilities:
  - `plugins/autorun/src/autorun/platforms.py::PLATFORMS`
  - Guarded by `plugins/autorun/tests/test_platform_registry.py`
- Command and hook handler registration:
  - `plugins/autorun/src/autorun/core.py::AutorunApp`
  - `plugins/autorun/src/autorun/plugins.py::app`
  - Existing tests inspect `plugins.app.command_handlers` and `plugins.app.chains`.
- Hook response schemas:
  - `plugins/autorun/src/autorun/core.py::HOOK_SCHEMAS`
  - Codex-specific field helpers in `core.py`
  - Guarded by `test_hook_entry.py`, `test_codex_response_schema.py`, and related schema tests.
- Task lifecycle persisted-state schema:
  - `plugins/autorun/src/autorun/task_lifecycle.py::TaskLifecycle.SCHEMA_VERSION`
  - Guarded by `test_task_lifecycle_comprehensive.py` and `test_task_lifecycle_ghost_task_bug.py`.
- Scoped allow/deny grammar:
  - `plugins/autorun/src/autorun/scoped_allow.py`
  - Used by command blocking and cache guard.

## Required Per-Checkpoint Audit

Each checkpoint must start with a short source-of-truth pass:

1. Search for existing constants, registries, schemas, and tests related to the task.
2. Reuse or extend the existing source.
3. If a new persisted schema is necessary, define a named module-level constant
   and test it. Do not independently version ephemeral diagnostics.
4. Do not add separate schema/version pathways for diagnostic output unless it
   is a persisted or externally consumed compatibility contract.
5. If a new diagnostic output references release version, import
   `autorun.__version__` or existing metadata helpers rather than duplicating
   version literals.
6. If a new capability row references a platform, derive it from `PLATFORMS`.
7. If a new command or skill row references command behavior, derive it from
   `app.command_handlers` or the future command registry, not markdown copies.
8. Record any unavoidable duplication with a migration/deletion follow-up.
9. Preserve useful behavior from every existing harness unless a test and note
   prove the behavior was unsafe, broken, or impossible on that harness.
10. Prefer an automatic, idempotent repair when autorun owns the broken state;
    otherwise produce actionable diagnostics rather than silently failing or
    requiring the user to infer the next command.

## Product and Maintainer Acceptance Gates

Every phase must pass these checks before it is considered implementation-ready:

- **Consolidated superset:** The phase keeps the union of useful Claude, Codex,
  Gemini-family, Qwen, and future Antigravity behavior. A harness-specific
  limitation must be represented as capability metadata and surfaced in status,
  not hidden in copied code or stale docs.
- **Correct by default:** Install, status, hook handling, command parsing, and
  daemon restart paths choose the safe common case automatically. Required user
  choices, such as Codex user hooks vs plugin hooks vs explicit `both`, must be
  named and persisted through the existing config/status pathway.
- **Hard to misuse:** Destructive or broad lifecycle actions must be scoped by
  ownership markers, `AUTORUN_HOME`, platform metadata, and existing session
  locks. Reinstall and rollback tests must prove third-party hooks, skills, and
  ongoing sessions survive.
- **Actionable when blocked:** If automatic repair is unsafe or impossible,
  output must include what was inspected, what was found, why autorun stopped,
  and the precise next command or config setting the user can apply.
- **Low overhead:** Hot hook paths must remain per-session, bounded, and
  configuration-driven. Expensive scans belong only in explicit diagnostics,
  doctor commands, or maintenance commands.
- **Single source of truth:** User-facing docs, skills, and status output should
  be generated from or validated against Python metadata whenever practical.
  Hand-written markdown may explain behavior, but must not become executable
  behavior by another name.

## Correction to Current Checkpoint

The capability snapshot is a read-only diagnostic, not a new capability registry.
It must:

- derive platforms from `PLATFORMS`;
- derive command aliases from `app.command_handlers`;
- derive hook chains from `app.chains`;
- import package version from `autorun.__version__`;
- avoid a separate schema/version field because this is a derived diagnostic,
  not a persisted compatibility contract;
- avoid installing hooks, restarting daemons, reading secrets, or writing user config paths.

## Rejection Rule

Reject any future implementation step if it adds:

- a hard-coded release version already available through package metadata;
- a second platform/capability registry not derived from `PLATFORMS`;
- a second hook schema table not derived from or explicitly layered on `core.py`;
- command markdown/skill behavior that cannot be traced to a Python command spec or handler;
- installer status parsing that ignores existing ownership/version metadata helpers;
- a harness-specific fix that silently drops useful behavior from another
  harness without a failing test, an explicit compatibility decision, and user
  guidance.

## Stage-by-Stage DRY Audit and Corrections

The original plan listed target modules such as `autorun.capabilities`,
`autorun.commands.registry`, `autorun.hooks.schemas`, and
`autorun.install.specs`. Those names describe desired boundaries, not permission
to create new parallel systems. The default implementation rule is:

1. Extend the existing module if it is already the source of truth.
2. Extract a new helper only when two or more existing call sites need the same
   behavior and the helper removes real duplication.
3. Add a new module only when the existing module would become less cohesive or
   unsafe to import on hot hook paths.

| Stage/checkpoint | Existing source of truth | Duplication risk | Corrected implementation rule |
|---|---|---|---|
| 0 Baseline/isolation | `PLATFORMS`, `plugins.app.command_handlers`, `plugins.app.chains`, `autorun.__version__`, existing version tests | Snapshot becoming a second registry or release-version source | Keep `capability_snapshot` read-only and derived; do not add independent snapshot schema/versioning unless it becomes a persisted external contract. |
| 1 Stale Stop marker and task-ignore | `task_lifecycle.py`, `plugins.py`, `session_manager.py`, `CONFIG` | Separate ghost-clear logic for Stop vs PostToolUse; markdown `/task-ignore` inline Python diverging from plugin handler | Extract one small stale-clear function inside `task_lifecycle.py` if needed, call it from both events, and register aliases to the same `handle_task_ignore`. |
| 2 Daemon ownership/restart | `ipc.py`, `restart_daemon.py`, `daemon.py`, test `DaemonManager` | Reimplementing process discovery, socket paths, or daemon home logic | Reuse `ipc.AUTORUN_CONFIG_DIR` and existing restart helpers; add ownership metadata where current helpers lack it. |
| 3 Hook schema/output/timeouts | `core.py::validate_hook_response`, `HOOK_SCHEMAS`, `Platform` response fields, `client.py`, `hook_entry.py`, `CONFIG` | New hook schema table drifting from current validation | Strengthen existing validators and tests first; only extract schema helpers after all callers route through the same code. Timeout values must come from `CONFIG`, not literals. |
| 4 Installer specs/rollback | `install.py`, `PLATFORMS`, `.claude-plugin` / `.codex-plugin` manifests, existing Codex install tests | A new installer spec layer duplicating `Platform.install_fn_name`, manifest parsing, hook ownership, or version reads | Add typed helpers inside `install.py` and derive platform rows from `PLATFORMS`; extract only after tests prove repeated logic. |
| 5 State doctor/retention/perf | `session_manager.py`, `task_lifecycle.py`, `TaskLifecycleConfig`, `ipc.py`, `logging_utils.py`, `CONFIG` | A new state store, new retention config, or scans over all history on hot hook paths | Diagnostics may be new, but state mutation must reuse `session_manager`; retention settings live in existing config dataclasses or `CONFIG`. Hot hook paths stay per-session. |
| 6 Command registry/adapters | `core.AutorunApp.command`, `plugins.app.command_handlers`, `_split_command_args`, `ScopedAllow`, `parse_scope_args` | Building a full new command framework while existing decorator registry still owns dispatch | First add metadata to existing command registration or adjacent thin specs; migrate task commands only; prove handlers and specs agree before broader conversion. |
| 7 Skills consolidation | `plugins/autorun/skills`, command markdown, future command specs, current installed skill metadata | Rewriting skills manually and drifting from command behavior | Do not generate/convert skills until command specs exist for the relevant family; validate checked-in skills against specs. |
| 8 Harness matrix | `platforms.py`, `config.detect_cli_type`, `core.CLI_TOOL_NAMES`, install platform probes | Treating Qwen/Antigravity as separate duplicate implementations or Gemini clones | Add capability fields to `Platform` only when tests need them; reuse Gemini-compatible mappings where true and add platform-specific fields only for proven divergence. |
| 9 Release/docs/version | `test_version_consistency`, package metadata, manifests, README/GEMINI/AGENTS docs | A second version test or release script with independent expected versions | Extend the existing version consistency test when new artifacts need coverage; never hard-code expected release versions in new tests. |

Each stage also needs a user-facing acceptance assertion: status or diagnostic
output must show what autorun will do automatically, what it refused to do, and
what command/config option resolves the refusal.

## Semantic Duplication Checks Required Before Each Stage

Before writing code in a stage, run a targeted search and record the result in
the checkpoint notes:

- **Version/schema:** search `schema_version`, `SCHEMA_VERSION`, `__version__`,
  `metadata.json`, `plugin.json`, and `gemini-extension.json`.
- **Platform/harness:** search `PLATFORMS`, `detect_cli_type`, `hook_platforms`,
  `task_management_style`, and platform-specific install function names.
- **Command parsing:** search the command name, `_split_command_args`,
  `parse_scope_args`, `ScopedAllow`, and `app.command`.
- **Hook schema/output:** search `validate_hook_response`, `HOOK_SCHEMAS`,
  `output_hook_response`, and event names.
- **Install ownership:** search owner markers, hook source modes,
  `codex_hook_source`, `codex_plugin_marketplace`, manifest readers, and status
  helpers.
- **State/perf:** search `session_state`, `TaskLifecycleConfig`,
  `hook_state_lock_timeout_seconds`, `daemon_dispatch_timeouts_seconds`, and log
  rotation helpers.
- **Docs/skills:** search existing command markdown and `skills/*/SKILL.md`
  before adding text.

If the search finds an existing helper, use it or explicitly explain why it is
not adequate before adding anything new.

## Revised Implementation Guardrails

- Do not create `autorun.capabilities` until the snapshot and platform tests
  prove `PLATFORMS` cannot hold the needed metadata cleanly.
- Do not create `autorun.hooks.schemas` until `core.py` schema tests are green
  and extraction is mechanical.
- Do not create `autorun.commands.registry` as a second dispatcher. If command
  specs are needed, they must either decorate or validate `AutorunApp.command`.
- Do not create `autorun.install.specs` as a parallel installer. Start with
  typed dataclasses inside `install.py` derived from `Platform`.
- Do not add new timeout constants. Use `CONFIG` and add config-key tests.
- Do not add new state stores. Use `session_manager` and add indexes or archive
  manifests only through the existing locking model.
- Do not generate docs/skills until the relevant behavior is represented in
  Python metadata and covered by tests.

## Current Code Corrections From This Audit

- `capability_snapshot.py` is acceptable as a diagnostic because it derives all
  behavior from existing registries.
- It must not introduce a separate schema/version pathway while it remains a
  derived diagnostic.
- Its package version must be `autorun.__version__`.
- The worktree isolation tests are valid because they caught legacy import-time
  creation of `~/.claude/sessions`; fixes should keep import side effects small
  and use existing env vars (`AUTORUN_TEST_STATE_DIR`, `AUTORUN_HOME`).

## Updated Continuation Rule

The next implementation step may proceed only after:

1. the capability snapshot/isolation checkpoint is green;
2. adjacent version/platform registry tests are run or explicitly deferred with
   a reason;
3. the next stage begins with the semantic duplication search checklist above.

## Deep Phase and Task Consolidation Audit

This section walks every major phase/task from the original plan and converts it
from "build new things" into "extend the smallest existing source of truth."
Implementation should follow this table before touching code in that phase.

### Phase 0: Baseline and Safety Envelope

Existing DRY anchors:

- `platforms.py::PLATFORMS`
- `plugins.app.command_handlers`
- `plugins.app.chains`
- `autorun.__version__` / metadata files
- `ipc.AUTORUN_CONFIG_DIR`
- `session_manager.AUTORUN_TEST_STATE_DIR` support
- `plugins/autorun/tests/conftest.py::DaemonManager`

Semantic duplication to avoid:

- A new platform inventory independent of `PLATFORMS`.
- A new command list independent of `app.command_handlers`.
- A new hook event list independent of `app.chains`.
- A second version/schema path for derived diagnostics.
- A test harness that bypasses existing daemon/test isolation helpers.

Consolidated plan:

- Keep `capability_snapshot.py` as a read-only diagnostic derived entirely from
  existing registries.
- Do not add persisted schema versioning to the snapshot. Use package version
  and git commit only.
- Worktree isolation tests should pin import-time side effects and reuse
  `AUTORUN_HOME`, `AUTORUN_TEST_STATE_DIR`, and `AUTORUN_USE_DAEMON=0`.
- The snapshot should make missing or inventory-only capabilities visible so
  later phases can prove the useful capability superset was preserved.

Validation required before leaving phase:

- `test_capability_snapshot.py`
- `test_worktree_isolation.py`
- `test_platform_registry.py`
- `TestManifestFiles::test_version_consistency`

### Phase 1: Stale Task Escape Hatch

Existing DRY anchors:

- `task_lifecycle.py::TaskLifecycle`
- `TaskLifecycleConfig`
- `TaskLifecycle.SCHEMA_VERSION` for persisted task state
- `_ghost_id_set_hash`, `_reset_ghost_counter`
- `plugins.py::_ghost_marker_regex`
- `plugins.py::clear_ghost_tasks`
- `plugins.py::reset_ghost_counter_on_activity`
- `plugins.py::handle_task_ignore`
- `session_manager.session_state`

Semantic duplication to avoid:

- Separate ghost/stale-clear parsers for `Stop` and `PostToolUse`.
- Separate state writes outside `TaskLifecycle`.
- New markdown or CLI task-ignore implementations that bypass `handle_task_ignore`.
- New config keys when `TaskLifecycleConfig` already owns stale-clear settings.
- Weakening `TaskLifecycle.SCHEMA_VERSION` migrations.

Consolidated plan:

- Move only the common stale-marker extraction/clear operation into one reusable
  helper near existing ghost-clear code. Prefer `task_lifecycle.py` if it needs
  task-state mutation; prefer `plugins.py` only if it is pure prompt/result
  extraction.
- Route both `Stop` and `PostToolUse` through the same helper.
- Register `/task-ignore`, `ar:task-ignore`, and Codex-native `ar task-ignore`
  through the same `handle_task_ignore` dispatcher path where possible.
- Keep persisted state migrations under `TaskLifecycle.SCHEMA_VERSION`; do not
  create a second task-state schema.
- If stale-clear cannot be applied safely, the block message must identify the
  task ids, the session scope, whether the marker threshold is armed, and the
  exact user override form for the active harness.

Tests to add first:

- Stop no-tool-cycle stale marker consumption.
- Marker before threshold does not clear.
- Marker for another session does not clear.
- Malformed marker does not clear.
- Real incomplete task still blocks.
- `/ar:task-ignore`, `/task-ignore`, `ar:task-ignore`, and `ar task-ignore`
  parity, using one handler.

Adjacent tests:

- `test_ghost_clear.py`
- `test_task_lifecycle_ghost_task_bug.py`
- `test_task_lifecycle_comprehensive.py`
- `test_task_cli_commands.py`

### Phase 2: Command Runtime and Alias Consolidation

Existing DRY anchors:

- `core.AutorunApp.command`
- `core.canonicalize_command_prompt`
- `Platform.command_prefixes`
- `plugins.py::_split_command_args`
- `scoped_allow.py::parse_scope_args`
- `scoped_allow.py::ScopedAllow`
- `CONFIG["command_mappings"]`

Semantic duplication to avoid:

- A second dispatcher that competes with `AutorunApp`.
- A second parser for quoted command arguments.
- A second scope grammar for `N|5m|perm`.
- Markdown commands with executable logic different from Python handlers.

Consolidated plan:

- Do not create `autorun.commands.registry` as a separate dispatcher in the
  first pass.
- If metadata is needed, add thin command-spec metadata that decorates or
  validates `AutorunApp.command` registrations.
- Migrate only one command family first, likely task lifecycle, and prove the
  spec and `app.command_handlers` agree.
- Delay skill/doc generation until command specs exist and two command families
  have proven the pattern.
- User-facing command help must prefer the active harness's reliable form
  (`ar:*` or `ar <cmd>` for Codex where slash interception applies) while
  preserving working legacy aliases.

Tests to add first:

- Command alias snapshot from `app.command_handlers`.
- Spec-to-handler agreement for the migrated family only.
- Quoted args, empty args, unknown command, and Codex `ar:*`/`ar <cmd>` forms.

### Phase 3: Hook Schema, Output, and Timeout Discipline

Existing DRY anchors:

- `core.HOOK_SCHEMAS`
- `core.validate_hook_response`
- Codex field helpers in `core.py`
- `Platform.schema_type`
- `Platform.unsupported_response_fields_by_event`
- `client.output_hook_response`
- `client.daemon_response_timeout_for_cli`
- `CONFIG["hook_state_lock_timeout_seconds"]`
- `CONFIG["daemon_dispatch_timeouts_seconds"]`
- `hooks/hook_entry.py` and existing hook-entry tests

Semantic duplication to avoid:

- New schema tables that drift from `validate_hook_response`.
- Platform-specific schema branches outside `Platform`/`core.py`.
- Magic timeout numbers in client, daemon, or hook entry code.
- Stderr/stdout handling outside `client.output_hook_response` or hook-entry
  plumbing.

Consolidated plan:

- Strengthen `core.py` schema validation and tests first.
- Extract a schema helper only after extraction is mechanical and all callers
  already route through `validate_hook_response`.
- Add timeout tests against config keys, not literal constants.
- Keep failure-mode behavior event-specific: tool gates fail closed, advisory
  prompt/lifecycle paths fail open except real Stop enforcement.
- Timeout and malformed-output messages must distinguish daemon slowness,
  daemon unavailability, invalid JSON, and harness schema rejection, and should
  point to the existing restart/install/status command instead of generic
  failure text.

Tests to add first:

- Per-event schema matrix, including Claude, Codex, Gemini/Qwen-compatible, and
  unknown CLI fallback.
- Exit-0-with-stderr and invalid JSON hook-entry behavior.
- Daemon dispatch timeout below outer hook timeout for each platform.

### Phase 4: Installer Composition, Idempotence, Upgrade, Rollback

Existing DRY anchors:

- `install.py`
- `platforms.py::PLATFORMS`
- `Platform.install_fn_name`
- `_read_plugin_version`
- `_detect_available_clis`
- `_install_for_codex`, `_install_for_gemini`, `_install_for_qwen`,
  `_install_for_antigravity`, `_install_for_forgecode`
- Codex helpers:
  - `_codex_hook_source_from_env`
  - `_codex_plugin_marketplace_from_env`
  - `_codex_uses_user_hooks`
  - `_codex_uses_plugin_hooks`
  - `_merge_codex_hooks`
  - `_build_codex_hook_block`
  - `_CODEX_PLUGIN_OWNED_MARKER`
  - `_CODEX_SKILL_OWNED_MARKER`
  - `_codex_plugin_marketplace_status`
- Existing Codex/temp-home install tests.

Semantic duplication to avoid:

- A parallel `install_specs.py` installer that re-parses manifests and platform
  data.
- New version readers independent of `_read_plugin_version`.
- New ownership markers beyond the existing marker strategy unless the old
  marker cannot express the case.
- Dedupe logic that ignores `codex_hook_source` and explicit `both`.
- Installer cleanup that removes third-party hooks.

Consolidated plan:

- Add typed helper dataclasses inside `install.py` first, only where they remove
  repeated dictionaries/tuples.
- Derive platform install rows from `PLATFORMS`.
- Preserve explicit Codex `both` mode; status should label it intentional.
- Remove only autorun-owned stale hooks/skills/plugin cache entries.
- Temp-home tests must cover third-party hooks, RTK/slayzone-like entries, user
  hooks, stale plugin cache versions, and mode transitions.
- Installer status must separate "installed and owned by autorun", "third-party
  preserved", "stale autorun-owned entry", "explicit both mode", and "manual
  user action required" so reinstall and rollback are predictable.

Tests to add first:

- Duplicate Codex user+plugin hook status is failure unless mode is explicit
  `both`.
- Switching `plugin -> user` removes only autorun-owned plugin hooks.
- Switching `user -> plugin` removes only autorun-owned user hooks.
- Third-party hooks and skills survive reinstall and rollback.

### Phase 5: Long-Lived State, Logs, Retention, and Performance

Existing DRY anchors:

- `session_manager.py`
- `TaskLifecycleConfig`
- `TaskLifecycle.cli_gc`
- `session_manager.all_session_state`
- `logging_utils.py`
- `CONFIG["hook_state_lock_timeout_seconds"]`
- `CONFIG["daemon_dispatch_timeouts_seconds"]`
- Existing stale lock, thread safety, and task lifecycle tests.

Semantic duplication to avoid:

- A second state backend.
- Hot hook paths that call `all_session_state`.
- New retention config disconnected from `TaskLifecycleConfig` or `CONFIG`.
- Deleting or compacting without archive/manifest/restore.
- Reading full prompts/transcripts/logs for normal diagnostics.

Consolidated plan:

- Keep all state reads/writes through `session_manager`.
- Add diagnostics as read-only first.
- Use per-session access in hot hooks; reserve `all_session_state` for explicit
  maintenance commands only.
- Extend `TaskLifecycleConfig` for persisted task retention knobs if needed.
- Use `logging_utils` for bounded logs; do not add ad hoc file logging.
- Long-lived history retention should preserve useful session history by
  default. Cleanup must be dry-run capable, archive/manifest backed when it
  removes data, and explicit about what can be restored.

Tests to add first:

- Large-state fixtures at 10 MB, 25 MB, and 100 MB.
- Hot no-op hook path does not scan all sessions.
- GC dry-run does not mutate.
- Archive/restore manifest round trip.
- Corrupt JSON, unknown DB-like files, and giant logs are classified without
  deletion.

### Phase 6: Daemon Handoff and Restart Scoping

Existing DRY anchors:

- `ipc.AUTORUN_CONFIG_DIR`
- `ipc.AUTORUN_SOCKET_PATH`
- `ipc.AUTORUN_LOCK_PATH`
- `restart_daemon.py`
- `client.py` daemon startup/retry logic
- `daemon.py` cleanup ownership helpers
- `test_daemon_startup_race.py`
- `test_daemon_restart_safety.py`

Semantic duplication to avoid:

- New daemon discovery by raw command string when lock/socket ownership already
  exists or can be extended.
- Restart code that kills all `autorun.daemon` processes.
- Tests that launch production daemons or mutate default `~/.autorun`.

Consolidated plan:

- Scope restart operations to the current `AUTORUN_HOME`/`ipc.AUTORUN_CONFIG_DIR`.
- If process discovery is needed, make it an ownership-aware helper in
  `restart_daemon.py`.
- Existing startup-race and restart-safety tests remain the adjacent regression
  suite.
- Isolated daemon tests are allowed only after ownership scoping tests exist.
- A normal restart must not kill unrelated production or worktree daemons. If an
  unowned daemon blocks the socket, status should report the PID, home, and safe
  next step rather than guessing.

Tests to add first:

- Worktree daemon restart cannot kill a fake production daemon.
- Stale socket in worktree home is cleaned without touching default home.
- Missing PID file with held flock is handled.
- Unowned daemon is reported, not killed, by normal restart.

### Phase 7: Skills and Markdown Command Consolidation

Existing DRY anchors:

- `plugins/autorun/commands/*.md`
- `plugins/autorun/skills/*/SKILL.md`
- `plugins.app.command_handlers`
- Future command metadata, once proven by Phase 2
- Existing cache skill and cache command docs

Semantic duplication to avoid:

- Converting slash commands to skills before command metadata exists.
- Duplicating executable behavior in markdown command files.
- Side-effecting skills that can be invoked implicitly.
- Removing known working aliases before parity tests pass.

Consolidated plan:

- Do not begin broad skill conversion until Phase 2 command metadata is stable.
- First validate existing command markdown against handler aliases.
- Convert one low-risk grouped family after tests prove no alias loss.
- Skills should guide workflows; commands should remain quick state changes.
- Side-effecting skills need explicit user invocation semantics and tests.
- Skills must improve discoverability without increasing accidental side
  effects. When a tool cannot accept slash commands, the skill should teach the
  correct harness-native invocation and link back to the same command metadata.

Tests to add first:

- Every command markdown alias maps to a handler or documented compatibility
  path.
- Every generated/updated skill references command metadata, not copied tables.
- Side-effecting skill metadata cannot trigger implicit execution.

### Phase 8: Qwen, Gemini, Antigravity, Claude App, Codex App

Existing DRY anchors:

- `platforms.py::PLATFORMS`
- `config.detect_cli_type`
- `core.CLI_TOOL_NAMES`
- Gemini template installer and hook normalizer
- Qwen install rewrite tests
- Codex install/status tests
- README/GEMINI docs already describing capability levels, but not yet generated.

Semantic duplication to avoid:

- Treating Qwen as only a Gemini alias with no version/capability checks.
- Forking Gemini-compatible logic into copied Qwen/Antigravity code when a
  platform field can express the difference.
- Claiming Antigravity app/CLI hook support before read-only inventory proves it.
- Reading shell startup files containing secrets.

Consolidated plan:

- Extend `Platform` fields only where tests need new capability metadata.
- Reuse Gemini-family hook normalization for Qwen/Antigravity only when tests
  prove the event/config surface matches.
- Keep Antigravity read-only/inventory-only until hook/skill install APIs are
  verified with rollback.
- Document GLM/Z.AI setup by environment variable names only.
- Unsupported or unverified app/CLI integrations must appear as explicit
  capability states in diagnostics, not as missing docs or half-enabled
  installers.

Tests to add first:

- Qwen capability matrix is not merely a Gemini alias.
- Qwen GLM-5.2 env docs contain no secret values.
- Gemini live backend tests skip only for binary/auth unavailability.
- Antigravity inventory does not enable hooks without verified API.

### Phase 9: Release Readiness and Documentation

Existing DRY anchors:

- `TestManifestFiles::test_version_consistency`
- package metadata and manifests listed above
- README/GEMINI/AGENTS docs
- capability snapshot diagnostic

Semantic duplication to avoid:

- A second version consistency test with its own expected version.
- Release docs that manually duplicate capability tables without validation.
- Tag/push/install steps in the implementation pass.

Consolidated plan:

- Extend the existing version consistency test when new release artifacts need
  coverage.
- Do not hard-code `0.12.0` or future versions in new tests.
- Generate or validate docs from the same metadata once command/platform
  metadata exists.
- Keep tag, push, live install, and production daemon restart out of the
  worktree implementation unless explicitly approved in a later turn.
- Release readiness requires a capability/status snapshot that proves no useful
  harness behavior was silently dropped, plus clear caveats for any capability
  that remains inventory-only or manually configured.

## Implementation Order With DRY Gates

The original implementation order remains valid only with these inserted gates:

1. **Before item 2:** verify snapshot derives from existing registries only.
2. **Before item 4:** search task lifecycle/ghost-clear sources and add tests
   that fail against the existing event-order bug.
3. **Before item 7:** prove aliases can be handled by existing `AutorunApp`
   dispatch; do not add a second dispatcher.
4. **Before item 8:** inspect `TaskLifecycle.cli_gc`, `session_manager`, and
   `logging_utils`; diagnostics must be read-only first.
5. **Before item 10:** inspect existing daemon race/restart tests and extend
   them instead of launching production daemon processes.
6. **Before item 12:** inspect Codex install source-mode helpers and ownership
   marker tests; preserve explicit `both`.
7. **Before item 16:** do not generate docs until the relevant metadata exists
   in Python.
8. **Before item 18:** search local/platform docs and use read-only probes;
   no Antigravity/Qwen writes until tests and rollback exist.

## Current Validation Status

Completed for this addendum:

- Source-of-truth audit for version/schema, platform registry, command dispatch,
  stale-task lifecycle, hook schemas/timeouts, installer source modes, state
  persistence, daemon restart, and skills/docs.
- Consolidated rule that diagnostics do not get independent schema/version
  paths unless they become persisted external contracts.
- Focused validation already run after removing the extra snapshot schema path:

```bash
PYTHONPATH=/Users/athundt/.claude/autorun-worktrees/autorun-hardening-single-pass-20260709/plugins/autorun/src \
AUTORUN_USE_DAEMON=0 \
uv run --project /Users/athundt/.claude/autorun/plugins/autorun pytest \
  plugins/autorun/tests/test_capability_snapshot.py \
  plugins/autorun/tests/test_worktree_isolation.py \
  plugins/autorun/tests/test_platform_registry.py \
  plugins/autorun/tests/test_dual_platform_hooks_install.py::TestManifestFiles::test_version_consistency -q
```

Result: `28 passed`.

Stage 1 stale-task/task-ignore checkpoint:

- Source-of-truth pass confirmed the existing anchors are
  `TaskLifecycle`, `TaskLifecycleConfig`, `TaskLifecycle.SCHEMA_VERSION`,
  `plugins.py::clear_ghost_tasks`, `plugins.py::handle_task_ignore`,
  `session_manager`, and `core.AutorunApp`.
- The stale-clear marker parser and state mutation now live in
  `task_lifecycle.py`; `PostToolUse` and `Stop` route through that shared path.
- The escape hatch is threshold-gated, current-session scoped, and limited to
  currently blocking tasks. Partial clears refresh the blocking task list, so
  real unfinished work still blocks Stop and gives the AI feedback.
- `/ar:task-ignore`, `/task-ignore`, `ar:task-ignore`, and `ar task-ignore`
  route through the same Python handler.
- Validation:

```bash
PYTHONPATH=/Users/athundt/.claude/autorun-worktrees/autorun-hardening-single-pass-20260709/plugins/autorun/src \
AUTORUN_USE_DAEMON=0 \
uv run --project /Users/athundt/.claude/autorun/plugins/autorun pytest \
  plugins/autorun/tests/test_ghost_clear.py \
  plugins/autorun/tests/test_stop_chain_task_lifecycle.py \
  plugins/autorun/tests/test_task_lifecycle_ghost_task_bug.py \
  plugins/autorun/tests/test_task_cli_commands.py -q
```

Result: `31 passed` for `test_ghost_clear.py`; `82 passed` for the adjacent
task lifecycle suites.

Stage 2 daemon ownership/restart checkpoint:

- Source-of-truth pass confirmed daemon lifecycle ownership belongs in
  `ipc.py`, `restart_daemon.py`, `daemon.py`, `client.py`, and the existing
  `test_daemon_restart_safety.py`, `test_daemon_startup_race.py`, and
  `test_client_fail_closed.py` suites.
- The root risk was the broad orphan process sweep in
  `restart_daemon.py::restart_daemon`, which matched every process containing
  `from autorun.daemon import main` and could kill unrelated production or
  worktree daemons.
- The fix resolves the current source directory before orphan cleanup and kills
  only daemon processes whose command line contains both the daemon marker and
  the current `src_dir`. Other worktree/production daemons remain untouched.
- The former broad cleanup capability is preserved as the explicit
  `--restart-all-daemons` maintenance mode. Help text, command docs, and tests
  state that it can interrupt active autorun-backed sessions in other installs.
- `restart_daemon.py` still uses the existing restart lock, `ipc` paths,
  `_stop_daemon`, socket readiness polling, and pycache cleanup; no second daemon
  ownership store or raw production daemon action was added.
- Validation:

```bash
PYTHONPATH=/Users/athundt/.claude/autorun-worktrees/autorun-hardening-single-pass-20260709/plugins/autorun/src \
AUTORUN_USE_DAEMON=0 \
uv run --project /Users/athundt/.claude/autorun/plugins/autorun pytest \
  plugins/autorun/tests/test_daemon_restart_safety.py \
  plugins/autorun/tests/test_daemon_startup_race.py \
  plugins/autorun/tests/test_client_fail_closed.py -q
```

Result: `57 passed`. Ruff check for `__main__.py`, `restart_daemon.py`, and
`test_daemon_restart_safety.py`: passed.
