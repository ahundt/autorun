# Changelog

Notable changes to the autorun marketplace (`ar` and `pdf-extractor`).

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions are the plugin versions in `.claude-plugin/marketplace.json`; the
marketplace itself carries a separate `version` field.

## [Unreleased]

## [1.0.0rc1] - 2026-08-15

### Added

- **Native Pi integration.** Pi installs an owned extension under
  `~/.pi/agent/extensions/ar/` with in-process tool vetoes, display-only
  commands, attributed continuation messages, bounded transcript projection,
  session lifecycle hooks, compaction events, and daemon-backed `TaskCreate`,
  `TaskUpdate`, and `TaskList` tools. Python remains the policy, task,
  persistence, ordering, and Stop-enforcement owner.
- **Prime Agent support as a Pi variant.** `prime-agent` (PrimeIntellect's
  build of the Pi coding agent) installs the same extension under
  `~/.prime/agent/extensions/ar/` with its own `cliType: "prime"` identity,
  `--cli prime` hook fallback, and `~/.prime/agent/AGENTS.md` guidance block.
  One template, one installer step, and one wire protocol serve both
  harnesses; `--prime` selects it alone.
- **Day units in scope durations.** `/ar:ok`, `/ar:globalok`, `/ar:cache`,
  and `/ar:task pause` durations accept `d` alongside `h`, `m`, and `s` — for
  example `2d` or `1d12h` — through the one shared `parse_duration` grammar.
  A zero count (`/ar:ok rm 0`, `/ar:cache ok 0`) is rejected with an error
  instead of granting an allow that could never activate.
- **OpenCode todo state enters the task lifecycle.** The OpenCode plugin
  forwards `todo.updated` events as `todowrite` receipts, so OpenCode's native
  todo list is mirrored into autorun's task status with its own ids and content;
  `cancelled` maps to `deleted`, an empty list clears only OpenCode-sourced
  records, and explicit task records are untouched.
- **Pi `TaskGet` and atomic `TaskUpdate` batches.** Pi and Prime gain a
  `TaskGet` tool, and `TaskUpdate` accepts a `taskUpdates` array applied in one
  lifecycle transaction with per-task confirmations.
- **Sequential Pi and Prime task ids, minted by the daemon.** `TaskCreate` in
  the Pi-family extension asks the daemon for the session's next id
  (`task_next_id_v1`, one above every numeric id recorded, never reused), so
  ids read `1`, `2`, `3` in tool results, `TaskList`, and
  `TaskUpdate(taskId=…)` — the same shape Claude Code's task tools use —
  instead of a 35-character `pi-<uuid>` string per call. When no daemon
  answers, the extension falls back to a `<cli>-<random>` id that the receipt
  then confirms or flags. `TaskList` and `/ar:task` list tasks in creation
  order rather than id-string order, so `10` no longer sorts before `9`.
- **Semantic XML regions in every shipped skill.** All 17 plugin skills, the
  Codex `$ar` catalog skill, the pdf-extractor skill, and the repo-internal
  maintainer skill wrap each major section in balanced, descriptive tags
  (`<purpose>`, `<requirements>`, `<workflow>`, `<output_contract>`, ...),
  Markdown inside and code in fences. `ai-skill-builder` 1.3.0 states the rule
  as SKILL-REQ004, `scripts/audit-skill.sh` fails a body with no region, an
  unbalanced tag, or a `## ` heading outside every region, its scaffolder and
  template emit regions, and `tests/test_skill_docs.py` gates every shipped
  SKILL.md on the same rules. `ai-skill-builder` also absorbs the standalone
  `engineer-agent-skills` package (P0 requirements SKILL-REQ001–013, the
  portable-standard vs runtime-extension matrix, claim audit, and per-host
  validation receipt in `references/portability-and-claim-audit.md`).

### Fixed

