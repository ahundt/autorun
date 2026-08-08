"""Read-only capability inventory for autorun harness support.

This module is intentionally side-effect-light: it inspects the registered
platforms, command handlers, skill metadata, and hook chains without installing hooks,
restarting daemons, or writing to user configuration paths.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from . import __commit__, __version__
from .command_docs import command_docs_inventory, marketplace_skill_docs_inventory
from .platforms import PLATFORMS, Platform


def _jsonable_platform(platform: Platform) -> dict[str, Any]:
    """Convert an immutable Platform spec to stable JSON data."""
    from .installer.steps import STEPS

    return {
        "name": platform.name,
        "display_name": platform.display_name,
        "binary": platform.binary,
        "install_flavor": platform.install_flavor,
        "has_hooks": platform.has_hooks,
        "schema_type": platform.schema_type,
        "hook_protocol": platform.hook_protocol.name,
        "pretool_decision_location": platform.hook_protocol.pretool_decision_location,
        "supports_ask_decision": platform.hook_protocol.supports_ask_decision,
        "stop_blocking_decision": platform.hook_protocol.stop_blocking_decision,
        "stop_response_uses_only_decision_and_reason": (platform.hook_protocol.stop_response_uses_only_decision_and_reason),
        "requires_json_for_unhandled_hook": (platform.hook_protocol.requires_json_for_unhandled_hook),
        "context_events_with_block_decision": sorted(platform.hook_protocol.context_events_with_block_decision),
        "hook_manifest_container_key": platform.hook_protocol.hook_manifest_container_key,
        "manifest_events_with_flat_handlers": sorted(platform.hook_protocol.manifest_events_with_flat_handlers),
        "manifest_events_without_matchers": sorted(platform.hook_protocol.manifest_events_without_matchers),
        "config_dir": platform.config_dir,
        "template_dir": platform.template_dir,
        "hooks_path_var": platform.hooks_path_var,
        "extension_manifest_name": platform.extension_manifest_name,
        "extension_hooks_at_root": platform.extension_hooks_at_root,
        "generates_toml_commands": platform.generates_toml_commands,
        "install_steps": [step.__name__ for step in STEPS[platform.name]],
        "list_cmd": list(platform.list_cmd),
        "app_bundle_ids": sorted(platform.app_bundle_ids),
        "app_paths": sorted(platform.app_paths),
        "detect_env_vars": sorted(platform.detect_env_vars),
        "detect_session_keys": sorted(platform.detect_session_keys),
        "detect_event_names": sorted(platform.detect_event_names),
        "detect_path_hints": sorted(platform.detect_path_hints),
        "harness_cli_to_autorun_events": dict(platform.harness_cli_to_autorun_events),
        "autorun_to_harness_cli_events": dict(platform.autorun_to_harness_cli_events),
        "tool_names": dict(platform.tool_names),
        "native_shell_read_commands": sorted(platform.native_shell_read_commands),
        "task_management_style": platform.task_management_style,
        "task_create_tools": sorted(platform.task_create_tools),
        "task_update_tools": sorted(platform.task_update_tools),
        "task_review_tools": sorted(platform.task_review_tools),
        "task_bulk_tools": sorted(platform.task_bulk_tools),
        "task_plan_tools": sorted(platform.task_plan_tools),
        "agent_spawn_tools": sorted(platform.agent_spawn_tools),
        "aggregates_conductor_tasks": platform.aggregates_conductor_tasks,
        "policy_commands_arrive_in_transcript": platform.policy_commands_arrive_in_transcript,
        "fingerprint_ignores_session_id": platform.fingerprint_ignores_session_id,
        "command_prefixes": list(platform.command_prefixes),
        "command_display_prefix": platform.command_display_prefix,
        "has_exit2_workaround": platform.has_exit2_workaround,
        "drops_additional_context": platform.drops_additional_context,
        "root_allow_decision": platform.hook_protocol.root_allow_decision,
        "root_block_decision": platform.hook_protocol.root_block_decision,
        "supports_additional_context_events": sorted(platform.supports_additional_context_events),
        "unsupported_response_fields_by_event": {event: sorted(fields) for event, fields in platform.unsupported_response_fields_by_event.items()},
        "command_invocation_hint": platform.command_invocation_hint,
        "skill_invocation_format": platform.skill_invocation_format,
        "task_dependency_syntax": platform.task_dependency_syntax,
        "native_task_statuses": sorted(platform.native_task_statuses),
        "config_dir_env_vars": list(platform.config_dir_env_vars),
        "config_dir_env_var_subdir": platform.config_dir_env_var_subdir,
        "extensions_subdir": platform.extensions_subdir,
        # The route's own words, not a reconstruction from path fragments: a
        # harness whose skills ship in a plugin package and one with no native
        # route at all are both "no subdir", and the snapshot must not present
        # them as the same thing. Destinations are omitted deliberately — they
        # resolve against the running user's home, and a snapshot is meant to
        # be comparable across machines.
        "native_skills": type(platform.native_skills).__name__,
        "native_skills_description": platform.native_skills.describe(),
        # Read tiers, kept distinct from the write destination above. Described
        # rather than resolved for the same reason: comparable across machines.
        "skill_search_routes": [
            route.describe() for route in platform.skill_search_routes
        ],
        "loads_shared_agents_skills": platform.loads_shared_agents_skills,
        "install_by_default": platform.install_by_default,
        "memory_filename": platform.memory_filename,
        "memory_template": platform.memory_template,
        "memory_workaround_flag": platform.memory_workaround_flag,
        "memory_sentinel_slug": platform.memory_sentinel_slug,
        "standalone_session_env_vars": list(platform.standalone_session_env_vars),
        "uninstall_cmd": list(platform.uninstall_cmd),
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

    commands = {alias: _handler_name(handler) for alias, handler in sorted(app.command_handlers.items())}
    aliases_by_handler: dict[str, list[str]] = {}
    for alias, handler_name in commands.items():
        aliases_by_handler.setdefault(handler_name, []).append(alias)
    return commands, {name: sorted(aliases) for name, aliases in sorted(aliases_by_handler.items())}


def _hook_inventory() -> dict[str, list[str]]:
    from . import plugins as _plugins  # noqa: F401 - registers handlers on import
    from .core import app

    return {event: [_handler_name(handler) for handler in handlers] for event, handlers in sorted(app.chains.items())}


def build_capability_snapshot() -> dict[str, Any]:
    """Return a stable, JSON-serializable autorun capability inventory."""
    commands, command_aliases = _command_inventory()
    hooks = _hook_inventory()
    plugin_root = Path(__file__).resolve().parents[2]
    skills, plugin_skills = marketplace_skill_docs_inventory(plugin_root.parent)
    return {
        "version": __version__,
        "commit": _git_commit(),
        "platforms": {name: _jsonable_platform(platform) for name, platform in sorted(PLATFORMS.items())},
        "commands": commands,
        "command_aliases": command_aliases,
        "command_docs": command_docs_inventory(plugin_root / "commands"),
        "skills": skills,
        "plugin_skills": plugin_skills,
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
