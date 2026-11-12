# autorun plugin development guidance

`CLAUDE.md` and `GEMINI.md` here are symlinks to this file. Edit `AGENTS.md`;
never replace a symlink with a copy. Repository-wide rules — **development
isolation is mandatory, and the live installation is touched only on the
user's written instruction** — are in the root [`AGENTS.md`](../../AGENTS.md)
and apply to everything below.

## Hook error prevention (CRITICAL)

Claude Code treats ANY stderr output from a hook as a hook error and discards
that hook's JSON response, so every protection silently stops working while
the session looks healthy.

1. **`pyproject.toml [tool.uv]`**: never add deprecated UV fields. UV prints a
   stderr warning for an unknown field, which breaks all hooks. `default-extras`
   was removed in UV 0.9+; put default extras in `[project] dependencies`.
2. **Slash commands**: every bash command in a `.md` file uses `uv run
   --project ${CLAUDE_PLUGIN_ROOT} python`, never bare `python3`, and
   `allowed-tools` says `Bash(uv *)`, not `Bash(python3:*)`. This includes
   `!`-prefixed dynamic output.
3. **`hooks/hook_entry.py` writes to stderr only for Claude's exit-2 deny**;
   other errors go through `fail_open()`, which writes JSON to stdout.
4. **Cache sync**: a `pyproject.toml` or `hooks.json` fix reaches a harness only
   through `autorun --install --force` (sandboxed; live only on the user's
   instruction). Hand-copying into `~/.claude/plugins/cache/` is overwritten by
   the next install.
5. **Session restart**: hook configuration is read once at session start, so
   those fixes take effect in the NEXT session.

Regression tests: `test_hook_entry.py::TestUVCompatibility` and `::TestCacheSync`.
Diagnose with `uv run --project <plugin_root> python -c "pass" 2>&1`; any output
beyond Building/Installed lines is the problem.

## Edit the repository, not the plugin cache

Work in `<git-root>/plugins/autorun/`. `~/.claude/plugins/cache/autorun/ar/<version>/`
(marketplace `autorun`, plugin `ar`) is a copy the plugin loads from; edits
there are unversioned and overwritten. If your path contains
`.claude/plugins/cache/` or a version directory, `cd` back to the checkout.

## Feature implementation lessons

Tests set `AUTORUN_HOME` and `AUTORUN_TEST_STATE_DIR` before any autorun import
or they reach the live daemon; spec:
[`docs/RUNTIME_STATE_ISOLATION.md`](docs/RUNTIME_STATE_ISOLATION.md).

1. **One override-grant parser.** `ScopedAllow` and `scoped_allow.parse_scope_args`
   own the `5m | 5 | perm | 2h30m | 2d` grammar and `_PARALLEL_GRACE_SECONDS`
   absorbs rtk's double hook; reuse them (`cache_guard.grant_override`).
2. **Daemon paths use `state_get`/`state_set`/`state_update`**, never
   `session_state()`; wrap legacy helpers in `state_synchronize`.
3. **A new Claude event needs its Gemini analog in the same change:**
   `plugins.py:@app.on(...)`, the `harness_cli_to_autorun_events` maps of the
   `gemini`, `antigravity` and `qwen` entries in `platforms.py`,
   `tests/harness_hook_events.py`, and both `hooks/hooks.json` and
   `src/autorun/gemini_template/hooks/hooks.json`.
   `PreCompact` maps to `PreCompress`, which is advisory; there is no `PostCompress`.
4. **New hook-stdin data needs a slot, a property, an `__init__` kwarg, and
   every `EventContext(...)` call site updated.** Never `getattr(ctx, "field",
   None)`: it hides broken plumbing (`transcript_path` taught this).
5. **Tool-blocking features slot AFTER TIER 1 (`/ar:ok` allows) and BEFORE
   TIER 2 (pattern blocks)** — `plugins.check_blocked_commands` →
   `CacheGuard.from_ctx(ctx).check(ctx)` — or an explicit allow cannot bypass them.
6. **Keep full persistent-state reads off warm hooks.** Hydrate through
   `ThreadSafeDB` once per session; coalescing file locks still reparses everything.
