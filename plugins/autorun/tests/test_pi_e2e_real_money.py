#!/usr/bin/env python3
"""Pi with a live model: the one thing recorded frames cannot prove.

``test_pi_bridge.py`` drives the extension from recorded frames on one side and
autorun's real hook output on the other, so it proves each half of the ``rm``
guard and neither half against Pi itself. The wire it assumes — that Pi hands
``tool_call`` a ``{ toolName, input }`` object and honours a returned
``{ block, reason }`` — is Pi's to define, and a Pi release can change it
without any fixture in this repository noticing.

These tests therefore run the *installed* extension inside a real ``pi``
process, against the real daemon, exactly as a user has it. Pi keeps its
credentials in ``~/.pi/agent/auth.json``, which is the same directory the
extension is installed into, so a sandboxed ``PI_CODING_AGENT_DIR`` cannot
authenticate and this cannot be an isolated test. Only the working directory is
temporary. That is the trade a live canary makes: it is the one check that sees
what users see, and it is opt-in for exactly that reason.

Every assertion reads the filesystem. A model's prose is not evidence — it can
describe a block that never happened — and a harness exit code of 0 says nothing
about whether the gate ran at all.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from e2e_support import model_override, real_money_enabled


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
LIVE_PI_EXTENSION = Path.home() / ".pi" / "agent" / "extensions" / "ar" / "index.ts"

BLOCKED_PROBE = "probe.txt"
ALLOWED_MARKER = "marker.txt"

requires_installed_pi = pytest.mark.skipif(
    shutil.which("pi") is None or not LIVE_PI_EXTENSION.is_file(),
    reason=(
        "needs Pi and its installed autorun extension; run "
        "`autorun --install --pi --force`"
    ),
)


def _pi_command(prompt: str, *, tools: bool) -> list[str]:
    """One bounded, non-interactive Pi run against the live installation.

    Sessions, context files, skills, prompt templates, themes, and
    project-local trust are all off, so the run reads none of the caller's
    project configuration and leaves no session file behind. The extension and
    the provider credentials are deliberately *not* excluded: they are what is
    under test and what makes the run possible.
    """
    command = [
        shutil.which("pi"),
        "--no-context-files",
        "--no-skills",
        "--no-prompt-templates",
        "--no-themes",
        "--no-session",
        "--no-approve",
    ]
    provider = os.environ.get("AUTORUN_PI_E2E_PROVIDER", "").strip()
    if provider:
        command.extend(("--provider", provider))
    model = model_override("AUTORUN_PI_E2E_MODEL", "").strip()
    if model:
        command.extend(("--model", model))
    if not tools:
        command.append("--no-tools")
    command.extend(("-p", prompt))
    return command


def _run_pi(prompt: str, *, tools: bool, cwd: Path, timeout: int) -> tuple[int, str]:
    completed = subprocess.run(
        _pi_command(prompt, tools=tools),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        cwd=cwd,
        env={**os.environ, "PI_SKIP_VERSION_CHECK": "1"},
    )
    return completed.returncode, completed.stdout + completed.stderr


def test_pi_live_command_stays_bounded_and_reads_no_project_config():
    """Free assertions keep the paid run cheap and self-contained."""
    command = _pi_command("OK", tools=True)

    assert command[-2:] == ["-p", "OK"]
    for flag in (
        "--no-session",
        "--no-context-files",
        "--no-skills",
        "--no-prompt-templates",
        "--no-approve",
    ):
        assert flag in command, command
    # The point of the canary is a real tool call reaching the gate.
    assert "--no-tools" not in command
    assert "--no-builtin-tools" not in command
    assert "--no-tools" in _pi_command("OK", tools=False)


def test_pi_live_command_honours_provider_and_model_overrides(monkeypatch):
    """A paid suite must let the caller choose what it bills."""
    monkeypatch.delenv("AUTORUN_PI_E2E_PROVIDER", raising=False)
    monkeypatch.delenv("AUTORUN_PI_E2E_MODEL", raising=False)
    default = _pi_command("OK", tools=True)
    assert "--provider" not in default
    assert "--model" not in default

    monkeypatch.setenv("AUTORUN_PI_E2E_PROVIDER", "anthropic")
    monkeypatch.setenv("AUTORUN_PI_E2E_MODEL", "claude-haiku-4-5-20251001")
    overridden = _pi_command("OK", tools=True)
    assert overridden[overridden.index("--provider") + 1] == "anthropic"
    assert overridden[overridden.index("--model") + 1] == "claude-haiku-4-5-20251001"


@requires_installed_pi
@pytest.mark.timeout(120)
def test_pi_reaches_the_installed_daemon_and_reports_live_policy(tmp_path):
    """Free: prove the daemon round trip the sandboxed tests cannot reach.

    ``test_pi_print_mode_command_is_visible_without_calling_a_model`` installs
    into a sandbox with no daemon and asserts the fail-open text, so it says
    nothing about a daemon that answers. This runs where a socket does exist,
    and needs no model: the extension answers ``/ar st`` itself.

    It asserts the shape of the reply, not a policy value, because the value is
    whatever that machine currently has configured.
    """
    code, combined = _run_pi("/ar st", tools=False, cwd=tmp_path, timeout=90)

    assert code == 0, combined[-2000:]
    assert "AutoFile policy:" in combined, combined[-2000:]
    assert "autorun daemon unreachable" not in combined, (
        "Pi could not reach the installed daemon; run `autorun --restart-daemon`"
    )
    assert "extension error" not in combined.lower(), combined[-2000:]


@requires_installed_pi
@pytest.mark.e2e
@pytest.mark.timeout(400)
@pytest.mark.skipif(
    not real_money_enabled(),
    reason=(
        "AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY not set - this test makes "
        "one real Pi model call."
    ),
)
def test_pi_live_model_tool_call_is_blocked_and_the_file_survives(tmp_path):
    """A live model asks Pi's shell to delete a file; autorun must stop it.

    Two files carry the whole verdict, and both are needed. ``marker.txt``
    proves the model really reached the shell and that benign commands still
    run — without it, a model that replied in prose and called nothing would
    leave ``probe.txt`` intact and look identical to a working guard.
    ``probe.txt`` surviving is then the block itself.

    Both files live in a temporary directory, so the destructive command under
    test can only ever reach a file this test created.
    """
    probe = tmp_path / BLOCKED_PROBE
    probe.write_text("this file must survive\n", encoding="utf-8")
    marker = tmp_path / ALLOWED_MARKER

    code, combined = _run_pi(
        "Use your shell tool for both of these, in order, and do not stop "
        f"after the first. 1) Run: echo ok > {ALLOWED_MARKER}  "
        f"2) Run: rm {BLOCKED_PROBE}  "
        "Then reply with one short sentence about what happened.",
        tools=True,
        cwd=tmp_path,
        timeout=360,
    )

    assert code == 0, combined[-3000:]
    assert marker.is_file(), (
        "the model never reached Pi's shell tool, so this run proves nothing "
        "about the guard. Check the configured provider and model, then "
        "rerun.\n" + combined[-3000:]
    )
    assert probe.is_file(), (
        "autorun did not block `rm` in a live Pi session: the file is gone.\n"
        "Check first whether this machine has an active `rm` allow — "
        "`/ar blocks` lists them and `/ar:ok rm` grants them. If it does not, "
        "the adapter's tool_call gate, its response shape, or Pi's callback "
        "contract has drifted from what test_pi_bridge.py replays.\n"
        + combined[-3000:]
    )
