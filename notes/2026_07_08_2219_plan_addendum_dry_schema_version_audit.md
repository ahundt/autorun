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

Stage 4 re-audit, 2026_07_08_2355:

- Read-only source check:
  - `_merge_codex_hooks()` already strips autorun-owned user hook entries while
    preserving unrelated user hook entries and events.
  - `_CODEX_PLUGIN_OWNED_MARKER` and `_codex_owned_plugin_hook_source()` already
    persist the selected Codex hook source mode (`user`, `plugin`, `both`,
    `none`) in the existing marker strategy.
  - `_install_for_codex(..., codex_hook_source="plugin")` already removes
    user-level autorun hooks and packages Codex-specific plugin hooks.
  - `_install_for_codex(..., codex_hook_source="user")` already removes
    autorun-owned plugin hooks by replacing the autorun-owned plugin source copy
    without `hooks/hooks.json`, while keeping user-level autorun hooks.
  - `_install_for_codex(..., codex_hook_source="none")` already removes autorun
    hooks from both user and plugin sources while preserving skills/plugin
    assets.
  - `_codex_plugin_marketplace_status()` already fails duplicate user+plugin
    hook sources unless the owned marker explicitly records `both`.
- Existing tests satisfying the Phase 4 TDD requirements:
  - `test_install_for_codex_preserves_user_hooks`
  - `test_install_for_codex_idempotent`
  - `test_install_for_codex_plugin_hook_source_packages_codex_hooks_only`
  - `test_install_for_codex_both_hook_source_installs_user_and_plugin_hooks`
  - `test_install_for_codex_user_mode_removes_owned_plugin_hooks_and_keeps_user_hooks`
  - `test_install_for_codex_none_hook_source_removes_user_and_plugin_hooks`
  - `test_install_for_codex_skills_preserves_user_authored`
  - `test_install_for_codex_preserves_existing_personal_marketplace_entries`
  - `test_install_for_codex_does_not_clobber_user_owned_personal_plugin_dir`
  - `test_codex_plugin_marketplace_status_flags_cached_plugin_hooks`
  - `test_codex_plugin_marketplace_status_allows_explicit_both_hook_source`
- Validation:
  - `PYTHONPATH=plugins/autorun/src uv run --isolated --with pytest --with
    pytest-timeout --with filelock --with psutil pytest
    plugins/autorun/tests/test_codex_install.py -q` => `42 passed`.
- Maintainer conclusion:
  - No new code is required for Phase 4 at this point. Adding another install
    pathway or marker would duplicate already-tested behavior and increase
    rollback complexity. Future work should focus only on live-install
    verification after explicit approval, not on a second implementation.

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

Stage 5 audit, 2026_07_09_0008:

- Read-only source check:
  - `TaskLifecycle.handle_stop()` and `handle_session_start()` use per-session
    `session_state()` through `TaskLifecycle._session_state()`, not
    `all_session_state()`.
  - `TaskLifecycle.cli_gc()` uses `all_session_state()` only inside the explicit
    maintenance command, with `dry_run`, TTL, current-session protection,
    incomplete-task protection, optional archive, and confirmation controls.
  - Existing GC tests cover dry-run no mutation, archive-before-delete,
    current-session protection, incomplete-task protection, no-archive mode,
    bulk clear, and lock interaction.
- Root performance risk that remains:
  - `session_manager._JSONStore` stores every session key in one
    `daemon_state.json`. Even per-session `session_state(session_id)` loads and
    saves the full JSON object. Therefore normal hot hook paths avoid explicit
    all-session enumeration but still scale with total state file size.
  - This is a schema/storage architecture issue, not a simple accidental
    `all_session_state()` call. A correct fix needs a dedicated TDD slice for a
    consolidated session-manager migration strategy, rollback/restore behavior,
    and compatibility with `all_session_state()` maintenance consumers.
- Validation:
  - `PYTHONPATH=plugins/autorun/src uv run --isolated --with pytest --with
    pytest-timeout --with filelock --with psutil pytest
    plugins/autorun/tests/test_task_lifecycle_ghost_task_bug.py::TestGarbageCollection
    plugins/autorun/tests/test_task_lifecycle_ghost_task_bug.py::TestGCLocking::test_gc_uses_session_state_for_locking
    plugins/autorun/tests/test_task_cli_commands.py::test_cli_gc_dry_run_previews_without_changes
    plugins/autorun/tests/test_task_cli_commands.py::test_cli_gc_archives_before_deletion
    -q` => `11 passed`.
- Maintainer conclusion:
  - Do not add a second task-lifecycle persistence path. The next Phase 5 code
    change, if taken, should be inside `session_manager` and should preserve
    existing `session_state()` / `all_session_state()` APIs while adding
    migration tests with large fixtures and corrupt-file classification.

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
- README/GEMINI docs describe capability levels; the read-only generated
  inventory is `autorun --capability-snapshot`, covered by capability snapshot
  tests rather than copied tables in documentation.

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
- Prefer Antigravity native staged bundles when `agy plugin validate` processes
  hooks; keep the Gemini importer as fallback when native validation or install
  fails.
- Document GLM/Z.AI setup by environment variable names only.
- Unsupported or unverified app/CLI integrations must appear as explicit
  capability states in diagnostics, not as missing docs or half-enabled
  installers.

Tests to add first:

- Qwen capability matrix is not merely a Gemini alias.
- Qwen GLM-5.2 env docs contain no secret values.
- Gemini live backend tests skip only for binary/auth unavailability.
- Antigravity native install uses a staged bundle that validates hooks before
  install; importer fallback remains non-native compatibility coverage.

Stage 8 evidence update, 2026_07_08_2347:

- Web references checked:
  - Claude Code hooks reference:
    https://docs.anthropic.com/en/docs/claude-code/hooks and
    https://code.claude.com/docs/en/hooks. Relevant facts: command hooks read
    JSON on stdin; plugin hooks live under `hooks/hooks.json`; `PreToolUse` can
    return `hookSpecificOutput.permissionDecision`; hook locations include user,
    project, plugin, skill, and agent scopes.
  - OpenAI Codex hooks:
    https://developers.openai.com/codex/hooks. Relevant facts: Codex accepts
    `hookSpecificOutput.permissionDecision="deny"` for `PreToolUse`, also
    accepts older `{decision:"block"}` output, and explicitly rejects unsupported
    fields such as `permissionDecision:"ask"`, `continue:false`,
    `stopReason`, and `suppressOutput` for current tool-control paths.
  - Gemini CLI hooks reference:
    https://geminicli.com/docs/hooks/reference/. Relevant facts: command hooks
    use a `command` field, `type="command"`, and timeout is in milliseconds
    with default 60000.
  - Qwen Code repository:
    https://github.com/QwenLM/qwen-code. Relevant facts: the public project
    describes hooks, auto-skills, extensions, IDE plugins, headless mode, and
    multi-protocol providers. This supports treating Qwen as a Gemini-derived
    but independently identified harness, not as a hidden Gemini alias.
  - Public Antigravity/Gemini migration reporting:
    https://www.techradar.com/pro/google-is-making-gemini-cli-users-switch-to-its-new-antigravity-2-0-so-what-will-it-mean-for-you.
    This is not primary API documentation, but it reports Antigravity 2.0 CLI
    migration, hooks, skills, subagents, and plugins support. Treat it as
    contextual only; local CLI probes below are the implementation evidence.
