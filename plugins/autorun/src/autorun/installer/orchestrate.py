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

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from . import claude, codex, discovery, extension, memory, registration, skills, status, steps, teardown
from ..platforms import PLATFORMS, ExtensionSkills, PluginPackageSkills
from .fs import Verdict, owns, preserved_paths, read_marker, record_tree
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


def _preflight_user_files(
    harnesses: Sequence[object],
    ctx: Context,
    marketplaces: Sequence[steps.Marketplace],
    *,
    removing: bool,
) -> tuple[status.Finding, ...]:
    """Validate every shared user file before the first durable mutation."""
    failures: list[status.Finding] = []

    def check(label: str, action) -> None:
        try:
            action()
        except (OSError, ValueError) as error:
            failures.append(
                status.Finding(
                    "installer preflight",
                    status.Level.BROKEN,
                    f"{label}: {error}",
                    "repair the user-owned file, then retry",
                )
            )

    for harness in harnesses:
        for region in steps.regions_for(harness, ctx, removing=removing):
            check(region.describe(), lambda region=region: memory.validate(region.path, region.block))
        for hooks in steps.hooks_for(harness, ctx, removing=removing):
            check(hooks.describe(), lambda hooks=hooks: codex.validate_hooks(hooks.path))
    for marketplace in marketplaces:
        expected = None if removing else marketplace.entry
        check(
            marketplace.describe(),
            lambda marketplace=marketplace, expected=expected: codex.validate_marketplace(
                marketplace.path, expected
            ),
        )
    return tuple(failures)


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
    resolved["_available_binaries"] = (
        tuple(available) if available is not None else None
    )
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
        home=home if home is not None else discovery.process_home(),
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
        findings.extend(
            _preflight_user_files(
                selected,
                staged,
                marketplaces,
                removing=mode is Mode.UNINSTALL,
            )
        )
        if any(finding.level is status.Level.BROKEN for finding in findings):
            return Result(mode, findings=tuple(findings), missing=missing)
        # Uninstall tells the harness first: removing the files under a
        # registration leaves the harness caching a path that no longer exists.
        if mode is Mode.UNINSTALL:
            registrations.extend(
                _registrations(
                    selected, named, marketplace_root, market, staged,
                    removing=True, run=run_command, available=available,
                )
            )
            if any(not outcome.ok for outcome in registrations):
                return Result(
                    mode,
                    registrations=tuple(registrations),
                    findings=tuple(findings),
                    missing=(),
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

        materializations = tuple(
            steps.extension_materialization_intents(selected, staged)
        )
        decisions = []
        if mode is Mode.UNINSTALL:
            decisions.extend(run((), staged, mode, extra=materializations))
        decisions.extend(run(paired, staged, mode))
        if mode is not Mode.UNINSTALL:
            decisions.extend(run((), staged, mode, extra=materializations))
        claimed = [decision.target for decision in decisions]
        if mode is not Mode.UNINSTALL:
            # A live native CLI owns these links/copies, but retirement still
            # needs to know they are current. Otherwise a healthy copied
            # extension is classified as an orphan and deleted before the CLI
            # gets its registration call.
            claimed.extend(
                steps.extension_materialization_targets(selected, staged)
            )
            # Every skill a selected plugin ships is claimed at the shared
            # root even when no selected harness routes there (a Claude-only
            # run publishes nothing to ~/.agents/skills). The sweep below
            # would otherwise read those trees as "no longer shipped" and
            # retire the copies every other harness reads.
            shared_root = discovery.shared_root(home=staged.home)
            claimed.extend(
                shared_root / name
                for plugin_dir in named.values()
                for name in skills.shippable_skills(plugin_dir)
            )
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
            extension_target = (
                extension.extension_dir(ctx, platform, plugin)
                if getattr(platform, "extensions_subdir", "")
                else None
            )
            extension_source = (
                extension.registration_source_dir(ctx, name, plugin)
                if extension_target is not None
                else None
            )
            target_present = bool(
                extension_target is not None
                and (extension_target.exists() or extension_target.is_symlink())
            )
            registration_owned = (
                _registration_owned(
                    platform,
                    plugin,
                    registration_market,
                    named=plugins,
                    ctx=ctx,
                )
                if target_present or removing
                else False
            )
            if removing and not registration_owned:
                continue
            if not removing and target_present and not registration_owned:
                # A same-name native extension with no exact marker/receipt is
                # the user's. Force never broadens ownership.
                continue
            values = _values(
                root,
                plugin,
                registration_market,
                extension_source or (
                    staged.get(plugin) if isinstance(staged, Mapping) else None
                ),
            )
            needs_reinstall = bool(
                not removing
                and target_present
                and extension_source is not None
                and not extension.materialization_tracks_source(
                    extension_target, extension_source
                )
                and not extension.materialization_matches_source(
                    extension_target, extension_source
                )
            )
            force_registration = ctx.force or needs_reinstall
            allow_fallback = flavor != "antigravity" or _registration_owned(
                PLATFORMS["gemini"],
                plugin,
                registration_market,
                named=plugins,
                ctx=ctx,
            )
            rollback_paths: list[Path] = []
            if force_registration and target_present and registration_owned:
                rollback_paths.append(extension_target)
                if flavor == "antigravity":
                    base = discovery.config_dir(platform, home=ctx.home)
                    if base is not None:
                        rollback_paths.append(base / "import_manifest.json")
            with preserved_paths(rollback_paths) as commit_registration:
                if (
                    force_registration
                    and not removing
                    and (registration_owned or not target_present)
                ):
                    if isinstance(custom_entry, registration.Registration):
                        withdrawn = registration.withdraw_entry(
                            custom_entry,
                            values,
                            run=run,
                            available=available,
                            label=name,
                        )
                    else:
                        withdrawn = registration.withdraw(
                            registration_name,
                            values,
                            run=run,
                            available=available,
                        )
                    done.extend(withdrawn)
                    if any(not outcome.ok for outcome in withdrawn):
                        continue
                if isinstance(custom_entry, registration.Registration):
                    if removing:
                        outcomes = registration.withdraw_entry(
                            custom_entry,
                            values,
                            run=run,
                            available=available,
                            label=name,
                        )
                    else:
                        outcomes = registration.register_entry(
                            custom_entry,
                            values,
                            run=run,
                            available=available,
                            label=name,
                            force=force_registration,
                            allow_fallback=allow_fallback,
                        )
                else:
                    if removing:
                        outcomes = registration.withdraw(
                            registration_name,
                            values,
                            run=run,
                            available=available,
                        )
                    else:
                        outcomes = registration.register(
                            registration_name,
                            values,
                            run=run,
                            available=available,
                            force=force_registration,
                            allow_fallback=allow_fallback,
                        )
                if (
                    not removing
                    and flavor == "claude"
                    and outcomes
                ):
                    source = registration_plugins.get(plugin)
                    if source is not None:
                        failed = next(
                            (outcome.detail for outcome in outcomes if not outcome.ok), ""
                        )
                        cached = None
                        filled = None
                        try:
                            cached = claude.cache_dir(
                                platform,
                                market=registration_market,
                                plugin=plugin,
                                version=steps._version(source),
                                home=ctx.home,
                            )
                            if cached is not None and not cached.is_dir():
                                filled = claude.cache_fallback(
                                    source,
                                    platform,
                                    market=registration_market,
                                    plugin=plugin,
                                    version=steps._version(source),
                                    home=ctx.home,
                                )
                                if filled is not None:
                                    cached = filled
                            if cached is not None:
                                claude.substitute_root(cached)
                        except Exception as error:
                            cache_failure = Outcome(
                                f"{name}: cache fallback",
                                False,
                                "; ".join(filter(None, (
                                    failed, f"{type(error).__name__}: {error}"
                                ))),
                            )
                            outcomes = (cache_failure,) if failed else (*outcomes, cache_failure)
                        else:
                            if failed and filled is not None:
                                outcomes = (Outcome(
                                    f"{name}: cache fallback", True, failed
                                ),)
                done.extend(outcomes)
                if outcomes and all(outcome.ok for outcome in outcomes):
                    commit_registration()
                used_import_fallback = any(
                    outcome.step.endswith("native registration fallback")
                    for outcome in outcomes
                )
                if (
                    not removing
                    and outcomes
                    and all(outcome.ok for outcome in outcomes)
                    and extension_target is not None
                    and extension_source is not None
                    and (
                        extension.native_receipt_names_source(
                            ctx,
                            platform,
                            plugin,
                            extension_target,
                            extension_source,
                        )
                        or (
                            used_import_fallback
                            and extension.antigravity_receipt_names_plugin(
                                ctx, platform, plugin
                            )
                        )
                    )
                ):
                    record_tree(
                        extension_target,
                        plugin=plugin,
                        ownership_proof=(
                            lambda installed,
                            selected=platform,
                            plugin_name=plugin,
                            expected=extension_source:
                            (
                                extension.native_receipt_names_source(
                                    ctx,
                                    selected,
                                    plugin_name,
                                    installed,
                                    expected,
                                )
                                or (
                                    used_import_fallback
                                    and extension.antigravity_receipt_names_plugin(
                                        ctx, selected, plugin_name
                                    )
                                )
                            )
                        ),
                    )
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


def _registration_owned(
    platform: object,
    plugin: str,
    market: str,
    *,
    named: Mapping[str, Path],
    ctx: Context,
) -> bool:
    """Whether this home positively contains this harness registration.

    Executable presence is not ownership: a user can have every supported CLI
    without ever installing autorun.  Native uninstallers are allowed only
    when a cache, owned plugin/extension tree, hook entry, or marketplace entry
    proves this exact plugin was installed in the redirected home.
    """
    flavor = (
        getattr(platform, "install_flavor", "")
        or getattr(platform, "name", "")
    )
    if flavor == "claude":
        return bool(
            claude.installed_versions(
                platform,
                market=market,
                plugin=plugin,
                home=ctx.home,
            )
        )
    if flavor == "codex":
        target = discovery.codex_plugin_source(plugin, home=ctx.home)
        marker = read_marker(target)
        if marker is not None and owns(marker, plugin):
            return True
        base = discovery.config_dir(platform, home=ctx.home)
        hooks_path = base / "hooks.json" if base is not None else None
        if hooks_path is not None and hooks_path.is_file():
            try:
                document = json.loads(hooks_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                document = {}
            if any(
                codex.is_ours(entry)
                for entries in document.get("hooks", {}).values()
                if isinstance(entries, list)
                for entry in entries
            ):
                return True
        return False
    if getattr(platform, "extensions_subdir", ""):
        base = discovery.extensions_dir(platform, home=ctx.home)
        source = named.get(plugin)
        if base is None or source is None:
            return False
        durable = extension.registration_source_dir(
            ctx, getattr(platform, "name", ""), plugin
        )
        template = (
            discovery.plugin_runtime_root(source) / steps.GEMINI_TEMPLATE_SUBDIR
        )
        return extension.materialization_unchanged(
            base / plugin,
            durable,
            plugin=plugin,
            legacy_sources=(template,),
            receipt_proof=lambda installed: (
                extension.native_receipt_names_source(
                    ctx,
                    platform,
                    plugin,
                    installed,
                    durable,
                )
                or extension.receipt_names_any_source(
                    installed, (durable, template)
                )
            ),
        )
    return False


def _companions_wanted(ctx: Context) -> list[str]:
    """Which optional products this run was asked to install."""
    return ["conductor"] if ctx.settings.get("conductor", True) else []


def _config_roots(harnesses: Iterable[object], ctx: Context) -> list[Path]:
    """Every config directory this run touched, for the lock sweep.

    Only roots this selection can fully re-claim are swept: a tree the sweep
    finds unclaimed is retired as "no longer shipped", so a root that other,
    unselected harnesses still populate must stay out of the list. An empty
    selection touches nothing and sweeps nothing.
    """
    harnesses = tuple(harnesses)  # iterated twice: config dirs, then staging
    if not harnesses:
        return []
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
    selected_names = {
        getattr(harness, "name", "") for harness in harnesses
    } - {""}
    # The shared ~/.agents/skills root is populated by every harness that
    # reads it. It is swept on every run so a skill autorun stopped shipping
    # retires, but ``perform`` claims every shipped skill there regardless of
    # selection, so a Claude-only run cannot retire the trees Codex, Qwen, Pi,
    # Prime, ForgeCode, or OpenCode still load.
    found.append(discovery.shared_root(home=ctx.home).parent)
    source_root = ctx.settings.get("_extension_source_root")
    staging = (
        Path(str(source_root))
        if source_root
        else ctx.home / ".autorun" / "installer" / "extension-sources"
    )
    # The staging root holds one subtree per harness. Sweeping the whole root
    # retired Gemini's staged source during a --claude-only install: no
    # gemini step ran, nothing claimed extension-sources/gemini/ar, and the
    # retirement sweep read that absence as "no longer shipped". Scope the
    # sweep to the selected harnesses' subtrees, and keep the upgrade path by
    # also sweeping any subtree whose name no registered platform claims — a
    # harness removed from the registry must not leak its staging forever.
    found.extend(staging / name for name in sorted(selected_names))
    try:
        found.extend(
            child
            for child in staging.iterdir()
            if child.is_dir()
            and child.name not in PLATFORMS
            and child.name not in selected_names
        )
    except OSError:
        pass
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
        # discovery.redirected_home, not a hand-rolled save/restore: it moves
        # HOME and USERPROFILE together, and Path.home() reads the second one
        # on Windows, so redirecting only the first isolates on one platform.
        with discovery.redirected_home(home):
            _exercise(root, home, record, calls)

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
