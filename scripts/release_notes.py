#!/usr/bin/env python3
"""Build the release body for the declared version from its CHANGELOG section.

The CHANGELOG section is the only hand-written copy of a release's notes. This
script adds what follows from the version alone -- the title, the date line, the
upgrade commands, and the comparison link -- and checks the section's structure,
so the tag annotation, the GitHub Release, and the CHANGELOG cannot drift apart.

    uv run --project plugins/autorun --locked python scripts/release_notes.py          # write docs/releases/<version>.md
    uv run --project plugins/autorun --locked python scripts/release_notes.py --check  # fail if stale or malformed

Only the section being released is checked. Older sections keep whatever shape
they were published with.
"""

from __future__ import annotations

import argparse
import re
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/ahundt/autorun"

# The order a reader needs them in: what to do first, internals last. A section
# may omit any heading except the upgrade one.
HEADINGS = (
    "Upgrading from",
    "Highlights",
    "Added",
    "Changed",
    "Deprecated",
    "Removed",
    "Fixed",
    "Security",
    "For contributors",
)

_SECTION_RE = re.compile(r"^## \[([^\]]+)\] - (\d{4}-\d{2}-\d{2})[^\n]*$", re.MULTILINE)
_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


@dataclass(frozen=True)
class Section:
    version: str
    date: str
    body: str


def declared_version(root: Path = REPO_ROOT) -> str:
    """The release version. A regex, not tomllib: autorun supports Python 3.10."""
    text = (root / "plugins" / "autorun" / "pyproject.toml").read_text(encoding="utf-8")
    match = _VERSION_RE.search(text)
    if not match:
        raise SystemExit("plugins/autorun/pyproject.toml declares no version")
    return match.group(1)


def sections(changelog: str) -> list[Section]:
    """Every dated release section, newest first, as the CHANGELOG orders them."""
    found = list(_SECTION_RE.finditer(changelog))
    result = []
    for index, match in enumerate(found):
        end = found[index + 1].start() if index + 1 < len(found) else len(changelog)
        # An undated heading such as `## [0.12.0]` also ends the section above it.
        undated = re.search(r"^## ", changelog[match.end():end], re.MULTILINE)
        if undated:
            end = match.end() + undated.start()
        result.append(Section(match.group(1), match.group(2), changelog[match.end():end].strip("\n")))
    return result


def is_prerelease(version: str) -> bool:
    return re.search(r"(a|b|rc)\d+$", version) is not None


def _outside_fences(body: str) -> list[str]:
    lines, fenced = [], False
    for line in body.splitlines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            lines.append(line)
    return lines


def structure_problems(section: Section, previous: str) -> list[str]:
    """What keeps ``section`` from reading upgrade-first, in numbered lists."""
    problems = []
    lines = _outside_fences(section.body)
    headings = [line[4:].strip() for line in lines if line.startswith("### ")]
    first_heading = next((i for i, line in enumerate(lines) if line.startswith("### ")), len(lines))
    if not any(line.strip() for line in lines[:first_heading]):
        problems.append("no summary paragraph before the first ### heading")

    expected = f"Upgrading from {previous}"
    if not headings or headings[0] != expected:
        problems.append(f"the first heading must be '### {expected}', found {headings[:1]}")

    ranks = []
    for heading in headings:
        rank = next((i for i, name in enumerate(HEADINGS) if heading.startswith(name)), None)
        if rank is None:
            problems.append(f"unknown heading '### {heading}'; allowed: {', '.join(HEADINGS)}")
        else:
            ranks.append(rank)
    if ranks != sorted(ranks) or len(set(ranks)) != len(ranks):
        problems.append(f"headings must appear once each, in this order: {', '.join(HEADINGS)}")

    for number, line in enumerate(lines, 1):
        if re.match(r"\s*[-*+] ", line):
            problems.append(f"line {number} is a bullet; number list items so readers can cite them: {line.strip()!r}")
        if re.match(r"\s*(\d+\.|[-*+]) \*\*", line):
            problems.append(f"line {number} opens with a bold heading; state the change plainly: {line.strip()!r}")
        if line.startswith(("# ", "## ", "#### ")):
            problems.append(f"line {number} uses a heading level other than ###: {line.strip()!r}")
    return problems


