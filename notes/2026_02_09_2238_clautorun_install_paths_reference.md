# Clautorun Install Paths Reference

Generated: 2026-02-09 23:30
Purpose: Document all locations where clautorun code exists so they can be audited for staleness after code changes.

## All Known Code Locations

### 1. Dev Repo Source (SINGLE SOURCE OF TRUTH)

```
/Users/athundt/.claude/clautorun/plugins/clautorun/src/clautorun/
```

- **Created by:** `git clone https://github.com/ahundt/clautorun.git`
- **Key files:** `core.py`, `main.py`, `__init__.py`, `__main__.py`, `daemon.py`, `client.py`, `install.py`, `config.py`, `plugins.py`, `integrations.py`, `task_lifecycle.py`
- **This is the authoritative source.** All other locations should reference or be derived from this.

### 2. Dev Repo Venv (site-packages)

```
/Users/athundt/.claude/clautorun/plugins/clautorun/.venv/lib/python3.12/site-packages/clautorun/
```

- **Created by:** `uv sync` or `uv pip install -e .` from dev repo root
- **Install type:** Should be editable (PEP 660) — check for `direct_url.json` in dist-info
- **Behavior:** If editable, `.py` file changes in source reflect immediately. Entry point/metadata changes require reinstall.
- **CLI to verify:** `uv pip list --python .venv/bin/python3 | grep clautorun`
- **CLI to refresh:** `uv sync --reinstall` or `uv pip install -e .`

### 3. UV Global Tool Install

```
/Users/athundt/.local/share/uv/tools/clautorun/lib/python3.12/site-packages/clautorun/
```

