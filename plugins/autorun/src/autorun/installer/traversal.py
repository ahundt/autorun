#!/usr/bin/env python3
"""One walk that serves status, dry run, install, uninstall and prune.

The old installer treats these as five subsystems: ``install_plugins`` (679
lines, 7 harness branches), ``uninstall_plugins`` (71), ``show_status`` plus two
helpers (504), a ``dry_run`` flag threaded through the call graph, and three
separate pruners (132). They are one traversal over (target, source, plugin)
triples, differing only in what happens to the answer.

    status / dry run   print the decision, write nothing
    install            act on PUBLISH and RETIRE
    uninstall          the same walk with source=None, so everything is RETIRE
    prune              that same source=None walk, scoped to skills

Status *is* dry run. Both ask "what would install do?" and neither writes, so
there is no third code path — the reason ``show_status`` grew its own per-harness
reporting is that nobody noticed it was recomputing what install already knew.

TWO IDEAS CARRY THIS
====================

**Steps are data.** A step yields :class:`Intent` objects and performs no I/O.
Which steps a harness runs is a field on its registry entry, so the orchestrator
has no harness branches at all — it is a comprehension. Adding a harness is a
registry entry; adding a capability is a function plus the harnesses that list
it. Neither touches this file.

**Intents are pure.** Because a step only *describes* what it wants, the same
step serves all three modes. That is what removes the ``dry_run`` parameter from
every function it currently threads through: dry run stops being an argument and
becomes one branch, here, at the top.

Complexity: O(H x S x I) decisions for H harnesses, S steps each and I intents
per step — the traversal itself is linear and does no I/O. Each decision costs
one marker read, plus a tree hash only when the target is about to change.
"""

from __future__ import annotations

import os
from itertools import chain
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping, Protocol, Sequence

from .discovery import process_home, redirected_home
from .fs import (
    Decision,
    Verdict,
    decide,
    decide_files,
    decide_link,
    owns,
    publish_files,
    publish_link,
    publish_tree,
    read_marker,
    withdraw_files,
    withdraw_link,
    withdrawn,
)

__all__ = [
    "Intent", "Mode", "Step", "Harness", "Context", "Target",
    "run", "walk", "report", "targets", "retirements", "Kind",
]


@dataclass(frozen=True, slots=True)
class Context:
    """Everything a step needs, resolved once at the entry point.

    Re-resolving a setting inside a callee re-applies the environment over the
    caller's explicit intent, which is the bug that made the custom-harness path
    fail under ``AUTORUN_CODEX_HOOK_SOURCE=plugin``. Resolve once, pass down.
    """

    marketplace_root: Path
    plugin_dirs: tuple[Path, ...] = ()
    #: The home this run targets. It must agree with ``$HOME``, and defaults to
    #: it. Setting this alone does *not* relocate an install: home-anchored
    #: skill routes resolve through the process-home seam (``$HOME`` when set), so a
    #: caller that changes only this field gets a partly-relocated result — some
    #: paths moved, some not. Redirect ``$HOME`` and let this default.
    home: Path = field(default_factory=process_home)

    def __post_init__(self) -> None:
        """Refuse a ``home`` that disagrees with ``$HOME``.

        Setting this field alone relocates *some* paths and not others: a skill
        route anchored at ``Path.home()`` reads ``$HOME`` directly, so a caller
        who redirects only this one gets a walk that reads a sandbox and writes
        a real home. That is not a hypothetical — it uninstalled 16 skills from
        a live machine during a self-check that looked isolated.

        Raising is the only safe answer. Silently correcting the field would
        hide the caller's mistaken belief, and honouring it would keep the split
        that caused the damage. ``$HOME`` is the seam; redirect it and let this
        default.
        """
        declared = Path(os.environ["HOME"]) if "HOME" in os.environ else None
        if declared is not None and self.home != declared:
            raise ValueError(
                f"Context(home={self.home}) disagrees with $HOME={declared}. "
                "Home-anchored routes resolve through the process-home seam, "
                "which reads $HOME when set, so this would read one home and write another. Set the "
                "HOME environment variable instead and leave `home` to default."
            )
    settings: Mapping[str, object] = field(default_factory=dict)
    force: bool = False


