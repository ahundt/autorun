"""Keep every registered backend tied to an explicit E2E boundary."""

from concurrent.futures import ThreadPoolExecutor
import json
import uuid
from pathlib import Path

import pytest

from autorun.platforms import PLATFORMS
from e2e_support import (
    BACKEND_E2E_CONTRACTS,
    RETIRED_GEMINI_BACKEND_ENV,
    find_task_recovery_marker,
    installed_task_pause_command_is_current,
    live_model_env,
    model_override,
    retired_gemini_backend_enabled,
    run_isolated_hook,
)
from test_codex_e2e_real_money import _codex_exec_command


TEST_ROOT = Path(__file__).parent
CONCURRENT_WARNING_CALLS = 4


def test_every_registered_backend_has_an_e2e_contract():
    """New platforms must declare their strongest supported E2E surface."""
    assert set(BACKEND_E2E_CONTRACTS) == set(PLATFORMS)


def test_every_backend_contract_points_to_a_real_test_module():
    """Coverage declarations must remain connected to executable tests."""
    for contract in BACKEND_E2E_CONTRACTS.values():
        assert (TEST_ROOT / contract.module).is_file(), contract


def test_hook_and_model_contracts_match_platform_capabilities():
    """Do not claim live hook coverage for platforms without a hook API."""
    for name, platform in PLATFORMS.items():
        contract = BACKEND_E2E_CONTRACTS[name]
        assert contract.hook_process is platform.has_hooks
        assert contract.isolation

    assert not BACKEND_E2E_CONTRACTS["gemini"].live_model
    assert not BACKEND_E2E_CONTRACTS["forgecode"].live_model


def test_retired_gemini_model_calls_require_the_dedicated_override(monkeypatch):
    monkeypatch.delenv(RETIRED_GEMINI_BACKEND_ENV, raising=False)
    assert not retired_gemini_backend_enabled()
    monkeypatch.setenv(RETIRED_GEMINI_BACKEND_ENV, "1")
    assert retired_gemini_backend_enabled()


@pytest.mark.parametrize("cli", ["claude", "gemini", "antigravity", "qwen", "codex"])
def test_registered_hook_backends_execute_isolated_process(cli, tmp_path):
    """Every hook backend must complete one real, daemon-free hook process."""
    plugin_root = TEST_ROOT.parent
    result = run_isolated_hook(
        plugin_root=plugin_root,
        hook_script=plugin_root / "hooks" / "hook_entry.py",
        cli=cli,
        payload={
            "hook_event_name": "SessionStart",
            "session_id": f"contract-{cli}-{uuid.uuid4().hex}",
            "cwd": str(tmp_path),
        },
    )
    assert result.returncode == 0, result.stderr
    if result.stdout.strip():
        assert isinstance(json.loads(result.stdout), dict)


@pytest.mark.parametrize("cli", ["claude", "gemini", "antigravity", "qwen", "codex"])
@pytest.mark.parametrize("root", ["task", "tasks"])
def test_task_pause_command_returns_recovery_marker_through_real_hook_process(
    cli,
    root,
    tmp_path,
):
    """Every hook harness and transport form preserves reason-only pause semantics."""
    plugin_root = TEST_ROOT.parent
    reason = "discuss the release boundary before implementation"
    for prefix in PLATFORMS[cli].command_prefixes:
        result = run_isolated_hook(
            plugin_root=plugin_root,
            hook_script=plugin_root / "hooks" / "hook_entry.py",
            cli=cli,
            payload={
                "hook_event_name": "UserPromptSubmit",
                "session_id": f"task-pause-{cli}-{uuid.uuid4().hex}",
                "cwd": str(tmp_path),
                "prompt": f"{prefix}{root} pause {reason}",
            },
        )

        assert result.returncode == 0, result.stderr
        assert find_task_recovery_marker(result.stdout), result.stdout
        assert reason in result.stdout
        assert "permanent" in result.stdout


@pytest.mark.parametrize("cli", ["claude", "gemini", "antigravity", "qwen", "codex"])
def test_bare_task_pause_is_five_minutes_through_real_hook_process(
    cli,
    tmp_path,
):
    plugin_root = TEST_ROOT.parent
    prefix = PLATFORMS[cli].command_display_prefix
    result = run_isolated_hook(
        plugin_root=plugin_root,
        hook_script=plugin_root / "hooks" / "hook_entry.py",
        cli=cli,
        payload={
            "hook_event_name": "UserPromptSubmit",
            "session_id": f"bare-task-pause-{cli}-{uuid.uuid4().hex}",
            "cwd": str(tmp_path),
            "prompt": f"{prefix}task pause",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "5m0s" in result.stdout
    assert find_task_recovery_marker(result.stdout), result.stdout


def test_installed_task_pause_command_generation_check_is_fail_closed(tmp_path):
    """Paid tests must not exercise stale installed command assets."""
    missing = tmp_path / "missing.toml"
    stale = tmp_path / "stale.toml"
    current = tmp_path / "current.toml"
    stale.write_text('prompt = "legacy task staleness command"')
    current.write_text('prompt = "generation-bound recovery marker"')

    assert not installed_task_pause_command_is_current(missing)
    assert not installed_task_pause_command_is_current(stale)
    assert installed_task_pause_command_is_current(current)


@pytest.mark.parametrize("cli", ["claude", "gemini", "antigravity", "qwen", "codex"])
def test_concurrent_git_warning_is_attempted_once_through_real_hook_processes(
    cli,
    tmp_path,
):
    """Cross-process hook entrypoints share one atomic warning claim per session."""
    plugin_root = TEST_ROOT.parent
    platform = PLATFORMS[cli]
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": f"dedup-{cli}-{uuid.uuid4().hex}",
        "cwd": str(tmp_path),
        "tool_name": platform.tool_names["bash"],
        "tool_input": {"command": "git commit -m e2e-dedup-check"},
    }

    def invoke(_index):
        return run_isolated_hook(
            plugin_root=plugin_root,
            hook_script=plugin_root / "hooks" / "hook_entry.py",
            cli=cli,
            payload=payload,
        )

    with ThreadPoolExecutor(max_workers=CONCURRENT_WARNING_CALLS) as pool:
        results = list(pool.map(invoke, range(CONCURRENT_WARNING_CALLS)))

    assert all(result.returncode == 0 for result in results)
    assert (
        sum("Git commit rules:" in result.stdout for result in results) == 1
    ), [result.stdout for result in results]


def test_paid_model_defaults_are_small_and_bounded(tmp_path, monkeypatch):
    """Free assertions lock paid suites to the smallest capable defaults."""
    monkeypatch.delenv("AUTORUN_CLAUDE_E2E_MODEL", raising=False)
    assert model_override("AUTORUN_CLAUDE_E2E_MODEL", "haiku") == "haiku"

    command = _codex_exec_command("gpt-5.3-codex-spark", tmp_path, tmp_path / "out", "OK")
    assert ["-c", 'model_reasoning_effort="low"'] == command[2:4]
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("--cd") + 1] == str(tmp_path)


def test_live_model_env_enables_daemon_and_preserves_isolated_home(tmp_path):
    """Paid CLI tests use daemon IPC inside pytest's private AUTORUN_HOME."""
    isolated_home = str(tmp_path / "autorun-home")
    env = live_model_env(
        {
            "AUTORUN_HOME": isolated_home,
            "AUTORUN_USE_DAEMON": "0",
            "AUTORUN_TEST_MODE": "1",
        }
    )
    assert env["AUTORUN_HOME"] == isolated_home
    assert "AUTORUN_USE_DAEMON" not in env
    assert "AUTORUN_TEST_MODE" not in env
