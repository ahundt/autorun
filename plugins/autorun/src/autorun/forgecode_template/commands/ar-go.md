---
name: ar-go
description: Start an autorun task with three-stage verification (initial → critical review → final verification)
---

Begin an autonomous run for the task described below. You must produce three
distinct stages and label each transition explicitly:

1. **Stage 1 — Initial implementation.** Implement the task. End the stage with the
   marker `AUTORUN_INITIAL_TASKS_COMPLETED` on its own line.
2. **Stage 2 — Critical evaluation.** Re-read your own work, list gaps and issues
   you find, fix them. End the stage with
   `CRITICALLY_EVALUATING_PREVIOUS_WORK_AND_CONTINUING_TASKS_AS_NEEDED`.
3. **Stage 3 — Final verification.** Verify that every requirement is satisfied
   and tests/build still pass. End with
   `AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY`.

Follow the safety guardrails in AGENTS.md. Do not skip stages.

{{parameters}}
