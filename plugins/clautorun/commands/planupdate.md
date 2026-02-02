---
description: Quick sync plan with codebase state
argument-hint: [plan file]
---

# Quick Plan Update (/cr:planupdate)

$ARGUMENTS

---

## 1. Foundation (Reference)

### 1.1 Key Principles

| Acronym | Meaning |
|---------|---------|
| TDD | Test-Driven Development |
| DRY | Don't Repeat Yourself |
| OODA | Observe-Orient-Decide-Act |
| KISS | Keep It Simple, Stupid |
| YAGNI | You Aren't Gonna Need It |
| SOLID | Single responsibility, Open/closed, Liskov, Interface segregation, Dependency inversion |
| RAII | Resource Acquisition Is Initialization |
| WOLOG | Without Loss Of Generality |

### 1.2 Definition of Concrete

Outputs include: File paths, line range references (`file.ts:42-56`), function names, error messages, testable commands, external source URIs.

---

## 2. Setup

### 2.1 Plan Mode Check

**IMPORTANT:** If not already in plan mode, use `EnterPlanMode` tool NOW.

### 2.2 Planning Task Setup (MANDATORY FIRST)

1. **Quote User Request**: In your plan output, include:
   ```markdown
   ## User Request
   > $ARGUMENTS
   ```
   (Quote only the user's custom text from $ARGUMENTS, not this instruction file)

2. **Create [PLANNING] Tasks** for checklist items:
   `TaskCreate(subject="[PLANNING] Status: [item]", activeForm="Checking [item]...")`

3. **Set Dependencies**: `TaskUpdate` with `addBlockedBy` for sequential steps

4. **Track Progress**: `TaskUpdate(taskId, status="in_progress")` → `TaskUpdate(taskId, status="completed")`

---

## 3. MODE: PLAN SYNC (Not Execution)

Quick update syncing plan status with codebase state. Not a full refinement. Do NOT execute code changes until user approves proceeding.

### Constraints: Sync, Don't Replace

**DO:**
1. Check if planned items already complete
2. Update file paths if files moved
3. Add newly discovered dependencies/blockers
4. Mark items done if code exists
5. Note blockers or changes

**DO NOT:**
1. Rewrite entire plan
2. Remove sections without explicit justification
3. Change goals without discussion
4. Skip verification

---

## 4. Quick Checklist

For each plan item, output ONE status:

| Status | Format | Example |
|--------|--------|---------|
| DONE | `DONE: [item] - verified at file.ts:42-56` | `DONE: Add auth - verified at auth.ts:15-28` |
| PENDING | `PENDING: [item] - no changes needed` | `PENDING: Add tests - no changes needed` |
| BLOCKED | `BLOCKED: [item] - blocked by [reason]` | `BLOCKED: Deploy - blocked by failing tests` |
| OUTDATED | `OUTDATED: [item] - path changed to [new]` | `OUTDATED: handler.js - path changed to handler.ts` |
| REMOVED | `REMOVED: [item] - reason: [justification]` | `REMOVED: Legacy API - reason: deprecated in v2` |

---

## 5. Wait Process (Abbreviated)

**Note**: This is a one-time verification after all status changes, not executed per-item like full planning modes.

After completing all status changes, verify:
1. Accuracy of status assignments
2. Nothing missed in checklist
3. TaskList updated via `TaskUpdate`

---

## 6. Status Tracking

**Note**: planupdate is a quick sync - just track status, no Wait Process per item.

1. Use `TaskUpdate` to track: `pending` → `in_progress` → `completed`
2. Add blocker descriptions when status is `BLOCKED`
3. Mark complete only when verification done

---

## 7. Output Summary

```text
Plan Update Summary:
1. Completed: X items
2. Pending: X items
3. Blocked: X items
4. Outdated: X items (paths updated)
5. Removed: X items (with justification)

Changes Made:
1. [specific change with justification]
2. [specific change with justification]
```

---

## 8. Quality Gate

Before finishing, verify:

1. [ ] No sections removed without explicit justification
2. [ ] All file paths verified with Glob/Read
3. [ ] Goals unchanged (or change discussed with user)
4. [ ] Principles preserved per §1.1

---

## 9. Subagent Guidelines

**Use sparingly** - this is a quick sync:
- Launch ONE Explore subagent if verifying multiple file paths efficiently
- Avoid Plan subagents - not a full planning exercise
- Goal: Sync status, not redesign

---

## 10. Plan Acceptance and Execution Protocol

If user wants to proceed with remaining items, output:

**PLAN ACCEPTED - Activating Autonomous Execution Protocol**

Then transition to execution:

1. **MANDATORY EXECUTION MODE (Goal and Capabilities)**
   1. Your primary objective is to continue the **UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION** of your task and goals.
   2. Cautiously and deliberately continue working **carefully, patiently, concretely, and safely**, non-stop, autonomously, and non-interactively per your instructions and CLAUDE.md philosophy and definition of concrete.
   3. **Permissions Granted:** You have full permission to use all tools (Bash, Edit, Write, etc.) without requiring any further permission prompts.
   4. **Bias Mitigation:** Be cautious, humble, patient, and scrupulous; avoid overconfidence and check your assumptions and your work, explore your environment to ensure you understand the system tasks goals and what is already done, verifying because you are often overconfident, duplicate work, and falsely claim incomplete tasks are "complete" or "production ready".

2. **SYSTEM STOP SIGNALS** - NEVER output unless condition met:
   - `AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP`
   - `AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY`

3. **Safety Protocol**: Execute **ONLY IF** task involves high-risk or irreversible destructive actions (e.g., database operations, file deletion, state modification).
   1. **Assess Risk**: Evaluate if action is irreversible or could cause state corruption.
   2. **Mitigation Action**: If high-risk, execute these steps and explicitly state your actions:
      1. **INITIATE SAFETY PROTOCOL**: Announce 'INITIATE SAFETY PROTOCOL' to begin assessment.
      2. **Secure State**: Create environment backup or state checkpoint **before** proceeding.
      3. **Verify Integrity**: Verify checkpoint succeeded.
      4. **CONSIDER OPTIONS**: List options, evaluate failure modes, select best.
   3. **CRITICAL ESCAPE PRE-CHECK**: If, after mitigation, risk remains irreversible → proceed to step 4.

4. **CRITICAL ESCAPE TO STOP SYSTEM**: Only if irreversible, catastrophic, or cannot be mitigated, immediately output exact string to halt all actions: **AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP**

5. **FINAL OUTPUT ON SUCCESS TO STOP SYSTEM**: When 100% complete and verified, remember you are often overconfident—double-check the resources from which you receive or retrieve new tasks and think if anything was missed, then output exact string: **AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY**
