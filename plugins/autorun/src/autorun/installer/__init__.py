"""Autorun's installer: what to write, where, and whether it may be written.

Read ``AGENTS.md`` in this directory first — it explains the shape in a page.
This module only re-exports the public surface so callers import one name.

The layering, shortest first:

    fs          the only code that mutates a tree; every write records what it wrote
    traversal   one walk serving status, dry run, install, uninstall and prune
    discovery   where autorun installs from, and where each plugin lives in it
    settings    one declaration per setting, driving resolution, help and parsers
    skills      which skills reach which harness, by which route
    memory      autorun's region inside a memory file the user owns
    runtime     how uv is invoked, and what it says about the interpreter
    harness     pure text translations into each harness's file format
    codex       Codex's hooks file, which one stray key disables entirely
    extension   materializing a Gemini-family extension directory
"""

from __future__ import annotations

from .discovery import marketplace_root, plugin_dir, resolve_plugins
from .fs import (
    Decision,
    Verdict,
    decide,
    json_document,
    publish_files,
    publish_tree,
    read_marker,
    withdraw_files,
    withdrawn,
)
from .settings import INSTALL_SETTINGS, build_parser, resolve_all
from .traversal import Context, Intent, Mode, Target, report, run, targets

__all__ = [
    # the walk
    "Context", "Intent", "Mode", "Target", "run", "report", "targets",
    # decisions and the only mutating primitives
    "Decision", "Verdict", "decide",
    "publish_tree", "publish_files", "withdrawn", "withdraw_files",
    "read_marker", "json_document",
    # where things are
    "marketplace_root", "plugin_dir", "resolve_plugins",
    # how the user configures it
    "INSTALL_SETTINGS", "build_parser", "resolve_all",
]
