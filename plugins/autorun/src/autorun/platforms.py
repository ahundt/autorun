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

from dataclasses import dataclass, field, replace
import os
from pathlib import Path
from typing import Callable, Mapping


def _process_home() -> Path:
    """Return the configured process home on every supported OS.

    ``Path.home()`` consults the account database on Windows and therefore
    ignores a test or deployment that intentionally redirects ``HOME``.  The
    installer treats ``HOME`` as its isolation seam, so prefer it whenever it
    is present and fall back to the platform-native home lookup otherwise.
    """
    configured = os.environ.get("HOME")
    return Path(configured) if configured else Path.home()


# The register(Platform(...)) declarations below provide Click/Typer-like
# declarative configuration: callers look up typed behavior instead of
# branching on harness names.


@dataclass(frozen=True, slots=True)
class SkillRoute:
    """Where one harness loads skills from when autorun does not use the
    shared ``~/.agents/skills`` root.

    One concept, one representation. This replaces three: a ``skills_subdir``
    path fragment, an ``extensions_subdir`` path fragment, and the absence of
    both — which made Codex (skills ship inside its plugin package) and
    ForgeCode (no native route at all) indistinguishable. The reporting code
    could not tell them apart either, so it described ForgeCode as having a
    plugin package that no installer writes.

    ``destinations`` takes the harness's already-resolved config directory
    because resolving it lives in ``install`` and this module must not import
    it. ``describe`` exists so no caller has to reconstruct a route's name
    from the shape of its return value.

    Subclasses override only where a harness genuinely differs, the same
    contract :class:`HookProtocol` uses for wire formats.
    """

    def destinations(
        self, config_dir: Path | None, *, home: Path | None = None
    ) -> tuple[Path, ...]:
        """Return every directory this route writes. Empty means none."""
        return ()

    def describe(self) -> str:
        """Return a phrase naming this route for dry runs and status output."""
        return "no native skill route"


@dataclass(frozen=True, slots=True)
class ConfigDirSkills(SkillRoute):
    """A skills directory inside the harness's own config directory."""

    subdir: str = "skills"

    def destinations(
        self, config_dir: Path | None, *, home: Path | None = None
    ) -> tuple[Path, ...]:
        return () if config_dir is None else (config_dir / self.subdir,)

    def describe(self) -> str:
        return f"config-dir {self.subdir}/"


@dataclass(frozen=True, slots=True)
class ExtensionSkills(SkillRoute):
    """Skills bundled inside an installed extension or plugin directory.

    Gemini and Qwen call the directory ``extensions``; Antigravity calls it
    ``plugins``. All three discover ``<unit>/skills/`` automatically, so the
    only difference is the name of the containing directory.
    """

    subdir: str = "extensions"

    def destinations(
        self, config_dir: Path | None, *, home: Path | None = None
    ) -> tuple[Path, ...]:
        return () if config_dir is None else (config_dir / self.subdir,)

    def describe(self) -> str:
        return f"installed {self.subdir}/"


@dataclass(frozen=True, slots=True)
class HomeDirSkills(SkillRoute):
    """A skills directory addressed from the home directory, not config_dir.

    Needed because two harnesses read roots that are not under their own
    config directory: ForgeCode documents ``~/forge/skills`` (no dot, while its
    config dir is ``~/.forge/``), and OpenCode reads Claude's
    ``~/.claude/skills`` as one of its own tiers. Antigravity's global root,
    ``~/.gemini/config/skills``, likewise sits beside its config dir rather
    than inside it.
    """

    subdir: str = ""

    def destinations(
        self, config_dir: Path | None, *, home: Path | None = None
    ) -> tuple[Path, ...]:
        """Resolve against the process home.

        ``Path.home()`` honours ``$HOME``, which is this repository's isolation
        seam: install tests redirect it with ``monkeypatch.setenv("HOME", ...)``
        and the sandboxed-install recipe does the same. Threading a separate
        home argument through every route would add a second mechanism for one
        question, and an optional one at that — a caller omitting it would
        silently get the real home instead of a type error.
        """
        root = home if home is not None else _process_home()
        return (root / self.subdir,) if self.subdir else ()

    def describe(self) -> str:
        return f"~/{self.subdir}"


@dataclass(frozen=True, slots=True)
class PluginPackageSkills(SkillRoute):
    """Skills carried inside an installed or staged plugin package.

    Claude's installed marketplace package needs no resolver. Codex's staged
    package lives at a configurable path (``codex_plugin_source_dir``, default
    ``~/plugins``), so its ``resolver`` holds the function itself rather than
    its name and construction proves the target is callable.
    """

    resolver: Callable[[Path | None], Path] | None = None

    def destinations(
        self, config_dir: Path | None, *, home: Path | None = None
    ) -> tuple[Path, ...]:
        return () if self.resolver is None else (self.resolver(home),)

    def describe(self) -> str:
        return "installed plugin package"


