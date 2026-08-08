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
  the files. No force path may ignore it.
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
- **A step knows only today's paths.** Retiring a route means adding it to
  `traversal.retirements`, or its trees leak forever carrying our marker.
- **Versioned harness caches belong to the harness.**
  `<config>/plugins/cache/<market>/<plugin>/<version>/` may copy an ownership
  marker from the registered source tree. The retirement sweep ignores that
  path: deleting it would remove state Claude or Codex still tracks. Claude's
  fallback cache writer proves ownership by path and deliberately writes no
  marker.
- **The installer runs before its dependencies exist.** `hooks/hook_entry.py` is
  stdlib-only; on `ImportError` it spawns `uv pip install autorun && autorun
  --install` in the background and the next hook finds the deps. So a missing
  dependency must surface as an `ImportError` at import: any other exception is
  caught by `run_fallback`'s `except Exception`, which fails open and never
  bootstraps, leaving autorun permanently uninstalled. `filelock` in `fs` is the
  package's only third-party import, and `config.py` is stdlib-only, which is
  what makes `settings.autorun_config()` safe to call mid-install. Adding a
  dependency, or a module-scope call that raises anything else, breaks this.
- **Read routes and write routes differ.** Antigravity reads
  `~/.gemini/config/skills` and writes its plugins dir; ForgeCode reads
  `~/forge/skills` and writes nowhere. The bridge targets read routes.

## Isolation

Never install against your own machine. It rewrites `hooks.json`, replaces
plugin caches, and restarts the daemon.

Unit tests set `AUTORUN_HOME` and `AUTORUN_TEST_STATE_DIR` **before any autorun
import**. A real install also needs `HOME`:

```bash
SB=/tmp/arsb; mkdir -p "$SB/home" "$SB/ar-home" "$SB/state"
env HOME="$SB/home" AUTORUN_HOME="$SB/ar-home" AUTORUN_TEST_STATE_DIR="$SB/state" \
    UV_CACHE_DIR="$(uv cache dir)" \
    uv run --project plugins/autorun python -m autorun --install --force
```

`HOME` is the seam. `Path.home()` honours it, so redirecting it moves every path
together. `Context.home` must **agree** with it and is checked: setting the
field while leaving `$HOME` alone reads a sandbox and writes the real home, and
that uninstalled 16 skills from a live machine during a self-check that looked
isolated. Use `discovery.redirected_home(path)` — a context manager — in any
demo or test that needs an isolated home. `AUTORUN_HOME` must be short, because the
daemon socket lives under it and `sun_path` is 104 bytes on macOS; overflow
looks like a hook timeout. Prove isolation by diffing a full listing rather than
a digest, since these trees also hold harness session logs.

Details: [`docs/RUNTIME_STATE_ISOLATION.md`](../../../docs/RUNTIME_STATE_ISOLATION.md).

## Checks

Every module self-checks:
`uv run python -c "from autorun.installer.fs import demo; demo()"`.
Named tests live in `plugins/autorun/tests/test_install_*.py`.
