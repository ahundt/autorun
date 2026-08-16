# Version Update Checklist

When updating versions in the autorun marketplace, use this checklist to ensure all locations are updated consistently.

## Unified Versioning

All plugins in this marketplace use the **same version number** for consistency. When releasing a new version, update ALL plugins to the same version.

The root `.claude-plugin/marketplace.json` catalog version is intentionally the
stable release line, not a third plugin version. Its plugin entries carry the
full package version, including an RC suffix; the catalog's top-level field
omits that suffix. For example, `1.0.0rc1` plugin entries belong to catalog
`1.0.0`. Change the catalog field only when the base release line changes.

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
| `.claude-plugin/marketplace.json` | plugin entries use `"version": "X.Y.Z"` | The top-level catalog uses the stable base version with any RC suffix removed. |

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

### pdf-extractor Plugin (3+ files)

It has no `pyproject.toml`: the plugin is a harness plugin, and its code ships
inside the `autorun` distribution as `pdf_extraction` behind the `pdf` extra.

| File | Field/Pattern | Notes |
|------|---------------|-------|
| `plugins/pdf-extractor/src/pdf_extraction/__init__.py` | `__version__ = "X.Y.Z"` | Do NOT change `pdfplumber>=0.10.0` in `plugins/autorun/pyproject.toml` — that's a third-party dep! |
| `plugins/pdf-extractor/.claude-plugin/plugin.json` | `"version": "X.Y.Z"` | |
| `plugins/pdf-extractor/gemini-extension.json` | `"version": "X.Y.Z"` | |

### Documentation (7+ files)

| File | Notes |
|------|-------|
| `README.md` | Section headers, install verification examples |
| `CHANGELOG.md` | Add the dated release section |
| `docs/releases/1.0.0rc1.md` | Canonical GitHub Release body; replace this path and its heading for the next release |
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
- `plugins/autorun/pyproject.toml` — `pdfplumber>=0.10.0` in the `pdf` extra
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

`pyproject.toml` has `autorun[pdf,pdf-gpu,pdf-llm,pdf-progress]>=0.10.0` in `[project.optional-dependencies]`. That is a **minimum** version requirement. Only bump it for breaking changes, not patch releases.

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
    "autorun[pdf,pdf-gpu,pdf-llm,pdf-progress]>=X.Y.Z",
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
# The PDF tests stay with their plugin, but the code they import ships in the
# autorun distribution and `plugins/pdf-extractor` deliberately has no
# pyproject.toml — so the environment must come from the autorun project, with
# `--extra pdf` for the backends. Running it as its own project resolves the
# workspace root instead and collects four `ModuleNotFoundError: pdf_extraction`.
uv run --project plugins/autorun --extra pdf --extra dev pytest plugins/pdf-extractor/tests/ -v
(cd plugins/autorun && uv run --project . pytest tests/test_release_artifacts.py -m release -v)
(cd plugins/autorun && AUTORUN_ENABLE_STATE_BENCHMARK=1 uv run --project . pytest tests/test_state_store_benchmark.py -m benchmark -v)
# Reinstall this machine from the candidate before the check below. The
# harness loads the plugin *cache*, not the source, so a fix to
# hooks/hook_entry.py or hooks/hooks.json that never reaches the cache is
# invisible: the live hook keeps running the previous file with nothing to say
# so. This publishes the candidate to every detected harness.
uv run --project plugins/autorun python -m autorun --install --force
# Hook configuration is read once at session start, so a hook change takes
# effect in the NEXT session, not this one.

# Opt-in, and only meaningful after that reinstall: confirms the Claude plugin
# cache and the installed Gemini extension actually carry the hook files in
# this tree. Skipped by default so an ordinary source edit does not fail the
# suite before the reinstall.
(cd plugins/autorun && AUTORUN_ENABLE_LIVE_INSTALL_CHECKS=1 uv run --project . pytest tests/test_hook_entry.py -k "cache_matches_source or gemini_extension_hooks_match" -v)

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
env HOME="$scratch_root/home" USERPROFILE="$scratch_root/home" \
  CLAUDE_CONFIG_DIR="$scratch_root/home/.claude" \
  AUTORUN_HOME="$scratch_root/autorun-home" \
  AUTORUN_TEST_STATE_DIR="$scratch_root/state" \
  claude plugin marketplace add "$scratch_root/checkout" --scope user
