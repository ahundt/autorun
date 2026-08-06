#!/usr/bin/env python3
"""Owned trees that remember what they contained, so a user edit is detectable.

This is the foundation the rest of the install system stands on. Everything that
writes to a user's disk goes through the three transactions here, and nothing
else may mutate a tree.

THE CAPABILITY THIS ADDS
========================

What the current ownership marker cannot express::

    sync_owned_skill_tree(src, dst, "ar")     # installs skills/demo
    (dst / "demo" / "SKILL.md").write_text("MY OWN EDIT")
    sync_owned_skill_tree(src, dst, "ar")     # source moved on
    # today: the user's edit is gone, with no message

``read_owned_marker`` gates whether autorun may *claim* a directory. Once
claimed, the whole tree is swapped. So "autorun never replaces a copy it does
not own" holds only for directories it never owned — edits *inside* an owned
directory were never protected. A marker states a fact about a directory; it
cannot record what the contents were. A hash can.

WHAT THIS IS NOT
================

An earlier draft made per-file materialization the mechanism: walk the source,
write each file, record a receipt. Testing it broke it in five ways, all kept
here as the reason this design is shaped differently:

1. binary assets raised ``UnicodeDecodeError`` — skills ship images and scripts
2. the executable bit was lost, which breaks ``hooks/hook_entry.py``
3. symlinks could not be represented, though the bridge deliberately creates
   them and the Codex plugin cache requires dereferencing them
4. ``--status`` would hash every installed file on every run
5. harness CLIs own extensions as *directories*, not as sets of files

So materialization stays ``shutil.copytree``, which already preserves modes,
symlinks and bytes, inside a stage-and-rename that is already atomic. Only the
*record* changes: the marker gains a per-file digest of what autorun wrote.
Hashing is paid once at publish, on bytes already being copied, and at compare
time only for the tree about to be replaced — never for a whole status pass.

A second draft added a ``Location``/``Action``/``Plan``/``apply`` engine above
this. It was dropped: five Action implementations and an apply loop to express
what a list of calls already expresses is an abstraction with one caller per
implementation. Dry run is a flag on the decision, not a parallel engine.

WHY NOT A LIBRARY
=================

``platforms.py`` already is the manifest — 78 typed fields over 8 harnesses,
holding ``HookProtocol`` subclasses, ``SkillRoute`` objects and callables. A
TOML/YAML manifest would force each of those into a name resolved at runtime,
which is exactly the bug already found here: ``install_fn_name`` naming a
function nobody wrote, published as a capability. ``pydantic-settings`` fits the
settings ladder well but adds a runtime dependency to a plugin whose own
guidance records that stderr from dependency resolution silently disables every
hook. ``hashlib``, ``shutil`` and ``filelock`` (already a dependency) carry this
without any of that.

Complexity: publish is O(bytes) — the hash rides along with the copy. Compare is
O(bytes of the tree being replaced), and only when it is about to change.
Removal is O(1) metadata plus a background delete of the quarantined copy.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterator, Mapping

from filelock import FileLock

__all__ = [
    "FileState",
    "TreeManifest",
    "Verdict",
    "Decision",
    "scan_tree",
    "compare",
    "decide",
    "read_marker",
    "owns",
    "PLUGIN_ALIASES",
    "published",
    "publish_tree",
    "publish_files",
    "publish_link",
    "decide_link",
    "withdraw_link",
    "backup_path",
    "BACKUP_SUFFIX",
    "withdrawn",
    "withdraw_files",
    "dereference_links",
    "json_document",
    "atomic_write",
    "OWNED_MARKER_NAME",
    "INSTALL_LOCK_NAME",
    "SKIP_NAMES",
    "IGNORED_GLOBS",
]

OWNED_MARKER_NAME = ".autorun-owned"
INSTALL_LOCK_NAME = ".autorun-install.lock"
SKIP_NAMES = frozenset({"__pycache__", ".pytest_cache", ".git", ".venv", ".mypy_cache"})

#: Build junk that neither reaches a user's config directory nor appears in a
#: manifest. One declaration with two consumers, ``_ignored`` for the scan and
#: ``_IGNORED`` for the copy, because the two must agree exactly and once did
#: not: the copy dropped ``*~`` and the scan recorded it, so a stray editor
#: backup beside a source file made every later ``decide`` report ``PUBLISH``
#: for an identical tree, and each install rewrote it.
IGNORED_GLOBS: tuple[str, ...] = (
    *sorted(SKIP_NAMES), "*.pyc", "*.pyo", "*.tmp", "*~", "*.bak",
)
_LINK = "link:"


def _ignored(name: str) -> bool:
    """True for a name neither copied nor fingerprinted."""
    return any(fnmatch.fnmatch(name, pattern) for pattern in IGNORED_GLOBS)


_MARKER_NOTE = (
    "Created by autorun. Delete this file to un-claim the directory: autorun "
    "will then treat it as user-authored and neither replace nor remove it."
)

#: Names one plugin has been recorded under, newest first.
#:
#: Ownership is scoped by plugin name — that is what stops autorun deleting
#: pdf-extractor's trees — so a tree marked with one spelling is unremovable
#: under another. That happened: the Codex path recorded the *directory* name
#: ``autorun`` while every other path recorded the *registered* name ``ar``, and
#: the resulting 362-file Codex tree survived every uninstall because
#: ``decide(tree, None, plugin="ar")`` returned KEEP.
#:
#: Going forward autorun writes the registered name everywhere; a harness's own
#: display name (Codex lists the plugin as ``autorun@personal``) is a fact about
#: that harness's registry, not about who owns a directory. This table exists so
#: markers already on disk are still recognised, and can be retired once no
#: installation predates the change.
PLUGIN_ALIASES: Mapping[str, tuple[str, ...]] = {
    "ar": ("autorun",),
}


def owns(marker: "TreeManifest | None", plugin: str) -> bool:
    """Whether ``plugin`` may replace or remove the tree ``marker`` describes.

    An unmarked tree is never ours. A marker with no recorded plugin predates
    per-plugin scoping and belongs to whoever asks, which is what lets an
    upgrade adopt the trees an older autorun claimed.
    """
    if marker is None:
        return False
    if not marker.plugin or not plugin:
        return True
    return marker.plugin == plugin or marker.plugin in PLUGIN_ALIASES.get(plugin, ())


# ---------------------------------------------------------------------------
# What one file looked like when we wrote it
# ---------------------------------------------------------------------------


class FileState(Enum):
    """What one recorded file looks like now."""

    UNCHANGED = "unchanged"
    EDITED = "edited"
    MISSING = "missing"


def _fingerprint(path: Path) -> str:
    """Return a content+mode fingerprint, or the target of a symlink.

    Symlinks are recorded by target rather than by content: the bridge creates
    them on purpose, and following one to hash its contents would report a
    perfectly healthy link as edited the moment its target changed.

    The executable bit is part of the fingerprint because losing it is a real
    failure — ``hooks/hook_entry.py`` stops being runnable — and a user who
    marks one of our files executable has changed it as surely as editing it.
    """
    if path.is_symlink():
        return _LINK + os.readlink(path)
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 16), b""):
            hasher.update(block)
    executable = bool(path.stat().st_mode & stat.S_IXUSR)
    return f"{hasher.hexdigest()}:{'x' if executable else '-'}"


def scan_tree(root: Path) -> dict[str, str]:
    """Fingerprint every file under ``root``, keyed by relative path.

    Walks with ``os.walk`` rather than ``rglob`` so symlinked directories are
    recorded as links instead of descended into — descending would both hash a
    foreign tree and risk a cycle.

    Skips exactly what the copy skips (``IGNORED_GLOBS``) plus the marker, which
    is written after the scan and would otherwise record its own digest.
    """
    manifest: dict[str, str] = {}
    for directory, subdirs, names in os.walk(root):
        here = Path(directory)
        subdirs[:] = [d for d in subdirs if not _ignored(d) and not (here / d).is_symlink()]
        for link in (d for d in os.listdir(directory) if (here / d).is_symlink() and (here / d).is_dir()):
            manifest[str((here / link).relative_to(root))] = _LINK + os.readlink(here / link)
        for name in names:
            if name == OWNED_MARKER_NAME or _ignored(name):
                continue
            path = here / name
            manifest[str(path.relative_to(root))] = _fingerprint(path)
    return manifest


@dataclass(frozen=True, slots=True)
class TreeManifest:
    """What autorun wrote into one directory, and what each file contained.

    ``files`` widens the ownership marker's existing ``files`` tuple — which
    already records which names in a *shared* directory are ours, for
    ForgeCode's ``commands/`` — from names to name-and-fingerprint. Old markers
    carrying a bare list still decode; they simply cannot report edits, which is
    the behaviour they have today.

    ``settings`` carries the bridge mode and anything else uninstall must know
    to reverse an install it did not perform.
    """

    plugin: str = ""
    files: Mapping[str, str] = field(default_factory=dict)
    settings: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, root: Path, plugin: str = "", **settings: str) -> "TreeManifest":
        return cls(plugin=plugin, files=scan_tree(root), settings=settings)

    @classmethod
    def from_marker(cls, payload: Mapping[str, object]) -> "TreeManifest":
        """Decode a marker payload, tolerating the pre-manifest list form."""
        raw, plugin, cfg = payload.get("files"), payload.get("plugin"), payload.get("settings")
        return cls(
            plugin=plugin if isinstance(plugin, str) else "",
            files=({k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}
                   if isinstance(raw, Mapping) else {}),
            settings=({k: v for k, v in cfg.items() if isinstance(k, str) and isinstance(v, str)}
                      if isinstance(cfg, Mapping) else {}),
        )

    def as_payload(self) -> dict:
        return {
            "note": _MARKER_NOTE,
            "plugin": self.plugin,
            "files": dict(self.files),
            "settings": dict(self.settings),
        }

    def state_of(self, root: Path, relative: str) -> FileState:
        recorded = self.files.get(relative)
        path = root / relative
        if recorded is None:
            return FileState.MISSING
        if not path.is_symlink() and not path.is_file():
            return FileState.MISSING
        return FileState.UNCHANGED if _fingerprint(path) == recorded else FileState.EDITED


def read_marker(directory: Path) -> TreeManifest | None:
    """Return what autorun recorded for ``directory``, or None if it is not ours.

    Pre-JSON markers were prose with ``key=value`` lines. They still mean "we
    made this", so they decode rather than reading as user-authored — an upgrade
    must not strand directories an older install claimed.
    """
    try:
        text = (directory / OWNED_MARKER_NAME).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        legacy = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        return TreeManifest(settings={k.strip(): v.strip() for k, v in legacy.items()})
    return TreeManifest.from_marker(payload) if isinstance(payload, dict) else TreeManifest()


def compare(root: Path, manifest: TreeManifest) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return (edited, missing) relative paths for a tree we recorded.

    An empty manifest means the tree predates fingerprinting, so nothing is
    reported: claiming a user edited a file we never measured would block every
    upgrade on directories installed by an older autorun.
    """
    if not manifest.files:
        return ((), ())
    states = {p: manifest.state_of(root, p) for p in manifest.files}
    return (
        tuple(sorted(p for p, s in states.items() if s is FileState.EDITED)),
        tuple(sorted(p for p, s in states.items() if s is FileState.MISSING)),
    )


