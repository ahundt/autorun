# Version Update Checklist

When updating versions in the autorun marketplace, use this checklist to ensure all locations are updated consistently.

## Unified Versioning

All plugins in this marketplace use the **same version number** for consistency. When releasing a new version, update ALL plugins to the same version.

The source of truth is `plugins/autorun/pyproject.toml`. The checklist coverage
test verifies that every maintained file carrying the current version is listed;
release and package tests separately validate artifact and runtime identities.

## Quick Method

```bash
# 1. Find all references to the OLD version
rg --hidden -n "OLD_VERSION" --glob '*.py' --glob '*.json' --glob '*.toml' --glob '*.md' \
  --glob '!**/__pycache__/**' --glob '!**/.venv/**' --glob '!notes/**'

# 2. Review EVERY match before replacing — see Gotchas below
# 3. Replace only the ones that are autorun version refs
# 4. Run tests: uv run --project plugins/autorun pytest plugins/autorun/tests/ -v
# 5. Verify zero old refs remain (excluding notes/)
```

## Additional Search Patterns

```bash
# Find all JSON version fields
rg --hidden -n '"version"' --glob '*.json' --glob '!**/__pycache__/**'

# Find all Python __version__ variables
rg -n '__version__' --glob '*.py' --glob '!**/__pycache__/**' --glob '!**/.venv/**'

# Find version in pyproject.toml files
rg -n '^version\s*=' --glob '*.toml'
```

## Current inventory

The `--hidden` grep in "Quick Method" is authoritative. Hidden Claude/Codex
manifests are release inputs, so a search without `--hidden` is incomplete. The
lists below name maintained source fields; generated `metadata.json` build
provenance is written by the release builder and is not hand-edited.

### Root/Marketplace

| File | Field/Pattern | Notes |
|------|---------------|-------|
| `pyproject.toml` | `version = "X.Y.Z"` | Only the `version` field. Do NOT change `>=X.Y.Z` minimum deps unless breaking change. |
| `src/autorun_workspace/__init__.py` | `__version__ = "X.Y.Z"` | |
| `.claude-plugin/marketplace.json` | plugin entries use `"version": "X.Y.Z"` | The top-level marketplace catalog has its own version; do not silently treat it as a plugin version. |

### autorun Plugin

| File | Field/Pattern | Notes |
|------|---------------|-------|
| `plugins/autorun/pyproject.toml` | `version = "X.Y.Z"` | |
| `plugins/autorun/.claude-plugin/plugin.json` | `"version": "X.Y.Z"` | |
| `plugins/autorun/.claude-plugin/marketplace.json` | `"version": "X.Y.Z"` | |
| `plugins/autorun/src/autorun/__init__.py` | `__version__ = "X.Y.Z"` | |
| `plugins/autorun/src/autorun/metadata.json` | generated `"version"` build metadata | Do not hand-edit; the release builder rewrites version, commit, and build time |
| `plugins/autorun/src/autorun/gemini_template/gemini-extension.json` | `"version": "X.Y.Z"` | Lives under `gemini_template/`, outside Claude's marketplace scan path — see the bug #24115 / #14449 workaround in `installer/extension.py` |
| `plugins/autorun/.codex-plugin/plugin.json` | `"version": "X.Y.Z"` | Codex package manifest |

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
| `CHANGELOG.md` | Add the dated release section |
| `AGENTS.md` | 2 refs — `## autorun Plugin (vX.Y.Z)` and `## pdf-extractor Plugin (vX.Y.Z)`. `CLAUDE.md` and `GEMINI.md` are symlinks to it; edit this file, never a link |
| `plugins/autorun/AGENTS.md` | 1 ref — the illustrative plugin-cache path `<version>/` |
| `plugins/autorun/HOOK_ARCHITECTURE.md` | Version references in docs |
| `docs/version_update_checklist.md` | No current-version field; update only examples that intentionally track the release |
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
| `plugins/autorun/tests/test_claude_e2e.py` | Cache path version dirs |
| `plugins/autorun/tests/test_install_codex.py` | Codex marketplace and hook fixtures |
| `plugins/autorun/tests/test_install_extension.py` | Extension manifest fixture. **See Gotcha #5** — comparison data, not a version reference. |
| `plugins/autorun/tests/test_install_memory_runtime.py` | Prerelease ordering pairs. **See Gotcha #5** and #2 — pairs must stay distinct. |
| `plugins/autorun/tests/test_install_entrypoint.py` | Package receipt version fixture. |
| `plugins/autorun/tests/test_package_resources.py` | Build metadata version fixture. |
| `plugins/autorun/tests/test_release_artifacts.py` | Expected release artifact metadata. |
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

