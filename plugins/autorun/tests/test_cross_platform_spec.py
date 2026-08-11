#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright 2025 Andrew Hundt <ATHundt@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Spec checker: the four ways autorun silently stopped working on Windows.

Each of these shipped, none raised, and every one was invisible until the
Windows job ran a full suite for the first time. They share a shape: code that
is correct on POSIX and quietly wrong elsewhere, with no error at the point of
the mistake.

  1. A path interpolated into generated source without escaping. `C:\\Users\\x`
     inside a quoted literal makes `\\U` an invalid escape, so the generated
     module fails to parse. This is why the daemon never started on Windows,
     and it had already appeared in a test and in the JavaScript shim.
  2. A POSIX-only venv layout literal. Windows writes `.venv/Scripts/name.exe`,
     so `.venv/bin/name` named a file that cannot exist and every plugin-local
     lookup missed.
  3. `start_new_session` with no `creationflags`. It calls setsid(), is
     POSIX-only, and Python accepts and ignores it on Windows, so a daemon
     meant to outlive its parent was reaped with it.
  4. A home redirect that sets only `HOME`. `Path.home()` reads `USERPROFILE`
     on Windows, so an "isolated" run read a sandbox and wrote a real home.

Every checker below is paired with a test that plants the defect and requires
the checker to catch it. A source scanner that has quietly stopped matching
passes forever and is worse than no check at all, because it reads as
coverage.
"""

import ast
import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SRC = PLUGIN_ROOT / "src" / "autorun"
HOOKS = PLUGIN_ROOT / "hooks"


TESTS = PLUGIN_ROOT / "tests"


def _python_sources() -> list[Path]:
    files = [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]
    files += [p for p in HOOKS.rglob("*.py") if "__pycache__" not in p.parts]
    # gemini_template/hooks/hook_entry.py is a symlink to hooks/hook_entry.py.
    return sorted({p.resolve() for p in files})


def _isolation_sources() -> list[Path]:
    """Product sources plus the tests, for checks about test isolation.

    The home-redirect check originally scanned src/ and hooks/ only, which
    skipped the one directory where home redirects actually live: a fixture
    moving HOME to a tmp_path is the whole mechanism of test isolation, and
    every offender was in tests/. A checker aimed at isolation that cannot see
    the isolation code passes forever and reads as coverage.
    """
    files = _python_sources()
    files += [p.resolve() for p in TESTS.rglob("*.py") if "__pycache__" not in p.parts]
    return sorted(set(files))


# --- 1. paths interpolated into generated source -----------------------------

# A quote, then a substitution: "...insert(0, '{src}')". The quotes belong to
# the generated program, so the value lands inside its literal and its
# backslashes become escapes. `{x!r}` and `json.dumps(x)` carry their own
# quoting, which is why neither is matched.
_QUOTED_SUBSTITUTION = re.compile(r"""['"]\{[A-Za-z0-9_][A-Za-z0-9_.\[\]()]*\}['"]""")

# Only strings that are themselves a program. A display message may quote a
# value freely -- f'no session "{name}"' is correct and common -- so the
# checker looks for source being built, not for quotes.
_CODE_MARKERS = (
    "import ",
    "sys.path",
    "const ",
    "function ",
    "require(",
    "=>",
)


def _docstring_lines(tree: ast.AST) -> set[int]:
    """Line numbers occupied by docstrings.

    Prose that quotes code is not code. Both remaining hits when this checker
    was written were docstrings explaining the very defect it looks for, and a
    checker that flags its own documentation gets silenced.
    """
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        doc = ast.get_docstring(node, clean=False)
        if doc is None:
            continue
        first = node.body[0]
        lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return lines


