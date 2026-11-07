"""Tests for multi-platform CLI detection (_CLI_DETECTORS refactor).

Verifies that detect_cli_type() and _CLI_DETECTORS correctly identify all
supported platforms: claude, gemini, antigravity, qwen, codex, forgecode.

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
        "QWEN_SESSION_ID", "QWEN_PROJECT_DIR", "QWEN_CODE",
        "CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_PROJECT_DIR",
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

    def test_cli_detectors_has_qwen(self):
        from autorun.config import _CLI_DETECTORS
        names = [d[0] for d in _CLI_DETECTORS]
        assert "qwen" in names, "_CLI_DETECTORS must include qwen"

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

    def test_qwen_detector_has_qwen_env_vars(self):
        from autorun.config import _CLI_DETECTORS
        qwen_entry = next(d for d in _CLI_DETECTORS if d[0] == "qwen")
        _, _, _, _, env_vars = qwen_entry
        assert "QWEN_SESSION_ID" in env_vars
        assert "QWEN_PROJECT_DIR" in env_vars


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
# Qwen Code detection tests
# ---------------------------------------------------------------------------

class TestQwenDetection:
    """Qwen Code platform detection."""

    def test_qwen_session_id_env_returns_qwen(self, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("QWEN_SESSION_ID", "qwen-session")
        from autorun.config import detect_cli_type
        assert detect_cli_type() == "qwen"

    def test_explicit_payload_qwen_returns_qwen(self, monkeypatch):
        _clean_env(monkeypatch)
        from autorun.config import detect_cli_type
        assert detect_cli_type(payload={"cli_type": "qwen"}) == "qwen"

    def test_qwen_home_path_hint_returns_qwen(self, monkeypatch):
        _clean_env(monkeypatch)
        from autorun.config import detect_cli_type
        result = detect_cli_type(payload={"transcript_path": "/Users/me/.qwen/session.json"})
        assert result == "qwen"


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


# ---------------------------------------------------------------------------
# Untagged payloads: one fixture table, both detectors
# ---------------------------------------------------------------------------

# (payload, harness). Real hook payloads carry ``session_id`` on every harness,
# so it cannot identify one; the transcript path and event name can.
_UNTAGGED_PAYLOADS = [
    ({"session_id": "s", "hook_event_name": "PreToolUse",
      "transcript_path": "/home/u/.claude/projects/p/s.jsonl"}, "claude"),
    ({"session_id": "s", "hook_event_name": "PreToolUse"}, "claude"),
    ({"session_id": "s", "hook_event_name": "PreToolUse",
      "transcript_path": "/home/u/.codex/sessions/2026/10/31/s.jsonl"}, "codex"),
    ({"session_id": "s", "hook_event_name": "PreToolUse",
      "transcript_path": "/home/u/.qwen/tmp/h/chats/s.jsonl"}, "qwen"),
    ({"session_id": "s", "transcript_path": "/home/u/.pi/agent/sessions/s.jsonl"}, "pi"),
    ({"session_id": "s", "transcript_path": "/home/u/.prime/agent/sessions/s.jsonl"}, "prime"),
    ({"session_id": "s", "transcript_path": "/home/u/.forge/s.jsonl"}, "forgecode"),
    ({"session_id": "s", "transcript_path": "/home/u/.config/opencode/s.json"}, "opencode"),
    ({"session_id": "s", "hook_event_name": "PreToolUse",
      "transcript_path": "/home/u/.gemini/antigravity/s.jsonl"}, "antigravity"),
    ({"session_id": "s", "hook_event_name": "BeforeTool",
      "transcript_path": "/home/u/.gemini/tmp/h/chats/s.json"}, "gemini"),
    ({"session_id": "s", "hook_event_name": "SessionStart",
      "transcript_path": "/home/u/.gemini/tmp/h/chats/s.json"}, "gemini"),
]

# Payloads hooks/hook_entry.py:detect_cli_type once answered differently from
# config.detect_cli_type, when it kept its own event and path lists.
_HOOK_ENTRY_FORMER_DISAGREEMENTS = [
    # It mapped every SessionEnd to gemini; Claude has SessionEnd too.
    ({"session_id": "s", "hook_event_name": "SessionEnd",
      "transcript_path": "/home/u/.claude/projects/p/s.jsonl"}, "claude"),
    # It mapped every PermissionRequest to forgecode, which has no hooks;
    # Claude, Codex and Qwen have PermissionRequest.
    ({"session_id": "s", "hook_event_name": "PermissionRequest",
      "transcript_path": "/home/u/.claude/projects/p/s.jsonl"}, "claude"),
    # It read no payload session keys, so GEMINI's sessionId went unseen.
    ({"sessionId": "s"}, "gemini"),
]


def _clear_detection_env(monkeypatch):
    from autorun.platforms import PLATFORMS

    names = {"AUTORUN_CLI_TYPE", "GEMINI_PROJECT_DIR", "QWEN_PROJECT_DIR",
             "CODEX_PROJECT_DIR", "ANTIGRAVITY_PROJECT_DIR"}
    for platform in PLATFORMS.values():
        names.update(platform.detect_env_vars)
    for name in names:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(("payload", "expected"), _UNTAGGED_PAYLOADS)
def test_config_detects_the_harness_of_an_untagged_payload(monkeypatch, payload, expected):
    """GEMINI listed the generic ``session_id`` as a session key and is checked
    second, so every untagged payload -- .claude, .codex, .qwen, .pi/agent
    transcripts alike -- was classified as gemini."""
    _clear_detection_env(monkeypatch)
    from autorun.config import detect_cli_type

    assert detect_cli_type(payload) == expected


def _hook_entry_detect():
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "hooks" / "hook_entry.py"
    spec = importlib.util.spec_from_file_location("autorun_hook_entry_detect", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.detect_cli_type


@pytest.mark.parametrize(
    ("payload", "expected"),
    _UNTAGGED_PAYLOADS + _HOOK_ENTRY_FORMER_DISAGREEMENTS,
)
def test_hook_entry_detector_agrees_with_config(monkeypatch, payload, expected):
    """hook_entry.py keeps a stdlib-only copy of detection; pin it to config's."""
    _clear_detection_env(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["hook_entry.py"])
    from autorun.config import detect_cli_type

    assert detect_cli_type(payload) == expected
    assert _hook_entry_detect()(payload) == expected


