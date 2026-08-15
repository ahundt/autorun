"""The step table, and the walk it drives, end to end against a sandboxed home.

Every other install test exercises one module. These exercise the thing a user
actually runs: resolve the plugins, stage what has to be generated, walk every
registered harness, and check the three properties that make an installer
trustworthy — a preview writes nothing, a second install changes nothing, and an
uninstall leaves nothing behind.

`HOME` is redirected with monkeypatch.setenv, and harness-specific relocation
environment variables are cleared so these tests exercise default-home routes.
Nothing here may touch the developer's own configuration.
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("AUTORUN_HOME", "/tmp/autorun-test-home")
os.environ.setdefault("AUTORUN_TEST_STATE_DIR", "/tmp/autorun-test-state")

from autorun.installer import discovery, steps  # noqa: E402
from autorun.installer.traversal import Context, Mode, run, targets  # noqa: E402
from autorun.platforms import PLATFORMS  # noqa: E402

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A home nothing outside this test can see."""
    home = tmp_path / "home"
    home.mkdir()
    # Both names: Path.home() resolves through os.path.expanduser, which reads
    # USERPROFILE on Windows and HOME elsewhere and never consults the other,
    # so setting one isolates this test on one platform and lets it write the
    # real home on the other.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    # OpenCode honours XDG_CONFIG_HOME as an explicit parent override.  A CI
    # runner may export it globally, so clear it here or the OpenCode command
    # and plugin intents intentionally land outside this sandbox.
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("PI_CODING_AGENT_DIR", raising=False)
    return home


@pytest.fixture
def walk(sandbox):
    """The paired harnesses and a staged Context, as an install builds them."""
    dirs, missing = discovery.resolve_plugins(REPO, ["ar"])
    assert not missing, missing
    ctx = Context(
        marketplace_root=REPO,
        plugin_dirs=dirs,
        home=sandbox,
        settings={"skill_placement": {"": "auto"}, "shared_skills_bridge": "none"},
    )
    named = {discovery.plugin_name(d): d for d in dirs}
    with steps.prepared(ctx, plugins=named) as staged:
        yield targets(PLATFORMS.values(), steps.STEPS), staged


def owned(home: Path) -> list[Path]:
    return sorted(p for p in home.rglob("*") if p.is_dir() and (p / ".autorun-owned").is_file())


# ─── The table is the only place a harness is named ──────────────────────────


def test_every_registered_harness_has_steps():
    """A harness missing from the table installs nothing and reports success,
    which is the failure mode this table exists to make impossible."""
    assert set(steps.STEPS) == set(PLATFORMS)


def test_the_table_names_no_harness_that_does_not_exist():
    assert not set(steps.STEPS) - set(PLATFORMS)


def test_pairing_is_data_not_a_branch():
    """Steps come from the table, so an unsupported harness stays out of the
    walk without any code testing for it."""
    assert all(t.install_steps == () for t in targets(PLATFORMS.values(), {}))
    assert all(t.install_steps for t in targets(PLATFORMS.values(), steps.STEPS))


# ─── The three properties that make an installer trustworthy ─────────────────


def test_a_preview_writes_nothing(walk, sandbox):
    """Status and dry run are the same code path, and neither may touch disk."""
    paired, ctx = walk
    before = sorted(str(p.relative_to(sandbox)) for p in sandbox.rglob("*"))

    decisions = run(paired, ctx, Mode.PREVIEW)

    after = sorted(str(p.relative_to(sandbox)) for p in sandbox.rglob("*"))
    assert decisions, "a preview that decides nothing proves nothing"
    assert after == before


def test_an_install_publishes_and_a_second_one_changes_nothing(walk, sandbox):
    """Re-running must converge. An install that republishes every run rewrites
    files a harness watches and invalidates its caches for no reason."""
    paired, ctx = walk

    first = Counter(d.verdict.value for d in run(paired, ctx, Mode.INSTALL))
    assert first["publish"], first
    assert owned(sandbox), "nothing landed"

    second = Counter(d.verdict.value for d in run(paired, ctx, Mode.INSTALL))
    assert set(second) == {"skip"}, second


def test_an_uninstall_leaves_nothing_of_ours_behind(walk, sandbox):
    """The property the old installer failed: a Codex tree recorded under a
    second name survived every uninstall."""
    paired, ctx = walk
    run(paired, ctx, Mode.INSTALL)
    assert owned(sandbox)

    run(paired, ctx, Mode.UNINSTALL)

    assert owned(sandbox) == []


def test_a_user_edit_inside_an_installed_skill_is_kept(walk, sandbox):
    """The capability the ownership marker could not express before: a marker
    states a fact about a directory, and cannot record what it contained."""
    paired, ctx = walk
    run(paired, ctx, Mode.INSTALL)
    edited = next(p for p in owned(sandbox) if (p / "SKILL.md").is_file())
    (edited / "SKILL.md").write_text("MY OWN EDIT\n", encoding="utf-8")

    decisions = run(paired, ctx, Mode.INSTALL)

    kept = [d for d in decisions if d.target == edited]
    assert kept and kept[0].verdict.value == "keep", kept
    assert (edited / "SKILL.md").read_text(encoding="utf-8") == "MY OWN EDIT\n"


