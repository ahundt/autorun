# The installer

`CLAUDE.md`/`GEMINI.md` are symlinks here. Edit this file.

## One idea

**Status, dry run, install, uninstall, prune are one traversal, three modes.**
All ask: what is here, is it ours, does it match what we ship?

| Mode | Effect |
|---|---|
| `PREVIEW` | prints, writes nothing. Status *is* dry run |
| `INSTALL` | acts on `PUBLISH` and `RETIRE` |
| `UNINSTALL` | same walk, sources dropped, so all `RETIRE` |

`decide()` takes `source: Path \| None`; `None` is the retirement question, not a
special case. Mode does not decide scope: `decide(plugin=...)` and `fs.owns` do,
so `--uninstall pdf-extractor` cannot touch autorun's trees.

## Three rules

1. **Only `fs` mutates:** `publish_tree`, `publish_files`, `withdrawn`,
   `withdraw_files`, `json_document`. Each records a manifest, so an
   unidentifiable tree cannot be created.
2. **Steps yield `Intent`s and touch no disk.** That is why no `dry_run` parameter
   is threaded anywhere.
3. **Harness differences are data.** Nothing in `traversal.py` names a harness.

## Who owns which question

| Question | Owner. Never re-derive elsewhere |
|---|---|
| Where does this harness keep config? | `discovery.config_dir` (CONFIG override → env var → default) |
| Extensions dir, or none? | `discovery.extensions_dir`, where `None` is a real answer |
| Where do skills go / come from? | `discovery.skill_destinations(reading=…)` |
| Is this tree ours? | `fs.owns` (+ `PLUGIN_ALIASES` for old markers) |
| May we write here? | `fs.decide` / `fs.decide_files` |
| Where is the marketplace / a plugin? | `discovery.marketplace_root`, `plugin_dir` |
| What did the user configure? | `settings.INSTALL_SETTINGS`, resolved once at entry |

## Modules

`fs` owned trees, marker+hash manifest, atomic JSON · `traversal` walk, modes,
`Intent`, retirement sweep · `discovery` roots, plugins, dirs · `settings` one
declaration → resolution, help, parsers · `skills` routes, blocking, bridge ·
`memory` sentinel region · `runtime` uv, probe, bootstrap, self-update, daemon ·
`harness` TOML + placeholders · `codex` hooks, marketplace · `extension`
materialize + refresh.

## Exact names. A wrong one is silent, never an error

| | Owner |
|---|---|
| `gemini-extension.json`, fixed. Not `<name>-extension.json`. All three family CLIs | `extension.MANIFEST_NAME` |
| `<ext>/hooks/hooks.json`. The manifest's own `hooks` field is ignored | `extension.stage_extension` |
| `.<cli>-extension-install.json` receipt, per harness | `extension.RECEIPT_GLOB` |
| `.autorun-owned` · `.autorun-install.lock` (in the **parent**) | `fs.OWNED_MARKER_NAME`, `INSTALL_LOCK_NAME` |
| Codex top level: only `description`, `hooks`. Any other key drops **every** hook | `codex.ALLOWED_TOP_LEVEL` |
| `.claude-plugin/marketplace.json` | `discovery.MARKETPLACE_MANIFEST` |

Harness-owned, never ours: `qwen-extension.json` and the
`*-extension-install.json` receipts.

## Extending

**Harness:** register the `Platform`, add a step-table row. A *custom* harness
clones its flavor's entry and reuses that flavor's steps, so no branch is needed.

**Capability:** `f(harness, ctx) -> Iterable[Intent]`, listed in the step tuples
that need it. No disk access: stage generated content to a temp dir and point the
`Intent` there, as `extension.stage_extension` does.

## Traps

- **A user edit in an owned tree is kept.** `decide()` returns `KEEP` and names
  the files. No force path may discard it. Ordinary publication keeps an edit
  even under `--force`; Claude's cache-specific forced refresh is the deliberate
  exception that copies each edited or unproven payload file to the installer
  backup directory, reports that path, and only then replaces the cache file.
  The other thing `--force` widens is a tree whose marker predates file hashes
  (`files: []`): without hashes an edit
  and a stale copy are indistinguishable, so a plain install or uninstall keeps
  it and says so, and `--force` republishes it (source still shipped) or
  retires it (route no longer shipped, or `--uninstall`) after the current
  tree is copied to `~/.autorun/installer/backups/` by `fs._park_backup`
  (never beside the skill — a harness would list the backup as a skill).
- **Shared dirs are owned per file.** For `commands/` use
  `publish_files`/`withdraw_files`. `publish_tree` there either refuses or
  deletes the user's files.
