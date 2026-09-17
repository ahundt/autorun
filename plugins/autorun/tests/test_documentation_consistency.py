"""Keep maintained user documentation aligned with installed interfaces."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 CI only
    import tomli as tomllib


REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DOC_PARTS = {".git", ".venv", "notes", "rejected_plans", "worktrees"}
# Release-inventory scope. Kept apart from EXCLUDED_DOC_PARTS on purpose: that
# one answers "is this maintained documentation", this one answers "must this
# move at release time". Both used to name the worktree directory and disagreed
# about its spelling ("worktrees" against ".worktrees"), which is why nested
# checkouts are excluded by their .git rather than by name in _own_files.
_EXCLUDED_INVENTORY_PARTS = {
    ".git", "notes", ".venv", "__pycache__", "htmlcov", "build", ".worktrees",
}


def _own_files(root: Path, excluded_dir_names: set[str]) -> Iterator[Path]:
    """Walk a repository's own files, never a checkout that happens to live in it.

    A nested checkout carries a ``.git`` at its own root: a directory for a
    clone, a file for a git worktree. Claude Code puts an agent's isolated
    worktree at ``.claude/worktrees/agent-<id>/``, so the whole repository
    appears a second time inside itself, and a scan that walks blindly counts
    every copied CHANGELOG.md and pyproject.toml as a file of this repository.
    That turned the release inventory below red for as long as an agent
    worktree existed — a passing suite became a failing one because of a
    feature that touches no source at all.

    The two callers keep their own name lists because they mean different
    things (generated docs versus release-inventory scope), but "this is
    somebody else's checkout" is one rule, and it is this one. Pruning in
    ``os.walk`` rather than filtering ``rglob`` output also means a large
    nested tree is never descended into.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = [
            name
            for name in dirnames
            if name not in excluded_dir_names and not (here / name / ".git").exists()
        ]
        for name in filenames:
            yield here / name


def _maintained_docs() -> list[Path]:
    """Return shipped Markdown while excluding historical and generated copies."""
    return sorted(
        path
        for path in _own_files(REPO_ROOT, EXCLUDED_DOC_PARTS)
        if path.suffix == ".md"
        and path.name != "CHANGELOG.md"
        and not EXCLUDED_DOC_PARTS.intersection(path.parts)
    )


def _long_cli_options(parser: argparse.ArgumentParser) -> set[str]:
    """Collect public long options recursively from the argparse tree."""
    options: set[str] = set()
    for action in parser._actions:
        if action.help is not argparse.SUPPRESS:
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
# RELEASING.md is the release runbook. It drifted silently
# when CLAUDE.md and GEMINI.md became symlinks to AGENTS.md: the checklist kept
# naming them, and its row for GEMINI.md claimed "Install verification examples
# (8 refs)" that no longer existed anywhere. A releaser following it hunts for
# content that is not there, and any version site the checklist omits is one a
# release silently leaves stale.

_CHECKLIST = REPO_ROOT / "RELEASING.md"
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
        "RELEASING.md names paths that do not exist, so a "
        "release will skip them silently:\n  " + "\n  ".join(missing)
    )


_LINE_CONTINUATION_RE = re.compile(r"\\\s*\n\s*")
_TAG_COMMAND_RE = re.compile(r"^\s*git tag -a .*$", re.MULTILINE)


def _shell_commands(text: str) -> list[str]:
    r"""Join backslash line continuations so one command is one string.

    Matching physical lines is what a first version of the caller below did, and
    it silently stopped checking anything the moment the documented command grew
    long enough to wrap: `-F` moved to the continuation line, the flag test
    looked only at line one, and the gate reported success while inspecting half
    a command. It would equally have missed a real `-F` sitting on a
    continuation line, which is the failure it exists to catch.
    """
    return _LINE_CONTINUATION_RE.sub(" ", text).splitlines()


def test_release_checklist_tags_without_stripping_markdown_headings():
    """`git tag -a -F notes.md` deletes every line of the notes that starts with #.

    Stage 4 exists so "the annotated tag carries the release body, so `git show`
    gives the notes to anyone with a clone". The plain `-F` form cannot deliver
    that: git's default `--cleanup=strip` treats a leading `#` as a comment, so
    it silently drops the title, every `##` section heading, and even the `#`
    comments inside the install code block, then collapses blank runs.

    The first release tagged under this runbook used the plain form and lost all
    ten of its `#` lines, 155 source lines arriving as 137. Nothing failed: the
    tag was created, the push succeeded, and the damage is visible only by
    diffing the annotation against the file. The published GitHub Release was
    unaffected because its body comes from `--notes-file` at creation time,
    which is why this can rot unnoticed across releases.

    The second assertion keeps the first honest. Checking only for the flag
    would keep passing if the notes ever stopped containing `#` lines, and would
    then be a gate for a property nothing depends on.
    """
    joined = "\n".join(_shell_commands(_CHECKLIST.read_text(encoding="utf-8")))
    tag_commands = [c.strip() for c in _TAG_COMMAND_RE.findall(joined)]
    assert tag_commands, "RELEASING.md documents no `git tag -a` command"

    from_file = [c for c in tag_commands if "-F" in c.split()]
    assert from_file, (
        "RELEASING.md documents `git tag -a` but never with -F, so the annotated "
        "tag no longer carries the release body this check protects"
    )

    unsafe = [c for c in from_file if "--cleanup=verbatim" not in c]
    assert not unsafe, (
        "RELEASING.md tags from a notes file without --cleanup=verbatim, so git "
        "will delete every Markdown heading from the annotation:\n  "
        + "\n  ".join(unsafe)
    )

    notes = sorted((REPO_ROOT / "docs" / "releases").glob("*.md"))
    assert notes, "no release notes found to protect"
    hashed = [n.name for n in notes if any(
        line.startswith("#") for line in n.read_text(encoding="utf-8").splitlines()
    )]
    assert hashed, (
        "no release-notes file starts a line with '#', so the --cleanup=verbatim "
        "requirement above now guards nothing and should be re-derived"
    )


_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.MULTILINE)
_SAME_FILE_LINK_RE = re.compile(r"\[[^\]]+\]\(#([^)]+)\)")


def _github_slug(heading: str) -> str:
    """Reproduce GitHub's heading anchor, including its double-hyphen quirk.

    Punctuation is dropped and each remaining space becomes one hyphen. Runs are
    *not* collapsed, so "CI \N{EM DASH} **RELEASER**" loses the dash and keeps the
    two spaces that surrounded it, landing at `...ci--releaser` with two hyphens.
    A slugger that collapses runs calls those correct anchors broken and buries
    the one real breakage among them, which is how this check first misread
    three of RELEASING.md's four stage headings.
    """
    return re.sub(r"[^\w\s-]", "", heading.strip().lower()).replace(" ", "-")


