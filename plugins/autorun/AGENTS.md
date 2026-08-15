# autorun plugin development guidance

One file per directory, shared by every harness: `CLAUDE.md` and `GEMINI.md`
here are symlinks to this file. Edit `AGENTS.md`; never replace a symlink with
a second copy.

Repository-level guidance (commands, installation, three-stage verification,
plugin overview) lives in the root [`AGENTS.md`](../../AGENTS.md). This file
covers developing the plugin itself.

## Hook error prevention (CRITICAL)

Claude Code treats ANY stderr output from a hook as a hook error and discards
that hook's JSON response. Every hook protection (rm blocking, git safety, file
policies) silently stops working while the session still looks healthy.

1. **`pyproject.toml [tool.uv]`**: never add deprecated UV fields. UV removes fields silently across versions and prints a stderr warning for unknown ones, which breaks all hooks. `default-extras` was removed in UV 0.9+; put default extras in `[project] dependencies` instead.
2. **Slash commands**: every bash command in a `.md` file must use `uv run --project ${CLAUDE_PLUGIN_ROOT} python`, never bare `python3`. `allowed-tools` frontmatter must say `Bash(uv *)`, not `Bash(python3:*)`. This covers `!`-prefixed dynamic output too, for example ``!`uv run --project ${CLAUDE_PLUGIN_ROOT} python -c "from autorun.config import CONFIG; print(CONFIG['key'])"` ``.
3. **Hook stderr**: `hook_entry.py` must never write to stderr. Route all error handling through `fail_open()`, which writes JSON to stdout.
4. **Cache sync**: after fixing `pyproject.toml` or `hooks.json` in the source, run `uv run --project plugins/autorun python -m autorun --install --force`. Hand-copying files into `~/.claude/plugins/cache/` is fragile and is overwritten on the next install.
5. **Session restart**: hook configuration is read once at session start, so `hooks.json` and `pyproject.toml` fixes take effect only in the NEXT session.

Regression tests: `test_hook_entry.py::TestUVCompatibility` and
`test_hook_entry.py::TestCacheSync`. Diagnose with `uv run --project
<plugin_root> python -c "pass" 2>&1`; any output beyond Building/Installed
lines is the problem.

## Read and edit the git repository, not the plugin cache

Work in `<git-root>/plugins/autorun/`. Never edit
`~/.claude/plugins/cache/autorun/ar/<version>/` (the marketplace
is `autorun`, the plugin inside it is `ar`).

Installing the plugin (`claude plugin marketplace add
https://github.com/ahundt/autorun.git`, then `claude plugin install ar@autorun`)
copies the repository into that cache, and the plugin loads from there, so an
AI following a runtime path lands in the cache by accident. Edits there are not
version controlled, are overwritten on the next install, and may already be
behind the repository's bug fixes.

You are in the wrong place if the path contains `.claude/plugins/cache/` or a
version directory like `1.0.0rc1/`. Recover by `cd <git-root>/plugins/autorun/`
(`git status` must succeed, `pwd` must end in `plugins/autorun/`), make the edit
and run the tests there, commit from the git root, then reinstall with
`claude plugin update ar@autorun`.

## Feature implementation lessons

Tests set `AUTORUN_HOME` and `AUTORUN_TEST_STATE_DIR` before any autorun import
or they reach the live daemon. Rules: root [`AGENTS.md`](../../AGENTS.md)
§ Critical Runtime Isolation; full spec:
[`docs/RUNTIME_STATE_ISOLATION.md`](docs/RUNTIME_STATE_ISOLATION.md).

Follow these when adding any new gated feature.

