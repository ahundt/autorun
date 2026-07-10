"""Single source of truth for AI coding CLI platforms supported by autorun.

Adding a new CLI is one Platform(...) definition plus (optionally) one
install function. All cli_type-aware code paths (detection, event maps,
tool names, schema validation, bug-workaround applicability, install
templates) read from this registry instead of holding parallel tables.

Thread-safety:
    Platform is a frozen+slots dataclass — fields cannot mutate after
    construction. PLATFORMS is read-only after module import (register
    raises on duplicate insertion).

Multi-process safety:
    All fields are immutable primitives (str, tuple, frozenset, bool) or
    plain dict (used for ordered mappings; treated as read-only).
    Child processes that import this module observe identical data.

Multi-session safety:
    No session-scoped data lives on Platform; session state belongs in
    EventContext / SessionStateManager.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


# Click/Typer-style decorator helper module: the @register() pattern keeps
# definitions terse while behavior stays in dispatch sites (which look up
# attributes by name instead of branching on cli_type).


@dataclass(frozen=True, slots=True)
class Platform:
    """Immutable specification for one AI coding CLI platform.

    Fields are grouped: identity, detection, event mapping, tool names,
    native task/checklist behavior, schema/bug applicability, install metadata.
    """

    # === Identity ===
    name: str
    display_name: str
    binary: str  # for shutil.which() probes

    # === Detection (used by config.detect_cli_type) ===
    detect_env_vars: tuple[str, ...] = ()
    detect_session_keys: tuple[str, ...] = ()
    detect_event_names: frozenset[str] = field(default_factory=frozenset)
    detect_path_hints: tuple[str, ...] = ()

    # === Event normalization (cli name → internal name) ===
    # Empty dict = identity (Claude / Codex use canonical names directly).
    cli_to_internal_events: Mapping[str, str] = field(default_factory=dict)
    internal_to_cli_events: Mapping[str, str] = field(default_factory=dict)

    # === Tool name resolution (logical key → API tool_name) ===
    tool_names: Mapping[str, str] = field(default_factory=dict)

    # === Native shell affordances ===
    # Some harnesses expose no dedicated file-read model tool. For those,
    # autorun allows bounded read-only shell inspection commands while still
    # blocking shell writes and unbounded follow-style reads.
    native_shell_read_commands: frozenset[str] = field(default_factory=frozenset)

    # === Native task/checklist tools ===
    # Different harnesses expose task progress differently:
    # - Claude has first-class TaskCreate/TaskUpdate/TaskList tools.
    # - Gemini may expose tracker_* tools or one write_todos bulk state tool.
    # - Codex exposes update_plan, a checklist/progress tool, not Plan Mode.
    task_management_style: str = "none"  # "task_tools" | "bulk_todos" | "plan_checklist" | "none"
    task_create_tools: frozenset[str] = field(default_factory=frozenset)
    task_update_tools: frozenset[str] = field(default_factory=frozenset)
    task_review_tools: frozenset[str] = field(default_factory=frozenset)
    task_bulk_tools: frozenset[str] = field(default_factory=frozenset)
    task_plan_tools: frozenset[str] = field(default_factory=frozenset)

    # === Autorun prompt commands ===
    # Autorun command handlers are registered in canonical /ar:* form. Some
    # harnesses accept additional prompt-hook spellings; dispatch normalizes any
    # listed prefix back to /ar:* before handlers run.
    command_prefixes: tuple[str, ...] = ("/ar:",)
    command_display_prefix: str = "/ar:"

    # === Hook capability ===
    has_hooks: bool = True
    schema_type: str = "strict"   # "strict" | "permissive" | "none"

    # === Bug workaround applicability ===
    has_exit2_workaround: bool = False     # Claude #4669
    drops_additional_context: bool = False  # Claude #18534

    # === Hook response capability metadata ===
    # "approve" is a Claude legacy allow shape. Codex rejects it on events such
    # as UserPromptSubmit, so strict platforms cannot share one schema without validation.
    normal_allow_decision: str | None = "approve"
    block_decision: str = "block"
    supports_additional_context_events: frozenset[str] = field(default_factory=frozenset)
    unsupported_response_fields_by_event: Mapping[str, frozenset[str]] = field(default_factory=dict)

    # === Install metadata ===
    config_dir: str = ""
    template_dir: str | None = None
    hooks_path_var: str = ""
    install_fn_name: str = ""
    list_cmd: tuple[str, ...] = ()
    app_bundle_ids: tuple[str, ...] = ()
    app_paths: tuple[str, ...] = ()


# === Registry ==============================================================
# Module-level dict — declaration order = detection priority.
PLATFORMS: dict[str, Platform] = {}

CUSTOM_HARNESS_FLAVOR_ALIASES = {
    "gemini": "gemini",
    "qwen": "qwen",
    "codex": "codex",
    "agy": "antigravity",
    "antigravity": "antigravity",
}
CUSTOM_HARNESS_FLAVOR_ORDER = ("gemini", "qwen", "antigravity", "agy", "codex")
CUSTOM_HARNESS_SPEC_FORMAT = "name=flavor:binary:config_dir[::display]"


def custom_harness_spec_help() -> str:
    """Return parser help for custom harness specs from one shared source."""
    flavors = "|".join(CUSTOM_HARNESS_FLAVOR_ORDER)
    return (
        "Custom harness target. Use with --install --custom-harness SPEC to "
        "install, or --status --custom-harness SPEC to inspect.\n"
        f"SPEC format: {CUSTOM_HARNESS_SPEC_FORMAT}; "
        f"flavor: {flavors} (agy is an alias for antigravity); "
        "binary is the CLI executable; config_dir is the harness config root; "
        "display is optional. Use ::display as the unambiguous separator. "
        "Repeat for multiple targets."
    )


def register(platform: Platform) -> Platform:
    """Click/Typer-style helper: register a Platform, return it for chaining."""
    if platform.name in PLATFORMS:
        raise ValueError(f"Platform {platform.name!r} already registered")
    PLATFORMS[platform.name] = platform
    return platform


# === Tool-name tables ======================================================
# Kept inline so PLATFORMS and core.CLI_TOOL_NAMES stay in lockstep.
_CLAUDE_TOOLS = {
    "grep": "Grep", "glob": "Glob", "read": "Read",
    "write": "Write", "edit": "Edit", "bash": "Bash", "ls": "LS",
    "task_create": "TaskCreate", "task_update": "TaskUpdate",
    "task_list": "TaskList",
    "task_progress": "TaskUpdate",
    "task_title": "subject", "task_id_param": "taskId",
}

_GEMINI_TOOLS = {
    "grep": "grep_search", "glob": "glob", "read": "read_file",
    "write": "write_file", "edit": "replace", "bash": "run_shell_command",
    "ls": "list_directory",
    "task_create": "tracker_create_task",
    "task_update": "tracker_update_task",
    "task_list": "tracker_list_tasks",
    "task_progress": "write_todos",
    "task_title": "title", "task_id_param": "id",
}

# Codex hook events use Claude-like shell/edit matcher names, but the current
# Codex model-facing tool surface does not expose Claude-style Grep/Glob/Read/
# Write tools. Keep suggestions pointed at the shell inspection/search commands
# and apply_patch path Codex can actually use.
_CODEX_TOOLS = dict(_CLAUDE_TOOLS)
_CODEX_TOOLS.update({
    "grep": "`rg -n` shell search",
    "glob": "`rg --files` shell listing",
    "read": "shell file inspection",
    "write": "apply_patch",
    "edit": "apply_patch",
    "task_progress": "update_plan",
})


# === Platform definitions ==================================================
# IMPORTANT: order matters for detection priority. Claude is the default
# fallback (registered first so the canonical Claude data structures exist
# first; detection_platforms() filters it out).

CLAUDE = register(Platform(
    name="claude",
    display_name="Claude Code",
    binary="claude",
    has_hooks=True,
    schema_type="strict",
    has_exit2_workaround=True,
    drops_additional_context=True,
    config_dir="~/.claude/",
    template_dir=None,                       # hooks live at plugin root
    hooks_path_var="${CLAUDE_PLUGIN_ROOT}",
    install_fn_name="_install_for_claude",
    list_cmd=("claude", "plugin", "list"),
    app_bundle_ids=("com.anthropic.claudefordesktop",),
    app_paths=("/Applications/Claude.app",),
    tool_names=_CLAUDE_TOOLS,
    task_management_style="task_tools",
    task_create_tools=frozenset({"TaskCreate"}),
    task_update_tools=frozenset({"TaskUpdate"}),
    task_review_tools=frozenset({"TaskList", "TaskGet"}),
    normal_allow_decision="approve",
    block_decision="block",
    supports_additional_context_events=frozenset({"UserPromptSubmit", "PostToolUse"}),
    # event maps left empty: Claude events are canonical (identity).
    internal_to_cli_events={
        "PreToolUse": "PreToolUse", "PostToolUse": "PostToolUse",
        "UserPromptSubmit": "UserPromptSubmit", "Stop": "Stop",
        "SessionStart": "SessionStart", "SessionEnd": "SessionEnd",
        "BeforeModel": "BeforeModel", "AfterModel": "AfterModel",
    },
))


GEMINI = register(Platform(
    name="gemini",
    display_name="Gemini CLI",
    binary="gemini",
    detect_env_vars=("GEMINI_SESSION_ID", "GEMINI_PROJECT_DIR", "GEMINI_CLI"),
    detect_session_keys=("GEMINI_SESSION_ID", "sessionId", "session_id"),
    detect_event_names=frozenset({
        "BeforeTool", "AfterTool", "BeforeAgent", "AfterAgent",
        "BeforeModel", "AfterModel", "BeforeToolSelection",
    }),
    detect_path_hints=(".gemini",),
    cli_to_internal_events={
        "BeforeTool": "PreToolUse",
        "AfterTool": "PostToolUse",
        "BeforeAgent": "UserPromptSubmit",
        "AfterAgent": "Stop",
        "SessionStart": "SessionStart",
        "SessionEnd": "SessionEnd",
        "BeforeModel": "BeforeModel",
        "AfterModel": "AfterModel",
        "PreCompress": "PreCompress",
    },
    internal_to_cli_events={
        "PreToolUse": "BeforeTool",
        "PostToolUse": "AfterTool",
        "UserPromptSubmit": "BeforeAgent",
        "Stop": "AfterAgent",
        "SessionStart": "SessionStart",
        "SessionEnd": "SessionEnd",
        "BeforeModel": "BeforeModel",
        "AfterModel": "AfterModel",
    },
    has_hooks=True,
    schema_type="permissive",
    has_exit2_workaround=False,
    drops_additional_context=False,
    config_dir="~/.gemini/",
    template_dir="gemini_template",
    hooks_path_var="${extensionPath}",
    install_fn_name="_install_for_gemini",
    list_cmd=("gemini", "extensions", "list"),
    tool_names=_GEMINI_TOOLS,
    task_management_style="bulk_todos",
    task_create_tools=frozenset({"task_create", "tracker_create_task"}),
    task_update_tools=frozenset({"task_update", "tracker_update_task"}),
    task_review_tools=frozenset({
        "task_list", "tracker_list_tasks",
        "task_get", "tracker_get_task",
    }),
    task_bulk_tools=frozenset({"write_todos"}),
    normal_allow_decision="allow",
    block_decision="deny",
    supports_additional_context_events=frozenset({
        "SessionStart", "UserPromptSubmit", "PostToolUse",
    }),
))


ANTIGRAVITY = register(Platform(
    name="antigravity",
    display_name="Google Antigravity",
    binary="agy",
    detect_env_vars=(
        "ANTIGRAVITY_SESSION_ID",
        "ANTIGRAVITY_PROJECT_DIR",
        "AGY_SESSION_ID",
    ),
    detect_session_keys=("ANTIGRAVITY_SESSION_ID", "AGY_SESSION_ID"),
    detect_event_names=frozenset(),
    detect_path_hints=(".antigravity", ".gemini/antigravity", ".gemini/antigravity-cli"),
    cli_to_internal_events=GEMINI.cli_to_internal_events,
    internal_to_cli_events=GEMINI.internal_to_cli_events,
    has_hooks=True,
    schema_type="permissive",
    has_exit2_workaround=False,
    drops_additional_context=False,
    config_dir="~/.gemini/antigravity-cli/",
    template_dir="gemini_template",
    hooks_path_var="${extensionPath}",
    install_fn_name="_install_for_antigravity",
    list_cmd=("agy", "plugin", "list"),
    tool_names=_GEMINI_TOOLS,
    task_management_style="bulk_todos",
    task_create_tools=GEMINI.task_create_tools,
    task_update_tools=GEMINI.task_update_tools,
    task_review_tools=GEMINI.task_review_tools,
    task_bulk_tools=GEMINI.task_bulk_tools,
    normal_allow_decision="allow",
    block_decision="deny",
    supports_additional_context_events=GEMINI.supports_additional_context_events,
    app_bundle_ids=("com.google.antigravity",),
    app_paths=("/Applications/Antigravity.app",),
))


QWEN = register(Platform(
    name="qwen",
    display_name="Qwen Code",
    binary="qwen",
    detect_env_vars=("QWEN_SESSION_ID", "QWEN_PROJECT_DIR", "QWEN_CODE"),
    detect_session_keys=("QWEN_SESSION_ID",),
    detect_event_names=GEMINI.detect_event_names,
    detect_path_hints=(".qwen",),
    cli_to_internal_events=GEMINI.cli_to_internal_events,
    internal_to_cli_events=GEMINI.internal_to_cli_events,
    has_hooks=True,
    schema_type="permissive",
    has_exit2_workaround=False,
    drops_additional_context=False,
    config_dir="~/.qwen/",
    template_dir="gemini_template",
    hooks_path_var="${extensionPath}",
    install_fn_name="_install_for_qwen",
    list_cmd=("qwen", "extensions", "list"),
    tool_names=_GEMINI_TOOLS,
    task_management_style="bulk_todos",
    task_create_tools=GEMINI.task_create_tools,
    task_update_tools=GEMINI.task_update_tools,
    task_review_tools=GEMINI.task_review_tools,
    task_bulk_tools=GEMINI.task_bulk_tools,
    command_prefixes=("/ar:",),
    command_display_prefix="/ar:",
    normal_allow_decision="allow",
    block_decision="deny",
    supports_additional_context_events=GEMINI.supports_additional_context_events,
))


CODEX = register(Platform(
    name="codex",
    display_name="Codex CLI",
    binary="codex",
    detect_env_vars=("CODEX_SESSION_ID", "CODEX_PROJECT_DIR"),
    detect_session_keys=("CODEX_SESSION_ID",),
    detect_path_hints=(".codex",),
    has_hooks=True,
    schema_type="strict",          # same JSON schema as Claude Code
    has_exit2_workaround=False,    # exit 0 + JSON deny works
    drops_additional_context=False,
    config_dir="~/.codex/",
    template_dir=None,             # user-level install at ~/.codex/hooks.json
    hooks_path_var="${PLUGIN_ROOT}",  # ${CLAUDE_PLUGIN_ROOT} also set as compat
    install_fn_name="_install_for_codex",
    list_cmd=("codex", "plugin", "list"),
    app_bundle_ids=("com.openai.codex",),
    app_paths=("/Applications/Codex.app",),
    tool_names=_CODEX_TOOLS,
    native_shell_read_commands=frozenset({"cat", "head", "tail"}),
    task_management_style="plan_checklist",
    task_plan_tools=frozenset({"update_plan"}),
    command_prefixes=("/ar:", "ar:", "ar "),
    command_display_prefix="ar:",
    normal_allow_decision=None,
    block_decision="block",
    supports_additional_context_events=frozenset({
        "SessionStart", "UserPromptSubmit", "PostToolUse", "SubagentStart",
    }),
    unsupported_response_fields_by_event={
        "PreToolUse": frozenset({
            "continue", "stopReason", "suppressOutput", "permissionDecision",
        }),
        "PostToolUse": frozenset({"suppressOutput"}),
    },
    # event_map: identity (Codex shares Claude's event names)
    internal_to_cli_events={
        "PreToolUse": "PreToolUse", "PostToolUse": "PostToolUse",
        "UserPromptSubmit": "UserPromptSubmit", "Stop": "Stop",
        "SessionStart": "SessionStart", "SessionEnd": "SessionEnd",
    },
))


FORGECODE = register(Platform(
    name="forgecode",
    display_name="ForgeCode",
    binary="forge",
    detect_env_vars=("FORGE_CONFIG", "_FORGE_CONVERSATION_ID"),
    detect_path_hints=(".forge",),
    has_hooks=False,
    schema_type="none",            # no hook responses
    config_dir="~/.forge/",
    template_dir="forgecode_template",
    install_fn_name="_install_for_forgecode",
    # tool_names empty: not relevant without hooks (advisory AGENTS.md only)
))


# === Lookup API ============================================================

def get_platform(name: str) -> Platform | None:
    """Return Platform by name, or None if not registered."""
    return PLATFORMS.get(name)


def hook_platforms() -> list[Platform]:
    """All platforms that support external hooks (excludes ForgeCode)."""
    return [p for p in PLATFORMS.values() if p.has_hooks]


def detection_platforms() -> list[Platform]:
    """All non-default platforms in detection priority order.

    Claude is the fallback default so it's excluded from positive detection.
    """
    priority = {"antigravity": -10}
    return sorted(
        (p for p in PLATFORMS.values() if p.name != "claude"),
        key=lambda p: priority.get(p.name, 0),
    )


def platform_for(name: str | None) -> Platform:
    """Return a known Platform, defaulting to Claude for unknown legacy callers."""
    return PLATFORMS.get(name or "", PLATFORMS["claude"])


def task_tool_role(cli_type: str | None, tool_name: str | None) -> str | None:
    """Classify a tool according to the platform's native task surface.

    Return values are deliberately small strings so hot-path hook code can
    dispatch without importing platform-specific classes or branching on CLI
    names: "create", "update", "review", "bulk", "plan", or None.
    """
    if not tool_name:
        return None

    def role_for(platform: Platform) -> str | None:
        if tool_name in platform.task_plan_tools:
            return "plan"
        if tool_name in platform.task_bulk_tools:
            return "bulk"
        if tool_name in platform.task_create_tools:
            return "create"
        if tool_name in platform.task_update_tools:
            return "update"
        if tool_name in platform.task_review_tools:
            return "review"
        return None

    platform = get_platform(cli_type or "")
    if platform is not None:
        return role_for(platform)

    # Backward compatibility and daemon robustness: older tests/hooks may omit
    # cli_type. Infer by native tool name only when no known platform was given.
    for candidate in PLATFORMS.values():
        role = role_for(candidate)
        if role is not None:
            return role
    return None


def is_task_tool(cli_type: str | None, tool_name: str | None) -> bool:
    """True when tool_name is any native task/checklist tool for cli_type."""
    return task_tool_role(cli_type, tool_name) is not None


def is_task_progress_tool(cli_type: str | None, tool_name: str | None) -> bool:
    """True when tool_name can create/update task state for cli_type."""
    return task_tool_role(cli_type, tool_name) in {"create", "update", "bulk", "plan"}