- **A counted allow is no longer spent twice by one command.** autorun runs
  twice for a single Bash command — its own hook, and the `rtk hook claude`
  entry that spawns another autorun — which is why grants carry the
  `session:tool:command` fingerprint of the call that consumed them. That
  fingerprint was recorded only on the use that reached zero, so at any higher
  count both invocations took the ordinary path and each decremented:
  `/ar:ok rm 3` bought one command, then a second, then blocked, and
  `/ar:ok rm 2` bought one. Every counted call is stamped now, and a repeat of
  the stamped call inside the grace window refreshes the stamp without
  decrementing — the rule `ScopedGrant.claim_once` already applied. A different
  command arriving in the same window still costs its own use. `/ar:cache ok N`
  grants are counted through the same code and were affected identically.
- **Five wrapper spellings no longer hide the command they wrap.** The
  transparent-wrapper grammar recorded the wrong arity for several options, and
  every mistake pointed the same way — the child command vanished and the guard
  allowed it. `sudo -k <command>` was treated as the credential-only form and
  discarded, though sudo documents that with a command it runs the command;
  `sudo -D <dir>` and `sudo -R <dir>` did not consume their directory, so the
  directory was read as the command; `chroot --skip-chdir` (which takes no
  value) swallowed NEWROOT; `env --block-signal`, `--default-signal` and
  `--ignore-signal` are documented with a bracketed value, so consuming the next
  word ate the command; and `env -a <name>` did not consume its value. Each
  option's arity now matches the tool that owns it.
- **The remaining predicates ask about the session's directory too.**
  `_has_uncommitted_changes` — registered under two names for user integration
  files — kept its pre-v4 body after its sibling became an alias: `git diff
  --quiet` with no directory, no scrubbed environment, and every error read as
  "no changes". It reported on whatever directory the daemon was started in,
  and a staged-only edit did not count as uncommitted work. It delegates to
  `_repo_differs_from_head` now, like `_has_unstaged_changes` already did. A
  user-written bash `when:` (`test -f package.json`) also ran in that same
  shared directory; it runs in the session's.
- **`git stash drop` is judged against the session's repository.** The guard
  that asks whether a stash exists ran `git stash list` in the hook process's
  own working directory — and the daemon serves every session on this machine
  from one process, started wherever it was started. So it answered for a
  repository the user was not in, both ways: a drop went through in a session
  whose stash was full because that other directory had none, and was blocked
  in a session with nothing to lose because it did. The first of those destroys
  work with no way back. It reads `ctx.cwd` now, with the same scrubbed
  environment and work-tree probe `_file_differs_from_ref` uses, and a probe
  that cannot answer blocks rather than permits — the rule the Time Machine
  predicate beside it already followed.
- **A Windows executable suffix no longer bypasses every command block.**
  `rm.exe -rf …`, `git.exe push` and `git.cmd checkout` matched no pattern,
  because the command name was read from argv[0] literally. That is the real
  filename on a platform this project tests in CI, so any block could be walked
  past by spelling it out. The one basename helper now takes off `.exe`,
  `.com`, `.bat` and `.cmd`, and splits on both path separators —
  `os.path.basename` splits backslashes only when it is itself running on
  Windows, and the hook parses the same command strings everywhere. The
  destructive-git and read-command predicates share that helper now instead of
  keeping their own copy.
- **An in-place `sed` is caught after the first `sed` and inside `sh -c`.**
  `_sed_modifies_files` asked for one segment's tokens, so
  `sed -n '1,20p' README.md && sed -i 's/old/new/g' README.md` was judged by
  the read-only invocation and the edit ran; `sh -c "sed -i …"`, whose argv[0]
  is the shell, was not seen at all. It now reads every command the way the
  Time Machine predicate beside it already did, which also recurses into shell
  bodies.
- **A destructive checkout or restore is caught wherever it sits in the line.**
  Both predicates read one segment — the first whose git subcommand is
  `checkout` or `restore` — and let it answer for the whole command. So
  `git checkout -- clean.py && git checkout -- dirty.py` was judged entirely by
  the file that had nothing to lose, and the write that discarded `dirty.py`
  ran unchallenged. The same shape hid behind a leading `git checkout -b` and a
  leading `git restore --staged`. Separately, `git restore` decided
  staged-versus-worktree by scanning the whole command string for the flag, so
  a `--staged` in an unrelated `echo` disarmed it. Every checkout and restore
  segment is judged now, and the restore flags are read from the restore's own
  tokens.