- **A colliding shipped file is backed up, not skipped.** The shipped set must
  be complete or the package is broken: skipping the user's `ar-go.md` leaves
  `/ar:go` absent while the install reports success. Their content moves to
  `ar-go.md.autorun-backup`, numbered when backups stack, and is named in the
  decision so the caller can report it. A file that matches what we recorded is
  replaced with no backup. Pass `backup=False` where a fallback exists, as a
  blocked skill still has its harness's native route.
- **Stage skills by name, never by directory.** Copying a whole `skills/` into an
  extension gave shared-root harnesses two unmarked, unprunable copies.
- **Markers say `ar` everywhere.** A tree recorded as `autorun` was unremovable
  under `ar`, which leaked 362 files.
- **A step names only today's paths.** The sweep (`traversal.retirements`)
  visits selected harnesses' config roots, so a retired route *inside* one is
  found by its marker. A retired route *outside* every root is not: declare
  it in `Platform.retired_config_dirs` (Antigravity's
  `~/.gemini/antigravity-cli/`), or its trees leak forever carrying our
  marker and no decision names them.
- **Claude cache payloads use the ordinary hash receipt, but a cache-specific walk.**
  `<config>/plugins/cache/<market>/<plugin>/<version>/` carries `.autorun-owned`
  with only the hashes autorun wrote or proved, and remains outside the general
  publication sweep. A plain install compares every proven file with the exact
  installed form for this build, including placeholder substitution and hook
  command pinning in `hooks.json`, `plugin.json` and `settings.json`. Edited
  files keep their last recorded hash in the receipt and are never re-recorded;
  unproven files are never added. One cache-parent lock covers intent recording,
  selection, backup, replacement, runtime work, `hooks.json` publication and the
  final receipt. Its timeout is the resolved
  `cache_install_lock_timeout_seconds` setting (CLI, environment, config file,
  then 600 seconds). Its accepted range is `(0, 86400]` seconds; invalid values
  stop with the key, rejected value, and range. Waiting is printed by the
  installer, never by hook code.
  Before the first replacement, the marker gains `intent.files`; recovery
  accepts either the prior or intended hash, and the completed receipt drops
  `intent`. A markerless cache adopts each raw bundled file whose hash matches;
  non-matching files remain unproven. Forced refresh copies files detected as
  edited or unproven when selection runs to `~/.autorun/installer/backups/` and
  reports each path. A file changed after backup verification but before
  replacement can still be replaced without a backup of that last change.
  Under an active interrupted-refresh intent, bytes equal to the previous
  autorun version are treated as untouched. The version root and `.venv` are
  never moved, renamed or deleted during install; Claude registration refresh
  uses `plugin update`/`enable`, never an uninstall/reinstall cycle. During a
  changed-build venv reinstall, a hook that hits the incomplete runtime denies
  tool-gate events (exit 2 for Claude PreToolUse) and allows lifecycle/context
  events with an `[autorun]` import error. Running sessions retain the same
  `.venv` path, so rc4 has no versioned-runtime switch. A matching runtime
  receipt skips reinstall only when its schema and non-editable mode match, the
  venv was not created or repaired in this run, and that venv's interpreter
  imports autorun from inside the venv. Changed installs replace `hooks.json`
  last after the runtime succeeds. Upgrades retain every old version because a
  running session may still invoke its captured hook command. Only explicit
  uninstall retires cache versions: it removes hash-proven unchanged payload
  and autorun metadata only from regular version directories, keeps edited and
  unproven files, and keeps `.venv` when those files leave the version tree in
  place. Symlinked and Windows reparse-point versions are reported without
  reading their marker or contents; a manifest path beneath a replaced linked
  directory is also kept and reported. It reports each kept path and uses the
  persistent cache `.autorun-install.lock` for install, refresh, and uninstall.
  The lock plus its plugin and market parents remain in place so waiters and new
  installers retain one lock authority. Cache proof, refresh
  and retirement share one metadata exclusion set for
  `.orphaned_at`, `.venv`, bytecode and file-manager files. Foreign same-name
  trees are left and reported; caches under another market or plugin are never
  visited.
- **Claude writes a link that leaves the plugin as its content.** Its own
  `plugin install`/`plugin update` keeps `CLAUDE.md -> AGENTS.md` and turns
  `src/pdf_extraction -> ../../pdf-extractor/src/pdf_extraction` into a
  directory of files. `claude._copy_payload` is the one owner of that form
  for the expected tree, adoption and the fallback fill. Expecting the link
  made a forced install fail with "directory blocks file", and no sandbox
  with a stand-in `claude` reproduces it, because the stand-in copies nothing.
