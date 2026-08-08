#!/usr/bin/env python3
"""The three entry points, composed from the modules below them.

Everything else in this package answers one question well. This is where those
answers become ``--install``, ``--status`` and ``--uninstall``, and it is
deliberately the only module that knows the order they happen in.

ONE RESOLUTION POINT
====================

Settings are resolved once, here, and passed down as a ``Context``. A callee
that re-reads the environment re-applies it over the caller's explicit intent,
which is the bug that made the custom-harness path fail under
``AUTORUN_CODEX_HOOK_SOURCE=plugin``: the flag was resolved at the top, then
resolved again three frames down where the flag was not in scope.

THE ORDER, AND WHY
==================

1. Stage what has to be generated, so the walk stays pure and a preview writes
   nothing.
2. Walk. This is where files land, and it is identical in all three modes.
3. Regions and hooks — the parts of a user's own files autorun owns a range of.
4. Registration. The harness CLIs come *after* the files, because
   ``claude plugin install`` reads what is on disk.
5. Teardown, uninstall only, after the files are gone.

Reversed on uninstall for the same reason: tell the harness to forget the
plugin before removing the files it points at, or the harness caches a path
that no longer resolves.

Complexity: one walk, O(harnesses x skills) intents; one subprocess per
registration command. A preview spawns nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from . import discovery, registration, skills, status, steps, teardown
from ..platforms import ExtensionSkills, PluginPackageSkills
from .fs import Verdict
from .runtime import Outcome, Runner, _spawn
from .traversal import Context, Mode, retirements, run, targets

__all__ = ["Result", "perform", "install", "uninstall", "preview"]


@dataclass(frozen=True, slots=True)
class Result:
    """Everything one run did, in a value a caller can report or assert on."""

    mode: Mode
    decisions: tuple = ()
    notes: tuple[str, ...] = ()
    registrations: tuple[Outcome, ...] = ()
    findings: tuple[status.Finding, ...] = ()
    torn_down: teardown.Teardown | None = None
    missing: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether anything happened that a user needs to act on.

        A ``KEEP`` is not a failure — it is autorun declining to overwrite an
        edit, which is the behaviour that was asked for — but it *is* reported,
        because a skill silently not updating is what a user would otherwise
        discover much later.
        """
        return (
            not self.missing
            and all(o.ok for o in self.registrations)
            and all(f.level is not status.Level.BROKEN for f in self.findings)
        )

    def lines(self, *, verbose: bool = False) -> list[str]:
        out = list(status.report_lines(self.decisions, self.findings, verbose=verbose))
        out.extend(self.notes)
        out.extend(o.describe() for o in self.registrations if verbose or not o.ok)
        if self.torn_down is not None:
            out.extend(self.torn_down.describe())
        if self.missing:
            out.append(f"not found: {', '.join(self.missing)}")
        return out


def _values(root: Path, plugin: str, market: str, extension: Path | None) -> dict[str, str]:
    """The placeholders every registration command draws from."""
    return {
        "root": str(root),
        "name": plugin,
        "market": market,
        "extension": str(extension) if extension is not None else "",
    }


