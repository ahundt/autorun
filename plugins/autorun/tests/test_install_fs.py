"""Ownership and atomicity contract for every tree autorun writes.

These are the thirteen edge cases the current installer handles across four
divergent removal policies, three copies of the stage-and-rename dance, and a
marker that records names but not contents. Each test names the failure it
prevents, because the point of pinning them before the rewrite is that a
replacement which loses one of them looks identical to one that does not.

The fourteenth is the open defect: a user edit inside a directory autorun owns
was silently destroyed on the next install. A marker states a fact about a
directory and structurally cannot record what the contents were, which is why
the fix is a per-file digest rather than a stricter check.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.installer.fs import (  # noqa: E402
    Decision,
    INSTALL_LOCK_NAME,
    OWNED_MARKER_NAME,
    Verdict,
    compare,
    decide,
    json_document,
    publish_tree,
    published,
    read_marker,
    withdraw_files,
    withdrawn,
)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """A plugin tree with the four content shapes that broke earlier drafts."""
    root = tmp_path / "src" / "demo"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("version one\n", encoding="utf-8")
    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00binary")
    script = root / "run.sh"
    script.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    (root / "linked.md").symlink_to(root / "SKILL.md")
    return root


# ─── Ownership and user data (edge cases 1-8) ────────────────────────────────


def test_a_large_edit_report_stays_readable(tmp_path):
    edits = tuple(f"changed-{index}.txt" for index in range(12))
    decision = Decision(Verdict.KEEP, tmp_path / "owned", "user-edited", edits)

    report = decision.describe()

    assert "changed-0.txt" in report
    assert "changed-9.txt" in report
    assert "changed-10.txt" not in report
    assert "+2 more" in report
    assert decision.edited == edits


def test_an_unmarked_directory_is_the_users_whatever_its_name(tmp_path, source):
    """Edge case 1. A directory autorun did not create is never replaced and
    never removed, even when its name is one autorun would have used."""
    theirs = tmp_path / "dest" / "demo"
    theirs.mkdir(parents=True)
    (theirs / "SKILL.md").write_text("hand written\n", encoding="utf-8")

    decision = publish_tree(source, theirs, plugin="ar")

    assert decision.verdict is Verdict.KEEP
    assert decision.reason == "user-authored"
    assert (theirs / "SKILL.md").read_text() == "hand written\n"
    assert withdrawn(theirs) is False
    assert (theirs / "SKILL.md").is_file(), "refused removal must not delete"


def test_exact_external_receipt_can_adopt_but_is_rechecked_inside_lock(
    tmp_path, source
):
    """A receipt proof that disappears before publication cannot authorize it."""
    target = tmp_path / "dest" / "demo"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("installed by harness\n", encoding="utf-8")
    checks = iter((True, False))

    decision = publish_tree(
        source,
        target,
        plugin="ar",
        ownership_proof=lambda _path: next(checks),
    )

    assert decision.verdict is Verdict.KEEP
    assert decision.reason == "user-authored"
    assert (target / "SKILL.md").read_text() == "installed by harness\n"
    assert read_marker(target) is None


def test_the_marker_records_the_registered_plugin_name_not_the_directory_name(
    tmp_path, source
):
    """Edge case 2. The plugin registers as `ar` while its directory is named
    `autorun`; recording the directory name makes a later scoped uninstall miss
    it entirely."""
    target = tmp_path / "dest" / "autorun"
    publish_tree(source, target, plugin="ar")

    assert read_marker(target).plugin == "ar"
    assert withdrawn(target, plugin="autorun") is False, "directory name is not identity"
    assert withdrawn(target, plugin="ar") is True


def test_a_shared_directory_is_emptied_per_file_not_wholesale(tmp_path):
    """Edge case 3. ForgeCode's `commands/` holds our files beside the user's,
    so the marker records exact filenames and removal touches only those.
    Removing the directory would take the user's commands with it."""
    shared = tmp_path / "forge" / "commands"
    shared.mkdir(parents=True)
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "ar-go.md").write_text("ours\n", encoding="utf-8")
    (staged / "ar-stop.md").write_text("ours\n", encoding="utf-8")

    for name in ("ar-go.md", "ar-stop.md"):
        shutil.copy2(staged / name, shared / name)
    (shared / "my-own.md").write_text("theirs\n", encoding="utf-8")
    publish_marker_for_shared(shared, "ar", ("ar-go.md", "ar-stop.md"))

    assert withdraw_files(shared, plugin="ar") == ("ar-go.md", "ar-stop.md")
    assert not (shared / "ar-go.md").exists()
    assert (shared / "my-own.md").read_text() == "theirs\n", "user file survives"
    assert shared.is_dir(), "a shared directory is never removed"


