"""Resource access for installed vs source autorun.

Provides path accessors for plugin resources (commands, skills, agents, hooks)
that work for both source repository development and installed package locations.

The plugin root is the directory containing .claude-plugin/, commands/, etc.
For source development, this is plugins/autorun/ in the git repo.
For installed packages, this falls back to the package installation directory.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def get_plugin_root() -> Path:
    """Get plugin root directory (works for both installed and source).

    Search strategy:
    1. Walk up from this file looking for .claude-plugin/marketplace.json
    2. Fall back to parent of src/ directory (standard layout)

    Returns:
        Path to plugin root containing .claude-plugin/, commands/, etc.

    Raises:
        FileNotFoundError: If plugin root cannot be located
    """
    current = Path(__file__).resolve()
    for parent in [current, *current.parents]:
        if (parent / ".claude-plugin" / "marketplace.json").exists():
            return parent

    # Fallback: assume standard layout src/autorun/resources.py -> ../../
    fallback = Path(__file__).resolve().parent.parent.parent
    if (fallback / ".claude-plugin" / "marketplace.json").exists():
        return fallback

    raise FileNotFoundError(
        f"Could not find plugin root (.claude-plugin/marketplace.json) "
        f"from {Path(__file__).resolve()}"
    )


def get_commands_dir() -> Path:
    """Get commands directory."""
    return get_plugin_root() / "commands"


def get_skills_dir() -> Path:
    """Get skills directory."""
    return get_plugin_root() / "skills"


def get_agents_dir() -> Path:
    """Get agents directory."""
    return get_plugin_root() / "agents"


def get_hooks_dir() -> Path:
    """Get hooks directory."""
    return get_plugin_root() / "hooks"
