"""Sentinel-delimited block merge/strip for agent memory files.

autorun writes advisory guidance into memory files owned by the *user*:
``~/.codex/AGENTS.md`` (install.py:_install_codex_agents_md) and
``<forge_base>/AGENTS.md`` (install.py:_install_for_forgecode). Those two paths
disagreed: Codex spliced a sentinel-delimited region and preserved everything
around it, while ForgeCode called ``shutil.copy2`` and destroyed user content on
every install.

These tests pin one shared contract for both, plus the strip half that
``uninstall_plugins`` needs. The docstring at install.py:2938 promised
"a future uninstall can strip our block cleanly" and no strip function existed.

Conventions follow test_codex_install.py: ``tmp_path`` for every root, and
``monkeypatch.setenv("HOME", ...)`` where ``Path.home()`` is involved.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.durable_io import atomic_write_text  # noqa: E402
from autorun.install import (  # noqa: E402
    install_sentinel_block,
    strip_sentinel_block,
)

START = "<!-- autorun:test-block:start -->"
END = "<!-- autorun:test-block:end -->"

USER_PREFIX = "# My own notes\n\nAlways run `make lint` before committing.\n"
USER_SUFFIX = "## Personal\n\nPrefer pnpm over npm.\n"


def _block_of(text: str) -> str:
    """Extract the text between the sentinels, exclusive."""
    start = text.index(START) + len(START)
    end = text.index(END)
    return text[start:end].strip()


# --------------------------------------------------------------------------
# atomic_write_text — the durability primitive the merge is built on
# --------------------------------------------------------------------------


def test_atomic_write_text_creates_file_and_parents(tmp_path):
    """A missing parent directory is created, matching atomic_write_json."""
    target = tmp_path / "nested" / "deeper" / "AGENTS.md"
    atomic_write_text(target, "hello\n")
    assert target.read_text(encoding="utf-8") == "hello\n"


def test_atomic_write_text_replaces_existing_content(tmp_path):
    target = tmp_path / "AGENTS.md"
    target.write_text("old\n", encoding="utf-8")
    atomic_write_text(target, "new\n")
    assert target.read_text(encoding="utf-8") == "new\n"


def test_atomic_write_text_leaves_no_staged_files_behind(tmp_path):
    """The mkstemp staging file must not survive a successful write."""
    target = tmp_path / "AGENTS.md"
    atomic_write_text(target, "content\n")
    assert [p.name for p in tmp_path.iterdir()] == ["AGENTS.md"]


def test_atomic_write_text_preserves_utf8(tmp_path):
    target = tmp_path / "AGENTS.md"
    atomic_write_text(target, "✓ é 中文\n")
    assert target.read_text(encoding="utf-8") == "✓ é 中文\n"


# --------------------------------------------------------------------------
# install_sentinel_block — create / append / replace
# --------------------------------------------------------------------------


def test_install_creates_file_when_absent(tmp_path):
    target = tmp_path / "AGENTS.md"
    install_sentinel_block(target, "guidance body", start=START, end=END)
    text = target.read_text(encoding="utf-8")
    assert text.startswith(START)
    assert text.rstrip().endswith(END)
    assert _block_of(text) == "guidance body"


def test_install_appends_when_file_has_user_content(tmp_path):
    """User content must survive, and our block goes after it."""
    target = tmp_path / "AGENTS.md"
    target.write_text(USER_PREFIX, encoding="utf-8")
    install_sentinel_block(target, "guidance body", start=START, end=END)
    text = target.read_text(encoding="utf-8")
    assert USER_PREFIX.strip() in text
    assert text.index(USER_PREFIX.strip()) < text.index(START)
    assert _block_of(text) == "guidance body"


def test_install_replaces_in_place_preserving_prefix_and_suffix(tmp_path):
    """Re-install swaps only the block; text on both sides is untouched."""
    target = tmp_path / "AGENTS.md"
    target.write_text(
        f"{USER_PREFIX}\n{START}\nold body\n{END}\n\n{USER_SUFFIX}",
        encoding="utf-8",
    )
    install_sentinel_block(target, "new body", start=START, end=END)
    text = target.read_text(encoding="utf-8")
    assert _block_of(text) == "new body"
    assert "old body" not in text
    assert USER_PREFIX.strip() in text
    assert USER_SUFFIX.strip() in text
    assert text.index(USER_PREFIX.strip()) < text.index(START)
    assert text.index(END) < text.index(USER_SUFFIX.strip())


def test_install_is_idempotent(tmp_path):
    """Two installs of the same body produce byte-identical output."""
    target = tmp_path / "AGENTS.md"
    target.write_text(USER_PREFIX, encoding="utf-8")
    install_sentinel_block(target, "guidance body", start=START, end=END)
    first = target.read_text(encoding="utf-8")
    install_sentinel_block(target, "guidance body", start=START, end=END)
    assert target.read_text(encoding="utf-8") == first


def test_install_emits_exactly_one_sentinel_pair(tmp_path):
    """Repeated installs must never accumulate sentinels."""
    target = tmp_path / "AGENTS.md"
    for _ in range(3):
        install_sentinel_block(target, "body", start=START, end=END)
    text = target.read_text(encoding="utf-8")
    assert text.count(START) == 1
    assert text.count(END) == 1


def test_install_strips_sentinels_already_present_in_the_body(tmp_path):
    """A template that ships its own sentinels must not double-wrap.

    codex_template/AGENTS.md:1 and :46 literally contain the sentinel pair, so
    this strip is load-bearing rather than defensive.
    """
    target = tmp_path / "AGENTS.md"
    body = f"{START}\nreal guidance\n{END}"
    install_sentinel_block(target, body, start=START, end=END)
    text = target.read_text(encoding="utf-8")
    assert text.count(START) == 1
    assert text.count(END) == 1
    assert "real guidance" in text


def test_install_preserves_user_content_byte_for_byte(tmp_path):
    """The assertion test_forgecode_install.py never made.

    A trivial idempotence check passes even if the function deletes the user's
    file first. This one does not.
    """
    target = tmp_path / "AGENTS.md"
    target.write_text(USER_PREFIX, encoding="utf-8")
    install_sentinel_block(target, "body", start=START, end=END)
    after = target.read_text(encoding="utf-8")
    assert USER_PREFIX.rstrip("\n") in after


def test_install_returns_false_when_body_is_empty(tmp_path):
    """No body means nothing to install; the file must be left alone."""
    target = tmp_path / "AGENTS.md"
    target.write_text(USER_PREFIX, encoding="utf-8")
    assert install_sentinel_block(target, "", start=START, end=END) is False
    assert target.read_text(encoding="utf-8") == USER_PREFIX


def test_install_creates_parent_directory(tmp_path):
    target = tmp_path / "nested" / "AGENTS.md"
    install_sentinel_block(target, "body", start=START, end=END)
    assert target.is_file()


# --------------------------------------------------------------------------
# Adoption — migrating files a previous autorun wrote unwrapped
# --------------------------------------------------------------------------


def test_install_adopts_unwrapped_content_it_previously_wrote(tmp_path):
    """ForgeCode users' AGENTS.md *is* the template, verbatim.

    install.py:3024-3026 used shutil.copy2, so every existing
    <forge_base>/AGENTS.md holds exactly the template body with no sentinels.
    Appending would duplicate it. Identical content means autorun wrote it, so
    wrap in place instead.
    """
    target = tmp_path / "AGENTS.md"
    target.write_text("guidance body\n", encoding="utf-8")
    install_sentinel_block(target, "guidance body", start=START, end=END)
    text = target.read_text(encoding="utf-8")
    assert text.count("guidance body") == 1
    assert text.count(START) == 1


def test_adoption_ignores_whitespace_differences(tmp_path):
    """Trailing-newline drift must not defeat adoption."""
    target = tmp_path / "AGENTS.md"
    target.write_text("guidance body", encoding="utf-8")
    install_sentinel_block(target, "guidance body\n\n", start=START, end=END)
    assert target.read_text(encoding="utf-8").count("guidance body") == 1


def test_adoption_does_not_swallow_user_content(tmp_path):
    """Adoption is exact-match only — a user edit means it is theirs now."""
    target = tmp_path / "AGENTS.md"
    target.write_text("guidance body\n\nMy own addition.\n", encoding="utf-8")
    install_sentinel_block(target, "guidance body", start=START, end=END)
    text = target.read_text(encoding="utf-8")
    assert "My own addition." in text
    assert text.count(START) == 1


# --------------------------------------------------------------------------
# strip_sentinel_block — the uninstall half
# --------------------------------------------------------------------------


def test_strip_removes_block_and_preserves_surrounding_content(tmp_path):
    target = tmp_path / "AGENTS.md"
    target.write_text(USER_PREFIX, encoding="utf-8")
    install_sentinel_block(target, "body", start=START, end=END)
    assert strip_sentinel_block(target, start=START, end=END) is True
    text = target.read_text(encoding="utf-8")
    assert START not in text
    assert END not in text
    assert "body" not in text
    assert USER_PREFIX.rstrip("\n") in text


def test_strip_preserves_both_prefix_and_suffix(tmp_path):
    target = tmp_path / "AGENTS.md"
    target.write_text(
        f"{USER_PREFIX}\n{START}\nbody\n{END}\n\n{USER_SUFFIX}", encoding="utf-8"
    )
    strip_sentinel_block(target, start=START, end=END)
    text = target.read_text(encoding="utf-8")
    assert USER_PREFIX.rstrip("\n") in text
    assert USER_SUFFIX.rstrip("\n") in text
    assert "body" not in text


def test_strip_returns_false_when_no_block_present(tmp_path):
    """A file autorun never touched must be reported as unchanged."""
    target = tmp_path / "AGENTS.md"
    target.write_text(USER_PREFIX, encoding="utf-8")
    assert strip_sentinel_block(target, start=START, end=END) is False
    assert target.read_text(encoding="utf-8") == USER_PREFIX


def test_strip_returns_false_when_file_absent(tmp_path):
    """Uninstalling on a machine that never installed must not raise."""
    assert (
        strip_sentinel_block(tmp_path / "nope.md", start=START, end=END) is False
    )


def test_strip_removes_file_when_it_held_only_our_block(tmp_path):
    """Leaving an empty file behind is litter; leaving user content is not."""
    target = tmp_path / "AGENTS.md"
    install_sentinel_block(target, "body", start=START, end=END)
    strip_sentinel_block(target, start=START, end=END)
    assert not target.exists() or target.read_text(encoding="utf-8").strip() == ""


def test_strip_is_idempotent(tmp_path):
    target = tmp_path / "AGENTS.md"
    target.write_text(USER_PREFIX, encoding="utf-8")
    install_sentinel_block(target, "body", start=START, end=END)
    strip_sentinel_block(target, start=START, end=END)
    first = target.read_text(encoding="utf-8")
    assert strip_sentinel_block(target, start=START, end=END) is False
    assert target.read_text(encoding="utf-8") == first


def test_install_then_strip_round_trips_to_original(tmp_path):
    """The strongest guarantee: install+strip is a no-op on user content."""
    target = tmp_path / "AGENTS.md"
    original = f"{USER_PREFIX}\n{USER_SUFFIX}"
    target.write_text(original, encoding="utf-8")
    install_sentinel_block(target, "body", start=START, end=END)
    strip_sentinel_block(target, start=START, end=END)
    after = target.read_text(encoding="utf-8")
    assert after.split() == original.split()


def test_strip_leaves_a_different_tools_sentinels_alone(tmp_path):
    """Only our own markers may be removed."""
    other_start = "<!-- othertool:start -->"
    other_end = "<!-- othertool:end -->"
    target = tmp_path / "AGENTS.md"
    target.write_text(
        f"{other_start}\ntheirs\n{other_end}\n", encoding="utf-8"
    )
    install_sentinel_block(target, "ours", start=START, end=END)
    strip_sentinel_block(target, start=START, end=END)
    text = target.read_text(encoding="utf-8")
    assert other_start in text
    assert "theirs" in text
    assert "ours" not in text


# --------------------------------------------------------------------------
# Malformed input — half a sentinel pair must not corrupt the file
# --------------------------------------------------------------------------


@pytest.mark.parametrize("marker", [START, END])
def test_install_with_only_one_marker_present_does_not_lose_user_content(
    tmp_path, marker
):
    """A truncated previous write leaves one marker; user text must survive."""
    target = tmp_path / "AGENTS.md"
    target.write_text(f"{USER_PREFIX}\n{marker}\n", encoding="utf-8")
    install_sentinel_block(target, "body", start=START, end=END)
    text = target.read_text(encoding="utf-8")
    assert USER_PREFIX.rstrip("\n") in text
    assert "body" in text


@pytest.mark.parametrize("marker", [START, END])
def test_strip_with_only_one_marker_present_does_not_lose_user_content(
    tmp_path, marker
):
    target = tmp_path / "AGENTS.md"
    target.write_text(f"{USER_PREFIX}\n{marker}\n", encoding="utf-8")
    strip_sentinel_block(target, start=START, end=END)
    assert USER_PREFIX.rstrip("\n") in target.read_text(encoding="utf-8")


def test_install_with_end_before_start_does_not_lose_user_content(tmp_path):
    """Reversed markers must be treated as absent, not as a valid region."""
    target = tmp_path / "AGENTS.md"
    target.write_text(f"{USER_PREFIX}\n{END}\nx\n{START}\n", encoding="utf-8")
    install_sentinel_block(target, "body", start=START, end=END)
    text = target.read_text(encoding="utf-8")
    assert USER_PREFIX.rstrip("\n") in text
    assert "body" in text
