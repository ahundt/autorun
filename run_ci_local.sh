#!/usr/bin/env bash
# run_ci_local.sh - Run CI checks locally (mirrors .github/workflows/ci.yml)
#
# Usage:
#   ./run_ci_local.sh
#
# Runs the same steps as CI in order:
#   1. uv sync --locked --dev --all-extras (install deps)
#   2. Python version verification
#   3. ruff critical errors (blocking)
#   4. ruff format check (blocking)
#   5. ruff full check (non-blocking, informational)
#   6. actionlint (validate CI workflow syntax)
#   7. pytest safe subset with STRICT marker exclusions

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Terminal colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

FAILED_COUNT=0
PASSED_COUNT=0
FAILED_NAMES=""

step() {
    local name="$1"
    shift
    echo ""
    echo -e "${BOLD}=== $name ===${NC}"
    if "$@"; then
        echo -e "${GREEN}✓ PASSED: $name${NC}"
        PASSED_COUNT=$((PASSED_COUNT + 1))
    else
        echo -e "${RED}✗ FAILED: $name${NC}"
        FAILED_COUNT=$((FAILED_COUNT + 1))
        FAILED_NAMES="$FAILED_NAMES\n  ✗ $name"
    fi
}

step_nonblocking() {
    local name="$1"
    shift
    echo ""
    echo -e "${BOLD}=== $name (non-blocking) ===${NC}"
    if "$@"; then
        echo -e "${GREEN}✓ PASSED: $name${NC}"
    else
        echo -e "${YELLOW}⚠ ISSUES: $name (informational only, not blocking)${NC}"
    fi
    PASSED_COUNT=$((PASSED_COUNT + 1))
}

echo -e "${BOLD}=== autorun Local CI ===${NC}"
echo "Working directory: $SCRIPT_DIR"

# Step 1: Install dependencies
# UV workspace with committed uv.lock -- use --locked to reproduce exact environment.
# --dev installs [dependency-groups] dev (pytest, pytest-asyncio, pytest-cov, etc.)
# --all-extras also installs [project.optional-dependencies] extras.
step "Install dependencies (uv sync --locked --dev --all-extras)" \
    uv sync --locked --dev --all-extras

# Step 2: Verify Python version
step "Verify Python version" \
    uv run python --version

# Step 3: Lint - critical errors only (blocking: syntax errors, undefined names)
# E9=syntax, F63/F7/F82=undefined names/imports
step "Lint - critical errors (blocking)" \
    uvx ruff check --select E9,F63,F7,F82 .

# Step 4: Lint - format check (non-blocking: codebase not uniformly formatted yet)
step_nonblocking "Lint - format check (informational)" \
    uvx ruff format --check .

# Step 5: Lint - full check (non-blocking: style, imports, etc.)
step_nonblocking "Lint - full check (informational)" \
    uvx ruff check .

# Step 6: Validate CI workflow syntax
if command -v actionlint &>/dev/null; then
    step "Validate CI workflow (actionlint)" \
        actionlint .github/workflows/ci.yml
else
    echo ""
    echo -e "${YELLOW}⚠ actionlint not found -- skipping workflow validation${NC}"
    echo "  Install with: brew install actionlint"
fi

# Step 7: Safe unit test subset
# Excludes tests requiring real tmux sessions, running daemon, subprocess spawning,
# external APIs, or >5s wall-clock time. All excluded markers are declared in
# plugins/autorun/pyproject.toml under [tool.pytest.ini_options] markers.
# No CLAUDE_CONFIG_DIR needed -- autorun tests don't use Claude session fixtures.
# Coverage threshold (fail_under=60) not applied -- run 'make test-all' for full coverage.
step "Unit tests (safe CI subset)" \
    uv run pytest tests/ \
        -m "not tmux and not daemon and not subprocess and not e2e and not interactive and not stress and not race and not slow" \
        --tb=short -v \
        --junitxml=test_results_local.xml

# Summary
echo ""
echo -e "${BOLD}=== Summary ===${NC}"
echo -e "${GREEN}Passed: $PASSED_COUNT${NC}"
if [ "$FAILED_COUNT" -gt 0 ]; then
    echo -e "${RED}Failed: $FAILED_COUNT${NC}"
    echo -e "${RED}$FAILED_NAMES${NC}"
    echo ""
    exit 1
else
    echo -e "${GREEN}All checks passed!${NC}"
fi