def test_every_same_file_doc_anchor_resolves_to_a_heading():
    """A renamed heading silently kills every table-of-contents link into it.

    RELEASING.md's overview table is how a releaser enters the runbook, and its
    Stage 5 row pointed at `#stage-5-verify-tag-is-on-the-right-commit` after the
    heading had become "Stage 5: Verify the tag, then approve the tag run". The
    link still rendered, still looked clickable, and landed on the top of the
    page, so the reader arrived at Stage 1 believing they were at Stage 5: the
    one stage whose approval publishes to PyPI permanently.

    Nothing catches this by reading: a dead anchor is indistinguishable from a
    live one in Markdown source. The structural check is cheap over the whole
    repository, so it runs there rather than on the release docs alone.
    """
    broken: list[str] = []
    for path in _maintained_docs():
        text = _FENCE_RE.sub("", path.read_text(encoding="utf-8"))
        headings = {_github_slug(match) for match in _HEADING_RE.findall(text)}
        broken.extend(
            f"{path.relative_to(REPO_ROOT)} -> #{anchor}"
            for anchor in _SAME_FILE_LINK_RE.findall(text)
            if anchor not in headings
        )
    assert not broken, (
        "Markdown links point at headings that do not exist, so they silently "
        "land at the top of the page instead:\n  " + "\n  ".join(sorted(broken))
    )


_UV_PROJECT_RE = re.compile(r"uv run --project (\S+)")
_CD_RE = re.compile(r"\(cd (\S+) &&")


def test_every_checklist_uv_command_names_a_real_project():
    """A runbook command has to *work*, not merely name files that exist.

    `(cd plugins/pdf-extractor && uv run --project . ...)` read fine and passed
    every prose check, but that directory deliberately has no `pyproject.toml`:
    UV resolved the workspace root instead, the autorun-ai distribution was never
    installed, and the documented command failed in a clean environment with
    four `ModuleNotFoundError: pdf_extraction` errors. The release gate ran
    something else entirely, so nothing noticed.

    Checking the project argument against a real `pyproject.toml` is the
    smallest structural property that would have caught it.
    """
    text = _CHECKLIST.read_text(encoding="utf-8")
    broken = []
    for line in text.splitlines():
        match = _UV_PROJECT_RE.search(line)
        if not match:
            continue
        project = match.group(1)
        base = REPO_ROOT
        entered = _CD_RE.search(line)
        if entered:
            base = REPO_ROOT / entered.group(1)
        resolved = (base / project).resolve()
        if not (resolved / "pyproject.toml").is_file():
            broken.append(line.strip())
    assert not broken, (
        "RELEASING.md runs `uv run --project` against a "
        "directory with no pyproject.toml, so UV resolves some other project "
        "and the command does not test what the runbook claims:\n  "
        + "\n  ".join(broken)
    )


def test_release_runbook_rehearses_testpypi_before_tagging():
    """A linear release run must prove OIDC publication before creating the tag."""
    runbook = _CHECKLIST.read_text(encoding="utf-8")

    setup = runbook.index("### One-time setup")
    rehearsal = runbook.index("### Rehearse on TestPyPI before any tag")
    tag = runbook.index("### Stage 4: Tag and push")
    assert setup < rehearsal < tag, (
        "RELEASING.md places TestPyPI setup or rehearsal "
        "after tag creation, so a releaser following the document in order can "
        "create the public tag before proving trusted publishing"
    )


def test_release_notes_name_upgrade_actions_date_and_diff():
    """The tracked GitHub release body must answer upgrade questions directly."""
    version = _declared_version("plugins/autorun/pyproject.toml", "version")
    notes = (REPO_ROOT / "docs" / "releases" / f"{version}.md").read_text(
        encoding="utf-8"
    )
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    # `findall`, not `search`: a second section for the same version would
    # otherwise be invisible, and the release body would agree with whichever
    # one came first. Learned from ai-session-search, whose gate stopped at the
    # first matching heading.
    headings = re.findall(
        rf"^## \[{re.escape(version)}\] - (\d{{4}}-\d{{2}}-\d{{2}})$",
        changelog,
        re.MULTILINE,
    )
    assert len(headings) == 1, (
        f"CHANGELOG.md has {len(headings)} sections for {version}; a release "
        "must have exactly one, or the notes agree with an arbitrary copy"
    )
    # The digit shape alone accepts 2026-02-30. Parsing rejects it, and rejects
    # a date that has not happened yet, which is what a stale heading looks like
    # when the bump was prepared days before the tag.
    from datetime import date

    try:
        stamped = date.fromisoformat(headings[0])
    except ValueError as error:
        raise AssertionError(
            f"CHANGELOG.md dates {version} {headings[0]!r}, which is not a real "
            f"calendar date ({error}); the digit shape alone accepts 2026-02-30"
        ) from None
    assert stamped <= date.today(), (
        f"CHANGELOG.md dates {version} {stamped.isoformat()}, which is in the "
        "future; set it to the day the release is tagged"
    )
    assert f"Date: {headings[0]}" in notes
    assert "## Upgrade notes" in notes
    assert re.search(rf"compare/v\S+\.\.\.v{re.escape(version)}", notes), (
        "the release body must carry a comparison link readers can follow"
    )


