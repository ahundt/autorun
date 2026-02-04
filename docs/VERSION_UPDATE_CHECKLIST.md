# Version Update Checklist

When updating versions in the clautorun marketplace, use this checklist to ensure all locations are updated consistently.

## Unified Versioning

All plugins in this marketplace use the **same version number** for consistency. When releasing a new version, update ALL plugins to the same version.

**Current Version: 0.7.0**

### Total Files to Update: 13

| Plugin | Files |
|--------|-------|
| Root/Marketplace | 3 (`pyproject.toml`, `src/clautorun_marketplace/__init__.py`, `.claude-plugin/marketplace.json`) |
| clautorun | 5 (`pyproject.toml`, `plugin.json`, `__init__.py`, `install.py` x2, `main.py` x3) |
| plan-export | 3 (`pyproject.toml`, `plugin.json`, `CLAUDE.md` header) |
| pdf-extractor | 3 (`pyproject.toml`, `plugin.json`, `__init__.py`) |
| Documentation | 2 (`README.md`, `CLAUDE.md` headers) |

## Search Patterns

Use these grep commands to find all version references:

```bash
# Find all version strings for a specific version (e.g., 0.7.0)
grep -rn "0\.7\.0" --include="*.py" --include="*.json" --include="*.toml" --include="*.md" . | grep -v notes/ | grep -v __pycache__ | grep -v .venv

# Find all JSON version fields
grep -rn '"version"' --include="*.json" . | grep -v notes/ | grep -v __pycache__

# Find all Python __version__ variables
grep -rn '__version__' --include="*.py" . | grep -v __pycache__ | grep -v .venv

# Find version in pyproject.toml files
grep -rn '^version\s*=' --include="*.toml" .
```

## clautorun Plugin (main)

| File | Field/Pattern | Example |
|------|---------------|---------|
| `pyproject.toml` (root) | `version = "X.Y.Z"` | `version = "0.7.0"` |
| `src/clautorun_marketplace/__init__.py` | Print statement | `print(f"📦 clautorun-marketplace vX.Y.Z")` |
| `README.md` | Header text | `clautorun plugin vX.Y.Z (Current)` |
| `CLAUDE.md` | Section header | `## clautorun Plugin (vX.Y.Z)` |
| `.claude-plugin/marketplace.json` | clautorun entry | `"version": "X.Y.Z"` (line ~16) |
| `plugins/clautorun/pyproject.toml` | `version = "X.Y.Z"` | |
| `plugins/clautorun/.claude-plugin/plugin.json` | `"version": "X.Y.Z"` | |
| `plugins/clautorun/src/clautorun/__init__.py` | `__version__ = "X.Y.Z"` | |
| `plugins/clautorun/src/clautorun/install.py` | Default version (2 places) | `version = "X.Y.Z"  # Default` |
| `plugins/clautorun/src/clautorun/install.py` | Print statement | `print(f"📦 clautorun-marketplace vX.Y.Z")` |
| `plugins/clautorun/src/clautorun/main.py` | Config defaults (3 places) | `"version": "X.Y.Z"` |

## plan-export Plugin

| File | Field/Pattern | Example |
|------|---------------|---------|
| `plugins/plan-export/pyproject.toml` | `version = "X.Y.Z"` | |
| `plugins/plan-export/.claude-plugin/plugin.json` | `"version": "X.Y.Z"` | |
| `.claude-plugin/marketplace.json` | plan-export entry | `"version": "X.Y.Z"` (line ~27) |
| `CLAUDE.md` | Section header | `## plan-export Plugin (vX.Y.Z)` |

## pdf-extractor Plugin

| File | Field/Pattern | Example |
|------|---------------|---------|
| `plugins/pdf-extractor/pyproject.toml` | `version = "X.Y.Z"` | |
| `plugins/pdf-extractor/.claude-plugin/plugin.json` | `"version": "X.Y.Z"` | |
| `plugins/pdf-extractor/src/pdf_extraction/__init__.py` | `__version__ = "X.Y.Z"` | |
| `.claude-plugin/marketplace.json` | pdf-extractor entry | `"version": "X.Y.Z"` (line ~38) |

## Historical References (DO NOT CHANGE)

These references document when features were introduced and should NOT be updated:

- `plugins/clautorun/src/clautorun/config.py` - Comments like "Command Blocking System v0.6.0"
- `plugins/clautorun/src/clautorun/main.py` - Deprecation notices like "Legacy Hook Handler (v0.6.1)"
- `README.md` - Feature introduction notes like "NEW v0.6.0:"
- `CLAUDE.md` - Feature notes like "Safety Guards (v0.6.0+)"
- `notes/` folder - All historical planning documents

## Dependency Version Requirements

The root `pyproject.toml` has minimum version requirements that may need updating:

```toml
[project.optional-dependencies]
all = [
    "clautorun>=X.Y.Z",
    "plan-export>=X.Y.Z",
    "pdf-extractor>=X.Y.Z",
]
```

These are minimum versions, so they don't need to match exactly but should be updated when making breaking changes.

## Verification Steps

After updating versions:

1. **Search for old version**: `grep -rn "OLD_VERSION" . | grep -v notes/`
2. **Run tests**: `uv run pytest plugins/*/tests/ -v`
3. **Reinstall**: `uv pip install -e . && uv run clautorun-marketplace`
4. **Verify output**: Check the version in the marketplace output

## Build Artifacts

Remove stale build directories after version updates:

```bash
trash plugins/clautorun/build/
trash plugins/plan-export/build/
trash plugins/pdf-extractor/build/
```

These contain cached code with old versions and can cause confusion.