@dataclass(frozen=True, slots=True)
class Intent:
    """One thing autorun wants at one path. Pure data — a step does no I/O.

    ``source is None`` already means "no longer shipped", so uninstall does not
    need its own intent type: it is the install walk with every source dropped.

    ``kind`` picks which publish/withdraw pair applies. Three, not a boolean:
    a flag could express two and the bridge needs a third, and a second flag
    beside it would admit a state that means nothing.
    """

    target: Path
    source: Path | None = None
    plugin: str = ""
    settings: Mapping[str, str] = field(default_factory=dict)
    ownership_proof: Callable[[Path], bool] | None = None
    kind: "Kind" = None  # defaults to Kind.TREE in __post_init__

    def __post_init__(self):
        if self.kind is None:
            object.__setattr__(self, "kind", Kind.TREE)


class Kind(Enum):
    """How one intent is published, and therefore how it is removed.

    TREE  a directory autorun owns outright: swap it whole.
    FILES a directory shared with the user: per file, against the manifest.
    LINK  a symlink into the shared root, so one edit applies everywhere.
    """

    TREE = "tree"
    FILES = "files"
    LINK = "link"


Step = Callable[["Harness", Context], Iterable[Intent]]


class Harness(Protocol):
    """What the traversal needs from a registry entry, and nothing more.

    Deliberately narrower than ``Platform``: the traversal is testable against a
    synthetic harness without touching the registry, which is what makes the
    zero-new-branches claim checkable rather than asserted.
    """

    name: str
    install_steps: tuple[Step, ...]


@dataclass(frozen=True, slots=True)
class Target:
    """A registry entry paired with the steps that install it.

    The sequence lives here rather than as a ``Platform`` field for two reasons.
    Steps are defined in the install modules, which import ``platforms``, so a
    field holding them would close an import cycle. And encoding them as *names*
    for later lookup already shipped a nonexistent handler as a capability.
    Holding the callables themselves cannot express that.

    ``platform`` stays reachable so a step can read the harness facts it needs —
    config dir, skill routes, hook protocol — without a second lookup.
    """

    platform: object
    install_steps: tuple[Step, ...] = ()

    @property
    def name(self) -> str:
        return getattr(self.platform, "name", str(self.platform))


def targets(
    platforms: Iterable[object], steps: Mapping[str, tuple[Step, ...]]
) -> tuple[Target, ...]:
    """Pair each selected harness with its declared steps.

    A harness with no entry gets no steps and contributes no intents, which is
    how an unsupported harness stays out of the walk without a branch testing
    for it.
    """
    return tuple(
        Target(p, steps.get(getattr(p, "name", ""), ())) for p in platforms
    )


class Mode(Enum):
    """What to do with each decision. Three, not five."""

    PREVIEW = "preview"      # status and dry run: identical, and neither writes
    INSTALL = "install"
    UNINSTALL = "uninstall"

    @property
    def retiring(self) -> bool:
        """Uninstall asks the install question with every source dropped.

        **The mode does not decide scope.** "Every source" means every source
        *in this walk*, and the walk contains only the intents the selected
        plugins produced. Which trees may actually be removed is decided one
        level down, by ``decide(target, None, plugin=intent.plugin)`` and
        ``fs.owns`` — so ``--uninstall pdf-extractor`` cannot touch autorun's
        artifacts even though every source in its walk is dropped.

        Stated here because reading this property alone suggests otherwise, and
        an auditor did in fact read it that way and report a data-loss bug that
        does not exist.
        """
        return self is Mode.UNINSTALL

    @property
    def writes(self) -> bool:
        return self is not Mode.PREVIEW


def backup_root(ctx: Context) -> Path:
    """Where ``--force`` parks the previous copy of a hashless legacy tree.

    Under autorun's own state directory, never inside a skills root: a
    ``<name>.autorun-backup`` directory beside a skill is discovered as a skill
    by every harness that scans the root. ``_backup_root`` in settings is the
    test seam, the same shape ``_extension_source_root`` uses.
    """
    configured = ctx.settings.get("_backup_root")
    return (
        Path(str(configured))
        if configured
        else ctx.home / ".autorun" / "installer" / "backups"
    )


