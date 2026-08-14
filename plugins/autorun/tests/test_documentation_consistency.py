"""Keep maintained user documentation aligned with installed interfaces."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

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


def test_current_changelog_covers_pi_and_published_distributions():
    """The current release entry must describe capability and distribution surfaces."""
    version = _declared_version("plugins/autorun/pyproject.toml", "version")
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    section = changelog.split(f"## [{version}]", 1)[1].split("\n## [", 1)[0]

    for required in ("Pi", "PyPI", "`autorun`", "`pdf-extractor`"):
        assert required in section, f"CHANGELOG.md [{version}] omits {required}"


def test_published_distributions_have_project_urls():
    """Package-index users need source, issue, and homepage links in metadata."""
    for relative_path in (
        "plugins/autorun/pyproject.toml",
        "plugins/pdf-extractor/pyproject.toml",
    ):
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
    assert "uv tool install 'pdf-extractor[cpu]'" in pdf_readme


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
    ("plugins/pdf-extractor/pyproject.toml", "version"),
    ("plugins/pdf-extractor/.claude-plugin/plugin.json", "version"),
    ("plugins/pdf-extractor/src/pdf_extraction/__init__.py", "__version__"),
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


def test_workspace_sources_match_the_declared_distribution_names():
    """A `[tool.uv.sources]` key that does not name a member is silently wrong.

    uv matches these keys against distribution names, not directory names. A
    mismatch does not error — uv just resolves the member from PyPI instead of
    the local tree, so a developer edits one copy and tests another.
    """
    names = {
        "plugins/autorun/pyproject.toml": "autorun",
        "plugins/pdf-extractor/pyproject.toml": "pdf-extractor",
    }
    for relative_path, expected in names.items():
        declared = tomllib.loads(
            (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        )["project"]["name"]
        assert declared == expected, f"{relative_path} declares '{declared}'"

    plugin = json.loads(
        (
            REPO_ROOT / "plugins" / "pdf-extractor" / ".claude-plugin" / "plugin.json"
        ).read_text(encoding="utf-8")
    )
    assert plugin["name"] == "pdf-extractor", (
        "the harness plugin id is a different namespace from the PyPI name and "
        "renaming it would break `claude plugin install pdf-extractor@autorun`"
    )

    workspace = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert set(workspace["tool"]["uv"]["sources"]) == set(names.values()), (
        "[tool.uv.sources] keys must match the member distribution names or uv "
        "resolves the workspace member from PyPI instead of the local tree"
    )


def test_pdf_extractor_extras_avoid_retired_or_known_vulnerable_backends():
    """Published extras must not select abandoned or unpatched dependencies."""
    pyproject = tomllib.loads(
        (REPO_ROOT / "plugins" / "pdf-extractor" / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    extras = pyproject["project"]["optional-dependencies"]
    cpu = "\n".join(extras["cpu"]).lower()
    gpu = "\n".join(extras["gpu"]).lower()

    assert "pypdf>=" in cpu
    assert "pypdf2" not in cpu
    assert "docling>=" in gpu
    assert "sys_platform != 'darwin'" in gpu
    assert "marker-pdf" not in gpu


def test_pdf_extractor_requires_no_extraction_backend():
    """Installing the package must not force any extraction library on anyone.

    Every backend imports inside its own ``extract()`` call, so a required
    dependency here would buy nothing and cost every user the download. The CI
    job that runs this plugin's tests therefore has to name ``--extra cpu``, or
    it would exercise a package with no backend installed and still pass.
    """
    pyproject = tomllib.loads(
        (REPO_ROOT / "plugins" / "pdf-extractor" / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert pyproject["project"]["dependencies"] == [], (
        "pdf-extractor declares a required dependency; every backend belongs in "
        "[project.optional-dependencies]"
    )
    extras = pyproject["project"]["optional-dependencies"]
    assert {"cpu", "gpu", "llm", "progress", "all"} <= set(extras)
    assert "--extra cpu" in workflow, (
        "CI runs the pdf-extractor suite without the cpu extra, so the backend "
        "tests would pass against a package that has no backend installed"
    )


def test_pdf_extractor_installs_from_wheels_on_python_314():
    """The optional CPU graph must retain Python 3.14 wheel coverage.

    markitdown pins magika below a release whose onnxruntime dependency has a
    cp314 artifact, so that backend remains gated below 3.14. Pillow is an
    explicit optional CPU constraint at the first release that both fixes the
    known advisories and publishes cp314 wheels.
    """
    pyproject = (
        REPO_ROOT / "plugins" / "pdf-extractor" / "pyproject.toml"
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