def perform(
    mode: Mode,
    *,
    marketplace_root: Path,
    plugins: Sequence[str] = ("ar",),
    settings: Mapping[str, object] | None = None,
    home: Path | None = None,
    market: str = "autorun",
    harnesses: Iterable[object] | None = None,
    run_command: Runner = _spawn,
    available: Iterable[str] | None = None,
    state_dir: Path | None = None,
    step_table: Mapping[str, tuple] | None = None,
    force: bool = False,
    teardown_enabled: bool = True,
) -> Result:
    """One run, in one mode. The single place the order of operations lives.

    ``harnesses`` defaults to every registered platform. Passing a subset is how
    ``--only`` works, and passing synthetic ones is how this is tested without
    the registry.
    """
    from ..platforms import PLATFORMS

    resolved = dict(settings or {})
    codex_marketplace = str(resolved.get("codex_plugin_marketplace", "personal"))
    resolved.setdefault(
        "_registration_variants", {"codex": f"codex:{codex_marketplace}"}
    )
    resolved.setdefault(
        "_registration_markets",
        {"codex": "personal" if codex_marketplace == "personal" else "autorun"},
    )
    directories, missing = discovery.resolve_plugins(marketplace_root, list(plugins))
    if not directories and mode is Mode.INSTALL:
        return Result(mode, missing=missing)

    selected = list(PLATFORMS.values() if harnesses is None else harnesses)
    named = {discovery.plugin_name(d): d for d in directories}
    for plugin in plugins:
        named.setdefault(plugin, marketplace_root / "plugins" / plugin)
    ctx = Context(
        marketplace_root=marketplace_root,
        # Placeholders keep plugin scope available during a source-independent
        # uninstall. Steps ignore missing sources, while guidance, hooks,
        # marketplace entries, and receipt-owned trees can still be retired.
        plugin_dirs=tuple(dict.fromkeys(named.values())),
        home=home or Path.home(),
        settings=resolved,
        force=force,
    )
    findings: list[status.Finding] = []
    if mode is not Mode.UNINSTALL:
        for name, claims in skills.duplicate_names(named).items():
            findings.append(status.Finding(
                "skill names",
                status.Level.BROKEN,
                f"{name}: {', '.join(claims)}",
                "select only one plugin that ships this skill name",
            ))
        for harness in selected:
            platform = getattr(harness, "platform", harness)
            placement = steps._placement(ctx, getattr(harness, "name", ""))
            problems = skills.unsatisfiable((platform,), placement)
            findings.extend(
                status.Finding(
                    "skill placement", status.Level.BROKEN, problem,
                    "use --skill-placement auto or both",
                )
                for problem in problems
            )
            if problems:
                continue
            _, planned = skills.skill_plan(
                platform,
                ctx,
                named,
                placement=placement,
                packaged_native=isinstance(
                    getattr(platform, "native_skills", None),
                    (ExtensionSkills, PluginPackageSkills),
                ),
            )
            if planned.refused:
                findings.append(status.Finding(
                    "skill placement",
                    status.Level.WARN,
                    f"{getattr(harness, 'name', '?')}: preserved conflicting "
                    f"user paths for {', '.join(planned.refused)}",
                ))
        if any(finding.level is status.Level.BROKEN for finding in findings):
            return Result(mode, findings=tuple(findings), missing=missing)
    step_table = steps.STEPS if step_table is None else step_table
    paired = targets(selected, step_table)

    registrations: list[Outcome] = []
    notes: list[str] = []
    with steps.prepared(
        ctx, plugins=named, harnesses=selected, step_table=step_table
    ) as staged:
        findings.extend(
            status.Finding("extension staging", status.Level.BROKEN, str(failure))
            for failure in staged.settings.get("_staging_failures", ())
        )
        if any(finding.level is status.Level.BROKEN for finding in findings):
            return Result(mode, findings=tuple(findings), missing=missing)
        marketplaces = tuple(
            marketplace
            for harness in selected
            for marketplace in steps.marketplaces_for(
                harness, staged, removing=mode is Mode.UNINSTALL
            )
        )
        # Uninstall tells the harness first: removing the files under a
        # registration leaves the harness caching a path that no longer exists.
        if mode is Mode.UNINSTALL:
            registrations.extend(
                _registrations(
                    selected, named, marketplace_root, market, staged,
                    removing=True, run=run_command, available=available,
                )
            )
            try:
                for harness in selected:
                    notes.extend(steps.apply_hooks(
                        steps.hooks_for(harness, staged, removing=True), mode
                    ))
                notes.extend(steps.apply_marketplaces(marketplaces, mode))
            except (OSError, ValueError) as error:
                findings.append(status.Finding(
                    "Codex withdrawal", status.Level.BROKEN, str(error),
                    "repair the user-owned JSON file, then retry uninstall",
                ))
                return Result(
                    mode, notes=tuple(notes), registrations=tuple(registrations),
                    findings=tuple(findings), missing=(),
                )

        decisions = run(paired, staged, mode)
        claimed = [decision.target for decision in decisions]
        stale = retirements(
            _config_roots(selected, staged),
            () if mode is Mode.UNINSTALL else claimed,
            plugins=named,
        )
        decisions.extend(run((), staged, mode, extra=stale))

        for harness in selected:
            notes.extend(steps.apply_regions(
                steps.regions_for(
                    harness, staged, removing=mode is Mode.UNINSTALL
                ),
                mode,
            ))
            if mode is not Mode.UNINSTALL:
                try:
                    notes.extend(steps.apply_hooks(
                        steps.hooks_for(harness, staged), mode
                    ))
                except (OSError, ValueError) as error:
                    findings.append(status.Finding(
                        "Codex hooks", status.Level.BROKEN, str(error),
                        "repair the user-owned hooks file, then retry",
                    ))

        if mode is not Mode.UNINSTALL:
            try:
                notes.extend(steps.apply_marketplaces(marketplaces, mode))
            except (OSError, ValueError) as error:
                findings.append(status.Finding(
                    "Codex marketplace", status.Level.BROKEN, str(error),
                    "resolve the conflicting marketplace entry, then retry",
                ))

        if mode is Mode.INSTALL and not any(
            finding.level is status.Level.BROKEN for finding in findings
        ):
            registrations.extend(
                _registrations(
                    selected, named, marketplace_root, market, staged,
                    removing=False, run=run_command, available=available,
                )
            )

    torn = (
        teardown.teardown(_config_roots(selected, ctx), state_dir=state_dir)
        if mode is Mode.UNINSTALL and teardown_enabled
        else None
    )
    return Result(
        mode=mode,
        decisions=tuple(decisions),
        notes=tuple(notes),
        registrations=tuple(registrations),
        findings=tuple(findings),
        torn_down=torn,
        missing=() if mode is Mode.UNINSTALL else missing,
    )


