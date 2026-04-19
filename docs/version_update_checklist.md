# Version Update Checklist

When updating versions in the autorun marketplace, use this checklist to ensure all locations are updated consistently.

## Unified Versioning

All plugins in this marketplace use the **same version number** for consistency. When releasing a new version, update ALL plugins to the same version.

**Current Version: 0.10.2rc1**

## Quick Method

```bash
# 1. Find all references to the OLD version
grep -rn "OLD_VERSION" --include="*.py" --include="*.json" --include="*.toml" --include="*.md" . \
  | grep -v __pycache__ | grep -v .venv | grep -v .git/

# 2. Review EVERY match before replacing — see Gotchas below
# 3. Replace only the ones that are autorun version refs
# 4. Run tests: uv run pytest plugins/autorun/tests/ -v
# 5. Verify zero old refs remain (excluding notes/)
```

## Additional Search Patterns

```bash
# Find all JSON version fields
grep -rn '"version"' --include="*.json" . | grep -v __pycache__

# Find all Python __version__ variables
grep -rn '__version__' --include="*.py" . | grep -v __pycache__ | grep -v .venv

# Find version in pyproject.toml files
grep -rn '^version\s*=' --include="*.toml" .
```

## Files to Update (~33 files)

The grep in "Quick Method" is the authoritative source. The lists below are a guide — grep is the real checklist.

### Root/Marketplace (3 files)

| File | Field/Pattern | Notes |
|------|---------------|-------|
| `pyproject.toml` | `version = "X.Y.Z"` | Only the `version` field. Do NOT change `>=X.Y.Z` minimum deps unless breaking change. |
| `src/autorun_workspace/__init__.py` | Print statement with version | |
| `.claude-plugin/marketplace.json` | `"version": "X.Y.Z"` (2 entries: autorun + pdf-extractor) | |

### autorun Plugin (8+ files)

| File | Field/Pattern | Notes |
|------|---------------|-------|
| `plugins/autorun/pyproject.toml` | `version = "X.Y.Z"` | |
| `plugins/autorun/.claude-plugin/plugin.json` | `"version": "X.Y.Z"` | |
| `plugins/autorun/.claude-plugin/marketplace.json` | `"version": "X.Y.Z"` | |
| `plugins/autorun/src/autorun/__init__.py` | `__version__ = "X.Y.Z"` | |
| `plugins/autorun/src/autorun/install.py` | 5 references: 2 fallback defaults, 1 print, 1 config dict, 1 `__version__` fallback | |
| `plugins/autorun/src/autorun/metadata.json` | `"version": "X.Y.Z"` | Build artifact — stale commit hash is OK |
| `plugins/autorun/src/autorun/aix_manifest.py` | Fallback version in `pkg.get("version", "X.Y.Z")` | |
| `plugins/autorun/gemini-extension.json` | `"version": "X.Y.Z"` | |

### pdf-extractor Plugin (4+ files)

| File | Field/Pattern | Notes |
|------|---------------|-------|
| `plugins/pdf-extractor/pyproject.toml` | `version = "X.Y.Z"` | Do NOT change `pdfplumber>=0.10.0` — that's a third-party dep! |
| `plugins/pdf-extractor/.claude-plugin/plugin.json` | `"version": "X.Y.Z"` | |
| `plugins/pdf-extractor/src/pdf_extraction/__init__.py` | `__version__ = "X.Y.Z"` | |
| `plugins/pdf-extractor/gemini-extension.json` | `"version": "X.Y.Z"` | |

### Documentation (6+ files)

| File | Notes |
|------|-------|
| `README.md` | Section headers, install verification examples |
| `CLAUDE.md` | Section header `## autorun Plugin (vX.Y.Z)` |
| `GEMINI.md` | Install verification examples (8 refs) |
| `aix.toml` | `version = "X.Y.Z"` |
| `plugins/autorun/HOOK_ARCHITECTURE.md` | Version references in docs |
| `docs/version_update_checklist.md` | `**Current Version: X.Y.Z**` at top |
| `plugins/pdf-extractor/CLAUDE.md` | Section header |

