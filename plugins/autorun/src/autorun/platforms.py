"""Single source of truth for AI coding CLI platforms supported by autorun.

Adding a hook-capable CLI requires one named HookProtocol value and one
Platform(...) registration, plus an install function only when its packaging
differs. Detection, event maps, response behavior, tools, and installation
metadata are then discoverable from this module instead of parallel tables.

Thread-safety:
    Platform is a frozen+slots dataclass — fields cannot mutate after
    construction. PLATFORMS is read-only after module import (register
    raises on duplicate insertion).

Multi-process safety:
    All fields are immutable primitives, a frozen HookProtocol value, or plain
    dict (used for ordered mappings; treated as read-only).
    Child processes that import this module observe identical data.

Multi-session safety:
    No session-scoped data lives on Platform; session state belongs in
    EventContext / SessionStateManager.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Mapping


# The register(Platform(...)) declarations below provide Click/Typer-like
# declarative configuration: callers look up typed behavior instead of
# branching on harness names.


@dataclass(frozen=True, slots=True)
class HookProtocol:
    """One harness CLI's hook wire contract.

    Workflow:
    - ``EventContext.respond`` asks this contract to serialize tool decisions,
      blocked context events, and Stop rejection.
    - ``client`` asks it for fail-closed daemon errors and unhandled-hook output.
    - ``validate_hook_response`` asks it for any harness-only schema filtering.
    - ``install`` asks it to translate the shared hook manifest.

    Permission decisions reject or allow one TOOL while the AI keeps running.
    A Stop-blocking decision rejects the Stop event and makes the AI continue
    its task. Neither operation should set session ``continue`` false, because
    that ends the agent loop instead of safely redirecting it.

    The fields below declare wire-level differences; named subclasses contain
    the few behavioral exceptions. ``capability_snapshot`` exposes both.
    """

    name: str
    # Where a PreToolUse permission decision appears in native JSON.
    pretool_decision_location: str = "root_and_hook_specific_output"
    # Native decision vocabulary and whether a harness can ask the user.
    supports_ask_decision: bool = False
    root_allow_decision: str = "approve"
    root_block_decision: str = "block"
    # The native decision value that rejects a Stop event so work continues.
    stop_blocking_decision: str = "block"
    # True when Stop accepts exactly decision/reason, with no common fields.
    stop_response_uses_only_decision_and_reason: bool = False
    # True when an installed hook must emit JSON even when no handler responds.
    requires_json_for_unhandled_hook: bool = False
    # UserPromptSubmit/PostToolUse events that accept a top-level block decision.
    # UserPromptSubmit rejection prevents prompt submission; PostToolUse cannot
    # undo an already-run tool and instead attaches the reason to its result.
    # In both cases the AI loop stays active. Empty means feedback-only fallback.
    context_events_with_block_decision: frozenset[str] = field(default_factory=frozenset)
    # Native manifest layout: container key plus events that omit matcher groups.
    hook_manifest_container_key: str = "hooks"
    manifest_events_with_flat_handlers: frozenset[str] = field(default_factory=frozenset)
    manifest_events_without_matchers: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        locations = {"root_and_hook_specific_output", "hook_specific_output", "root"}
        if self.pretool_decision_location not in locations:
            raise ValueError(f"Unknown PreToolUse decision location: {self.pretool_decision_location}")

    def normalize_tool_decision(self, decision: str) -> str:
        if decision not in {"allow", "deny", "ask", "block"}:
            return "deny"
        if decision == "block":
            return "deny"
        return decision if decision != "ask" or self.supports_ask_decision else "deny"

    def block_decision_for_context_event(self, autorun_event: str, reason: str) -> dict | None:
        """Return native block feedback, or None for feedback-only fallback.

        A UserPromptSubmit block rejects that prompt. A PostToolUse block only
        annotates the completed result; it never rolls back the tool. Neither
        response ends the AI loop because ``continue`` remains true.
        """
        if autorun_event not in self.context_events_with_block_decision:
            return None
        return {
            "decision": self.root_block_decision,
            "reason": reason,
            "continue": True,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": reason,
        }

    def context_response(
        self,
        *,
        event_name: str,
        system_message: str,
        root_reason: str,
        additional_context: str | None,
    ) -> dict:
        """Serialize an allowed UserPromptSubmit/PostToolUse response."""
        response = {
            "continue": True,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": system_message,
            "decision": self.root_allow_decision,
            "reason": root_reason,
        }
        if additional_context is not None:
            response["hookSpecificOutput"] = {
                "hookEventName": event_name,
                "additionalContext": additional_context,
            }
        return response

    def pretool_response(self, decision: str, reason: str, event_name: str) -> dict:
        """Serialize a tool decision while keeping the agent loop alive.

        CRITICAL SEMANTICS:
        1. Permission decisions control the TOOL, not the AI. ``continue=True``
           therefore accompanies dual-schema responses; ``continue=False``
           would stop the agent session rather than safely deny one tool.
        2. ``root_and_hook_specific_output`` retains both the legacy root
           decision and hookSpecificOutput for Claude/Gemini compatibility.
           Qwen uses ``hook_specific_output`` only; Agy uses ``root`` only.
        3. ``hookSpecificOutput.permissionDecisionReason`` is the canonical
           portable message for nested-output protocols and is always populated.
           Portable tests should assert it instead of a duplicate root reason.
        4. Claude deny may deliberately hide JSON ``reason`` and
           ``systemMessage`` because its exit-2 workaround sends the same text
           on stderr; duplicating all channels produces triple printing. The
           canonical hookSpecificOutput reason remains present.

        Exit code 0 means the hook completed, even when JSON denies a tool.
        Claude's separately configured exit-2 workaround reports the denial as
        a blocking hook error; other harnesses use exit 0 plus native JSON.
        """
        decision = self.normalize_tool_decision(decision)
        hook_output = {
            "hookEventName": event_name,
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
        if self.pretool_decision_location == "hook_specific_output":
            return {"hookSpecificOutput": hook_output}
        if self.pretool_decision_location == "root":
            return {
                "decision": self.root_allow_decision if decision == "allow" else decision,
                "reason": reason,
            }
        return {
            "decision": (self.root_allow_decision if decision == "allow" else self.root_block_decision),
            "permissionDecision": decision,
            "reason": reason,
            "continue": True,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": reason,
            "hookSpecificOutput": hook_output,
        }

    def response_to_reject_stop_and_continue(self, reason: str) -> dict:
        """Prevent a Stop while continuing work.

        ``decision`` blocks the Stop event and ``reason`` becomes continuation
        guidance. Never encode this action as ``continue=False``: that stops the
        entire agent. Claude/Gemini retain legacy common fields but omit
        ``systemMessage`` here: Claude Code renders ``systemMessage`` as its
        own standalone "<hook> says: <text>" history entry AND separately
        renders the block ``reason`` as a "<hook> hook error: <text>" row
        inside its "Ran N hooks" summary (confirmed by inspecting the
        installed Claude Code binary) — setting both duplicates the identical
        text on two UI surfaces. ``reason`` alone already drives blocking and
        continuation guidance; ``systemMessage`` here is pure redundant UI
        decoration. Current Codex, Qwen, and Agy use their minimal native
        decision/reason contracts and were never affected (their Stop
        renderers only ever populate one field from this response shape).
        """
        response = {"decision": self.stop_blocking_decision, "reason": reason}
        if self.stop_response_uses_only_decision_and_reason:
            return response
        return {
            "continue": True,
            **response,
            "stopReason": "",
            "suppressOutput": False,
        }

    def fail_closed_pretool_response(self, reason: str, event_name: str) -> dict:
        """Deny one tool when autorun cannot reach its permission daemon."""
        return self.pretool_response("deny", reason, event_name)

    def response_for_unhandled_hook(self) -> dict:
        """Return the native no-op output required when no handler responds."""
        return {"continue": True} if self.requires_json_for_unhandled_hook else {}

    def filter_response_to_harness_schema(self, event: str, response: dict) -> dict | None:
        """Return None so the shared response validator handles this harness."""
        return None

    def _manifest_handlers(self, groups: object, event: str) -> list[dict]:
        """Normalize one event's shared matcher groups to the harness shape."""
        valid_groups = [group for group in groups if isinstance(group, dict)] if isinstance(groups, list) else []
        if event in self.manifest_events_with_flat_handlers:
            return [
                {key: value for key, value in handler.items() if key != "matcher"}
                for group in valid_groups
                for handler in (group.get("hooks", []) if isinstance(group.get("hooks"), list) else [group])
            ]
        if self.hook_manifest_container_key == "hooks":
            return [
                ({key: value for key, value in group.items() if key != "matcher"} if event in self.manifest_events_without_matchers else dict(group))
                for group in valid_groups
            ]
        return [{"matcher": "*", **group} for group in valid_groups]

    def translate_manifest(self, data: dict, event_map: Mapping[str, str | None]) -> dict:
        """Translate the shared hook manifest to this harness CLI's contract."""
        source = data.get("hooks")
        if not isinstance(source, dict):
            return data
        translated: dict[str, list[dict]] = {}
        for event, groups in source.items():
            target = event_map.get(event)
            if target:
                translated.setdefault(target, []).extend(self._manifest_handlers(groups, target))
        if self.hook_manifest_container_key == "hooks":
            return {**data, "hooks": translated}
        return {self.hook_manifest_container_key: translated}


