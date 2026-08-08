"""Codex's hooks file, which one stray key disables entirely.

Codex is the harness where being almost right is worse than failing: three of
its rules discard work silently rather than reporting it. These tests pin all
three, plus the property that matters most in a file autorun does not own —
the user's hooks, and other tools' hooks, come through untouched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.installer.codex import (  # noqa: E402
    ALLOWED_TOP_LEVEL,
    HOOK_TIMEOUT_SECONDS,
    is_ours,
    merge_hooks,
    shadowing_override,
    unknown_top_level,
    wrap,
)

THEIRS = {"hooks": [{"type": "command", "command": "/usr/local/bin/mine.sh"}]}
OURS = "uv run --quiet --no-sync --project /p python /p/hooks/hook_entry.py --cli codex"


@pytest.fixture
def hooks(tmp_path: Path) -> Path:
    target = tmp_path / "hooks.json"
    target.write_text(json.dumps({"hooks": {"PreToolUse": [THEIRS]}}), encoding="utf-8")
    return target


def events(path: Path) -> dict:
    return json.loads(path.read_text())["hooks"]


# ─── Rule 1: an unknown top-level key drops every hook in the file ───────────


def test_a_file_with_a_rejected_key_is_refused_not_extended(tmp_path):
    """HooksFile is #[serde(deny_unknown_fields)] in openai/codex. Adding to a
    file that is already disabling itself would report success while every
    hook, including the user's, stays dead."""
    poisoned = tmp_path / "poisoned.json"
    original = {"hooks": {}, "version": 2}
    poisoned.write_text(json.dumps(original), encoding="utf-8")

    with pytest.raises(ValueError, match="version"):
        merge_hooks(poisoned, {"Stop": [OURS]})

    assert json.loads(poisoned.read_text()) == original, "left exactly as found"


def test_we_never_introduce_a_rejected_key(hooks):
    merge_hooks(hooks, {"PreToolUse": [OURS]})

    assert unknown_top_level(json.loads(hooks.read_text())) == ()


def test_the_allowed_set_is_the_two_keys_codex_accepts():
    assert ALLOWED_TOP_LEVEL == {"description", "hooks"}


# ─── Rule 2: a bare command entry is silently dropped ───────────────────────


def test_our_entry_is_wrapped_never_bare(hooks):
    """A bare {"type": "command"} is discarded by Codex, which reads exactly
    like a hook that ran and decided to allow."""
    merge_hooks(hooks, {"PreToolUse": [OURS]})

    ours = next(e for e in events(hooks)["PreToolUse"] if is_ours(e))
    assert "hooks" in ours
    assert ours["hooks"][0]["type"] == "command"


def test_wrap_produces_one_inner_entry_per_command():
    wrapped = wrap(["a hook_entry.py", "b hook_entry.py"])

    assert [h["command"] for h in wrapped["hooks"]] == ["a hook_entry.py", "b hook_entry.py"]


def test_every_entry_we_write_carries_a_timeout(hooks):
    """Autorun's hook answers a local daemon in milliseconds, so a longer wait
    is a hang. With no timeout the harness default applies and a stuck daemon
    stalls the session instead of failing open."""
    merge_hooks(hooks, {"PreToolUse": [OURS]})

    ours = next(e for e in events(hooks)["PreToolUse"] if is_ours(e))
    assert [h["timeout"] for h in ours["hooks"]] == [HOOK_TIMEOUT_SECONDS]


# ─── Rule 3: AGENTS.override.md shadows AGENTS.md ───────────────────────────


def test_a_non_blank_override_is_reported(tmp_path):
    """Codex reads the override first and uses the first non-blank file, so
    guidance written to AGENTS.md can be invisible while install reports it
    was written."""
    (tmp_path / "AGENTS.override.md").write_text("my rules\n", encoding="utf-8")

    assert shadowing_override(tmp_path) == tmp_path / "AGENTS.override.md"


@pytest.mark.parametrize("content", ["", "   \n", "\n\n"])
def test_a_blank_override_shadows_nothing(tmp_path, content):
    """Otherwise every user with an empty placeholder gets a false warning."""
    (tmp_path / "AGENTS.override.md").write_text(content, encoding="utf-8")

    assert shadowing_override(tmp_path) is None


def test_no_override_at_all_is_not_a_finding(tmp_path):
    assert shadowing_override(tmp_path) is None


# ─── The file belongs to the user ───────────────────────────────────────────


def test_the_users_hook_survives_the_merge(hooks):
    merge_hooks(hooks, {"PreToolUse": [OURS]})

    entries = events(hooks)["PreToolUse"]
    assert THEIRS in entries
    assert len(entries) == 2


def test_another_tools_hook_is_not_claimed():
    """Observed live: codebase-memory-mcp registers a SubagentStart hook in the
    same file. Claiming it would delete another tool's integration."""
    foreign = {
        "matcher": "*",
        "hooks": [{"type": "command", "command": "'/x/codebase-memory-mcp' hook-augment"}],
    }

    assert not is_ours(foreign)


