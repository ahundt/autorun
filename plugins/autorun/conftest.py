# -*- coding: utf-8 -*-
# conftest.py — pytest automatically discovers and loads this file before running
# any tests in this directory or subdirectories. This is a standard pytest convention:
# https://docs.pytest.org/en/stable/reference/fixtures.html#conftest-py-sharing-fixtures-across-files
#
# This parent conftest.py (plugins/autorun/conftest.py) runs BEFORE
# plugins/autorun/tests/conftest.py and provides a Python version guard.
# The tests/conftest.py provides fixtures, markers, and daemon lifecycle management.
#
# Python 3.10+ guard -- loads src/autorun/python_check.py by file path so it
# runs BEFORE tests/conftest.py is parsed (which contains Python 3.6+ class
# annotations that cause a SyntaxError at parse time in Python 2).
#
# We import by file path rather than 'from autorun.python_check import ...'
# because that would trigger autorun/__init__.py (Python 3 syntax) to be
# parsed first. The relative import inside python_check.py is inside a
# try/except (ImportError, SyntaxError, ValueError) so it gracefully falls back.
#
# python_check.py auto-calls check_and_exit() when sys.version_info < (3, 10),
# so 'import _pc' below is the entire guard -- one effective line.
#
# NOTE: Keep this file in pure Python 2/3 compatible syntax.
import os as _os
import sys as _sys
import tempfile as _tempfile

# Keep deliberate test controls, never the invoking hook's runtime settings.
_test_controls = (
    'AUTORUN_KEEP_TEST_ARTIFACTS', 'AUTORUN_REQUIRE_JS_RUNTIME',
    'AUTORUN_TMUX_BIN', 'AUTORUN_ORIGINAL_TMUX',
    'AUTORUN_ALLOW_RETIRED_GEMINI_CLI_BACKEND_TESTS',
)
for _name in list(_os.environ):
    if (_name.startswith('AUTORUN_')
            and not _name.startswith(('AUTORUN_TEST_', 'AUTORUN_ENABLE_'))
            and '_E2E_' not in _name
            and _name not in _test_controls):
        _os.environ.pop(_name, None)
for _name in (
    # UV_PYTHON stays: CI's setup-uv selects each matrix interpreter with it,
    # and without it the repo's .python-version would steer test 'uv run'.
    'UV_PROJECT', 'UV_PROJECT_ENVIRONMENT', 'UV_WORKING_DIR',
    'UV_CONFIG_FILE', 'UV_TOOL_DIR', 'UV_TOOL_BIN_DIR', 'UV_PYTHON_INSTALL_DIR',
    'XDG_DATA_HOME',
):
    _os.environ.pop(_name, None)
_os.environ.pop('CLAUDE_PLUGIN_ROOT', None)
del _test_controls

# Isolate state plus daemon socket/PID/logs before tests/conftest.py or the
# autorun package can import modules whose paths are resolved at import time.
#
# The root has to be SHORT. The daemon socket lives at
# <runtime>/autorun-home/daemon.sock, and AF_UNIX sun_path is 104 bytes on
# macOS. The platform temp directory there is /var/folders/<32 chars>/T, which
# measured 103 bytes for that socket -- one byte of headroom -- and 111 bytes
# once realpath expands /var to /private/var. Overflow raises nothing
# recognisable: the daemon simply never listens and every test that needs it
# reports a hook timeout instead. Prefer a short root and say so loudly if even
# that will not fit, rather than shipping a harness that works by one byte.
#
# mkdtemp already gives each *process* its own root, and pytest-xdist workers
# are separate processes that each import this file, so per-worker isolation
# falls out of that. Keeping the root short is what makes running several of
# them at once viable at all.
_runtime = None
for _candidate in ('/tmp', None):
    if _candidate is not None and not _os.path.isdir(_candidate):
        continue
    try:
        _runtime = _tempfile.mkdtemp(prefix='ar_test_', dir=_candidate)
        break
    except OSError:
        continue
if _runtime is None:
    raise RuntimeError('no writable temporary directory for the test runtime')
del _candidate
_state = _os.path.join(_runtime, 'sessions')
_home = _os.path.join(_runtime, 'autorun-home')
_os.makedirs(_state)
_os.makedirs(_home)

# Fail here, naming the measurement, instead of letting it surface later as an
# unexplained timeout. Windows has no AF_UNIX (CPython #77589) and the daemon
# listens on loopback there, so the limit does not apply.
if hasattr(__import__('socket'), 'AF_UNIX'):
    _sock = _os.path.realpath(_os.path.join(_home, 'daemon.sock'))
    _limit = 104 if _sys.platform == 'darwin' else 108
    _sock_size = len(_sock.encode(_sys.getfilesystemencoding() or 'utf-8'))
    if _sock_size >= _limit:
        raise RuntimeError(
            'test daemon socket path is %d bytes, limit %d: %s\n'
            'Set TMPDIR to a shorter directory before running pytest.'
            % (_sock_size, _limit, _sock)
        )
    del _sock, _limit, _sock_size

# The home this run started with, for the two things that must still see it
# after the redirect below: the canary that fails the suite when a test
# modifies the installed copy, and the opt-in live-install checks. setdefault,
# because an xdist worker inherits the controller's already-redirected HOME.
_os.environ.setdefault('AUTORUN_TEST_REAL_HOME', _os.path.expanduser('~'))
_os.environ['AUTORUN_TEST_RUNTIME_DIR'] = _runtime
_os.environ['AUTORUN_TEST_STATE_DIR'] = _state
_os.environ['AUTORUN_HOME'] = _home
# Harness discovery and installers also resolve paths through the process home.
_process_home = _os.path.join(_runtime, 'home')
_os.makedirs(_process_home)
_os.environ['HOME'] = _process_home
_os.environ['USERPROFILE'] = _process_home
_pi_home = _os.path.join(_process_home, '.pi', 'agent')
_os.makedirs(_pi_home)
_os.environ['PI_CODING_AGENT_DIR'] = _pi_home

_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'src', 'autorun'))
import python_check as _pc  # noqa: E402  # auto-exits with helpful message if Python < 3.10
_sys.path.pop(0)
del _sys, _os, _tempfile, _runtime, _state, _home, _process_home, _pi_home, _pc