@dataclass(frozen=True, slots=True)
class ClaudeHookProtocol(HookProtocol):
    """Claude contract for the client exit-2 duplicate-message workaround.

    ``EventContext.respond`` still places the denial reason in
    ``hookSpecificOutput`` for the AI, but omits duplicate root JSON text when
    the client will print stderr.
    """

    def pretool_response(self, decision: str, reason: str, event_name: str) -> dict:
        # Explicit base call avoids zero-argument super()'s replacement-class
        # edge case in frozen dataclasses with slots=True.
        response = HookProtocol.pretool_response(self, decision, reason, event_name)
        if response.get("permissionDecision") == "deny":
            response["reason"] = ""
            response["systemMessage"] = ""
        return response


@dataclass(frozen=True, slots=True)
class GeminiHookProtocol(HookProtocol):
    """Legacy Gemini CLI daemon-failure response contract.

    The common dual response is useful before validation, but a fail-closed
    client fallback must omit Claude's unsupported root permissionDecision.
    """

    def fail_closed_pretool_response(self, reason: str, event_name: str) -> dict:
        response = self.pretool_response("deny", reason, event_name)
        response.pop("permissionDecision", None)
        return response


@dataclass(frozen=True, slots=True)
class QwenHookProtocol(HookProtocol):
    """Qwen response filter used by ``validate_hook_response``.

    Qwen consumes hookSpecificOutput-only PreToolUse decisions and compact
    decision/reason Stop responses, so portable Claude/Gemini fields must not
    reach its CLI.
    """

    @staticmethod
    def _invalid_pretool_response_reason(problem: str) -> str:
        """Return actionable fail-closed guidance for a malformed Qwen response."""
        return (
            f"[AR_EVENT_V1:invalid_qwen_pretool_response] {problem} "
            "The tool was blocked because autorun could not verify the permission "
            "decision. Check the autorun hook/daemon logs, run "
            "`autorun --restart-daemon`, then retry."
        )

    def filter_response_to_harness_schema(self, event: str, response: dict) -> dict | None:
        """Keep only fields accepted by Qwen's documented native schema."""
        if event == "PreToolUse":
            hook_output = response.get("hookSpecificOutput")
            if "hookSpecificOutput" in response and not isinstance(hook_output, dict):
                reason = self._invalid_pretool_response_reason("Qwen PreToolUse hookSpecificOutput must be a JSON object.")
                return self.pretool_response("deny", reason, "PreToolUse")
            nested_decision = hook_output.get("permissionDecision") if hook_output is not None else None
            decision = nested_decision or response.get("permissionDecision") or response.get("decision")
            reason = (
                (hook_output.get("permissionDecisionReason") if hook_output is not None else None)
                or response.get("reason")
                or response.get("systemMessage")
                or ""
            )
            if decision not in {"allow", "deny", "ask", "block"}:
                reason = self._invalid_pretool_response_reason(
                    "Qwen PreToolUse response contained no permission decision"
                    if decision is None
                    else f"Qwen PreToolUse response contained unknown permission decision {decision!r}"
                )
                decision = "deny"
            return self.pretool_response(decision, reason, "PreToolUse")
        if event in {"Stop", "SubagentStop"}:
            return {key: response[key] for key in ("decision", "reason") if key in response}
        return None