def _registrations(
    harnesses: Sequence[object],
    plugins: Mapping[str, Path],
    root: Path,
    market: str,
    ctx: Context,
    *,
    removing: bool,
    run: Runner,
    available: Iterable[str] | None,
) -> list[Outcome]:
    """Tell each harness, for each plugin. Never raises into the walk's result."""
    staged_all = ctx.settings.get("_staged_extensions") or {}
    done: list[Outcome] = []
    for harness in harnesses:
        name = getattr(harness, "name", "")
        platform = getattr(harness, "platform", harness)
        flavor = (
            getattr(platform, "install_flavor", "")
            or getattr(platform, "name", "")
        )
        variants = ctx.settings.get("_registration_variants", {})
        registration_name = (
            str(variants.get(name, name)) if isinstance(variants, Mapping) else name
        )
        markets = ctx.settings.get("_registration_markets", {})
        registration_market = (
            str(markets.get(name, market)) if isinstance(markets, Mapping) else market
        )
        staged = staged_all.get(name, {}) if isinstance(staged_all, Mapping) else {}
        custom_entries = ctx.settings.get("_registrations", {})
        custom_entry = (
            custom_entries.get(name) if isinstance(custom_entries, Mapping) else None
        )
        registration_plugins = plugins
        if not removing and flavor == "codex":
            staged_codex = ctx.settings.get("_staged_codex", {})
            registration_plugins = {
                plugin: directory
                for plugin, directory in plugins.items()
                if (
                    isinstance(staged_codex, Mapping)
                    and plugin in staged_codex
                )
                or (directory / ".codex-plugin" / "plugin.json").is_file()
            }
        elif not removing and getattr(platform, "extensions_subdir", ""):
            registration_plugins = {
                plugin: directory
                for plugin, directory in plugins.items()
                if isinstance(staged, Mapping) and plugin in staged
            }
        for plugin in registration_plugins:
            values = _values(
                root, plugin, registration_market,
                staged.get(plugin) if isinstance(staged, Mapping) else None,
            )
            if ctx.force and not removing:
                if isinstance(custom_entry, registration.Registration):
                    done.extend(registration.withdraw_entry(
                        custom_entry, values, run=run, available=available, label=name
                    ))
                else:
                    done.extend(registration.withdraw(
                        registration_name, values, run=run, available=available
                    ))
            if isinstance(custom_entry, registration.Registration):
                if removing:
                    done.extend(registration.withdraw_entry(
                        custom_entry, values, run=run, available=available, label=name
                    ))
                else:
                    done.extend(registration.register_entry(
                        custom_entry, values, run=run, available=available,
                        label=name, force=ctx.force,
                    ))
            else:
                if removing:
                    done.extend(registration.withdraw(
                        registration_name, values, run=run, available=available
                    ))
                else:
                    done.extend(registration.register(
                        registration_name, values, run=run, available=available,
                        force=ctx.force,
                    ))
        # Companions are separate products. Installing one when requested does
        # not authorize removing it as a side effect of uninstalling autorun.
        wanted = [] if removing else _companions_wanted(ctx)
        if wanted:
            plugin = next(iter(plugins), "")
            values = _values(
                root, plugin, registration_market,
                staged.get(plugin) if isinstance(staged, Mapping) else None,
            )
            for outcomes in registration.companions(
                name, wanted, values, removing=removing, run=run, available=available,
            ).values():
                done.extend(outcomes)
    return done


