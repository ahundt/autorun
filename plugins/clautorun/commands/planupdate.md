---
description: Quick sync plan with codebase state
argument-hint: [plan file]
---

# Quick Plan Update (/cr:planupdate)

$ARGUMENTS

## 0. Plan Mode Check

**IMPORTANT:** If you are not already in plan mode, use the `EnterPlanMode` tool NOW before proceeding. Planning commands require plan mode for proper operation.

## MODE: PLAN SYNC (Not Execution)

You are syncing plan status with the current codebase state. This is a QUICK update - not a full refinement. Do NOT execute code changes until user approves proceeding.

## CRITICAL CONSTRAINTS: Sync, Don't Replace

**DO:**
- Check if planned items are already complete in the codebase
- Update file paths if files have moved
- Add newly discovered dependencies or blockers
- Mark items as done if code already exists
- Note any blockers or changes

**DO NOT:**
- Rewrite the entire plan
- Remove sections without explicit justification
- Change goals without discussion
- Skip the verification step

## 1. Quick Checklist

For each plan item, output ONE status:

- `DONE: [item] - verified at file.ts:42`
- `PENDING: [item] - no changes needed`
- `BLOCKED: [item] - blocked by [reason]`
- `OUTDATED: [item] - path changed to [new path]`
- `REMOVED: [item] - reason: [justification]`

## 2. Subagent Usage Guidelines

**For quick updates, use subagents sparingly:**
- **Explore subagent**: Launch ONE Explore subagent if you need to verify multiple file paths efficiently
- **Avoid Plan subagents**: This is a quick sync, not a full planning exercise

**Keep it lightweight**: The goal is to sync status, not redesign the plan.

## 3. Output Summary

```text
Plan Update Summary:
- Completed: X items
- Pending: X items
- Blocked: X items
- Outdated: X items (paths updated)
- Removed: X items (with justification)

Changes Made:
1. [specific change with justification]
2. [specific change with justification]
```

## 4. Quality Gate

Before finishing, verify:

- [ ] No sections were removed without explicit justification
- [ ] All file paths verified with Glob/Read
- [ ] Goals unchanged (or change discussed with user)
- [ ] CLAUDE.md philosophy preserved (TDD, DRY, KISS, etc.)

## 5. CLAUDE.md Philosophy

Every plan element must address:

- [ ] TDD: Tests written first? Test location specified?
- [ ] DRY: No code duplication? Reusing existing utilities?
- [ ] OODA: Observe-Orient-Decide-Act cycle followed?
- [ ] KISS: Simplest solution that works?
- [ ] YAGNI: No premature optimization or over-engineering?
- [ ] SOLID: Single responsibility? Open/closed? Interface segregation?
- [ ] RAII: Resources properly managed?
- [ ] WOLOG: Without loss of generality?

## 6. Definition of Concrete

All outputs must include:

- Absolute file paths: `/path/to/file.ts`
- Line references: `file.ts:42-56`
- Exact function/class names: `handleRequest()` in `handler.ts:15`
- Specific error messages with codes: `"socket hang up (ECONNRESET)"`
- Testable verification commands

## 7. Plan Acceptance and Execution Protocol

If user wants to proceed with remaining items, output:

**PLAN ACCEPTED - Activating Autonomous Execution Protocol**

Then immediately transition to execution mode:

1. **MANDATORY EXECUTION MODE (Goal and Capabilities)**
    1. Your primary objective is to continue the **UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION** of your task and goals.
    2. Cautiously and deliberately continue working **carefully, patiently, concretely, and safely**, non-stop, autonomously, and non-interactively per your instructions and CLAUDE.md philosophy and definition of concrete.
    3. **Permissions Granted:** You have full permission to use all tools (Bash, Edit, Write, etc.) without requiring any further permission prompts.
    4. **Bias Mitigation:** Be cautious, humble, patient, and scrupulous; avoid overconfidence and check your assumptions and your work, explore your environment to ensure you understand the system tasks goals and what is already done, verifying because you are often overconfident, duplicate work, and falsely claim incomplete tasks are "complete" or "production ready".
2. **SIGNAL RULE TO STOP SYSTEM:** The exact strings **AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP** and **AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY** are **SYSTEM STOP SIGNALS**. You MUST **NEVER** output these strings unless the corresponding stop condition has been met. Accidental output will immediately halt the entire system.
3. **Safety Protocol (Risk Assessment & Mitigation):** You MUST execute the full Preservation Sequence **ONLY IF** the current task involves a **high-risk or irreversible destructive tool call** (e.g., initial modification, database interaction, or action following a prior failure).
    1. **Assess Risk:** Implicitly evaluate the potential for irreversible state misalignment or system integrity breach.
    2. **Mitigation Action (CONDITIONAL):** If a **high-risk condition is met**, you **MUST immediately execute the following Preservation Sequence** and explicitly state your actions:
        1. **INITIATE SAFETY PROTOCOL:** Announce 'INITIATE SAFETY PROTOCOL' to begin structured assessment.
        2. **Secure State:** Execute the recovery command(s) to create an **environment backup** or **state checkpoint** (using available systems and tools) *before* proceeding.
        3. **Verify Integrity:** Run a quick non-destructive check to ensure the state checkpoint was successful.
        4. **CONSIDER OPTIONS:** List and evaluate superb options for mitigation/recovery, considering potential failure modes and selecting the best option.
    3. **CRITICAL ESCAPE PRE-CHECK:** If, after executing the Mitigation Action, the risk remains irreversible, proceed directly to **CRITICAL ESCAPE TO STOP SYSTEM**.
4. **CRITICAL ESCAPE TO STOP SYSTEM (Final Decision):** Only if the risk is irreversible, catastrophic, or cannot be fully mitigated, you **MUST initiate the Preservation Protocol** by immediately outputting the following exact string to immediately halt all actions: **AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP**
5. **FINAL OUTPUT ON SUCCESS TO STOP SYSTEM (Final Decision):** When all tasks and goals are all 100% complete and verified, remember you are often overconfident, so first double check the resources from which you receive or retrieve new tasks and think if anything was missed, you **MUST** conclude your entire final response with the exact string: **AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY**
