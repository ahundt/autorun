**CLAUDE CODE 2026: THE UNABRIDGED UNIFIED AUTOMATION ARCHITECTURE**
*Authorized Technical Specification | Version 4.2.0 (Stable) | Revision 2026-02-02*

This document serves as the **definitive, unabridged reference** for the Claude Code 2026 runtime. It aggregates all architectural changes, including the Unified Skills Protocol, Programmatic Tool Calling (PTC), the Event Hooks System, and the Plugin Distribution Manifest.

It is designed to be the single source of truth. **Any deviation from the protocols defined below constitutes a system failure.**

---

### **1. THE UNIFIED SKILLS PROTOCOL (USP)**

In the 2026 architecture, the legacy distinction between "Slash Commands" and "Tools" has been abolished. All deterministic capabilities are now unified under **Skills**. A Skill is a directory-based unit containing configuration, natural language instruction, and executable logic.

#### **1.1 The Skill Directory Structure**

Skills are auto-discovered by the runtime on `SessionStart`.

* **Global Skills:** `~/.claude/skills/`
* **Project Skills:** `./.claude/skills/`

**Standard Layout:**

```text
.claude/skills/
└── sync-deps/           # The Skill Name (invoked via /sync-deps)
    ├── SKILL.md         # The Definition & Front Matter
    └── scripts/         # Local support scripts
        └── audit.py     # Deterministic logic called by the skill

```

#### **1.2 The `SKILL.md` Front Matter Specification**

The `SKILL.md` file uses YAML front matter to define permissions, execution modes, and visibility. This is the control plane for the skill.

**Complete Schema:**

```markdown
---
# IDENTITY
name: sync-deps
description: "Deterministically audits package manifests and synchronizes lockfiles."
version: "2.1.0"

# VISIBILITY & TRIGGERING
user-invocable: true            # Can the user type /sync-deps?
disable-model-invocation: true  # CRITICAL: If true, the AI CANNOT call this autonomously.
                                # Must be false for general tools, true for dangerous ops.

# EXECUTION CONTEXT
allowed-tools:                  # Whitelist of tools accessible inside this skill.
  - bash                        # Required for shell execution.
  - edit_file                   # Required for manifest updates.
  - code_execution              # Required for PTC Orchestration.

# REASONING CONFIGURATION (2026)
ultrathink: true                # Forces the "Extended Reasoning" tier (CoT level 5).
                                # Use for complex dependency resolution or refactoring.

# CONDITIONAL LOADING
paths:                          # Only load this skill if these globs match.
  - "package.json"
  - "pyproject.toml"
---

```

#### **1.3 Embedded Execution (The `!` Operator)**

The 2026 runtime allows for **Pre-Prompt Execution**. You can embed shell commands directly into the Markdown body of the skill using the `!` prefix.

**Behavior:**

1. User/Hook invokes `/sync-deps`.
2. Runtime scans `SKILL.md`.
3. Runtime executes all lines starting with `!`.
4. Runtime injects the *output* of those commands into the prompt context.
5. Only *then* is the LLM invoked.

**Example Body (`SKILL.md`):**

```markdown
# Context Injection
I am preparing to sync dependencies. Here is the current `npm` state:

! npm outdated --json --long || echo '{"error": "No outdated packages"}'

# Instruction
Based on the JSON output above, please use **Programmatic Tool Calling** to update the package file.

```

---

### **2. PROGRAMMATIC TOOL CALLING (PTC)**

