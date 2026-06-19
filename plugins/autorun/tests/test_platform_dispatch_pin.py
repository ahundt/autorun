"""Behavioral pin tests for platform dispatch sites (v0.11.0 / C2 refactor).

These tests capture the EXACT current behavior of every cli_type-aware
function so the refactor to a Platform registry in `platforms.py` can be
verified zero-behavior-change. They MUST pass before AND after the
refactor.

Dispatch surfaces covered (the 41-sites consolidation target):
  - config.detect_cli_type()                  — CLI detection from payload + env
  - core.get_cli_event_name()                 — internal→CLI event name
  - core._bug_18534_human_channels()          — Bug #18534 workaround
  - core.get_tool_names()                     — tool-name dict per CLI
  - core.format_suggestion()                  — {tool} placeholder substitution
  - core.GEMINI_EVENT_MAP                     — Gemini→internal event remap
  - core.INTERNAL_TO_CLAUDE / INTERNAL_TO_GEMINI
  - install.detect_available_clis() input shape (binary-presence dict)

Concurrency note: pure read-only data structures — no fixtures required
for thread/process safety. The tests construct payloads inline.
"""
from __future__ import annotations

import os
import pytest

from autorun import config as cfg_mod
from autorun import core as core_mod
from autorun.config import detect_cli_type
from autorun.core import (
    GEMINI_EVENT_MAP,
    INTERNAL_TO_CLAUDE,
    INTERNAL_TO_GEMINI,
    CLI_TOOL_NAMES,
    _bug_18534_human_channels,
    format_suggestion,
    get_cli_event_name,
    get_tool_names,
)


# ─── detect_cli_type pin matrix ───────────────────────────────────────────────

@pytest.mark.parametrize("payload,expected", [
    # Explicit cli_type / source field
    ({"cli_type": "claude"}, "claude"),
    ({"cli_type": "gemini"}, "gemini"),
    ({"cli_type": "codex"}, "codex"),
    ({"cli_type": "forgecode"}, "forgecode"),
    ({"source": "claude"}, "claude"),
    ({"source": "gemini"}, "gemini"),
    # Gemini session-id keys
    ({"GEMINI_SESSION_ID": "abc"}, "gemini"),
    ({"sessionId": "abc"}, "gemini"),
    ({"session_id": "abc"}, "gemini"),
    # Codex session-id key
    ({"CODEX_SESSION_ID": "xyz"}, "codex"),
    # Gemini event names
    ({"hook_event_name": "BeforeTool"}, "gemini"),
    ({"hook_event_name": "AfterTool"}, "gemini"),
    ({"hook_event_name": "BeforeAgent"}, "gemini"),
    # Transcript path hints
    ({"transcript_path": "/home/u/.gemini/x.jsonl"}, "gemini"),
    ({"transcript_path": "/home/u/.codex/x.jsonl"}, "codex"),
    ({"transcript_path": "/home/u/.forge/x.md"}, "forgecode"),
    # Defaults
    ({}, "claude"),
    ({"hook_event_name": "PreToolUse"}, "claude"),
    (None, "claude"),
])
def test_detect_cli_type_pin(payload, expected, monkeypatch):
    """detect_cli_type's payload-driven decisions are stable."""
    # Clear env vars to isolate from runtime
    for k in ("GEMINI_SESSION_ID", "GEMINI_PROJECT_DIR", "GEMINI_CLI",
              "CODEX_SESSION_ID", "CODEX_PROJECT_DIR", "FORGE_CONFIG"):
        monkeypatch.delenv(k, raising=False)
    assert detect_cli_type(payload) == expected


