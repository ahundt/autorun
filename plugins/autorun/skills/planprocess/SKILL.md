---
name: planprocess
description: Use when the user asks to "execute the plan", "run the plan", or "/ar:planprocess". Executes an approved plan step by step with the wait process, task tracking, and verification before each completion claim.
aliases: [pp]
argument-hint: "[plan path or task description]"
---

# Execute an Approved Plan (/ar:planprocess)

<purpose>

This skill executes a plan that is already written and approved. It starts
autorun's three-stage execution for that plan: work through every step, run the
wait process after each one, and claim completion only with evidence. To write
or improve a plan first, use `/ar:plannew` or `/ar:planrefine`.

The user's argument is the plan's path or the task it covers. With no argument,
execute the plan approved most recently in this session.

## Harness-specific plan lifecycle

- **Claude Code:** Use `EnterPlanMode` and `ExitPlanMode` where this skill instructs; `ExitPlanMode` is the approval boundary.
- **Pi:** Do not call `EnterPlanMode` or `ExitPlanMode` on Pi because Pi exposes neither tool. Write or refine the durable plan note directly, report its exact path for approval, and after approval start execution with `/ar planprocess <path>`.
- **Native task tracking is conditional:** Before following any task-operation instruction below, use only a mutation tool that the current session actually exposes. Claude uses `TaskCreate`/`TaskUpdate`; Pi and Prime use those tools only when the autorun extension registered them; Codex uses the flat `update_plan` checklist only when exposed (`update_plan` is unavailable in Codex Plan mode). If no native task mutation tool is available, keep progress in the durable Markdown checklist, do not call or retry an unavailable task tool, and report that native task state was not updated. Every unqualified `TaskCreate`, `TaskUpdate`, dependency, and `TaskList` instruction below is conditional on those capabilities.
- **Other harnesses:** Use a native plan-mode boundary and native task tracking only when those tools actually exist; otherwise use the same durable-note approval and checklist workflow as Pi with that harness's documented autorun command spelling.

</purpose>

---

<requirements>

## Rules that survive compaction

If this session was compacted, these rules still apply without exception:

1. **Execute only an approved plan.** If the plan has not been approved, stop and ask for approval: on Claude Code through `ExitPlanMode`, on Pi by reporting the durable plan note path.
2. **When TaskCreate is exposed, ALL plan steps require TaskCreate.** If another native checklist mutation tool is exposed, track every step with that tool instead. Otherwise, use the durable Markdown checklist as required by the capability rule above.
3. **Before writing any code, scaffold every task and check it is visible in `TaskList`.** For each implementation step N:
   1. `TaskCreate(subject="[TDD] Step N: Write tests for <step description>")`
   2. `TaskCreate(subject="[EXEC] Step N: <step description>")`
   3. `TaskUpdate([EXEC] task, addBlockedBy=[[TDD] task])`: tests must pass before implementation
   4. `TaskUpdate([EXEC] Step N, addBlockedBy=[[EXEC] Step N-1])`: steps run in order

   Then `TaskCreate(subject="[VERIFY] Run full test suite and confirm all steps complete")`.
4. **Keep tasks current:** `TaskUpdate(status="in_progress")` before starting a step, `TaskUpdate(status="completed")` only when its tests pass and its requirements are met, then `TaskList` to find the next one. Unexpected blocking work gets `TaskCreate(subject="[BLOCKER] description")` at once. After every 3 tool calls, check that the current task is `in_progress`.
5. **Never delete content from plan files.** Only add or make micro-edits: tick checkboxes, append results and new user messages to the plan's quoted list. Read before editing; verify after.
6. **Concrete evidence for every claim:** file paths, `file:line` ranges, function names, exact error messages, the exact commands run and their output, and full URIs for every external source.

Hold each step to these principles: TDD (test first, location named), DRY,
OODA before each decision, KISS, YAGNI, SOLID, RAII (resources released) and
WOLOG (the solution generalizes as far as it should, no further).

</requirements>

---

<workflow>

## Process

1. **Load the plan.** Read the whole plan file. List its steps, its quoted user messages and its verification commands. If a step's premise is false in the code (a cited line moved, a file is gone), record that in the plan and fix the step before executing it.
2. **Create the tasks** of rules 2 and 3.
3. **For each step, in dependency order:**
   1. Mark it in progress.
   2. Write or update the test first and run it; see it fail for the stated reason.
   3. Make the change.
   4. Run the step's verification command and read the output.
   5. Output the exact string "Wait," and run the wait process below out loud, step by step.
   6. Tick the plan's checkbox and complete the task only when the check passed and any error correction is done.
