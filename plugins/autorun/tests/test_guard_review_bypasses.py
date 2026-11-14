"""Dispatch regressions for the independent shell guard review; execute no scripts."""
import json
import os
import shlex
import subprocess
import sys
import types
from pathlib import Path
from statistics import median
from time import perf_counter

import pytest

from autorun import command_detection as cd
from autorun import core, plugins  # noqa: F401 -- register dispatch handlers
from autorun import integrations


def dispatch(command):
    return core.app.dispatch(core.EventContext(
        session_id="test-guard-review", event="PreToolUse", tool_name="Bash",
        tool_input={"command": command}, cli_type="claude", store=core.ThreadSafeDB(),
    )) or {}


def decision(command):
    return dispatch(command).get("hookSpecificOutput", {}).get("permissionDecision")


REDIRECTS = [">/dev/null", "> /dev/null", "2>/dev/null", "</dev/null",
             "&>/dev/null", ">>/dev/null", "2>&1"]
DESTRUCTIVE = ["rm -rf victim", "git reset --hard", "sed -i s/a/b/ victim",
               "find victim -delete", "find victim -fprint out",
               "find victim -fprint0 out", "find victim -fprintf out %p",
               "find victim -fls out"]


@pytest.mark.parametrize("prefix", REDIRECTS)
@pytest.mark.parametrize("command", DESTRUCTIVE)
def test_g1_leading_redirections(prefix, command):
    assert decision(f"{prefix} {command}") == "deny"


@pytest.mark.parametrize("prefix", REDIRECTS)
def test_g1_fallback_leading_redirections(prefix, monkeypatch):
    monkeypatch.setattr(cd, "BASHLEX_AVAILABLE", False)
    cd._extract_cached.cache_clear()
    cd.extract_commands.cache_clear()
    try:
        assert decision(f"{prefix} find victim -delete") == "deny"
    finally:
        cd._extract_cached.cache_clear()
        cd.extract_commands.cache_clear()


@pytest.mark.parametrize("body", [f"$({s})" for s in DESTRUCTIVE[:4]] + ["`rm -rf victim`"])
def test_g2_expanding_heredoc(body):
    reader = "cat" if body.startswith("`") else ":"
    assert decision(f"{reader} <<EOF\n{body}\nEOF") == "deny"


@pytest.mark.parametrize("delimiter", ["'EOF'", '"EOF"', r"\EOF"])
@pytest.mark.parametrize("body", [f"$({s})" for s in DESTRUCTIVE[:4]] + ["`rm -rf victim`"])
def test_g2_literal_heredoc(delimiter, body):
    assert decision(f": <<{delimiter}\n{body}\nEOF") != "deny"


def nested(script, kind, depth):
    for delimiter in "DCBA"[:depth]:
        script = (f"sh <<'{delimiter}'\n{script}\n{delimiter}" if kind == "heredoc"
                  else "sh -c " + shlex.quote(script))
    return script


@pytest.mark.parametrize("kind", ["heredoc", "-c"])
def test_g3_uninspected_nesting(kind):
    command = nested("rm -rf victim", kind, 4)
    result = dispatch(command)
    assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny", (result, cd._extract_cached(command))
    assert cd._extract_cached(command).inspection_incomplete
    message = json.dumps(result).lower()
    assert f"shell nested more than {cd._MAX_RECURSION_DEPTH} levels deep" in message
    assert "run the inner command directly" in message


@pytest.mark.parametrize("kind", ["heredoc", "-c"])
def test_g3_harmless_within_limit(kind):
    assert decision(nested("echo hi", kind, cd._MAX_RECURSION_DEPTH)) != "deny"


@pytest.mark.parametrize("command", [
    "sed -e 's/a/b/;d' -i victim",
    "sed -e 's/a/b/&&d' -i victim",
    "tmutil setdestination 'backup;disk' && tmutil disable",
])
def test_g4_quoted_separator_before_mutation(command):
    assert decision(command) == "deny"


