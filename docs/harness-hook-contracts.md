# Harness hook contracts

Read on 2026-11-12. Installed-version evidence is used where available. Current official documentation and changelogs are included because several installed clients lag their published contract.

## Legend

Each event cell states whether the harness emits the event and what its output can do:

- `B`: block the operation and return a reason.
- `N`: show a user-visible notice without blocking.
- `C`: inject context into the model or conversation.
- `AR`: what autorun currently installs or bridges.
- `Gap`: supported behavior autorun does not use.

`Not established` means the cited source does not define the behavior and no installed source settled it.

## Sources

| ID | Harness source, version, and read date |
|---|---|
| C1 | Claude Code hooks reference, <https://code.claude.com/docs/en/hooks>, read 2026-11-12; installed Claude Code 2.1.322. |
| C2 | Claude Code changelog, <https://code.claude.com/docs/en/changelog>, release 2.1.322, read 2026-11-12. |
| X1 | Codex hooks reference, <https://developers.openai.com/codex/hooks>, read 2026-11-12; installed Codex CLI 0.167.0. |
| G1 | Gemini CLI hooks reference, <https://github.com/google-gemini/gemini-cli/blob/main/docs/hooks/reference.md>, read 2026-11-12; installed Gemini CLI 0.51.0. |
| G2 | Gemini CLI v0.68.0 changelog and PR #30111, <https://github.com/google-gemini/gemini-cli/blob/main/docs/changelogs/latest.md> and <https://github.com/google-gemini/gemini-cli/pull/30111>, released 2026-11-10, read 2026-11-12. |
| Q1 | Qwen Code hooks reference, <https://qwenlm.github.io/qwen-code-docs/en/users/features/hooks/>, read 2026-11-12. Installed Qwen Code 0.21.15: `bundled/qc-helper/docs/features/hooks.md`, `chunks/chunk-4W4BROUS.js`, and `chunks/chunk-L7RG5PVT.js`; upstream tag `v0.21.15`, commit `5dce251`. |
| A1 | Antigravity hooks reference, <https://antigravity.google/docs/hooks>, read 2026-11-12; installed Agy 1.1.12 embedded hook reference and protobuf symbols in `/opt/homebrew/bin/agy`. |
| A2 | Antigravity engine changelog, <https://www.antigravity.google/changelog?tab=engine>, read 2026-11-12. |
| O1 | OpenCode plugin documentation, <https://opencode.ai/docs/build/plugins>, and V1 migration guide, <https://opencode.ai/docs/build/plugins/migrate-v1>, read 2026-11-12; installed OpenCode 1.18.31. |
| P1 | Pi extension API, <https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md>, read 2026-11-12; installed Pi 1.5.2 at `/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent/docs/extensions.md`. |
| P2 | Pi changelog, <https://github.com/earendil-works/pi/blob/main/packages/coding-agent/CHANGELOG.md>, version 1.5.2 dated 2026-11-10, read 2026-11-12. |
| R1 | Prime Agent extension documentation, <https://github.com/PrimeIntellect-ai/prime-agent/blob/main/packages/coding-agent/docs/extensions.md>, and changelog, <https://github.com/PrimeIntellect-ai/prime-agent/blob/main/CHANGELOG.md>, read 2026-11-12. Installed Prime Agent is 0.7.1; current published changelog is 0.10.2 dated 2026-10-28. |
| F1 | ForgeCode commands documentation, <https://forgecode.dev/docs/commands/>, and lifecycle-hook proposal <https://github.com/tailcallhq/forgecode/issues/2562>, closed as not planned; installed ForgeCode 2.13.21. Read 2026-11-12. |
| AR1 | autorun registry and protocol implementation: `plugins/autorun/src/autorun/platforms.py`, shared manifests under `plugins/autorun/hooks/` and `src/autorun/gemini_template/hooks/`, read at commit `7f642ff2`. |
| AR2 | Pi/Prime bridge: `plugins/autorun/src/autorun/pi_template/extensions/autorun/index.ts`; OpenCode bridge: `plugins/autorun/src/autorun/opencode_template/plugin/autorun.js`, read at commit `7f642ff2`. |

## Event and output matrix

### Claude Code