- Local CLI evidence:
  - `agy --help` reports `plugin` / `plugins` subcommands plus `--print`,
    `--prompt`, `--sandbox`, and `--dangerously-skip-permissions`.
  - `agy plugin --help` reports `list`, `import [source]`, `install <target>`,
    `uninstall`, `enable`, `disable`, `validate [path]`, and `link <mp>
    <target>`.
  - `agy plugin list` shows imported `ar` from `gemini-cli` with components
    `skills`, `commands`, and `hooks` under
    `~/.gemini/antigravity-cli/plugins/ar`.
  - `agy plugin validate plugins/autorun/src/autorun/gemini_template` fails
    because native Antigravity validation expects `plugin.json` plus root
    `hooks.json`, not only `gemini-extension.json` and nested
    `hooks/hooks.json`. Later checkpoint `2026_07_09_0101` adds a staged
    native bundle with those files and keeps the importer as fallback.
  - `qwen --help` reports `extensions`, `hooks`, OpenAI-compatible auth flags,
    and `--auth-type` choices including `openai`.
  - `qwen extensions --help` reports `install`, `uninstall`, `list`, `update`,
    `disable`, `enable`, `link`, `new`, `settings`, and `sources`.
- Git history references:
  - `458c1c13` introduced the `Platform` dataclass registry as the single source
    of truth.
  - `9629c5d2` propagated explicit CLI identity through daemon/hook paths.
  - `19b1bfcb` added the Antigravity importer platform.
  - `2e6b661e` added Qwen Code hook support and Gemini-family install rewrites.
- Current root issue found during this phase:
  - Imported Antigravity `ar` hooks exist, but both
    `~/.gemini/antigravity-cli/plugins/ar/hooks/hooks.json` and root
    `~/.gemini/antigravity-cli/plugins/ar/hooks.json` still contained
    `--cli gemini`. That makes Antigravity sessions run with Gemini identity,
    defeating platform-specific detection, timeout, schema, and task-surface
    behavior.
- Corrected implementation rule:
  - Prefer a staged Antigravity native bundle when `agy plugin validate`
    reports hooks processed; use the Gemini-flavored importer path as fallback.
  - Reuse `_sync_gemini_extension_resources()` and
    `_set_gemini_family_hook_cli()` for Qwen, Antigravity, and custom
    Gemini-flavored extension directories.
  - `_set_gemini_family_hook_cli()` must rewrite both nested
    `hooks/hooks.json` and root `hooks.json`, because Antigravity import
    materializes both.
  - Custom flavored harnesses should be represented by an explicit target
    directory plus validated CLI identity routed through the same sync/rewrite
    helper. Do not copy the Gemini installer into a new harness-specific branch
    unless a tested API divergence requires it.
  - The custom-location helper must rewrite only autorun's own
    `hook_entry.py --cli gemini` command strings. It must not rewrite unrelated
    custom hooks that happen to contain `--cli gemini`.
  - User-facing custom harness install flags were implemented in the
    2026_07_09_0005 and 2026_07_09_0012 checkpoints:
    `--custom-harness name=flavor:binary:config_dir[:display]` accepts the
    tested flavors `gemini`, `qwen`, `agy`, `antigravity`, and `codex`.
    `agy` and `antigravity` normalize to the validated `antigravity` hook
    identity; `codex` installs scoped user hooks and `AGENTS.md` into the
    supplied config directory while skipping global Codex assets. Later
    checkpoints added idempotent reinstall, dry-run, and
    `--status --custom-harness SPEC` coverage; any future rollback work should
    stay in the existing installer path, not in a second hook schema or
    installer registry.
  - Native Antigravity plugin install support now depends on a staged
    `plugin.json` + root `hooks.json` bundle validated by `agy plugin validate`;
    importer fallback remains tested for validation/install failure cases.
- Stage 8 validation status:
  - Focused Antigravity/Qwen/Codex hook identity tests passed:
    `15 passed`.
  - Broader non-live install pathway suite passed with known worktree-basename
    assumptions deselected: `72 passed, 2 deselected`.
  - Broader non-live hook-entry suite passed with known live-environment checks
    deselected: `79 passed, 11 deselected`.
  - `ruff check --ignore E402` passed for the touched installer, hook entry,
    template hook entry, and tests.
  - Full `test_install_pathways.py` currently has two pre-existing worktree-name
    assumptions: tests expect repository root basename `autorun`, but this work
    is intentionally running in `autorun-hardening-single-pass-20260709`.
  - Full `test_hook_entry.py` currently has known live-environment failures:
    UV compatibility tests attempt an x86_64 `cryptography==49.0.0` build
    without OpenSSL/pkg-config, and a live-cache sync test detects that the
    installed Claude cache has not been updated from this worktree. Do not fix
    those by installing into live sessions from this worktree unless the user
    explicitly requests it in the current turn.

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

Stage 1 stale-task/task-ignore checkpoint, 2026_07_08_2230:

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

Stage 2 daemon ownership/restart checkpoint, 2026_07_08_2245:

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

Stage 3 hook timeout/schema checkpoint, 2026_07_08_2302:

- Source-of-truth pass confirmed hook response schemas still belong in
  `core.py::HOOK_SCHEMAS`, `core.py::validate_hook_response`, and
  `platforms.py` response-capability metadata. No second schema table was added.
- Timeout ownership is now consolidated in `CONFIG`:
  `daemon_dispatch_timeouts_seconds`,
  `daemon_client_response_timeouts_seconds`, and
  `hook_wrapper_timeouts_seconds`.
- The root timeout bug was a layer inversion: Gemini/Qwen client waits were
  `2.5s` while daemon permission dispatch could run for `3.0s`. The client
  could therefore time out before the daemon reached its own controlled
  platform-correct response path.
- The fixed layering is explicitly tested:
  `daemon dispatch < client response wait < hook wrapper wait < outer harness
  hooks.json timeout`. This keeps hooks fast while avoiding premature
  fail-closed responses that break active sessions.
- `client.py::daemon_response_timeout_for_cli` now reads `CONFIG` instead of a
  private table. `hooks/hook_entry.py::hook_timeout_for_cli` reads `CONFIG` when
  the package import works, with a documented bootstrap-only fallback for broken
  installs where `autorun.config` cannot be imported yet.
- `autorun --cli` choices now derive from `platforms.py::hook_platforms()` so
  help text and argument validation do not drift from the registry.
- Canonical `hooks/hook_entry.py` and
  `src/autorun/gemini_template/hooks/hook_entry.py` were re-synced byte-for-byte.
- Dependency setup caveat: full workspace sync currently pulls unrelated native
  `cryptography` builds through `pdf-extractor`/`claude-agent-sdk` and fails on
  this machine's cross-arch OpenSSL setup. Focused tests were run in an isolated
  `uv` environment with source `PYTHONPATH` and lightweight test dependencies.
