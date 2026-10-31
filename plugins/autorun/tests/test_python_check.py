"""python_check refuses every interpreter older than 3.10.

conftest.py and the repo-root autorun.py launcher rely on check_and_exit() to
stop an old interpreter with a readable message. The 3.0-3.9 branch printed
that message and then returned True, so check_and_exit() never exited there.
"""

import sys
import types

import pytest

from autorun import python_check


def _pretend(monkeypatch, version):
    # Replace the module's `sys` rather than the real one, so pytest itself
    # never sees a fake interpreter version.
    monkeypatch.setattr(
        python_check, "sys", types.SimpleNamespace(version_info=version, exit=sys.exit)
    )


@pytest.mark.parametrize(
    ("version", "expected"),
    [((2, 7, 18), False), ((3, 9, 18), False), ((3, 10, 0), True)],
)
def test_check_python_version(monkeypatch, capsys, version, expected):
    _pretend(monkeypatch, version)
    assert python_check.check_python_version() is expected


def test_check_and_exit_exits_on_python_3_9(monkeypatch, capsys):
    _pretend(monkeypatch, (3, 9, 18))
    with pytest.raises(SystemExit) as raised:
        python_check.check_and_exit()
    assert raised.value.code == 1
    assert "requires Python 3.10+" in capsys.readouterr().out