def test_remerging_converges_instead_of_accumulating(hooks):
    """Removing and re-adding, rather than editing in place, is what makes a
    changed command replace the old one instead of firing twice."""
    merge_hooks(hooks, {"PreToolUse": [OURS]})
    merge_hooks(hooks, {"PreToolUse": [OURS + " --v2"]})

    entries = events(hooks)["PreToolUse"]
    assert len([e for e in entries if is_ours(e)]) == 1
    assert THEIRS in entries
    assert "--v2" in json.dumps(entries)


def test_an_empty_command_list_removes_ours_and_keeps_theirs(hooks):
    merge_hooks(hooks, {"PreToolUse": [OURS]})

    merge_hooks(hooks, {"PreToolUse": []})

    assert events(hooks)["PreToolUse"] == [THEIRS]


def test_an_event_that_becomes_empty_is_dropped_entirely(tmp_path):
    target = tmp_path / "hooks.json"
    target.write_text(json.dumps({"hooks": {}}), encoding="utf-8")
    merge_hooks(target, {"Stop": [OURS]})

    merge_hooks(target, {"Stop": []})

    assert "Stop" not in events(target), "an empty list is not a hook registration"


# ─── Every spelling autorun has ever written ────────────────────────────────


@pytest.mark.parametrize(
    "entry",
    [
        {"_autorun_owned": True},
        {"type": "command", "command": "python hook_entry.py --cli codex"},
        {"hooks": [{"type": "command", "command": "uv run hook_entry.py"}]},
    ],
    ids=["legacy-flag", "legacy-bare", "current-wrapper"],
)
def test_every_legacy_spelling_is_recognised(entry):
    """An unrecognised old entry is not replaced, so it stays beside the new
    one and both fire."""
    assert is_ours(entry)


@pytest.mark.parametrize(
    "entry",
    [{"hooks": [{"type": "command", "command": "/bin/theirs"}]}, "not a dict", None, 42],
    ids=["user-command", "string", "none", "number"],
)
def test_nothing_else_is_claimed(entry):
    assert not is_ours(entry)


# ─── Removing ours without removing theirs ──────────────────────────────────


def test_an_event_we_no_longer_ship_is_swept(tmp_path):
    """A step knows only today's events. SessionEnd was registered, Codex never
    accepted the name, and the entry outlived the versions that wrote it."""
    target = tmp_path / "hooks.json"
    target.write_text(json.dumps({"hooks": {}}), encoding="utf-8")
    merge_hooks(target, {"SessionEnd": [OURS], "Stop": [OURS]})

    merge_hooks(target, {"Stop": [OURS]})

    assert "SessionEnd" not in events(target)
    assert "Stop" in events(target), "the events we still ship are untouched"


def test_sweeping_a_retired_event_keeps_the_users_hook_there(tmp_path):
    target = tmp_path / "hooks.json"
    target.write_text(json.dumps({"hooks": {"SessionEnd": [THEIRS]}}), encoding="utf-8")
    merge_hooks(target, {"SessionEnd": [OURS]})

    merge_hooks(target, {"Stop": [OURS]})

    assert events(target)["SessionEnd"] == [THEIRS]


def test_a_command_the_user_added_inside_our_wrapper_survives(tmp_path):
    """The wrapper is a shape autorun authored, so a hand-added command inside
    it is the likely case. Dropping the whole entry deletes a hook we never
    wrote."""
    target = tmp_path / "hooks.json"
    mixed = {"hooks": [
        {"type": "command", "command": OURS},
        {"type": "command", "command": "/usr/local/bin/also-mine.sh"},
    ]}
    target.write_text(json.dumps({"hooks": {"Stop": [mixed]}}), encoding="utf-8")

    merge_hooks(target, {"Stop": []})

    assert events(target)["Stop"] == [
        {"hooks": [{"type": "command", "command": "/usr/local/bin/also-mine.sh"}]}
    ]


# ─── Agreement with the file the current installer wrote ────────────────────


# ─── The marketplace that lists the plugin ──────────────────────────────────


def test_the_generated_entry_matches_the_one_on_disk():
    """The live file was written by the installer being replaced, so an
    identical entry is the strongest available equivalence check."""
    from autorun.installer.codex import marketplace_entry

    live = Path.home() / ".agents" / "plugins" / "marketplace.json"
    if not live.is_file():
        pytest.skip("codex marketplace not installed on this machine")
    document = json.loads(live.read_text(encoding="utf-8"))
    existing = next((p for p in document.get("plugins", []) if p.get("name") == "autorun"), None)
    if existing is None:
        pytest.skip("autorun is not listed in the local marketplace")

    assert marketplace_entry("autorun", existing["source"]["path"]) == existing


