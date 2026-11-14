"""Shell syntax unsupported by bashlex must not bypass command guards."""

import pytest

from autorun import core, plugins  # noqa: F401 — register the dispatch handlers
from autorun import command_detection


@pytest.mark.parametrize("command", [
    "for ((i=0;i<3;i++)); do rm -rf x; done",
    "for ((i=0;i<3;i++)); do sed -i s/a/b/ f; done",
    "for ((i=0;i<3;i++)); do git reset --hard; done",
    "for ((i=0;i<3;i++)); do find . -delete; done",
    "case $x in a) rm -rf x;; esac",
    "case $x in a) find . -delete;; esac",
    "if [[ -d x ]]; then find . -delete; fi",
    "if [[ -d x ]]; then rm -rf x; fi",
    "coproc find . -delete",
    "coproc rm -rf x",
    "[[ -d x ]] && find . -delete",
    "if true; then find . -delete; fi",
    "{ find . -delete; }",
    "( find . -delete )",
    "! find . -delete",
    "time find . -delete",
    "while read x; do find . -delete; done < f",
    "echo $((1+2)); find . -delete",
    "function f { find . -delete; }; f",
])
def test_dispatch_denies_dangerous_shell_commands(command):
    assert _decision(command) == "deny"


@pytest.mark.parametrize("command", [
    "for ((i=0;i<3;i++)); do echo $i; done",
    "case $x in a) echo hi;; esac",
    "if [[ -d x ]]; then ls; fi",
    "echo 'case x in a) rm -rf x;; esac'",
    "for ((i=0;i<3;i++)); do find . -name '*.py'; done",
    "for ((i=0;i<3;i++)); do echo 'case x in a) rm -rf x;; esac'; done",
    'for ((i=0;i<3;i++)); do echo "then find . -delete; rm -rf x"; done',
])
def test_dispatch_allows_safe_shell_commands(command):
    assert _decision(command) != "deny"


@pytest.mark.parametrize("prefix", [
    "do", "then", "else", "elif", "if", "while", "until", "{", "(",
    "!", "time", "time -p", "coproc", "function f {", "function f() {",
    "case $x in a)", "case 'a b' in 'a b')", "a)", "(a)",
    "then { ( ! time coproc",
])
def test_fallback_recovers_commands_in_both_apis(prefix, monkeypatch):
    monkeypatch.setattr(command_detection, "BASHLEX_AVAILABLE", False)
    command_detection.extract_commands.cache_clear()
    command_detection._extract_cached.cache_clear()
    command = f"{prefix} find . -delete"
    try:
        assert command_detection.extract_commands(command)[0] == {"find"}
        assert list(command_detection.shell_segments(command))[0][1] == ["find", ".", "-delete"]
    finally:
        command_detection.extract_commands.cache_clear()
        command_detection._extract_cached.cache_clear()


def _decision(command):
    ctx = core.EventContext(
        session_id="test-unparsed-shell",
        event="PreToolUse",
        tool_name="Bash",
        tool_input={"command": command},
        cli_type="claude",
        store=core.ThreadSafeDB(),
    )
    result = core.app.dispatch(ctx)
    return (result or {}).get("hookSpecificOutput", {}).get("permissionDecision")


@pytest.mark.parametrize("command", [
    "for ((i=0;i<3;i++)); do echo $(rm -rf x); done",
    "for ((i=0;i<3;i++)); do y=$(rm -rf x); done",
    "for ((i=0;i<3;i++)); do echo `rm -rf x`; done",
    'case $x in a) echo "$(git reset --hard)";; esac',
    "if [[ -d x ]]; then z=$(find . -delete); fi",
    'for ((i=0;i<3;i++)); do echo "$(echo $(rm -rf x))"; done',
])
def test_dispatch_denies_substitutions_in_unparsed_shell(command):
    assert _decision(command) == "deny"


@pytest.mark.parametrize("command", [
    "for ((i=0;i<3;i++)); do echo '$(rm -rf x)'; done",
    "for ((i=0;i<3;i++)); do echo $((i+1)); done",
    'for ((i=0;i<3;i++)); do echo "$(date)"; done',
    r'for ((i=0;i<3;i++)); do echo \$(rm -rf x); done',
])
def test_dispatch_allows_data_and_safe_substitutions(command):
    assert _decision(command) != "deny"


@pytest.mark.parametrize("body,expected", [
    ('echo "$(echo $(find . -delete))"', ["echo $(find . -delete)"]),
    ("echo '$(find . -delete)'", []),
    ('echo "\'$(find . -delete)\'"', ["find . -delete"]),
    ("echo $((1+2))", []),
    ("echo $((1+$(find . -delete)))", ["find . -delete"]),
    ('echo "$(echo \')\'; find . -delete)"', ["echo ')'; find . -delete"]),
    ('echo `find . -delete`', ["find . -delete"]),
])
def test_fallback_substitution_quote_and_parenthesis_boundaries(body, expected):
    assert list(command_detection._substitution_scripts(body)) == expected


@pytest.mark.parametrize("depth", range(1, 6))
def test_fallback_substitutions_obey_existing_depth_limit(depth, monkeypatch):
    script = "find . -delete"
    for _ in range(depth):
        script = f"echo $({script})"
    tokens = [tokens for _, tokens, _ in command_detection._fallback_segments(script)]
    assert (["find", ".", "-delete"] in tokens) == (
        depth <= command_detection._MAX_RECURSION_DEPTH
    )
    monkeypatch.setattr(command_detection, "BASHLEX_AVAILABLE", False)
    command_detection.extract_commands.cache_clear()
    command_detection._extract_cached.cache_clear()
    try:
        assert _decision(script) == "deny"
        assert command_detection._extract_cached(script).inspection_incomplete == (
            depth > command_detection._MAX_RECURSION_DEPTH
        )
    finally:
        command_detection.extract_commands.cache_clear()
        command_detection._extract_cached.cache_clear()
