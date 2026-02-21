#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Migration script: clautorun → autorun with statistical sampling and context

DRY-RUN MODE (default): Collects all changes, samples N%, shows before/after with 3 lines of context.
REAL-RUN MODE (--dangerously-do-real-file-edits): Modifies files after human confirmation.

SAFETY NOTES:
1. DRY-RUN (default): Preview-only, NEVER modifies files
2. REAL-RUN (--dangerously-do-real-file-edits): Actually modifies files after human confirmation ("i am human")
3. Self-protection: Script is located in scripts/ directory (explicitly excluded from processing)
4. Script filename: migrate_to_autorun.py is explicitly protected from modification
5. .gitignore handling: Uses hardcoded EXCLUDE_PATTERNS (respects common patterns but not full .gitignore)
   - If .gitignore specifies additional exclusions, verify they're in EXCLUDE_PATTERNS
6. ENCODING: All files are read/written as UTF-8. Non-UTF-8 files will trigger encoding errors (safe fail).
   - Original file encodings are NOT preserved (Bug #8 - future enhancement)
"""

import os
import re
import argparse
import logging
import random
import fnmatch
from pathlib import Path
from typing import List, Tuple, Dict
from dataclasses import dataclass

@dataclass
class Replacement:
    """Single replacement pattern with description"""
    pattern: str
    replacement: str
    description: str
    case_sensitive: bool = True

    def compile(self):
        """Compile regex with appropriate flags"""
        flags = 0 if self.case_sensitive else re.IGNORECASE
        return re.compile(self.pattern, flags)

@dataclass
class Change:
    """Single line change with context"""
    file_path: Path
    line_num: int
    pattern_desc: str
    before_lines: List[Tuple[int, str]]  # List of (line_number, content)
    after_lines: List[Tuple[int, str]]


# Define all replacements in STRICT priority order (most specific first, no overlaps)
REPLACEMENTS = [
    # TIER 1: Most specific - exact context-dependent patterns
    Replacement(
        r"plugins\.clautorun\.src\.clautorun",
        "plugins.autorun.src.autorun",
        "Full module path: plugins.clautorun.src.clautorun"
    ),
    Replacement(
        r"\.venv/bin/clautorun",
        ".venv/bin/autorun",
        "CRITICAL: Binary path in venv"
    ),

    # TIER 2: Identifiers (function/variable names) - must come before general string replacements
    Replacement(
        r"def\s+get_clautorun_bin\b",
        "def get_autorun_bin",
        "CRITICAL: Function definition get_clautorun_bin()"
    ),
    Replacement(
        r"\bget_clautorun_bin\b",
        "get_autorun_bin",
        "CRITICAL: Function call/reference get_clautorun_bin()"
    ),
    Replacement(
        r"\bclautorun_main\b",
        "autorun_main",
        "CRITICAL: Identifier clautorun_main"
    ),
    Replacement(
        r"\bclautorun_bin\b",
        "autorun_bin",
        "CRITICAL: Identifier clautorun_bin"
    ),
    Replacement(
        r"\bclautorun_bootstrap\b",
        "autorun_bootstrap",
        "CRITICAL: Identifier clautorun_bootstrap"
    ),

    # TIER 3: Module/package names
    Replacement(
        r"clautorun_marketplace",
        "autorun_marketplace",
        "Module name: clautorun_marketplace"
    ),
    Replacement(
        r"plugins\.clautorun",
        "plugins.autorun",
        "Python reference: plugins.clautorun"
    ),

    # TIER 4: Python imports (context-aware)
    # BUG #3 FIX: Removed redundant patterns (lines 108-110 were redundant with TIER 3 pattern 100-103)
    # TIER 3 pattern already matches "plugins.clautorun", so these are not needed
    Replacement(
        r"from\s+clautorun\b",
        "from autorun",
        "Python import: from clautorun"
    ),
    Replacement(
        r"import\s+clautorun\b",
        "import autorun",
        "Python import: import clautorun"
    ),

    # TIER 5: Paths and file references
    Replacement(
        r"plugins/clautorun",
        "plugins/autorun",
        "Directory path: plugins/clautorun"
    ),

    # TIER 6: Command prefixes
    Replacement(
        r"/cr:",
        "/ar:",
        "Command prefix: /cr: → /ar:"
    ),
    Replacement(
        r"/CR:",
        "/AR:",
        "Command prefix: /CR: → /AR:"
    ),
    Replacement(
        r"/Cr:",
        "/Ar:",
        "Command prefix: /Cr: → /Ar:"
    ),

    # TIER 6B: Test identifiers and labels (CRITICAL - specific patterns to avoid false matches)
    # These are test case names that reference the old command prefix
    # BUG #4 FIX: Add test patterns for all command variants, keep patterns specific to avoid false matches
    # Original patterns for status command
    Replacement(
        r'"cr_st_',
        '"ar_st_',
        'Test label: "cr_st_ → "ar_st_'
    ),
    Replacement(
        r"'cr_st_",
        "'ar_st_",
        "Test label: 'cr_st_ → 'ar_st_"
    ),
    # Additional command variants (go, find, justify, proc, stop, estop, etc.)
    Replacement(
        r'"cr_go_',
        '"ar_go_',
        'Test label: "cr_go_ → "ar_go_'
    ),
    Replacement(
        r'"cr_find_',
        '"ar_find_',
        'Test label: "cr_find_ → "ar_find_'
    ),
    Replacement(
        r'"cr_justify_',
        '"ar_justify_',
        'Test label: "cr_justify_ → "ar_justify_'
    ),
    Replacement(
        r'"cr_proc_',
        '"ar_proc_',
        'Test label: "cr_proc_ → "ar_proc_'
    ),
    Replacement(
        r'"cr_stop_',
        '"ar_stop_',
        'Test label: "cr_stop_ → "ar_stop_'
    ),
    # Test function names that reference command names (keep specific patterns)
    Replacement(
        r'def\s+test_new_cr_',
        'def test_new_ar_',
        'Test function: test_new_cr_* → test_new_ar_*'
    ),
    Replacement(
        r'\btest_new_cr_',
        'test_new_ar_',
        'Test call: test_new_cr_* → test_new_ar_*'
    ),

    # TIER 7: Command prefix in JSON matcher patterns (CRITICAL for hooks.json)
    # Must come before plain /cr: patterns to avoid double-replacement
    Replacement(
        r"\|/cr:",
        "|/ar:",
        "JSON matcher: |/cr: → |/ar: (alternation pattern in hooks)"
    ),
    Replacement(
        r"/cr:\|",
        "/ar:|",
        "JSON matcher: /cr:| → /ar:| (alternation pattern in hooks)"
    ),
    # BUG #2 NOTE: Additional standalone pattern not needed - TIER 6 /cr: pattern handles all remaining cases

    # TIER 7B: Command prefix descriptors with word boundaries (CRITICAL)
    # These describe the prefix itself, not command invocations
    # Lowercase variants
    Replacement(
        r"uses\s+'cr'\s+as",
        "uses 'ar' as",
        "Descriptor: uses 'cr' as → uses 'ar' as"
    ),
    Replacement(
        r'uses\s+"cr"\s+as',
        'uses "ar" as',
        'Descriptor: uses "cr" as → uses "ar" as'
    ),
    # Uppercase variants (CR, Cr)
    Replacement(
        r"uses\s+'CR'\s+as",
        "uses 'AR' as",
        "Descriptor: uses 'CR' as → uses 'AR' as"
    ),
    Replacement(
        r'uses\s+"CR"\s+as',
        'uses "AR" as',
        'Descriptor: uses "CR" as → uses "AR" as'
    ),
    Replacement(
        r"uses\s+'Cr'\s+as",
        "uses 'Ar' as",
        "Descriptor: uses 'Cr' as → uses 'Ar' as"
    ),
    Replacement(
        r'uses\s+"Cr"\s+as',
        'uses "Ar" as',
        'Descriptor: uses "Cr" as → uses "Ar" as'
    ),

    # TIER 7C: JSON name fields with word boundaries (CRITICAL)
    # Matches "name": "cr" but not "name": "crisis" or other cr* words
    # BUG #8 FIX: Preserve original spacing around colons using capture groups
    # Lowercase
    Replacement(
        r'"name"(\s*:\s*)"cr"(?=[,\s\n}])',
        r'"name"\1"ar"',
        'JSON: "name": "cr" → "name": "ar" (preserves spacing)'
    ),
    Replacement(
        r"'name'(\s*:\s*)'cr'(?=[,\s\n}])",
        r"'name'\1'ar'",
        "JSON: 'name': 'cr' → 'name': 'ar' (preserves spacing)"
    ),
    # Uppercase/mixed case (if they exist)
    Replacement(
        r'"name"(\s*:\s*)"CR"(?=[,\s\n}])',
        r'"name"\1"AR"',
        'JSON: "name": "CR" → "name": "AR" (preserves spacing)'
    ),
    Replacement(
        r"'name'(\s*:\s*)'CR'(?=[,\s\n}])",
        r"'name'\1'AR'",
        "JSON: 'name': 'CR' → 'name': 'AR' (preserves spacing)"
    ),

    # TIER 8: Code format variations
    Replacement(
        r"\[cr:",
        "[ar:",
        "Markdown format: [cr: → [ar:"
    ),
    Replacement(
        r"`cr:",
        "`ar:",
        "Backtick format: `cr: → `ar:"
    ),

    # TIER 9: Configuration and quoted strings
    Replacement(
        r'name\s*=\s*"clautorun"',
        'name = "autorun"',
        'TOML: name = "clautorun"'
    ),
    Replacement(
        r"name\s*=\s*'clautorun'",
        "name = 'autorun'",
        "TOML: name = 'clautorun'"
    ),
    Replacement(
        r'"clautorun"',
        '"autorun"',
        'String literal: "clautorun"'
    ),
    Replacement(
        r"'clautorun'",
        "'autorun'",
        "String literal: 'clautorun'"
    ),
    Replacement(
        r"clautorun-workspace",
        "autorun-workspace",
        "Config ID: clautorun-workspace"
    ),

    # TIER 10: Entry points and scripts
    Replacement(
        r"scripts\.clautorun",
        "scripts.autorun",
        "Entry point: scripts.clautorun"
    ),
    # BUG #1 FIX: Preserve original spacing around assignment operator
    Replacement(
        r"clautorun(\s*=\s*)",
        r"autorun\1",
        "Entry point assignment: clautorun = (preserves spacing)"
    ),

    # TIER 11: Documentation and generic text (least specific, catch-all)
    Replacement(
        r"clautorun\s+command",
        "autorun command",
        "Doc string: clautorun command"
    ),
    Replacement(
        r"clautorun\s+plugin",
        "autorun plugin",
        "Doc string: clautorun plugin"
    ),
    Replacement(
        r"clautorun\s+project",
        "autorun project",
        "Doc string: clautorun project"
    ),
    Replacement(
        r"clautorun\s+daemon",
        "autorun daemon",
        "Doc string: clautorun daemon"
    ),

    # TIER 11B: CRITICAL - Case variants of "clautorun" word (MUST come before catch-all)
    # These handle title case and uppercase variants found in 38+ files
    # Bare word patterns (title case)
    Replacement(
        r"\bClautorun\b",
        "Autorun",
        "Bare word: Clautorun (title case in docstrings, comments)"
    ),
    # Bare word patterns (all caps)
    Replacement(
        r"\bCLAUTORUN\b",
        "AUTORUN",
        "Bare word: CLAUTORUN (all caps, environment vars, constants)"
    ),
    # Class name patterns with embedded case (ClautorunDaemon, ClautorunSession, etc.)
    Replacement(
        r"Clautorun([A-Z]\w*)",
        r"Autorun\1",
        "Class name: Clautorun* → Autorun* (ClautorunDaemon → AutorunDaemon, etc.)"
    ),

    # TIER 11C: CRITICAL - Identifiers and environment variables with underscores
    # These MUST come before catch-all because \bCLAUTORUN\b won't match CLAUTORUN_* (underscore breaks word boundary)
    # ALL CAPS environment variables: CLAUTORUN_PLUGIN_ROOT, CLAUTORUN_NO_BOOTSTRAP, etc.
    Replacement(
        r"\bCLAUTORUN_",
        "AUTORUN_",
        "Environment variable: CLAUTORUN_* → AUTORUN_* (all caps)"
    ),
    # Title case variants (rare but should be handled): Clautorun_something
    Replacement(
        r"\bClautorun_",
        "Autorun_",
        "Environment variable: Clautorun_* → Autorun_* (title case)"
    ),
    # Lowercase identifiers: clautorun_available, clautorun_result, clautorun_bin, clautorun_diagnostic_*.log, etc.
    Replacement(
        r"\bclautorun_",
        "autorun_",
        "Identifier with underscore: clautorun_* → autorun_* (lowercase)"
    ),

    # TIER 12 (FINAL): Catch-all for bare "clautorun" word (comments, messages, etc.)
    # MUST come LAST so specific patterns take priority
    Replacement(
        r"\bclautorun\b",
        "autorun",
        "Bare word: clautorun (comments, messages, catch-all)"
    ),
]

EXCLUDE_PATTERNS = [
    # System and version control
    ".git/", ".worktrees/", "notes/", "rejected/",
    # Build and environment artifacts (respects .gitignore)
    "__pycache__/", ".pytest_cache/", ".mypy_cache/",
    ".venv/", ".env/", "*.pyc", "*.pyo", "*.lock",
    "*.bak", "*.tmp", ".DS_Store", "egg-info/",
    "build/", "dist/",
    # BUG #7 FIX: Only exclude the migration script itself, not all scripts
    # Using filename exclusion in should_exclude_file() instead
]

def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    # Clear existing handlers to prevent duplication (Bug #4 fix)
    logging.root.handlers.clear()
    logging.basicConfig(level=level, format='%(message)s', force=True)

logger = logging.getLogger(__name__)

def should_exclude_file(file_path: Path) -> bool:
    """Check if file should be excluded from processing.

    CRITICAL: This function ensures the migration script never modifies itself.
    Uses fnmatch for proper glob pattern matching (not naive substring matching).
    """
    path_str = str(file_path)

    # BUG #6 FIX: Robust script self-protection (filename + path resolution)
    # Check both the filename and the resolved path to prevent self-modification
    if file_path.name == "migrate_to_autorun.py":
        try:
            # Verify it's the actual migration script by comparing resolved paths
            if file_path.resolve() == Path(__file__).resolve():
                return True
        except (OSError, RuntimeError):
            # If resolution fails, exclude as a safety measure
            return True

    # Use fnmatch for proper glob pattern matching
    for pattern in EXCLUDE_PATTERNS:
        if fnmatch.fnmatch(path_str, f"*{pattern}*") or fnmatch.fnmatch(path_str, pattern):
            return True
    return False

def get_files_to_process(root_dir: Path, extensions: List[str]) -> List[Path]:
    files = []
    for ext in extensions:
        for file_path in root_dir.rglob(f"*{ext}"):
            if not should_exclude_file(file_path):
                files.append(file_path)
    return sorted(files)

def apply_all_replacements(content: str, replacements: List[Replacement]) -> str:
    """Apply ALL patterns to content in order (cumulative)"""
    result = content
    for replacement in replacements:
        pattern = replacement.compile()
        result = pattern.sub(replacement.replacement, result)
    return result

def apply_all_replacements_with_tracking(content: str, replacements: List[Replacement]) -> Tuple[str, Dict[str, int]]:
    """Apply ALL patterns to content and track which patterns matched (CUMULATIVE).

    BUG #9 FIX: Tracks which patterns ACTUALLY matched during cumulative application,
    not re-applying them individually which could give different results.

    Returns: (modified_content, dict of pattern_description -> match_count)
    """
    result = content
    pattern_matches = {}

    for replacement in replacements:
        pattern = replacement.compile()
        # Count matches before replacement
        matches = pattern.findall(result)
        if matches:
            pattern_matches[replacement.description] = len(matches)
        # Apply replacement
        result = pattern.sub(replacement.replacement, result)

    return result, pattern_matches

def collect_all_changes(content: str, file_path: Path, replacements: List[Replacement], context_lines: int = 3) -> List[Change]:
    """Collect ALL changes with context lines (CUMULATIVE - all patterns applied per line)"""
    changes = []
    lines = content.split('\n')

    # Apply ALL patterns to get final result
    modified_content = apply_all_replacements(content, replacements)
    modified_lines = modified_content.split('\n')

    # Ensure same number of lines (replacements shouldn't change line count)
    max_lines = max(len(lines), len(modified_lines))
    if len(lines) < max_lines:
        lines.extend([''] * (max_lines - len(lines)))
    if len(modified_lines) < max_lines:
        modified_lines.extend([''] * (max_lines - len(modified_lines)))

    # Find which lines changed (compare original vs modified)
    changed_line_nums = set()
    for i in range(min(len(lines), len(modified_lines))):
        if lines[i] != modified_lines[i]:
            changed_line_nums.add(i)

    # For each changed line, create ONE change entry with ALL patterns applied
    for line_num in sorted(changed_line_nums):
        if line_num < len(lines):
            # Get before context
            context_start = max(0, line_num - context_lines)
            context_end = min(len(lines), line_num + context_lines + 1)

            before_lines = [(i + 1, lines[i]) for i in range(context_start, context_end)]
            after_lines = [(i + 1, modified_lines[i]) for i in range(context_start, min(len(modified_lines), context_end))]

            # Pad after_lines if needed
            if len(after_lines) < len(before_lines):
                for i in range(len(after_lines), len(before_lines)):
                    after_lines.append((i + 1, ''))

            # BUG #9 FIX: Detect which patterns ACTUALLY matched by applying cumulatively
            # This mirrors the actual replacement process (cumulative application)
            pattern_descs = []
            current_line = lines[line_num]
            for replacement in replacements:
                pattern = replacement.compile()
                # Check if this pattern matches in the current state (cumulative)
                if pattern.search(current_line):
                    pattern_descs.append(replacement.description)
                    # Apply the replacement to match cumulative behavior
                    current_line = pattern.sub(replacement.replacement, current_line)

            combined_desc = " + ".join(pattern_descs) if pattern_descs else "Unknown pattern"

            changes.append(Change(
                file_path=file_path,
                line_num=line_num + 1,
                pattern_desc=combined_desc,
                before_lines=before_lines,
                after_lines=after_lines
            ))

    return changes

def detect_issues(file_path: Path, original: str, modified: str) -> List[str]:
    """Detect potential issues with replacements"""
    issues = []

    # 1. Double-replacement artifacts
    if "ararun" in modified or "autoorun" in modified or "autoo" in modified:
        issues.append("DOUBLE-REPLACEMENT ARTIFACT (ararun, autoorun, etc)")

    # 2. Content corruption check
    if len(modified) < len(original) * 0.8:
        issues.append(f"CONTENT SHRINKAGE: {len(modified)} vs {len(original)} bytes (>20%)")
    if len(modified) > len(original) * 1.2:
        issues.append(f"CONTENT GROWTH: {len(modified)} vs {len(original)} bytes (>20%)")

    # 3. Unresolved old references - CRITICAL
    unresolved = []
    if re.search(r"\bfrom\s+clautorun\s+import\b", modified):
        unresolved.append("from clautorun import")
    if re.search(r"\bimport\s+clautorun\s+", modified):
        unresolved.append("import clautorun")
    if "plugins.clautorun.src.clautorun" in modified:
        unresolved.append("plugins.clautorun.src.clautorun")
    if re.search(r'\bget_clautorun_bin\b', modified):
        unresolved.append("get_clautorun_bin() (function name)")
    if re.search(r'\bclautorun_bin\b', modified):
        unresolved.append("clautorun_bin (variable)")
    if re.search(r'\.venv/bin/clautorun\b', modified):
        unresolved.append(".venv/bin/clautorun (path)")

    if unresolved:
        issues.append(f"UNRESOLVED REFERENCES: {', '.join(unresolved)}")

    # 4. Mixed prefix consistency
    # BUG #10 NOTE: Simplified - only warn if BOTH prefixes exist AND not in obvious doc/example files
    # This is just an informational warning, not a blocker
    if file_path.suffix in ['.py', '.md'] and '/cr:' in modified and '/ar:' in modified:
        # Skip warning for obvious documentation and example files
        if not any(x in str(file_path) for x in ['test', 'example', 'doc', 'README', 'CLAUDE', 'GEMINI']):
            issues.append("MIXED PREFIXES: Both /cr: and /ar: found (informational warning only)")

    # 5. Plugin manifest changes
    if 'plugin.json' in str(file_path):
        if '"name": "cr"' not in modified and '"name": "ar"' not in modified:
            issues.append("PLUGIN NAME MISMATCH: Missing both cr and ar plugin names")
        if '"clautorun"' in modified:
            issues.append("UNRESOLVED PACKAGE NAME: Still contains 'clautorun'")

    return issues

def migrate(root: Path = Path.cwd(), sample_percent: float = 5.0, verbose: bool = False, dangerously_do_real_file_edits: bool = False):
    """Generate migration preview (dry-run) or apply changes (dangerously_do_real_file_edits)"""
    setup_logging(verbose)

    # Bug #6 fix: Validate root directory and permissions
    plugins_dir = root / "plugins" / "clautorun"
    if not plugins_dir.exists():
        logger.error(f"plugins/clautorun not found at {plugins_dir}")
        return False
    if not plugins_dir.is_dir():
        logger.error(f"plugins/clautorun is not a directory")
        return False
    if not os.access(plugins_dir, os.R_OK):
        logger.error(f"No read permission for {plugins_dir}")
        return False
    if dangerously_do_real_file_edits and not os.access(plugins_dir, os.W_OK):
        logger.error(f"No write permission for {plugins_dir} (required for REAL-RUN mode)")
        return False

    # HUMAN CONFIRMATION GATE - ONLY for dangerously_do_real_file_edits mode
    if dangerously_do_real_file_edits:
        print("\n" + "=" * 100)
        print("⚠️  REAL-RUN MODE: This will modify files in your repository")
        print("=" * 100)
        print("\nThis operation will permanently modify files in your codebase.")
        print("All changes are reversible via git (commit will be created).\n")
        print("Type 'i am human' (exactly as shown) to confirm you understand and authorize this action:")
        print("=" * 100 + "\n")

        confirmation = input("Confirmation: ").strip()
        if confirmation != "i am human":
            print("\n❌ Confirmation failed. Exiting without changes.\n")
            return False

        print("\n✓ Confirmed by human. Proceeding with file modifications...\n")

    # SAFETY CHECK: Ensure migration script is not in any file list
    script_path = Path(__file__).resolve()
    logger.info(f"Migration script path: {script_path}")
    logger.info(f"This script WILL NOT be modified (explicit protection)")
    mode_label = "REAL-RUN" if dangerously_do_real_file_edits else "DRY-RUN"
    logger.info(f"Mode: {mode_label}")
    logger.info("")

    # Find files
    py_files = get_files_to_process(root / "plugins" / "clautorun", [".py"])
    py_files += get_files_to_process(root / "src", [".py"])
    json_files = get_files_to_process(root / "plugins" / "clautorun", [".json"])
    md_files = get_files_to_process(root, [".md"])
    toml_files = [root / "pyproject.toml"] if (root / "pyproject.toml").exists() else []

    all_files = py_files + json_files + md_files + toml_files

    # SAFETY VALIDATION: Verify script won't process itself
    if script_path in all_files:
        logger.error("FATAL: Migration script found in file list!")
        logger.error("This indicates a configuration error - aborting to prevent self-modification")
        return False

    logger.info("=" * 100)
    if dangerously_do_real_file_edits:
        logger.info("REAL-RUN: Scanning and applying changes")
    else:
        logger.info("DRY-RUN PREVIEW: Statistical Sampling with Context")
    logger.info("=" * 100)
    logger.info(f"Scanning {len(all_files)} files...")
    if not dangerously_do_real_file_edits:
        logger.info(f"Sampling: {sample_percent}% of changed lines (with ±3 lines context)")
    logger.info(f"Excluded patterns: {', '.join(EXCLUDE_PATTERNS)}")
    logger.info("")

    # Collect ALL changes
    all_changes = []
    files_changed = set()
    files_modified = set()  # Bug #7 fix: Track modified vs skipped separately
    files_skipped = set()
    all_issues = []

    for file_path in all_files:
        try:
            # Bug #2 fix: Fail explicitly on encoding errors, don't silently corrupt
            try:
                original_content = file_path.read_text(encoding='utf-8')
            except UnicodeDecodeError as e:
                all_issues.append((file_path.relative_to(root), f"ENCODING ERROR: {e}"))
                continue

            # Collect changes from this file
            file_changes = collect_all_changes(original_content, file_path, REPLACEMENTS, context_lines=3)
            all_changes.extend(file_changes)

            if file_changes:
                files_changed.add(file_path)

                # SHARED: Apply all changes using existing function (no code duplication)
                modified_content = apply_all_replacements(original_content, REPLACEMENTS)

                # SHARED: Detect issues
                issues = detect_issues(file_path, original_content, modified_content)
                if issues:
                    all_issues.extend([(file_path.relative_to(root), issue) for issue in issues])

                # REAL-RUN ONLY: Modify file if no issues detected
                if dangerously_do_real_file_edits and not issues:
                    try:
                        file_path.write_text(modified_content, encoding='utf-8')
                        # Verify write succeeded by reading back
                        verify_content = file_path.read_text(encoding='utf-8')
                        if verify_content != modified_content:
                            all_issues.append((file_path.relative_to(root), "WRITE VERIFICATION FAILED: content mismatch"))
                            files_skipped.add(file_path)  # Track skipped
                        else:
                            logger.info(f"✓ Modified: {file_path.relative_to(root)}")
                            files_modified.add(file_path)  # Track modified
                    except (IOError, OSError) as e:
                        # Bug #5 fix: Specific exception handling
                        all_issues.append((file_path.relative_to(root), f"WRITE ERROR: {type(e).__name__}: {e}"))
                        files_skipped.add(file_path)
                elif dangerously_do_real_file_edits and issues:
                    logger.warning(f"⚠️  Skipped (issues found): {file_path.relative_to(root)}")
                    files_skipped.add(file_path)  # Track skipped
                    for issue in issues:
                        logger.warning(f"    - {issue}")

        except (IOError, OSError, ValueError, TypeError) as e:
            # Bug #5 fix: Specific exception handling, not broad Exception
            all_issues.append((file_path.relative_to(root), f"ERROR: {type(e).__name__}: {e}"))

    # Calculate sample size
    sample_size = max(1, int(len(all_changes) * sample_percent / 100))
    sampled_changes = random.sample(all_changes, min(sample_size, len(all_changes)))
    # Sort by file for display
    sampled_changes = sorted(sampled_changes, key=lambda c: (str(c.file_path), c.line_num))

    # Display samples
    logger.info(f"SHOWING {len(sampled_changes)} SAMPLES ({sample_percent}% of {len(all_changes)} total changes):\n")

    total_lines_shown = 0

    for change in sampled_changes:
        logger.info(f"{'─' * 100}")
        logger.info(f"{change.file_path.relative_to(root)} : L{change.line_num}")
        logger.info(f"{change.pattern_desc}")
        logger.info(f"{'─' * 100}")

        # Show before context
        logger.info("BEFORE:")
        for line_num, line_text in change.before_lines:
            marker = ">>>" if line_num == change.line_num else "   "
            logger.info(f"  {marker} L{line_num:5d} | {line_text[:96]}")

        total_lines_shown += len(change.before_lines)

        # Show after context
        logger.info("AFTER:")
        for line_num, line_text in change.after_lines:
            marker = ">>>" if line_num == change.line_num else "   "
            logger.info(f"  {marker} L{line_num:5d} | {line_text[:96]}")

        total_lines_shown += len(change.after_lines)
        logger.info("")

    # Summary statistics
    logger.info(f"\n{'=' * 100}")
    logger.info("SUMMARY")
    logger.info(f"{'=' * 100}")
    mode_display = "REAL-RUN (Files Modified)" if dangerously_do_real_file_edits else "DRY-RUN (Preview Only)"
    logger.info(f"Mode: {mode_display}")
    logger.info(f"Files affected: {len(files_changed)}")
    logger.info(f"Total changes detected: {len(all_changes)}")
    logger.info(f"Sample size shown: {len(sampled_changes)} ({sample_percent}%)")
    logger.info(f"Total context lines shown: ~{total_lines_shown} (7 lines per change)")

    # Group changes by pattern
    pattern_counts = {}
    for change in all_changes:
        pattern = change.pattern_desc
        pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1

    logger.info(f"\nChange distribution by pattern:")
    for pattern in sorted(pattern_counts.keys(), key=lambda p: -pattern_counts[p])[:15]:
        count = pattern_counts[pattern]
        logger.info(f"  {count:4d}  {pattern}")

    if len(pattern_counts) > 15:
        logger.info(f"  ... and {len(pattern_counts) - 15} more patterns")

    if all_issues:
        logger.info(f"\n⚠ ISSUES DETECTED ({len(all_issues)}):")
        for file_path, issue in all_issues[:15]:
            logger.info(f"  {file_path}: {issue}")
        if len(all_issues) > 15:
            logger.info(f"  ... and {len(all_issues) - 15} more")
    else:
        logger.info(f"\n✓ No issues detected")

    logger.info(f"\n{'=' * 100}")
    if dangerously_do_real_file_edits:
        # Bug #10 fix: Show modified + skipped counts separately
        logger.info(f"✓ REAL-RUN COMPLETE - {len(files_modified)} files modified, {len(files_skipped)} files skipped")
        logger.info("Next steps:")
        logger.info("  1. Review changes: git diff")
        logger.info("  2. Test changes: uv run pytest plugins/autorun/tests/ -v")
        logger.info("  3. Verify no stray references: grep -r 'clautorun' plugins/autorun/ src/")
        logger.info("  4. Commit: git add -A && git commit -m 'refactor: rename clautorun → autorun'")
    else:
        logger.info("DRY-RUN COMPLETE - NO FILES MODIFIED")
        logger.info("Review samples above to verify replacements are correct")
    logger.info("=" * 100)

    # Bug #7 fix: Return False only if there are actual issues, not if only some files were skipped
    return len(all_issues) == 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Migration tool: clautorun → autorun (DRY-RUN default, use --dangerously-do-real-file-edits to modify files)"
    )
    parser.add_argument(
        "-s", "--sample",
        type=float,
        default=5.0,
        help="Sample percentage (default: 5%%)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output"
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="Output file path for samples (default: stdout)"
    )
    parser.add_argument(
        "--dangerously-do-real-file-edits",
        action="store_true",
        help="REAL-RUN MODE: Actually modify files (requires human confirmation 'i am human')"
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=42,
        help="Random seed for reproducible sampling (default: 42, set to None for non-reproducible)"
    )

    args = parser.parse_args()
    # Bug #9 fix: Document random seed and make it configurable
    if args.random_seed is not None:
        random.seed(args.random_seed)
        logger.debug(f"Random seed set to {args.random_seed} (reproducible samples)")
    else:
        logger.debug("Random seed not set (non-reproducible samples)")

    # Redirect logging to output file if specified
    if args.output:
        # Create output file and set logging to write to it
        output_file = Path(args.output)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Remove existing handlers and add file handler
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

        file_handler = logging.FileHandler(args.output)
        file_handler.setLevel(logging.DEBUG if args.verbose else logging.INFO)
        file_handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(file_handler)

        # Also print to stdout that we're saving to file
        print(f"Saving samples to: {output_file.absolute()}")

    success = migrate(sample_percent=args.sample, verbose=args.verbose, dangerously_do_real_file_edits=args.dangerously_do_real_file_edits)

    if args.output:
        print(f"✓ Samples saved to: {Path(args.output).absolute()}")

    exit(0 if success else 1)
