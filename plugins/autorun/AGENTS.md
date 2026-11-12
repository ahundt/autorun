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
   `gemini`, `antigravity` and `qwen` entries in `platforms.py` (`core.GEMINI_EVENT_MAP`
   is derived from them), `tests/harness_hook_events.py`, and both
   `hooks/hooks.json` and `src/autorun/gemini_template/hooks/hooks.json`.
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
