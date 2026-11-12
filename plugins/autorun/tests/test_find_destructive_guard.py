"""`find` actions that delete or overwrite files are denied; every other find is not.

The removed read-redirect guard for `find` was the only thing that looked at
`find . -delete` or `find . -fprint out`. Those destroy data the way `rm` and
a `>` redirect do, so they keep a guard of their own; listing files with find,
alone or in a pipe, stays allowed. `find -exec rm` and `xargs rm` are the `rm`
guard's, through the scripts find and xargs run.
"""

import json

import pytest

from autorun import plugins
from autorun.core import EventContext, ThreadSafeDB, format_commands_for_cli
from autorun.platforms import PLATFORMS, hook_platforms

HOOK_PLATFORMS = [p.name for p in hook_platforms()]

DESTRUCTIVE_FIND = [
    "find . -delete",
    "find . -name x -delete",
    "find . -type f -name '*.tmp' -delete",
    "find . -fprint out.txt",
    "find . -fprint0 out.txt",
    "find . -fprintf out.txt '%p\\n'",
    "find . -fls out.txt",
    "sudo find / -name x -delete",
    "env X=1 find . -delete",
    "sh -c 'find . -delete'",
    "ls && find . -delete",
    "find . -name x -delete | wc -l",
    "echo start; find . -fprint out.txt",
]

ALLOWED_FIND = [
    "find . -name x",
    "find . -type f -print",
    "find . -name x | xargs wc -l",
    "ls | find . -name x",
    "find . -name x -print0 | xargs -0 ls -l",
    "find . -exec grep -l delete {} +",
]

RM_GUARDED = [
    "find . -name x -exec rm {} \\;",
    "find . -name x -exec rm -f {} +",
    "find . -name x | xargs rm",
]


def _ctx(cli_type: str, store: ThreadSafeDB, **fields) -> EventContext:
    return EventContext(session_id=f"test-find-guard-{cli_type}", cli_type=cli_type, store=store, **fields)


def _check(cli_type: str, command: str, store: ThreadSafeDB | None = None):
    ctx = _ctx(
        cli_type,
        store or ThreadSafeDB(),
        event="PreToolUse",
        tool_name=PLATFORMS[cli_type].tool_names["bash"],
        tool_input={"command": command},
    )
    return plugins.check_blocked_commands(ctx)


def _decision(result) -> str:
    if not result:
        return "allow"
    output = result.get("hookSpecificOutput", {})
    return output.get("permissionDecision") or result.get("permissionDecision") or result.get("decision") or "allow"


@pytest.mark.parametrize("cli_type", HOOK_PLATFORMS)
@pytest.mark.parametrize("command", DESTRUCTIVE_FIND)
def test_find_that_deletes_or_overwrites_is_denied(cli_type, command):
    assert _decision(_check(cli_type, command)) == "deny"


@pytest.mark.parametrize("cli_type", HOOK_PLATFORMS)
@pytest.mark.parametrize("command", ALLOWED_FIND)
def test_find_that_only_lists_is_allowed(cli_type, command):
    assert _decision(_check(cli_type, command)) != "deny"


@pytest.mark.parametrize("cli_type", HOOK_PLATFORMS)
@pytest.mark.parametrize("command", RM_GUARDED)
def test_find_exec_rm_and_xargs_rm_stay_denied(cli_type, command):
    assert _decision(_check(cli_type, command)) == "deny"


@pytest.mark.parametrize("cli_type", HOOK_PLATFORMS)
def test_find_denial_names_the_action_and_the_safe_route(cli_type):
    text = json.dumps(_check(cli_type, "find . -name x -delete"))
    assert "-delete" in text
    assert "trash" in text
    assert json.dumps(format_commands_for_cli("/ar:ok find", cli_type))[1:-1] in text


@pytest.mark.parametrize("cli_type", HOOK_PLATFORMS)
def test_ok_find_allows_one_destructive_find(cli_type):
    store = ThreadSafeDB()
    command = "find . -name x -delete"
    assert _decision(_check(cli_type, command, store)) == "deny"
    plugins.app.dispatch(_ctx(cli_type, store, event="UserPromptSubmit", prompt="/ar:ok find", tool_name="", tool_input={}))
    assert _decision(_check(cli_type, command, store)) != "deny"
    # The grant is spent. A repeat of the same call within a second is the
    # parallel-hook grace (scoped_allow._PARALLEL_GRACE_SECONDS), so the next
    # call is a different find.
    assert _decision(_check(cli_type, "find . -name y -delete", store)) == "deny"
