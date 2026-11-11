"""The read-redirect guards are gone; the destructive guards are not.

`cat`, `head`, `tail`, `grep`, `find` and `echo >` used to be denied with a
suggestion to use the harness's own read/search/write tool. Agents mostly did
the same read another way in the shell, and Claude Code no longer ships the
`Grep`/`Glob` tools those messages named. The guards were removed for every
harness. The destructive guards must keep denying on every hook platform.
"""

import re

import pytest

from autorun import plugins
from autorun.config import CONFIG
from autorun.core import EventContext, ThreadSafeDB, format_suggestion
from autorun.platforms import PLATFORMS, hook_platforms

HOOK_PLATFORMS = [p.name for p in hook_platforms()]

READ_COMMANDS = [
    "cat f",
    "head f",
    "tail f",
    "grep x f",
    "find . -name x",
    "echo x > f",
]

DESTRUCTIVE_COMMANDS = [
    "rm f",
    "git reset --hard",
    "sed -i s/a/b/ f",
    "git push",
]


def _decision(cli_type: str, command: str) -> str:
    ctx = EventContext(
        session_id=f"test-read-guards-{cli_type}",
        event="PreToolUse",
        tool_name=PLATFORMS[cli_type].tool_names["bash"],
        tool_input={"command": command},
        cli_type=cli_type,
        store=ThreadSafeDB(),
    )
    result = plugins.check_blocked_commands(ctx)
    if not result:
        return "allow"
    # Every hook platform carries "deny" either in hookSpecificOutput, at the
    # root as permissionDecision, or at the root as decision (root-only schema).
    output = result.get("hookSpecificOutput", {})
    return output.get("permissionDecision") or result.get("permissionDecision") or result.get("decision") or "allow"


@pytest.mark.parametrize("cli_type", HOOK_PLATFORMS)
@pytest.mark.parametrize("command", READ_COMMANDS)
def test_read_commands_are_allowed_on_every_hook_platform(cli_type, command):
    assert _decision(cli_type, command) != "deny"


@pytest.mark.parametrize("cli_type", HOOK_PLATFORMS)
@pytest.mark.parametrize("command", DESTRUCTIVE_COMMANDS)
def test_destructive_commands_are_still_denied_on_every_hook_platform(cli_type, command):
    assert _decision(cli_type, command) == "deny"


POLICY_MESSAGES = {
    "policies.SEARCH": lambda: CONFIG["policies"]["SEARCH"][1],
    "policy_blocked.SEARCH": lambda: CONFIG["policy_blocked"]["SEARCH"],
    "policy_blocked.JUSTIFY": lambda: CONFIG["policy_blocked"]["JUSTIFY"],
}


@pytest.mark.parametrize("cli_type", sorted(PLATFORMS))
@pytest.mark.parametrize("message", sorted(POLICY_MESSAGES))
def test_search_policy_messages_name_no_missing_tool(cli_type, message):
    rendered = format_suggestion(POLICY_MESSAGES[message](), cli_type)
    assert not re.search(r"\{[a-z_]+\}", rendered), rendered
    # Claude Code has no Grep/Glob by default, Pi's grep/find tools are off by
    # default and ForgeCode has no tool names, so the messages name none.
    for name in sorted({p.tool_names.get(k, "") for p in PLATFORMS.values() for k in ("grep", "glob")} - {""}):
        assert not re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", rendered), (name, rendered)
