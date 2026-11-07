"""Native harness events the shared Gemini template has no entry for.

Qwen Code fires SubagentStop, which can block a subagent from finishing
(https://github.com/QwenLM/qwen-code/blob/main/docs/users/features/hooks.md).
Qwen's manifest is translated from the Gemini template, which has no such
event, so Qwen never subscribed and subagent task enforcement never ran there.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import autorun
from autorun.installer.extension import translated_hooks
from autorun.platforms import PLATFORMS, hook_platforms

TEMPLATE = Path(autorun.__file__).parent / "gemini_template"


def _events(platform):
    manifest = translated_hooks(TEMPLATE, platform)
    return manifest[platform.hook_protocol.hook_manifest_container_key]


def test_qwen_subscribes_to_subagent_stop_with_the_stop_handler():
    events = _events(PLATFORMS["qwen"])
    assert "SubagentStop" in events
    assert events["SubagentStop"] == events["Stop"]


@pytest.mark.parametrize("platform", [p for p in hook_platforms() if p.cloned_hook_events], ids=lambda p: p.name)
def test_every_cloned_event_is_native_to_its_harness_and_reaches_its_manifest(platform):
    """A cloned event the harness does not fire costs nothing but misleads;
    one whose model is not installed is silently dropped."""
    native = set(platform.autorun_to_harness_cli_events.values())
    events = _events(platform)
    for event, model in platform.cloned_hook_events.items():
        assert event in native, f"{platform.name} does not fire {event}"
        assert event in events, f"{event} missing from {platform.name}'s manifest"
        assert events[event] == events[model]


def test_a_qwen_subagent_stop_block_is_answered_in_qwens_shape(monkeypatch):
    """Qwen reads SubagentStop's verdict from top-level decision and reason."""
    from autorun.core import EventContext

    monkeypatch.setenv("AUTORUN_CLI_TYPE", "qwen")
    ctx = EventContext(session_id="qwen-subagent", event="SubagentStop", cli_type="qwen")
    response = ctx.respond("block", "finish task 3 first")
    assert response.get("decision") == "block", response
    assert "finish task 3 first" in (response.get("reason") or ""), response


def test_every_event_codex_installs_is_one_the_registry_says_codex_fires():
    """steps._CODEX_EVENTS installed SubagentStop while the Codex registry
    entry did not list it, so the two owners of 'what Codex fires' disagreed."""
    from autorun.installer import steps

    native = set(PLATFORMS["codex"].autorun_to_harness_cli_events.values())
    assert set(steps._CODEX_EVENTS) <= native, set(steps._CODEX_EVENTS) - native
