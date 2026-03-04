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
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'src', 'autorun'))
import python_check as _pc  # auto-exits with helpful message if Python < 3.10
_sys.path.pop(0)
del _sys, _os, _pc
