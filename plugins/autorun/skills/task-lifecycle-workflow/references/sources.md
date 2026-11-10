# Research sources and design decisions

Primary and specification-owner sources were preferred. This package retains the public references that shaped or materially challenged the design; private messages and local research paths are excluded.

## Sources used in v3

Accessed 2026-08-12 unless stated otherwise.

1. Anthropic, [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
   - Distinguishes fixed workflows from dynamic agents and recommends the simplest adequate pattern.
   - Supplies routing, prompt chaining with gates, parallelization, orchestrator-workers, and evaluator-optimizer as composable patterns.
2. Anthropic, [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
   - Supports orchestrator-worker delegation for breadth-first independent research.
   - Warns that highly dependent work and shared context reduce the value of multi-agent execution; motivates goal-rich bounded assignments and explicit synthesis.
3. Anthropic, [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
   - Supports compact high-signal context, just-in-time retrieval, and structured notes that preserve goals and dependencies through compaction.
4. Anthropic, [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
   - Supports incremental work, persistent progress artifacts, current-state reads, and direct end-to-end checks before completion.
5. Anthropic, [Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps)
   - Supports separate generator/evaluator roles and direct interaction with the live artifact.
   - Reports that planner/evaluator components can be valuable while bulky harness components can be removed only after outcome checks.
6. Anthropic, [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
   - Separates task, trial, grader, transcript, outcome, and harness evaluation; supports outcome evidence over self-reported completion.
   - Supports human calibration of model graders and separate capability/regression cases.
7. Anthropic, [Building a C compiler with a team of parallel Claudes](https://www.anthropic.com/engineering/building-c-compiler)
   - Shows value from locks and parallel work on distinct failures, plus failure when agents converge on one coupled defect.
   - Supports known-good oracles, specialized ownership, and treating high-cost multi-agent execution as earned rather than default.
8. Agent Skills, [Specification](https://agentskills.io/specification)
   - Defines the portable package, frontmatter, relative resources, and progressive disclosure boundary.
9. Anthropic, [The complete guide to building skills for Claude](https://resources.anthropic.com/hubfs/The-Complete-Guide-to-Building-Skill-for-Claude.pdf)
   - Informed concrete activation descriptions, concise core instructions, conditional resources, testing, and iterative refinement.
10. Anthropic, [Claude Code skills documentation](https://code.claude.com/docs/en/skills)
    - Used for host-specific discovery and invocation claims; it does not establish behavior on other hosts.

## Earlier topology sources retained

Accessed 2026-08-05. These sources informed earlier topology revisions and remain relevant to the current design.

11. OpenAI Agents SDK, [Agent orchestration](https://openai.github.io/openai-agents-python/multi_agent/)
    - Distinguishes manager-style agents-as-tools from handoffs and documents chaining, parallel work, classification, and evaluator loops.
12. OpenAI Agents SDK, [Human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)
    - Demonstrates pause, serialized state, approval/rejection, and top-level resume.
13. OpenAI Agents SDK, [Guardrails](https://openai.github.io/openai-agents-python/guardrails/)
    - Distinguishes boundary guardrails from delegated-tool checks and notes that parallel checks may not prevent an early side effect.
14. OpenAI Agents SDK, [Tracing](https://openai.github.io/openai-agents-python/tracing/)
    - Supports whole-workflow traces and spans for turns, agents, tools, guardrails, and handoffs.
15. OpenAI, [A practical guide to building agents](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/)
    - Describes incremental orchestration, run-loop exit conditions, layered guardrails, and human intervention for risk or repeated failure.
    - [PDF edition](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf).
16. Google Agent Development Kit, [Graph-based workflows](https://adk.dev/graphs/)
    - Documents nodes, routing, branches, state, nested graphs, and cycles.
17. Google Agent Development Kit, [Routes](https://adk.dev/graphs/routes/)
    - Documents conditional edges, loop back-edges, fan-out/fan-in, and join-path failsafes.
18. Google Agent Development Kit, [Dynamic workflows](https://adk.dev/graphs/dynamic/)
    - Covers conditionals, loops, recursion, checkpointing, and resume behavior.
19. Google Agent Development Kit, [Human input](https://adk.dev/graphs/human-input/)
    - Covers pause/resume nodes and user input within a graph.
20. Google Agent Development Kit, [Workflow patterns](https://adk.dev/workflows/patterns/)
    - Supplies generator-critic and shared-state examples.
21. Microsoft AutoGen, [GraphFlow](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/graph-flow.html)
    - Covers sequential, parallel, conditional, and looping execution; the API is experimental, so it is supporting evidence only.
22. LangGraph, [Workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)
    - Distinguishes predetermined workflows from dynamic agents and documents evaluator-optimizer and conditional tool loops.
23. LangGraph, [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
    - Defines state, nodes, static edges, conditional branches, and parallel destinations.
24. LangGraph, [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
    - Documents checkpointed pause/resume and node restart behavior.
25. LangGraph, [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
    - Covers checkpoints, fault tolerance, and retaining successful writes.
26. Microsoft Agent Framework, [Workflows](https://learn.microsoft.com/en-us/agent-framework/journey/workflows)
    - Contrasts functional control flow with graph topology and covers checkpoints, human input, and multi-agent patterns.
27. Microsoft Agent Framework, [Workflow checkpoints](https://learn.microsoft.com/en-us/agent-framework/workflows/checkpoints)
    - Describes checkpoint state for executors, pending work, and shared workflow state.
28. Google Developers Blog, [Announcing Agent Development Kit for Go 2.0](https://developers.googleblog.com/announcing-adk-go-20/)
    - Provides a supporting overview of conditional routes, fan-out/fan-in, nested graphs, cycles, and human pause.

## Agent packaging and prompt-structure sources retained

Accessed 2026-08-12. These sources define the boundary between the portable role contract and runtime adapters.

29. OpenAI, [Build skills](https://learn.chatgpt.com/docs/build-skills)
    - Defines `SKILL.md`, optional package resources, implicit description matching, and `agents/openai.yaml` as OpenAI appearance, invocation-policy, and dependency metadata.
30. OpenAI, [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
    - Confirms dynamic subagent workflows in ChatGPT and Codex and defines named Codex agents as standalone TOML under user or project `.codex/agents/`.
31. Anthropic, [Create custom subagents](https://code.claude.com/docs/en/sub-agents)
    - Defines named Claude Code subagents under user or project `.claude/agents/` and documents optional skill preloading.
32. Anthropic, [Create plugins](https://code.claude.com/docs/en/plugins)
    - Defines plugin-root `agents/` and `skills/` as sibling Claude-specific components and requires direct plugin discovery tests.
33. Anthropic, [Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)
    - Supports consistent descriptive XML regions for mixed instructions, context, and examples while warning against unnecessary subagent use for simple or tightly coupled work. This package selects XML as a mandatory authoring contract; that local requirement is not a claim that the portable specification parses or requires XML.

## Topology decision

| Candidate | Useful property | Limitation | Use |
| --- | --- | --- | --- |
| fixed linear pipeline | cheap and easy to explain | cannot safely absorb mid-work corrections | fast route only |
| directed control/work graph | feedback, review, recovery, pause/resume, and explicit loop-backs | needs bounded exits and a separate executable prerequisite rule | overall workflow topology |
| acyclic dependency scheduler | dependencies, ownership, parallel work, targeted invalidation | cannot contain its own feedback cycle and remain executable | node activation within each stable epoch |
| hierarchical state machine | lifecycle, interruption, route to earlier state | needs an inner dependency model | outer event controller |
| shared-ledger supervisor | durable evidence and continuity | weak alone for ordering and joins | shared state plus trackkeeper |
| OODA/PDCA loop | compact mental model | too coarse for ownership, permissions, and invalidation | principle, not topology |

Selected design: one event-driven directed workflow graph with explicit review, verification, recovery, authorization, and evidence-refinement loop-backs; acyclic node activation dependencies inside each stable epoch; and a shared ledger audited by a logical trackkeeper. This combination directly represents user feedback, dependency order, safe parallelism, targeted invalidation, checkpoint/resume, independent review, and bounded convergence. Each loop names its re-entry condition and exit gate, and roles or branches are added only when their checks can change the outcome.

## Trackkeeper authority and escalation decision

The orchestrator may perform every workflow pass and remains the only dispatcher, task-state writer, synthesis owner, and user-facing decision maker. Delegated roles supply accountability or an independent check without restricting the orchestrator's investigation, audit, critique, planning, verification, recovery, or adjudication. This follows the manager-style ownership described by sources 1, 2, and 11 and avoids split-brain corrections. The trackkeeper is the independent evaluator: it audits both orchestrator and worker behavior from direct evidence, emits structured findings, and rechecks dispositions, but never commands workers or implements repairs. Source 6 requires treating its output as fallible evaluation evidence rather than a verdict.

Unresolved high or critical findings block only their affected action. Source 13 distinguishes blocking checks that run before consequential execution from concurrent checks that may finish after a side effect begins; the portable skill therefore defines gate semantics but claims independent enforcement only after runtime testing. Source 12 supplies a checkpointed outer interruption and resume mechanism, not a reason to escalate early.

Before any user escalation, the workflow tests assumptions, gathers permitted evidence, applies an intent-preserving reversible default, replans or recovers, and may use one independent evidence-based adjudication pass. User input is reserved for proven user-only authority or information, or a consequential choice with materially different outcomes and no evidence-backed safe default. This autonomy rule comes from the local human requirements and system philosophy; the public sources support the surrounding manager, evaluator, guardrail, and pause/resume mechanisms.

## Research queries retained

1. `site:anthropic.com/engineering agents workflows parallelization orchestrator workers evaluator optimizer loops`
2. `site:anthropic.com/engineering multi-agent research context long-running harness evals agents`
3. `site:agentskills.io specification SKILL.md progressive disclosure`
4. `site:openai.github.io/openai-agents-python multi agent orchestration human in the loop guardrails tracing`
5. `site:adk.dev graphs routes dynamic human input workflow patterns`
6. `site:docs.langchain.com/oss/python/langgraph workflows graph interrupts persistence`
7. `site:learn.microsoft.com agent framework workflows checkpoints`
8. `site:learn.chatgpt.com build skills agents/openai.yaml subagents custom agents`
9. `site:code.claude.com sub-agents plugin agents skills`
10. `site:platform.claude.com prompting best practices XML subagent orchestration`

The source log establishes design provenance. It does not prove that a runtime discovers or follows the skill; that evidence belongs in [compatibility-and-validation.md](compatibility-and-validation.md).
