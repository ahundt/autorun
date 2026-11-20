"""An unmarked tree autorun provably wrote is autorun's, not the user's.

Ownership lived only in the ``.autorun-owned`` marker inside each tree. A user
who keeps ``~/.agents`` in git ignored that file and lost it, after which every
skill an earlier release had copied there read as user-authored: rc4 left them
in place, and because Codex reads that root it also withheld its own packaged
copies, so Codex kept running months-old skills. An edited copy must still be
the user's; only a byte-exact match to something autorun wrote is adopted.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.installer import fs, provenance, traversal  # noqa: E402
from autorun.installer.fs import OWNED_MARKER_NAME, Decision, Verdict  # noqa: E402
from autorun.installer.traversal import Intent, _perform, retirements  # noqa: E402


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTORUN_HOME", str(tmp_path / "ar-home"))
    monkeypatch.setattr(provenance, "LEGACY_DIGESTS", {})


def _put(path: Path, text: str) -> None:
    """Write LF bytes on every OS: ``write_text`` turns ``\\n`` into ``\\r\\n`` on Windows,
    which would make a source and a deliberately CRLF copy byte-identical."""
    path.write_bytes(text.encode("utf-8"))


def _skill(root: Path, body: str = "# skill\n") -> Path:
    root.mkdir(parents=True)
    _put(root / "SKILL.md", body)
    return root


def _lose_marker(tree: Path) -> None:
    (tree / OWNED_MARKER_NAME).unlink()


def test_a_copy_identical_to_the_shipped_source_is_autoruns(tmp_path):
    source = _skill(tmp_path / "src" / "commit")
    copy = _skill(tmp_path / "shared" / "commit")
    assert provenance.autorun_wrote(copy, plugin="ar", source=source)
    _put(copy / "SKILL.md", "# mine now\n")
    assert not provenance.autorun_wrote(copy, plugin="ar", source=source)


def test_a_dereferenced_copy_of_a_linked_source_is_autoruns(tmp_path):
    """An install that dereferenced links wrote a regular file where the
    source had a symlink; both are what autorun publishes."""
    source = tmp_path / "src" / "tmux"
    source.mkdir(parents=True)
    _put(source / "GUIDE.md", "guide\n")
    try:
        (source / "SKILL.md").symlink_to("GUIDE.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need privileges here")
    copy = tmp_path / "shared" / "tmux"
    copy.mkdir(parents=True)
    _put(copy / "GUIDE.md", "guide\n")
    _put(copy / "SKILL.md", "guide\n")
    assert provenance.autorun_wrote(copy, plugin="ar", source=source)


def test_publishing_records_a_receipt_that_outlives_the_marker(tmp_path):
    source = _skill(tmp_path / "src" / "commit", "# v1\n")
    target = tmp_path / "skills" / "commit"
    intent = Intent(target=target, source=source, plugin="ar")
    assert _perform(intent, Decision(Verdict.PUBLISH, target, "new")).verdict is Verdict.PUBLISH
    _lose_marker(target)

    # The source moved on; the copy is the one autorun wrote, so it is autorun's.
    _put(source / "SKILL.md", "# v2\n")
    assert provenance.autorun_wrote(target, plugin="ar", source=source)
    assert not provenance.autorun_wrote(target, plugin="pdf-extractor", source=source)

    _put(target / "SKILL.md", "# edited after the marker was lost\n")
    assert not provenance.autorun_wrote(target, plugin="ar", source=source)


def test_receipt_excludes_an_edit_after_publication(tmp_path, monkeypatch):
    source = _skill(tmp_path / "src" / "local", "# shipped\n")
    target = tmp_path / "skills" / "local"
    ctx = traversal.Context(marketplace_root=tmp_path)
    publish = traversal.publish_tree

    def publish_then_edit(*args, **kwargs):
        result = publish(*args, **kwargs)
        _put(target / "SKILL.md", "# my edit\n")
        return result

    monkeypatch.setattr(traversal, "publish_tree", publish_then_edit)
    [decision] = traversal.run((), ctx, traversal.Mode.INSTALL, extra=[
        Intent(target=target, source=source, plugin="ar"),
    ])
    assert decision.verdict is Verdict.PUBLISH
    _lose_marker(target)

    intents = retirements([target.parent], [], plugins=["ar"])
    assert traversal.run((), ctx, traversal.Mode.UNINSTALL, extra=intents) == []
    assert (target / "SKILL.md").read_bytes() == b"# my edit\n"
    _put(target / "SKILL.md", "# shipped\n")
    assert provenance.autorun_wrote(target, plugin="ar")


def test_tree_receipt_accounts_for_writing_through_a_marker_symlink(tmp_path):
    source = _skill(tmp_path / "src" / "local")
    _put(source / "marker.json", "{}\n")
    try:
        (source / OWNED_MARKER_NAME).symlink_to("marker.json")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need privileges here")
    target = tmp_path / "skills" / "local"
    [decision] = traversal.run((), traversal.Context(marketplace_root=tmp_path),
                              traversal.Mode.INSTALL, extra=[
        Intent(target=target, source=source, plugin="ar", settings={"bridge": "copy"}),
    ])
    assert decision.verdict is Verdict.PUBLISH
    assert (target / OWNED_MARKER_NAME).is_symlink()
    _lose_marker(target)
    assert provenance.tree_digest(target) not in provenance.tree_digests(source)
    assert provenance.autorun_wrote(target, plugin="ar")


def test_retiring_forgets_the_receipt(tmp_path):
    source = _skill(tmp_path / "src" / "commit")
    target = tmp_path / "skills" / "commit"
    _perform(Intent(target=target, source=source, plugin="ar"), Decision(Verdict.PUBLISH, target, "new"))
    _perform(Intent(target=target, source=None, plugin="ar"), Decision(Verdict.RETIRE, target, "gone"))
    assert not target.exists()
    _skill(target, "# v1\n")  # the user makes their own at the same path
    _put(source / "SKILL.md", "# moved on\n")
    assert not provenance.autorun_wrote(target, plugin="ar", source=source)


def test_a_released_version_in_the_legacy_catalog_is_autoruns(tmp_path, monkeypatch):
    stale = _skill(tmp_path / "shared" / "mermaid-diagrams", "# old release\n")
    digest = provenance.tree_digest(stale)
    monkeypatch.setattr(
        provenance, "LEGACY_DIGESTS", {"autorun-skills": {"mermaid-diagrams": (digest,)}}
    )
    assert provenance.autorun_wrote(stale, plugin="autorun-skills")
    assert not provenance.autorun_wrote(stale, plugin="ar"), "another plugin's version"
    _put(stale / "SKILL.md", "# old release, edited\n")
    assert not provenance.autorun_wrote(stale, plugin="autorun-skills")


def test_decide_adopts_a_proven_unmarked_tree_and_keeps_an_edited_one(tmp_path):
    source = _skill(tmp_path / "src" / "commit", "# v2\n")
    copy = _skill(tmp_path / "shared" / "commit", "# v2\n")
    proof = provenance.proof_for(plugin="ar", source=source)
    assert fs.decide(copy, source, plugin="ar", ownership_proof=proof).verdict is Verdict.PUBLISH
    _put(copy / "SKILL.md", "# mine\n")
    assert fs.decide(copy, source, plugin="ar", ownership_proof=proof).verdict is Verdict.KEEP


def test_the_sweep_retires_an_unmarked_copy_autorun_wrote(tmp_path, monkeypatch):
    """rc1-rc3 copied skills into ~/.agents/skills; rc4 no longer ships there.
    A marked copy was always swept; one whose marker was lost was stranded."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    root = home / ".agents"
    stale = _skill(root / "skills" / "mermaid-diagrams", "# old release\n")
    mine = _skill(root / "skills" / "my-own", "# mine\n")
    monkeypatch.setattr(
        provenance,
        "LEGACY_DIGESTS",
        {"autorun-skills": {"mermaid-diagrams": (provenance.tree_digest(stale),)}},
    )
    found = list(retirements([root], [], plugins=["autorun-skills"]))
    assert [intent.target for intent in found] == [stale]
    [intent] = found
    assert intent.ownership_proof is not None and intent.ownership_proof(stale)
    assert fs.decide(stale, None, plugin="autorun-skills",
                     ownership_proof=intent.ownership_proof).verdict is Verdict.RETIRE
    assert mine.is_dir()


