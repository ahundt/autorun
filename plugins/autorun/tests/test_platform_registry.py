"""Tests for the Platform dataclass registry (v0.11.0 / C2).

These tests pin the Platform abstraction's INVARIANTS (immutability,
single source of truth, multi-process/thread/session safety) for the
currently-shipped platforms: Claude, Gemini, Antigravity, Qwen Code, Codex,
ForgeCode.
"""

from __future__ import annotations

import dataclasses
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Set

import pytest


# Imports are top-level so test collection FAILS LOUDLY if platforms.py
# is missing — TDD-first marker that C2 implementation is incomplete.
from autorun.platforms import (
    PLATFORMS,
    HookProtocol,
    Platform,
    register,
    get_platform,
    detection_platforms,
    hook_platforms,
    to_autorun_event,
    to_harness_cli_event,
)


# ─── Registry shape ───────────────────────────────────────────────────────────


def test_registry_contains_supported_platforms():
    for name in ("claude", "gemini", "antigravity", "qwen", "codex", "forgecode"):
        assert name in PLATFORMS, f"PLATFORMS missing {name!r}"


def test_platform_lookup_returns_same_instance():
    assert get_platform("claude") is PLATFORMS["claude"]
    assert get_platform("gemini") is PLATFORMS["gemini"]
    assert get_platform("unknown") is None


def test_detection_platforms_excludes_claude():
    """Claude is the fallback default — not part of positive detection iteration."""
    names = [p.name for p in detection_platforms()]
    assert "claude" not in names
    assert {"gemini", "antigravity", "qwen", "codex", "forgecode"}.issubset(set(names))


def test_hook_platforms_excludes_forgecode():
    """ForgeCode has no external hooks — should not appear in hook-capable list."""
    names = [p.name for p in hook_platforms()]
    assert "forgecode" not in names
    assert {"claude", "gemini", "antigravity", "qwen", "codex"}.issubset(set(names))


# ─── Immutability (multi-thread / multi-session safety) ───────────────────────


def test_platform_is_frozen_dataclass():
    """frozen=True ensures fields cannot mutate after construction."""
    p = PLATFORMS["claude"]
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        p.name = "evil"  # type: ignore[misc]


def test_platform_uses_slots():
    """slots=True prevents arbitrary attribute assignment."""
    p = PLATFORMS["claude"]
    # frozen+slots raises one of these depending on Python's internal path
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError, TypeError)):
        p.arbitrary_attr = "x"  # type: ignore[attr-defined]


def test_register_duplicate_raises():
    """Re-registering same platform name must raise (catches accidental overwrites)."""
    p = Platform(name="duplicate_for_test", display_name="X", binary="x")
    register(p)
    try:
        with pytest.raises(ValueError):
            register(Platform(name="duplicate_for_test", display_name="X2", binary="x2"))
    finally:
        # Clean up to keep registry consistent for other tests
        PLATFORMS.pop("duplicate_for_test", None)


# ─── Per-platform field invariants ────────────────────────────────────────────


def test_claude_platform_fields():
    p = PLATFORMS["claude"]
    assert p.binary == "claude"
    assert p.has_hooks is True
    assert p.schema_type == "strict"
    assert p.has_exit2_workaround is True
    assert p.drops_additional_context is True
    assert "Grep" in p.tool_names.values()
    assert "/Applications/Claude.app" in p.app_paths
    assert "com.anthropic.claudefordesktop" in p.app_bundle_ids


def test_gemini_platform_fields():
    p = PLATFORMS["gemini"]
    assert p.binary == "gemini"
    assert p.has_hooks is True
    assert p.schema_type == "permissive"
    assert p.drops_additional_context is False
    assert "grep_search" in p.tool_names.values()
    assert "BeforeTool" in p.detect_event_names


def test_antigravity_platform_fields():
    p = PLATFORMS["antigravity"]
    assert p.binary == "agy"
    assert p.has_hooks is True
    assert p.schema_type == "permissive"
    assert p.list_cmd == ("agy", "plugin", "list")
    assert ".gemini/antigravity-cli" in p.detect_path_hints
    assert "/Applications/Antigravity.app" in p.app_paths
    assert "com.google.antigravity" in p.app_bundle_ids
    assert p.task_management_style == "bulk_todos"
    assert p.hook_protocol.name == "antigravity"
    assert p.harness_cli_to_autorun_events["PreInvocation"] == "UserPromptSubmit"
    assert p.harness_cli_to_autorun_events["PostInvocation"] == "AfterModel"
    assert p.autorun_to_harness_cli_events["PreToolUse"] == "PreToolUse"
    assert p.autorun_to_harness_cli_events["Stop"] == "Stop"