def upgrade_commands(version: str) -> str:
    """The commands that upgrade an existing install, derived from the version.

    ``--force`` replaces the tool environment, so a user who installed the PDF
    extra must name it again; ``autorun --install`` republishes what each harness
    loads, which the package upgrade alone leaves at the old version.
    """
    pin = (
        " The version pin is required: without it, `uv` skips release candidates."
        if is_prerelease(version)
        else ""
    )
    steps = (
        "If you installed the PDF backends (`uv tool list --show-extras` lists "
        f"`pdf`), use `'autorun-ai[pdf]=={version}'`, or the upgrade removes them.{pin}",
        "Restart open agent sessions. Each one reads its hooks only when it starts.",
        "If you installed only through the Claude Code marketplace, run "
        "`claude plugin update ar@autorun` and restart Claude Code instead.",
        "New installs use the same command. The README covers other harnesses and "
        f"options: <{REPOSITORY}#readme>.",
    )
    # Wrap like the hand-written text, but never inside a `code span`: a command
    # split across lines reads as two in `git show`.
    def keep_code_whole(match: re.Match[str]) -> str:
        return match.group(0).replace(" ", "\0")

    numbered = "\n".join(
        textwrap.fill(
            re.sub(r"`[^`]+`", keep_code_whole, step),
            width=80,
            initial_indent=f"{number}. ",
            subsequent_indent="   ",
            break_long_words=False,
            break_on_hyphens=False,
        ).replace("\0", " ")
        for number, step in enumerate(steps, 1)
    )
    return (
        "```bash\n"
        f"uv tool install --force 'autorun-ai=={version}' && autorun --install\n"
        f"autorun --version    # expect: autorun {version}\n"
        "```\n"
        "\n"
        f"{numbered}"
    )


def render(section: Section, previous: str) -> str:
    """The release body: the section with its headings promoted, plus derived parts."""
    body = re.sub(r"^### ", "## ", section.body, flags=re.MULTILINE)
    heading = f"## Upgrading from {previous}\n"
    body = body.replace(heading, heading + "\n" + upgrade_commands(section.version) + "\n", 1)
    return (
        f"# autorun v{section.version}\n"
        "\n"
        f"Date: {section.date}\n"
        "\n"
        f"{body}\n"
        "\n"
        f"[Compare v{previous} with v{section.version}]"
        f"({REPOSITORY}/compare/v{previous}...v{section.version})\n"
    )


def build(root: Path = REPO_ROOT) -> tuple[Path, str, list[str]]:
    """Return the notes path, its expected content, and any structure problems."""
    version = declared_version(root)
    found = sections((root / "CHANGELOG.md").read_text(encoding="utf-8"))
    matching = [i for i, section in enumerate(found) if section.version == version]
    if len(matching) != 1:
        raise SystemExit(f"CHANGELOG.md has {len(matching)} dated sections for {version}; need exactly one")
    index = matching[0]
    if index + 1 >= len(found):
        raise SystemExit(f"CHANGELOG.md has no dated release before {version} to compare against")
    section, previous = found[index], found[index + 1].version
    path = root / "docs" / "releases" / f"{version}.md"
    return path, render(section, previous), structure_problems(section, previous)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="write nothing; fail if the section is malformed or the notes file differs",
    )
    args = parser.parse_args(argv)
    path, expected, problems = build()
    relative = path.relative_to(REPO_ROOT)
    for problem in problems:
        print(f"CHANGELOG.md: {problem}", file=sys.stderr)
    if problems:
        return 1
    if args.check:
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != expected:
            print(f"{relative} is stale; run scripts/release_notes.py", file=sys.stderr)
            return 1
        return 0
    path.write_text(expected, encoding="utf-8")
    print(f"wrote {relative}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
