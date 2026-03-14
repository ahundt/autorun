# Autorun Architecture & Workflow

This diagram illustrates the technical architecture of autorun, showing how user-configurable Markdown files drive the safety policies and command execution.

```mermaid
flowchart TD
    %% THEME: Technical, Accurate, Complete
    classDef file fill:#fffbe6,stroke:#faad14,stroke-width:1px,color:#614700,font-family:monospace
    classDef cmd fill:#e6f7ff,stroke:#1890ff,stroke-width:2px,color:#002766,font-weight:bold
    classDef state fill:#f6ffed,stroke:#52c41a,stroke-width:2px,color:#135200
    classDef block fill:#fff1f0,stroke:#ff4d4f,stroke-width:2px,color:#5c0011
    classDef panic fill:#000,stroke:#ff0000,stroke-width:2px,color:#fff,font-weight:bold

    %% --- LAYER 1: CONFIGURATION (MD FILES) ---
    subgraph Config [🛠️ CONFIGURATION LAYER]
        direction TB
        Note_MD[User Customization:<br/>Create/Remove .md files in commands/<br/>to add capabilities or adjust policies]:::file

        Files_Pol[commands/f.md<br/>commands/a.md]:::file
        Files_Cmd[commands/no.md<br/>commands/go.md]:::file

        Note_MD --- Files_Pol & Files_Cmd
    end

    %% --- LAYER 2: ACTIVE SAFETY STATE ---
    subgraph Safety [🛡️ ACTIVE SAFETY STATE]
        direction TB

        subgraph File_Pol [File Policy - Mutually Exclusive]
            P_Find(State: SEARCH<br/>Cmd: /ar:f / /ar:find<br/>Modify existing only):::state
            P_Just(State: JUSTIFY<br/>Cmd: /ar:j / /ar:justify<br/>Explain changes):::state
            P_Allow(State: ALLOW<br/>Cmd: /ar:a / /ar:allow<br/>Create freely):::state
        end

        subgraph Cmd_Block [Command Guard - Block & Redirect]
            Block_Cmd("/ar:no <pattern> [desc]<br/>Stops execution"):::block
            Redirect("Auto-Redirects:<br/>rm -> trash<br/>git reset -> git stash"):::block
            Allow_Cmd("/ar:ok <pattern><br/>Override a block"):::state

            Block_Cmd ~~~ Redirect
        end
    end

    %% --- LAYER 3: WORKFLOW ---
    subgraph Workflow [🚀 ACTION WORKFLOW]
        direction TB

        subgraph Plan_Phase [1. Planning]
            W_PN(/ar:pn 'task'<br/>-> plannew.md):::cmd
            W_PR(/ar:pr 'feedback'<br/>-> planrefine.md):::cmd
            W_Export[[Auto-Export to notes/]]:::file

            W_PN --> W_PR
            W_PR --> W_Export
        end

        subgraph Exec_Phase [2. Execution]
            W_PP(/ar:pp / /ar:go<br/>-> planprocess.md):::cmd
            AI_Loop(AI Implementation Loop):::state

            W_PP --> AI_Loop
        end
    end

    %% --- CRITICAL STOP ---
    Stop(⛔ /ar:estop<br/>Emergency Stop):::panic

    %% --- CONNECTIONS ---
    Files_Pol -.-> File_Pol
    Files_Cmd -.-> Cmd_Block

    File_Pol ===> AI_Loop
    Cmd_Block ===> AI_Loop

    W_Export --> W_PP

    %% Style adjustments
    linkStyle default stroke:#666,stroke-width:1px
    style Config fill:#fff,stroke:#ccc,stroke-dasharray: 5 5
    style Safety fill:#fff,stroke:#ccc
    style Workflow fill:#fff,stroke:#ccc
```

## 🛠️ Layer 1: Configuration (The Customizable Core)
Autorun is driven by Markdown files located in the `commands/` directory.
- **You are in control**: You can add, remove, or modify these `.md` files to change how the agent behaves.
- **Example**: `commands/no.md` defines the logic for the `/ar:no` command. Removing this file would remove that capability.

## 🛡️ Layer 2: Active Safety State
This layer enforces constraints on every action the AI takes.

### File Policy (Mutually Exclusive)
Controls *if* and *how* the AI can create or modify files.
- **SEARCH (`/ar:f`)**: **Strictest.** The AI can only modify *existing* files found via search tools. No new files allowed.
- **JUSTIFY (`/ar:j`)**: The AI must provide a reasoning (justification) before creating any new file.
- **ALLOW (`/ar:a`)**: **Permissive.** The AI can create files freely. Best for new projects.

### Command Guard (Block & Redirect)
The system actively monitors for dangerous commands.
- **Auto-Redirection**: Dangerous commands are automatically redirected to safe alternatives.
    - `rm` -> **`trash`** (Move to trash instead of delete)
    - `git reset` -> **`git stash`** (Save work instead of losing it)
- **`/ar:no <pattern> [desc]`**: Manually block specific patterns.
- **`/ar:ok <pattern>`**: Override a specific block or redirection for this session.

## 🚀 Layer 3: Action Workflow
Once safety is configured, the AI operates in two main phases:

### 1. Planning
- **`/ar:pn` (Plan New)**: Reads `plannew.md` to guide the AI in creating a structured plan.
- **`/ar:pr` (Plan Refine)**: Reads `planrefine.md` to help you iteratively improve the plan.
- **Auto-Export**: The approved plan is automatically saved to the `notes/` directory.

### 2. Execution
- **`/ar:pp` (Plan Process)**: Reads `planprocess.md` to execute the saved plan step-by-step.
- **`/ar:go`**: A faster, direct execution command for simpler tasks.
- **AI Implementation Loop**: The agent writes code, runs tests, and fixes errors, all while being checked against the **Active Safety State** from Layer 2.

## ⛔ Emergency Stop
- **`/ar:estop`**: Immediately halts the entire process at any time.