def _perform(
    intent: Intent, decision: Decision, *, force: bool = False, backups: Path | None = None
) -> Decision:
    """Carry out one decision. The only place this module touches the disk.

    A shared directory takes the per-file pair; an exclusive one takes the
    whole-tree pair. Choosing between them is the only thing an ``Intent``
    flag decides, because everything else about the two is identical.
    """
    publishers = {Kind.TREE: publish_tree, Kind.FILES: publish_files, Kind.LINK: publish_link}
    if decision.verdict is Verdict.PUBLISH and intent.source is not None:
        if intent.kind is Kind.TREE:
            return publish_tree(
                intent.source,
                intent.target,
                plugin=intent.plugin,
                ownership_proof=intent.ownership_proof,
                force=force,
                backup_root=backups,
                **intent.settings,
            )
        if intent.kind is Kind.LINK:
            return publish_link(
                intent.source,
                intent.target,
                plugin=intent.plugin,
                ownership_proof=intent.ownership_proof,
                exact_target=(
                    intent.source
                    if intent.settings.get("registration_link") == "1"
                    else None
                ),
                **intent.settings,
            )
        return publishers[intent.kind](
            intent.source, intent.target, plugin=intent.plugin, **intent.settings
        )
    if decision.verdict is Verdict.RETIRE:
        if intent.kind is Kind.FILES:
            removed = bool(withdraw_files(intent.target, plugin=intent.plugin))
        elif intent.kind is Kind.LINK and intent.source is not None:
            if intent.target.is_symlink():
                removed = withdraw_link(
                    intent.target,
                    intent.source.parent,
                    exact_target=(
                        intent.source
                        if intent.settings.get("registration_link") == "1"
                        else None
                    ),
                )
            else:
                removed = withdrawn(
                    intent.target,
                    plugin=intent.plugin,
                    ownership_proof=intent.ownership_proof,
                    force=force,
                    backup_root=backups,
                )
        else:
            removed = withdrawn(
                intent.target,
                plugin=intent.plugin,
                ownership_proof=intent.ownership_proof,
                force=force,
                backup_root=backups,
            )
        # Report what happened. `withdrawn` returns False for a symlink, a
        # foreign marker or a failed delete, and discarding that made a refused
        # removal read exactly like a clean one.
        if not removed:
            return Decision(Verdict.KEEP, intent.target, "could not remove", decision.edited)
    return decision


def walk(harnesses: Sequence[Harness], ctx: Context) -> Iterator[Intent]:
    """Every intent every selected harness declares, in registry order.

    No branch here names a harness. That is the whole point: a harness's
    sequence is data on its registry entry, so this loop never learns about one.
    """
    return (
        intent
        for harness in harnesses
        for step in harness.install_steps
        for intent in step(harness, ctx)
    )


def retirements(
    roots: Iterable[Path], claimed: Iterable[Path], *, plugins: Iterable[str]
) -> Iterator[Intent]:
    """Retire every tree we own that no current intent claims.

    The upgrade path. A step yields intents for where its capability writes
    *today*, so anything a previous version wrote somewhere else is never
    visited, never decided about, and never removed — while still carrying our
    marker. Retiring Qwen's native skill route left 17 marked directories behind
    for exactly this reason, and the current registry cannot even name that
    location, so enumerating routes would not have found them.

    Sweeping for our own marker is what makes this correct as routes change
    rather than correct on the day it was written. Everything downstream is
    unchanged: ``source=None`` is already the retirement question, so
    :func:`decide` returns RETIRE for ours-and-unchanged and KEEP for a tree the
    user has edited, with no new policy anywhere.

    ``plugins`` is required, not optional, and that is deliberate. Ownership
    comparison treats an empty plugin name as "belongs to whoever asks", so a
    defaulted scope would sweep *every* plugin's trees — harmless during a full
    install and data loss during ``--uninstall pdf-extractor``, which must leave
    autorun's own artifacts alone. Making the caller name the scope turns that
    into a missing argument rather than a silent over-reach.

    This sweep is half of the upgrade story, and only half. Stale content
    *inside* a tree that is still published is cleared by the republish itself,
    because :func:`publish_tree` swaps the whole directory — an extension that
    used to carry a ``skills/`` directory loses it the moment a staging tree
    without one is published, while its commands survive. The sweep covers the
    other case: a tree that is no longer published *anywhere*, which nothing
    would otherwise visit. Neither mechanism subsumes the other, and a claimed
    tree is skipped here precisely because the republish already owns it.
    """
    from .fs import owned_trees

    scope = [name for name in plugins if name]
    if not scope:
        raise ValueError(
            "retirements() needs at least one plugin name: an empty scope would "
            "sweep every plugin's trees, which is data loss during a partial "
            "uninstall."
        )
    keep = {path.resolve() for path in claimed}
    seen: set[Path] = set()
    for root in roots:
        for plugin in scope:
            for tree in owned_trees(root, plugin=plugin):
                parts = tree.relative_to(root).parts
                if ("plugins", "cache") in zip(parts, parts[1:]):
                    continue  # harness copies source markers into versioned cache
                resolved = tree.resolve()
                if resolved in keep or resolved in seen:
                    continue
                seen.add(resolved)
                manifest = read_marker(tree)
                kind = (
                    Kind.FILES
                    if manifest is not None and manifest.settings.get("kind") == "files"
                    else Kind.TREE
                )
                yield Intent(target=tree, source=None, plugin=plugin, kind=kind)


