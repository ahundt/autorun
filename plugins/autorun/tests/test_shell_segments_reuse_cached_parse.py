"""Guard argv parity and a single shared parse for shell segments."""
from collections import Counter
from unittest.mock import patch

import pytest

from autorun import command_detection as cd
from test_guards_in_unparsed_shell import _decision

# Captured from the accepted worktree before either change. Heredoc bodies
# were incorrectly split into commands; only its header and final find remain.
HEREDOC = (
    "python3 - <<'EOF'\n"
    + "".join(f"body_{i:03d} = 'abcdefghij'\n" for i in range(200))
    + "EOF\nfind . -delete"
)
CASES = [
    ('cat f | head -2', [['cat', 'f'], ['head', '-2']]),
    ('echo hi && find . -delete', [['echo', 'hi'], ['find', '.', '-delete']]),
    ('(echo hi; git status)', [['echo', 'hi'], ['git', 'status']]),
    ("bash -c 'echo hi; find . -delete'",
     [['bash', '-c', 'echo hi; find . -delete'], ['echo', 'hi'], ['find', '.', '-delete']]),
    ('sudo find . -delete', [['sudo', 'find', '.', '-delete']]),
    ('X=1 find . -delete', [['X=1', 'find', '.', '-delete']]),
    ("echo 'a;b && c'", [['echo', 'a;b && c']]),
    (HEREDOC, [['python3', '-', '<<', 'EOF'], ['find', '.', '-delete']]),
    ("bash <<< 'echo hi; find . -delete'",
     [['bash', '<<<', 'echo hi; find . -delete'], ['echo', 'hi'], ['find', '.', '-delete']]),
    ('echo "$(date)"', [['echo', '$(date)'], ['date']]),
    ('echo `date`', [['echo', '`date`'], ['date']]),
    ('for x in a b; do echo "$x"; done', [['echo', '$x']]),
]


@pytest.mark.parametrize("command,expected", CASES, ids=[
    "pipe", "and", "subshell", "bash-c", "sudo", "assignment", "quoted",
    "heredoc", "here-string", "substitution", "backticks", "loop",
])
def test_guard_segment_tokens_match_baseline(command, expected):
    assert {tuple(tokens) for _, tokens in cd.shell_segments(command)} == {
        tuple(tokens) for tokens in expected
    }


def test_dispatch_parses_each_distinct_script_once():
    cd._extract_cached.cache_clear()
    cd.extract_commands.cache_clear()
    bashlex = cd._bashlex()[0]
    with patch.object(bashlex, "parse", wraps=bashlex.parse) as parse:
        assert _decision("sed -i 's/a/b/' f.txt && find . -name '*.py' -newer x -delete") == "deny"
        list(cd.shell_segments("sed -i 's/a/b/' f.txt && find . -name '*.py' -newer x -delete"))
    counts = Counter(call.args[0] for call in parse.call_args_list)
    # The original and extracted argv strings are distinct scripts. The sed
    # predicate short-circuits, so set iteration can omit the extracted find.
    assert counts and max(counts.values()) == 1, counts
    assert parse.call_count == len(counts), counts
    assert len(counts) in (2, 3), counts


def test_quoted_heredoc_body_is_data():
    command = CASES[7][0]
    segments = list(cd.shell_segments(command))
    assert any(tokens == ["find", ".", "-delete"] for _, tokens in segments)
    assert all("body_" not in text for text, _ in segments)