def test_hook_entry_payload_detectors_are_the_registrys():
    """hook_entry.py cannot import the registry, so its table is a copy; this
    keeps the copy equal to config._CLI_DETECTORS, order included."""
    import importlib.util

    from autorun.config import _CLI_DETECTORS

    path = Path(__file__).resolve().parent.parent / "hooks" / "hook_entry.py"
    spec = importlib.util.spec_from_file_location("autorun_hook_entry_table", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    registry = [
        (name, tuple(keys), frozenset(events), tuple(hints))
        for name, keys, events, hints, _env in _CLI_DETECTORS
    ]
    copy = [
        (name, tuple(keys), frozenset(events), tuple(hints))
        for name, keys, events, hints in module._PAYLOAD_DETECTORS
    ]
    assert copy == registry


# ─── hook_entry and config detect the same harness (behavioural pin) ─────────
_DETECTION_ENV = sorted({
    var for p in __import__("autorun.platforms", fromlist=["x"]).PLATFORMS.values()
    for var in p.detect_env_vars
} | {"CLAUDE_PROJECT_DIR", "AUTORUN_CLI_TYPE"})

_ENV_CASES = [
    {},
    {"PI_CODING_AGENT": "1", "OPENCODE_CONFIG": "x"},
    {"PI_SESSION_ID": "s", "FORGE_CONFIG": "x"},
    {"GEMINI_PROJECT_DIR": "/p", "CLAUDE_PROJECT_DIR": "/p"},
    {"QWEN_PROJECT_DIR": "/p", "CLAUDE_PROJECT_DIR": "/p"},
    {"CODEX_PROJECT_DIR": "/p", "CLAUDE_PROJECT_DIR": "/p"},
    {"GEMINI_PROJECT_DIR": "/p"},
    {"GEMINI_SESSION_ID": "g", "CLAUDE_PROJECT_DIR": "/p"},
    {"CODEX_THREAD_ID": "t"},
] + [{var: "1"} for var in _DETECTION_ENV if var not in {"CLAUDE_PROJECT_DIR", "AUTORUN_CLI_TYPE"}]

_PAYLOAD_CASES = [
    None,
    {"hook_event_name": "PreToolUse", "transcript_path": "/home/u/.claude/projects/p/s.jsonl"},
    {"hook_event_name": "PreToolUse", "transcript_path": "/srv/codex/sessions/s.jsonl"},
    {"hook_event_name": "PreToolUse", "transcript_path": "/srv/qwen/tmp/s.jsonl"},
    {"hook_event_name": "PreToolUse", "transcript_path": "/srv/gemini/tmp/s.jsonl"},
    {"hook_event_name": "BeforeTool"},
]


@pytest.mark.parametrize("env", _ENV_CASES, ids=lambda e: "+".join(e) or "none")
@pytest.mark.parametrize("payload", _PAYLOAD_CASES, ids=lambda p: (p or {}).get("transcript_path", str(p)))
def test_hook_entry_and_config_detect_the_same_harness(monkeypatch, env, payload):
    """hook_entry.py keeps a stdlib copy of detection for when autorun cannot
    import; literal-presence checks let its order and rules drift (Pi after
    OpenCode there, before it here), so the two answered differently for one
    hook. The answer is pinned, not the source text."""
    import importlib.util
    import io
    import sys as _sys

    from autorun.config import detect_cli_type

    path = Path(__file__).resolve().parent.parent / "hooks" / "hook_entry.py"
    spec = importlib.util.spec_from_file_location("autorun_hook_entry_detect", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for var in _DETECTION_ENV:
        monkeypatch.delenv(var, raising=False)
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    monkeypatch.setattr(_sys, "argv", ["hook_entry.py"])
    monkeypatch.setattr(_sys, "stdin", io.StringIO(""))

    assert module.detect_cli_type(payload) == detect_cli_type(payload)


def test_an_inherited_project_dir_does_not_override_a_claude_session(monkeypatch):
    """Gemini sets CLAUDE_PROJECT_DIR as an alias of GEMINI_PROJECT_DIR
    (https://geminicli.com/docs/hooks/), so a *_PROJECT_DIR beside it never
    tells harnesses apart; only session ids do."""
    from autorun.config import detect_cli_type

    for var in _DETECTION_ENV:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/p")
    monkeypatch.setenv("GEMINI_PROJECT_DIR", "/p")
    claude_payload = {"hook_event_name": "PreToolUse",
                      "transcript_path": "/home/u/.claude/projects/p/s.jsonl"}
    assert detect_cli_type(claude_payload) == "claude"
    assert detect_cli_type(None) == "claude"


def test_hook_entry_detection_tables_equal_the_registry():
    import importlib.util

    from autorun.platforms import PLATFORMS, detection_platforms

    path = Path(__file__).resolve().parent.parent / "hooks" / "hook_entry.py"
    spec = importlib.util.spec_from_file_location("autorun_hook_entry_env_table", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module._ENV_DETECTORS == tuple((p.name, p.detect_env_vars) for p in detection_platforms())
    assert module._CLAUDE_PATH_HINTS == PLATFORMS["claude"].detect_path_hints
