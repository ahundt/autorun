"""Read-only capability inventory for autorun harness support.

This module is intentionally side-effect-light: it inspects the registered
platforms, command handlers, and hook chains without installing hooks,
restarting daemons, or writing to user configuration paths.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from . import __commit__, __version__
from .command_docs import command_docs_inventory
from .platforms import PLATFORMS, Platform


def _jsonable_platform(platform: Platform) -> dict[str, Any]:
    """Convert an immutable Platform spec to stable JSON data."""
    return {
        "name": platform.name,
        "display_name": platform.display_name,
        "binary": platform.binary,
        "has_hooks": platform.has_hooks,
        "schema_type": platform.schema_type,
        "config_dir": platform.config_dir,
        "template_dir": platform.template_dir,
        "hooks_path_var": platform.hooks_path_var,
        "install_fn_name": platform.install_fn_name,
        "list_cmd": list(platform.list_cmd),
        "app_bundle_ids": sorted(platform.app_bundle_ids),
        "app_paths": sorted(platform.app_paths),
        "detect_env_vars": sorted(platform.detect_env_vars),
        "detect_session_keys": sorted(platform.detect_session_keys),
        "detect_event_names": sorted(platform.detect_event_names),
        "detect_path_hints": sorted(platform.detect_path_hints),
        "cli_to_internal_events": dict(platform.cli_to_internal_events),
        "internal_to_cli_events": dict(platform.internal_to_cli_events),
        "tool_names": dict(platform.tool_names),
        "native_shell_read_commands": sorted(platform.native_shell_read_commands),
        "task_management_style": platform.task_management_style,
        "task_create_tools": sorted(platform.task_create_tools),
        "task_update_tools": sorted(platform.task_update_tools),
        "task_review_tools": sorted(platform.task_review_tools),
        "task_bulk_tools": sorted(platform.task_bulk_tools),
        "task_plan_tools": sorted(platform.task_plan_tools),
        "command_prefixes": list(platform.command_prefixes),
        "command_display_prefix": platform.command_display_prefix,
        "has_exit2_workaround": platform.has_exit2_workaround,
        "drops_additional_context": platform.drops_additional_context,
        "normal_allow_decision": platform.normal_allow_decision,
        "block_decision": platform.block_decision,
        "supports_additional_context_events": sorted(platform.supports_additional_context_events),
        "unsupported_response_fields_by_event": {
            event: sorted(fields)
            for event, fields in platform.unsupported_response_fields_by_event.items()
        },
    }


def _git_commit() -> str:
    if __commit__ and __commit__ != "unknown":
        return __commit__
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=Path(__file__).resolve().parents[3],
            text=True,
            capture_output=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"


def _handler_name(handler: Any) -> str:
    module = getattr(handler, "__module__", "")
    qualname = getattr(handler, "__qualname__", repr(handler))
    return f"{module}.{qualname}" if module else qualname


def _command_inventory() -> tuple[dict[str, str], dict[str, list[str]]]:
    from . import plugins as _plugins  # noqa: F401 - registers handlers on import
    from .core import app

    commands = {
        alias: _handler_name(handler)
        for alias, handler in sorted(app.command_handlers.items())
    }
    aliases_by_handler: dict[str, list[str]] = {}
    for alias, handler_name in commands.items():
        aliases_by_handler.setdefault(handler_name, []).append(alias)
    return commands, {name: sorted(aliases) for name, aliases in sorted(aliases_by_handler.items())}


def _hook_inventory() -> dict[str, list[str]]:
    from . import plugins as _plugins  # noqa: F401 - registers handlers on import
    from .core import app

    return {
        event: [_handler_name(handler) for handler in handlers]
        for event, handlers in sorted(app.chains.items())
    }


def build_capability_snapshot() -> dict[str, Any]:
    """Return a stable, JSON-serializable autorun capability inventory."""
    commands, command_aliases = _command_inventory()
    hooks = _hook_inventory()
    plugin_root = Path(__file__).resolve().parents[2]
    return {
        "version": __version__,
        "commit": _git_commit(),
        "platforms": {
            name: _jsonable_platform(platform)
            for name, platform in sorted(PLATFORMS.items())
        },
        "commands": commands,
        "command_aliases": command_aliases,
        "command_docs": command_docs_inventory(plugin_root / "commands"),
        "hook_events": hooks,
    }


def write_capability_snapshot(output: str | Path | None = None) -> dict[str, Any]:
    """Write the snapshot to output path or stdout and return the data."""
    snapshot = build_capability_snapshot()
    text = json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
    if output and str(output) != "-":
        Path(output).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write autorun capability snapshot JSON.")
    parser.add_argument("output", nargs="?", default="-", help="Output path, or '-' for stdout")
    args = parser.parse_args(argv)
    write_capability_snapshot(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
