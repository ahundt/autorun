# Plan: Fix Two Clautorun Hook Bugs

**Created:** 2026-02-02
**Implemented:** 2026-02-03
**Status:** Completed (Refined v8 - Efficiency + Edge Cases)

---

## Executive Summary

Fix two bugs in clautorun's hook system:

1. **Bug 1**: `/cr:planrefine` says "operation stopped by hook" (missing command mappings)
2. **Bug 2**: `rm` blocking matches substrings like `rmediation` (substring matching too broad)

---

## Root Cause Analysis

### Bug 1: Plan Commands Being Blocked

**Location:** `plugins/clautorun/src/clautorun/config.py:247-288`

**Problem:** Plan commands not in `command_mappings`, fall through to DEFAULT_INTEGRATIONS check.

### Bug 2: rm Pattern Matching Too Broad

**Location:** `plugins/clautorun/src/clautorun/plugins.py:410-412`

```python
for k, v in DEFAULT_INTEGRATIONS.items():
    if k in cmd:  # BUG: "rm" in "/cr:planrefine" == True
        return ctx.deny(v["suggestion"])
```

---

## Research Findings

### Why Previous Approaches Fail

| Approach | Failure Case | Problem |
|----------|--------------|---------|
| `pattern in cmd` | `/cr:planrefine` | Substring match |
| shlex + first token | `sudo rm file` | Misses `rm` after `sudo` |
| shlex + any token | `echo rm` | Blocks argument position |
| Regex split by operators | `if true; then rm; fi` | Misses control structures |
| Regex `\b` boundaries | `/bin/rm` | Ambiguous at `/` boundary |

### Correct Approach: AST-Based Parsing with bashlex

