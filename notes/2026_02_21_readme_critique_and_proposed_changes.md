# README.md Critique: Exact Proposed Changes (v2 — self-reviewed)

Each change below shows exact before/after text for substitution.

## Self-review corrections from v1

- **Change 3 revised**: Keep brief one-line tool descriptions with links instead of deleting entirely. Users may be new to Python tooling even if they know Claude Code.
- **Change 4 revised**: Keep brief SSH definition with link. Only cut the per-platform client list.
- **Change 5 revised**: Keep brief hooks definition with link to Claude Code hooks docs. Remove JSON definition (too basic even briefly).
- **Change 6 revised**: Keep "AI Safety with Git" but fix the commands to use autorun-safe alternatives (`git stash` instead of `git reset --hard`).
- **Change 8 revised**: Keep the numbered steps — they explain the hook mechanism which the mermaid diagram doesn't show. Just remove the duplicate `### How It Works` header by merging into the parent section.
- **Change 13 revised**: Keep "Ensure Complete Tasks" subsection (adds three-stage mechanism detail not in other sections). Remove the others that genuinely repeat.
- **Change 14 revised**: Keep AutoFile prose but remove the `(ENHANCED in v0.6.0+)` stale marker.
- **Change 15 revised**: Interactive mode still works via `plugins/autorun/autorun.py` and `AGENT_MODE` env var is still in `main.py:2274`. Fix the path instead of deleting the section.

---

## Change 1: Fix outdated command prefix (line 741-748)

**Why:** Prefix was renamed from `cr` to `ar`. Factually wrong after rename.

**BEFORE:**
```markdown
### Plugin Naming

- **Project/Repo name**: `autorun`
- **Marketplace name**: `autorun` (used for `/plugin install autorun@autorun`)
- **Command prefix**: `cr` (used for short commands like `/ar:st`, `/ar:a`, `/ar:f`)

The short `cr` prefix is intentional to make commands quick to type while the full name `autorun` is used for the project, repository, and marketplace identification.
```

**AFTER:**
```markdown
### Plugin Naming

- **Project/Repo name**: `autorun`
- **Marketplace name**: `autorun` (used for `/plugin install autorun@autorun`)
- **Command prefix**: `ar` (used for short commands like `/ar:st`, `/ar:a`, `/ar:f`)
```

---

## Change 2: Fix "Install all 3 plugins" → 2 plugins (line 76)

**Why:** The marketplace has 2 plugins (autorun, pdf-extractor), not 3. Line 67 itself says "2 plugins" — the comment on line 76 contradicts it.

**BEFORE:**
```
# Install all 3 plugins from GitHub
```

**AFTER:**
```
# Install plugins from GitHub
```

---

## Change 3: Trim "What is pytest/venv/UV" to one-liners with links (lines 379-386)

**Why:** Full paragraph definitions are excessive, but one-line descriptions with links help users new to Python tooling.

**BEFORE:**
```markdown
**What is pytest?**
pytest is a popular testing framework for Python that makes it easy to write simple and scalable tests. It automatically discovers test files and functions, provides detailed output, and supports powerful fixtures and plugins.

**What are Virtual Environments?**
Virtual environments are isolated Python environments that keep project dependencies separate. Think of them as clean rooms for each project - they prevent different projects from conflicting with each other's requirements.

**What is UV?**
UV is a modern, extremely fast Python package manager and virtual environment manager. It's like `pip` + `venv` but 10-100x faster with better dependency resolution.

**Quick Core Tests:**
```

**AFTER:**
```markdown
> [pytest](https://docs.pytest.org/) runs the tests, [UV](https://docs.astral.sh/uv/) manages Python dependencies (fast alternative to pip+venv).

**Quick Core Tests:**
```

---

## Change 4: Trim SSH section to brief description + Mosh (lines 474-489)

**Why:** Brief SSH description with link is appropriate. Per-platform client list (Windows/macOS/Linux/iOS/Android with 3-4 apps each) is out of scope.