def test_nothing_lands_outside_the_sandboxed_home(walk, sandbox):
    """HOME is the seam. A route resolving through anything else would write to
    the developer's own configuration from a test that redirected it."""
    paired, ctx = walk

    decisions = run(paired, ctx, Mode.INSTALL)

    outside = [d.target for d in decisions if not str(d.target).startswith(str(sandbox))]
    assert outside == [], outside


# ─── The two phases a walk cannot express ────────────────────────────────────


def test_a_region_preview_writes_nothing_then_install_and_uninstall_are_exact(tmp_path):
    """A memory file belongs to the user; autorun owns one range inside it."""
    from autorun.installer.memory import Block

    target = tmp_path / "AGENTS.md"
    region = steps.Region(target, Block("guidance"), "autorun guidance")

    assert steps.apply_regions([region], Mode.PREVIEW)
    assert not target.exists(), "a preview created the file"

    theirs = "# Their notes\n\nkeep this paragraph.\n"
    target.write_text(theirs, encoding="utf-8")
    steps.apply_regions([region], Mode.INSTALL)
    assert "autorun guidance" in target.read_text(encoding="utf-8")
    assert "keep this paragraph." in target.read_text(encoding="utf-8")

    steps.apply_regions([region], Mode.UNINSTALL)
    assert target.read_text(encoding="utf-8") == theirs


def test_a_hooks_merge_keeps_the_users_own_entries(tmp_path):
    import json

    hooks = tmp_path / "hooks.json"
    theirs = {"hooks": [{"type": "command", "command": "/usr/local/bin/mine.sh"}]}
    hooks.write_text(json.dumps({"hooks": {"Stop": [theirs]}}), encoding="utf-8")
    entry = steps.Hooks(hooks, {"Stop": ("uv run hook_entry.py --cli codex",)})

    steps.apply_hooks([entry], Mode.PREVIEW)
    assert json.loads(hooks.read_text())["hooks"]["Stop"] == [theirs], "preview wrote"

    steps.apply_hooks([entry], Mode.INSTALL)
    after = json.loads(hooks.read_text())["hooks"]["Stop"]
    assert theirs in after and len(after) == 2

    steps.apply_hooks([entry], Mode.UNINSTALL)
    assert json.loads(hooks.read_text())["hooks"]["Stop"] == [theirs]


def test_an_eighth_harness_needs_no_module_edited(sandbox):
    """The claim the whole design rests on, tested rather than asserted.

    Adding a harness to the installer this replaces meant a new `_install_for_*`
    function, a dispatch branch, a summary branch, an uninstall branch and a
    status branch. Here it is a registry entry cloned from a flavor and one row
    in the step table: no module changes, and the walk never learns it exists.
    """
    from dataclasses import replace

    newcomer = replace(
        PLATFORMS["codex"], name="acme", display_name="Acme Code",
        binary="acme", config_dir="~/.acme", config_dir_env_vars=(),
    )
    table = {**steps.STEPS, "acme": steps.STEPS["codex"]}

    dirs, _ = discovery.resolve_plugins(REPO, ["ar"])
    ctx = Context(
        marketplace_root=REPO, plugin_dirs=dirs, home=sandbox,
        settings={"skill_placement": {"": "auto"}},
    )
    named = {discovery.plugin_name(d): d for d in dirs}
    with steps.prepared(ctx, plugins=named) as staged:
        landed = run(targets([newcomer], table), staged, Mode.INSTALL)
        assert any(d.verdict.value == "publish" for d in landed)
        assert (sandbox / ".acme").is_dir(), "the new harness's own config dir"

        gone = run(targets([newcomer], table), staged, Mode.UNINSTALL)

    assert any(d.verdict.value == "retire" for d in gone)
    assert owned(sandbox) == [], "a harness added without code removes without code"


def test_orchestrator_runs_a_custom_harness_through_its_flavor_steps(sandbox):
    from autorun.installer.orchestrate import install
    from autorun.installer.settings import (
        CustomHarness,
        steps_for_custom,
        synthesize,
    )

    custom = CustomHarness("mine", "codex", "mine-cli", "~/.mine", "Mine")
    harness = synthesize(custom)
    table = steps_for_custom(custom, steps.STEPS)

    result = install(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={
            "skill_placement": {"mine": "auto"},
            "codex_hook_source": "user",
            "_codex_hook_command": "uv run hook_entry.py --cli codex",
        },
        home=sandbox,
        harnesses=(harness,),
        step_table=table,
        available=(),
        state_dir=sandbox / ".state",
    )

    assert result.ok is True
    assert (sandbox / ".mine" / "commands" / "ar-go.md").is_file()
    assert (sandbox / ".mine" / "hooks.json").is_file()


