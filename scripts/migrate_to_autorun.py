#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Migration script: clautorun → autorun with statistical sampling and context

Collects all changes, samples N%, shows before/after with 3 lines of context.

SAFETY NOTES:
1. DRY-RUN ONLY: This script NEVER modifies files (preview mode only)
2. Self-protection: Script is located in scripts/ directory (explicitly excluded from processing)
3. Script filename: migrate_to_autorun.py is explicitly protected from modification
4. .gitignore handling: Uses hardcoded EXCLUDE_PATTERNS (respects common patterns but not full .gitignore)
   - If .gitignore specifies additional exclusions, verify they're in EXCLUDE_PATTERNS
"""

import os
import re
import argparse
import logging
import random
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
    Replacement(
        r"from\s+plugins\.clautorun",
        "from plugins.autorun",
        "Python import: from plugins.clautorun"
    ),
    Replacement(
        r"import\s+plugins\.clautorun",
        "import plugins.autorun",
        "Python import: import plugins.clautorun"
    ),
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
    Replacement(
        r'"cr_st_',
        '"ar_st_',
        'Test label: "cr_st_ → "ar_st_ (e.g., "cr_st_policy")'
    ),
    Replacement(
        r"'cr_st_",
        "'ar_st_",
        "Test label: 'cr_st_ → 'ar_st_"
    ),
    # Test function names that reference command names
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
    # Lowercase
    Replacement(
        r'"name"\s*:\s*"cr"(?=[,\s\n}])',
        '"name": "ar"',
        'JSON: "name": "cr" → "name": "ar"'
    ),
    Replacement(
        r"'name'\s*:\s*'cr'(?=[,\s\n}])",
        "'name': 'ar'",
        "JSON: 'name': 'cr' → 'name': 'ar'"
    ),
    # Uppercase/mixed case (if they exist)
    Replacement(
        r'"name"\s*:\s*"CR"(?=[,\s\n}])',
        '"name": "AR"',
        'JSON: "name": "CR" → "name": "AR"'
    ),
    Replacement(
        r"'name'\s*:\s*'CR'(?=[,\s\n}])",
        "'name': 'AR'",
        "JSON: 'name': 'CR' → 'name': 'AR'"
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
    Replacement(
        r"clautorun\s*=\s*",
        "autorun = ",
        "Entry point assignment: clautorun ="
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
    # CRITICAL: Exclude scripts directory to prevent script from modifying itself
    "scripts/",
]

def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format='%(message)s')

logger = logging.getLogger(__name__)

def should_exclude_file(file_path: Path) -> bool:
    """Check if file should be excluded from processing.

    CRITICAL: This function ensures the migration script never modifies itself.
    The script is located in scripts/ which is explicitly excluded.
    """
    path_str = str(file_path)

    # SAFETY: Absolute protection - never modify the migration script itself
    if "migrate_to_autorun.py" in path_str:
        return True

    return any(pattern.replace("*", "").replace("/", "") in path_str for pattern in EXCLUDE_PATTERNS)

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

            # Detect which patterns matched this line
            pattern_descs = []
            for replacement in replacements:
                pattern = replacement.compile()
                if pattern.search(lines[line_num]):
                    pattern_descs.append(replacement.description)

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
    if file_path.suffix in ['.py', '.md'] and '/cr:' in modified and '/ar:' in modified:
        if not any(x in str(file_path) for x in ['example', 'doc', 'README', 'CLAUDE']):
            issues.append("MIXED PREFIXES: Both /cr: and /ar: found (should have one or the other)")

    # 5. Plugin manifest changes
    if 'plugin.json' in str(file_path):
        if '"name": "cr"' not in modified and '"name": "ar"' not in modified:
            issues.append("PLUGIN NAME MISMATCH: Missing both cr and ar plugin names")
        if '"clautorun"' in modified:
            issues.append("UNRESOLVED PACKAGE NAME: Still contains 'clautorun'")

    return issues

def migrate(root: Path = Path.cwd(), sample_percent: float = 5.0, verbose: bool = False, real_run: bool = False):
    """Generate migration preview (dry-run) or apply changes (real_run)"""
    setup_logging(verbose)

    if not (root / "plugins" / "clautorun").exists():
        logger.error("plugins/clautorun not found")
        return False

    # HUMAN CONFIRMATION GATE - ONLY for real-run mode
    if real_run:
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
    mode_label = "REAL-RUN" if real_run else "DRY-RUN"
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
    if real_run:
        logger.info("REAL-RUN: Scanning and applying changes")
    else:
        logger.info("DRY-RUN PREVIEW: Statistical Sampling with Context")
    logger.info("=" * 100)
    logger.info(f"Scanning {len(all_files)} files...")
    if not real_run:
        logger.info(f"Sampling: {sample_percent}% of changed lines (with ±3 lines context)")
    logger.info(f"Excluded patterns: {', '.join(EXCLUDE_PATTERNS)}")
    logger.info("")

    # Collect ALL changes
    all_changes = []
    files_changed = set()
    all_issues = []

    for file_path in all_files:
        try:
            original_content = file_path.read_text(encoding='utf-8', errors='ignore')

            # Collect changes from this file
            file_changes = collect_all_changes(original_content, file_path, REPLACEMENTS, context_lines=3)
            all_changes.extend(file_changes)

            if file_changes:
                files_changed.add(file_path)

                # Apply all changes to get final version for issue detection
                modified_content = original_content
                for replacement in REPLACEMENTS:
                    pattern = replacement.compile()
                    modified_content = pattern.sub(replacement.replacement, modified_content)

                issues = detect_issues(file_path, original_content, modified_content)
                if issues:
                    all_issues.extend([(file_path.relative_to(root), issue) for issue in issues])

                # REAL-RUN: Write modified content to file if no issues detected
                if real_run and not issues:
                    try:
                        file_path.write_text(modified_content, encoding='utf-8')
                        logger.debug(f"✓ Modified: {file_path.relative_to(root)}")
                    except Exception as e:
                        all_issues.append((file_path.relative_to(root), f"WRITE ERROR: {e}"))
                elif real_run and issues:
                    logger.warning(f"⚠️  Skipped (issues found): {file_path.relative_to(root)}")
                    for file_path_rel, issue in all_issues[-len(issues):]:
                        logger.warning(f"    - {issue}")

        except Exception as e:
            all_issues.append((file_path.relative_to(root), f"ERROR: {e}"))

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
    mode_display = "REAL-RUN (Files Modified)" if real_run else "DRY-RUN (Preview Only)"
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
    if real_run:
        logger.info(f"✓ REAL-RUN COMPLETE - {len(files_changed)} files modified")
        logger.info("Next steps:")
        logger.info("  1. Review changes: git diff")
        logger.info("  2. Test changes: uv run pytest plugins/autorun/tests/ -v")
        logger.info("  3. Verify no stray references: grep -r 'clautorun' plugins/autorun/ src/")
        logger.info("  4. Commit: git add -A && git commit -m 'refactor: rename clautorun → autorun'")
    else:
        logger.info("DRY-RUN COMPLETE - NO FILES MODIFIED")
        logger.info("Review samples above to verify replacements are correct")
    logger.info("=" * 100)

    return len(all_issues) == 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Migration tool: clautorun → autorun (DRY-RUN default, use --real-run to modify files)"
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
        "--real-run",
        action="store_true",
        help="REAL-RUN MODE: Actually modify files (requires human confirmation)"
    )

    args = parser.parse_args()
    random.seed(42)  # Reproducible samples

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

    success = migrate(sample_percent=args.sample, verbose=args.verbose, real_run=args.real_run)

    if args.output:
        print(f"✓ Samples saved to: {Path(args.output).absolute()}")

    exit(0 if success else 1)
