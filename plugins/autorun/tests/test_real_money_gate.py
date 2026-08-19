"""One owner decides what costs money, and every paid test says so.

A module name is not evidence about spending. Twenty-seven tests live in the
five ``test_*_e2e_real_money.py`` modules and only nine of them call a paid
model; the rest run local hook subprocesses. A maintainer who sees
``test_qwen_e2e_real_money.py ... FAILED`` in a log cannot tell from that line
whether an API call was billed, and in this repository's own history that
ambiguity cost a round of investigation.

Two properties fix it, and these guards hold them:

1. **Selectable.** Every paid test carries the ``real_money`` marker, so
   ``pytest -m "not real_money"`` deselects the paid set by name rather than
   leaving "no skip line appeared" as the only evidence.
2. **Single owner.** ``e2e_support.requires_real_money`` is the only gate.
   Seven modules used to re-derive ``os.environ.get(REAL_MONEY_ENV, "0") ==
   "1"`` for themselves, so a paid test added without one of those copies was
   gated by nothing and no check noticed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from e2e_support import REAL_MONEY_ENV, real_money_enabled, requires_real_money


TESTS_DIR = Path(__file__).resolve().parent
GATE_OWNER = "e2e_support.py"


def _test_modules() -> list[Path]:
    return sorted(p for p in TESTS_DIR.rglob("*.py") if p.name != "__init__.py")


def _reads_real_money_env(source: str) -> list[int]:
    """Line numbers where this source reads the opt-in variable itself.

    Only real environment access counts. A docstring or skip reason naming the
    variable is documentation, which every paid test should keep.
    """
    tree = ast.parse(source)
    variable_names = {"REAL_MONEY_ENV"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "REAL_MONEY_ENV":
                    variable_names.add(alias.asname or alias.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if isinstance(value, ast.Constant) and value.value == REAL_MONEY_ENV:
                variable_names.update(
                    target.id for target in targets if isinstance(target, ast.Name)
                )

    def names_opt_in(node: ast.AST) -> bool:
        return any(
            (isinstance(part, ast.Constant) and part.value == REAL_MONEY_ENV)
            or (isinstance(part, ast.Name) and part.id in variable_names)
            or (isinstance(part, ast.Attribute) and part.attr in variable_names)
            for part in ast.walk(node)
        )

    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name not in {"get", "getenv"}:
                continue
        elif isinstance(node, ast.Subscript):
            pass
        else:
            continue
        segment = ast.unparse(node)
        if names_opt_in(node) and ("environ" in segment or "getenv" in segment):
            lines.append(node.lineno)
    return sorted(set(lines))


@pytest.mark.parametrize(
    "source",
    [
        'os.environ.get("AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY", "0")',
        'os.getenv("AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY", "0")',
        'os.environ["AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY"]',
        'os.environ.get(REAL_MONEY_ENV, "0")',
        'os.getenv(REAL_MONEY_ENV, "0")',
        'os.environ[REAL_MONEY_ENV]',
    ],
)
def test_real_money_environment_reader_detects_literal_and_constant_forms(source):
    """The audit must recognize the forms contributors actually write."""
    assert _reads_real_money_env(source) == [1]


def test_only_e2e_support_reads_the_real_money_environment_variable():
    """One place decides whether paid tests run; the rest ask it."""
    offenders = {}
    for module in _test_modules():
        if module.name == GATE_OWNER:
            continue
        source = module.read_text(encoding="utf-8")
        if REAL_MONEY_ENV not in source:
            continue
        hits = _reads_real_money_env(source)
        if hits:
            offenders[module.relative_to(TESTS_DIR).as_posix()] = hits

    assert not offenders, (
        "These modules re-derive the real-money opt-in instead of importing "
        f"`requires_real_money` from {GATE_OWNER}. A second copy is a paid "
        "test that can be added with no gate at all: "
        f"{offenders}"
    )


def test_the_real_money_marker_is_registered(pytestconfig):
    """`--strict-markers` is on, so an unregistered mark is a collection error.

    Asked of the running config rather than parsed out of ``pyproject.toml``:
    what matters is that *pytest* knows the marker, and reading the file would
    also drag in ``tomllib``, which is not stdlib before Python 3.11 and the
    matrix still covers 3.10.
    """
    names = {entry.split(":", 1)[0].strip() for entry in pytestconfig.getini("markers")}

    assert "real_money" in names, (
        "`pytest -m \"not real_money\"` is how a maintainer proves no paid "
        "test ran. Without the registration, --strict-markers rejects the "
        f"selector outright. Registered markers: {sorted(names)}"
    )


def test_requires_real_money_both_marks_and_skips():
    """The gate is one decorator so a test cannot be labelled without being gated."""

    @requires_real_money
    def sample_test():  # pragma: no cover - never executed, only inspected
        raise AssertionError("guard fixture must not run")

    marks = {mark.name for mark in sample_test.pytestmark}
    assert "real_money" in marks, (
        "requires_real_money must label the test so it is selectable; "
        f"got {sorted(marks)}"
    )
    assert "skipif" in marks, (
        "requires_real_money must also gate the test; a label alone would let "
        f"a paid call run unguarded. Got {sorted(marks)}"
    )

    skipif = next(m for m in sample_test.pytestmark if m.name == "skipif")
    (condition,) = skipif.args
    enabled = real_money_enabled()
    assert condition is not enabled, (
        "The skip condition must be the negation of the opt-in: skip when the "
        f"variable is unset. {REAL_MONEY_ENV} enabled={enabled}, "
        f"condition={condition}"
    )
    assert REAL_MONEY_ENV in skipif.kwargs["reason"], (
        "The skip reason must name the variable so the output says how to opt in"
    )


def test_every_gated_test_lives_behind_the_shared_decorator():
    """No module may hand-roll a skipif whose reason is about paid calls."""
    offenders = {}
    for module in _test_modules():
        if module.name == GATE_OWNER:
            continue
        source = module.read_text(encoding="utf-8")
        if REAL_MONEY_ENV not in source:
            continue
        hand_rolled = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for decorator in node.decorator_list:
                text = ast.unparse(decorator)
                if "skipif" in text and REAL_MONEY_ENV in text:
                    hand_rolled.append((node.name, decorator.lineno))
        if hand_rolled:
            offenders[module.relative_to(TESTS_DIR).as_posix()] = hand_rolled

    assert not offenders, (
        "Use `@requires_real_money` from e2e_support instead of a local "
        f"skipif; one owner keeps the marker and the gate together: {offenders}"
    )