### Skills (4+ files)

| File | Notes |
|------|-------|
| `plugins/autorun/skills/ai-session-tools/SKILL.md` | Version in skill description |
| `plugins/autorun/skills/autorun-maintainer/SKILL.md` | 3 version references |
| `plugins/autorun/skills/claude-session-tools/SKILL.md` | Version in skill description |
| `plugins/pdf-extractor/skills/pdf-extractor/SKILL.md` | 2 refs — do NOT change `pdfplumber>=0.10.0` in install commands! |
| `plugins/pdf-extractor/skills/pdf-extractor/references/backends.md` | Do NOT change `pdfplumber>=0.10.0`! |

### Tests (6 files)

| File | Notes |
|------|-------|
| `plugins/autorun/tests/test_self_update.py` | ~25 refs. **See Gotcha #2** — parametrized test cases must keep distinct version pairs. |
| `plugins/autorun/tests/test_hook_entry.py` | Cache path version dirs |
| `plugins/autorun/tests/test_hooks_format.py` | Semver sort test data |
| `plugins/autorun/tests/test_install_pathways.py` | Cache version sort test data. **See Gotcha #3** — version lists must stay distinct. |
| `plugins/autorun/tests/test_bootstrap_config.py` | Version in config |
| `plugins/autorun/tests/test_claude_e2e_real_money.py` | Cache path version dirs |

## Gotchas (learned from 0.10.0 → 0.10.1 release)

### Gotcha 1: Third-party dependency version collision

Blind `0.10.0` → `0.10.1` replace will change `pdfplumber>=0.10.0` to `pdfplumber>=0.10.1`. This is a **third-party library version**, not autorun's version.

**Affected files:**
- `plugins/pdf-extractor/pyproject.toml` — `pdfplumber>=0.10.0`
- `plugins/pdf-extractor/CLAUDE.md` — install commands
- `plugins/pdf-extractor/skills/pdf-extractor/SKILL.md` — install commands (2 places)
- `plugins/pdf-extractor/skills/pdf-extractor/references/backends.md` — dependency note

**Fix:** Review every match. Only replace lines where the version refers to autorun/pdf-extractor package version, not third-party dependency versions.

### Gotcha 2: Test parametrization collapse

`test_self_update.py` has parametrized test cases like:
```python
("0.10.0", "v0.10.1", True),   # patch bump — update available
("0.10.1", "v0.10.0", False),  # downgrade — no update
("0.10.0", "v0.10.0", False),  # same version — no update
```

Blind replace turns ALL three into `("0.10.1", "v0.10.1", ...)` — collapsing distinct test cases into duplicates. The "patch bump" case becomes identical to "same version."

**Fix:** After bulk replace, manually verify parametrized test cases still have **distinct version pairs** that test the intended comparison (upgrade, downgrade, same).

### Gotcha 3: Test version list deduplication

`test_install_pathways.py` has version lists like `["0.8.0", "0.9.0", "0.10.0", "0.10.1"]` for testing sort order. Blind replace turns this into `["0.8.0", "0.9.0", "0.10.1", "0.10.1"]` — duplicate entries that break the sort test.

**Fix:** After bulk replace, verify version lists in test files still have **all-distinct entries**.

### Gotcha 4: Minimum version deps in root pyproject.toml

`pyproject.toml` has `autorun>=0.10.0` and `pdf-extractor>=0.10.0` in `[project.optional-dependencies]`. These are **minimum** version requirements. Only bump these for breaking changes, not patch releases.

### Gotcha 5: Block message scope hint must be on separate line

`config.py` DEFAULT_INTEGRATIONS "To allow" lines end with the command, then `\nScope: [N|5m|permanent]` on a new line. If the scope hint is on the **same line** as the `/ar:ok` command (e.g. `/ar:ok 'git push' [N|5m|permanent]`), it breaks `test_actual_command_blocking::TestArOkQuotingInSuggestions` because the test parses everything after `/ar:ok` as the copy-pasteable pattern.

## Historical References (DO NOT CHANGE)

