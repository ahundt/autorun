#!/usr/bin/env python3
"""Settings resolve CLI parameter > environment > config file > default.

Three tiers existed. The file tier did not: three per-feature config files had
three separate loaders (`plan-export.config.json`, `plan-notify.config.json`,
`task-lifecycle.config.json`) and none of them backed the CONFIG dict, so a
user could tune those three features from a file and nothing else.

The file is overlaid onto CONFIG at import rather than consulted at each of the
fifty-odd `CONFIG.get` call sites. That is what makes the tier arrive
everywhere at once instead of wherever someone remembered to add it, and it is
why an absent file changes nothing: with no file, CONFIG holds exactly the
values its source declares.

Environment variables still win, because the resolvers that read them consult
the environment before CONFIG. That ordering is the point of the chain: a
setting exported for one session must not be overridden by a file written for
the machine.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from autorun import config as config_module

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PLUGIN_ROOT / "src"

# The probe puts SRC_DIR first on sys.path instead of relying on the working
# directory. `python -c` puts the *caller's* cwd at sys.path[0], and CI runs
# pytest from PLUGIN_ROOT, which holds autorun.py -- the bootstrap launcher.
# A module shadows a package of the same name, so an inherited-cwd import
# resolves to the launcher, fails on `autorun.python_check`, and exits 1 with
# its diagnostic on stdout. Keep the explicit path; do not "simplify" this to a
# bare `from autorun...`, and pass every process's returncode/stdout/stderr into
# the assertion so the next failure names itself.
_IMPORT_PROBE = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, sys.argv[1])
    from autorun.config import CONFIG
    print(CONFIG["log_file_backup_count"])
    """
)


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTORUN_HOME", str(tmp_path))
    return tmp_path


def _write(config_home, payload):
    path = config_home / config_module.USER_CONFIG_FILENAME
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_an_absent_file_changes_nothing(config_home):
    """The common case: no file, no difference.

    This is what makes the tier safe to add to a release candidate.
    """
    defaults = config_module.default_config()
    assert config_module.apply_user_config(dict(defaults)) == defaults


def test_a_file_value_overrides_the_default(config_home):
    _write(config_home, {"log_file_backup_count": 9})
    merged = config_module.apply_user_config(dict(config_module.default_config()))
    assert merged["log_file_backup_count"] == 9


def test_an_unknown_key_is_ignored_rather_than_accepted(config_home):
    """A typo must not silently create a setting nothing reads.

    Accepting it would make `autorun --status` report a value that has no
    effect, which is worse than declining it.
    """
    _write(config_home, {"log_file_backup_kount": 9, "log_file_backup_count": 4})
    merged = config_module.apply_user_config(dict(config_module.default_config()))
    assert "log_file_backup_kount" not in merged
    assert merged["log_file_backup_count"] == 4


def test_a_wrong_type_is_declined(config_home):
    """A string where an int belongs must not reach arithmetic later."""
    _write(config_home, {"log_file_max_bytes": "quite large"})
    merged = config_module.apply_user_config(dict(config_module.default_config()))
    assert merged["log_file_max_bytes"] == config_module.default_config()["log_file_max_bytes"]


@pytest.mark.parametrize("content", ["{not json", "[]", "null", '"text"'])
def test_an_unreadable_file_leaves_the_defaults_alone(config_home, content):
    """A broken config file must not stop autorun starting.

    These settings gate command blocking and file policies. Refusing to load is
    a safer failure than refusing to run.
    """
    (config_home / config_module.USER_CONFIG_FILENAME).write_text(content, encoding="utf-8")
    assert config_module.apply_user_config(dict(config_module.default_config())) == (
        config_module.default_config()
    )


def test_the_environment_still_outranks_the_file(config_home, monkeypatch):
    """The ordering that gives the chain its meaning.

    A file is written for a machine; an environment variable is set for one
    session, and the narrower scope wins.
    """
    flag = "AUTORUN_BUG_CLAUDE_CODE_TASK_TOOLS_GATED_OFF_BUG_80305_WORKAROUND_ENABLED"
    monkeypatch.setitem(config_module.CONFIG, flag, "never")
    monkeypatch.setenv(flag, "always")

    assert config_module.workaround_applies(flag, affected=False) is True


def test_the_live_config_reflects_the_file_at_import(config_home):
    """Wired, not merely available -- the entry point has to apply it.

    A loader nothing calls is the failure this project has hit before: the
    check exists, the caller never passes it, and every unit test still passes.

    Runs in a subprocess rather than reloading the module. `importlib.reload`
    rebinds `config.CONFIG` to a new dict while every module that already did
    `from .config import CONFIG` keeps the old one, so the reload silently
    splits the process into two configurations and breaks later tests -- which
    is exactly what it did when this test first used it.
    """
    _write(config_home, {"log_file_backup_count": 7})
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE, str(SRC_DIR)],
        capture_output=True, text=True, timeout=120,
    )
    detail = f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}"
    assert result.returncode == 0, detail
    assert result.stdout.strip() == "7", f"the file tier is not applied at import: {detail}"
