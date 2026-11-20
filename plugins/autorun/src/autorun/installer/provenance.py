"""Whether autorun wrote an unmarked tree: proof that does not live in the tree.

Ownership is the ``.autorun-owned`` marker inside each tree (:mod:`fs`). A
marker is a dotfile in a directory the user may manage: a ``~/.agents`` kept in
git ignored it, lost it, and every skill an earlier release had copied there
then read as user-authored. rc4 left those copies in place and, because Codex
reads that root, withheld its own packaged copies, so Codex ran months-old
skills with nothing to say why.

This module answers the question the marker can no longer answer, and answers
it only with an exact match, so an edited tree stays the user's:

1. the tree is byte-identical to the source being installed now ("identical"
   reads every file but the marker and :data:`REGENERATED_GLOBS`, so a user's
   ``SKILL.md.bak`` or ``.git`` inside the tree makes it the user's);
2. the tree is byte-identical to what autorun last published at that path,
   per the receipt :func:`record` keeps in autorun's own state directory,
   which the user's ignore rules and cleans never reach;
3. the tree is byte-identical to a skill version autorun committed before
   receipts existed (:data:`LEGACY_DIGESTS`, generated from the repository
   history by ``scripts/legacy_skill_digests.py`` and frozen: receipts cover
   every later install).

The answer is the ``ownership_proof`` :func:`fs.decide` and :func:`fs.withdrawn`
already accept for harness receipts, so adoption and retirement reuse their
existing, lock-checked paths and nothing about a marked tree changes.

Complexity: a digest is one :func:`fs.scan_tree` of the tree (O(bytes)); the
receipt store is one small JSON document read per question.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from contextlib import nullcontext
from pathlib import Path
from typing import Callable, Iterator, Mapping

from filelock import FileLock

from .fs import (
    _LINK,
    INSTALL_LOCK_NAME,
    REGENERATED_GLOBS,
    SKIP_NAMES,
    TreeManifest,
    _fingerprint,
    autorun_state_dir,
    json_document,
    owns,
    read_marker,
    scan_tree,
)

__all__ = [
    "LEGACY_DIGESTS",
    "REGENERATED_GLOBS",
    "autorun_wrote",
    "forget",
    "proof_for",
    "proven_trees",
    "receipts_path",
    "record",
    "tree_digest",
    "tree_digests",
]


def _load_legacy() -> Mapping[str, Mapping[str, tuple[str, ...]]]:
    try:
        from ._legacy_skill_digests import DIGESTS
    except ImportError:  # a trimmed build without the catalog proves nothing extra
        return {}
    return DIGESTS


#: plugin -> skill name -> digests of every version committed before receipts.
LEGACY_DIGESTS: Mapping[str, Mapping[str, tuple[str, ...]]] = _load_legacy()


def _digest(manifest: Mapping[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(sorted(manifest.items()), separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]


# ``REGENERATED_GLOBS`` (imported from ``fs``, its one definition) is what an
# installed tree's digest leaves out: ``fs.IGNORED_GLOBS`` also skips ``*.bak``,
# ``*~``, ``*.tmp``, ``.git`` and ``.venv``, which a copy autorun writes never
# holds, so inside an unmarked directory they are the user's work.
_UNREAD_DIRECTORY = "unread-directory"


def tree_digest(root: Path) -> str:
    """An installed tree's content, marker and regenerated junk excluded.

    Every other file counts, whatever its name, so one file autorun did not
    write makes the digest match nothing. ``scan_tree`` applies the ignore
    patterns to directory names too, so a directory the user named like one
    (``._notes/``, ``cache.pyc/``) would hide everything inside it. The
    junk is files; such a directory is recorded as an entry of its own and
    the digest matches nothing autorun wrote.
    """
    manifest = scan_tree(root, ignored_globs=REGENERATED_GLOBS)
    for directory, subdirs, _names in os.walk(root):
        here = Path(directory)
        for sub in subdirs:
            if any(fnmatch.fnmatch(sub, pattern) for pattern in REGENERATED_GLOBS):
                manifest[(here / sub).relative_to(root).as_posix() + "/"] = _UNREAD_DIRECTORY
        subdirs[:] = [
            d for d in subdirs
            if not (here / d).is_symlink()
            and not any(fnmatch.fnmatch(d, pattern) for pattern in REGENERATED_GLOBS)
        ]
    return _digest(manifest)


def tree_digests(root: Path) -> frozenset[str]:
    """Every digest a copy of the source ``root`` can have once published.

    Read as the copy reads it (``fs.IGNORED_GLOBS`` left out), not as
    :func:`tree_digest` reads an installed tree.

    A tree publication keeps links (``copytree(symlinks=True)``); earlier
    installs dereferenced them, writing the file a link named. Both are what
    autorun wrote, so both count. A link whose target is missing or outside
    the tree's own files has no dereferenced form and keeps its link entry.
    """
    raw = scan_tree(root)
    resolved = dict(raw)
    for relative, fingerprint in raw.items():
        if not fingerprint.startswith(_LINK):
            continue
        try:
            target = (root / relative).resolve()
            if target.is_file():
                resolved[relative] = _fingerprint(target)
        except (OSError, RuntimeError):
            continue
    return frozenset({_digest(raw), _digest(resolved)})


def receipts_path() -> Path:
    """Where the publication receipts live: autorun's state, not the tree."""
    return autorun_state_dir() / "installer" / "published-trees.json"