def test_a_catalogued_copy_inside_someone_elses_tree_is_left_alone(tmp_path, monkeypatch):
    """A user's own plugin bundle may carry copies of autorun skills. The
    catalog only describes copies earlier releases put directly in the shared
    skills root; a same-named directory nested in another tree is that tree's."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    foreign = _skill(home / ".gemini" / "plugins" / "ar" / "skills" / "commit", "# released\n")
    monkeypatch.setattr(
        provenance, "LEGACY_DIGESTS", {"ar": {"commit": (provenance.tree_digest(foreign),)}}
    )
    assert list(retirements([home / ".gemini"], [], plugins=["ar"])) == []
    assert foreign.is_dir()


def test_the_receipt_store_lives_in_autoruns_state_not_beside_the_tree(tmp_path):
    source = _skill(tmp_path / "src" / "commit")
    target = tmp_path / "skills" / "commit"
    _perform(Intent(target=target, source=source, plugin="ar"), Decision(Verdict.PUBLISH, target, "new"))
    assert provenance.receipts_path().is_file()
    assert provenance.receipts_path().is_relative_to(Path(os.environ["AUTORUN_HOME"]))
    assert sorted(p.name for p in target.iterdir()) == [OWNED_MARKER_NAME, "SKILL.md"]


def test_the_legacy_catalog_holds_the_dereferenced_tmux_automation_rc_copy():
    """The stale copy found on a real machine: SKILL.md was a symlink in git
    (f115ca56) and the install that wrote it dereferenced the link."""
    from autorun.installer import _legacy_skill_digests

    assert _legacy_skill_digests.DIGESTS, "the catalog shipped empty"
    assert all(
        isinstance(digest, str) and len(digest) == 32
        for names in _legacy_skill_digests.DIGESTS.values()
        for digests in names.values()
        for digest in digests
    )
    assert _legacy_skill_digests.DIGESTS["autorun-skills"]["tmux-automation"]


def _write(path: Path, text: str = "mine\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _put(path, text)
    return path


#: Names ``fs.IGNORED_GLOBS`` keeps out of a manifest. Inside an unmarked tree
#: each can still be the user's own work, which an exact-copy proof must see.
USER_CONTENT_UNDER_IGNORED_NAMES = (
    "SKILL.md.bak", "notes~", "draft.tmp", ".git/HEAD", ".venv/pyvenv.cfg",
    "__pycache__/my-notes.txt", "htmlcov/index.html", "coverage.xml",
)
#: What the operating system or Python writes into any directory in use; a
#: copy holding only these is still the copy autorun wrote.
REGENERATED_JUNK = (
    ".DS_Store", "._SKILL.md", "Thumbs.db", "desktop.ini",
    "__pycache__/helper.cpython-313.pyc", "scripts/__pycache__/x.cpython-312.pyo",
)


@pytest.mark.parametrize("extra", USER_CONTENT_UNDER_IGNORED_NAMES)
def test_a_copy_holding_the_users_file_under_an_ignored_name_is_not_proven(tmp_path, extra):
    """2026-11-17: a tree equal to the source in every file ``scan_tree`` reads
    was retired as "an exact copy autorun installed" with the user's own
    ``SKILL.md.bak`` and ``.git`` inside it, and no backup."""
    source = _skill(tmp_path / "src" / "commit")
    copy = _skill(tmp_path / "shared" / "commit")
    assert provenance.autorun_wrote(copy, plugin="ar", source=source)
    _write(copy / extra)
    assert not provenance.autorun_wrote(copy, plugin="ar", source=source)


@pytest.mark.parametrize("extra", REGENERATED_JUNK)
def test_a_copy_holding_only_regenerated_junk_is_still_autoruns(tmp_path, extra):
    source = _skill(tmp_path / "src" / "commit")
    copy = _skill(tmp_path / "shared" / "commit")
    _write(copy / extra)
    assert provenance.autorun_wrote(copy, plugin="ar", source=source)


@pytest.mark.parametrize("extra", USER_CONTENT_UNDER_IGNORED_NAMES)
def test_the_install_walk_keeps_a_stale_copy_that_holds_the_users_file(tmp_path, monkeypatch, extra):
    """The property at the entry point: after the sweep and the removal the
    walk performs, the user's file is still where they left it."""
    from autorun.installer.traversal import Context, Mode, run

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    root = home / ".agents"
    stale = _skill(root / "skills" / "mermaid-diagrams", "# old release\n")
    monkeypatch.setattr(
        provenance,
        "LEGACY_DIGESTS",
        {"autorun-skills": {"mermaid-diagrams": (provenance.tree_digest(stale),)}},
    )
    kept = _write(stale / extra)

    intents = list(retirements([root], [], plugins=["autorun-skills"]))
    run((), Context(marketplace_root=tmp_path / "marketplace", home=home), Mode.INSTALL, extra=intents)
    assert kept.read_text(encoding="utf-8") == "mine\n"
    assert (stale / "SKILL.md").is_file()