| Event | Contract, autorun behavior, and gap |
|---|---|
| SessionStart | Emits on `startup`, `resume`, `clear`, `compact`, and `fork`. `N=systemMessage`; `C=hookSpecificOutput.additionalContext`; startup is advisory. AR subscribes and treats `fork` as a fork source. No gap found for the outputs autorun uses. [C1][AR1] |
| UserPromptSubmit | Emits before prompt processing, including harness-authored turns. The event has no matcher filter, so ordinary prompts reach AR; the former `/ar:` field in AR's manifest was ignored and has been removed. `B=decision:block`; `N=systemMessage`; `C=additionalContext`. AR distinguishes harness-authored wrappers and can block the cache-expiry prompt check. [C1][AR1] |
| PreToolUse | Emits before each tool. `B=hookSpecificOutput.permissionDecision=deny` with `permissionDecisionReason`; `ask` is supported. AR subscribes and preserves the nested denial even when the Claude exit-2 workaround suppresses duplicate JSON text. [C1][AR1] |
| PostToolUse | Emits after successful tools. `N=systemMessage`; `C=additionalContext`; blocking cannot undo the tool. AR subscribes and uses it for task reminders and context. [C1][AR1] |
| Stop | Emits when the main agent finishes. `B=decision:block` plus `reason`; `N=systemMessage`; `C=additionalContext`. AR subscribes; blocked Stops use one reason channel and allowed Stops keep notices. Stop input includes `background_tasks` and `session_crons`; AR parses both but deliberately does not treat either as running-subagent proof because the arrays can be stale or ambient. [C1][AR1] |
| SubagentStop | Emits when a subagent finishes and includes `agent_id`, `agent_type`, `background_tasks`, and `session_crons`. Output control matches Stop. AR subscribes and blocks or allows through the Claude protocol. The arrays are deliberately not running-agent evidence: idle teammate entries can remain `status=running`, ambient tasks can appear, and crons are scheduled wakeups. `stop_payload_lists_running_agents` therefore remains unset, pinned by `claude-2.1.322-background-work.json`. [C1][AR1] |
| PermissionRequest | Emits when a permission dialog is about to appear. `B/C` use `hookSpecificOutput.decision`; user-facing text is part of the decision message. AR has a protocol shape and registry mapping but the shared manifest does not subscribe. Gap: add a handler only after defining how it composes with the earlier PreToolUse decision so a second hook cannot weaken a denial. [C1][AR1] |
| Compact | `PreCompact` and `PostCompact` emit. PreCompact is advisory; notices use `systemMessage`, and context is event-specific. AR subscribes to both and dispatches its compact handlers. [C1][AR1] |

### Codex CLI

| Event | Contract, autorun behavior, and gap |
|---|---|
| SessionStart | Emits on session start/resume. `N=systemMessage`; `C=hook-specific additionalContext`; common fields are strict. AR subscribes through the shared manifest and filters to Codex's event schema. [X1][AR1] |
| UserPromptSubmit | Emits during a turn. AR's generated hook entry has no matcher, so ordinary prompts reach the daemon. `B` is supported; `N=systemMessage`; `C=additionalContext`. AR also replays policy commands from the rollout when prompt hooks are absent. [X1][AR1] |
| PreToolUse | Emits before tools. `B=decision:block` and nested `permissionDecision=deny`; the reason stays in the nested field so Codex does not render duplicate rows. AR subscribes and fails closed on malformed denial output. [X1][AR1] |
| PostToolUse | Emits after tools. `N=systemMessage`; `C=additionalContext`; a block is feedback only. AR subscribes and strips fields Codex rejects. [X1][AR1] |
| Stop | Emits at main-thread completion. `B=decision:block`; `N=systemMessage`; common output is strict. AR subscribes and uses compact block output or allowed notices. [X1][AR1] |
| SubagentStop | Emits with the parent `session_id`; `agent_id`, `agent_type`, and `agent_transcript_path` identify the child. A block continues the subagent flow. AR subscribes, preserves the child fields, and uses `agent_id` plus the spawn receipt to return only that child's delegated task to parent verification. [X1][AR1] |
| PermissionRequest | Emits before a permission decision. AR has the nested decision serializer and a registry mapping, but the shared manifest does not subscribe. The same composition proposal as Claude applies. [X1][AR1] |
| Compact | `PreCompact` and `PostCompact` emit for `manual` or `auto` triggers and accept common output. AR now installs both events and lists their identity mappings in the Codex registry, so generated hooks and capability validation share one explicit contract. [X1][AR1] |

### Legacy Gemini CLI

