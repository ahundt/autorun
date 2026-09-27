"""Clearing os.environ in a test without leaving the isolation contract.

A test that runs code "with no environment" usually means "without the variable
under test", but `patch.dict(os.environ, {}, clear=True)` also drops
AUTORUN_HOME and the test state variables. For the length of the block, anything
that resolves autorun's directory in that process, including another thread,
falls back to the developer's real ~/.autorun. That created the real directory
on some suite runs and not others.
"""

from __future__ import annotations

import os

#: The variables conftest.py exports so no test reaches live state. The one
#: list: test_runtime_isolation_canaries.py checks the same names.
REQUIRED_ISOLATION_VARS = (
    "AUTORUN_HOME",
    "AUTORUN_TEST_STATE_DIR",
    "AUTORUN_TEST_RUNTIME_DIR",
)


def isolation_only(**extra: str) -> dict[str, str]:
    """The isolation variables as currently set, plus ``extra``.

    Pass it as the replacement mapping to `patch.dict(os.environ, ..., clear=True)`
    to clear everything else.
    """
    kept = {name: os.environ[name] for name in REQUIRED_ISOLATION_VARS if name in os.environ}
    kept.update(extra)
    return kept