- **Never `uv tool uninstall` a name whose console script is also ours.** uv
  removes scripts by *name*, not by owner, and every uv tool shares one bin
  directory. Measured, with `autorun-ai` installed and providing all three:
  `uv tool uninstall autorun` printed "Uninstalled 3 executables: autorun,
  autorun-install, extract-pdfs", and `uv tool uninstall
  autorun-pdf-extractor` printed "Uninstalled 1 executable: extract-pdfs" —
  the second already shipped. Restoring them is not possible from a uv tool
  install: the only local path is `.../site-packages/autorun`, the import
  package, which `uv tool install` refuses. So
  `entrypoint._retire_legacy_distributions` keeps such a distribution and
  reports it with the command to reclaim the disk. The pip route keeps one
  too when its receipt is in this interpreter (`_pip_package_owned`): pip in
  the same environment shares the bin directory and removes scripts by name
  the same way.
- **The installer runs before its dependencies exist.** `hooks/hook_entry.py` is
  stdlib-only; on `ImportError` it spawns an install of *this source* — the
  plugin directory when it has a `pyproject.toml`, else `BOOTSTRAP_SOURCE`, the
  git URL — followed by `python -m autorun --install`, and the next hook finds
  the deps. It never installs a bare distribution name. So a missing
  dependency must surface as an `ImportError` at import: any other exception is
  caught by `run_fallback`'s `except Exception`, which fails open and never
  bootstraps, leaving autorun permanently uninstalled. `filelock` in `fs` is the
  package's only third-party import, and `config.py` is stdlib-only, which is
  what makes `settings.autorun_config()` safe to call mid-install. Adding a
  dependency, or a module-scope call that raises anything else, breaks this.
- **Read routes and write routes differ.** Antigravity reads
  `~/.gemini/config/skills` and writes its plugins dir. ForgeCode reads both
  `~/.agents/skills` and `<base>/skills`; autorun writes only the latter by
  default. The bridge targets read routes.
- **Never create a directory a harness uses to pick its base.** ForgeCode's
  base is `~/forge` when it exists, else `~/.forge`; every ForgeCode route is
  under `discovery.config_dir`, so an install into `~/.forge` cannot create
  `~/forge` and switch ForgeCode away from the user's configuration.
- **A native copy the harness made is proven ours by receipt plus content,
  never by name.** Gemini and Qwen record the source path in
  `.<cli>-extension-install.json`. Agy records only `{"name": "ar", "source":
  "antigravity"}` in `~/.gemini/config/import_manifest.json`, so for Agy the
  content half is an exact match with today's source, or every hook command
  running our `hook_entry.py` (`extension.bundle_hooks_are_ours`), or a copied
  skill carrying our marker (hookless pdf-extractor). Exact match alone holds
  only until the source next changes; requiring it left Agy on its first
  bundle forever, silently. A same-name plugin that fails the proof is the
  user's and the skip is a `FAIL` outcome naming the path.
- **A root `gemini-extension.json` is a template.** `steps.extension_template`
  is the one owner: `gemini_template/` when present, else the plugin directory
  when the manifest sits at its root (pdf-extractor), else no extension.

## Isolation

The rule, the sandbox recipe, and the proof are in the root `AGENTS.md` and
[`docs/RUNTIME_STATE_ISOLATION.md`](../../../docs/RUNTIME_STATE_ISOLATION.md).
Installer specifics:

- `HOME` is the seam; `Context.home` must **agree** with `$HOME` and is checked.
  Setting the field alone reads a sandbox and writes the real home — that
  uninstalled 16 live skills during a self-check that looked isolated. Use
  `discovery.redirected_home(path)` in any demo or test that needs a home.
- Redirecting `$HOME` does not move the daemon: `ipc.AUTORUN_CONFIG_DIR` is
  fixed at import. An in-process self-check that later runs `uninstall()` with
  teardown on signals the developer's live daemon (`orchestrate.demo` did).
  Set `AUTORUN_HOME` before the first import, or stand in for
  `teardown.stop_daemon` as `orchestrate._exercise` and `teardown.demo` do.
- `AUTORUN_HOME` must be short: the daemon socket lives under it, `sun_path` is
  104 bytes on macOS, and overflow looks like a hook timeout.

## Checks

Every module self-checks, from the repository root:
`uv run --project plugins/autorun python -c "from autorun.installer.fs import demo; demo()"`.
Named tests live in `plugins/autorun/tests/test_install_*.py`.

`test_install_gotchas.py::test_every_installer_module_self_check_passes` runs
that command for each module, discovering them rather than listing them, so a
new module's `demo()` is covered the moment it exists. Until it was added the
claim above was unverified and `registration.demo()` had been failing for eight
days: it asserted every `REGISTRATIONS` key was a bare harness name, which
stopped being true when the `codex:<marketplace>` variants arrived.
