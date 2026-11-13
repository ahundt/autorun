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

import tmux_screens
from autorun import tmux_tab_ai_session_status as status
from autorun.platforms import PLATFORMS
from autorun.__main__ import main

SESSIONS = [
    {'tmux_target': 'work:1.0', 'session_name': 'work', 'window_id': '1', 'pane': '0',
     'pane_id': '%3', 'path': '~/src/one', 'harness': 'claude', 'content': '❯ \n', 'prompt_type': 'input',
     'is_active': False, 'title': 'one'},
    {'tmux_target': 'work:2.1', 'session_name': 'work', 'window_id': '2', 'pane': '1',
     'pane_id': '%7', 'path': '~/src/two', 'harness': 'codex', 'content': '', 'prompt_type': 'unknown',
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
    fake.execute_tmux_command.return_value = {'returncode': 0, 'stdout': tmux_screens.READY}
    fake.send_literal.return_value = True
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
        ('work:1.0', 'claude'), ('work:2.1', 'codex')]


def test_list_json_prints_the_empty_discovery_json_when_no_pane_is_found(capsys):
    with patch.object(status, 'discover_claude_sessions', return_value=[]), \
            patch.object(status, 'get_tmux_utilities', return_value=MagicMock()):
        assert main(['tmux', 'list', '--json']) == 0

    assert json.loads(capsys.readouterr().out) == {'sessions': [], 'count': 0}


def test_list_table_names_each_session_with_its_harness(discovered, capsys):
    assert main(['tmux', 'list']) == 0

    out = capsys.readouterr().out
    assert 'work:1' in out and 'claude' in out and 'codex' in out
    assert 'autorun tmux send --to' in out


def test_list_types_nothing(discovered, tmux):
    main(['tmux', 'list'])

    tmux.send_keys.assert_not_called()
    tmux.send_literal.assert_not_called()


def test_send_reaches_the_one_sender_with_that_selection(discovered, capsys):
    with patch.object(status, 'execute_session_selections', return_value=[]) as sender:
        main(['tmux', 'send', '--to', '%3', '--', 'x'])

    selection, sessions = sender.call_args.args[:2]
    assert selection == 'A'
    assert sender.call_args.kwargs['command'] == 'x'
    assert [s['tmux_target'] for s in sessions] == ['work:1.0', 'work:2.1']


def test_send_types_into_the_named_pane_only_after_naming_it(discovered, tmux, capsys):
    printed_before_typing = []
    tmux.send_literal.side_effect = tmux.send_keys.side_effect = lambda keys, *a, **k: (
        printed_before_typing.append(capsys.readouterr().out) or True)

    assert main(['tmux', 'send', '--to', '%7', '--', 'hello', 'there']) == 0

    # Addressed by pane id, which tmux never reuses, not by session:window.pane.
    typed = [(name, args[:4]) for name, args, _ in tmux.method_calls
             if name in ('send_literal', 'send_keys')]
    assert typed == [('send_literal', ('hello there', '%7', None, None)),
                     ('send_keys', ('C-m', '%7', None, None))]
    before = printed_before_typing[0]
    assert 'session work' in before and 'window 2' in before and 'pane 1' in before
    assert '~/src/two' in before and 'codex' in before
    assert 'work:1.0' not in before, 'named a pane it is not going to type into'


@pytest.mark.parametrize('argv', [
    ['tmux', 'send', '--', 'x'],
    ['tmux', 'send', 'x'],
    ['tmux', 'send', '--to', '%3'],
    ['tmux', 'send', '--to', '%3', '--'],
])
def test_send_without_a_target_or_text_types_nothing_and_fails(argv, discovered, tmux, capsys):
    assert main(argv) != 0

    tmux.send_keys.assert_not_called()
    tmux.send_literal.assert_not_called()
    out = capsys.readouterr().out
    if '--to' not in argv:
        assert 'all' in out and 'awaiting-input' in out and '%' in out, out


def test_send_awaiting_input_skips_a_pane_whose_state_is_unknown(discovered, tmux, capsys):
    assert main(['tmux', 'send', '--to', 'awaiting-input', '--', 'go']) == 0

    assert {c.args[1:4] for c in tmux.send_keys.call_args_list} == {('%3', None, None)}


def test_send_accepts_awaiting_for_awaiting_input(discovered, tmux):
    assert main(['tmux', 'send', '--to', 'awaiting', '--', 'go']) == 0

    assert {c.args[1:4] for c in tmux.send_keys.call_args_list} == {('%3', None, None)}


def test_a_failed_send_exits_nonzero(discovered, tmux, capsys):
    tmux.send_keys.return_value = False

    assert main(['tmux', 'send', '--to', '%3', '--', 'x']) == 1
    assert '[FAIL]' in capsys.readouterr().out


def test_a_send_that_cannot_be_read_back_is_not_ok_and_exits_nonzero(discovered, tmux, capsys):
    with patch('autorun.tmux_utils._text_on_cursor_line', return_value=None):
        assert main(['tmux', 'send', '--to', '%3', '--', 'x']) == 1

    out = capsys.readouterr().out
    assert 'sent, not confirmed' in out and '[ok]' not in out, out


@pytest.mark.parametrize('to', ['Z', '%99', '%3,%99', 'work:1.0'])
def test_an_invalid_or_gone_target_types_nothing_and_exits_nonzero(to, discovered, tmux, capsys):
    assert main(['tmux', 'send', '--to', to, '--', 'x']) == 1

    tmux.send_keys.assert_not_called()
    tmux.send_literal.assert_not_called()
    assert '%3' in capsys.readouterr().out, 'the current listing, with its pane ids'


@pytest.mark.parametrize('to', ['A', 'A,B', 'b'])
def test_a_letter_is_refused_because_letters_shift_between_list_and_send(
        to, discovered, tmux, capsys):
    """send re-discovers the panes, so a letter can name another pane than
    the one the user saw in `list` when a window opened or closed between
    them. Only the pane id names the same pane both times."""
    assert main(['tmux', 'send', '--to', to, '--', 'x']) == 1

    tmux.send_keys.assert_not_called()
    tmux.send_literal.assert_not_called()
    out = capsys.readouterr().out
    assert 'pane id' in out and '%3' in out and '%7' in out, out


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
# executable name autorun's discovery matches (argv[0]); `tui` draws Claude
# Code's input line, `❯ ` between two rules, with the cursor hidden, and
# `stuck` is the same screen with Enter ignored, so the text stays typed.
# `screen=<file>` draws that file (a tmux_screens capture) and then shows
# every byte it is sent, so a test can see that nothing was typed into it.
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
if mode.startswith("screen="):
    with open(mode[len("screen="):], encoding="utf-8") as f:
        out.write(f.read().replace("\n", "\r\n")); out.flush()
    while True:
        ch = os.read(fd, 1)
        if ch == b"\x03":
            break
        out.write(f"\r\nRECEIVED {ch!r}"); out.flush()
    sys.exit(0)
buf, last = "", ""
def draw():
    out.write("\x1b[?25l\x1b[2J\x1b[H")
    out.write(f"{name} {mode} last: {last}\r\n")
    out.write("─" * 40 + "\r\n")
    out.write("❯ " + buf + "\r\n")
    out.write("─" * 40 + "\r\n")
    out.write(f"\x1b[3;{3 + len(buf)}H")  # back onto the input line, after the text
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


def _fake_harness_argv(exe, screen, how, python, stub, executable_dir):
    """Command that runs the interactive stub behind a real named executable."""
    import shlex

    command = [str(executable_dir / exe), str(python), str(stub), exe, screen]
    if how == 'shell':
        return ['bash', '-c', f'{shlex.join(command)}; echo exited']
    return command


def test_fake_harness_argv_uses_a_real_executable_name_under_a_path_with_spaces(tmp_path):
    """Process discovery reads the executable name, so the fixture must not
    rely on an interpreter preserving the synthetic argv[0] from `exec -a`."""
    from pathlib import Path

    executable_dir = tmp_path / 'fake harness binaries'
    argv = _fake_harness_argv(
        'claude', 'plain', 'own', Path('/python'), tmp_path / 'stub.py', executable_dir)

    assert Path(argv[0]).name == 'claude'
    assert executable_dir == Path(argv[0]).parent


def _cleanup_private_tmux(real_tmux, socket):
    """Stop the owned server and remove its socket even when no server started."""
    import os
    import subprocess
    import time

    try:
        subprocess.run(
            [real_tmux, '-S', str(socket), 'kill-server'],
            capture_output=True, text=True, timeout=10,
            env={key: value for key, value in os.environ.items() if key != 'TMUX'},
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    for _ in range(20):
        if not os.path.exists(socket):
            return
        time.sleep(0.05)
    try:
        os.unlink(socket)
    except FileNotFoundError:
        pass


def test_private_tmux_cleanup_stops_server_and_unlinks_socket_after_body_error(request):
    import os
    import subprocess
    import tempfile
    import uuid
    import psutil

    real_tmux = getattr(request.config, '_autorun_real_tmux_bin', None)
    if real_tmux is None or os.name != 'posix':
        pytest.skip('no private tmux server on this platform')
    socket = os.path.join(tempfile.gettempdir(), f'arh9-check-{uuid.uuid4().hex[:8]}')
    env = {key: value for key, value in os.environ.items() if key != 'TMUX'}
    command = [real_tmux, '-f', '/dev/null', '-S', socket]
    started = subprocess.run(
        [*command, 'new-session', '-d', '-s', 'cleanup-check'],
        capture_output=True, text=True, timeout=10, env=env,
    )
    assert started.returncode == 0, started.stderr
    pid_result = subprocess.run(
        [*command, 'display-message', '-p', '#{pid}'],
        capture_output=True, text=True, timeout=10, env=env,
    )
    pid = int(pid_result.stdout.strip())
    try:
        try:
            raise RuntimeError('test body failed')
        except RuntimeError:
            pass
    finally:
        _cleanup_private_tmux(real_tmux, socket)
    assert not os.path.exists(socket)
    assert not psutil.pid_exists(pid)

    open(socket, 'w').close()
    _cleanup_private_tmux(real_tmux, socket)
    assert not os.path.exists(socket)


@pytest.fixture
def private_tmux(request, tmp_path):
    """A tmux server only this test uses, and a way to launch stubs in it.

    The server is reached the way conftest.py reaches the suite's private
    one: AUTORUN_TMUX_BIN names a wrapper that adds `-S <socket>`. A server
    of its own keeps panes other tests leave behind out of the letters.
    Yields (tmux, env, stub_argv): tmux(*args) runs a tmux command there,
    env runs autorun against it, and stub_argv(exe, screen, how) is the argv
    of a stub harness, run as the pane's own process or a shell's child.
    """
    import os
    import shlex
    import shutil
    import subprocess
    import sys
    import tempfile
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

    # The launcher remains the pane process while its child runs the Python
    # terminal stub. Its hard-link name is therefore what ps(1) reports,
    # independent of whether the selected Python preserves `exec -a` argv[0].
    executable_dir = tmp_path / 'fake harness binaries'
    executable_dir.mkdir()
    launcher_source = executable_dir / 'launcher.c'
    launcher_source.write_text(r'''
#include <errno.h>
#include <sys/wait.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc < 3) return 64;
    pid_t child = fork();
    if (child < 0) return 71;
    if (child == 0) {
        execv(argv[1], &argv[1]);
        _exit(127);
    }
    int status;
    while (waitpid(child, &status, 0) < 0) {
        if (errno != EINTR) return 71;
    }
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    if (WIFSIGNALED(status)) return 128 + WTERMSIG(status);
    return 1;
}
''')
    launcher = executable_dir / 'launcher'
    compiler = shutil.which('cc')
    assert compiler, 'tmux tests need a C compiler for named fake harness executables'
    compiled = subprocess.run(
        [compiler, str(launcher_source), '-o', str(launcher)],
        capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    for exe in {row[0] for row in _PANES}:
        os.link(launcher, executable_dir / exe)

    env = {k: v for k, v in os.environ.items() if k not in ('TMUX', 'ANTHROPIC_API_KEY')}
    env['AUTORUN_TMUX_BIN'] = str(wrapper)
    env['PATH'] = f"{wrapper.parent}{os.pathsep}{env.get('PATH', '')}"

    def tmux(*args):
        return subprocess.run([str(wrapper), *args], capture_output=True, text=True,
                              encoding='utf-8', env=env, timeout=10)

    def stub_argv(exe, screen, how='own'):
        if screen in tmux_screens.NOT_READY:  # draw that capture
            drawn = tmp_path / f'{screen}.txt'
            drawn.write_text(tmux_screens.NOT_READY[screen], encoding='utf-8')
            screen = f'screen={drawn}'
        return _fake_harness_argv(exe, screen, how, sys.executable, stub, executable_dir)

    try:
        yield tmux, env, stub_argv
    finally:
        _cleanup_private_tmux(real_tmux, socket)


def _wait_for_screens(tmux, targets):
    """Each pane has drawn its first screen (a stub's banner, a shell's prompt)."""
    import time

    for target in targets:
        for _ in range(50):
            if tmux('capture-pane', '-p', '-t', target).stdout.strip():
                break
            time.sleep(0.1)


def _autorun_tmux(env, *args):
    import subprocess
    import sys

    return subprocess.run([sys.executable, '-m', 'autorun', 'tmux', *args], capture_output=True,
                          text=True, encoding='utf-8', env=env, timeout=120)


def _pane_ids(env):
    """{tmux_target: pane id} from `autorun tmux list --json`, the ids `send --to` takes."""
    result = _autorun_tmux(env, 'list', '--json')
    assert result.returncode == 0, result.stdout + result.stderr
    return {s['tmux_target']: s['pane_id'] for s in json.loads(result.stdout)['sessions']}


@pytest.mark.tmux
class TestTmuxCommandOnAPrivateServer:
    """`autorun tmux list` and `send`, run as a separate process the way a user
    runs them, against a tmux server only this test uses (private_tmux)."""

    @pytest.fixture
    def server(self, private_tmux):
        tmux, env, stub_argv = private_tmux
        for index, (exe, _, how, screen) in enumerate(_PANES):
            argv = stub_argv(exe, screen, how)
            if index == 0:
                result = tmux('new-session', '-d', '-s', 'h9', '-x', '100', '-y', '20', *argv)
            else:
                result = tmux('new-window', '-d', '-t', f'h9:{index}', *argv)
            assert result.returncode == 0, result.stderr
        _wait_for_screens(tmux, [f'h9:{i}' for i in range(len(_PANES))])
        return tmux, env

    @staticmethod
    def _autorun(env, *args):
        return _autorun_tmux(env, *args)

    def _screens(self, tmux):
        return [tmux('capture-pane', '-p', '-t', f'h9:{i}').stdout for i in range(len(_PANES))]

    def test_list_finds_every_harness_as_the_pane_process_and_as_a_shell_child(self, server):
        _, env = server

        result = self._autorun(env, 'list', '--json')

        assert result.returncode == 0, result.stdout + result.stderr
        found = [(s['tmux_target'], s['harness']) for s in json.loads(result.stdout)['sessions']]
        assert found == [(f'h9:{i}.0', harness) for i, (_, harness, _, _) in enumerate(_PANES)]

    def test_list_table_names_the_harness_of_each_pane(self, server):
        _, env = server

        result = self._autorun(env, 'list')

        assert result.returncode == 0, result.stdout + result.stderr
        rows = [line.split() for line in result.stdout.splitlines() if line[:2] in ('A ', 'B ', 'E ')]
        assert [(r[0], r[2], r[3]) for r in rows] == [
            ('A', 'h9:0.0', 'claude'), ('B', 'h9:1.0', 'codex'), ('E', 'h9:4.0', 'antigravity')]
        assert all(r[1].startswith('%') for r in rows), rows

    def test_send_to_the_first_pane_id_types_into_that_pane_only(self, server):
        tmux, env = server
        before = self._screens(tmux)

        result = self._autorun(env, 'send', '--to', _pane_ids(env)['h9:0.0'], '--', 'hello')

        assert result.returncode == 0, result.stdout + result.stderr
        assert 'h9:0.0: session h9, window 0, pane 0' in result.stdout
        assert 'harness claude' in result.stdout and '[ok] A h9:0.0' in result.stdout
        after = self._screens(tmux)
        # The TUI took the line: it shows it as the last submission and the
        # boxed input line is empty again.
        assert 'last: hello' in after[0] and '❯ hello' not in after[0], after[0]
        assert after[1:] == before[1:], 'a pane --to did not name changed'

    def test_text_left_in_a_tui_input_box_is_reported_and_exits_nonzero(self, server):
        tmux, env = server
        stuck = len(_PANES) - 1
        letter = chr(ord('A') + stuck)
        before = self._screens(tmux)

        result = self._autorun(env, 'send', '--to', _pane_ids(env)[f'h9:{stuck}.0'], '--', 'stays')

        assert result.returncode == 1, result.stdout + result.stderr
        assert f'[FAIL] {letter} h9:{stuck}.0: typed, not submitted' in result.stdout
        after = self._screens(tmux)
        assert '❯ stays' in after[stuck], after[stuck]
        assert after[:stuck] == before[:stuck]

    def test_send_with_no_target_changes_no_pane(self, server):
        tmux, env = server
        before = self._screens(tmux)

        result = self._autorun(env, 'send', '--', 'hello')

        assert result.returncode == 1
        assert self._screens(tmux) == before


@pytest.mark.tmux
class TestSplitWindowsOnAPrivateServer:
    """Discovery found a harness in any pane of a window, but send typed into
    the window's active pane, so text meant for an AI session could land in a
    shell or editor beside it. Each pane running a harness is now its own
    entry, targeted as session:window.pane, in tmux's order: sessions by name,
    windows by index, panes by index.

    sp:0  pane 0 claude (TUI, inactive) | pane 1 a shell (active)
    sp:1  pane 0 codex | pane 1 pi (both harnesses)
    sp:2  agy, unsplit
    """

    PANES = ['sp:0.0', 'sp:0.1', 'sp:1.0', 'sp:1.1', 'sp:2.0']

    @pytest.fixture
    def server(self, private_tmux):
        tmux, env, stub_argv = private_tmux
        steps = [
            ('new-session', '-d', '-s', 'sp', '-x', '120', '-y', '20', *stub_argv('claude', 'tui')),
            # split-window without -d makes the new shell pane the active one
            ('split-window', '-h', '-t', 'sp:0', 'sh'),
            ('new-window', '-d', '-t', 'sp:1', *stub_argv('codex', 'plain')),
            ('split-window', '-d', '-h', '-t', 'sp:1', *stub_argv('pi', 'plain', 'shell')),
            ('new-window', '-d', '-t', 'sp:2', *stub_argv('agy', 'plain')),
        ]
        for step in steps:
            result = tmux(*step)
            assert result.returncode == 0, (step, result.stderr)
        _wait_for_screens(tmux, self.PANES)
        active = tmux('display-message', '-p', '-t', 'sp:0', '#{pane_index}').stdout.strip()
        assert active == '1', 'the shell pane must be the active one'
        return tmux, env

    def _screens(self, tmux):
        return {t: tmux('capture-pane', '-p', '-t', t).stdout for t in self.PANES}

    def test_each_harness_pane_is_its_own_entry_and_the_shell_is_none(self, server):
        _, env = server

        result = _autorun_tmux(env, 'list', '--json')

        assert result.returncode == 0, result.stdout + result.stderr
        found = [(s['tmux_target'], s['pane'], s['harness'])
                 for s in json.loads(result.stdout)['sessions']]
        assert found == [('sp:0.0', '0', 'claude'), ('sp:1.0', '0', 'codex'),
                         ('sp:1.1', '1', 'pi'), ('sp:2.0', '0', 'antigravity')]

    def test_send_reaches_the_inactive_harness_pane_not_the_active_shell(self, server):
        tmux, env = server
        before = self._screens(tmux)

        result = _autorun_tmux(env, 'send', '--to', _pane_ids(env)['sp:0.0'], '--', 'hello')

        assert result.returncode == 0, result.stdout + result.stderr
        assert 'sp:0.0: session sp, window 0, pane 0' in result.stdout
        after = self._screens(tmux)
        assert 'last: hello' in after['sp:0.0'], after['sp:0.0']
        assert after['sp:0.1'] == before['sp:0.1'], 'text reached the shell pane'
        assert {t: after[t] for t in self.PANES[2:]} == {t: before[t] for t in self.PANES[2:]}

    def test_two_harness_panes_in_one_window_are_sent_to_separately(self, server):
        tmux, env = server
        before = self._screens(tmux)

        result = _autorun_tmux(env, 'send', '--to', _pane_ids(env)['sp:1.1'], '--', 'hi')

        assert result.returncode == 0, result.stdout + result.stderr
        assert 'sp:1.1: session sp, window 1, pane 1' in result.stdout and 'harness pi' in result.stdout
        after = self._screens(tmux)
        assert 'got: hi' in after['sp:1.1'], after['sp:1.1']
        assert {t: after[t] for t in self.PANES if t != 'sp:1.1'} == \
            {t: before[t] for t in self.PANES if t != 'sp:1.1'}

    def test_a_pane_id_still_names_the_listed_pane_after_a_window_closes(self, server):
        """Letters follow the current windows: with sp:0 gone, the codex pane
        listed as B is A. The pane id the listing showed still reaches it,
        and the letter the user saw is refused instead of typed elsewhere."""
        tmux, env = server
        codex = _pane_ids(env)['sp:1.0']
        assert tmux('kill-window', '-t', 'sp:0').returncode == 0
        before = {t: tmux('capture-pane', '-p', '-t', t).stdout for t in self.PANES[2:]}

        by_letter = _autorun_tmux(env, 'send', '--to', 'B', '--', 'wrong')
        by_id = _autorun_tmux(env, 'send', '--to', codex, '--', 'right')

        assert by_letter.returncode == 1 and 'pane id' in by_letter.stdout, by_letter.stdout
        assert by_id.returncode == 0, by_id.stdout + by_id.stderr
        after = {t: tmux('capture-pane', '-p', '-t', t).stdout for t in self.PANES[2:]}
        assert 'got: right' in after['sp:1.0'] and 'wrong' not in after['sp:1.0'], after['sp:1.0']
        assert {t: after[t] for t in self.PANES[3:]} == {t: before[t] for t in self.PANES[3:]}

    def test_a_harness_a_shell_runs_in_the_background_is_not_listed(self, private_tmux, tmp_path):
        """The pane's foreground is `cat`; a harness started with `&` there
        does not receive typed keys, so the pane is not an AI session."""
        tmux, env, _ = private_tmux
        import os
        import shlex
        import signal
        import time

        pid_file = tmp_path / 'background.pid'
        launch = f'set -m; exec -a codex sleep 60 & echo $! > {shlex.quote(str(pid_file))}; exec cat'
        assert tmux('new-session', '-d', '-s', 'bg', 'bash', '-c', launch).returncode == 0
        try:
            _wait_for_screens(tmux, ['bg:0'])
            time.sleep(0.5)  # let the background job exec

            result = _autorun_tmux(env, 'list', '--json')

            assert result.returncode == 0, result.stdout + result.stderr
            assert 'bg:0.0' not in result.stdout, result.stdout
        finally:
            # The job is in its own process group, so killing the server
            # leaves it running with the pane's terminal open; the next
            # tests' tmux servers then stalled for 10 seconds at a time.
            for _ in range(50):
                if pid_file.exists() and pid_file.read_text().strip():
                    os.kill(int(pid_file.read_text()), signal.SIGKILL)
                    break
                time.sleep(0.1)

    def test_an_unsplit_window_is_unchanged(self, server):
        tmux, env = server
        before = self._screens(tmux)

        result = _autorun_tmux(env, 'send', '--to', _pane_ids(env)['sp:2.0'], '--', 'yo')

        assert result.returncode == 0, result.stdout + result.stderr
        assert 'sp:2.0: session sp, window 2, pane 0' in result.stdout
        after = self._screens(tmux)
        assert 'got: yo' in after['sp:2.0'], after['sp:2.0']
        assert {t: after[t] for t in self.PANES[:-1]} == {t: before[t] for t in self.PANES[:-1]}


@pytest.mark.tmux
class TestNoSendTypesIntoADialogOnAPrivateServer:
    """`--to awaiting-input` treated every prompt autorun recognises as
    waiting, so it typed text and Enter into Claude Code's permission dialog
    (Enter accepts the highlighted "1. Yes"), plan approval and question
    menus; `--to all` and letters typed into them too, and into a pane
    mid-turn. Each pane here runs a stub named `claude` (the harness whose
    prompts autorun reads) drawing one tmux_screens capture, and shows any
    byte it receives; window 0 is Claude Code idle at its input prompt."""

    READABLE = sorted(n for n, p in PLATFORMS.items() if p.tmux_prompt_state_readable)
    NOT_READY = sorted(tmux_screens.NOT_READY)

    @pytest.fixture(params=READABLE)
    def server(self, request, private_tmux):
        tmux, env, stub_argv = private_tmux
        exe = PLATFORMS[request.param].binary
        for index, screen in enumerate(['tui', *self.NOT_READY]):
            argv = stub_argv(exe, screen)
            if index == 0:
                result = tmux('new-session', '-d', '-s', 'dg', '-x', '100', '-y', '20', *argv)
            else:
                result = tmux('new-window', '-d', '-t', f'dg:{index}', *argv)
            assert result.returncode == 0, result.stderr
        _wait_for_screens(tmux, [f'dg:{i}' for i in range(len(self.NOT_READY) + 1)])
        return tmux, env

    def _screens(self, tmux):
        return [tmux('capture-pane', '-p', '-t', f'dg:{i}').stdout
                for i in range(len(self.NOT_READY) + 1)]

    def _assert_untouched(self, before, after, result):
        for i, reason in enumerate(self.NOT_READY, start=1):
            assert 'RECEIVED' not in after[i] and after[i] == before[i], (reason, after[i])
            letter = chr(ord('A') + i)
            assert f'[refused] {letter} dg:{i}.0: not sent' in result.stdout, result.stdout
            assert f'({reason})' in result.stdout or f'special_prompt:{reason}' in result.stdout, \
                (reason, result.stdout)

    def test_all_types_only_into_the_pane_at_its_input_prompt(self, server):
        tmux, env = server
        before = self._screens(tmux)

        result = _autorun_tmux(env, 'send', '--to', 'all', '--', 'continue')

        assert result.returncode == 1, result.stdout + result.stderr
        after = self._screens(tmux)
        assert 'last: continue' in after[0], after[0]
        assert '[ok] A dg:0.0' in result.stdout
        self._assert_untouched(before, after, result)

    def test_pane_ids_naming_each_such_pane_type_into_none_of_them(self, server):
        tmux, env = server
        before = self._screens(tmux)
        ids = _pane_ids(env)
        targets = ','.join(ids[f'dg:{i}.0'] for i in range(1, len(self.NOT_READY) + 1))

        result = _autorun_tmux(env, 'send', '--to', targets, '--', 'continue')

        assert result.returncode == 1, result.stdout + result.stderr
        after = self._screens(tmux)
        assert after[0] == before[0]
        self._assert_untouched(before, after, result)

    def test_awaiting_input_types_into_no_dialog_and_no_pane_mid_turn(self, server):
        tmux, env = server
        before = self._screens(tmux)

        result = _autorun_tmux(env, 'send', '--to', 'awaiting-input', '--', 'continue')

        after = self._screens(tmux)
        assert 'last: continue' in after[0], after[0]
        for i, reason in enumerate(self.NOT_READY, start=1):
            assert 'RECEIVED' not in after[i] and after[i] == before[i], (reason, after[i])
        # Only the half-typed prompt is an input prompt, so only it is a
        # target to refuse; the exit status says not every target was sent.
        typing = chr(ord('A') + 1 + self.NOT_READY.index('user_typing'))
        assert f'[refused] {typing} ' in result.stdout and result.returncode == 1, result.stdout

    def test_list_shows_each_dialog_as_needing_the_person(self, server):
        _, env = server

        result = _autorun_tmux(env, 'list', '--json')

        assert result.returncode == 0, result.stdout + result.stderr
        found = {s['tmux_target']: (s['status'], s['awaiting'])
                 for s in json.loads(result.stdout)['sessions']}
        assert found['dg:0.0'] == ('idle', True)
        for i, reason in enumerate(self.NOT_READY, start=1):
            if reason in tmux_screens.DIALOGS:
                assert found[f'dg:{i}.0'] == ('dialog', False), (reason, found)