**BEFORE:**
```markdown
**What is SSH?** SSH (Secure Shell) is a secure network protocol that lets you securely access and control your computer from anywhere in the world.

**SSH Clients for Different Devices:**

**Enhanced SSH Experience (Recommended):**
- **Mosh (Mobile Shell)**: [mosh.org](https://mosh.org/) - A mobile SSH client that handles network interruptions gracefully
  - **Why Mosh?** Keeps your connection alive even when switching networks (WiFi → 4G → WiFi), works with poor connections, provides intelligent local echo for reduced lag, and automatically resumes where you left off after reconnection
  - **Installation**: `brew install mosh` (macOS), `sudo apt install mosh` (Ubuntu/Debian)
  - **Usage**: `mosh username@your-server-address` instead of `ssh username@your-server-address`

**Traditional SSH Clients:**
- **Windows**: [Windows Terminal](https://learn.microsoft.com/en-us/windows/terminal/) (built-in, modern), [VS Code Terminal](https://code.visualstudio.com/) (built-in to VS Code), [Fluent Terminal](https://github.com/felixse/FluentTerminal) (free), [Hyper](https://hyper.is/) (modern, extensible)
- **macOS**: [iTerm2](https://iterm2.com/) (recommended, powerful), [VS Code Terminal](https://code.visualstudio.com/) (built-in to VS Code), or built-in Terminal app
- **Linux**: Most terminal emulators work well (gnome-terminal, konsole, etc.), [VS Code Terminal](https://code.visualstudio.com/) (built-in to VS Code)
- **iOS**: [Terminus](https://www.termius.com/mobile) (supports Mosh), [Prompt](https://panic.com/prompt/) (supports Mosh), or [Blink Shell](https://blink.sh/) (supports Mosh)
- **Android**: [Termius](https://www.termius.com/mobile) (supports Mosh), [JuiceSSH](https://juicessh.com/), or [ConnectBot](https://github.com/connectbot/connectbot)
```

**AFTER:**
```markdown
**Remote Access**: Use [SSH](https://www.openssh.com/) to connect to byobu sessions from any device. For unreliable or mobile connections, [Mosh](https://mosh.org/) handles network interruptions gracefully (auto-reconnects, works across WiFi/cellular switches).

- **Mosh install**: `brew install mosh` (macOS), `sudo apt install mosh` (Linux)
- **Usage**: `mosh user@server` then `byobu-attach autorun-work`
```

---

## Change 5: Trim "What are Hooks?" to one-liner, remove "What is JSON?" (lines 1296-1301)

**Why:** Brief hooks definition with link is appropriate since hooks are central to autorun. JSON definition is too basic for anyone reading hook integration docs.

**BEFORE:**
```markdown
**What are Hooks?**
Hooks are automated scripts that run at specific points during program execution. Think of them as custom triggers that let you extend or modify how a program works. In autorun, hooks intercept commands before they reach Claude Code, enabling file policy enforcement and command processing.

**What is JSON?**
JSON (JavaScript Object Notation) is a lightweight data format that's easy for humans to read and write, and easy for computers to parse and generate. It's commonly used for configuration files and data exchange between programs.

**Setup:**
```

**AFTER:**
```markdown
Hooks are scripts triggered at specific points during execution — autorun uses them to intercept commands for policy enforcement. See [Claude Code Hooks docs](https://docs.claude.com/en/docs/claude-code/hooks).

**Setup:**
```

---

## Change 6: Fix "AI Safety with Git" to use autorun-safe commands (lines 714-724)

**Why:** This section recommends `git reset --hard` and `git checkout --` — both of which autorun's own safety guards block. Contradictory. Replace with safe alternatives. Keep section (useful for contributors) but fix the commands.

**BEFORE:**
```markdown
**Git for Contributors:**
Git provides complete version control for collaborative development. Essential commands:
- `git diff` - Review your changes before committing
- `git add . && git commit -m "Description"` - Commit your changes
- `git push origin feature-branch` - Share your changes for review

**AI Safety with Git:**
- **Instant rollback**: `git reset --hard HEAD~1` undoes all AI changes instantly
- **Selective revert**: `git checkout -- filename` restores specific files
- **Safe experimentation**: Test AI suggestions knowing you can revert completely
- **Change visibility**: See exactly what AI modified before committing
```

