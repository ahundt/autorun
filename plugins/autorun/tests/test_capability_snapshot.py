import json
import subprocess
import sys
from pathlib import Path


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


def test_capability_snapshot_records_multi_harness_task_surfaces():
    from autorun.capability_snapshot import build_capability_snapshot

    platforms = build_capability_snapshot()["platforms"]

    assert platforms["claude"]["task_management_style"] == "task_tools"
    assert platforms["codex"]["task_management_style"] == "plan_checklist"
    assert platforms["gemini"]["task_management_style"] == "bulk_todos"
    assert platforms["qwen"]["task_management_style"] == "bulk_todos"
    assert platforms["antigravity"]["task_management_style"] == "bulk_todos"


def test_capability_snapshot_aliases_have_one_owner():
    from autorun.capability_snapshot import build_capability_snapshot

    aliases = build_capability_snapshot()["command_aliases"]
    owners_by_alias = {}
    for command_name, command_aliases in aliases.items():
        for alias in command_aliases:
            owners_by_alias.setdefault(alias, set()).add(command_name)

    assert {
        alias: owners for alias, owners in owners_by_alias.items() if len(owners) > 1
    } == {}


def test_capability_snapshot_command_docs_cover_runtime_ar_aliases():
    from autorun.capability_snapshot import build_capability_snapshot

    snapshot = build_capability_snapshot()
    command_docs = snapshot["command_docs"]

    missing_docs = sorted(
        alias for alias in snapshot["commands"]
        if alias.startswith("/ar:") and alias.removeprefix("/ar:") not in command_docs
    )

    assert missing_docs == []
    assert command_docs["restart-daemon"]["executable"] is True
    assert "current autorun install/source tree" in command_docs["restart-daemon"]["description"]
    assert command_docs["task-ignore"]["aliases"] == ["ti", "ignore-task"]


def test_capability_snapshot_covers_installed_skills_with_descriptions():
    """The machine-readable API must include every marketplace plugin skill."""
    from autorun.capability_snapshot import build_capability_snapshot

    snapshot = build_capability_snapshot()
    plugins_root = Path(__file__).parents[2]
    expected = {
        path.parent.name for path in plugins_root.glob("*/skills/*/SKILL.md")
    }

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
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert data["platforms"]["codex"]["command_display_prefix"] == "ar:"
    assert not (fake_home / ".codex" / "hooks.json").exists()
    assert not (fake_home / ".claude").exists()
