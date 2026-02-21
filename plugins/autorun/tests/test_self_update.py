"""Test self-update mechanism.

Test Coverage:
- check_for_updates() detects current and latest versions
- UpdateStrategy.detect() identifies installation method
- perform_self_update() executes update via correct pathway

TDD Methodology:
- RED: These tests will FAIL until we implement the update mechanism
- GREEN: Implement minimal code to make tests pass
- REFACTOR: Clean up while keeping tests green
"""
import subprocess
from unittest.mock import patch, MagicMock, mock_open
import json
import pytest


class TestCheckForUpdates:
    """Test update detection logic."""

    def test_check_for_updates_detects_current_version(self):
        """Test: check_for_updates() reads current version from importlib.metadata.

        AAA Pattern:
        - Arrange: Mock importlib.metadata.version to return known version
        - Act: Call check_for_updates()
        - Assert: Returns current version correctly
        """
        # Arrange
        mock_urlopen = MagicMock()
        mock_urlopen.__enter__.return_value.read.return_value = json.dumps({
            "tag_name": "v0.9.0"
        }).encode()

        with patch("importlib.metadata.version", return_value="0.7.0"):
            with patch("urllib.request.urlopen", return_value=mock_urlopen):
                # Act
                from autorun.install import check_for_updates
                update_available, current, latest = check_for_updates()

                # Assert
                assert current == "0.7.0"
                assert latest == "0.9.0"
                assert update_available is True  # 0.9.0 > 0.7.0

    def test_check_for_updates_when_already_latest(self):
        """Test: check_for_updates() returns False when already on latest version."""
        # Arrange
        mock_urlopen = MagicMock()
        mock_urlopen.__enter__.return_value.read.return_value = json.dumps({
            "tag_name": "v0.9.0"
        }).encode()

        with patch("importlib.metadata.version", return_value="0.9.0"):
            with patch("urllib.request.urlopen", return_value=mock_urlopen):
                # Act
                from autorun.install import check_for_updates
                update_available, current, latest = check_for_updates()

                # Assert
                assert current == "0.9.0"
                assert latest == "0.9.0"
                assert update_available is False

    def test_check_for_updates_handles_network_failure(self):
        """Test: check_for_updates() handles network errors gracefully."""
        # Arrange
        import urllib.error

        with patch("importlib.metadata.version", return_value="0.9.0"):
            with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Network error")):
                # Act
                from autorun.install import check_for_updates
                update_available, current, latest = check_for_updates()

                # Assert
                assert update_available is False
                assert current == "0.9.0"
                assert latest == "unknown"

    def test_check_for_updates_handles_missing_package(self):
        """Test: check_for_updates() handles package not installed."""
        # Arrange
        from importlib.metadata import PackageNotFoundError

        with patch("importlib.metadata.version", side_effect=PackageNotFoundError()):
            # Act
            from autorun.install import check_for_updates
            update_available, current, latest = check_for_updates()

            # Assert
            assert update_available is False
            assert current == "unknown"
            assert latest == "unknown"


class TestUpdateStrategyDetection:
    """Test UpdateStrategy.detect() auto-detection logic."""

    def test_update_strategy_detects_aix(self):
        """Test: UpdateStrategy.detect() prefers AIX when available.

        AIX has highest priority for updates.
        """
        # Arrange
        with patch("autorun.install.detect_aix_installed", return_value=True):
            # Act
            from autorun.install import UpdateStrategy
            strategy = UpdateStrategy.detect()

            # Assert
            assert strategy.method == "aix"
            assert strategy.cli is None

    def test_update_strategy_detects_claude_plugin(self):
        """Test: UpdateStrategy.detect() detects Claude Code plugin install."""
        # Arrange
        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.output = "autorun (installed)"

        with patch("autorun.install.detect_aix_installed", return_value=False):
            with patch("shutil.which", side_effect=lambda x: "/usr/bin/claude" if x == "claude" else None):
                with patch("autorun.install.run_cmd", return_value=mock_result):
                    # Act
                    from autorun.install import UpdateStrategy
                    strategy = UpdateStrategy.detect()

                    # Assert
                    assert strategy.method == "plugin"
                    assert strategy.cli == "claude"

    def test_update_strategy_detects_gemini_plugin(self):
        """Test: UpdateStrategy.detect() detects Gemini CLI plugin install."""
        # Arrange
        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.output = "autorun-workspace@0.9.0"

        with patch("autorun.install.detect_aix_installed", return_value=False):
            with patch("shutil.which", side_effect=lambda x: "/usr/bin/gemini" if x == "gemini" else None):
                with patch("autorun.install.run_cmd", return_value=mock_result):
                    # Act
                    from autorun.install import UpdateStrategy
                    strategy = UpdateStrategy.detect()

                    # Assert
                    assert strategy.method == "plugin"
                    assert strategy.cli == "gemini"

    def test_update_strategy_falls_back_to_uv(self):
        """Test: UpdateStrategy.detect() falls back to UV when plugins not found."""
        # Arrange
        with patch("autorun.install.detect_aix_installed", return_value=False):
            with patch("shutil.which", return_value=None):  # No CLIs
                with patch("autorun.install.has_uv", return_value=True):
                    # Act
                    from autorun.install import UpdateStrategy
                    strategy = UpdateStrategy.detect()

                    # Assert
                    assert strategy.method == "uv"
                    assert strategy.cli is None

    def test_update_strategy_falls_back_to_pip(self):
        """Test: UpdateStrategy.detect() falls back to pip when UV unavailable."""
        # Arrange
        with patch("autorun.install.detect_aix_installed", return_value=False):
            with patch("shutil.which", return_value=None):
                with patch("autorun.install.has_uv", return_value=False):
                    # Act
                    from autorun.install import UpdateStrategy
                    strategy = UpdateStrategy.detect()

                    # Assert
                    assert strategy.method == "pip"
                    assert strategy.cli is None