@dataclass(frozen=True, slots=True)
class CodexHookProtocol(HookProtocol):
    """Codex-only strict responses used by EventContext and the client.

    A blocked context event or daemon failure rejects only that hook/tool and
    leaves the agent loop running; unsupported Claude common fields are omitted.
    """

    def context_response(
        self,
        *,
        event_name: str,
        system_message: str,
        root_reason: str,
        additional_context: str | None,
    ) -> dict:
        if not system_message and additional_context in (None, ""):
            return {}
        response = {
            "continue": True,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": system_message,
        }
        if additional_context is not None:
            response["hookSpecificOutput"] = {
                "hookEventName": event_name,
                "additionalContext": additional_context,
            }
        return response

    def fail_closed_pretool_response(self, reason: str, event_name: str) -> dict:
        return {
            "decision": "block",
            "reason": reason,
            "systemMessage": reason,
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            },
        }


# Protocol references and behavior are documented beside each Platform below.
# Claude: nested hookSpecificOutput permission decisions; block+reason rejects
# Stop; deny reasons may
# use the configured exit-2 workaround.
# https://code.claude.com/docs/en/hooks#stop-and-subagentstop-decision-control
CLAUDE_HOOKS = ClaudeHookProtocol(
    "claude",
    supports_ask_decision=True,
    # Claude accepts top-level block/reason for PostToolUse, but autorun's
    # established non-Codex behavior is feedback-only for context events.
    # Keep that tested behavior instead of turning existing deny() calls into
    # new prompt/result blocks merely because the wire format supports them.
    # https://code.claude.com/docs/en/hooks#posttooluse-decision-control
)

# Legacy Gemini CLI: root allow/deny decisions; deny rejects AfterAgent
# (autorun Stop). Retained for existing Gemini hook installations.
# https://github.com/google-gemini/gemini-cli/blob/main/docs/hooks/index.md
GEMINI_HOOKS = GeminiHookProtocol(
    "gemini",
    root_allow_decision="allow",
    root_block_decision="deny",
    stop_blocking_decision="deny",
    requires_json_for_unhandled_hook=True,
    # BeforeAgent/AfterTool accept structured decisions, but autorun has long
    # treated both as feedback-only context events. Preserve that behavior;
    # AfterTool is additionally filtered as lifecycle output by the validator.
    # https://github.com/google-gemini/gemini-cli/blob/main/docs/hooks/writing-hooks.md
    # https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/hooks/types.ts
    # https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/hooks/hookAggregator.ts
)

# Qwen Code: Claude-like event names, hookSpecificOutput-only tool decisions,
# and compact decision/reason Stop responses.
# https://qwenlm.github.io/qwen-code-docs/en/users/features/hooks/
QWEN_HOOKS = QwenHookProtocol(
    "qwen",
    pretool_decision_location="hook_specific_output",
    supports_ask_decision=True,
    root_allow_decision="allow",
    root_block_decision="deny",
    stop_response_uses_only_decision_and_reason=True,
    requires_json_for_unhandled_hook=True,
    # DOCS/IMPLEMENTATION MISMATCH: Qwen's docs and PostToolUseOutput type expose
    # top-level decision/reason, and HookAggregator records a block. However,
    # firePostToolUseHook() never calls isBlockingDecision(); it stops only when
    # continue=false. Pre-protocol autorun emitted feedback-only responses here,
    # so retaining that contract avoids a misleading pseudo-block.
    # https://qwenlm.github.io/qwen-code-docs/en/users/features/hooks/#individual-hook-event-details
    # https://github.com/QwenLM/qwen-code/blob/main/packages/core/src/hooks/types.ts
    # https://github.com/QwenLM/qwen-code/blob/main/packages/core/src/hooks/hookAggregator.ts
    # https://github.com/QwenLM/qwen-code/blob/main/packages/core/src/core/toolHookTriggers.ts
    manifest_events_without_matchers=frozenset({"UserPromptSubmit", "Stop"}),
)

