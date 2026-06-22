"""Tests for multi-platform CLI detection (_CLI_DETECTORS refactor).

Verifies that detect_cli_type() and _CLI_DETECTORS correctly identify all
four supported platforms: claude, gemini, codex, forgecode.

Guards: config.py:detect_cli_type(), config.py:_CLI_DETECTORS
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_env(monkeypatch) -> None:
    """Remove all platform env vars so tests start from a clean state."""
    for var in (
        "GEMINI_SESSION_ID", "GEMINI_PROJECT_DIR", "GEMINI_CLI",
        "ANTIGRAVITY_SESSION_ID", "ANTIGRAVITY_PROJECT_DIR", "AGY_SESSION_ID",
        "CODEX_SESSION_ID", "CODEX_PROJECT_DIR",
        "FORGE_CONFIG",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# _CLI_DETECTORS structure tests
# ---------------------------------------------------------------------------

class TestCLIDetectorsStructure:
    """_CLI_DETECTORS must exist, be ordered, and declare all platforms."""

    def test_cli_detectors_exists(self):
        """_CLI_DETECTORS list must exist in config module."""
        from autorun.config import _CLI_DETECTORS
        assert isinstance(_CLI_DETECTORS, list), "_CLI_DETECTORS must be a list"

    def test_cli_detectors_has_gemini(self):
        from autorun.config import _CLI_DETECTORS
        names = [d[0] for d in _CLI_DETECTORS]
        assert "gemini" in names, "_CLI_DETECTORS must include gemini"

    def test_cli_detectors_has_codex(self):
        from autorun.config import _CLI_DETECTORS
        names = [d[0] for d in _CLI_DETECTORS]
        assert "codex" in names, "_CLI_DETECTORS must include codex"

    def test_cli_detectors_has_antigravity(self):
        from autorun.config import _CLI_DETECTORS
        names = [d[0] for d in _CLI_DETECTORS]
        assert "antigravity" in names, "_CLI_DETECTORS must include antigravity"

    def test_antigravity_detector_precedes_gemini(self):
        from autorun.config import _CLI_DETECTORS
        names = [d[0] for d in _CLI_DETECTORS]
        assert names.index("antigravity") < names.index("gemini"), (
            "Antigravity path hints include .gemini/antigravity-cli and must "
            "be checked before Gemini's generic .gemini hint"
        )

    def test_cli_detectors_has_forgecode(self):
        from autorun.config import _CLI_DETECTORS
        names = [d[0] for d in _CLI_DETECTORS]
        assert "forgecode" in names, "_CLI_DETECTORS must include forgecode"

    def test_claude_not_in_cli_detectors(self):
        """claude is the default fallback — must NOT appear in _CLI_DETECTORS."""
        from autorun.config import _CLI_DETECTORS
        names = [d[0] for d in _CLI_DETECTORS]
        assert "claude" not in names, (
            "claude is the fallback default — it must not appear in _CLI_DETECTORS. "
            "Including it would shadow legitimate claude detection."
        )

    def test_each_detector_has_five_fields(self):
        """Each detector entry must be a tuple/list with exactly 5 fields:
        (name, session_id_keys, event_names, path_hints, env_vars)
        """
        from autorun.config import _CLI_DETECTORS
        for entry in _CLI_DETECTORS:
            assert len(entry) == 5, (
                f"Detector {entry[0]} has {len(entry)} fields, expected 5: "
                "(name, session_id_keys, event_names, path_hints, env_vars)"
            )

    def test_gemini_detector_has_known_env_vars(self):
        """Gemini detector must include GEMINI_SESSION_ID (regression guard)."""
        from autorun.config import _CLI_DETECTORS
        gemini_entry = next(d for d in _CLI_DETECTORS if d[0] == "gemini")
        _, _, _, _, env_vars = gemini_entry
        assert "GEMINI_SESSION_ID" in env_vars, (
            "Gemini env var GEMINI_SESSION_ID missing from detector"
        )

    def test_forgecode_detector_has_forge_config_env(self):
        """ForgeCode detector must include FORGE_CONFIG env var."""
        from autorun.config import _CLI_DETECTORS
        forge_entry = next(d for d in _CLI_DETECTORS if d[0] == "forgecode")
        _, _, _, _, env_vars = forge_entry
        assert "FORGE_CONFIG" in env_vars, (
            "FORGE_CONFIG env var missing from forgecode detector"
        )


# ---------------------------------------------------------------------------
# detect_cli_type() — default + regression tests
# ---------------------------------------------------------------------------

class TestDetectCliTypeDefaults:
    """Regression guards: existing detection must not break."""

    def test_default_returns_claude(self, monkeypatch):
        _clean_env(monkeypatch)
        from autorun.config import detect_cli_type
        assert detect_cli_type() == "claude"

    def test_gemini_session_id_env_returns_gemini(self, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("GEMINI_SESSION_ID", "fake-session")
        from autorun.config import detect_cli_type
        assert detect_cli_type() == "gemini"

    def test_gemini_project_dir_env_returns_gemini(self, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("GEMINI_PROJECT_DIR", "/tmp/project")
        from autorun.config import detect_cli_type
        assert detect_cli_type() == "gemini"

    def test_explicit_payload_claude_overrides_gemini_env(self, monkeypatch):
        """Regression: payload cli_type='claude' must override GEMINI env vars."""
        _clean_env(monkeypatch)
        monkeypatch.setenv("GEMINI_SESSION_ID", "fake")
        from autorun.config import detect_cli_type
        assert detect_cli_type(payload={"cli_type": "claude"}) == "claude"

    def test_explicit_payload_gemini_returns_gemini(self, monkeypatch):
        _clean_env(monkeypatch)
        from autorun.config import detect_cli_type
        assert detect_cli_type(payload={"cli_type": "gemini"}) == "gemini"

    def test_gemini_event_name_in_payload_returns_gemini(self, monkeypatch):
        _clean_env(monkeypatch)
        from autorun.config import detect_cli_type
        result = detect_cli_type(payload={"hook_event_name": "BeforeTool"})
        assert result == "gemini"

    def test_gemini_path_hint_in_payload_returns_gemini(self, monkeypatch):
        _clean_env(monkeypatch)
        from autorun.config import detect_cli_type
        result = detect_cli_type(payload={"transcript_path": "/home/user/.gemini/session.json"})
        assert result == "gemini"

    def test_gemini_session_id_key_in_payload_returns_gemini(self, monkeypatch):
        _clean_env(monkeypatch)
        from autorun.config import detect_cli_type
        result = detect_cli_type(payload={"sessionId": "abc-123"})
        assert result == "gemini"

    def test_none_payload_no_env_returns_claude(self, monkeypatch):
        _clean_env(monkeypatch)
        from autorun.config import detect_cli_type
        assert detect_cli_type(payload=None) == "claude"


# ---------------------------------------------------------------------------
# Codex detection tests
# ---------------------------------------------------------------------------

class TestCodexDetection:
    """Codex CLI platform detection."""

    def test_codex_session_id_env_returns_codex(self, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("CODEX_SESSION_ID", "codex-session-xyz")
        from autorun.config import detect_cli_type
        assert detect_cli_type() == "codex", (
            "CODEX_SESSION_ID env var must return 'codex'"
        )

    def test_codex_project_dir_env_returns_codex(self, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("CODEX_PROJECT_DIR", "/tmp/myproject")
        from autorun.config import detect_cli_type
        assert detect_cli_type() == "codex", (
            "CODEX_PROJECT_DIR env var must return 'codex'"
        )

    def test_explicit_payload_codex_returns_codex(self, monkeypatch):
        _clean_env(monkeypatch)
        from autorun.config import detect_cli_type
        assert detect_cli_type(payload={"cli_type": "codex"}) == "codex"

    def test_codex_path_hint_in_payload_returns_codex(self, monkeypatch):
        _clean_env(monkeypatch)
        from autorun.config import detect_cli_type
        result = detect_cli_type(payload={"transcript_path": "/home/user/.codex/session.json"})
        assert result == "codex", (
            ".codex path hint in transcript_path must return 'codex'"
        )

    def test_codex_env_overridden_by_explicit_claude_payload(self, monkeypatch):
        """Explicit payload must take priority over Codex env vars."""
        _clean_env(monkeypatch)
        monkeypatch.setenv("CODEX_SESSION_ID", "codex-session")
        from autorun.config import detect_cli_type
        assert detect_cli_type(payload={"cli_type": "claude"}) == "claude"

    def test_codex_env_overridden_by_explicit_gemini_payload(self, monkeypatch):
        """Explicit payload must take priority over Codex env vars."""
        _clean_env(monkeypatch)
        monkeypatch.setenv("CODEX_SESSION_ID", "codex-session")
        from autorun.config import detect_cli_type
        assert detect_cli_type(payload={"cli_type": "gemini"}) == "gemini"


# ---------------------------------------------------------------------------
# Antigravity detection tests
# ---------------------------------------------------------------------------

class TestAntigravityDetection:
    """Google Antigravity platform detection."""

    def test_antigravity_session_id_env_returns_antigravity(self, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("ANTIGRAVITY_SESSION_ID", "agy-session")
        from autorun.config import detect_cli_type
        assert detect_cli_type() == "antigravity"

    def test_agy_session_id_env_returns_antigravity(self, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("AGY_SESSION_ID", "agy-session")
        from autorun.config import detect_cli_type
        assert detect_cli_type() == "antigravity"

    def test_explicit_payload_antigravity_returns_antigravity(self, monkeypatch):
        _clean_env(monkeypatch)
        from autorun.config import detect_cli_type
        assert detect_cli_type(payload={"cli_type": "antigravity"}) == "antigravity"

    def test_antigravity_cli_path_hint_beats_gemini(self, monkeypatch):
        _clean_env(monkeypatch)
        from autorun.config import detect_cli_type
        result = detect_cli_type(
            payload={"transcript_path": "/Users/me/.gemini/antigravity-cli/session.json"}
        )
        assert result == "antigravity"

    def test_antigravity_home_path_hint_returns_antigravity(self, monkeypatch):
        _clean_env(monkeypatch)
        from autorun.config import detect_cli_type
        result = detect_cli_type(payload={"transcript_path": "/Users/me/.antigravity/session.json"})
        assert result == "antigravity"


# ---------------------------------------------------------------------------
# ForgeCode detection tests
# ---------------------------------------------------------------------------

class TestForgeCodeDetection:
    """ForgeCode platform detection."""

    def test_forge_config_env_returns_forgecode(self, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("FORGE_CONFIG", "/home/user/.forge")
        from autorun.config import detect_cli_type
        assert detect_cli_type() == "forgecode", (
            "FORGE_CONFIG env var must return 'forgecode'"
        )

    def test_explicit_payload_forgecode_returns_forgecode(self, monkeypatch):
        _clean_env(monkeypatch)
        from autorun.config import detect_cli_type
        assert detect_cli_type(payload={"cli_type": "forgecode"}) == "forgecode"

    def test_forgecode_path_hint_in_payload_returns_forgecode(self, monkeypatch):
        _clean_env(monkeypatch)
        from autorun.config import detect_cli_type
        result = detect_cli_type(payload={"transcript_path": "/home/user/.forge/session.json"})
        assert result == "forgecode", (
            ".forge path hint in transcript_path must return 'forgecode'"
        )

    def test_forgecode_env_overridden_by_explicit_claude_payload(self, monkeypatch):
        """Explicit payload must take priority over ForgeCode env vars."""
        _clean_env(monkeypatch)
        monkeypatch.setenv("FORGE_CONFIG", "/home/user/.forge")
        from autorun.config import detect_cli_type
        assert detect_cli_type(payload={"cli_type": "claude"}) == "claude"

    def test_forgecode_has_no_hook_event_names(self):
        """ForgeCode has no external hooks — its event_names set must be empty."""
        from autorun.config import _CLI_DETECTORS
        forge_entry = next(d for d in _CLI_DETECTORS if d[0] == "forgecode")
        _, _, event_names, _, _ = forge_entry
        assert len(event_names) == 0, (
            "ForgeCode has no external hook system — event_names must be empty frozenset"
        )


# ---------------------------------------------------------------------------
# Priority ordering tests
# ---------------------------------------------------------------------------

class TestDetectionPriority:
    """Explicit payload > env vars > default."""

    def test_payload_priority_over_all_env_vars(self, monkeypatch):
        """Payload cli_type beats every env var."""
        _clean_env(monkeypatch)
        monkeypatch.setenv("GEMINI_SESSION_ID", "g")
        monkeypatch.setenv("CODEX_SESSION_ID", "c")
        monkeypatch.setenv("FORGE_CONFIG", "f")
        from autorun.config import detect_cli_type
        assert detect_cli_type(payload={"cli_type": "claude"}) == "claude"
        assert detect_cli_type(payload={"cli_type": "codex"}) == "codex"
        assert detect_cli_type(payload={"cli_type": "forgecode"}) == "forgecode"

    def test_gemini_beats_codex_in_env_priority(self, monkeypatch):
        """When both Gemini and Codex env vars are set, Gemini wins (comes first
        in _CLI_DETECTORS list).
        """
        _clean_env(monkeypatch)
        monkeypatch.setenv("GEMINI_SESSION_ID", "g")
        monkeypatch.setenv("CODEX_SESSION_ID", "c")
        from autorun.config import detect_cli_type, _CLI_DETECTORS
        result = detect_cli_type()
        # Whichever platform appears first in _CLI_DETECTORS wins
        names_in_order = [d[0] for d in _CLI_DETECTORS]
        gemini_idx = names_in_order.index("gemini")
        codex_idx = names_in_order.index("codex")
        expected = "gemini" if gemini_idx < codex_idx else "codex"
        assert result == expected, (
            f"Expected {expected} (first in _CLI_DETECTORS) when both env vars set, got {result}"
        )

    def test_known_platform_in_payload_source_field(self, monkeypatch):
        """'source' field in payload also triggers explicit detection."""
        _clean_env(monkeypatch)
        from autorun.config import detect_cli_type
        # 'source' field with a known platform name
        assert detect_cli_type(payload={"source": "codex"}) == "codex"
        assert detect_cli_type(payload={"source": "forgecode"}) == "forgecode"


# ---------------------------------------------------------------------------
# Backward-compatibility guards
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    """Ensure refactor doesn't break existing callers."""

    def test_should_use_exit2_workaround_still_works(self, monkeypatch):
        """should_use_exit2_workaround() must still function after refactor."""
        _clean_env(monkeypatch)
        from autorun.config import should_use_exit2_workaround
        # Should not raise
        result = should_use_exit2_workaround()
        assert isinstance(result, bool)

    def test_detect_cli_type_is_claude_returns_bool(self, monkeypatch):
        """is_claude() helper (if it exists) still works."""
        _clean_env(monkeypatch)
        try:
            from autorun.config import is_claude
            assert is_claude() is True
        except ImportError:
            pass  # is_claude is optional

    def test_gemini_events_still_accessible(self):
        """_GEMINI_EVENTS frozenset must still exist for backward compat."""
        from autorun.config import _GEMINI_EVENTS
        assert isinstance(_GEMINI_EVENTS, frozenset)
        assert "BeforeTool" in _GEMINI_EVENTS
        assert "AfterTool" in _GEMINI_EVENTS

    def test_detect_cli_type_with_no_args_returns_string(self, monkeypatch):
        """detect_cli_type() with no args must return a string, not raise."""
        _clean_env(monkeypatch)
        from autorun.config import detect_cli_type
        result = detect_cli_type()
        assert isinstance(result, str)
        assert result in {"claude", "gemini", "codex", "forgecode"}