@pytest.mark.parametrize("verb", ["checkout", "restore"])
def test_g4_git_quoted_path_keeps_separator(verb, monkeypatch):
    monkeypatch.setattr(integrations, "_git_diff_quiet", lambda cwd, ref, file_path=None: file_path == "clean;dirty")
    assert decision(f"git {verb} -- 'clean;dirty'") == "deny"


def test_g4_tmutil_quoted_nonverb_is_not_a_mutation():
    assert decision("tmutil 'disable;not-a-verb'") != "deny"


@pytest.mark.parametrize("command", [
    "echo hi >/dev/null", ">/dev/null find . -name '*.py'",
    "cat <<EOF\nhello $(date)\nEOF", "sed -e 's/a/b/;d' f", "sed -n '1,5p;6q' f",
    'git commit -m "$(cat <<\'EOF\nrm -rf victim; git reset --hard; sed -i x f; find . -delete\nEOF\n)"',
])
def test_required_safe_commands(command):
    assert decision(command) != "deny"


@pytest.mark.skipif(not os.environ.get("AUTORUN_TEST_GUARD_BENCHMARK"), reason="explicit timing run")
def test_dispatch_benchmark(monkeypatch):
    commands = {
        "ordinary": "git status && ls -la | head -5",
        "sed_find": "sed -i 's/a/b/' f && find . -name '*.py' -newer x -delete",
        "quoted_5kb": "cat <<'EOF'\n" + "x" * (5120 - 16) + "\nEOF",
        "segments_75kb": "; ".join(["echo abcdefghijkl"] * 4000) + " " * 802,
    }
    versions = {os.environ["AUTORUN_TEST_GUARD_BENCHMARK"]: (cd, integrations)}
    if "paired" in versions:
        # Load the exact starting modules in memory, without changing checkout files.
        baseline = []
        with monkeypatch.context() as patch:
            for name in ("command_detection", "integrations"):
                module = types.ModuleType("autorun." + name)
                module.__package__ = "autorun"
                patch.setitem(sys.modules, module.__name__, module)
                path = f"plugins/autorun/src/autorun/{name}.py"
                source = subprocess.check_output(["git", "show", f"d888f4e8:{path}"], text=True)
                exec(compile(source, path, "exec"), module.__dict__)
                baseline.append(module)
        versions = {"before": tuple(baseline), "after": (cd, integrations)}

    def measure(command, modules, cold):
        parser, predicates = modules
        with monkeypatch.context() as patch:
            for module, names in (
                (parser, ("command_matches_pattern", "command_tokens_for", "extract_commands", "shell_command_from_tool_input")),
                (predicates, ("check_conditions", "check_when_predicate", "default_integrations", "invalidate_caches", "load_all_integrations")),
            ):
                for name in names:
                    patch.setattr(plugins, name, getattr(module, name))
            if cold:
                parser._extract_cached.cache_clear()
                parser.extract_commands.cache_clear()
            start = perf_counter()
            dispatch(command)
            return (perf_counter() - start) * 1000

    timings = {}
    for name, command in commands.items():
        for cold in (True, False):
            for modules in versions.values():
                measure(command, modules, cold)  # grammar/import/state warmup
            samples = {version: [] for version in versions}
            for i in range(20):
                order = list(versions) if i % 2 else list(reversed(versions))
                for version in order:
                    samples[version].append(measure(command, versions[version], cold))
            for version in versions:
                timings[f"{version}_{name}_{'cold' if cold else 'warm'}"] = {
                    "bytes": len(command.encode()), "median_ms": round(median(samples[version]), 4),
                }
    print("GUARD_DISPATCH_BENCHMARK=" + json.dumps(timings, sort_keys=True))
    Path(__file__).with_name("guard_benchmark_" + os.environ["AUTORUN_TEST_GUARD_BENCHMARK"] + ".json").write_text(
        json.dumps(timings, indent=2) + "\n"
    )