- **A git reached by absolute path no longer bypasses the checkout guard.**
  `command_matches_pattern` matched `/usr/bin/git checkout` by basename, so the
  pattern fired, but the predicate deciding whether the write would discard
  uncommitted changes compared the raw token, answered "nothing would be lost",
  and let the overwrite through. It compares the basename now, like every other
  layer.
- **Creating a branch is no longer blocked as a destructive checkout.** The
  `git reset --hard` block recommends `git checkout -b backup/<stamp>-wip` to
  save the work first, and a dirty repository is exactly the state that produces
  that block — so the recommended recovery was blocked too, with a message about
  discarding changes. `git checkout -b` and `--orphan` create a branch at the
  current commit and cannot overwrite working-tree content; git aborts rather
  than write over a local change. Both are allowed now. `-B`, which moves an
  existing branch ref, and `-f`, which really does overwrite, are unchanged.
- **A user-authored shared skill no longer produces a duplicate listing.**
  When `~/.agents/skills/<name>` holds the user's own loadable skill, the
  installer previously published autorun's same-named skill natively (for
  example into `~/.pi/agent/skills/`), so harnesses that read both routes
  listed the name twice. The native fallback now runs only when the blocked
  name would otherwise reach the harness by no route at all; the withheld
  copy is reported as a preserved conflict, and reinstalling retires
  previously published duplicates.
- **A harness-targeted install no longer retires the shared skills other
  harnesses read.** `autorun --install --claude` (or an empty selection)
  claimed nothing under `~/.agents/skills` because Claude loads its skills
  from the plugin package, so the retirement sweep removed every autorun-owned
  tree there as "no longer shipped" — the copies Codex, Qwen, Pi, Prime,
  ForgeCode, and OpenCode load. Every shipped skill is now claimed at the
  shared root on every install, and an empty selection sweeps nothing.
- **A tree installed before file hashes were recorded is no longer stuck.**
  Such a tree (owned marker, `files: []`) that had drifted from the source was
  reported as "you edited files we installed" naming every file, and no
  reinstall could refresh it, so harnesses reading `~/.agents/skills` loaded
  stale bodies indefinitely. A plain install now keeps it, names only the
  files that differ from what autorun ships, and says to rerun with
  `--force`; `--force` republishes it after copying the current tree to
  `~/.autorun/installer/backups/<name>-<stamp>/`. The same tree on a route
  autorun no longer uses (or under `--uninstall`) had the same problem with
  no way out; it is now kept with the same explanation and retired by
  `--force` after the same backup. A recorded user edit is still kept by
  every path.
- **Trees on Antigravity's old write root are decided at last.** Installs
  between 2026-07-09 and 2026-08-08 also materialized the bundle under
  `~/.gemini/antigravity-cli/plugins/<plugin>`; today's Antigravity entry
  keeps its config under `~/.gemini/config`, and the retirement sweep visited
  only current config roots, so those trees were never listed by status,
  never kept with a reason, and never retired. A harness now declares roots
  it used to write to (`Platform.retired_config_dirs`), the sweep reads them,
  and such trees follow the rule above: kept with the `--force`
  explanation by a plain install, retired with a backup by `--force`.
- **Antigravity no longer stays on the first bundle it imported.** Agy's
  import manifest names the plugin but no source path, so a copy under
  `~/.gemini/config/plugins/ar` was accepted as autorun's only while it
  matched the generated source byte for byte. Once the source changed, a copy
  made before autorun stamped its marker was skipped on every install without
  a message. The receipt plus autorun's own content in the copy (hook commands
  running its hook entry, or a copied skill carrying its marker) now proves
  ownership, so the copy is refreshed and stamped; a same-name plugin that
  fails that proof is reported as a failed install step naming the path.
- **pdf-extractor is staged for Gemini, Qwen, and Antigravity again.** The
  staging step required a `gemini_template/` directory; pdf-extractor has no
  hooks and keeps `gemini-extension.json` at its root, so since the installer
  rewrite it reached no Gemini-family harness and its earlier
  materializations were never refreshed. A root manifest now makes the plugin
  directory the template, and a hookless bundle gets no `hooks/` tree and no
  `hooks` manifest reference.
