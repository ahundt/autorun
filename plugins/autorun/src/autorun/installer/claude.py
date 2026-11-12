#!/usr/bin/env python3
"""Claude's plugin cache: the fallback that fills it, and the paths inside it.

Claude installs plugins by copying them into a versioned cache and loading the
newest. Its own CLI does that, and ``registration`` drives the CLI, so this
module exists for the two things the CLI leaves undone.

THE FALLBACK
============

``claude plugin install`` can fail for reasons unrelated to the plugin — no
network, a marketplace it will not re-read, a CLI version that rejects a local
path. When it does, the files are simply absent and every hook autorun installs
is silently inert. The fallback writes the cache entry directly, so the failure
degrades to "installed but not registered" rather than "not installed".

Ownership there uses the installer's ordinary ``.autorun-owned`` file-hash
receipt, but a cache-specific walk. Autorun adopts a markerless cache only when
it exactly matches the bundled payload, refreshes only recorded and unchanged
files, preserves Claude's managed ``.venv``, and removes cache versions only
during an explicit uninstall. Upgrades retain every older version because a
running Claude session keeps invoking the directory and private interpreter it
captured at session start.

THE PLACEHOLDER
===============

A plugin's ``hooks.json`` refers to its own root as ``${CLAUDE_PLUGIN_ROOT}``.
Claude does not reliably expand that for a locally-sourced marketplace, and an
unexpanded placeholder becomes a path that does not exist — so the hook never
runs and nothing reports why. Substituting the real directory in the *cached*
copy leaves the repository's own file untouched, which matters because that file
is tracked and the expansion is machine-specific.

Complexity: fill and refresh copy O(payload bytes); mismatch detection hashes
O(payload files + bytes) while excluding `.venv` and generated caches;
substitution reads only the handful of candidate JSON documents. Refresh uses
one same-directory temporary file per changed payload file and retains no extra
copy after each atomic rename. Uninstall lists one plugin directory and removes
only proven generated trees.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Callable, Iterable, Iterator

from . import discovery, harness
from .fs import (
    atomic_write,
    compare,
    fill_tree,
    owns,
    read_marker,
    record_tree,
    refresh_unmarked_tree,
    scan_tree,
    withdrawn,
)

__all__ = [
    "CACHE_SUBDIR", "SUBSTITUTED_NAMES",
    "cache_dir", "cache_fallback", "cache_mismatches", "record_cache", "refresh_cache",
    "retire_cache_versions", "substitute_root", "installed_versions",
    "CONSOLE_HOOK_COMMAND", "pin_hook_command",
    "cache_hook_argv", "cache_hook_command",
]

#: The hook command build_support.py writes into a wheel's hooks.json: the
#: console script, found through PATH. Kept identical to that literal.
CONSOLE_HOOK_COMMAND = "autorun --cli claude"

#: Where Claude keeps plugin copies, relative to its config directory. The
#: marketplace name and the plugin name are separate levels, which is why one
#: product listed under two names produces two trees.
CACHE_SUBDIR = Path("plugins") / "cache"

#: Files inside a cached plugin that carry the plugin-root placeholder. Only
#: these are rewritten: substituting through every file would rewrite command
#: documentation that mentions the variable by name.
SUBSTITUTED_NAMES = ("hooks.json", "plugin.json", "settings.json")


def cache_hook_argv(directory: Path, *, cli: str = "claude") -> tuple[str, ...]:
    """Run the cached hook with its private, non-editable interpreter."""
    from .runtime import cache_venv_python

    return (
        str(cache_venv_python(directory)),
        "-I",
        str(directory / "hooks" / "hook_entry.py"),
        "--cli",
        cli,
    )


def cache_hook_command(directory: Path, *, cli: str = "claude") -> str:
    """Render :func:`cache_hook_argv` for Claude's command manifest."""
    from .runtime import render_hook_command

    return render_hook_command(
        cache_hook_argv(directory, cli=cli),
        {"PYTHONPATH": "", "PYTHONHOME": ""},
    )


def cache_dir(
    platform: object, *, market: str, plugin: str, version: str, home: Path | None = None
) -> Path | None:
    """Where Claude would keep this exact version, or None if it has no config.

    Built from the registry's config directory rather than a literal ``~/.claude``
    so a relocated Claude installation, and an isolated test, both resolve here.
    """
    base = discovery.config_dir(platform, home=home)
    return None if base is None else base / CACHE_SUBDIR / market / plugin / version


