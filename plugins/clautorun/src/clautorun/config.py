#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright 2025 Andrew Hundt <ATHundt@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Centralized configuration for clautorun plugin.

This module provides the single source of truth for all configuration
constants, following DRY (Don't Repeat Yourself) principles.

Usage:
    from clautorun.config import CONFIG
    # or
    from clautorun import CONFIG
"""

# =============================================================================
# Command Blocking System v0.6.0
# =============================================================================
# Fields:
#   suggestion: Message shown when command is blocked
#   commands: Alternative commands to suggest (can use {args} placeholder)
#   commands_description: Explains what the alternative command does
#   when: Predicate name - returns True to block, False to allow (absent = always block)
# =============================================================================
DEFAULT_INTEGRATIONS = {
    "rm": {
        "suggestion": "Use the 'trash' CLI command instead for safe file deletion.\n\nExample:\n  Instead of: rm /path/to/file\n  Use: trash /path/to/file\n\nThe 'trash' command safely moves files to the trash instead of permanently deleting them.\n\nInstall: brew install trash (macOS) or go install github.com/andraschume/trash-cli@latest (Linux)\n\nTo allow in this session: /cr:ok rm",
        "commands": ["trash {args}"],
        "commands_description": "Move to trash (recoverable)",
    },
    "rm -rf": {
        "suggestion": "Use the 'trash' CLI command instead - rm -rf is permanently destructive.\n\nExample:\n  Instead of: rm -rf /path/to/dir\n  Use: trash /path/to/dir\n\nThe 'trash' command safely moves files to the trash instead of permanently deleting them.\n\nInstall: brew install trash (macOS) or go install github.com/andraschume/trash-cli@latest (Linux)\n\nTo allow in this session: /cr:ok rm -rf",
        "commands": ["trash {args}"],
        "commands_description": "Move to trash (recoverable)",
    },
    "git reset --hard": {
        "suggestion": "DANGEROUS: 'git reset --hard' permanently discards all uncommitted changes.\n\n**SAFER ALTERNATIVES (in order of preference):**\n\n1. **Stash changes** (RECOMMENDED - preserves work, easily recoverable):\n   git stash push -m \"WIP: brief description of changes\"\n   # Later: git stash list, git stash pop, or git stash apply\n\n2. **Create backup branch** (if stash isn't suitable):\n   git checkout -b backup/$(date +%Y%m%d-%H%M)-wip\n   git add -A && git commit -m \"WIP: checkpoint before reset\"\n   git checkout -  # return to original branch\n\n3. **Selective restore** (to discard specific files only):\n   git restore <file>           # discard working tree changes\n   git restore --staged <file>  # unstage but keep changes\n\n**View what would be lost:**\n   git status && git diff\n\nTo allow in this session: /cr:ok 'git reset --hard'",
        "commands": ["git stash push -m 'WIP: {args}'"],
        "commands_description": "Stash changes (recoverable)",
    },
    "git checkout .": {
        "suggestion": "DANGEROUS: 'git checkout .' discards ALL uncommitted changes in working directory.\n\n**SAFER ALTERNATIVES:**\n\n1. **Stash changes** (RECOMMENDED):\n   git stash push -m \"WIP: saving changes before checkout\"\n\n2. **Create backup branch**:\n   git checkout -b backup/$(date +%Y%m%d-%H%M)-wip\n   git add -A && git commit -m \"WIP: checkpoint\"\n   git checkout -\n\n3. **Selective restore** (discard specific files only):\n   git restore <file>\n\n**View what would be lost:**\n   git diff\n\nTo allow in this session: /cr:ok 'git checkout .'",
        "commands": ["git stash push -m 'WIP: {args}'"],
        "commands_description": "Stash changes (recoverable)",
        "when": "_has_unstaged_changes",
    },
    "git checkout --": {
        "suggestion": "CAUTION: 'git checkout -- <file>' discards unstaged changes to specific file.\n\n**SAFER ALTERNATIVE:**\n   git stash push <file> -m 'WIP: <file>'\n\n**View what would be lost:**\n   git diff <file>\n\nTo allow in this session: /cr:ok 'git checkout --'",
        "commands": ["git stash push {file} -m 'WIP: {file}'"],
        "commands_description": "Stash file changes (recoverable)",
        "when": "_file_has_unstaged_changes",
    },
    "git stash drop": {
        "suggestion": "CAUTION: 'git stash drop' permanently deletes stashed changes.\n\n**SAFER ALTERNATIVES:**\n\n1. **Apply stash instead** (keeps changes):\n   git stash pop    # apply and remove from stash\n   git stash apply  # apply and keep in stash\n\n2. **View stash contents first**:\n   git stash show -p  # see what's in the stash\n\nTo allow in this session: /cr:ok 'git stash drop'",
        "commands": ["git stash pop"],
        "commands_description": "Apply stash instead (keeps changes)",
        "when": "_stash_exists",
    },
    "git clean -f": {
        "suggestion": "DANGEROUS: 'git clean -f' permanently deletes untracked files.\n\n**SAFER ALTERNATIVES:**\n\n1. **Preview first** (ALWAYS do this):\n   git clean -n   # dry-run, shows what would be deleted\n\n2. **Stash untracked files**:\n   git stash push -u -m \"WIP: stashing untracked files\"\n\n3. **Move to backup** (manual safety):\n   mkdir -p ../backup-untracked && git clean -n | xargs -I{} mv {} ../backup-untracked/\n\n4. **Interactive mode**:\n   git clean -i  # prompts for each file\n\nTo allow in this session: /cr:ok 'git clean -f'",
        "commands": ["git clean -n"],
        "commands_description": "Dry-run preview (shows what would be deleted)",
    },
    "git reset HEAD~": {
        "suggestion": "CAUTION: 'git reset HEAD~' undoes commits (mixed reset by default).\n\n**SAFER ALTERNATIVES:**\n\n1. **Soft reset** (keeps all changes staged):\n   git reset --soft HEAD~1\n\n2. **Create backup branch first**:\n   git checkout -b backup/$(date +%Y%m%d-%H%M)-before-reset\n   git checkout -\n   git reset HEAD~1\n\n3. **Revert instead** (creates new commit, preserves history):\n   git revert HEAD\n\n**Recovery if you already reset:**\n   git reflog  # find the commit hash\n   git reset --hard <hash>  # restore to that point\n\nTo allow in this session: /cr:ok 'git reset HEAD~'",
        "commands": None,
    },
    "dd if=": {
        "suggestion": "Avoid direct disk writes - use proper backup tools. Consider rsync, ddrescue, or backup utilities instead.\n\nTo allow in this session: /cr:ok dd if=",
        "commands": None,
    },
    "mkfs": {
        "suggestion": "Filesystem creation is dangerous - backup data first and use partition managers like GNOME Disks or gparted.\n\nTo allow in this session: /cr:ok mkfs",
        "commands": None,
    },
    "fdisk": {
        "suggestion": "Partition modification is dangerous - backup data first. Use GUI tools like GNOME Disks or gparted for safer operations.\n\nTo allow in this session: /cr:ok fdisk",
        "commands": None,
    }
}


# Configuration - Three-stage completion system with clear instruction/confirmation naming
CONFIG = {
    # ─── Stage 1: Initial Work ────────────────────────────────────────────────
    "stage1_instruction": "starting tasks, analyzing user requirements, and developing comprehensive plan",
    "stage1_confirmation": "AUTORUN_STAGE1_COMPLETE",

    # ─── Stage 2: Critical Evaluation ─────────────────────────────────────────
    "stage2_instruction": "Critically evaluate previous work and continue tasks as needed",
    "stage2_confirmation": "AUTORUN_STAGE2_COMPLETE",

    # ─── Stage 3: Final Verification ──────────────────────────────────────────
    "stage3_instruction": "Verify all tasks completed, critically evaluated, corrected and verified",
    "stage3_confirmation": "AUTORUN_STAGE3_COMPLETE",

    # ─── Descriptive Completion Markers ──────────────────────────────────────
    # NOTE: These are DESCRIPTIVE strings the AI outputs to communicate what it accomplished.
    # The hook system recognizes BOTH the short stage markers AND these descriptive versions.
    # Markdown command files use these descriptive strings for clarity.
    "completion_marker": "AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY",

    # ─── Emergency Stop ───────────────────────────────────────────────────────
    # NOTE: This is a DESCRIPTIVE string that the AI outputs to communicate its action.
    # It should describe WHAT the AI is doing, not just be a state variable name.
    "emergency_stop": "AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP",

    # ─── Plan Acceptance Trigger ───────────────────────────────────────────
    # NOTE: When Claude outputs this string after plan approval, autorun activates automatically
    "plan_accepted_marker": "PLAN ACCEPTED",

    # ─── Timing ───────────────────────────────────────────────────────────────
    "max_recheck_count": 3,
    "monitor_stop_delay_seconds": 300,
    "stage3_countdown_calls": 5,

    # ─── Injection Template ───────────────────────────────────────────────────
    "injection_template": """Your primary objective is to continue the **UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION** of your current tasks and goals.

**THREE-STAGE COMPLETION SYSTEM:**
This system ensures thorough, high-quality work through a structured three-stage process. Each stage builds upon the previous one, with explicit requirements for advancement.

1.  **MANDATORY PROCESS TO CONTINUE EXECUTION:** Cautiously and deliberately continue working **carefully, patiently, concretely, and safely**, non-stop, autonomously, and non-interactively per your instructions and CLAUDE.md philosophy and definition of concrete.
    * **Permissions Granted:** You have full permission to use all tools (Bash, Edit, Write, etc.) without requiring any further permission prompts.
2.  **SYSTEM STOP SIGNAL RULE:** The exact strings **{emergency_stop}**, **{stage1_confirmation}**, **{stage2_confirmation}**, and **{stage3_confirmation}** are **SYSTEM STOP SIGNALS**. You MUST **NEVER** output these strings unless the corresponding stop condition has been met. Accidental output will immediately halt the entire system.
3.  **Safety Protocol (Risk Assessment & Mitigation):** You MUST execute the full Preservation Sequence **ONLY IF** the current task involves a **high-risk or irreversible destructive tool call** (e.g., initial modification, database interaction, or action following a prior failure).
    * **Assess Risk:** Implicitly evaluate the potential for irreversible state misalignment or system integrity breach.
    * **Mitigation Action (CONDITIONAL):** If a **high-risk condition is met**, you **MUST immediately execute the following Preservation Sequence** and explicitly state your actions:
        1.  **INITIATE SAFETY PROTOCOL:** Announce 'INITIATE SAFETY PROTOCOL' to begin structured assessment.
        2.  **Secure State:** Execute the recovery command(s) to create an **environment backup** or **state checkpoint** (using available systems and tools) *before* proceeding.
        3.  **Verify Integrity:** Run a quick non-destructive check to ensure the state checkpoint was successful.
        4.  **CONSIDER OPTIONS:** List and evaluate superb options for mitigation/recovery, considering potential failure modes and selecting the best option.
    * **CRITICAL ESCAPE PRE-CHECK:** If, after executing the Mitigation Action, the risk remains irreversible, proceed directly to **Step 4: CRITICAL ESCAPE TO STOP SYSTEM**.
4.  **CRITICAL ESCAPE TO STOP SYSTEM (Final Decision):** Only if the risk is irreversible, catastrophic, or cannot be fully mitigated, you **MUST initiate the Preservation Protocol** by immediately outputting the following exact string to immediately halt all actions: **{emergency_stop}**
5.  **STAGE 1 - INITIAL IMPLEMENTATION:** {stage1_instruction}
    * When Stage 1 is complete, output **{stage1_confirmation}** to advance to Stage 2
6.  **STAGE 2 - CRITICAL EVALUATION:** {stage2_instruction}
    * When Stage 2 is complete, output **{stage2_confirmation}** to advance to Stage 3
7.  **STAGE 3 - FINAL VERIFICATION:** {stage3_instruction}
    * Stage 3 instructions: {stage3_instructions}
    * When Stage 3 is complete, output **{stage3_confirmation}** for final completion
8.  **FINAL OUTPUT ON SUCCESS TO STOP SYSTEM:** Only when all three stages are complete and verified, output **{stage3_confirmation}** to stop the system
9.  **FILE CREATION POLICY:** {policy_instructions}""",

    # ─── Recheck Template ─────────────────────────────────────────────────────
    "recheck_template": """AUTORUN TASK VERIFICATION: The task appears complete but requires careful verification before final confirmation.

Original Task: {activation_prompt}

CRITICAL VERIFICATION INSTRUCTIONS:
1. Carefully review ALL aspects of the original task above
2. Verify EVERY requirement has been fully met and tested
3. Check for any incomplete, partial, or missed elements
4. Test any implemented functionality thoroughly
5. Double-check your work against the original requirements
6. Verify all files are in their correct final state
7. Ensure no temporary or incomplete work remains
{verification_requirements}

Only if you are ABSOLUTELY CERTAIN everything is complete, tested, and meets all requirements, output: {stage3_confirmation}

If ANY aspect is incomplete, uncertain, or needs additional work, continue until truly finished.

This is verification attempt #{recheck_count} of {max_recheck_count}.""",

    # ─── Forced Compliance Template ───────────────────────────────────────────
    "forced_compliance_template": """AUTORUN FORCED COMPLIANCE OVERRIDE: System has detected prolonged verification cycles.

Original Task: {activation_prompt}

FORCED COMPLIANCE PROTOCOL ACTIVATED:
Due to extended verification duration, the system is forcing task completion with the following requirements:

{verification_requirements}

SYSTEM OVERRIDE INSTRUCTIONS:
1. Complete any remaining critical requirements immediately
2. Ensure basic functionality is implemented and working
3. Add any missing documentation or comments
4. Perform final validation and cleanup

After completing the above forced requirements, output: {stage3_confirmation}

NOTE: This is a forced compliance override to prevent infinite verification loops.
Ensure core functionality is working before final completion.""",

    # ─── Procedural Injection Template (Wait Process Methodology) ─────────────
    "procedural_injection_template": """Your primary objective is to continue the **UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION** of your current tasks and goals using the **Sequential Improvement Methodology**.

**WAIT PROCESS (Execute after every step and substep):**
After every step and substep you must say "Wait," and execute this sequential thinking process:

1. **Elaborate and Refine Best Practices**: Elaborate and refine best practices lists based on current context
2. **Comprehensive Critique**: Harshly and constructively critique your work overall and line by line against every single best practice and criteria
3. **Pre-mortem Analysis**: Identify potential failure modes and weaknesses
4. **Multiple Solution Generation**: Propose multiple concrete solutions to each identified issue
5. **Synthesized Solution Building**: Synthesize insights from all previous critiques and solutions
6. **Sequential Quality Enhancement**: Each proposal must be superb quality, building on previous iterations
7. **Best Solution Selection**: Choose the optimal solution from all proposals with compelling justification
8. **Error Correction Protocol**: If errors are found, immediately insert and execute corrective steps

**THREE-STAGE COMPLETION SYSTEM:**
1. **STAGE 1 - INITIAL IMPLEMENTATION:** {stage1_instruction}
   * When Stage 1 is complete, output **{stage1_confirmation}** to advance to Stage 2
2. **STAGE 2 - CRITICAL EVALUATION:** {stage2_instruction}
   * When Stage 2 is complete, output **{stage2_confirmation}** to advance to Stage 3
3. **STAGE 3 - FINAL VERIFICATION:** {stage3_instruction}
   * Stage 3 instructions: {stage3_instructions}
   * When Stage 3 is complete, output **{stage3_confirmation}** for final completion

**SYSTEM STOP SIGNALS:** The exact strings **{emergency_stop}**, **{stage1_confirmation}**, **{stage2_confirmation}**, and **{stage3_confirmation}** are SYSTEM STOP SIGNALS. NEVER output these unless the corresponding stop condition has been met.

**CRITICAL ESCAPE TO STOP SYSTEM:** Only if risk is irreversible, output: **{emergency_stop}**

**FILE CREATION POLICY:** {policy_instructions}""",

    # ─── Policies ─────────────────────────────────────────────────────────────
    "policies": {
        "ALLOW": ("allow-all", "ALLOW ALL: Full permission to create/modify files."),
        "JUSTIFY": ("justify-create", "JUSTIFIED: Search existing first. Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> for new files."),
        "SEARCH": ("strict-search", "STRICT SEARCH: ONLY modify existing files. Use Glob/Grep. NO new files.")
    },

    # ─── Policy Blocked Messages ──────────────────────────────────────────────
    "policy_blocked": {
        "SEARCH": 'Blocked: STRICT SEARCH policy active. To proceed: 1) Identify what functionality this file provides, 2) Search for existing files handling similar functionality using Glob patterns like "*related-topic*", 3) Use Grep to find files with relevant classes/functions/imports, 4) Modify the most appropriate existing file. Search examples: "*auth*" for authentication, "*api*" for endpoints, "*config*" for settings, "*model*" for data structures.',
        "JUSTIFY": "Blocked: JUSTIFIED CREATION policy requires justification. To proceed: 1) Search for existing files using Glob/Grep related to your functionality, 2) Evaluate if existing files can be extended, 3) If no existing file works, include <AUTOFILE_JUSTIFICATION>Specific technical reason why existing files cannot accommodate this functionality</AUTOFILE_JUSTIFICATION> in your reasoning during the same prompt where you request the file creation, then retry file creation."
    },

    # ─── Command Mappings ─────────────────────────────────────────────────────
    # Values must match keys in COMMAND_HANDLERS (case-sensitive)
    # Commands support /cr: prefix with short and long forms
    "command_mappings": {
        # ─── New Short Forms (/cr: prefix) ────────────────────────────────────
        "/cr:a": "ALLOW",           # Allow all file creation
        "/cr:j": "JUSTIFY",         # Justify new files
        "/cr:f": "SEARCH",          # Find existing files only
        "/cr:st": "STATUS",         # Show status
        "/cr:go": "activate",       # Start autorun
        "/cr:gp": "activate",       # Start autoproc (procedural)
        "/cr:x": "stop",            # Graceful stop
        "/cr:sos": "emergency_stop", # Emergency stop
        "/cr:tm": "tmux_session",   # Tmux session management
        "/cr:tt": "tmux_test",      # Tmux test workflow

        # ─── New Long Forms (/cr: prefix) ─────────────────────────────────────
        "/cr:allow": "ALLOW",       # Allow all file creation
        "/cr:justify": "JUSTIFY",   # Justify new files
        "/cr:find": "SEARCH",       # Find existing files only
        "/cr:status": "STATUS",     # Show status
        "/cr:run": "activate",      # Start autorun
        "/cr:proc": "activate",     # Start autoproc (procedural)
        "/cr:stop": "stop",         # Graceful stop
        "/cr:estop": "emergency_stop", # Emergency stop
        "/cr:tmux": "tmux_session", # Tmux session management
        "/cr:ttest": "tmux_test",   # Tmux test workflow (ttest to avoid collision with test.md)

        # ─── Legacy Commands (backward compatibility) ─────────────────────────
        "/autorun": "activate",
        "/autoproc": "activate",
        "/autostop": "stop",
        "/estop": "emergency_stop",
        "/afs": "SEARCH",
        "/afa": "ALLOW",
        "/afj": "JUSTIFY",
        "/afst": "STATUS",

        # ─── Command Blocking (NEW in v0.6.0) ───────────────────────────────────────
        "/cr:no": "BLOCK_PATTERN",
        "/cr:ok": "ALLOW_PATTERN",
        "/cr:clear": "CLEAR_PATTERN",
        "/cr:globalno": "GLOBAL_BLOCK_PATTERN",
        "/cr:globalok": "GLOBAL_ALLOW_PATTERN",
        "/cr:globalstatus": "GLOBAL_BLOCK_STATUS"
    },

    # Built-in command integrations (suggestions for dangerous commands)
    "default_integrations": DEFAULT_INTEGRATIONS
}
