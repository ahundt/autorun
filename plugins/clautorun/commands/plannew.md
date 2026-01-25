---
description: Create new plan from scratch
argument-hint: <feature/project description>
---

# Create New Plan (/cr:plannew)

$ARGUMENTS

## 0. Plan Mode Check

**IMPORTANT:** If you are not already in plan mode, use the `EnterPlanMode` tool NOW before proceeding. Planning commands require plan mode for proper operation.

## MODE: PLAN CREATION (Not Execution)

You are creating a NEW plan document from scratch. There is no existing plan to reference. Do NOT execute code changes until plan is approved.

## 1. Your Process

1. **Identify Expertise Areas**: Write the areas of expertise needed and act as an expert in those areas.
2. **Generate Best Practices**: For each area of expertise separately write 10 best practices generally and 10 best practices specifically for the task.
3. **Explore Context**: Explore to understand the context and determine what is already done and the right point at which to work to avoid duplication.
4. **Launch Subagents for Exploration**: Use the Task tool to launch parallel Explore subagents (up to 3) to efficiently understand:
    - Existing implementations and patterns in the codebase
    - Related components and dependencies
    - Testing patterns and conventions
5. **Requirements Discovery**:
    1. **Clarify the Goal**: What specific outcome does this plan achieve?
    2. **Identify Stakeholders**: Who benefits? (users, developers, maintainers)
    3. **Define Success Criteria**: How will we know when this is complete?
    4. **Scope Boundaries**: What is explicitly IN scope? What is OUT of scope?
6. **Architecture Exploration**: Before designing, explore the codebase:
    1. Use Glob to find related files
    2. Read 2-3 similar implementations for reference
    3. Identify existing patterns to follow
    4. **Concrete Requirement**: Cite specific files (e.g., `src/handler.ts:42-56`)
7. **Launch Plan Subagent**: Use the Task tool with Plan subagent to design the implementation approach based on exploration results.
8. **Plan Structure**: Create plan addressing CLAUDE.md Philosophy checklist
9. **Critique Work**: Critique your work overall and line by line.
10. **Propose Multiple Solutions**: Propose multiple solutions to each issue and choose the best solution - this needs to make a compelling case.
11. **Describe Logic Flow**: Describe the logic flow as you go through the description.
12. **Use Actual Quotes**: Use actual quotes and file:line references whenever possible.
13. **Thread Management**: Each thread of updates needs a unique name, with an incrementing version number (`<taskname>-v1`, `<taskname>-v2`, ...).
14. **MANDATORY WAIT PROCESS TRIGGER**: After every step and sub-step of your plan, both during creation and execution, you must say "Wait," and execute **Your Wait Process** "out loud" step by step and substep by substep.
15. **Final Verification**: Check your work. Do not hallucinate.

## 2. Subagent Usage Guidelines

**When to use subagents:**
- **Explore subagents**: For codebase exploration, finding patterns, understanding existing implementations
- **Plan subagents**: For designing implementation approaches, considering alternatives, architectural decisions
- **Specialized agents**: Use code-reviewer, code-explorer, code-architect agents when their descriptions match your needs

**Parallel execution**: Launch multiple subagents in a single message when their tasks are independent.

## 3. Plan Output Format

Create plan with:

1. **Summary**: 2-3 sentences describing the goal and approach
2. **Checklist**: Concrete checkbox items with file:line references
3. **Dependencies**: What must be done first?
4. **Risks**: What could go wrong?
5. **Verification Steps**: How to test each step?

## 4. Your Wait Process (Sequential Improvement Methodology)

After every step and sub-step of your plan you must say "Wait," and execute this sequential thinking process:

1. **Elaborate and Refine Best Practices**: Elaborate and refine best practices lists (create new lists if none exist yet) based on current context - keep refining as new circumstances develop.
2. **Comprehensive Critique**: Harshly and constructively critique your work overall and line by line against every single best practice and criteria.
3. **Pre-mortem Analysis**: Identify potential failure modes, edge cases, and weaknesses. Assume the final outcome was a disaster, determine what went wrong, and how those outcomes can be prevented.
4. **Multiple Solution Generation**: Propose multiple new and/or best practice superb quality concrete solutions (high level and specific quoted implementations) to each identified issue in each of your task, your critiques, and in the pre-mortem analysis.
5. **Synthesized Solution Building**: Synthesize insights from all cumulative context including all previous critiques, the original work (if applicable), all previous proposed solutions, and all accumulated best practices to create refined solutions that incorporate lessons learned from the complete analysis.
6. **Sequential Quality Enhancement**: Each proposal must be superb quality, building on the benefits of previous iterations.
7. **Best Solution Selection**: Choose the optimal solution from all proposals including the original and synthesized, or combinations of all proposals, in ranked order with compelling justification.
8. **Error Correction Protocol**: If the wait process identifies errors, immediately write it in your todo list and execute corrective steps to redo the work correctly.

## 5. Checkbox Management Logic

- **Create plan with checkbox items** → Execute Wait Process (both during creation and execution)
  - put all checkbox items in your todo list
  - new markdown files with todo items need checkbox items
  - existing markdown files with checkbox todos need checkboxes for new todos too
- **Execute each checkbox item** → Execute Wait Process after completion
- **Check off boxes only when**: Execution is complete AND the task is complete including after the error correction protocol is complete (this is a todo list)
- **If wait process finds errors**: Continue working until error correction protocol resolves all issues, then check off

## 6. Quality Standards & Limitations

The goal is to work towards your overall goal with sufficiently detailed and verifiable outcomes:

- **Sequential improvement**: Each iteration builds on previous insights for compounding quality gains
- **Superb proposal quality**: All solutions must meet the highest standards with thorough justification
- **Direct quotes and meaningful justifications**: Show why assessments are made, not just what they are
- **Verifiable outcomes**: All reasoning must be transparent and reproducible by others
- **Compelling cases**: Final selections must demonstrate clear superiority through rigorous comparison

**Suggested Improvements:**

- Consider time-boxing wait processes for efficiency while maintaining quality
- Document recurring patterns to build institutional knowledge
- Consider parallel evaluation of solutions where appropriate
- Establish clear quality gates to prevent perfectionism paralysis

## 7. CLAUDE.md Philosophy

Every plan element must address:

- [ ] TDD: Tests written first? Test location specified?
- [ ] DRY: No code duplication? Reusing existing utilities?
- [ ] OODA: Observe-Orient-Decide-Act cycle followed?
- [ ] KISS: Simplest solution that works?
- [ ] YAGNI: No premature optimization or over-engineering?
- [ ] SOLID: Single responsibility? Open/closed? Interface segregation?
- [ ] RAII: Resources properly managed?
- [ ] WOLOG: Without loss of generality?

## 8. Definition of Concrete

All outputs must include:

- Absolute file paths: `/path/to/file.ts`
- Line references: `file.ts:42-56`
- Exact function/class names: `handleRequest()` in `handler.ts:15`
- Specific error messages with codes: `"socket hang up (ECONNRESET)"`
- Testable verification commands

## 9. Plan Acceptance and Execution Protocol

When user approves the plan, output:

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
