"""Dispatch regressions for the independent shell guard review; execute no scripts."""
import json
import os
import shlex
import subprocess
import sys
import types
from statistics import median
from time import process_time
from unittest.mock import patch

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


GUARDED = ["rm -rf victim", "git reset --hard", "sed -i s/a/b/ victim", "find victim -delete"]


@pytest.mark.parametrize("command", GUARDED)
@pytest.mark.parametrize("form", ["two-heredocs", "string-doc", "doc-string", "doc-file", "safe-doc-file"])
def test_b1_last_stdin_redirect(command, form):
    script = {
        "two-heredocs": f"sh <<'A' <<'B'\necho safe\nA\n{command}\nB",
        "string-doc": f"sh <<< 'echo safe' <<'B'\n{command}\nB",
        "doc-string": f"sh <<'A' <<< {shlex.quote(command)}\necho safe\nA",
        "doc-file": f"sh <<'A' <victim\n{command}\nA",
        "safe-doc-file": f"sh <<'A' <victim\necho safe {shlex.quote(command)}\nA",
    }[form]
    assert (decision(script) == "deny") == (form not in {"doc-file", "safe-doc-file"})


@pytest.mark.parametrize("command", GUARDED)
@pytest.mark.parametrize("operator", ["<", ">"])
def test_b2_fallback_process_substitution(command, operator):
    assert decision(f"echo $((1)) {operator}({command})") == "deny"


@pytest.mark.parametrize("command", GUARDED)
@pytest.mark.parametrize("operator", ["<", ">"])
def test_b2_heredoc_process_substitution_is_literal(command, operator):
    assert decision(f"cat <<EOF\n{operator}({command})\nEOF") != "deny"


@pytest.mark.parametrize("command", GUARDED)
@pytest.mark.parametrize("option", ["-S ", "-S", "--split-string=", "--split-string ", "-i -S "])
def test_b3_env_split_string(command, option):
    assert decision("env " + option + shlex.quote(command)) == "deny"


@pytest.mark.parametrize("command", GUARDED)
def test_b3_split_command_tokens(command):
    script = "env -S " + shlex.quote(command)
    assert decision(script) == "deny"
    assert cd.command_tokens_for(script, command.split()[0]) == tuple(command.split())


@pytest.mark.parametrize("command", GUARDED)
def test_b1_overridden_literal_stdin_stays_data(command):
    assert decision(f"sh <<'A' <<'B'\n{command}\nA\necho safe\nB") != "deny"
    assert decision(f"sh <<'A' 3<<'B'\necho safe\nA\n{command}\nB") != "deny"


@pytest.mark.parametrize("source,expected", [
    (r"echo $'a\\b\'c'", ["echo", "a\\b'c"]),
    ("echo \"$'rm'\"", ["echo", "$'rm'"]),
    ("echo '$\"rm\"'", ["echo", '$"rm"']),
    (r"echo $'tab\there'", ["echo", "tab\there"]),
])
def test_b4_quote_decoder_preserves_literals(source, expected):
    assert decision(source) != "deny"
    assert next(cd.shell_segments(source))[1] == expected


@pytest.mark.parametrize("command", GUARDED)
@pytest.mark.parametrize("form", ["ansi-name", "locale-name", "ansi-argument"])
def test_b4_fallback_quotes(command, form, monkeypatch):
    name, _, args = command.partition(" ")
    script = {"ansi-name": "$'" + name + "' " + args,
              "locale-name": '$"' + name + '" ' + args,
              "ansi-argument": name + " " + " ".join("$'" + x + "'" for x in args.split())}[form]
    monkeypatch.setattr(cd, "BASHLEX_AVAILABLE", False)
    cd._extract_cached.cache_clear()
    cd.extract_commands.cache_clear()
    try:
        assert decision(script) == "deny"
    finally:
        cd._extract_cached.cache_clear()
        cd.extract_commands.cache_clear()