- **Status and dry run list each shared skill once.** The walk decided the
  same `~/.agents/skills/<name>` intent once per harness that reads the shared
  root (six lines per skill on a default install) and re-hashed the published
  tree each time; identical intents are now decided once per run.
- **Python 3.10 installs resolve again.** The lock selected onnxruntime
  1.24.3 for the `<3.11` fork (via markitdown → magika in the `pdf` extra),
  a version that ships no cp310 wheels; a workspace constraint now holds
  onnxruntime below 1.24 for Python 3.10.
- **Cross-process message-claim test carries a hook deadline.** Without one,
  the first contended store-lock attempt fails open by design and slow
  Windows runners saw two claim winners.
- **PyPI publishing through one distribution.** `autorun` is the only published
  package. PDF extraction ships inside it: `pdf_extraction` and the
  `extract-pdfs` script are always present, and every extraction backend is an
  optional extra, so `autorun[pdf]` is the ordinary PDF install and nobody else
  downloads an extraction library. `pdf-extractor` remains a separate plugin in
  every harness — its own manifest, command, and skill — it is simply not a
  separate Python package. The remaining extras are `pdf-gpu`, `pdf-llm`,
  `pdf-progress`, and `pdf-all`. The `pdf` extra uses maintained `pypdf` under
  the compatible `pypdf2` backend id. marker-pdf is omitted because it pins
  Pillow below the patched release; docling is constrained off macOS while its
  model stack pins transformers 4.x. `autorun --install` retires the
  `autorun-pdf-extractor` and `pdf-extractor` distributions that earlier
  versions installed. A tag workflow runs the complete CI gate, publishes to
  TestPyPI, then enters the protected PyPI environment through OIDC.
- **Shared Pi/OpenCode daemon transport.** Both in-process adapters use the
  packaged bounded JSON client while retaining their native event and response
  shapes.
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

- **Each notes component has its own switch and destination.** Accepted and
  rejected plans are now described by one table, so `/ar:pe accepted off` and
  `/ar:pe accepted dir <path>` work exactly like their `rejected` counterparts,
  a bare `/ar:pe <component>` toggles it, and `/ar:pe` reports every component's
  state and destination. A component writes only when both plan export and that
  component are on. Adding a component is a table row rather than an edit to the
  parser, the status text, the config dataclass and the defaults.

### Fixed

- **Failed installer preflight remains write-free.** The supported `filelock`
  range stops below 3.29.5, whose changed macOS lock-file lifecycle leaves an
  empty `.autorun-install.lock` after malformed shared-file preflight. The
  installer does not unlink potentially live locks.
- **Retired Gemini CLI checks are optional in deterministic runs.** An
  unresponsive externally installed Gemini CLI now skips the free registration
  probe unless legacy paid tests were explicitly enabled; direct hook tests
  remain active.
- **Pi task receipts report authoritative state.** Mutation results now show
  the Python-confirmed status and subject, including normalized and
  ghost-protected transitions. Task lists use one bounded, dependency-aware
  snapshot, and active-branch replay removes only Pi-sourced records.
- **Concurrent Pi task operations preserve lifecycle state.** Threaded,
  overlapping daemon-client, and spawned-process tests cover same-session and
  different-session writes, working-directory changes, state-root isolation,
  persistence, Stop blocking, and Stop release.
- **Direct JSON hook processes retain their lock-acquisition budget under
  contention.** They poll the shared state lock every 5 ms instead of the
  `filelock` default of 50 ms. The 500 ms hook deadline is unchanged, but
  four/eight-writer reminder and message-delivery bursts no longer discard most
  of their budget between attempts under coverage load.
- **Source-checkout installs no longer publish test artifacts.** `.coverage*`,
  `coverage.xml`, `htmlcov`, and `.ruff_cache` are excluded from plugin trees,
  and `--install --force` bounds modified-file detail to ten names plus the
  omitted count.

- **A failed Claude registration no longer deletes the cached hook runtime.**
  The cache fallback introduced at `c09c3b2` ran whenever native registration
  failed, even when Claude's versioned cache already existed. Its atomic tree
  replacement intentionally omitted `.venv`, removing Claude's managed
  packages. Fallback now fills only an absent cache, checks existence while
  holding the cross-process publication lock, uses a non-replacing rename when
  racing Claude's native installer, and preserves a concurrently or previously
  populated cache verbatim.
