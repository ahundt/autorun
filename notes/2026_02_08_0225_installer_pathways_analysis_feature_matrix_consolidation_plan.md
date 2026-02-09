---
session_id: 5972dfc0-1209-4441-8d1b-6a48ba9fe265
original_path: /Users/athundt/.claude/plans/modular-greeting-crab.md
export_timestamp: 2026-02-08T02:25:16.208493
export_destination: /Users/athundt/.claude/clautorun/notes/2026_02_08_0225_installer_pathways_analysis_feature_matrix_consolidation_plan.md
---

# Installer Pathways: Analysis, Feature Matrix & Consolidation Plan

## Context

Two installer implementations grew independently — a modern 315-line `install_plugins.py` and a legacy 1136-line `install.py`. Each has unique capabilities the other lacks. Goal: document everything, then propose a consolidated DRY modern superset.

---

## 1. Entry Points

`plugins/clautorun/pyproject.toml:55-59`:

| Entry Point | Function | Role |
|------------|----------|------|
| `clautorun` | `__main__:main` | 🔀 Multiplexer: hook handler (default) OR `--install` / `--status` |
| `clautorun-interactive` | `__main__:main` | 🔀 Alias — identical to `clautorun` |
| `clautorun-install` | `install:main` | 📦 Legacy installer (direct) |
| `claude-session-tools` | `claude_session_tools:main` | 🔍 Session history (unrelated) |

---

## 2. Pathway Map

### 📦 Modern Installer (`install_plugins.py` — 315 lines)

| Command | What It Does |
|---------|-------------|
| `clautorun --install` | Install + enable all plugins via marketplace |
| `clautorun --install clautorun,pdf-extractor` | Install specific plugins (comma-separated) |
| `clautorun --install --force-install` | Uninstall first, then reinstall |
| `clautorun --install --tool` | Also `uv tool install . --force` |
| `clautorun --status` | Show plugin enabled/disabled status |

### 📦 Legacy Installer (`install.py` — 1136 lines)

| Command | What It Does |
|---------|-------------|
| `clautorun-install` / `clautorun-install install` | Full install: UV check → deps → marketplace → cache fallback → UV tool |
| `clautorun-install install --force` | Remove existing first, then install |
| `clautorun-install install --marketplace` | Install all 3 plugins via `MarketplaceInstaller` |
| `clautorun-install uninstall` | Uninstall plugin + UV tools |
| `clautorun-install check` / `status` | Comprehensive status (UV env, tools in PATH, marketplace, commands) |
| `clautorun-install sync` | Dev workflow: copy source → cache |
| `clautorun install` / `uninstall` / `check` | Same (routed via `__main__.py:201`) |

### ⚙️ Bootstrap Config (standalone in `__main__.py`)

| Command | What It Does |
|---------|-------------|
| `clautorun --no-bootstrap` | Add `--no-bootstrap` flag to hooks.json commands |
| `clautorun --enable-bootstrap` | Remove `--no-bootstrap` flag from hooks.json commands |

### 🏃 Runtime (default, no install flags)

| Command | What It Does |
|---------|-------------|
| `clautorun` (no args) | Hook handler: daemon mode (`client.py`) or legacy (`main.py`) |

---

## 3. Feature Matrix

Legend: ✅ = has it | ❌ = missing | ⚠️ = partial | 🔧 = dev-only

### 🔌 Plugin Registration

