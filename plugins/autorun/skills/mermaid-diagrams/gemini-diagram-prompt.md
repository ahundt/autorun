# Gemini Image Generation Prompt for autorun

Copy the prompt below into Gemini and ask it to generate an image.

---

Create a clean, professional flowchart diagram for the "autorun" project — a Claude Code plugin that enables autonomous AI task execution with built-in safety guards.

The diagram should show the user's journey from intent to outcome:

1. USER INTENT: Developer has a task (bug fix, feature, refactor)
2. COMMAND: Types "/ar:go <task>" in Claude Code CLI
3. SAFETY LAYER: AutoFile policy controls what files AI can create/modify
   - allow-all: full freedom
   - justify-create: requires reasoning
   - find-only: modify existing files only
4. THREE-STAGE EXECUTION:
   - Stage 1: AI implements the task
   - Stage 2: AI critically evaluates its own work, finds gaps, fixes issues
   - Stage 3: AI verifies all requirements met
5. SAFETY GUARDS: Dangerous commands blocked (rm → trash, git reset --hard → git stash)
6. OUTCOME: Verified, complete work with all three stages confirmed

Style requirements:
- Left-to-right flow showing progression from intent to outcome
- Color-coded stages (blue for user actions, green for safe execution, red for blocked actions)
- Icons or visual indicators for safety checkpoints
- Clean, minimal design suitable as a README hero image
- Show the key value proposition: "autonomous execution that self-verifies"