def test_the_install_walk_removes_a_stale_copy_autorun_wrote(tmp_path, monkeypatch):
    """The other half, through the same walk: an exact copy does go."""
    from autorun.installer.traversal import Context, Mode, run

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    root = home / ".agents"
    stale = _skill(root / "skills" / "mermaid-diagrams", "# old release\n")
    _write(stale / ".DS_Store", "finder\n")
    monkeypatch.setattr(
        provenance,
        "LEGACY_DIGESTS",
        {"autorun-skills": {"mermaid-diagrams": (provenance.tree_digest(stale),)}},
    )
    intents = list(retirements([root], [], plugins=["autorun-skills"]))
    assert [intent.target for intent in intents] == [stale]
    run((), Context(marketplace_root=tmp_path / "marketplace", home=home), Mode.INSTALL, extra=intents)
    assert not stale.exists()


#: Directories the user names like regenerated junk. ``scan_tree`` applies its
#: ignore patterns to directory names too, so without a rule of its own the
#: digest skipped everything inside them.
USER_DIRECTORIES_NAMED_LIKE_JUNK = ("._notes", "cache.pyc", ".DS_Store", "Thumbs.db")


@pytest.mark.parametrize("name", USER_DIRECTORIES_NAMED_LIKE_JUNK)
def test_a_directory_named_like_junk_holding_a_user_file_is_not_proven(tmp_path, name):
    source = _skill(tmp_path / "src" / "commit")
    copy = _skill(tmp_path / "shared" / "commit")
    _write(copy / name / "mine.txt")
    assert not provenance.autorun_wrote(copy, plugin="ar", source=source)