1. **Reuse `ScopedAllow` and `parse_scope_args` for every override grant.** Never write a second TTL/count parser: the `5m | 5 | perm | 2h30m` grammar is `scoped_allow.py:parse_scope_args` (line 44), and `_PARALLEL_GRACE_SECONDS` (line 187) already absorbs rtk's double-hook. See `cache_guard.grant_override`.
2. **Use `state_get`, `state_set`, and `state_update` in daemon paths, never `session_state()`.** They keep `ThreadSafeDB` coherent; wrap legacy direct-persistence helpers in `state_synchronize`. `session_state()` is for standalone administration and persistence internals only.
3. **A new Claude event needs its Gemini analog wired in the same change, in three places:** `plugins.py:@app.on(...)`, `core.py:GEMINI_EVENT_MAP`, and BOTH `hooks/hooks.json` and `src/autorun/gemini_template/hooks/hooks.json`. `PreCompact` maps to `PreCompress`, which is advisory and cannot block; no `PostCompress` exists.
4. **New hook-stdin data needs a slot, a property, an `__init__` kwarg, and every `EventContext(...)` call site updated.** Never `getattr(ctx, "field", None)`: it returns None when the plumbing is broken instead of failing. `transcript_path` is the case that taught this.
5. **Features that may block tools slot AFTER TIER 1 (`/ar:ok` allows) and BEFORE TIER 2 (pattern blocks),** or an explicit allow cannot bypass the new gate. Site: `plugins.check_blocked_commands` → `CacheGuard.from_ctx(ctx).check(ctx)` (`plugins.py:1126`).
6. **Keep full persistent-state reads off warm hooks.** Hydrate through `ThreadSafeDB` once per session and use atomic updates for shared fields. Coalescing file locks alone does not fix this; it still reparses the full durable state.
7. **Fail open when data is unknown.** A gate that errors or denies on missing fields is worse than one that allows, so CacheGuard returns `HookDecision.allow()` whenever its axis data is None. Cross-CLI robustness falls out of this for free.
8. **Default off.** A new gate defaults `False` in its `FeatureToggle`; users opt in with `/ar:<feature> on`.
9. **Anchor `.gitignore` directory patterns with a leading `/` when you mean the repo root.** Unanchored `cache/` also matches `plugins/autorun/skills/cache/`, which hid the `/ar:cache` skill from git entirely.
10. **Harness hook-event allowlists have one owner, `tests/harness_hook_events.py`.** Edit the sets there only, and only with a source: an unknown event name in a Claude-scanned manifest is what bug #24115 turns into a silent disable of every hook.
11. **Capture tool-result fixtures from a transcript's `toolUseResult` field, not its rendered `tool_result` block.** They are different objects, and only `toolUseResult` matches what the hook receives as `tool_response`. The delegation spawn ledger was built from the rendered prose `agentId: <id>` and recorded nothing in a live session, because the hook is handed `{"agentId": "<id>", ...}`, which `coerce_tool_result_to_str` JSON-encodes to `"agentId": "<id>"`. Every unit test passed against the wrong shape. `hooks/hook_entry.py` replays a captured payload from stdin; `~/.autorun/hook_entry_debug.log` cannot settle it, since it logs the stdin byte count and not the payload.
12. **Give every wire contract one live canary.** Fixture drift is invisible to fixture-driven tests by construction, so a contract only a real harness produces needs one real-money end-to-end check behind `AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1`. Assert on persisted state, never the model's reply: a harness exit code of 0 says nothing about whether the hook saw anything. `test_claude_live_fanout_populates_the_spawn_ledger` is the worked example, under $0.01 and ~27s. `test_pi_live_model_tool_call_is_blocked_and_the_file_survives` is the second, ~16s: it asks a live model to run one allowed command and one blocked one, then reads two files. The allowed one is not decoration — without it a model that answers in prose and calls nothing leaves the probe intact and is indistinguishable from a working guard. Pi's canary cannot be isolated the way the others are, because `auth.json` lives in the same directory as the installed extension, so redirecting `PI_CODING_AGENT_DIR` takes the credentials with it; only its working directory is temporary. Record what a canary cannot cover in `BACKEND_E2E_CONTRACTS`, which is asserted against `PLATFORMS` so a new harness cannot ship without declaring its strongest surface.

## Bug Workaround Policy

Every SDK bug workaround (Claude Code, Gemini CLI, future CLIs) must follow all
of this.

**Flag** — ONE key serving as both env var and CONFIG entry:

1. Format `AUTORUN_BUG_<DESCRIPTIVE_NAME>_BUG_<NUMBER>_WORKAROUND_ENABLED`
2. Lookup order: env var → CONFIG dict → default `True`
3. Values: `true`/`1`/`auto` (affected platform) · `always` (all) · `false`/`0`/`never` (off)

**Code** — a self-contained removable unit, invisible to callers:

1. One bracketed helper (`# --- BUG #N WORKAROUND START/END --- DELETE WHEN FIXED ---`) with one one-line call site
2. The helper checks env → CONFIG → `cli_type` (via `detect_cli_type()`, never a hardcoded name) and no-ops on unaffected platforms
3. It sets both the workaround AND the designed output (for example `systemMessage` AND `additionalContext`) so the designed field is ready when the bug is fixed
4. It preserves `respond()` print guards: `reason=""` when `systemMessage` is set (anti-double-print), and `reason=""` plus `systemMessage=""` on a PreToolUse deny (anti-triple-print with stderr)
5. It uses only fields in `HOOK_SCHEMAS` for that event type; `validate_hook_response()` strips the rest
6. Every affected site carries the bug number, full issue link, description, disable key, and deletion instruction
7. Removal is: delete the helper START→END, replace the call with the designed-behavior literal

