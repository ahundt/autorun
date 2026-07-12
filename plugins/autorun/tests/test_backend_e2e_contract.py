"""Keep every registered backend tied to an explicit E2E boundary."""

import json
import uuid
from pathlib import Path

import pytest

from autorun.platforms import PLATFORMS
from e2e_support import BACKEND_E2E_CONTRACTS, model_override, run_isolated_hook
from test_codex_e2e_real_money import _codex_exec_command


TEST_ROOT = Path(__file__).parent


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


def test_paid_model_defaults_are_small_and_bounded(tmp_path, monkeypatch):
    """Free assertions lock paid suites to the smallest capable defaults."""
    monkeypatch.delenv("AUTORUN_CLAUDE_E2E_MODEL", raising=False)
    assert model_override("AUTORUN_CLAUDE_E2E_MODEL", "haiku") == "haiku"

    command = _codex_exec_command("gpt-5.3-codex-spark", tmp_path, tmp_path / "out", "OK")
    assert ["-c", 'model_reasoning_effort="low"'] == command[2:4]
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("--cd") + 1] == str(tmp_path)