@pytest.mark.parametrize("name", USER_DIRECTORIES_NAMED_LIKE_JUNK)
def test_a_symlinked_directory_named_like_junk_is_not_proven(tmp_path, name):
    source = _skill(tmp_path / "src" / "commit")
    copy = _skill(tmp_path / "shared" / "commit")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    try:
        (copy / name).symlink_to(elsewhere, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need privileges here")
    assert not provenance.autorun_wrote(copy, plugin="ar", source=source)


def _stale_mermaid(home: Path, monkeypatch) -> Path:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    stale = _skill(home / ".agents" / "skills" / "mermaid-diagrams", "# old release\n")
    monkeypatch.setattr(
        provenance,
        "LEGACY_DIGESTS",
        {"autorun-skills": {"mermaid-diagrams": (provenance.tree_digest(stale),)}},
    )
    return stale


@pytest.mark.parametrize("extra", ("SKILL.md.bak", "extra.md", ".git/HEAD"))
def test_uninstall_keeps_an_unmarked_tree_holding_a_user_file(tmp_path, monkeypatch, extra):
    from autorun.installer.traversal import Context, Mode, run

    home = tmp_path / "home"
    stale = _stale_mermaid(home, monkeypatch)
    kept = _write(stale / extra)
    intents = list(retirements([home / ".agents"], [], plugins=["autorun-skills"]))
    run((), Context(marketplace_root=tmp_path / "m", home=home), Mode.UNINSTALL, extra=intents)
    assert kept.read_text(encoding="utf-8") == "mine\n"
    assert (stale / "SKILL.md").is_file()


def test_uninstall_removes_an_unmarked_tree_autorun_wrote(tmp_path, monkeypatch):
    from autorun.installer.traversal import Context, Mode, run

    home = tmp_path / "home"
    stale = _stale_mermaid(home, monkeypatch)
    intents = list(retirements([home / ".agents"], [], plugins=["autorun-skills"]))
    run((), Context(marketplace_root=tmp_path / "m", home=home), Mode.UNINSTALL, extra=intents)
    assert not stale.exists()


def test_a_receipt_for_another_plugin_does_not_prove_ownership(tmp_path):
    source = _skill(tmp_path / "src" / "commit", "# v1\n")
    target = tmp_path / "skills" / "commit"
    _perform(Intent(target=target, source=source, plugin="pdf-extractor"),
             Decision(Verdict.PUBLISH, target, "new"))
    _lose_marker(target)
    _put(source / "SKILL.md", "# moved on\n")  # only the receipt can match
    assert provenance.autorun_wrote(target, plugin="pdf-extractor", source=source)
    assert not provenance.autorun_wrote(target, plugin="ar", source=source)


def test_a_copy_with_crlf_line_endings_is_not_proven(tmp_path):
    """Git on Windows rewrites ``\\n`` to ``\\r\\n``: a different file, so the user's."""
    source = _skill(tmp_path / "src" / "commit", "# a\n# b\n")
    copy = tmp_path / "shared" / "commit"
    copy.mkdir(parents=True)
    (copy / "SKILL.md").write_bytes(b"# a\r\n# b\r\n")
    assert not provenance.autorun_wrote(copy, plugin="ar", source=source)


def test_an_extra_ordinary_file_is_not_proven(tmp_path):
    source = _skill(tmp_path / "src" / "commit")
    copy = _skill(tmp_path / "shared" / "commit")
    _write(copy / "extra.md")
    assert not provenance.autorun_wrote(copy, plugin="ar", source=source)


def test_a_receipt_from_before_the_stricter_digest_still_matches_a_clean_tree(tmp_path):
    """A receipt written by an earlier build digested with ``fs.IGNORED_GLOBS``.
    For a tree holding none of those names the two schemes agree, so existing
    receipts keep proving what they proved; a tree that differs is kept."""
    copy = _skill(tmp_path / "skills" / "commit")
    old_scheme = provenance._digest(fs.scan_tree(copy))
    assert old_scheme == provenance.tree_digest(copy)


def test_the_digest_equals_the_one_the_catalog_script_computes_for_a_blob_set(tmp_path):
    """Pins the scheme to the frozen catalog without needing git history: the
    script digests ``(path, fingerprint)`` pairs through ``provenance._digest``
    with ``fs`` fingerprints, and a tree materialised from the same files must
    digest the same through ``tree_digest``. A drift in either side, such as a
    new ignore rule or a different fingerprint, fails here instead of silently
    stranding every catalogued copy."""
    import importlib.util

    script = Path(__file__).resolve().parents[3] / "scripts" / "legacy_skill_digests.py"
    if not script.is_file():
        pytest.skip("repository scripts are not shipped in this checkout")
    spec = importlib.util.spec_from_file_location("legacy_skill_digests_under_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # imports only; main() is behind __main__

    files = {"SKILL.md": (b"# skill\n", False), "scripts/run.sh": (b"#!/bin/sh\n", True)}
    root = tmp_path / "tree"
    shas = {}
    for relative, (data, executable) in files.items():
        _write(root / relative, data.decode())
        if executable:
            (root / relative).chmod(0o755)
        shas[relative] = hashlib.sha1(data).hexdigest()

    class Blobs(module.Blobs):  # same fingerprint rule, bytes supplied instead of `git cat-file`
        def __init__(self):
            self.cache = {sha: files[rel][0] for rel, sha in shas.items()}

    blobs = Blobs()
    raw = {
        rel: blobs.fingerprint("100755" if files[rel][1] else "100644", sha)
        for rel, sha in shas.items()
    }
    if os.name == "nt":
        pytest.skip("the executable bit is not recorded on Windows")
    assert module._digest(raw) == provenance.tree_digest(root)


# Commits holding a version of a skill that an earlier release installed.
# ``tmux-automation`` has a symlinked SKILL.md at f115ca56, which that
# release's install dereferenced into a regular file; the plugin is the one
# that ships the name today, which is how the catalog keys it.
LEGACY_VERSIONS = (
    ("f115ca56", "autorun", "autorun-skills", "tmux-automation"),
    ("ee734a6d", "autorun-skills", "autorun-skills", "mermaid-diagrams"),
    ("929c9e9d", "autorun", "ar", "commit"),
)


def _materialise(commit: str, plugin_dir: str, name: str, into: Path, dereference: bool) -> Path:
    import posixpath
    import subprocess

    repo = Path(__file__).resolve().parents[3]
    prefix = f"plugins/{plugin_dir}/skills/{name}/"
    listing = subprocess.run(
        ["git", "ls-tree", "-r", commit, "--", prefix.rstrip("/")],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    files = {}
    for line in listing:
        meta, path = line.split("\t", 1)
        mode, _kind, sha = meta.split()
        files[path[len(prefix):]] = (mode, sha)

    def blob(sha: str) -> bytes:
        return subprocess.run(
            ["git", "cat-file", "blob", sha], cwd=repo, capture_output=True, check=True
        ).stdout

    root = into / name
    for relative, (mode, sha) in files.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if mode == "120000":
            target = blob(sha).decode()
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(relative), target))
            if dereference and resolved in files:
                destination.write_bytes(blob(files[resolved][1]))
            else:
                destination.symlink_to(target)
        else:
            destination.write_bytes(blob(sha))
            if mode == "100755":
                destination.chmod(0o755)
    return root


@pytest.mark.parametrize("commit,plugin_dir,plugin,name", LEGACY_VERSIONS)
def test_a_skill_tree_rebuilt_from_git_history_is_still_recognised(
    tmp_path, monkeypatch, commit, plugin_dir, plugin, name
):
    """The frozen catalog was generated from history with the old digest rule.
    Rebuilding the tree an old release installed and asking the new one proves
    the stricter rule did not strand it. Skipped where history is shallow."""
    import importlib
    import subprocess

    repo = Path(__file__).resolve().parents[3]
    if subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=repo,
                      capture_output=True).returncode:
        pytest.skip(f"{commit} is not in this clone (shallow history)")
    monkeypatch.setattr(
        provenance, "LEGACY_DIGESTS", importlib.import_module(
            "autorun.installer._legacy_skill_digests").DIGESTS,
    )
    for dereference in (False, True):
        root = _materialise(commit, plugin_dir, name, tmp_path / str(dereference), dereference)
        assert provenance.autorun_wrote(root, plugin=plugin, name=name), (commit, name, dereference)


def test_decide_keeps_an_unmarked_copy_holding_a_backup_instead_of_publishing_over_it(tmp_path):
    """``fs.decide`` is the second gate behind ``skills.blocked_names``; it must
    refuse on its own, because other callers hand it the same proof."""
    source = _skill(tmp_path / "src" / "commit")
    copy = _skill(tmp_path / "shared" / "commit")
    _write(copy / "SKILL.md.bak")
    proof = provenance.proof_for(plugin="ar", source=source)
    assert fs.decide(copy, source, plugin="ar", ownership_proof=proof).verdict is Verdict.KEEP
    assert fs.decide(copy, None, plugin="ar", ownership_proof=proof).verdict is Verdict.KEEP