1. **Search for old version**: use the `rg --hidden` command in "Quick Method"
2. **Run core tests**: `uv run --project plugins/autorun pytest plugins/autorun/tests/test_unit_simple.py -v`
3. **Run version-sensitive tests**: `uv run --project plugins/autorun pytest plugins/autorun/tests/test_install_memory_runtime.py plugins/autorun/tests/test_hook_entry.py plugins/autorun/tests/test_hooks_format.py plugins/autorun/tests/test_bootstrap_config.py plugins/autorun/tests/test_actual_command_blocking.py -v`
4. **Run full suite**: `uv run --project plugins/autorun pytest plugins/autorun/tests/ -v`
5. **Verify config loads**: `uv run --project plugins/autorun python -c "from autorun.config import DEFAULT_INTEGRATIONS; print(len(DEFAULT_INTEGRATIONS))"`

## Release Workflow

Every public write below is marked **RELEASER**. Do not push, tag, or create a
GitHub release while preparing a candidate on someone else's behalf.

### Stage 1: Version bump
Follow the file lists above and commit locally. Do not push yet.

### Stage 2: Pre-flight checks
```bash
# Capture one candidate commit and require every later check to name it.
release_sha=$(git rev-parse HEAD)
git fetch origin main --tags
test -z "$(git status --porcelain=v1)"

# Run from the plugin directories, matching CI discovery/configuration.
(cd plugins/autorun && uv run --project . pytest tests/ -m "not tmux and not e2e and not release" -v)
(cd plugins/pdf-extractor && uv run --project . --locked --extra dev pytest tests/ -v)
(cd plugins/autorun && uv run --project . pytest tests/test_release_artifacts.py -m release -v)
(cd plugins/autorun && AUTORUN_ENABLE_STATE_BENCHMARK=1 uv run --project . pytest tests/test_state_store_benchmark.py -m benchmark -v)

# No existing tag for this version
git tag -l 'vX.Y.Z'                    # expect empty
git ls-remote --tags origin vX.Y.Z     # expect empty
```

Rehearse the marketplace install from the exact candidate checkout, never the
live home. This validates what a fresh Claude install will copy without waiting
for the tag. Keep the scratch directory until its files have been inspected.

```bash
scratch_root=$(mktemp -d "${TMPDIR:-/tmp}/autorun-rc1.XXXXXX")
mkdir -p "$scratch_root/home"
git worktree add --detach "$scratch_root/checkout" "$release_sha"
env HOME="$scratch_root/home" \
  CLAUDE_CONFIG_DIR="$scratch_root/home/.claude" \
  AUTORUN_HOME="$scratch_root/autorun-home" \
  AUTORUN_TEST_STATE_DIR="$scratch_root/state" \
  claude plugin marketplace add "$scratch_root/checkout" --scope user
env HOME="$scratch_root/home" \
  CLAUDE_CONFIG_DIR="$scratch_root/home/.claude" \
  AUTORUN_HOME="$scratch_root/autorun-home" \
  AUTORUN_TEST_STATE_DIR="$scratch_root/state" \
  claude plugin install ar@autorun --scope user
env HOME="$scratch_root/home" CLAUDE_CONFIG_DIR="$scratch_root/home/.claude" \
  claude plugin list
find "$scratch_root" -type f -print | sort
```

The inventory must contain the registered `ar@autorun` plugin and must not
contain `.coverage`, `coverage.xml`, `htmlcov`, `.ruff_cache`, or a development
`.venv` copied from the checkout. Claude may create and own a managed `.venv`
later; the installer must preserve that runtime. Do not point this rehearsal at
`~/.claude`, `~/.codex`, or a running daemon's state directory. After inspecting
the result, detach it with `git worktree remove "$scratch_root/checkout"` before
discarding the scratch directory.

### Stage 3: Push the candidate and wait for CI — **RELEASER public write**

```bash
git push origin "$release_sha":main
git fetch origin main
test "$(git rev-parse origin/main)" = "$release_sha"
```

The GitHub-backed Claude marketplace follows the repository's default branch,
not a release asset. Requiring `origin/main`, the CI run, and the later tag to
name the same SHA keeps a fresh marketplace install identical to the RC.

