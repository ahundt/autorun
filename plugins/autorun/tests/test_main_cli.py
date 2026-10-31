"""`autorun` CLI subcommands, exercised through ``__main__.main``.

Each test drives the console entry point and then checks the effect where the
rest of autorun reads it, never only the printed message.
"""

import json
import uuid

import pytest

from autorun import __main__ as entry
from autorun.platforms import PLATFORMS


def _clear_session_env(monkeypatch) -> None:
    monkeypatch.delenv("AUTORUN_SESSION_ID", raising=False)
    monkeypatch.delenv("AUTORUN_USE_IDENTITY", raising=False)
    for platform in PLATFORMS.values():
        for key in platform.standalone_session_env_vars:
            monkeypatch.delenv(key, raising=False)


def _write_decision(session: str, target, capsys) -> str:
    """Run a daemonless PreToolUse Write and return the gate's decision."""
    capsys.readouterr()
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": session,
        "tool_name": "Write",
        "tool_input": {"file_path": str(target), "content": "x\n"},
        "cwd": str(target.parent),
        "cli_type": "claude",
    }
    try:
        entry.run_direct(payload)
    except SystemExit as exit_:
        # output_hook_response exits 0 with nothing printed when no rule fired.
        assert exit_.code == 0
        return "allow"
    response = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    return response.get("hookSpecificOutput", {}).get("permissionDecision", "allow")


class TestArgumentHandling:
    def test_a_misspelled_install_flag_is_an_error_not_a_silent_default(self, monkeypatch, capsys):
        """``--codex-hook-sorce=plugin`` used to be swallowed by
        parse_known_args, so the install ran with the default source."""
        called = []
        monkeypatch.setattr("autorun.install.install_plugins", lambda *a, **k: called.append(1) or 0)

        with pytest.raises(SystemExit) as exit_:
            entry.main(["--install", "--codex-hook-sorce=plugin"])

        assert exit_.value.code == 2
        assert "unrecognized arguments: --codex-hook-sorce=plugin" in capsys.readouterr().err
        assert called == []

    def test_the_event_argument_antigravity_hooks_append_is_accepted(self, monkeypatch):
        """platforms._native_handler appends ``--event <name>`` to every
        Antigravity hook command, including ``autorun --cli ...``; rejecting
        unknown arguments must not reject that one."""
        handled = []
        monkeypatch.setattr(entry, "run_hook_handler", lambda: handled.append(1) or 0)
        monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)

        assert entry.main(["--cli", "gemini", "--event", "BeforeTool"]) == 0
        assert handled == [1]

    def test_update_method_comes_from_the_environment_when_no_flag_is_given(self, monkeypatch):
        """CLI > env > config > default: AUTORUN_UPDATE_METHOD was ignored
        because --update-method defaulted to "auto" in the parser."""
        import subprocess

        from autorun.installer import entrypoint

        calls = []
        monkeypatch.setenv("AUTORUN_UPDATE_METHOD", "gemini")
        monkeypatch.setattr(entrypoint, "version", lambda _name: "1.0.0")
        monkeypatch.setattr(entrypoint, "_latest_version", lambda _current: "1.0.1")
        monkeypatch.setattr(
            entrypoint, "_run", lambda argv, **_k: calls.append(tuple(argv)) or subprocess.CompletedProcess(argv, 0, "", "")
        )

        assert entry.main(["--update"]) == 0
        assert calls[0][:3] == ("gemini", "extensions", "update"), calls

        calls.clear()
        assert entry.main(["--update", "--update-method", "claude"]) == 0
        assert calls[0][:3] == ("claude", "plugin", "update"), "the flag outranks the environment"


class TestFilePolicyCommand:
    """`autorun file allow|justify|search|status` sets what the Write gate reads."""

    @pytest.fixture(autouse=True)
    def _direct_mode(self, monkeypatch):
        _clear_session_env(monkeypatch)
        monkeypatch.setenv("AUTORUN_CLI_TYPE", "claude")
        monkeypatch.setattr(entry, "USE_DAEMON", False)

    def test_file_search_from_the_session_env_denies_a_new_file_write(self, tmp_path, monkeypatch, capsys):
        """The command used to write ``__autofile_policy__<id>`` with
        ``strict-search``, a key and value no gate reads, and report success."""
        session = f"file-cli-{uuid.uuid4().hex[:8]}"
        monkeypatch.setenv("CLAUDE_SESSION_ID", session)
        target = tmp_path / "new_module.py"

        assert _write_decision(session, target, capsys) == "allow"
        assert entry.main(["file", "search"]) == 0
        assert "strict-search" in capsys.readouterr().out
        assert _write_decision(session, target, capsys) == "deny"

        assert entry.main(["file", "allow"]) == 0
        assert _write_decision(session, target, capsys) == "allow"

    def test_file_session_flag_selects_the_session_and_status_reads_it_back(self, tmp_path, capsys):
        session = f"file-cli-{uuid.uuid4().hex[:8]}"
        target = tmp_path / "another.py"

        assert entry.main(["file", "justify", "--session", session]) == 0
        assert _write_decision(session, target, capsys) == "deny"

        assert entry.main(["file", "status", "--session", session]) == 0
        assert "justify-create" in capsys.readouterr().out

    def test_file_command_without_a_session_fails(self, capsys):
        assert entry.main(["file", "search"]) == 1
        assert "--session" in capsys.readouterr().out

    def test_file_set_under_the_daemon_fails_loudly_with_the_slash_command(self, monkeypatch, capsys):
        """The daemon serves policy from its cache, so a state write from this
        process would be ignored by a live session. Say so; change nothing."""
        monkeypatch.setattr(entry, "USE_DAEMON", True)
        session = f"file-cli-{uuid.uuid4().hex[:8]}"

        assert entry.main(["file", "search", "--session", session]) == 1
        assert "/ar:f" in capsys.readouterr().out

        monkeypatch.setattr(entry, "USE_DAEMON", False)
        assert entry.main(["file", "status", "--session", session]) == 0
        assert "allow-all" in capsys.readouterr().out

    def test_file_global_fails_because_no_global_policy_exists(self, capsys):
        assert entry.main(["file", "search", "--global"]) == 1
        assert "/ar:f" in capsys.readouterr().out
