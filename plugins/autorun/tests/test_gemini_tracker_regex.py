import os
from pathlib import Path
from autorun.task_lifecycle import TaskLifecycle
from autorun.core import EventContext

def test_gemini_create_task_with_title_and_new_regex(tmp_path):
    # Setup mock context
    ctx = EventContext(
        session_id="test_gemini_tasks",
        event="PostToolUse",
        tool_name="tracker_create_task",
        tool_input={"title": "Test Gemini Task", "description": "native gemini task"},
        tool_result="Created task gemini-123: Test Gemini Task",
        cli_type="gemini"
    )
    
    tl = TaskLifecycle(ctx=ctx)
    tl.handle_task_create(ctx)
    
    tasks = tl.tasks
    # Should have extracted ID gemini-123 from result string and mapped title to subject
    assert "gemini-123" in tasks
    assert tasks["gemini-123"]["subject"] == "Test Gemini Task"
    assert tasks["gemini-123"]["description"] == "native gemini task"
