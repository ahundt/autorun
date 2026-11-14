"""Cancellation must not turn an unfinished dispatch into permission to proceed."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest

from autorun import client, core


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["PreToolUse", "PostToolUse"])
async def test_cancelled_dispatch_fails_closed(monkeypatch, event):
    daemon = core.AutorunDaemon(core.AutorunApp())
    entered = asyncio.Event()

    async def blocked_dispatch(ctx, cli_type):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(daemon, "_dispatch_with_timeout", blocked_dispatch)
    reader = asyncio.StreamReader()
    reader.feed_data(json.dumps({
        "session_id": "cancelled-dispatch", "cli_type": "claude",
        "hook_event_name": event, "tool_name": "Bash",
        "tool_input": {"command": "rm -rf x"},
    }).encode() + b"\n")
    writer = Mock(drain=AsyncMock(), wait_closed=AsyncMock())
    task = asyncio.create_task(daemon.handle_client(reader, writer))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    for call in writer.write.call_args_list:
        response = json.loads(call.args[0])
        assert response != {}, "unfinished dispatch sent pass-through {}"
        assert "restarting" in json.dumps(response).lower()
        if event == "PreToolUse":
            specific = response["hookSpecificOutput"]
            assert specific["hookEventName"] == "PreToolUse"
            assert specific["permissionDecision"] == "deny"
            assert client.load_guidance() in specific["permissionDecisionReason"]
        else:
            # No hook deadline in this request, so the client is one that prints the
            # answer as is and the private failure field is withheld.
            assert response == core.validate_hook_response(event, response, "claude")
            assert "hookSpecificOutput" not in response
    writer.close.assert_called_once()
    writer.wait_closed.assert_awaited_once()


@pytest.mark.asyncio
async def test_completed_none_dispatch_still_passes_through(monkeypatch):
    daemon = core.AutorunDaemon(core.AutorunApp())
    dispatch = AsyncMock(return_value=None)
    monkeypatch.setattr(daemon, "_dispatch_with_timeout", dispatch)
    reader = asyncio.StreamReader()
    reader.feed_data(json.dumps({
        "session_id": "completed-dispatch", "cli_type": "claude",
        "hook_event_name": "PreToolUse", "tool_name": "Bash",
        "tool_input": {"command": "echo safe"},
    }).encode() + b"\n")
    writer = Mock(drain=AsyncMock(), wait_closed=AsyncMock())

    await daemon.handle_client(reader, writer)

    dispatch.assert_awaited_once()
    writer.write.assert_called_once_with(b"{}\n")
    writer.close.assert_called_once()
    writer.wait_closed.assert_awaited_once()