> **⚠️ AVAILABILITY WARNING (Feb 2026):**
>
> Programmatic Tool Calling (PTC) is available via **Claude API only** with `advanced-tool-use-2025-11-20` beta header.
>
> **NOT available in Claude Code** CLI or Desktop app as of Feb 2026.
>
> - The `allowed_callers` field is ignored by Claude Code
> - TaskCreate/TaskUpdate cannot be called from code_execution
> - See [GitHub issue #12836](https://github.com/anthropics/claude-code/issues/12836) for feature request status
>
> **Implication**: Tasks must be created via direct TaskCreate tool calls by Claude, not programmatically in loops.

PTC is the "Orchestrator" pattern that decouples logic from context. It allows the AI to utilize the `code_execution` sandbox (Python) to call other tools in a loop, returning only the final result to the chat.

#### **2.1 The `allowed_callers` Whitelist**

For a tool to be callable from within the Python sandbox, it must explicitly opt-in. This security measure prevents the sandbox from executing unauthorized actions.

**Tool Definition (MCP/Plugin Config):**

```json
{
  "name": "fetch_jira_ticket",
  "description": "Retrieves ticket details from the Atlassian API.",
  "input_schema": {
    "type": "object",
    "properties": { "ticket_id": { "type": "string" } }
  },
  "allowed_callers": [
    "code_execution_20250825"  # The specific signed identifier for the 2026 Python Sandbox
  ]
}

```

#### **2.2 The Execution Flow**

Instead of a `tool_use` stop sequence, the model generates a `code_execution` block.

**Model Output (Python Orchestrator):**

```python
# The AI writes this script dynamically
import asyncio

async def audit_and_fix():
    # 1. CALL EXTERNAL TOOL (via PTC)
    # Note: No HTTP overhead for the chat context; happens in backend
    outdated = await get_outdated_packages()

    results = []
    for pkg in outdated:
        if pkg['severity'] == 'high':
            # 2. LOGIC LOOP
            # 3. CALL EXTERNAL TOOL (via PTC)
            await update_package_version(pkg['name'], "latest")
            results.append(f"Updated {pkg['name']}")

    print(f"Orchestration Complete: {', '.join(results)}")

# The Runtime executes this.
# The Context Window receives: "Orchestration Complete: Updated lodash, express"

```

---

### **3. THE EVENT HOOKS SYSTEM**

Hooks provide the "Invisible Hand" of automation. They allow you to intercept lifecycle events in the Claude Code CLI and inject data or block actions.

**Configuration File:** `.claude/settings.json`

#### **3.1 Supported Lifecycle Events**

* `UserPromptSubmit`: Fires before the user's input is sent to the model.
* `PreToolUse`: Fires before a tool is executed (allows blocking/modification).
* `PostToolUse`: Fires after a tool execution completes (allows cleanup/verification).
* `Stop`: Fires when the agent finishes a turn.

#### **3.2 The `PostToolUse` Dependency Automation Pattern**

This is the standard pattern for keeping `package.json` and `node_modules` in sync without user intervention.

**The Hook Configuration:**

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",  # Only fire if the tool was 'Edit' or 'Write'
        "description": "Watch for manifest changes",
        "hooks": [
          {
            "type": "command",
            "command": "bash .claude/hooks/detect_manifest_change.sh"
          }
        ]
      }
    ]
  }
}

```

**The Data Protocol (`stdin`/`stdout`):**

* **Input (`stdin`):** The hook script receives a JSON object containing the tool name, input arguments, and result.
* **Output (`stdout`):** Any text printed to stdout is **injected** into the conversation as a "System Note" for the AI.
* **Control (`exit code`):**
* `0`: Continue normal execution.
* `2`: **Hard Block**. Fail the tool use and force the AI to retry based on the stdout error message.

---

#### **3.5 ExitPlanMode Plan Approval Detection**

The `ExitPlanMode` tool is Claude Code's built-in mechanism for exiting plan mode and requesting user approval. Plugins can detect plan approval via PostToolUse hooks.

**ExitPlanMode Tool Signature:**
- **Tool Name**: `"ExitPlanMode"`
- **Input**: `{ "plan": "<plan-content>" }` (plan text from plan file)
- **Tool Result on Approval**: `"User has approved your plan. You can now start coding..."`
- **Tool Result on Rejection**: User declines via UI → tool may not fire PostToolUse, or returns rejection message

**Hook Detection Pattern for Plan Approval:**
```python
@app.on("PostToolUse")
def detect_plan_approval(ctx: EventContext) -> Optional[Dict]:
    """Detect when user approves a plan via ExitPlanMode tool."""
    # Only handle ExitPlanMode tool
    if ctx.tool_name != "ExitPlanMode":
        return None

    # Check tool result for approval indicators
    tool_result = ctx.tool_result or ""
    if "approved your plan" in tool_result.lower():
        # User approved - activate autorun or other workflows
        ctx.autorun_active = True
        ctx.autorun_task = ctx.plan_arguments or "Execute the accepted plan"
        return ctx.block("Plan approved - activating autonomous execution...")
    return None