4. **Delegate independent work in parallel** when it helps (subagent table below), then verify every finding yourself: Read every file reference, confirm code snippets match the file, cite `file:line` ranges, mark anything you could not verify "[UNVERIFIED]", and never pass on an unchecked claim.
5. **Name each competing approach** with an incrementing version (`<taskname>-v1`, `<taskname>-v2`) so a later note can say which one was tried and why it changed.
6. **Final verification before any completion claim:**
   1. [ ] Primary use case works
   2. [ ] Every edge case in the requirements is covered
   3. [ ] Every language and platform named is covered
   4. [ ] Every file type and format named is handled
   5. [ ] Every user example is addressed
   6. [ ] No requirement was silently dropped

   Then check your work again: verify file references with Read, confirm code runs, check `TaskList` for skipped steps. If any case is missing, return to its step.

## Wait process

After outputting "Wait,", execute these 8 steps:

1. **Elaborate Best Practices**: Elaborate and refine best practices lists (create new lists if none exist yet) as numbered list for current context; keep refining as new circumstances develop
2. **Comprehensive Critique**: Harshly and constructively critique overall + line-by-line against every single best practice and criteria
3. **Pre-mortem Analysis**: Identify potential failure modes, edge cases, weaknesses. Assume disaster → determine what went wrong → prevent those outcomes
4. **Multiple Solution Generation**: Propose ≥3 concrete solutions (high-level + specific code quotes) to each identified issue in your task, critiques, and pre-mortem analysis
5. **Synthesized Solution Building**: Synthesize insights from all cumulative context including all previous critiques, the original work (if applicable), all previous proposed solutions, and all accumulated best practices to create refined solutions that incorporate lessons learned from the complete analysis
6. **Sequential Quality Enhancement**: Each proposal builds on the benefits of previous iterations
7. **Best Solution Selection**: Choose the optimal solution from all proposals including the original, synthesized, or combinations of all proposals, in ranked order with the reason for each rank
8. **Error Correction Protocol**: On error: immediately TaskCreate(fix) → TaskUpdate ALL steps from error point onward (failed_step and all subsequent steps) back to "pending" or "in_progress" status → Update dependencies (addBlockedBy) for reset steps to reflect corrected sequence → execute corrective steps → return to the step that produced the error and redo from there

Before ticking a step, answer yes to each: Is the code clean, robust and
concise? Is the plan file up to date with what was learned? Does the change fit
the existing code and patterns? Does it handle each backend the plan names?

</workflow>

---

<delegation>

## Subagents

| Subagent Type | When to Use |
|---------------|-------------|
| **Explore** | Codebase exploration, finding patterns, understanding implementations |
| **Plan** | Designing approaches, alternatives, architectural decisions |
| **code-architect** | Complex architectural planning (Claude Code `feature-dev` plugin, when installed) |
| **code-explorer** | Deep analysis of existing features (same plugin) |
| **code-reviewer** | Code quality, bugs, security review (Claude Code `pr-review-toolkit` plugin, when installed) |
| **pr-test-analyzer** | Test coverage analysis (same plugin) |

Launch independent subagents in one message so they run in parallel.

</delegation>

---

<acceptance_protocol>

## Execution protocol

1. **Execution mode**
   1. Continue the uninterrupted, autonomous, non-interactive, patient and safe execution of the plan until every step is verified.
   2. Work carefully and concretely, per your instructions and the definition of concrete in rule 6.
   3. **Bias mitigation:** be cautious and check your assumptions and your work; explore the environment to learn what is already done, because overconfidence leads to duplicated work and to calling incomplete tasks "complete" or "production ready".

2. **System stop signals.** Never output either unless its condition is met:
   - `AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP`
   - `AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY`

3. **Safety protocol.** Run it only when a step involves a high-risk or irreversible action (database operations, file deletion, state modification).
   1. **Assess risk:** is the action irreversible, or could it corrupt state?
   2. **Mitigate:** if it is high-risk, say what you do at each step:
      1. Announce 'INITIATE SAFETY PROTOCOL'.
      2. Create a backup or state checkpoint **before** proceeding.
      3. Verify the checkpoint succeeded.
      4. List the options, evaluate their failure modes, select the best.
   3. **Escape pre-check:** if the risk is still irreversible after mitigation, go to step 4.

4. **Escape to stop:** only if the action is irreversible, catastrophic or cannot be mitigated, output this exact string to halt all actions: **AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP**

5. **Final output on success:** when every step is complete and verified, check once more where your tasks come from and whether anything was missed, then output this exact string: **AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY**

</acceptance_protocol>
