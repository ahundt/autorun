"""Spec checker: install/uninstall destinations must not be path literals.

This is a lint-style guard, not a behavior test. It exists because the same
defect kept recurring by hand: `Path.home() / ".agents" / "skills"` was written
out at five separate call sites, and when uninstall cleanup was added it
repeated two of them. A literal in uninstall that drifts from install silently
leaves artifacts behind, and nothing fails.

The rule this enforces:

- Cross-harness shared locations come from CONFIG via the `shared_agents_*`
  helpers in install.py.
- Per-harness locations come from `platforms.Platform.config_dir` and its
  companion fields, which config.py:1060-1062 already names the single source
  of truth.

If this test fails, do not add the path to the allowlist below unless it is
genuinely a fixed third-party location autorun does not deploy into. Add a
CONFIG key or a Platform field instead.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "autorun"

# Directory names autorun DEPLOYS into. A literal naming one of these is the
# defect this checker exists to catch.
DEPLOY_DIR_LITERALS = {
    ".agents",
    ".agent",
}

# Locations autorun reads or writes that belong to a specific third-party tool
# and are fixed by that tool's own spec, so a literal is correct. Each entry
# names why it is exempt.
ALLOWED_LITERAL_FILES = {
    # Reads another tool's fixed layout rather than deploying autorun assets.
    "plan_export.py": "plan export reads Claude Code's own fixed config paths",
    "ai_monitor.py": "session state dir, already overridable via AUTORUN_TEST_STATE_DIR",
}


def _python_sources() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _string_literals(path: Path) -> list[tuple[int, str]]:
    """Return (lineno, value) for every string constant in a module."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:  # pragma: no cover - would fail the whole suite anyway
        pytest.fail(f"{path} does not parse: {exc}")
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _path_built_literals(path: Path) -> list[tuple[int, str]]:
    """Return string literals used to CONSTRUCT a path, not to match one.

    `Path.home() / ".agents" / "skills"` is a deploy destination and is what
    this checker targets. `".claude" in str(target)` inspects a path someone
    else owns and is not a destination, so matching on bare literals produces
    false positives. Only `/` operands and `Path(...)` arguments count.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:  # pragma: no cover
        pytest.fail(f"{path} does not parse: {exc}")

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            for side in (node.left, node.right):
                if isinstance(side, ast.Constant) and isinstance(side.value, str):
                    found.append((side.lineno, side.value))
        elif isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name in {"Path", "expanduser", "joinpath"}:
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        found.append((arg.lineno, arg.value))
    return found


def test_no_source_file_hardcodes_a_shared_agents_directory():
    """The shared agents dir is CONFIG['shared_agents_dir'], never a literal."""
    offenders: list[str] = []
    for path in _python_sources():
        if path.name in ALLOWED_LITERAL_FILES:
            continue
        for lineno, value in _path_built_literals(path):
            if value in DEPLOY_DIR_LITERALS:
                offenders.append(f"{path.relative_to(SRC)}:{lineno}: {value!r}")

    assert not offenders, (
        "Hardcoded shared-agents directory. Use install.shared_agents_dir() or "
        "shared_agents_skills_dir() so install and uninstall read one "
        "configuration:\n  " + "\n  ".join(offenders)
    )


def test_no_source_file_hardcodes_a_harness_config_directory():
    """Per-harness config dirs live on Platform.config_dir.

    A literal here means install and uninstall can disagree about where a
    harness keeps its files, and a relocated config_dir is silently ignored.
    """
    harness_dirs = {".claude", ".codex", ".gemini", ".qwen", ".forge"}
    # install.py legitimately holds the *defaults* that Platform.config_dir
    # values are compared against, plus marketplace-cache discovery that
    # predates this rule. Cap the count so it cannot grow.
    budget = {"install.py": 30, "main.py": 1}

    counts: dict[str, int] = {}
    detail: dict[str, list[str]] = {}
    for path in _python_sources():
        if path.name in ALLOWED_LITERAL_FILES:
            continue
        for lineno, value in _path_built_literals(path):
            if value in harness_dirs:
                key = path.name
                counts[key] = counts.get(key, 0) + 1
                detail.setdefault(key, []).append(f"{path.relative_to(SRC)}:{lineno}")

    for name, found in sorted(counts.items()):
        allowed = budget.get(name, 0)
        assert found <= allowed, (
            f"{name} gained hardcoded harness config directories "
            f"({found} > {allowed} allowed). Derive them from "
            f"platforms.Platform.config_dir instead, or lower the budget if you "
            f"removed some:\n  " + "\n  ".join(detail[name])
        )


def test_every_configured_location_key_has_a_helper():
    """A CONFIG key nobody reads is a promise the code does not keep."""
    import sys

    sys.path.insert(0, str(SRC.parent))
    from autorun.config import CONFIG

    install_src = (SRC / "install.py").read_text(encoding="utf-8")
    for key in CONFIG:
        if not key.startswith(("shared_agents_", "codex_plugin_source_")):
            continue
        assert f'"{key}"' in install_src, (
            f"CONFIG['{key}'] is declared but never read by install.py"
        )


def test_uninstall_and_install_share_every_location_helper():
    """Both sides must call the same helpers, or cleanup misses artifacts."""
    src = (SRC / "install.py").read_text(encoding="utf-8")

    def _function_body(name: str) -> str:
        match = re.search(rf"^def {re.escape(name)}\(.*?(?=^def |\Z)", src, re.M | re.S)
        assert match, f"{name} not found in install.py"
        return match.group(0)

    uninstall_body = "".join(
        _function_body(fn)
        for fn in ("_uninstall_agents_skill_links", "_uninstall_platform_memory_blocks")
    )
    assert "shared_agents_skills_dir()" in uninstall_body
    assert "platform_skills_dir(" in uninstall_body
    # And it must not reconstruct paths by hand.
    assert 'Path.home() / ".agents"' not in uninstall_body
    assert 'Path.home() / ".claude"' not in uninstall_body


def test_platform_memory_declarations_are_complete_or_absent():
    """A half-declared memory file installs nothing and reports nothing."""
    import sys

    sys.path.insert(0, str(SRC.parent))
    from autorun.platforms import PLATFORMS

    for name, platform in PLATFORMS.items():
        declared = [
            bool(platform.memory_filename),
            bool(platform.memory_template),
            bool(platform.memory_sentinel_slug),
        ]
        assert len(set(declared)) == 1, (
            f"platform {name!r} declares memory fields partially: "
            f"filename={platform.memory_filename!r} "
            f"template={platform.memory_template!r} "
            f"slug={platform.memory_sentinel_slug!r}"
        )


def test_platform_skills_subdir_requires_a_config_dir():
    """skills_subdir without config_dir resolves to nothing, silently."""
    import sys

    sys.path.insert(0, str(SRC.parent))
    from autorun.platforms import PLATFORMS

    for name, platform in PLATFORMS.items():
        if platform.skills_subdir:
            assert platform.config_dir, (
                f"platform {name!r} declares skills_subdir with no config_dir"
            )