- Validation:

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with psutil --with filelock \
  --with pytest-asyncio --with pytest-mock pytest \
  plugins/autorun/tests/test_client_fail_closed.py \
  plugins/autorun/tests/test_hook_entry.py::TestHookEntryExecutionPriority::test_hook_timeout_is_platform_specific \
  plugins/autorun/tests/test_dual_cli_pathways.py::TestGeminiPathway::test_template_hook_entry_matches_canonical \
  plugins/autorun/tests/test_dual_cli_pathways.py::TestSharedContract::test_hook_entry_is_single_source_of_truth \
  plugins/autorun/tests/test_dual_platform_hooks_install.py::TestClaudeHooksJson::test_timeout_is_seconds \
  plugins/autorun/tests/test_dual_platform_hooks_install.py::TestGeminiHooksJson::test_timeout_is_milliseconds -q
```

Result: `14 passed`.

```bash
uv run ruff check \
  plugins/autorun/src/autorun/config.py \
  plugins/autorun/src/autorun/client.py \
  plugins/autorun/src/autorun/__main__.py \
  plugins/autorun/hooks/hook_entry.py \
  plugins/autorun/src/autorun/gemini_template/hooks/hook_entry.py \
  plugins/autorun/tests/test_client_fail_closed.py \
  plugins/autorun/tests/test_hook_entry.py
```

Result: passed.

Stage 14 final validation closure, 2026_07_09_020436_EDT:

- Latest source-focused validation result for this worktree:
  `3836 passed, 74 skipped, 3 deselected`.
- The 3 deselected checks are the live deployed-copy sync assertions for
  Claude cache and Gemini extension hook-entry copies. They are intentionally
  not fixed by mutating live install paths from this isolated worktree.
- The full default suite still reports those live installed-copy mismatches
  until autorun is deliberately installed from the intended release tree.

Stage 14 diff review checkpoint, 2026_07_09_020436_EDT:

- Reviewed the branch diff against `main` at merge-base
  `fb3e901ab8a8e928c389ad7f39a966884f79cdd5`, focusing on harness API
  boundaries: installer argument parsing, Codex/Gemini-family hook identity,
  daemon restart ownership, hook timeout layering, session-state isolation, and
  stale-task marker handling.
- Fixed a custom harness parser edge case: `name=flavor:binary:config_dir`
  now preserves literal `:` characters inside `config_dir` instead of treating
  them as an optional display suffix. `::display` is documented and tested as
  the unambiguous display separator when a display name is needed.
- Fixed a custom Codex install diagnostic: malformed custom `hooks.json` files
  now report the actual custom config path instead of hard-coding
  `~/.codex/hooks.json`.
- Fixed scoped daemon restart ownership: lock-file PID reads now obey the same
  source-tree filter as psutil fallback discovery, and a source-scoped restart
  refuses to clean up or replace a responding daemon socket when no daemon from
  the current source tree owns the lock. The risky broad cleanup remains
  explicit as `--restart-all-daemons`.
- Fixed an accidental default live test: `test_gemini_session_start_hook_fires`
  invokes the real `gemini` CLI, so it is now gated behind
  `AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1` like the neighboring Gemini E2E
  tests.
- `git diff --check` on the working tree: passed.
- `ruff check --ignore E402` on the touched source/test files: passed.
- Targeted regression validation:

```bash
UV_PROJECT_ENVIRONMENT=.venv-arm64 \
uv run --project plugins/autorun \
  --python /opt/homebrew/opt/python@3.12/bin/python3.12 \
  --with pytest --with pytest-timeout --with pytest-asyncio --with pytest-mock pytest \
  plugins/autorun/tests/test_gemini_before_tool_hooks.py::test_gemini_session_start_hook_fires \
  plugins/autorun/tests/test_daemon_restart_safety.py \
  plugins/autorun/tests/test_install_pathways.py::TestCustomHarnessInstall \
  plugins/autorun/tests/test_install_pathways.py::TestAntigravityImportSync \
  plugins/autorun/tests/test_client_fail_closed.py \
  plugins/autorun/tests/test_hook_entry.py::TestHookEntryExecutionPriority \
  plugins/autorun/tests/test_bootstrap_config.py::TestCLIArgumentParsing \
  plugins/autorun/tests/test_bootstrap_config.py::TestMainFunctionRouting -q
```

Result: `95 passed, 1 skipped`.

- Full default suite after the parser/restart review fixes:

```bash
UV_PROJECT_ENVIRONMENT=.venv-arm64 \
uv run --project plugins/autorun \
  --python /opt/homebrew/opt/python@3.12/bin/python3.12 \
  --with pytest --with pytest-timeout --with pytest-asyncio --with pytest-mock pytest \
  plugins/autorun/tests -q
```

Result before gating the accidental live Gemini prompt test:
`3836 passed, 73 skipped, 4 failed`.

The failures were:

- live installed-copy mismatch:
  `test_claude_cache_hook_entry_matches_source`
- live installed-copy mismatch:
  `test_gemini_extension_hook_entry_matches_source`
- live installed-copy mismatch:
  `test_cache_matches_source_hook_entry`
- accidental ungated live Gemini invocation:
  `test_gemini_session_start_hook_fires`

The first three failures remain release-install checks, not source-worktree
failures: the live files in `~/.claude/plugins/cache/autorun/ar/0.12.0` and
`~/.gemini/extensions/ar` are older than this branch. They should be cleared by
a deliberate install from the intended release tree, not by silently mutating
live hook locations from this isolated worktree while other sessions may be
running.

- Source-focused full suite after the live Gemini test gate:

```bash
UV_PROJECT_ENVIRONMENT=.venv-arm64 \
uv run --project plugins/autorun \
  --python /opt/homebrew/opt/python@3.12/bin/python3.12 \
  --with pytest --with pytest-timeout --with pytest-asyncio --with pytest-mock pytest \
  plugins/autorun/tests -q \
  -k 'not test_claude_cache_hook_entry_matches_source and not test_gemini_extension_hook_entry_matches_source and not test_cache_matches_source_hook_entry'
```

Result: `3836 passed, 74 skipped, 3 deselected`.

Stage 12 native Antigravity checkpoint, 2026_07_09_0101:

- Closed the earlier native Antigravity deferral without forking Gemini-family
  installer logic. The installer now stages an Antigravity-native bundle in a
  temporary directory from the existing Gemini-family resources.
- `_stage_antigravity_native_bundle(plugin_dir, bundle_dir)` reuses
  `_sync_gemini_extension_resources(..., cli_name="antigravity")`, then writes
  root `hooks.json` and `plugin.json` with `hooks`, `commands`, and `skills`
  fields. Root `hooks.json` is required because `agy plugin validate` skips
  hooks when only nested `hooks/hooks.json` exists.
- `_install_for_antigravity()` now prefers `agy plugin validate <bundle>` plus
  `agy plugin install <bundle>` when validation reports hooks processed. It
  falls back to `agy plugin import gemini` and the existing post-import sync
  when native validation/install is unavailable.
- User-facing installer/README/skill wording was updated from importer-only to
  native-bundle-with-importer-fallback. This preserves current live workflows
  while enabling native CLI support where Antigravity accepts it.
- Local non-mutating Antigravity validation:

```bash
tmpdir=$(mktemp -d /private/tmp/autorun-agy-native.XXXXXX)
PYTHONPATH=plugins/autorun/src uv run python -c \
  'from pathlib import Path; from autorun.install import _stage_antigravity_native_bundle; _stage_antigravity_native_bundle(Path("plugins/autorun"), Path("'$tmpdir'") / "ar")'