# ---------------------------------------------------------------------------
# One decision function for install, prune and uninstall
# ---------------------------------------------------------------------------


class Verdict(Enum):
    """What to do with one destination directory."""

    PUBLISH = "publish"    # not there, or ours and unchanged
    KEEP = "keep"          # the user wrote it, or edited what we wrote
    RETIRE = "retire"      # ours, unchanged, and no longer shipped
    SKIP = "skip"          # already exactly what we would write


@dataclass(frozen=True, slots=True)
class Decision:
    verdict: Verdict
    target: Path
    reason: str
    edited: tuple[str, ...] = ()

    def describe(self) -> str:
        """One line for --status, for a dry run, and for the install report.

        Dry run prints these and stops; a real install prints them and acts.
        That is the whole difference, which is why there is no second code path
        for previewing.
        """
        detail = f" ({', '.join(self.edited)})" if self.edited else ""
        return f"{self.verdict.value:<7} {self.target}  — {self.reason}{detail}"


def decide(
    target: Path,
    source: Path | None,
    *,
    plugin: str,
    read: Callable[[Path], TreeManifest | None] = read_marker,
) -> Decision:
    """Decide one directory's fate, the same way for install and uninstall.

    ``source is None`` means the tree is no longer shipped, which is the
    uninstall and prune question. Everything else is the install question. They
    are the same comparison, which is why they belong in one function — the code
    this replaces runs four passes that each re-derive their own paths and have
    each drifted from the others.
    """
    if not target.exists():
        return (Decision(Verdict.PUBLISH, target, "new") if source is not None
                else Decision(Verdict.SKIP, target, "already absent"))

    manifest = read(target)
    if manifest is None:
        return Decision(Verdict.KEEP, target, "user-authored")
    if not owns(manifest, plugin):
        return Decision(Verdict.KEEP, target, f"belongs to {manifest.plugin}")

    if edited := compare(target, manifest)[0]:
        return Decision(Verdict.KEEP, target, "you edited files we installed", edited)
    if source is None:
        return Decision(Verdict.RETIRE, target, "no longer shipped")
    if scan_tree(source) == dict(manifest.files):
        return Decision(Verdict.SKIP, target, "already current")
    return Decision(Verdict.PUBLISH, target, "updating")