| Capability | Modern (`install_plugins.py`) | Legacy (`install.py`) | Ideal (consolidated) |
|-----------|------|------|------|
| Marketplace add | ✅ Calls `claude plugin marketplace add` with `find_marketplace_root()` | ✅ Calls same command, but computes root via `self.marketplace_root` (hardcoded parent traversal) | ✅ Keep modern: `find_marketplace_root()` with `@lru_cache` is cleaner and testable |
| Plugin install | ✅ `claude plugin install <name>@clautorun` for each selected plugin | ✅ Same command, but only for clautorun (unless `--marketplace`) | ✅ Keep modern: loop over all selected plugins |
| Plugin enable | ✅ Calls `claude plugin enable` after each install — ensures plugin is active | ❌ Never calls enable — plugins may install but remain disabled | ✅ Must keep: without enable, installed plugins silently don't work |
| Try update first | ❌ Always does fresh install — wasteful if already installed | ✅ Tries `claude plugin update` first, falls back to install — faster for existing installs, preserves settings | ✅ Port from legacy: update-then-install is faster and preserves plugin config |
| Multi-plugin install | ✅ Default installs all plugins from `PluginName` enum; user can select subset via comma-separated list | ⚠️ Only installs clautorun by default; `--marketplace` flag triggers separate `MarketplaceInstaller` class (100 lines of duplication) | ✅ Keep modern: one code path handles both "all" and selective install |
| Plugin name validation | ✅ `PluginName(str, Enum)` rejects typos at parse time; IDE autocomplete works | ❌ Hardcoded string `"clautorun"` — typos silently fail | ✅ Keep modern: enum prevents entire class of bugs |
| Force reinstall | ✅ `--force-install` uninstalls first then reinstalls — reliable for version changes | ✅ `--force` removes from cache and plugin dir before installing | ✅ Both work; keep modern flag name, port cache cleanup from legacy |

### 🛡️ Fallback & Recovery

| Capability | Modern | Legacy | Ideal |
|-----------|------|------|------|
| Cache fallback | ❌ If `claude plugin install` fails, install fails — no fallback | ✅ Falls back to `install_to_cache()`: copies plugin to `~/.claude/plugins/cache/clautorun/clautorun/<version>/` — works even without `claude` CLI | ✅ Port from legacy: essential for environments where `claude plugin install` fails (CI, air-gapped systems, broken plugin system) |
| JSON registration | ❌ No fallback registration | ✅ Writes to `~/.claude/plugins/installed_plugins.json` with scope, version, timestamps — Claude Code discovers manually installed plugins via this file | ✅ Port from legacy: paired with cache fallback; without JSON registration, cache-installed plugins are invisible to Claude Code |
| Backup before overwrite | ❌ No backup — force reinstall destroys previous version | ✅ Creates `.backup` copy of existing cache version before overwriting — enables rollback if new version is broken | ✅ Port from legacy: safety net costs one `shutil.copytree` call |
| Path substitution | ❌ Assumes `${CLAUDE_PLUGIN_ROOT}` is resolved by Claude Code at runtime | ✅ Replaces `${CLAUDE_PLUGIN_ROOT}` with actual absolute path in `plugin.json` and `hooks.json` — required for cache-installed plugins where Claude Code doesn't do the substitution | ✅ Port from legacy: required for cache fallback path to work; Claude Code only substitutes for marketplace-installed plugins |

### 🔍 Environment Validation

| Capability | Modern | Legacy | Ideal |
|-----------|------|------|------|
| Check `claude` CLI | ✅ `shutil.which("claude")` — early exit with install URL if missing | ✅ `subprocess.run(["claude", "--version"])` — same purpose but heavier (spawns process) | ✅ Keep modern: `shutil.which` is cheaper and sufficient |
| Check UV available | ❌ Doesn't check — silently fails if UV needed later | ✅ Runs `uv --version`, reports version, warns if missing | ✅ Port from legacy: UV is needed for dep sync and tool install; better to check upfront than fail mid-install |
| Check project files | ❌ No validation of project structure | ✅ Checks `pyproject.toml` and `uv.lock` exist — catches common "forgot to clone" or "wrong directory" errors | ✅ Port from legacy: cheap sanity check that prevents confusing downstream errors |
| Check `.venv` | ⚠️ Only checked in `show_status()` if `CLAUDE_PLUGIN_ROOT` is set | ✅ Checks `.venv` exists in plugin dir — catches "forgot to run `uv sync`" | ✅ Port from legacy: early warning saves user from cryptic ImportErrors |
| Sync dependencies | ❌ Assumes deps are already installed — fails if they're not | ✅ Runs `uv sync --extra claude-code` to ensure all deps are current | ✅ Port from legacy: makes install self-contained rather than requiring manual `uv sync` first |
| Editable install | ❌ Not supported | ✅ 🔧 Runs `uv pip install -e .` — dev workflow where source edits take effect immediately without reinstall | ✅ 🔧 Port from legacy: essential for development, skip in production |
| Python version check | ❌ No check — crashes later with cryptic syntax errors on Python < 3.10 | ✅ 100+ lines including Python 2 compat messages — over-engineered but catches the problem | ✅ Simplified: one-liner `sys.version_info >= (3, 10)` with clear error message; delete Python 2 compat code |
| Detect Claude Code | ❌ Only checks for `claude` binary, not `~/.claude/` directory | ✅ Checks `~/.claude/` exists — distinguishes "Claude Code not installed" from "claude binary not in PATH" | ✅ Port from legacy: more precise error message helps users fix the right problem |

