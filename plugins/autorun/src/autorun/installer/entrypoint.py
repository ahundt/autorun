"""Public installer commands composed from the manifest-driven engine."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable, Mapping, Sequence

from ..platforms import PLATFORMS
from . import (
    discovery,
    extension,
    memory,
    orchestrate,
    registration,
    runtime,
    settings,
    status,
    steps,
)
from .fs import Verdict

__all__ = [
    "install_plugins",
    "uninstall_plugins",
    "show_status",
    "perform_self_update",
    "install_main",
    "parse_selection",
    "resolve_custom_harnesses",
]

_FALLBACK_PLUGINS = ("ar", "pdf-extractor")
_ALIASES = {"autorun": "ar"}
_run = runtime._spawn

_UV_TOOL_SCRIPTS = {
    "autorun": "autorun",
    "autorun-pdf-extractor": "extract-pdfs",
    "pdf-extractor": "extract-pdfs",
}

# Plugin id -> distributions, the one we install first and retired names after.
#
# `pdf-extractor` is absent on purpose. It is a plugin in every harness — its
# own plugin.json, commands and skill — but it is not a Python distribution:
# `pdf_extraction` and the `extract-pdfs` script ship inside `autorun` under the
# `pdf` extra. So installing or removing that plugin moves harness files only,
# and must never reach a package.
#
# The two trailing names are distributions earlier versions really did install
# and nothing else can remove. A retired distribution leaves no ownership marker
# for `traversal.retirements` to sweep, and its `extract-pdfs` entry point loses
# the shared name to whichever tool installed last, so the environment is
# invisible from the CLI while still occupying its full size on disk.
_PLUGIN_DISTRIBUTIONS = {
    "ar": ("autorun", "autorun-pdf-extractor", "pdf-extractor"),
}


def _marketplace_root() -> Path:
    """Current source root, or a stable placeholder for receipt-only removal."""
    found = discovery.marketplace_root()
    if found is not None:
        return found
    here = Path(__file__).resolve()
    return next(
        (parent for parent in here.parents if discovery.is_marketplace_root(parent)),
        here.parents[3],
    )


def _plugin_names(root: Path) -> tuple[str, ...]:
    """Installable names from the marketplace manifest, with offline fallback."""
    try:
        document = json.loads((root / discovery.MARKETPLACE_MANIFEST).read_text(encoding="utf-8"))
        names = tuple(entry["name"] for entry in document.get("plugins", ()) if isinstance(entry, Mapping) and isinstance(entry.get("name"), str))
    except (OSError, AttributeError, ValueError):
        names = ()
    return tuple(dict.fromkeys(names)) if names else _FALLBACK_PLUGINS


def parse_selection(root: Path, selection: str = "all") -> tuple[str, ...]:
    """Normalize one comma-separated selection against the live manifest."""
    known = _plugin_names(root)
    if not selection or selection == "all":
        return known
    selected = tuple(dict.fromkeys(_ALIASES.get(token, token) for token in (part.strip() for part in selection.split(",")) if token))
    unknown = tuple(name for name in selected if name not in known)
    if unknown:
        raise ValueError(f"unknown plugin(s): {', '.join(unknown)}; expected {', '.join(known)}")
    return selected


def resolve_custom_harnesses(
    cli_specs: Sequence[str] = (),
) -> tuple[settings.CustomHarness, ...]:
    """Merge persisted custom targets with same-name CLI overrides."""
    configured = settings.CUSTOM_HARNESS.resolve(None).value
    explicit = settings.CUSTOM_HARNESS.resolve(cli_specs, config={}).value if cli_specs else ()
    merged = {item.name: item for item in configured}
    merged.update({item.name: item for item in explicit})
    return tuple(merged.values())


def _resolved_settings(
    *,
    tool: bool | None = None,
    conductor: bool | None = None,
    codex_hook_source: str | None = None,
    codex_plugin_marketplace: str | None = None,
    claude_agents_skills: str | None = None,
    skill_placement: object = None,
    custom_harnesses: Sequence[str] = (),
) -> tuple[dict[str, object], tuple[settings.CustomHarness, ...]]:
    cli = SimpleNamespace(
        tool=tool,
        conductor=conductor,
        codex_hook_source=codex_hook_source,
        codex_plugin_marketplace=codex_plugin_marketplace,
        shared_skills_bridge=claude_agents_skills,
        skill_placement=skill_placement,
        custom_harness=None,
    )
    resolved = {
        declaration.name: declaration.resolve(getattr(cli, declaration.name, None)).value
        for declaration in settings.INSTALL_SETTINGS
        if declaration is not settings.CUSTOM_HARNESS
    }
    custom = resolve_custom_harnesses(custom_harnesses)
    resolved["custom_harness"] = custom
    return resolved, custom


def _refreshable(platform: object, root: Path, plugins: Sequence[str]) -> bool:
    """Whether an absent CLI still has an autorun-owned extension to refresh."""
    base = discovery.extensions_dir(platform)
    if base is None:
        return False
    directories, _ = discovery.resolve_plugins(root, plugins)
    return any(
        extension.refreshable(
            base / discovery.plugin_name(plugin_dir),
            discovery.plugin_runtime_root(plugin_dir) / steps.GEMINI_TEMPLATE_SUBDIR,
            plugin=discovery.plugin_name(plugin_dir),
        )
        for plugin_dir in directories
    )


def _harnesses(
    root: Path,
    plugins: Sequence[str],
    custom: Sequence[settings.CustomHarness],
    *,
    claude: bool = False,
    gemini: bool = False,
    codex_only: bool = False,
    antigravity: bool = False,
    qwen: bool = False,
    pi: bool = False,
    uninstalling: bool = False,
) -> tuple[tuple[object, ...], Mapping[str, tuple], tuple[str, ...], tuple[str, ...]]:
    available = tuple(
        binary
        for binary in dict.fromkeys(
            (
                *(platform.binary for platform in PLATFORMS.values()),
                *(spec.binary for spec in custom),
            )
        )
        if shutil.which(binary)
    )
    requested = tuple(
        name
        for name, chosen in (
            ("claude", claude),
            ("gemini", gemini),
            ("codex", codex_only),
            ("antigravity", antigravity),
            ("qwen", qwen),
            ("pi", pi),
        )
        if chosen
    )
    refreshable = {
        name: _refreshable(PLATFORMS[name], root, plugins)
        for name in requested
    }
    missing = tuple(
        name
        for name in requested
        if PLATFORMS[name].binary not in available and not refreshable[name]
    )
    if uninstalling:
        selected = list(PLATFORMS.values())
        missing = ()
    elif requested:
        selected = [PLATFORMS[name] for name in requested if name not in missing]
    else:
        selected = [
            platform
            for platform in PLATFORMS.values()
            if (
                (platform.install_by_default and platform.binary in available)
                or _refreshable(platform, root, plugins)
            )
        ]

    table: Mapping[str, tuple] = steps.STEPS
    seen = {getattr(platform, "name", "") for platform in selected}
    for spec in custom:
        if spec.name in seen:
            raise ValueError(f"custom harness {spec.name!r} duplicates a registered harness")
        platform = settings.synthesize(spec)
        selected.append(platform)
        seen.add(spec.name)
        table = settings.steps_for_custom(spec, table)
    return tuple(selected), table, available, missing


def _guidance(
    root: Path,
    harnesses: Iterable[object],
    custom: Sequence[settings.CustomHarness],
    *,
    required: bool = True,
) -> dict[str, str]:
    """Resolved per-harness memory bodies from each registry template."""
    plugin = discovery.plugin_dir(root, "ar")
    if plugin is None:
        return {}
    base = discovery.plugin_runtime_root(plugin)
    custom_by_name = {item.name: item for item in custom}
    bodies = {}
    for platform in harnesses:
        relative = str(getattr(platform, "memory_template", "") or "")
        source = base / relative
        if not relative:
            continue
        if not source.is_file():
            if required:
                raise ValueError(f"{getattr(platform, 'name', '?')} guidance template not found: {source}")
            continue
        body = source.read_text(encoding="utf-8")
        if spec := custom_by_name.get(getattr(platform, "name", "")):
            original = PLATFORMS["forgecode" if spec.flavor == "claude" else spec.flavor].display_name
            body = body.replace(original, spec.display_name)
        bodies[getattr(platform, "name", "")] = body
    return bodies


def _runtime_settings(
    root: Path,
    resolved: dict[str, object],
    harnesses: Sequence[object],
    custom: Sequence[settings.CustomHarness],
    *,
    require_guidance: bool = True,
) -> Path | None:
    """Add generated command, guidance, registration, and socket inputs."""
    from ..ipc import _get_autorun_config_dir

    resolved["_extension_source_root"] = (
        _get_autorun_config_dir() / "installer" / "extension-sources"
    )
    plugin = discovery.plugin_dir(root, "ar")
    if plugin is None:
        return None
    no_sync = bool(resolved.get("hook_no_sync", True))
    python = str(resolved.get("hook_python", "") or "")
    command = runtime.hook_command(
        plugin,
        cli="codex",
        python=python,
        no_sync=no_sync,
    )
    plugin_command = (
        runtime.UvCommand(
            project=Path("${CLAUDE_PLUGIN_ROOT}"),
            script=Path("${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py"),
            args=("--cli", "codex"),
            python=python,
            no_sync=no_sync,
        )
        .shell()
        .replace("'${CLAUDE_PLUGIN_ROOT}'", "${CLAUDE_PLUGIN_ROOT}")
    )
    resolved.update(
        _hook_command=runtime.hook_command(
            plugin,
            cli="opencode",
            python=python,
            no_sync=no_sync,
        ).argv(),
        _pi_hook_command=runtime.hook_command(
            plugin,
            cli="pi",
            python=python,
            no_sync=no_sync,
        ).argv(),
        _codex_hook_command=command.shell(),
        _codex_plugin_hook_command=plugin_command,
        _extension_hook_commands={
            getattr(harness, "name", ""): runtime.hook_command(
                plugin,
                cli=(
                    getattr(
                        getattr(harness, "platform", harness),
                        "install_flavor",
                        "",
                    )
                    or getattr(harness, "name", "gemini")
                ),
                python=python,
                no_sync=no_sync,
            ).shell()
            for harness in harnesses
            if getattr(
                getattr(harness, "platform", harness),
                "extensions_subdir",
                "",
            )
        },
        _daemon_socket=str(_get_autorun_config_dir() / "daemon.sock"),
        # Where AF_UNIX does not exist the daemon publishes a port here
        # instead; the shim picks whichever one is present.
        _daemon_port_file=str(_get_autorun_config_dir() / "daemon.port"),
        _guidance=_guidance(root, harnesses, custom, required=require_guidance),
    )
    return plugin


def _state_dir() -> Path:
    from ..ipc import _get_autorun_config_dir

    return _get_autorun_config_dir()


def _print_result(result: orchestrate.Result, *, verbose: bool = False) -> None:
    for line in result.lines(verbose=verbose):
        print(line)


def _package_receipt(package: str) -> Path:
    return _state_dir() / "package-installs" / f"{package}.json"


def _remove_distribution(package: str) -> runtime.Outcome | None:
    """Remove one distribution this home installed, or ``None`` if it owns none.

    Shared by uninstall and by the install-time legacy sweep so the two cannot
    disagree about what "installed by us" means.
    """
    uv_owned = bool(shutil.which("uv")) and _uv_tool_installed(package)
    pip_owned = _pip_package_owned(package)
    if not uv_owned and not pip_owned:
        return None
    argv = (
        ("uv", "tool", "uninstall", package)
        if uv_owned
        else (sys.executable, "-m", "pip", "uninstall", "-y", package)
    )
    try:
        removed = _run(argv)
    except (OSError, subprocess.SubprocessError) as error:
        return runtime.Outcome(f"{package} CLI", False, str(error))
    text = removed.stderr or removed.stdout
    absent = "not installed" in text.lower() or "not found" in text.lower()
    ok = removed.returncode == 0 or absent
    if ok and pip_owned:
        _package_receipt(package).unlink(missing_ok=True)
    return runtime.Outcome(
        f"{package} CLI", ok, "" if ok else runtime._first_line(text)
    )


def _retire_legacy_distributions(plugin: str) -> list[runtime.Outcome]:
    """Remove the distributions this plugin used to ship under another name.

    ``traversal.retirements`` sweeps trees a previous version wrote somewhere
    the current one no longer visits. A renamed *distribution* is the same
    upgrade obligation with none of the same machinery: nothing publishes the
    old package, nothing claims it, and no ownership marker exists to sweep, so
    it stays installed forever. Its console script is worse than dead — uv hands
    the shared name to whichever tool installed last, so the orphan is invisible
    from the CLI while still occupying its full environment on disk.

    Order matters. uv deletes an entry point when the tool that recorded it goes
    away, so retiring the old name *after* installing the new one removes the
    working script; this runs first and lets the install create it cleanly.

    A failure here is reported, not fatal: an orphaned environment wastes disk
    but does not break the install that follows it.
    """
    outcomes = [
        _remove_distribution(package)
        for package in _PLUGIN_DISTRIBUTIONS.get(plugin, ())[1:]
    ]
    return [outcome for outcome in outcomes if outcome is not None]


def _pip_package_owned(package: str) -> bool:
    """Whether this autorun home recorded installing ``package`` with pip."""
    try:
        receipt = json.loads(_package_receipt(package).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        receipt.get("distribution") == package
        and receipt.get("installer") == "pip"
        and Path(str(receipt.get("python", ""))).resolve()
        == Path(sys.executable).resolve()
    )


def install_plugins(
    selection: str = "all",
    *,
    tool: bool | None = None,
    force: bool = False,
    claude_only: bool = False,
    gemini_only: bool = False,
    codex_only: bool = False,
    antigravity_only: bool = False,
    qwen_only: bool = False,
    pi_only: bool = False,
    conductor: bool | None = None,
    codex_hook_source: str | None = None,
    codex_plugin_marketplace: str | None = None,
    claude_agents_skills: str | None = None,
    skill_placement: object = None,
    custom_harnesses: Sequence[str] = (),
    dry_run: bool = False,
) -> int:
    """Install selected plugins through one manifest traversal."""
    if message := discovery.python_too_old():
        print(message)
        return 1
    root = _marketplace_root()
    try:
        plugins = parse_selection(root, selection)
        resolved, custom = _resolved_settings(
            tool=tool,
            conductor=conductor,
            codex_hook_source=codex_hook_source,
            codex_plugin_marketplace=codex_plugin_marketplace,
            claude_agents_skills=claude_agents_skills,
            skill_placement=skill_placement,
            custom_harnesses=custom_harnesses,
        )
        harnesses, table, available, missing = _harnesses(
            root,
            plugins,
            custom,
            claude=claude_only,
            gemini=gemini_only,
            codex_only=codex_only,
            antigravity=antigravity_only,
            qwen=qwen_only,
            pi=pi_only,
        )
    except ValueError as error:
        print(f"Error: {error}")
        return 1
    if missing:
        print(f"Target CLI not found: {', '.join(missing)}")
        return 1
    if not harnesses:
        print("No maintained target CLI is installed; choose an explicit target or custom harness.")
        return 1
    try:
        plugin = _runtime_settings(root, resolved, harnesses, custom)
    except ValueError as error:
        print(f"Error: {error}")
        return 1
    resolved["_registrations"] = {
        spec.name: registration.with_binary(registration.REGISTRATIONS[spec.flavor], spec.binary)
        for spec in custom
        if spec.flavor in registration.REGISTRATIONS and getattr(next(h for h in harnesses if getattr(h, "name", "") == spec.name), "extensions_subdir", "")
    }

    if not dry_run:
        if "ar" in plugins:
            if plugin is None:
                print("Error: autorun plugin source was not found")
                return 1
            for retired in _retire_legacy_distributions("ar"):
                print(retired.describe())
            boot = runtime.bootstrap(
                plugin,
                uv_tool_env="/uv/tools/" in str(Path(sys.executable)),
                install_tool=bool(resolved["tool"]),
                run=_run,
            )
            for outcome in boot:
                print(outcome.describe())
            if not all(outcome.ok for outcome in boot):
                return 1
        if bool(resolved.get("write_source_metadata")):
            from .. import __commit__, __version__

            for directory in discovery.resolve_plugins(root, plugins)[0]:
                status.write_metadata(
                    directory,
                    status.metadata_document(__version__, commit=__commit__, env={"SOURCE_DATE_EPOCH": os.environ.get("SOURCE_DATE_EPOCH", "")}),
                    allowed=True,
                )

    call = orchestrate.preview if dry_run else orchestrate.install
    result = call(
        marketplace_root=root,
        plugins=plugins,
        settings=resolved,
        harnesses=harnesses,
        run_command=_run,
        available=available,
        state_dir=_state_dir(),
        step_table=table,
        force=force,
    )
    _print_result(result, verbose=dry_run)
    ok = result.ok
    if (
        not dry_run
        and ok
        and "ar" in plugins
        and os.environ.get("AUTORUN_USE_DAEMON", "1") != "0"
    ):
        restarted = runtime.restart_daemon(run=_run)
        print(restarted.describe())
        ok = restarted.ok
    return 0 if ok else 1


def uninstall_plugins(selection: str = "all") -> int:
    """Remove exactly the selected plugins, even when their source is gone."""
    root = _marketplace_root()
    try:
        plugins = parse_selection(root, selection)
        resolved, custom = _resolved_settings()
        harnesses, table, available, _ = _harnesses(root, plugins, custom, uninstalling=True)
    except ValueError as error:
        print(f"Error: {error}")
        return 1
    _runtime_settings(root, resolved, harnesses, custom, require_guidance=False)
    result = orchestrate.uninstall(
        marketplace_root=root,
        plugins=plugins,
        settings=resolved,
        harnesses=harnesses,
        run_command=_run,
        available=available,
        state_dir=_state_dir(),
        step_table=table,
        teardown_enabled="ar" in plugins,
    )
    _print_result(result)
    ok = result.ok
    for plugin, package in (
        (name, distribution)
        for name, distributions in _PLUGIN_DISTRIBUTIONS.items()
        for distribution in distributions
    ):
        if plugin not in plugins:
            continue
        uv_owned = bool(shutil.which("uv")) and _uv_tool_installed(package)
        pip_owned = _pip_package_owned(package)
        if not uv_owned and not pip_owned:
            continue
        argv = (
            ("uv", "tool", "uninstall", package)
            if uv_owned
            else (sys.executable, "-m", "pip", "uninstall", "-y", package)
        )
        try:
            removed = _run(argv)
        except (OSError, subprocess.SubprocessError) as error:
            print(runtime.Outcome(f"{package} CLI", False, str(error)).describe())
            ok = False
            continue
        text = removed.stderr or removed.stdout
        absent = "not installed" in text.lower() or "not found" in text.lower()
        outcome = runtime.Outcome(
            f"{package} CLI",
            removed.returncode == 0 or absent,
            "" if removed.returncode == 0 or absent else runtime._first_line(text),
        )
        print(outcome.describe())
        ok = ok and outcome.ok
        if outcome.ok and pip_owned:
            _package_receipt(package).unlink(missing_ok=True)
    return 0 if ok else 1


def _uv_tool_installed(package: str) -> bool:
    """Whether the selected home positively owns this uv tool install."""
    executable = shutil.which(_UV_TOOL_SCRIPTS[package])
    configured = os.environ.get("UV_TOOL_DIR")
    if configured:
        tool_root = Path(configured)
    else:
        data = os.environ.get("XDG_DATA_HOME")
        tool_root = (
            Path(data) / "uv" / "tools"
            if data
            else discovery.process_home() / ".local" / "share" / "uv" / "tools"
        )
    expected = (tool_root / package).resolve()
    if executable:
        resolved = Path(executable).resolve()
        if resolved == expected or expected in resolved.parents:
            return True
    # A renamed distribution leaves its old environment behind and hands the
    # shared console script to the new one, so PATH can no longer reach it.
    # The environment still sits under this home's tool root, which is proof
    # enough of ownership on its own.
    return expected.is_dir()


def _registry_entries() -> Mapping[str, Sequence[str]]:
    path = discovery.personal_marketplace()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    plugins = document.get("plugins", ())
    return {"codex": tuple(str(entry.get("name")) for entry in plugins if isinstance(entry, Mapping) and entry.get("name"))}


def show_status(custom_harnesses: Sequence[str] = (), *, include_legacy_gemini: bool = False) -> int:
    """Preview the next install and run checks a file walk cannot answer."""
    from .. import __build_time__, __commit__, __version__

    print(
        "build "
        f"version={__version__} commit={__commit__} built={__build_time__} "
        f"package={Path(__file__).resolve().parents[1]} "
        f"python={Path(sys.executable).resolve()}"
    )
    hooks_disabled = os.environ.get("AUTORUN_DISABLE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if hooks_disabled:
        print("BROKEN AUTORUN_DISABLE=1: every autorun hook is bypassed")
    root = _marketplace_root()
    try:
        plugins = parse_selection(root, "all")
        resolved, custom = _resolved_settings(custom_harnesses=custom_harnesses)
        harnesses, table, available, _ = _harnesses(root, plugins, custom)
    except ValueError as error:
        print(f"Error: {error}")
        return 1
    if include_legacy_gemini and PLATFORMS["gemini"] not in harnesses:
        harnesses = (*harnesses, PLATFORMS["gemini"])
    if not harnesses:
        print("No maintained target CLI or configured custom harness was found.")
        return 1
    try:
        plugin = _runtime_settings(root, resolved, harnesses, custom)
    except ValueError as error:
        print(f"Error: {error}")
        return 1
    result = orchestrate.preview(
        marketplace_root=root,
        plugins=plugins,
        settings=resolved,
        harnesses=harnesses,
        run_command=_run,
        available=available,
        state_dir=_state_dir(),
        step_table=table,
    )
    _print_result(result, verbose=True)
    findings = ()
    probe_ok = True
    if plugin is not None and "ar" in plugins:
        command = runtime.hook_command(
            plugin,
            cli="claude",
            python=str(resolved.get("hook_python", "") or ""),
            no_sync=bool(resolved.get("hook_no_sync", True)),
        )
        memory_files = tuple(
            base / platform.memory_filename for platform in harnesses if platform.memory_filename and (base := discovery.config_dir(platform)) is not None
        )
        skill_routes = {
            platform.name: tuple(
                dict.fromkeys(
                    (
                        *discovery.skill_destinations(platform, reading=True),
                        *((discovery.shared_root(),) if platform.loads_shared_agents_skills else ()),
                    )
                )
            )
            for platform in harnesses
        }
        findings = status.health(
            hook_command=command.argv(),
            memory_files=memory_files,
            known_slugs=tuple(platform.memory_sentinel_slug for platform in PLATFORMS.values() if platform.memory_sentinel_slug),
            registry_entries=_registry_entries(),
            skill_routes=skill_routes,
            codex_dir=(discovery.config_dir(PLATFORMS["codex"]) if PLATFORMS["codex"] in harnesses else None),
            codex_guidance=memory.Block(PLATFORMS["codex"].memory_sentinel_slug),
            run=_run,
        )
        for finding in findings:
            print(finding.describe())
        probe = runtime.probe_hook_runtime(
            plugin,
            python=str(resolved.get("hook_python", "") or ""),
            no_sync=bool(resolved.get("hook_no_sync", True)),
        )
        print(probe.describe())
        probe_ok = probe.ok
    needs_install = any(getattr(decision, "verdict", None) in (Verdict.KEEP, Verdict.PUBLISH, Verdict.RETIRE) for decision in result.decisions)
    return 0 if not hooks_disabled and result.ok and not needs_install and probe_ok and all(finding.level is not status.Level.BROKEN for finding in findings) else 1


def _latest_version(current: str) -> str:
    """Newest compatible GitHub release, including RCs for RC installs.

    GitHub's ``/releases/latest`` endpoint intentionally omits prereleases, so
    it can never advance an RC installation to a newer RC. Stable installs do
    not opt into prereleases; draft releases are never candidates.
    """
    try:
        request = urllib.request.Request(
            "https://api.github.com/repos/ahundt/autorun/releases?per_page=100",
            headers={"User-Agent": "autorun-installer"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            releases = json.loads(response.read())
        allow_prerelease = any(char.isalpha() for char in current.lstrip("vV"))
        candidates = (
            str(item.get("tag_name", "")).lstrip("vV")
            for item in releases
            if isinstance(item, Mapping)
            and not item.get("draft", False)
            and (allow_prerelease or not item.get("prerelease", False))
        )
        return max((tag for tag in candidates if tag), key=runtime._as_tuple)
    except (OSError, TypeError, ValueError, urllib.error.URLError):
        return "unknown"


def perform_self_update(method: str = "auto") -> runtime.Outcome:
    """Update through the detected installation route."""
    try:
        current = version("autorun")
    except PackageNotFoundError:
        current = "unknown"
    extension_name = runtime.installed_extension_name(discovery.extensions_dir(PLATFORMS["gemini"]) or Path())
    return runtime.self_update(
        runtime.Version(current, _latest_version(current)),
        method=method,
        extension=extension_name,
        run=_run,
    )


def _map_legacy_flags(argv: Sequence[str]) -> list[str]:
    if not argv:
        return ["--install"]
    commands = {
        "install": "--install",
        "uninstall": "--uninstall",
        "check": "--status",
        "status": "--status",
    }
    return [commands[argv[0]], *argv[1:]] if argv[0] in commands else list(argv)


def install_main(argv: Sequence[str] | None = None) -> int:
    """Compatibility entry point for ``autorun-install`` and ``-m install``."""
    from ..__main__ import main

    return main(_map_legacy_flags(sys.argv[1:] if argv is None else argv))
