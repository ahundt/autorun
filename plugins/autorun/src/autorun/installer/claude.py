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

Ownership there is by *path*, not by marker: the cache belongs to Claude, is
keyed by version, and Claude's installer prunes it. A marker would make
autorun's retirement sweep offer to delete state Claude still tracks, which is
why :func:`fs.replace_tree` exists and why this is its only caller.

THE PLACEHOLDER
===============

A plugin's ``hooks.json`` refers to its own root as ``${CLAUDE_PLUGIN_ROOT}``.
Claude does not reliably expand that for a locally-sourced marketplace, and an
unexpanded placeholder becomes a path that does not exist — so the hook never
runs and nothing reports why. Substituting the real directory in the *cached*
copy leaves the repository's own file untouched, which matters because that file
is tracked and the expansion is machine-specific.

Complexity: the fallback is O(bytes) once; substitution is O(bytes of the JSON
files in the cached copy), which is a handful of small documents.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator

from . import discovery, harness
from .fs import replace_tree

__all__ = [
    "CACHE_SUBDIR", "SUBSTITUTED_NAMES",
    "cache_dir", "cache_fallback", "substitute_root", "installed_versions",
]

#: Where Claude keeps plugin copies, relative to its config directory. The
#: marketplace name and the plugin name are separate levels, which is why one
#: product listed under two names produces two trees.
CACHE_SUBDIR = Path("plugins") / "cache"

#: Files inside a cached plugin that carry the plugin-root placeholder. Only
#: these are rewritten: substituting through every file would rewrite command
#: documentation that mentions the variable by name.
SUBSTITUTED_NAMES = ("hooks.json", "plugin.json", "settings.json")


def cache_dir(
    platform: object, *, market: str, plugin: str, version: str, home: Path | None = None
) -> Path | None:
    """Where Claude would keep this exact version, or None if it has no config.

    Built from the registry's config directory rather than a literal ``~/.claude``
    so a relocated Claude installation, and an isolated test, both resolve here.
    """
    base = discovery.config_dir(platform, home=home)
    return None if base is None else base / CACHE_SUBDIR / market / plugin / version


def installed_versions(
    platform: object, *, market: str, plugin: str, home: Path | None = None
) -> tuple[str, ...]:
    """Every version of this plugin in the cache, oldest name first.

    Reported rather than pruned. Claude loads the newest and manages the rest;
    deleting one here would remove state its installer still tracks.
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

    Returns the directory written, or None when Claude declares no config
    directory — which is a real answer for a machine without Claude, not a
    failure to report.
    """
    target = cache_dir(platform, market=market, plugin=plugin, version=version, home=home)
    if target is None or not source.is_dir():
        return None
    return replace_tree(source, target)


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
        after = harness.substitute(before, directory)
        if after != before:
            path.write_text(after, encoding="utf-8")
            # The receipt is portable between hosts; do not persist Windows
            # separators in a list that is later compared on POSIX.
            changed.append(path.relative_to(directory).as_posix())
    return tuple(changed)


def _candidates(directory: Path, names: Iterable[str]) -> Iterator[Path]:
    wanted = set(names)
    for path in sorted(directory.rglob("*")):
        if path.is_file() and not path.is_symlink() and path.name in wanted:
            yield path


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

        # No marker: this directory is Claude's, and a marker would offer it to
        # autorun's retirement sweep.
        assert not (written / OWNED_MARKER_NAME).exists()
        assert read_marker(written) is None

        # Substitution reaches the hook manifest and leaves prose alone.
        changed = substitute_root(written)
        assert changed == ("hooks/hooks.json",), changed
        assert str(written) in (written / "hooks" / "hooks.json").read_text()
        assert "${CLAUDE_PLUGIN_ROOT}" in (written / "README.md").read_text()

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

        # Replacing a version is atomic and complete, not a merge.
        (written / "stale.txt").write_text("from the previous copy\n", encoding="utf-8")
        cache_fallback(
            source, FakeClaude(), market="autorun", plugin="ar", version="1.2.3", home=home
        )
        assert not (written / "stale.txt").exists(), "a replaced cache entry is not a merge"

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