def test_custom_extension_harness_registers_through_its_own_binary(sandbox):
    import subprocess

    from autorun.installer import registration
    from autorun.installer.orchestrate import install
    from autorun.installer.settings import CustomHarness, steps_for_custom, synthesize

    custom = CustomHarness("mine", "qwen", "mine-cli", "~/.mine", "Mine")
    harness = synthesize(custom)
    table = steps_for_custom(custom, steps.STEPS)
    entry = registration.with_binary(registration.REGISTRATIONS["qwen"], custom.binary)
    calls = []

    def record(argv):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = install(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={
            "skill_placement": {"mine": "auto"},
            "_registrations": {"mine": entry},
        },
        home=sandbox,
        harnesses=(harness,),
        step_table=table,
        run_command=record,
        available=("mine-cli",),
        state_dir=sandbox / ".state",
    )

    assert result.ok is True
    assert calls and calls[0][:3] == ("mine-cli", "extensions", "install")
    source = Path(calls[0][-2])
    assert source == (
        sandbox / ".autorun" / "installer" / "extension-sources" / "mine" / "ar"
    )
    hooks = (source / "hooks" / "hooks.json").read_text(encoding="utf-8")
    assert "autorun --cli qwen" in hooks
    assert "uv run" not in hooks


def test_gemini_extension_uses_the_installed_cli_without_a_fake_project(walk, sandbox):
    paired, ctx = walk
    gemini = [target for target in paired if target.name == "gemini"]

    run(gemini, ctx, Mode.INSTALL)

    extension = (
        sandbox / ".autorun" / "installer" / "extension-sources" / "gemini" / "ar"
    )
    hooks = (extension / "hooks" / "hooks.json").read_text(encoding="utf-8")
    assert "autorun --cli gemini" in hooks
    assert "uv run" not in hooks
    assert not (extension / "hooks" / "hook_entry.py").exists()


def test_antigravity_native_skills_are_inside_the_staged_plugin(walk, sandbox):
    paired, ctx = walk
    antigravity = [target for target in paired if target.name == "antigravity"]

    run(antigravity, ctx, Mode.INSTALL)

    plugin = (
        sandbox
        / ".autorun"
        / "installer"
        / "extension-sources"
        / "antigravity"
        / "ar"
    )
    assert (plugin / "skills" / "commit" / "SKILL.md").is_file()
    assert not (plugin.parent / "commit").exists(), "a skill must not land beside the plugin"


def test_antigravity_staging_uses_its_native_manifest_and_hook_events(walk, sandbox):
    import json

    paired, ctx = walk
    antigravity = [target for target in paired if target.name == "antigravity"]

    run(antigravity, ctx, Mode.INSTALL)

    plugin = (
        sandbox
        / ".autorun"
        / "installer"
        / "extension-sources"
        / "antigravity"
        / "ar"
    )
    manifest = json.loads((plugin / "plugin.json").read_text(encoding="utf-8"))
    hooks = json.loads((plugin / "hooks.json").read_text(encoding="utf-8"))
    assert manifest["hooks"] == "./hooks.json"
    assert not (plugin / "gemini-extension.json").exists()
    assert set(hooks["autorun"]) == {"PreToolUse", "PostToolUse", "Stop"}
    serialized = (plugin / "hooks.json").read_text(encoding="utf-8")
    for event in ("PreToolUse", "PostToolUse", "Stop"):
        assert f"--cli antigravity --event {event}" in serialized
    for groups in hooks["autorun"].values():
        handlers = [
            handler
            for group in groups
            for handler in (group.get("hooks", []) if "hooks" in group else [group])
        ]
        assert handlers
        assert all(handler["command"].startswith("autorun --cli antigravity") for handler in handlers)
        assert all(handler["timeout"] == 5 for handler in handlers)
        assert all("name" not in handler for handler in handlers)


def test_qwen_staging_rewrites_hook_identity_without_deprecated_toml(walk, sandbox):
    paired, ctx = walk
    qwen = [target for target in paired if target.name == "qwen"]

    run(qwen, ctx, Mode.INSTALL)

    extension = (
        sandbox / ".autorun" / "installer" / "extension-sources" / "qwen" / "ar"
    )
    hooks = (extension / "hooks" / "hooks.json").read_text(encoding="utf-8")
    assert "autorun --cli qwen" in hooks
    assert "uv run" not in hooks
    assert not (extension / "hooks" / "hook_entry.py").exists()
    assert not (extension / "commands" / "ar").exists()


def test_extension_staging_failure_makes_the_install_result_broken(sandbox, monkeypatch):
    from autorun.installer.orchestrate import install

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(steps.extension, "stage_extension", fail)
    result = install(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={"skill_placement": {"": "auto"}},
        home=sandbox,
        harnesses=(PLATFORMS["antigravity"],),
        available=(),
        state_dir=sandbox / ".state",
    )

    assert result.ok is False
    assert any("staging" in finding.check for finding in result.findings)