### 📋 Python Dependency Management

| Capability | Modern | Legacy | Ideal |
|-----------|------|------|------|
| clautorun deps | ❌ `claude plugin install` registers the plugin but doesn't install Python packages like `claude-agent-sdk` | ✅ `uv sync --extra claude-code` installs all deps declared in clautorun's `pyproject.toml` | ✅ Port from legacy: without Python deps installed, hook handler crashes on first `import` |
| pdf-extractor deps | ❌ Not attempted | ❌ Not attempted — legacy only syncs clautorun's deps | ✅ NEW: add `uv pip install pdfplumber pdfminer.six PyPDF2 markitdown tqdm` to installer + daemon bootstrap. Without this, `import pdf_extraction.backends` crashes (3 eager top-level imports) |
| `CmdResult` dataclass | ✅ Immutable `@dataclass(frozen=True, slots=True)` with `.has_text()` method — all subprocess calls return uniform result object | ❌ Raw `subprocess.run()` calls with ad-hoc `returncode` checks scattered across 20+ call sites | ✅ Keep modern: single return type eliminates inconsistent error handling patterns |
| Cached marketplace root | ✅ `@lru_cache find_marketplace_root()` searches upward for `.claude-plugin/marketplace.json` — called once, cached forever | ❌ `self.marketplace_root = self.package_dir.parent.parent` — hardcoded parent traversal that breaks if directory structure changes | ✅ Keep modern: marker-file search is resilient to directory structure changes |

### 🔧 UV Tool Management

| Capability | Modern | Legacy | Ideal |
|-----------|------|------|------|
| Install UV tool | ✅ Opt-in via `--tool` flag — `uv tool install . --force` makes `clautorun`, `clautorun-install` globally available | ✅ Always runs after plugin install — makes interactive mode work but adds ~10s to every install | ✅ Keep modern: opt-in is better; not every user needs global CLI tools |
| Uninstall UV tool | ❌ No uninstall capability | ✅ `uv tool uninstall clautorun` — clean removal, handles "not installed" gracefully | ✅ Port from legacy: uninstall must be symmetric with install |

### 🗑️ Uninstall

| Capability | Modern | Legacy | Ideal |
|-----------|------|------|------|
| Plugin uninstall | ⚠️ Only uninstalls during force-reinstall — no dedicated uninstall pathway | ✅ Dedicated `uninstall` action: tries `claude plugin uninstall`, reports result | ✅ Port from legacy: users need a way to cleanly remove the plugin |
| UV tool uninstall | ❌ Not supported | ✅ `uv tool uninstall clautorun` — handles "not installed" edge case | ✅ Port from legacy: part of complete uninstall |
| Remove cache | ❌ Not supported | ✅ `shutil.rmtree(cache_dir)` — removes manually cached versions | ✅ Port from legacy: cache-installed plugins survive `claude plugin uninstall`; manual cleanup needed |
| Remove legacy dir | ❌ Not supported | ✅ Removes `~/.claude/plugins/clautorun/` if it exists from older install methods | ✅ Port from legacy: one-time migration cleanup; can be removed in future versions |

### 📊 Status / Check