@pytest.mark.parametrize("command", GUARDED)
@pytest.mark.parametrize("quote", ["ansi", "locale", "hex", "octal", "unicode", "escaped"])
def test_b4_command_quoting(command, quote):
    name, args = command.split(" ", 1)
    quoted = {
        "ansi": "$'" + name + "'", "locale": '$"' + name + '"',
        "hex": "$'" + "".join(r"\x%02x" % ord(c) for c in name) + "'",
        "octal": "$'" + "".join(r"\%03o" % ord(c) for c in name) + "'",
        "unicode": "$'" + "".join(r"\u%04x" % ord(c) for c in name) + "'",
        "escaped": "".join("\\" + c for c in name),
    }[quote]
    assert decision(f"{quoted} {args}") == "deny"


@pytest.mark.parametrize("command", ["sed $'-i' s/a/b/ victim", "find victim $'-delete'", r"$'r\xZZm' -rf victim"])
def test_b4_argument_quoting_and_invalid_escape(command):
    assert decision(command) == "deny"


@pytest.mark.parametrize("command", GUARDED)
@pytest.mark.parametrize("form", ["single", "double", "unquoted"])
def test_b5_inner_heredoc_quote_provenance(command, form):
    script = {
        "single": f"sh -c 'cat <<\"EOF\"\n$({command})\nEOF'",
        "double": f'''sh -c "cat <<'EOF'\n\\$({command})\nEOF"''',
        "unquoted": "sh -c " + shlex.quote(f"cat <<EOF\n$({command})\nEOF"),
    }[form]
    assert (decision(script) == "deny") == (form == "unquoted")


@pytest.mark.parametrize("command", GUARDED)
def test_b5_printed_script_keeps_original_quotes(command):
    script = f'cat <<"EOF"\n$({command})\nEOF'
    assert decision("echo " + shlex.quote(script) + " | sh") != "deny"


@pytest.mark.parametrize("command", GUARDED)
def test_b1_nonstdin_heredoc_still_expands(command):
    assert decision(f"cat 3<<EOF\n$({command})\nEOF") == "deny"


@pytest.mark.parametrize("command,expected", [("FOO=1", set()), ("x=$(date)", {"date"})])
def test_b6_assignment_is_not_a_command(command, expected):
    assert decision(command) != "deny"
    extracted = cd._extract_cached(command)
    assert extracted.names == expected
    assert extracted.strings == expected
    assert extracted.all_potential == expected
    assert any(tokens[0].startswith(command[:2]) for _, tokens in cd.shell_segments(command))


@pytest.mark.parametrize("command", [
    'echo "use rm -rf carefully"', r"echo $'tab\there'", r"printf $'%s\n' hello",
    "env -S 'ls -la'", "env FOO=1 ls", "sh <<'A'\necho hello\nA",
    "cat <(sort a) <(sort b)", "echo $((1+2))",
])
def test_b_required_allows(command):
    assert decision(command) != "deny"


@pytest.mark.parametrize("command", [
    '"$PYTHON" script.py', '$CC -o x x.c', '${EDITOR:-vi} notes.txt',
    '"$(which python3)" -V', '$HOME/bin/tool --help', '"${VENV}/bin/pytest" -q',
    '$(brew --prefix)/bin/git status', 'for f in a b; do "$f" --version; done',
    'x=ls; $x -la', 'PY=python3; "$PY" -c "print(1)"',
    'bash script.sh < input.txt', 'sh ./run.sh < /dev/null',
    'echo "$(date)" && "$(pwd)/run" x', '`which ls` -la', '$@', '"$@"',
    'eval "$(ssh-agent -s)"', 'eval "$(direnv hook zsh)"',
    'echo {a,b,c}', 'cp file.{txt,bak}', 'mkdir -p src/{a,b}/tests', 'ls *.{py,md}',
    'git -C "$REPO" status', 'node "$SCRIPT"',
    'while read -r l; do echo "$l"; done < file', 'sort < in > out',
    'python3 - < script.py', 'xargs -n1 echo < list', '. ./env.sh',
    'source ~/.zshrc', 'source <(kubectl completion zsh)', 'parallel echo ::: a b',
    'flock /tmp/l -c "echo hi"', 'script -q -c "ls" /dev/null',
    'busybox ls', 'ssh host "ls"', 'cat <<EOF >&3\nhello $(date)\nEOF',
    'sh < file', 'sh <&3', 'sh <> file',
    "bash script.sh <<'EOF'\nrm -rf victim\nEOF",
])
def test_ordinary_commands_stay_allowed(command):
    assert decision(command) != "deny"