**AFTER:**
```markdown
**AI Safety with Git:**
- **Undo last commit**: `git reset --soft HEAD~1` undoes commit, keeps changes staged
- **Stash changes**: `git stash` temporarily shelves changes, `git stash pop` restores
- **Restore a file**: `git restore filename` reverts specific file to last commit
- **Change visibility**: `git diff` shows exactly what was modified before committing
```

---

## Change 7: Remove "Plugin Implementation Approaches - Research Findings" (lines 1719-1845)

**Why:** Resolved design research from early development. Discusses whether to use markdown commands vs executable scripts vs symlinks, includes ❌/✅/⚠️ status markers, and lists 5 "Possible Solutions" for a problem already solved. Not relevant to users or current contributors — the implementation approach is settled.

**BEFORE:**
```markdown
#### Plugin Implementation Approaches - Research Findings

**Official Plugin Pattern** (Sources: [Agent SDK Overview]...):
...
[126 lines through end of "#### Possible Solutions"]
...
5. **Pure Markdown** (Simplest):
   - Convert to pure prompts without executable code
   - Example: `/afs` becomes a markdown prompt explaining strict-search policy
   - Loses programmatic state management
```

**AFTER:**
(Delete entire section from `#### Plugin Implementation Approaches - Research Findings` through `- Loses programmatic state management`)

---

## Change 8: Merge duplicate "How It Works" into parent section (line 573)

**Why:** `## How It Works` at line 544 and `### How It Works` at line 573 creates a confusing duplicate header. The numbered steps at 573-580 add useful hook mechanism detail, but the header is redundant.

**BEFORE:**
```markdown
**Emergency Stop**: At any point, `/ar:sos` outputs `AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP` and immediately halts.

### How It Works

1. User sends `/ar:go <task description>` (or legacy `/autorun`)
2. UserPromptSubmit hook activates session state with three-stage tracking
3. AI works autonomously through each stage
4. At each stage boundary, the system validates completion markers
5. Only after all three stages complete does the session end
6. Emergency stop (`/ar:sos`) immediately halts at any point

### Safety Mechanisms
```

**AFTER:**
```markdown
**Emergency Stop**: At any point, `/ar:sos` outputs `AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP` and immediately halts.

**Hook mechanism**: User sends `/ar:go <task>` → UserPromptSubmit hook activates three-stage tracking → AI works autonomously → system validates completion markers at each stage boundary → session ends only after all three stages complete.

### Safety Mechanisms
```

---

## Change 9: Remove expected pytest output block (lines 403-410)

**Why:** Sample pytest output is filler — every developer has seen `PASSED` lines.

**BEFORE:**
```markdown
**Expected output:**
```
============================= test session starts ==============================

tests/test_unit_simple.py::TestConfiguration::test_completion_marker PASSED
tests/test_unit_simple.py::TestConfiguration::test_emergency_stop_phrase PASSED
...
```

**Full Test Suite with Coverage:**
```

**AFTER:**
```markdown
**Full Test Suite with Coverage:**
```

---

## Change 10: Replace stale test counts with directory reference (lines 1007-1074)

**Why:** Test counts are already stale — says "48 tests" and "12 pipe context tests" but we just added 18 more pipe context tests (now 30). Listing individual test names in README creates maintenance burden. Keep the run command (useful), drop the counts and per-test listings.

**BEFORE:**
```markdown
**Test Coverage:**

Comprehensive test suite with 48 tests across 5 test files:

```bash
# Run all task lifecycle + command blocking tests
uv run pytest plugins/autorun/tests/test_task_lifecycle_*.py plugins/autorun/tests/test_pipe_context_blocking.py -v

