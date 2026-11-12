"""`autorun tmux list` and `autorun tmux send`, through `autorun.__main__.main`.

`list` only reads. `send` types into the panes `--to` names, after printing
each target's session, window, pane, directory and harness, and it goes
through the same selection parser and sender as the `--execute` mode of
`commands/tmux-exec`. Each test states the violation it forbids:

- `list --json` printing anything other than the discovery JSON;
- `send` typing into a pane `--to` did not name, or typing before it has
  named the target;
- `send` with no `--to` (or no text) typing anything, or exiting 0;
- a failed send exiting 0.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from autorun import tmux_tab_ai_session_status as status
from autorun.__main__ import main

SESSIONS = [
    {'tmux_target': 'work:1', 'session_name': 'work', 'window_id': '1', 'pane': '0',
     'path': '~/src/one', 'harness': 'claude', 'content': '❯ \n', 'prompt_type': 'input',
     'is_active': False, 'title': 'one'},
    {'tmux_target': 'work:2', 'session_name': 'work', 'window_id': '2', 'pane': '1',
     'path': '~/src/two', 'harness': 'codex', 'content': '', 'prompt_type': 'unknown',
     'is_active': False, 'title': 'two'},
]


@pytest.fixture(autouse=True)
def _no_sdk_analysis(monkeypatch):
    """analyze_sessions calls the Anthropic API when a key is set; never here."""
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)


@pytest.fixture
def discovered():
    with patch.object(status, 'discover_claude_sessions',
                      side_effect=lambda tmux: [dict(s) for s in SESSIONS]) as discover, \
            patch.object(status, 'get_tmux_utilities', return_value=MagicMock()):
        yield discover


@pytest.fixture
def tmux():
    """The tmux every send reaches; send_keys succeeds and nothing is left on the input line."""
    fake = MagicMock()
    fake.send_keys.return_value = True
    with patch('autorun.tmux_utils.get_tmux_utilities', return_value=fake), \
            patch('autorun.tmux_utils._text_on_cursor_line', return_value=False), \
            patch('time.sleep'):
        yield fake


def test_list_json_prints_the_discovery_json(discovered, capsys):
    assert main(['tmux', 'list', '--json']) == 0

    data = json.loads(capsys.readouterr().out)
    assert data['count'] == 2
    assert [(s['tmux_target'], s['harness']) for s in data['sessions']] == [
        ('work:1', 'claude'), ('work:2', 'codex')]


def test_list_table_names_each_session_with_its_harness(discovered, capsys):
    assert main(['tmux', 'list']) == 0

    out = capsys.readouterr().out
    assert 'work:1' in out and 'claude' in out and 'codex' in out
    assert 'autorun tmux send --to' in out


def test_list_types_nothing(discovered, tmux):
    main(['tmux', 'list'])

    tmux.send_keys.assert_not_called()


def test_send_reaches_the_one_sender_with_that_selection(discovered, capsys):
    with patch.object(status, 'execute_session_selections', return_value=[]) as sender:
        main(['tmux', 'send', '--to', 'A', '--', 'x'])

    selection, sessions = sender.call_args.args[:2]
    assert selection == 'A'
    assert sender.call_args.kwargs['command'] == 'x'
    assert [s['tmux_target'] for s in sessions] == ['work:1', 'work:2']


def test_send_types_into_the_named_pane_only_after_naming_it(discovered, tmux, capsys):
    printed_before_typing = []
    tmux.send_keys.side_effect = lambda keys, *a, **k: (
        printed_before_typing.append(capsys.readouterr().out) or True)

    assert main(['tmux', 'send', '--to', 'B', '--', 'hello', 'there']) == 0

    typed = [(c.args[0], c.args[1], c.args[2]) for c in tmux.send_keys.call_args_list]
    assert typed == [('hello there', 'work', '2'), ('C-m', 'work', '2')]
    before = printed_before_typing[0]
    assert 'session work' in before and 'window 2' in before and 'pane 1' in before
    assert '~/src/two' in before and 'codex' in before
    assert 'work:1' not in before, 'named a pane it is not going to type into'


@pytest.mark.parametrize('argv', [
    ['tmux', 'send', '--', 'x'],
    ['tmux', 'send', 'x'],
    ['tmux', 'send', '--to', 'A'],
    ['tmux', 'send', '--to', 'A', '--'],
])
def test_send_without_a_target_or_text_types_nothing_and_fails(argv, discovered, tmux, capsys):
    assert main(argv) != 0

    tmux.send_keys.assert_not_called()
    out = capsys.readouterr().out
    if '--to' not in argv:
        assert 'all' in out and 'awaiting-input' in out and 'A' in out, out


def test_send_awaiting_input_skips_a_pane_whose_state_is_unknown(discovered, tmux, capsys):
    assert main(['tmux', 'send', '--to', 'awaiting-input', '--', 'go']) == 0

    assert {c.args[1:3] for c in tmux.send_keys.call_args_list} == {('work', '1')}


def test_send_accepts_awaiting_for_awaiting_input(discovered, tmux):
    assert main(['tmux', 'send', '--to', 'awaiting', '--', 'go']) == 0

    assert {c.args[1:3] for c in tmux.send_keys.call_args_list} == {('work', '1')}


def test_a_failed_send_exits_nonzero(discovered, tmux, capsys):
    tmux.send_keys.return_value = False

    assert main(['tmux', 'send', '--to', 'A', '--', 'x']) == 1
    assert '[FAIL]' in capsys.readouterr().out


def test_an_invalid_selection_types_nothing_and_exits_nonzero(discovered, tmux):
    assert main(['tmux', 'send', '--to', 'Z', '--', 'x']) == 1

    tmux.send_keys.assert_not_called()


def test_status_module_main_takes_argv(discovered, capsys, monkeypatch):
    monkeypatch.setattr('sys.argv', ['tmux-exec', '--execute'])

    assert status.main(['list', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['count'] == 2


def test_bare_tmux_prints_its_help_and_exits_nonzero(capsys):
    assert main(['tmux']) == 1
    out = capsys.readouterr().out
    assert 'list' in out and 'send' in out


def test_tmux_exec_runs_the_same_subcommand_without_autorun_on_path():
    """The fallback for Claude Code marketplace installs: the script runs
    `autorun tmux` from the plugin's own source. With no --to it must refuse
    before it discovers anything, so this touches no tmux server."""
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / 'commands' / 'tmux-exec'
    result = subprocess.run([sys.executable, str(script), 'send', '--', 'x'],
                            capture_output=True, text=True, timeout=60)

    assert result.returncode == 1, result.stdout + result.stderr
    assert 'Nothing sent' in result.stdout and 'awaiting-input' in result.stdout