def _companions_wanted(ctx: Context) -> list[str]:
    """Which optional products this run was asked to install."""
    return ["conductor"] if ctx.settings.get("conductor", True) else []


def _config_roots(harnesses: Iterable[object], ctx: Context) -> list[Path]:
    """Every config directory this run touched, for the lock sweep."""
    found = []
    for harness in harnesses:
        platform = getattr(harness, "platform", harness)
        base = discovery.config_dir(platform, home=ctx.home)
        if base is not None:
            found.append(base)
        if (
            getattr(platform, "install_flavor", "")
            or getattr(platform, "name", "")
        ) == "codex":
            found.append(discovery.codex_plugin_source(home=ctx.home).parent)
    found.append(discovery.shared_root(home=ctx.home).parent)
    return found


def install(**kwargs) -> Result:
    """Install. Returns what happened rather than printing it."""
    return perform(Mode.INSTALL, **kwargs)


def uninstall(**kwargs) -> Result:
    """Remove what autorun published, and say what it kept."""
    return perform(Mode.UNINSTALL, **kwargs)


def preview(**kwargs) -> Result:
    """Status and dry run, which are the same question asked the same way."""
    return perform(Mode.PREVIEW, **kwargs)


def demo() -> None:
    """Self-check: order, idempotence, and a preview that spawns nothing."""
    import tempfile

    calls: list[tuple[str, ...]] = []

    def record(argv):
        import subprocess

        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    import os

    root = discovery.marketplace_root(Path(__file__).resolve())
    assert root is not None, "this package must be able to find its own marketplace"

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        home.mkdir()
        # $HOME is the seam, and redirecting it is the whole isolation
        # mechanism. Passing `home=` while leaving $HOME alone reads a sandbox
        # and writes the real one; an earlier version of this self-check did
        # exactly that and uninstalled skills from a live machine. `Context`
        # now refuses the mismatch, and this is the correct form.
        previous = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        try:
            _exercise(root, home, record, calls)
        finally:
            if previous is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = previous

    print("installer.orchestrate: all self-checks passed")


def _exercise(root: Path, home: Path, record: Runner, calls: list) -> None:
    """The self-check body, with ``$HOME`` already redirected by the caller."""
    common = dict(
    marketplace_root=root, plugins=["ar"], home=home,
    run_command=record, available=(), settings={"skill_placement": {"": "auto"}},
    )

    # A preview writes nothing and spawns nothing.
    before = sorted(p.name for p in home.rglob("*"))
    seen = preview(**common)
    assert seen.decisions, "a preview that decides nothing proves nothing"
    assert sorted(p.name for p in home.rglob("*")) == before
    assert calls == [], "a preview must not run a harness CLI"

    # Install lands files, and a second one changes nothing.
    first = install(**common)
    assert any(d.verdict is Verdict.PUBLISH for d in first.decisions)
    second = install(**common)
    assert all(d.verdict is Verdict.SKIP for d in second.decisions), "not idempotent"

    # Uninstall removes ours and reports what it kept.
    state = home / ".autorun"
    state.mkdir()
    gone = uninstall(**common, state_dir=state)
    assert any(d.verdict is Verdict.RETIRE for d in gone.decisions)
    assert gone.torn_down is not None and gone.torn_down.kept == state
    assert any("session state" in line for line in gone.lines())

    # An unknown plugin is named rather than counted.
    nothing = install(marketplace_root=root, plugins=["nosuchplugin"], home=home)
    assert nothing.missing == ("nosuchplugin",) and not nothing.ok
    assert "nosuchplugin" in " ".join(nothing.lines())


if __name__ == "__main__":
    demo()
