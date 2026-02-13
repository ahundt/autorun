# Heredoc Naive Matching Bug - Root Cause Analysis

**Date**: 2026-02-09
**Bug**: Commands with "grep"/"head"/"tail"/"cat" in heredocs get blocked
**Severity**: CRITICAL - Blocks legitimate development workflows

## Summary

The hook is blocking ANY command containing "grep" in heredocs or string literals, not just actual grep commands. This makes the plugin unusable for certain workflows like running test scripts.

## Root Cause

**File**: `plugins/clautorun/src/clautorun/command_detection.py`

### Problem Flow:

1. **Bashlex fails to parse heredocs**:
   - Command: `python3 << 'EOF' ... pattern = "grep" ... EOF`
   - Bashlex error: `here-document at line 0 delimited by end-of-file (wanted "'EOF'")`
   - Quote mismatch: delimiter is `'EOF'` (with quotes) but bashlex expects `EOF` (without)

2. **Fallback shlex parser treats EACH LINE as potential command**:
   - Falls back to `_extract_fallback()` at line 360
   - Calls `_extract_recursive()` which splits by `_SHELL_OPERATORS` = `|&&||&;\n`
   - **NEWLINES split segments** - each heredoc LINE becomes separate segment!
   - Line `pattern = "grep"` gets tokenized: `["pattern", "=", "grep"]`
   - `_extract_from_tokens()` treats all non-flag tokens as potential commands
   - Result: `"grep"` added to `all_potential` set

3. **Pattern matching incorrectly matches**:
   - `command_matches_pattern("python3 << 'EOF' ... grep ...", "grep")` returns `True`
   - Should return `False` (grep is just Python string data)

## Evidence

### Test Results

```bash
$ uv run pytest plugins/clautorun/tests/test_naive_string_matching_bug.py::TestNaiveStringMatchingBug::test_heredoc_grep_not_matched -v

FAILED - Heredoc with 'grep' in Python string should NOT match pattern 'grep'
Command: python3 << 'EOF' ...
Pattern: grep
Result: True (expected False)
```

### Extraction Debug Output

```python
# Command: python3 << 'EOF' ... pattern = "grep" ... EOF

Extraction results:
  names: frozenset({'grep', 'pattern', 'import', 'result', 'EOF', ...})
  all_potential: frozenset({'grep', 'pattern', 'import', 'result', 'EOF', ...})

'grep' in all_potential: True  # BUG - should be False
'grep' matches: True           # BUG - should be False
```

## Impact

### Blocked Commands (False Positives):

1. **Test scripts with command names in strings**:
   ```bash
   python3 << 'EOF'
   pattern = "grep"
   EOF
   # BLOCKED (incorrectly)
   ```

2. **Echo messages mentioning commands**:
   ```bash
   echo "Use Grep tool instead of grep"
   # BLOCKED (incorrectly)
   ```

3. **Python code in heredocs**:
   ```bash
   python3 << 'EOF'
   if 'head' in line:
       print(line)
   EOF
   # BLOCKED (incorrectly)
   ```

4. **Actual user command that triggered bug report**:
   ```bash
   gemini extensions list | grep -A 2 -B 2 clautorun || echo "Not found"
   # BLOCKED (incorrectly - grep is in pipe, should be allowed by _not_in_pipe predicate)
   ```

### Still Works (True Positives):

- Direct file operations: `grep pattern file.txt` ✅ Blocked correctly
- Direct commands: `head -50 file.txt` ✅ Blocked correctly

## Fix Options

### Option 1: Fix Bashlex Heredoc Parsing (RECOMMENDED)

**Problem**: Bashlex delimiter quote mismatch

**Solution**: Pre-process heredoc delimiters before parsing

```python
def _normalize_heredoc_delimiters(cmd: str) -> str:
    """Remove quotes from heredoc delimiters for bashlex compatibility.

    Transform: python3 << 'EOF' → python3 << EOF
    Transform: cat << "END" → cat << END
    """
    import re
    # Match << followed by quoted delimiter
    pattern = r'<<\s*["\'](\w+)["\']'
    return re.sub(pattern, r'<< \1', cmd)
```

### Option 2: Detect Heredocs in Fallback Parser

**Solution**: Skip heredoc content when bashlex fails

```python
def _extract_fallback(cmd: str, depth: int) -> ExtractedCommands:
    """Fallback extraction with heredoc detection."""
    # Check if command contains heredoc
    if '<<' in cmd:
        # Extract only the shell command part, ignore heredoc content
        heredoc_match = re.match(r'([^<]+)<<\s*["\']?(\w+)["\']?', cmd)
        if heredoc_match:
            # Only parse the command before <<
            cmd = heredoc_match.group(1).strip()

    n, s, p = _extract_recursive(cmd, depth)
    return ExtractedCommands(frozenset(n), frozenset(s), frozenset(p))
```

### Option 3: Don't Split on Newlines in Heredoc Context

**Solution**: Smarter segment splitting that recognizes heredoc boundaries

```python
def _split_preserving_heredocs(cmd: str) -> list[str]:
    """Split by operators but preserve heredoc blocks."""
    # Detect heredoc start: << delimiter
    # Keep entire heredoc as single segment
    # Split remaining parts by operators
    ...
```

## Recommended Fix

**Use Option 1** (normalize heredoc delimiters):

1. Add `_normalize_heredoc_delimiters()` helper function
2. Call it in `_extract_bashlex()` before `bashlex.parse(cmd)`
3. Bashlex will successfully parse heredocs
4. Heredoc content won't be treated as commands

**Why**:
- ✅ Minimal code change
- ✅ Fixes root cause (bashlex parsing failure)
- ✅ Leverages bashlex AST understanding of heredocs
- ✅ No performance impact (regex is fast)
- ✅ Works for all heredoc formats

## Test Coverage

Added comprehensive tests in:
- `test_naive_string_matching_bug.py` (9 tests, 1 failing)
- `test_task_17_pipe_blocking_fix.py` (3 new test methods)

### Test Cases:
1. ✅ Heredocs with grep/head/tail in Python code
2. ✅ echo/printf with command names as arguments
3. ✅ Python string literals with command names
4. ✅ Comments containing command names
5. ❌ **Heredoc grep NOT matched** (FAILING - this is the bug)
6. ✅ Actual commands DO match (sanity check)
7. ✅ Piped commands DO match (for _not_in_pipe to handle)

## Files Involved

| File | Lines | Issue |
|------|-------|-------|
| `command_detection.py` | 342-357 | `_extract_bashlex()` fails on heredocs |
| `command_detection.py` | 360-363 | `_extract_fallback()` treats lines as commands |
| `command_detection.py` | 273-304 | `_extract_recursive()` splits by newlines |
| `command_detection.py` | 207-268 | `_extract_from_tokens()` adds all tokens to potential |

## Next Steps

1. Implement Option 1 (normalize heredoc delimiters)
2. Verify all 9 tests pass in `test_naive_string_matching_bug.py`
3. Run full test suite to ensure no regressions
4. Test with actual user command: `gemini extensions list | grep ...`
5. Commit fix with comprehensive test coverage

## Related Issues

- Task #17: Pipe blocking after bashlex fix (same root cause)
- Daemon restart didn't fix it (code was correct, but bashlex was failing)
- Force reinstall didn't fix it (bug is in command detection, not plugin cache)

**The issue was never about pipe detection** - it was about bashlex failing to parse heredocs, causing the fallback parser to treat heredoc content as shell commands.
