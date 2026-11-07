"""Cases from a differential review of the guard against bash and gawk.

awk can run commands, write files and load code from its program text, and
the shell can hide where that text comes from (`#`, `{`, `$P`, `$'..'`). An
awk program is allowed only when it is plainly a field filter; anything the
guard cannot read as one is blocked.
"""

from __future__ import annotations

import subprocess
import uuid

import pytest

from autorun import plugins
from autorun.core import EventContext, ThreadSafeDB


def _decide(cmd: str, cli: str = "claude", cwd: str | None = None) -> str:
    kwargs = {"cwd": cwd} if cwd else {}
    ctx = EventContext(
        session_id=f"deep-{uuid.uuid4().hex[:10]}", event="PreToolUse", tool_name="Bash",
        tool_input={"command": cmd}, cli_type=cli, store=ThreadSafeDB(), **kwargs,
    )
    result = plugins.check_blocked_commands(ctx) or {}
    return result.get("hookSpecificOutput", {}).get("permissionDecision", "")


def _reason(cmd: str, cwd: str | None = None) -> str:
    kwargs = {"cwd": cwd} if cwd else {}
    ctx = EventContext(
        session_id=f"deep-{uuid.uuid4().hex[:10]}", event="PreToolUse", tool_name="Bash",
        tool_input={"command": cmd}, store=ThreadSafeDB(), **kwargs,
    )
    result = plugins.check_blocked_commands(ctx) or {}
    return result.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


DECOY = "ls | awk '{print $1}'"
PAYLOAD = "awk 'BEGIN{system(\"touch /tmp/pwn\")}'"


@pytest.mark.parametrize("cmd", [
    f"{DECOY}; echo $#; {PAYLOAD}",
    f"{DECOY}; echo a#b; {PAYLOAD}",
    f"{DECOY}; {{ {PAYLOAD}; }}",
    f"{DECOY}; if true; then {PAYLOAD}; fi",
    f"{DECOY}; for i in 1; do {PAYLOAD}; done",
    f"{DECOY}; ! {PAYLOAD}",
    f"{DECOY}; true;({PAYLOAD})",
    f"{DECOY}; f() {{ {PAYLOAD}; }}; f",
    "P='BEGIN{system(\"touch /tmp/pwn\")}'; ls | awk \"$P\"",
    "P='BEGIN{system(\"touch /tmp/pwn\")}'; ls | awk $P",
    "ls | awk $'BEGIN{\\x73ystem(\"touch /tmp/pwn\")}'",
    "ls | awk '{print /\"/; system(\"touch /tmp/pwn\")}'",
    "ls | awk '{print $0 ~ /;/ > \"/tmp/out\"}'",
    "ls | awk '@load \"rwarray\"; {print}'",
    "ls | awk '@include \"inplace\"; {print}'",
    "ls | awk 'BEGIN{f=\"sys\" \"tem\"; @f(\"touch /tmp/pwn\")}'",
    "ls | awk 'BEGIN{ARGV[1]=\"/etc/passwd\";ARGC=2}{print}'",
])
@pytest.mark.parametrize("cli", ["claude", "codex"])
def test_awk_that_could_run_write_or_read_is_blocked(cmd, cli):
    assert _decide(cmd, cli) == "deny", cmd


@pytest.mark.parametrize("cmd", [
    "ls -l | awk '{print $5}'",
    "ps aux | awk '{print $2, $11}'",
    "ls | awk -F: '{print $1}'",
    "ls | awk -F'|' '{print $2}'",
    "df -h | awk 'NR>1{print $5}'",
    "rg -c x . | awk -F: '{s+=$2} END {print s+0}'",
    "ls | awk '{print $1}' 2>/dev/null",
    "git log --oneline | awk '{print $1}' | head -5",
])
def test_a_plain_field_filter_on_a_pipe_is_allowed(cmd):
    assert _decide(cmd) != "deny", cmd


@pytest.mark.parametrize("cmd", [
    "cat f; echo $#; sudo cat /etc/shadow",
    "cat f; git log --grep=fix#1; doas cat /etc/shadow",
    "cat f; echo ${#arr[@]}; sudo -u postgres cat /etc/shadow",
    "cat f; echo $#; cat x > ~/.ssh/authorized_keys",
    "cat a 1<>~/.bashrc",
    "tail -n 5 log; tail -f log",
])
def test_codex_read_exemption_sees_past_a_hash_and_every_segment(cmd):
    assert _decide(cmd, "codex") == "deny", cmd


@pytest.mark.parametrize("cmd", [
    "sed -E -i 's/a/b/' f",
    "sed -n -i 's/a/b/' f",
    "sed 's/a/b/' -i f",
])
def test_sed_in_place_is_seen_wherever_the_option_sits(cmd):
    assert _decide(cmd) == "deny", cmd


def test_a_bare_home_operand_stays_expandable():
    assert "trash ~" in _reason("rm -rf ~")
    assert "trash '~'" not in _reason("rm -rf ~")


def test_a_git_stash_suggestion_never_executes_the_users_arguments(tmp_path):
    from test_integrations import _init_git_repo

    repo = _init_git_repo(tmp_path / "repo")
    (repo / "seed.txt").write_text("dirty\n")
    reason = _reason("git reset --hard 'x$(touch p)'", cwd=str(repo))
    suggestion = [line for line in reason.splitlines() if "Use instead" in line]
    assert suggestion, reason
    assert "$(" not in suggestion[0]
    bash = subprocess.run(["bash", "-n", "-c", suggestion[0].split("`")[1]], capture_output=True)
    assert bash.returncode == 0, bash.stderr