agy plugin validate "$tmpdir/ar"
```

Result: `agy plugin validate` returned 0 and reported `skills: 9 processed`,
`commands: 82 processed (converted to skills)`, and `hooks: 2 processed`.

- Test validation:

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with filelock --with psutil \
  --with pytest-asyncio --with pytest-mock pytest \
  plugins/autorun/tests/test_install_pathways.py::TestAntigravityImportSync \
  plugins/autorun/tests/test_install_pathways.py::TestInstallMainAdapter \
  plugins/autorun/tests/test_bootstrap_config.py::TestCLIArgumentParsing \
  plugins/autorun/tests/test_bootstrap_config.py::TestMainFunctionRouting \
  plugins/autorun/tests/test_skill_docs.py -q
```

Result: `45 passed`.

```bash
uv run ruff check --ignore E402 \
  plugins/autorun/src/autorun/install.py \
  plugins/autorun/src/autorun/__main__.py \
  plugins/autorun/tests/test_install_pathways.py \
  plugins/autorun/tests/test_bootstrap_config.py \
  plugins/autorun/tests/test_skill_docs.py
```

Result: passed.

Stage 11 focused regression checkpoint, 2026_07_09_0045:

- Reconciled stale plan language so completed custom-harness status/dry-run and
  capability snapshot work is not still described as pending.
- Ran a combined focused regression suite covering Codex install/status, custom
  harness install/status/help, bootstrap parser routing, skill documentation,
  and release-facing Gemini doc version consistency.
- Validation:

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with filelock --with psutil \
  --with pytest-asyncio --with pytest-mock pytest \
  plugins/autorun/tests/test_codex_install.py \
  plugins/autorun/tests/test_install_pathways.py::TestInstallPathwayRouting \
  plugins/autorun/tests/test_install_pathways.py::TestInstallMainAdapter \
  plugins/autorun/tests/test_install_pathways.py::TestCustomHarnessInstall \
  plugins/autorun/tests/test_bootstrap_config.py::TestCLIArgumentParsing \
  plugins/autorun/tests/test_bootstrap_config.py::TestMainFunctionRouting \
  plugins/autorun/tests/test_skill_docs.py \
  plugins/autorun/tests/test_dual_platform_hooks_install.py::TestManifestFiles::test_version_consistency -q
```

Result: `100 passed`.

```bash
uv run ruff check --ignore E402 \
  plugins/autorun/src/autorun/platforms.py \
  plugins/autorun/src/autorun/install.py \
  plugins/autorun/src/autorun/__main__.py \
  plugins/autorun/tests/test_install_pathways.py \
  plugins/autorun/tests/test_bootstrap_config.py \
  plugins/autorun/tests/test_skill_docs.py \
  plugins/autorun/tests/test_dual_platform_hooks_install.py
```

Result: passed.

Stage 10 Gemini doc version checkpoint, 2026_07_09_0043:

- The existing release version consistency test covered README, manifests,
  package metadata, and selected skill versions, but not `GEMINI.md`.
- Found stale Gemini install verification examples still saying `ar@0.11.0`
  and `pdf-extractor@0.11.0` while the release metadata is `0.12.0`.
- Extended `TestManifestFiles.test_version_consistency` to require
  `GEMINI.md` to contain `ar@<current version>` and to reject stale `0.11.0`
  text. This keeps Gemini-facing docs tied to the same version source checked
  for README and manifests.
- Updated the four stale `GEMINI.md` verification examples to `0.12.0`.
- Validation:

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with filelock --with psutil \
  --with pytest-asyncio --with pytest-mock pytest \
  plugins/autorun/tests/test_dual_platform_hooks_install.py::TestManifestFiles::test_version_consistency -q
```

Result: `1 passed`.

```bash
uv run ruff check plugins/autorun/tests/test_dual_platform_hooks_install.py
```

Result: passed.

Stage 9 skill audit checkpoint, 2026_07_09_0040:

- Audited `plugins/autorun/skills/autorun-maintainer/SKILL.md` against the
  hardening plan lessons. The skill source was stale in ways that could cause a
  future maintainer to break active sessions: it covered only Claude/Gemini,
  used version-specific plugin cache examples, and recommended broad daemon
  process cleanup as routine recovery.
- Updated the shippable repo skill source to cover Claude Code, Codex CLI,
  Gemini-family CLIs, Google Antigravity, Qwen Code, ForgeCode, custom
  harnesses, and desktop app integrations.
- Added maintainer guidance for `autorun --install-dry-run`,
  `autorun --status --custom-harness SPEC`, scoped `autorun --restart-daemon`,
  and `autorun --restart-all-daemons` only with explicit current-turn user
  approval.
- Replaced hard-coded `0.11.0` plugin cache paths with `<version>` examples and
  removed broad `pkill -f` advice from the skill. The repair matrix now routes
  ordinary hangs through scoped restart first.
- Added a regression test so the maintainer skill must keep current
  multi-harness coverage, custom harness status/install guidance, and scoped
  restart safety wording.
- Validation:

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with filelock --with psutil pytest \
  plugins/autorun/tests/test_skill_docs.py -q
```

Result: `3 passed`.

```bash
uv run ruff check plugins/autorun/tests/test_skill_docs.py
```

Result: passed.

Stage 2 command runtime/alias checkpoint, 2026_07_08_2357:

- Source-of-truth pass confirmed command execution still belongs in
  `core.py::AutorunApp.command()` and handler registration in `plugins.py`.
  Markdown command metadata belongs in `command_docs.py`, and
  `capability_snapshot.py` remains a derived read-only diagnostic.
- The current implementation already supports the DRY path needed for
  Claude-style slash commands and Codex-native prompt forms:
  `/ar:*` aliases register once with `AutorunApp.command`, and Codex dispatch
  accepts both `ar:<command>` and `ar <command>` through the platform prefix
  registry instead of a second dispatcher.
- `task-ignore` is intentionally not implemented as inline markdown Python.
  `/ar:task-ignore`, `/task-ignore`, `ar:task-ignore`, and `ar task-ignore`
  route to the same `handle_task_ignore` runtime handler, preserving user-only
  override semantics without letting the AI silently discard real unfinished
  work.
- `/ar:ok` and Codex `ar:ok` keep using the existing shared
  `_parse_allow_args` and `scoped_allow.parse_scope_args` grammar, including
  unquoted multiword patterns. No new scope parser or second allow/block syntax
  was added.
- The focused test pass below covers command-doc parity, command alias
  ownership, Codex plain aliases, task-ignore routing, and the high-risk
  unquoted multiword allow pattern. No code change was required for this
  checkpoint.
- Validation:

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with filelock --with psutil pytest \
  plugins/autorun/tests/test_capability_snapshot.py \
  plugins/autorun/tests/test_codex_response_schema.py::test_codex_plain_ar_alias_dispatches_without_leading_slash \
  plugins/autorun/tests/test_codex_response_schema.py::test_every_slash_ar_command_also_dispatches_with_codex_plain_prefix \
  plugins/autorun/tests/test_codex_response_schema.py::test_codex_accepts_colon_and_space_plain_ar_command_spelling \
  plugins/autorun/tests/test_codex_response_schema.py::test_codex_plain_ar_allow_alias_unblocks_same_session_command \
  plugins/autorun/tests/test_codex_response_schema.py::test_codex_plain_ar_allow_supports_unquoted_multiword_patterns \
  plugins/autorun/tests/test_codex_response_schema.py::test_task_ignore_command_marks_task_ignored_across_native_and_plain_aliases \
  plugins/autorun/tests/test_codex_response_schema.py::test_codex_suggestions_use_plain_ar_aliases_not_rejected_slash_commands \
  plugins/autorun/tests/test_core_configuration.py::test_new_ar_command_mappings \
  plugins/autorun/tests/test_core_configuration.py::test_command_handlers \
  plugins/autorun/tests/test_core_configuration.py::test_handler_variations_available \
  plugins/autorun/tests/test_ghost_clear.py::test_task_ignore_aliases_route_to_one_handler \
  plugins/autorun/tests/test_ghost_clear.py::test_plain_task_ignore_alias_uses_task_lifecycle_state -q
```

