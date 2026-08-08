"""Subprocess-created Python environments must not select an architecture implicitly."""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SEARCH_ROOTS = (REPO_ROOT / "plugins", REPO_ROOT / "src")
PYTHON_SUFFIX = ".py"
EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
    }
)


def _literal_token(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _uv_venv_calls(path: Path) -> list[tuple[int, list[ast.AST]]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls: list[tuple[int, list[ast.AST]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        command = node.args[0]
        if not isinstance(command, (ast.List, ast.Tuple)):
            continue
        tokens = list(command.elts)
        if len(tokens) >= 2 and [_literal_token(token) for token in tokens[:2]] == [
            "uv",
            "venv",
        ]:
            calls.append((node.lineno, tokens))
    return calls


def _repository_python_files():
    for search_root in SEARCH_ROOTS:
        for path in search_root.rglob(f"*{PYTHON_SUFFIX}"):
            relative_parts = path.relative_to(REPO_ROOT).parts
            if not EXCLUDED_DIRECTORY_NAMES.intersection(relative_parts):
                yield path


def test_uv_venv_subprocesses_select_an_explicit_interpreter():
    """Require native inheritance or an intentional cross-architecture interpreter."""
    missing_interpreter: list[str] = []
    for path in _repository_python_files():
        for line, tokens in _uv_venv_calls(path):
            literal_tokens = [_literal_token(token) for token in tokens]
            if "--python" not in literal_tokens:
                missing_interpreter.append(f"{path.relative_to(REPO_ROOT)}:{line}")

    assert not missing_interpreter, (
        "uv venv subprocesses must pass --python with sys.executable for native "
        "inheritance or an explicit interpreter for intentional cross-architecture "
        f"coverage: {', '.join(missing_interpreter)}"
    )