def _tags_published_on_origin() -> set[str] | None:
    """Tags that exist on the remote, or None when the remote is unreachable."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", "origin"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return {
        line.rsplit("refs/tags/", 1)[-1].removesuffix("^{}")
        for line in result.stdout.splitlines()
        if "refs/tags/" in line
    }


def test_release_notes_compare_link_names_a_tag_that_exists_publicly():
    """A dead verification link is worse than no link.

    The body compared from `v0.12.0`, a tag that exists only locally: the URL
    returns 404 for every reader who is not the maintainer, and tagging the
    release later would not repair the *base*. The
    old assertion hardcoded the same wrong string, so it agreed with the defect
    instead of catching it — it checked the sentence, not the claim.

    Only the base is checked here. The release's own tag is created by a later
    authorized publication step and is expected to be absent until then.
    """
    version = _declared_version("plugins/autorun/pyproject.toml", "version")
    notes = (REPO_ROOT / "docs" / "releases" / f"{version}.md").read_text(
        encoding="utf-8"
    )
    match = re.search(rf"compare/(v\S+?)\.\.\.v{re.escape(version)}", notes)
    assert match, "no comparison link found in the release body"

    published = _tags_published_on_origin()
    if published is None:
        pytest.skip("origin is unreachable, so publication cannot be verified")
    assert match.group(1) in published, (
        f"the release body compares from {match.group(1)}, which is not a tag "
        f"on origin, so the published link 404s. Published tags: "
        f"{sorted(published)}"
    )


def test_readme_documents_every_harness_skill_placement_accepts():
    """The documented vocabulary is what a user can discover; the parser's is
    what actually works. When they disagree, the difference is invisible.

    `settings.harness_names()` reads the registry so the parser cannot drift,
    but the README's list was typed by hand and omitted `prime`. Prime Agent
    users could not learn from any document that `--skill-placement
    prime=native` is accepted, even though it always was.
    """
    from autorun.installer.settings import harness_names

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    sentence = re.search(r"Valid harness names are(.+?)\.", readme, re.DOTALL)
    assert sentence, "README no longer states the valid harness names"
    documented = set(re.findall(r"`([a-z]+)`", sentence.group(1)))

    missing = sorted(set(harness_names()) - documented)
    invented = sorted(documented - set(harness_names()))
    assert not missing and not invented, (
        f"README.md's harness list disagrees with the registry. "
        f"Undocumented but accepted: {missing}. Documented but rejected: {invented}."
    )


def test_plugin_readme_describes_one_python_distribution():
    """One distribution is the packaging contract; the plugin README denied it.

    `pdf-extractor` is a harness plugin, not a Python package: `pyproject.toml`
    ships its code inside `autorun` behind the `pdf` extra. A reader following
    the old sentence searches an index for a distribution that does not exist.
    """
    readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")

    assert "autorun-ai[pdf]" in readme, (
        "the plugin README must name the extra that actually installs PDF support"
    )
    assert "distribution is released and installed separately" not in readme


def test_root_readme_starts_with_published_install_and_no_generated_banner():
    """The first-run path must use the release artifact without generated artwork."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    quick_start = readme.split("## Quick Start", 1)[1].split("\n## ", 1)[0]

    assert "uv tool install autorun" in quick_start
    assert "Gemini_Generated_Image" not in readme


def test_current_changelog_covers_pi_and_published_distributions():
    """The current release entry must describe capability and distribution surfaces."""
    version = _declared_version("plugins/autorun/pyproject.toml", "version")
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    section = changelog.split(f"## [{version}]", 1)[1].split("\n## [", 1)[0]

    for required in ("Pi", "PyPI", "`autorun-ai`", "`autorun-ai[pdf]`"):
        assert required in section, f"CHANGELOG.md [{version}] omits {required}"


def test_published_distributions_have_project_urls():
    """Package-index users need source, issue, and homepage links in metadata."""
    for relative_path in ("plugins/autorun/pyproject.toml",):
        project = tomllib.loads((REPO_ROOT / relative_path).read_text(encoding="utf-8"))[
            "project"
        ]
        urls = project.get("urls", {})
        assert {"Homepage", "Repository", "Issues"} <= set(urls), (
            f"{relative_path} omits project URLs from its package-index metadata"
        )