```

**Use Case**: clautorun uses this pattern to automatically activate three-stage autorun when users approve plans, eliminating the need for custom "PLAN ACCEPTED" text markers.

**Sources:**
- [ExitPlanMode tool description](https://github.com/Piebald-AI/claude-code-system-prompts/blob/main/system-prompts/tool-description-exitplanmode.md)
- [Bug #9701: Auto-approval issues](https://github.com/anthropics/claude-code/issues/9701)
- [Bug #12288: Empty input field regression](https://github.com/anthropics/claude-code/issues/12288)

---

#### **3.6 TaskCreate/TaskUpdate for Planning Workflows**

Claude Code provides built-in task management tools that persist across conversation turns. These tools are essential for planning workflows to track progress and dependencies.

**TaskCreate Tool** - Create tasks with metadata:
```python
TaskCreate({
  "subject": "[PLANNING] Step 1: Research existing patterns",
  "description": "Search codebase for authentication patterns and conventions",
  "activeForm": "Researching patterns..."  # Shown in spinner while in_progress
})
```

**TaskUpdate Tool** - Set dependencies and track progress:
```python
# Set dependency (Step 2 blocks on Step 1)
TaskUpdate({
  "taskId": "2",
  "addBlockedBy": ["1"]  # Task 2 cannot start until Task 1 completes
})

# Update status as work progresses
TaskUpdate({"taskId": "1", "status": "in_progress"})  # Starting work
TaskUpdate({"taskId": "1", "status": "completed"})    # Finished work
```

**TaskList Tool** - Query current task state:
```python
TaskList()  # Returns array of all tasks with id, subject, status, blockedBy, blocks
```

**Common Pattern**: Tasks with `[PLANNING]` prefix distinguish planning tasks from execution tasks.

**Pre-Prompt Injection Pattern** (enforces task creation):
```markdown
## 0. Pre-Planning Context

**Current TaskList state:**
! `claude --output-format json --print tool_result TaskList 2>/dev/null | head -50`

⚠️ **If TaskList above is empty**: You MUST call TaskCreate for each planning step NOW.
```

**How It Works**:
1. `!` operator executes bash command BEFORE LLM reasoning
2. TaskList output is injected into context
3. Empty TaskList = visible trigger for immediate TaskCreate calls
4. Non-empty TaskList = AI sees existing tasks and can resume/extend them

**Why This Matters**: Planning instructions at conversation start "gradually lose importance as conversation grows" ([source](https://github.com/anthropics/claude-code/issues/7777)). Pre-prompt injection makes TaskList state **immediately visible**, dramatically improving task creation compliance.

**Sources:**
- [Claude Code Todos to Tasks](https://medium.com/@richardhightower/claude-code-todos-to-tasks-5a1b0e351a1c)
- [Slash Commands Documentation](https://docs.anthropic.com/en/docs/claude-code/slash-commands)

---

### **4. THE INTEGRATION PIPELINE ("The Golden Path")**

This section details the explicit, "Explicitly Designed Way" to integrate Hooks, Skills, and PTC into a single automated workflow.

**The Scenario:** A dependency file (`package.json`) is modified.

**Step 1: The Trigger**
The User or the AI uses the `edit_file` tool to change a version number in `package.json`.

**Step 2: The Hook Interception**
The `PostToolUse` hook defined in `settings.json` matches the `Edit` action and executes `.claude/hooks/detect_manifest_change.sh`.

**Step 3: The Script Logic**

```bash
#!/bin/bash
# .claude/hooks/detect_manifest_change.sh

# 1. Read the event payload
read -r JSON_INPUT
FILE_PATH=$(echo "$JSON_INPUT" | jq -r '.input.path')