| Event | Contract, autorun behavior, and gap |
|---|---|
| SessionStart | Emits and accepts `systemMessage` plus `additionalContext`. AR subscribes. [G1][AR1] |
| BeforeAgent | Prompt event. Gemini 0.51.0 invokes it without matcher context, so the old `/ar:` matcher was ignored; AR now omits the matcher to state the catch-all contract explicitly. Every ordinary prompt reaches the awaiting-user restore and prompt-time cache handlers. `B=decision:deny`; `C=additionalContext`; notices use common output. [G1][AR1] |
| BeforeTool | `B=decision:deny`; reason and context are supported. AR subscribes and normalizes unsupported `ask` to deny. [G1][AR1] |
| AfterTool | Emits after tools; `C=additionalContext`; output cannot undo the tool. AR subscribes. [G1][AR1] |
| AfterAgent | Stop event. `B=decision:deny` triggers retry; allowed common output can carry a notice. AR subscribes. [G1][AR1] |
| SubagentStop | Neither installed 0.51.0 nor current 0.68.0 exposes a native SubagentStop event. The 0.68 change accepts Claude's `SubagentStop` spelling only in `gemini hooks migrate` and merges that configuration under Gemini's existing AfterAgent event. AR correctly keeps `SubagentStop` out of the Gemini manifest; adding it would be invalid on both versions. AfterAgent carries no documented field that distinguishes a child completion, so AR cannot safely update the delegation ledger from it. [G1][G2][AR1] |
| PermissionRequest | Not established in the installed 0.51.0 hook reference. No AR subscription. [G1][AR1] |
| PreCompress | Emits before compression and is advisory. AR subscribes as `PreCompact`. [G1][AR1] |

### Qwen Code

| Event | Contract, autorun behavior, and gap |
|---|---|
| SessionStart | Emits. `N=systemMessage`; `C=additionalContext`. AR subscribes. [Q1][AR1] |
| UserPromptSubmit | Emits before the prompt and has no matcher target. AR's Qwen protocol strips matcher fields, so every ordinary prompt reaches the awaiting-user restore and prompt-time cache handlers; the shared source manifest now omits the stale matcher too. `C=additionalContext`; standard fields remain available. [Q1][AR1] |
| PreToolUse | Emits before tools. The official output is nested `permissionDecision` plus `permissionDecisionReason`; `allow`, `deny`, and `ask` are supported. AR subscribes and converts malformed or unknown decisions to deny. [Q1][AR1] |
| PostToolUse | Emits after tools. `C=additionalContext`; common `systemMessage` is available. AR subscribes. [Q1][AR1] |
| Stop | Emits before the response ends. `B=decision:block`; `N=systemMessage`; `C=additionalContext`. AR now preserves common fields on allowed Stops and keeps blocking responses compact. Waiting-user, delegation, fork, and loop-guard notices therefore appear as Qwen hook system messages. [Q1][AR1] |
| SubagentStop | Emits and accepts `decision`/`reason` plus common output. AR clones the Stop handler and now preserves allowed notices there too. [Q1][AR1] |
| PermissionRequest | Emits and accepts `hookSpecificOutput.decision`. AR has a serializer and registry mapping but the translated manifest does not subscribe. Use the same denial-composition proposal as Claude and Codex. [Q1][AR1] |
| PreCompact | Emits as `PreCompact`; `C=additionalContext`. AR translates `PreCompress` to it and subscribes. [Q1][AR1] |

### Google Antigravity

| Event | Contract, autorun behavior, and gap |
|---|---|
| SessionStart | Emits before the first model invocation. Output is `injectSteps`; startup cannot be blocked. AR installs the event, converts startup notices/context to an `ephemeralMessage`, and consumes one queued allowed-Stop notice here if this is the next supported injection point. [A1][AR1] |
| Prompt | `PreInvocation` supplies `invocationNum`, where the first model invocation is `0`, plus `lastUserInput`. AR installs it and maps only `invocationNum=0` to UserPromptSubmit; later invocations remain BeforeModel. Typed-prompt command checks, awaiting-user restore, and cache warnings therefore run once, while later model calls cannot impersonate a user prompt. [A1][AR1] |
| PreToolUse | Emits before tools. `B=decision:deny`; `reason` and `permissionOverrides` are supported. AR subscribes and fails closed when no valid decision survives filtering. [A1][AR1] |
| PostToolUse | Emits after tools and defines no control output used by autorun. AR subscribes for observation and emits `{}`. [A1][AR1] |
| Stop | Emits with `fullyIdle`. `B=decision:continue`; `reason` is injected only when continuing. AR blocks correctly. Because allowed Stop has no notice field, AR atomically queues its notice and injects it exactly once as an `ephemeralMessage` at the next SessionStart or PreInvocation; a queue-write failure converts the Stop to `continue` with the notice and persistence error instead of dropping it. [A1][AR1] |
| SubagentStop | No distinct event is documented. Stop `fullyIdle=false` means active background commands or asynchronous tasks still run; it does not identify a child or distinguish subagents from other asynchronous work. AR records that inverse boolean through `Platform.stop_payload_lists_running_agents` and does not declare a delegation abandoned while it is false. [A1][AR1] |
| PermissionRequest | No permission-request hook is documented. PreToolUse is the permission gate. [A1] |
| Compact | No compression hook is documented. [A1] |

