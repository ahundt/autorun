"""Shared isolation and capability contracts for harness E2E tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


REAL_MONEY_ENV = "AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY"


@dataclass(frozen=True, slots=True)
class BackendE2EContract:
    """Declare the strongest meaningful E2E boundary for one platform."""

    module: str
    hook_process: bool
    live_model: bool
    isolation: str


BACKEND_E2E_CONTRACTS = {
    "claude": BackendE2EContract("test_claude_e2e_real_money.py", True, True, "temporary cwd and unique session"),
    "gemini": BackendE2EContract("test_gemini_e2e_real_money.py", True, False, "retired model backend; hook process only"),
    "antigravity": BackendE2EContract("test_antigravity_e2e_real_money.py", True, True, "sandboxed print and temporary log"),
    "qwen": BackendE2EContract("test_qwen_e2e_real_money.py", True, True, "bare, no history, zero tools"),
    "codex": BackendE2EContract("test_codex_e2e_real_money.py", True, True, "read-only sandbox and temporary cwd"),
    "forgecode": BackendE2EContract("test_install_pathways.py", False, False, "advisory install; no external hook API"),
}


def real_money_enabled() -> bool:
    """Return whether the caller explicitly opted into paid model calls."""
    return os.environ.get(REAL_MONEY_ENV, "0") == "1"


def model_override(env_name: str, default: str) -> str:
    """Resolve a paid-test model with an explicit low-cost default."""
    return os.environ.get(env_name, default).strip() or default


def isolated_hook_env(plugin_root: Path, session_id: str) -> dict[str, str]:
    """Build a direct-hook environment that cannot reach the shared daemon."""
    env = os.environ.copy()
    env.update(
        {
            "AUTORUN_PLUGIN_ROOT": str(plugin_root),
            "AUTORUN_USE_DAEMON": "0",
            "AUTORUN_TEST_MODE": "1",
            "AUTORUN_SESSION_ID": session_id,
        }
    )
    return env


def run_isolated_hook(
    *,
    plugin_root: Path,
    hook_script: Path,
    cli: str,
    payload: dict,
    timeout: int = 20,
) -> subprocess.CompletedProcess[str]:
    """Execute one real hook process with unique state and no shared daemon."""
    session_id = str(payload.get("session_id") or f"e2e-{cli}-{os.getpid()}")
    return subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(plugin_root),
            sys.executable,
            str(hook_script),
            "--cli",
            cli,
        ],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=isolated_hook_env(plugin_root, session_id),
    )