Result: `58 passed`.

Stage 8 hook integration/custom harness checkpoint, 2026_07_09_0005:

- External documentation refresh:
  - Codex hooks documentation confirms Codex loads hooks in additive source
    layers and supports user/project/session/plugin/managed hook sources. It
    also confirms `PreToolUse` deny output uses
    `hookSpecificOutput.permissionDecision = "deny"` and that unsupported
    fields such as `continue`, `stopReason`, and `suppressOutput` on
    `PreToolUse` cause Codex hook-run failures:
    https://developers.openai.com/codex/hooks.
  - Claude Code hooks documentation confirms `PreToolUse`, `PostToolUse`,
    `UserPromptSubmit`, `Stop`, and plugin/agent frontmatter hook formats, and
    confirms direct slash command expansion has a separate hook path from tool
    calls:
    https://docs.anthropic.com/en/docs/claude-code/hooks.
  - Gemini CLI hooks reference confirms Gemini-family event names such as
    `SessionStart`, `SessionEnd`, `BeforeModel`, `AfterModel`, and
    `PreCompress`, and confirms Gemini hook outputs have different event-level
    flow-control support from Claude/Codex:
    https://geminicli.com/docs/hooks/reference/.
  - Qwen Code public repository advertises hooks, skills, subagents, and
    multi-provider support; local CLI probing remains the more authoritative
    source for exact extension subcommands:
    https://github.com/QwenLM/qwen-code.
  - Public Antigravity 2.0 reporting is not primary API documentation, but it
    corroborates that Hooks, Agent Skills, Subagents, and Extensions are carried
    forward as Antigravity plugins while Gemini CLI consumer support was being
    retired:
    https://www.techradar.com/pro/google-is-making-gemini-cli-users-switch-to-its-new-antigravity-2-0-so-what-will-it-mean-for-you.
- Local source-of-truth pass confirmed the current shared anchors are
  `platforms.py::Platform`, `install.py::_install_gemini_family_extensions`,
  `_sync_gemini_extension_resources`, `_set_gemini_family_hook_cli`, and the
  existing Codex/Claude dedicated installer paths. The fix does not add a
  second platform registry or second hook schema table.
- The concrete gap addressed here is custom Gemini-family install locations.
  `autorun --install --custom-harness name=flavor:binary:config_dir[:display]`
  now accepts repeatable custom targets for known Gemini-family hook flavors
  (`gemini`, `qwen`). The custom binary is used only to run extension commands;
  installed hooks are stamped with the known `hook_entry.py --cli <flavor>`
  value, preventing arbitrary custom strings from entering hook schema
  selection.
- `_install_gemini_family_extensions()` now separates executable name
  (`cli_name`) from hook identity (`hook_cli_name`). Existing Gemini and Qwen
  callers keep the default where both values are the same. Custom harnesses can
  run a custom binary while reusing the vetted Gemini/Qwen response schema and
  event mapping.
- Scope deliberately not claimed by this checkpoint: arbitrary custom Claude or
  Codex config directories. Those installers still have dedicated path and
  source-composition rules (`~/.claude` plugin cache, `~/.codex/hooks.json`,
  Codex plugin marketplace and hook-source markers). Supporting custom strict
  harness directories should be a separate TDD slice with tests for preserving
  third-party hooks and user/plugin source transitions.
- Validation:

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with filelock --with psutil pytest \
  plugins/autorun/tests/test_install_pathways.py::TestInstallMainAdapter \
  plugins/autorun/tests/test_install_pathways.py::TestCustomHarnessInstall \
  plugins/autorun/tests/test_bootstrap_config.py::TestMainFunctionRouting::test_install_with_custom_harness_passes_custom_specs \
  plugins/autorun/tests/test_bootstrap_config.py::TestMainFunctionRouting::test_install_with_codex_hook_source_passes_plugin_mode \
  plugins/autorun/tests/test_bootstrap_config.py::TestMainFunctionRouting::test_install_with_qwen_passes_qwen_only_flag -q
```

Result: `15 passed`.

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with filelock --with psutil pytest \
  plugins/autorun/tests/test_platform_registry.py \
  plugins/autorun/tests/test_install_pathways.py::TestInstallPathwayRouting \
  plugins/autorun/tests/test_install_pathways.py::TestInstallMainAdapter \
  plugins/autorun/tests/test_install_pathways.py::TestGenerateGeminiTomlCommands \
  plugins/autorun/tests/test_install_pathways.py::TestAntigravityImportSync \
  plugins/autorun/tests/test_install_pathways.py::TestCustomHarnessInstall \
  plugins/autorun/tests/test_dual_platform_hooks_install.py::TestGeminiHooksJson \
  plugins/autorun/tests/test_dual_platform_hooks_install.py::TestClaudeHooksJson -q
```

Result: `74 passed`.

```bash
uv run ruff check --ignore E402 \
  plugins/autorun/src/autorun/install.py \
  plugins/autorun/src/autorun/__main__.py \
  plugins/autorun/tests/test_install_pathways.py \
  plugins/autorun/tests/test_bootstrap_config.py
```

Result: passed. `E402` remains intentionally ignored for `install.py` because
that module has an executable Python-version guard before normal imports.

Stage 8 custom harness correction, 2026_07_09_0012:

- User review caught a missing Antigravity custom flavor: `agy` is the actual
  CLI binary spelling and must be accepted as a custom harness flavor alias.
- The parser now accepts `agy` and `antigravity`, normalizing both to the
  validated hook identity `antigravity`. This keeps hook commands stamped as
  `hook_entry.py --cli antigravity`, matching the existing hook wrapper and
  schema tests, while still letting custom targets run binaries such as
  `agy-lab`.
- The custom harness flavor set now covers `gemini`, `qwen`, `agy`,
  `antigravity`, and `codex`. Unknown values are still rejected before install
  so custom specs cannot create arbitrary hook schemas.
- Scoped Codex custom config-dir support was added in the same TDD slice:
  `codex` custom harnesses install user-level hooks and `AGENTS.md` into the
  supplied config directory while intentionally skipping global `~/.agents`
  skills and Codex plugin marketplace writes.
