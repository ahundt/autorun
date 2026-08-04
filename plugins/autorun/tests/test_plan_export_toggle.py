"""/ar:pe on|off|globalon|globaloff — project and global plan-export toggles.

The command follows the /ar:cache shape: one dispatcher, bare subcommand
tokens, no-argument form shows status. `on`/`off` pin the current project,
`globalon`/`globaloff` set the default for every project, and a project pin
beats the global default. Config persists in one JSON file; the project pins
live under its "projects" key, so no file is added to user repositories.

Every user-visible export message ends with the config hint so the reader
always sees how to turn the feature off, spelled for their harness
(Claude `/ar:pe`, Codex `ar:pe`).
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("AUTORUN_HOME", tempfile.mkdtemp(prefix="autorun-pe-home-"))
os.environ.setdefault(
    "AUTORUN_TEST_STATE_DIR", tempfile.mkdtemp(prefix="autorun-pe-state-")
)

from autorun import plan_export as pe_mod  # noqa: E402
from autorun.core import app  # noqa: E402
from autorun.plan_export import (  # noqa: E402
    PlanExportConfig,
    plan_export_command,
    with_plan_export_config_hint,
)


@pytest.fixture()
def config_path(monkeypatch, tmp_path):
    path = tmp_path / "autorun-home" / "plan-export.config.json"
    monkeypatch.setattr(pe_mod, "CONFIG_PATH", path)
    monkeypatch.setattr(
        pe_mod, "LEGACY_CONFIG_PATH", tmp_path / "claude-home" / "plan-export.config.json"
    )
    return path


@pytest.fixture()
def legacy_config_path(config_path):
    return pe_mod.LEGACY_CONFIG_PATH


@pytest.fixture()
def project(tmp_path):
    d = tmp_path / "project-a"
    d.mkdir()
    return d


@pytest.fixture()
def other_project(tmp_path):
    d = tmp_path / "project-b"
    d.mkdir()
    return d


class TestDispatchRegistration:
    def test_slash_pe_resolves(self):
        assert app._find_command("/ar:pe") is not None

    def test_codex_spelling_resolves(self):
        assert app._find_command("ar:pe", "codex") is not None

    def test_planexport_long_form_resolves(self):
        assert app._find_command("/ar:planexport") is not None


class TestProjectToggle:
    def test_off_pins_only_the_current_project(self, config_path, project, other_project):
        reply = plan_export_command("off", project, "claude")
        assert "off" in reply.lower()
        assert not PlanExportConfig.load(project).enabled
        assert PlanExportConfig.load(other_project).enabled

    def test_on_pin_beats_global_off(self, config_path, project, other_project):
        plan_export_command("globaloff", project, "claude")
        plan_export_command("on", project, "claude")
        assert PlanExportConfig.load(project).enabled
        assert not PlanExportConfig.load(other_project).enabled

    def test_pin_persists_in_the_projects_key(self, config_path, project):
        plan_export_command("off", project, "claude")
        data = json.loads(config_path.read_text())
        assert data["projects"][str(project)]["enabled"] is False
        assert data.get("enabled", True) is True


class TestGlobalToggle:
    def test_globaloff_disables_everywhere_without_a_pin(
        self, config_path, project, other_project
    ):
        reply = plan_export_command("globaloff", project, "claude")
        assert "off" in reply.lower()
        assert not PlanExportConfig.load(project).enabled
        assert not PlanExportConfig.load(other_project).enabled
        assert json.loads(config_path.read_text())["enabled"] is False

    def test_globalon_restores_the_default(self, config_path, project):
        plan_export_command("globaloff", project, "claude")
        plan_export_command("globalon", project, "claude")
        assert PlanExportConfig.load(project).enabled


class TestStatusAndUsage:
    def test_bare_command_shows_status_with_config_hint(self, config_path, project):
        reply = plan_export_command("", project, "claude")
        assert "Config: /ar:pe off|on|globaloff|globalon" in reply

    def test_status_names_the_deciding_layer(self, config_path, project):
        plan_export_command("off", project, "claude")
        reply = plan_export_command("", project, "claude")
        assert "project" in reply.lower()

    def test_codex_status_spells_the_command_without_a_slash(
        self, config_path, project
    ):
        reply = plan_export_command("", project, "codex")
        assert "Config: ar:pe off|on|globaloff|globalon" in reply
        assert "/ar:pe" not in reply

    def test_unknown_argument_returns_usage_and_changes_nothing(
        self, config_path, project
    ):
        reply = plan_export_command("sideways", project, "claude")
        for token in ("on", "off", "globalon", "globaloff"):
            assert token in reply
        assert not config_path.exists()


class TestSettingsSubcommands:
    """dir/pattern/rejected/reset replace the old pe-* command family."""

    def test_dir_sets_the_output_directory(self, config_path, project):
        plan_export_command("dir exported plans", project, "claude")
        assert PlanExportConfig.load(project).output_plan_dir == "exported plans"

    def test_pattern_sets_the_filename_pattern(self, config_path, project):
        plan_export_command("pattern {date}_{name}", project, "claude")
        assert PlanExportConfig.load(project).filename_pattern == "{date}_{name}"

    def test_fmt_is_accepted_for_pattern(self, config_path, project):
        plan_export_command("fmt {name}", project, "claude")
        assert PlanExportConfig.load(project).filename_pattern == "{name}"

    def test_rejected_without_a_value_toggles(self, config_path, project):
        plan_export_command("rejected", project, "claude")
        assert not PlanExportConfig.load(project).export_rejected
        plan_export_command("rejected", project, "claude")
        assert PlanExportConfig.load(project).export_rejected

    def test_rejected_accepts_explicit_on_and_off(self, config_path, project):
        plan_export_command("rejected off", project, "claude")
        assert not PlanExportConfig.load(project).export_rejected
        plan_export_command("rejected on", project, "claude")
        assert PlanExportConfig.load(project).export_rejected

    def test_rejected_dir_sets_the_rejected_directory(self, config_path, project):
        plan_export_command("rejected dir archive/rejected", project, "claude")
        assert (
            PlanExportConfig.load(project).output_rejected_plan_dir
            == "archive/rejected"
        )

    def test_reset_restores_defaults_and_clears_pins(self, config_path, project):
        plan_export_command("off", project, "claude")
        plan_export_command("dir elsewhere", project, "claude")
        plan_export_command("reset", project, "claude")
        config = PlanExportConfig.load(project)
        assert config.enabled
        assert config.output_plan_dir == "notes"

    def test_dir_without_a_value_returns_usage_and_writes_nothing(
        self, config_path, project
    ):
        reply = plan_export_command("dir", project, "claude")
        assert "dir <path>" in reply
        assert not config_path.exists()


class TestCommandSurface:
    """One document per spelling; the dash-name family stays deleted."""

    COMMANDS_DIR = Path(__file__).resolve().parents[1] / "commands"

    def test_only_two_plan_export_command_documents_exist(self):
        family = sorted(
            p.name
            for p in self.COMMANDS_DIR.glob("*.md")
            if p.name.startswith(("pe", "planexport"))
        )
        assert family == ["pe.md", "planexport.md"], family

    @pytest.mark.parametrize("name", ["pe.md", "planexport.md"])
    def test_description_shows_the_option_grammar(self, name):
        text = (self.COMMANDS_DIR / name).read_text(encoding="utf-8")
        frontmatter = text.split("---")[1]
        assert "description:" in frontmatter
        assert "[on|off|globalon|globaloff" in frontmatter
        assert "argument-hint:" in frontmatter


class TestConfigHintHelper:
    def test_claude_hint_uses_the_slash_spelling(self):
        msg = with_plan_export_config_hint("Plan exported to notes/x.md", "claude")
        assert msg.startswith("Plan exported to notes/x.md")
        assert "Config: /ar:pe off|on|globaloff|globalon" in msg

    def test_codex_hint_drops_the_slash(self):
        msg = with_plan_export_config_hint("Plan exported to notes/x.md", "codex")
        assert "Config: ar:pe off|on|globaloff|globalon" in msg
        assert "/ar:pe" not in msg


class TestHarnessNeutralLocation:
    """Settings serve every harness, so they live in autorun's config dir."""

    def test_import_time_paths_are_isolated_under_the_test_env(self):
        assert not str(pe_mod.CONFIG_PATH).startswith(str(Path.home() / ".claude"))
        assert not str(pe_mod.PLANS_DIR).startswith(str(Path.home() / ".claude"))

    def test_legacy_claude_file_is_read_when_no_new_file_exists(
        self, config_path, legacy_config_path, project
    ):
        legacy_config_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_config_path.write_text(json.dumps({"enabled": False}), encoding="utf-8")
        assert not PlanExportConfig.load(project).enabled

    def test_first_write_publishes_the_new_file_and_leaves_the_legacy_one(
        self, config_path, legacy_config_path, project
    ):
        legacy_config_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_config_path.write_text(json.dumps({"enabled": False}), encoding="utf-8")
        plan_export_command("globalon", project, "claude")
        assert config_path.exists()
        assert PlanExportConfig.load(project).enabled
        assert json.loads(legacy_config_path.read_text())["enabled"] is False

    def test_corrupt_new_file_does_not_resurrect_legacy_settings(
        self, config_path, legacy_config_path, project
    ):
        legacy_config_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_config_path.write_text(json.dumps({"enabled": False}), encoding="utf-8")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("{not json", encoding="utf-8")
        assert PlanExportConfig.load(project).enabled


class TestConfigLoadRobustness:
    def test_missing_file_defaults_to_enabled(self, config_path, project):
        assert PlanExportConfig.load(project).enabled

    def test_corrupt_file_defaults_to_enabled(self, config_path, project):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("{not json", encoding="utf-8")
        assert PlanExportConfig.load(project).enabled

    def test_load_without_a_project_keeps_the_global_view(self, config_path, project):
        plan_export_command("off", project, "claude")
        assert PlanExportConfig.load().enabled