# Agy (Google Antigravity CLI): root tool decisions, continue+reason to reject
# Stop, flat invocation handlers under the autorun manifest key.
# https://antigravity.google/docs/cli-plugins
# Closed-binary discovery hint, not wire-contract proof: installed Agy strings
# include "permissionOverrides" and "force_ask"; retain regression tests because
# no open-source response parser is available to cross-check the documentation.
ANTIGRAVITY_HOOKS = HookProtocol(
    "antigravity",
    pretool_decision_location="root",
    supports_ask_decision=True,
    root_allow_decision="allow",
    root_block_decision="deny",
    stop_blocking_decision="continue",
    stop_response_uses_only_decision_and_reason=True,
    requires_json_for_unhandled_hook=True,
    # Agy keeps autorun's established feedback-only context behavior, including
    # PostToolUse additionalContext; no verified native contract requires
    # discarding that existing feedback.
    # https://antigravity.google/docs/cli-plugins
    hook_manifest_container_key="autorun",
    manifest_events_with_flat_handlers=frozenset(
        {
            "PreInvocation",
            "PostInvocation",
            "Stop",
        }
    ),
)

# Codex CLI: strict PreToolUse schema and compact block+reason Stop response.
# https://learn.chatgpt.com/docs/hooks
CODEX_HOOKS = CodexHookProtocol(
    "codex",
    stop_response_uses_only_decision_and_reason=True,
    # Codex is the one pre-protocol context path that emitted decision:block.
    # Its parser requires a non-empty reason and marks UserPromptSubmit or
    # PostToolUse blocked while leaving the surrounding agent loop available.
    # https://github.com/openai/codex/blob/main/codex-rs/hooks/src/engine/output_parser.rs
    # https://github.com/openai/codex/blob/main/codex-rs/hooks/src/events/user_prompt_submit.rs
    # https://github.com/openai/codex/blob/main/codex-rs/hooks/src/events/post_tool_use.rs
    context_events_with_block_decision=frozenset({"UserPromptSubmit", "PostToolUse"}),
)
NO_HOOKS = HookProtocol("none", stop_response_uses_only_decision_and_reason=True)


@dataclass(frozen=True, slots=True)
class Platform:
    """Immutable specification for one AI coding CLI platform.

    Fields are grouped: identity, detection, event mapping, tool names,
    native task/checklist behavior, schema/bug applicability, install metadata.
    Hook protocol fields describe semantics rather than duplicating response or
    installer code: EventContext.respond() and validate_hook_response() consume
    response fields; install._set_gemini_family_hook_cli() consumes manifest
    fields. ``autorun.capability_snapshot`` exposes all of them read-only for
    diagnostics and harness integration.
    """

    # === Identity ===
    name: str
    display_name: str
    binary: str  # for shutil.which() probes

    # === Detection (used by config.detect_cli_type) ===
    detect_env_vars: tuple[str, ...] = ()
    detect_session_keys: tuple[str, ...] = ()
    # Environment keys exported to standalone child commands. Keep these
    # separate from detect_session_keys, which also contains hook-payload keys.
    standalone_session_env_vars: tuple[str, ...] = ()
    detect_event_names: frozenset[str] = field(default_factory=frozenset)
    detect_path_hints: tuple[str, ...] = ()

    # === Event normalization ===
    # Maps wire-level harness names to/from autorun's @app.on event names.
    # Empty mappings mean the harness already uses autorun's names.
    harness_cli_to_autorun_events: Mapping[str, str] = field(default_factory=dict)
    autorun_to_harness_cli_events: Mapping[str, str] = field(default_factory=dict)

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
    # Status values this harness's own task-update tool accepts. Guidance must
    # never name a value absent here: the AI would call it and get a hard
    # validation error instead of the action autorun offered it. Empty means
    # "unverified", which routes guidance to autorun's harness-independent
    # marker instead — safe for any harness, including ones not listed above.
    native_task_statuses: frozenset[str] = field(default_factory=frozenset)

    # === Autorun prompt commands ===
    # Autorun command handlers are registered in canonical /ar:* form. Some
    # harnesses accept additional prompt-hook spellings; dispatch normalizes any
    # listed prefix back to /ar:* before handlers run.
    command_prefixes: tuple[str, ...] = ("/ar:",)
    command_display_prefix: str = "/ar:"

    # === Hook capability ===
    has_hooks: bool = True
    schema_type: str = "strict"  # "strict" | "permissive" | "none"
    # Native protocol identity and the smallest per-harness differences used by
    # the shared response builder and manifest translator.
    hook_protocol: HookProtocol = CLAUDE_HOOKS

    # === Bug workaround applicability ===
    has_exit2_workaround: bool = False  # Claude #4669
    drops_additional_context: bool = False  # Claude #18534

    # === Hook response capability metadata ===
    supports_additional_context_events: frozenset[str] = field(default_factory=frozenset)
    unsupported_response_fields_by_event: Mapping[str, frozenset[str]] = field(default_factory=dict)

    # === Install metadata ===
    # False keeps an archived/legacy integration available through its explicit
    # CLI flag without selecting it during the default maintained-harness install.
    install_by_default: bool = True
    config_dir: str = ""
    # Env vars the harness itself documents as relocating its config root
    # (e.g. CODEX_HOME). install.platform_config_dir() honors them between the
    # CONFIG["harness_config_dirs"] override and the config_dir default.
    config_dir_env_vars: tuple[str, ...] = ()
    template_dir: str | None = None
    hooks_path_var: str = ""
    install_fn_name: str = ""
    list_cmd: tuple[str, ...] = ()
    app_bundle_ids: tuple[str, ...] = ()
    app_paths: tuple[str, ...] = ()

    # === Agent memory file ===
    # Harness-global instructions file that the harness injects into every
    # session. autorun rents one sentinel-delimited region inside it and never
    # touches the surrounding user content. Empty memory_filename means the
    # harness has no such file and the shared installer skips it.
    #
    # Content is per-harness by design: guidance that names Claude Code's
    # compaction behavior is false for Codex, so each platform points at its own
    # template rather than sharing one body.
    memory_filename: str = ""
    # Template path relative to the plugin's src/autorun/ directory.
    memory_template: str = ""
    # True only when the harness's own documentation describes direct discovery
    # of the shared ~/.agents/skills root. This is factual capability data, not
    # a preference: install.resolve_skill_routes() turns it plus the user's
    # --skill-placement value into one route per harness, so a harness that
    # cannot read the shared root never has `auto` point there.
    loads_shared_agents_skills: bool = False
    # Subdirectory of config_dir this harness scans for skills. Empty means the
    # harness has no config-dir-local skills directory — Codex reads the shared
    # ~/.agents/skills instead, which is configuration, not platform data.
    skills_subdir: str = ""
    # Stable slug for the sentinel pair. Changing it orphans blocks already
    # written into users' files, which uninstall would then fail to remove.
    memory_sentinel_slug: str = ""

    # === Uninstall metadata ===
    # Uninstall is declared beside install so the two cannot drift. A harness
    # that materializes extensions needs both fields: the CLI owns its own
    # registry, so deleting the directory without telling it leaves a dangling
    # entry in `<cli> extensions list`; and the CLI may be gone by uninstall
    # time, so the directory must be removable without it.
    #
    # Subdirectory of config_dir holding installed extensions. Empty means the
    # harness has no extension directory of its own — Claude installs through
    # `claude plugin`, ForgeCode is template-only.
    extensions_subdir: str = ""
    # argv template that removes one installed unit. "{name}" is substituted
    # with the extension name.
    uninstall_cmd: tuple[str, ...] = ()


