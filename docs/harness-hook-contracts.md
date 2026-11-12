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
| G2 | Gemini CLI v0.68.0 changelog, <https://github.com/google-gemini/gemini-cli/blob/main/docs/changelogs/latest.md>, released 2026-11-10, read 2026-11-12. |
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
| UserPromptSubmit | Emits before prompt processing, including harness-authored turns. `B=decision:block`; `N=systemMessage`; `C=additionalContext`. AR subscribes, distinguishes harness-authored wrappers, and can block the cache-expiry prompt check. [C1][AR1] |
| PreToolUse | Emits before each tool. `B=hookSpecificOutput.permissionDecision=deny` with `permissionDecisionReason`; `ask` is supported. AR subscribes and preserves the nested denial even when the Claude exit-2 workaround suppresses duplicate JSON text. [C1][AR1] |
| PostToolUse | Emits after successful tools. `N=systemMessage`; `C=additionalContext`; blocking cannot undo the tool. AR subscribes and uses it for task reminders and context. [C1][AR1] |
| Stop | Emits when the main agent finishes. `B=decision:block` plus `reason`; `N=systemMessage`; `C=additionalContext`. AR subscribes; blocked Stops use one reason channel and allowed Stops keep notices. Stop input now includes `background_tasks` and `session_crons`; AR parses both but does not yet treat either as proof of a running subagent. [C1][AR1] |
| SubagentStop | Emits when a subagent finishes and includes `agent_id`, `agent_type`, `background_tasks`, and `session_crons`. Output control matches Stop. AR subscribes and blocks or allows through the Claude protocol. Gap: no captured payload proves that the task arrays identify running subagents, so `stop_payload_lists_running_agents` remains false. [C1][AR1] |
| PermissionRequest | Emits when a permission dialog is about to appear. `B/C` use `hookSpecificOutput.decision`; user-facing text is part of the decision message. AR has a protocol shape and registry mapping but the shared manifest does not subscribe. Gap: add a handler only after defining how it composes with the earlier PreToolUse decision so a second hook cannot weaken a denial. [C1][AR1] |
| Compact | `PreCompact` and `PostCompact` emit. PreCompact is advisory; notices use `systemMessage`, and context is event-specific. AR subscribes to both and dispatches its compact handlers. [C1][AR1] |

### Codex CLI

| Event | Contract, autorun behavior, and gap |
|---|---|
| SessionStart | Emits on session start/resume. `N=systemMessage`; `C=hook-specific additionalContext`; common fields are strict. AR subscribes through the shared manifest and filters to Codex's event schema. [X1][AR1] |
| UserPromptSubmit | Emits during a turn. `B` is supported; `N=systemMessage`; `C=additionalContext`. AR subscribes and also replays policy commands from the rollout when prompt hooks are absent. [X1][AR1] |
| PreToolUse | Emits before tools. `B=decision:block` and nested `permissionDecision=deny`; the reason stays in the nested field so Codex does not render duplicate rows. AR subscribes and fails closed on malformed denial output. [X1][AR1] |
| PostToolUse | Emits after tools. `N=systemMessage`; `C=additionalContext`; a block is feedback only. AR subscribes and strips fields Codex rejects. [X1][AR1] |
| Stop | Emits at main-thread completion. `B=decision:block`; `N=systemMessage`; common output is strict. AR subscribes and uses compact block output or allowed notices. [X1][AR1] |
| SubagentStop | Emits when a subagent completes; a block reruns/continues the subagent. AR subscribes and maps the event directly. [X1][AR1] |
| PermissionRequest | Emits before a permission decision. AR has the nested decision serializer and a registry mapping, but the shared manifest does not subscribe. The same composition proposal as Claude applies. [X1][AR1] |
| Compact | `PreCompact` and `PostCompact` emit and accept common notice/context fields. The shared manifest subscribes. Gap: the Codex registry does not list the compact events even though identity mapping currently lets hook entry process them; add explicit registry rows so capability snapshots and future validation cannot drift. [X1][AR1] |

### Legacy Gemini CLI