env HOME="$scratch_root/home" USERPROFILE="$scratch_root/home" \
  CLAUDE_CONFIG_DIR="$scratch_root/home/.claude" \
  AUTORUN_HOME="$scratch_root/autorun-home" \
  AUTORUN_TEST_STATE_DIR="$scratch_root/state" \
  claude plugin install ar@autorun --scope user
env HOME="$scratch_root/home" USERPROFILE="$scratch_root/home" \
  CLAUDE_CONFIG_DIR="$scratch_root/home/.claude" \
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

## PyPI prerelease setup

`.github/workflows/publish.yml` uploads one distribution with Trusted
Publishing (OIDC). No API token is stored anywhere.

| Distribution | Source directory | Installs as |
|--------------|------------------|-------------|
| `autorun` | `plugins/autorun` | `uv tool install autorun`, or `'autorun[pdf]'` for PDF extraction |

There is no second package. `plugins/pdf-extractor` is a plugin in every
harness — its own manifest, command, and skill — while its code ships inside
this wheel as `pdf_extraction` behind the `pdf` extra.
`test_documentation_consistency.py::test_the_pdf_plugin_ships_inside_autorun_and_not_beside_it`
holds that in place, and the publish workflow fails the build if the wheel stops
carrying `pdf_extraction`, `extract-pdfs`, or the pdf extras.

### One-time setup — **RELEASER, account access required**

Two registrations are required: one project on each of two indexes. Until the
first upload, register the project as a pending publisher
([PyPI documentation](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)).

At <https://test.pypi.org/manage/account/publishing/> and
<https://pypi.org/manage/account/publishing/>, add a pending publisher for
`autorun` with:

| Field | Value |
|-------|-------|
| Owner | `ahundt` |
| Repository | `autorun` |
| Workflow | `publish.yml` |
| Environment | `testpypi` on TestPyPI, `pypi` on PyPI |

Create `testpypi` and `pypi` under GitHub Settings -> Environments. Give `pypi`
a required reviewer so a tag push cannot reach the real index unattended;
`testpypi` needs no protection. Confirm both environments before rehearsal:

```bash
names=$(gh api repos/ahundt/autorun/environments --jq \
  '[.environments[].name] | sort | join(" ")')
test "$names" = "pypi testpypi"
```

The GitHub API cannot confirm pending-publisher records on PyPI. The releaser
must verify both records in the two account pages before continuing.

### Rehearse on TestPyPI before any tag

Run this only after Stage 3 has pushed `publish.yml` and exact-SHA CI is green.
`workflow_dispatch` builds and publishes to TestPyPI only; its
`testpypi_only` input defaults to true, so the rehearsal cannot reach PyPI:

```bash
gh workflow run publish.yml --ref main
```

Require the workflow to succeed, then install it both ways in throwaway tool
directories so the rehearsal cannot disturb the installed CLIs. Install the
plain form too: it is the one that proves no extraction library is required.

```bash
UV_TOOL_DIR=$(mktemp -d) UV_TOOL_BIN_DIR=$(mktemp -d) \
  uv tool install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ autorun
UV_TOOL_DIR=$(mktemp -d) UV_TOOL_BIN_DIR=$(mktemp -d) \
  uv tool install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ 'autorun[pdf]'
```

TestPyPI refuses a re-upload of an existing file. The workflow passes
`skip-existing: true` there so an identical rerun succeeds; PyPI remains strict.
A changed artifact needs a new version and a complete restart from Stage 1.

### Stage 4: Tag and push — **RELEASER public write**

Derive the tag from the declared version instead of typing it. `v1.0.0-rc1` for
`1.0.0rc1` is a plausible slip, and a wrong tag on a public repository is one of
the writes the recovery table below says to stop on rather than correct in
place. `test_every_release_identity_field_declares_the_same_version` separately
guarantees that every manifest agrees with the field read here, so one substitution
covers them all.

```bash
test "$(git rev-parse HEAD)" = "$release_sha"
release_version=$(rg -N -o -r '$1' '^version = "(.+)"' plugins/autorun/pyproject.toml)
test -n "$release_version"
release_tag="v$release_version"

git tag -a "$release_tag" "$release_sha" -m "autorun $release_tag"
git push origin "$release_tag"
```

