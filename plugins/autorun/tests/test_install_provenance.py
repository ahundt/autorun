"""An unmarked tree autorun provably wrote is autorun's, not the user's.

Ownership lived only in the ``.autorun-owned`` marker inside each tree. A user
who keeps ``~/.agents`` in git ignored that file and lost it, after which every
skill an earlier release had copied there read as user-authored: rc4 left them
in place, and because Codex reads that root it also withheld its own packaged
copies, so Codex kept running months-old skills. An edited copy must still be
the user's; only a byte-exact match to something autorun wrote is adopted.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.installer import fs, provenance  # noqa: E402
from autorun.installer.fs import OWNED_MARKER_NAME, Decision, Verdict  # noqa: E402
from autorun.installer.traversal import Intent, _perform, retirements  # noqa: E402


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTORUN_HOME", str(tmp_path / "ar-home"))
    monkeypatch.setattr(provenance, "LEGACY_DIGESTS", {})


def _skill(root: Path, body: str = "# skill\n") -> Path:
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(body, encoding="utf-8")
    return root


def _lose_marker(tree: Path) -> None:
    (tree / OWNED_MARKER_NAME).unlink()


def test_a_copy_identical_to_the_shipped_source_is_autoruns(tmp_path):
    source = _skill(tmp_path / "src" / "commit")
    copy = _skill(tmp_path / "shared" / "commit")
    assert provenance.autorun_wrote(copy, plugin="ar", source=source)
    (copy / "SKILL.md").write_text("# mine now\n", encoding="utf-8")
    assert not provenance.autorun_wrote(copy, plugin="ar", source=source)


def test_a_dereferenced_copy_of_a_linked_source_is_autoruns(tmp_path):
    """An install that dereferenced links wrote a regular file where the
    source had a symlink; both are what autorun publishes."""
    source = tmp_path / "src" / "tmux"
    source.mkdir(parents=True)
    (source / "GUIDE.md").write_text("guide\n", encoding="utf-8")
    try:
        (source / "SKILL.md").symlink_to("GUIDE.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need privileges here")
    copy = tmp_path / "shared" / "tmux"
    copy.mkdir(parents=True)
    (copy / "GUIDE.md").write_text("guide\n", encoding="utf-8")
    (copy / "SKILL.md").write_text("guide\n", encoding="utf-8")
    assert provenance.autorun_wrote(copy, plugin="ar", source=source)


def test_publishing_records_a_receipt_that_outlives_the_marker(tmp_path):
    source = _skill(tmp_path / "src" / "commit", "# v1\n")
    target = tmp_path / "skills" / "commit"
    intent = Intent(target=target, source=source, plugin="ar")
    assert _perform(intent, Decision(Verdict.PUBLISH, target, "new")).verdict is Verdict.PUBLISH
    _lose_marker(target)

    # The source moved on; the copy is the one autorun wrote, so it is autorun's.
    (source / "SKILL.md").write_text("# v2\n", encoding="utf-8")
    assert provenance.autorun_wrote(target, plugin="ar", source=source)
    assert not provenance.autorun_wrote(target, plugin="pdf-extractor", source=source)

    (target / "SKILL.md").write_text("# edited after the marker was lost\n", encoding="utf-8")
    assert not provenance.autorun_wrote(target, plugin="ar", source=source)


def test_retiring_forgets_the_receipt(tmp_path):
    source = _skill(tmp_path / "src" / "commit")
    target = tmp_path / "skills" / "commit"
    _perform(Intent(target=target, source=source, plugin="ar"), Decision(Verdict.PUBLISH, target, "new"))
    _perform(Intent(target=target, source=None, plugin="ar"), Decision(Verdict.RETIRE, target, "gone"))
    assert not target.exists()
    _skill(target, "# v1\n")  # the user makes their own at the same path
    (source / "SKILL.md").write_text("# moved on\n", encoding="utf-8")
    assert not provenance.autorun_wrote(target, plugin="ar", source=source)


def test_a_released_version_in_the_legacy_catalog_is_autoruns(tmp_path, monkeypatch):
    stale = _skill(tmp_path / "shared" / "mermaid-diagrams", "# old release\n")
    digest = provenance.tree_digest(stale)
    monkeypatch.setattr(
        provenance, "LEGACY_DIGESTS", {"autorun-skills": {"mermaid-diagrams": (digest,)}}
    )
    assert provenance.autorun_wrote(stale, plugin="autorun-skills")
    assert not provenance.autorun_wrote(stale, plugin="ar"), "another plugin's version"
    (stale / "SKILL.md").write_text("# old release, edited\n", encoding="utf-8")
    assert not provenance.autorun_wrote(stale, plugin="autorun-skills")


def test_decide_adopts_a_proven_unmarked_tree_and_keeps_an_edited_one(tmp_path):
    source = _skill(tmp_path / "src" / "commit", "# v2\n")
    copy = _skill(tmp_path / "shared" / "commit", "# v2\n")
    proof = provenance.proof_for(plugin="ar", source=source)
    assert fs.decide(copy, source, plugin="ar", ownership_proof=proof).verdict is Verdict.PUBLISH
    (copy / "SKILL.md").write_text("# mine\n", encoding="utf-8")
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
