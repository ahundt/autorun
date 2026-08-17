# Thin aliases over the commands README.md, AGENTS.md, .github/workflows/ci.yml
# and docs/version_update_checklist.md already document. This file adds short
# names and nothing else — no second test driver, no second dependency set.
#
# It used to have both. Every test target shelled out to `run_tests.py`, which
# `.gitignore` excludes, so a fresh clone got "can't open file"; and `install-deps`
# ran `pip install -e .[dev]` after the `dev` extra was replaced by
# `[dependency-groups].dev`, which pip reports as a warning and an exit code of
# zero. `make ci` therefore installed nothing, ran no tests, and said so in
# neither case.
#
# Run from this directory. `uv sync` puts the interpreter in the workspace
# environment, and `uv run` is what finds it.
.PHONY: help install-deps test test-all test-unit test-integration test-plugin \
        test-hook test-quick test-coverage test-verbose test-release \
        test-benchmark clean lint format ci dev-setup

UV_RUN := uv run --project . --locked
PYTEST := $(UV_RUN) pytest

help:
	@echo "autorun development targets"
	@echo ""
	@echo "  install-deps  - Sync the locked dev environment (pytest, ruff, xdist)"
	@echo "  test          - Run the suite CI runs (excludes tmux, e2e, release)"
	@echo "  test-all      - Run every collected test, including tmux and e2e"
	@echo "  test-unit     - Tests marked 'unit'"
	@echo "  test-integration - Tests marked 'integration'"
	@echo "  test-plugin   - Tests marked 'plugin'"
	@echo "  test-hook     - Tests marked 'hook'"
	@echo "  test-release  - Build and install release artifacts (marker 'release')"
	@echo "  test-benchmark - Storage cost measurements (marker 'benchmark')"
	@echo "  test-quick    - Import and configuration smoke check"
	@echo "  test-coverage - Full suite with an HTML coverage report"
	@echo "  test-verbose  - Full suite with per-test output"
	@echo "  clean         - Remove caches and coverage artifacts"
	@echo "  lint / format - ruff check + ruff format"
	@echo "  ci            - install-deps, clean, lint, test"
	@echo ""
	@echo "Every target runs against the committed uv.lock, as CI does."

# The one definition of the dev environment is [dependency-groups].dev, which
# `uv sync` includes by default; --dev is explicit so the intent survives a
# future default change. --all-extras matches CI so the PDF backends are present.
install-deps:
	uv sync --project . --locked --dev --all-extras

# The default: the same selection .github/workflows/ci.yml runs, so a green
# `make test` means the same thing a green matrix job does.
test:
	$(PYTEST) tests/ -m "not tmux and not e2e and not release"

test-all:
	$(PYTEST) tests/

test-unit:
	$(PYTEST) tests/ -m unit

test-integration:
	$(PYTEST) tests/ -m integration

test-plugin:
	$(PYTEST) tests/ -m plugin

test-hook:
	$(PYTEST) tests/ -m hook

test-release:
	$(PYTEST) tests/test_release_artifacts.py -m release

test-benchmark:
	AUTORUN_ENABLE_STATE_BENCHMARK=1 $(PYTEST) tests/test_state_store_benchmark.py -m benchmark

# Cheapest possible answer to "is this checkout importable and configured?".
# CONFIG and the integration table are what every hook path reads first.
#
# `sys.path.pop(0)` drops the `-c` entry for the current directory, which is
# this one: `autorun.py` sits here and shadows the installed `autorun` package,
# so the import fails with "'autorun' is not a package". pytest does not hit
# this because it rewrites sys.path from rootdir.
test-quick:
	$(UV_RUN) python -c "import sys; sys.path.pop(0); \
	    from autorun.config import CONFIG, DEFAULT_INTEGRATIONS; \
	    from autorun.plugins import app; \
	    assert CONFIG['completion_marker'] and DEFAULT_INTEGRATIONS and app; \
	    print('imports and configuration OK')"

test-coverage:
	$(PYTEST) tests/ -m "not tmux and not e2e and not release" \
	    --cov=src/autorun --cov-report=html --cov-report=term-missing

test-verbose:
	$(PYTEST) tests/ -m "not tmux and not e2e and not release" -v

clean:
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf htmlcov/ .coverage coverage.xml 2>/dev/null || true

# The same two passes .github/workflows/ci.yml runs, in the same order: the
# narrow selection blocks, the full one reports. `ruff format --check` is not
# here for the reason the workflow gives — 150 of 153 files predate ruff format,
# so it fails everywhere until a dedicated reformatting commit lands. `make
# format` is how you would make that commit.
lint:
	$(UV_RUN) ruff check --select E9,F63,F7,F82 .
	$(UV_RUN) ruff check --exit-zero --statistics .

format:
	$(UV_RUN) ruff format .

ci: install-deps clean lint test

dev-setup: install-deps
	@echo "Development environment synced."
	@echo ""
	@echo "Next steps:"
	@echo "  make test-quick   # imports and configuration"
	@echo "  make test         # the suite CI runs"
	@echo "  make lint         # ruff"