@pytest.mark.parametrize("command", GUARDED)
def test_generated_name_still_inspects_substitution(command):
    assert decision('"$(' + command + ')" x') == "deny"


NEXT_FORMS = {
    "substituted-name": lambda c: '"$(echo ' + c.split()[0] + ')" ' + c.partition(" ")[2],
    "parameter-name": lambda c: "${x:-" + c.split()[0] + "} " + c.partition(" ")[2],
    "brace-argv": lambda c: "{" + c.replace(" ", ",") + "}",
    "continued-name": lambda c: c[0] + "\\\n" + c[1:],
    "command--": lambda c: "command -- " + c,
    "builtin": lambda c: "builtin " + c,
    "exec": lambda c: "exec " + c,
    "escaped-name": lambda c: "".join("\\" + x for x in c.split()[0]) + " " + c.partition(" ")[2],
    "su-c": lambda c: "su -c " + shlex.quote(c),
    "sudo-sh-c": lambda c: "sudo sh -c " + shlex.quote(c),
    "xargs-sh-c": lambda c: "xargs sh -c " + shlex.quote(c),
    "watch": lambda c: "watch " + shlex.quote(c),
    "script-c": lambda c: "script -c " + shlex.quote(c),
    "flock-c": lambda c: "flock victim -c " + shlex.quote(c),
    "timeout-sh-c": lambda c: "timeout 5 sh -c " + shlex.quote(c),
    "nohup": lambda c: "nohup " + c,
    "ssh-host": lambda c: "ssh host " + shlex.quote(c),
    "parallel": lambda c: "parallel " + shlex.quote(c),
    "find-exec-sh-c": lambda c: "find victim -exec sh -c " + shlex.quote(c) + r" \;",
    "busybox-sh-c": lambda c: "busybox sh -c " + shlex.quote(c),
    "eval": lambda c: "eval " + shlex.quote(c),
    "sh-dev-stdin": lambda c: "sh /dev/stdin <<'A'\n" + c + "\nA",
    "sh-s": lambda c: "sh -s <<'A'\n" + c + "\nA",
    "sh-dash": lambda c: "sh - <<'A'\n" + c + "\nA",
    "dot-dev-stdin": lambda c: ". /dev/stdin <<'A'\n" + c + "\nA",
    "source-dev-stdin": lambda c: "source /dev/stdin # " + c,
    "bash-process-stdin": lambda c: "bash < <(echo " + c + ")",
}


@pytest.mark.parametrize("command", GUARDED)
@pytest.mark.parametrize("form", NEXT_FORMS)
def test_next_forms(command, form):
    result = decision(NEXT_FORMS[form](command))
    allowed = {"ssh-host", "substituted-name", "parameter-name", "source-dev-stdin", "bash-process-stdin"}
    assert (result == "deny") == (form not in allowed), (form, command, result)


def test_b2_fallback_segments_share_cached_parse():
    command = "echo $((1)) <(find victim -delete)"
    assert decision(command) == "deny"
    with patch.object(cd, "_fallback_segments", side_effect=AssertionError("reparsed fallback")):
        assert any(tokens == ["find", "victim", "-delete"] for _, tokens in cd.shell_segments(command))


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