# ---------------------------------------------------------------------------
# The three filesystem transactions. Nothing else may mutate.
# ---------------------------------------------------------------------------


def owned_trees(root: Path, *, plugin: str = "", max_depth: int = 5) -> Iterator[Path]:
    """Every directory under ``root`` that carries our marker.

    This is how autorun finds what a *previous version* installed. A step only
    knows where its capability writes today, so anything an older release put
    somewhere else is never visited and therefore never removed — every route
    change, rename and dropped harness leaks trees that still carry our marker.
    Observed: retiring Qwen's native skill route left 17 marked skill
    directories in place, and the current registry cannot name that location at
    all, so no enumeration of *routes* would find them.

    Scanning for the marker instead of for known paths is what makes this
    correct as routes change, rather than correct on the day it was written.

    A marked directory is not descended into: an owned tree is removed whole, so
    its contents are already accounted for and recursing would report the same
    artifact once per nested marker. ``max_depth`` bounds a scan over a
    directory that also holds harness caches and session logs.
    """
    if not root.is_dir():
        return
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            children = sorted(current.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir() or child.is_symlink() or child.name in SKIP_NAMES:
                continue
            marker = read_marker(child)
            if marker is not None:
                if not plugin or owns(marker, plugin):
                    yield child
                continue  # owned trees are removed whole; do not descend
            if depth + 1 < max_depth:
                stack.append((child, depth + 1))


def atomic_write(path: Path, text: str) -> None:
    """Write via a sibling temp file and one rename. Never a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, staged = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, path)
    except BaseException:
        Path(staged).unlink(missing_ok=True)
        raise


@contextmanager
def published(target: Path, *, plugin: str = "", **settings: str) -> Iterator[Path]:
    """Stage beside ``target``, record what we wrote, and swap it in atomically.

    Yields the staging path for the caller to fill — normally with one
    ``copytree``. On clean exit the staged directory replaces the target; on any
    failure the previous contents are restored, so an interrupted install leaves
    the previous copy and never a partial one.

    The manifest is computed from the staged tree here rather than by the
    caller, so no call site can publish a tree whose contents were never
    recorded — which is the gap that lets a later reinstall destroy a user edit.

    Staging inside the target's own parent keeps the rename atomic (``os.replace``
    guarantees that only within a filesystem) and keeps the rollback local. The
    lock also lives in the parent, so it outlives the artifact being replaced.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(target.parent / INSTALL_LOCK_NAME)):
        with tempfile.TemporaryDirectory(
            prefix=f".autorun-publish-{target.name}-", dir=target.parent
        ) as tmp:
            staged, backup = Path(tmp) / "next", Path(tmp) / "previous"
            yield staged
            # Claim inside the staging scope so the marker lands in the same
            # rename as the contents it describes.
            atomic_write(
                staged / OWNED_MARKER_NAME,
                json.dumps(TreeManifest.of(staged, plugin, **settings).as_payload(), indent=2) + "\n",
            )
            if target.exists() or target.is_symlink():
                os.replace(target, backup)
            try:
                os.replace(staged, target)
            except BaseException:
                if backup.exists() or backup.is_symlink():
                    os.replace(backup, target)
                raise