- Validation:

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with filelock --with psutil pytest \
  plugins/autorun/tests/test_install_pathways.py::TestCustomHarnessInstall -q
```

Result: `7 passed`.

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with filelock --with psutil pytest \
  plugins/autorun/tests/test_hook_entry.py::TestHookEntryExecutionPriority::test_antigravity_is_accepted_cli_type \
  plugins/autorun/tests/test_hook_entry.py::TestHookEntryExecutionPriority::test_antigravity_tool_gate_fail_closed_uses_permissive_schema \
  plugins/autorun/tests/test_platform_registry.py \
  plugins/autorun/tests/test_install_pathways.py::TestInstallMainAdapter \
  plugins/autorun/tests/test_install_pathways.py::TestCustomHarnessInstall \
  plugins/autorun/tests/test_bootstrap_config.py::TestMainFunctionRouting -q
```

Result: `52 passed`.

```bash
uv run ruff check --ignore E402 \
  plugins/autorun/src/autorun/install.py \
  plugins/autorun/src/autorun/__main__.py \
  plugins/autorun/tests/test_install_pathways.py \
  plugins/autorun/tests/test_bootstrap_config.py
```

Result: passed.

Stage 8 custom harness status/help checkpoint, 2026_07_09_0034:

- User review identified CLI bloat in the first implementation of custom
  harness status: a separate `--custom-harness-status` flag duplicated the
  existing `--status` operation. The public CLI was corrected so status remains
  one operation: `autorun --status --custom-harness SPEC`.
- `--custom-harness` is now a target-spec option shared by install and status
  paths. This preserves the compact action/target grammar:
  `--install --custom-harness SPEC` installs a scoped target, while
  `--status --custom-harness SPEC` inspects that scoped target.
- The custom harness grammar and aliases moved into the platform source of
  truth via `CUSTOM_HARNESS_FLAVOR_ALIASES`, `CUSTOM_HARNESS_SPEC_FORMAT`, and
  `custom_harness_spec_help()`. Both CLI parsers use that helper, so accepted
  values and help text cannot drift between `autorun` and
  `python -m ...install`.
- Help text now lists the working values directly:
  `flavor: gemini|qwen|antigravity|agy|codex`, says `agy` is an alias for
  `antigravity`, explains `binary` and `config_dir`, and explicitly documents
  both `--install --custom-harness` and `--status --custom-harness` usage.
- `show_status(custom_harnesses=...)` now aggregates custom harness checks into
  the normal multi-harness status report. Missing Claude CLI no longer prevents
  Codex, Antigravity, Qwen, ForgeCode, or custom harness status from being
  displayed; it marks the aggregate result nonzero and continues.
- Validation:

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with filelock --with psutil pytest \
  plugins/autorun/tests/test_bootstrap_config.py::TestCLIArgumentParsing::test_custom_harness_help_lists_values_and_usage \
  plugins/autorun/tests/test_install_pathways.py::TestInstallMainAdapter::test_install_module_custom_harness_help_lists_values_and_usage \
  plugins/autorun/tests/test_install_pathways.py::TestInstallMainAdapter::test_install_module_main_status_with_custom_harness_routes_to_show_status \
  plugins/autorun/tests/test_bootstrap_config.py::TestMainFunctionRouting::test_status_with_custom_harness_routes_to_show_status -q
```

Result: `4 passed`.

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with filelock --with psutil \
  --with pytest-asyncio --with pytest-mock pytest \
  plugins/autorun/tests/test_codex_install.py \
  plugins/autorun/tests/test_install_pathways.py::TestInstallPathwayRouting \
  plugins/autorun/tests/test_install_pathways.py::TestInstallMainAdapter \
  plugins/autorun/tests/test_install_pathways.py::TestCustomHarnessInstall \
  plugins/autorun/tests/test_bootstrap_config.py::TestCLIArgumentParsing \
  plugins/autorun/tests/test_bootstrap_config.py::TestMainFunctionRouting -q
```

Result: `96 passed`.

```bash
uv run ruff check --ignore E402 \
  plugins/autorun/src/autorun/platforms.py \
  plugins/autorun/src/autorun/install.py \
  plugins/autorun/src/autorun/__main__.py \
  plugins/autorun/tests/test_install_pathways.py \
  plugins/autorun/tests/test_bootstrap_config.py
```

Result: passed.

Stage 8 custom harness idempotence checkpoint, 2026_07_09_0016:

- Added custom harness regression coverage for upgrade/reinstall safety.
- Scoped Codex config-dir installs are now tested for repeatability: running
  `_install_for_codex(..., codex_dir=<custom>, install_global_assets=False)`
  twice does not duplicate autorun hook entries within any event and does not
  duplicate the autorun-owned `AGENTS.md` guidance block.
- Top-level `install_plugins(..., custom_harnesses=[...])` now has a regression
  test proving `flavor=codex` routes to `_install_for_codex` with
  `install_global_assets=False`, `codex_hook_source="user"`, and the supplied
  config directory. This prevents custom Codex-like harness installs from
  mutating global `~/.agents` skills or Codex plugin marketplace state.
- The original global Codex installer behavior remains covered by
  `test_codex_install.py`; the new custom path is an opt-in scoped install
  surface rather than a replacement for normal Codex CLI setup.
- Validation:

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with filelock --with psutil pytest \
  plugins/autorun/tests/test_install_pathways.py::TestCustomHarnessInstall -q
```

Result: `9 passed`.

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with filelock --with psutil \
  --with pytest-asyncio --with pytest-mock pytest \
  plugins/autorun/tests/test_codex_install.py \
  plugins/autorun/tests/test_install_pathways.py::TestCustomHarnessInstall \
  plugins/autorun/tests/test_install_pathways.py::TestInstallMainAdapter \
  plugins/autorun/tests/test_bootstrap_config.py::TestMainFunctionRouting -q
```

Result: `73 passed`.

```bash
uv run ruff check --ignore E402 \
  plugins/autorun/src/autorun/install.py \
  plugins/autorun/src/autorun/__main__.py \
  plugins/autorun/tests/test_install_pathways.py \
  plugins/autorun/tests/test_bootstrap_config.py
```

Result: passed.

Stage 8 install dry-run checkpoint, 2026_07_09_0019:

- Added install-specific dry-run support via `--install-dry-run` for both
  `autorun --install` and the direct install-module parser. This avoids
  overloading the existing task GC `--dry-run` semantics.
- `install_plugins(..., dry_run=True)` now resolves the marketplace, parses the
  selected plugins, detects available platform targets, parses custom harness
  specs, and prints the intended target matrix. It then returns before package
  metadata writes, dependency sync, hook/plugin installs, hook conflict scans,
  UV tool installs, and daemon restarts.
- The dry-run output explicitly says no files, hooks, plugin state,
  dependencies, or daemons were changed. Custom harness entries show name,
  normalized flavor, binary, config directory, and display name, so users can
  verify `agy -> antigravity` normalization and scoped Codex paths before a
  real install.