def _manifest_name(directory: Path) -> str:
    for relative in (Path(".claude-plugin") / "plugin.json", Path("plugin.json")):
        try:
            document = json.loads((directory / relative).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name = document.get("name") if isinstance(document, dict) else None
        if isinstance(name, str) and name:
            return name
    return ""


def cache_owned(directory: Path, plugin: str) -> bool:
    """Whether a file-hash receipt proves this cache belongs to ``plugin``."""
    marker = read_marker(directory)
    return (
        directory.is_dir()
        and not directory.is_symlink()
        and marker is not None
        and owns(marker, plugin)
    )


def _matches_shipped_payload(source: Path, directory: Path) -> bool:
    """Whether an unmarked cache is an exact copy of this bundled payload."""
    if not source.is_dir() or not directory.is_dir() or directory.is_symlink():
        return False
    expected = scan_tree(source)
    actual = scan_tree(directory)
    actual.pop(".orphaned_at", None)
    return actual == expected


def record_cache(source: Path, directory: Path, *, plugin: str = "") -> bool:
    """Adopt an exact legacy payload or refresh this installer's hash receipt."""
    plugin = plugin or _manifest_name(source)
    if not plugin:
        return False
    marker = read_marker(directory)
    if marker is None:
        proof = lambda installed: _matches_shipped_payload(source, installed)
    else:
        proof = lambda installed: cache_owned(installed, plugin)
    return record_tree(directory, plugin=plugin, ownership_proof=proof)


def _cache_keep_paths(directory: Path, plugin: str) -> tuple[str, ...]:
    marker = read_marker(directory)
    if marker is None or not owns(marker, plugin):
        return ("unproven cache payload",)
    edited, _missing, extra = compare(directory, marker)
    return tuple(sorted((*edited, *extra)))


def cache_mismatches(source: Path, directory: Path) -> tuple[str, ...]:
    """Payload paths whose shipped presence or content differs in the cache.

    ``scan_tree`` already excludes runtimes and generated bytecode. Claude's
    orphan timestamp is registration metadata, and the three substituted JSON
    names intentionally differ after installation; their presence is compared
    but their machine-specific bytes are not.
    """
    if not source.is_dir() or not directory.is_dir():
        return ("cache directory missing",)
    expected = scan_tree(source)
    actual = scan_tree(directory)
    actual.pop(".orphaned_at", None)
    paths = sorted(set(expected) | set(actual))
    mismatches = []
    for relative in paths:
        if relative not in expected or relative not in actual:
            mismatches.append(relative)
            continue
        if Path(relative).name in SUBSTITUTED_NAMES:
            continue
        if expected[relative] != actual[relative]:
            mismatches.append(relative)
    return tuple(mismatches)


def refresh_cache(
    source: Path,
    directory: Path,
    *,
    plugin: str = "",
    after_step: Callable[[str], None] | None = None,
) -> Path | None:
    """Refresh stale payload files in place while retaining Claude's private venv."""
    plugin = plugin or _manifest_name(source)
    if not plugin:
        return None
    if read_marker(directory) is None and not record_cache(source, directory, plugin=plugin):
        raise RuntimeError(f"KEEP {directory}: unproven cache payload")
    kept = _cache_keep_paths(directory, plugin)
    if kept:
        raise RuntimeError(f"KEEP {directory}: " + ", ".join(kept))
    if not cache_mismatches(source, directory):
        return None
    refreshed = refresh_unmarked_tree(
        source,
        directory,
        ownership_proof=lambda installed: (
            cache_owned(installed, plugin) and not _cache_keep_paths(installed, plugin)
        ),
        preserve_names=(".venv",),
        after_step=after_step,
    )
    if not refreshed:
        raise RuntimeError(f"KEEP {directory}: ownership proof failed")
    if not record_cache(source, directory, plugin=plugin):
        raise RuntimeError(f"KEEP {directory}: could not record refreshed payload")
    return directory


def retire_cache_versions(
    platform: object,
    *,
    market: str,
    plugin: str,
    keep: Iterable[str] = (),
    home: Path | None = None,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """Remove proven generated versions except ``keep``; return removed, kept.

    A malformed or foreign same-name tree is left in place and reported in the
    second tuple. This is the cache-specific equivalent of the installer's
    marker rule: no positive ownership proof, no deletion.
    """
    base = discovery.config_dir(platform, home=home)
    if base is None:
        return (), ()
    root = base / CACHE_SUBDIR / market / plugin
    wanted = set(keep)
    removed, unproven = [], []
    if not root.is_dir():
        return (), ()
    for directory in sorted((path for path in root.iterdir() if path.is_dir()), key=lambda path: path.name):
        if directory.name in wanted:
            continue
        if withdrawn(directory, plugin=plugin):
            removed.append(directory)
        else:
            unproven.append(directory)
    return tuple(removed), tuple(unproven)


def installed_versions(
    platform: object, *, market: str, plugin: str, home: Path | None = None
) -> tuple[str, ...]:
    """Every version of this plugin in the cache, oldest name first.

    Upgrades leave old versions in place because running Claude sessions keep
    invoking the version directory and private interpreter captured at start.
    """
    base = discovery.config_dir(platform, home=home)
    if base is None:
        return ()
    root = base / CACHE_SUBDIR / market / plugin
    if not root.is_dir():
        return ()
    return tuple(sorted(child.name for child in root.iterdir() if child.is_dir()))


def cache_fallback(
    source: Path,
    platform: object,
    *,
    market: str,
    plugin: str,
    version: str,
    home: Path | None = None,
) -> Path | None:
    """Fill Claude's cache directly, for when its own CLI could not.

    Returns the directory newly written, or None when it already exists or
    Claude declares no config directory. Existing payload reconciliation is
    handled separately by :func:`refresh_cache`, which preserves ``.venv``.
    """
    target = cache_dir(platform, market=market, plugin=plugin, version=version, home=home)
    if target is None or not source.is_dir():
        return None
    written = fill_tree(source, target)
    if written is not None and not record_cache(source, written, plugin=plugin):
        raise RuntimeError(f"could not record Claude cache ownership for {written}")
    return written


def substitute_root(directory: Path, *, names: Iterable[str] = SUBSTITUTED_NAMES) -> tuple[str, ...]:
    """Expand ``${CLAUDE_PLUGIN_ROOT}`` inside a cached copy. Returns what changed.

    Applied to the copy Claude loads, never to the repository's own file: that
    one is tracked, and the expansion names a directory on this machine.

    A file that does not contain the placeholder is left alone rather than
    rewritten with identical content, so this does not churn mtimes a harness
    watches.
    """
    changed = []
    for path in _candidates(directory, names):
        try:
            before = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        after = harness.substitute(before, directory, json_string=path.suffix == ".json")
        if after != before:
            atomic_write(path, after)
            # The receipt is portable between hosts; do not persist Windows
            # separators in a list that is later compared on POSIX.
            changed.append(path.relative_to(directory).as_posix())
    return tuple(changed)


def _candidates(directory: Path, names: Iterable[str]) -> Iterator[Path]:
    wanted = set(names)
    for path in sorted(directory.rglob("*")):
        if path.is_file() and not path.is_symlink() and path.name in wanted:
            yield path


def pin_hook_command(directory: Path, command: str) -> bool:
    """Give a cached copy's hooks the installer's absolute hook command.

    A wheel's hooks.json runs :data:`CONSOLE_HOOK_COMMAND`, which works only
    where Claude Code's PATH holds uv's tool directory. A source checkout's
    wrapper also names ``hook_entry.py`` and must be replaced after the cache
    snapshot, or it would continue importing mutable checkout code. The shared
    bundle ownership predicate proves every command in that manifest is ours;
    foreign commands remain untouched. Returns whether the file changed.
    """
    path = directory / "hooks" / "hooks.json"
    if not command or not path.is_file():
        return False
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    from .codex import COMMAND_MARK
    from .extension import bundle_hooks_are_ours

    owned_bundle = bundle_hooks_are_ours(directory)
    changed = False
    for event, matchers in (document.get("hooks") or {}).items():
        if not isinstance(event, str) or not event:
            continue
        pinned = f"{command} --event {shlex.quote(event)}"
        for matcher in matchers if isinstance(matchers, list) else ():
            for hook in (matcher.get("hooks") or ()) if isinstance(matcher, dict) else ():
                if (
                    isinstance(hook, dict)
                    and isinstance(hook.get("command"), str)
                    and (
                        hook["command"] == CONSOLE_HOOK_COMMAND
                        or (owned_bundle and COMMAND_MARK in hook["command"])
                    )
                    and hook["command"] != pinned
                ):
                    hook["command"] = pinned
                    changed = True
    if changed:
        atomic_write(path, json.dumps(document, indent=2) + "\n")
    return changed


def demo() -> None:
    """Self-check: the fallback leaves no marker, and substitution is exact."""
    import json
    import tempfile

    from .fs import OWNED_MARKER_NAME, read_marker

    # A synthetic harness, not the real one: this module must work from the
    # registry's declared config directory, and a literal here would be a
    # second authority for a question `discovery.config_dir` already answers.
    class FakeClaude:
        name = "fake-cache-harness"
        config_dir = "~/.fake-cache-harness"
        config_dir_env_vars: tuple[str, ...] = ()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        home = root / "home"
        source = root / "plugin"
        (source / "hooks").mkdir(parents=True)
        (source / "hooks" / "hooks.json").write_text(
            json.dumps({"hooks": {"PreToolUse": ["${CLAUDE_PLUGIN_ROOT}/hooks/entry.py"]}}),
            encoding="utf-8",
        )
        (source / "README.md").write_text("mentions ${CLAUDE_PLUGIN_ROOT} in prose\n", encoding="utf-8")

        written = cache_fallback(
            source, FakeClaude(), market="autorun", plugin="ar", version="1.2.3", home=home
        )
        expected = cache_dir(
            FakeClaude(), market="autorun", plugin="ar", version="1.2.3", home=home
        )
        assert written == expected
        assert written.parts[-3:] == ("autorun", "ar", "1.2.3"), written
        assert written.parts[-5:-3] == ("plugins", "cache"), written
        assert (written / "hooks" / "hooks.json").is_file()

        # The cache-specific receipt records exactly what autorun installed.
        marker = read_marker(written)
        assert (written / OWNED_MARKER_NAME).is_file()
        assert marker is not None and marker.plugin == "ar"

        # Substitution reaches the hook manifest and leaves prose alone.
        changed = substitute_root(written)
        assert changed == ("hooks/hooks.json",), changed
        assert record_cache(source, written, plugin="ar")
        # As JSON writes it: a Windows path's backslashes arrive escaped.
        assert json.dumps(str(written))[1:-1] in (written / "hooks" / "hooks.json").read_text(encoding="utf-8")
        assert "${CLAUDE_PLUGIN_ROOT}" in (written / "README.md").read_text(encoding="utf-8")

        # Idempotent: nothing left to expand, so nothing is rewritten.
        assert substitute_root(written) == ()

        # A second version lands beside the first; both are reported, neither
        # is pruned, because Claude loads the newest and manages the rest.
        cache_fallback(
            source, FakeClaude(), market="autorun", plugin="ar", version="1.2.4", home=home
        )
        assert installed_versions(FakeClaude(), market="autorun", plugin="ar", home=home) == (
            "1.2.3", "1.2.4",
        )

        # An existing version belongs to Claude and may contain its managed
        # runtime. Fallback fills an absence; it never replaces that tree.
        (written / "stale.txt").write_text("from the previous copy\n", encoding="utf-8")
        assert cache_fallback(
            source, FakeClaude(), market="autorun", plugin="ar", version="1.2.3", home=home
        ) is None
        assert (written / "stale.txt").is_file(), "fallback replaced Claude's cache"

        # A harness with no config directory is a real answer, not a failure.
        class Bare:
            name = "bare"
            config_dir = ""
            config_dir_env_vars: tuple[str, ...] = ()

        assert cache_dir(Bare(), market="m", plugin="p", version="1", home=home) is None
        assert cache_fallback(source, Bare(), market="m", plugin="p", version="1", home=home) is None
        assert installed_versions(Bare(), market="m", plugin="p", home=home) == ()

    print("installer.claude: all self-checks passed")


if __name__ == "__main__":
    demo()