class TestPerformSelfUpdate:
    """Test perform_self_update() execution logic."""

    def test_perform_self_update_skips_when_already_latest(self):
        """Test: perform_self_update() returns early when already on latest version."""
        # Arrange
        with patch("autorun.install.check_for_updates", return_value=(False, "0.9.0", "0.9.0")):
            # Act
            from autorun.install import perform_self_update
            result = perform_self_update(method="auto")

            # Assert
            assert result.ok is True
            assert "Already on latest version" in result.output
            assert "0.9.0" in result.output

    def test_perform_self_update_via_aix(self):
        """Test: perform_self_update() executes aix skills update."""
        # Arrange
        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.output = "Updated autorun to 0.10.0"

        with patch("autorun.install.check_for_updates", return_value=(True, "0.9.0", "0.10.0")):
            with patch("autorun.install.run_cmd", return_value=mock_result) as mock_run:
                # Act
                from autorun.install import perform_self_update
                result = perform_self_update(method="aix")

                # Assert
                assert result.ok is True
                mock_run.assert_called_once_with(
                    ["aix", "skills", "update", "autorun"],
                    timeout=120
                )

    def test_perform_self_update_via_uv(self):
        """Test: perform_self_update() executes UV update + re-register."""
        # Arrange
        mock_install_result = MagicMock()
        mock_install_result.ok = True
        mock_install_result.output = "Installed autorun 0.10.0"

        mock_register_result = MagicMock()
        mock_register_result.ok = True
        mock_register_result.output = "Registered plugins"

        with patch("autorun.install.check_for_updates", return_value=(True, "0.9.0", "0.10.0")):
            with patch("autorun.install.run_cmd", side_effect=[mock_install_result, mock_register_result]) as mock_run:
                with patch("autorun.install.get_python_runner", return_value=["uv", "run", "python"]):
                    # Act
                    from autorun.install import perform_self_update
                    result = perform_self_update(method="uv")

                    # Assert
                    assert result.ok is True
                    # Should call uv pip install + re-register
                    assert mock_run.call_count == 2

    def test_perform_self_update_via_pip(self):
        """Test: perform_self_update() executes pip update + re-register."""
        # Arrange
        mock_install_result = MagicMock()
        mock_install_result.ok = True

        mock_register_result = MagicMock()
        mock_register_result.ok = True

        with patch("autorun.install.check_for_updates", return_value=(True, "0.9.0", "0.10.0")):
            with patch("autorun.install.run_cmd", side_effect=[mock_install_result, mock_register_result]) as mock_run:
                # Act
                from autorun.install import perform_self_update
                result = perform_self_update(method="pip")

                # Assert
                assert result.ok is True
                # Should call pip install + re-register
                assert mock_run.call_count == 2

    def test_perform_self_update_auto_detects_method(self):
        """Test: perform_self_update() auto-detects installation method when method='auto'."""
        # Arrange
        mock_strategy = MagicMock()
        mock_strategy.method = "aix"
        mock_strategy.cli = None

        mock_result = MagicMock()
        mock_result.ok = True

        with patch("autorun.install.check_for_updates", return_value=(True, "0.9.0", "0.10.0")):
            with patch("autorun.install.UpdateStrategy.detect", return_value=mock_strategy):
                with patch("autorun.install.run_cmd", return_value=mock_result):
                    # Act
                    from autorun.install import perform_self_update
                    result = perform_self_update(method="auto")

                    # Assert
                    assert result.ok is True


class TestUpdateStrategyDataclass:
    """Test UpdateStrategy dataclass structure."""

    def test_update_strategy_is_frozen(self):
        """Test: UpdateStrategy is immutable (frozen dataclass)."""
        from autorun.install import UpdateStrategy

        strategy = UpdateStrategy(method="uv", cli=None)
        with pytest.raises((AttributeError, Exception)):
            strategy.method = "pip"  # Should fail - frozen dataclass

    def test_update_strategy_has_required_fields(self):
        """Test: UpdateStrategy has method and cli fields."""
        from autorun.install import UpdateStrategy

        strategy = UpdateStrategy(method="plugin", cli="claude")
        assert strategy.method == "plugin"
        assert strategy.cli == "claude"

        strategy2 = UpdateStrategy(method="uv", cli=None)
        assert strategy2.method == "uv"
        assert strategy2.cli is None