# These strings are dispatched as tool-input data, never run by a shell.
REVIEW_GUARDED = [*DESTRUCTIVE[:4], "tmutil disable", "git checkout -- victim"]
REVIEW_WRAPPERS = ["FOO=1", "env", "sudo", "command", "nice"]
REVIEW_OPTIONS = ["sudo -u root", "env -i", "timeout 5"]


def assert_denied(commands):
    failures = [f"{command!r}: {result!r}" for command in commands
                if (result := decision(command)) != "deny"]
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("form", ["R1", "R2", "R3"])
def test_review_redirections(form, monkeypatch):
    monkeypatch.setattr(integrations, "_git_diff_quiet", lambda *args, **kwargs: True)
    if form == "R1":
        commands = [f"{wrapper} {redirect} {command}" for wrapper in REVIEW_WRAPPERS
                    for redirect in [">/dev/null", "2>/dev/null", "> /dev/null"]
                    for command in REVIEW_GUARDED]
    else:
        prefix = "{fd}>f" if form == "R2" else ">$(echo f)"
        commands = [f"{prefix} {command}" for command in REVIEW_GUARDED]
    assert_denied(commands)


def test_review_fallback_wrapper_redirections(monkeypatch):
    monkeypatch.setattr(cd, "BASHLEX_AVAILABLE", False)
    monkeypatch.setattr(integrations, "_git_diff_quiet", lambda *args, **kwargs: True)
    cd._extract_cached.cache_clear()
    cd.extract_commands.cache_clear()
    try:
        assert_denied(
            f"if true; then {wrapper} {redirect} {command}; fi"
            for wrapper in [*REVIEW_WRAPPERS, *REVIEW_OPTIONS]
            for redirect in [">/dev/null", "2>/dev/null", "> /dev/null"]
            for command in REVIEW_GUARDED
        )
    finally:
        cd._extract_cached.cache_clear()
        cd.extract_commands.cache_clear()


@pytest.mark.parametrize("wrapper", REVIEW_OPTIONS)
def test_review_redirections_after_wrapper_options(wrapper, monkeypatch):
    monkeypatch.setattr(integrations, "_git_diff_quiet", lambda *args, **kwargs: True)
    assert_denied(f"{wrapper} >/dev/null {command}" for command in REVIEW_GUARDED)


@pytest.mark.parametrize("context", ["shell", "substitution", "and", "pipe", "heredoc"])
def test_review_nested_redirections(context, monkeypatch):
    monkeypatch.setattr(integrations, "_git_diff_quiet", lambda *args, **kwargs: True)
    commands = []
    prefixes = [f"{wrapper} {redirect}" for wrapper in REVIEW_WRAPPERS
                for redirect in [">/dev/null", "2>/dev/null", "> /dev/null"]]
    for prefix in [*prefixes, "{fd}>f", ">$(echo f)"]:
        for command in REVIEW_GUARDED:
            script = f"{prefix} {command}"
            commands.append({
                "shell": "sh -c " + shlex.quote(script),
                "substitution": f"echo $({script})",
                "and": f"true && {script}",
                "pipe": f"echo x | {script}",
                "heredoc": f": <<EOF\n$({script})\nEOF",
            }[context])
    assert_denied(commands)


@pytest.mark.parametrize("prefix", ["<<<x", "< <(echo)"])
def test_review_input_redirections(prefix, monkeypatch):
    monkeypatch.setattr(integrations, "_git_diff_quiet", lambda *args, **kwargs: True)
    assert_denied(f"{prefix} {command}" for command in REVIEW_GUARDED)


@pytest.mark.parametrize("command", [
    "echo hi >/dev/null", ">/dev/null find . -name '*.py'",
    "env FOO=1 ls >/dev/null", "find . -name '>' -print", "echo '{fd}' >f",
    "ls 2>&1 | tee log", "cat > out.txt <<EOF\nplain text\nEOF",
    'git commit -m "$(cat <<\'EOF\n' + "; ".join(REVIEW_GUARDED) + '\nEOF\n)"',
    ": <<EOF\n\\$(find victim -delete)\nEOF",
    ': <<E""OF\n$(find victim -delete)\nEOF',
    "'{fd}'>f find victim -delete",
    "{fd} >f find victim -delete",
])
def test_review_safe_redirections(command):
    assert decision(command) != "deny"