def test_a_shared_directory_takes_a_changed_source(tmp_path):
    """decide_files compared the destination against the manifest and never the
    source, so a shipped file that changed still read as "already current".
    _perform acts on PUBLISH and not SKIP, which froze ForgeCode's and
    OpenCode's commands/ at whatever the first install wrote — forever, with
    every later run reporting success."""
    from autorun.installer.fs import decide_files, publish_files

    source = tmp_path / "src"
    source.mkdir()
    (source / "ar-go.md").write_text("v1\n", encoding="utf-8")
    destination = tmp_path / "forge" / "commands"

    assert publish_files(source, destination, plugin="ar").verdict is Verdict.PUBLISH
    assert decide_files(source, destination, plugin="ar").verdict is Verdict.SKIP

    (source / "ar-go.md").write_text("v2\n", encoding="utf-8")

    assert decide_files(source, destination, plugin="ar").verdict is Verdict.PUBLISH
    publish_files(source, destination, plugin="ar")
    assert (destination / "ar-go.md").read_text(encoding="utf-8") == "v2\n"


def test_a_directory_we_do_not_own_never_reaches_the_lock(tmp_path):
    """Taking the install lock writes a file into the parent. Reading ownership
    only inside the lock turned a read-only parent holding someone else's
    commands/ into a PermissionError that aborted the whole uninstall instead
    of skipping one directory."""
    import os
    import stat

    from autorun.installer.fs import withdraw_files

    parent = tmp_path / "readonly"
    theirs = parent / "commands"
    theirs.mkdir(parents=True)
    (theirs / "their-command.md").write_text("hand written\n", encoding="utf-8")
    os.chmod(parent, stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert withdraw_files(theirs, plugin="ar") == ()
    finally:
        os.chmod(parent, 0o755)

    assert (theirs / "their-command.md").is_file()


def publish_marker_for_shared(directory: Path, plugin: str, names: tuple[str, ...]) -> None:
    """Claim only ``names`` inside a directory autorun shares with the user."""
    from autorun.installer.fs import TreeManifest, atomic_write, _fingerprint

    manifest = TreeManifest(
        plugin=plugin, files={n: _fingerprint(directory / n) for n in names}
    )
    atomic_write(
        directory / OWNED_MARKER_NAME, json.dumps(manifest.as_payload(), indent=2)
    )


def test_a_legacy_prose_marker_still_means_ours(tmp_path):
    """Edge case 4. Markers predating the JSON format were `key=value` prose.
    They still mean autorun created the directory, so an upgrade must not
    strand every tree an older install claimed."""
    legacy = tmp_path / "dest" / "legacy"
    legacy.mkdir(parents=True)
    (legacy / OWNED_MARKER_NAME).write_text("plugin=ar\nmode=copy\n", encoding="utf-8")

    marker = read_marker(legacy)

    assert marker is not None, "a legacy marker is not user-authored"
    assert marker.settings["mode"] == "copy", "its settings still decode"
    assert withdrawn(legacy) is True and not legacy.exists()


def test_an_unrecorded_plugin_survives_a_scoped_uninstall(tmp_path, source):
    """Edge case 5. Uninstalling one plugin must leave the others, including
    trees another plugin claimed."""
    mine = tmp_path / "dest" / "ar"
    theirs = tmp_path / "dest" / "pdf"
    publish_tree(source, mine, plugin="ar")
    publish_tree(source, theirs, plugin="pdf-extractor")

    assert withdrawn(mine, plugin="ar") is True
    assert withdrawn(theirs, plugin="ar") is False
    assert (theirs / "SKILL.md").is_file(), "another plugin's tree is untouched"


def test_a_copy_mode_bridge_records_its_mode_so_uninstall_can_reverse_it(
    tmp_path, source
):
    """Edge case 6. A bridge that copied rather than linked leaves a real
    directory; without the mode in the marker, uninstall cannot tell it from a
    native install and leaves it behind."""
    target = tmp_path / "dest" / "bridged"
    publish_tree(source, target, plugin="ar", bridge="copy")

    assert read_marker(target).settings["bridge"] == "copy"


def test_a_bridged_symlink_is_identified_by_its_target(tmp_path, source):
    """Edge case 7. A link-mode bridge writes a symlink, which carries no
    marker; it is recognisable because it resolves into the shared root."""
    shared = tmp_path / "shared" / "demo"
    shutil.copytree(source, shared, symlinks=True)
    link = tmp_path / "dest" / "demo"
    link.parent.mkdir(parents=True)
    link.symlink_to(shared)

    assert link.is_symlink()
    assert link.resolve() == shared.resolve()
    assert withdrawn(link) is False, "a symlink is not a directory we own outright"


def test_registration_link_refuses_a_sibling_source(tmp_path, source):
    """An in-root sibling is not exact ownership of a native extension link."""
    from autorun.installer import fs

    expected = tmp_path / "sources" / "gemini" / "ar"
    sibling = tmp_path / "sources" / "gemini" / "ar-user"
    shutil.copytree(source, expected)
    shutil.copytree(source, sibling)
    installed = tmp_path / "home" / ".gemini" / "extensions" / "ar"
    installed.parent.mkdir(parents=True)
    installed.symlink_to(sibling, target_is_directory=True)

    decision = fs.decide_link(
        None,
        installed,
        expected.parent,
        plugin="ar",
        exact_target=expected,
    )

    assert decision.verdict is fs.Verdict.KEEP
    assert fs.withdraw_link(
        installed, expected.parent, exact_target=expected
    ) is False
    assert installed.is_symlink()
    assert (sibling / "SKILL.md").is_file()


def test_native_extension_receipts_do_not_look_like_user_edits(tmp_path, source):
    """Harness-owned receipt files stay outside autorun's content snapshot."""
    from autorun.installer import fs

    target = tmp_path / "extension"
    fs.publish_tree(source, target, plugin="ar")
    (target / ".gemini-extension-install.json").write_text("{}", encoding="utf-8")
    (target / "qwen-extension.json").write_text("{}", encoding="utf-8")

    marker = fs.read_marker(target)
    assert marker is not None
    assert fs.compare(target, marker) == ((), (), ())


def test_the_marker_lands_in_the_same_rename_as_the_contents(tmp_path, source):
    """Edge case 8. Writing the marker after the swap leaves a window where the
    tree exists unclaimed, and a crash there makes it permanently user-authored
    in autorun's eyes."""
    target = tmp_path / "dest" / "demo"
    with published(target, plugin="ar") as staged:
        shutil.copytree(source, staged, symlinks=True)
        assert not target.exists(), "nothing is visible until the rename"

    assert (target / OWNED_MARKER_NAME).is_file()
    assert (target / "SKILL.md").is_file()


# ─── Atomicity and interruption (edge cases 9-13) ────────────────────────────


def test_a_failed_publish_restores_the_previous_contents(tmp_path, source):
    """Edge case 9. Stage beside the target, move the old aside, rename the new
    in, and put the old back if that fails."""
    target = tmp_path / "dest" / "demo"
    publish_tree(source, target, plugin="ar")

    with pytest.raises(RuntimeError):
        with published(target, plugin="ar") as staged:
            staged.mkdir()
            (staged / "partial").write_text("half\n", encoding="utf-8")
            raise RuntimeError("interrupted")

    assert (target / "SKILL.md").read_text() == "version one\n"
    assert not (target / "partial").exists()


def test_a_failed_shared_publish_restores_every_file_and_the_receipt(tmp_path, monkeypatch):
    from autorun.installer import fs

    source = tmp_path / "source"
    source.mkdir()
    (source / "a.md").write_text("v1\n", encoding="utf-8")
    shared = tmp_path / "commands"
    fs.publish_files(source, shared, plugin="ar")
    (shared / "user.md").write_text("mine\n", encoding="utf-8")
    before = {str(p.relative_to(shared)): p.read_bytes() for p in shared.rglob("*") if p.is_file()}

    (source / "a.md").write_text("v2\n", encoding="utf-8")
    (source / "b.md").write_text("v2\n", encoding="utf-8")
    real_copy = fs.shutil.copy2
    calls = 0

    def fail_second_copy(src, dst, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(fs.shutil, "copy2", fail_second_copy)
    with pytest.raises(OSError, match="disk full"):
        fs.publish_files(source, shared, plugin="ar")

    after = {str(p.relative_to(shared)): p.read_bytes() for p in shared.rglob("*") if p.is_file()}
    assert after == before


def test_a_failed_link_and_copy_fallback_restores_the_owned_tree(tmp_path, monkeypatch):
    from autorun.installer import fs

    old_source = tmp_path / "old"
    old_source.mkdir()
    (old_source / "SKILL.md").write_text("old\n", encoding="utf-8")
    target = tmp_path / "bridge"
    fs.publish_tree(old_source, target, plugin="ar")
    before = {str(p.relative_to(target)): p.read_bytes() for p in target.rglob("*") if p.is_file()}

    new_source = tmp_path / "new"
    new_source.mkdir()
    (new_source / "SKILL.md").write_text("new\n", encoding="utf-8")

    def links_unavailable(*args, **kwargs):
        raise OSError("links unavailable")

    def copy_failed(*args, **kwargs):
        raise OSError("copy failed")

    monkeypatch.setattr(Path, "symlink_to", links_unavailable)
    monkeypatch.setattr(fs.shutil, "copytree", copy_failed)
    with pytest.raises(OSError, match="copy failed"):
        fs.publish_link(new_source, target, plugin="ar")

    after = {str(p.relative_to(target)): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    assert after == before


def test_a_failed_removal_puts_the_directory_back(tmp_path, source, monkeypatch):
    """Edge case 10. Removal quarantines first and deletes the copy, so a
    failure mid-delete restores rather than leaving a half-emptied tree."""
    target = tmp_path / "dest" / "demo"
    publish_tree(source, target, plugin="ar")

    def exploding_rmtree(path, *args, **kwargs):
        assert not kwargs.get("ignore_errors"), "a failed delete must not be swallowed"
        raise OSError("disk full")

    monkeypatch.setattr("autorun.installer.fs.shutil.rmtree", exploding_rmtree)

    with pytest.raises(OSError):
        withdrawn(target, plugin="ar")

    assert (target / "SKILL.md").read_text() == "version one\n", "restored intact"


def test_ownership_is_rechecked_inside_the_lock(tmp_path, source):
    """Edge case 11. Checking ownership, releasing, then removing leaves a
    window in which a user-authored directory can take that path. The recheck
    must happen under the same lock as the mutation."""
    target = tmp_path / "dest" / "demo"
    publish_tree(source, target, plugin="ar")
    (target / OWNED_MARKER_NAME).unlink()  # becomes user-authored

    assert withdrawn(target) is False
    assert (target / "SKILL.md").is_file()


def test_the_lock_lives_in_the_parent_so_it_outlives_the_target(tmp_path, source):
    """Edge case 12. A lock inside the directory being replaced is destroyed by
    the very rename it is guarding."""
    target = tmp_path / "dest" / "demo"

    with published(target, plugin="ar") as staged:
        shutil.copytree(source, staged, symlinks=True)
        # Probed inside the transaction: filelock removes the lockfile when it
        # releases, so its absence afterwards says nothing about where it was.
        assert (target.parent / INSTALL_LOCK_NAME).exists()
        assert not (target / INSTALL_LOCK_NAME).exists()

    assert not (target / INSTALL_LOCK_NAME).exists(), "the lock is never published"


def test_an_interrupted_first_install_leaves_no_directory_at_all(tmp_path, source):
    """Edge case 13. When there was nothing to restore, a failure must leave
    nothing — never a partially copied tree that later reads as installed."""
    target = tmp_path / "dest" / "demo"

    with pytest.raises(RuntimeError):
        with published(target, plugin="ar") as staged:
            staged.mkdir()
            (staged / "partial").write_text("half\n", encoding="utf-8")
            raise RuntimeError("interrupted")

    assert not target.exists()


# ─── The open defect: edits inside a tree we own ─────────────────────────────


def test_a_user_edit_inside_an_owned_tree_survives_reinstall(tmp_path, source):
    """The defect this module exists to fix.

    `read_owned_marker` gates whether autorun may *claim* a directory. Once
    claimed, the whole tree was swapped, so "autorun never replaces a copy it
    does not own" held only for directories it never owned. Edits inside were
    never protected and vanished with no message.
    """
    target = tmp_path / "dest" / "demo"
    publish_tree(source, target, plugin="ar")

    (target / "SKILL.md").write_text("MY OWN EDIT\n", encoding="utf-8")
    (source / "SKILL.md").write_text("version two\n", encoding="utf-8")

    decision = publish_tree(source, target, plugin="ar")

    assert decision.verdict is Verdict.KEEP
    assert decision.edited == ("SKILL.md",)
    assert (target / "SKILL.md").read_text() == "MY OWN EDIT\n"
    assert "SKILL.md" in decision.describe(), "the report names the file"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows does not expose POSIX executable bits")
def test_flipping_the_executable_bit_counts_as_an_edit(tmp_path, source):
    """Losing the executable bit stops `hook_entry.py` being runnable, so mode
    is part of the fingerprint, not just bytes."""
    target = tmp_path / "dest" / "demo"
    publish_tree(source, target, plugin="ar")

    run = target / "run.sh"
    os.chmod(run, os.stat(run).st_mode & ~stat.S_IXUSR)

    assert "run.sh" in decide(target, source, plugin="ar").edited


def test_a_symlink_is_compared_by_target_not_by_content(tmp_path, source):
    """Following a link to hash its contents reports a healthy bridge link as
    edited the moment its target legitimately changes."""
    target = tmp_path / "dest" / "demo"
    publish_tree(source, target, plugin="ar")
    (target / "SKILL.md").write_text("upstream moved on\n", encoding="utf-8")

    edited, _, _ = compare(target, read_marker(target))

    assert "linked.md" not in edited, "the link itself is unchanged"


def test_build_junk_is_never_published_and_never_unrecorded(tmp_path, source):
    """Copying a file the manifest does not record leaves an unrecorded file
    inside an owned tree, which then reads as neither ours nor the user's.
    The copy exclusion and the scan exclusion must be the same set."""
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"\x00")
    (source / "stale.pyc").write_bytes(b"\x00")
    (source / "editor.md~").write_text("backup\n", encoding="utf-8")
    (source / ".coverage").write_text("coverage data\n", encoding="utf-8")
    (source / ".coverage.worker").write_text("parallel data\n", encoding="utf-8")
    (source / "coverage.xml").write_text("<coverage/>\n", encoding="utf-8")
    (source / "htmlcov").mkdir()
    (source / "htmlcov" / "index.html").write_text("report\n", encoding="utf-8")
    (source / ".ruff_cache").mkdir()
    (source / ".ruff_cache" / "CACHEDIR.TAG").write_text("cache\n", encoding="utf-8")

    target = tmp_path / "dest" / "demo"
    publish_tree(source, target, plugin="ar")

    assert not (target / "__pycache__").exists()
    assert not (target / "stale.pyc").exists()
    assert not (target / "editor.md~").exists()
    assert not (target / ".coverage").exists()
    assert not (target / ".coverage.worker").exists()
    assert not (target / "coverage.xml").exists()
    assert not (target / "htmlcov").exists()
    assert not (target / ".ruff_cache").exists()

    published_files = {
        str(p.relative_to(target))
        for p in target.rglob("*")
        if p.is_file() and p.name != OWNED_MARKER_NAME
    }
    assert published_files <= set(read_marker(target).files), "every published file is recorded"


def test_a_pre_manifest_marker_upgrades_instead_of_blocking(tmp_path, source):
    """A tree installed before fingerprinting has no digests. Reporting its
    files as edited would block every upgrade on directories an older autorun
    installed, so an empty manifest reports nothing."""
    target = tmp_path / "dest" / "demo"
    publish_tree(source, target, plugin="ar")
    (target / OWNED_MARKER_NAME).write_text(
        json.dumps({"plugin": "ar", "files": ["SKILL.md"]}), encoding="utf-8"
    )

    assert decide(target, source, plugin="ar").verdict is Verdict.PUBLISH


# ─── Registry documents ──────────────────────────────────────────────────────


def test_an_unparseable_registry_raises_instead_of_being_clobbered(tmp_path):
    """Starting from the default when a document will not parse is how a user's
    own hook and marketplace entries disappear."""
    doc = tmp_path / "hooks.json"
    doc.write_text("{ this is not json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        with json_document(doc, lambda: {"hooks": {}}):
            pass

    assert doc.read_text() == "{ this is not json", "left exactly as found"


def test_a_default_nobody_changed_is_never_materialized(tmp_path):
    """Opening a missing registry and changing nothing must not create it.
    Writing an empty document because someone looked is how a harness gains a
    config file it never asked for."""
    doc = tmp_path / "registry.json"

    with json_document(doc, lambda: {"plugins": {}}):
        pass

    assert not doc.exists()


def test_an_unchanged_registry_is_not_rewritten(tmp_path):
    """A no-op install must not churn an mtime the harness watches."""
    doc = tmp_path / "registry.json"
    with json_document(doc, lambda: {"plugins": {}}) as document:
        document["plugins"]["ar"] = {"enabled": True}
    stamp = doc.stat().st_mtime_ns

    with json_document(doc) as document:
        document["plugins"]["ar"] = {"enabled": True}  # same value

    assert doc.stat().st_mtime_ns == stamp