| Capability | Modern | Legacy | Ideal |
|-----------|------|------|------|
| Plugin status | ✅ Runs `claude plugin list`, checks each plugin name appears with "enabled" | ✅ Same approach | ✅ Keep modern: simpler implementation, same result |
| Functional test | ❌ No verification that commands actually work end-to-end | ✅ Pipes `/help` through `claude -p` and uses jq to check `/afs` appears in slash commands — proves commands are loaded and working | ⚠️ Consider porting, but the jq pipe is fragile (depends on Claude output format). Simpler alternative: just check `claude plugin list` output shows "enabled" |
| UV env check | ⚠️ Only checks if `.venv` exists when `CLAUDE_PLUGIN_ROOT` is set (in `show_status()`) | ✅ Full check: UV installed, `pyproject.toml` exists, `uv.lock` exists, `.venv` exists — systematic | ✅ Port from legacy: comprehensive env check helps diagnose install problems |
| Tools in PATH | ❌ Doesn't check | ✅ `which clautorun-interactive` and `which clautorun-install` — verifies UV tool install worked | ✅ Port from legacy: confirms tools are globally accessible |
| Marketplace check | ✅ Inferred from `claude plugin list` output | ✅ Explicit `claude plugin marketplace list` with "clautorun" check | ✅ Keep either: both approaches work; explicit check is slightly more informative |

### 🔧 Dev Workflow

| Capability | Modern | Legacy | Ideal |
|-----------|------|------|------|
| Source → cache sync | ❌ Not supported — developers must reinstall after every change | ✅ `sync` action: `install_to_cache()` copies source to Claude Code cache — changes visible after Claude Code restart without full reinstall | ✅ Port from legacy: essential for development iteration speed; reinstall takes 30s+ vs sync takes <1s |
| Bootstrap toggle | ✅ `--no-bootstrap` / `--enable-bootstrap` in `__main__.py` — adds/removes flag from hooks.json via regex | ❌ Not supported | ✅ Keep modern: allows disabling background dep installs for debugging or air-gapped environments |

---

## 4. Dead Code & Cleanup Items

| Item | Location | Status |
|------|----------|--------|
| 💀 `marketplace_compat()` | `__main__.py:241-249` | Dead — no entry point |
| 💀 `marketplace_main()` | `install_plugins.py:302-308` | Dead — no entry point |
| 💀 `src/clautorun_marketplace/` ref | Root `pyproject.toml:65` | Directory doesn't exist |
| 💀 `"clautorun-marketplace v0.7.0"` banner | `install_plugins.py:176`, `install.py:970` | Prints removed brand |
| 💀 `clautorun-marketplace` in docs | `CLAUDE.md:10,14`, `README.md:77,93,96` | References removed command |
| 💀 Python 2 compat | `install.py:24-34` | Dead — `requires-python >= 3.10` |
| 💀 `MarketplaceInstaller` class | `install.py:945-1050` | Redundant — `install_plugins("all")` covers this |
| 💀 `plan-export` in `PluginName` | `install_plugins.py:31` | Merged into clautorun |
| 💀 `plan-export` in marketplace.json | `.claude-plugin/marketplace.json:22-31` | Plugin dir removed |

---

## 5. pdf-extractor Dependency Analysis

### 📋 Core Dependencies (`pyproject.toml:23-29`)

| Package | Version | Import in `backends.py` | Style | After Install? |
|---------|---------|------------------------|-------|:--------------:|
| `markitdown` | ≥0.1.0 | Line 167 (factory) | 🟢 Lazy | ⚠️ graceful fail |
| `pdfplumber` | ≥0.10.0 | Line 18 (top-level) | 🔴 Eager | 💥 crashes module |
| `pdfminer.six` | ≥20221105 | Line 22 (top-level) | 🔴 Eager | 💥 crashes module |
| `PyPDF2` | ≥3.0.0 | Line 19 (top-level) | 🔴 Eager | 💥 crashes module |
| `tqdm` | ≥4.60.0 | `extractors.py:8` | 🔴 Eager | 💥 crashes module |

### 📋 Optional Dependencies (`pyproject.toml:31-34`)

| Extra | Packages | Import Style | After Install? |
|-------|----------|-------------|:--------------:|
| `gpu` | `docling`, `marker-pdf` | 🟢 Lazy (factory) | ⚠️ graceful fail |
| `llm` | `pymupdf4llm` | 🟡 try/except | ⚠️ graceful fail |