def run(
    harnesses: Sequence[Harness],
    ctx: Context,
    mode: Mode = Mode.PREVIEW,
    *,
    extra: Iterable[Intent] = (),
) -> list[Decision]:
    """The installer, the uninstaller, the status pass and the dry run.

    Callers pick a mode; nothing below this line knows which one, because a
    decision is the same object whether it is printed or acted on.
    """
    decisions = []
    # Every harness that reads the shared ``~/.agents/skills`` root yields the
    # same intent for each skill. Deciding it once per walk keeps the report
    # honest (one line per path, not one per reading harness) and hashes each
    # published tree once instead of once per harness; the second identical
    # intent could only ever have concluded "already current".
    decided: set[tuple] = set()
    for intent in chain(walk(harnesses, ctx), extra):
        key = (
            intent.kind, intent.target, intent.source, intent.plugin,
            tuple(sorted(intent.settings.items())),
        )
        if key in decided:
            continue
        decided.add(key)
        source = None if mode.retiring else intent.source
        if intent.kind is Kind.LINK:
            # A link's ownership is its target, not a marker; `decide` reads a
            # live symlink as user-authored and would refuse it forever.
            inside = (intent.source or intent.target).parent
            decision = decide_link(
                source,
                intent.target,
                inside,
                plugin=intent.plugin,
                ownership_proof=intent.ownership_proof,
                exact_target=(
                    intent.source
                    if intent.settings.get("registration_link") == "1"
                    else None
                ),
            )
        elif intent.kind is Kind.FILES:
            if source is not None:
                decision = decide_files(source, intent.target, plugin=intent.plugin)
            else:
                manifest = read_marker(intent.target)
                if manifest is None:
                    decision = Decision(Verdict.SKIP, intent.target, "already absent")
                elif owns(manifest, intent.plugin):
                    decision = Decision(Verdict.RETIRE, intent.target, "no longer shipped")
                else:
                    decision = Decision(Verdict.KEEP, intent.target, "belongs to another plugin")
        else:
            decision = decide(
                intent.target,
                source,
                plugin=intent.plugin,
                ownership_proof=(
                    intent.ownership_proof if source is not None else None
                ),
                force=ctx.force,
            )
        decisions.append(
            _perform(intent, decision, force=ctx.force, backups=backup_root(ctx))
            if mode.writes
            else decision
        )
    return decisions


def report(decisions: Iterable[Decision]) -> str:
    """Render decisions for a human, grouped so the actionable ones lead.

    KEEP is first because it is the only verdict that asks the user to do
    something: a name collided, or they edited a file autorun installed. A count
    nobody can act on is what the old status pass printed instead.
    """
    order = (Verdict.KEEP, Verdict.PUBLISH, Verdict.RETIRE, Verdict.SKIP)
    grouped = {v: [d for d in decisions if d.verdict is v] for v in order}
    lines = [
        f"{verdict.value}: {len(found)}"
        for verdict, found in grouped.items()
        if found
    ]
    detail = [f"  {d.describe()}" for d in grouped[Verdict.KEEP]]
    return "\n".join(lines + detail)



def _made(path: Path) -> Path:
    """Create a directory and return it, so a `with` header stays one line."""
    path.mkdir(parents=True, exist_ok=True)
    return path

