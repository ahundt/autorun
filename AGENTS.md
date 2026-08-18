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
3. **Prove isolation** by snapshotting the live trees before and after; a
   sandboxed hook that says `autorun CLI timed out` usually means the socket
   path is too long.

```bash
SB=/tmp/arsb; mkdir -p "$SB/home" "$SB/ar-home" "$SB/state"
env HOME="$SB/home" USERPROFILE="$SB/home" PI_CODING_AGENT_DIR="$SB/home/.pi/agent" \
    AUTORUN_HOME="$SB/ar-home" AUTORUN_TEST_STATE_DIR="$SB/state" \
    UV_CACHE_DIR="$(uv cache dir)" \
    uv run --project plugins/autorun python -m autorun --install --force
```

Details, Docker recipe, socket-length trap, snapshot recipe:
[`plugins/autorun/docs/RUNTIME_STATE_ISOLATION.md`](plugins/autorun/docs/RUNTIME_STATE_ISOLATION.md);
installer-specific traps: [`plugins/autorun/src/autorun/installer/AGENTS.md`](plugins/autorun/src/autorun/installer/AGENTS.md).

### Operational one-liners

- **Configure an isolated run:** `SB=$(mktemp -d /tmp/arsb.XXXXXX) && mkdir -p "$SB/home" "$SB/ar-home" "$SB/state" "$SB/uv-cache" && env HOME="$SB/home" USERPROFILE="$SB/home" PI_CODING_AGENT_DIR="$SB/home/.pi/agent" AUTORUN_HOME="$SB/ar-home" AUTORUN_TEST_STATE_DIR="$SB/state" UV_CACHE_DIR="$SB/uv-cache"`
- **Launch the checkout CLI:** `uv run --project plugins/autorun python -m autorun --status`; restart only its daemon with `uv run --project plugins/autorun python -m autorun --restart-daemon`.
- **Install a local development CLI:** `uv tool install --force --editable plugins/autorun && autorun --install`; the editable install is for the developer CLI/daemon, not the live hook venv.
- **Install a published release:** `uv tool install --force autorun && autorun --install` (use the git or marketplace commands below when the release is not on PyPI).
- **Repair the live Claude cache after refreshing assets:** `CACHE="$HOME/.claude/plugins/cache/autorun/ar/1.0.0rc1" && uv run --project plugins/autorun python -m autorun --install --force && uv venv --clear --python 3.13 "$CACHE/.venv" && uv pip install --python "$CACHE/.venv/bin/python" --reinstall "$PWD/plugins/autorun" && uv run --project plugins/autorun python -m autorun --restart-daemon`. Use this only after explicit current-turn approval; refresh first, repair the cache venv second, and restart last.
- **Cache invariant:** the hook interpreter must import from `"$CACHE/.venv/lib/python*/site-packages"`; install it with `uv pip install --python "$CACHE/.venv/bin/python" --reinstall ...`, never `--editable`, so checkout edits cannot change the live hook between repairs.

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

UV workspace with two harness plugins: **autorun** (`/ar:` — autonomous
execution with three-stage verification, file policies, safety guards, task
tracking, plan export) and **pdf-extractor** (`/pdf-extractor:extract`,
`extract-pdfs`). `gemini` in code and docs means the Qwen Code / Antigravity
family; standalone Gemini CLI is retired but installable with `--gemini`.

| Path | What |
|------|------|
| `plugins/autorun/src/autorun/` | Package: `config.py` (all CONFIG), `__main__.py` (CLI + hook routing), `plugins.py` (command handlers), `core.py`/`session_manager.py` (daemon state), `installer/` (install walk), `task_lifecycle.py`, `plan_export.py`, `cache_guard.py`, `integrations.py` |
| `plugins/autorun/{commands,skills,agents,hooks}/` | Slash commands, skills, tmux agents, `hooks/hook_entry.py` + `hooks.json` |
| `plugins/autorun/AGENTS.md` | Plugin development guidance: hook error prevention, feature lessons, bug-workaround policy, harness families |
| `plugins/pdf-extractor/` | Manifests, command, skill, `CLAUDE.md` (full docs), and `src/pdf_extraction` (symlinked into `plugins/autorun/src/` so it ships in the `autorun` distribution; backends beyond `pdftotext` need the `pdf` extra) |
| `README.md` | User documentation: installation, every `/ar:` command, three-stage markers, safety-guard defaults, tmux integration, troubleshooting |

`/ar:help` lists every command in the current harness's spelling. Stage
markers: `AUTORUN_INITIAL_TASKS_COMPLETED` →
`CRITICALLY_EVALUATING_PREVIOUS_WORK_AND_CONTINUING_TASKS_AS_NEEDED` →
`AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY` (`config.py`).

## 4. Testing

```bash
uv run --project plugins/autorun pytest plugins/autorun/tests/test_unit_simple.py -q   # quick
uv run --project plugins/autorun pytest plugins/autorun/tests/ -q                       # full
```

Paid model calls are off unless you export
`AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1`, and every such test carries the
`real_money` marker, so which ones they are is a query rather than a claim:

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
uv tool install --force autorun && autorun --install                                      # PyPI release
uv tool install --force 'git+https://github.com/ahundt/autorun.git#subdirectory=plugins/autorun' && autorun --install   # git
git clone https://github.com/ahundt/autorun.git && cd autorun && uv tool install --force --editable plugins/autorun && autorun --install   # local clone
```

Claude Code alone can instead use the marketplace: `claude plugin marketplace
add https://github.com/ahundt/autorun.git && claude plugin install ar@autorun`.
Full options, extras (`autorun[pdf]`), and verification: `README.md`. Inside a
development session these commands fall under section 1.