| Event | Contract, autorun behavior, and gap |
|---|---|
| SessionStart | Emits and accepts `systemMessage` plus `additionalContext`. AR subscribes. [G1][AR1] |
| BeforeAgent | Prompt event. `B=decision:deny`; `C=additionalContext`; notices use common output. AR subscribes, but its manifest matcher is `/ar:`. Gap: ordinary prompts may not reach the prompt-time cache warning on clients that enforce the matcher. Change to a documented catch-all after verifying 0.51.0 and 0.68.0 matcher behavior. [G1][AR1] |
| BeforeTool | `B=decision:deny`; reason and context are supported. AR subscribes and normalizes unsupported `ask` to deny. [G1][AR1] |
| AfterTool | Emits after tools; `C=additionalContext`; output cannot undo the tool. AR subscribes. [G1][AR1] |
| AfterAgent | Stop event. `B=decision:deny` triggers retry; allowed common output can carry a notice. AR subscribes. [G1][AR1] |
| SubagentStop | Installed 0.51.0 does not expose a distinct event. Gemini CLI 0.68.0 added SubagentStop event mapping. AR does not subscribe. Gap: add a version-compatible manifest mapping after testing how pre-0.68 clients handle an unknown event. [G1][G2][AR1] |
| PermissionRequest | Not established in the installed 0.51.0 hook reference. No AR subscription. [G1][AR1] |
| PreCompress | Emits before compression and is advisory. AR subscribes as `PreCompact`. [G1][AR1] |

### Qwen Code

| Event | Contract, autorun behavior, and gap |
|---|---|
| SessionStart | Emits. `N=systemMessage`; `C=additionalContext`. AR subscribes. [Q1][AR1] |
| UserPromptSubmit | Emits before the prompt. `C=additionalContext`; standard fields remain available. AR subscribes, but the translated matcher comes from Gemini's `/ar:` matcher. Gap: ordinary prompts may miss the prompt-time cache warning; use a verified catch-all matcher. [Q1][AR1] |
| PreToolUse | Emits before tools. The official output is nested `permissionDecision` plus `permissionDecisionReason`; `allow`, `deny`, and `ask` are supported. AR subscribes and converts malformed or unknown decisions to deny. [Q1][AR1] |
| PostToolUse | Emits after tools. `C=additionalContext`; common `systemMessage` is available. AR subscribes. [Q1][AR1] |
| Stop | Emits before the response ends. `B=decision:block`; `N=systemMessage`; `C=additionalContext`. AR now preserves common fields on allowed Stops and keeps blocking responses compact. Waiting-user, delegation, fork, and loop-guard notices therefore appear as Qwen hook system messages. [Q1][AR1] |
| SubagentStop | Emits and accepts `decision`/`reason` plus common output. AR clones the Stop handler and now preserves allowed notices there too. [Q1][AR1] |
| PermissionRequest | Emits and accepts `hookSpecificOutput.decision`. AR has a serializer and registry mapping but the translated manifest does not subscribe. Use the same denial-composition proposal as Claude and Codex. [Q1][AR1] |
| PreCompact | Emits as `PreCompact`; `C=additionalContext`. AR translates `PreCompress` to it and subscribes. [Q1][AR1] |

### Google Antigravity

| Event | Contract, autorun behavior, and gap |
|---|---|
| SessionStart | Emits before the first model invocation. Output is `injectSteps`; startup cannot be blocked. AR now installs the event and converts startup notices/context to an `ephemeralMessage`. [A1][AR1] |
| Prompt | No prompt-admission hook is documented. `PreInvocation` can inject context or terminate immediately before each model call, but it is not equivalent to a typed-prompt event. AR has a protocol mapping for BeforeModel but no handler to serve. Gap proposal: add a prompt-policy handler at BeforeModel, with tests that distinguish user input from later model invocations. [A1][AR1] |
| PreToolUse | Emits before tools. `B=decision:deny`; `reason` and `permissionOverrides` are supported. AR subscribes and fails closed when no valid decision survives filtering. [A1][AR1] |
| PostToolUse | Emits after tools and defines no control output used by autorun. AR subscribes for observation and emits `{}`. [A1][AR1] |
| Stop | Emits with `fullyIdle`. `B=decision:continue`; `reason` is injected only when continuing. AR subscribes and blocks correctly. The official schema defines no field for a notice on an allowed Stop, so waiting-user and loop-guard pass-through text cannot be delivered on this event. [A1][AR1] |
| SubagentStop | No distinct event is documented. `fullyIdle` on Stop is the supported background-work signal; AR receives it in the native payload but does not yet expose a registry field for it. Gap: capture the installed payload and map `fullyIdle=false` to the Stop gate's running-work evidence without treating unrelated background commands as subagents. [A1][AR1] |
| PermissionRequest | No permission-request hook is documented. PreToolUse is the permission gate. [A1] |
| Compact | No compression hook is documented. [A1] |

### OpenCode