These references document when features were introduced and should NOT be updated:

- `plugins/autorun/src/autorun/config.py` - Comments like "Command Blocking System v0.6.0"
- `plugins/autorun/src/autorun/main.py` - Deprecation notices like "Legacy Hook Handler (v0.6.1)"
- `README.md` - Feature introduction notes like "NEW v0.6.0:"
- `CLAUDE.md` - Feature notes like "Safety Guards (v0.6.0+)"
- `notes/` folder - All historical planning documents

## Dependency Version Requirements

The root `pyproject.toml` has minimum version requirements:

```toml
[project.optional-dependencies]
all = [
    "autorun>=X.Y.Z",
    "pdf-extractor>=X.Y.Z",
]
```

These are minimum versions — only bump for breaking changes, not patch releases. See Gotcha #4.

## Verification Steps

After updating versions:

1. **Search for old version**: `grep -rn "OLD_VERSION" . | grep -v __pycache__`
2. **Run core tests**: `uv run pytest plugins/autorun/tests/test_unit_simple.py -v`
3. **Run version-sensitive tests**: `uv run pytest plugins/autorun/tests/test_self_update.py test_hook_entry.py test_hooks_format.py test_install_pathways.py test_bootstrap_config.py test_actual_command_blocking.py -v`
4. **Run full suite**: `uv run pytest plugins/autorun/tests/ -v`
5. **Verify config loads**: `uv run python -c "from autorun.config import DEFAULT_INTEGRATIONS; print(len(DEFAULT_INTEGRATIONS))"`

## Release Workflow

### Stage 1: Version bump
Follow the file lists above. Commit and push.

### Stage 2: Pre-flight checks
```bash
# Tests pass
uv run pytest plugins/autorun/tests/ -v

# Working tree is clean
git status  # expect clean

# No existing tag for this version
git tag -l 'vX.Y.Z'                    # expect empty
git ls-remote --tags origin vX.Y.Z     # expect empty

# Main is up to date
git pull origin main
```

### Stage 3: Wait for CI
```bash
# Check latest run
gh run list --limit 3

# Watch it (replace RUN_ID)
gh run watch <RUN_ID> --exit-status

# If it fails, check logs
gh run view <RUN_ID> --log-failed
```

### Stage 4: Tag and push
```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

### Stage 5: Verify tag is on the right commit
```bash
git log vX.Y.Z --oneline -1
# Must show the version bump commit
```

### Stage 6: Create GitHub release (optional)
```bash
gh release create vX.Y.Z --title "autorun vX.Y.Z" --generate-notes
```

### If CI fails after tagging
```bash
# Delete broken tag
git tag -d vX.Y.Z
git push origin :vX.Y.Z

# Fix, commit, push to main, wait for CI to pass
# Then re-tag
git tag vX.Y.Z
git push origin vX.Y.Z
```

## PyPI Publishing (future — not yet configured)

autorun is currently distributed via GitHub (`claude plugin install` / `gemini extensions install`), not PyPI. If PyPI publishing is added in the future, follow the pattern from [ai_session_tools/notes/release-process.md](https://github.com/ahundt/ai_session_tools/blob/main/notes/release-process.md):

1. **Trusted Publishers** — configure on PyPI/TestPyPI with exact owner/repo/workflow/environment match
2. **GitHub Environments** — `testpypi` (auto-publish) + `pypi` (manual approval gate)
3. **SHA-pinned actions** — `npx pin-github-action .github/workflows/publish.yml`
4. **Tag-version check** — build job verifies git tag matches pyproject.toml version
5. **TestPyPI first** — publish to TestPyPI, verify install, then approve PyPI
6. **Version conflicts** — TestPyPI doesn't allow overwrites; use `.post1` suffix if needed

## Build Artifacts

Remove stale build directories after version updates:

```bash
trash plugins/autorun/build/
trash plugins/pdf-extractor/build/
```

These contain cached code with old versions and can cause confusion.

## Deleted Plugins

- **plan-export** — merged into autorun plugin. Skip all plan-export references.