### OpenCode

| Event | Contract, autorun behavior, and gap |
|---|---|
| SessionStart | Current plugin APIs expose session/context hooks, but autorun only sends an `OpenCodeAttach` frame. Startup guidance instead comes from the installed `AGENTS.md`. Gap: migrate the plugin to the documented session hook and deliver SessionStart context. [O1][AR2] |
| Prompt | OpenCode 1.18.31 V1 `chat.message` runs before durable prompt admission with mutable message parts. AR forwards UserPromptSubmit with `prompt_author=user` (or `automation` for its own Stop continuation), blocks if the daemon returns a denial, and appends visible daemon feedback as a synthetic notice part. User-only command checks, awaiting-user restore, and cache warnings now run on typed prompts. [O1][AR2] |
| PreToolUse | `tool.execute.before` can veto by throwing. AR subscribes, asks the daemon, and throws the denial reason. [O1][AR2] |
| PostToolUse | OpenCode emits general tool events, but AR forwards only `todo.updated` as a synthetic PostToolUse for task state. Gap: add a general after-tool route if the current API provides stable result mutation; otherwise keep task-only forwarding. [O1][AR2] |
| Stop | The public event schema emits `session.idle` with `sessionID`. AR forwards Stop there; when blocked, it feature-detects `client.session.promptAsync` and submits a synthetic autorun continuation message to `/session/{id}/prompt_async`. If an older client lacks that method, the bridge preserves its former no-continuation behavior instead of throwing. [O1][AR2] |
| SubagentStop | Not established in the official plugin pages read. No AR route. [O1][AR2] |
| PermissionRequest | No separate route is used; PreToolUse veto is the enforced gate. [O1][AR2] |
| Compact | OpenCode 1.18.31 V1 `experimental.session.compacting` runs before summary generation and accepts added context; `session.compacted` emits after success. AR forwards PreCompact, appends daemon context, and forwards PostCompact on the completion event. [O1][AR2] |

### Pi

| Event | Contract, autorun behavior, and gap |
|---|---|
| session_start | Emits with reasons including `startup`, `reload`, `new`, `resume`, and `fork`. AR forwards SessionStart; startup text is now a displayed custom message queued with `deliverAs:nextTurn`, so it enters the next model context without starting a turn. [P1][P2][AR2] |
| before_agent_start | Emits after input expansion and before the run. It can return a displayed custom message to the model. AR forwards UserPromptSubmit. Pi cannot block the prompt through this return shape, so cache enforcement is a notice on Pi. [P1][AR2] |
| tool_call | Can block with `{block:true, reason}` and can mutate input. AR forwards PreToolUse and blocks with the daemon reason. [P1][AR2] |
| tool_result | Can replace or append result content. AR forwards PostToolUse, appends reminders, and fails the task-tool result when the daemon did not confirm a task mutation. [P1][AR2] |
| agent_settled | Final-idle signal with an `aborted` flag. AR ignores aborted settles so cancellation cannot be immediately restarted. Otherwise it forwards Stop; a block queues one hidden continuation with `triggerTurn:true`, while allowed response text is shown through `ctx.ui.notify`. [P1][AR2] |
| SubagentStop | Pi subagent children use the same `agent_settled` event and are identified by the extension environment. AR forwards SubagentStop and does not start a continuation from the child adapter. [P1][AR2] |
| PermissionRequest | No separate extension event is needed for autorun; `tool_call` is the blocking gate. [P1][AR2] |
| Compact | `session_before_compact` can cancel/customize and `session_compact` reports completion. AR forwards PreCompact and PostCompact. [P1][AR2] |

### Prime Agent