| Event | Contract, autorun behavior, and gap |
|---|---|
| SessionStart | Current plugin APIs expose session/context hooks, but autorun only sends an `OpenCodeAttach` frame. Startup guidance instead comes from the installed `AGENTS.md`. Gap: migrate the plugin to the documented session hook and deliver SessionStart context. [O1][AR2] |
| Prompt | Current plugin APIs expose a prompt-admission hook and a model-context hook. AR uses neither, so prompt-time cache warnings and typed control-command feedback do not run through a hook. This is a precise implementation proposal: send UserPromptSubmit at prompt admission, return a visible warning there, and use the context hook only for model context. [O1][AR2] |
| PreToolUse | `tool.execute.before` can veto by throwing. AR subscribes, asks the daemon, and throws the denial reason. [O1][AR2] |
| PostToolUse | OpenCode emits general tool events, but AR forwards only `todo.updated` as a synthetic PostToolUse for task state. Gap: add a general after-tool route if the current API provides stable result mutation; otherwise keep task-only forwarding. [O1][AR2] |
| Stop | No autorun Stop bridge is installed. Current plugin docs expose session/model hooks but the exact final-idle event was not established. Gap remains `not established`; do not map a model-response event to Stop without proof that no further tool turn follows. [O1][AR2] |
| SubagentStop | Not established in the official plugin pages read. No AR route. [O1][AR2] |
| PermissionRequest | No separate route is used; PreToolUse veto is the enforced gate. [O1][AR2] |
| Compact | Current V2 docs expose compaction context hooks. AR does not use them. Proposal: forward PreCompact/PostCompact after pinning the event ordering and output mutation contract. [O1][AR2] |

### Pi

| Event | Contract, autorun behavior, and gap |
|---|---|
| session_start | Emits with reasons including `startup`, `reload`, `new`, `resume`, and `fork`. AR forwards SessionStart; startup text is now a displayed custom message queued with `deliverAs:nextTurn`, so it enters the next model context without starting a turn. [P1][P2][AR2] |
| before_agent_start | Emits after input expansion and before the run. It can return a displayed custom message to the model. AR forwards UserPromptSubmit. Pi cannot block the prompt through this return shape, so cache enforcement is a notice on Pi. [P1][AR2] |
| tool_call | Can block with `{block:true, reason}` and can mutate input. AR forwards PreToolUse and blocks with the daemon reason. [P1][AR2] |
| tool_result | Can replace or append result content. AR forwards PostToolUse, appends reminders, and fails the task-tool result when the daemon did not confirm a task mutation. [P1][AR2] |
| agent_settled | Final-idle signal. AR forwards Stop; a block queues a hidden continuation message with `triggerTurn:true`, while an allowed response with text is now shown through `ctx.ui.notify`. [P1][AR2] |
| SubagentStop | Pi subagent children use the same `agent_settled` event and are identified by the extension environment. AR forwards SubagentStop and does not start a continuation from the child adapter. [P1][AR2] |
| PermissionRequest | No separate extension event is needed for autorun; `tool_call` is the blocking gate. [P1][AR2] |
| Compact | `session_before_compact` can cancel/customize and `session_compact` reports completion. AR forwards PreCompact and PostCompact. [P1][AR2] |

### Prime Agent

| Event | Contract, autorun behavior, and gap |
|---|---|
| SessionStart | Installed Prime Agent 0.7.1 uses the Pi-compatible runtime and the same autorun template, so behavior matches Pi: context is queued for the next turn and displayed. Current Prime 0.10.2 is a newer implementation; compatibility with the Pi event API is not established. [R1][AR1][AR2] |
| Prompt | Installed 0.7.1 behavior matches Pi `before_agent_start`: notice/context, no prompt block. Current 0.10.2 contract is not established. [R1][AR2] |
| PreToolUse | Installed 0.7.1 matches Pi `tool_call` and blocks with a reason. Current 0.10.2 contract is not established. [R1][AR2] |
| PostToolUse | Installed 0.7.1 matches Pi `tool_result`. Current 0.10.2 contract is not established. [R1][AR2] |
| Stop | Installed 0.7.1 matches Pi `agent_settled`; allowed notices are now visible and blocked Stops continue. Current 0.10.2 contract is not established. [R1][AR2] |
| SubagentStop | Installed 0.7.1 matches the Pi child route. Current 0.10.2 contract is not established. [R1][AR2] |
| PermissionRequest | Installed 0.7.1 uses the Pi tool-call gate; no separate event is used. Current 0.10.2 contract is not established. [R1][AR2] |
| Compact | Installed 0.7.1 matches Pi compact events. Current 0.10.2 contract is not established. Proposal: add version detection and a separate protocol/installer entry if 0.10.x no longer loads Pi extensions. [R1][AR1][AR2] |

### ForgeCode