- **Binary at:** `/Users/athundt/.local/bin/clautorun`
- **Created by:** `uv tool install .` (from install.py:1082 or daemon.py:_install_clautorun)
- **Code pathway:** `daemon.py:_install_clautorun()` line 140-143 runs `uv tool install --force <plugin_root>`
- **Known bug:** `uv tool install --force` does NOT invalidate directory cache (uv#9492). Old code runs despite file timestamp change.
- **Best practice fix:** Use `uv tool install --editable .` for dev, or `uv tool install --reinstall .` for production.
- **CLI to verify:** `/Users/athundt/.local/bin/clautorun --version` or `python3 -c "import clautorun; print(clautorun.__file__)"` using the tool's python
- **CLI to refresh:** `uv tool install --reinstall /Users/athundt/.claude/clautorun/plugins/clautorun`

### 4. Gemini Extension - Source Copy

```
/Users/athundt/.gemini/extensions/clautorun-workspace/plugins/clautorun/src/clautorun/
```

- **Created by:** `gemini extensions install /path/to/clautorun` or `gemini extensions install https://github.com/ahundt/clautorun.git`
- **Code pathway:** `install.py:install_gemini()` around line 609-654 runs `gemini extensions install <workspace_root> --consent`
- **Behavior:** Gemini CLI COPIES the extension directory. This is a separate clone, NOT a symlink.
- **CLI to verify:** `gemini extensions list`
- **CLI to refresh:** `gemini extensions update clautorun-workspace` or re-run install

### 5. Gemini Extension - Plugin Venv (site-packages)

```
/Users/athundt/.gemini/extensions/clautorun-workspace/plugins/clautorun/.venv/lib/python3.12/site-packages/clautorun/
```

- **Created by:** `uv sync` inside the Gemini extension's plugin directory
- **Code pathway:** Created during extension install or first `uv sync` in that directory
- **Behavior:** This venv is INSIDE the Gemini extension copy. It may be editable (pointing to the extension's source copy) or a regular install.
- **CLI to verify:** Check `direct_url.json` in dist-info directory
- **CLI to refresh:** `cd /Users/athundt/.gemini/extensions/clautorun-workspace/plugins/clautorun && uv sync --reinstall`

### 6. Gemini Extension - Workspace Root Venv (site-packages)

```
/Users/athundt/.gemini/extensions/clautorun-workspace/.venv/lib/python3.12/site-packages/clautorun/
```

- **Created by:** `uv sync` at workspace root level
- **Code pathway:** Created during extension install
- **Behavior:** Workspace-level venv. May have different version than nested plugin venv.
- **CLI to refresh:** `cd /Users/athundt/.gemini/extensions/clautorun-workspace && uv sync --reinstall`

### 7. Gemini Extension - Build Artifacts

```
/Users/athundt/.gemini/extensions/clautorun-workspace/plugins/clautorun/build/lib/clautorun/
```

- **Created by:** `python setup.py build` or `uv build` or stale setuptools build
- **Code pathway:** Automatic during certain install operations
- **Behavior:** STALE - these are build artifacts that should be cleaned. Python may import from here if it appears earlier in sys.path.
- **CLI to clean:** `rm -rf /Users/athundt/.gemini/extensions/clautorun-workspace/plugins/clautorun/build/`
- **Should be in .gitignore:** Yes

### 8. Claude Code Plugin Cache

```
~/.claude/plugins/cache/clautorun/clautorun/<version>/
```

- **Created by:** `claude plugin install https://github.com/ahundt/clautorun.git`
- **Code pathway:** Claude Code's internal plugin manager copies repo to cache
- **Known bug:** After update, `CLAUDE_PLUGIN_ROOT` may resolve to old cached version (claude-code#15642)
- **Behavior:** READ-ONLY copy. Changes here don't persist across plugin updates.
- **CLI to verify:** `claude plugin list`
- **CLI to refresh:** `claude plugin update clautorun`

### 9. pip --user Install (if used)

```
~/.local/lib/python3.12/site-packages/clautorun/
```

- **Created by:** `pip install --user clautorun` or `pip3 install --user -e .`
- **Code pathway:** `daemon.py:_get_pip_command()` line 53-63 uses `pip3 install --user`
- **Behavior:** User-level install. May shadow or be shadowed by venv installs.
- **CLI to verify:** `pip3 show clautorun`
- **CLI to refresh:** `pip3 install --user --upgrade clautorun`

## Code Pathways That Create These Locations

### daemon.py Bootstrap (Background Thread)

File: `plugins/clautorun/src/clautorun/daemon.py`

```
_bootstrap_optional_deps() → runs in background thread
├── _ensure_uv()                    → pip3 install --user uv
├── _install_clautorun()            → uv tool install --force <plugin_root>  [Creates Location #3]
├── _install_bashlex()              → uv pip install -q bashlex
└── _install_pdf_deps()             → uv pip install --python <exe> <deps>
```

### install.py Main Installer

File: `plugins/clautorun/src/clautorun/install.py`

```
main() with --install flag
├── install_claude()                → Registers with Claude Code plugin system  [Creates Location #8]
├── install_gemini()                → gemini extensions install <root>          [Creates Locations #4, #5, #6]
└── install_uv_tool()               → uv tool install --force .                [Creates Location #3]
```

### client.py Daemon Auto-Start

File: `plugins/clautorun/src/clautorun/client.py`

```
run_client() → forward()
└── On ConnectionRefusedError:
    └── Popen([sys.executable, "-c", daemon_code])    → Starts daemon which runs bootstrap
        └── daemon.py:main() → _bootstrap_optional_deps()  [May create Location #3]
```

### hook_entry.py Binary Resolution

File: `plugins/clautorun/hooks/hook_entry.py`

```
Resolution order for finding clautorun binary:
1. <script_dir>/../.venv/bin/clautorun          → Location #2 or #5 venv binary
2. shutil.which("clautorun")                     → Location #3 global binary
3. Fallback: direct Python import from source    → Location #1 source
```

## Quick Audit Commands

```bash
# Find ALL clautorun core.py files on the system
find ~ -type f -name "core.py" 2>/dev/null | grep clautorun

# Check which version each location has (look for "decision" field in respond())
for f in \
  ~/.claude/clautorun/plugins/clautorun/src/clautorun/core.py \
  ~/.claude/clautorun/plugins/clautorun/.venv/lib/python3.12/site-packages/clautorun/core.py \
  ~/.local/share/uv/tools/clautorun/lib/python3.12/site-packages/clautorun/core.py \
  ~/.gemini/extensions/clautorun-workspace/plugins/clautorun/src/clautorun/core.py \
  ~/.gemini/extensions/clautorun-workspace/plugins/clautorun/.venv/lib/python3.12/site-packages/clautorun/core.py \
  ~/.gemini/extensions/clautorun-workspace/.venv/lib/python3.12/site-packages/clautorun/core.py \
  ~/.gemini/extensions/clautorun-workspace/plugins/clautorun/build/lib/clautorun/core.py; do
  if [ -f "$f" ]; then
    echo "=== $(stat -f '%Sm' "$f") | $(wc -c < "$f" | tr -d ' ') bytes | $f ==="
    grep -n '"decision"' "$f" | head -3
  fi
done

# Check install types (editable vs regular)
for venv in \
  ~/.claude/clautorun/plugins/clautorun/.venv \
  ~/.gemini/extensions/clautorun-workspace/plugins/clautorun/.venv \
  ~/.gemini/extensions/clautorun-workspace/.venv; do
  echo "=== $venv ==="
  cat "$venv"/lib/python3.12/site-packages/clautorun_*.dist-info/direct_url.json 2>/dev/null || echo "No direct_url.json (not editable)"
done

# Check running daemons
pgrep -fl "clautorun"

# Kill all daemons (forces reload on next hook)
pkill -f "clautorun.daemon"
```

## Gemini CLI Environment Variables (from official docs)

| Variable | Purpose |
|----------|---------|
| `GEMINI_CLI_HOME` | Root directory for Gemini CLI user-level config and storage (controls where extensions live) |
| `GEMINI_API_KEY` | API key for Gemini API |
| `GEMINI_MODEL` | Default Gemini model |
| `GEMINI_SANDBOX` | Sandbox mode (true/false/docker/podman) |
| `GEMINI_SYSTEM_MD` | Replace built-in system prompt with markdown file |
| `GEMINI_CLI_SYSTEM_DEFAULTS_PATH` | Override default system defaults file |
| `GEMINI_CLI_SYSTEM_SETTINGS_PATH` | Override system settings file |
| `DEBUG` / `DEBUG_MODE` | Verbose debug logging |
| `NO_COLOR` | Disable color output |

### Extension Path Variable (inside hooks/extensions)

`${extensionPath}` — Fully-qualified path of the extension in user's filesystem. **Does NOT unwrap symlinks** — meaning hooks using `${extensionPath}` work correctly through symlinks.

Source: https://geminicli.com/docs/extensions/reference/

## Best Practice: Eliminate Copies

### The `gemini extensions link` Command (KEY SOLUTION)

```bash
# INSTEAD OF: gemini extensions install /path/to/clautorun  (COPIES files)
# USE:        gemini extensions link /path/to/clautorun      (SYMLINKS)
```

`gemini extensions link <path>` creates a **symbolic link** instead of copying. Source changes reflect immediately. No syncing, no updating needed. This is the official Gemini CLI solution for local development.

Source: https://geminicli.com/docs/extensions/reference/

### The ideal state is:
1. **Source** at dev repo (Location #1) — single source of truth
2. **Dev venv** uses editable install (`uv pip install -e .`) — no copy needed
3. **UV global tool** uses `uv tool install --editable .` — no copy needed
4. **Gemini extension** uses `gemini extensions link` — **symlink, no copy**
5. **Daemon** killed and restarted after source changes (`/cr:restart-daemon`)
6. **Build artifacts** cleaned (`rm -rf build/`) and .gitignored
7. **Claude plugin cache** updated via `claude plugin update`

### Migration from Copy to Link

```bash
# Remove the current copied extension
gemini extensions uninstall clautorun-workspace

# Link instead (symlink to dev repo)
gemini extensions link /Users/athundt/.claude/clautorun

# Verify
gemini extensions list
ls -la ~/.gemini/extensions/clautorun-workspace  # Should show -> symlink
```

Sources:
- https://geminicli.com/docs/extensions/reference/
- https://geminicli.com/docs/get-started/configuration/
- https://github.com/google-gemini/gemini-cli/issues/4473