def test_unsatisfiable_skill_placement_is_broken_before_any_write(sandbox):
    from autorun.installer.orchestrate import install

    result = install(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={"skill_placement": {"forgecode": "native"}},
        home=sandbox,
        harnesses=(PLATFORMS["forgecode"],),
        available=(),
        state_dir=sandbox / ".state",
    )

    assert result.ok is False
    assert result.decisions == ()
    assert any("cannot be satisfied" in finding.detail for finding in result.findings)
    assert not (sandbox / ".forge").exists()


def test_duplicate_skill_names_are_broken_before_any_write(sandbox, tmp_path):
    from autorun.installer.orchestrate import install

    market = tmp_path / "market"
    for plugin in ("one", "two"):
        skill = market / "plugins" / plugin / "skills" / "same"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(plugin, encoding="utf-8")

    result = install(
        marketplace_root=market,
        plugins=("one", "two"),
        settings={"skill_placement": {"": "auto"}},
        home=sandbox,
        harnesses=(PLATFORMS["codex"],),
        available=(),
        state_dir=sandbox / ".state",
    )

    assert result.ok is False
    assert result.decisions == ()
    assert any("one:same" in finding.detail and "two:same" in finding.detail for finding in result.findings)
    assert list(sandbox.iterdir()) == []


def test_default_claude_forge_opencode_install_has_one_visible_copy_per_skill(sandbox):
    """Claude's plugin package already carries its skills. Publishing them a
    second time under ~/.claude/skills makes OpenCode race that copy against
    the shared ~/.agents/skills copy while building its name-keyed catalog."""
    from autorun.installer.orchestrate import install

    result = install(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={"skill_placement": {"": "auto"}},
        home=sandbox,
        harnesses=tuple(
            PLATFORMS[name] for name in ("claude", "forgecode", "opencode")
        ),
        available=(),
        state_dir=sandbox / ".state",
    )

    visible = (
        sandbox / ".claude" / "skills" / "commit" / "SKILL.md",
        sandbox / ".agents" / "skills" / "commit" / "SKILL.md",
    )
    assert result.ok is True
    assert sum(path.is_file() for path in visible) == 1, visible


@pytest.mark.parametrize("edited", [False, True])
def test_upgrade_retires_only_unchanged_legacy_claude_skill_copy(sandbox, edited):
    from autorun.installer.fs import publish_tree
    from autorun.installer.orchestrate import install

    legacy = sandbox / ".claude" / "skills" / "commit"
    publish_tree(
        REPO / "plugins" / "autorun" / "skills" / "commit",
        legacy,
        plugin="ar",
    )
    if edited:
        (legacy / "SKILL.md").write_text("user edit\n", encoding="utf-8")

    result = install(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={"skill_placement": {"": "auto"}},
        home=sandbox,
        harnesses=(PLATFORMS["claude"], PLATFORMS["opencode"]),
        available=(),
        state_dir=sandbox / ".state",
    )

    assert result.ok is True
    assert legacy.exists() is edited
    if edited:
        assert (legacy / "SKILL.md").read_text(encoding="utf-8") == "user edit\n"


USER_COPY = "---\ndescription: the user's own commit skill\n---\nuser copy\n"


def test_a_loadable_user_skill_is_not_duplicated_into_the_extension(sandbox):
    """Qwen reads ~/.agents/skills beside its extension skills, so a native
    copy of a name the user already provides lists that name twice. The
    user's loadable copy IS the route; autorun withholds its own and keeps
    the user's file untouched."""
    from autorun.installer.orchestrate import install

    user_skill = sandbox / ".agents" / "skills" / "commit"
    user_skill.mkdir(parents=True)
    (user_skill / "SKILL.md").write_text(USER_COPY, encoding="utf-8")

    result = install(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={"skill_placement": {"": "auto"}},
        home=sandbox,
        harnesses=(PLATFORMS["qwen"],),
        available=(),
        state_dir=sandbox / ".state",
    )

    native = sandbox / ".qwen" / "extensions" / "ar" / "skills"
    assert result.ok is True
    assert not (native / "commit").exists(), "a native copy would list the name twice"
    assert not (native / "philosophy").exists(), "unblocked names stay on the shared route"
    assert (user_skill / "SKILL.md").read_text(encoding="utf-8") == USER_COPY