| Event | Contract, autorun behavior, and gap |
|---|---|
| SessionStart | Installed Prime Agent 0.7.1 uses the Pi-compatible runtime and the same autorun template, so context is queued for the next turn and displayed. Prime 0.10.x has no Pi extension route, and autorun refuses this integration. [R1][AR1][AR2] |
| Prompt | Installed 0.7.1 matches Pi `before_agent_start`: notice/context, no prompt block. Prime 0.10.x has no Pi extension route, so autorun refuses this integration. [R1][AR2] |
| PreToolUse | Installed 0.7.1 matches Pi `tool_call` and blocks with a reason. Prime 0.10.x has no Pi extension route. [R1][AR2] |
| PostToolUse | Installed 0.7.1 matches Pi `tool_result`. Prime 0.10.x has no Pi extension route. [R1][AR2] |
| Stop | Installed 0.7.1 matches Pi `agent_settled`. Prime 0.10.2 is a Rust runtime without the Pi coding-agent extension package; autorun pins an exclusive 0.10.0 ceiling and refuses to install enforced hooks on 0.10.x instead of publishing a dead Pi adapter. [R1][AR2] |
| SubagentStop | Installed 0.7.1 matches the Pi child route. Prime 0.10.x has no Pi extension route. [R1][AR2] |
| PermissionRequest | Installed 0.7.1 uses the Pi tool-call gate; no separate event is used. Prime 0.10.x has no Pi extension route. [R1][AR2] |
| Compact | Installed 0.7.1 matches Pi compact events. Prime 0.10.x does not load this extension API, so autorun does not install the Pi adapter there. [R1][AR1][AR2] |

### ForgeCode

| Event | Contract, autorun behavior, and gap |
|---|---|
| SessionStart | ForgeCode 2.13.21 has internal lifecycle machinery but no user-configurable hook or plugin surface; proposal #2562 was closed as not planned. AR therefore supplies only `AGENTS.md`, skills, and command files. The negative enforcement contract is pinned in `forgecode-2.13.21-enforcement.json`. [F1][AR1] |
| Prompt | No supported prompt hook. Guidance is advisory through memory and commands. [F1][AR1] |
| PreToolUse | No supported tool hook; guards cannot be enforced. The lifecycle-hook proposal was closed as not planned. [F1][AR1] |
| PostToolUse | No supported hook. [F1] |
| Stop | No supported hook; no Stop gate. [F1] |
| SubagentStop | No supported hook. [F1] |
| PermissionRequest | No supported hook. [F1] |
| Compact | No supported hook. [F1] |

## User-visible behavior checks

| Harness | Stop block | Allowed Stop notice | Guard denial reason and `/ar:ok` hint | Task reminders | Prompt-time cache warning |
|---|---|---|---|---|---|
| Claude Code | Enforced by Stop `decision:block`. [C1][AR1] | `systemMessage`. [C1][AR1] | PreToolUse nested denial; reason retained for the model and user workaround channel. [C1][AR1] | PostToolUse/Stop/SessionStart. [C1][AR1] | UserPromptSubmit can block or warn. [C1][AR1] |
| Codex | Enforced by Stop `decision:block`. [X1][AR1] | `systemMessage`. [X1][AR1] | PreToolUse denial with nested reason; malformed denials fail closed. [X1][AR1] | PostToolUse/Stop/SessionStart. [X1][AR1] | UserPromptSubmit can block or warn. [X1][AR1] |
| Gemini CLI | Enforced by AfterAgent `decision:deny`. [G1][AR1] | Common notice field. [G1][AR1] | BeforeTool denial reason. [G1][AR1] | AfterTool/AfterAgent/SessionStart. [G1][AR1] | Every BeforeAgent prompt reaches the warning; AR installs no matcher. [G1][AR1] |
| Qwen Code | Enforced by Stop `decision:block`. [Q1][AR1] | Fixed in `942cddd2`: `systemMessage`. [Q1][AR1] | Nested PreToolUse denial; invalid shape becomes deny. [Q1][AR1] | PostToolUse/Stop/SessionStart. [Q1][AR1] | Every UserPromptSubmit reaches the warning; the protocol emits no matcher. [Q1][AR1] |
| Antigravity | Enforced by Stop `decision:continue`. [A1][AR1] | Queued and injected exactly once at SessionStart/PreInvocation because allowed Stop has no notice field. [A1][AR1] | PreToolUse denial reason; invalid shape becomes deny. [A1][AR1] | Stop blocks; `fullyIdle=false` preserves delegated work; SessionStart/PreInvocation inject notices. [A1][AR1] | First PreInvocation (`invocationNum=0`) runs prompt policy; later invocations do not. [A1][AR1] |
| OpenCode | Enforced at `session.idle` by a synthetic `promptAsync` continuation when the installed client exposes it. [O1][AR2] | The continuation text enters the conversation; allowed Stop notices need no separate idle output. [O1][AR2] | `tool.execute.before` throws the daemon reason. [O1][AR2] | Todo updates plus PreCompact/PostCompact and Stop. [O1][AR2] | `chat.message` runs prompt checks and appends visible feedback. [O1][AR2] |
| Pi | Enforced by an `agent_settled` continuation message. [P1][AR2] | Fixed in `7f642ff2`: `ctx.ui.notify`. [P1][AR2] | `tool_call` returns `{block:true, reason}`. [P1][AR2] | Tool result, prompt, SessionStart, and Stop routes. [P1][AR2] | Warning only; before_agent_start cannot reject the prompt. [P1][AR2] |
| Prime Agent | Enforced only on Pi-compatible releases below 0.10.0; current 0.10.x is refused. [R1][AR2] | Visible on supported Pi-compatible releases. [R1][AR2] | Pi tool gate below 0.10.0; no claimed route on 0.10.x. [R1][AR2] | Pi lifecycle routes below 0.10.0. [R1][AR2] | Prompt warning below 0.10.0; no claimed route on 0.10.x. [R1][AR2] |
| ForgeCode | Unsupported. [F1] | Unsupported. [F1] | Advisory memory/commands only. [F1][AR1] | Advisory memory only. [F1][AR1] | Unsupported. [F1] |

