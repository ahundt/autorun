"""The step table, and the walk it drives, end to end against a sandboxed home.

Every other install test exercises one module. These exercise the thing a user
actually runs: resolve the plugins, stage what has to be generated, walk every
registered harness, and check the three properties that make an installer
trustworthy — a preview writes nothing, a second install changes nothing, and an
uninstall leaves nothing behind.

`HOME` is redirected with monkeypatch.setenv, which is the single isolation
seam: `Path.home()` reads it, so every route moves together. Nothing here may
touch the developer's own configuration.
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
    monkeypatch.setenv("HOME", str(home))
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
        settings={"skill_placement": {"": "auto"}, "shared_skills_bridge": "link"},
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
        home=sandbox,
        settings={"codex_hook_source": "user", "_codex_hook_command": "uv run hook_entry.py"},
    )

    merging = [
        name for name, platform in PLATFORMS.items() if steps.hooks_for(platform, ctx)
    ]

    assert merging == ["codex"], merging