### 📋 Undeclared (in code, not in pyproject.toml)

| Package | Location | Style | After Install? |
|---------|----------|-------|:--------------:|
| `pdfbox` | `backends.py:26` | 🟡 try/except | ⚠️ graceful fail |
| `pdftotext` | `backends.py:268` (subprocess) | 🟢 System CLI | Depends on OS |

### 🔴 The Gap: No Installer Installs pdf-extractor's Python Deps

| Pathway | Installs pdf-extractor deps? |
|---------|:---:|
| `clautorun --install` | ❌ registers plugin only |
| `clautorun-install` | ❌ clautorun only |
| `clautorun-install --marketplace` | ❌ registers plugin only |
| Root `uv sync` | ❌ pdf-extractor in optional `all` extra, not in `dependencies` |
| Root `uv sync --extra all` | ✅ but nothing calls this |
| `uv pip install ./plugins/pdf-extractor` | ✅ manual only |
| daemon bootstrap | ❌ not yet implemented |

### 📊 Backend Availability (9 backends, after clean install)

| Backend | Available? | Import Style | License |
|---------|:----------:|:------------:|---------|
| markitdown | ⚠️ graceful | 🟢 Lazy | MIT |
| pdfplumber | 💥 crash | 🔴 Eager | MIT |
| pdfminer | 💥 crash | 🔴 Eager | MIT |
| pypdf2 | 💥 crash | 🔴 Eager | BSD-3 |
| pymupdf4llm | ⚠️ graceful | 🟡 try/except | AGPL-3.0 |
| pdfbox | ⚠️ graceful | 🟡 try/except | Apache-2.0 |
| docling | ⚠️ graceful | 🟢 Lazy | MIT |
| marker | ⚠️ graceful | 🟢 Lazy | GPL-3.0 |
| pdftotext | Depends on OS | 🟢 subprocess | System |

**3 of 9 backends 💥 crash the entire module. The other 6 degrade gracefully.**

---

## 6. Fix Plan: Lazy Imports + Bootstrap

Two complementary changes:

### 6a. Graceful imports in `backends.py` (prevent crash-on-import)

**File**: `plugins/pdf-extractor/src/pdf_extraction/backends.py`

Move 3 eager imports to try/except, matching the existing `pdfbox`/`pymupdf4llm` pattern:

```python
# BEFORE (lines 18-22) — 🔴 crashes if missing:
import pdfplumber
import PyPDF2
from pdfminer.high_level import extract_text

# AFTER — 🟡 graceful, same pattern as pdfbox/pymupdf4llm:
try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    import PyPDF2
except ImportError:
    PyPDF2 = None

try:
    from pdfminer.high_level import extract_text
except ImportError:
    extract_text = None
```

Update 3 backend factories to check for `None` (matching `PdfboxExtractor`/`Pymupdf4llmExtractor` pattern):

```python
# PdfplumberExtractor factory → raise ImportError("pdfplumber not installed") if None
# Pypdf2Extractor factory → raise ImportError("PyPDF2 not installed") if None
# PdfminerExtractor factory → raise ImportError("pdfminer.six not installed") if None
```

Make `tqdm` graceful in `extractors.py:8`:

```python
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, **kwargs):
        return iterable
```

### 6b. Bootstrap pdf-extractor deps via daemon

**File**: `plugins/clautorun/src/clautorun/daemon.py`

Add `_install_pdf_deps()` as step 4 in `_bootstrap_optional_deps()`, following the `_install_bashlex()` pattern:

- Check if `plugins/pdf-extractor/` exists (skip if not)
- Check if any core dep is missing (`pdfplumber`, `pdfminer`, `PyPDF2`, `markitdown`, `tqdm`)
- If missing: `uv pip install -q pdfplumber pdfminer.six PyPDF2 markitdown tqdm`
- Runs in background thread — non-blocking

### 6c. How they work together

