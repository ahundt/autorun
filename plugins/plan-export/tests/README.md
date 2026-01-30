# Plan Export Race Condition Tests

Comprehensive TDD test suite for the plan export race condition fix.

## Safety First

**CRITICAL**: These tests are designed with complete isolation from active user sessions:

- ✅ **Never uses real `~/.claude/sessions/`** - All test state is in temporary directories
- ✅ **Unique test IDs** - Each test gets a unique UUID prefix
- ✅ **Comprehensive cleanup** - All temporary files, directories, and locks are cleaned up
- ✅ **Synthetic test data** - Mock plan files with JSON test data, not real user plans
- ✅ **Automatic cleanup** - pytest `tmp_path` fixture ensures cleanup even if tests fail

## Test Categories

### 1. Baseline Functionality Tests (`TestBaselineFunctionality`)
Tests basic single-session export behavior:
- Single session export success
- Missing session_id handling
- Export when disabled

### 2. Concurrent Same-Session Tests (`TestConcurrentSameSession`)
Tests that same-session concurrent exports are properly serialized:
- Lock serialization verification
- Lock timeout scenarios
- No cross-contamination

### 3. Multi-Session Race Condition Tests (`TestMultiSessionRaceConditions`)
**PRIMARY TESTS** - Designed to trigger the original bug:
- Concurrent exports from different sessions
- Random timing variations
- Verifies no cross-contamination occurs

### 4. Stress Test Scenarios (`TestStressScenarios`)
High-concurrency stress tests:
- Rapid sequential exports (20 rapid exports)
- High concurrency stress (20 sessions × 3 exports = 60 concurrent operations)
- Deadlock prevention tests

### 5. Cleanup Verification Tests (`TestCleanupVerification`)
Verifies proper resource cleanup:
- Lock file cleanup
- Exception-safe cleanup
- Temporary file isolation

## Installation

```bash
# Install test dependencies
pip install -r tests/requirements.txt

# Or with UV (recommended)
uv pip install -r tests/requirements.txt
```

## Running Tests

### Basic Test Run
```bash
# Run all tests
cd tests
pytest test_race_condition_fix.py -v

# Run with detailed output
pytest test_race_condition_fix.py -v -s
```

### Parallel Execution (Recommended)
```bash
# Run with pytest-xdist for parallel execution
pytest test_race_condition_fix.py -n auto

# Specify number of workers
pytest test_race_condition_fix.py -n 4
```

### Run Specific Test Categories
```bash
# Run only baseline tests
pytest test_race_condition_fix.py::TestBaselineFunctionality -v

# Run only stress tests
pytest test_race_condition_fix.py::TestStressScenarios -v

# Run specific test
pytest test_race_condition_fix.py::TestMultiSessionRaceConditions::test_concurrent_different_sessions -v
```

### Coverage Report
```bash
# Generate coverage report
pytest test_race_condition_fix.py --cov=../scripts/plan_export --cov-report=term-missing

# Generate HTML coverage report
pytest test_race_condition_fix.py --cov=../scripts/plan_export --cov-report=html
open htmlcov/index.html
```

## Test Output Interpretation

### Success Indicators
```
tests/test_race_condition_fix.py::TestBaselineFunctionality::test_single_session_export_success PASSED
tests/test_race_condition_fix.py::TestConcurrentSameSession::test_same_session_serialization PASSED
tests/test_race_condition_fix.py::TestMultiSessionRaceConditions::test_concurrent_different_sessions PASSED
tests/test_race_condition_fix.py::TestStressScenarios::test_high_concurrency_stress PASSED
```

### Failure Examples
```
FAILED - Multi-session cross-contamination detected
FAILED - Lock timeout not handled correctly
FAILED - Lock files not cleaned up
FAILED - Content integrity check failed
```

## Continuous Integration

Add to your CI pipeline:

```yaml
# .github/workflows/test.yml
- name: Run race condition tests
  run: |
    cd tests
    pytest test_race_condition_fix.py -n auto --cov=../scripts/plan_export --cov-report=xml

- name: Upload coverage
  uses: codecov/codecov-action@v3
  with:
    file: ./tests/coverage.xml
```

## Debugging Failed Tests

### Enable Verbose Logging
```bash
pytest test_race_condition_fix.py -v -s --log-cli-level=DEBUG
```

### Run Single Test with Debugging
```bash
pytest test_race_condition_fix.py::TestStressScenarios::test_high_concurrency_stress -v -s
```

### Check Temporary Files
Tests use pytest's `tmp_path` fixture which creates directories like:
```
/tmp/pytest-of-user/pytest-current/test_XXX_plan_export_test_abc123/
```

These are automatically cleaned up after the test session.

## Performance Benchmarks

Expected test times (on modern hardware):
- Baseline tests: ~2 seconds
- Concurrent same-session: ~5 seconds
- Multi-session race conditions: ~10 seconds
- Stress scenarios: ~30 seconds
- **Total (sequential): ~50 seconds**
- **Total (parallel -n auto): ~15 seconds**

## Contributing

When adding new tests:
1. **Always use `tmp_path` fixture** for temporary directories
2. **Never use real `~/.claude/sessions/`** - use test state directory
3. **Add comprehensive cleanup** in test teardown
4. **Verify isolation** - tests should not interfere with each other
5. **Test edge cases** - timeouts, exceptions, rapid operations
6. **Document safety measures** - explain why test is safe

## Safety Checklist

Before running tests, verify:
- [ ] Not using real `~/.claude/sessions/` directory
- [ ] Not touching active user session locks
- [ ] Using unique test IDs (UUID)
- [ ] Proper cleanup in place
- [ ] Tests are isolated from each other

## Troubleshooting

### Tests Hang or Timeout
```bash
# Check for orphaned lock files
ls -la /tmp/pytest-of-*/pytest-current/*/.*.lock

# Kill orphaned Python processes
pkill -9 -f "pytest test_race_condition_fix"
```

### Temporary Directory Not Cleaned Up
```bash
# Clean pytest temporary directories
rm -rf /tmp/pytest-of-*/pytest-current/
```

### Import Errors
```bash
# Verify plan_export.py is in correct location
ls -la ../scripts/plan_export.py

# Verify session_manager.py is accessible
ls -la ../../clautorun/src/clautorun/session_manager.py
```

## License

Apache License 2.0 - see LICENSE file in parent directory.