def test_blocked_shared_skill_falls_back_inside_only_the_extension(sandbox):
    """A blocker that is not a loadable skill leaves the name reaching Qwen by
    no route at all, so only then does the name fall back into the extension —
    and only that name."""
    from autorun.installer.orchestrate import install

    stray = sandbox / ".agents" / "skills" / "commit"
    stray.mkdir(parents=True)
    (stray / "notes.txt").write_text("not a skill", encoding="utf-8")

    result = install(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={"skill_placement": {"": "auto"}},
        home=sandbox,
        harnesses=(PLATFORMS["qwen"],),
        available=(),
        state_dir=sandbox / ".state",
    )

    native = sandbox / ".qwen" / "extensions" / "ar" / "skills"
    assert result.ok is True
    assert (native / "commit" / "SKILL.md").is_file()
    assert not (native / "philosophy").exists(), "only the blocked name falls back"
    assert (stray / "notes.txt").read_text(encoding="utf-8") == "not a skill"


def test_targeted_install_preserves_other_harnesses_staged_sources(sandbox):
    """A Claude-only run must not sweep another harness's staged extension
    source. Nothing in a targeted walk claims extension-sources/qwen/ar, and
    the retirement sweep read that absence as "no longer shipped" — which
    broke the installed Qwen extension's refresh source until a full or
    --gemini install re-staged it."""
    from autorun.installer.orchestrate import install

    common = dict(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={"skill_placement": {"": "auto"}},
        home=sandbox,
        available=(),
        state_dir=sandbox / ".state",
    )
    first = install(harnesses=(PLATFORMS["qwen"],), **common)
    staged = (
        sandbox / ".autorun" / "installer" / "extension-sources" / "qwen" / "ar"
    )
    assert first.ok is True
    assert staged.is_dir(), "the qwen install must stage its extension source"

    second = install(harnesses=(PLATFORMS["claude"],), **common)
    assert second.ok is True
    assert staged.is_dir(), (
        "a Claude-only install swept another harness's staged source"
    )


def test_targeted_install_preserves_shared_skills_other_harnesses_read(sandbox):
    """A Claude-only run must not retire the shared ``~/.agents/skills`` trees.

    Claude reads only its own plugin skills, so a Claude-only walk claims
    nothing under the shared root — but Codex, Qwen, Pi, Prime, ForgeCode and
    OpenCode load their skills from exactly there. Retiring them as "no longer
    shipped" would be the staging-sweep bug again, one directory over.
    """
    from autorun.installer import discovery
    from autorun.installer.orchestrate import install

    common = dict(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={"skill_placement": {"": "auto"}},
        home=sandbox,
        available=(),
        state_dir=sandbox / ".state",
    )
    assert install(harnesses=(PLATFORMS["qwen"],), **common).ok is True
    shared_commit = discovery.shared_root(home=sandbox) / "commit"
    assert (shared_commit / "SKILL.md").is_file(), "qwen publishes to the shared root"

    second = install(harnesses=(PLATFORMS["claude"],), **common)
    assert second.ok is True
    assert (shared_commit / "SKILL.md").is_file(), (
        "a Claude-only install retired the shared skills other harnesses read"
    )


def test_install_with_no_harness_selected_retires_nothing(sandbox):
    """An empty selection is a no-op, never a sweep of every owned tree."""
    from autorun.installer import discovery
    from autorun.installer.orchestrate import install

    common = dict(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={"skill_placement": {"": "auto"}},
        home=sandbox,
        available=(),
        state_dir=sandbox / ".state",
    )
    assert install(harnesses=(PLATFORMS["qwen"],), **common).ok is True
    shared_commit = discovery.shared_root(home=sandbox) / "commit"
    staged = sandbox / ".autorun" / "installer" / "extension-sources" / "qwen" / "ar"
    assert (shared_commit / "SKILL.md").is_file() and staged.is_dir()

    install(harnesses=(), **common)
    assert (shared_commit / "SKILL.md").is_file(), "empty selection retired shared skills"
    assert staged.is_dir(), "empty selection retired a staged extension source"


def test_staging_for_an_unregistered_harness_still_retires(sandbox):
    """The whole-root sweep existed for the upgrade path: a harness removed
    from the registry must not leak its staged tree forever. Scoping the
    sweep to selected harnesses keeps that promise only if unregistered
    names remain sweepable."""
    import shutil

    from autorun.installer.orchestrate import install

    common = dict(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={"skill_placement": {"": "auto"}},
        home=sandbox,
        available=(),
        state_dir=sandbox / ".state",
    )
    assert install(harnesses=(PLATFORMS["qwen"],), **common).ok is True
    sources = sandbox / ".autorun" / "installer" / "extension-sources"
    legacy = sources / "legacyharness" / "ar"
    shutil.copytree(sources / "qwen" / "ar", legacy)

    assert install(harnesses=(PLATFORMS["claude"],), **common).ok is True
    assert not legacy.exists(), (
        "a staged tree under a name no platform claims must retire"
    )


