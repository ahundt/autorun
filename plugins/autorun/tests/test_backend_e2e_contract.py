"""Keep every registered backend tied to an explicit E2E boundary."""

from concurrent.futures import ThreadPoolExecutor
import json
import re
import uuid
from pathlib import Path

import pytest

from autorun.platforms import PLATFORMS
from e2e_support import (
    BACKEND_E2E_CONTRACTS,
    autorun_extension_listed,
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


@pytest.mark.parametrize(
    ("listing", "expected"),
    [
        ("✓ ar (installed)\n✓ conductor (0.1.0)", True),
        ("autorun-workspace (0.9.0)\n", True),
        ("autorun is not installed\n", False),
        ("✓ archive-tools (1.0.0)\n", False),
    ],
)
def test_gemini_extension_identity_accepts_current_id_and_legacy_aliases(listing, expected):
    """The installed Gemini ID is ``ar``; aliases remain backward compatible."""
    assert autorun_extension_listed(listing) is expected


@pytest.mark.parametrize("cli", ["claude", "gemini", "qwen", "codex"])
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


@pytest.mark.parametrize("cli", ["claude", "gemini", "qwen", "codex"])
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


@pytest.mark.parametrize("cli", ["claude", "gemini", "qwen", "codex"])
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
    # The hook reports a live countdown.  Process startup consumes a small
    # amount of the five-minute grant (Windows runners routinely spend 1–3s
    # starting ``uv``), so assert the configured TTL rather than one exact
    # wall-clock rendering.
    countdown = re.search(r"active \((?:(\d+)m)?(\d+)s remaining\)", result.stdout)
    assert countdown, result.stdout
    minutes = int(countdown.group(1) or 0)
    seconds = minutes * 60 + int(countdown.group(2))
    assert 240 <= seconds <= 300, result.stdout
    assert find_task_recovery_marker(result.stdout), result.stdout


@pytest.mark.parametrize("cli", ["claude", "gemini", "qwen", "codex", "pi", "prime"])
def test_remote_write_commands_are_denied_through_real_hook_process(cli, tmp_path):
    """Every hook backend, including the Pi family, gates pushes and releases.

    A local ``git tag`` is deliberately allowed: it reaches a server only through
    ``git push``, which is the gated step. Pi and Prime run the same
    ``hook_entry.py --cli`` fallback the installed extension spawns when the
    daemon is unreachable, so this is the exact process boundary a Pi session
    crosses before a tool executes.
    """
    plugin_root = TEST_ROOT.parent
    tool_name = PLATFORMS[cli].tool_names["bash"]
    expectations = {
        "git push origin main": "deny",
        "git tag -a v9.9.9 -m x && git push origin v9.9.9": "deny",
        "gh release create v9.9.9 --notes x": "deny",
        "git tag v9.9.9": "allow",
    }
    for command, expected in expectations.items():
        result = run_isolated_hook(
            plugin_root=plugin_root,
            hook_script=plugin_root / "hooks" / "hook_entry.py",
            cli=cli,
            cwd=tmp_path,
            payload={
                "hook_event_name": "PreToolUse",
                "session_id": f"remote-write-{cli}-{uuid.uuid4().hex}",
                "cwd": str(tmp_path),
                "tool_name": tool_name,
                "tool_input": {"command": command},
            },
        )
        response = json.loads(result.stdout) if result.stdout.strip() else {}
        specific = response.get("hookSpecificOutput", {})
        decision = specific.get("permissionDecision") or response.get("decision")
        reason = specific.get("permissionDecisionReason") or response.get("reason") or ""
        # Claude Code's deny is exit 2 with the reason on stderr (bug #4669
        # workaround, see config.should_use_exit2_workaround); every other
        # backend answers with exit 0 and a JSON veto on stdout.
        if result.returncode == 2:
            decision, reason = "deny", result.stderr
        else:
            assert result.returncode == 0, (cli, command, result.stderr)
        if expected == "deny":
            assert decision in ("deny", "block"), (cli, command, response, result.stderr)
            assert "permission" in reason, (cli, command, reason)
        else:
            # An allowed call may carry advisory context but never a veto;
            # harnesses differ on whether they spell out "allow" at all.
            assert decision not in ("deny", "block"), (cli, command, response)


def test_antigravity_does_not_claim_a_prompt_hook_or_task_command(tmp_path):
    """The official Agy hook list has no user-prompt event or checklist API."""
    plugin_root = TEST_ROOT.parent
    result = run_isolated_hook(
        plugin_root=plugin_root,
        hook_script=plugin_root / "hooks" / "hook_entry.py",
        cli="antigravity",
        event="UserPromptSubmit",
        payload={
            "conversationId": f"agy-no-prompt-{uuid.uuid4().hex}",
            "workspacePaths": [str(tmp_path)],
            "prompt": "ar:task pause",
        },
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}
    assert PLATFORMS["antigravity"].task_management_style == "none"
    assert "UserPromptSubmit" not in PLATFORMS["antigravity"].native_hook_events


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
    monkeypatch,
):
    """Cross-process hook entrypoints share one atomic warning claim per session.

    The claim primitive deliberately fails open when its lock budget runs out
    (a duplicated warning beats a lost one), and four concurrent uv-run hook
    processes on a saturated two-core CI runner can genuinely exhaust that
    budget. AUTORUN_DEBUG=1 makes the fail-open path observable in the
    isolated home's log, so measured contention skips with evidence instead
    of masquerading as an atomicity bug — while a duplicate WITHOUT that
    evidence still fails, which is the defect this test exists to catch.
    """
    from autorun import ipc

    monkeypatch.setenv("AUTORUN_DEBUG", "1")
    plugin_root = TEST_ROOT.parent
    platform = PLATFORMS[cli]
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": f"dedup-{cli}-{uuid.uuid4().hex}",
        "cwd": str(tmp_path),
        "tool_name": platform.tool_names["bash"],
        "tool_input": {"command": "git commit -m e2e-dedup-check"},
    }
    log_file = Path(ipc.AUTORUN_LOG_FILE)
    log_offset = log_file.stat().st_size if log_file.is_file() else 0

    def invoke(_index):
        return run_isolated_hook(
            plugin_root=plugin_root,
            hook_script=plugin_root / "hooks" / "hook_entry.py",
            cli=cli,
            payload=payload,
        )

    with ThreadPoolExecutor(max_workers=CONCURRENT_WARNING_CALLS) as pool:
        results = list(pool.map(invoke, range(CONCURRENT_WARNING_CALLS)))

    delivered = sum("Git commit rules:" in result.stdout for result in results)
    timed_out = [
        result
        for result in results
        if "autorun CLI timed out after" in (result.stdout + result.stderr)
    ]
    try:
        with log_file.open(encoding="utf-8", errors="replace") as handle:
            handle.seek(log_offset)
            appended_log = handle.read()
    except OSError:
        appended_log = ""
    failed_open = "delivering fail-open" in appended_log
    if timed_out or (failed_open and delivered != 1):
        pytest.skip(
            f"runner contention: {len(timed_out)} wrapper timeouts, "
            f"fail-open logged={failed_open}, deliveries={delivered}; the "
            "claim contract explicitly duplicates rather than lose a warning "
            "when its lock budget is exhausted"
        )
    # Name the process that failed: a bare ``all(...)`` reports only a
    # generator, which is what a CI-only failure of this test looked like.
    crashed = [
        (result.returncode, result.stdout[-600:], result.stderr[-1200:])
        for result in results
        if result.returncode != 0
    ]
    assert crashed == [], crashed
    assert delivered == 1, [result.stdout for result in results]


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