@dataclass(frozen=True, slots=True)
class CombinedSkillRoutes(SkillRoute):
    """A harness that loads skills from more than one native location.

    No registered harness needs this today, but the capability predates the
    route objects: reading two roots off an ``or`` of two optional fields
    silently dropped the second one, and the guard against that regression is
    older than this class. Composing routes keeps every destination reported
    and gives a new harness variation somewhere to go that is not a new field.
    """

    routes: tuple[SkillRoute, ...] = ()

    def destinations(
        self, config_dir: Path | None, *, home: Path | None = None
    ) -> tuple[Path, ...]:
        def resolve(route: SkillRoute) -> tuple[Path, ...]:
            try:
                return route.destinations(config_dir, home=home)
            except TypeError as error:
                if "unexpected keyword argument 'home'" not in str(error):
                    raise
                # Keep compatibility with third-party route objects written
                # against the pre-Context.home protocol.
                return route.destinations(config_dir)

        return tuple(
            destination
            for route in self.routes
            for destination in resolve(route)
        )

    def describe(self) -> str:
        return " AND ".join(route.describe() for route in self.routes) or (
            SkillRoute.describe(self)
        )


@dataclass(frozen=True, slots=True)
class NoNativeSkillRoute(SkillRoute):
    """The harness has no directory or package autorun can write skills to.

    ForgeCode and OpenCode both read the shared ``~/.agents/skills`` root and
    ship no extension or plugin package. Saying so plainly is the point: the
    previous fallback claimed a plugin package for them, which reads as
    coverage rather than as the gap it is.
    """

    def describe(self) -> str:
        return (
            "no native skill route: this harness has no skills directory and "
            "no plugin package, so nothing is written"
        )


def _codex_plugin_package_dir(home: Path | None = None) -> Path:
    """Return the skill root inside Codex's staged plugin directory."""
    from .installer.discovery import codex_plugin_source

    return codex_plugin_source(home=home) / "skills"


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

    def response_for_unhandled_hook(self, event: str | None = None) -> dict:
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
class AntigravityHookProtocol(HookProtocol):
    """Google Antigravity 2.0's event-specific hook wire contract."""

    @staticmethod
    def _message(response: dict) -> str:
        hook_output = response.get("hookSpecificOutput")
        nested = hook_output if isinstance(hook_output, dict) else {}
        for value in (
            response.get("reason"),
            response.get("systemMessage"),
            nested.get("additionalContext"),
            nested.get("permissionDecisionReason"),
        ):
            if isinstance(value, str) and value:
                return value
        return ""

    @staticmethod
    def _native_handler(handler: dict, event: str) -> dict:
        native = {key: value for key, value in handler.items() if key != "name"}
        command = native.get("command")
        if (
            isinstance(command, str)
            and ("hook_entry.py" in command or "autorun --cli" in command)
            and "--event" not in command
        ):
            native["command"] = f"{command} --event {event}"
        # The shared Gemini manifest stores milliseconds; Antigravity's field
        # is seconds. Five seconds preserves the same wrapper budget.
        if native.get("timeout") == 5000:
            native["timeout"] = 5
        return native

    def translate_manifest(
        self,
        data: dict,
        event_map: Mapping[str, str | None],
    ) -> dict:
        translated = HookProtocol.translate_manifest(self, data, event_map)
        events = translated.get(self.hook_manifest_container_key)
        if not isinstance(events, dict):
            return translated
        for event, entries in events.items():
            if not isinstance(entries, list):
                continue
            native_entries = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                handlers = entry.get("hooks")
                if isinstance(handlers, list):
                    native_entries.append(
                        {
                            "matcher": entry.get("matcher", "*"),
                            "hooks": [
                                self._native_handler(handler, event)
                                for handler in handlers
                                if isinstance(handler, dict)
                            ],
                        }
                    )
                else:
                    native_entries.append(self._native_handler(entry, event))
            events[event] = native_entries
        return translated

    def response_for_unhandled_hook(self, event: str | None = None) -> dict:
        if event == "PreToolUse":
            return {"decision": "allow"}
        if event in {"Stop", "SubagentStop"}:
            return {"decision": ""}
        return {}

    def filter_response_to_harness_schema(
        self,
        event: str,
        response: dict,
    ) -> dict | None:
        """Emit only fields documented for the current native event."""
        if event == "PreToolUse":
            hook_output = response.get("hookSpecificOutput")
            nested = hook_output if isinstance(hook_output, dict) else {}
            decision = (
                response.get("decision")
                or response.get("permissionDecision")
                or nested.get("permissionDecision")
            )
            decision = {
                "approve": "allow",
                "block": "deny",
            }.get(decision, decision)
            reason = self._message(response)
            if decision not in {
                "allow",
                "deny",
                "ask",
                "force_ask",
                "deny_unless_prior_grant",
            }:
                decision = "deny"
                reason = (
                    "[AR_EVENT_V1:invalid_antigravity_pretool_response] "
                    "Antigravity PreToolUse received no valid permission decision; "
                    "the tool was blocked. Restart the autorun daemon, then retry."
                )
            native = {"decision": decision}
            if reason:
                native["reason"] = reason
            overrides = response.get("permissionOverrides")
            if isinstance(overrides, list):
                native["permissionOverrides"] = overrides
            return native
        if event == "PostToolUse":
            return {}
        if event in {"BeforeModel", "UserPromptSubmit", "AfterModel"}:
            message = self._message(response)
            native = (
                {"injectSteps": [{"ephemeralMessage": message}]}
                if message
                else {}
            )
            if event == "AfterModel" and response.get("terminationBehavior") in {
                "force_continue",
                "terminate",
                "",
            }:
                native["terminationBehavior"] = response["terminationBehavior"]
            return native
        if event in {"Stop", "SubagentStop"}:
            native = {
                "decision": "continue"
                if response.get("decision") == "continue"
                else ""
            }
            reason = self._message(response)
            if reason:
                native["reason"] = reason
            return native
        return {}


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

