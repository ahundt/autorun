import json
import subprocess
import sys
from pathlib import Path


def _run_isolated_probe(code: str, tmp_path: Path) -> subprocess.CompletedProcess:
    env = {
        "HOME": str(tmp_path / "home"),
        "AUTORUN_HOME": str(tmp_path / "autorun-home"),
        "AUTORUN_TEST_STATE_DIR": str(tmp_path / "state"),
        "AUTORUN_USE_DAEMON": "0",
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )


def test_ipc_uses_autorun_home_before_import(tmp_path):
    result = _run_isolated_probe(
        """
import json
import autorun.ipc as ipc
print(json.dumps({
    "config_dir": str(ipc.AUTORUN_CONFIG_DIR),
    "socket": str(ipc.AUTORUN_SOCKET_PATH),
}))
""",
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["config_dir"] == str(tmp_path / "autorun-home")
    assert data["socket"].startswith(str(tmp_path / "autorun-home"))


def test_session_manager_uses_test_state_dir_before_import(tmp_path):
    result = _run_isolated_probe(
        """
import json
from autorun.session_manager import get_session_manager, session_state
with session_state("session-a") as state:
    state["value"] = "ok"
manager = get_session_manager()
print(json.dumps({
    "state_dir": str(manager.state_dir),
    "files": sorted(p.name for p in manager.state_dir.iterdir()),
}))
""",
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["state_dir"] == str(tmp_path / "state")
    assert "daemon_state.json" in data["files"]
    assert not (tmp_path / "home" / ".claude" / "sessions").exists()