## What changed upstream that autorun should adopt

1. Claude Code 2.1.322 documents more lifecycle events, including `SubagentStart`, task events, model-switch events, and structured `background_tasks`/`session_crons` on Stop and SubagentStop. Autorun parses the arrays but does not treat them as liveness proof because current reports show stale `running` teammates and ambient tasks; the negative contract is pinned in `tests/fixtures/hook_contracts/claude-2.1.322-background-work.json`. [C1][C2]
2. Codex 0.167.0 documents `PreModelRequest`, `PostModelRequest`, `Interrupt`, and `SubagentStart`. Autorun needs a handler-level design before subscribing because none of its current policies target those points. PreCompact/PostCompact are now explicitly installed and registered; the parent-session SubagentStop contract is pinned in `tests/fixtures/hook_contracts/codex-0.167.0-lifecycle.json`. [X1]
3. Gemini CLI 0.68.0 added a `gemini hooks migrate` alias from Claude's `SubagentStop` setting to Gemini's native AfterAgent event; it did not add a native event. AR pins that distinction in `tests/fixtures/hook_contracts/gemini-0.68.0-subagent-stop.json` and keeps the invalid key out of the shared manifest for both 0.51 and 0.68. [G2]
4. Qwen 0.21.15 supports `systemMessage` on Stop. Implemented in `942cddd2`; allowed Stop notices no longer disappear. [Q1]
5. Antigravity 1.1.12 supports SessionStart/PreInvocation `injectSteps`; `invocationNum=0` identifies the first model call, and Stop `fullyIdle=false` means a background command or asynchronous task is active. AR now routes first-invocation prompt policy, defers allowed-Stop notices to the next injection point, and treats the inverse idle flag as running-work evidence without claiming it identifies a specific subagent. [A1]
6. OpenCode 1.18.31's V1 API provides `chat.message`, `session.idle`, `experimental.session.compacting`, `session.compacted`, and `client.session.promptAsync`; V1 object entrypoints remain supported from 1.18.29. AR now uses those routes for prompt policy, Stop continuation, and compaction while feature-detecting promptAsync for older clients. The pinned source fixture is `tests/fixtures/hook_contracts/opencode-1.18.31-plugin-api.json`. [O1]
7. Pi 1.5.2 removed `session_switch` and `session_fork` in favor of `session_start.reason`, and added `aborted` to `agent_settled`. Autorun already consumes `session_start.reason`; it now suppresses Stop dispatch for an aborted settle so cancellation remains cancellation. [P1][P2]
8. Prime Agent 0.10.2 replaced the Pi-derived TypeScript package with a Rust workspace and does not expose the Pi extension API. The Prime platform records `runtime_version_max_exclusive=(0, 10, 0)`; real installs probe `prime-agent --version` and skip the integration outside that proven range. [R1]
9. ForgeCode 2.13.21 still has no user-configurable lifecycle hook API; internal hooks are not an extension surface, and proposal #2562 was closed as not planned. AR keeps `has_hooks=false` and pins the advisory-only boundary in `tests/fixtures/hook_contracts/forgecode-2.13.21-enforcement.json`. [F1]