- Validation:

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with filelock --with psutil pytest \
  plugins/autorun/tests/test_install_pathways.py::TestInstallMainAdapter::test_install_module_main_install_dry_run_routes_to_install_plugins \
  plugins/autorun/tests/test_install_pathways.py::TestCustomHarnessInstall::test_install_plugins_dry_run_does_not_write_or_install \
  plugins/autorun/tests/test_bootstrap_config.py::TestMainFunctionRouting::test_install_dry_run_passes_dry_run_flag -q
```

Result: `3 passed`.

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with filelock --with psutil \
  --with pytest-asyncio --with pytest-mock pytest \
  plugins/autorun/tests/test_codex_install.py \
  plugins/autorun/tests/test_install_pathways.py::TestInstallMainAdapter \
  plugins/autorun/tests/test_install_pathways.py::TestCustomHarnessInstall \
  plugins/autorun/tests/test_bootstrap_config.py::TestMainFunctionRouting -q
```

Result: `76 passed`.

```bash
uv run ruff check --ignore E402 \
  plugins/autorun/src/autorun/install.py \
  plugins/autorun/src/autorun/__main__.py \
  plugins/autorun/tests/test_install_pathways.py \
  plugins/autorun/tests/test_bootstrap_config.py
```

Result: passed.

Stage 7 skill-doc safety checkpoint, 2026_07_08_2315:

- Added a focused skill-doc regression test for two safety properties:
  user-invocable skills must not advertise an `/ar:*` slash command as the skill
  activation path, and `SKILL.md` entrypoints must not embed Claude-only
  executable markdown snippets.
- Updated `ai-session-tools` and the `claude-session-tools` alias skill to use
  skill-native invocation wording (`$ai-session-tools`, harness skill picker, or
  natural language) instead of the nonexistent `/ar:ai-session-tools` runtime
  command.
- Updated the cache skill with a harness note separating skill activation from
  the runtime `/ar:cache` command. The note documents Codex-safe prompt forms:
  `ar:cache` and `ar cache ...`, matching the existing Codex platform prefix
  registry.
- This keeps slash commands and skills distinct: slash commands remain runtime
  hooks where a harness supports them, while skills remain guidance entrypoints
  that do not implicitly run side-effecting code.
- Validation:

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with psutil --with filelock pytest \
  plugins/autorun/tests/test_skill_docs.py -q
```

Result: `2 passed`.

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with filelock --with psutil pytest \
  plugins/autorun/tests/test_capability_snapshot.py \
  plugins/autorun/tests/test_install_pathways.py::TestGenerateGeminiTomlCommands -q
```

Result: `12 passed`.

```bash
uv run ruff check --ignore E402 \
  plugins/autorun/src/autorun/command_docs.py \
  plugins/autorun/src/autorun/capability_snapshot.py \
  plugins/autorun/src/autorun/install.py \
  plugins/autorun/tests/test_capability_snapshot.py \
  plugins/autorun/tests/test_skill_docs.py
```

Result: passed.

Stage 7 command-doc DRY checkpoint, 2026_07_08_2321:

- Existing source-of-truth pass confirmed runtime command dispatch already lives
  in `core.py::AutorunApp.command()` and registrations in `plugins.py`; the
  capability snapshot already inventories `app.command_handlers`.
- The markdown command docs had a second lightweight frontmatter parser inside
  `_generate_gemini_toml_commands()`. That parser now lives in
  `command_docs.py` and is reused by both Gemini TOML generation and
  `capability_snapshot.py`.
- `build_capability_snapshot()` now includes `command_docs`, a read-only
  metadata inventory keyed by command filename stem. It exposes description,
  aliases, executable-snippet presence, file name, and frontmatter name without
  executing any command body.
- The first parity test checks that every registered runtime `/ar:*` alias has a
  matching command markdown file. This guards against adding a handler without
  user-facing docs, while intentionally not requiring docs-only commands to
  become runtime handlers.
- The snapshot test also locks in high-risk command metadata for
  `restart-daemon` and executable alias metadata for `task-ignore`, creating a
  reusable basis for future skill generation without copying command tables.
- Validation:

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with filelock --with psutil pytest \
  plugins/autorun/tests/test_capability_snapshot.py \
  plugins/autorun/tests/test_install_pathways.py::TestGenerateGeminiTomlCommands -q
```

Result: `12 passed`.

```bash
uv run ruff check --ignore E402 \
  plugins/autorun/src/autorun/command_docs.py \
  plugins/autorun/src/autorun/capability_snapshot.py \
  plugins/autorun/src/autorun/install.py \
  plugins/autorun/tests/test_capability_snapshot.py
```

Result: passed.

Stage 6 help/docs clarity checkpoint, 2026_07_08_2331:

- The normal restart command is now documented as scoped to the current autorun
  install/source tree in the package CLI help, slash-command help, README, and
  Claude-facing command table.
- The broad recovery command remains named `autorun --restart-all-daemons` and
  is explicitly described as risky because it can interrupt active
  autorun-backed sessions in other installs. This is not documented as a routine
  reload path.
- `restart_daemon.py` no longer has stale `scripts.restart_daemon` import/usage
  examples. The module comment now points package callers at
  `autorun.restart_daemon.restart_daemon(all_daemons=False)`.
- A parser-help regression assertion verifies that both restart flags and the
  risk wording remain present even when argparse wraps the help text.
- Validation:

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with filelock --with psutil pytest \
  plugins/autorun/tests/test_daemon_restart_safety.py -q
```

Result: `30 passed`.

```bash
uv run ruff check --ignore E402 \
  plugins/autorun/src/autorun/restart_daemon.py \
  plugins/autorun/tests/test_daemon_restart_safety.py
```

Result: passed.

Stage 6 daemon handoff/restart checkpoint, 2026_07_08_2338:

- Source-of-truth pass confirmed restart ownership belongs in
  `restart_daemon.py`, `ipc.py`, and the existing daemon restart safety tests.
- A remaining root cause existed after the earlier source-tree kill scoping:
  `get_daemon_pid()` fallback discovery was still broad when `daemon.lock` was
  missing but `daemon.flock` existed. Because `restart_daemon()` called it before
  resolving the current source tree, a normal restart could still stop an
  unrelated live/worktree daemon discovered by command line.
- The fix resolves `src_dir` before PID discovery in `restart_daemon()` and
  passes it into `get_daemon_pid(src_dir=...)`. Fallback process scanning now
  ignores daemon processes whose command line does not contain the current source
  directory. Lock-file PID reads remain unchanged because the lock path is
  already scoped by `ipc.AUTORUN_CONFIG_DIR`.
- `get_daemon_pid()` remains backward compatible for callers that do not pass
  `src_dir`; explicit maintenance/status callers can still perform broad
  discovery when that is actually intended.
- Validation:

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with filelock --with psutil pytest \
  plugins/autorun/tests/test_daemon_restart_safety.py -q
```

Result: `30 passed`.

```bash
uv run ruff check \
  plugins/autorun/src/autorun/restart_daemon.py \
  plugins/autorun/tests/test_daemon_restart_safety.py