### Stage 5: Verify tag is on the right commit
```bash
test "$(git rev-list -n 1 "$release_tag")" = "$release_sha"
test "$(git ls-remote origin "refs/tags/$release_tag^{}" | cut -f1)" = "$release_sha"
```

### Stage 6: Create GitHub prerelease — **RELEASER public write**

The `--prerelease` flag is part of updater correctness. It governs **self-update
only**: `installer/entrypoint.py` sets `allow_prerelease` from whether the
*installed* version contains a letter, then filters candidates on the release's
`prerelease` field, so an RC published without the flag is offered to stable
installs as an ordinary upgrade.

It does not govern fresh installs, and that is expected rather than a marking
failure. `claude plugin marketplace add` follows the default branch, so a fresh
install takes whatever `main` holds regardless of any release flag. Both halves
are true at once: a stable user is not pulled onto an RC by the updater, while
anyone installing from scratch during an RC window gets the RC.

Use the reviewed release draft, not generated notes, and make retries idempotent
by inspecting first.

```bash
if gh release view "$release_tag" >/dev/null 2>&1; then
  gh release view "$release_tag" --json tagName,isDraft,isPrerelease,url
else
  gh release create "$release_tag" --verify-tag --prerelease \
    --title "autorun $release_tag" \
    --notes-file docs/releases/1.0.0rc1.md
fi
test "$(gh release view "$release_tag" --json isPrerelease --jq .isPrerelease)" = true
test "$(gh release view "$release_tag" --json isDraft --jq .isDraft)" = false
```

### Stage 7: Verify the published release from the public install path

Stage 2's rehearsal installs from a local worktree, which is the right pre-tag
check but is not the command the release notes give users. Run that command
against the published state, in a scratch `HOME`, never `~/.claude`:

```bash
verify_root=$(mktemp -d "${TMPDIR:-/tmp}/autorun-verify.XXXXXX")
mkdir -p "$verify_root/home"
env HOME="$verify_root/home" USERPROFILE="$verify_root/home" \
  CLAUDE_CONFIG_DIR="$verify_root/home/.claude" \
  AUTORUN_HOME="$verify_root/autorun-home" \
  AUTORUN_TEST_STATE_DIR="$verify_root/state" \
  claude plugin marketplace add https://github.com/ahundt/autorun.git --scope user
env HOME="$verify_root/home" USERPROFILE="$verify_root/home" \
  CLAUDE_CONFIG_DIR="$verify_root/home/.claude" \
  AUTORUN_HOME="$verify_root/autorun-home" \
  AUTORUN_TEST_STATE_DIR="$verify_root/state" \
  claude plugin install ar@autorun --scope user
```

The installed plugin's version must equal `$release_version`, and the plugin
must register as `ar` in marketplace `autorun`. Remove the scratch directory once
its contents have been inspected.

### Recovery table

| State | Recovery |
|---|---|
| Before the tag is pushed | Delete/recreate the local tag after the fix and repeat every exact-SHA gate. |
| Remote tag exists, GitHub release absent | Stop. Do not move or delete the remote tag; fix on `main` and issue the next RC version. |
| GitHub prerelease exists and is correct | Treat a retry as success; verify its tag, commit, draft flag, and prerelease flag. |
| GitHub prerelease exists but content or artifacts are wrong | Do not replace immutable code under the tag. Correct prose in place only when code is unchanged; otherwise issue the next RC version. |
| Any public step partially succeeds | Inventory remote tag and release state before retrying. Never assume a failed command made no public write. |

## PyPI tag publication

A `v*` tag push runs full CI against the tagged commit, then TestPyPI, then
PyPI. The build jobs require the tag to match both package versions and require
each distribution name to match its workflow matrix entry. Complete the PyPI
prerelease setup and TestPyPI rehearsal before Stage 4.

## Build Artifacts

Remove stale build directories after version updates:

```bash
trash plugins/autorun/build/
trash plugins/pdf-extractor/build/
```

These contain cached code with old versions and can cause confusion.

## Deleted Plugins

- **plan-export** — merged into autorun plugin. Skip all plan-export references.