def test_qwen_platform_fields():
    p = PLATFORMS["qwen"]
    assert p.binary == "qwen"
    assert p.has_hooks is True
    assert p.schema_type == "permissive"
    assert p.list_cmd == ("qwen", "extensions", "list")
    assert ".qwen" in p.detect_path_hints
    assert "grep_search" in p.tool_names.values()
    assert p.hook_protocol.name == "qwen"
    assert p.autorun_to_harness_cli_events["PreToolUse"] == "PreToolUse"
    assert p.autorun_to_harness_cli_events["Stop"] == "Stop"
    assert p.task_management_style == "bulk_todos"


@pytest.mark.parametrize(
    ("platform_name", "harness_event", "autorun_event"),
    [
        ("gemini", "BeforeTool", "PreToolUse"),
        ("qwen", "PreToolUse", "PreToolUse"),
        ("qwen", "PreCompact", "PreCompress"),
        ("antigravity", "PreInvocation", "UserPromptSubmit"),
        ("antigravity", "PostInvocation", "AfterModel"),
        ("codex", "Stop", "Stop"),
    ],
)
def test_registry_event_helpers_round_trip(platform_name, harness_event, autorun_event):
    assert to_autorun_event(harness_event, platform_name) == autorun_event
    assert to_harness_cli_event(autorun_event, platform_name) == harness_event


def test_codex_platform_fields():
    p = PLATFORMS["codex"]
    assert p.binary == "codex"
    assert p.has_hooks is True
    assert p.schema_type == "strict"  # same JSON schema as Claude
    assert p.has_exit2_workaround is False
    assert p.drops_additional_context is False
    assert "/Applications/Codex.app" in p.app_paths
    assert "com.openai.codex" in p.app_bundle_ids


def test_forgecode_platform_fields():
    p = PLATFORMS["forgecode"]
    assert p.binary == "forge"
    assert p.has_hooks is False
    assert p.schema_type == "none"


def test_qwen_protocol_translation_preserves_metadata_and_drops_unsupported_events():
    protocol = PLATFORMS["qwen"].hook_protocol
    source = {
        "description": "shared",
        "hooks": {
            "BeforeTool": [{"matcher": "Bash", "hooks": [{"command": "owned"}]}],
            "AfterModel": [{"hooks": [{"command": "unsupported"}]}],
        },
    }
    translated = protocol.translate_manifest(source, {"BeforeTool": "PreToolUse", "AfterModel": None})
    assert translated == {
        "description": "shared",
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"command": "owned"}]}]},
    }


def test_qwen_protocol_removes_matchers_from_matcherless_native_events():
    protocol = PLATFORMS["qwen"].hook_protocol
    translated = protocol.translate_manifest(
        {
            "hooks": {
                "BeforeAgent": [{"matcher": "legacy", "hooks": [{"command": "prompt"}]}],
                "AfterAgent": [{"matcher": "legacy", "hooks": [{"command": "stop"}]}],
            }
        },
        {"BeforeAgent": "UserPromptSubmit", "AfterAgent": "Stop"},
    )
    assert translated == {
        "hooks": {
            "UserPromptSubmit": [{"hooks": [{"command": "prompt"}]}],
            "Stop": [{"hooks": [{"command": "stop"}]}],
        }
    }


def test_antigravity_protocol_translation_flattens_only_flat_events():
    protocol = PLATFORMS["antigravity"].hook_protocol
    source = {
        "hooks": {
            "BeforeTool": [
                {"hooks": [{"command": "one"}]},
                {"matcher": "named", "hooks": [{"command": "two"}]},
            ],
            "AfterAgent": [{"matcher": "ignored", "hooks": [{"command": "stop"}]}],
        }
    }
    assert protocol.translate_manifest(source, {"BeforeTool": "PreToolUse", "AfterAgent": "Stop"}) == {
        "autorun": {
            "PreToolUse": [
                {"matcher": "*", "hooks": [{"command": "one"}]},
                {"matcher": "named", "hooks": [{"command": "two"}]},
            ],
            "Stop": [{"command": "stop"}],
        }
    }