- **A broken runtime no longer tells every attached session to retry forever.**
  When `import autorun` failed, `fail_closed_tool_gate` correctly denied every
  tool call, but its retry advice could never succeed. Bootstrap-in-flight is
  now the only recoverable state. Other failures keep denying every command and
  print an exact out-of-band command that installs this source into the hook's
  own interpreter. `AUTORUN_DISABLE=1`, read before any autorun import, remains
  the explicit human kill switch when work must continue before repair.
- **A cold start plus a response could overrun the hook wrapper on four of
  seven harnesses.** The two waits were separate constants, each checked
  against the wrapper budget and never checked as a sum: gemini, antigravity
  and qwen spent 0.8s starting the daemon plus 3.5s awaiting a reply against a
  4.0s wrapper, and opencode 0.8 + 4.0 against 4.5. The wrapper fires first, so
  the client's own bound is unreachable and the failure response explaining the
  timeout is never written. The room for a cold start is the wrapper minus the
  response — 0.5s on gemini, 1.0s on Claude — so no single constant can serve
  every harness. `client_total_budget()` derives one deadline per harness and
  the client spends it across both phases, which makes the sum correct by
  construction. The retry cap becomes a recursion guard rather than the bound;
  at 8 attempts of 0.1s it ended every cold start at 0.8s with the remaining
  budget unspent, invisible where a daemon becomes reachable in a measured
  0.143s and not where interpreter startup is slower.
- **`can_prompt()` refused to prompt on a real terminal.** The Windows console
  probe treated any stream without a usable `fileno()` as non-interactive.
  `NUL`, which `subprocess.DEVNULL` supplies, always yields a real handle, so
  `GetConsoleMode` refusing it is authoritative; failing to obtain a handle at
  all is missing evidence and now leaves the `isatty()` answer standing. The
  probe moved to `windows_tty_is_a_console()` so it can be exercised off
  Windows — inside `can_prompt` it sits behind a `sys.platform` guard, so every
  test of it passed vacuously everywhere except the one platform that ran it.
- **The CLI launcher crashed instead of printing repair instructions.**
  `autorun.py` prints its guidance when the package may not be importable, so
  it cannot reach `logging_utils.use_utf8_output()`. On a cp1252 console the
  emoji raised `UnicodeEncodeError` and the user received a traceback in place
  of the steps that would fix the install. Stdout and stderr are reconfigured
  with `errors="replace"` using only the standard library, so every glyph is
  kept and at worst one degrades.
- **A daemon that died during startup left no evidence.** Its stderr went to
  `DEVNULL`, so an import failure, a failed bind and a merely slow start all
  produced the same "failed to start after N attempts". Startup output now
  appends to the daemon log, and the connection error from the last attempt is
  carried into the failure.
- **Plan export did nothing on Windows.** `is_plan_file` tested the raw path
  for the literal `/.claude/plans/`, which a Windows path
  (`C:\Users\<user>\.claude\plans\<name>.md`) never contains, so no plan was
  ever tracked and export, recovery and the rejected-plan backup all skipped
  silently while reporting success.
- **Hooks took the slow path on every Windows event.** `get_autorun_bin`
  looked for `autorun` beside the interpreter and at `.venv/bin/autorun`; a
  Windows venv writes `.venv/Scripts/autorun.exe`, so all three plugin-local
  tiers missed and resolution fell through to the global binary, which the
  direct-daemon check refuses. Both layouts are now accepted on every platform.
- **The daemon never started on Windows.** The spawn string embedded the source
  directory in generated Python without escaping, so `C:\Users\...` made `\U`
  an invalid escape and the interpreter died before importing anything. Every
  hook then fell through to the CLI, which waited on the daemon it had just
  failed to start until the caller gave up — reported as `autorun CLI timed out
  after 5s`. The daemon was also spawned with `start_new_session` alone, which
  is POSIX-only, so even when it did start it was reaped with its parent.
- **The daemon fast path did not exist on Windows.** `try_daemon` returned
  immediately when `socket.AF_UNIX` was absent instead of using the loopback
  endpoint the daemon publishes there, so every event paid a second interpreter
  start.
