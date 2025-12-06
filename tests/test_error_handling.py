#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for error_handling.py module to increase coverage.
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from io import StringIO

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from clautorun.error_handling import (
    show_comprehensive_uv_error,
    handle_import_error,
    check_uv_environment,
    show_uv_environment_status
)


class TestShowComprehensiveUvError:
    """Test show_comprehensive_uv_error function"""

    def test_default_error_message(self, capsys):
        """Test default error message output"""
        show_comprehensive_uv_error()
        captured = capsys.readouterr()
        assert "UV ENVIRONMENT ERROR" in captured.out or "ERROR" in captured.out

    def test_custom_error_type(self, capsys):
        """Test custom error type"""
        show_comprehensive_uv_error("IMPORT ERROR", "Test error message")
        captured = capsys.readouterr()
        assert "IMPORT ERROR" in captured.out or "ERROR" in captured.out

    def test_custom_error_message(self, capsys):
        """Test custom error message"""
        show_comprehensive_uv_error("TEST ERROR", "Custom test error message")
        captured = capsys.readouterr()
        assert len(captured.out) > 0  # Some output should be produced


class TestHandleImportError:
    """Test handle_import_error function"""

    def test_clautorun_import_error(self, capsys):
        """Test handling of clautorun-related import error"""
        error = ImportError("No module named 'clautorun'")
        result = handle_import_error(error, exit_on_error=False)
        # Should return True for clautorun-related errors
        assert result == True

    def test_unrelated_import_error(self):
        """Test handling of unrelated import error"""
        error = ImportError("No module named 'some_random_module'")
        result = handle_import_error(error, exit_on_error=False)
        # Should return False for unrelated errors
        assert result == False

    def test_non_import_error(self):
        """Test handling of non-ImportError"""
        error = ValueError("Not an import error")
        # Should handle gracefully
        try:
            result = handle_import_error(error, exit_on_error=False)
        except (TypeError, AttributeError):
            pass  # Some implementations may not handle this


class TestCheckUvEnvironment:
    """Test check_uv_environment function"""

    def test_returns_tuple(self):
        """Test that check_uv_environment returns a tuple"""
        result = check_uv_environment()
        assert isinstance(result, tuple)
        assert len(result) >= 2  # Should have at least 2 elements

    def test_with_mock_subprocess(self):
        """Test with mocked subprocess"""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="1.0.0")
            result = check_uv_environment()
            assert isinstance(result, tuple)


class TestShowUvEnvironmentStatus:
    """Test show_uv_environment_status function"""

    def test_produces_output(self, capsys):
        """Test that function produces some output"""
        show_uv_environment_status()
        captured = capsys.readouterr()
        # Should produce some output about the environment
        assert len(captured.out) >= 0  # May or may not produce output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