def test_hook_protocol_translation_ignores_malformed_groups_without_mutating_input():
    protocol = PLATFORMS["antigravity"].hook_protocol
    source = {"hooks": {"BeforeTool": "invalid", "AfterAgent": [None, 3]}}
    before = repr(source)
    assert protocol.translate_manifest(source, {"BeforeTool": "PreToolUse", "AfterAgent": "Stop"}) == {"autorun": {"PreToolUse": [], "Stop": []}}
    assert repr(source) == before


def test_hook_protocol_translation_combines_collisions_and_copies_flat_handlers():
    protocol = PLATFORMS["antigravity"].hook_protocol
    source = {
        "hooks": {
            "AfterAgent": [{"hooks": [{"command": "one"}]}],
            "OtherStop": [{"hooks": [{"command": "two"}]}],
        }
    }
    translated = protocol.translate_manifest(source, {"AfterAgent": "Stop", "OtherStop": "Stop"})
    assert translated == {
        "autorun": {
            "Stop": [
                {"command": "one"},
                {"command": "two"},
            ]
        }
    }
    translated["autorun"]["Stop"][0]["command"] = "changed"
    assert source["hooks"]["AfterAgent"][0]["hooks"][0]["command"] == "one"


def test_hook_protocol_rejects_invalid_decision_location():
    with pytest.raises(ValueError, match="decision location"):
        HookProtocol("invalid", pretool_decision_location="unknown")


def test_qwen_protocol_filter_fails_closed_on_non_mapping_hook_output():
    protocol = PLATFORMS["qwen"].hook_protocol
    response = protocol.filter_response_to_harness_schema("PreToolUse", {"hookSpecificOutput": "invalid"})
    assert response is not None
    hook_output = response["hookSpecificOutput"]
    assert hook_output["permissionDecision"] == "deny"
    assert "must be a JSON object" in hook_output["permissionDecisionReason"]
    assert "autorun --restart-daemon" in hook_output["permissionDecisionReason"]


def test_qwen_protocol_filter_translates_explicit_root_allow():
    protocol = PLATFORMS["qwen"].hook_protocol
    response = protocol.filter_response_to_harness_schema("PreToolUse", {"decision": "allow"})
    assert response["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert response["hookSpecificOutput"]["permissionDecisionReason"] == ""


def test_qwen_protocol_filter_fails_closed_when_permission_decision_is_missing():
    protocol = PLATFORMS["qwen"].hook_protocol
    response = protocol.filter_response_to_harness_schema("PreToolUse", {})
    hook_output = response["hookSpecificOutput"]
    assert hook_output["permissionDecision"] == "deny"
    assert "contained no permission decision" in hook_output["permissionDecisionReason"]


@pytest.mark.parametrize(
    "hook_output",
    [
        {"hookEventName": "PreToolUse"},
        {"hookEventName": "PreToolUse", "permissionDecision": "unexpected"},
    ],
)
def test_qwen_protocol_filter_fails_closed_on_missing_or_unknown_nested_decision(hook_output):
    protocol = PLATFORMS["qwen"].hook_protocol
    response = protocol.filter_response_to_harness_schema("PreToolUse", {"hookSpecificOutput": hook_output})
    nested = response["hookSpecificOutput"]
    assert nested["permissionDecision"] == "deny"
    assert "could not verify" in nested["permissionDecisionReason"]


def test_qwen_posttool_uses_shared_feedback_only_compatibility_path():
    """Do not emit the pseudo-block ignored by Qwen's PostToolUse consumer."""
    protocol = PLATFORMS["qwen"].hook_protocol
    assert protocol.filter_response_to_harness_schema("PostToolUse", {"decision": "block", "reason": "feedback"}) is None


def test_hook_protocol_unknown_tool_decision_fails_closed():
    protocol = PLATFORMS["claude"].hook_protocol
    response = protocol.pretool_response("unexpected", "Blocked", "PreToolUse")
    assert response["decision"] == "block"
    assert response["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    ("platform_name", "expected"),
    [
        ("claude", {}),
        ("codex", {}),
        ("gemini", {"continue": True}),
        ("qwen", {"continue": True}),
        ("antigravity", {"continue": True}),
    ],
)
def test_hook_protocol_empty_response_contract(platform_name, expected):
    assert PLATFORMS[platform_name].hook_protocol.response_for_unhandled_hook() == expected


# ─── Multi-thread safety (PLATFORMS is read-only across sessions) ─────────────


def test_concurrent_get_platform_is_safe():
    """Many threads can call get_platform concurrently without exceptions or
    cross-contamination of the returned instances.
    """
    results: dict[int, str] = {}
    errors: list[Exception] = []

    def worker(i: int):
        try:
            p = get_platform(("claude", "gemini", "antigravity", "qwen", "codex", "forgecode")[i % 6])
            results[i] = p.name
        except Exception as exc:  # pragma: no cover — defensive
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=16) as ex:
        list(ex.map(worker, range(200)))

    assert not errors, f"concurrent get_platform raised: {errors}"
    assert len(results) == 200


