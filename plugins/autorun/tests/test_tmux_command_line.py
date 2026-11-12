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


# ─── End to end, on a private tmux server ──────────────────────────────────

# A stand-in for an AI coding harness. `exec -a <name>` gives it the
# executable name autorun's discovery matches (argv[0]); `tui` draws the input
# line inside a bordered box with the cursor hidden, as Claude Code does, and
# `stuck` is the same screen with Enter ignored, so the text stays typed.
_STUB = r'''
import os, sys, tty
name, mode = sys.argv[1], sys.argv[2]
out = sys.stdout
if mode == "plain":
    out.write(f"{name} ready\n> "); out.flush()
    for line in sys.stdin:
        out.write(f"got: {line.strip()}\n> "); out.flush()
    sys.exit(0)
fd = sys.stdin.fileno()
tty.setraw(fd)
buf, last = "", ""
def draw():
    out.write("\x1b[?25l\x1b[2J\x1b[H")
    out.write(f"{name} {mode} last: {last}\r\n")
    out.write("╭" + "─" * 40 + "╮\r\n")
    out.write("│ > " + buf.ljust(37) + "│\r\n")
    out.write("╰" + "─" * 40 + "╯\r\n")
    out.write(f"\x1b[3;{5 + len(buf)}H")  # back onto the input line, after the text
    out.flush()
draw()
while True:
    ch = os.read(fd, 1).decode(errors="replace")
    if ch in "\r\n":
        if mode == "tui":
            buf, last = "", buf
    elif ch == "\x03":
        break
    elif ch == "\x7f":
        buf = buf[:-1]
    else:
        buf += ch
    draw()
'''

# (executable name, harness autorun reports, how the pane runs it, screen)
_PANES = [
    ('claude', 'claude', 'own', 'tui'),
    ('codex', 'codex', 'own', 'plain'),
    ('pi', 'pi', 'own', 'plain'),
    ('prime-agent', 'prime', 'own', 'plain'),
    ('agy', 'antigravity', 'own', 'plain'),
    ('claude', 'claude', 'shell', 'plain'),
    ('codex', 'codex', 'shell', 'tui'),
    ('pi', 'pi', 'shell', 'plain'),
    ('prime-agent', 'prime', 'shell', 'plain'),
    ('agy', 'antigravity', 'shell', 'plain'),
    ('claude', 'claude', 'own', 'stuck'),
]