# Google Antigravity 2.0: every event has its own native response schema, and
# invocation/Stop handlers are flat under one named hook container.
# https://www.antigravity.google/docs/hooks
ANTIGRAVITY_HOOKS = AntigravityHookProtocol(
    "antigravity",
    pretool_decision_location="root",
    supports_ask_decision=True,
    root_allow_decision="allow",
    root_block_decision="deny",
    stop_blocking_decision="continue",
    stop_response_uses_only_decision_and_reason=True,
    requires_json_for_unhandled_hook=True,
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

# OpenCode's events arrive over the daemon socket from the in-process JS
# plugin, not from an external hook command. The shim reads the Claude-shaped
# JSON (`hookSpecificOutput.permissionDecision`, falling back to root
# `decision`) and vetoes by throwing, so the base dual-shape response is the
# contract; there is no exit-code channel and no ask dialog to answer, and
# Claude's deny-blanking specialization exists only for its exit-2 stderr
# duplication, which a socket does not have.
OPENCODE_HOOKS = HookProtocol("opencode")

# Pi's TypeScript extension consumes the shared dual-shape response in-process.
# Tool denial becomes `{ block: true, reason }`; Stop rejection is translated
# into a hidden custom continuation message after `agent_settled`.
PI_HOOKS = HookProtocol("pi")


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
    install_flavor: str = ""

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
    # Optional provenance applied by the shared task lifecycle to records from
    # this harness's native task tools. Empty preserves caller metadata.
    task_record_source: str = ""
    # Tools whose result announces a newly spawned subagent, feeding the
    # delegation spawn ledger. The name here is the one serialized into hook
    # stdin, which is not always the one hook config matches on: Codex writes
    # `spawn_agent` and accepts `Agent` only as a matcher alias
    # (codex-rs/core/src/tools/hook_names.rs:46). Empty means this harness
    # exposes no subagent spawn autorun can observe.
    agent_spawn_tools: frozenset[str] = field(default_factory=frozenset)
    # True when this harness can carry a Conductor plan whose conductor/tracks
    # files autorun merges into its own task list. Declared rather than
    # inferred from the harness name so a family member that gains Conductor
    # opts in here instead of being excluded by a string literal.
    aggregates_conductor_tasks: bool = False
    # True when this harness can deliver PreToolUse with a transcript path but
    # skip UserPromptSubmit, so an autorun policy command typed by the user
    # reaches autorun only by reading the transcript. Such a command is applied
    # after the tool call it was meant to authorize, which is why a
    # default-scope allow granted this way also needs a grace window.
    policy_commands_arrive_in_transcript: bool = False
    # True when this harness's session id is not stable across the hook calls
    # belonging to one tool invocation, so scoped-allow fingerprints key on the
    # harness name instead and parallel invocations still match each other.
    fingerprint_ignores_session_id: bool = False
    # Status values this harness's own task-update tool accepts. Guidance must
    # never name a value absent here: the AI would call it and get a hard
    # validation error instead of the action autorun offered it. Empty means
    # "unverified", which routes guidance to autorun's harness-independent
    # marker instead — safe for any harness, including ones not listed above.
    native_task_statuses: frozenset[str] = field(default_factory=frozenset)

    # Native syntax for declaring that one task waits on another, or "" when
    # this harness's task tools expose no dependency parameter. Same rule as
    # native_task_statuses above: guidance that names a parameter the harness
    # rejects costs the AI a failed call instead of the ordering autorun asked
    # for. Codex's update_plan is a flat checklist and the Gemini family's task
    # tools take a title and a status, so only Claude Code declares one.
    task_dependency_syntax: str = ""

    # === Autorun prompt commands ===
    # Autorun command handlers are registered in canonical /ar:* form. Some
    # harnesses accept additional prompt-hook spellings; dispatch normalizes any
    # listed prefix back to /ar:* before handlers run. One shared superset is
    # accepted on EVERY platform so a spelling carried over from another harness
    # still works wherever the harness lets the text reach autorun: slash-colon
    # (Claude/Gemini native), bare colon and space (Codex swallows unknown slash
    # commands before hooks see them), and the dash form ForgeCode/OpenCode
    # command files advertise because ':' is illegal in their filenames.
    # Only command_display_prefix is per-platform: rendered guidance and native
    # autocomplete keep each harness's own spelling.
    command_prefixes: tuple[str, ...] = ("/ar:", "ar:", "ar ", "/ar-", "ar-")
    command_display_prefix: str = "/ar:"
    # One line telling a user how commands reach autorun on THIS harness, shown
    # as the header of `/ar:help`. Written in canonical /ar: form and rendered
    # through format_commands_for_cli, so the display prefix stays the only
    # place a spelling is decided.
    command_invocation_hint: str = "Type /ar:<command>; installed command files appear in completion."
    # How a user invokes an installed Agent Skill here. Claude, Qwen, and
    # Antigravity make every skill a slash command; Codex mentions it with `$`;
    # harnesses with only a model-facing skill tool are asked in words.
    skill_invocation_format: str = "/{name}"

    # === Hook capability ===
    has_hooks: bool = True
    schema_type: str = "strict"  # "strict" | "permissive" | "none"
    # Native protocol identity and the smallest per-harness differences used by
    # the shared response builder and manifest translator.
    hook_protocol: HookProtocol = CLAUDE_HOOKS
    # Native support is not the same as installed ownership. A hook with no
    # autorun handler costs a subprocess per event and can still fail, so the
    # installer registers only the explicit handled subset.
    native_hook_events: frozenset[str] = field(default_factory=frozenset)
    installed_hook_events: frozenset[str] = field(default_factory=frozenset)

    # === Bug workaround applicability ===
    has_exit2_workaround: bool = False  # Claude #4669
    drops_additional_context: bool = False  # Claude #18534
    # Claude #80305/#80401: TaskCreate/Get/Update/List can be gated off at
    # process start, or unregistered mid-session. Declared here rather than
    # compared as a name so a new harness in an affected family inherits the
    # workaround by adding a registry row, the way every other capability does.
    gates_mutable_task_tools: bool = False

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
    # Subdirectory to append when the env var above names a PARENT rather than
    # the config dir itself, as XDG_CONFIG_HOME does. Empty means the variable
    # already holds the config dir (CODEX_HOME, CLAUDE_CONFIG_DIR, ...).
    config_dir_env_var_subdir: str = ""
    # Roots an earlier release wrote to that no current step targets. The
    # installer never writes here; the retirement sweep reads them so an owned
    # tree left on an old route is decided (kept with a reason, or retired)
    # instead of sitting outside every swept root forever.
    retired_config_dirs: tuple[str, ...] = ()
    template_dir: str | None = None
    hooks_path_var: str = ""
    extension_manifest_name: str = "gemini-extension.json"
    extension_hooks_at_root: bool = False
    generates_toml_commands: bool = True
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
    memory_workaround_flag: str = ""
    # True only when the harness's own documentation describes direct discovery
    # of the shared ~/.agents/skills root. This is factual capability data, not
    # a preference: install.resolve_skill_routes() turns it plus the user's
    # --skill-placement value into one route per harness, so a harness that
    # cannot read the shared root never has `auto` point there.
    loads_shared_agents_skills: bool = False
    # Where skills go when this harness does not take the shared-root route.
    # A SkillRoute rather than another path fragment, so "packages its skills"
    # and "has no native route" are distinguishable; see SkillRoute.
    native_skills: SkillRoute = field(default_factory=NoNativeSkillRoute)
    # Every root this harness READS skills from, beyond the shared
    # ~/.agents/skills root that loads_shared_agents_skills already covers.
    # Deliberately separate from native_skills: writing and reading are
    # different questions, and collapsing them is what hid Gemini's
    # ~/.gemini/skills and Qwen's ~/.qwen/skills from duplicate detection.
    # Same route classes, because "a directory holding skills" is one concept.
    skill_search_routes: tuple[SkillRoute, ...] = ()
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

_ANTIGRAVITY_TOOLS = {
    **_GEMINI_TOOLS,
    "grep": "grep_search",
    "glob": "find_by_name",
    "read": "view_file",
    "write": "write_to_file",
    "edit": "replace_file_content",
    "bash": "run_command",
    "ls": "list_dir",
    # Antigravity 2.0 documents no checklist/task CRUD tool. These values are
    # prose fallbacks for shared guidance, not invented API tool identifiers.
    "task_create": "autorun task markers",
    "task_update": "autorun task markers",
    "task_list": "autorun task status",
    "task_progress": "autorun task markers",
    "task_title": "task title",
    "task_id_param": "task id",
}

# OpenCode's model-facing tool ids are lowercase (probed against 1.18.13; the
# shim forwards `input.tool` verbatim in PreToolUse frames). todowrite/todoread
# are its todo-list tools; they matter only once the shim sends PostToolUse,
# so verify them live when that lands.
_OPENCODE_TOOLS = {
    "grep": "grep",
    "glob": "glob",
    "read": "read",
    "write": "write",
    "edit": "edit",
    "bash": "bash",
    "ls": "list",
    "task_create": "todowrite",
    "task_update": "todowrite",
    "task_list": "todoread",
    "task_progress": "todowrite",
    "task_title": "content",
    "task_id_param": "id",
}

# Pi's built-ins are lowercase and include first-class grep/find/ls tools.
_PI_TOOLS = {
    "grep": "grep",
    "glob": "find",
    "read": "read",
    "write": "write",
    "edit": "edit",
    "bash": "bash",
    "ls": "ls",
    "task_create": "autorun task markers",
    "task_update": "autorun task markers",
    "task_list": "autorun task status",
    "task_progress": "autorun task markers",
    "task_title": "task title",
    "task_id_param": "task id",
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
        command_invocation_hint="Type /ar to autocomplete, or /ar:<command> directly.",
        standalone_session_env_vars=("CLAUDE_SESSION_ID",),
        has_hooks=True,
        schema_type="strict",
        hook_protocol=CLAUDE_HOOKS,
        has_exit2_workaround=True,
        drops_additional_context=True,
        gates_mutable_task_tools=True,
        config_dir="~/.claude/",
        config_dir_env_vars=("CLAUDE_CONFIG_DIR",),
        template_dir=None,  # hooks live at plugin root
        hooks_path_var="${CLAUDE_PLUGIN_ROOT}",
        # No dedicated install function: Claude is installed inline through the
        # marketplace and plugin cache. This declared "_install_for_claude" for
        # a function nobody wrote, and capability_snapshot published it.
        skill_search_routes=(ConfigDirSkills("skills"),),
        memory_filename="CLAUDE.md",
        memory_template="claude_template/CLAUDE.md",
        memory_workaround_flag=(
            "AUTORUN_BUG_CLAUDE_CODE_NO_TOKEN_COUNT_FOR_HOOKS_BUG_54673_"
            "WORKAROUND_ENABLED"
        ),
        memory_sentinel_slug="claude-memory-md",
        # The installed plugin already exposes its skills. A second global copy
        # under ~/.claude/skills is also scanned by OpenCode, which then races
        # it against the canonical ~/.agents/skills copy by name.
        native_skills=PluginPackageSkills(),
        list_cmd=("claude", "plugin", "list"),
        app_bundle_ids=("com.anthropic.claudefordesktop",),
        app_paths=("/Applications/Claude.app",),
        tool_names=_CLAUDE_TOOLS,
        task_management_style="task_tools",
        task_create_tools=frozenset({"TaskCreate"}),
        task_update_tools=frozenset({"TaskUpdate"}),
        agent_spawn_tools=frozenset({"Agent", "Task"}),
        # TaskUpdate declares addBlockedBy and addBlocks.
        task_dependency_syntax="{task_update}({task_id_param}=N, addBlockedBy=[M])",
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


# Google retired the standalone Gemini CLI on 2026-06-18 for free, Pro, and
# Ultra personal accounts, consolidating on Antigravity CLI (`agy`). Enterprise
# Gemini Code Assist licences and paid API keys still run the old binary, which
# is why this entry stays and why install_by_default is False. The QWEN and
# ANTIGRAVITY entries carry the live traffic; Qwen Code is itself a fork of
# Gemini CLI v0.8.2, which is why all three share these event names.
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
        skill_search_routes=(ConfigDirSkills("skills"),),
        # https://geminicli.com/docs/cli/using-agent-skills/ — Gemini's
        # discovery tiers include the shared ~/.agents/skills root, so the
        # extension copy is a duplicate rather than the only route.
        loads_shared_agents_skills=True,
        list_cmd=("gemini", "extensions", "list"),
        native_skills=ExtensionSkills("extensions"),
        extensions_subdir="extensions",
        uninstall_cmd=("gemini", "extensions", "uninstall", "{name}"),
        tool_names=_GEMINI_TOOLS,
        task_management_style="bulk_todos",
        task_create_tools=frozenset({"task_create", "tracker_create_task"}),
        aggregates_conductor_tasks=True,
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
            # https://www.antigravity.google/docs/hooks
            "PreToolUse": "PreToolUse",
            "PostToolUse": "PostToolUse",
            "PreInvocation": "BeforeModel",
            "PostInvocation": "AfterModel",
            "Stop": "Stop",
        },
        autorun_to_harness_cli_events={
            "PreToolUse": "PreToolUse",
            "PostToolUse": "PostToolUse",
            "BeforeModel": "PreInvocation",
            "AfterModel": "PostInvocation",
            "Stop": "Stop",
        },
        has_hooks=True,
        schema_type="permissive",
        hook_protocol=ANTIGRAVITY_HOOKS,
        native_hook_events=frozenset(
            {
                "PreToolUse",
                "PostToolUse",
                "PreInvocation",
                "PostInvocation",
                "Stop",
            }
        ),
        installed_hook_events=frozenset(
            {"PreToolUse", "PostToolUse", "Stop"}
        ),
        has_exit2_workaround=False,
        drops_additional_context=False,
        # Agy 1.1.7 materializes native plugins and its import receipt under
        # ~/.gemini/config.  ~/.gemini/antigravity-cli remains a runtime/log
        # location and therefore stays only in detection hints — and in the
        # retirement sweep, because the install.py lifecycle (2026-07-09 to
        # 2026-08-08) also materialized the bundle under
        # ~/.gemini/antigravity-cli/plugins/<plugin>.
        config_dir="~/.gemini/config/",
        retired_config_dirs=("~/.gemini/antigravity-cli/",),
        template_dir="gemini_template",
        hooks_path_var="${extensionPath}",
        extension_manifest_name="plugin.json",
        extension_hooks_at_root=True,
        skill_search_routes=(HomeDirSkills(".gemini/config/skills"),),
        list_cmd=("agy", "plugin", "list"),
        native_skills=ExtensionSkills("plugins"),
        extensions_subdir="plugins",
        uninstall_cmd=("agy", "plugin", "uninstall", "{name}"),
        tool_names=_ANTIGRAVITY_TOOLS,
        task_management_style="none",
        supports_additional_context_events=frozenset({"BeforeModel", "AfterModel"}),
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
        skill_search_routes=(ConfigDirSkills("skills"),),
        # Qwen Code's Storage.getUserSkillsDirs() maps
        # SKILL_PROVIDER_CONFIG_DIRS = [".qwen", ".agents"] over os.homedir(),
        # so ~/.agents/skills is a user-scope discovery root alongside
        # ~/.qwen/skills (verified in the shipped 0.21.1 bundle; upstream
        # QwenLM/qwen-code#2042, closed completed 2026-03-03).
        loads_shared_agents_skills=True,
        list_cmd=("qwen", "extensions", "list"),
        # Declared alongside loads_shared_agents_skills on purpose. The two are
        # a capability and a fact, not a contradiction: `auto` resolves to
        # exactly one route (the shared root, because Qwen reads it), and this
        # field says where a native copy goes when one is actually needed —
        # under `--skill-placement native`/`both`, or as the per-name fallback
        # when something that is not a loadable skill blocks the shared route.
        #
        # Retiring the field was tried and reverted: it made a blocked name
        # reach Qwen by no route at all, silently dropping that skill. The
        # duplicate exposure that prompted it had a different cause —
        # `if shared_conflicts: include_skills = True` republished *every*
        # skill of *every* plugin natively after a single collision — and that
        # is fixed per name. A user-authored loadable skill on the shared root
        # no longer triggers the fallback at all: Qwen already sees the user's
        # copy, so a native copy would list the name twice
        # (skills.skill_plan's visible_via_shared).
        native_skills=ExtensionSkills("extensions"),
        generates_toml_commands=False,
        extensions_subdir="extensions",
        uninstall_cmd=("qwen", "extensions", "uninstall", "{name}"),
        tool_names=_GEMINI_TOOLS,
        task_management_style="bulk_todos",
        task_create_tools=GEMINI.task_create_tools,
        task_update_tools=GEMINI.task_update_tools,
        task_review_tools=GEMINI.task_review_tools,
        task_bulk_tools=GEMINI.task_bulk_tools,
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
        skill_search_routes=(ConfigDirSkills("skills"),),
        memory_filename="AGENTS.md",
        memory_template="codex_template/AGENTS.md",
        # https://learn.chatgpt.com/docs/build-skills — Codex scans the shared
        # $HOME/.agents/skills root directly, so it needs no second copy.
        native_skills=PluginPackageSkills(_codex_plugin_package_dir),
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
        agent_spawn_tools=frozenset({"spawn_agent"}),
        policy_commands_arrive_in_transcript=True,
        fingerprint_ignores_session_id=True,
        command_display_prefix="ar:",
        command_invocation_hint=(
            "Type /ar:<command>. Codex keeps its own slash menu closed, so a "
            "leading slash never reaches autorun; skills answer to $name."
        ),
        skill_invocation_format="${name}",
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


PI = register(
    Platform(
        name="pi",
        display_name="Pi",
        binary="pi",
        detect_env_vars=("PI_CODING_AGENT", "PI_SESSION_ID"),
        detect_session_keys=("PI_SESSION_ID",),
        standalone_session_env_vars=("PI_SESSION_ID",),
        detect_path_hints=(".pi/agent",),
        has_hooks=True,
        schema_type="strict",
        hook_protocol=PI_HOOKS,
        tool_names=_PI_TOOLS,
        task_management_style="task_tools",
        task_create_tools=frozenset({"TaskCreate"}),
        task_update_tools=frozenset({"TaskUpdate"}),
        task_review_tools=frozenset({"TaskList", "TaskGet"}),
        task_record_source="pi_task_tool",
        task_dependency_syntax="{task_update}({task_id_param}=N, addBlockedBy=[M])",
        native_task_statuses=frozenset(
            {"pending", "in_progress", "completed", "deleted"}
        ),
        config_dir="~/.pi/agent/",
        config_dir_env_vars=("PI_CODING_AGENT_DIR",),
        skill_search_routes=(ConfigDirSkills("skills"),),
        memory_filename="AGENTS.md",
        memory_template="pi_template/AGENTS.md",
        memory_sentinel_slug="pi-agents-md",
        loads_shared_agents_skills=True,
        native_skills=ConfigDirSkills("skills"),
        command_display_prefix="/ar ",
        command_invocation_hint="Type /ar <command>; the installed extension dispatches it directly.",
        skill_invocation_format="/skill:{name}",
        supports_additional_context_events=frozenset(
            {"SessionStart", "UserPromptSubmit", "PostToolUse"}
        ),
        autorun_to_harness_cli_events={
            "PreToolUse": "PreToolUse",
            "PostToolUse": "PostToolUse",
            "UserPromptSubmit": "UserPromptSubmit",
            "Stop": "Stop",
            "SessionStart": "SessionStart",
            "SessionEnd": "SessionEnd",
            "PreCompact": "PreCompact",
            "PostCompact": "PostCompact",
        },
        native_hook_events=frozenset(
            {
                "session_start",
                "before_agent_start",
                "tool_call",
                "tool_result",
                "session_before_compact",
                "session_compact",
                "session_tree",
                "agent_start",
                "agent_settled",
                "session_shutdown",
            }
        ),
        installed_hook_events=frozenset(
            {
                "SessionStart",
                "UserPromptSubmit",
                "PreToolUse",
                "PostToolUse",
                "PreCompact",
                "PostCompact",
                "Stop",
                "SessionEnd",
            }
        ),
    )
)


# Prime's wire contract is Pi's; ``replace`` (not a fresh HookProtocol) so any
# future non-default PI_HOOKS field reaches Prime the way every other PI field
# does through ``PRIME = replace(PI, ...)`` below.
PRIME_HOOKS = replace(PI_HOOKS, name="prime")

# Prime Agent is PrimeIntellect's build of the Pi coding agent: the shipped
# 0.7.1 bundle keeps Pi's runtime (its launcher's helper is named
# __piBundleCreateRequire and it sets PI_CODING_AGENT=true in subprocesses)
# and rebrands the config dir via pkg.piConfig = {name: "prime-agent",
# configDir: ".prime/agent"}. Everything except identity, discovery paths,
# and the wire label is therefore inherited from PI verbatim: same template,
# same extension step, same event and tool contracts.
PRIME = register(
    replace(
        PI,
        name="prime",
        display_name="Prime Agent",
        binary="prime-agent",
        # Prime sets PI_CODING_AGENT (Pi's spelling) even in its own build,
        # so no environment signal distinguishes the two. Prime claims none:
        # the installed extension always passes cliType explicitly, and
        # transcript paths carry .prime/agent for the path-hint route. Its
        # env prefix is PRIME_AGENT (from piConfig.name), which names the
        # config-dir override below.
        detect_env_vars=(),
        detect_session_keys=(),
        standalone_session_env_vars=(),
        detect_path_hints=(".prime/agent",),
        config_dir="~/.prime/agent/",
        config_dir_env_vars=("PRIME_AGENT_CODING_AGENT_DIR",),
        hook_protocol=PRIME_HOOKS,
        task_record_source="prime_task_tool",
        memory_sentinel_slug="prime-agents-md",
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
        skill_search_routes=(HomeDirSkills("forge/skills"),),
        memory_filename="AGENTS.md",
        memory_template="forgecode_template/AGENTS.md",
        memory_sentinel_slug="forgecode-agents-md",
        # Commands arrive as files whose name IS the invocation, and ':' is
        # illegal in ForgeCode command filenames, so the shipped documents are
        # ar-<cmd>.md. Guidance must teach the spelling those files create.
        command_display_prefix="/ar-",
        command_invocation_hint=(
            "Type /ar:<command>. ForgeCode delivers no events to autorun, so "
            "only the installed command files run and guards stay advisory."
        ),
        # ForgeCode exposes skills through a model-facing `skill` tool, with no
        # user-typed invocation of its own.
        skill_invocation_format="the {name} skill",
        # https://forgecode.dev/docs/skills/ documents ~/.agents/skills as the
        # "agents" tier between project (.forge/skills) and global, and
        # forge_domain's Env::agents_skills_path() resolves it as
        # home.join(".agents/skills") (string present in the shipped binary).
        # ForgeCode ships no extension or plugin package, so without this the
        # shared root is its only route and `auto` would point at nothing.
        loads_shared_agents_skills=True,
        # tool_names empty: not relevant without hooks (advisory AGENTS.md only)
    )
)


OPENCODE = register(
    Platform(
        name="opencode",
        display_name="OpenCode",
        binary="opencode",
        # OPENCODE_CONFIG / OPENCODE_CONFIG_DIR are worth detecting because a
        # user who set them is running OpenCode, but neither relocates the
        # config dir — see config_dir_env_vars below. No session env var is
        # documented, so standalone identity needs --session or
        # AUTORUN_SESSION_ID like the Claude fallback.
        detect_env_vars=("OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR"),
        detect_path_hints=(".opencode", ".config/opencode"),
        # Enforced through the installed JS plugin: `tool.execute.before`
        # carries every tool call to the daemon socket and throws on deny, in
        # the role external hook commands play elsewhere. autorun ships one
        # plugin file for this harness only: OpenCode runs on Bun and loads it
        # in-process, so its users already have that runtime. No other
        # platform may pull in a second runtime — Python is the only one
        # autorun requires everywhere else.
        has_hooks=True,
        schema_type="strict",
        hook_protocol=OPENCODE_HOOKS,
        tool_names=_OPENCODE_TOOLS,
        config_dir="~/.config/opencode/",
        # Probed against opencode 1.18.13: with OPENCODE_CONFIG_DIR pointed at
        # an empty directory, `opencode serve` still loaded
        # ~/.config/opencode/opencode.json; with XDG_CONFIG_HOME set it loaded
        # <XDG>/opencode/opencode.json. XDG_CONFIG_HOME names the parent, so
        # the harness subdirectory is appended.
        config_dir_env_vars=("XDG_CONFIG_HOME",),
        config_dir_env_var_subdir="opencode",
        skill_search_routes=(
            ConfigDirSkills("skills"),
            HomeDirSkills(".claude/skills"),
        ),
        memory_filename="AGENTS.md",
        memory_template="opencode_template/AGENTS.md",
        memory_sentinel_slug="opencode-agents-md",
        # OpenCode reads ~/.agents/skills natively (opencode.ai/docs/skills),
        # so skills arrive through the shared route with no per-harness link.
        loads_shared_agents_skills=True,
        # Commands are Claude-format markdown ($ARGUMENTS, $1..$N, !`cmd`,
        # @path) copied from the portable bundle; the files are named
        # ar-<cmd>.md, so typed invocation stays /ar-<cmd>, and every other
        # control command reaches the daemon through the plugin's registered
        # `autorun` tool.
        command_display_prefix="/ar-",
        command_invocation_hint=(
            "Type /ar:<command> for the installed files; every other control "
            "command reaches autorun through the `autorun` tool. Blocked "
            "tools are vetoed in-process by the autorun plugin."
        ),
        # OpenCode also reaches skills through its native `skill` tool.
        skill_invocation_format="the {name} skill",
        task_management_style="bulk_todos",
        task_bulk_tools=frozenset({"todowrite"}),
        task_review_tools=frozenset({"todoread"}),
        task_record_source="opencode_todo",
        native_task_statuses=frozenset({"pending", "in_progress", "completed", "deleted"}),
        supports_additional_context_events=frozenset({"PostToolUse"}),
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


def agent_spawn_tools_for(cli_type: "str | None") -> frozenset[str]:
    """Return the spawn-tool names to trust for one harness.

    Mirrors :func:`task_tool_role`: a known harness answers only for its own
    names, so a payload naming another harness's spawn tool cannot seed this
    one's ledger. An unknown or custom harness falls back to every declared
    name, because recording a real spawn matters more than excluding a
    hypothetical forgery from a harness nobody has registered.
    """
    platform = get_platform(cli_type or "")
    if platform is not None and platform.agent_spawn_tools:
        return platform.agent_spawn_tools
    if platform is not None:
        return frozenset()
    return frozenset().union(*(p.agent_spawn_tools for p in PLATFORMS.values()))