def _key(target: Path) -> str:
    return os.path.normcase(str(target.resolve()))


def record(target: Path, plugin: str, digest: str) -> None:
    """Save the digest captured before the published tree became visible.

    Nothing is saved when the tree at ``target`` no longer has that digest:
    a later publish or an edit has replaced what this publish wrote, and a
    receipt for bytes that are not there would prove ownership of whatever
    tree later matched them. The comparison and the write run under the lock
    every publisher of ``target`` holds while it swaps, so no publish can land
    between them.
    """
    if target.is_symlink() or not target.is_dir():
        # Withdrawn since the publish (a lock here would recreate its parent),
        # or replaced by a link: its contents are another folder's, and the
        # receipt would be filed under that folder's path.
        return
    path = receipts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        one_directory = path.parent.samefile(target.parent)
    except OSError:
        return  # which locks to take is unknown; taking the same one twice raises
    # json_document locks the receipts file's directory under the same lock
    # name. When that directory is also the target's parent, its lock is the
    # publication lock, and a second FileLock on it in this process raises.
    publication_lock = (
        nullcontext() if one_directory else FileLock(str(target.parent / INSTALL_LOCK_NAME))
    )
    with publication_lock, json_document(path) as receipts:
        if tree_digest(target) == digest:
            receipts[_key(target)] = {"plugin": plugin, "digest": digest}


def forget(target: Path) -> None:
    """Drop the receipt for a tree autorun removed."""
    path = receipts_path()
    if not path.is_file():
        return
    with json_document(path) as receipts:
        receipts.pop(_key(target), None)