1. **First session**: daemon bootstrap installs deps in background → `backends.py` loads gracefully meanwhile → deps available by the time user calls `/pdf-extractor:extract`
2. **Subsequent sessions**: deps already installed → bootstrap skips → zero overhead
3. **No pdf-extractor**: bootstrap detects plugin absent → skips entirely

---

## 7. Consolidated Modern DRY Installer (Proposed)

### 🎯 Goal

One installer module (`install_plugins.py`) that is the superset of both, with install.py deleted. DRY: every capability exists once. WOLOG: no loss of functionality.

### 📐 Architecture

Single entry point `clautorun --install` routes to `install_plugins.py`. The `clautorun-install` entry point is rewired to point to the same function. Legacy `clautorun install` subcommand removed.

```
clautorun --install [PLUGINS]       → install_plugins.install_plugins()
clautorun --install --force         → same, with force=True
clautorun --install --tool          → same, + uv tool install
clautorun --uninstall               → install_plugins.uninstall_plugins()    [NEW]
clautorun --status                  → install_plugins.show_status()
clautorun --sync                    → install_plugins.sync_to_cache()        [NEW]
clautorun --no-bootstrap            → __main__.set_bootstrap_config(False)
clautorun --enable-bootstrap        → __main__.set_bootstrap_config(True)
clautorun-install                   → same as clautorun --install            [rewired]
```

### 📋 Feature Superset (what the consolidated installer provides)

**Keep from Modern (`install_plugins.py`):**

| Feature | Source | Why Keep |
|---------|--------|----------|
| `CmdResult` dataclass | `install_plugins.py:49-58` | Clean error handling, `.has_text()` method |
| `PluginName` enum | `install_plugins.py:27-42` | Validation, typo prevention, IDE completion |
| `@lru_cache find_marketplace_root()` | `install_plugins.py:95-116` | Efficient, cached path resolution |
| `run_cmd()` helper | `install_plugins.py:61-92` | DRY subprocess calls with timeout + executable check |
| `claude plugin enable` after install | `install_plugins.py:213` | Ensures plugins are actually active |
| Comma-separated plugin selection | `install_plugins.py:146-157` | User-friendly multi-plugin install |

**Port from Legacy (`install.py`):**

| Feature | Source | How to Port |
|---------|--------|-------------|
| `claude plugin update` (try first) | `install.py:434-446` | Add to install loop: try update, fall back to install |
| Cache fallback | `install.py:499-564` | New `_install_to_cache()` function using `run_cmd()` |
| `installed_plugins.json` registration | `install.py:566-601` | New `_register_in_json()` function |
| Path substitution | `install.py:253-293` | New `_substitute_paths()` function |
| Backup before overwrite | `install.py:234-251` | Add to cache fallback path |
| UV env validation | `install.py:141-183` | New `_check_uv_env()` returning `CmdResult` |
| `uv sync` dep install | `install.py:185-223` | New `_sync_dependencies()` function |
| Python version check | `install.py:37-103` | Simplified — just `sys.version_info >= (3, 10)` |
| `~/.claude/` detection | `install.py:137-139` | One-liner `Path.home() / ".claude"` check |
| Uninstall | `install.py:811-836` | New `uninstall_plugins()` function |
| UV tool uninstall | `install.py:784-809` | Add to uninstall flow |
| Cache removal | `install.py:603-628` | Add to uninstall flow |
| Source → cache sync | `install.py:630-640` | New `sync_to_cache()` function |
| Status: tools in PATH | `install.py:855-887` | Add `which` checks to `show_status()` |
| Status: commands work | `install.py:323-368` | Add functional test to `show_status()` |
| pdf-extractor dep install | NEW | `uv pip install` pdf-extractor core deps |

**Delete (not ported):**

| Feature | Source | Why Delete |
|---------|--------|------------|
| 💀 Python 2 compat | `install.py:24-34` | Dead — `requires-python >= 3.10` |
| 💀 `MarketplaceInstaller` class | `install.py:945-1050` | `install_plugins("all")` covers this |
| 💀 `marketplace_compat()` | `__main__.py:241-249` | Dead — no entry point |
| 💀 `marketplace_main()` | `install_plugins.py:302-308` | Dead — no entry point |
| 💀 `plan-export` in `PluginName` | `install_plugins.py:31` | Merged into clautorun |
| 💀 `"clautorun-marketplace"` banner | `install_plugins.py:176` | Removed brand |
| 💀 `is_plugin_installed()` jq pipe | `install.py:323-368` | Fragile; use `claude plugin list` instead |

