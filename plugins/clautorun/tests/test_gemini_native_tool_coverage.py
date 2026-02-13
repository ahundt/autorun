#!/usr/bin/env python3
"""Verify Gemini CLI native tool coverage in hook matchers."""
import json
from pathlib import Path

def test_gemini_native_tool_matcher_coverage():
    """Verify that BeforeTool matcher includes Gemini native tools."""
    hooks_file = Path(__file__).parent.parent / "hooks" / "gemini-hooks.json"
    
    with open(hooks_file) as f:
        hooks_data = json.load(f)
    
    before_tool_hooks = hooks_data.get("hooks", {}).get("BeforeTool", [])
    assert before_tool_hooks, "No BeforeTool hooks found"
    
    # Find the main pretool hook
    matcher = ""
    for hook_config in before_tool_hooks:
        if any(h.get("name") == "clautorun-pretool" for h in hook_config.get("hooks", [])):
            matcher = hook_config.get("matcher", "")
            break
    
    assert matcher, "clautorun-pretool matcher not found"
    
    # Check for native Gemini tools
    native_tools = ["read_file", "glob", "grep_search"]
    for tool in native_tools:
        assert tool in matcher, f"Native tool '{tool}' missing from matcher: {matcher}"

def test_after_agent_hook_exists():
    """Verify that AfterAgent hook is configured."""
    hooks_file = Path(__file__).parent.parent / "hooks" / "gemini-hooks.json"
    
    with open(hooks_file) as f:
        hooks_data = json.load(f)
    
    after_agent_hooks = hooks_data.get("hooks", {}).get("AfterAgent", [])
    assert after_agent_hooks, "AfterAgent hooks missing from gemini-hooks.json"
    
    # Check for clautorun-afteragent
    found = False
    for config in after_agent_hooks:
        if any(h.get("name") == "clautorun-afteragent" for h in config.get("hooks", [])):
            found = True
            break
    
    assert found, "clautorun-afteragent hook not found in AfterAgent section"
