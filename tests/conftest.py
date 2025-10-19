#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pytest configuration and fixtures for clautorun testing
"""
import pytest
import tempfile
import shutil
from pathlib import Path
import sys
import json

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from clautorun.main import CONFIG, COMMAND_HANDLERS


@pytest.fixture
def temp_session_dir():
    """Create a temporary directory for session storage"""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)


@pytest.fixture
def mock_session_state(temp_session_dir):
    """Create a mock session state for testing"""
    # Import the module properly to avoid naming conflicts
    import clautorun.main as main_module

    # Store original value
    original_state_dir = main_module.STATE_DIR

    # Set the temporary directory
    main_module.STATE_DIR = temp_session_dir

    yield

    # Restore original STATE_DIR
    main_module.STATE_DIR = original_state_dir


@pytest.fixture
def sample_commands():
    """Sample commands for testing"""
    return {
        "policy_commands": ["/afs", "/afa", "/afj", "/afst"],
        "control_commands": ["/autostop", "/estop"],
        "normal_commands": ["help me", "what is this", "test file"],
        "autorun_command": "/autorun test task description"
    }


@pytest.fixture
def expected_responses():
    """Expected responses for commands"""
    return {
        "/afs": "AutoFile policy: strict-search - STRICT SEARCH: ONLY modify existing files. Use Glob/Grep. NO new files.",
        "/afa": "AutoFile policy: allow-all - ALLOW ALL: Full permission to create/modify files.",
        "/afj": "AutoFile policy: justify-create - JUSTIFIED: Search existing first. Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> for new files.",
        "/afst": "Current policy: allow-all",
        "/autostop": "Autorun stopped",
        "/estop": "Emergency stop activated"
    }


@pytest.fixture
def plugin_input_data():
    """Sample input data for plugin testing"""
    return {
        "prompt": "/afs",
        "session_id": "test_session",
        "session_transcript": []
    }


@pytest.fixture
def hook_input_data():
    """Sample input data for hook testing"""
    return {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "test_session",
        "prompt": "/afa",
        "session_transcript": []
    }