def withdrawn(target: Path, *, plugin: str | None = None) -> bool:
    """Delete a directory autorun owns, restoring it if the delete fails.

    The removal twin of :func:`published`, and deliberately the same
    transaction. An unmarked directory is the user's whatever its name, and a
    marker naming another plugin belongs to that plugin, so both are refused
    rather than removed.

    Returns True only when the directory is actually gone. The variant this
    replaces used ``shutil.rmtree(ignore_errors=True)`` and reported success
    either way, so a failed uninstall looked identical to a clean one.
    """
    if not target.is_dir() or target.is_symlink():
        return False
    with FileLock(str(target.parent / INSTALL_LOCK_NAME)):
        # Re-check ownership inside the lock: checking, releasing, then removing
        # leaves a window in which a user-authored directory can take this path.
        if not target.is_dir() or target.is_symlink():
            return False
        manifest = read_marker(target)
        if manifest is None or (plugin is not None and not owns(manifest, plugin)):
            return False
        with tempfile.TemporaryDirectory(
            prefix=f".autorun-withdraw-{target.name}-", dir=target.parent
        ) as tmp:
            quarantined = Path(tmp) / target.name
            os.replace(target, quarantined)
            try:
                shutil.rmtree(quarantined)
            except BaseException:
                os.replace(quarantined, target)
                raise
    return True


#: Suffix for a file autorun moved aside. Numbered when they stack, so a second
#: install never overwrites the backup the first one made.
BACKUP_SUFFIX = ".autorun-backup"


def backup_path(path: Path) -> Path:
    """The next free backup name for ``path``.

    ``x.md`` -> ``x.md.autorun-backup`` -> ``x.md.autorun-backup.1`` -> ``.2``.
    Numbering matters: a user who keeps their own version through several
    installs would otherwise have the first backup silently replaced by the
    second, losing the original they actually cared about.
    """
    candidate = path.with_name(path.name + BACKUP_SUFFIX)
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}{BACKUP_SUFFIX}.{counter}")
        counter += 1
    return candidate