| Event | Contract, autorun behavior, and gap |
|---|---|
| SessionStart | No supported lifecycle hook API was found. AR supplies `AGENTS.md`, skills, and command files. [F1][AR1] |
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
| Gemini CLI | Enforced by AfterAgent `decision:deny`. [G1][AR1] | Common notice field. [G1][AR1] | BeforeTool denial reason. [G1][AR1] | AfterTool/AfterAgent/SessionStart. [G1][AR1] | Handler exists, but the `/ar:` matcher may omit ordinary prompts. [G1][AR1] |
| Qwen Code | Enforced by Stop `decision:block`. [Q1][AR1] | Fixed in `942cddd2`: `systemMessage`. [Q1][AR1] | Nested PreToolUse denial; invalid shape becomes deny. [Q1][AR1] | PostToolUse/Stop/SessionStart. [Q1][AR1] | Handler exists, but the translated `/ar:` matcher may omit ordinary prompts. [Q1][AR1] |
| Antigravity | Enforced by Stop `decision:continue`. [A1][AR1] | Not supported by the allowed-Stop schema; `reason` is defined only for continue. [A1] | PreToolUse denial reason; invalid shape becomes deny. [A1][AR1] | Stop blocks and SessionStart context; PostToolUse is observational. [A1][AR1] | No prompt hook; proposed BeforeModel policy route requires author/turn tests. [A1][AR1] |
| OpenCode | No Stop route. [O1][AR2] | No Stop route. [O1][AR2] | `tool.execute.before` throws the daemon reason. [O1][AR2] | Task reminders only through todo updates and memory. [O1][AR2] | Supported plugin prompt route is unused; proposal above. [O1][AR2] |
| Pi | Enforced by an `agent_settled` continuation message. [P1][AR2] | Fixed in `7f642ff2`: `ctx.ui.notify`. [P1][AR2] | `tool_call` returns `{block:true, reason}`. [P1][AR2] | Tool result, prompt, SessionStart, and Stop routes. [P1][AR2] | Warning only; before_agent_start cannot reject the prompt. [P1][AR2] |
| Prime Agent | Same as installed Pi-compatible 0.7.1. [R1][AR2] | Fixed in `7f642ff2`. [R1][AR2] | Same as installed Pi-compatible 0.7.1. [R1][AR2] | Same as installed Pi-compatible 0.7.1. [R1][AR2] | Warning only on installed 0.7.1; current 0.10.2 not established. [R1][AR2] |
| ForgeCode | Unsupported. [F1] | Unsupported. [F1] | Advisory memory/commands only. [F1][AR1] | Advisory memory only. [F1][AR1] | Unsupported. [F1] |

## What changed upstream that autorun should adopt

1. Claude Code 2.1.322 documents more lifecycle events, including `SubagentStart`, task events, model-switch events, and structured `background_tasks`/`session_crons` on Stop and SubagentStop. Autorun already parses the two arrays. A follow-up needs captured payloads before either array becomes running-subagent proof. [C1][C2]
2. Codex 0.167.0 documents `PreModelRequest`, `PostModelRequest`, `Interrupt`, and `SubagentStart`. Autorun needs a handler-level design before subscribing because none of its current policies target those points. Add explicit compact-event registry rows now or with that change. [X1]
3. Gemini CLI 0.68.0 added SubagentStop mapping. Test unknown-event handling on 0.51.0 before adding it to the shared extension manifest; an unrecognized event must not disable older hooks. [G2]
4. Qwen 0.21.15 supports `systemMessage` on Stop. Implemented in `942cddd2`; allowed Stop notices no longer disappear. [Q1]
5. Antigravity 1.1.12 supports SessionStart `injectSteps`. Implemented in `8a65bc4e`. Its `fullyIdle` Stop field should be captured and evaluated as background-work evidence without equating every background command with a subagent. [A1]
6. OpenCode's current plugin API has prompt, context, and compaction hooks. Autorun's V1-style bridge uses only tool veto and todo events. Migrate in a separate change with an installed 1.18.31 fixture and tests for prompt admission, context mutation, final-idle semantics, and plugin reload. [O1]
7. Pi 1.5.2 removed `session_switch` and `session_fork` in favor of `session_start.reason`, and added `aborted` to `agent_settled`. Autorun already consumes `session_start.reason`; allowed lifecycle notices were fixed in `7f642ff2`. Add `aborted` to the forwarded Stop frame only if a policy needs to distinguish cancellation from normal completion. [P1][P2]
8. Prime Agent's current release is 0.10.2 while the installed client is 0.7.1 and still Pi-compatible. Before claiming current-version support, detect the runtime version and establish whether 0.10.x loads the Pi extension API; use a separate Platform protocol if it does not. [R1]
9. ForgeCode still has no documented lifecycle hook API, and the lifecycle-hook proposal was closed as not planned. Continue using memory, skills, and commands; do not claim enforced guards or a Stop gate. [F1]
