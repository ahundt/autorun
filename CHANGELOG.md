# Changelog

Notable changes to the autorun marketplace (`ar` and `pdf-extractor`).

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions are the plugin versions in `.claude-plugin/marketplace.json`; the
marketplace itself carries a separate `version` field.

## [Unreleased]

### Added

- **Agent memory install.** `autorun --install` writes a sentinel-delimited
  guidance block into each harness's memory file — `~/.claude/CLAUDE.md`,
  `~/.codex/AGENTS.md`, `<forge>/AGENTS.md`. Content is per-harness: the Claude
  block covers two measured failure modes (context-capacity claims made without
  measurement, and stop-gate denials) that would be false statements on Codex.
  Only autorun's own block is ever written or removed; surrounding user content
  is untouched. Disable with
  `AUTORUN_BUG_CLAUDE_CODE_NO_TOKEN_COUNT_FOR_HOOKS_BUG_54673_WORKAROUND_ENABLED=false`.
- **`--claude-agents-skills {link,copy,none}`.** Bridges skills authored in the
  shared `~/.agents/skills` directory into Claude Code, which reads only its own
  config-dir skills folder. Defaults to `none`, so no install silently rewrites
  a skills directory. Refuses outright when the target skills directory is
  itself a symlink, because Claude Code stops loading user skills in that layout
  ([anthropics/claude-code#38051](https://github.com/anthropics/claude-code/issues/38051)).
- **Health checks in `--status`.** Six probes for install states that previously
  produced no signal at all: guidance written where the harness will not read it
  (`~/.codex/AGENTS.override.md` shadowing), broken skill links, a skill reaching
  one harness by two paths, an orphaned sentinel slug, a stray top-level key that
  makes Codex drop every hook in the file, and artifacts left by an interrupted
  uninstall. Advisory only — it reports, it never repairs.
- **Configurable install locations.** `shared_agents_dir`,
  `shared_agents_skills_subdir`, `shared_agents_plugins_subdir` and
  `codex_plugin_source_dir` in CONFIG. Install and uninstall read the same keys,
  so relocating one moves both.

### Fixed

- **`--uninstall` no longer leaves most of the install behind.** It now removes
  Gemini, Qwen and Antigravity extension directories (asking each harness CLI
  first, so its registry stays consistent), ForgeCode command files, plugin
  skills copied into the shared agents directory, guidance blocks, bridged
  skills, autorun's entry in the Codex personal marketplace and the plugin
  source it points at, and leftover `.autorun-install.lock` files. Every removal
  is gated on an ownership marker autorun writes at creation time, so a
  directory autorun did not create is never deleted, however it is named.
- **`--claude-agents-skills copy` was a one-way door.** Copies are real
  directories, indistinguishable from user-authored ones, so uninstall skipped
  them. They now carry the ownership marker.
- **`--uninstall pdf-extractor` deleted the shared plugin cache and the global
  `autorun` CLI.** Autorun-wide artifacts are now removed only on a full
  uninstall.
- **ForgeCode install destroyed user-authored `<base>/AGENTS.md`.** It used
  `shutil.copy2`; it now merges a sentinel block like every other harness.
  Idempotence tests could not catch this — overwriting with the same template
  always yields identical bytes.
- **A stray sentinel marker made memory installs grow the guidance file
  without bound** while leaving the block permanently un-strippable.
- **`AUTORUN_CODEX_*` environment variables overrode explicit CLI flags.**
  argparse supplied its own default, which was indistinguishable from a user's
  explicit choice.
- **`~/.codex/AGENTS.override.md` silently shadows autorun's guidance.** Install
  now warns instead of reporting success for a file Codex will not read.
- **The Stop-block message printed twice**, once from the hook response and once
  from the task-lifecycle echo.
- **Uninstall restarted the daemon it had just removed the code for.** It now
  stops it.
- **A check-then-publish race in `_ensure_codex_plugin_source`** let a
  user-authored directory appear between the ownership check and the write, and
  be replaced.

### Changed

- **Bug #4669 follows the bug-workaround policy.** The exit-2 deny workaround
  now has a bracketed removable block and a CONFIG key
  (`AUTORUN_BUG_CLAUDE_CODE_DENY_IGNORED_AT_EXIT_ZERO_BUG_4669_WORKAROUND_ENABLED`),
  so it can be disabled without an environment variable. `AUTORUN_EXIT2_WORKAROUND`
  and `--exit2-mode` keep working and take precedence. Applicability now comes
  from `Platform.has_exit2_workaround` rather than a hardcoded harness name.
- **Agent memory installs are declarative.** `Platform.memory_filename`,
  `memory_template` and `memory_sentinel_slug` drive one shared installer;
  adding a harness needs no installer code.
- **Uninstall metadata lives beside install metadata** on `Platform`
  (`extensions_subdir`, `uninstall_cmd`), so the two cannot drift.
- `TaskLifecycle.cli_status` / `cli_export` take `output_format=` instead of
  `format=`, which shadowed the builtin.

### Removed

- Dead symbols: `get_python_version_info`, `is_uv_environment`,
  `_verify_gemini_installation`, `ALL_TASK_TOOLS`, `CLAUDE_MODE_CYCLE`,
  `BOOTSTRAP_MSG`, and the unread `plan_acceptance_notify` CONFIG entry.

### Internal

- `staged_replacement`, an RAII context manager, replaced three copy-pasted
  lock/stage/rollback blocks and gained a `precondition` that runs inside the
  lock.
- `durable_io.atomic_write_text` for user-owned files that are
  read-modify-written rather than regenerated.
- A spec checker (`tests/test_install_location_spec.py`) fails the suite when an
  install or uninstall path is hardcoded, when a teardown function deletes
  without consulting an ownership marker, or when a claimed location has no
  uninstall-side reader.
- `_expand_home` gives `~` expansion a single seam; install and uninstall
  previously resolved through `Path.home()` and `Path.expanduser()`
  respectively, which differ under test.

## [0.12.0]

Baseline for this changelog. See `git log` for history before it.
