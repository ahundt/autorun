import json
import subprocess
import sys
from pathlib import Path

from conftest import skip_if_windows_service_provider_error


def test_capability_snapshot_contains_all_registered_platforms():
    from autorun import __version__
    from autorun.capability_snapshot import build_capability_snapshot
    from autorun.platforms import PLATFORMS

    snapshot = build_capability_snapshot()

    assert set(snapshot["platforms"]) == set(PLATFORMS)
    assert snapshot["version"] == __version__
    assert snapshot["commands"]
    assert snapshot["skills"]
    assert snapshot["hook_events"]


def test_capability_snapshot_covers_every_platform_field():
    """A field the snapshot omits is a per-harness value nobody reviews.

    Divergence arrives as new Platform fields, and one left at its class
    default on a single harness is a silent wrong answer there — exactly how
    command_display_prefix was wrong on ForgeCode and OpenCode until F2.
    Serializing every field in _jsonable_platform makes adding one force a
    conscious per-platform decision. The extra hook-protocol expansion keys
    do not count against coverage; the hook_protocol field itself is
    serialized under its own name.
    """
    import dataclasses

    from autorun.capability_snapshot import build_capability_snapshot
    from autorun.platforms import Platform

    entry = build_capability_snapshot()["platforms"]["claude"]
    missing = {field.name for field in dataclasses.fields(Platform)} - set(entry)
    assert not missing, (
        f"Platform fields absent from the capability snapshot: {sorted(missing)}; "
        "serialize each in _jsonable_platform"
    )


def test_capability_snapshot_records_multi_harness_task_surfaces():
    from autorun.capability_snapshot import build_capability_snapshot

    platforms = build_capability_snapshot()["platforms"]

    assert platforms["claude"]["task_management_style"] == "task_tools"
    assert platforms["codex"]["task_management_style"] == "plan_checklist"
    assert platforms["gemini"]["task_management_style"] == "bulk_todos"
    assert platforms["qwen"]["task_management_style"] == "bulk_todos"
    assert platforms["antigravity"]["task_management_style"] == "none"
    assert platforms["antigravity"]["task_create_tools"] == []


def test_capability_snapshot_exposes_each_harness_hook_contract():
    """Diagnostics must explain wire behavior without reading implementation code."""
    from autorun.capability_snapshot import build_capability_snapshot

    platforms = build_capability_snapshot()["platforms"]
    assert platforms["claude"]["pretool_decision_location"] == "root_and_hook_specific_output"
    assert platforms["gemini"]["stop_blocking_decision"] == "deny"
    assert platforms["qwen"]["pretool_decision_location"] == "hook_specific_output"
    assert platforms["antigravity"]["hook_manifest_container_key"] == "autorun"
    assert platforms["codex"]["context_events_with_block_decision"] == ["PostToolUse", "UserPromptSubmit"]


def test_capability_snapshot_records_command_turn_behavior_without_replacing_handlers():
    from autorun.capability_snapshot import build_capability_snapshot

    snapshot = build_capability_snapshot()
    turn_behavior = snapshot["command_starts_agent_turn"]

    for alias in ("/ar:go", "/ar:run", "/ar:gp", "/ar:proc"):
        assert turn_behavior[alias] is True
    for alias in ("/ar:pn", "/ar:pr", "/ar:pu", "/ar:pp"):
        assert turn_behavior[alias] is True
    for alias in ("/ar:st", "/ar:stop", "/ar:sos", "/ar:reload"):
        assert turn_behavior[alias] is False


def test_pi_disposition_covers_every_derived_identity():
    from autorun.capability_snapshot import build_capability_snapshot

    snapshot = build_capability_snapshot()
    disposition = snapshot["pi_disposition"]
    allowed = {"native", "adapted", "intentionally_unsupported", "not_applicable"}

    assert set(disposition["commands"]) == set(snapshot["commands"])
    assert set(disposition["hook_events"]) == set(snapshot["hook_events"])
    assert set(disposition["skills"]) == set(snapshot["skills"])
    assert set(disposition["command_docs"]) == set(snapshot["command_docs"])
    assert all(value in allowed for group in disposition.values() for value in group.values())
    assert disposition["hook_events"]["PreCompact"] == "adapted"
    assert disposition["hook_events"]["PreToolUse"] == "adapted"


def test_capability_snapshot_aliases_have_one_owner():
    from autorun.capability_snapshot import build_capability_snapshot

    aliases = build_capability_snapshot()["command_aliases"]
    owners_by_alias = {}
    for command_name, command_aliases in aliases.items():
        for alias in command_aliases:
            owners_by_alias.setdefault(alias, set()).add(command_name)

    assert {alias: owners for alias, owners in owners_by_alias.items() if len(owners) > 1} == {}


def test_capability_snapshot_command_docs_cover_runtime_ar_aliases():
    from autorun.capability_snapshot import build_capability_snapshot

    snapshot = build_capability_snapshot()
    command_docs = snapshot["command_docs"]
    # A workflow converted to a skill still answers to `/ar:<name>`: Claude Code
    # namespaces plugin skills as `/<plugin>:<skill>` and this plugin is `ar`.
    # Either surface documents the spelling; neither means it is undocumented.
    documented = set(command_docs) | set(snapshot["skills"])

    missing_docs = sorted(alias for alias in snapshot["commands"] if alias.startswith("/ar:") and alias.removeprefix("/ar:") not in documented)

    assert missing_docs == []
    assert command_docs["restart-daemon"]["executable"] is True
    assert "current autorun install/source tree" in command_docs["restart-daemon"]["description"]
    # `/ar:task status` and `/ar:task ignore` are subcommands of task.md's
    # documented grammar; a per-subcommand document would be a second copy.
    assert "task-status" not in command_docs
    assert "task-ignore" not in command_docs


def test_capability_snapshot_covers_installed_skills_with_descriptions():
    """The machine-readable API must include every marketplace plugin skill."""
    from autorun.capability_snapshot import build_capability_snapshot

    snapshot = build_capability_snapshot()
    plugins_root = Path(__file__).parents[2]
    expected = {path.parent.name for path in plugins_root.glob("*/skills/*/SKILL.md")}

    assert set(snapshot["skills"]) == expected
    assert snapshot["plugin_skills"]["pdf-extractor"] == ["pdf-extractor"]
    assert "pdf-extractor" in snapshot["skills"]
    assert all(skill["name"] for skill in snapshot["skills"].values())
    assert all(skill["description"] for skill in snapshot["skills"].values())


def test_capability_snapshot_cli_writes_json_without_touching_home(tmp_path):
    output_path = tmp_path / "capabilities.json"
    fake_home = tmp_path / "home"
    env = {
        "HOME": str(fake_home),
        "USERPROFILE": str(fake_home),
        "AUTORUN_HOME": str(tmp_path / "autorun-home"),
        "AUTORUN_TEST_STATE_DIR": str(tmp_path / "state"),
        "AUTORUN_USE_DAEMON": "0",
    }

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "autorun",
            "--capability-snapshot",
            str(output_path),
        ],
        env={**env, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=10,
    )
    skip_if_windows_service_provider_error(result)

    assert result.returncode == 0, result.stderr or result.stdout
    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert data["platforms"]["codex"]["command_display_prefix"] == "ar:"
    assert not (fake_home / ".codex" / "hooks.json").exists()
    assert not (fake_home / ".claude").exists()