**Tests** — a self-contained removable block:

1. Bracketed `# --- BUG #N TESTS START/END ---` with a shared `_BUG_FLAG` constant
2. Passing with the flag both True and False, covering affected+enabled, affected+disabled, unaffected, `env=always`, and `env=never`
3. No non-bug test depends on the block, so it can be deleted whole

**When fixed**: set the flag `False` for a quick disable, or delete the helper,
replace the call with the literal, and delete the CONFIG key and test block.
Defense-in-depth handlers stay.

**CONFIG template** (`config.py`, `# ─── Bug Workarounds ───`):

```
# BUG #NNNNN: What's broken. https://github.com/anthropics/claude-code/issues/NNNNN
# Workaround: what changes. Override: env var same name (true|false|always|never).
# Evidence: notes/YYYY_MM_DD_*.md — Set to False when fixed.
"AUTORUN_BUG_<NAME>_BUG_<NUMBER>_WORKAROUND_ENABLED": True,
```

| Bug | Platform | Key | Default | Effect |
|-----|----------|-----|---------|--------|
| [#4669](https://github.com/anthropics/claude-code/issues/4669): deny ignored at exit 0 | Claude Code | `AUTORUN_BUG_CLAUDE_CODE_DENY_IGNORED_AT_EXIT_ZERO_BUG_4669_WORKAROUND_ENABLED`; `AUTORUN_EXIT2_WORKAROUND` and `--exit2-mode` remain as higher-precedence aliases | `True` | stderr + exit 2 |
| [#18534](https://github.com/anthropics/claude-code/issues/18534): additionalContext dropped | Claude Code | `AUTORUN_BUG_CLAUDE_CODE_IGNORES_ADDITIONAL_CONTEXT_JSON_ENTRY_BUG_18534_WORKAROUND_ENABLED` | `True` | channel="ai" → "both" |

Read `src/autorun/config.py:should_use_exit2_workaround()` and its bracketed
block for #4669 (resolution order, value tokens, removal). The permanent
Claude/Gemini split layout is documented in `src/autorun/installer/extension.py`.

Upstream status, checked 2026-08-05 with `gh`: #4669 closed 2026-01-05 and
#24115 closed 2026-04-27, both `NOT_PLANNED`; #18534 closed 2026-01-19 as
`DUPLICATE`; only #14449 closed `COMPLETED` (2025-12-19), by PR #14460 merging
the `hooks/hooks.json` convention this already targets. Three of the four were
closed without a fix, so a closed issue is not permission to delete a
workaround. Verify the behavior first.

## Quick install/update command

Run from the repository root after editing anything under `src/autorun/` or
`hooks/`, after changing plugin configuration, and when testing a fix:

```bash
(uv run --project plugins/autorun python -m autorun --install --force && \
  cd plugins/autorun && \
  uv tool install --force --editable . && \
  cd ../.. && \
  autorun --restart-daemon) 2>&1 | tee "install-$(date +%Y%m%d-%H%M%S).log"
```

It syncs the plugin to both the Claude Code and Gemini caches, installs the
`autorun` and `aise` commands globally, and restarts the daemon so code changes
take effect. Allow a 3-minute timeout through the Bash tool: the UV tool step
takes 1-2 minutes on a first run or a dependency change.

## Gemini-family harnesses

`gemini` here covers the Qwen Code and Antigravity (agy) family, and Qwen Code
forked Gemini CLI, so `GEMINI_EVENT_MAP`, `gemini_template/`, and the `gemini`
platform key cover all of them. Standalone Gemini CLI is retired but still
supported; its `enableHooks` prerequisite and legacy install live in
[`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) and [README.md](../../README.md).

## Entry points

- **Commands**: `commands/autorun` — executable called by the plugin system (JSON stdin/stdout)
- **Hooks**: `hooks/hook_entry.py` — configured via `hooks/hooks.json`
- **CLI**: `autorun` → `src/autorun/__main__.py:main` (via `uv tool install --editable .`)
- **Config**: `src/autorun/config.py` — single source of truth for all CONFIG values
