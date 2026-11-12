# autorun Marketplace

`CLAUDE.md` and `GEMINI.md` here are symlinks to this file. Edit `AGENTS.md`.

## 1. Development isolation is MANDATORY

Many harness sessions run on this machine at once and share the live daemon,
`~/.autorun`, and the installed trees (`~/.agents`, `~/.claude`, `~/.codex`,
`~/.gemini`, `~/.qwen`, `~/.pi`, `~/.prime`, `~/.config/opencode`). A live
install or daemon restart reaches all of them; one such install left other
sessions looping and burned ~12% of a week's tokens (2026-08-15).

1. **Every install, uninstall, dry run, status probe, self-check, test, and
   dogfood run happens in a sandbox** (`HOME`/`USERPROFILE`, `AUTORUN_HOME`,
   `AUTORUN_TEST_STATE_DIR` redirected to a short path) or in Docker.
   `pytest` isolates itself via `plugins/autorun/conftest.py`; nothing else does.
   Keeping the sandbox working is part of every change.
2. **NEVER touch the live installation without the user's explicit written
   instruction in the current conversation naming the action**: `autorun
   --install`/`--uninstall`/`--restart-daemon`/`--restart-all-daemons`,
   `claude plugin install|update`, `uv tool install` of autorun, or any hand
   edit, link, or deletion under the live config directories. Your own task
   list, an `/ar:ok` grant for something else, or "to verify the fix" is not
   that instruction. Report what a sandboxed `--install-dry-run` shows instead.
3. **Prove isolation** by snapshotting the live trees before and after. If a
   sandboxed hook says `autorun CLI timed out`, check the socket path length
   first.

```bash
SB=$(mktemp -d /tmp/arsb.XXXXXX); mkdir -p "$SB/home" "$SB/ar-home" "$SB/state"   # short and unique per run
env -i PATH="$PATH" HOME="$SB/home" USERPROFILE="$SB/home" PI_CODING_AGENT_DIR="$SB/home/.pi/agent" \
    AUTORUN_HOME="$SB/ar-home" AUTORUN_TEST_STATE_DIR="$SB/state" \
    UV_CACHE_DIR="$(uv cache dir)" \
    uv run --project plugins/autorun python -m autorun --install --force
```

Details, Docker recipe, socket-length trap, snapshot recipe:
[`plugins/autorun/docs/RUNTIME_STATE_ISOLATION.md`](plugins/autorun/docs/RUNTIME_STATE_ISOLATION.md);
installer-specific traps: [`plugins/autorun/src/autorun/installer/AGENTS.md`](plugins/autorun/src/autorun/installer/AGENTS.md).

### Operational one-liners

Each one-liner below acts on the live machine unless it follows this prefix.

- **Isolated-run prefix** (append the command): `SB=$(mktemp -d /tmp/arsb.XXXXXX) && mkdir -p "$SB/home" "$SB/ar-home" "$SB/state" && env -i PATH="$PATH" HOME="$SB/home" USERPROFILE="$SB/home" PI_CODING_AGENT_DIR="$SB/home/.pi/agent" AUTORUN_HOME="$SB/ar-home" AUTORUN_TEST_STATE_DIR="$SB/state" UV_CACHE_DIR="$(uv cache dir)"`
- **Launch the checkout CLI:** `uv run --project plugins/autorun python -m autorun --status`; restart only its daemon with `uv run --project plugins/autorun python -m autorun --restart-daemon`.
- **Install a local development CLI:** `uv tool install --force --editable plugins/autorun && autorun --install`; the editable install is for the developer CLI/daemon, not the live hook venv.
- **Install a published release:** `uv tool install --force autorun-ai && autorun --install` (use the git or marketplace commands below when the release is not on PyPI). The distribution is `autorun-ai`; the command, the import package and the marketplace plugin are all still `autorun`.
- **Repair the live Claude cache after refreshing assets:** `CACHE="$HOME/.claude/plugins/cache/autorun/ar/1.0.0rc4" && uv run --project plugins/autorun python -m autorun --install --force && "$CACHE/.venv/bin/python" -I -c 'import autorun,pathlib,sys; assert pathlib.Path(sys.prefix).resolve() in pathlib.Path(autorun.__file__).resolve().parents' && uv run --project plugins/autorun python -m autorun --restart-daemon`. Use this only after explicit current-turn approval. The install refreshes cache assets in place, backs up and reports edited files, reinstalls the checkout non-editably into the existing cache venv, and normally restarts the daemon; the assertion verifies that private runtime; the explicit restart is retained as a visible retry after verification. Do not clear or replace `.venv`: running sessions retain its interpreter path. Hook manifest changes still require a new Claude session.
- **Cache invariant:** the hook interpreter imports from `"$CACHE/.venv/lib/python*/site-packages"`; `autorun --install --force` reinstalls that snapshot non-editably. Never use `--editable`, so later checkout edits cannot change a running hook between repairs.

## 2. Rules that hold everywhere

- Tests set `AUTORUN_HOME` and `AUTORUN_TEST_STATE_DIR` before any autorun
  import; they never touch the live daemon socket, PID, locks, logs, or history.
- Hook code never prints outside CLI entry points: stdout is the hook response,
  and any stderr disables every hook. Log via `logging_utils.get_logger()`.
- Daemon paths use `EventContext.state_get/state_set/state_update`; wrap
  legacy persistence in `state_synchronize`.
- Never hide persistent-state I/O or lock failures by raising hook timeouts,
  and never weaken a concurrency, protocol, or isolation assertion to make a
  test pass.