7. **Fail open when data is unknown.** `CacheGuard.check` returns `None` (allow)
   whenever the data a threshold needs is missing.
8. **Default off.** A new gate stays off until the user runs `/ar:<feature> on`;
   the cache gate reads its `cache/toggle` state through
   `cache_guard._toggle_enabled`. One exception: the cache guard's
   prompt-time expiry check (`CacheGuard.check_prompt_after_expiry`) runs
   with nothing stored, at CONFIG `cache_guard_expired_prompt_action`, which
   is `warn`: it shows a notice and never stops a prompt unless the user
   runs `/ar:cache on` (block). `/ar:cache off` turns it off.
9. **Anchor `.gitignore` directory patterns with `/`** when you mean the repo
   root: unanchored `cache/` once hid a plugin directory named `cache` from git.
10. **Harness hook-event allowlists have one owner, `tests/harness_hook_events.py`.**
    An unknown event name in a Claude-scanned manifest is what
    [anthropics/claude-code#24115](https://github.com/anthropics/claude-code/issues/24115)
    turns into a silent disable of every hook.
11. **Capture tool-result fixtures from a transcript's `toolUseResult` field,
    not its rendered `tool_result` block.** Only `toolUseResult` matches what
    the hook receives as `tool_response`; the delegation spawn ledger was built
    from the rendered prose and recorded nothing live while every unit test
    passed. `hooks/hook_entry.py` replays a captured payload from stdin;
    `~/.autorun/hook_entry_debug.log` logs only the byte count and cannot settle it.
12. **Give every wire contract one live canary** gated by
    `@requires_real_money` from `tests/e2e_support.py`, asserting on persisted
    state, never the model's reply
    (`test_claude_live_fanout_populates_the_spawn_ledger`,
    `test_pi_live_model_tool_call_is_blocked_and_the_file_survives`; Pi's cannot
    be home-isolated because `auth.json` shares the extension directory). Record
    what a canary cannot cover in `BACKEND_E2E_CONTRACTS`, which is asserted
    against `PLATFORMS`.
13. **`@requires_real_money` is the only real-money gate**, and it both skips
    (unless `AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1`) and applies the
    `real_money` marker. Never re-read that variable in a test module:
    `test_real_money_gate.py` fails the build if you do, because a hand-rolled
    copy is how a paid test gets added with no gate at all. The marker is what
    makes the negative provable — a module *name* is not evidence, since paid
    tests also live in `test_claude_e2e.py`, `test_demo.py`,
    `test_gemini_before_tool_hooks.py` and `test_gemini_e2e_improved.py`, none
    of which say "real money". The two marker queries are in the root
    `AGENTS.md` section 4.

## State database versions and task statuses

Releases on users' machines (1.0.0rc1 to rc3) cannot change, and a rollback
runs their code against whatever a newer build left on disk. Every one of
them refuses any `PRAGMA user_version` but 2 at daemon start ("is not an
autorun schema generation"), and none reads the stored table definitions.
So two numbers, with different jobs:

- **`user_version` is the compatibility major** (`SCHEMA_USER_VERSION`).
  Raise it only for a change older builds cannot read or would damage. The
  release that raises it also ships the reverse step (`autorun
  --state-downgrade`) and names it in its release notes, and deletes
  `tests/test_schema_rollback_older_builds.py::test_older_builds_refuse_a_bumped_user_version`
  knowingly. A build refuses only a greater `user_version`, naming the build
  that wrote it and a command that works (`_check_schema_compatibility`, the
  one rule both validators use).
- **`schema_meta('schema_revision')` is additive** (`SCHEMA_REVISION`). Any
  revision opens; a lower one is migrated up inside `initialize`'s `BEGIN
  IMMEDIATE`, re-read under that lock, never down. A revision step holds the
  write lock for a time that does not grow with the database (measured in
  `tests/test_state_store_benchmark.py::TestRevision3Upgrade`) and copies no
  database.

Task status policy has one owner, `task_status.py`. A rewritten tasks table has no
status CHECK; its `blocks_stop` and `prunable` columns exist for older builds
and fail closed for statuses written after them. Code here never takes
policy from them: `list_incomplete` passes `task_status.py`'s lists as
parameters and narrows candidates with `prunable = 0` through
`tasks_retention`. Older lifecycle code cannot restore `awaiting-user`, so
the compatibility `blocks_stop` column blocks it. An earlier revision-3
definition is repaired without changing rows or rebuilding an index: its
partial index additionally excludes `awaiting-user`, preserving the index's
existing membership. Older readers use another index for that query after
rollback; they may read finished history but cannot silently exempt the task.
Adding a status changes `task_status.py` only. Changing what an existing
status means (`blocks_stop`, `prunable`) is an incompatible change:
`test_existing_status_policy_never_changes` freezes them, because skipping
REINDEX in the revision-3 rewrite depends on it.

If SQLite refuses the metadata rewrite, startup keeps the old definition and
never copies tasks or rebuilds indexes under the shared write lock. Newer
statuses use a CHECK-accepted `pending` column and an internal payload marker,
decoded at the repository boundary. An older writer advancing `updated_at`
invalidates that marker; older builds see blocking `pending`. The same fallback
applies to a historical CHECK-free layout whose indexes would need rebuilding.
When an earlier revision-3 definition also refuses its compatibility repair,
startup reads its open candidates without the writer slot and encodes each
existing awaiting-user row in its own short transaction. Completed history
is not visited, and older readers see blocking pending rows.
`autorun --state-rebuild-tasks` is optional offline maintenance: stop **all**
daemons using the store first. It refuses an active scoped daemon and checks
free space before rebuilding; it can hold the write lock for the table's full
copy time, so no startup or hook may call it. `user_version` stays 2.

## The Stop gate decides from state

`TaskLifecycle._stop_decision` blocks only on a task the agent can act on now
and lists every task that waits (`task_status.WAITING_TASK_STATUSES`, a
blocker that is still open, a row a fork inherited). Keep it that way:

- No clock and no block count decides anything. A delegation ends on
  evidence: SubagentStop, the recorded parent harness process gone, or a task
  update. `stop_block_max_count` is only a loop guard for Stops with no
  main-agent tool call between them.
- When nothing is actionable the gate returns `None`, never `ctx.allow()`:
  `autorun_injection` registers after it on Stop and must still answer.
- The awaiting-user clear runs inside `reset_stop_sequence_on_user_prompt`,
  the first UserPromptSubmit handler, and returns `None`; the prompt-time
  cache check stays last (`tests/test_cache_expiry_at_prompt.py`).
- Where `Platform.policy_commands_arrive_in_transcript` is true, Stop also
  restores on a different user-authored transcript prompt from the one stored
  with the decision. Reuse `latest_transcript_prompt` and
  `plugins._transcript_policy_context` with `resolve_prompt_author`; never add
  a second parser. The Codex `response_item` user shape is covered by
  `tests/fixtures/cache_expiry/codex_rollout_no_usable_record.jsonl`.
  A missing prompt identity refuses a new awaiting-user marker. Missing
  transcript evidence or a legacy decision without that identity makes the
  task actionable for Stop and prints why; it cannot stay silently exempt.
  Restoration compares the recorded decision before writing so an older
  reply cannot clear a newer decision. This fallback adds no per-tool work.
- Who wrote a prompt is `core.resolve_prompt_author`; which conversation an
  event belongs to is `core.resolve_conversation_role`. New code asks these
  rather than reading `agent_id` or `source` to decide either.
- Harness differences are `Platform` fields (`agent_spawn_running_results`,
  `stop_payload_lists_running_agents`, `fork_session_start_sources`,
  `goal_clear_command`). Set a field true or non-empty only with a captured
  payload, and say where it came from.
- Cost per hook: one task snapshot per Stop (`_open_task_snapshot`), waits
  derived in memory, one `pid_exists` per distinct recorded pid at Stop and
  SessionStart only, no task-row read on a prompt with nothing awaiting the
  user (`tests/test_stop_gate_hot_path.py`).

## Bug workaround policy

Every SDK bug workaround (Claude Code, Gemini-family, future CLIs):

- **Flag**: one key that is both env var and CONFIG entry,
  `AUTORUN_BUG_<NAME>_BUG_<NUMBER>_WORKAROUND_ENABLED`; lookup env → CONFIG →
  default `True`; values `true`/`1`/`auto` (affected platform), `always`,
  `false`/`0`/`never`. CONFIG template lives under `# ─── Bug Workarounds ───`
  in `config.py`.
- **Code**: one bracketed helper (`# --- BUG #N WORKAROUND START/END --- DELETE
  WHEN FIXED ---`) with a one-line call site; it checks env → CONFIG → `cli_type`
  via `detect_cli_type()`, sets both the workaround and the designed output,
  keeps `respond()`'s print guards (`reason=""` when `systemMessage` is set;
  both empty on a PreToolUse deny), uses only `HOOK_SCHEMAS` fields
  (`validate_hook_response()` strips the rest), and every site names the issue
  link, disable key, and deletion instruction.
- **Tests**: a bracketed `# --- BUG #N TESTS START/END ---` block with a shared
  `_BUG_FLAG`, covering flag True/False, affected/unaffected, `always`, `never`;
  nothing outside the block depends on it.
- **When fixed**: set the flag `False`, or delete helper, call, CONFIG key, and
  test block. Defense-in-depth handlers stay.

| Bug | Key | Effect |
|-----|-----|--------|
| [#4669](https://github.com/anthropics/claude-code/issues/4669) deny ignored at exit 0 | `AUTORUN_BUG_CLAUDE_CODE_DENY_IGNORED_AT_EXIT_ZERO_BUG_4669_WORKAROUND_ENABLED` (`AUTORUN_EXIT2_WORKAROUND`, `--exit2-mode` are higher-precedence aliases) | stderr + exit 2 |
| [#18534](https://github.com/anthropics/claude-code/issues/18534) additionalContext dropped | `AUTORUN_BUG_CLAUDE_CODE_IGNORES_ADDITIONAL_CONTEXT_JSON_ENTRY_BUG_18534_WORKAROUND_ENABLED` | channel `ai` → `both` |
| [#80305](https://github.com/anthropics/claude-code/issues/80305) mutable task tools gated off | `AUTORUN_BUG_CLAUDE_CODE_TASK_TOOLS_GATED_OFF_BUG_80305_WORKAROUND_ENABLED` | one ToolSearch load attempt, then the exact next-session env fix |
| [#80401](https://github.com/anthropics/claude-code/issues/80401) mutable task tools vanish mid-session | `AUTORUN_BUG_CLAUDE_CODE_TASK_TOOLS_VANISH_MID_SESSION_BUG_80401_WORKAROUND_ENABLED` | the same no-loop recovery in Stop and staleness guidance |

`config.py:should_use_exit2_workaround()` is the worked example. Upstream
status (checked 2026-08-05): #4669 and #24115 closed `NOT_PLANNED`, #18534
closed `DUPLICATE`, #14449 `COMPLETED` by the `hooks/hooks.json` convention this
already targets — a closed issue is not permission to delete a workaround.

## Harness families

- **Gemini family**: `gemini` (the standalone Gemini CLI, retired but
  installable with `--gemini`), `qwen` and `antigravity` (alias `agy`) are
  separate registry entries with their own event maps, built from one extension
  template, `gemini_template/`; see [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).
- **Pi family**: `prime` is Prime Agent, PrimeIntellect's Pi build
  (`~/.prime/agent/`). `platforms.PRIME` is `dataclasses.replace(PI, ...)`;
  `pi_template/` and `steps.pi_extension_step` serve both by substituting
  `__AUTORUN_CLI_TYPE__`. A new Pi variant is a registry entry and a `STEPS`
  row, never a second template.

## Trying a change end to end

Default: the sandboxed install from the root `AGENTS.md`, then inspect the
sandbox's trees and hooks (`--install-dry-run` previews without writing). On
the live machine, only when the user has written the instruction, use the root
`AGENTS.md` one-liners: "Install a local development CLI", then "Repair the
live Claude cache" when Claude's hooks must run the new code. Both restart the
daemon every running session shares.

## Entry points

`hooks/hook_entry.py` via `hooks/hooks.json` · CLI `autorun` →
`src/autorun/__main__.py:main` · `src/autorun/config.py` (all CONFIG values).
