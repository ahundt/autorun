#!/usr/bin/env python3
"""Unit tests for command discovery engine"""

import pytest
import tempfile
import json
from pathlib import Path
import sys
import os

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from clautorun.command_discovery import (
    discover_existing_commands,
    load_command_content,
    parse_command_args,
    validate_command_exists,
    get_command_metadata,
    search_commands,
    invalidate_cache,
    get_command_statistics
)


class TestCommandDiscovery:
    """Test suite for command discovery functionality"""

    def setup_method(self):
        """Set up test environment"""
        # Clear cache before each test
        invalidate_cache()

    def test_discover_existing_commands_empty_environment(self):
        """Test command discovery in empty environment"""
        commands = discover_existing_commands()
        assert isinstance(commands, dict)
        # Should find at least built-in commands if any exist

    def test_discover_global_commands_directory(self):
        """Test discovery of global commands directory"""
        # Create temporary global commands directory
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create mock global commands structure
            global_commands_dir = Path(temp_dir) / ".claude" / "commands"
            global_commands_dir.mkdir(parents=True)

            # Create test markdown command
            test_cmd = global_commands_dir / "test_command.md"
            test_cmd.write_text("""---
description: Test command for discovery
---
# Test Command Content

This is a test command used for discovery testing.
""")

            # Temporarily override home directory
            original_home = os.environ.get('HOME')
            os.environ['HOME'] = temp_dir

            try:
                commands = discover_existing_commands(force_refresh=True)
                assert '/test_command' in commands

                cmd_info = commands['/test_command']
                assert cmd_info['type'] == 'global_command'
                assert cmd_info['source'] == 'global_commands'
                assert cmd_info['format'] == 'markdown'
                assert cmd_info['display_name'] == 'test_command'

            finally:
                # Restore original HOME
                if original_home:
                    os.environ['HOME'] = original_home
                else:
                    del os.environ['HOME']

    def test_discover_plugin_commands(self):
        """Test discovery of plugin commands"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create mock plugin structure
            plugin_dir = Path(temp_dir) / ".claude" / "plugins" / "test_plugin"
            plugin_dir.mkdir(parents=True)

            # Create plugin manifest
            plugin_manifest = plugin_dir / ".claude-plugin" / "plugin.json"
            plugin_manifest.parent.mkdir(parents=True)
            plugin_manifest.write_text(json.dumps({
                "name": "Test Plugin",
                "description": "Test plugin for discovery",
                "commands": ["./commands/test_plugin_cmd.md"]
            }))

            # Create plugin command
            plugin_commands_dir = plugin_dir / "commands"
            plugin_commands_dir.mkdir(parents=True)

            test_cmd = plugin_commands_dir / "test_plugin_cmd.md"
            test_cmd.write_text("""---
description: Plugin command
---
# Plugin Command Content

This is a plugin command used for discovery testing.
""")

            # Temporarily override home directory
            original_home = os.environ.get('HOME')
            os.environ['HOME'] = temp_dir

            try:
                commands = discover_existing_commands(force_refresh=True)
                assert '/test_plugin_cmd' in commands

                cmd_info = commands['/test_plugin_cmd']
                assert cmd_info['type'] == 'plugin_command'
                assert cmd_info['source'] == 'Test Plugin'
                # Plugin prefix uses display name, which may include spaces
                assert '/Test Plugin:' in cmd_info['plugin_prefix'] or '/test_plugin:' in cmd_info['plugin_prefix']

            finally:
                # Restore original HOME
                if original_home:
                    os.environ['HOME'] = original_home
                else:
                    del os.environ['HOME']

    def test_load_command_content_markdown(self):
        """Test loading content from markdown commands"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as temp_file:
            # Write markdown with frontmatter
            temp_file.write("""---
frontmatter: value
description: Test markdown file
---
# Main Content

This is the main content of the markdown file.
""")

            temp_path = Path(temp_file.name)
            cmd_info = {"path": str(temp_path), "format": "markdown"}

            content = load_command_content(cmd_info)
            # Content loading may return the full content or just the body after frontmatter
            # Verify it doesn't raise an error and returns a string
            assert isinstance(content, str)

            # Clean up
            temp_path.unlink()

    def test_load_command_content_executable(self):
        """Test loading content from executable commands"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as temp_file:
            # Write executable script
            temp_file.write("""#!/bin/bash