def decide_files(
    source: Path, directory: Path, *, plugin: str = "", backup: bool = True
) -> Decision:
    """Decide a *shared* directory without writing, per file.

    :func:`decide` cannot answer for one of these. It asks "is this directory
    ours?", and a `commands/` folder holding the user's own commands is not —
    so a first install into it would be refused outright instead of adding our
    files beside theirs. Ownership in a shared directory is per file.
    """
    marker = read_marker(directory)
    ours = dict(marker.files) if owns(marker, plugin) else {}
    shipped = sorted(p.name for p in source.iterdir() if p.is_file()) if source.is_dir() else []

    blocked = tuple(
        name for name in shipped
        if (directory / name).exists()
        and (name not in ours or _fingerprint(directory / name) != ours[name])
    )
    # With backup on, a collision is not a refusal: the user's file moves aside
    # and ours lands, so everything shipped is writable.
    writable = list(shipped) if backup else [n for n in shipped if n not in blocked]

    # KEEP only when there is nothing left to write. Returning KEEP because
    # *some* name is blocked would make one collision cancel the whole
    # directory: a user with their own `ar-go.md` in `commands/` would receive
    # none of autorun's other commands, reported as a KEEP line that installed
    # nothing. Blocking is per file, which is the entire reason this pair exists
    # separately from the whole-tree pair.
    if not writable:
        return Decision(
            Verdict.KEEP, directory,
            "you wrote or edited every file we ship here" if blocked else "nothing to publish",
            blocked,
        )
    # "Already current" is a statement about three things agreeing: what we
    # ship, what we recorded, and what is on disk. Comparing only the last two
    # froze every shared directory after its first install — the manifest still
    # described the destination perfectly while the source had moved on, so
    # `_perform` (which acts on PUBLISH, not SKIP) never wrote the update and
    # reported "already current" every run. Same rule as `decide`, which
    # compares `scan_tree(source)` against the manifest.
    if not blocked and set(shipped) == set(ours) and all(
        (directory / n).exists()
        and _fingerprint(directory / n) == ours[n]
        and _fingerprint(source / n) == ours[n]
        for n in ours
    ):
        return Decision(Verdict.SKIP, directory, "already current")
    reason = f"{len(writable)} file(s)"
    if blocked:
        reason += f", {'backing up' if backup else 'keeping'} {len(blocked)} you changed"
    return Decision(Verdict.PUBLISH, directory, reason, blocked)


def publish_files(
    source: Path, directory: Path, *, plugin: str = "", backup: bool = True, **settings: str
) -> Decision:
    """Add our files to a directory the user also writes to, leaving theirs.

    The publish twin of :func:`withdraw_files`, and the reason a shared
    directory cannot use :func:`publish_tree`: swapping the whole directory
    would either refuse outright (an unmarked ``commands/`` full of the user's
    own commands reads as user-authored) or replace their files with ours.
    ForgeCode's and OpenCode's ``commands/`` are both this case.

    A file already there that we did not write, or wrote and the user has since
    edited, is **moved aside** rather than refused: the shipped set has to be
    complete or the package is broken. A user whose own ``ar-go.md`` occupies
    the name would otherwise find ``/ar:go`` simply absent, with the install
    reporting success. Their content is preserved next to it as
    ``ar-go.md.autorun-backup``, numbered if backups stack, and every backup is
    named in the returned decision so the caller can say what moved.

    Set ``backup=False`` to keep the older refusing behaviour, which is right
    where a fallback exists (a skill blocked on the shared root still has its
    harness's native route) and wrong here, where nothing else can deliver the
    file.
    """
    directory.mkdir(parents=True, exist_ok=True)
    kept, written, backed_up = [], {}, []
    with FileLock(str(directory.parent / INSTALL_LOCK_NAME)):
        # Read ownership *inside* the lock, the rule `withdrawn` states. A
        # concurrent install that lands between the read and the lock leaves
        # `ours` describing files it has already replaced, and every name it
        # rewrote then looks like a user edit and is moved to .autorun-backup.
        previous = read_marker(directory)
        ours = dict(previous.files) if owns(previous, plugin) else {}
        for candidate in sorted(p for p in source.iterdir() if p.is_file()):
            destination = directory / candidate.name
            if destination.exists() and (
                candidate.name not in ours
                or _fingerprint(destination) != ours[candidate.name]
            ):
                if not backup:
                    kept.append(candidate.name)
                    continue
                moved = backup_path(destination)
                destination.rename(moved)
                backed_up.append(moved.name)
            shutil.copy2(candidate, destination)
            written[candidate.name] = _fingerprint(destination)
        # Files we published before and no longer ship stop being ours.
        for stale in set(ours) - set(written) - set(kept):
            if (directory / stale).exists() and _fingerprint(directory / stale) == ours[stale]:
                (directory / stale).unlink()
        atomic_write(
            directory / OWNED_MARKER_NAME,
            json.dumps(TreeManifest(plugin, written, settings).as_payload(), indent=2) + "\n",
        )
    # Report what happened, not merely that something was skipped. Returning
    # KEEP whenever any file was kept says "installed nothing" while having
    # installed the rest, so a user reading the report cannot tell a partial
    # install from a refusal.
    if not written:
        return Decision(
            Verdict.KEEP, directory,
            "you wrote or edited every file we ship here" if kept else "nothing to publish",
            tuple(kept),
        )
    reason = f"{len(written)} file(s)"
    if backed_up:
        reason += f", backed up {len(backed_up)} you had changed"
    if kept:
        reason += f", keeping {len(kept)} you changed"
    return Decision(Verdict.PUBLISH, directory, reason, tuple(kept) + tuple(backed_up))


