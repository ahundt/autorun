"""Keep maintained user documentation aligned with installed interfaces."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DOC_PARTS = {".git", ".venv", "notes", "rejected_plans", "worktrees"}


def _maintained_docs() -> list[Path]:
    """Return shipped Markdown while excluding historical and generated copies."""
    return sorted(
        path
        for path in REPO_ROOT.rglob("*.md")
        if path.name != "CHANGELOG.md"
        and not EXCLUDED_DOC_PARTS.intersection(path.parts)
    )


def _long_cli_options(parser: argparse.ArgumentParser) -> set[str]:
    """Collect public long options recursively from the argparse tree."""
    options: set[str] = set()
    for action in parser._actions:
        options.update(
            option
            for option in action.option_strings
            if option.startswith("--") and option != "--help"
        )
        if isinstance(action, argparse._SubParsersAction):
            for subparser in action.choices.values():
                options.update(_long_cli_options(subparser))
    return options


def _cli_choice_signatures(parser: argparse.ArgumentParser) -> set[str]:
    """Collect exact accepted-value lists recursively from argparse choices."""
    signatures: set[str] = set()
    for action in parser._actions:
        long_options = [option for option in action.option_strings if option.startswith("--")]
        if long_options and action.choices:
            signatures.add(f"{long_options[0]}: {'|'.join(map(str, action.choices))}")
        if isinstance(action, argparse._SubParsersAction):
            for subparser in action.choices.values():
                signatures.update(_cli_choice_signatures(subparser))
    return signatures


def test_readme_mentions_every_public_cli_option():
    """New CLI flags must be documented in the primary user reference."""
    from autorun.__main__ import create_parser

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    missing = sorted(
        option for option in _long_cli_options(create_parser()) if option not in readme
    )

    assert missing == []


def test_readme_lists_every_cli_choice_value():
    """Option docs must state usable values, not only parameter names."""
    from autorun.__main__ import create_parser

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    missing = sorted(
        signature
        for signature in _cli_choice_signatures(create_parser())
        if signature not in readme
    )

    assert missing == []


def test_maintained_docs_reference_only_installed_autorun_commands():
    """Do not present skills or removed commands as `/ar:*` commands."""
    command_names = {
        path.stem for path in (PLUGIN_ROOT / "commands").glob("*.md")
    }
    invalid: list[str] = []
    for path in _maintained_docs():
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for name in re.findall(r"/ar:([A-Za-z0-9_-]+)", line):
                if name not in command_names:
                    invalid.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: /ar:{name}")

    assert invalid == []


def test_every_installed_command_has_a_description():
    """Command menus and capability snapshots require useful metadata."""
    from autorun.command_docs import iter_command_docs

    missing = [
        doc.path.name
        for doc in iter_command_docs(PLUGIN_ROOT / "commands")
        if not doc.description.strip()
    ]

    assert missing == []


def test_readme_documents_custom_harness_grammar_and_values():
    """Custom harness help must use the same unambiguous grammar as the parser."""
    from autorun.platforms import custom_harness_spec_help

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    help_text = custom_harness_spec_help()

    assert "name=flavor:binary:config_dir[::display]" in readme
    assert "name=flavor:binary:config_dir[:display]" not in readme
    for flavor in ("gemini", "qwen", "antigravity", "agy", "codex"):
        assert flavor in help_text
        assert flavor in readme
