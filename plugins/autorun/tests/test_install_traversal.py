"""One traversal serves status, dry run, install, uninstall and prune.

The installer this replaces treats those as five subsystems that each re-derive
their own destination paths, and all five had drifted from one another. These
tests pin the property that makes one walk sufficient: a decision is the same
object whether it is printed or acted on, so the mode only chooses what happens
afterwards.

The zero-branch claim is checked against the real registry rather than asserted,
because "adding a harness needs no orchestrator change" is only worth stating if
something fails when it stops being true.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.installer.fs import Verdict, read_marker  # noqa: E402
from autorun.installer.traversal import (
    Kind,  # noqa: E402
    Context,
    Intent,
    Mode,
    Step,
    Target,
    report,
    run,
    targets,
    walk,
)


@dataclass(frozen=True, slots=True)
class Fake:
    name: str
    install_steps: tuple[Step, ...]


def skills_step(harness, ctx: Context) -> Iterable[Intent]:
    root = ctx.marketplace_root / "skills"
    return [
        Intent(target=ctx.home / ".fake" / "skills" / p.name, source=p, plugin="ar")
        for p in sorted(root.iterdir())
        if p.is_dir()
    ]


def commands_step(harness, ctx: Context) -> Iterable[Intent]:
    return [
        Intent(
            target=ctx.home / ".fake" / "commands",
            source=ctx.marketplace_root / "commands",
            plugin="ar",
            kind=Kind.FILES,
        )
    ]


@pytest.fixture
def ctx(tmp_path: Path) -> Context:
    for name in ("alpha", "beta"):
        skill = tmp_path / "market" / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    commands = tmp_path / "market" / "commands"
    commands.mkdir(parents=True)
    (commands / "go.md").write_text("go\n", encoding="utf-8")
    return Context(marketplace_root=tmp_path / "market", home=tmp_path / "home")


@pytest.fixture
def harness() -> Fake:
    return Fake("fake", (skills_step, commands_step))


# ─── The three modes ─────────────────────────────────────────────────────────


def test_preview_describes_exactly_what_install_then_does(ctx, harness):
    """Status and dry run are the same question, and neither may write. The
    old status pass grew its own per-harness reporting because nobody noticed
    it was recomputing what install already knew."""
    preview = run([harness], ctx, Mode.PREVIEW)

    assert [d.verdict for d in preview] == [Verdict.PUBLISH] * 3
    assert not (ctx.home / ".fake" / "skills").exists(), "preview must not write"

    installed = run([harness], ctx, Mode.INSTALL)

    assert [d.verdict for d in installed] == [d.verdict for d in preview]
    assert [d.target for d in installed] == [d.target for d in preview]


def test_a_second_install_is_a_no_op(ctx, harness):
    """SKIP rather than a rewrite, so a repeated install does not churn mtimes
    the harness watches."""
    run([harness], ctx, Mode.INSTALL)

    assert all(d.verdict is Verdict.SKIP for d in run([harness], ctx, Mode.INSTALL))
    assert all(d.verdict is Verdict.SKIP for d in run([harness], ctx, Mode.PREVIEW))


def test_uninstall_is_the_install_walk_with_every_source_dropped(ctx, harness):
    """`source is None` already means "no longer shipped", which is why
    uninstall needs no traversal, no path derivation and no intent type of its
    own."""
    run([harness], ctx, Mode.INSTALL)

    removed = run([harness], ctx, Mode.UNINSTALL)

    assert any(d.verdict is Verdict.RETIRE for d in removed)
    assert not (ctx.home / ".fake" / "skills" / "beta").exists()


# ─── User data survives every mode ───────────────────────────────────────────


def test_an_edited_skill_is_kept_named_and_left_on_disk(ctx, harness):
    run([harness], ctx, Mode.INSTALL)
    edited = ctx.home / ".fake" / "skills" / "alpha" / "SKILL.md"
    edited.write_text("MINE\n", encoding="utf-8")
    (ctx.marketplace_root / "skills" / "alpha" / "SKILL.md").write_text("v2\n", encoding="utf-8")

    decisions = run([harness], ctx, Mode.INSTALL)

    assert any(d.verdict is Verdict.KEEP for d in decisions)
    assert edited.read_text() == "MINE\n"
    assert "SKILL.md" in report(decisions), "the report names the file, not a count"


def test_an_edited_skill_is_not_retired_by_uninstall(ctx, harness):
    """The same comparison protects against removal as against replacement."""
    run([harness], ctx, Mode.INSTALL)
    edited = ctx.home / ".fake" / "skills" / "alpha" / "SKILL.md"
    edited.write_text("MINE\n", encoding="utf-8")

    run([harness], ctx, Mode.UNINSTALL)

    assert edited.read_text() == "MINE\n"


# ─── Shared directories are owned per file ───────────────────────────────────


def test_a_shared_directory_the_user_already_owns_still_accepts_our_files(ctx, harness):
    """ForgeCode's and OpenCode's `commands/` hold our files beside theirs. A
    whole-directory decision calls such a folder user-authored and refuses,
    so a first install into it would install nothing."""
    commands = ctx.home / ".fake" / "commands"
    commands.mkdir(parents=True)
    (commands / "theirs.md").write_text("pre-existing\n", encoding="utf-8")

    run([harness], ctx, Mode.INSTALL)

    assert (commands / "go.md").is_file(), "ours was added"
    assert (commands / "theirs.md").read_text() == "pre-existing\n", "theirs untouched"
    assert set(read_marker(commands).files) == {"go.md"}, "only ours is claimed"


def test_uninstall_leaves_a_shared_directory_and_the_users_files(ctx, harness):
    run([harness], ctx, Mode.INSTALL)
    commands = ctx.home / ".fake" / "commands"
    (commands / "mine.md").write_text("theirs\n", encoding="utf-8")

    run([harness], ctx, Mode.UNINSTALL)

    assert commands.is_dir(), "a shared directory is never removed"
    assert (commands / "mine.md").is_file()
    assert not (commands / "go.md").exists()


# ─── Zero orchestrator branches ──────────────────────────────────────────────


def test_adding_a_harness_adds_no_branch(ctx, harness):
    """Two harnesses walk through the identical code path; the second one is
    not special-cased anywhere."""
    second = Fake("other", (skills_step,))

    assert len(list(walk([harness, second], ctx))) == 5  # 3 + 2


def test_the_real_registry_pairs_without_naming_a_harness(ctx):
    """Every registered platform can be paired with steps, and one that
    declares none drops out of the walk without anything testing for its
    name — which is what makes a new harness a registry entry rather than an
    orchestrator change."""
    from autorun.platforms import PLATFORMS

    paired = targets(PLATFORMS.values(), {"claude": (skills_step,)})

    assert {t.name for t in paired} == set(PLATFORMS)
    assert sum(len(t.install_steps) for t in paired) == 1
    assert len(run(paired, ctx, Mode.PREVIEW)) == 2, "one harness, one step, two skills"


def test_a_target_keeps_its_platform_reachable():
    """A step needs the harness facts — config dir, skill routes, hook protocol
    — without a second registry lookup."""
    from autorun.platforms import PLATFORMS

    target = Target(PLATFORMS["codex"], ())

    assert target.name == "codex"
    assert target.platform is PLATFORMS["codex"]
