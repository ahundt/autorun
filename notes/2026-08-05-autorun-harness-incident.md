# Autorun harness incident — 2026-08-05

## Status

- Root-cause fixes are implemented and the focused Codex installer suite passes (`58 passed`).
- Release metadata is installed as `1.0.0rc1` across all marketplace packages.
- Final forced reinstall completed across all configured harnesses; scoped daemon is healthy at PID `7943`.

## Observed symptoms

- The live tmux inventory contained four sessions: three `cmux-athundt-*` sessions and `main`; `main` had 50 windows and two attached clients.
- Autorun-backed Claude and Codex panes were active at `main:11.1` and `main:12.1`; other panes showed autorun-related Stop-hook output.
- Multiple panes reported skipped skills such as `ai-skill-builder`, `commit`, `philosophy`, `plannew`, `streamline-text`, `tabs`, and `tabw` because their cached directories had no `SKILL.md`.
- Codex skill discovery failed with: `parsing skill header "codebase-memory": yaml: line 2: mapping values are not allowed in this context`.
- One active pane repeatedly reported a session-local stale Stop task (`#19`); that state was not changed because it belongs to another active project/session.
- Historical pane evidence showed hook timeouts during machine load around `117.25 / 165.09 / 93.67` on 16 cores. This supports transient resource saturation as a timeout amplifier, not an autorun daemon source-path failure.

## Root causes

1. `~/.agents/skills/codebase-memory/SKILL.md` had an unquoted colon in YAML frontmatter (`Triggers on:`), which made the Codex skill checker reject the file and caused `aix skill list` to fail globally.
2. `plugins/autorun/skills/` contains asset-only and empty directories left by cross-harness skill sharing. `_collect_plugin_skill_sources()` filtered them for global installation, but `_copy_codex_plugin_source()` copied them into the Codex plugin bundle. Codex interpreted each directory as a skill and warned when `SKILL.md` was absent.
3. Existing `0.12.0` cache directories preserved the broken bundle until a new version was installed. The source copy at `/Users/athundt/plugins/autorun` confirmed the issue was reproducible before reinstall.
4. The stale Stop task is session-local enforcement state, not shared daemon corruption. It requires the owning session to complete, delete, or explicitly ignore the task.
5. AIX found duplicated `codebase-memory` copies in Claude, Agents, Codex, and OpenCode roots. All four copies had to be repaired because changing one root did not change the others.

## Changes

- Quoted the `codebase-memory` description so YAML frontmatter parses correctly.
- Changed Codex plugin source staging to omit only top-level `skills/<name>/` directories without a real `SKILL.md`; complete skills, references, and link-backed entrypoints remain copied and symlinks are still dereferenced.
- Added a regression test proving asset-only skill directories do not enter the Codex bundle.
- Repaired duplicated `codebase-memory` YAML headers and normalized the bundled `parallel-subagent` skill name to its directory name so strict skill validation succeeds.
- Bumped unified package/plugin metadata and maintained release docs to `1.0.0rc1`.

## Verification and prevention

- Run `aix skill validate <path> --strict --json` for every skill changed or relied upon; all four relevant skills passed after the frontmatter repair.
- Before reinstalling a Codex plugin, inspect both the source and cache with `find .../skills -name SKILL.md`; every top-level skill directory in the bundle must contain one.
- Use a new version directory for cache refreshes; do not diagnose an old cache as the current source until the installed version and source path match.
- Capture the affected tmux panes before sending keys or changing session state.
- Restart only the autorun daemon owned by the current source tree; broad all-daemon restarts can interrupt unrelated harnesses.
- Treat hook timeout spikes and daemon/path errors separately: check load and the autorun daemon logs before changing hook timeouts or concurrency.
- For a Stop block, preserve another project’s task state unless its owner confirms the task is obsolete; the supported Codex override is `ar:task-ignore <id>`.

## Final evidence

- Full autorun suite: `4632 passed, 81 skipped`.
- Version-sensitive install suite: `316 passed, 1 skipped`.
- `autorun --version`: `autorun 1.0.0rc1`.
- `autorun --status`: Claude, Gemini, Conductor, Antigravity, Qwen, Codex, and ForgeCode installed/enabled; Codex reports `7` plugin-source and `7` plugin-cache skills.
- New Codex cache: `/Users/athundt/.codex/plugins/cache/personal/autorun/1.0.0rc1/`; all seven top-level skill directories contain `SKILL.md`.
- Strict AIX validation passes for all seven top-level skills in that final Codex cache, including the linked `parallel-subagent` entrypoint.
- Claude hook cache: `/Users/athundt/.claude/plugins/cache/autorun/ar/1.0.0rc1/hooks/hooks.json`; all commands use resolved absolute paths and contain no `${CLAUDE_PLUGIN_ROOT}` placeholder.
- Hook smoke tests: Claude returned one parseable deny JSON object on stdout with exit `2` and denial text on stderr; Codex returned one parseable allow JSON object on stdout with exit `0` for a piped `git status | sed` payload.
- AIX cross-platform skill inventory: `OK`; OpenCode now reports one valid skill and the repaired copies validate in strict mode.
- The current pane history still contains the earlier Codex parse error and the separate `main:28.1` stale Stop-task error; fresh post-install output has no shared missing-skill or hook-path error. The stale task was left intact to avoid changing another active project’s task state.
- AIX doctor still reports one unrelated file-permission warning for `/Users/athundt/.config/opencode/opencode.json`; no permission change was made because it was outside the autorun failure path.

## Diagnostic limitation

The codebase-memory MCP transport closed during structural graph lookup, so source tracing used the repository’s permitted `rg`/file inspection fallback. Runtime evidence came from `processtree`, `tmux capture-pane`, autorun logs, and the focused test suite.

## Post-restart verification

- Fresh `autorun --version` reports `autorun 1.0.0rc1`; `autorun --status` reports Claude, Gemini/Conductor, Codex, Antigravity, Qwen, and ForgeCode surfaces installed as expected.
- The daemon is still PID `7943`, with the expected source path `/Users/athundt/.claude/autorun/plugins/autorun/src` and `bashlex` available.
- Live tmux inventory remains four sessions, with autorun-backed panes at `main:11.1` and `main:12.1`. The current Codex pane is on this verification tree. The existing Claude pane at `main:11.1` was started with the older `0.12.0` plugin cache and was left running; a new install cannot change an already-running process. Its captured tail showed completed tests and no new autorun failure.
- `autorun --help`, `autorun task --help`, `autorun task status --help`, `autorun task export --help`, and `autorun task gc --help` all render valid help. `autorun task status` without a session correctly returns `Error: No session ID provided and CLAUDE_SESSION_ID not set`; `autorun task help` is intentionally invalid because argparse exposes help through `--help`.
- `autorun --capability-snapshot -` reports version `1.0.0rc1`, task lifecycle handlers, cache/plan/tmux command surfaces, and seven autorun plugin skills. The seven installed Codex skill directories all contain `SKILL.md` and pass strict AIX validation.
- Isolated regression checks pass: `316 passed, 1 skipped` for installer/version/hook pathways and `358 passed` for task-lifecycle/SQLite tests. The one skip remains `No claude-hooks.json in cache`.
- The separate `main:28.1` Stop-hook block still belongs to another active project’s session-local task `#19`; it was not modified.