# Run specific test suites
uv run pytest plugins/autorun/tests/test_task_lifecycle_basic.py          # 8 basic tests
uv run pytest plugins/autorun/tests/test_task_lifecycle_integration.py    # 10 integration tests
uv run pytest plugins/autorun/tests/test_task_lifecycle_failure_modes.py  # 8 failure mode tests
uv run pytest plugins/autorun/tests/test_task_lifecycle_edge_cases.py     # 10 edge case tests
uv run pytest plugins/autorun/tests/test_pipe_context_blocking.py         # 12 pipe context tests
```

**Test Categories:**

1. **Basic Tests** (8 tests) - `test_task_lifecycle_basic.py`
   [... 4 bullets ...]

2. **Integration Tests** (10 tests) - `test_task_lifecycle_integration.py`
   [... 5 bullets ...]

3. **Failure Mode Tests** (8 tests) - `test_task_lifecycle_failure_modes.py`
   [... 8 bullets ...]

4. **Edge Case Tests** (10 tests) - `test_task_lifecycle_edge_cases.py`
   [... 10 bullets ...]

5. **Pipe Context Blocking Tests** (12 tests) - `test_pipe_context_blocking.py`
   [... 6 bullets ...]

**All 48 tests pass with 100% success rate**, verifying:
- ✅ PRIMARY GOAL: AI continuation enforcement (stop hook blocks incomplete tasks)
- ✅ Context-aware command blocking (pipes allowed, direct file operations blocked)
- ✅ Thread safety (concurrent access, atomic operations)
- ✅ Failure resilience (format changes, corruption recovery, pruning)
- ✅ Edge case handling (boundary conditions, unusual inputs)
- ✅ DRY patterns (reuses session_state(), no code duplication)
```

**AFTER:**
```markdown
**Tests:**

```bash
# Run task lifecycle + command blocking tests
uv run pytest plugins/autorun/tests/test_task_lifecycle_*.py plugins/autorun/tests/test_pipe_context_blocking.py -v

# Run specific suites
uv run pytest plugins/autorun/tests/test_task_lifecycle_basic.py
uv run pytest plugins/autorun/tests/test_task_lifecycle_integration.py
uv run pytest plugins/autorun/tests/test_task_lifecycle_failure_modes.py
uv run pytest plugins/autorun/tests/test_task_lifecycle_edge_cases.py
uv run pytest plugins/autorun/tests/test_pipe_context_blocking.py
```

Test suites cover: basic operations, integration flows (stop hook blocking, resume detection), failure modes (task explosion, escape hatch, race conditions), edge cases (circular deps, special chars), and pipe context blocking (heredoc detection, stdin vs file reads). See `plugins/autorun/tests/` for details.
```

---

## Change 11: Remove "NEW v0.6.0" marker (line 684)

**Why:** v0.6.0 features are not "new" — they've been in the codebase for multiple versions.

**BEFORE:**
```markdown
- **NEW v0.6.0:** `/ar:no`, `/ar:ok`, `/ar:clear`, `/ar:globalno`, `/ar:globalok`, `/ar:globalstatus` (Command Blocking)
```

**AFTER:**
```markdown
- Command blocking: `/ar:no`, `/ar:ok`, `/ar:clear`, `/ar:globalno`, `/ar:globalok`, `/ar:globalstatus`
```

---

## Change 12: Remove "Custom Command Workflows" after License (lines 2123-2151)

**Why:** Content after License is unexpected placement. References `$ARGUMENTS` and `/autorun myworkflow` patterns from an older architecture. If this content is still relevant, it should be moved before the License section.

**BEFORE:**
```markdown
## License

Apache License 2.0 - see [LICENSE](LICENSE) file for details.

### Custom Command Workflows

You can extend autorun with your own command workflows by creating markdown files:

#### Creating Custom Workflows
1. Create a markdown file in `~/.claude/commands/` (e.g., `myworkflow.md`)
2. Include `$ARGUMENTS` placeholder in the file to receive arguments
3. Title must include `(/autorun: Your Methodology Name)`
4. Use `/autorun myworkflow` to execute with your custom methodology

#### Argument Handling
[... 15 more lines ...]
```

**AFTER:**
```markdown
## License

Apache License 2.0 - see [LICENSE](LICENSE) file for details.
```

---

## Change 13: Trim "What autorun Does For You" redundant subsections (lines 280-345)

**Why:** Lines 259-279 ("Reduce User Interruptions" + "File Creation Control") already explain the core value. Lines 280-345 repeat the same points:
- "Reduce Manual Interventions" repeats "Reduce User Interruptions" with hook implementation details
- "Prevent File Clutter" repeats "File Creation Control" with hook implementation details
- "Survive Crashes" and "Work From Anywhere" repeat tmux section content
- "Capabilities Matrix" + "Measurable Technical Benefits" repeat everything in table/list format