def test_public_install_guides_use_release_artifact_identities():
    """The workspace root is not the installable autorun-ai distribution, and
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
        assert "uv tool install autorun" in text

    pdf_readme = (
        REPO_ROOT / "plugins" / "pdf-extractor" / "README.md"
    ).read_text(encoding="utf-8")
    assert "uv tool install 'autorun-ai[pdf]'" in pdf_readme


def test_release_checklist_covers_every_file_carrying_the_version():
    """Any file holding the current version must be on the checklist.

    Read the version from the autorun package rather than hardcoding it, so
    this keeps working across releases.
    """
    # Read with a regex rather than tomllib: tomllib is stdlib only on 3.11+,
    # and autorun supports 3.10 (pyproject.toml requires-python). Same approach
    # as build_support.build_metadata, which reads this field the same way.
    pyproject = REPO_ROOT / "plugins" / "autorun" / "pyproject.toml"
    match = re.search(
        r'^version\s*=\s*["\']([^"\']+)["\']',
        pyproject.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match, f"no version field in {pyproject}"
    version = match.group(1)

    listed = _checklist_paths()
    uncovered = []
    for path in _own_files(REPO_ROOT, _EXCLUDED_INVENTORY_PARTS):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(REPO_ROOT)
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
        "RELEASING.md, so the next release will leave them "
        "stale:\n  " + "\n  ".join(sorted(uncovered))
    )


@pytest.mark.parametrize("nested_git", ("dir", "file"))
@pytest.mark.parametrize(
    "location",
    (
        ".claude/worktrees/agent-abc123",  # what Claude Code actually creates
        "vendor/some-other-repo",  # any nested clone, named nothing special
    ),
)
def test_a_checkout_inside_the_repository_is_not_scanned_as_part_of_it(
    tmp_path, nested_git, location
):
    """A copy of this repository inside it is somebody else's, not ours.

    Both shapes appear in practice: ``dir`` is a nested clone, ``file`` is a
    git worktree, which is what Claude Code creates at
    ``.claude/worktrees/agent-<id>/`` when an agent runs isolated. Either way
    the copy holds a CHANGELOG.md and a pyproject.toml carrying this version,
    and counting them made the release-inventory check below report every one
    as an uninventoried file.

    ``_EXCLUDED_INVENTORY_PARTS`` is the set that matters: it names
    ``.worktrees`` and so never covered ``.claude/worktrees``, which is why
    the real failure happened under that set and not under the doc one. The
    ``vendor/`` case pins that the rule is "has its own .git" rather than a
    second directory name to remember — an earlier draft of this test put the
    copy only where a name list already excluded it, and passed with the fix
    removed.

    Built in tmp_path rather than under the real root: writing a fake checkout
    into this repository would be visible to the other xdist workers scanning
    it at the same time, so the test would corrupt the runs it shares a repo
    with. The predicate exercised is the one the scan uses.
    """
    (tmp_path / "CHANGELOG.md").write_text("mine\n", encoding="utf-8")
    nested = tmp_path / location
    (nested / "docs").mkdir(parents=True)
    (nested / "CHANGELOG.md").write_text("theirs\n", encoding="utf-8")
    (nested / "docs" / "deep.md").write_text("theirs too\n", encoding="utf-8")
    if nested_git == "dir":
        (nested / ".git").mkdir()
        (nested / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    else:
        (nested / ".git").write_text("gitdir: /elsewhere/.git/worktrees/agent\n", encoding="utf-8")

    found = {
        path.relative_to(tmp_path).as_posix()
        for path in _own_files(tmp_path, _EXCLUDED_INVENTORY_PARTS)
    }

    assert "CHANGELOG.md" in found, "the repository's own files must still be scanned"
    intruders = sorted(p for p in found if p.startswith(f"{location}/"))
    assert not intruders, (
        "files inside a nested checkout were scanned as if they belonged to "
        f"this repository: {intruders}"
    )


def _declared_version(relative_path: str, field: str) -> str:
    """Read one release-identity field, by its own file format."""
    path = REPO_ROOT / relative_path
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".toml":
        # Regex, not tomllib: tomllib is 3.11+ and autorun supports 3.10. Same
        # approach as build_support.build_metadata.
        match = re.search(rf'^{field}\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    elif path.suffix == ".py":
        match = re.search(rf'{field}\s*=\s*["\']([^"\']+)["\']', text)
    else:
        document = json.loads(text)
        if "plugins" in document and field == "plugins":
            versions = {plugin["version"] for plugin in document["plugins"]}
            assert len(versions) == 1, f"{relative_path} releases its plugins apart"
            return versions.pop()
        return document[field]
    assert match, f"no {field} field in {relative_path}"
    return match.group(1)


# Every field that declares the release version, and must therefore move
# together. Files that merely *contain* the version as test data are excluded on
# purpose -- see Gotcha 5 in RELEASING.md. The root
# marketplace catalog is excluded too: it carries the stable base line, which
# test_root_marketplace_catalog_tracks_the_plugin_base_release checks separately.
RELEASE_IDENTITY_FIELDS = (
    ("pyproject.toml", "version"),
    ("src/autorun_workspace/__init__.py", "__version__"),
    (".claude-plugin/marketplace.json", "plugins"),
    ("plugins/autorun/pyproject.toml", "version"),
    ("plugins/autorun/.claude-plugin/plugin.json", "version"),
    ("plugins/autorun/.claude-plugin/marketplace.json", "plugins"),
    ("plugins/autorun/.codex-plugin/plugin.json", "version"),
    ("plugins/autorun/src/autorun/__init__.py", "__version__"),
    ("plugins/autorun/src/autorun/metadata.json", "version"),
    ("plugins/autorun/src/autorun/gemini_template/gemini-extension.json", "version"),
    # The pdf plugin has no pyproject: it is a harness plugin whose code ships
    # inside the autorun-ai distribution as `pdf_extraction`.
    ("plugins/pdf-extractor/src/pdf_extraction/__init__.py", "__version__"),
    ("plugins/pdf-extractor/.claude-plugin/plugin.json", "version"),
    ("plugins/pdf-extractor/gemini-extension.json", "version"),
)


def test_every_release_identity_field_declares_the_same_version():
    """A file left at the previous version must fail the release, not ship.

    test_release_checklist_covers_every_file_carrying_the_version answers a
    different question: it skips any file that does not contain the *current*
    version, which is exactly the shape a stale file has. This one names each
    declaration site and requires them to agree.
    """
    source = _declared_version("plugins/autorun/pyproject.toml", "version")
    stale = {
        path: found
        for path, field in RELEASE_IDENTITY_FIELDS
        if (found := _declared_version(path, field)) != source
    }

    assert not stale, (
        f"plugins/autorun/pyproject.toml declares {source!r}; these disagree and "
        "would ship the wrong version:\n  "
        + "\n  ".join(f"{path}: {found!r}" for path, found in sorted(stale.items()))
    )


def test_root_marketplace_catalog_tracks_the_plugin_base_release():
    marketplace = json.loads(
        (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(
            encoding="utf-8"
        )
    )
    plugin_versions = {plugin["version"] for plugin in marketplace["plugins"]}

    assert len(plugin_versions) == 1, "marketplace plugins must release together"
    plugin_version = plugin_versions.pop()
    base_version = re.sub(r"(?:a|b|rc)\d+$", "", plugin_version)
    assert marketplace["version"] == base_version, (
        "the catalog version names the stable release line while plugin entries "
        "carry the full prerelease version"
    )


def test_the_pdf_plugin_ships_inside_autorun_and_not_beside_it():
    """One distribution. The pdf plugin is a harness plugin, not a package.

    Two things pull the other way and both have already been tried. Splitting it
    out again is easy to do by accident, and it costs a second trusted publisher,
    a second version to keep in step, and a second `uv tool install` whose
    ``extract-pdfs`` entry point silently loses the shared name to whichever tool
    installed last. The plugin id in .claude-plugin/plugin.json is a separate
    namespace from any of this and stays `pdf-extractor`.
    """
    assert not (REPO_ROOT / "plugins" / "pdf-extractor" / "pyproject.toml").exists(), (
        "plugins/pdf-extractor declares a distribution again; its code belongs "
        "in plugins/pdf-extractor/src/pdf_extraction behind the `pdf` extra"
    )
    assert (REPO_ROOT / "plugins" / "autorun" / "src" / "pdf_extraction").is_dir()

    autorun = tomllib.loads(
        (REPO_ROOT / "plugins" / "autorun" / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )["project"]
    assert autorun["name"] == "autorun-ai"
    assert autorun["scripts"]["extract-pdfs"] == "pdf_extraction.cli:main"

    plugin = json.loads(
        (
            REPO_ROOT / "plugins" / "pdf-extractor" / ".claude-plugin" / "plugin.json"
        ).read_text(encoding="utf-8")
    )
    assert plugin["name"] == "pdf-extractor", (
        "the harness plugin id is its own namespace and renaming it would break "
        "`claude plugin install pdf-extractor@autorun`"
    )

    workspace = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert set(workspace["tool"]["uv"]["sources"]) == {"autorun-ai"}, (
        "[tool.uv.sources] keys must match the member distribution names or uv "
        "resolves the workspace member from PyPI instead of the local tree"
    )
    assert workspace["tool"]["uv"]["workspace"]["members"] == ["plugins/autorun"]


def _autorun_extras() -> dict:
    return tomllib.loads(
        (REPO_ROOT / "plugins" / "autorun" / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )["project"]["optional-dependencies"]


def test_pdf_extractor_extras_avoid_retired_or_known_vulnerable_backends():
    """Published extras must not select abandoned or unpatched dependencies."""
    extras = _autorun_extras()
    cpu = "\n".join(extras["pdf"]).lower()
    gpu = "\n".join(extras["pdf-gpu"]).lower()

    assert "pypdf>=" in cpu
    assert "pypdf2" not in cpu
    assert "docling>=" in gpu
    assert "sys_platform != 'darwin'" in gpu
    assert "marker-pdf" not in gpu


def test_pdf_extraction_backends_are_all_optional():
    """Installing autorun must not force any extraction library on anyone.

    Every backend imports inside its own ``extract()`` call, so a required
    dependency here would buy nothing and cost every autorun user the download —
    including the ones who never touch a PDF. tests/pdf_extraction/ runs in
    autorun's own suite now, so CI has to name ``--extra pdf`` or it would
    exercise a package with no backend installed and still pass.
    """
    required = tomllib.loads(
        (REPO_ROOT / "plugins" / "autorun" / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )["project"]["dependencies"]
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    backends = ("markitdown", "pdfplumber", "pdfminer", "pypdf", "docling", "pymupdf4llm")
    assert not [
        entry for entry in required if entry.lower().startswith(backends)
    ], f"an extraction backend became a required autorun dependency: {required}"
    assert {"pdf", "pdf-gpu", "pdf-llm", "pdf-progress", "pdf-all"} <= set(
        _autorun_extras()
    )
    assert "--extra pdf" in workflow, (
        "CI runs tests/pdf_extraction/ without the pdf extra, so the backend "
        "tests would pass against a package that has no backend installed"
    )


def test_a_prerelease_note_pins_the_version_it_tells_readers_to_install():
    """`uv tool install autorun` cannot install a prerelease.

    pip and uv both exclude prereleases from an unqualified requirement, so the
    RC note's own command installs nothing while the project is RC-only, and
    installs the *stable* release the moment one exists — in both cases leaving
    the reader believing they are running the candidate the note describes. A
    version that says `rc`, `a` or `b` has to carry the pin into every install
    line the note prints.
    """
    version = tomllib.loads(
        (REPO_ROOT / "plugins" / "autorun" / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )["project"]["version"]

    for note in sorted((REPO_ROOT / "docs" / "releases").glob("*.md")):
        released = note.stem
        if not re.search(r"(rc|a|b)\d+$", released):
            continue
        # Only fenced commands: prose may name the unpinned form to warn about it.
        unpinned, inside_fence = [], False
        for line in note.read_text(encoding="utf-8").splitlines():
            if line.startswith("```"):
                inside_fence = not inside_fence
                continue
            if (
                inside_fence
                and "uv tool install" in line
                and not line.lstrip().startswith("#")
                and released not in line
            ):
                unpinned.append(line.strip())
        assert not unpinned, (
            f"docs/releases/{note.name} installs without the {released} pin, so "
            f"the reader gets a different version than the note describes: {unpinned}"
        )
        if released == version:
            assert "--version" in note.read_text(encoding="utf-8"), (
                f"docs/releases/{note.name} never tells the reader how to check "
                f"which version answered"
            )


def test_the_pdf_plugin_guides_name_every_harness_flag_the_cli_accepts():
    """A per-plugin guide that lists *some* target flags reads as the whole set.

    Both PDF guides listed `--claude`, `--gemini`, `--qwen`, `--antigravity`
    and `--codex` and stopped, so a Pi or Prime Agent user reading the guide for
    the plugin they want concluded their harness was unsupported — while
    `autorun --help` had accepted `--pi` and `--prime` since the Pi family
    landed. Deriving the expected set from the parser is what keeps this true
    the next time a harness is added, rather than true on the day it was
    written.
    """
    from autorun.__main__ import create_parser

    selection = {
        option
        for action in create_parser()._actions
        for option in action.option_strings
        if isinstance(action.help, str) and action.help.startswith("Install for")
    }
    assert {"--pi", "--prime", "--claude"} <= selection, selection

    for guide in ("README.md", "CLAUDE.md"):
        text = (REPO_ROOT / "plugins" / "pdf-extractor" / guide).read_text(
            encoding="utf-8"
        )
        missing = sorted(flag for flag in selection if flag not in text)
        assert not missing, f"plugins/pdf-extractor/{guide} omits {missing}"


#: Every file whose `uv run` commands validate a release candidate, and must
#: therefore run against the graph `uv.lock` commits rather than a fresh
#: resolution. The workflow and the runbook are the same contract asked of a
#: machine and of a person; scoping the rule to one of them is how the runbook
#: kept telling a maintainer to re-resolve while CI was locked down.
_LOCKED_UV_SURFACES = (
    Path(".github") / "workflows" / "ci.yml",
    Path("RELEASING.md"),
)


def _unlocked_uv_runs(text: str) -> list[str]:
    """Lines invoking a project environment without pinning it to the lock.

    `--no-project` has no environment to pin, so it is not in scope: the
    publish workflow uses it to read a TOML file with the interpreter alone.
    """
    return [
        line.strip()
        for line in text.splitlines()
        if "uv run" in line
        and not line.lstrip().startswith("#")
        and "--no-project" not in line
        and "--locked" not in line
        and "--frozen" not in line
    ]


@pytest.mark.parametrize("relative", _LOCKED_UV_SURFACES, ids=lambda p: p.name)
def test_every_release_gate_environment_is_the_locked_one(relative):
    """A `uv run` without `--locked` may re-resolve, and nothing would notice.

    The matrix opens with `uv sync --locked`, which is the whole point of
    committing `uv.lock`: the release is tested against the graph it ships. A
    later `uv run` in the same job that omits the flag is allowed to resolve
    something else the moment metadata and lock diverge, so a stale or
    release-platform-incompatible lock passes CI green. The step exists to test
    the PDF backends, and the backends are exactly where the graph is widest.

    The release checklist is the same gate run by hand, so it is held to the
    same rule. Locking only the workflow left the maintainer's pre-flight —
    the full suite, the artifact build, the benchmark, the post-install cache
    check — free to resolve a graph the candidate does not ship, while the
    checklist reads as the authoritative validation of that candidate.
    """
    text = (REPO_ROOT / relative).read_text(encoding="utf-8")

    unlocked = _unlocked_uv_runs(text)

    assert not unlocked, f"{relative} resolves outside the committed lock: {unlocked}"


def test_quick_method_runs_tests_against_the_committed_lock():
    """The first copy/paste test command is part of the release gate too."""
    checklist = (REPO_ROOT / "RELEASING.md").read_text(
        encoding="utf-8"
    )
    quick_method = checklist.split("## Quick Method", 1)[1].split(
        "## Additional Search Patterns", 1
    )[0]

    assert "uv run --project plugins/autorun --locked pytest" in quick_method


def test_the_dev_environment_has_one_definition():
    """`uv sync --dev` and `autorun[dev]` must not be two different rooms.

    Both were declared, and they disagreed: the published extra floored pytest
    at 7 and carried black and mypy, while the dependency group floored it at
    8.4.2 and carried pytest-timeout and pytest-xdist. Anything invoked with
    `--extra dev` therefore ran without the timeout plugin that `pyproject.toml`
    configures a global `timeout = 30` for, and without the xdist the suite's
    `--dist=loadgroup` addopts names. The group is what CI syncs and what the
    tooling settings are written against, so it is the definition; a second
    public one can only drift.
    """
    pyproject = tomllib.loads(
        (REPO_ROOT / "plugins" / "autorun" / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )

    assert "dev" not in pyproject["project"].get("optional-dependencies", {}), (
        "a public `dev` extra is a second definition of the dev environment"
    )
    group = " ".join(pyproject["dependency-groups"]["dev"])
    assert "pytest-timeout" in group and "pytest-xdist" in group, group


#: `pip install -e .[dev]`, `uv sync --extra pdf`, `'autorun[pdf,pdf-gpu]'` —
#: every spelling a contributor entrypoint uses to name an extra.
_EXTRA_REQUEST = re.compile(r"(?:\.|autorun|\])\[([A-Za-z0-9_.,\- ]+)\]")


def test_no_contributor_entrypoint_installs_an_extra_that_was_removed():
    """An undeclared extra is a warning, not an error, so the target looks fine.

    `Makefile:install-deps` ran `pip install -e .[dev]` for as long as the
    `dev` extra existed. Deleting the extra in favour of the single
    `[dependency-groups].dev` definition did not break that command loudly:
    pip prints `WARNING: autorun <version> does not provide the extra 'dev'`,
    installs the runtime dependencies, and exits 0. So `make install-deps`
    reports success while installing no pytest, no ruff and no xdist, and the
    failure surfaces two targets later as `make ci` reaching `ruff: command not
    found`.

    Checking the *name* rather than the exit code is what catches it: the
    entrypoints and `pyproject.toml` have to agree about which extras exist,
    and only one of the two is authoritative.
    """
    pyproject = tomllib.loads(
        (REPO_ROOT / "plugins" / "autorun" / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    declared = set(pyproject["project"].get("optional-dependencies", {}))

    entrypoints = [
        REPO_ROOT / "plugins" / "autorun" / "Makefile",
        REPO_ROOT / ".github" / "workflows" / "ci.yml",
        REPO_ROOT / "RELEASING.md",
    ]
    undeclared: list[str] = []
    for path in entrypoints:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#") or "install" not in line:
                continue
            for match in _EXTRA_REQUEST.findall(line):
                for extra in match.replace(" ", "").split(","):
                    if extra and extra not in declared:
                        undeclared.append(f"{path.name}: [{extra}] in {line.strip()}")

    assert not undeclared, (
        f"declared extras are {sorted(declared)}; these ask for others: {undeclared}"
    )


def test_pdf_extractor_installs_from_wheels_on_python_314():
    """The optional CPU graph must retain Python 3.14 wheel coverage.

    markitdown pins magika below a release whose onnxruntime dependency has a
    cp314 artifact, so that backend remains gated below 3.14. Pillow is an
    explicit optional CPU constraint at the first release that both fixes the
    known advisories and publishes cp314 wheels.
    """
    pyproject = (
        REPO_ROOT / "plugins" / "autorun" / "pyproject.toml"
    ).read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    lock = (REPO_ROOT / "uv.lock").read_text(encoding="utf-8")

    assert '"markitdown>=0.1.0; python_version < \'3.14\'"' in pyproject
    assert '"pillow>=12.3.0"' in pyproject
    assert '"marker-pdf' not in pyproject
    assert '"Programming Language :: Python :: 3.14"' in pyproject
    assert "matrix.python-version != '3.14'" not in workflow

    # The declarations above state the intent; this is the lockfile effect.
    assert "pillow-10.4.0" not in lock, "the advisory-affected Pillow remains locked"
    assert re.search(r"pillow-\d+\.\d+\.\d+-cp314-", lock), (
        "uv.lock resolves no pillow wheel for cp314, so a 3.14 install builds "
        "the sdist and fails without system jpeg headers"
    )


def test_ci_actions_are_pinned_to_full_commits():
    mutable = []
    workflows = REPO_ROOT / ".github" / "workflows"
    for path in workflows.glob("*.yml"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "uses:" not in line or "@" not in line:
                continue
            ref = line.split("@", 1)[1].split("#", 1)[0].strip()
            if not re.fullmatch(r"[0-9a-f]{40}", ref):
                mutable.append(f"{path.relative_to(REPO_ROOT)}:{number}: {ref}")
    assert not mutable, "CI actions use mutable refs:\n  " + "\n  ".join(mutable)


def test_setup_bun_pins_the_runtime_not_only_the_action():
    """Pinning the action is weaker than pinning the toolchain it installs.

    ``test_ci_actions_are_pinned_to_full_commits`` above proves every ``uses:``
    is a 40-hex commit, and that assertion passed on the day Bun 1.4.2 shipped
    and segfaulted the Windows job -- because setup-bun carried no
    ``bun-version`` and installed whatever was latest. The action was pinned;
    the runtime was not, so a third-party release turned a release gate red
    with no change to autorun. Every uv command in this repo passes
    ``--locked`` for the same reason.
    """
    workflows = REPO_ROOT / ".github" / "workflows"
    unpinned, versions = [], set()
    for path in sorted(workflows.glob("*.yml")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, 1):
            if "uses:" not in line or "oven-sh/setup-bun" not in line:
                continue
            # The pin belongs to this step, so stop at the next "- name:".
            pin = None
            for follower in lines[number : number + 15]:
                if re.match(r"\s*-\s+name:", follower):
                    break
                found = re.search(r'bun-version:\s*"?([^"\s]+)"?', follower)
                if found:
                    pin = found.group(1)
                    break
            if pin is None:
                unpinned.append(f"{path.relative_to(REPO_ROOT)}:{number}")
            else:
                versions.add(pin)

    assert versions, "no setup-bun step found; this gate is watching nothing"
    assert not unpinned, (
        "setup-bun installs the latest Bun unless bun-version is set, so any "
        "Bun release can fail CI on unchanged code:\n  " + "\n  ".join(unpinned)
    )
    assert len(versions) == 1, (
        f"setup-bun steps disagree on the Bun version: {sorted(versions)}; "
        "the matrix job and the coverage job must run the same runtime"
    )


def test_each_autorun_spelling_keeps_its_own_job():
    """Five things are spelled "autorun"; exactly one of them is "autorun-ai".

    There are exactly THREE names to know, and one derived spelling:

    | thing                | spelling     | where it lives                     |
    |----------------------|--------------|------------------------------------|
    | PyPI distribution    | `autorun-ai` | pyproject `name`, install commands |
    | import package       | `autorun`    | `src/autorun/`, `import autorun`   |
    | console script       | `autorun`    | `[project.scripts]`                |
    | marketplace          | `autorun`    | `.claude-plugin/marketplace.json`  |
    | plugin id / prefix   | `ar`         | plugin.json, `/ar:<cmd>`, commands/|
    | wheel filename stem  | *derived*    | `DISTRIBUTION.replace("-", "_")`   |

    The wheel stem is deliberately absent from that list of names: nothing
    declares it, the build backend emits it because PEP 427 normalises "-" to
    "_". It was briefly written out as a literal in four places, which made a
    fourth spelling to keep in sync for no gain; it is now computed from the
    distribution, and this test fails if a literal comes back.

    `ar` is short on purpose, so the prefix types quickly. It is namespaced by
    the marketplace, never by PyPI -- `ar` on PyPI belongs to an unrelated
    ar-archive reader, and autorun neither needs nor could have it.

    Only the distribution moved. PyPI prohibits the bare name `autorun`
    ("This project name isn't allowed"), which is why it carries a suffix at
    all -- there is no other reason, and nothing else should follow it.

    The confusion this excludes runs both ways: renaming the console script or
    the import package to match the distribution (breaking every user's muscle
    memory, every `hooks.json` command and every `import autorun`), or leaving a
    packaging field spelling the distribution `autorun` (unregistrable, and for
    `[tool.uv.sources]` it silently resolves the workspace member from PyPI
    instead of the local tree).
    """
    # No local `import tomllib`: this module already binds it at the top with a
    # `tomli` fallback, because tomllib is not stdlib before 3.11. A function
    # scope import re-runs the real import and raises ModuleNotFoundError on
    # 3.10 regardless of that fallback, which is how this failed one CI job
    # while every other Python passed.
    plugin = tomllib.loads(
        (PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    workspace = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert plugin["project"]["name"] == "autorun-ai", "the distribution carries the suffix"
    assert "autorun" in plugin["project"]["scripts"], (
        "the console script must stay `autorun`; renaming it to match the "
        "distribution breaks every documented command and every hooks.json entry"
    )
    assert (PLUGIN_ROOT / "src" / "autorun" / "__init__.py").is_file(), (
        "the import package must stay `autorun`; `import autorun` is public API"
    )
    assert set(workspace["tool"]["uv"]["sources"]) == {"autorun-ai"}, (
        "[tool.uv.sources] keys are distribution names, not directory names"
    )

    # Self-referential and workspace requirements name the distribution, so a
    # missed one makes `uv lock` report unsatisfiable requirements.
    for label, requirements in (
        ("plugin pdf-all extra", plugin["project"]["optional-dependencies"]["pdf-all"]),
        ("workspace all extra", workspace["project"]["optional-dependencies"]["all"]),
    ):
        for requirement in requirements:
            assert not requirement.startswith("autorun["), (
                f"{label} requires the prohibited bare name: {requirement!r}"
            )

    # -- `ar`: the plugin id, which is also the `/ar:` prefix ----------------
    # Renaming the distribution must not drag the prefix along. Every `/ar:<cmd>`
    # in a user's muscle memory, every hooks.json command, and every installed
    # tree at `<config>/plugins/cache/autorun/ar/<version>/` is keyed on it, and
    # the installer's own note records what a wrong marker costs: "A tree
    # recorded as `autorun` was unremovable under `ar`, which leaked 362 files."
    manifest = json.loads(
        (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    market = json.loads(
        (PLUGIN_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )
    assert manifest["name"] == "ar", "the plugin id is the `/ar:` prefix"
    assert market["name"] == "autorun", "the marketplace keeps the bare name"
    assert [entry["name"] for entry in market["plugins"]] == ["ar"], (
        "the marketplace publishes exactly the `ar` plugin"
    )

    # -- the wheel stem is derived, never spelled ----------------------------
    # `test_release_artifacts` cannot parse pyproject at module scope, because
    # `tomllib` is not stdlib before 3.11 and CI runs 3.10 -- so it carries the
    # distribution as a constant, and this is where the two are held together.
    release_artifacts = PLUGIN_ROOT / "tests" / "test_release_artifacts.py"
    source = release_artifacts.read_text(encoding="utf-8")
    declared = plugin["project"]["name"]
    assert f'DISTRIBUTION = "{declared}"' in source, (
        f"test_release_artifacts.DISTRIBUTION must be {declared!r}"
    )
    stem = declared.replace("-", "_")
    for path in (release_artifacts, REPO_ROOT / ".github" / "workflows" / "publish.yml"):
        body = path.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("#")
        )
        assert stem not in code, (
            f"{path.name} spells the wheel stem {stem!r} instead of deriving it from "
            "the distribution; that is a fourth name to keep in sync"
        )


def test_no_module_looks_up_a_distribution_by_a_literal_name():
    """``importlib.metadata`` is the only API here that takes a *distribution*.

    Stated as the violation: a call such as ``version("autorun")`` anywhere in
    shipped source. That is the shape the rename produced twice, and both times
    it failed silently rather than loudly -- ``version("autorun")`` raises
    ``PackageNotFoundError``, whose handler degrades to the string "unknown",
    which compares as older than every release tag, so self-update offers an
    upgrade forever and no message says why.

    The fix is not "spell it autorun-ai here too": a literal is a second source
    of truth wherever it appears. `_PLUGIN_DISTRIBUTIONS` is the one place that
    knows the distribution's name, so every lookup must read it. That keeps the
    next rename a one-line change instead of a search.
    """
    import ast

    lookups = {"version", "distribution", "metadata"}
    roots = (
        PLUGIN_ROOT / "src",
        PLUGIN_ROOT / "hooks",
        REPO_ROOT / "plugins" / "pdf-extractor" / "src",
    )
    offenders, call_sites = [], []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if path.is_symlink():
                continue  # pdf_extraction is symlinked into both trees
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            # Only names actually imported from importlib.metadata count. A
            # bare `version(...)` is an ordinary helper in several modules, and
            # `PackageNotFoundError("...")` is a literal string by design.
            aliases = {
                alias.asname or alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module == "importlib.metadata"
                for alias in node.names
                if alias.name in lookups
            }
            dotted = {f"importlib.metadata.{name}" for name in lookups}
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                rendered = ast.unparse(node.func)
                if rendered not in aliases and rendered not in dotted:
                    continue
                where = f"{path.relative_to(REPO_ROOT)}:{node.lineno}"
                call_sites.append(where)
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    offenders.append(f"{where} {rendered}({first.value!r})")

    # Without this the check passes by finding nothing -- which is exactly what
    # it would do if the roots, the import form, or `ast.unparse` ever stopped
    # matching. "No offenders" must mean "looked and found none".
    assert call_sites, (
        f"no importlib.metadata call sites found under {[str(r) for r in roots]}; "
        "the scan is broken, not the source"
    )
    assert not offenders, (
        "distribution looked up by a literal name instead of "
        "_PLUGIN_DISTRIBUTIONS['ar'][0]:\n  " + "\n  ".join(offenders)
    )


def test_documented_install_commands_name_the_registrable_distribution():
    """No shipped doc may tell a user to install the bare name `autorun`.

    Stated as the violation: an `install` command whose target is `autorun`,
    `autorun[...]` or `autorun==...`. PyPI refuses that name, so such a command
    cannot succeed -- it is not a style preference but a broken instruction.

    Paths are not targets: `uv tool install --editable plugins/autorun` and the
    `git+https://github.com/ahundt/autorun.git#subdirectory=plugins/autorun`
    form both name locations and stay as they are.
    """
    # Parse the install *target*, not any `autorun` on the line. The naive
    # regex flagged `uv tool install --force autorun-ai && autorun --install`,
    # whose second `autorun` is the console script being run, not a target.
    value_flags = {"--index-url", "--extra-index-url", "--python", "--with", "--from"}
    verb = re.compile(r"\b(?:uv tool install|pip3? install|python -m pip install)\b(.*)")

    def targets(line):
        """The first positional argument of each install command on ``line``."""
        for segment in re.split(r"&&|\|\||;", line):
            found = verb.search(segment)
            if not found:
                continue
            skip = False
            for token in found.group(1).replace("`", " ").split():
                if skip:
                    skip = False
                elif token in value_flags:
                    skip = True
                elif not token.startswith("-"):
                    yield token.strip("'\"")
                    break

    def distribution(target):
        """The distribution a target names, or None when it names a location."""
        if "/" in target or target.startswith((".", "git+", "$")):
            return None
        return re.split(r"[\[=<>~!]", target, maxsplit=1)[0]

    # Release notes are globbed rather than named: spelling a version here would
    # put a literal this file must bump every release, which
    # test_release_checklist_covers_every_file_carrying_the_version rejects, and
    # would silently stop covering the next release's notes.
    offenders = []
    for path in (
        REPO_ROOT / "README.md",
        REPO_ROOT / "AGENTS.md",
        REPO_ROOT / "CHANGELOG.md",
        REPO_ROOT / "RELEASING.md",
        PLUGIN_ROOT / "README.md",
        REPO_ROOT / "plugins" / "pdf-extractor" / "README.md",
        REPO_ROOT / "plugins" / "pdf-extractor" / "CLAUDE.md",
        *sorted((REPO_ROOT / "docs" / "releases").glob("*.md")),
    ):
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for target in targets(line):
                if distribution(target) == "autorun":
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{number}: installs {target!r}"
                    )
    assert not offenders, (
        "these install commands name the prohibited bare distribution "
        "`autorun` instead of `autorun-ai`:\n  " + "\n  ".join(offenders)
    )


def test_publishing_jobs_state_their_own_precondition():
    """A job that reaches a package index must not inherit its gate from `needs`.

    GitHub skips a job when *any* transitive ancestor was skipped, not only a
    direct dependency. `publish.yml` skips `test` on a `workflow_dispatch` run
    and lets `build` through with `always()`; `publish-testpypi` carried only
    `needs: build`, so it was skipped along with `test` -- and the run still
    reported `success`. The TestPyPI rehearsal uploaded nothing and proved
    nothing, which matters because that rehearsal is the only thing standing
    between a misconfigured OIDC setup and a permanent, unrepeatable write to
    the real index.

    Stated as the violation it excludes: a job declaring an `environment` (the
    publishing jobs, and only those) whose `if` does not name the job it needs.
    A green run that published nothing is the failure this catches, and it is
    indistinguishable from a working one without this check.
    """
    import yaml

    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    )
    inherited = []
    for name, job in workflow["jobs"].items():
        if "environment" not in job:
            continue
        condition = str(job.get("if", ""))
        needs = job.get("needs") or []
        for need in [needs] if isinstance(needs, str) else needs:
            if need not in condition:
                inherited.append(
                    f"{name}: needs {need!r} but its `if` ({condition or 'absent'}) "
                    f"never names it, so a skipped ancestor skips it silently"
                )
    assert not inherited, (
        "a publishing job can be skipped by a skipped ancestor while the run "
        "still reports success:\n  " + "\n  ".join(inherited)
    )


def test_the_testpypi_rehearsal_installs_the_wheel_it_uploaded():
    """The rehearsal must prove the wheel installs, not that bytes were accepted.

    Stated as the violation: a `publish-testpypi` job whose only action is an
    upload. TestPyPI accepting a file says nothing about whether the wheel
    resolves, whether its console scripts exist, or whether it imports -- and
    the very next job writes to PyPI, where a version is immutable, so a wheel
    problem found afterwards costs a new version rather than a re-upload.

    Both halves are asserted because either alone is satisfiable while the
    release still publishes unrehearsed bytes: an install step that never runs
    the CLI proves resolution only, and a CLI invocation that resolves from
    PyPI would pass even if nothing had been uploaded.

    Learned from ai-session-search, whose rehearsal had the same shape.
    """
    import yaml

    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["publish-testpypi"]["steps"]
    scripts = "\n".join(str(step.get("run", "")) for step in steps)

    assert "test.pypi.org/simple" in scripts, (
        "publish-testpypi never installs from the index it just uploaded to, so "
        "a green rehearsal proves only that TestPyPI accepted the bytes"
    )
    assert re.search(r"\bautorun --version\b", scripts), (
        "publish-testpypi installs the wheel but never runs the console script, "
        "so a distribution that resolves and cannot execute still reaches PyPI"
    )
    # The publish action itself is not a `run:` step, so its absence here would
    # mean the upload was removed and only the verification remains.
    assert any(
        "pypi-publish" in str(step.get("uses", "")) for step in steps
    ), "publish-testpypi no longer uploads anything"