def test_codex_personal_package_and_marketplace_resolve_to_the_same_tree(sandbox):
    import json

    from autorun.installer.orchestrate import install

    result = install(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={
            "skill_placement": {"": "auto"},
            "codex_hook_source": "user",
            "codex_plugin_marketplace": "personal",
            "_codex_hook_command": "uv run hook_entry.py --cli codex",
        },
        home=sandbox,
        harnesses=(PLATFORMS["codex"],),
        available=(),
        state_dir=sandbox / ".state",
    )

    source = sandbox / "plugins" / "ar"
    marketplace = json.loads(
        (sandbox / ".agents" / "plugins" / "marketplace.json").read_text()
    )
    entry = next(item for item in marketplace["plugins"] if item["name"] == "ar")
    assert result.ok is True
    assert entry["source"] == {"source": "local", "path": "./plugins/ar"}
    assert sandbox / entry["source"]["path"] == source
    assert (source / ".codex-plugin" / "plugin.json").is_file()
    assert not (source / "skills" / "commit").exists(), "auto uses the shared route"
    assert (source / ".codex-plugin" / "skills" / "ar" / "SKILL.md").is_file()
    assert (sandbox / ".agents" / "skills" / "commit" / "SKILL.md").is_file()


def test_codex_plugin_hook_mode_packages_hooks_without_user_hooks(sandbox):
    from autorun.installer.orchestrate import install

    command = "uv run ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py --cli codex"
    result = install(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={
            "skill_placement": {"": "auto"},
            "codex_hook_source": "plugin",
            "codex_plugin_marketplace": "personal",
            "_codex_plugin_hook_command": command,
        },
        home=sandbox,
        harnesses=(PLATFORMS["codex"],),
        available=(),
        state_dir=sandbox / ".state",
    )

    hooks = sandbox / "plugins" / "ar" / "hooks" / "hooks.json"
    assert result.ok is True
    assert command in hooks.read_text(encoding="utf-8")
    assert not (sandbox / ".codex" / "hooks.json").exists()


def test_codex_github_mode_registers_remote_identity_without_local_package(sandbox):
    import subprocess

    from autorun.installer.orchestrate import install

    calls = []

    def record(argv):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = install(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={
            "skill_placement": {"": "auto"},
            "codex_hook_source": "user",
            "codex_plugin_marketplace": "github",
            "_codex_hook_command": "uv run hook_entry.py --cli codex",
        },
        home=sandbox,
        harnesses=(PLATFORMS["codex"],),
        run_command=record,
        available=("codex",),
        state_dir=sandbox / ".state",
    )

    assert result.ok is True
    assert calls == [
        ("codex", "plugin", "marketplace", "add", "ahundt/autorun"),
        ("codex", "plugin", "add", "ar@autorun"),
    ]
    assert not (sandbox / "plugins" / "ar").exists()
    assert not (sandbox / ".agents" / "plugins" / "marketplace.json").exists()


def test_force_withdraws_registration_before_readding_it(sandbox):
    import subprocess

    from autorun.installer.orchestrate import install

    calls = []

    def record(argv):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = install(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={
            "skill_placement": {"": "auto"},
            "codex_hook_source": "user",
            "codex_plugin_marketplace": "personal",
            "_codex_hook_command": "uv run hook_entry.py --cli codex",
        },
        home=sandbox,
        harnesses=(PLATFORMS["codex"],),
        run_command=record,
        available=("codex",),
        state_dir=sandbox / ".state",
        force=True,
    )

    assert result.ok is True
    assert calls == [
        ("codex", "plugin", "remove", "ar@personal"),
        ("codex", "plugin", "remove", "autorun@personal"),
        ("codex", "plugin", "remove", "ar@autorun"),
        ("codex", "plugin", "remove", "autorun@autorun"),
        ("codex", "plugin", "add", "ar@personal"),
    ]


def test_force_retries_a_failed_extension_registration_without_second_withdraw(
    sandbox,
):
    """A forced CLI uninstall can leave a registered but empty extension.

    The old Gemini-family path retried the install once after the destructive
    uninstall, but never repeated that uninstall.  Without this edge case a
    transient install failure strands the user's extension in the half-removed
    state and a second run reports ``already installed``.
    """
    import subprocess

    from autorun.installer.orchestrate import install

    calls = []
    install_attempts = 0

    def record(argv):
        nonlocal install_attempts
        call = tuple(argv)
        calls.append(call)
        if call[:3] == ("gemini", "extensions", "install"):
            install_attempts += 1
            if install_attempts == 1:
                return subprocess.CompletedProcess(argv, 1, "", "network unreachable")
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = install(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={"skill_placement": {"": "auto"}, "conductor": False},
        home=sandbox,
        harnesses=(PLATFORMS["gemini"],),
        run_command=record,
        available=("gemini",),
        state_dir=sandbox / ".state",
        force=True,
    )

    assert result.ok is True
    assert calls == [
        ("gemini", "extensions", "uninstall", "ar"),
        ("gemini", "extensions", "install", calls[1][-2], "--consent"),
        ("gemini", "extensions", "install", calls[1][-2], "--consent"),
    ]


