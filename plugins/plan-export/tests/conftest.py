"""
Pytest configuration for plan export race condition tests.

This file provides pytest fixtures and configuration for all tests.
"""

import pytest
import sys
from pathlib import Path

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "clautorun" / "src"))


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "stress: marks tests as stress tests"
    )
    config.addinivalue_line(
        "markers", "race: marks tests as race condition tests"
    )


@pytest.fixture(scope="session")
def test_timeout():
    """Default timeout for test operations."""
    return 10.0


@pytest.fixture(scope="session")
def stress_test_timeout():
    """Extended timeout for stress tests."""
    return 60.0