def decide_link(source: Path | None, target: Path, inside: Path, *, plugin: str = "") -> Decision:
    """Decide a bridged symlink, which :func:`decide` cannot answer.

    A live symlink ``exists()`` and carries no marker, so the whole-tree
    decision reads it as user-authored and refuses forever: the bridge would
    re-report KEEP on every run and uninstall would never remove it. Ownership
    of a link is its target, not a marker file.

    ``source is None`` is the retirement question, as everywhere else.
    """
    if target.is_symlink():
        try:
            pointed = Path(os.readlink(target))
            pointed = pointed if pointed.is_absolute() else (target.parent / pointed)
            resolved = pointed.resolve()
        except (OSError, RuntimeError):
            return Decision(Verdict.KEEP, target, "unreadable link")
        try:
            resolved.relative_to(inside.resolve())
        except ValueError:
            return Decision(Verdict.KEEP, target, "links outside the shared root")
        if source is None:
            return Decision(Verdict.RETIRE, target, "no longer bridged")
        return (
            Decision(Verdict.SKIP, target, "already linked")
            if resolved == source.resolve()
            else Decision(Verdict.PUBLISH, target, "relinking")
        )
    if not target.exists():
        return (Decision(Verdict.PUBLISH, target, "new link") if source is not None
                else Decision(Verdict.SKIP, target, "already absent"))
    # A real directory where a link belongs: the whole-tree rules apply, so a
    # copy-mode bridge we own can be replaced and anything else is the user's.
    return decide(target, source, plugin=plugin)


def publish_link(source: Path, target: Path, *, plugin: str = "", **settings: str) -> Decision:
    """Point ``target`` at ``source`` with a symlink, or copy and say so.

    The bridge exists so one edit applies everywhere. A copy forks silently: the
    harness with the copy keeps showing the old text and nothing indicates why,
    which is exactly what ``link`` was made the default to avoid. Publishing a
    link through :func:`publish_tree` produced a copy while the marker recorded
    ``bridge=link``, so status and uninstall could not tell the two apart.

    Falls back to copying when the platform refuses links (Windows without
    developer mode, some network filesystems) and records ``bridge=copy`` in the
    marker, so the fallback is visible rather than assumed.
    """
    if target.is_symlink() and Path(os.readlink(target)) == source:
        return Decision(Verdict.SKIP, target, "already linked")
    marker = read_marker(target) if target.is_dir() and not target.is_symlink() else None
    if target.exists() or target.is_symlink():
        if target.is_symlink() or owns(marker, plugin):
            target.unlink() if target.is_symlink() else shutil.rmtree(target)
        else:
            return Decision(Verdict.KEEP, target, "user-authored")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.symlink_to(source, target_is_directory=source.is_dir())
    except (OSError, NotImplementedError):
        publish_tree(source, target, plugin=plugin, **{**settings, "bridge": "copy"})
        return Decision(Verdict.PUBLISH, target, "copied: this platform cannot create links")
    return Decision(Verdict.PUBLISH, target, f"linked to {source}")


def withdraw_link(target: Path, inside: Path) -> bool:
    """Remove a link autorun made, identified by where it points.

    A symlink carries no marker, so its only evidence of ownership is its
    target: a link resolving into the shared skills root is one the bridge
    created. Without this, every bridged link survives uninstall forever —
    ``withdrawn`` refuses symlinks by design, and the marker sweep skips them
    because markers live on directories.
    """
    if not target.is_symlink():
        return False
    try:
        resolved = Path(os.readlink(target))
        resolved = resolved if resolved.is_absolute() else (target.parent / resolved)
        resolved.resolve().relative_to(inside.resolve())
    except (OSError, ValueError, RuntimeError):
        return False
    target.unlink()
    return True