@pytest.mark.parametrize("message", ["already installed", "up-to-date"])
def test_force_does_not_accept_existing_extension_after_withdraw(sandbox, message):
    """After forced removal, an existing-state reply means the files are gone."""
    import subprocess

    from autorun.installer.orchestrate import install

    calls = []

    def record(argv):
        call = tuple(argv)
        calls.append(call)
        if call[:3] == ("gemini", "extensions", "install"):
            return subprocess.CompletedProcess(argv, 1, "", message)
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = install(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={"skill_placement": {"": "auto"}, "conductor": False},
        home=sandbox,
        harnesses=(PLATFORMS["gemini"],),
        run_command=record,
        available=("gemini",),
        state_dir=sandbox / ".state",
        force=True,
    )

    assert result.ok is False
    assert calls == [
        ("gemini", "extensions", "uninstall", "ar"),
        ("gemini", "extensions", "install", calls[1][-2], "--consent"),
    ]


@pytest.mark.parametrize(
    ("harness", "binary", "uninstall", "install"),
    [
        ("gemini", "gemini", ("gemini", "extensions", "uninstall", "ar"),
         ("gemini", "extensions", "install")),
        ("qwen", "qwen", ("qwen", "extensions", "uninstall", "ar"),
         ("qwen", "extensions", "install")),
        ("antigravity", "agy", ("agy", "plugin", "uninstall", "ar"),
         ("agy", "plugin", "install")),
    ],
)
def test_force_retry_policy_is_shared_by_all_extension_flavors(
    harness, binary, uninstall, install
):
    """The retry belongs to the registration contract, not a harness branch."""
    import subprocess

    from autorun.installer import registration

    calls = []
    attempts = 0

    def record(argv):
        nonlocal attempts
        call = tuple(argv)
        calls.append(call)
        if call[:3] == install:
            attempts += 1
            if attempts == 1:
                return subprocess.CompletedProcess(argv, 1, "", "network unreachable")
        stdout = "ar\n" if call[:3] == ("agy", "plugin", "list") else ""
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    values = {
        "name": "ar", "market": "autorun", "root": "/repo",
        "extension": "/staged",
    }
    withdrawn = registration.withdraw(
        harness, values, run=record, available=(binary,)
    )
    outcomes = registration.register(
        harness,
        values,
        run=record,
        available=(binary,),
        force=True,
    )

    assert all(outcome.ok for outcome in withdrawn)
    assert all(outcome.ok for outcome in outcomes)
    install_calls = [call for call in calls if call[:3] == install]
    assert calls[0] == uninstall
    assert len(install_calls) == 2
    assert install_calls[0] == install_calls[1]
    if harness == "antigravity":
        assert [call[:3] for call in calls].count(("agy", "plugin", "validate")) == 2
        assert calls[-1] == ("agy", "plugin", "list")


@pytest.mark.parametrize("listed", [True, False])
def test_antigravity_native_failure_imports_gemini_then_verifies_plugin(listed):
    """The pre-redesign Agy import fallback is successful only when listed."""
    import subprocess

    from autorun.installer import registration

    calls = []

    def record(argv):
        call = tuple(argv)
        calls.append(call)
        if call[:3] == ("agy", "plugin", "validate"):
            return subprocess.CompletedProcess(argv, 1, "", "invalid native bundle")
        if call[:3] == ("agy", "plugin", "list"):
            return subprocess.CompletedProcess(argv, 0, "ar\n" if listed else "other\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    outcomes = registration.register(
        "antigravity",
        {
            "name": "ar",
            "market": "autorun",
            "root": "/repo",
            "extension": "/durable/ar",
        },
        run=record,
        available=("agy",),
    )

    assert calls == [
        ("agy", "plugin", "validate", "/durable/ar"),
        ("agy", "plugin", "import", "gemini"),
        ("agy", "plugin", "list"),
    ]
    assert all(outcome.ok for outcome in outcomes) is listed


def test_forced_extension_failure_keeps_cli_recovery_detail_and_user_store(
    sandbox,
):
    """The CLI's integrity-store remedy survives the compact result API."""
    import subprocess

    from autorun.installer.orchestrate import install

    integrity = sandbox / ".gemini" / "extension_integrity.json"
    integrity.parent.mkdir(parents=True)
    integrity.write_text("{}", encoding="utf-8")
    message = (
        "Extension integrity store cannot be verified. Please delete "
        f"{integrity} to reset it."
    )

    def record(argv):
        if tuple(argv)[:3] == ("gemini", "extensions", "install"):
            return subprocess.CompletedProcess(argv, 1, "", message)
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = install(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={"skill_placement": {"": "auto"}, "conductor": False},
        home=sandbox,
        harnesses=(PLATFORMS["gemini"],),
        run_command=record,
        available=("gemini",),
        state_dir=sandbox / ".state",
        force=True,
    )

    assert result.ok is False
    assert any("extension_integrity.json" in line for line in result.lines())
    assert integrity.read_text(encoding="utf-8") == "{}"