### 📐 Proposed Module Structure

```python
# install_plugins.py — consolidated, ~450 lines (vs 315 + 1136 = 1451 today)

# --- Data types ---
class PluginName(str, Enum): ...      # Keep (validated plugin names)
class CmdResult: ...                    # Keep (clean subprocess results)

# --- Discovery ---
@lru_cache
def find_marketplace_root() -> Path: ...   # Keep (cached upward search)
def _get_plugin_root() -> Path | None: ... # Port from install.py

# --- Environment ---
def _check_python() -> CmdResult: ...            # Simplified from install.py
def _check_uv_env() -> CmdResult: ...            # Port from install.py
def _sync_dependencies() -> CmdResult: ...        # Port from install.py
def _substitute_paths(plugin_dir) -> CmdResult: . # Port from install.py

# --- Install ---
def install_plugins(selection, *, tool, force, sync_deps) -> int: ...  # Keep + enhance
def _install_to_cache(plugin_dir) -> CmdResult: ...                    # Port from install.py
def _register_in_json(install_path, version) -> CmdResult: ...        # Port from install.py
def _install_pdf_deps() -> CmdResult: ...                              # NEW

# --- Uninstall ---
def uninstall_plugins(selection) -> int: ...       # Port from install.py

# --- Status ---
def show_status() -> int: ...                      # Keep + enhance with UV/PATH checks

# --- Dev ---
def sync_to_cache() -> int: ...                    # Port from install.py
```

### 📐 Entry Point Changes

`plugins/clautorun/pyproject.toml`:

```toml
[project.scripts]
clautorun            = "clautorun.__main__:main"
clautorun-interactive = "clautorun.__main__:main"
clautorun-install    = "clautorun.__main__:main"     # ← rewired to __main__ (was install:main)
claude-session-tools = "clautorun.claude_session_tools:main"
```

`__main__.py` changes:
- Remove legacy `sys.argv[1] in ["install", "uninstall", "check"]` dispatch (line 201)
- Add `--uninstall` and `--sync` flags
- Remove `marketplace_compat()` dead code

### 📐 Files Changed

| File | Action | Net Lines |
|------|--------|-----------|
| `install_plugins.py` | Expand with ported features | 315 → ~450 (+135) |
| `install.py` | **Delete entirely** | 1136 → 0 (-1136) |
| `__main__.py` | Rewire, add flags, remove dead code | ~254 → ~240 (-14) |
| `pyproject.toml` | Rewire `clautorun-install` entry point | ~1 line |
| `backends.py` | Graceful imports | ~15 lines changed |
| `extractors.py` | Graceful tqdm | ~4 lines changed |
| `daemon.py` | Add `_install_pdf_deps()` | ~35 lines added |
| Root `pyproject.toml` | Remove `src/clautorun_marketplace` ref | ~1 line |
| `.claude-plugin/marketplace.json` | Remove `plan-export` entry | ~10 lines |
| `CLAUDE.md` | Update install instructions | ~10 lines |
| `README.md` | Update install instructions | ~10 lines |

**Net: -1001 lines** (1451 → ~450 for installer code)

### ✅ Verification

1. `clautorun --install` — installs all plugins, enables them, pdf-extractor deps installed
2. `clautorun --install clautorun` — installs only clautorun
3. `clautorun --install --force` — force reinstall
4. `clautorun --install --tool` — also installs UV tool
5. `clautorun --uninstall` — removes plugins + UV tools
6. `clautorun --status` — shows UV env, plugins, tools in PATH, deps available
7. `clautorun --sync` — dev workflow: source → cache
8. `python3 -c "import pdf_extraction.backends"` — no crash even without deps
9. `uv run pytest plugins/clautorun/tests/ -v` — all tests pass
10. `uv run pytest plugins/pdf-extractor/tests/ -v` — all tests pass