@pytest.mark.tmux
class TestTmuxCommandOnAPrivateServer:
    """`autorun tmux list` and `send`, run as a separate process the way a user
    runs them, against a tmux server only this test uses.

    The server is reached the way conftest.py reaches the suite's private
    one: AUTORUN_TMUX_BIN names a wrapper that adds `-S <socket>`. A server
    of its own keeps panes other tests leave behind out of the letters."""

    @pytest.fixture
    def server(self, request, tmp_path):
        import os
        import shlex
        import subprocess
        import sys
        import tempfile
        import time
        import uuid
        from pathlib import Path

        real_tmux = getattr(request.config, '_autorun_real_tmux_bin', None)
        assert real_tmux and os.environ.get('AUTORUN_TEST_TMUX_SOCKET'), \
            "tmux-marked tests need tmux and the suite's private server setup"
        # A socket path must stay under the 104-byte limit of sun_path.
        socket = Path(tempfile.gettempdir()) / f'arh9-{uuid.uuid4().hex[:8]}'
        wrapper = tmp_path / 'bin' / 'tmux'
        wrapper.parent.mkdir()
        wrapper.write_text(
            '#!/bin/sh\nunset TMUX\nexport SHELL=/bin/sh\n'
            f'exec {shlex.quote(real_tmux)} -f /dev/null -S {shlex.quote(str(socket))} "$@"\n')
        wrapper.chmod(0o755)
        stub = tmp_path / 'stub.py'
        stub.write_text(_STUB)

        env = {k: v for k, v in os.environ.items() if k not in ('TMUX', 'ANTHROPIC_API_KEY')}
        env['AUTORUN_TMUX_BIN'] = str(wrapper)
        env['PATH'] = f"{wrapper.parent}{os.pathsep}{env.get('PATH', '')}"

        def tmux(*args):
            return subprocess.run([str(wrapper), *args], capture_output=True, text=True,
                                  encoding='utf-8', env=env, timeout=10)

        for index, (exe, _, how, screen) in enumerate(_PANES):
            launch = f'exec -a {exe} {shlex.quote(sys.executable)} {shlex.quote(str(stub))} {exe} {screen}'
            if how == 'shell':  # a subshell execs the stub, so bash stays as its parent
                launch = f'({launch}); echo exited'
            argv = ['bash', '-c', launch]
            if index == 0:
                result = tmux('new-session', '-d', '-s', 'h9', '-x', '100', '-y', '20', *argv)
            else:
                result = tmux('new-window', '-d', '-t', f'h9:{index}', *argv)
            assert result.returncode == 0, result.stderr
        for index in range(len(_PANES)):  # each stub has drawn its first screen
            for _ in range(50):
                if tmux('capture-pane', '-p', '-t', f'h9:{index}').stdout.strip():
                    break
                time.sleep(0.1)
        try:
            yield tmux, env
        finally:
            tmux('kill-server')

    @staticmethod
    def _autorun(env, *args):
        import subprocess
        import sys

        return subprocess.run([sys.executable, '-m', 'autorun', 'tmux', *args], capture_output=True,
                              text=True, encoding='utf-8', env=env, timeout=120)

    def _screens(self, tmux):
        return [tmux('capture-pane', '-p', '-t', f'h9:{i}').stdout for i in range(len(_PANES))]

    def test_list_finds_every_harness_as_the_pane_process_and_as_a_shell_child(self, server):
        _, env = server

        result = self._autorun(env, 'list', '--json')

        assert result.returncode == 0, result.stdout + result.stderr
        found = [(s['tmux_target'], s['harness']) for s in json.loads(result.stdout)['sessions']]
        assert found == [(f'h9:{i}', harness) for i, (_, harness, _, _) in enumerate(_PANES)]

    def test_list_table_names_the_harness_of_each_pane(self, server):
        _, env = server

        result = self._autorun(env, 'list')

        assert result.returncode == 0, result.stdout + result.stderr
        rows = [line.split() for line in result.stdout.splitlines() if line[:2] in ('A ', 'B ', 'E ')]
        assert [(r[0], r[1], r[2]) for r in rows] == [
            ('A', 'h9:0', 'claude'), ('B', 'h9:1', 'codex'), ('E', 'h9:4', 'antigravity')]

    def test_send_to_a_types_into_the_first_pane_only(self, server):
        tmux, env = server
        before = self._screens(tmux)

        result = self._autorun(env, 'send', '--to', 'A', '--', 'hello')

        assert result.returncode == 0, result.stdout + result.stderr
        assert 'h9:0: session h9, window 0, pane 0' in result.stdout
        assert 'harness claude' in result.stdout and '[ok] h9:0' in result.stdout
        after = self._screens(tmux)
        # The TUI took the line: it shows it as the last submission and the
        # boxed input line is empty again.
        assert 'last: hello' in after[0] and '│ > hello' not in after[0], after[0]
        assert after[1:] == before[1:], 'a pane --to did not name changed'

    def test_text_left_in_a_tui_input_box_is_reported_and_exits_nonzero(self, server):
        tmux, env = server
        stuck = len(_PANES) - 1
        letter = chr(ord('A') + stuck)
        before = self._screens(tmux)

        result = self._autorun(env, 'send', '--to', letter, '--', 'stays')

        assert result.returncode == 1, result.stdout + result.stderr
        assert f'[FAIL] h9:{stuck}: typed, not submitted' in result.stdout
        after = self._screens(tmux)
        assert '│ > stays' in after[stuck], after[stuck]
        assert after[:stuck] == before[:stuck]

    def test_send_with_no_target_changes_no_pane(self, server):
        tmux, env = server
        before = self._screens(tmux)

        result = self._autorun(env, 'send', '--', 'hello')

        assert result.returncode == 1
        assert self._screens(tmux) == before