```

Result: passed.

Stage 4 Codex installer composition checkpoint, 2026_07_08_2355:

- Source-of-truth pass confirmed Codex install ownership already lives in
  `install.py` helpers: `_install_for_codex`, `_merge_codex_hooks`,
  `_install_codex_plugin_marketplace`, `_copy_codex_plugin_source`,
  `_CODEX_PLUGIN_OWNED_MARKER`, and `_codex_plugin_marketplace_status`.
- No `install_specs.py`, new installer registry, or second version reader was
  added. The existing autorun-owned marker now carries one additional line,
  `codex_hook_source=<user|plugin|both|none>`, so status can distinguish
  intentional `both` mode from accidental duplicate hook sources.
- The root issue addressed here was status ambiguity: user hooks plus plugin
  cache hooks were always reported as duplicate/broken, even when the installer
  had explicitly been run with `codex_hook_source="both"`.
- Reinstall transitions remain idempotent and scoped:
  - `both -> user` rewrites only the autorun-owned plugin source and removes
    plugin-bundled hooks.
  - User-owned hooks in `~/.codex/hooks.json` remain preserved by
    `_merge_codex_hooks`.
  - Existing duplicate user+plugin hooks without an explicit `both` marker still
    report as a status failure requiring action.
- Validation:

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with psutil --with filelock \
  --with pytest-asyncio --with pytest-mock pytest \
  plugins/autorun/tests/test_codex_install.py -q
```

Result: `42 passed`.

```bash
uv run ruff check --ignore E402 \
  plugins/autorun/src/autorun/install.py \
  plugins/autorun/tests/test_codex_install.py
```

Result: passed. `E402` is ignored here because `install.py` intentionally keeps
a Python-version guard before imports.

Stage 5 state isolation/performance checkpoint, 2026_07_09_0008:

- Source-of-truth pass confirmed state persistence still belongs in
  `session_manager.py`; task retention and archive behavior remain in
  `TaskLifecycleConfig` and `TaskLifecycle.cli_gc`.
- The concrete bug addressed here was process-local singleton contamination:
  `session_manager._get_store(state_dir)` and `get_session_manager(state_dir)`
  previously used one global store/manager. The first state directory used in a
  process won, so later explicit `state_dir=` calls could silently write to the
  wrong `daemon_state.json`.
- The fix keys store/manager caches by resolved state directory. This preserves
  singleton reuse for the same path while isolating test, worktree, and live
  state directories in the same Python process.
- Legacy `_store` and `_manager` aliases remain as compatibility reset hooks for
  existing tests and cleanup code; `_reset_for_testing()` now clears the keyed
  caches too.
- This change does not add a second backend and does not make hot hook paths call
  `all_session_state`. Maintenance GC still uses the existing bulk
  `all_session_state` path, with dry-run/archive tests preserved.
- Validation:

```bash
PYTHONPATH=plugins/autorun/src uv run --isolated \
  --with pytest --with pytest-timeout --with filelock --with psutil pytest \
  plugins/autorun/tests/test_session_manager.py \
  plugins/autorun/tests/test_stale_lock_recovery.py \
  plugins/autorun/tests/test_task_lifecycle_ghost_task_bug.py::TestGarbageCollection \
  plugins/autorun/tests/test_task_lifecycle_ghost_task_bug.py::TestGCLocking -q
```

Result: `63 passed`.

```bash
uv run ruff check \
  plugins/autorun/src/autorun/session_manager.py \
  plugins/autorun/tests/test_session_manager.py
```

Result: passed.

Stage 13 validation closure checkpoint, 2026_07_09_0136:

- Source-of-truth pass confirmed stale-task marker parsing still belongs in
  `task_lifecycle.py`; no second task-clear pathway was added.
- The broad test run exposed one real source bug after the earlier hardening:
  `extract_stale_clear_task_ids()` assumed all hook context fields were strings.
  Mock, partial, or malformed contexts can carry non-string sentinels, which
  crashed Stop handling after the stale-clear hatch armed. The fix keeps Stop
  fail-closed by parsing only real string text and returning no marker ids for
  non-text fields.
- The first broad validation used a stale x86_64 worktree `.venv` on this
  arm64 host. That caused nested `uv run --project plugins/autorun ...`
  subprocesses to build `cryptography==49.0.0` for `x86_64-apple-darwin` and
  fail before hook or CLI code ran. This was not a source regression; it was an
  invalid local validation environment.
- Validation was rerun with a worktree-local arm64 project environment:

```bash
UV_PROJECT_ENVIRONMENT=.venv-arm64 \
uv run --project plugins/autorun \
  --python /opt/homebrew/opt/python@3.12/bin/python3.12 \
  python -c "import platform, autorun; print(platform.machine()); print('autorun ok')"
```

Result: `arm64`, `autorun ok`.

```bash
UV_PROJECT_ENVIRONMENT=.venv-arm64 \
uv run --project plugins/autorun \
  --python /opt/homebrew/opt/python@3.12/bin/python3.12 \
  --with pytest --with pytest-timeout --with pytest-asyncio --with pytest-mock pytest \
  plugins/autorun/tests/test_hook_entry.py::TestUVCompatibility \
  plugins/autorun/tests/test_task_14_cli_non_interactive.py \
  plugins/autorun/tests/test_task_lifecycle_failure_modes.py::TestFailureModes::test_02_stuck_task_always_blocks_without_user_action \
  plugins/autorun/tests/test_task_lifecycle_integration.py::TestTaskLifecycleIntegration::test_10_stop_always_blocked_with_incomplete_tasks \
  plugins/autorun/tests/test_task_lifecycle_integration.py::TestTaskLifecycleIntegration::test_13_no_stage_reset_when_stage_not_stage2_completed -q
```

Result: `15 passed`.

```bash
UV_PROJECT_ENVIRONMENT=.venv-arm64 \
uv run --project plugins/autorun \
  --python /opt/homebrew/opt/python@3.12/bin/python3.12 \
  --with pytest --with pytest-timeout --with pytest-asyncio --with pytest-mock pytest \
  plugins/autorun/tests -q
```

Result: `3831 passed, 74 skipped, 3 failed`.

The 3 remaining failures are deployed-copy checks only:

- `plugins/autorun/tests/test_dual_platform_hooks_install.py::TestDeployedCopiesMatchSource::test_claude_cache_hook_entry_matches_source`
- `plugins/autorun/tests/test_dual_platform_hooks_install.py::TestDeployedCopiesMatchSource::test_gemini_extension_hook_entry_matches_source`
- `plugins/autorun/tests/test_hook_entry.py::TestAllLocationsSync::test_cache_matches_source_hook_entry`

They report that live files under `~/.claude/plugins/cache/autorun/ar/0.12.0`
and `~/.gemini/extensions/ar` do not match this worktree source. This is a live
installation-state condition, not a source regression. The safe next action is a
deliberate install/restart from the intended release tree, not an implicit
mutation from this worktree while active sessions may still be running.

```bash
uv run ruff check --ignore E402 \
  plugins/autorun/src/autorun/install.py \
  plugins/autorun/src/autorun/__main__.py \
  plugins/autorun/src/autorun/task_lifecycle.py \
  plugins/autorun/tests/test_install_pathways.py \
  plugins/autorun/tests/test_bootstrap_config.py \
  plugins/autorun/tests/test_skill_docs.py \
  plugins/autorun/tests/test_dual_platform_hooks_install.py
```

Result: passed.