# === Registry ==============================================================
# Module-level dict — declaration order = detection priority.
PLATFORMS: dict[str, Platform] = {}

CUSTOM_HARNESS_FLAVOR_ALIASES = {
    "claude": "claude",
    "gemini": "gemini",
    "qwen": "qwen",
    "codex": "codex",
    "agy": "antigravity",
    "antigravity": "antigravity",
}
CUSTOM_HARNESS_FLAVOR_ORDER = ("claude", "gemini", "qwen", "antigravity", "agy", "codex")
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
        "Repeat for multiple targets. The claude flavor installs the portable "
        "markdown commands + AGENTS.md bundle (no hooks). Persistent targets "
        "can be declared in CONFIG['custom_harnesses'] with the same SPEC "
        "format; a CLI spec overrides a config spec with the same name."
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
    "grep": "Grep",
    "glob": "Glob",
    "read": "Read",
    "write": "Write",
    "edit": "Edit",
    "bash": "Bash",
    "ls": "LS",
    "task_create": "TaskCreate",
    "task_update": "TaskUpdate",
    "task_list": "TaskList",
    "task_progress": "TaskUpdate",
    "task_title": "subject",
    "task_id_param": "taskId",
}

_GEMINI_TOOLS = {
    "grep": "grep_search",
    "glob": "glob",
    "read": "read_file",
    "write": "write_file",
    "edit": "replace",
    "bash": "run_shell_command",
    "ls": "list_directory",
    "task_create": "tracker_create_task",
    "task_update": "tracker_update_task",
    "task_list": "tracker_list_tasks",
    "task_progress": "write_todos",
    "task_title": "title",
    "task_id_param": "id",
}

# Codex hook events use Claude-like shell/edit matcher names, but the current
# Codex model-facing tool surface does not expose Claude-style Grep/Glob/Read/
# Write tools. Keep suggestions pointed at the shell inspection/search commands
# and apply_patch path Codex can actually use.
_CODEX_TOOLS = dict(_CLAUDE_TOOLS)
_CODEX_TOOLS.update(
    {
        "grep": "`rg -n` shell search",
        "glob": "`rg --files` shell listing",
        "read": "shell file inspection",
        "write": "apply_patch",
        "edit": "apply_patch",
        "task_progress": "update_plan",
    }
)


# === Platform definitions ==================================================
# IMPORTANT: order matters for detection priority. Claude is the default
# fallback (registered first so the canonical Claude data structures exist
# first; detection_platforms() filters it out).