def test_nonforced_extension_failure_is_not_retried(sandbox):
    """Only a destructive forced uninstall earns a second install attempt."""
    import subprocess

    from autorun.installer import registration

    calls = []

    def record(argv):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 1, "", "network unreachable")

    outcomes = registration.register(
        "gemini",
        {"name": "ar", "market": "autorun", "extension": "/staged"},
        run=record,
        available=("gemini",),
    )

    assert len(outcomes) == 1
    assert len(calls) == 1


# ─── Generated artifacts, staged before the walk ─────────────────────────────


def test_the_gemini_family_gets_a_toml_twin_of_every_markdown_command(tmp_path):
    """The family reads commands/<namespace>/<name>.toml, Claude reads
    commands/<name>.md. One source, two renderings, or the two command sets
    drift — which is what happened while each format had its own list."""
    extension_dir = tmp_path / "ext"
    (extension_dir / "commands").mkdir(parents=True)
    (extension_dir / "commands" / "go.md").write_text(
        "---\nname: go\ndescription: start a run\n---\n\nDo the thing with $ARGUMENTS.\n",
        encoding="utf-8",
    )

    written = steps.stage_toml_commands(extension_dir, "ar")

    assert written == ("go",)
    rendered = (extension_dir / "commands" / "ar" / "go.toml").read_text(encoding="utf-8")
    assert 'description = "start a run"' in rendered
    assert "{{args}}" in rendered, "the family's own placeholder, or it never expands"


def test_the_namespace_directory_is_what_makes_the_command_prefixed(tmp_path):
    extension_dir = tmp_path / "ext"
    (extension_dir / "commands").mkdir(parents=True)
    (extension_dir / "commands" / "st.md").write_text(
        "---\nname: st\ndescription: status\n---\n\nbody\n", encoding="utf-8"
    )

    steps.stage_toml_commands(extension_dir, "ar")

    assert (extension_dir / "commands" / "ar" / "st.toml").is_file()


def test_a_plugin_with_no_commands_is_not_an_error(tmp_path):
    assert steps.stage_toml_commands(tmp_path / "nothing-here", "ar") == ()


def test_the_opencode_shim_substitutes_both_values_absolutely(tmp_path):
    """The plugin runs inside OpenCode's process, so a path resolved through
    the host PATH mid-session picks up whatever the user's shell has."""
    plugin = tmp_path / "plugins" / "autorun"
    template = plugin / steps.OPENCODE_TEMPLATE_SUBDIR
    template.mkdir(parents=True)
    (template / "autorun.js").write_text(
        "const socket = '__AUTORUN_SOCKET__';\nconst cmd = __AUTORUN_HOOK_ENTRY_COMMAND__;\n",
        encoding="utf-8",
    )
    bridge = plugin / steps.BRIDGE_TEMPLATE_SUBDIR
    bridge.mkdir(parents=True)
    (bridge / "daemon-client.mjs").write_text("export const bridge = true;\n")

    shims = steps.stage_opencode_shim(
        tmp_path / "staged", {"ar": plugin},
        socket="/tmp/ar.sock", command=("uv", "run", "hook entry.py"),
    )

    text = (shims["ar"] / "autorun.js").read_text(encoding="utf-8")
    assert "__AUTORUN_SOCKET__" not in text and "/tmp/ar.sock" in text
    assert "__AUTORUN_HOOK_ENTRY_COMMAND__" not in text
    assert '["uv", "run", "hook entry.py"]' in text


def test_a_plugin_without_the_shim_template_stages_nothing(tmp_path):
    assert steps.stage_opencode_shim(
        tmp_path / "staged", {"ar": tmp_path / "empty"}, socket="s", command="c"
    ) == {}


def test_only_opencode_receives_the_javascript_bridge():
    """Every other harness's users need Python alone and must not be handed a
    second runtime requirement."""
    receiving = [
        name for name, tuple_ in steps.STEPS.items() if steps.opencode_shim_step in tuple_
    ]

    assert receiving == ["opencode"], receiving


def test_a_harness_with_no_memory_file_gets_no_region(sandbox):
    """`None` is a real answer, not a missing case."""

    class Bare:
        name = "bare"
        config_dir = ""
        memory_filename = ""

    ctx = Context(marketplace_root=REPO, home=sandbox)
    assert steps.regions_for(Bare(), ctx) == ()
    assert steps.hooks_for(Bare(), ctx) == ()


def test_only_codex_merges_into_a_user_owned_hooks_file(sandbox):
    """Claude and the Gemini family carry hooks inside a tree autorun owns
    outright, so those are ordinary intents rather than a merge."""
    ctx = Context(
        marketplace_root=REPO,
        plugin_dirs=(REPO / "plugins" / "autorun",),
        home=sandbox,
        settings={"codex_hook_source": "user", "_codex_hook_command": "uv run hook_entry.py"},
    )

    merging = [
        name for name, platform in PLATFORMS.items() if steps.hooks_for(platform, ctx)
    ]

    assert merging == ["codex"], merging