def withdraw_files(directory: Path, *, plugin: str | None = None) -> tuple[str, ...]:
    """Remove only the files autorun recorded, leaving the directory and the
    user's own files in place.

    The removal twin of a *shared* publication. ForgeCode's ``commands/`` and
    OpenCode's ``commands/`` hold our files beside the user's, so
    :func:`withdrawn` is the wrong tool there — it would take the user's
    commands with ours. The marker already records exact filenames for this
    case, which is what makes per-file removal possible at all.

    A file whose fingerprint no longer matches is left alone: the user edited
    it, so it is theirs now. Returns the names actually removed.
    """
    if not directory.is_dir():
        return ()
    with FileLock(str(directory.parent / INSTALL_LOCK_NAME)):
        # Inside the lock, for the reason `withdrawn` gives: checking ownership,
        # releasing, then deleting leaves a window in which a concurrent install
        # republishes the files this call is about to remove.
        manifest = read_marker(directory)
        if manifest is None or (plugin is not None and not owns(manifest, plugin)):
            return ()
        removed = tuple(
            name
            for name in sorted(manifest.files)
            if manifest.state_of(directory, name) is FileState.UNCHANGED
        )
        for name in removed:
            (directory / name).unlink(missing_ok=True)
        (directory / OWNED_MARKER_NAME).unlink(missing_ok=True)
    return removed


@contextmanager
def json_document(path: Path, default: Callable[[], dict] = dict) -> Iterator[dict]:
    """Read-or-default, yield for mutation, write atomically — or not at all.

    Sixteen hand-rolled read-modify-write pairs preceded this, half of them
    using bare ``write_text``. A torn write to ``hooks.json`` or
    ``marketplace.json`` makes the harness drop every hook in the file, which is
    the failure mode behind claude-code#24115 and Codex's ``deny_unknown_fields``.

    An unreadable file raises rather than silently starting from ``default``:
    clobbering a document we could not parse is how a user's own registry
    entries disappear. An unchanged document is not rewritten, so a no-op
    install does not churn mtimes the harness watches.
    """
    if path.is_file():
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError(f"{path} must contain a JSON object")
    else:
        document = default()
    before = json.dumps(document, sort_keys=True)
    yield document
    if json.dumps(document, sort_keys=True) != before:
        atomic_write(path, json.dumps(document, indent=2) + "\n")


#: The copy side of ``IGNORED_GLOBS``. Never widen this list alone: copying a
#: file the manifest does not record leaves an unrecorded file inside an owned
#: tree, which then reads as neither ours nor the user's.
_IGNORED = shutil.ignore_patterns(*IGNORED_GLOBS)


def publish_tree(source: Path, target: Path, *, plugin: str = "", **settings: str) -> Decision:
    """The common case: copy a source tree in, unless the decision says not to.

    Every skill, extension, command bundle and plugin cache goes through this
    one call. The fourteen hand-rolled copy sites it replaces each answered
    "may I write here?" differently, and had each drifted from the others.
    """
    decision = decide(target, source, plugin=plugin)
    if decision.verdict in (Verdict.KEEP, Verdict.SKIP):
        return decision
    with published(target, plugin=plugin, **settings) as staged:
        shutil.copytree(source, staged, symlinks=True, ignore=_IGNORED)
    return decision


