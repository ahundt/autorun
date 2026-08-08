# Version Update Checklist

When updating versions in the autorun marketplace, use this checklist to ensure all locations are updated consistently.

## Unified Versioning

All plugins in this marketplace use the **same version number** for consistency. When releasing a new version, update ALL plugins to the same version.

The source of truth is `plugins/autorun/pyproject.toml`. The release consistency
test checks both packages, manifests, marketplaces, Python `__version__` values,
and maintained version-bearing docs against that value.

## Quick Method

```bash
# 1. Find all references to the OLD version
rg -n "OLD_VERSION" --glob '*.py' --glob '*.json' --glob '*.toml' --glob '*.md' \
  --glob '!**/__pycache__/**' --glob '!**/.venv/**' --glob '!notes/**'

# 2. Review EVERY match before replacing — see Gotchas below
# 3. Replace only the ones that are autorun version refs
# 4. Run tests: uv run pytest plugins/autorun/tests/ -v
# 5. Verify zero old refs remain (excluding notes/)
```

## Additional Search Patterns

```bash
# Find all JSON version fields
rg -n '"version"' --glob '*.json' --glob '!**/__pycache__/**'

# Find all Python __version__ variables
rg -n '__version__' --glob '*.py' --glob '!**/__pycache__/**' --glob '!**/.venv/**'

# Find version in pyproject.toml files
rg -n '^version\s*=' --glob '*.toml'
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
| `plugins/autorun/src/autorun/gemini_template/gemini-extension.json` | `"version": "X.Y.Z"` | Lives under `gemini_template/`, outside Claude's marketplace scan path — see the bug #24115 / #14449 workaround in `install.py` |

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
| `AGENTS.md` | 2 refs — `## autorun Plugin (vX.Y.Z)` and `## pdf-extractor Plugin (vX.Y.Z)`. `CLAUDE.md` and `GEMINI.md` are symlinks to it; edit this file, never a link |
| `plugins/autorun/AGENTS.md` | 1 ref — the illustrative plugin-cache path `<version>/` |
| `plugins/autorun/HOOK_ARCHITECTURE.md` | Version references in docs |
| `docs/version_update_checklist.md` | `**Current Version: X.Y.Z**` at top |
| `plugins/autorun/.codex-plugin/plugin.json` | Codex plugin package version |
| `plugins/pdf-extractor/CLAUDE.md` | Section header |

### Skills (4+ files)

| File | Notes |
|------|-------|
| `plugins/pdf-extractor/skills/pdf-extractor/SKILL.md` | 2 refs — do NOT change `pdfplumber>=0.10.0` in install commands! |
| `plugins/pdf-extractor/skills/pdf-extractor/references/backends.md` | Do NOT change `pdfplumber>=0.10.0`! |

### Tests

| File | Notes |
|------|-------|
| `plugins/autorun/tests/test_hook_entry.py` | Cache path version dirs |
| `plugins/autorun/tests/test_hooks_format.py` | Semver sort test data |
| `plugins/autorun/tests/test_bootstrap_config.py` | Version in config |
| `plugins/autorun/tests/test_claude_e2e_real_money.py` | Cache path version dirs |
| `plugins/autorun/tests/test_install_codex.py` | Codex marketplace and hook fixtures |
| `plugins/autorun/tests/test_install_extension.py` | Extension manifest fixture. **See Gotcha #5** — comparison data, not a version reference. |
| `plugins/autorun/tests/test_install_memory_runtime.py` | Prerelease ordering pairs. **See Gotcha #5** and #2 — pairs must stay distinct. |
| `plugins/autorun/src/autorun/installer/extension.py` | Self-check manifest fixture. **See Gotcha #5**. |
| `plugins/autorun/src/autorun/installer/runtime.py` | Prerelease ordering assertions and the comment citing them. **See Gotcha #5**. |

## Gotchas (learned from 0.10.0 → 0.10.1 release)

### Gotcha 1: Third-party dependency version collision

Unchecked `0.10.0` → `0.10.1` replace will change `pdfplumber>=0.10.0` to `pdfplumber>=0.10.1`. This is a **third-party library version**, not autorun's version.

**Affected files:**
- `plugins/pdf-extractor/pyproject.toml` — `pdfplumber>=0.10.0`
- `plugins/pdf-extractor/CLAUDE.md` — install commands
- `plugins/pdf-extractor/skills/pdf-extractor/SKILL.md` — install commands (2 places)
- `plugins/pdf-extractor/skills/pdf-extractor/references/backends.md` — dependency note

**Fix:** Review every match. Only replace lines where the version refers to autorun/pdf-extractor package version, not third-party dependency versions.

### Gotcha 2: Test parametrization collapse

`test_install_memory_runtime.py` has parametrized test cases like:
```python
("0.10.0", "v0.10.1", True),   # patch bump — update available
("0.10.1", "v0.10.0", False),  # downgrade — no update
("0.10.0", "v0.10.0", False),  # same version — no update
```

Unchecked replacement turns ALL three into `("0.10.1", "v0.10.1", ...)` — collapsing distinct test cases into duplicates. The "patch bump" case becomes identical to "same version."

**Fix:** After bulk replace, manually verify parametrized test cases still have **distinct version pairs** that test the intended comparison (upgrade, downgrade, same).

### Gotcha 3: Minimum version deps in root pyproject.toml

`pyproject.toml` has `autorun>=0.10.0` and `pdf-extractor>=0.10.0` in `[project.optional-dependencies]`. These are **minimum** version requirements. Only bump these for breaking changes, not patch releases.

### Gotcha 4: Block message scope hint must be on separate line

`config.py` DEFAULT_INTEGRATIONS "To allow" lines end with the command, then `\nScope: [N|5m|permanent]` on a new line. If the scope hint is on the **same line** as the `/ar:ok` command (e.g. `/ar:ok 'git push' [N|5m|permanent]`), it breaks `test_actual_command_blocking::TestArOkQuotingInSuggestions` because the test parses everything after `/ar:ok` as the copy-pasteable pattern.

### Gotcha 5: Installer fixtures that merely happen to match the current version

Four files carry `1.0.0rc1` as *test data*, not as a version reference:
`installer/runtime.py` and `test_install_memory_runtime.py` compare
release-candidate ordering (`1.0.0rc1` against `1.0.0`, `1.0.1`, `1.0.0rc2`),
and `installer/extension.py` and `test_install_extension.py` stamp a manifest
fixture. They are listed above only because the coverage test requires every
file holding the current version to appear here.

**Fix:** Leave them alone. They need no bump, and replacing them would collapse
the ordering pairs the same way Gotcha #2 describes. Substituting an invented
version is also wrong: it becomes a real release number later and the fixture
silently starts asserting something else.

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

These are minimum versions — only bump for breaking changes, not patch releases. See Gotcha #3.

## Verification Steps

After updating versions:

1. **Search for old version**: `grep -rn "OLD_VERSION" . | grep -v __pycache__`
2. **Run core tests**: `uv run pytest plugins/autorun/tests/test_unit_simple.py -v`
3. **Run version-sensitive tests**: `uv run pytest plugins/autorun/tests/test_install_memory_runtime.py plugins/autorun/tests/test_hook_entry.py plugins/autorun/tests/test_hooks_format.py plugins/autorun/tests/test_bootstrap_config.py plugins/autorun/tests/test_actual_command_blocking.py -v`
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

autorun is currently distributed via GitHub (`claude plugin install` / `gemini extensions install`), not PyPI. If PyPI publishing is added in the future, follow the pattern from the [AI Session Search release guide](https://github.com/ahundt/ai-session-search/blob/main/docs/development/releasing.md):

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
