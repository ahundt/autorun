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
    """Do not present removed commands as `/ar:*` commands.

    Claude Code namespaces a plugin skill as `/<plugin>:<skill>` and this
    plugin is named `ar`, so a workflow converted from `commands/x.md` to
    `skills/x/SKILL.md` still answers to `/ar:x`. Both surfaces count; a name
    on neither is a spelling no harness can resolve.
    """
    command_names = {
        path.stem for path in (PLUGIN_ROOT / "commands").glob("*.md")
    } | {path.parent.name for path in (PLUGIN_ROOT / "skills").glob("*/SKILL.md")}
    invalid: list[str] = []
    for path in _maintained_docs():
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for name in re.findall(r"/ar:([A-Za-z0-9_-]+)", line):
                if name not in command_names:
                    invalid.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: /ar:{name}")

    assert invalid == []


def test_every_shipped_document_has_strictly_valid_yaml_frontmatter():
    """Harnesses read frontmatter with real YAML parsers.

    Claude Code, Codex, and Qwen all parse SKILL.md and command frontmatter as
    YAML, and a document whose frontmatter will not parse loses its
    description, or the whole document, without saying so. An unquoted
    `argument-hint: [a|b] extra` or a description with a bare colon is enough.
    """
    import yaml

    invalid = []
    for path in sorted((PLUGIN_ROOT / "commands").glob("*.md")) + sorted(
        (PLUGIN_ROOT / "skills").glob("*/SKILL.md")
    ):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        try:
            yaml.safe_load(text.split("---", 2)[1])
        except yaml.YAMLError as exc:
            invalid.append(f"{path.relative_to(PLUGIN_ROOT)}: {str(exc).splitlines()[0]}")

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


def test_readme_skill_placement_matches_shared_skill_registry():
    """The primary placement guide must name every shared-root harness."""
    from autorun.platforms import PLATFORMS

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    placement = readme.split("#### Choosing where skills are installed", 1)[1].split(
        "#### Bundled Skills", 1
    )[0]
    shared = {
        platform.display_name
        for platform in PLATFORMS.values()
        if platform.loads_shared_agents_skills
    }

    for display_name in shared:
        documented = {
            "Codex CLI": "Codex",
            "Legacy Gemini CLI": "legacy Gemini",
        }.get(display_name, display_name)
        assert documented in placement
    assert "Command Code" not in placement
    assert "`opencode`" in placement


# Agent memory files are injected into model context repeatedly, so a stale
# path or method name in them is expensive: it sends every future session to a
# file or symbol that does not exist. A 2026-08-05 review found four such
# references that had drifted silently because no test read them
# (`hooks/claude-hooks.json` for `hooks/hooks.json`, and
# `CacheGuard.from_session().on_pretooluse(...)` for the real
# `CacheGuard.from_ctx(ctx).check(ctx)`). These two tests close that gap.

AGENT_MEMORY_FILES = (
    REPO_ROOT / "AGENTS.md",
    PLUGIN_ROOT / "AGENTS.md",
)
# Only repo-relative source references are checkable: a `~/...` or absolute
# path names a user's machine, not this tree.
_DOC_PATH_RE = re.compile(r"`([\w./-]+/[\w.-]+\.(?:py|json|md|toml))`")
# `ClassName.method_name(` in prose, plus the chained `).method_name(` form —
# the stale `CacheGuard.from_session().on_pretooluse(...)` hid in the chain,
# where the receiver is a `)` rather than a class name.
_DOC_METHOD_RE = re.compile(r"`?\b([A-Z]\w+)\.([a-z_][a-z0-9_]*)\(")
_DOC_CHAINED_METHOD_RE = re.compile(r"\)\.([a-z_][a-z0-9_]*)\(")


def _memory_file_text() -> list[tuple[Path, str]]:
    return [(path, path.read_text(encoding="utf-8")) for path in AGENT_MEMORY_FILES]


def test_agent_memory_files_only_reference_paths_that_exist():
    """Every repo-relative path in an agent memory file must resolve."""
    missing: list[str] = []
    for path, text in _memory_file_text():
        for reference in sorted(set(_DOC_PATH_RE.findall(text))):
            if any(
                (root / reference).exists()
                for root in (PLUGIN_ROOT, REPO_ROOT, PLUGIN_ROOT / "src" / "autorun")
            ):
                continue
            missing.append(f"{path.relative_to(REPO_ROOT)} -> {reference}")
    assert not missing, (
        "agent memory files reference paths that do not exist; every session "
        "that reads them is sent somewhere real:\n  " + "\n  ".join(missing)
    )