Keep "Ensure Complete Tasks" (adds three-stage mechanism detail not covered elsewhere). Remove the others.

**BEFORE:**
```markdown
### Reduce Manual Interventions (autorun feature)
- **Current Behavior**: Claude Code stops and waits for manually typing continue
- **autorun Action**: Hook system intercepts Claude Code stop events and automatically re-injects continuation prompts
- **Mechanism**: UserPromptSubmit and Stop hooks detect when Claude stops working, analyze the transcript for completion markers, and inject "continue working" prompts when tasks are incomplete
- **Benefit**: Start autonomous tasks and return to completed work with fewer interruptions

### Prevent File Clutter (autorun feature)
- **Current Behavior**: AI creates multiple experimental files during development
- **autorun Action**: PreToolUse hooks intercept Write tool calls and enforce file creation policies
- **Mechanism**: Before each file creation, the hook scans the conversation transcript for policy compliance. It blocks or allows file operations based on the current policy level (`/afs`, `/afj`, `/afa`) and any required justifications
- **Policy Levels**:
  1. Strict search - Hook blocks all new file creation, forcing AI to modify existing files found through search
  2. Justified creation - Hook allows new files only when AI includes required justification tags
  3. Allow all - Hook allows all file creation operations
- **Benefit**: Maintain clean project directories with only essential files

### Ensure Complete Tasks (autorun feature)
- **Current Behavior**: AI may claim task completion after implementing only partial requirements
- **autorun Action**: Hook system implements three-stage verification by detecting completion markers and re-injecting the original task
- **Mechanism**: When AI outputs a completion marker, the hook detects this first completion and re-injects the original task with a verification checklist. Only after a second completion marker does the system allow the session to end
- **Benefit**: Reduce incomplete features and ensure all requirements are implemented

### Survive Crashes and Disconnections (tmux/byobu feature)
- **Current Behavior**: Application crashes or network drops terminate work sessions
- **tmux/byobu Action**: Maintains session state across interruptions using terminal multiplexing
- **How it Works**: Terminal multiplexer keeps processes running on the server regardless of client connectivity
- **Benefit**: Resume work from the exact interruption point after reconnection
- **Note**: autorun integrates with tmux/byobu but does not provide session persistence itself

### Work From Anywhere (tmux/byobu + SSH/Mosh feature)
- **Current Behavior**: Users must stay at their workstation to monitor and intervene in AI sessions
- **SSH/Mosh + tmux Action**: Enables remote session monitoring and intervention from any device
- **How it Works**: SSH/Mosh clients connect to the tmux session through network connections
- **Benefit**: Monitor and control AI work from any location with internet access
- **Note**: autorun provides commands to work within tmux sessions but does not provide remote access

### Concrete Capabilities Matrix

**tmux/byobu Base Capabilities:**
- **Session Persistence**: Processes continue running even when client disconnects
- **Session Isolation**: Multiple independent sessions can run simultaneously
- **Remote Access**: SSH/Mosh provides secure remote access from any device
- **Multiplexing**: Split terminal windows for simultaneous viewing
- **Process Recovery**: Automatic session recovery after system restart

**autorun Enhanced Capabilities:**
- **Automatic Continuation**: Keeps Claude working without manually typing continue
- **File Policy Enforcement**: Three-tier system to prevent file clutter
- **Three-Stage Verification**: Helps ensure tasks are complete
- **Session State Management**: Robust state isolation and recovery
- **Targeted Session Safety**: Commands never affect current Claude Code session

### Measurable Technical Benefits

**Before autorun + tmux/byobu:**
- AI work lost when terminal closes
- Manual intervention required for session interruptions
- No file creation control during autonomous workflows
- No verification that tasks are actually complete

**With autorun + tmux/byobu:**
- Reduced data loss during crashes or disconnections
- Decreased need for manual intervention
- File creation policies to reduce unnecessary files
- Three-stage verification to help ensure task completion

### Testing
```