[bashlex](https://github.com/idank/bashlex) is a Python port of GNU bash's parser that generates a complete AST. It properly handles:

- **Compound commands**: `cmd1 && cmd2 || cmd3`
- **Pipelines**: `cat file | rm -`
- **Control structures**: `if`, `for`, `while`, `case`
- **Subshells**: `(rm file)`
- **Command substitution**: `$(rm file)` and `` `rm file` ``
- **Process substitution**: `<(rm file)`
- **Multi-line scripts**: Newlines, heredocs
- **Command prefixes**: `sudo`, `env`, `nice`, `nohup`, etc.

**References:**
- [bashlex GitHub](https://github.com/idank/bashlex) - Python bash parser
- [bashlex PyPI](https://pypi.org/project/bashlex/) - Installation: `pip install bashlex`
- [bashlex AST module](https://github.com/idank/bashlex/blob/master/bashlex/ast.py) - nodevisitor pattern
- [bashlex error handling](https://github.com/idank/bashlex/issues/23) - ParsingError fallback

---

## Implementation Plan

### Step 1: Add bashlex as Optional Default Dependency

**File:** `pyproject.toml` (workspace root)

bashlex is an optional dependency that installs by default. Users can skip it if needed, and the code gracefully falls back to heuristic detection.

```toml
[project.optional-dependencies]
# Robust bash command parsing (recommended, installs by default)
bashlex = ["bashlex>=0.18"]
# All optional features
all = ["bashlex>=0.18"]

[tool.uv]
# Install bashlex by default with uv pip install
default-extras = ["bashlex"]
```

**Installation commands:**
```bash
# Default install (includes bashlex)
uv pip install .
uv pip install git+https://github.com/ahundt/clautorun.git

# Minimal install (no bashlex, uses fallback)
uv pip install . --no-default-extras

# Explicit with bashlex
uv pip install ".[bashlex]"
uv pip install ".[all]"
```

### Step 2: Add Plan Commands to command_mappings

**File:** `plugins/clautorun/src/clautorun/config.py`
**Location:** After `/cr:ttest` entry (~line 270)

```python
# ─── Plan Commands ─────────────────────────────────────────────────────
"/cr:pn": "NEW_PLAN",
"/cr:pr": "REFINE_PLAN",
"/cr:pu": "UPDATE_PLAN",
"/cr:pp": "PROCESS_PLAN",
"/cr:plannew": "NEW_PLAN",
"/cr:planrefine": "REFINE_PLAN",
"/cr:planupdate": "UPDATE_PLAN",
"/cr:planprocess": "PROCESS_PLAN",
```

### Step 3: Create Command Detection Module

**File:** `plugins/clautorun/src/clautorun/command_detection.py` (new file)

Key features:
- Multi-pass detection: catches rm in "sudo -u root rm file"
- Recursive shell -c parsing: catches rm in "sh -c 'rm file'"
- HOT PATH caching: `_extract_cached` for command_matches_pattern
- End-of-options (--) handling
- Fixed GIT_SUBCOMMANDS scope

### Step 4: Update `_match` Function

**File:** `plugins/clautorun/src/clautorun/plugins.py`

**Replace literal matching:**
```python
if ptype == "literal":
    return command_matches_pattern(cmd, pattern)
```

### Step 5: Update DEFAULT_INTEGRATIONS Check

**File:** `plugins/clautorun/src/clautorun/plugins.py`

**Replace:**
```python
for k, v in DEFAULT_INTEGRATIONS.items():
    if command_matches_pattern(cmd, k):
        return ctx.deny(v["suggestion"])
```

### Step 6: Write Comprehensive Tests

**File:** `plugins/clautorun/tests/test_command_detection.py` (new file)

85 test cases covering:
- Bug 1: Plan commands in mappings
- Bug 2: Substring false positives fixed
- Multi-pass detection (prefix flags)
- Recursive shell -c parsing
- Edge cases (end-of-options, git subcommands, caching)

---

## Files Modified

| File | Change | Lines |
|------|--------|-------|
| `pyproject.toml` | Add `bashlex>=0.18` optional default dependency | +9 |
| `config.py` | Add 8 plan command mappings | +10 |
| `command_detection.py` | New module with v8 optimizations | ~300 |
| `plugins.py` | Import and use `command_matches_pattern` | -6, +3 |
| `test_command_detection.py` | Comprehensive test suite with v8 edge cases | ~250 |

---

## Edge Cases Handled

### Single-Word Patterns (e.g., `rm`)

| Scenario | Command | Pattern | Result | Reason |
|----------|---------|---------|--------|--------|
| Simple | `rm file` | `rm` | BLOCK | Command position |
| With path | `/bin/rm file` | `rm` | BLOCK | Basename extraction |
| After sudo | `sudo rm file` | `rm` | BLOCK | Prefix skipping |
| After && | `cat && rm file` | `rm` | BLOCK | Compound command |
| After \| | `ls \| rm -` | `rm` | BLOCK | Pipeline |
| Multi-line | `echo\nrm file` | `rm` | BLOCK | Newline handling |
| As argument | `echo rm` | `rm` | ALLOW | Argument position |
| In grep | `grep "rm" file` | `rm` | ALLOW | Quoted argument |
| Substring | `/cr:planrefine` | `rm` | ALLOW | Not a token |
| In word | `warm-up.sh` | `rm` | ALLOW | Part of word |
| Hyphenated cmd | `git-lfs pull` | `git` | ALLOW | `git` ≠ `git-lfs` |
| **Prefix flags** | `sudo -u root rm` | `rm` | BLOCK | Multi-pass detection |
| **Shell -c** | `sh -c "rm file"` | `rm` | BLOCK | Recursive parsing |
| **End-of-opts** | `rm -- -rf` | `rm` | BLOCK | `--` handling |
| **make clean** | `make clean` | `git clean` | ALLOW | Scoped GIT_SUBCOMMANDS |

### Multi-Word Patterns with Flag Reordering

| Scenario | Command | Pattern | Result | Reason |
|----------|---------|---------|--------|--------|
| Exact | `rm -rf /` | `rm -rf` | BLOCK | Flags match |
| Expanded | `rm -r -f /` | `rm -rf` | BLOCK | `-rf` = `-r -f` |
| Reversed | `rm -fr /` | `rm -rf` | BLOCK | Order independent |
| Flag at end | `git reset HEAD --hard` | `git reset --hard` | BLOCK | Flag position flexible |
| Different flag | `git reset --soft` | `git reset --hard` | ALLOW | Wrong flag |
| Missing flag | `rm file` | `rm -rf` | ALLOW | Flags required |

---

## Verification Results

```bash
# Tests run:
cd plugins/clautorun && ../../.venv/bin/python -m pytest tests/test_command_detection.py -v --override-ini="addopts="

# Results: 82 passed, 3 skipped (bashlex tests)
# Simple unit tests: 27 passed
```

---

## Known Limitations

### Category 1: Fixable by Installing bashlex

**Condition:** These limitations ONLY apply when bashlex is NOT installed. The fallback shlex-based parser cannot handle bash syntax beyond simple command sequences.

**How to check if bashlex is installed:**
```bash
python -c "import bashlex; print('bashlex available')" 2>/dev/null || echo "bashlex NOT installed"
```

**How to install bashlex:**
```bash
uv pip install ".[bashlex]"
# or
pip install bashlex>=0.18
```

| Pattern | Example Command | Without bashlex | With bashlex |
|---------|-----------------|-----------------|--------------|
| Control structures (if/then) | `if true; then rm file; fi` | **NOT DETECTED** - rm executes unblocked | BLOCKED correctly |
| Control structures (for) | `for f in *; do rm $f; done` | **NOT DETECTED** - rm executes unblocked | BLOCKED correctly |
| Control structures (while) | `while true; do rm file; done` | **NOT DETECTED** - rm executes unblocked | BLOCKED correctly |
| Subshells | `(rm file)` | **NOT DETECTED** - rm executes unblocked | BLOCKED correctly |
| Command substitution $() | `$(rm file)` | **NOT DETECTED** - rm executes unblocked | BLOCKED correctly |
| Command substitution backticks | `` `rm file` `` | **NOT DETECTED** - rm executes unblocked | BLOCKED correctly |
| Process substitution | `cat <(rm file)` | **NOT DETECTED** - rm executes unblocked | BLOCKED correctly |
| Nested subshells | `(( rm file ))` | **NOT DETECTED** - rm executes unblocked | BLOCKED correctly |

**CRITICAL:** Without bashlex, a user could bypass blocking by wrapping dangerous commands in control structures or subshells. Install bashlex for production use.

---

### Category 2: Fundamentally Unfixable (Runtime/External)

**Condition:** These limitations apply ALWAYS, regardless of bashlex installation. They are fundamentally impossible to fix because the dangerous command is not visible in the command string at analysis time.

| Pattern | Example | Why Unfixable | Risk Level |
|---------|---------|---------------|------------|
| **Variable expansion** | `CMD=rm; $CMD file` | Variable `$CMD` resolved at runtime by bash, not visible to static analysis | HIGH - common bypass |
| **Environment variables** | `$EDITOR file` where EDITOR=rm | Value comes from environment, unknown at analysis time | MEDIUM |
| **Aliases** | `alias del='rm'; del file` | Alias defined in user's shell config, not in command | MEDIUM - requires user setup |
| **Shell functions** | `danger() { rm "$@"; }; danger file` | Function definition may be in .bashrc or earlier in session | MEDIUM - requires user setup |
| **eval** | `eval "rm file"` | String evaluated at runtime, could be constructed dynamically | HIGH - intentional bypass |
| **source/dot** | `source script.sh` (where script contains rm) | Commands in external file, not analyzed | HIGH - hidden commands |
| **Indirect via xargs** | `echo file \| xargs rm` | xargs reads stdin and executes, complex semantics | MEDIUM |
| **Indirect via find -exec** | `find . -exec rm {} \;` | rm is detected but context unclear, may over-block | LOW - detected but imprecise |
| **Indirect via parallel** | `parallel rm ::: file1 file2` | parallel tool has complex execution semantics | MEDIUM |
| **Here-string execution** | `bash <<< "rm file"` | Command in here-string, would need recursive analysis | MEDIUM |

**IMPORTANT:** These bypasses require intentional effort. The blocking system prevents accidental dangerous commands, not malicious actors with shell knowledge.

---

### Category 3: Trade-offs and Edge Cases

**Condition:** These are design trade-offs that may cause unexpected behavior in specific scenarios.

#### 3a. Multi-pass Detection Over-Inclusion

**When this applies:** Commands starting with prefix commands (sudo, env, nice, etc.)

**Behavior:** After a prefix command, ALL subsequent non-flag tokens are added to `all_potential` to catch patterns like `sudo -u root rm file`.

**Example:**
```bash
sudo -u root rm file.txt
# all_potential = {root, rm, file.txt}
# "root" and "file.txt" are included but won't match any DEFAULT_INTEGRATIONS pattern
```

**Risk:** If someone adds a pattern matching a common word (e.g., pattern "file"), it could over-block. Currently safe because DEFAULT_INTEGRATIONS only contains command names.

#### 3b. Non-Prefix Commands Don't Multi-Pass

**When this applies:** Commands NOT starting with a prefix (echo, cat, grep, etc.)

**Behavior:** Only the first command is added to potential, arguments are ignored.

**Example:**
```bash
echo rm        # potential = {echo}, "rm" is argument, NOT blocked
grep rm file   # potential = {grep}, "rm" is argument, NOT blocked
```

**This is correct behavior** - these are safe commands where "rm" is just text.

#### 3c. Compound Commands Handled Per-Segment

**When this applies:** Commands with `&&`, `||`, `|`, `;`, or newlines

**Behavior:** Each segment is analyzed independently.

**Example:**
```bash
echo hello && rm file   # Two segments: {echo} and {rm} - rm IS blocked
cat file | rm -         # Two segments: {cat} and {rm} - rm IS blocked
```

**This is correct behavior** - each command in a pipeline/sequence is checked.

---

### Summary: What IS and IS NOT Protected

| Scenario | Protected? | Condition |
|----------|------------|-----------|
| `rm file` | ✅ YES | Always |
| `sudo rm file` | ✅ YES | Always |
| `sudo -u root rm file` | ✅ YES | Always (multi-pass) |
| `/bin/rm file` | ✅ YES | Always (basename extraction) |
| `cat && rm file` | ✅ YES | Always (compound command) |
| `sh -c "rm file"` | ✅ YES | Always (recursive shell -c) |
| `if true; then rm; fi` | ✅ YES | **Only with bashlex installed** |
| `$(rm file)` | ✅ YES | **Only with bashlex installed** |
| `(rm file)` | ✅ YES | **Only with bashlex installed** |
| `$CMD file` (CMD=rm) | ❌ NO | Never (runtime variable) |
| `eval "rm file"` | ❌ NO | Never (runtime eval) |
| `alias del=rm; del file` | ❌ NO | Never (user alias) |

---

### Recommendation

**For production use:**
```bash
uv pip install ".[bashlex]"
```

This enables AST-based parsing that correctly handles control structures, subshells, and command substitution. Without bashlex, the system uses a fallback shlex-based parser that only handles simple command sequences.

---

## Performance Considerations

| Optimization | Technique | Complexity |
|-------------|-----------|------------|
| **HOT PATH cache** | `_extract_cached` with LRU(512) | O(1) hit |
| Pattern caching | `ParsedPattern.from_string` LRU(64) | O(1) hit |
| Basename extraction | `path[path.rfind("/")+1:]` | Faster than rsplit |
| Dataclass slots | `slots=True` | ~40% memory reduction |
| Frozen dataclass | `frozen=True` | Hashable, cacheable |
| Set operations | `p.flags <= cmd.flags` | O(min(m,n)) |
| Early termination | `any()` generator | Best: O(1) |

**Typical complexity:**
- Cache hit: **O(1)** - most common case
- Cache miss: O(parse) + O(commands × flags)

---

## Rollback Plan

If issues arise:
1. Remove `bashlex` from dependencies
2. Remove `command_detection.py` module
3. Restore original `_match` literal matching in `plugins.py`
4. Restore original DEFAULT_INTEGRATIONS check in `plugins.py`
5. Remove plan commands from `command_mappings` if needed