def _receipt(target: Path) -> Mapping[str, str] | None:
    try:
        receipts = json.loads(receipts_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entry = receipts.get(_key(target)) if isinstance(receipts, dict) else None
    return entry if isinstance(entry, dict) else None


def autorun_wrote(
    target: Path,
    *,
    plugin: str,
    name: str = "",
    source: Path | None = None,
    catalog: bool = True,
) -> bool:
    """Whether ``target`` is, byte for byte, a tree autorun wrote for ``plugin``.

    Only an exact match counts: a tree differing in one byte from every
    version autorun knows is the user's, which is the only safe reading of a
    tree with no marker.
    """
    if not target.is_dir() or target.is_symlink():
        return False
    try:
        current = tree_digest(target)
        if source is not None and source.is_dir() and current in tree_digests(source):
            return True
    except OSError:
        return False
    receipt = _receipt(target)
    if (
        receipt is not None
        and receipt.get("digest") == current
        and owns(TreeManifest(plugin=str(receipt.get("plugin", ""))), plugin)
    ):
        return True
    return catalog and current in LEGACY_DIGESTS.get(plugin, {}).get(name or target.name, ())


def proof_for(
    *, plugin: str, source: Path | None = None, name: str = ""
) -> Callable[[Path], bool]:
    """``autorun_wrote`` bound as the ``ownership_proof`` :func:`fs.decide` takes."""
    return lambda target: autorun_wrote(target, plugin=plugin, name=name, source=source)


def proven_trees(
    root: Path, *, plugin: str, max_depth: int = 5, catalog_root: Path | None = None
) -> Iterator[Path]:
    """Unmarked trees under ``root`` that autorun provably wrote for ``plugin``.

    The retirement sweep's other half: :func:`fs.owned_trees` finds what still
    carries the marker, this finds what lost it. A receipt names the exact
    path autorun published, so it counts anywhere. The legacy catalog only
    describes copies earlier releases put directly in the shared skills root
    (``catalog_root``, default :func:`discovery.shared_root`): a same-named
    directory nested in someone else's tree (a user's own plugin bundle that
    carries copies of autorun skills) is part of that tree, never autorun's to
    retire. Only those candidates are fingerprinted, so a large root costs a
    directory listing, not a hash of everything under it.
    """
    if not root.is_dir():
        return
    if catalog_root is None:
        from .discovery import shared_root

        catalog_root = shared_root()
    try:
        catalog_parent = catalog_root.resolve()
    except OSError:
        catalog_parent = None
    named = set(LEGACY_DIGESTS.get(plugin, {}))
    try:
        receipts = json.loads(receipts_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        receipts = {}
    receipted = set(receipts) if isinstance(receipts, dict) else set()
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
            if read_marker(child) is not None:
                continue  # marked trees are owned_trees' to report
            receipted_here = _key(child) in receipted
            catalogued_here = child.name in named and child.parent.resolve() == catalog_parent
            if (receipted_here or catalogued_here) and autorun_wrote(
                child, plugin=plugin, catalog=catalogued_here
            ):
                yield child
                continue
            if depth + 1 < max_depth:
                stack.append((child, depth + 1))


def demo() -> None:
    """Self-check: exact matches are autorun's, an edit is the user's."""
    import tempfile

    from .fs import OWNED_MARKER_NAME, publish_tree

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        previous = os.environ.get("AUTORUN_HOME")
        # The receipt store follows AUTORUN_HOME; never the developer's own.
        os.environ["AUTORUN_HOME"] = str(root / "ar-home")
        try:
            source = root / "src" / "commit"
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text("# v1\n", encoding="utf-8")
            target = root / "skills" / "commit"
            publish_tree(source, target, plugin="ar")
            record(target, "ar", tree_digest(target))
            (target / OWNED_MARKER_NAME).unlink()  # the user's tooling lost it

            (source / "SKILL.md").write_text("# v2\n", encoding="utf-8")
            assert autorun_wrote(target, plugin="ar", source=source), "receipt proves it"
            assert not autorun_wrote(target, plugin="pdf-extractor"), "scoped by plugin"
            assert list(proven_trees(root / "skills", plugin="ar")) == [target], "by receipt"

            (target / "SKILL.md").write_text("# mine\n", encoding="utf-8")
            assert not autorun_wrote(target, plugin="ar", source=source), "an edit is the user's"
            forget(target)
            assert _receipt(target) is None
        finally:
            if previous is None:
                os.environ.pop("AUTORUN_HOME", None)
            else:
                os.environ["AUTORUN_HOME"] = previous
    print("installer.provenance: all self-checks passed")


if __name__ == "__main__":
    demo()