def test_a_same_name_with_a_different_source_is_not_ours_to_replace(tmp_path):
    """A name collision cannot establish ownership of another plugin."""
    from autorun.installer.codex import marketplace_entry, publish_marketplace

    market = tmp_path / "marketplace.json"
    theirs = marketplace_entry("autorun", "./theirs")
    publish_marketplace(market, "personal", theirs)

    with pytest.raises(ValueError, match="different source"):
        publish_marketplace(
            market, "personal", marketplace_entry("autorun", "./plugins/autorun")
        )

    plugins = json.loads(market.read_text())["plugins"]
    assert plugins == [theirs]


def test_republishing_an_unchanged_entry_writes_nothing(tmp_path):
    from autorun.installer.codex import marketplace_entry, publish_marketplace

    market = tmp_path / "marketplace.json"
    entry = marketplace_entry("autorun", "./plugins/autorun")
    publish_marketplace(market, "personal", entry)
    stamp = market.stat().st_mtime_ns

    assert publish_marketplace(market, "personal", entry) is False
    assert market.stat().st_mtime_ns == stamp


def test_withdrawing_leaves_every_other_plugin(tmp_path):
    from autorun.installer.codex import (
        marketplace_entry,
        publish_marketplace,
        withdraw_from_marketplace,
    )

    market = tmp_path / "marketplace.json"
    publish_marketplace(market, "personal", marketplace_entry("autorun", "./a"))
    publish_marketplace(market, "personal", marketplace_entry("their-tool", "./b"))

    ours = marketplace_entry("autorun", "./a")
    assert withdraw_from_marketplace(market, ours) is True

    assert [p["name"] for p in json.loads(market.read_text())["plugins"]] == ["their-tool"]
    assert withdraw_from_marketplace(market, ours) is False


def test_withdrawal_preserves_a_same_name_entry_from_another_source(tmp_path):
    from autorun.installer.codex import marketplace_entry, withdraw_from_marketplace

    market = tmp_path / "marketplace.json"
    ours = marketplace_entry("autorun", "./plugins/autorun")
    theirs = marketplace_entry("autorun", "./theirs")
    market.write_text(json.dumps({"name": "personal", "plugins": [ours, theirs]}))

    assert withdraw_from_marketplace(market, ours) is True
    assert json.loads(market.read_text())["plugins"] == [theirs]


# ─── Staging must flatten links the Codex cache cannot follow ───────────────


def test_a_symlinked_skill_is_flattened_when_staged(tmp_path):
    """The Codex plugin cache ignores symlinks, so a staged SKILL.md that is a
    link is simply absent from the packaged plugin — the skill ships with no
    content and nothing reports it. Autorun's own bridge creates such links."""
    from autorun.installer.codex import dereference_links

    real = tmp_path / "real"
    real.mkdir()
    (real / "SKILL.md").write_text("# real content\n", encoding="utf-8")
    staged = tmp_path / "staged" / "skills" / "commit"
    staged.mkdir(parents=True)
    (staged / "SKILL.md").symlink_to(real / "SKILL.md")

    assert dereference_links(tmp_path / "staged") == ()

    assert not (staged / "SKILL.md").is_symlink()
    assert (staged / "SKILL.md").read_text() == "# real content\n"


def test_a_symlinked_directory_is_flattened_too(tmp_path):
    from autorun.installer.codex import dereference_links

    real = tmp_path / "real"
    real.mkdir()
    (real / "SKILL.md").write_text("# content\n", encoding="utf-8")
    staged = tmp_path / "staged" / "skills"
    staged.mkdir(parents=True)
    (staged / "linked").symlink_to(real)

    dereference_links(tmp_path / "staged")

    assert not (staged / "linked").is_symlink()
    assert (staged / "linked" / "SKILL.md").is_file()


def test_a_dangling_link_is_reported_not_silently_removed(tmp_path):
    """Replacing it with nothing would hide a broken bridge instead of
    surfacing it."""
    from autorun.installer.codex import dereference_links

    staged = tmp_path / "staged" / "skills"
    staged.mkdir(parents=True)
    (staged / "dangling").symlink_to(tmp_path / "nowhere")

    broken = dereference_links(tmp_path / "staged")

    assert broken == ("skills/dangling",)
    assert (staged / "dangling").is_symlink()


def test_it_reads_the_real_installed_hooks_file_correctly():
    """The strongest available check: the file on this machine was written by
    the installer being replaced."""
    live = Path.home() / ".codex" / "hooks.json"
    if not live.is_file():
        pytest.skip("codex hooks not installed on this machine")

    document = json.loads(live.read_text(encoding="utf-8"))

    assert unknown_top_level(document) == (), "the live file would load in Codex"
    for event, entries in document.get("hooks", {}).items():
        assert len([e for e in entries if is_ours(e)]) <= 1, f"{event}: at most one of ours"