CLAUDE = register(
    Platform(
        name="claude",
        display_name="Claude Code",
        binary="claude",
        standalone_session_env_vars=("CLAUDE_SESSION_ID",),
        has_hooks=True,
        schema_type="strict",
        hook_protocol=CLAUDE_HOOKS,
        has_exit2_workaround=True,
        drops_additional_context=True,
        config_dir="~/.claude/",
        config_dir_env_vars=("CLAUDE_CONFIG_DIR",),
        template_dir=None,  # hooks live at plugin root
        hooks_path_var="${CLAUDE_PLUGIN_ROOT}",
        install_fn_name="_install_for_claude",
        memory_filename="CLAUDE.md",
        memory_template="claude_template/CLAUDE.md",
        memory_sentinel_slug="claude-memory-md",
        skills_subdir="skills",
        list_cmd=("claude", "plugin", "list"),
        app_bundle_ids=("com.anthropic.claudefordesktop",),
        app_paths=("/Applications/Claude.app",),
        tool_names=_CLAUDE_TOOLS,
        task_management_style="task_tools",
        task_create_tools=frozenset({"TaskCreate"}),
        task_update_tools=frozenset({"TaskUpdate"}),
        task_review_tools=frozenset({"TaskList", "TaskGet"}),
        # Verified against the live tool: TaskUpdate rejects anything else with
        # InputValidationError ('expected one of "pending"|"in_progress"|
        # "completed" ... expected "deleted"'). Notably absent: "delegated",
        # which autorun tracks internally but Claude Code's tool cannot set.
        native_task_statuses=frozenset({"pending", "in_progress", "completed", "deleted"}),
        supports_additional_context_events=frozenset({"UserPromptSubmit", "PostToolUse"}),
        # event maps left empty: Claude events are canonical (identity).
        autorun_to_harness_cli_events={
            "PreToolUse": "PreToolUse",
            "PostToolUse": "PostToolUse",
            "UserPromptSubmit": "UserPromptSubmit",
            "Stop": "Stop",
            "SessionStart": "SessionStart",
            "SessionEnd": "SessionEnd",
            "BeforeModel": "BeforeModel",
            "AfterModel": "AfterModel",
        },
    )
)


