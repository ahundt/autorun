#!/usr/bin/env python3
"""Convert dashed markdown lists to numbered lists, in place.

Usage:
    python3 number_lists.py FILE.md [FILE.md ...]

What it does:
    Rewrites each top-level "- item" as "1. item", "2. item", ... and one
    nested level ("   - sub") as an indented "1.", "2.", ... under its parent.
    Continuation lines are re-indented to the new marker width. Fenced code
    blocks, tables (lines starting with "|"), and YAML frontmatter delimiters
    are left untouched. Existing numbered items keep their numbers and provide
    context for nested dashes beneath them.

Caveats (review the diff after running):
    Numbering resets at any non-blank, non-list line, so two lists separated
    only by a blank line continue one sequence. Deeper than two list levels
    and task-list checkboxes ("- [x]") are not handled specially.
"""
import argparse
import re


def convert(path):
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    out = []
    in_fence = False
    top_n = 0          # current top-level counter (0 = not in a list)
    nested_n = 0
    marker_w = 0       # width of current top-level marker, for continuations
    for line in lines:
        stripped = line.rstrip("\n")
        if stripped.startswith("```"):
            in_fence = not in_fence
            out.append(line)
            top_n = nested_n = 0
            continue
        if in_fence or stripped.startswith("|") or stripped == "---":
            out.append(line)
            continue
        m_top = re.match(r"^- (.*)$", stripped)
        m_num = re.match(r"^(\d+)\. ", stripped)
        m_nested = re.match(r"^(\s{3,4})- (.*)$", stripped)
        m_cont = re.match(r"^(\s{2,})(\S.*)$", stripped)
        if m_num:
            top_n = int(m_num.group(1))
            nested_n = 0
            marker_w = len(f"{top_n}. ")
            out.append(line)
        elif m_top:
            top_n += 1
            nested_n = 0
            marker = f"{top_n}. "
            marker_w = len(marker)
            out.append(marker + m_top.group(1) + "\n")
        elif m_nested and top_n > 0:
            nested_n += 1
            out.append(" " * marker_w + f"{nested_n}. " + m_nested.group(2) + "\n")
        elif m_cont and top_n > 0:
            out.append(" " * marker_w + m_cont.group(2) + "\n")
        elif stripped == "":
            out.append(line)
        else:
            top_n = nested_n = 0
            out.append(line)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(out)
    print(f"converted {path}")


def main():
    parser = argparse.ArgumentParser(
        description="Convert dashed markdown lists to numbered lists, in place.",
        epilog="Review the diff afterward: lists separated only by a blank line "
               "continue one numbering sequence.",
    )
    parser.add_argument("files", nargs="+", metavar="FILE.md",
                        help="markdown file to rewrite in place")
    for path in parser.parse_args().files:
        convert(path)


if __name__ == "__main__":
    main()
