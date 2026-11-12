"""`autorun file`, `autorun task` and `autorun tmux` with no operation print
that subcommand's usage and exit 1.

`file` and `task` built their help with create_parser().add_subparsers(),
and argparse refuses a second subparser group on the same parser, so both
crashed with `argparse.ArgumentError: cannot have multiple subparser
arguments` instead of saying what to type.
"""

import pytest

from autorun.__main__ import main


@pytest.mark.parametrize('command, operations', [
    ('file', ('allow', 'justify', 'existing-only', 'status')),
    ('task', ('status', 'export', 'clear', 'gc')),
    ('tmux', ('list', 'send')),
])
def test_a_bare_subcommand_prints_its_usage_and_exits_nonzero(command, operations, capsys):
    assert main([command]) == 1

    out = capsys.readouterr()
    assert f'usage: autorun {command}' in out.out or f'{command} [-h]' in out.out, out.out
    assert all(op in out.out for op in operations), out.out
    assert 'Traceback' not in out.out + out.err