- Commits follow `plugins/autorun/skills/commit/SKILL.md` (`<files>:` or
  `type(scope):` subject; previous behavior, exact changes, why, verification).
  Read the full staged diff before every commit.

## 3. What is here

**Three names, and no fourth.** `autorun` is the console script, the import
package, the marketplace, the repo and `~/.autorun/`. `ar` is the plugin id and
therefore the `/ar:` prefix — deliberately short so it types fast, namespaced by
the marketplace and never by PyPI. `autorun-ai` is **only** the PyPI
distribution, and only because PyPI prohibits the bare name ("This project name
isn't allowed"). Write `autorun-ai` just where a *distribution* is named:
install commands, `[project] name`, `[tool.uv.sources]` keys, dependency
requirements, `uv build --package`, and `importlib.metadata.version(...)`; a miss
in the last four fails silently.

The wheel is `autorun_ai-*.whl`, but that is **not** a fourth name to write:
nothing declares it, PEP 427 just normalises `-` to `_`. Derive it
(`DISTRIBUTION.replace("-", "_")`). `test_each_autorun_spelling_keeps_its_own_job`
pins all of the above, including that no module spells the stem out and that the
plugin id stays `ar`.

Three harness plugins, one uv workspace member (`plugins/autorun`): **autorun**
(`/ar:` — autonomous execution with three-stage verification, file policies,
safety guards, task tracking, plan export, and the skills those commands use),
**autorun-skills** (eleven general-purpose skills that need nothing from
autorun; skills only, no code) and **pdf-extractor**
(`/pdf-extractor:extract`, `extract-pdfs`), whose code ships inside
`autorun-ai`. `autorun-skills` and `pdf-extractor` are plugin ids, named on
purpose, not spellings of `autorun`; neither is a distribution.
`autorun --install` installs all three. In the registry `gemini` is the standalone Gemini CLI, retired
but installable with `--gemini`; `qwen` and `antigravity` are separate entries
built from the same extension template.

| Path | What |
|------|------|
| `plugins/autorun/src/autorun/` | Package: `config.py` (all CONFIG), `__main__.py` (CLI + hook routing), `plugins.py` (command handlers), `core.py`/`session_manager.py` (daemon state), `installer/` (install walk), `task_lifecycle.py`, `plan_export.py`, `cache_guard.py`, `integrations.py` |
| `plugins/autorun/{commands,skills,agents,hooks}/` | Slash commands, skills, tmux agents, `hooks/hook_entry.py` + `hooks.json` |
| `plugins/autorun/AGENTS.md` | Plugin development guidance: hook error prevention, feature lessons, bug-workaround policy, harness families |
| `plugins/autorun-skills/` | Manifests for every harness family, `README.md`, and `skills/`: the general-purpose skills. An install hands any copy `ar` wrote under an earlier release to this plugin (`installer/skills.heirs`) |
| `plugins/pdf-extractor/` | Manifests, command, skill, `CLAUDE.md` (full docs), and `src/pdf_extraction` (symlinked into `plugins/autorun/src/` so it ships in the `autorun-ai` distribution; backends beyond `pdftotext` need the `pdf` extra) |
| `README.md` | User documentation: installation, every `/ar:` command, three-stage markers, safety-guard defaults, tmux integration, troubleshooting |

`/ar:help` lists every command in the current harness's spelling. Each
command has one name, one registration and one document in `commands/`; a
name autorun used to advertise keeps working only through
`core._RETIRED_COMMAND_SPELLINGS` (input tolerance), and nothing autorun
prints or ships may name it (`tests/test_command_surface_one_name.py`). Stage
markers: `AUTORUN_INITIAL_TASKS_COMPLETED` →
`CRITICALLY_EVALUATING_PREVIOUS_WORK_AND_CONTINUING_TASKS_AS_NEEDED` →
`AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY` (`config.py`).

## 4. Testing

Needs uv and Python 3.10 or later.

```bash
uv sync --project plugins/autorun --locked --dev --all-extras                          # CI's test environment, every extra included
uv run --project plugins/autorun pytest plugins/autorun/tests/test_unit_simple.py -q   # quick
uv run --project plugins/autorun pytest plugins/autorun/tests/ -q                       # full
uvx ruff check --select E9,F63,F7,F82 .                                                 # the lint CI runs
```

Paid model calls are off unless you export
`AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1`, and every such test carries the
`real_money` marker, so these queries list them:

```bash
uv run --project plugins/autorun pytest plugins/autorun/tests/ -m real_money --collect-only -q   # list what would cost money
uv run --project plugins/autorun pytest plugins/autorun/tests/ -m "not real_money" -q            # run with none of it collected
```

A module name proves nothing here: most tests in `test_*_e2e_real_money.py`
are free hook subprocesses, and paid tests also live in modules whose names
say nothing about cost. `tests/e2e_support.py:requires_real_money` is the only
gate; `tests/test_real_money_gate.py` fails if a second copy appears.

## 5. Installing for real (end users)

`autorun` is one published distribution; `autorun --install` then publishes
every detected harness's native assets. In priority order:

```bash
uv tool install --force autorun-ai && autorun --install                                   # PyPI release
uv tool install --force 'git+https://github.com/ahundt/autorun.git#subdirectory=plugins/autorun' && autorun --install   # git
git clone https://github.com/ahundt/autorun.git && cd autorun && uv tool install --force --editable plugins/autorun && autorun --install   # local clone
```

Claude Code alone can instead use the marketplace: `claude plugin marketplace
add https://github.com/ahundt/autorun.git && claude plugin install ar@autorun && claude plugin install autorun-skills@autorun`.
Full options, extras (`autorun-ai[pdf]`), and verification: `README.md`. Inside a
development session these commands fall under section 1.