def test_agent_memory_files_only_reference_methods_that_exist():
    """Every `Class.method(` named in an agent memory file must be defined."""
    source = "\n".join(
        candidate.read_text(encoding="utf-8")
        for candidate in (PLUGIN_ROOT / "src" / "autorun").rglob("*.py")
        if "__pycache__" not in candidate.parts
    )
    defined = set(re.findall(r"^\s*(?:async\s+)?def\s+(\w+)", source, re.MULTILINE))
    missing: list[str] = []
    for path, text in _memory_file_text():
        named = {(owner, method) for owner, method in _DOC_METHOD_RE.findall(text)}
        named |= {("<chained>", m) for m in _DOC_CHAINED_METHOD_RE.findall(text)}
        for owner, method in sorted(named):
            if method not in defined:
                missing.append(f"{path.relative_to(REPO_ROOT)} -> {owner}.{method}()")
    assert not missing, (
        "agent memory files name methods that no longer exist:\n  "
        + "\n  ".join(missing)
    )


# === Release checklist must name real files, and name all of them ===
#
# docs/version_update_checklist.md is the release runbook. It drifted silently
# when CLAUDE.md and GEMINI.md became symlinks to AGENTS.md: the checklist kept
# naming them, and its row for GEMINI.md claimed "Install verification examples
# (8 refs)" that no longer existed anywhere. A releaser following it hunts for
# content that is not there, and any version site the checklist omits is one a
# release silently leaves stale.

_CHECKLIST = REPO_ROOT / "docs" / "version_update_checklist.md"
_CHECKLIST_PATH_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|", re.MULTILINE)


def _checklist_paths() -> set[str]:
    text = _CHECKLIST.read_text(encoding="utf-8")
    return {
        match
        for match in _CHECKLIST_PATH_RE.findall(text)
        # Table rows also carry example values like `"version": "X.Y.Z"`; a real
        # path has a suffix and no spaces or quotes.
        if "/" in match or match.endswith((".md", ".toml", ".json", ".py"))
        if '"' not in match and " " not in match
    }


def test_release_checklist_names_only_files_that_exist():
    missing = sorted(p for p in _checklist_paths() if not (REPO_ROOT / p).exists())
    assert not missing, (
        "docs/version_update_checklist.md names paths that do not exist, so a "
        "release will skip them silently:\n  " + "\n  ".join(missing)
    )


def test_public_install_guides_use_release_artifact_identities():
    """The workspace root is not the installable autorun distribution, and
    Claude registers the plugin as ``ar`` inside marketplace ``autorun``."""
    documents = (
        REPO_ROOT / "README.md",
        REPO_ROOT / "AGENTS.md",
        PLUGIN_ROOT / "README.md",
        PLUGIN_ROOT / "AGENTS.md",
        PLUGIN_ROOT / "docs" / "INTEGRATION_GUIDE.md",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in documents)

    assert "plugin install https://github.com/ahundt/autorun.git" not in combined
    assert "plugin install autorun@autorun" not in combined
    assert "hooks/claude-hooks.json" not in combined

    root_readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    artifact_readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
    for text in (root_readme, artifact_readme):
        assert "#subdirectory=plugins/autorun" in text
        assert "claude plugin install ar@autorun" in text


def test_release_checklist_covers_every_file_carrying_the_version():
    """Any file holding the current version must be on the checklist.

    Read the version from the autorun package rather than hardcoding it, so
    this keeps working across releases.
    """
    import tomllib

    pyproject = REPO_ROOT / "plugins" / "autorun" / "pyproject.toml"
    version = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]

    listed = _checklist_paths()
    uncovered = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(REPO_ROOT)
        parts = set(rel.parts)
        if parts & {".git", "notes", ".venv", "__pycache__", "htmlcov", "build", ".worktrees"}:
            continue
        if rel.suffix not in {".md", ".toml", ".json", ".py"}:
            continue
        try:
            if version not in path.read_text(encoding="utf-8"):
                continue
        except (OSError, UnicodeDecodeError):
            continue
        if rel.as_posix() not in listed:
            uncovered.append(str(rel))

    assert not uncovered, (
        f"these files contain the current version {version!r} but are absent from "
        "docs/version_update_checklist.md, so the next release will leave them "
        "stale:\n  " + "\n  ".join(sorted(uncovered))
    )
