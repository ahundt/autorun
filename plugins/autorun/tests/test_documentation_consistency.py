"""Keep maintained user documentation aligned with installed interfaces."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 CI only
    import tomli as tomllib


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


_UV_PROJECT_RE = re.compile(r"uv run --project (\S+)")
_CD_RE = re.compile(r"\(cd (\S+) &&")


def test_every_checklist_uv_command_names_a_real_project():
    """A runbook command has to *work*, not merely name files that exist.

    `(cd plugins/pdf-extractor && uv run --project . ...)` read fine and passed
    every prose check, but that directory deliberately has no `pyproject.toml`:
    UV resolved the workspace root instead, the autorun distribution was never
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
        "docs/version_update_checklist.md runs `uv run --project` against a "
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
        "docs/version_update_checklist.md places TestPyPI setup or rehearsal "
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
    release_heading = re.search(
        rf"^## \[{re.escape(version)}\] - (\d{{4}}-\d{{2}}-\d{{2}})$",
        changelog,
        re.MULTILINE,
    )

    assert release_heading
    assert f"Date: {release_heading.group(1)}" in notes
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

    assert "autorun[pdf]" in readme, (
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

    for required in ("Pi", "PyPI", "`autorun`", "`autorun[pdf]`"):
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
        assert "uv tool install autorun" in text

    pdf_readme = (
        REPO_ROOT / "plugins" / "pdf-extractor" / "README.md"
    ).read_text(encoding="utf-8")
    assert "uv tool install 'autorun[pdf]'" in pdf_readme


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
# purpose -- see Gotcha 5 in docs/version_update_checklist.md. The root
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
    # inside the autorun distribution as `pdf_extraction`.
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
    assert autorun["name"] == "autorun"
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
    assert set(workspace["tool"]["uv"]["sources"]) == {"autorun"}, (
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
    Path("docs") / "version_update_checklist.md",
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
        REPO_ROOT / "docs" / "version_update_checklist.md",
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