All eleven jobs must be green, not just the matrix. The seven-entry matrix
covers Python 3.10-3.14 on Ubuntu plus macOS 3.13 and Windows 3.13; four more
run once each: `coverage` (75% floor), `release-artifacts` (`-m release`),
`tmux-integration` (`-m tmux`), and `state-benchmark` (`-m benchmark`). Those
four are the only place their markers run, since the matrix deselects them.

Two failure shapes are worth recognising before reading logs. A job that dies
in "Install dependencies" with "Unable to find lockfile at `uv.lock`" means the
lockfile is missing from the checkout, not that a dependency broke. A job whose
JUnit XML never appears, with a `+++ Timeout +++` stack dump instead of a test
summary, hit the global per-test timeout in `pyproject.toml`: pytest-timeout
kills the whole session, so the remaining tests never run and the first
reported failure is the only one you get.

```bash
# Find the push run for the exact candidate, then verify its identity.
run_id=$(gh run list --workflow ci.yml --commit "$release_sha" --event push \
  --limit 1 --json databaseId --jq '.[0].databaseId')
test -n "$run_id"
test "$(gh run view "$run_id" --json headSha --jq .headSha)" = "$release_sha"

# Wait for completion and require all eleven expanded jobs to succeed.
gh run watch "$run_id" --exit-status
test "$(gh run view "$run_id" --json jobs --jq '.jobs | length')" = 11
test "$(gh run view "$run_id" --json jobs --jq \
  '[.jobs[] | select(.conclusion != "success")] | length')" = 0

# If it fails, check logs
gh run view "$run_id" --log-failed
```

The workflow file is part of the release trust boundary. Every external
`uses:` reference must remain pinned to a full 40-character commit SHA; the
trailing version comment is for humans.

### Stage 4: Tag and push — **RELEASER public write**
```bash
test "$(git rev-parse HEAD)" = "$release_sha"
git tag -a vX.Y.Z "$release_sha" -m "autorun vX.Y.Z"
git push origin vX.Y.Z
```

### Stage 5: Verify tag is on the right commit
```bash
test "$(git rev-list -n 1 vX.Y.Z)" = "$release_sha"
test "$(git ls-remote origin 'refs/tags/vX.Y.Z^{}' | cut -f1)" = "$release_sha"
```

### Stage 6: Create GitHub prerelease — **RELEASER public write**

The `--prerelease` flag is part of updater correctness. RC installs opt into
prereleases; stable installs filter them out. Use the reviewed release draft,
not generated notes, and make retries idempotent by inspecting first.

```bash
if gh release view vX.Y.Z >/dev/null 2>&1; then
  gh release view vX.Y.Z --json tagName,isDraft,isPrerelease,url
else
  gh release create vX.Y.Z --verify-tag --prerelease \
    --title "autorun vX.Y.Z" \
    --notes-file notes/YYYY-MM-DD-rc-release-draft.md
fi
test "$(gh release view vX.Y.Z --json isPrerelease --jq .isPrerelease)" = true
test "$(gh release view vX.Y.Z --json isDraft --jq .isDraft)" = false
```

### Recovery table

| State | Recovery |
|---|---|
| Before the tag is pushed | Delete/recreate the local tag after the fix and repeat every exact-SHA gate. |
| Remote tag exists, GitHub release absent | Stop. Do not move or delete the remote tag; fix on `main` and issue the next RC version. |
| GitHub prerelease exists and is correct | Treat a retry as success; verify its tag, commit, draft flag, and prerelease flag. |
| GitHub prerelease exists but content or artifacts are wrong | Do not replace immutable code under the tag. Correct prose in place only when code is unchanged; otherwise issue the next RC version. |
| Any public step partially succeeds | Inventory remote tag and release state before retrying. Never assume a failed command made no public write. |

## PyPI Publishing (future — not yet configured)

autorun is currently distributed from GitHub through the package subdirectory
and the Claude marketplace flow, not PyPI. If PyPI publishing is added in the
future, follow the pattern from the [AI Session Search release guide](https://github.com/ahundt/ai-session-search/blob/main/docs/development/releasing.md):

1. **Trusted Publishers** — configure on PyPI/TestPyPI with exact owner/repo/workflow/environment match
2. **GitHub Environments** — `testpypi` (auto-publish) + `pypi` (manual approval gate)
3. **SHA-pinned actions** — already enforced for `.github/workflows/ci.yml`
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