GEMINI = register(
    Platform(
        name="gemini",
        display_name="Legacy Gemini CLI",
        binary="gemini",
        install_by_default=False,
        detect_env_vars=("GEMINI_SESSION_ID", "GEMINI_PROJECT_DIR", "GEMINI_CLI"),
        detect_session_keys=("GEMINI_SESSION_ID", "sessionId", "session_id"),
        standalone_session_env_vars=("GEMINI_SESSION_ID",),
        detect_event_names=frozenset(
            {
                "BeforeTool",
                "AfterTool",
                "BeforeAgent",
                "AfterAgent",
                "BeforeModel",
                "AfterModel",
                "BeforeToolSelection",
            }
        ),
        detect_path_hints=(".gemini",),
        harness_cli_to_autorun_events={
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
        autorun_to_harness_cli_events={
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
        hook_protocol=GEMINI_HOOKS,
        has_exit2_workaround=False,
        drops_additional_context=False,
        config_dir="~/.gemini/",
        template_dir="gemini_template",
        hooks_path_var="${extensionPath}",
        install_fn_name="_install_for_gemini",
        # https://geminicli.com/docs/cli/using-agent-skills/ — Gemini's
        # discovery tiers include the shared ~/.agents/skills root, so the
        # extension copy is a duplicate rather than the only route.
        loads_shared_agents_skills=True,
        list_cmd=("gemini", "extensions", "list"),
        extensions_subdir="extensions",
        uninstall_cmd=("gemini", "extensions", "uninstall", "{name}"),
        tool_names=_GEMINI_TOOLS,
        task_management_style="bulk_todos",
        task_create_tools=frozenset({"task_create", "tracker_create_task"}),
        task_update_tools=frozenset({"task_update", "tracker_update_task"}),
        task_review_tools=frozenset(
            {
                "task_list",
                "tracker_list_tasks",
                "task_get",
                "tracker_get_task",
            }
        ),
        task_bulk_tools=frozenset({"write_todos"}),
        supports_additional_context_events=frozenset(
            {
                "SessionStart",
                "UserPromptSubmit",
                "PostToolUse",
            }
        ),
    )
)


ANTIGRAVITY = register(
    Platform(
        name="antigravity",
        display_name="Google Antigravity",
        binary="agy",
        detect_env_vars=(
            "ANTIGRAVITY_SESSION_ID",
            "ANTIGRAVITY_PROJECT_DIR",
            "AGY_SESSION_ID",
        ),
        detect_session_keys=("ANTIGRAVITY_SESSION_ID", "AGY_SESSION_ID"),
        standalone_session_env_vars=(
            "ANTIGRAVITY_SESSION_ID",
            "AGY_SESSION_ID",
        ),
        # Native event names overlap Claude and therefore cannot identify Agy alone.
        detect_event_names=frozenset(),
        detect_path_hints=(".antigravity", ".gemini/antigravity", ".gemini/antigravity-cli"),
        harness_cli_to_autorun_events={
            # Agy's Invocation names are model lifecycle hooks, not tool hooks:
            # PostToolUse remains PostToolUse while PostInvocation is AfterModel.
            # https://antigravity.google/docs/cli-plugins
            "PreToolUse": "PreToolUse",
            "PostToolUse": "PostToolUse",
            "PreInvocation": "UserPromptSubmit",
            "PostInvocation": "AfterModel",
            "Stop": "Stop",
        },
        autorun_to_harness_cli_events={
            "PreToolUse": "PreToolUse",
            "PostToolUse": "PostToolUse",
            "UserPromptSubmit": "PreInvocation",
            "AfterModel": "PostInvocation",
            "Stop": "Stop",
        },
        has_hooks=True,
        schema_type="permissive",
        hook_protocol=ANTIGRAVITY_HOOKS,
        has_exit2_workaround=False,
        drops_additional_context=False,
        config_dir="~/.gemini/antigravity-cli/",
        template_dir="gemini_template",
        hooks_path_var="${extensionPath}",
        install_fn_name="_install_for_antigravity",
        list_cmd=("agy", "plugin", "list"),
        extensions_subdir="plugins",
        uninstall_cmd=("agy", "plugin", "uninstall", "{name}"),
        tool_names=_GEMINI_TOOLS,
        task_management_style="bulk_todos",
        task_create_tools=GEMINI.task_create_tools,
        task_update_tools=GEMINI.task_update_tools,
        task_review_tools=GEMINI.task_review_tools,
        task_bulk_tools=GEMINI.task_bulk_tools,
        supports_additional_context_events=GEMINI.supports_additional_context_events,
        app_bundle_ids=("com.google.antigravity",),
        app_paths=("/Applications/Antigravity.app",),
    )
)


QWEN = register(
    Platform(
        name="qwen",
        display_name="Qwen Code",
        binary="qwen",
        detect_env_vars=("QWEN_SESSION_ID", "QWEN_PROJECT_DIR", "QWEN_CODE"),
        detect_session_keys=("QWEN_SESSION_ID",),
        standalone_session_env_vars=("QWEN_SESSION_ID",),
        # Native event names overlap Claude and therefore cannot identify Qwen alone.
        detect_event_names=frozenset(),
        detect_path_hints=(".qwen",),
        harness_cli_to_autorun_events={"PreCompact": "PreCompress"},
        autorun_to_harness_cli_events={
            "PreToolUse": "PreToolUse",
            "PostToolUse": "PostToolUse",
            "UserPromptSubmit": "UserPromptSubmit",
            "Stop": "Stop",
            "SessionStart": "SessionStart",
            "SessionEnd": "SessionEnd",
            "PreCompress": "PreCompact",
            "SubagentStop": "SubagentStop",
            "PermissionRequest": "PermissionRequest",
        },
        has_hooks=True,
        schema_type="permissive",
        hook_protocol=QWEN_HOOKS,
        has_exit2_workaround=False,
        drops_additional_context=False,
        config_dir="~/.qwen/",
        config_dir_env_vars=("QWEN_HOME",),
        template_dir="gemini_template",
        hooks_path_var="${extensionPath}",
        install_fn_name="_install_for_qwen",
        list_cmd=("qwen", "extensions", "list"),
        extensions_subdir="extensions",
        uninstall_cmd=("qwen", "extensions", "uninstall", "{name}"),
        tool_names=_GEMINI_TOOLS,
        task_management_style="bulk_todos",
        task_create_tools=GEMINI.task_create_tools,
        task_update_tools=GEMINI.task_update_tools,
        task_review_tools=GEMINI.task_review_tools,
        task_bulk_tools=GEMINI.task_bulk_tools,
        command_prefixes=("/ar:",),
        command_display_prefix="/ar:",
        supports_additional_context_events=GEMINI.supports_additional_context_events,
    )
)


CODEX = register(
    Platform(
        name="codex",
        display_name="Codex CLI",
        binary="codex",
        detect_env_vars=(
            "CODEX_THREAD_ID",
            "CODEX_SESSION_ID",
            "CODEX_PROJECT_DIR",
        ),
        detect_session_keys=("CODEX_THREAD_ID", "CODEX_SESSION_ID"),
        standalone_session_env_vars=("CODEX_THREAD_ID", "CODEX_SESSION_ID"),
        detect_path_hints=(".codex",),
        has_hooks=True,
        schema_type="strict",  # same JSON schema as Claude Code
        hook_protocol=CODEX_HOOKS,
        has_exit2_workaround=False,  # exit 0 + JSON deny works
        drops_additional_context=False,
        config_dir="~/.codex/",
        config_dir_env_vars=("CODEX_HOME",),
        template_dir=None,  # user-level install at ~/.codex/hooks.json
        hooks_path_var="${PLUGIN_ROOT}",  # ${CLAUDE_PLUGIN_ROOT} also set as compat
        install_fn_name="_install_for_codex",
        memory_filename="AGENTS.md",
        memory_template="codex_template/AGENTS.md",
        # https://learn.chatgpt.com/docs/build-skills — Codex scans the shared
        # $HOME/.agents/skills root directly, so it needs no second copy.
        loads_shared_agents_skills=True,
        # Predates the shared installer; keep the slug so uninstall still finds
        # blocks written by earlier autorun versions.
        memory_sentinel_slug="codex-agents-md",
        list_cmd=("codex", "plugin", "list"),
        app_bundle_ids=("com.openai.codex",),
        app_paths=("/Applications/Codex.app",),
        tool_names=_CODEX_TOOLS,
        native_shell_read_commands=frozenset({"cat", "head", "tail"}),
        task_management_style="plan_checklist",
        task_plan_tools=frozenset({"update_plan"}),
        command_prefixes=("/ar:", "ar:", "ar "),
        command_display_prefix="ar:",
        supports_additional_context_events=frozenset(
            {
                "SessionStart",
                "UserPromptSubmit",
                "PostToolUse",
                "SubagentStart",
            }
        ),
        unsupported_response_fields_by_event={
            "PreToolUse": frozenset(
                {
                    "continue",
                    "stopReason",
                    "suppressOutput",
                    "permissionDecision",
                }
            ),
            "PostToolUse": frozenset({"suppressOutput"}),
        },
        # event_map: identity (Codex shares Claude's event names)
        autorun_to_harness_cli_events={
            "PreToolUse": "PreToolUse",
            "PostToolUse": "PostToolUse",
            "UserPromptSubmit": "UserPromptSubmit",
            "Stop": "Stop",
            "SessionStart": "SessionStart",
            "SessionEnd": "SessionEnd",
        },
    )
)


FORGECODE = register(
    Platform(
        name="forgecode",
        display_name="ForgeCode",
        binary="forge",
        detect_env_vars=("FORGE_CONFIG", "_FORGE_CONVERSATION_ID"),
        standalone_session_env_vars=("_FORGE_CONVERSATION_ID",),
        detect_path_hints=(".forge",),
        has_hooks=False,
        schema_type="none",  # no hook responses
        hook_protocol=NO_HOOKS,
        config_dir="~/.forge/",
        config_dir_env_vars=("FORGE_CONFIG",),
        template_dir="forgecode_template",
        install_fn_name="_install_for_forgecode",
        memory_filename="AGENTS.md",
        memory_template="forgecode_template/AGENTS.md",
        memory_sentinel_slug="forgecode-agents-md",
        # tool_names empty: not relevant without hooks (advisory AGENTS.md only)
    )
)


# === Lookup API ============================================================


@dataclass(frozen=True, slots=True)
class StandaloneSessionIdentity:
    """One explicit or unambiguous logical-session identity."""

    session_id: str
    source: str
    platform_name: str | None = None


class SessionIdentityResolutionError(ValueError):
    """Standalone CLI session selection was missing or ambiguous."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def _session_environment_keys() -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            key
            for platform in PLATFORMS.values()
            for key in platform.standalone_session_env_vars
        )
    )


def standalone_session_help() -> str:
    """Describe standalone session selection from registry metadata."""
    keys = ", ".join(f"${key}" for key in _session_environment_keys())
    return (
        "Session ID (default precedence: --session, $AUTORUN_SESSION_ID, "
        f"then one unambiguous active-harness value from {keys})"
    )


def resolve_standalone_session_identity(
    explicit_session_id: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> StandaloneSessionIdentity:
    """Resolve a standalone command's logical session without harness bias."""
    environment = os.environ if environ is None else environ
    explicit_value = (
        explicit_session_id.strip()
        if isinstance(explicit_session_id, str)
        else ""
    )
    if explicit_value:
        return StandaloneSessionIdentity(
            session_id=explicit_value,
            source="--session",
        )

    shared = environment.get("AUTORUN_SESSION_ID", "").strip()
    if shared:
        return StandaloneSessionIdentity(
            session_id=shared,
            source="AUTORUN_SESSION_ID",
        )

    candidates: dict[str, list[tuple[str, str]]] = {}
    for platform in PLATFORMS.values():
        values = [
            (key, environment.get(key, "").strip())
            for key in platform.standalone_session_env_vars
            if environment.get(key, "").strip()
        ]
        if values:
            candidates[platform.name] = values

    selected_name = environment.get("AUTORUN_CLI_TYPE", "").strip().lower()
    selected_name = CUSTOM_HARNESS_FLAVOR_ALIASES.get(
        selected_name,
        selected_name,
    )
    if selected_name:
        selected = PLATFORMS.get(selected_name)
        if selected is None:
            known = ", ".join(PLATFORMS)
            raise SessionIdentityResolutionError(
                f"Unknown AUTORUN_CLI_TYPE {selected_name!r}; expected one of "
                f"{known}, or pass --session SESSION_ID.",
                reason="invalid-harness",
            )
        selected_values = candidates.get(selected.name, [])
        unique = {value for _key, value in selected_values}
        if len(unique) == 1:
            key = selected_values[0][0]
            return StandaloneSessionIdentity(
                session_id=next(iter(unique)),
                source=key,
                platform_name=selected.name,
            )
        expected = ", ".join(selected.standalone_session_env_vars)
        if not selected_values:
            raise SessionIdentityResolutionError(
                f"AUTORUN_CLI_TYPE selects {selected.display_name}, but none "
                f"of {expected} is set; pass --session SESSION_ID.",
                reason="missing",
            )
        keys = ", ".join(key for key, _value in selected_values)
        raise SessionIdentityResolutionError(
            f"Conflicting {selected.display_name} session identities in "
            f"{keys}; pass --session SESSION_ID.",
            reason="ambiguous",
        )

    flattened = [
        (platform_name, key, value)
        for platform_name, values in candidates.items()
        for key, value in values
    ]
    unique = {value for _platform, _key, value in flattened}
    if len(unique) == 1:
        platforms = {platform for platform, _key, _value in flattened}
        source = (
            flattened[0][1]
            if len(flattened) == 1
            else "unambiguous harness environment"
        )
        return StandaloneSessionIdentity(
            session_id=next(iter(unique)),
            source=source,
            platform_name=(
                next(iter(platforms))
                if len(platforms) == 1
                else None
            ),
        )
    if not flattened:
        raise SessionIdentityResolutionError(
            "No standalone session identity found; pass --session SESSION_ID, "
            "set AUTORUN_SESSION_ID, or run inside a supported harness that "
            f"exports one of {', '.join(_session_environment_keys())}.",
            reason="missing",
        )

    keys = ", ".join(key for _platform, key, _value in flattened)
    raise SessionIdentityResolutionError(
        f"Ambiguous standalone session identities are set in {keys}; pass "
        "--session SESSION_ID or set AUTORUN_CLI_TYPE to the active harness.",
        reason="ambiguous",
    )


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


def to_autorun_event(event: str, harness_name: str | None) -> str:
    """Map a harness CLI event to the autorun name consumed by ``@app.on``."""
    platform = platform_for(harness_name)
    return platform.harness_cli_to_autorun_events.get(event, event)


def to_harness_cli_event(event: str, harness_name: str | None) -> str:
    """Map an autorun dispatcher event to the harness CLI's native name."""
    platform = platform_for(harness_name)
    return platform.autorun_to_harness_cli_events.get(event, event)


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