- **The OpenCode shim could not reach the daemon on Windows.** It connected
  only over a Unix socket, and the installer substituted the socket and port
  paths into JavaScript string literals without escaping, so both were mangled
  by the backslashes in a Windows path.
- **`autorun task` prompted where it was told not to.** `sys.stdin.isatty()`
  is true for `NUL` on Windows, which is what `subprocess.DEVNULL` supplies, so
  a deliberately non-interactive run prompted and then died on EOF instead of
  printing its settings or refusing.
- **A durable checkpoint could undo another process's work.** The task-staleness
  counter was blind-set at one point while other processes advanced it through
  an atomic update, so a process that had crossed the reminder boundary and
  reset the counter was pushed back and the boundary was crossed twice. Three
  of eight concurrent processes emitted a reminder that must be emitted once.
- **`autorun` CLI subcommands crashed on a non-UTF-8 console.** Windows
  reported `'charmap' codec can't encode characters in position 0-1` instead of
  the command's own output, including refusals the user had asked for.
- **Concurrent first-run processes could fail to create the state database.**
  SQLite does not run the busy handler while taking the exclusive lock a
  journal-mode change needs, so a process losing that race was refused with
  "database is locked" even though `busy_timeout` was set.
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
  yields identical bytes under the same source and toolchain.
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

- The Claude E2E module no longer labels regular network-free hook tests as
  real-money tests; paid `claude -p` cases remain explicitly opt-in inside the
  shared harness.
- CI's external actions are pinned to immutable commit SHAs. The release
  checklist gates one exact commit through all eleven jobs, a scratch-home
  marketplace install, immutable remote-tag recovery, and prerelease metadata
  verification.

- `staged_replacement`, an RAII context manager, replaced three copy-pasted
  lock/stage/rollback blocks and gained a `precondition` that runs inside the
  lock.
- `durable_io.atomic_write_text` for user-owned files that are
  read-modify-written rather than regenerated.
- A spec checker (`tests/test_install_location_spec.py`) fails the suite when an
  install or uninstall path is hardcoded, when a teardown function deletes
  without consulting an ownership marker, or when a claimed location has no
  uninstall-side reader.
- **A canary fails the suite when a test modifies autorun's installed copy.**
  `AUTORUN_HOME` and `AUTORUN_TEST_STATE_DIR` redirect state, but nothing
  redirected the installed plugin, the harness settings pointing at it, or the
  shared marketplace registry — so a test that shelled out to an installer, or
  resolved a path from the real home instead of its fixture, edited the user's
  working install and the suite still passed. `tests/conftest.py` now
  fingerprints those artifacts at session start and reports any create, edit, or
  delete at session finish, with a non-zero exit. Only code and configuration
  are watched; sockets, PID files, logs and databases under `~/.autorun` are
  excluded because a user's own daemon writes them while the suite runs, and a
  canary with false positives gets deleted. `e2e` and `release` selections
  install on purpose and are exempt, as is
  `AUTORUN_ALLOW_LIVE_INSTALL_WRITES=1`.
  `tests/test_live_install_canary.py` exercises every branch against a fake
  home, including an end-to-end pytest run whose test writes to the install and
  must exit non-zero — a canary nobody has seen fail reads as coverage while
  providing none.
- `_expand_home` gives `~` expansion a single seam; install and uninstall
  previously resolved through `Path.home()` and `Path.expanduser()`
  respectively, which differ under test.
- **`uv.lock` is committed.** CI runs `uv sync --locked`, which needs the
  lockfile in the checkout; without it the flag pinned nothing, because the
  matrix jobs happened to run `uv run` first and that writes a lockfile as a
  side effect. Contributors get the same resolution CI does. The lockfile is
  not published in the wheel or sdist, so it does not constrain consumers.
- **The CI matrix covers Python 3.10 through 3.14**, plus dedicated coverage,
  release-artifact, tmux, and state-benchmark jobs. The added versions found
  two real breaks: an unguarded `tomllib` import, which is stdlib only on
  3.11+, and multiprocessing tests that relied on workers inheriting the
  parent's environment, which stops holding when 3.14 makes forkserver the
  default start method on Linux.

## [0.12.0]

Baseline for this changelog. See `git log` for history before it.
