"""The JavaScript bridge's timer is the outermost budget for Pi, Prime and
OpenCode, and config.py's layering needs it to stay outermost:
daemon response < hook wrapper < bridge. A bridge timer at or under the
wrapper kills the fallback hook before it can answer, which fails closed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autorun.config import CONFIG

SRC = Path(__file__).parents[1] / "src" / "autorun"
BRIDGE = SRC / "bridge_template" / "daemon-client.mjs"
BRIDGE_USERS = {
    "pi": SRC / "pi_template" / "extensions" / "autorun" / "index.ts",
    "prime": SRC / "pi_template" / "extensions" / "autorun" / "index.ts",
    "opencode": SRC / "opencode_template" / "plugin" / "autorun.js",
}


def _bridge_seconds(source: Path) -> float:
    """The timeout a bridge user passes, else the bridge's default."""
    passed = re.search(r"timeoutMs:\s*(\d+)", source.read_text(encoding="utf-8"))
    if passed:
        return int(passed.group(1)) / 1000
    default = re.search(r"timeoutMs\s*=\s*(\d+)", BRIDGE.read_text(encoding="utf-8"))
    assert default, "daemon-client.mjs lost its timeoutMs default"
    return int(default.group(1)) / 1000


@pytest.mark.parametrize("cli_type", sorted(BRIDGE_USERS))
def test_the_bridge_timer_is_the_outermost_budget(cli_type):
    response = CONFIG["daemon_client_response_timeouts_seconds"][cli_type]
    wrapper = CONFIG["hook_wrapper_timeouts_seconds"][cli_type]
    bridge = _bridge_seconds(BRIDGE_USERS[cli_type])
    assert response < wrapper < bridge, (cli_type, response, wrapper, bridge)