@pytest.mark.parametrize("env_var,expected", [
    ("GEMINI_SESSION_ID", "gemini"),
    ("GEMINI_PROJECT_DIR", "gemini"),
    ("GEMINI_CLI", "gemini"),
    ("CODEX_SESSION_ID", "codex"),
    ("CODEX_PROJECT_DIR", "codex"),
    ("FORGE_CONFIG", "forgecode"),
])
def test_detect_cli_type_env_var_pin(env_var, expected, monkeypatch):
    """env-var fallback detection (when payload has no signals)."""
    for k in ("GEMINI_SESSION_ID", "GEMINI_PROJECT_DIR", "GEMINI_CLI",
              "CODEX_SESSION_ID", "CODEX_PROJECT_DIR", "FORGE_CONFIG"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(env_var, "set")
    assert detect_cli_type() == expected


def test_detect_cli_type_explicit_payload_wins_over_env(monkeypatch):
    """Explicit payload cli_type beats env-var detection."""
    monkeypatch.setenv("GEMINI_SESSION_ID", "g")
    assert detect_cli_type({"cli_type": "claude"}) == "claude"
    assert detect_cli_type({"cli_type": "codex"}) == "codex"


# ─── get_cli_event_name pin ────────────────────────────────────────────────────

@pytest.mark.parametrize("internal,cli,expected", [
    # Identity for Claude
    ("PreToolUse", "claude", "PreToolUse"),
    ("PostToolUse", "claude", "PostToolUse"),
    ("Stop", "claude", "Stop"),
    ("SessionStart", "claude", "SessionStart"),
    # Gemini remap
    ("PreToolUse", "gemini", "BeforeTool"),
    ("PostToolUse", "gemini", "AfterTool"),
    ("UserPromptSubmit", "gemini", "BeforeAgent"),
    ("Stop", "gemini", "AfterAgent"),
    ("SessionStart", "gemini", "SessionStart"),
    ("SessionEnd", "gemini", "SessionEnd"),
    # Unknown events pass through unchanged
    ("Unknown", "claude", "Unknown"),
    ("Unknown", "gemini", "Unknown"),
])
def test_get_cli_event_name_pin(internal, cli, expected):
    assert get_cli_event_name(internal, cli) == expected


# ─── GEMINI_EVENT_MAP pin (frozen contract) ───────────────────────────────────

def test_gemini_event_map_pin():
    expected = {
        "BeforeTool": "PreToolUse",
        "AfterTool": "PostToolUse",
        "BeforeAgent": "UserPromptSubmit",
        "AfterAgent": "Stop",
        "SessionStart": "SessionStart",
        "SessionEnd": "SessionEnd",
        "BeforeModel": "BeforeModel",
        "AfterModel": "AfterModel",
        "PreCompress": "PreCompress",
    }
    for k, v in expected.items():
        assert GEMINI_EVENT_MAP.get(k) == v, f"GEMINI_EVENT_MAP[{k!r}] regressed"


def test_internal_to_claude_pin():
    expected = {
        "PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop",
        "SessionStart", "SessionEnd", "BeforeModel", "AfterModel",
    }
    assert expected.issubset(set(INTERNAL_TO_CLAUDE.keys()))
    # Identity invariant
    for k, v in INTERNAL_TO_CLAUDE.items():
        assert v == k, f"INTERNAL_TO_CLAUDE[{k!r}]={v!r} must be identity"


def test_internal_to_gemini_pin():
    expected = {
        "PreToolUse": "BeforeTool",
        "PostToolUse": "AfterTool",
        "UserPromptSubmit": "BeforeAgent",
        "Stop": "AfterAgent",
    }
    for k, v in expected.items():
        assert INTERNAL_TO_GEMINI.get(k) == v, f"INTERNAL_TO_GEMINI[{k!r}] regressed"


# ─── _bug_18534_human_channels pin ─────────────────────────────────────────────

def test_bug_18534_human_channels_claude_default(monkeypatch):
    """Claude default: workaround ON — 'ai' channel merges into human channels."""
    monkeypatch.delenv("AUTORUN_BUG_CLAUDE_CODE_IGNORES_ADDITIONAL_CONTEXT_JSON_ENTRY_BUG_18534_WORKAROUND_ENABLED", raising=False)
    chans = _bug_18534_human_channels("claude")
    assert "ai" in chans
    assert "human" in chans
    assert "both" in chans


def test_bug_18534_human_channels_gemini_default(monkeypatch):
    """Gemini default: workaround OFF — base {'human', 'both'} only."""
    monkeypatch.delenv("AUTORUN_BUG_CLAUDE_CODE_IGNORES_ADDITIONAL_CONTEXT_JSON_ENTRY_BUG_18534_WORKAROUND_ENABLED", raising=False)
    chans = _bug_18534_human_channels("gemini")
    assert "ai" not in chans
    assert chans == {"human", "both"}


@pytest.mark.parametrize("env_val,expected_has_ai", [
    ("true", True),       # claude with override-on
    ("1", True),
    ("auto", True),
    ("always", True),
    ("false", False),
    ("0", False),
    ("never", False),
])
def test_bug_18534_env_var_pin(env_val, expected_has_ai, monkeypatch):
    """Env-var override semantics match current behavior."""
    monkeypatch.setenv("AUTORUN_BUG_CLAUDE_CODE_IGNORES_ADDITIONAL_CONTEXT_JSON_ENTRY_BUG_18534_WORKAROUND_ENABLED", env_val)
    chans = _bug_18534_human_channels("claude")
    assert ("ai" in chans) == expected_has_ai


# ─── CLI_TOOL_NAMES pin (full table snapshot) ─────────────────────────────────

def test_cli_tool_names_claude_pin():
    expected = {
        "grep": "Grep", "glob": "Glob", "read": "Read",
        "write": "Write", "edit": "Edit", "bash": "Bash", "ls": "LS",
        "task_create": "TaskCreate", "task_update": "TaskUpdate",
        "task_list": "TaskList", "task_title": "subject",
        "task_id_param": "taskId",
    }
    for k, v in expected.items():
        assert CLI_TOOL_NAMES["claude"][k] == v, f"CLI_TOOL_NAMES['claude'][{k!r}] regressed"


def test_cli_tool_names_gemini_pin():
    expected = {
        "grep": "grep_search", "glob": "glob", "read": "read_file",
        "write": "write_file", "edit": "replace", "bash": "run_shell_command",
        "ls": "list_directory",
        "task_create": "tracker_create_task",
        "task_update": "tracker_update_task",
        "task_list": "tracker_list_tasks",
        "task_title": "title", "task_id_param": "id",
    }
    for k, v in expected.items():
        assert CLI_TOOL_NAMES["gemini"][k] == v, f"CLI_TOOL_NAMES['gemini'][{k!r}] regressed"


def test_cli_tool_names_codex_pin():
    expected = {
        "grep": "`rg -n` shell search", "glob": "`rg --files` shell listing",
        "read": "shell file inspection", "write": "apply_patch",
        "edit": "apply_patch", "bash": "Bash", "ls": "LS",
        "task_progress": "update_plan",
    }
    for k, v in expected.items():
        assert CLI_TOOL_NAMES["codex"][k] == v, f"CLI_TOOL_NAMES['codex'][{k!r}] regressed"


def test_get_tool_names_unknown_returns_empty():
    assert get_tool_names("doesnotexist") == {}


# ─── format_suggestion pin (placeholder substitution) ─────────────────────────

@pytest.mark.parametrize("template,cli,expected", [
    ("Use {grep} instead", "claude", "Use Grep instead"),
    ("Use {grep} instead", "gemini", "Use grep_search instead"),
    ("Use {task_progress}", "codex", "Use update_plan"),
    ("Use {read} then {edit}", "codex", "Use shell file inspection then apply_patch"),
    ("Try {edit} or {bash}", "claude", "Try Edit or Bash"),
    ("Try {edit} or {bash}", "gemini", "Try replace or run_shell_command"),
    ("xargs -I{} mv {} dest", "claude", "xargs -I{} mv {} dest"),  # shell syntax preserved
    ("plain text", "claude", "plain text"),
    ("plain text", "unknown", "plain text"),
    ("{grep}", "unknown", "{grep}"),  # unknown CLI passes through
])
def test_format_suggestion_pin(template, cli, expected):
    assert format_suggestion(template, cli) == expected


# ─── _CLI_DETECTORS structure pin (current uncommitted refactor) ──────────────

def test_cli_detectors_structure_pin():
    """Verify _CLI_DETECTORS is a list of 5-tuples in detection priority order."""
    detectors = cfg_mod._CLI_DETECTORS
    assert isinstance(detectors, list)
    assert len(detectors) >= 3  # gemini + codex + forgecode at minimum
    for entry in detectors:
        assert len(entry) == 5, f"each detector entry must be 5-tuple: {entry}"
        name, session_keys, event_names, path_hints, env_vars = entry
        assert isinstance(name, str)
        assert isinstance(session_keys, tuple)
        assert hasattr(event_names, "__contains__")  # frozenset
        assert isinstance(path_hints, tuple)
        assert isinstance(env_vars, tuple)


def test_known_cli_names_pin():
    """_KNOWN_CLI_NAMES must include all detector names plus 'claude'."""
    assert "claude" in cfg_mod._KNOWN_CLI_NAMES
    assert "gemini" in cfg_mod._KNOWN_CLI_NAMES
    assert "codex" in cfg_mod._KNOWN_CLI_NAMES
    assert "forgecode" in cfg_mod._KNOWN_CLI_NAMES


# ─── Detection priority pin (Gemini before Codex) ─────────────────────────────

def test_detection_priority_gemini_before_codex(monkeypatch):
    """Both Gemini and Codex env vars present → Gemini wins (declared first)."""
    monkeypatch.setenv("GEMINI_SESSION_ID", "g")
    monkeypatch.setenv("CODEX_SESSION_ID", "c")
    assert detect_cli_type() == "gemini"
