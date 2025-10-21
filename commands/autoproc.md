# Your Task (/autorun Workflow: Sequential Improvement Methodology)

 $ARGUMENTS - fully autonomously following your process and your wait process and make a concrete step by step plan with checkbox checklist items broken down step by step and substep by substep and add everything to your todo list per the checkbox management logic and immediately execute it with your process doing each of the mandatory execution mode, the rigorous methodology steps, the wait process, and the safety and exit protocols as needed throughout.

## 1. Your Process

1. **MANDATORY EXECUTION MODE (Goal and Capabilities)**
    1. Your primary objective is to continue the **UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION** of your task and goals.
    2. Cautiously and deliberately continue working **carefully, patiently, concretely, and safely**, non-stop, autonomously, and non-interactively per your instructions and CLAUDE.md philosophy and definition of concrete.
    3. **Permissions Granted:** You have full permission to use all tools (Bash, Edit, Write, etc.) without requiring any further permission prompts.
    4. **Bias Mitigation:** Be cautious, humble, patient, and scrupulous; avoid overconfidence and check your assumptions and your work, explore your environment to ensure you understand the system tasks goals and what is already done, verifying because you are often overconfident, duplicate work, and falsely claim incomplete tasks are "complete" or "production ready".
    5. **Safety and Exit Protocols** check the safety and exit protocols.
2. Write the areas of expertise needed (Act as an expert in those areas).
3. For each area of expertise separately write 10 best practices generally and 10 best practices specifically for the task.
4. Explore to understand the context and determine what is already done and the right point at which to work to avoid duplication.
5. Critique your work overall and line by line.
6. Propose multiple solutions to each issue and choose the best solution - this needs to make a compelling case.
7. Describe the logic flow as you go through the description.
8. Use actual quotes whenever possible.
9. **Thread Management:** Each thread of updates needs a unique name, with an incrementing version number (`<taskname>-v1`, `<taskname>-v2`, ...).
10. Check your work. Check the Safety and Exit Protocols. Do not hallucinate.
11. **MANDATORY WAIT PROCESS TRIGGER:** After every step and sub-step of your plan, both during creation and execution, you must say "Wait," and execute **Your Wait Process** "out loud" step by step and substep by substep.

## 2. Your **WAIT PROCESS (Sequential Improvement Methodology)**

After every step and sub-step of your plan you must say "Wait," and execute this sequential thinking process:

1. **Elaborate and Refine Best Practices**: Elaborate and refine best practices lists (create new lists if none exist yet) based on current context - keep refining as new circumstances develop.
2. **Comprehensive Critique**: Harshly and constructively critique your work overall and line by line against every single best practice and criteria.
3. **Pre-mortem Analysis**: Identify potential failure modes and weaknesses, assume the final outcome was a disaster, determine what went wrong, and how those outcomes can be prevented.
4. **Multiple Solution Generation**: Propose multiple new and/or best practice superb quality concrete solutions (high level and specific quoted implementations) to each identified issue in each of your task, your critiques, and in the pre-mortem analysis.
5. **Synthesized Solution Building**: Synthesize insights from all cumulative context including all previous critiques, the original work (if applicable), all previous proposed solutions, and all accumulated best practices to create refined solutions that incorporate lessons learned from the complete analysis.
6. **Sequential Quality Enhancement**: Each proposal must be superb quality, building on the benefits of previous iterations.
7. **Best Solution Selection**: Choose the optimal solution from all proposals including the original and synthesized, or combinations of all proposals, in ranked order with compelling justification.
8. **Error Correction Protocol**: If the wait process identifies errors, immediately write it in your todo list and execute corrective steps to redo the work correctly.

## 3. Checkbox Management Logic

- **Create plan with checkbox items** → Execute Wait Process (both during creation and execution)
  - put all checkbox items in your todo list
  - new markdown files with todo items need checkbox items
  - existing markdown files with checkbox todos need checkboxes for new todos too
- **Execute each checkbox item** → Execute Wait Process after completion
- **Check off boxes only when**: Execution is complete AND the task is complete including after the error correction protocol is complete (this is a todo list)
- **If wait process finds errors**: Continue working until error correction protocol resolves all issues, then check off

## 4. Quality Standards & Limitations

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

## 5. **SAFETY AND EXIT PROTOCOLS (Autonomous Constraints)**

1. **MANDATORY PROCESS TO CONTINUE EXECUTION (Item 1.1 above):** remains in force.
2. **SIGNAL RULE TO STOP SYSTEM:** The exact strings **AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP** and **AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY** are **SYSTEM STOP SIGNALS**. You MUST **NEVER** output these strings unless the corresponding stop condition has been met. Accidental output will immediately halt the entire system.
3. **Safety Protocol (Risk Assessment & Mitigation):** You MUST execute the full Preservation Sequence **ONLY IF** the current task involves a **high-risk or irreversible destructive tool call** (e.g., initial modification, database interaction, or action following a prior failure).
    1. **Assess Risk:** Implicitly evaluate the potential for irreversible state misalignment or system integrity breach.
    2. **Mitigation Action (CONDITIONAL):** If a **high-risk condition is met**, you **MUST immediately execute the following Preservation Sequence** and explicitly state your actions:
        1. **INITIATE SAFETY PROTOCOL:** Announce 'INITIATE SAFETY PROTOCOL' to begin structured assessment.
        2. **Secure State:** Execute the recovery command(s) to create an **environment backup** or **state checkpoint** (using available systems and tools) *before* proceeding.
        3. **Verify Integrity:** Run a quick non-destructive check to ensure the state checkpoint was successful.
        4. **CONSIDER OPTIONS:** List and evaluate superb options for mitigation/recovery, considering potential failure modes and selecting the best option.
    3. **CRITICAL ESCAPE PRE-CHECK:** If, after executing the Mitigation Action, the risk remains irreversible, proceed directly to **Step 5.3: CRITICAL ESCAPE TO STOP SYSTEM**.
4. **CRITICAL ESCAPE TO STOP SYSTEM (Final Decision):** Only if the risk is irreversible, catastrophic, or cannot be fully mitigated, you **MUST initiate the Preservation Protocol** by immediately outputting the following exact string to immediately halt all actions: **AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP**
5. **FINAL OUTPUT ON SUCCESS TO STOP SYSTEM (Final Decision):** When all tasks and goals are all 100% complete and verified, remember you are often overconfident, so first double check the resources from which you receive or retrieve new tasks and think if anything was missed, you **MUST** conclude your entire final response with the exact string: **AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY**
