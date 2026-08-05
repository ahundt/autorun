"""One authority for which hook-event names each harness accepts.

Three suites (test_hooks_format.py, test_split_layout.py,
test_dual_cli_pathways.py) validate shipped hooks manifests against these
sets, and an unknown event name in a Claude-scanned manifest is what bug
#24115 turns into a silent disable of every plugin hook. The sets therefore
live once, here: the per-file copies drifted apart — PostCompact and five
other valid names were each present in some copies and absent from others.

CLAUDE_CODE_VALID_EVENTS mirrors the HOOK_EVENTS list verified against
Claude Code 2.1.88 (27 names; docs/claude-code-hooks-api.md and
https://code.claude.com/docs/en/plugins-reference describe the same
surface). GEMINI_CLI_VALID_EVENTS follows docs/gemini-cli-hooks-api.md.

Membership means the harness ACCEPTS the name in a manifest. Subscribing
autorun to a new event stays a separate, per-event decision with its own
source — never add a subscription just because the name is valid.
"""

CLAUDE_CODE_VALID_EVENTS = frozenset({
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Notification",
    "UserPromptSubmit",
    "SessionStart",
    "SessionEnd",
    "Stop",
    "StopFailure",
    "SubagentStart",
    "SubagentStop",
    "PreCompact",
    "PostCompact",
    "PermissionRequest",
    "PermissionDenied",
    "Setup",
    "TeammateIdle",
    "TaskCreated",
    "TaskCompleted",
    "Elicitation",
    "ElicitationResult",
    "ConfigChange",
    "WorktreeCreate",
    "WorktreeRemove",
    "InstructionsLoaded",
    "CwdChanged",
    "FileChanged",
})

GEMINI_CLI_VALID_EVENTS = frozenset({
    "BeforeTool",
    "AfterTool",
    "BeforeAgent",
    "AfterAgent",
    "BeforeModel",
    "AfterModel",
    "BeforeToolSelection",
    "SessionStart",
    "SessionEnd",
    "Notification",
    "PreCompress",
})
