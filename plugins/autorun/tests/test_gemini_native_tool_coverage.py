#!/usr/bin/env python3
"""Verify Gemini CLI native tool coverage in hook matchers."""
import json
from pathlib import Path

def test_gemini_native_tool_matcher_coverage():
    """Verify that BeforeTool matcher includes Gemini native tools."""
    hooks_file = Path(__file__).parent.parent / "hooks" / "hooks.json"
    
    with open(hooks_file, encoding="utf-8") as f:
        hooks_data = json.load(f)
    
    before_tool_hooks = hooks_data.get("hooks", {}).get("BeforeTool", [])
    assert before_tool_hooks, "No BeforeTool hooks found"
    
    # Find the main pretool hook
    matcher = ""
    for hook_config in before_tool_hooks:
        if any(h.get("name") == "autorun-pretool" for h in hook_config.get("hooks", [])):
            matcher = hook_config.get("matcher", "")
            break
    
    assert matcher, "autorun-pretool matcher not found"
    
    # Check for native Gemini tools
    native_tools = ["read_file", "glob", "grep_search"]
    for tool in native_tools:
        assert tool in matcher, f"Native tool '{tool}' missing from matcher: {matcher}"

def test_after_agent_hook_exists():
    """Verify that AfterAgent hook is configured."""
    hooks_file = Path(__file__).parent.parent / "hooks" / "hooks.json"
    
    with open(hooks_file, encoding="utf-8") as f:
        hooks_data = json.load(f)
    
    after_agent_hooks = hooks_data.get("hooks", {}).get("AfterAgent", [])
    assert after_agent_hooks, "AfterAgent hooks missing from hooks.json"
    
    # Check for autorun-afteragent
    found = False
    for config in after_agent_hooks:
        if any(h.get("name") == "autorun-afteragent" for h in config.get("hooks", [])):
            found = True
            break
    
    assert found, "autorun-afteragent hook not found in AfterAgent section"