@pytest.mark.parametrize("command", [
    "env FOO=1 ls >/dev/null", "find . -name '>' -print",
    "echo '{fd}' >f", "echo hi >/dev/null", "ls 2>&1 | tee log",
    "env FOO='>' ls >/dev/null", "env >'a b' find . -name '>' -print",
])
def test_review_fallback_safe_redirections(command, monkeypatch):
    monkeypatch.setattr(cd, "BASHLEX_AVAILABLE", False)
    cd._extract_cached.cache_clear()
    cd.extract_commands.cache_clear()
    try:
        assert decision(f"if true; then {command}; fi") != "deny"
    finally:
        cd._extract_cached.cache_clear()
        cd.extract_commands.cache_clear()


def test_review_repeated_segment_argv_is_split_once():
    cd._extract_cached.cache_clear()
    cd.extract_commands.cache_clear()
    with patch.object(cd, "_shlex_split_safe", wraps=cd._shlex_split_safe) as split:
        assert decision("echo same; echo same; env >/dev/null find victim -delete") == "deny"
    # The original script and an extracted command are distinct cached parses.
    assert [call.args[0] for call in split.call_args_list].count("echo same") <= 2


@pytest.mark.skipif(not os.environ.get("AUTORUN_TEST_GUARD_BENCHMARK"), reason="explicit timing run")
@pytest.mark.timeout(600)  # Opt-in paired measurements and 200-dispatch profiles.
def test_dispatch_benchmark(monkeypatch, capsys):
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
                source = subprocess.check_output(["git", "show", f"01fb2d42:{path}"], text=True)
                exec(compile(source, path, "exec"), module.__dict__)
                baseline.append(module)
        versions = {"before": tuple(baseline), "after": (cd, integrations)}

    def measure(command, modules, cold, profiler=None):
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
                parser.ParsedPattern.from_string.cache_clear()
            if profiler is not None:
                profiler.enable()
            cpu_start = process_time()
            dispatch(command)
            elapsed = (process_time() - cpu_start) * 1000
            if profiler is not None:
                profiler.disable()
            return elapsed

    timings = {}
    for name, command in commands.items():
        for cold in (True, False):
            for modules in versions.values():
                measure(command, modules, cold)  # grammar/import/state warmup
            samples = {version: [] for version in versions}
            for _ in range(50):
                for version in versions:  # ABAB in the same process.
                    samples[version].append(measure(command, versions[version], cold))
            for version in versions:
                timings[f"{version}_{name}_{'cold' if cold else 'warm'}"] = {
                    "bytes": len(command.encode()),
                    "median_cpu_ms": median(samples[version]),
                }
    with capsys.disabled():
        print("GUARD_DISPATCH_BENCHMARK=" + json.dumps(timings, sort_keys=True))
    if "before" in versions and "after" in versions:
        failures = []
        for name in commands:
            failed = False
            for state in ("cold", "warm"):
                before = timings[f"before_{name}_{state}"]["median_cpu_ms"]
                after = timings[f"after_{name}_{state}"]["median_cpu_ms"]
                if after > before * 1.10:
                    failures.append((name, state, before, after))
                    failed = True
            if failed:
                import cProfile
                import pstats
                profiler = cProfile.Profile(timer=process_time)
                for _ in range(200):
                    measure(commands[name], versions["after"], True, profiler)
                with capsys.disabled():
                    print("COLD_DISPATCH_PROFILE=" + name)
                    pstats.Stats(profiler).sort_stats("cumulative").print_stats(35)
        assert not failures, failures