**AFTER:**
```markdown
### Ensure Complete Tasks (autorun feature)
- **Current Behavior**: AI may claim task completion after implementing only partial requirements
- **autorun Action**: Hook system implements three-stage verification by detecting completion markers and re-injecting the original task
- **Mechanism**: When AI outputs a completion marker, the hook detects this first completion and re-injects the original task with a verification checklist. Only after a second completion marker does the system allow the session to end
- **Benefit**: Reduce incomplete features and ensure all requirements are implemented

### Session Persistence (tmux/byobu feature)
- [tmux](https://github.com/tmux/tmux)/[byobu](https://www.byobu.org/) maintains session state across terminal closures, network drops, and system reboots
- autorun integrates with tmux sessions via `/ar:tm` but does not provide session persistence itself
- Access sessions remotely via SSH/[Mosh](https://mosh.org/) from any device

### Testing
```

---

## Change 14: Remove stale "(ENHANCED in v0.6.0+)" marker (line 800)

**Why:** Stale version marker. Keep the AutoFile prose section (adds justification tag detail not in table).

**BEFORE:**
```markdown
### Command Blocking Commands (ENHANCED in v0.6.0+)
```

**AFTER:**
```markdown
### Command Blocking Commands
```

---

## Change 15: Fix Interactive Mode path (lines 1327-1354)

**Why:** The path `python autorun.py` (root) doesn't exist. The file is at `plugins/autorun/autorun.py`. The `AGENT_MODE` env var is still functional in `main.py:2274`. Fix path, keep section.

**BEFORE:**
```markdown
**Setup:**
```bash
# Navigate to autorun directory
cd /path/to/autorun

# Activate virtual environment
source .venv/bin/activate

# Run interactive mode
AGENT_MODE=SDK_ONLY python autorun.py
```
```

**AFTER:**
```markdown
**Setup:**
```bash
# Run interactive mode from the plugin directory
cd plugins/autorun
AGENT_MODE=SDK_ONLY uv run python autorun.py
```
```

---

## Change 16: Remove duplicate "Command Reference" / "Interactive Mode Commands" (lines 1356-1360)

**Why:** Orphaned section heading with only 3 lines about `quit/exit/q`. This belongs in the Interactive Mode section if anywhere, not as a standalone `## Command Reference`.

**BEFORE:**
```markdown
## Command Reference

### Interactive Mode Commands
- `quit`, `exit`, `q` - Exit the application
- Ctrl+C - Interrupt, Ctrl+C twice - Exit
- Ctrl+D - Exit immediately
```

**AFTER:**
(Merge into Interactive Mode section above, or delete if Interactive Mode section already covers exit commands)

---

## Summary of all changes

| # | Lines | Action | Why |
|---|-------|--------|-----|
| 1 | 741-748 | Fix `cr` → `ar` prefix | Factually wrong after rename |
| 2 | 76 | Fix "3 plugins" → drop count | Wrong count, contradicts line 67 |
| 3 | 379-386 | Trim 3 paragraphs → 1-line with links | Reduce verbosity, keep discoverability |
| 4 | 474-489 | Trim SSH section, keep Mosh + link | Drop per-platform client list |
| 5 | 1296-1301 | Trim hooks def to 1-line with link, drop JSON def | Keep useful context, drop obvious |
| 6 | 714-724 | Fix git commands to use safe alternatives | Current text recommends blocked commands |
| 7 | 1719-1845 | Remove 126-line research findings | Resolved dev scratchpad |
| 8 | 573-580 | Merge into parent section, drop duplicate header | Reduce confusion, keep content |
| 9 | 403-410 | Remove sample pytest output | Filler |
| 10 | 1007-1074 | Replace 67-line test listing with commands + summary | Counts are stale, test files are source of truth |
| 11 | 684 | Remove "NEW v0.6.0" marker | No longer new |
| 12 | 2123-2151 | Remove post-License orphaned section | Wrong placement, outdated patterns |
| 13 | 280-345 | Keep "Ensure Complete Tasks" + condensed tmux, remove 4 redundant subsections | Reduces repetition while keeping unique mechanism detail |
| 14 | 800 | Remove "(ENHANCED in v0.6.0+)" | Stale version marker |
| 15 | 1340 | Fix `python autorun.py` → `uv run python autorun.py` in `plugins/autorun/` | Wrong path — root `autorun.py` doesn't exist |
| 16 | 1356-1360 | Merge or delete orphaned Command Reference | Orphaned section heading |

Estimated reduction: ~300 lines removed, essential information preserved or improved.