def test_concurrent_detection_platforms_returns_same_list():
    """Reads of detection_platforms() across threads return logically-equal data."""
    snapshots: list[Set[str]] = []
    lock = threading.Lock()

    def worker():
        names = {p.name for p in detection_platforms()}
        with lock:
            snapshots.append(names)

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(snapshots) == 50
    first = snapshots[0]
    for s in snapshots[1:]:
        assert s == first


# ─── Multi-process safety (Platform fields are simple, immutable data) ─────────


def test_platform_fields_are_immutable_primitives():
    """All Platform fields must be primitive/immutable types so a child process
    that imports `platforms` sees the same data without runtime mutation risk.
    """
    p = PLATFORMS["claude"]
    allowed = (str, int, bool, float, tuple, frozenset, type(None), dict, HookProtocol)
    for field in dataclasses.fields(p):
        value = getattr(p, field.name)
        assert isinstance(value, allowed), f"Platform field {field.name!r} = {value!r} is not a process-safe type"
    assert dataclasses.is_dataclass(p.hook_protocol)
    assert p.hook_protocol.__dataclass_params__.frozen


def test_platform_task_metadata_is_immutable():
    """Native task/checklist tool metadata must be safe in one shared daemon."""
    for p in PLATFORMS.values():
        for field_name in (
            "task_create_tools",
            "task_update_tools",
            "task_review_tools",
            "task_bulk_tools",
            "task_plan_tools",
        ):
            assert isinstance(getattr(p, field_name), frozenset)


def test_task_tool_role_uses_platform_native_surfaces():
    from autorun.platforms import is_task_progress_tool, is_task_tool, task_tool_role

    assert task_tool_role("claude", "TaskCreate") == "create"
    assert task_tool_role("claude", "update_plan") is None
    assert task_tool_role("gemini", "write_todos") == "bulk"
    assert task_tool_role("codex", "update_plan") == "plan"
    assert is_task_progress_tool("codex", "update_plan") is True
    assert is_task_tool("codex", "TaskCreate") is False


def test_task_tool_role_infers_unique_tool_when_cli_type_is_missing():
    from autorun.platforms import task_tool_role

    assert task_tool_role(None, "write_todos") == "bulk"
    assert task_tool_role("unknown", "write_todos") == "bulk"
    assert task_tool_role("claude", "write_todos") is None
    assert task_tool_role(None, "update_plan") == "plan"


# ─── Backward-compat aliases derived from PLATFORMS ───────────────────────────


def test_config_aliases_derived_from_platforms():
    """config._CLI_DETECTORS and _KNOWN_CLI_NAMES must derive from PLATFORMS so
    adding a new platform = adding a single Platform() — no parallel maintenance.
    """
    from autorun import config as cfg

    detector_names = {entry[0] for entry in cfg._CLI_DETECTORS}
    expected_detector_names = {p.name for p in detection_platforms()}
    assert detector_names == expected_detector_names

    assert cfg._KNOWN_CLI_NAMES >= set(PLATFORMS.keys())


def test_core_aliases_derived_from_platforms():
    """core.CLI_TOOL_NAMES + INTERNAL_TO_GEMINI / INTERNAL_TO_CLAUDE must match
    PLATFORMS data — no parallel maintenance.
    """
    from autorun import core as core_mod

    # Tool names — both directions
    assert core_mod.CLI_TOOL_NAMES["claude"] == dict(PLATFORMS["claude"].tool_names)
    assert core_mod.CLI_TOOL_NAMES["gemini"] == dict(PLATFORMS["gemini"].tool_names)
    # Event maps
    expected_g2i = dict(PLATFORMS["gemini"].harness_cli_to_autorun_events)
    for k, v in expected_g2i.items():
        assert core_mod.GEMINI_EVENT_MAP.get(k) == v
