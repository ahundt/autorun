"""Test UV-first with pip fallback behavior.

Test Coverage:
- has_uv() caching and detection
- get_python_runner() returns correct command based on UV availability
- ErrorFormatter produces UV-first error messages with pip fallback

TDD Methodology:
- RED: These tests will FAIL until we implement the functions
- GREEN: Implement minimal code to make tests pass
- REFACTOR: Clean up while keeping tests green
"""
import shutil
from unittest.mock import patch, MagicMock
import pytest


class TestUVPipFallback:
    """Test that pip fallback works when UV unavailable."""

    def test_has_uv_returns_true_when_available(self):
        """Test: has_uv() returns True when UV is in PATH.

        AAA Pattern:
        - Arrange: Mock shutil.which to return UV path
        - Act: Call has_uv()
        - Assert: Returns True
        """
        # Arrange
        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/uv"

            # Act
            from clautorun.install import has_uv
            has_uv.cache_clear()  # Clear lru_cache for test isolation
            result = has_uv()

            # Assert
            assert result is True
            mock_which.assert_called_once_with("uv")

    def test_has_uv_returns_false_when_unavailable(self):
        """Test: has_uv() returns False when UV not in PATH.

        AAA Pattern:
        - Arrange: Mock shutil.which to return None
        - Act: Call has_uv()
        - Assert: Returns False
        """
        # Arrange
        with patch("shutil.which") as mock_which:
            mock_which.return_value = None  # UV not found

            # Act
            from clautorun.install import has_uv
            has_uv.cache_clear()  # Clear lru_cache for test isolation
            result = has_uv()

            # Assert
            assert result is False
            mock_which.assert_called_once_with("uv")

    def test_has_uv_caches_result(self):
        """Test: has_uv() caches result via @lru_cache."""
        # Arrange
        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/uv"

            # Act
            from clautorun.install import has_uv
            has_uv.cache_clear()
            result1 = has_uv()
            result2 = has_uv()  # Should use cached result

            # Assert
            assert result1 is True
            assert result2 is True
            # shutil.which should only be called once due to caching
            assert mock_which.call_count == 1

    def test_get_python_runner_with_uv_available(self):
        """Test: get_python_runner() returns UV command when available."""
        # Arrange
        with patch("clautorun.install.has_uv", return_value=True):
            # Act
            from clautorun.install import get_python_runner
            result = get_python_runner()

            # Assert
            assert result == ["uv", "run", "python"]

    def test_get_python_runner_with_uv_unavailable(self):
        """Test: get_python_runner() falls back to python when UV unavailable."""
        # Arrange
        with patch("clautorun.install.has_uv", return_value=False):
            # Act
            from clautorun.install import get_python_runner
            result = get_python_runner()

            # Assert
            assert result == ["python"]

    def test_error_formatter_marketplace_not_found_with_uv(self):
        """Test: ErrorFormatter.marketplace_not_found() includes UV commands when UV available."""
        # Arrange
        with patch("clautorun.install.has_uv", return_value=True):
            # Act
            from clautorun.install import ErrorFormatter
            error_msg = ErrorFormatter.marketplace_not_found()

            # Assert
            assert "uv run python" in error_msg
            assert "pip install" in error_msg  # Fallback also shown
            assert "Option 1:" in error_msg
            assert "Option 2:" in error_msg
            assert "Option 3:" in error_msg
            assert "TROUBLESHOOTING" in error_msg

    def test_error_formatter_marketplace_not_found_without_uv(self):
        """Test: ErrorFormatter.marketplace_not_found() uses pip command when UV unavailable."""
        # Arrange
        with patch("clautorun.install.has_uv", return_value=False):
            # Act
            from clautorun.install import ErrorFormatter
            error_msg = ErrorFormatter.marketplace_not_found()

            # Assert
            assert "python -m plugins.clautorun.src.clautorun.install" in error_msg
            assert "pip install" in error_msg
            # Should NOT contain uv commands when UV not available
            assert error_msg.count("uv run python") == 0 or "If UV not available" in error_msg

    def test_error_formatter_uv_not_found(self):
        """Test: ErrorFormatter.uv_not_found() provides installation instructions."""
        # Arrange
        pip_fallback = "pip install -e . && python -m clautorun --install"

        # Act
        from clautorun.install import ErrorFormatter
        error_msg = ErrorFormatter.uv_not_found(pip_fallback)

        # Assert
        assert "UV not found in PATH" in error_msg
        assert "INSTALL UV" in error_msg
        assert "curl -LsSf https://astral.sh/uv/install.sh" in error_msg
        assert "powershell" in error_msg  # Windows install
        assert "brew install uv" in error_msg  # Homebrew
        assert pip_fallback in error_msg
        assert "https://docs.astral.sh/uv" in error_msg


class TestErrorFormatterStructure:
    """Test ErrorFormatter dataclass structure and immutability."""

    def test_error_formatter_is_frozen(self):
        """Test: ErrorFormatter is immutable (frozen dataclass)."""
        from clautorun.install import ErrorFormatter

        # Should not be able to set attributes on class instance
        formatter = ErrorFormatter()
        with pytest.raises(AttributeError):
            formatter.new_attribute = "should fail"  # type: ignore

    def test_error_formatter_has_required_templates(self):
        """Test: ErrorFormatter has all required error message templates."""
        from clautorun.install import ErrorFormatter

        # Check that templates exist as class attributes
        assert hasattr(ErrorFormatter, "MARKETPLACE_NOT_FOUND")
        assert hasattr(ErrorFormatter, "UV_NOT_FOUND")
        assert isinstance(ErrorFormatter.MARKETPLACE_NOT_FOUND, str)
        assert isinstance(ErrorFormatter.UV_NOT_FOUND, str)

    def test_error_formatter_marketplace_template_has_placeholders(self):
        """Test: MARKETPLACE_NOT_FOUND template has {install_command} placeholder."""
        from clautorun.install import ErrorFormatter

        # Template should contain placeholder
        assert "{install_command}" in ErrorFormatter.MARKETPLACE_NOT_FOUND

    def test_error_formatter_uv_template_has_placeholders(self):
        """Test: UV_NOT_FOUND template has {pip_fallback_command} placeholder."""
        from clautorun.install import ErrorFormatter

        # Template should contain placeholder
        assert "{pip_fallback_command}" in ErrorFormatter.UV_NOT_FOUND