def quoted_substitutions(source: str, filename: str = "<test>") -> list[str]:
    """Return interpolations that land inside a generated program's literal."""
    tree = ast.parse(source, filename=filename)
    documentation = _docstring_lines(tree)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and not isinstance(node.value, str):
            continue
        if not isinstance(node, (ast.Constant, ast.JoinedStr)):
            continue
        if node.lineno in documentation:
            continue
        text = ast.unparse(node)
        if not any(marker in text for marker in _CODE_MARKERS):
            continue
        offenders.extend(
            match.group(0)
            for match in _QUOTED_SUBSTITUTION.finditer(text)
            if "!r" not in match.group(0)
        )
    return offenders


def test_no_module_quotes_an_interpolated_value_in_generated_source():
    offenders = []
    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        for found in quoted_substitutions(text):
            offenders.append(f"{path.relative_to(PLUGIN_ROOT)}: {found}")
    assert not offenders, (
        "a value is interpolated inside quotes that belong to generated "
        "source, so a Windows path's backslashes become escape sequences and "
        "the generated program does not parse. Use {value!r}, or json.dumps "
        "for JavaScript, and drop the surrounding quotes:\n"
        + "\n".join(offenders)
    )


def test_the_quoted_substitution_checker_catches_the_original_defect():
    """The exact line that stopped the daemon from starting on Windows."""
    original = (
        "daemon_code = (\"import sys; sys.path.insert(0, '{0}'); \"\n"
        '             "from autorun.daemon import main; main()").format(str(src_dir))'
    )
    assert quoted_substitutions(original), "checker missed the shipped defect"


def test_the_quoted_substitution_checker_accepts_the_fixed_forms():
    fixed = (
        'code = "import sys; sys.path.insert(0, {0!r}); main()".format(str(src))\n'
        'js = template.replace("__SOCKET__", json.dumps(socket))\n'
    )
    assert not quoted_substitutions(fixed)


# --- 2. POSIX-only venv layout literals --------------------------------------

_POSIX_VENV = re.compile(r"\.venv/bin/|['\"]bin['\"]\s*/\s*['\"]autorun")


def posix_only_venv_paths(source: str) -> list[str]:
    """Return venv paths written in the POSIX layout only."""
    return [m.group(0) for m in _POSIX_VENV.finditer(source)]


def test_no_module_assumes_a_posix_only_venv_layout():
    offenders = []
    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        # The constants that exist to express the difference name both, and
        # the comments explaining them necessarily spell one out.
        body = "\n".join(
            line for line in text.splitlines() if not line.strip().startswith("#")
        )
        if "Scripts" in body:
            continue
        for found in posix_only_venv_paths(body):
            offenders.append(f"{path.relative_to(PLUGIN_ROOT)}: {found}")
    assert not offenders, (
        "a venv executable path is written in the POSIX layout only. Windows "
        "uses .venv/Scripts/<name>.exe, so this lookup cannot match there. "
        "Resolve through the shared bin-dir and executable-name constants:\n"
        + "\n".join(offenders)
    )


def test_the_venv_layout_checker_catches_the_original_defect():
    original = 'venv_bin = Path(plugin_root) / ".venv" / "bin" / "autorun"'
    assert posix_only_venv_paths(original), "checker missed the shipped defect"


# --- 3. background spawns that only detach on POSIX --------------------------


def spawns_missing_windows_detach(source: str, filename: str = "<test>") -> list[int]:
    """Return line numbers of Popen calls that detach on POSIX only."""
    tree = ast.parse(source, filename=filename)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", getattr(node.func, "id", ""))
        if name not in {"Popen", "run"}:
            continue
        keywords = {kw.arg for kw in node.keywords}
        # A **kwargs spread is how the shared helper supplies both keys.
        if None in keywords:
            continue
        if "start_new_session" in keywords and "creationflags" not in keywords:
            offenders.append(node.lineno)
    return offenders


def test_no_background_spawn_detaches_on_posix_only():
    offenders = []
    for path in _python_sources():
        for lineno in spawns_missing_windows_detach(
            path.read_text(encoding="utf-8"), str(path)
        ):
            offenders.append(f"{path.relative_to(PLUGIN_ROOT)}:{lineno}")
    assert not offenders, (
        "start_new_session calls setsid() and is POSIX-only: Python accepts "
        "and ignores it on Windows, so a process meant to outlive its parent "
        "stays in the parent's tree and is reaped with it. Pass "
        "**ipc.detached_spawn_kwargs(), which supplies both keys:\n"
        + "\n".join(offenders)
    )


