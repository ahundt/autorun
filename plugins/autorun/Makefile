.PHONY: help test test-unit test-integration test-plugin test-hook test-compatibility test-all install-deps clean lint format

# Default target
help:
	@echo "clautorun Test Suite"
	@echo ""
	@echo "Available targets:"
	@echo "  test          - Run all tests with coverage"
	@echo "  test-unit     - Run unit tests only"
	@echo "  test-integration - Run integration tests only"
	@echo "  test-plugin    - Run plugin tests only"
	@echo "  test-hook     - Run hook tests only"
	@echo "  test-compatibility - Run compatibility tests only"
	@echo "  test-all      - Run all tests (default)"
	@echo "  test-quick    - Quick functionality check"
	@echo "  install-deps  - Install test dependencies"
	@echo "  clean         - Clean test artifacts"
	@echo "  lint          - Run linting"
	@echo "  format        - Format code"
	@echo ""
	@echo "Examples:"
	@echo "  make test              # Run all tests"
	@echo "  make test-unit         # Run unit tests only"
	@echo "  make test-coverage     # Run with coverage report"
	@echo "  make install-deps       # Install dependencies first"

# Install dependencies
install-deps:
	@echo "📦 Installing test dependencies..."
	pip install -e .[dev]

# Quick functionality test
test-quick:
	@echo "🚀 Running quick functionality test..."
	python run_tests.py --quick

# Run all tests with coverage (default)
test test-all:
	@echo "🧪 Running all tests with coverage..."
	python run_tests.py --coverage --all

# Run unit tests only
test-unit:
	@echo "🧪 Running unit tests..."
	python run_tests.py --unit --coverage

# Run integration tests only
test-integration:
	@echo "🧪 Running integration tests..."
	python run_tests.py --integration --coverage

# Run plugin tests only
test-plugin:
	@echo "🧪 Running plugin tests..."
	python run_tests.py --plugin --coverage

# Run hook tests only
test-hook:
	@echo "🧪 Running hook tests..."
	python run_tests.py --hook --coverage

# Run compatibility tests only
test-compatibility:
	@echo "🧪 Running compatibility tests..."
	python run_tests.py --compatibility

# Run tests with HTML coverage report
test-coverage:
	@echo "🧪 Running tests with HTML coverage report..."
	python run_tests.py --coverage --report=html

# Run tests with verbose output
test-verbose:
	@echo "🧪 Running tests with verbose output..."
	python run_tests.py --verbose --coverage

# Clean test artifacts
clean:
	@echo "🧹 Cleaning test artifacts..."
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name ".coverage" -exec rm -f {} + 2>/dev/null || true
	rm -rf htmlcov/ 2>/dev/null || true
	rm -rf .coverage 2>/dev/null || true
	rm -rf coverage.xml 2>/dev/null || true

# Run linting
lint:
	@echo "🔍 Running linting..."
	ruff check .
	ruff format --check .

# Format code
format:
	@echo "✨ Formatting code..."
	ruff format .

# Full CI pipeline
ci: install-deps clean lint test-all

# Development setup
dev-setup: install-deps
	@echo "🛠️ Development setup complete!"
	@echo ""
	@echo "Next steps:"
	@echo "  make test-quick      # Verify basic functionality"
	@echo "  make test           # Run full test suite"
	@echo "  make lint           # Check code quality"