# 2. Check for manifest file
if [[ "$FILE_PATH" == *"package.json"* ]]; then
  # 3. CONTEXT INJECTION
  # We do NOT run npm install here. We tell the AI to invoke the specialized Skill.
  echo "SYSTEM_ALERT: Critical manifest modification detected."
  echo "PROTOCOL_MANDATE: You must immediately invoke the '/sync-deps' skill to reconcile the lockfile."
fi

```

**Step 4: The Skill Invocation**
The AI receives the "SYSTEM_ALERT" in its context. Following the mandate, it emits the tool call: `tool_use(name="sync-deps")`.

**Step 5: The Skill Execution (PTC)**
The `.claude/skills/sync-deps/SKILL.md` loads.

1. Front Matter `ultrathink: true` enables advanced reasoning.
2. Embedded `! npm outdated` runs and injects fresh data.
3. The AI uses **Programmatic Tool Calling** to write a Python script that runs the install and verifies the build state.

---

### **5. PLUGIN DISTRIBUTION MANIFEST**

To share these capabilities, you package them as a Plugin.

**File:** `.claude-plugin/plugin.json`
**Manifest Version:** `2` (Required for 2026 features)

```json
{
  "name": "dev-ops-automator",
  "version": "4.2.0",
  "manifest_version": 2,
  "description": "Provides full-cycle dependency automation via Hooks and PTC.",
  "permissions": {
    "filesystem": ["read", "write"],
    "tools": ["bash", "code_execution", "edit_file"]
  },
  "skills": [
    {
      "name": "sync-deps",
      "path": "../skills/sync-deps/SKILL.md",
      "description": "Dependency reconciliation skill."
    }
  ],
  "hooks": {
    "PostToolUse": "../hooks/detect_manifest_change.sh"
  }
}

```

**Plugin CLI Commands:**

* `/plugin install <package>`: Install from registry.
* `/plugin marketplace add <url>`: Add custom registry.
* `/plugin enable-auto-update`: **(New in 2.0.70)** Background updates.
* `claude --plugin-dir ./local-plugin`: Dev mode loading.

---

### **6. SESSION CONTROL REFERENCE**

The following typed commands are the **only** recognized methods for terminating the 2026 CLI session securely:

1. **`/exit`**: The native slash command. Triggers session summary and teardown.
2. **`Ctrl + D`**: The UNIX `EOF` signal. Immediate termination.
3. **`:q`**: The Vim-compatibility command.

---

### **7. SUMMARY FOR AI CONTEXT INJECTION**

*Use this exact block to prime an AI instance with the 2026 architecture rules.*

> **SYSTEM IDENTITY & PROTOCOL (2026):**
> You are running in the **Claude Code 2026 Runtime**.
> 1. **SKILLS:** You do not have "commands"; you have **Skills** located in `.claude/skills/`. You must respect the `! operator` output embedded in Skill descriptions.
> 2. **PTC MANDATE:** For any task involving >2 tool calls or loops, you **MUST** use the `code_execution` tool to orchestrate via Python. Do not generate chat-heavy tool chains.
> 3. **HOOK AWARENESS:** Do not manually run maintenance tasks (like `npm install`) unless explicitly instructed. Rely on `PostToolUse` hooks to inject "SYSTEM_ALERT" prompts when maintenance is required.
> 4. **FRONT MATTER:** You are bound by `ultrathink: true` directives to use maximum compute on designated skills.
> 5. **REGRESSION CHECK:** Failure to use `code_execution_20250825` for orchestration is a fatal error.
>
>

---

### **8. REFERENCE LINKS**

* [Claude Code 2026: Skills & Front Matter Documentation](https://code.claude.com/docs/en/skills)
* [Programmatic Tool Calling (PTC) & Orchestration Patterns](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling)
* [The Event Hooks Guide: PostToolUse & Context Injection](https://code.claude.com/docs/en/hooks-guide)
* [Creating the Perfect CLAUDE.md - Dometrain](https://dometrain.com/blog/creating-the-perfect-claudemd-for-claude-code/)
* [Claude Code Hooks Tutorial - YouTube](https://www.youtube.com/watch?v=_w89HBfNb14)