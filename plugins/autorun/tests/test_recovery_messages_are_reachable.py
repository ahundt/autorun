#!/usr/bin/env python3
"""A message that tells the reader what to do must name something that exists.

Philosophy principles 6 and 15: feedback must be specific and actionable, and
when automation cannot fix a problem the guidance must be a recovery path the
reader can actually take. A path that is not there, or a command the emitting
gate itself blocks, fails that at the moment it matters most.

Three instances of this defect class were found in one session:

* ``core.py`` told users to run ``plugins/autorun/scripts/restart_daemon.py``.
  There is no ``scripts/`` directory; the module is
  ``src/autorun/restart_daemon.py`` and the supported entry point is
  ``autorun --restart-daemon``. An installed user has no checkout at all, so a
  repo-relative path can never be right in a runtime message.
* ``client.py`` told users to run ``autorun --restart-daemon`` and retry, from
  inside the PreToolUse gate that blocks exactly that call. Fixed separately;
  pinned by test_client_fail_closed.py.
* Two command guards recommended harness tools (Grep, Glob) that are absent
  from some sessions.

REQUIREMENT for future edits: a runtime-facing string may name an installed
command (``autorun --restart-daemon``), an env var, or an absolute runtime path
under AUTORUN_HOME. It may not name a repo-relative path, because the runtime
has no repository.
"""

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = Path(__file__).resolve().parents[1]

#: Repo-relative paths that appear in source strings. Restricted to the two
#: prefixes this repository actually uses so an unrelated URL or glob pattern
#: cannot trip the check.
_REPO_PATH = re.compile(r"\b(?:plugins/autorun|docs)/[A-Za-z0-9_./-]+")

#: Trailing punctuation a prose sentence leaves attached to a path.
_TRIM = ".,:;)`'\"\\"

#: URLs are stripped before matching. ``docs/en/hooks`` inside
#: ``https://docs.anthropic.com/en/docs/claude-code/hooks`` is a live upstream
#: link, not a repository path, and a check that flags it would be switched off
#: rather than obeyed -- which is worse than not having one.
_URL = re.compile(r"https?://\S+")


def _source_files():
    for root in (PACKAGE_ROOT / "src", PACKAGE_ROOT / "hooks"):
        yield from sorted(root.rglob("*.py"))


def _string_constants(tree):
    """Every string literal in the module, docstrings included.

    Docstrings are in scope deliberately: they are where a maintainer looks for
    the repair procedure, so a dead path there misleads exactly the reader this
    check protects.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node


#: An issue-shaped reference: a hash followed by four or more digits. Three
#: digits or fewer are far more often an internal id, a port, or a size.
_BARE_ISSUE = re.compile(r"#(\d{4,})\b")


def test_every_file_naming_an_issue_number_also_carries_its_url():
    """A bare "#80305" identifies nothing on its own.

    autorun integrates with Claude Code, the Gemini family, Codex, Pi, Prime,
    OpenCode and several libraries, all of which number issues sequentially
    from one. The same number exists in most of those trackers and means
    something different in each.

    REQUIREMENT: a file may use the short form for readability, but it must
    resolve it somewhere in the same file -- one URL in the region header is
    enough. This is deliberately per-file rather than per-mention: requiring a
    60-character URL at all 120 mention sites would cost more readability than
    the ambiguity does, and a reader who has the file open has the answer.
    """
    unresolved = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        for number in sorted(set(_BARE_ISSUE.findall(text))):
            if f"/{number}" not in text:
                unresolved.append(f"{path.relative_to(REPO_ROOT).as_posix()}: #{number}")

    assert not unresolved, (
        "these files name an issue number without giving its URL anywhere in "
        "the file; the same number exists in several trackers autorun "
        "integrates with, so the short form alone identifies nothing. Add the "
        "full URL once per file:\n  " + "\n  ".join(unresolved)
    )


def test_every_repo_relative_path_named_in_source_exists():
    """Spec check: no source string may point at a file that is not there.

    Catches the whole class rather than the one known instance, so a future
    edit naming a moved or renamed path fails here instead of reaching a user
    who is already stuck.
    """
    missing = []
    for path in _source_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # pragma: no cover - would fail the suite anyway
            pytest.fail(f"{path}: {exc}")
        for node in _string_constants(tree):
            for candidate in _REPO_PATH.findall(_URL.sub(" ", node.value)):
                candidate = candidate.rstrip(_TRIM)
                if not (REPO_ROOT / candidate).exists():
                    missing.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {candidate}"
                    )

    assert not missing, (
        "these source strings name repository paths that do not exist. A "
        "runtime message must name an installed command or an absolute runtime "
        "path, never a repo-relative one -- an installed user has no "
        "checkout:\n  " + "\n  ".join(missing)
    )
