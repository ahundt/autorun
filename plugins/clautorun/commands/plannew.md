---
description: Create new plan from scratch
argument-hint: <feature/project description>
---

# Create New Plan (/cr:plannew)

$ARGUMENTS

---

## 1. Foundation (Read First)

### 1.1 Key Principles

| Acronym | Meaning | Question to Ask |
|---------|---------|-----------------|
| TDD | Test-Driven Development | Tests written first? Location specified? |
| DRY | Don't Repeat Yourself | No code duplication? Reusing utilities? |
| OODA | Observe-Orient-Decide-Act | Cycle followed before each decision? |
| KISS | Keep It Simple, Stupid | Simplest solution that works? |
| YAGNI | You Aren't Gonna Need It | No premature optimization? |
| SOLID | Single responsibility, Open/closed, Liskov, Interface segregation, Dependency inversion | Each principle addressed? |
| RAII | Resource Acquisition Is Initialization | Resources properly managed? |
| WOLOG | Without Loss Of Generality | Solution generalizes appropriately? |

### 1.2 Definition of Concrete

All outputs should include where applicable:

1. **File paths**: `/path/to/file.ts` or `./relative/path.ts`
2. **Line references**: `file.ts:42-56`
3. **Function/class names**: `handleRequest()` in `handler.ts:15-28`
4. **Error messages**: `"socket hang up (ECONNRESET)"`
5. **Testable commands**: `npm test -- --grep "auth"`
6. **Sources and references**: All sources used (web URIs, file:// URIs, web searches, documentation, research papers) must be recorded with full URIs and cited in plan output

### 1.3 Quality Standards

1. **Sequential improvement**: Each iteration builds on previous insights
2. **Superb quality**: All solutions meet highest standards with thorough justification
3. **Direct quotes**: Show WHY assessments are made, not just WHAT
4. **Verifiable outcomes**: Reasoning transparent and reproducible
5. **Compelling cases**: Selections demonstrate clear superiority

---

## 2. Setup

### 2.1 Plan Mode Check

**IMPORTANT:** If not already in plan mode, use `EnterPlanMode` tool NOW.

### 2.2 Planning Task Setup (MANDATORY FIRST)

Before any planning work, create ALL planning tasks:

1. **Quote User Request** at plan output top:
   ```markdown
   ## User Request
   > [user's $ARGUMENTS text here]
   ```

2. **Create [PLANNING] Tasks** for EVERY step AND substep:
   1. `TaskCreate(subject="[PLANNING] Step N: [name]", activeForm="Planning [name]...")`
   2. `TaskCreate(subject="[PLANNING] Step N.M: [substep]", activeForm="Planning [substep]...")`
   3. The `[PLANNING]` prefix distinguishes from execution tasks

3. **Parse $ARGUMENTS** into fine-grained requirement tasks:
   1. Identify each distinct requirement
   2. `TaskCreate(subject="[PLANNING] Requirement: [item]")` for each
   3. Example: "Add auth with OAuth and JWT" → two tasks: OAuth planning, JWT planning

4. **Set Dependencies**: `TaskUpdate(taskId=N, addBlockedBy=[N-1])` for ordered steps

5. **Track Progress**:
   1. Start: `TaskUpdate(taskId, status="in_progress")`
   2. Finish: `TaskUpdate(taskId, status="completed")`

---

## 3. MODE: PLAN CREATION (Not Execution)

You are creating a NEW plan from scratch. No existing plan to reference. Do NOT execute code changes until plan approved.

---

## 4. Process Steps

**PERSISTENT RULE**: After EVERY step AND substep of your plan (both during creation and execution), you must output the exact string "Wait," then execute §5 Wait Process "out loud" step by step and substep by substep.

### Step 1: Identify Expertise Areas
List expertise areas needed. Act as expert. **Output in plan**: State expertise explicitly.

### Step 2: Generate Best Practices
For each expertise area separately, write:
- 10 general best practices
- 10 task-specific best practices

**Output in plan** under "## Best Practices" as numbered list.

### Step 3: Explore Context
Understand existing work. Determine right integration point. Avoid duplication.

### Step 4: Launch Explore Subagents
Use Task tool to launch parallel Explore subagents (up to 3) to understand:
1. Existing implementations and patterns
2. Related components and dependencies
3. Testing patterns and conventions

**CRITICAL - Verify Subagent Findings:**
1. Use Read tool to verify EVERY file reference
2. Confirm code snippets match actual content
3. Do NOT trust unverified claims
4. CITE file:line-range for verified findings
5. Mark unverifiable findings as "[UNVERIFIED]"

### Step 5: Requirements Discovery
1. **Clarify Goal**: What specific outcome does this plan achieve?
2. **Identify Stakeholders**: Who benefits? (users, developers, maintainers)
3. **Define Success Criteria**: How will we know when complete?
4. **Scope Boundaries**: What is IN scope? What is OUT of scope?

### Step 6: Architecture Exploration
Before designing, explore codebase:
1. Use Glob to find related files
2. Read 2-3 similar implementations for reference
3. Identify existing patterns to follow
4. **Concrete Requirement**: Cite specific files (e.g., `src/handler.ts:42-56`)

### Step 7: Launch Plan Subagent
Use Task tool with Plan subagent to design implementation from exploration results.

### Step 8: Plan Structure
Create plan addressing §1.1 Philosophy checklist:
- [ ] TDD, [ ] DRY, [ ] OODA, [ ] KISS, [ ] YAGNI, [ ] SOLID, [ ] RAII, [ ] WOLOG

### Step 9: Describe Logic Flow
Trace logic flow through the description. Show data/control flow.

### Step 10: Use Actual Quotes
Quote actual code with file:line-range references. No paraphrasing.

### Step 11: Critique Work
Critique overall + line-by-line against ALL best practices (§1.1) and quality standards (§1.3).

### Step 12: Propose Multiple Solutions
Propose ≥3 distinct solutions to each issue. Choose best with compelling justification.

### Step 13: Thread Management
Each thread of updates (different approaches/solutions being refined) needs a unique name with incrementing version number (`<taskname>-v1`, `<taskname>-v2`, ...). This enables referencing specific iterations: "In auth-solution-v2 we tried X, but auth-solution-v3 uses Y".

### Step 14: Final Verification - Completeness Check

1. **Verify ALL cases from $ARGUMENTS:**
   1. [ ] Primary use case implemented
   2. [ ] ALL edge cases from requirements included
   3. [ ] ALL languages/platforms mentioned covered
   4. [ ] ALL file types/formats specified handled
   5. [ ] ALL user examples addressed
   6. [ ] No requirements silently dropped

2. Check your work. Do not hallucinate.
3. Verify all file references with Read tool
4. Confirm code examples are syntactically correct
5. Ensure no steps skipped (check TaskList)
6. **If ANY case missing**: Return to relevant step. Do NOT proceed incomplete.

---

## 5. Wait Process (Sequential Improvement Methodology)

After outputting "Wait," execute these 8 steps:

1. **Elaborate Best Practices**: Elaborate and refine best practices lists (create new lists if none exist yet) as numbered list for current context; keep refining as new circumstances develop
2. **Comprehensive Critique**: Harshly and constructively critique overall + line-by-line against every single best practice and criteria
3. **Pre-mortem Analysis**: Identify potential failure modes, edge cases, weaknesses. Assume disaster → determine what went wrong → prevent those outcomes
4. **Multiple Solution Generation**: Propose ≥3 superb quality concrete solutions (high-level + specific code quotes) to each identified issue in your task, critiques, and pre-mortem analysis
5. **Synthesized Solution Building**: Synthesize insights from all cumulative context including all previous critiques, the original work (if applicable), all previous proposed solutions, and all accumulated best practices to create refined solutions that incorporate lessons learned from the complete analysis
6. **Sequential Quality Enhancement**: Each proposal must be superb quality, building on benefits of previous iterations
7. **Best Solution Selection**: Choose the optimal solution from all proposals including the original, synthesized, or combinations of all proposals, in ranked order with compelling justification
8. **Error Correction Protocol**: On error: immediately TaskCreate(fix) → TaskUpdate ALL steps from error point onward (failed_step and all subsequent steps) back to "pending" or "in_progress" status → Update dependencies (addBlockedBy) for reset steps to reflect corrected sequence → execute corrective steps → return to the step that produced the error and redo from there

---

## 6. Checkbox Management

1. **Create plan with checkboxes** → Put all in TaskList via TaskCreate
2. **Execute each checkbox** → Execute Wait Process after completion
3. **Check off only when**: Execution complete AND error correction complete
4. **On errors**: Continue until resolved, then check off

---

## 7. Output Format

### Plan Structure
1. **Summary**: 2-3 sentences (goal + approach)
2. **Checklist**: Concrete items with file:line-range references
3. **Dependencies**: What must be done first?
4. **Risks**: What could go wrong?
5. **Verification**: How to test each step?

### Code Block Format
```language
// File: /path/to/file.ext
// Line: XX-YY (where this code goes)
// Purpose: Brief description

[actual code here - not pseudocode]
```

**Requirement**: Every implementation step needs ≥1 code block showing exact change.

---

## 8. Subagent Guidelines

| Subagent Type | When to Use |
|---------------|-------------|
| **Explore** | Codebase exploration, finding patterns, understanding implementations |
| **Plan** | Designing approaches, alternatives, architectural decisions |
| **code-architect** | Complex architectural planning |
| **code-explorer** | Deep analysis of existing features |
| **code-reviewer** | Code quality, bugs, security review |

**Parallel execution**: Launch multiple in single message when tasks are independent.

---

## 9. Plan Acceptance and Execution Protocol

When user approves, output:

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