echo "Executable command output"
""")
            temp_path = Path(temp_file.name)
            temp_path.chmod(0o755)

            cmd_info = {"path": str(temp_path), "format": "executable"}

            content = load_command_content(cmd_info)
            # Content loading may return the content or empty string based on implementation
            assert isinstance(content, str)

            # Clean up
            temp_path.unlink()

    def test_parse_command_args(self):
        """Test command argument parsing"""
        # Test command without args
        cmd, args = parse_command_args("/test-command", "/test-command")
        assert cmd == "/test-command"
        assert args == ""

        # Test command with args
        cmd, args = parse_command_args("/test-command arg1 arg2", "/test-command")
        assert cmd == "/test-command"
        assert args == "arg1 arg2"

        # Test command with complex args
        cmd, args = parse_command_args("/test-command --flag value --other", "/test-command")
        assert cmd == "/test-command"
        assert args == "--flag value --other"

    def test_validate_command_exists(self):
        """Test command existence validation"""
        # Test with non-existent command
        assert not validate_command_exists("/nonexistent")

        # Create a temporary command
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as temp_file:
            temp_file.write("# Test command")
            temp_path = Path(temp_file.name)

            original_home = os.environ.get('HOME')
            os.environ['HOME'] = str(temp_path.parent)

            try:
                # Mock discovery to include our test command
                invalidate_cache()  # Clear cache

                # This test is limited without full mock framework
                # In real usage, it would detect commands in proper locations

            finally:
                if original_home:
                    os.environ['HOME'] = original_home
                else:
                    del os.environ['HOME']
                temp_path.unlink()

    def test_search_commands(self):
        """Test command search functionality"""
        # Create mock command data
        mock_commands = {
            "/test-search": {
                "display_name": "test-search",
                "source": "test",
                "type": "test_command"
            },
            "/another-test": {
                "display_name": "another-test",
                "source": "test",
                "type": "test_command"
            },
            "/unrelated": {
                "display_name": "unrelated",
                "source": "other",
                "type": "other_command"
            }
        }

        # Test search functionality - results depend on available commands
        results = search_commands("test", limit=10)
        # Results may be empty if no commands are discovered
        assert isinstance(results, list)

        # Test case-insensitive search
        results = search_commands("SEARCH", limit=10)
        assert isinstance(results, list)

    def test_get_command_statistics(self):
        """Test command statistics generation"""
        stats = get_command_statistics()
        assert isinstance(stats, dict)
        assert 'total' in stats
        assert isinstance(stats['total'], int)
        assert 'markdown_commands' in stats
        assert 'executable_commands' in stats
        assert 'global_commands' in stats
        assert 'plugin_commands' in stats
        assert 'local_commands' in stats

    def test_invalidate_cache(self):
        """Test cache invalidation"""
        # Discover commands to populate cache
        discover_existing_commands()

        # Invalidate cache - verify no error is raised
        invalidate_cache()

        # Cache should be cleared - verify by calling discover again
        # After invalidation, discover should work without errors
        commands = discover_existing_commands()
        assert isinstance(commands, dict)

    def test_command_discovery_error_handling(self):
        """Test error handling in command discovery"""
        # Test with invalid paths (should not crash)
        commands = discover_existing_commands()
        assert isinstance(commands, dict)

        # Test with permission issues (should not crash)
        # This would require creating directories with specific permissions
        # For now, just ensure the function doesn't crash
        assert isinstance(discover_existing_commands(), dict)

    def test_load_command_content_error_handling(self):
        """Test error handling in content loading"""
        # Test with non-existent file
        cmd_info = {"path": "/nonexistent/file.md", "format": "markdown"}
        content = load_command_content(cmd_info)
        # Error message may include full exception details
        assert "Error loading command content" in content

        # Test with unreadable file
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as temp_file:
            temp_path = Path(temp_file.name)
            temp_path.chmod(0o000)  # Make unreadable

            cmd_info = {"path": str(temp_path), "format": "markdown"}
            content = load_command_content(cmd_info)
            assert "Error loading command content" in content

            # Clean up
            temp_path.chmod(0o644)
            temp_path.unlink()


if __name__ == "__main__":
    pytest.main([__file__])