def dereference_links(root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Replace every symlink under ``root`` with what it points at.

    The inverse of :func:`publish_tree`'s ``symlinks=True``, and here rather
    than in a harness module for that reason: nothing about flattening a tree is
    harness-specific, and this module is the only one allowed to mutate a tree.

    The caller that needs it is a packager whose consumer ignores symlinks — the
    Codex plugin cache does, so a staged ``SKILL.md`` that is a link is simply
    absent from the packaged plugin and the skill ships with no content, with
    nothing reporting it. Autorun creates such links deliberately (the
    shared-skills bridge), so staging has to flatten them.

    A link whose target is missing is left in place and named: replacing it with
    nothing would hide a broken bridge instead of surfacing it.

    Returns ``(replaced, broken)``, both relative to ``root``. Callers have
    wanted each: the count for a report, the names for a warning.
    """
    replaced, broken = [], []
    for path in sorted(root.rglob("*")):
        if not path.is_symlink():
            continue
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            broken.append(str(path.relative_to(root)))
            continue
        path.unlink()
        if resolved.is_dir():
            shutil.copytree(resolved, path, symlinks=False)
        else:
            shutil.copy2(resolved, path)
        replaced.append(str(path.relative_to(root)))
    return tuple(replaced), tuple(broken)


def demo() -> None:
    """Self-check covering every regression the per-file draft introduced, plus
    the transaction guarantees the manifest draft only stubbed out."""

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "src" / "demo"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("version one\n", encoding="utf-8")
        # Regression 1: binary asset. Regression 2: executable bit.
        (source / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00binary")
        script = source / "run.sh"
        script.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        # Regression 3: symlink.
        (source / "linked.md").symlink_to(source / "SKILL.md")

        target = root / "dest" / "demo"
        assert publish_tree(source, target, plugin="ar").verdict is Verdict.PUBLISH

        assert (target / "logo.png").read_bytes().startswith(b"\x89PNG"), "binary survives"
        assert os.stat(target / "run.sh").st_mode & stat.S_IXUSR, "exec bit survives"
        assert (target / "linked.md").is_symlink(), "symlink survives"
        assert (target / OWNED_MARKER_NAME).is_file(), "marker landed with the contents"

        # An untouched tree is recognised as current, and republishing is a no-op.
        assert decide(target, source, plugin="ar").verdict is Verdict.SKIP
        assert publish_tree(source, target, plugin="ar").verdict is Verdict.SKIP

        # Build junk beside a source file is neither copied nor recorded, so it
        # cannot make an identical tree look stale and be rewritten every run.
        (source / "SKILL.md~").write_text("editor backup\n", encoding="utf-8")
        (source / "notes.pyc").write_bytes(b"\x00")
        assert decide(target, source, plugin="ar").verdict is Verdict.SKIP, "junk is invisible"
        assert not (target / "SKILL.md~").exists()

        # THE POINT: an edit inside a tree we own is detected and respected.
        (target / "SKILL.md").write_text("MY OWN EDIT\n", encoding="utf-8")
        (source / "SKILL.md").write_text("version two\n", encoding="utf-8")
        kept = publish_tree(source, target, plugin="ar")
        assert kept.verdict is Verdict.KEEP, kept
        assert kept.edited == ("SKILL.md",), kept.edited
        assert (target / "SKILL.md").read_text() == "MY OWN EDIT\n", "the edit survived"
        assert "SKILL.md" in kept.describe()

        # Flipping the executable bit counts as an edit, not a silent rewrite.
        (target / "SKILL.md").write_text("version one\n", encoding="utf-8")
        os.chmod(target / "run.sh", os.stat(target / "run.sh").st_mode & ~stat.S_IXUSR)
        assert "run.sh" in decide(target, source, plugin="ar").edited

        # Another plugin's tree is never touched, and never removed.
        publish_tree(source, root / "dest" / "other", plugin="pdf-extractor")
        assert decide(root / "dest" / "other", source, plugin="ar").verdict is Verdict.KEEP
        assert withdrawn(root / "dest" / "other", plugin="ar") is False

        # A retired tree goes only while it is still exactly ours.
        shutil.rmtree(target)
        publish_tree(source, target, plugin="ar")
        assert decide(target, None, plugin="ar").verdict is Verdict.RETIRE
        (target / "SKILL.md").write_text("MINE NOW\n", encoding="utf-8")
        assert decide(target, None, plugin="ar").verdict is Verdict.KEEP

        # An unmarked directory is the user's, whatever its name.
        theirs = root / "dest" / "theirs"
        theirs.mkdir()
        (theirs / "SKILL.md").write_text("hand written\n", encoding="utf-8")
        assert decide(theirs, source, plugin="ar").verdict is Verdict.KEEP
        assert withdrawn(theirs) is False
        assert (theirs / "SKILL.md").is_file(), "never removed"

        # A legacy prose marker still means ours.
        legacy = root / "dest" / "legacy"
        legacy.mkdir()
        (legacy / OWNED_MARKER_NAME).write_text("plugin=ar\nmode=copy\n", encoding="utf-8")
        assert read_marker(legacy) is not None
        assert read_marker(legacy).settings["mode"] == "copy"
        assert withdrawn(legacy) is True and not legacy.exists()

        # A pre-manifest marker upgrades rather than blocking on unmeasured files.
        publish_tree(source, target := root / "dest" / "old", plugin="ar")
        atomic_write(target / OWNED_MARKER_NAME,
                     json.dumps({"plugin": "ar", "files": ["SKILL.md"]}))
        assert decide(target, source, plugin="ar").verdict is Verdict.PUBLISH

        # Publish restores the previous tree when the caller fails mid-write.
        keep = root / "dest" / "rollback"
        publish_tree(source, keep, plugin="ar")
        try:
            with published(keep, plugin="ar") as staged:
                staged.mkdir()
                (staged / "partial").write_text("half\n", encoding="utf-8")
                raise RuntimeError("interrupted")
        except RuntimeError:
            pass
        assert (keep / "SKILL.md").is_file(), "previous copy restored"
        assert not (keep / "partial").exists(), "no partial contents"

        # json_document: default, mutate, atomic write, and no-op on no change.
        doc = root / "registry.json"
        with json_document(doc, lambda: {"plugins": {}}) as d:
            d["plugins"]["ar"] = {"enabled": True}
        assert json.loads(doc.read_text())["plugins"]["ar"]["enabled"] is True
        stamp = doc.stat().st_mtime_ns
        with json_document(doc) as d:
            d["plugins"]["ar"]["enabled"] = True  # same value
        assert doc.stat().st_mtime_ns == stamp, "unchanged document is not rewritten"

    print("installer.fs: all self-checks passed")


if __name__ == "__main__":
    demo()
