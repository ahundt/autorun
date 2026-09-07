# Releasing autorun

The runbook for cutting a release: bump the version everywhere, prove the
candidate, publish it. Follow the steps in order; the reference material at the
end answers "which files" and "what went wrong last time".

## The release run, stage by stage

Work top to bottom: one-time setup, seven numbered stages, and a TestPyPI
rehearsal that sits between Stage 3 and Stage 4. Every public write is marked
**RELEASER**: do not push, tag, approve, or publish while preparing a candidate
on someone else's behalf.

Steps that have a website route give both it and the equivalent commands. They
do the same thing; the browser suits a step you want to look at before
committing to it, the commands suit inspection and repetition.

| Step | What it does |
|---|---|
| [Before you start](#before-you-start) | Register the PyPI and TestPyPI pending publishers and the two GitHub environments. Once per project. **RELEASER** |
| [Stage 1](#stage-1-version-bump) | Bump the version everywhere the [version sites](#current-inventory) table lists, and commit locally. |
| [Stage 2](#stage-2-pre-flight-checks) | Run every gate against one candidate SHA. Nothing public happens yet. |
| [Stage 3](#stage-3-push-the-candidate-and-wait-for-ci--releaser-public-write) | Push the candidate and wait for exact-SHA CI. **RELEASER** |
| [Rehearsal](#rehearse-on-testpypi-before-any-tag) | `gh workflow run publish.yml` uploads to TestPyPI only, proving OIDC before the tag. |
| [Stage 4](#stage-4-tag-and-push--releaser-public-write) | Create and push the annotated tag. **RELEASER** |
| [Stage 5](#stage-5-verify-the-tag-then-approve-the-tag-run--releaser-public-write) | Confirm the tag names the proven commit, then approve `pypi`. This approval is what publishes to PyPI, and the version becomes permanent. **RELEASER** |
| [Stage 6](#stage-6-create-github-prerelease--releaser-public-write) | Publish the GitHub prerelease from `docs/releases/<version>.md`. **RELEASER** |
| [Stage 7](#stage-7-verify-the-published-release-from-the-public-install-path) | Install from the public index and confirm the version answers. |

If a step fails after a public write, go to the [recovery table](#recovery-table)
before retrying: a partly-successful step may already have published something.

Everything below the [reference](#reference) divider is lookup material, not steps.

## Before you start

`.github/workflows/publish.yml` uploads one distribution with Trusted
Publishing (OIDC). No API token is stored anywhere.

| Distribution | Source directory | Installs as |
|--------------|------------------|-------------|
| `autorun-ai` | `plugins/autorun` | `uv tool install autorun-ai`, or `'autorun-ai[pdf]'` for PDF extraction |

There is no second package. `plugins/pdf-extractor` is a plugin in every
harness (its own manifest, command, and skill) while its code ships inside
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
`autorun-ai` with:

| Field | Value |
|-------|-------|
| Owner | `ahundt` |
| Repository | `autorun` |
| Workflow | `publish.yml` |
| Environment | `testpypi` on TestPyPI, `pypi` on PyPI |

Create `testpypi` and `pypi` at <https://github.com/ahundt/autorun/settings>,
**Environments** in the left sidebar, **New environment**, name it,
**Configure environment**. Give `pypi` a required reviewer so a tag push cannot
reach the real index unattended; `testpypi` needs no protection. Confirm both
environments before rehearsal:

```bash
names=$(gh api repos/ahundt/autorun/environments --jq \
  '[.environments[].name] | sort | join(" ")')
test "$names" = "pypi testpypi"
```

The GitHub API cannot confirm pending-publisher records on PyPI. The releaser
must verify both records in the two account pages before continuing.

**"Pending publishers" is the first-upload path only.** Once an upload creates
the project, the record becomes an ordinary publisher on the project itself and
nothing appears under Pending any more, so a later release that follows this
section looking for it finds an empty page and no explanation. After the first
publication, read the recorded identity from the file's provenance instead. It
needs no login and returns the repository, workflow and environment PyPI
actually accepted:

```bash
curl -sS "https://pypi.org/integrity/autorun-ai/$release_version/autorun_ai-$release_version-py3-none-any.whl/provenance" \
  | python3 -c 'import json,sys; a=json.load(sys.stdin)["attestation_bundles"][0]["publisher"]; print(a)'
```

## Release Workflow

Every public write below is marked **RELEASER**. Do not push, tag, or create a
GitHub release while preparing a candidate on someone else's behalf.

Every command in this checklist passes `--locked`. It is the same rule
`.github/workflows/ci.yml` follows and
`test_documentation_consistency.py::test_every_release_gate_environment_is_the_locked_one`
enforces on both files: a candidate is validated against the graph `uv.lock`
commits, never against a fresh resolution that the release will not ship. A
command that must run outside the lock has to say why, on the line.

### Stage 1: Version bump
Follow the file lists above and commit locally. Do not push yet.

### Stage 2: Pre-flight checks

These run on the machine you are sitting at, and prove that platform only. A
green local run is the precondition for pushing, not evidence the release is
sound: the Windows and Linux jobs exist because they fail on things macOS never
sees. Stage 3's exact-SHA CI run is the separate precondition for tagging.

```bash
# Capture one candidate commit and require every later check to name it.
release_sha=$(git rev-parse HEAD)
git fetch origin main --tags
test -z "$(git status --porcelain=v1)"

# Run from the plugin directories, matching CI discovery/configuration.
(cd plugins/autorun && uv run --project . --locked pytest tests/ -m "not tmux and not e2e and not release" -v)
# The PDF tests stay with their plugin, but the code they import ships in the
# autorun-ai distribution and `plugins/pdf-extractor` deliberately has no
# pyproject.toml — so the environment must come from the autorun project, with
# `--extra pdf` for the backends. Running it as its own project resolves the
# workspace root instead and collects four `ModuleNotFoundError: pdf_extraction`.
uv run --project plugins/autorun --locked --extra pdf pytest plugins/pdf-extractor/tests/ -v
(cd plugins/autorun && uv run --project . --locked pytest tests/test_release_artifacts.py -m release -v)
(cd plugins/autorun && AUTORUN_ENABLE_STATE_BENCHMARK=1 uv run --project . --locked pytest tests/test_state_store_benchmark.py -m benchmark -v)

# CONFIG must still load after a version bump. No test above imports it at the
# top level, so a broken table reaches the release as an import error.
uv run --project plugins/autorun --locked python -c "from autorun.config import DEFAULT_INTEGRATIONS; print(len(DEFAULT_INTEGRATIONS))"
# ─────────────────────────────────────────────────────────────────────────────
# LIVE INSTALL BOUNDARY. Everything above this line runs against the checkout
# and a scratch home. The next command is the one exception in this checklist:
# it publishes the candidate to every harness detected on THIS machine and
# restarts nothing, so every session already attached keeps its old hook until
# it restarts. Run it only as the releasing maintainer, on your own machine,
# when you intend that. An agent must not run it without your written
# instruction in the current conversation naming this command — see the
# isolation rule in AGENTS.md §1, which governs every other install here.
#
# It exists because the harness loads the plugin *cache*, not the source, so a
# fix to hooks/hook_entry.py or hooks/hooks.json that never reaches the cache is
# invisible: the live hook keeps running the previous file with nothing to say
# so. The opt-in check below is what reads the cache, and it is meaningless
# without this. To rehearse instead of committing, run the same command with
# HOME, USERPROFILE, PI_CODING_AGENT_DIR, AUTORUN_HOME and
# AUTORUN_TEST_STATE_DIR redirected to a scratch tree, as the marketplace
# rehearsal below in this stage does, and skip the cache check.
uv run --project plugins/autorun --locked python -m autorun --install --force
# ─────────────────────────────────────────────────────────────────────────────
# Hook configuration is read once at session start, so a hook change takes
# effect in the NEXT session, not this one.

# Opt-in, and only meaningful after that reinstall: confirms the Claude plugin
# cache and the installed Gemini extension actually carry the hook files in
# this tree. Skipped by default so an ordinary source edit does not fail the
# suite before the reinstall.
(cd plugins/autorun && AUTORUN_ENABLE_LIVE_INSTALL_CHECKS=1 uv run --project . --locked pytest tests/test_hook_entry.py -k "cache_matches_source or gemini_extension_hooks_match" -v)

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

Browser: <https://github.com/ahundt/autorun/actions/workflows/ci.yml>, newest
run. Check its commit against `$release_sha` before reading the result; a run
for an earlier push looks identical at a glance.

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
  --extra-index-url https://pypi.org/simple/ autorun-ai
UV_TOOL_DIR=$(mktemp -d) UV_TOOL_BIN_DIR=$(mktemp -d) \
  uv tool install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ 'autorun-ai[pdf]'
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

# The tag body below is the notes file byte-for-byte, and a pushed tag cannot be
# moved. A version bump prepared days before the tag day therefore bakes a stale
# date into the annotation and into the release page, permanently. 421732cc had
# to move a release date for this reason; these two checks are what make the
# next one fail loudly instead. `test_release_notes_name_upgrade_actions_date`
# only requires the date to be in the past, which a stale date also satisfies.
test "$(rg -N -o -r '$1' '^Date: (.+)$' "docs/releases/$release_version.md")" = "$(date +%F)"
rg -N -q "^## \[$release_version\] - $(date +%F)\$" CHANGELOG.md

# -F, not -m: the annotated tag carries the release body, so `git show` gives the
# notes to anyone with a clone and they do not live only on GitHub.
#
# --cleanup=verbatim is what makes that true. git's default for -F is
# --cleanup=strip, which reads a leading '#' as a comment and deletes the line,
# so plain -F silently removes the title, every '##' section heading, and the
# '#' comments inside the install snippet, then collapses blank runs. Measured
# on this file: 155 lines in, 137 out, all 10 '#' lines gone. With verbatim the
# annotation is byte-identical to the source.
git tag -a "$release_tag" "$release_sha" --cleanup=verbatim \
  -F "docs/releases/$release_version.md"

# Prove the round trip before pushing, while the tag is still local and cheap to
# redo. After the push the recovery table forbids moving it.
diff <(git cat-file tag "$release_tag" | sed -n '6,$p') \
     "docs/releases/$release_version.md"

git push origin "$release_tag"
```

The first tag pushed under this runbook predates that flag, so its annotation is
the stripped form. It was left alone rather than retagged: the commit under it is
correct, the published release body comes from `--notes-file` and is unaffected,
and moving a pushed tag is the one thing the recovery table below rules out.

### Stage 5: Verify the tag, then approve the tag run — **RELEASER public write**

```bash
test "$(git rev-list -n 1 "$release_tag")" = "$release_sha"
test "$(git ls-remote origin "refs/tags/$release_tag^{}" | cut -f1)" = "$release_sha"
```

The tag push starts `publish.yml`, which runs CI, uploads to TestPyPI, installs
the wheel back from TestPyPI, and then waits on the `pypi` environment's
required reviewer. Approving is the irreversible half of the release: PyPI
versions are immutable, so inspect the run first.

Browser: <https://github.com/ahundt/autorun/actions/workflows/publish.yml>, open
the tag run. The run page carries a banner reading **"<owner> requested your
review to deploy to pypi"** with a **Review deployments** button, and the parked
job shows as `publish-pypi` / **"pypi waiting for review"**. Use the banner on
the run page itself, not the waiting job. In the dialog it opens, tick `pypi`
and choose **Approve and deploy**.

Everything named above was read off the live gate on 2026-08-23, when
`v1.0.0rc1` became the first tag to reach it. The dialog's own two labels are
the exception and still come from GitHub's review-deployments documentation,
because opening that dialog is a step the releaser takes, not one to rehearse.
Treat the `pending_deployments` commands below as authoritative if the wording
differs.

Commands, for inspection and for repeating the same step without the browser.
The approval call needs a token carrying `repo` and `workflow` scope;
`current_user_can_approve` tells you before you post whether it will work.

**Do not reach for `gh run view --log` here.** GitHub serves a run's logs only
once the whole run is finished, and a run parked at this gate is by definition
unfinished, so the request answers `run <id> is still in progress; logs will be
available when it is complete`. Read the step conclusions instead, and prove the
rehearsal against TestPyPI directly.

```bash
run_id=$(gh run list --workflow publish.yml --branch "$release_tag" \
  --limit 1 --json databaseId --jq '.[0].databaseId')
gh run view "$run_id"                      # which jobs passed, which is waiting

# Which rehearsal steps ran, readable while the run is still parked.
gh run view "$run_id" --json jobs --jq \
  '.jobs[] | select(.name=="publish-testpypi") | .steps[] | "\(.conclusion)\t\(.name)"'

# Stronger than any log line: install the rehearsed wheel and run its CLI. An
# accepted upload only proves TestPyPI took the file.
sandbox=$(mktemp -d /tmp/arv.XXXX)
uv venv --python 3.13 "$sandbox/venv"
uv pip install --python "$sandbox/venv/bin/python" \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  "autorun-ai==$release_version"
env HOME="$sandbox/home" AUTORUN_HOME="$sandbox/ar-home" \
  "$sandbox/venv/bin/autorun" --version   # expect $release_version

# Repeat against the sdist. The wheel is prebuilt, so installing it proves
# nothing about the build backend, and this project's backend is the unusual
# part: build_support.py reaches into the sibling pdf-extractor plugin to put
# pdf_extraction inside the one distribution. Only an sdist install runs it, and
# only these users hit it -- --no-binary, an unsupported platform, a policy that
# forbids wheels.
uv venv --python 3.13 "$sandbox/sdist-venv"
uv pip install --python "$sandbox/sdist-venv/bin/python" \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  --no-binary autorun-ai "autorun-ai==$release_version"
"$sandbox/sdist-venv/bin/python" -c "import autorun, pdf_extraction"

# What is pending, and whether you may approve it.
gh api "repos/ahundt/autorun/actions/runs/$run_id/pending_deployments" \
  --jq '.[] | {environment: .environment.name, can_approve: .current_user_can_approve}'

# Approve, naming the environment id the call above reported.
env_id=$(gh api "repos/ahundt/autorun/actions/runs/$run_id/pending_deployments" \
  --jq '.[] | select(.environment.name=="pypi") | .environment.id')
gh api --method POST \
  "repos/ahundt/autorun/actions/runs/$run_id/pending_deployments" \
  -f state=approved -F "environment_ids[]=$env_id" -f comment="rc verified"
```

### Stage 6: Create GitHub prerelease — **RELEASER public write**

Every pushed `v*` tag gets a release page, release candidates included.
`installer/entrypoint.py:_latest_version` enumerates
`https://api.github.com/repos/ahundt/autorun/releases`, not tags, so a tag with
no release page is invisible to `autorun --self-update` no matter how it is
flagged, and it offers the reader no reviewed notes. This stage is idempotent
and independent of the calendar: if a tag was pushed and the release page was
never made, run it for that tag now rather than folding the tag into the next
candidate.

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

Browser: <https://github.com/ahundt/autorun/releases/new> (or the **Draft a new
release** button on the releases page). Labels below were read off the live form
on 2026-08-22, because GitHub's own documentation for this page is stale: it
describes a "This is a pre-release" checkbox that the form does not have.

1. **Tag: Select tag** — pick the tag Stage 4 already pushed. The control will
   also create a tag on publish ("Choose an existing tag, or create a new tag
   when you publish this release"), and that is the one thing to avoid here: a
   tag made this way is lightweight and carries none of the release body, which
   is exactly what Stage 4's `git tag -a -F` exists to put in it.
2. Confirm **Target: main**.
3. Fill **Release title**, and paste `docs/releases/<version>.md` into
   **Release description**. Leave **Generate release notes** alone; the reviewed
   file is the source, not GitHub's commit summary.
4. Under **Release label**, select the **Pre-release** radio rather than
   **None**. It is a two-option radio group, so leaving it at the default
   publishes a production release.
5. **Publish release**, or **Save draft** to look at it first.

`gh release create` has no `--web` flag, so this URL is the route rather than a
shortcut to it.

Commands. Use the reviewed release draft, not generated notes, and make retries
idempotent by inspecting first.

**autorun blocks its own release command.** Every maintainer of this repository
runs autorun, and its safety guard answers `gh release create` with `Command
blocked: gh release create` / `The user requires explicit permission before
creating releases`. That is the guard working, not a broken checklist, and it
lands on an agent-driven release rather than on a human typing the command. Grant
it for the attempt, or take the browser route above:

```bash
/ar:ok 'gh release create'
```

```bash
if gh release view "$release_tag" >/dev/null 2>&1; then
  gh release view "$release_tag" --json tagName,isDraft,isPrerelease,url
else
  gh release create "$release_tag" --verify-tag --prerelease \
    --title "autorun $release_tag" \
    --notes-file "docs/releases/$release_version.md"
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
| Remote tag exists, GitHub release absent, tag content correct | Finish Stage 6 for that tag, however late. Every pushed `v*` tag gets its release page, release candidates included. |
| Remote tag exists but the code under it is wrong | Do not move or delete the remote tag. Publish its release page anyway so the tag is not a silent artifact, then fix on `main` and issue the next RC version. |
| GitHub prerelease exists and is correct | Treat a retry as success; verify its tag, commit, draft flag, and prerelease flag. |
| GitHub prerelease exists but content or artifacts are wrong | Do not replace immutable code under the tag. Correct prose in place only when code is unchanged; otherwise issue the next RC version. |
| Any public step partially succeeds | Inventory remote tag and release state before retrying. Never assume a failed command made no public write. |

---

# Reference

Lookup material. Nothing below here is a step in the release run.

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
# 4. Run tests: uv run --project plugins/autorun --locked pytest plugins/autorun/tests/ -v
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
inside the `autorun-ai` distribution as `pdf_extraction` behind the `pdf` extra.

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
| `RELEASING.md` | No current-version field; update only examples that intentionally track the release |
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

These are minimum versions. Bump them only for breaking changes, not patch releases. See Gotcha #3.

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