def demo() -> None:
    """Self-check: one synthetic harness, no branches, all three modes."""
    import tempfile

    @dataclass(frozen=True, slots=True)
    class Fake:
        name: str
        install_steps: tuple[Step, ...]

    def skills_step(harness: Fake, ctx: Context) -> Iterable[Intent]:
        root = ctx.marketplace_root / "skills"
        return [
            Intent(target=ctx.home / ".fake" / "skills" / p.name, source=p, plugin="ar")
            for p in sorted(root.iterdir())
            if p.is_dir()
        ]

    def commands_step(harness: Fake, ctx: Context) -> Iterable[Intent]:
        return [Intent(
            target=ctx.home / ".fake" / "commands",
            source=ctx.marketplace_root / "commands",
            plugin="ar",
            kind=Kind.FILES,
        )]

    with tempfile.TemporaryDirectory() as tmp, redirected_home(
        _made(Path(tmp) / "home")
    ):
        root = Path(tmp)
        for name in ("alpha", "beta"):
            skill = root / "market" / "skills" / name
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
        cmds = root / "market" / "commands"
        cmds.mkdir(parents=True)
        (cmds / "go.md").write_text("go\n", encoding="utf-8")

        harness = Fake("fake", (skills_step, commands_step))
        ctx = Context(marketplace_root=root / "market", home=root / "home")

        # A shared directory the user already owns still accepts our files.
        # decide() would call the whole directory user-authored and refuse.
        (ctx.home / ".fake" / "commands").mkdir(parents=True)
        (ctx.home / ".fake" / "commands" / "theirs.md").write_text("pre\n", encoding="utf-8")

        # PREVIEW writes nothing, and says so before anything exists.
        preview = run([harness], ctx, Mode.PREVIEW)
        assert len(preview) == 3, preview
        assert all(d.verdict is Verdict.PUBLISH for d in preview)
        assert not (ctx.home / ".fake" / "skills").exists(), "preview must not write"
        assert not (ctx.home / ".fake" / "commands" / "go.md").exists(), "preview must not write"
        assert read_marker(ctx.home / ".fake" / "commands") is None, "preview must not claim"

        # INSTALL acts on exactly what PREVIEW described.
        installed = run([harness], ctx, Mode.INSTALL)
        assert [d.verdict for d in installed] == [d.verdict for d in preview]
        assert (ctx.home / ".fake" / "skills" / "alpha" / "SKILL.md").is_file()
        assert (ctx.home / ".fake" / "commands" / "go.md").is_file()

        # A second install is a no-op, and status is the same call.
        again = run([harness], ctx, Mode.INSTALL)
        assert all(d.verdict is Verdict.SKIP for d in again), again
        assert [d.verdict for d in run([harness], ctx, Mode.PREVIEW)] == [Verdict.SKIP] * 3

        # A user edit is kept, named, and survives.
        edited = ctx.home / ".fake" / "skills" / "alpha" / "SKILL.md"
        edited.write_text("MINE\n", encoding="utf-8")
        (root / "market" / "skills" / "alpha" / "SKILL.md").write_text("v2\n", encoding="utf-8")
        kept = run([harness], ctx, Mode.INSTALL)
        assert any(d.verdict is Verdict.KEEP for d in kept)
        assert edited.read_text() == "MINE\n"
        assert "SKILL.md" in report(kept), report(kept)

        # A user's own file beside ours is never removed by uninstall.
        (ctx.home / ".fake" / "commands" / "mine.md").write_text("theirs\n", encoding="utf-8")
        removed = run([harness], ctx, Mode.UNINSTALL)
        assert (ctx.home / ".fake" / "commands" / "mine.md").is_file(), "shared dir keeps user files"
        assert not (ctx.home / ".fake" / "skills" / "beta").exists(), "unedited skill retired"
        assert edited.read_text() == "MINE\n", "an edited skill is kept, not retired"
        assert any(d.verdict is Verdict.RETIRE for d in removed)

        # Adding a harness adds no branch to this module — and a second harness
        # that yields the same skill intents (the shared-root shape) adds no
        # decisions either: an identical intent is decided once per walk.
        second = Fake("other", (skills_step,))
        both = run([harness, second], ctx, Mode.PREVIEW)
        assert len(both) == 3, "3 intents + 2 identical ones decided once, no special cases"

        # The real registry pairs the same way, and a harness with no declared
        # steps drops out of the walk without anything testing for its name.
        from ..platforms import PLATFORMS

        paired = targets(PLATFORMS.values(), {"claude": (skills_step,)})
        assert {t.name for t in paired} == set(PLATFORMS)
        assert sum(len(t.install_steps) for t in paired) == 1, "only claude declared steps"
        walked = run(
            paired,
            ctx,
            Mode.PREVIEW,
        )
        assert len(walked) == 2, "one harness x one step x two skills"

    print("installer.traversal: all self-checks passed")


if __name__ == "__main__":
    demo()