def test_the_detach_checker_catches_the_original_defect():
    original = (
        "subprocess.Popen(\n"
        "    [sys.executable, '-c', code],\n"
        "    stdout=subprocess.DEVNULL,\n"
        "    start_new_session=True,\n"
        ")\n"
    )
    assert spawns_missing_windows_detach(original), "checker missed the shipped defect"


def test_the_detach_checker_accepts_both_fixed_forms():
    explicit = (
        "subprocess.Popen(argv, start_new_session=True, creationflags=flags)\n"
    )
    shared = "subprocess.Popen(argv, **ipc.detached_spawn_kwargs())\n"
    assert not spawns_missing_windows_detach(explicit)
    assert not spawns_missing_windows_detach(shared)


# --- 4. home redirects that move only one variable ---------------------------

_HOME_WRITE = re.compile(r"""environ\[\s*['"]HOME['"]\s*\]\s*=|setenv\(\s*['"]HOME['"]""")


def home_writes_without_userprofile(source: str, filename: str = "<test>") -> list[int]:
    """Return line numbers where HOME moves and USERPROFILE does not."""
    try:
        documentation = _docstring_lines(ast.parse(source, filename=filename))
    except SyntaxError:
        documentation = set()
    lines = source.splitlines()
    offenders = []
    for index, line in enumerate(lines, start=1):
        if index in documentation or not _HOME_WRITE.search(line):
            continue
        # The pair has to be visible together; a redirect split across
        # unrelated functions is the bug, not a style choice.
        window = "\n".join(lines[max(0, index - 12) : index + 12])
        if "USERPROFILE" not in window:
            offenders.append(index)
    return offenders


def test_no_home_redirect_forgets_userprofile():
    # _isolation_sources, not _python_sources: a test fixture redirecting HOME
    # to a tmp_path is exactly the code this check is about, and scanning only
    # the product missed all nine offenders.
    offenders = []
    for path in _isolation_sources():
        for lineno in home_writes_without_userprofile(
            path.read_text(encoding="utf-8"), str(path)
        ):
            offenders.append(f"{path.relative_to(PLUGIN_ROOT)}:{lineno}")
    assert not offenders, (
        "HOME is redirected without USERPROFILE. Path.home() resolves through "
        "os.path.expanduser, which reads USERPROFILE on Windows and HOME "
        "elsewhere and never consults the other, so this redirect moves the "
        "home on one platform only -- an isolated run then reads a sandbox "
        "and writes a real home:\n" + "\n".join(offenders)
    )


def test_the_home_checker_catches_the_original_defect():
    original = (
        "previous = os.environ.get('HOME')\n"
        "os.environ['HOME'] = str(home)\n"
        "try:\n"
        "    yield home\n"
    )
    assert home_writes_without_userprofile(original), "checker missed the shipped defect"


def test_the_home_checker_accepts_the_fixed_form():
    fixed = (
        'names = ("HOME", "USERPROFILE")\n'
        "for name in names:\n"
        "    os.environ[name] = str(home)\n"
    )
    assert not home_writes_without_userprofile(fixed)


@pytest.mark.parametrize(
    "checker",
    [
        quoted_substitutions,
        posix_only_venv_paths,
        home_writes_without_userprofile,
    ],
)
def test_every_checker_is_quiet_on_ordinary_code(checker):
    """A checker that flags normal code gets disabled, which is the same as
    having no checker."""
    ordinary = (
        'path = Path(root) / "sessions" / f"{session_id}.json"\n'
        'message = f"exported {name} to {destination}"\n'
        "os.environ.setdefault('AUTORUN_HOME', str(home))\n"
    )
    assert not checker(ordinary)
