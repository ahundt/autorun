# Audit: clautorun Capabilities vs README.md + Plan Export Regression (v0.8.0)

## User Messages (Quoted, Numbered)

> **Message 1**: "ok now check the README.md and cross reference against the actual code and list out all of the capabilities and how to test them directly yourself as a claude code session"

> **Message 2** (via /cr:plannew): "ok now check the README.md and cross reference against the actual code and list out all of the capabilities and how to test them directly yourself as a claude code session, note, for example i think there might be some step 1 / 2 / 3 strings that are incorrect in the README.md as you can see vs the plannew strings you should be able to see now, also we need you to enumerate all of the capabilities"

> **Message 3**: "investigate yourself, also you should search the notes/ folder for prior assessments and attempts to fix the bug and cross reference against the actual code bc things have changed over time and there are bugs in claude code itself where exit plan mode calls don't seem to come, and we have made plans for workarounds and proxy calls and state we can store as workarounds while retaining the real pathway just in case it is bugfixed and you also need to be mindful of the multiprocess multithreaded multi session multi cli tool (gemini cli and claude code cli) way that clautorun runs so as not to introduce new regressions or access the wrong plan file"

> **Message 4** (via /cr:planrefine): "you need to keep the plan intact and expand your plan and analysis you need to ensure the tests cover all of that functinality, and also expand the current plan to directly try it all out yourself, also i suspect there is a regression in the plan export yet again where when i accept the plan it won't correctly be copied to the notes folder as it is supposed to be, also what about copying the rejected plans, and you need to for each feature do a manual test for example for rm make a file in /tmp/ and try doing rm on it and so on for each capabilitiy, and you will need to maintain a notes file of your findings and action items as you execute and that needs to be written as part of your plan, and all my messages to you need to be quoted directly in a numbered list in the plan too"

> **Message 5**: "one detail to know is that when clautorun is bootstrapping it forks off a nohup durable process to do the self install so the timeout shouldn't matter for that pathway, which i think you mentioned is a concern, we already should have code to work around that one time case"

> **Message 6**: "timeout 10 is likely too short yes that's probably a real concern, did you read the notes/*api*.md files in this session? should provide good api reference info if needed"

> **Message 7**: "don't lose my messages make sure you have them all! it looks like you just deleted one accidentally **Message 4-duplicate-placeholder**"
> *(Clarification: the deleted entry was an exact duplicate of Message 4's text, accidentally created during editing. No unique message content was lost.)*

> **Message 8**: "is there any other context information or key resource files or paths or links or other details you are missing that a completely cleared context would need or find useful to avoid re-searching? include all necessary info for execution directly in the plan now"

> **Message 9**: "continue"

> **Message 10**: "do micro edits to the plan to expand the plan one massive change risks data loss"

> **Message 11**: "continue with updating the plan with the necessary reference info"

> **Message 12**: "you need to have numbered lists for the execution tasks and you need to update the plan to make sure all the execution tasks are correctly defined and described both the manual confirmation and the automated"

> **Message 13**: "you also need to ensure the tasks are accurate and completely described all the execution tasks need to be laid out properly and individually"

> **Message 14**: "is there any other context information or key resource files or paths or links or other details you are missing that a completely cleared context would need or find useful to avoid re-searching? include all necessary info for execution directly in the plan now"

> **Message 15**: "you must continue refining the plan carefully check the plan for safety all steps must be done in a tmp folder you can initialize a temp git repo there you must ensure the whole plan keeps all real data safe, you must also clearly delineate in writing in the plan the manual tests you will do and the automatic tests you will do there is a lot of ambiguity here everything must be manually tested and any manual test failures need steps for fixing and adding automated tests to prevent the bug from reoccuring and iteratively redoing the manual tests and updates until everything succeeds, futhermore every manual and automated stage must ensure there are robust automated tests in place both direct tests and e2e tests and each step needs to be validated and cross referenced against the README.md and the readme needs to be brought up to date, and there needs to be ongoing notes taken about the process in the notes/ folder throughout excution including the manual tests that were run and the outcomes so it is a fully reproducible process and verifiable work and the bugs can be understood and cross referenced later, choose the name for that notes file now"

> **Message 16**: "careul if you are putting line numbers they need to be line ranges with also semantic reference info including filename and function / class name and line ranges because remember part of this plan is editing the code so line info must be present but must also have the context necessary if/when it becomes outdates, and remember to includ my previous message and this message in the plan file directly"

> **Message 17**: "you need to think through the workflow here how will updates to clautorun be handled you are claude so when there are updates and fixes you may be best served by runn and restart another claude in the tmp dir via tmux and give it the directions of what it needs to do and restart / update it, (alternatively, i can restart you as needed but that is more manual) read @/Users/athundt/.claude/clautorun/plugins/clautorun/skills/tmux-automation/SKILL.md for @/Users/athundt/.claude/clautorun/plugins/clautorun/skills/tmux-automation/SKILL.md also it appears there may be a bug in the installer where skills aren't being installed properly as right now that skill file i just provided the path to doesn't appear when i type /tmux-automation or /cr:tmux-automation"

> **Message 18**: "first immediately copy the current plan into notes/ with an appropriately dated and formatted name and description md and put yourself back in plan mode"
> ✅ **COMPLETED**: Plan copied to `notes/2026_02_18_2130_capability_audit_and_plan_export_regression_investigation.md`

> **Message 19**: "remember you are doing micro edits to correct and refine the existing plan, and also mmake sure the separate claude is using the haiku model"

> **Message 20**: "remember the lifecycle and cleanup of the tmux sessions / windows too and the transition between numbered tests"

> **Message 21**: "oh leave the tmp dir in place for analysis i just said remove the tmux sessions"

---

## ADDITIONAL BUG FOUND: Skills Not Installing/Registering

**Bug**: Skill exists at `plugins/clautorun/skills/tmux-automation/SKILL.md` but `/tmux-automation` and `/cr:tmux-automation` don't work.

**Root cause identified**:
- Directory name: `tmux-automation`
- Skill name in SKILL.md frontmatter: `automated-cli-testing-sessions`
- User expected: `/tmux-automation` or `/cr:tmux-automation`
- Actual working command: `/automated-cli-testing-sessions` or `/cr:automated-cli-testing-sessions`

**Mismatch**: Directory name != skill name in frontmatter

**Investigation needed**:
1. How does Claude Code skill discovery work? (Does it use directory name or frontmatter name?)
2. Check `plugin.json:21` — `"skills": "./skills/"` — does this enable auto-discovery?
3. Check if other skills have same mismatch (claude-session-tools, mermaid-diagrams, clautorun-maintainer)
4. Test if renaming frontmatter `name:` to match directory fixes it
5. Or if symlinking SKILL.md with matching name fixes it

**Skills found**:
- `claude-session-tools/SKILL.md` — frontmatter name: TBD (need to read)
- `clautorun-maintainer/SKILL.md` — frontmatter name: TBD
- `mermaid-diagrams/SKILL.md` — frontmatter name: TBD
- `tmux-automation/SKILL.md` — frontmatter name: `automated-cli-testing-sessions` (MISMATCH)

---

## Context

Cross-reference of README.md documentation against actual implementation in
`plugins/clautorun/src/clautorun/`. Prompted by user observation that stage
marker strings in README.md appear incorrect. Goal: enumerate all capabilities,
provide exact test commands, run live manual tests for each capability, investigate
the plan export regression (plans not copying to notes/ on acceptance and rejected
plans not copying), and maintain a notes file of findings and action items.

**Multi-process/multi-session/multi-CLI constraint:** clautorun runs as a shared
daemon serving concurrent Claude Code and Gemini CLI sessions simultaneously.
All fixes must be safe across processes and CLIs. Never use naive "most recent
file" approaches; always use session-scoped state via `GLOBAL_SESSION_ID` shelve.

---

## CRITICAL BUG 1: Stage Marker Strings in README.md are WRONG

README.md documents **short/simple** stage markers that **do not match** the
actual strings in `config.py` and all command files (`plannew.md`, `go.md`).

| Stage | README.md (WRONG) | config.py / plannew.md / go.md (CORRECT) |
|-------|-------------------|------------------------------------------|
| Stage 1 | `AUTORUN_STAGE1_COMPLETE` | `AUTORUN_INITIAL_TASKS_COMPLETED` |
| Stage 2 | `AUTORUN_STAGE2_COMPLETE` | `CRITICALLY_EVALUATING_PREVIOUS_WORK_AND_CONTINUING_TASKS_AS_NEEDED` |
| Stage 3 | `AUTORUN_STAGE3_COMPLETE` | `AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY` |
| Emergency | (not mentioned) | `AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP` |

**Source locations:**
- Wrong strings: `README.md:561,565,569,575,577,579` (mermaid diagram + stage descriptions)
- Correct strings: `config.py:184,200,215,235`
- Correct strings: `commands/plannew.md:245,247,249,254`
- Correct strings: `commands/go.md:14,20`

**Impact:** Users following README.md will output wrong markers → autorun system
won't detect stage completion → sessions never advance or terminate properly.

---

## CRITICAL BUG 2: Plan Export Regression Investigation

### Chronological Bug History (from notes/)

| Date | Bug | Root Cause | Status |
|------|-----|-----------|--------|
| 2025-12-20 | All exports go to `rejected/` | `"approved" in str(tool_response)` never matches | Fixed → use `permission_mode` |
| 2025-12-20 | Missing `{datetime}` template var | Not implemented | Fixed |
| 2026-01-29 | Wrong plan exported under load | Race: multiple sessions call `get_most_recent_plan()` | Fixed → SessionLock + per-session isolation |
| 2026-01-29 | Stale lock file recovery | Crashed process leaves orphaned lock | Fixed → PID-based detection |
| 2026-02-03 | Wrong plan exported | Plugin ignores `tool_response.filePath` | Fixed (commit `905b584`) → primary source is now `filePath` |
| 2026-02-12 | `NoneType` crash | `ctx.payload` doesn't exist; use `ctx.event` | Fixed |
| 2026-02-12 | Recovery disabled | `recover_unexported_plans()` returns `None` early | Fixed |
| 2026-02-12 | Formatting corruption | Triple newlines in code | Fixed |
| 2026-02-13 | `SessionStart` hook error | Schema violations / stderr noise | Fixed |
| **2026-02-18** | **Plans not copying to notes/** | **CURRENT — investigate below** | **OPEN** |

### Current Architecture (verified against actual code)

**Two hook files exist — one per CLI:**
- `hooks/claude-hooks.json` — Claude Code; referenced by `.claude-plugin/plugin.json:22`
- `hooks/hooks.json` — Gemini CLI; standalone registration

**Hook Timeouts (potential regression):**

| File | Timeout value | Unit | Effective |
|------|--------------|------|-----------|
| `hooks/claude-hooks.json:7,13,19,23,28,33,38` | `10` | ms (Claude Code hooks spec) | **10ms — critically short** |
| `hooks/hooks.json:11,21,32,42,52,64,74,82` | `5000` | ms (Gemini CLI hooks spec) | 5 seconds — adequate |

**Bootstrap/install timeout note (per user, Message 5):** The one-time
self-install pathway forks a `nohup` durable process, so hook timeout does
NOT affect installation. Existing code handles this case.

**Timeout unit correction (from notes/hooks_api_reference.md:825,857,315-316):**
- Claude Code `timeout`: **SECONDS** — `"timeout": 10` = 10 seconds ✅ adequate
- Gemini CLI `timeout`: **MILLISECONDS** — `"timeout": 5000` = 5 seconds ✅ adequate
- Both hook files have equivalent ~5-10 second timeouts. Timeout is NOT the regression.
- The API reference (`hooks_api_reference.md:289`) shows `"timeout": 10` as the standard example for Claude Code.

**Conclusion: timeout: 10 is correct (10 seconds for Claude Code). Look elsewhere for the regression.**

**Likely real regression causes to investigate:**
1. `get_current_plan()` returns None — plan not in `active_plans` (not tracked via Write), AND `tool_result.filePath` missing from ExitPlanMode response in current Claude Code version
2. Content-hash dedup fires incorrectly — plan was marked as "exported" when it wasn't (stale tracking DB)
3. `config.enabled` is False in user's `~/.claude/plan-export.config.json`
4. Exception swallowed silently in `export_on_exit_plan_mode()` — check logs at `~/.claude/plan-export-debug.log`
5. `record_write()` not called — plan created outside Write/Edit hooks (e.g., via plugin system that bypasses PostToolUse)
6. `project_dir` mismatch — `ctx.cwd` not set correctly, so plan tracked under different project dir than recovery looks for

**PLAN_TOOLS coverage (config.py:34):**
```python
PLAN_TOOLS = {"ExitPlanMode", "exit_plan_mode"}  # Both CLIs covered ✅
```

**Claude Code (claude-hooks.json):**
- `PreToolUse` matcher (`line 12`): `"Write|Edit|Bash|ExitPlanMode"` ✅ ExitPlanMode covered
- `PostToolUse` matcher (`line 18`): `"ExitPlanMode|Write|Edit"` ✅ ExitPlanMode covered
- `SessionStart` (`line 26`): No matcher, fires for all ✅

**Gemini CLI (hooks.json):**
- `BeforeTool` matcher (`line 27`): `"write_file|run_shell_command|replace|read_file|glob|grep_search"` ❌ `exit_plan_mode` MISSING — PreToolUse backup does NOT fire for Gemini ExitPlanMode
- `AfterTool` matcher (`line 59`): `"write_file|replace|read_file|exit_plan_mode"` ✅ covered
- `SessionStart` (`line 4`): No matcher, fires for all ✅

**Bug**: Gemini's BeforeTool matcher is missing `exit_plan_mode`, so
`track_and_export_plans_early()` (the PreToolUse backup) never runs for Gemini
when a plan is accepted. The primary `export_on_exit_plan_mode()` (AfterTool)
still runs. If AfterTool times out or fails, there's no Gemini PreToolUse backup.

### Plan Export Flow (current implementation)

**Option 2 path (regular accept — PostToolUse fires):**
```
ExitPlanMode triggered
  → PreToolUse(ExitPlanMode) → track_and_export_plans_early()  [Claude only]
  → PostToolUse(ExitPlanMode) → export_on_exit_plan_mode()     [both CLIs]
    → get_current_plan():
        1. tool_result.filePath (primary — most reliable)
        2. JSON parse of tool_result string → filePath
        3. active_plans for this project (fallback)
        4. get_plan_from_exit_message() → parse "saved to:" pattern
```

**Option 1 path (fresh context — PostToolUse does NOT fire, Claude bug):**
```
User accepts with Option 1 → NEW session_id assigned
  → SessionStart fires → recover_unexported_plans()
    → get_unexported():
        reads GLOBAL_SESSION_ID shelve → active_plans for this project
        filters by content hash (skips already exported)
```

**Rejected plan detection:** The `export()` method accepts `rejected: bool`
parameter (`plan_export.py:671`). Currently, `export_on_exit_plan_mode()`
(`plan_export.py:1005`) calls `exporter.export(plan)` WITHOUT `rejected=True`.
The `rejected=True` path exists but may not be called when appropriate.

**Question:** When is a plan considered "rejected"? Looking at history notes
(`2026_02_03_1913_plan_export_tool_response_filepath_bug_fix.md`): rejected
means user declined the plan (ExitPlanMode was NOT accepted). Currently there
is no hook that detects plan rejection (user pressing "Reject" or abandoning
plan mode without accepting). This is a gap.

### Rejection Detection Gap

Currently `export_rejected: True` is in DEFAULT_CONFIG but there is no code
path that calls `export(plan, rejected=True)`. The rejected plan directory
(`notes/rejected/`) is configured but never written to during normal usage.

**To fix:** Detect when ExitPlanMode is NOT called but plan was in progress
(SessionStart recovery with `rejected=True`). Or detect via `tool_result`
message content indicating rejection.

---

## Files to Modify

| File | Change | Priority |
|------|--------|----------|
| `README.md` | Fix 6 wrong stage marker strings + add missing commands | HIGH |
| `hooks/claude-hooks.json` | Investigate/fix `"timeout": 10` (may be wrong unit) | CRITICAL |
| `hooks/hooks.json` | Add `exit_plan_mode` to BeforeTool matcher for Gemini | MEDIUM |
| `plan_export.py` | Add actual rejected plan detection path | MEDIUM |

---

## Complete Capabilities + Test Commands

### 1. AutoFile Policy

**Implementation:** `plugins.py:58-83`, `config.py:349-351`

| Command | Policy | Test |
|---------|--------|------|
| `/cr:a` `/cr:allow` `/afa` | allow-all | `/cr:a` → shows "AutoFile policy: allow-all" |
| `/cr:j` `/cr:justify` `/afj` | justify-create | `/cr:j` → then try Write tool without `<AUTOFILE_JUSTIFICATION>` tag |
| `/cr:f` `/cr:find` `/afs` | strict-search | `/cr:f` → then try to create any new file |
| `/cr:st` `/cr:status` `/afst` | show status | `/cr:st` → shows current policy + active blocks |

**How to test strict mode:** After `/cr:f`, ask Claude to create a new file. The
`enforce_file_policy()` hook at `plugins.py:118-139` fires on `PreToolUse` for
Write/Edit and blocks with a `policy_blocked` message.

**Manual live test procedure:**
```bash
# Test 1: strict-search blocks new file creation
/cr:f
# Then: "Please create /tmp/clautorun-test-policy.txt"
# Expected: hook blocks, shows policy_blocked["SEARCH"] message

# Test 2: justify-create blocks without tag
/cr:j
# Then: "Please create /tmp/clautorun-test-justify.txt"
# Expected: blocked — no <AUTOFILE_JUSTIFICATION> tag present

# Test 3: justify-create allows with tag
# Then: "Please create /tmp/clautorun-test-justify.txt <AUTOFILE_JUSTIFICATION>testing policy</AUTOFILE_JUSTIFICATION>"
# Expected: allowed

# Test 4: allow-all permits everything
/cr:a
/cr:st   # Verify shows allow-all
```

---

### 2. Autorun / Autonomous Execution

**Implementation:** `plugins.py:562-675`, `config.py:178-245`

| Command | Mode | Test |
|---------|------|------|
| `/cr:go <task>` `/cr:run` `/autorun` | 3-stage autonomous | `/cr:go "Write a hello world script and test it"` |
| `/cr:gp <task>` `/cr:proc` `/autoproc` | Procedural (Wait Process) | `/cr:gp "Refactor the config module"` |
| `/cr:x` `/cr:stop` `/autostop` | Graceful stop | Issue after task starts |
| `/cr:sos` `/cr:estop` `/estop` | Emergency stop | Issue at any time |

**Actual stage flow (from `config.py:184,200,215,235`):**
```
Stage 1: AI outputs → AUTORUN_INITIAL_TASKS_COMPLETED
Stage 2: AI outputs → CRITICALLY_EVALUATING_PREVIOUS_WORK_AND_CONTINUING_TASKS_AS_NEEDED
Stage 3: AI outputs → AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY
Emergency: AI outputs → AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP
```

**Manual live test procedure:**
```bash
/cr:go "Create a /tmp/clautorun-test-hello.txt file with 'hello world' content"
# Expected: AI writes file, outputs all 3 stage markers in sequence, session ends
# Verify: cat /tmp/clautorun-test-hello.txt shows 'hello world'
# Verify: All 3 AUTORUN_* strings appear in response

/cr:sos
# Expected: outputs AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP, stops immediately
```

---

### 3. Safety Guards (DEFAULT_INTEGRATIONS)

**Implementation:** `config.py:59-175`, `plugins.py:446-550`

28 built-in command blocks. Fires on `BeforeTool` for bash commands.

| Blocked Command | Suggested Alternative | Config Line |
|----------------|----------------------|-------------|
| `rm -rf`, `rm -r`, `rm -f` | `trash` (reversible) | `config.py:60-68` |
| `git reset --hard` | `git stash` | `config.py:70-75` |
| `git checkout -- .` | `git stash` | `config.py:76-80` |
| `git clean -f`, `git clean -fd` | `git clean -n` (dry run) | `config.py:81-93` |
| `dd if=`, `mkfs`, `fdisk`, `parted` | (disk safety) | `config.py:109-120` |
| `grep` | `{grep}` tool (Grep/grep_search) | `config.py:133-142` |
| `find` | `{glob}` tool (Glob/glob) | `config.py:143-152` |
| `cat`, `head`, `tail` | `{read}` tool (Read/read_file) | `config.py:153-164` |
| `sed` | `{edit}` tool (Edit/replace) | `config.py:165-175` |

**Manual live test procedure (create actual test files in /tmp/):**
```bash
# Pre-setup: create test files for rm tests
touch /tmp/clautorun-test-rm-1.txt
touch /tmp/clautorun-test-rm-2.txt
echo "test content" > /tmp/clautorun-test-read.txt

# Test rm blocking:
# "Please run: rm /tmp/clautorun-test-rm-1.txt"
# Expected: blocked, suggests 'trash' command
# VERIFY: /tmp/clautorun-test-rm-1.txt still exists after block

# Test rm -rf blocking:
# "Please run: rm -rf /tmp/clautorun-test-rm-2.txt"
# Expected: blocked, suggests 'trash' command

# Test grep blocking:
# "Please run: grep 'test' /tmp/clautorun-test-read.txt"
# Expected: blocked, suggests 'Grep' tool (Claude) or 'grep_search' tool (Gemini)
# Verify format_suggestion() is working: check exact tool name in message

# Test find blocking:
# "Please run: find /tmp -name '*.txt'"
# Expected: blocked, suggests 'Glob' tool

# Test cat blocking:
# "Please run: cat /tmp/clautorun-test-read.txt"
# Expected: blocked, suggests 'Read' tool

# Test sed blocking:
# "Please run: sed -i 's/test/replaced/g' /tmp/clautorun-test-read.txt"
# Expected: blocked, suggests 'Edit' tool

# Test git reset --hard blocking:
# "Please run: git reset --hard HEAD~1"
# Expected: blocked, suggests 'git stash'

# Test git clean -f blocking:
# "Please run: git clean -f"
# Expected: blocked, suggests 'git clean -n' (dry run)

# Cleanup (using trash, not rm):
# trash /tmp/clautorun-test-rm-1.txt
# trash /tmp/clautorun-test-rm-2.txt
# trash /tmp/clautorun-test-read.txt
```

---

### 4. Session / Global Pattern Overrides

**Implementation:** `plugins.py:187-435`

| Command | Scope | Action | Test |
|---------|-------|--------|------|
| `/cr:no <pattern>` | session | block | `/cr:no rm` |
| `/cr:ok <pattern>` | session | allow | `/cr:ok rm` |
| `/cr:clear` | session | clear all | `/cr:clear` |
| `/cr:blocks` | session | show | `/cr:blocks` |
| `/cr:globalno <pattern>` | global | block | `/cr:globalno "git reset"` |
| `/cr:globalok <pattern>` | global | allow | `/cr:globalok "git reset"` |
| `/cr:globalclear` | global | clear all | `/cr:globalclear` |
| `/cr:globalstatus` | global | show | `/cr:globalstatus` |

**Pattern types supported** (`plugins.py:275-322`):
- Literal (default): `/cr:no rm`
- Regex prefix: `/cr:no regex:eval\(`
- Glob prefix: `/cr:no glob:*.tmp`
- Auto-detect regex `/…/`: `/cr:no /eval\(.*assert/`

**ReDoS protection**: `plugins.py:187-240` validates all regex patterns.

**Manual live test procedure:**
```bash
# Test session block/allow cycle:
/cr:no clautorun-session-test-pattern
/cr:blocks   # Verify pattern appears

# "Please run: clautorun-session-test-pattern --help"
# Expected: blocked with session block message

/cr:ok clautorun-session-test-pattern
/cr:blocks   # Verify pattern removed or allowed

# Test global block:
/cr:globalno clautorun-global-test-pattern
/cr:globalstatus   # Verify pattern in global blocks

# Test clear:
/cr:clear         # Clear session
/cr:globalclear   # Clear global
/cr:globalstatus  # Verify cleared

# Test regex pattern:
/cr:no regex:clautorun-test-[0-9]+
# "Please run: clautorun-test-42 --debug"
# Expected: blocked by regex match

# Test glob pattern:
/cr:no glob:clautorun-test-*.tmp
# "Please run: touch clautorun-test-file.tmp"
# Expected: blocked by glob match
/cr:clear
```

---

### 5. Platform-Aware Tool Names (format_suggestion)

**Implementation:** `core.py:130-215` (committed `c0e4367`)

| Template Key | Claude Code | Gemini CLI | Used In |
|---|---|---|---|
| `{grep}` | `Grep` | `grep_search` | grep/awk blocks |
| `{glob}` | `Glob` | `glob` | find block |
| `{read}` | `Read` | `read_file` | cat/head/tail blocks |
| `{write}` | `Write` | `write_file` | echo redirect block |
| `{edit}` | `Edit` | `replace` | sed block |
| `{bash}` | `Bash` | `run_shell_command` | (reserved) |
| `{ls}` | `LS` | `list_directory` | (reserved) |

**Three naming layers** (as of Claude Code CLI v2.1.47):
1. API tool_name: what hooks see (`Glob`, not `Search`)
2. CLI display: what terminal shows (`Search` for Glob in Claude Code)
3. Bash command: what's being blocked (`find`, `grep`, etc.)

**Test (automated):** `uv run pytest plugins/clautorun/tests/test_core.py::TestFormatSuggestion -v`
(21 tests; canary tests pin exact API names per CLI version)

**Manual live test:** Trigger a grep block (see Section 3) and verify the
suggestion text says "Grep tool" (Claude) not "grep_search" or vice versa.

---

### 6. Plan Management

**Implementation:** `plugins.py:901-944`, `commands/plannew.md`, etc.

| Command | File Read | Test |
|---------|-----------|------|
| `/cr:pn` `/cr:plannew` | `commands/plannew.md` | `/cr:pn` → displays plan creation template |
| `/cr:pr` `/cr:planrefine` | `commands/planrefine.md` | `/cr:pr` → displays critique prompts |
| `/cr:pu` `/cr:planupdate` | `commands/planupdate.md` | `/cr:pu` → displays update prompts |
| `/cr:pp` `/cr:planprocess` | `commands/planprocess.md` | `/cr:pp` → displays execution workflow |

**Manual live test:**
```bash
/cr:pn   # Should display plannew.md content with stage markers
# Verify: content shows AUTORUN_INITIAL_TASKS_COMPLETED (correct marker)
# Verify: content does NOT show AUTORUN_STAGE1_COMPLETE (wrong marker)
```

---

### 7. Plan Export

**Implementation:** `plan_export.py:348-1072`

| Command | Function | Test |
|---------|----------|------|
| `/cr:pe` | Show status/config | `/cr:pe` |
| `/cr:pe-on` | Enable auto-export | `/cr:pe-on` |
| `/cr:pe-off` | Disable auto-export | `/cr:pe-off` |
| `/cr:pe-dir <path>` | Set output dir | `/cr:pe-dir notes/` |
| `/cr:pe-fmt <pattern>` | Set filename pattern | `/cr:pe-fmt {datetime}_{name}` |
| `/cr:pe-reset` | Reset to defaults | `/cr:pe-reset` |
| `/cr:pe-rej` | Toggle rejected export | `/cr:pe-rej` |
| `/cr:pe-rdir <path>` | Set rejected dir | `/cr:pe-rdir notes/rejected/` |
| `/cr:pe-cfg` | Interactive config | `/cr:pe-cfg` |

**Auto-export trigger:** `plan_export.py:1005-1030` fires on `PostToolUse`
for `ExitPlanMode`. Config defaults: `output_plan_dir="notes"`,
`filename_pattern="{datetime}_{name}"`, `export_rejected=True`,
`output_rejected_plan_dir="notes/rejected"`.

**Manual live test (to validate end-to-end):**
```bash
/cr:pe   # Check current config — is enabled: True?
# Note current files in notes/ and notes/rejected/
ls notes/    # baseline

# Enter plan mode, create a trivial plan, accept it
# Then verify:
ls notes/    # Should have a new .md file
ls notes/rejected/   # Should NOT have a new file (plan was accepted, not rejected)

# Check hook timeout (CRITICAL regression check):
time uv run --quiet --project plugins/clautorun python plugins/clautorun/hooks/hook_entry.py --cli claude <<< '{}'
# If > 10ms, the claude-hooks.json timeout: 10 is the regression
```

**Rejected plan test:**
```bash
# Enter plan mode, create a plan, EXIT plan mode WITHOUT accepting (abandon)
# Then start a new session
# On SessionStart, recover_unexported_plans() should export to notes/rejected/
# Verify: ls notes/rejected/ shows a new file
```

**Known issue — Gemini BeforeTool missing exit_plan_mode:**
`hooks.json:27` BeforeTool matcher: `"write_file|run_shell_command|replace|read_file|glob|grep_search"`
Does NOT include `exit_plan_mode`. So `track_and_export_plans_early()`
(PreToolUse backup) does NOT fire on Gemini when ExitPlanMode is called.
Only the AfterTool path fires. Fix: add `|exit_plan_mode` to BeforeTool matcher.

**Known issue — claude-hooks.json timeout:**
`hooks/claude-hooks.json:7,13,19,23,28,33,38` all have `"timeout": 10`.
If this is milliseconds (Claude Code hooks spec), ALL hooks time out before
completing. `hooks.json` uses `"timeout": 5000` (5 seconds). Verify the unit
and fix if needed.

---

### 8. Tmux / Session Tools

**Implementation:** `tmux_utils.py`, `ai_monitor.py`, `plugins.py:958-1016`

| Command | Function | Test |
|---------|----------|------|
| `/cr:tm` `/cr:tmux` | Session lifecycle | `/cr:tm` → session management options |
| `/cr:tt` `/cr:ttest` | CLI testing | `/cr:tt` → isolated test session |
| `/cr:tabs` | Discover sessions | `/cr:tabs` → lists tmux windows with Claude |
| `/cr:tabw` | Execute across tabs | `/cr:tabw` → multi-session actions |

**Note:** Command handlers for tmux are in `commands/tm.md`, `commands/tt.md`,
`commands/tabs.md` (markdown slash commands), not Python decorators.

---

### 9. Task Lifecycle Tracking

**Implementation:** `task_lifecycle.py` (1500+ lines)

| Command | Alias | Function | Test |
|---------|-------|----------|------|
| `/task-status` | `/ts` `/tasks` | Show task states | `/task-status` |
| `/task-ignore <id>` | `/ti` | Mark ignored | `/task-ignore <id> reason` |

**Hooks registered** (`task_lifecycle.py:1519+`): fires on `AfterTool` for
`write_todos`. Tracks TaskCreate/TaskUpdate/TaskComplete lifecycle; can block
session stop if tasks still pending.

**Manual live test:**
```bash
# Create a task, check status, ignore it
# "Use TaskCreate to create a test task 'clautorun-test-task'"
/task-status   # Should show the task as pending/in_progress
/task-ignore <id> "test cleanup"
/task-status   # Should show task as ignored
```

---

### 10. Documentation Commands

**Implementation:** `commands/gc.md`, `commands/ph.md`

| Command | Content | Test |
|---------|---------|------|
| `/cr:gc` `/cr:commit` | Git commit 17-step requirements | `/cr:gc` |
| `/cr:ph` `/cr:philosophy` | Universal design 17 principles | `/cr:ph` |

---

### 11. System Commands

| Command | Function | Test |
|---------|----------|------|
| `/cr:reload` | Force-reload integration files | `/cr:reload` |
| `/test-clautorun` | Verify plugin loaded | `/test-clautorun` |
| `/cr:restart-daemon` | Restart clautorun daemon | `/cr:restart-daemon` |

**Manual live test:**
```bash
/test-clautorun   # Should confirm plugin is loaded and working
/cr:st            # Should show current AutoFile policy
/cr:reload        # Should report count of loaded integrations
```

---

### 12. Gemini CLI Compatibility

**Implementation:** `config.py:439-502`, `core.py:88-115`, `core.py:234-321`

| Feature | Implementation | Test |
|---------|---------------|------|
| CLI auto-detect | `config.py:detect_cli_type()` | `GEMINI_SESSION_ID` env var → gemini |
| Exit-2 workaround | `config.py:should_use_exit2_workaround()` | `CLAUTORUN_EXIT2_WORKAROUND=always` |
| Event normalization | `core.py:GEMINI_EVENT_MAP` | `BeforeTool` → `PreToolUse` |
| Tool name substitution | `core.py:format_suggestion()` | See section 5 above |

**Environment vars:**
```bash
export CLAUTORUN_EXIT2_WORKAROUND=auto    # Default (recommended)
export CLAUTORUN_EXIT2_WORKAROUND=always  # Force exit-2 for all CLIs
export CLAUTORUN_EXIT2_WORKAROUND=never   # Disable (Gemini JSON-only mode)
```

---

### 13. Hooks Registration

**Two hook files:**
- `hooks/claude-hooks.json` → Claude Code (referenced by `.claude-plugin/plugin.json:22`)
- `hooks/hooks.json` → Gemini CLI

**Claude Code (claude-hooks.json):**

| Event | Matcher | Purpose |
|-------|---------|---------|
| `UserPromptSubmit` | `/afs\|/afa\|/afj\|/afst\|/autorun\|/autostop\|/estop\|/cr:` | Command dispatch |
| `PreToolUse` | `Write\|Edit\|Bash\|ExitPlanMode` | Safety guards + file policy + plan export backup |
| `PostToolUse` | `ExitPlanMode\|Write\|Edit` | Plan export (primary) |
| `PostToolUse` | `TaskCreate\|TaskUpdate\|TaskGet\|TaskList` | Task lifecycle |
| `SessionStart` | (all) | Recovery of unexported plans |
| `Stop` | (all) | Three-stage completion |
| `SubagentStop` | (all) | Subagent stop |

**Gemini CLI (hooks.json):**

| Event | Matcher | Purpose |
|-------|---------|---------|
| `SessionStart` | (all) | Initialize session state |
| `BeforeAgent` | `/afs\|/afa\|...\|/cr:` | Command dispatch |
| `BeforeTool` | `write_file\|run_shell_command\|replace\|read_file\|glob\|grep_search` | Safety guards (❌ missing `exit_plan_mode`) |
| `AfterAgent` | `AUTORUN\|/cr:` | Post-command processing |
| `AfterModel` | (all) | Post-generation processing |
| `AfterTool` | `write_file\|replace\|read_file\|exit_plan_mode` | Plan export |
| `AfterTool` | `write_todos` | Task lifecycle |
| `SessionEnd` | (all) | Cleanup |

---

## Execution Plan: Live Testing Protocol

When executing, follow this protocol for each capability test:

### Notes File
Create and maintain: `notes/{YYYY}_{MM}_{DD}_{HH}{mm}_capability_audit_findings.md`

Structure:
```markdown
# clautorun Capability Audit — {date}

## Test Results

### 1. AutoFile Policy
- [ ] allow-all: PASS/FAIL — observation
- [ ] justify-create block: PASS/FAIL — observation
- [ ] justify-create allow with tag: PASS/FAIL — observation
- [ ] strict-search: PASS/FAIL — observation

### 2. Stage Markers (README vs actual)
- [ ] README has wrong strings at lines 561,565,569: CONFIRMED/NOT FOUND

### 3. Safety Guards
- [ ] rm blocked: PASS/FAIL
- [ ] rm -rf blocked: PASS/FAIL
- [ ] grep blocked + correct tool name shown: PASS/FAIL
- [ ] find blocked: PASS/FAIL
- [ ] cat blocked: PASS/FAIL
- [ ] sed blocked: PASS/FAIL
- [ ] git reset --hard blocked: PASS/FAIL
- [ ] git clean -f blocked: PASS/FAIL

### 4. Session/Global Blocks
- [ ] /cr:no adds block: PASS/FAIL
- [ ] blocked command is denied: PASS/FAIL
- [ ] /cr:ok removes block: PASS/FAIL
- [ ] /cr:clear clears all: PASS/FAIL
- [ ] /cr:globalno persists: PASS/FAIL
- [ ] /cr:globalclear removes: PASS/FAIL

### 5. Plan Export
- [ ] Accepted plan copies to notes/: PASS/FAIL
- [ ] Rejected/abandoned plan copies to notes/rejected/: PASS/FAIL
- [ ] claude-hooks.json timeout: 10 — unit verified: SECONDS/MS
- [ ] hook execution time: __ms (< 10ms = regression)
- [ ] Gemini BeforeTool missing exit_plan_mode: CONFIRMED/FIXED

### 6. format_suggestion
- [ ] Automated tests pass (21 tests): PASS/FAIL
- [ ] grep block shows "Grep tool" for Claude: PASS/FAIL

### 7. Plan Management Commands
- [ ] /cr:pn shows correct stage markers: PASS/FAIL
- [ ] /cr:pr shows critique prompts: PASS/FAIL

### 8. Task Lifecycle
- [ ] /task-status shows tasks: PASS/FAIL
- [ ] /task-ignore works: PASS/FAIL

### 9. Documentation Commands
- [ ] /cr:gc shows commit requirements: PASS/FAIL
- [ ] /cr:ph shows design philosophy: PASS/FAIL

### 10. System Commands
- [ ] /test-clautorun confirms loaded: PASS/FAIL
- [ ] /cr:st shows policy: PASS/FAIL
- [ ] /cr:reload reports integrations: PASS/FAIL

## Action Items
- [ ] Fix README.md stage markers (lines 561,565,569,575,577,579)
- [ ] Verify/fix claude-hooks.json timeout unit
- [ ] Add exit_plan_mode to Gemini BeforeTool matcher
- [ ] Add actual rejected plan detection call path
- [ ] Add missing commands to README quick-reference table

## Bugs Found
[Document any new bugs found during testing with file:line references]
```

---

## Test Coverage Gaps (from test audit)

Current state: ~95% coverage across 99 test files.

**Missing tests identified:**
1. **claude-hooks.json timeout unit** — no test verifies timeout value is correct unit
2. **Gemini BeforeTool missing exit_plan_mode** — no test verifies backup fires for Gemini ExitPlanMode
3. **Rejected plan detection** — tests exist for `export(rejected=True)` but no test for
   when `rejected=True` is automatically triggered
4. **README stage marker strings** — no automated test comparing README.md strings
   to `config.py` values (easy to add: grep README, compare to `CONFIG["stage1_message"]`)
5. **Complex pipe chains** — `cat file | grep pattern | sed` only partially tested
6. **Concurrent Gemini + Claude export** — existing tests use SessionLock but don't
   test Gemini session + Claude session writing same plan simultaneously

**New tests to add:**
```python
# test_readme_accuracy.py — NEW
def test_readme_stage_markers_match_config():
    """README.md must not contain AUTORUN_STAGE[123]_COMPLETE."""
    readme = Path("README.md").read_text()
    assert "AUTORUN_STAGE1_COMPLETE" not in readme
    assert "AUTORUN_STAGE2_COMPLETE" not in readme
    assert "AUTORUN_STAGE3_COMPLETE" not in readme
    from clautorun.config import CONFIG
    assert CONFIG["stage1_message"] in readme
    assert CONFIG["stage2_message"] in readme
    assert CONFIG["stage3_message"] in readme

# test_plan_export_hooks.py — additions
def test_gemini_before_tool_matcher_includes_exit_plan_mode():
    """Gemini BeforeTool matcher must include exit_plan_mode."""
    hooks = json.loads(Path("hooks/hooks.json").read_text())
    before_tool = hooks["hooks"]["BeforeTool"][0]["matcher"]
    assert "exit_plan_mode" in before_tool

def test_claude_hooks_timeout_is_reasonable():
    """claude-hooks.json timeouts must be > 100ms."""
    hooks = json.loads(Path("hooks/claude-hooks.json").read_text())
    for event, handlers in hooks["hooks"].items():
        for handler_group in handlers:
            for hook in handler_group.get("hooks", []):
                timeout = hook.get("timeout", 0)
                assert timeout > 100, f"{event} timeout {timeout} is too short"
```

---

## README.md Fix Required

**File to edit:** `README.md`

**Lines to fix:**
- `README.md:561` `AUTORUN_STAGE1_COMPLETE` → `AUTORUN_INITIAL_TASKS_COMPLETED`
- `README.md:565` `AUTORUN_STAGE2_COMPLETE` → `CRITICALLY_EVALUATING_PREVIOUS_WORK_AND_CONTINUING_TASKS_AS_NEEDED`
- `README.md:569` `AUTORUN_STAGE3_COMPLETE` → `AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY`
- `README.md:575` prose description for Stage 1
- `README.md:577` prose description for Stage 2
- `README.md:579` prose description for Stage 3
- Add `AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP` to emergency stop section

**Also add to README (missing from quick reference table):**
- `/cr:blocks` and `/cr:globalstatus` (show commands)
- `/cr:globalclear` (global clear)
- `/task-status` / `/task-ignore` in the main command table
- `/cr:reload` system command

---

---

## Execution Context (Self-Contained Reference for Fresh Context)

### Git / Repo

- **Branch**: `feature/gemini-cli-integration`
- **Repo root**: `/Users/athundt/.claude/clautorun/`
- **Working dir for all commands**: `/Users/athundt/.claude/clautorun/`
- **Source of truth**: `plugins/clautorun/src/clautorun/` — edit ONLY here
- **DO NOT edit**: plugin cache at `~/.claude/plugins/cache/clautorun/` (overwritten on install)

### Key Source Files (all under `plugins/clautorun/src/clautorun/`)

| File | Purpose | Key Functions/Classes (semantic references) |
|------|---------|---------------------------------------------|
| `config.py` | PLAN_TOOLS, WRITE_TOOLS, EDIT_TOOLS, DEFAULT_INTEGRATIONS, stage markers | `PLAN_TOOLS` dict:34, `DEFAULT_INTEGRATIONS` dict:59-175, `CONFIG["stage1_message"]`:184, `CONFIG["stage2_message"]`:200, `CONFIG["stage3_message"]`:215, `detect_cli_type()`:439-477 |
| `core.py` | Daemon, EventContext, GEMINI_EVENT_MAP, CLI_TOOL_NAMES, format_suggestion | `GEMINI_EVENT_MAP` dict:88-95, `INTERNAL_TO_GEMINI` dict:108-115, `CLI_TOOL_NAMES` dict:145-167, `get_tool_names()`:170-175, `format_suggestion()`:178-215, `ClautorunDaemon` class:1066-1486 |
| `plan_export.py` | PlanExport class, record_write, get_current_plan, export, recover_unexported_plans | `PlanExport` class:388-745, `record_write()`:530-547, `get_current_plan()`:549-576, `_determine_output_dir()`:628-667, `export()`:671-745, `export_plan()`:752-788, `export_rejected_plan()`:791-831, `export_on_exit_plan_mode()`:1005-1030, `recover_unexported_plans()`:956-1072 |
| `plugins.py` | @app.command handlers, enforce_file_policy, check_blocked_commands, detect_plan_approval | `_make_policy_handler()` factory:58-72, `@app.command("/cr:st")`:86-115, `enforce_file_policy()`:118-139, `check_blocked_commands()`:446-550, `activate_autorun()`:562-581, `detect_plan_approval()`:748-792 |
| `integrations.py` | load_all_integrations, mtime-based reload, invalidate_caches | `load_all_integrations()`, `invalidate_caches()` (grep for exact lines) |
| `session_manager.py` | session_state(), SessionLock (fcntl.flock), RAII locking | `session_state()` context manager, `SessionLock` class (grep for exact lines) |
| `task_lifecycle.py` | TaskCreate/Update/List/Get tracking, register_hooks | `register_hooks()` around line 1519+ (grep for exact location) |
| `hook_entry.py` (in `hooks/`) | Entry point for all hooks; resolves binary, forwards to daemon | `main()` function (grep for exact lines) |

### Hook Config Files

| File | CLI | Timeout unit | Current timeout | Path |
|------|-----|-------------|----------------|------|
| `hooks/claude-hooks.json` | Claude Code | **seconds** | 10 s (adequate) | `plugins/clautorun/hooks/claude-hooks.json` |
| `hooks/hooks.json` | Gemini CLI | **milliseconds** | 5000 ms = 5 s | `plugins/clautorun/hooks/hooks.json` |

- Source: `notes/hooks_api_reference.md:825` (Claude = seconds), `:857` (Gemini = ms)
- Claude plugin manifest: `plugins/clautorun/.claude-plugin/plugin.json` → `"hooks": "./hooks/claude-hooks.json"`

### State / Data Paths

| Path | Purpose |
|------|---------|
| `~/.claude/plans/*.md` | Plan files created by Claude Code (`PLANS_DIR` in plan_export.py:206) |
| `~/.claude/sessions/plugin___plan_export__.db` | Shelve DB: `active_plans` + `tracking` dicts (survives daemon restart + Option 1 session clear) |
| `~/.claude/plan-export.config.json` | User config override (default: enabled=true, notes/, notes/rejected/) |
| `~/.claude/plan-export-debug.log` | Debug log when `debug_logging: true` in config — ENABLE THIS for regression diagnosis |
| `~/.clautorun/daemon.sock` | Unix socket for daemon IPC |
| `~/.clautorun/daemon.pid` | Daemon PID file |

### Install / Deploy Commands

```bash
# Primary install (from repo root) — run after any Python source change:
(uv run --project plugins/clautorun python -m clautorun --install --force && \
  cd plugins/clautorun && uv tool install --force --editable . && \
  cd ../.. && clautorun --restart-daemon) 2>&1 | tee "install-$(date +%Y%m%d-%H%M%S).log"

# Quick restart only (no reinstall — if Python source is editable):
clautorun --restart-daemon
# OR in Claude Code session:
/cr:restart-daemon

# Check daemon running:
pgrep -fl "clautorun" || echo "daemon not running"

# Kill all daemons (force cold restart):
pkill -f "clautorun.daemon" 2>/dev/null; sleep 1
```

### Test Commands

```bash
# Run all tests (from repo root):
uv run pytest plugins/clautorun/tests/ -v --tb=short

# Run just format_suggestion canary tests (21 tests):
uv run pytest plugins/clautorun/tests/test_core.py::TestFormatSuggestion -v

# Run plan export tests:
uv run pytest plugins/clautorun/tests/test_plan_export_class.py -v --tb=short

# Run simple smoke tests:
uv run pytest plugins/clautorun/tests/test_unit_simple.py -v

# Run command blocking tests:
uv run pytest plugins/clautorun/tests/test_command_blocking.py -v
```

### Environment Variables

| Variable | Values | Purpose |
|----------|--------|---------|
| `CLAUTORUN_EXIT2_WORKAROUND` | `auto` (default) / `always` / `never` | Exit-2 workaround for Claude Code bug #4669 |
| `GEMINI_SESSION_ID` | set by Gemini CLI | Presence → detected as Gemini CLI session |
| `GEMINI_PROJECT_DIR` | set by Gemini CLI | Presence → detected as Gemini CLI session |
| `CLAUDE_PLUGIN_ROOT` | set by Claude Code | Absolute path to plugin root (for hook commands) |
| `CLAUTORUN_DEBUG` | any non-empty | Enable verbose debug output |
| `${extensionPath}` | set by Gemini | Same as `CLAUDE_PLUGIN_ROOT` for Gemini hooks |

### Plan Export: Enable Debug Logging

```bash
# Option 1: edit ~/.claude/plan-export.config.json
echo '{"enabled": true, "debug_logging": true, "notify_claude": true}' > ~/.claude/plan-export.config.json

# Option 2: via Claude Code session (shows config)
/cr:pe

# Then watch debug log:
tail -f ~/.claude/plan-export-debug.log
```

### Multiple Code Location Warning

There are **9 locations** where clautorun code may exist (see `notes/clautorun_install_paths_reference.md`). After editing `plugins/clautorun/src/clautorun/`, the daemon must be restarted or the editable install must be active. Key locations:

1. **Dev source** (EDIT HERE): `plugins/clautorun/src/clautorun/`
2. **Dev venv** (editable): `plugins/clautorun/.venv/lib/python3.12/site-packages/clautorun/`
3. **UV global tool**: `~/.local/share/uv/tools/clautorun/lib/python3.12/site-packages/clautorun/`
4. **Gemini extension**: `~/.gemini/extensions/clautorun-workspace/plugins/clautorun/src/clautorun/`
5. **Claude plugin cache** (READ-ONLY): `~/.claude/plugins/cache/clautorun/clautorun/<version>/`

Hook binary resolution order (from `hooks/hook_entry.py`):
1. `<script_dir>/../.venv/bin/clautorun` (venv binary)
2. `shutil.which("clautorun")` (UV global tool binary)
3. Direct Python import from source (fallback)

### API Reference Files in notes/

| File | Contents |
|------|---------|
| `notes/hooks_api_reference.md` | Full hooks API: event names, field specs, timeout units, response schemas |
| `notes/claude-code-hooks-api.md` | Claude Code specific: PreToolUse/PostToolUse/Stop schemas, tool names |
| `notes/gemini-cli-hooks-api.md` | Gemini CLI specific: BeforeTool/AfterTool schemas, tool names |
| `notes/clautorun_install_paths_reference.md` | All 9 code locations, install commands, audit scripts |
| `notes/gemini_claude_integration_env_vars_reference.md` | All env vars for both CLIs |

### Test Coverage Summary

**Total tests**: 99 test files, ~95% capability coverage (from test audit in this session)

**Fully tested**:
- ✅ All 3 AutoFile policies (allow/justify/search)
- ✅ Safety guards: rm, grep, find, cat, head, tail, sed, awk, git-destructive
- ✅ format_suggestion() + CLI_TOOL_NAMES (21 tests)
- ✅ Session/global blocks (literal, regex, glob patterns)
- ✅ Plan export on ExitPlanMode (PostToolUse hook)
- ✅ SessionStart recovery of unexported plans
- ✅ Multi-session isolation
- ✅ CLI detection (Claude vs Gemini)
- ✅ Exit-2 workaround
- ✅ Stage markers (3 stages)
- ✅ Task lifecycle (Create/Update/List/Get)

**Test gaps identified**:
- ⚠️ Gemini BeforeTool missing `exit_plan_mode` (backup hook doesn't fire)
- ⚠️ Rejected plan automatic detection (code path exists but never called)
- ⚠️ README stage marker accuracy (no automated test)
- ⚠️ Complex pipe chains (partial coverage)

### Troubleshooting Quick Reference

```bash
# Plan export not working? Enable debug logging first:
echo '{"enabled": true, "debug_logging": true}' > ~/.claude/plan-export.config.json
tail -f ~/.claude/plan-export-debug.log

# Check if plan was tracked:
python3 -c "import shelve; db=shelve.open('/Users/athundt/.claude/sessions/plugin___plan_export__.db'); print('active_plans:', dict(db.get('active_plans', {}))); print('tracking:', dict(db.get('tracking', {}))); db.close()"

# Check current config:
cat ~/.claude/plan-export.config.json 2>/dev/null || echo "Using defaults"

# Check recent plan files:
ls -lat ~/.claude/plans/*.md | head -5

# Check notes/ for exported plans:
ls -lat notes/*.md 2>/dev/null | head -5

# Check rejected plans:
ls -lat notes/rejected/*.md 2>/dev/null | head -5

# Verify hooks are firing (watch in separate terminal):
tail -f ~/.claude/plan-export-debug.log

# Force daemon restart (clears any stuck state):
pkill -f "clautorun.daemon"; sleep 2; pgrep -fl clautorun
```

### Recent Commits (Context)

```bash
# From git log (this session):
c0e4367 fix(core): replace format_map with str.replace() in format_suggestion(); add lru_cache and 5 stability tests
a6d6fd7 docs(core,config): document three tool naming layers as of Claude Code CLI v2.1.47
4914ca5 feat(core): add CLI_TOOL_NAMES dispatch table and format_suggestion() for platform-aware tool name substitution
99c5ea9 fix(hooks): pass explicit --cli arg so shared daemon handles Gemini+Claude simultaneously
fb21b68 test: add TDD tests for CLI-specific event name mapping
```

**What changed recently**: Platform-aware tool name substitution (`format_suggestion()`) was just implemented (commits c0e4367, a6d6fd7, 4914ca5). All suggestion strings in `config.py` now use `{grep}`, `{glob}`, `{read}`, etc. placeholders that resolve to the correct tool name per CLI.

### Known Bugs & Workarounds

**Claude Code Bug #4669** (upstream, not our bug):
- **What**: Option 1 ("Continue with fresh context") on plan accept does NOT fire PostToolUse hook
- **Impact**: Plan never exported on Option 1 accept
- **Workaround**: SessionStart recovery via `recover_unexported_plans()` scans for unexported plans
- **Code**: `plan_export.py:956-1072`

**Gemini BeforeTool missing exit_plan_mode**:
- **What**: `hooks.json:27` BeforeTool matcher is `"write_file|run_shell_command|replace|read_file|glob|grep_search"` — no `exit_plan_mode`
- **Impact**: PreToolUse backup (`track_and_export_plans_early`) doesn't fire for Gemini ExitPlanMode
- **Workaround**: AfterTool still fires (primary path works)
- **Fix**: Add `|exit_plan_mode` to BeforeTool matcher

**Plan export regression (current)** — suspected causes:
1. `tool_result.filePath` missing from ExitPlanMode response (Claude Code version change)
2. Stale content-hash in tracking DB
3. `config.enabled: false` in user config
4. Silent exception in `export_on_exit_plan_mode()`
5. `project_dir` mismatch (plan tracked under different cwd)
6. `record_write()` not called (plan created via path that bypasses hooks)

**Diagnosis procedure** (from `plan_export.py:18-102` docstring):
1. Enable `debug_logging: true` in `~/.claude/plan-export.config.json`
2. Accept a plan, watch `~/.claude/plan-export-debug.log`
3. Check if hook fired: search log for "export_on_exit_plan_mode"
4. Check if plan found: search log for "get_current_plan" → what was returned?
5. Check tracking state: `python3 -c "import shelve; db=shelve.open('~/.claude/sessions/plugin___plan_export__.db'); print(dict(db.get('active_plans', {}))); db.close()"`

---

## Summary of Work Required

**Primary deliverables** (PLANNING PHASE - not executing yet):
1. ✅ **Capability audit** — COMPLETE (13 capability areas, all documented with test commands)
2. ✅ **Test coverage audit** — COMPLETE (99 test files, 95% coverage, gaps identified)
3. ✅ **Plan export regression analysis** — COMPLETE (6 likely causes identified, debug procedure documented)
4. ✅ **Manual test procedures defined** — COMPLETE (each capability has concrete test steps)
5. ✅ **Automated test expansion plan defined** — COMPLETE (see section below)

---

## TESTING WORKFLOW: Spawn Separate Claude Session via Tmux

**Chicken-and-egg problem** (per Message 17):
- This Claude session is RUNNING ON clautorun hooks
- Testing clautorun fixes in THIS session is unreliable (testing the system running you)
- Solution: Spawn SEPARATE Claude Code session in tmux/byobu for testing

**Workflow**:
1. Make code fixes in THIS session (safe - planning and editing only)
2. Spawn separate Claude session in tmux (fresh environment)
3. Install updated clautorun in separate session
4. Execute manual tests in separate session
5. Capture results back to THIS session
6. Analyze results, iterate on fixes

**Tmux commands** (from `/Users/athundt/.claude/clautorun/plugins/clautorun/skills/tmux-automation/SKILL.md`):
```bash
# Create isolated session (use byobu for F-key shortcuts)
byobu new-session -d -s clautorun-test

# Navigate to test environment
byobu send-keys -t clautorun-test "cd /tmp/clautorun-audit-2026-02-18"
byobu send-keys -t clautorun-test C-m

# Start Claude Code
byobu send-keys -t clautorun-test "claude"
byobu send-keys -t clautorun-test C-m
sleep 5  # Wait for Claude startup

# Switch to haiku model (per Message 19)
byobu send-keys -t clautorun-test "/model haiku"
byobu send-keys -t clautorun-test C-m
sleep 2

# Install updated clautorun (from git repo)
byobu send-keys -t clautorun-test "cd /Users/athundt/.claude/clautorun && uv run --project plugins/clautorun python -m clautorun --install --force"
byobu send-keys -t clautorun-test C-m
sleep 30  # UV tool install can take 1-2 minutes

# Verify installation
byobu send-keys -t clautorun-test "/test-clautorun"
byobu send-keys -t clautorun-test C-m

# Capture output
byobu capture-pane -t clautorun-test -p -S -30 > /tmp/test-session-output.txt
```

**CRITICAL syntax** (from SKILL.md):
- Commands in quotes: `byobu send-keys -t session "command text"`
- Control sequences separate: `byobu send-keys -t session C-m` (Enter key)
- NEVER: `byobu send-keys -t session "command C-m"` (WRONG)
- ALWAYS: Type command → separate C-m for Enter

---

## CRITICAL SAFETY CONSTRAINTS

**ALL testing must be done safely**:
- ✅ Use `/tmp/clautorun-audit-{date}/` for ALL test files (isolated, auto-cleaned on reboot)
- ✅ Initialize temp git repo in `/tmp/clautorun-audit-git/` for git command tests
- ✅ NEVER test destructive commands on real repo or real files
- ✅ Copy clautorun source to tmp for testing if needed (read-only operations on real source)
- ❌ DO NOT run `rm`, `git reset --hard`, `git clean -f` on real directories
- ❌ DO NOT modify real plan files during testing
- ❌ DO NOT pollute notes/ with test data

**Test data locations** (all in /tmp/):
```bash
/tmp/clautorun-audit-2026-02-18/           # Root test directory
/tmp/clautorun-audit-2026-02-18/files/     # Test files for rm/cat/grep/sed tests
/tmp/clautorun-audit-2026-02-18/git-test/  # Temp git repo for git command tests
```

---

## EXECUTION TASKS (Numbered, Sequential)

**Semantic Reference Format** (per Message 16):
- Line numbers may shift during editing → always include semantic context
- Format: `file.py:function_name():lines X-Y` or `file.py:ClassName.method():lines X-Y`
- For config/dicts: `file.py:DICT_NAME` dict:line or `file.py:CONFIG["key"]`:line
- Use grep to find exact current line numbers: `grep -n "function_name" file.py`
- Bare line numbers (e.g., "line 561") are used only with search instructions (e.g., "search for 'AUTORUN_STAGE'")

### Phase 1: Setup & Environment Verification (Tasks 1-7)

**EXECUTION CONTEXT**: Tasks 1-5 run in THIS session (planner). Tasks 6+ run in SEPARATE tmux Claude session (tester).

1. **Create notes file (THIS SESSION)**
   - **Filename**: `notes/2026_02_18_capability_audit_manual_and_automated_test_execution.md`
   - **Template**: Use structure from Template section (search for "## Test Results" in this plan)
   - **Purpose**: Document ALL manual test executions, outcomes, failures, fixes, and re-tests
   - **Update frequency**: After EVERY task (real-time execution log)
   - **Execute**: Create file with initial template structure
2. **Set up isolated test environment**
   - **Create test directories**:
     ```bash
     mkdir -p /tmp/clautorun-audit-2026-02-18/{files,git-test}
     cd /tmp/clautorun-audit-2026-02-18
     ```
   - **Initialize temp git repo** (for git command tests):
     ```bash
     cd /tmp/clautorun-audit-2026-02-18/git-test
     git init
     echo "test file" > test.txt
     git add test.txt
     git commit -m "Initial commit for testing"
     cd /tmp/clautorun-audit-2026-02-18
     ```
   - **Verify isolation**: `pwd` → must be in /tmp/, NOT in real repo
   - **Document in notes**: Test environment paths created

3. **Enable plan export debug logging**
   - **Command**: `echo '{"enabled": true, "debug_logging": true, "notify_claude": true}' > ~/.claude/plan-export.config.json`
   - **Verify**: `cat ~/.claude/plan-export.config.json`
   - **Document**: Config confirmed

4. **Verify daemon running**
   - **Command**: `pgrep -fl clautorun`
   - **If not running**: `cd /Users/athundt/.claude/clautorun && clautorun --restart-daemon`
   - **Verify**: `pgrep -fl clautorun` shows process
   - **Document**: Daemon status

5. **Spawn separate Claude Code session in tmux (THIS SESSION)**
   - **Purpose**: Create isolated testing environment (separate from this planning session)
   - **Commands**:
     ```bash
     # Create session
     byobu new-session -d -s clautorun-test

     # Navigate to temp test dir
     byobu send-keys -t clautorun-test "mkdir -p /tmp/clautorun-audit-2026-02-18 && cd /tmp/clautorun-audit-2026-02-18"
     byobu send-keys -t clautorun-test C-m
     sleep 1

     # Start Claude Code
     byobu send-keys -t clautorun-test "claude"
     byobu send-keys -t clautorun-test C-m
     sleep 5

     # Switch to haiku model (per Message 19 - efficiency)
     byobu send-keys -t clautorun-test "/model haiku"
     byobu send-keys -t clautorun-test C-m
     sleep 2

     # Install clautorun from git repo
     byobu send-keys -t clautorun-test "cd /Users/athundt/.claude/clautorun && uv run --project plugins/clautorun python -m clautorun --install --force"
     byobu send-keys -t clautorun-test C-m
     sleep 30  # UV tool install takes time

     # Restart daemon
     byobu send-keys -t clautorun-test "clautorun --restart-daemon"
     byobu send-keys -t clautorun-test C-m
     sleep 3

     # Verify installation
     byobu send-keys -t clautorun-test "/test-clautorun"
     byobu send-keys -t clautorun-test C-m
     sleep 2

     # Capture verification output
     byobu capture-pane -t clautorun-test -p -S -30 > /tmp/test-session-verify.txt
     cat /tmp/test-session-verify.txt  # Review output
     ```
   - **Verification**: `/tmp/test-session-verify.txt` should show clautorun plugin active
   - **Document in notes**: Session created, Claude started, clautorun installed

6. **Send test instructions to separate session (THIS SESSION)**
   - **Purpose**: Give the testing Claude clear instructions on what to execute
   - **Command**: Create instruction file for the test session
   - **Execute**:
     ```bash
     cat > /tmp/clautorun-test-instructions.txt << 'EOF'
You are testing the clautorun plugin v0.8.0.

Execute these tests in order and report results:

1. /cr:st — verify shows AutoFile policy
2. /cr:f — switch to strict-search
3. Create /tmp/clautorun-audit-2026-02-18/files/test-policy.txt — expect BLOCKED
4. /cr:a — switch to allow-all
5. Create /tmp/clautorun-audit-2026-02-18/files/test-allow.txt — expect ALLOWED

Report: PASS or FAIL for each test
EOF

     # Send to test session
     byobu send-keys -t clautorun-test "cat /tmp/clautorun-test-instructions.txt"
     byobu send-keys -t clautorun-test C-m
     ```
   - **Document in notes**: Instructions sent to test session

7. **Baseline current state (THIS SESSION)**
   - **Commands**:
     ```bash
     ls -la notes/ > /tmp/notes-baseline-before.txt
     ls -la notes/rejected/ > /tmp/notes-rejected-baseline-before.txt 2>/dev/null || echo "rejected dir does not exist" > /tmp/notes-rejected-baseline-before.txt
     ls -la ~/.claude/plans/*.md | tail -5 > /tmp/plans-baseline.txt
     ```
   - **Run verification commands** (lines 950-970):
     ```bash
     grep -n "AUTORUN_STAGE[123]_COMPLETE" README.md  # Record count
     grep -n "stage[123]_message" plugins/clautorun/src/clautorun/config.py
     grep "BeforeTool" plugins/clautorun/hooks/hooks.json -A3
     cat ~/.claude/plan-export.config.json
     ```
   - **Document in notes**: All baseline states recorded

### Phase 2: Manual Capability Testing (Tasks 8-18)

**NOTE**: Original numbering 6-16 is preserved below for stability. With added tmux setup tasks (5-7), these are now functionally tasks 8-18.

**EXECUTION CONTEXT**: Tests 6-16 below are executed BY the separate tmux Claude session (`clautorun-test`). THIS session (planner) sends commands via `byobu send-keys` and captures results via `byobu capture-pane`.

**Tmux session lifecycle** (for tests 6-16):
```bash
# Created once in task 5: byobu new-session -d -s clautorun-test
# Reused for ALL tests 6-16 (single persistent session)
# Cleaned up in task 32 (final cleanup)

# Between tests: Session stays alive, just send new commands
# DO NOT kill/recreate between each test - wastes time and loses context
```

**Test transition pattern** (moving from test N to test N+1):
```bash
# After completing test N:
# 1. Capture results: byobu capture-pane -t clautorun-test -p -S -40 > /tmp/test-N-output.txt
# 2. Analyze in THIS session: cat /tmp/test-N-output.txt
# 3. Document in notes file: PASS/FAIL + observations
# 4. If FAIL: pause, fix bug, restart daemon in test session, re-run test N
# 5. If PASS: proceed directly to test N+1 (same session, just send next command)

# No cleanup between tests - single session runs all tests sequentially
```

**Tmux workflow pattern** (apply to ALL tests 6-16):
```bash
# Send command to test session
byobu send-keys -t clautorun-test "<command or prompt>"
byobu send-keys -t clautorun-test C-m
sleep <wait-time>  # 2-5 seconds depending on operation

# Capture output
byobu capture-pane -t clautorun-test -p -S -40 > /tmp/test-<number>-output.txt
cat /tmp/test-<number>-output.txt  # Analyze in THIS session

# Move to next test immediately (no session restart)
```

**CRITICAL: Manual testing iteration protocol**:
- Execute manual test → Document outcome in notes file
- If PASS: Continue to next test
- If FAIL:
  1. Document failure details (error message, unexpected behavior)
  2. Identify root cause (code location + why it failed)
  3. Add automated regression test for this failure
  4. Fix the bug
  5. Run automated test → verify it fails before fix, passes after fix
  6. Re-run manual test → verify now PASS
  7. Update notes file with fix applied + re-test result
  8. Continue to next test

6. **Test AutoFile policies (MANUAL)**
   - **Procedure**: See "### 1. AutoFile Policy" section (search for "AutoFile Policy" in this plan)
   - **Code references**: `plugins.py:enforce_file_policy():118-139`, `config.py:CONFIG["policy_blocked"]`
   - **Test 6.1 — strict-search blocks new files**:
     - Execute: `/cr:f`
     - Verify status: `/cr:st` shows "AutoFile policy: strict-search"
     - Attempt: "Please create /tmp/clautorun-audit-2026-02-18/files/test-policy.txt"
     - Expected: Hook blocks Write tool, outputs policy_blocked["SEARCH"] message
     - Pass criteria: File NOT created, message contains "Use the {glob} tool" (formatted for CLI)
     - If FAIL: Follow iteration protocol (document, fix, add test, re-test)
   - **Test 6.2 — justify-create blocks without tag**:
     - Execute: `/cr:j`
     - Verify: `/cr:st` shows "AutoFile policy: justify-create"
     - Attempt: "Please create /tmp/clautorun-audit-2026-02-18/files/test-justify.txt"
     - Expected: Blocked, message says <AUTOFILE_JUSTIFICATION> required
     - Pass criteria: File NOT created
     - If FAIL: Follow iteration protocol
   - **Test 6.3 — justify-create allows with tag**:
     - Attempt: "Please create /tmp/clautorun-audit-2026-02-18/files/test-justify-allowed.txt <AUTOFILE_JUSTIFICATION>testing policy</AUTOFILE_JUSTIFICATION>"
     - Expected: Allowed, file created
     - Pass criteria: File exists at `/tmp/clautorun-audit-2026-02-18/files/test-justify-allowed.txt`
     - If FAIL: Follow iteration protocol
   - **Test 6.4 — allow-all permits everything**:
     - Execute: `/cr:a`
     - Verify: `/cr:st` shows "AutoFile policy: allow-all"
     - Attempt: "Please create /tmp/clautorun-audit-2026-02-18/files/test-allow.txt"
     - Expected: Allowed, file created
     - Pass criteria: File exists
     - If FAIL: Follow iteration protocol
   - **Cross-reference README.md**: Search README for "AutoFile" → verify documented behavior matches test results
   - **Document in notes file**: PASS/FAIL for each sub-test + error messages + README accuracy

7. **Test Autorun stage markers (MANUAL)**
   - **Procedure**: See "### 2. Autorun / Autonomous Execution" section (search for "Autorun" in this plan)
   - **Code references**: `config.py:CONFIG["stage1_message"]`:184, `CONFIG["stage2_message"]`:200, `CONFIG["stage3_message"]`:215, `plugins.py:activate_autorun()`:562-581
   - **Test 7.1 — Three-stage completion**:
     - Execute: `/cr:go "Create /tmp/clautorun-audit-2026-02-18/files/hello.txt with 'hello world' content"`
     - Expected behavior:
       1. AI creates file at `/tmp/clautorun-audit-2026-02-18/files/hello.txt`
       2. AI outputs: `AUTORUN_INITIAL_TASKS_COMPLETED` (Stage 1)
       3. AI outputs: `CRITICALLY_EVALUATING_PREVIOUS_WORK_AND_CONTINUING_TASKS_AS_NEEDED` (Stage 2)
       4. AI outputs: `AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY` (Stage 3)
       5. Session terminates automatically
     - Verification after session ends:
       ```bash
       cat /tmp/clautorun-audit-2026-02-18/files/hello.txt  # Should show "hello world"
       ```
     - Pass criteria:
       - File exists and contains "hello world"
       - All 3 exact marker strings appear in AI response
       - Session ends after Stage 3
     - If FAIL: Follow iteration protocol (likely config.py stage marker mismatch)
   - **Test 7.2 — Emergency stop**:
     - Execute: `/cr:sos`
     - Expected: AI outputs `AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP` immediately
     - Pass criteria: Emergency marker appears, session stops
     - If FAIL: Follow iteration protocol
   - **Cross-reference README.md**:
     - Search README for stage marker strings
     - If README has "AUTORUN_STAGE1_COMPLETE" → document as BUG (wrong marker)
     - Verify lines 561, 565, 569, 575, 577, 579 → record which have wrong markers
   - **Document in notes file**:
     - PASS/FAIL for each test
     - Exact marker strings observed (quote verbatim)
     - README discrepancies found (line numbers)
     - Any deviations from expected behavior

8. **Test Safety Guards (rm/grep/find/cat/sed) (MANUAL)**
   - **Procedure**: See "### 3. Safety Guards (DEFAULT_INTEGRATIONS)" section (search for "Safety Guards" in this plan)
   - **Code references**: `config.py:DEFAULT_INTEGRATIONS` dict:59-175 (rm:60-68, git reset:70-75, grep:133-142, find:143-152, cat/head/tail:153-164, sed:165-175), `plugins.py:check_blocked_commands()`:446-550, `core.py:format_suggestion()`:178-215
   - **Pre-setup** (in safe /tmp/ directory):
     ```bash
     cd /tmp/clautorun-audit-2026-02-18/files
     touch rm-test-1.txt
     touch rm-test-2.txt
     echo "test content for grep" > read-test.txt
     echo "line 1\nline 2\nline 3" > read-test-multiline.txt
     ```
   - **Tests to execute**:
     - **8.1 rm blocking**: "Please run: rm /tmp/clautorun-audit-2026-02-18/files/rm-test-1.txt"
       - Pass: Blocked, suggests 'trash', file still exists
       - Verify: `ls /tmp/clautorun-audit-2026-02-18/files/rm-test-1.txt` → file exists
       - If FAIL: Follow iteration protocol
     - **8.2 rm -rf blocking**: "Please run: rm -rf /tmp/clautorun-audit-2026-02-18/files/rm-test-2.txt"
       - Pass: Blocked, suggests 'trash'
       - If FAIL: Follow iteration protocol
     - **8.3 grep blocking + tool name verification**: "Please run: grep 'test' /tmp/clautorun-audit-2026-02-18/files/read-test.txt"
       - Pass: Blocked, message says "Grep tool" (Claude Code) OR "grep_search tool" (Gemini CLI)
       - **CRITICAL**: Copy exact tool name from message → verify matches CLI
       - If tool name wrong: format_suggestion() bug → follow iteration protocol
       - If FAIL (not blocked): config.py DEFAULT_INTEGRATIONS bug → follow iteration protocol
     - **8.4 find blocking**: "Please run: find /tmp/clautorun-audit-2026-02-18 -name '*.txt'"
       - Pass: Blocked, suggests "Glob tool" (Claude) or "glob tool" (Gemini)
       - If FAIL: Follow iteration protocol
     - **8.5 cat blocking**: "Please run: cat /tmp/clautorun-audit-2026-02-18/files/read-test.txt"
       - Pass: Blocked, suggests "Read tool" (Claude) or "read_file tool" (Gemini)
       - If FAIL: Follow iteration protocol
     - **8.6 sed blocking**: "Please run: sed -i 's/test/replaced/g' /tmp/clautorun-audit-2026-02-18/files/read-test.txt"
       - Pass: Blocked, suggests "Edit tool" (Claude) or "replace tool" (Gemini)
       - Verify: File content NOT changed (sed didn't run)
       - If FAIL: Follow iteration protocol
     - **8.7 git reset --hard blocking** (in temp git repo):
       - `cd /tmp/clautorun-audit-2026-02-18/git-test`
       - Attempt: "Please run: git reset --hard HEAD~1"
       - Pass: Blocked, suggests 'git stash'
       - Verify: Repo state unchanged
       - If FAIL: Follow iteration protocol
     - **8.8 git clean -f blocking** (in temp git repo):
       - Create untracked file: `touch /tmp/clautorun-audit-2026-02-18/git-test/untracked.txt`
       - Attempt: "Please run: git clean -f"
       - Pass: Blocked, suggests 'git clean -n' (dry run)
       - Verify: untracked.txt still exists
       - If FAIL: Follow iteration protocol
   - **Cross-reference README.md**:
     - Search README for "Safety Guards" section
     - Verify: Each blocked command is documented
     - Verify: Suggested alternatives match actual suggestions seen
     - Document any discrepancies
   - **Cleanup**: `cd /tmp/clautorun-audit-2026-02-18 && rm -rf files/* git-test/untracked.txt`
   - **Document in notes file**: PASS/FAIL for each + exact tool names seen + README accuracy

9. **Test Session/Global Blocks (MANUAL)**
   - **Procedure**: See "### 4. Session / Global Pattern Overrides" section (search for "Session / Global" in this plan)
   - **Code references**: `plugins.py:_make_block_allow_handler()` factory:359-435, `plugins.py:_pattern_matches()`:243-272, `plugins.py:_parse_pattern()`:275-322, `plugins.py:_validate_regex()`:187-240
   - **Test 9.1 — Session block/allow cycle**:
     - Execute: `/cr:no clautorun-session-test-pattern`
     - Verify: `/cr:blocks` shows pattern in list
     - Attempt: "Please run: clautorun-session-test-pattern --help"
     - Expected: Blocked with session block message
     - Execute: `/cr:ok clautorun-session-test-pattern`
     - Verify: `/cr:blocks` shows pattern removed or in allow list
     - Pass criteria: Block works, allow works, status commands accurate
     - If FAIL: Follow iteration protocol
   - **Test 9.2 — Global blocks persist**:
     - Execute: `/cr:globalno clautorun-global-test-pattern`
     - Verify: `/cr:globalstatus` shows pattern
     - Execute: `/cr:globalclear`
     - Verify: `/cr:globalstatus` shows empty or list is cleared
     - Pass criteria: Global persistence works, clear works
     - If FAIL: Check `session_manager.py:session_state()` and shelve DB persistence
   - **Test 9.3 — Regex pattern**:
     - Execute: `/cr:no regex:clautorun-test-[0-9]+`
     - Verify: `/cr:blocks` shows the regex pattern
     - Attempt: "Please run: clautorun-test-42 --debug"
     - Expected: Blocked by regex match
     - Execute: `/cr:clear`
     - Pass criteria: Regex matching works, ReDoS protection active
     - If FAIL: Check `plugins.py:_validate_regex()`:187-240
   - **Test 9.4 — Glob pattern**:
     - Execute: `/cr:no glob:clautorun-test-*.tmp`
     - Verify: `/cr:blocks` shows glob pattern
     - Attempt: "Please run: touch /tmp/clautorun-audit-2026-02-18/files/test-file.tmp"
     - Expected: Blocked by glob match
     - Execute: `/cr:clear`
     - Pass criteria: Glob matching works
     - If FAIL: Check `plugins.py:_pattern_matches()`:243-272
   - **Cross-reference README.md**:
     - Search for "Session" and "Global" in README
     - Verify pattern type documentation (literal, regex, glob)
     - Document any discrepancies
   - **Document in notes**: PASS/FAIL for each sub-test + pattern matching behavior + README accuracy

10. **Test format_suggestion() platform awareness (AUTOMATED)**
    - **Test type**: Direct unit tests (existing test suite)
    - **Code references**: `core.py:format_suggestion()`:178-215, `core.py:CLI_TOOL_NAMES` dict:145-167, `core.py:get_tool_names()`:170-175
    - **Command**: `uv run pytest plugins/clautorun/tests/test_core.py::TestFormatSuggestion -v 2>&1 | tee /tmp/test-format-suggestion.log`
    - **Expected**: 21 tests pass
      - Canary tests verify exact API names per CLI
      - Substitution tests verify {grep}→Grep/grep_search replacement
      - Coverage tests verify no unreplaced placeholders remain
    - **Pass criteria**: All 21 tests PASS, no failures
    - **If FAIL**:
      - Check which test failed → identify issue
      - If canary test failed: API names changed (Claude Code update)
      - If substitution test failed: CLI_TOOL_NAMES mapping wrong
      - If coverage test failed: New placeholder added without mapping
      - Follow iteration protocol: fix → re-run test
    - **Cross-reference README.md**: Search for "format_suggestion" or "platform-aware" → verify feature documented
    - **Document in notes**: Test count + PASS/FAIL + any failures + README mention

11. **Test Plan Management commands (MANUAL)**
    - **Procedure**: See "### 6. Plan Management" section (search for "Plan Management" in this plan)
    - **Code references**: `plugins.py:_make_plan_handler()` factory:901-944, `commands/plannew.md`, `commands/planrefine.md`, `commands/planupdate.md`, `commands/planprocess.md`
    - **Test 11.1 — /cr:pn displays correct stage markers**:
      - Execute: `/cr:pn`
      - Expected: Displays plannew.md content with correct stage markers
      - Pass criteria:
        - Content shows "AUTORUN_INITIAL_TASKS_COMPLETED" (correct)
        - Content does NOT show "AUTORUN_STAGE1_COMPLETE" (wrong)
        - All 3 stage markers present and correct
      - Verify: Search displayed text for "AUTORUN_" → confirm all markers correct
      - If FAIL: `commands/plannew.md` has wrong markers → fix them
    - **Test 11.2 — /cr:pr displays planrefine.md**:
      - Execute: `/cr:pr`
      - Expected: Displays planrefine.md content (this very file's template)
      - Pass criteria: Content shows Wait Process methodology
      - If FAIL: File not found or handler broken → follow iteration protocol
    - **Test 11.3 — /cr:pu displays planupdate.md**:
      - Execute: `/cr:pu`
      - Expected: Displays plan update prompts
      - Pass criteria: Content shown
    - **Test 11.4 — /cr:pp displays planprocess.md**:
      - Execute: `/cr:pp`
      - Expected: Displays plan execution workflow
      - Pass criteria: Content shown
    - **Cross-reference README.md**:
      - Search for "Plan Management" or "/cr:pn" in README
      - Verify all 4 commands documented
      - Document any missing or inaccurate info
    - **Document in notes**: PASS/FAIL for each command + stage marker verification + README accuracy

12. **Test Plan Export (accepted plan) (MANUAL)**
    - **Procedure**: See "### 7. Plan Export" section (search for "Plan Export" in this plan)
    - **Code references**: `plan_export.py:export_on_exit_plan_mode()`:1005-1030, `plan_export.py:PlanExport.export()`:671-745, `plan_export.py:get_current_plan()`:549-576, `hooks/claude-hooks.json:PostToolUse` matcher line ~18
    - **SAFETY**: Use SEPARATE test session, NOT this plan (to avoid interfering with this plan's export)
    - **Pre-test baseline**:
      ```bash
      ls -la notes/ | wc -l > /tmp/notes-count-before-test12.txt
      ls -la notes/rejected/ | wc -l > /tmp/rejected-count-before-test12.txt 2>/dev/null || echo "0" > /tmp/rejected-count-before-test12.txt
      tail -20 ~/.claude/plan-export-debug.log > /tmp/debug-before-test12.txt 2>/dev/null || touch /tmp/debug-before-test12.txt
      ```
    - **Execute in NEW separate Claude Code session**:
      1. Open fresh Claude Code session (separate terminal or tab)
      2. `cd /Users/athundt/.claude/clautorun`
      3. Execute: `/cr:pn "Test plan export - trivial task"`
      4. AI writes trivial plan content (e.g., "Create /tmp/test-plan-export.txt")
      5. Call ExitPlanMode
      6. **IMPORTANT**: Accept using Option 2 ("Continue in this session") NOT Option 1
    - **Verification** (back in execution session):
      ```bash
      ls -la notes/ | wc -l > /tmp/notes-count-after-test12.txt
      diff /tmp/notes-count-before-test12.txt /tmp/notes-count-after-test12.txt  # Expect +1
      ls -lat notes/*.md | head -1  # Most recent should be test plan
      grep "test plan export" notes/*.md | head -1  # Find exported test plan
      tail -50 ~/.claude/plan-export-debug.log | grep "Exported plan"  # Should show export
      ```
    - **Pass criteria**:
      - notes/ count increased by 1
      - notes/rejected/ count unchanged
      - Debug log shows "Exported plan to notes/..."
      - Exported file contains test plan content
    - **If FAIL**:
      - Check: `cat ~/.claude/plan-export.config.json` → verify enabled: true
      - Check: `tail -100 ~/.claude/plan-export-debug.log` → search for errors/exceptions
      - Check shelve: `python3 -c "import shelve; db=shelve.open(os.path.expanduser('~/.claude/sessions/plugin___plan_export__.db')); print('active_plans:', dict(db.get('active_plans', {}))); print('tracking:', dict(db.get('tracking', {}))); db.close()"`
      - Identify root cause (one of 6 suspects from lines 132-138 in plan)
      - Follow iteration protocol: document cause → fix → add automated test → re-test
    - **Cross-reference README.md**: Search for "Plan Export" and "ExitPlanMode" → verify documented behavior
    - **Document in notes**: PASS/FAIL + exported filename + any failures + root cause

13. **Test Plan Export (rejected plan) (MANUAL)**
    - **Procedure**: See "### 7. Plan Export" section, rejected plan test subsection
    - **Code references**: `plan_export.py:recover_unexported_plans()`:956-1072, `plan_export.py:export_rejected_plan()`:791-831, `plan_export.py:PlanExport.export()` with `rejected=True` parameter:671-745
    - **SAFETY**: Use SEPARATE test session (do not interfere with this plan)
    - **Pre-test baseline**:
      ```bash
      ls -la notes/rejected/ | wc -l > /tmp/rejected-count-before-test13.txt 2>/dev/null || echo "0" > /tmp/rejected-count-before-test13.txt
      ```
    - **Execute in NEW separate Claude Code session**:
      1. Open fresh session
      2. `cd /Users/athundt/.claude/clautorun`
      3. Execute: `/cr:pn "Rejected plan test - will abandon"`
      4. AI writes trivial plan
      5. **DO NOT call ExitPlanMode** — just end session or switch away
      6. Wait 5 seconds
      7. Open ANOTHER new Claude Code session (triggers SessionStart hook)
    - **Expected**: SessionStart → `recover_unexported_plans()` detects abandoned plan → exports with `rejected=True` → notes/rejected/
    - **Verification** (in execution session):
      ```bash
      ls -la notes/rejected/ | wc -l > /tmp/rejected-count-after-test13.txt
      diff /tmp/rejected-count-before-test13.txt /tmp/rejected-count-after-test13.txt  # Expect +1
      ls -lat notes/rejected/*.md | head -1  # Should show rejected plan
      grep "Rejected plan test" notes/rejected/*.md | head -1  # Find it
      ```
    - **Pass criteria**:
      - notes/rejected/ count increased by 1
      - Rejected plan file contains abandoned plan content
    - **If FAIL**:
      - Check: Does `recover_unexported_plans()` actually use `rejected=True`? (Read plan_export.py:956-1072)
      - Check debug log for SessionStart execution
      - Follow iteration protocol: fix rejected=True parameter → add automated test → re-test
    - **Cross-reference README.md**: Search for "rejected plans" → verify behavior documented
    - **Document in notes**: PASS/FAIL + rejected plan filename + SessionStart recovery behavior

14. **Test Task Lifecycle (MANUAL)**
    - **Procedure**: See "### 9. Task Lifecycle Tracking" section (search for "Task Lifecycle" in this plan)
    - **Code references**: `task_lifecycle.py:register_hooks()` (grep for exact line), `task_lifecycle.py:TaskLifecycleConfig` class (grep for line range), hooks registered for TaskCreate/TaskUpdate/TaskList/TaskGet
    - **Test 14.1 — TaskCreate and /task-status**:
      - Execute: "Please use TaskCreate to create a test task with subject 'clautorun-audit-test-task' and description 'Testing task lifecycle tracking'"
      - Expected: AI uses TaskCreate tool
      - Execute: `/task-status`
      - Expected: Shows task in pending or in_progress state
      - Note task ID from output (e.g., #102)
      - Pass criteria: Task appears in status list with correct subject
      - If FAIL: Check hooks/claude-hooks.json PostToolUse matcher includes TaskCreate → follow iteration protocol
    - **Test 14.2 — /task-ignore marks task ignored**:
      - Execute: `/task-ignore <id> "test cleanup - audit completed"`
      - Expected: Task marked as ignored
      - Execute: `/task-status`
      - Expected: Task still shows but marked ignored
      - Pass criteria: Ignore functionality works
      - If FAIL: Check task_lifecycle.py task ignore logic → follow iteration protocol
    - **Cross-reference README.md**:
      - Search for "Task Lifecycle" or "/task-status"
      - Verify commands documented
      - Document if missing from quick reference table
    - **Document in notes**: PASS/FAIL + task ID used + ignore behavior + README accuracy

15. **Test Documentation Commands (MANUAL)**
    - **Procedure**: See "### 10. Documentation Commands" section
    - **Code references**: `commands/gc.md` file, `commands/ph.md` file, `plugins.py:_make_plan_handler()` reads these files:901-944
    - **Test 15.1 — /cr:gc shows commit requirements**:
      - Execute: `/cr:gc`
      - Expected: Displays git commit 17-step requirements content
      - Pass criteria: Content includes "Pre-Git Commit Analysis Process" or similar section headers
      - Verify: Content is helpful for writing commits
      - If FAIL: commands/gc.md missing or handler broken → follow iteration protocol
    - **Test 15.2 — /cr:ph shows design philosophy**:
      - Execute: `/cr:ph`
      - Expected: Displays universal design 17 principles
      - Pass criteria: Content includes "CORE PRINCIPLES" or "Automatic and Correct"
      - Verify: Content shows philosophy guidelines
      - If FAIL: commands/ph.md missing or handler broken → follow iteration protocol
    - **Cross-reference README.md**:
      - Search for "/cr:gc" and "/cr:ph" in README
      - Verify both commands documented in quick reference
      - Document if missing or misdescribed
    - **Document in notes**: PASS/FAIL for each + content verification + README accuracy

16. **Test System Commands (MANUAL)**
    - **Procedure**: See "### 11. System Commands" section
    - **Code references**: `plugins/clautorun/commands/test.md`, `plugins.py:@app.command("/cr:st")`:86-115, `plugins.py` reload handler (grep for /cr:reload)
    - **Test 16.1 — /test-clautorun verifies plugin**:
      - Execute: `/test-clautorun`
      - Expected: Confirms plugin loaded and working
      - Pass criteria: Shows version number or "clautorun plugin is active" or similar
      - If FAIL: Plugin not loaded or command handler broken → follow iteration protocol
    - **Test 16.2 — /cr:st shows current policy**:
      - Execute: `/cr:st`
      - Expected: Shows current AutoFile policy
      - Pass criteria: Displays one of: "allow-all" / "justify-create" / "strict-search"
      - Also shows any active session blocks
      - If FAIL: Status handler broken at `plugins.py:86-115` → follow iteration protocol
    - **Test 16.3 — /cr:reload reloads integrations**:
      - Execute: `/cr:reload`
      - Expected: Reports count of loaded integrations
      - Pass criteria: Shows "Reloaded N integrations" with N >= 28 (DEFAULT_INTEGRATIONS count)
      - If FAIL: Reload handler or integrations.py broken → follow iteration protocol
    - **Cross-reference README.md**:
      - Search for "System Commands" or these specific commands
      - Verify /cr:reload is documented (currently missing from quick ref table)
      - Document missing commands
    - **Document in notes**: PASS/FAIL for each + actual output + README gaps

### Phase 3: Automated Test Expansion (Tasks 17-23)

**AUTOMATED TESTING STRATEGY**:
- Every manual test failure → add automated regression test
- Every capability → both direct unit tests AND e2e integration tests
- All new tests must PASS before proceeding to Phase 4 fixes

**Test types**:
- **Direct/Unit tests**: Test single function in isolation (e.g., `format_suggestion()` with specific input)
- **E2E/Integration tests**: Test full workflow end-to-end (e.g., ExitPlanMode → export → verify file)

17. **Add README stage marker accuracy test (AUTOMATED — Direct)**
    - **File**: Create `plugins/clautorun/tests/test_readme_accuracy.py`
    - **Location**: New file in tests directory
    - **Content**:
      ```python
      from pathlib import Path

      def test_readme_stage_markers_match_config():
          """README.md must not contain wrong AUTORUN_STAGE[123]_COMPLETE markers."""
          readme_path = Path(__file__).parent.parent.parent.parent / "README.md"
          readme = readme_path.read_text()

          # README must NOT have these wrong strings
          assert "AUTORUN_STAGE1_COMPLETE" not in readme, \
              "README has wrong stage1 marker - should be AUTORUN_INITIAL_TASKS_COMPLETED"
          assert "AUTORUN_STAGE2_COMPLETE" not in readme, \
              "README has wrong stage2 marker - should be CRITICALLY_EVALUATING..."
          assert "AUTORUN_STAGE3_COMPLETE" not in readme, \
              "README has wrong stage3 marker - should be AUTORUN_ALL_TASKS_COMPLETED..."

          # README MUST have these correct strings
          from clautorun.config import CONFIG
          assert CONFIG["stage1_message"] in readme, \
              f"README missing correct stage1_message: {CONFIG['stage1_message']}"
          assert CONFIG["stage2_message"] in readme, \
              f"README missing correct stage2_message: {CONFIG['stage2_message']}"
          assert CONFIG["stage3_message"] in readme, \
              f"README missing correct stage3_message: {CONFIG['stage3_message']}"
      ```
    - **Run**: `uv run pytest plugins/clautorun/tests/test_readme_accuracy.py -v`
    - **Expected**: FAIL if README has wrong markers (confirms bug exists)
    - **Document**: Test result + line numbers where wrong markers found

18. **Add Gemini BeforeTool matcher test (AUTOMATED — Direct config validation)**
    - **File**: `plugins/clautorun/tests/test_plan_export_hooks.py` (add to existing file OR create new)
    - **Location**: Append to existing test class or create new TestGeminiHooks class
    - **Purpose**: Validate hooks/hooks.json BeforeTool matcher includes exit_plan_mode
    - **Content**:
      ```python
      import json
      from pathlib import Path

      def test_gemini_before_tool_matcher_includes_exit_plan_mode():
          """Gemini BeforeTool must include exit_plan_mode for PreToolUse backup."""
          hooks_path = Path(__file__).parent.parent / "hooks" / "hooks.json"
          hooks = json.loads(hooks_path.read_text())

          # Get BeforeTool matcher
          before_tool_matchers = hooks["hooks"]["BeforeTool"]
          assert len(before_tool_matchers) > 0, "No BeforeTool hooks registered"

          matcher = before_tool_matchers[0]["matcher"]
          assert "exit_plan_mode" in matcher, \
              f"BeforeTool matcher missing exit_plan_mode. Current: {matcher}"
      ```
    - **Run**: `uv run pytest plugins/clautorun/tests/test_plan_export_hooks.py::test_gemini_before_tool_matcher_includes_exit_plan_mode -v`
    - **Expected**: FAIL (confirms bug — exit_plan_mode is missing from hooks.json:27)
    - **Document**: Test result + current matcher string

19. **Add hook timeout validation test (AUTOMATED — Direct config validation)**
    - **File**: `plugins/clautorun/tests/test_hook_config.py` (new file)
    - **Location**: New test file for hook configuration validation
    - **Purpose**: Validate claude-hooks.json and hooks.json have adequate timeout values
    - **Content**:
      ```python
      import json
      from pathlib import Path

      class TestHookTimeouts:
          def test_claude_hooks_timeout_adequate(self):
              """claude-hooks.json timeout must be >= 5 seconds (Claude uses seconds)."""
              hooks_path = Path(__file__).parent.parent / "hooks" / "claude-hooks.json"
              hooks = json.loads(hooks_path.read_text())

              for event, handlers in hooks["hooks"].items():
                  for handler_group in handlers:
                      for hook in handler_group.get("hooks", []):
                          timeout = hook.get("timeout", 0)
                          assert timeout >= 5, \
                              f"{event} timeout {timeout}s too short (need >= 5s for daemon warmup)"

          def test_gemini_hooks_timeout_adequate(self):
              """hooks.json timeout must be >= 5000ms (Gemini uses milliseconds)."""
              hooks_path = Path(__file__).parent.parent / "hooks" / "hooks.json"
              hooks = json.loads(hooks_path.read_text())

              for event, handlers in hooks["hooks"].items():
                  for handler_group in handlers:
                      for hook in handler_group.get("hooks", []):
                          timeout = hook.get("timeout", 0)
                          assert timeout >= 5000, \
                              f"{event} timeout {timeout}ms too short (need >= 5000ms)"
      ```
    - **Run**: `uv run pytest plugins/clautorun/tests/test_hook_config.py -v`
    - **Expected**: PASS (timeout: 10 seconds for Claude, 5000ms for Gemini are both adequate)
    - **Document**: Test result confirming timeouts are correct

20. **Add plan export end-to-end test (AUTOMATED — E2E integration)**
    - **File**: `plugins/clautorun/tests/test_plan_export_e2e.py` (new file for integration test)
    - **Location**: New test file for end-to-end plan export workflows
    - **Purpose**: Test full plan export workflow: create plan → export → verify file → verify content
    - **Content**:
      ```python
      import tempfile
      from pathlib import Path
      from clautorun.plan_export import export_plan, export_rejected_plan

      class TestPlanExportE2E:
          def test_accepted_plan_exports_to_notes(self, tmp_path):
              """Full flow: create plan → export → verify in notes/"""
              plan_path = tmp_path / "test-plan.md"
              plan_path.write_text("# Test Plan\n\nTest content")
              project_dir = tmp_path / "project"
              project_dir.mkdir()

              result = export_plan(plan_path, project_dir, session_id="test-session")

              assert result["success"], f"Export failed: {result.get('error')}"
              assert "notes/" in result["destination"]
              assert Path(result["destination"]).exists()

          def test_rejected_plan_exports_to_rejected_dir(self, tmp_path):
              """Rejection flow: create plan → export as rejected → verify in notes/rejected/"""
              plan_path = tmp_path / "rejected-plan.md"
              plan_path.write_text("# Rejected Plan\n\nTest content")
              project_dir = tmp_path / "project"
              project_dir.mkdir()

              result = export_rejected_plan(plan_path, project_dir, session_id="test-session")

              assert result["success"], f"Export failed: {result.get('error')}"
              assert "rejected" in result["destination"]
              assert Path(result["destination"]).exists()

          def test_content_hash_dedup(self, tmp_path):
              """Export same plan twice → verify single export (dedup via content hash)"""
              plan_path = tmp_path / "dup-plan.md"
              plan_path.write_text("# Duplicate Plan\n\nSame content")
              project_dir = tmp_path / "project"
              project_dir.mkdir()

              result1 = export_plan(plan_path, project_dir, session_id="test-1")
              result2 = export_plan(plan_path, project_dir, session_id="test-2")

              assert result1["success"]
              assert not result2["success"], "Second export should be skipped (duplicate content hash)"
              assert "already exported" in result2.get("message", "").lower()
      ```
    - **Run**: `uv run pytest plugins/clautorun/tests/test_plan_export_e2e.py -v`
    - **Expected**: PASS (validates export logic works correctly)
    - **Document**: Test results + any failures found

21. **Add format_suggestion regression test for shell syntax (AUTOMATED — Direct unit test)**
    - **File**: `plugins/clautorun/tests/test_core.py` (add to existing TestFormatSuggestion class)
    - **Location**: Append to TestFormatSuggestion class at `test_core.py:TestFormatSuggestion` (grep for class definition)
    - **Purpose**: Prevent regression to format_map (which raised ValueError on `xargs -I{} mv {}` shell syntax)
    - **Content**:
      ```python
      def test_format_suggestion_handles_shell_braces(self):
          """format_suggestion must not raise ValueError on shell syntax like xargs -I{} mv {}"""
          from clautorun.config import DEFAULT_INTEGRATIONS
          from clautorun.core import format_suggestion

          # git clean suggestion contains shell syntax: xargs -I{} mv {}
          git_clean_msg = DEFAULT_INTEGRATIONS["git clean -f"]["suggestion"]

          # This should NOT raise ValueError (previous bug with format_map)
          try:
              result = format_suggestion(git_clean_msg, "claude")
          except ValueError as e:
              pytest.fail(f"format_suggestion raised ValueError on shell syntax: {e}")

          # Verify shell syntax preserved
          assert "xargs -I{} mv {}" in result, "Shell brace syntax was incorrectly replaced"

          # Verify template var was replaced
          assert "{glob}" not in result, "Template variable {glob} was not replaced"
          assert "Glob" in result, "Expected 'Glob' tool name for Claude CLI"
      ```
    - **Run**: `uv run pytest plugins/clautorun/tests/test_core.py::TestFormatSuggestion::test_format_suggestion_handles_shell_braces -v`
    - **Expected**: PASS (confirms str.replace() fix works, no ValueError)
    - **Document**: Test result confirming shell syntax handled correctly

22. **Run all expanded tests (AUTOMATED — Full suite baseline)**
    - **Command**: `uv run pytest plugins/clautorun/tests/ -v --tb=short 2>&1 | tee /tmp/test-results-expanded.log`
    - **Purpose**: Establish baseline - see which new tests fail (expected) before fixes applied
    - **Expected**: All existing tests pass (99 test files, ~1500+ tests)
    - **Note**: New tests may FAIL — this is expected if bugs exist
    - **Document in notes file**:
      - Total test count
      - PASS count
      - FAIL count
      - List each FAIL with test name and reason
      - Save full output to `test-results-expanded.log`

23. **Run specific new tests individually (AUTOMATED — Verify each new test)**
    - **Purpose**: Run each new test separately to confirm expected failures before fixes
    - **Test 1 (README markers)**: `uv run pytest plugins/clautorun/tests/test_readme_accuracy.py::test_readme_stage_markers_match_config -v`
      - Expected: FAIL (confirms README has wrong markers)
      - Document: Line numbers where wrong markers found
    - **Test 2**: `uv run pytest plugins/clautorun/tests/test_plan_export_hooks.py::test_gemini_before_tool_matcher_includes_exit_plan_mode -v`
      - Expected: FAIL (confirms exit_plan_mode missing from matcher)
      - Document: Current matcher string
    - **Test 3**: `uv run pytest plugins/clautorun/tests/test_hook_config.py -v`
      - Expected: PASS (confirms timeouts are adequate)
      - Document: Confirmed timeout values
    - **Test 4**: `uv run pytest plugins/clautorun/tests/test_plan_export_e2e.py -v`
      - Expected: PASS (export logic works in isolation)
      - Document: Result
    - **Test 5**: `uv run pytest plugins/clautorun/tests/test_core.py::TestFormatSuggestion::test_format_suggestion_handles_shell_braces -v`
      - Expected: PASS (str.replace() handles shell syntax)
      - Document: Result

### Phase 4: Bug Fixes (Tasks 24-32)

**NOTE**: Some task numbers appear duplicated below due to incremental editing. The CORRECT sequence is:
- 24: Skills installation bug (NEW)
- 25: Gemini BeforeTool matcher fix (exists below, may appear twice - use first occurrence)
- 26: README stage markers fix (exists below, may appear twice - use first occurrence)
- 27: Plan export regression fix
- 28: Rejected plan detection
- 29: Run full test suite
- 30: Commit fixes
- 31: Verify THIS plan exports
- 32: Final verification

24. **Investigate and fix skills installation bug (CODE FIX)**
    - **Bug**: Skill at `skills/tmux-automation/SKILL.md` not accessible via `/tmux-automation` or `/cr:tmux-automation`
    - **Root cause found**:
      - Directory name: `tmux-automation/`
      - Frontmatter name: `automated-cli-testing-sessions`
      - Expected command: `/tmux-automation` (from directory)
      - Actual working command: `/automated-cli-testing-sessions` (from frontmatter)
    - **Code reference**: `install.py:_discover_and_copy()` around line 920 (grep for "skills" and "folder")
    - **Investigation steps**:
      1. Read `install.py` skill discovery code
      2. Check how Claude Code resolves skill names (directory vs frontmatter?)
      3. Check all other skills for same mismatch:
         - `claude-session-tools/` → frontmatter name?
         - `clautorun-maintainer/` → frontmatter name?
         - `mermaid-diagrams/` → frontmatter name?
      4. Determine: Should frontmatter match directory? Or should both be supported?
    - **Fix options**:
      - Option A: Rename frontmatter `name:` to match directory (e.g., `tmux-automation`)
      - Option B: Add alias support (both names work)
      - Option C: Document actual skill name in README (no code change)
    - **Testing**:
      - Before fix: `/tmux-automation` doesn't work
      - After fix: `/tmux-automation` works
      - Verify: `/cr:tmux-automation` also works (prefix support)
    - **Document in notes**: Root cause + fix applied + verification

25. **Fix Gemini BeforeTool matcher (CODE FIX)**
    - **Condition**: If test 18 FAILED (confirms exit_plan_mode missing)
    - **File**: `plugins/clautorun/hooks/hooks.json`
    - **Location**: BeforeTool matcher at line ~27 (grep for '"BeforeTool"' to find exact line)
    - **Current matcher**:
      ```json
      "matcher": "write_file|run_shell_command|replace|read_file|glob|grep_search"
      ```
    - **Fixed matcher**:
      ```json
      "matcher": "write_file|run_shell_command|replace|read_file|glob|grep_search|exit_plan_mode"
      ```
    - **Why**: PreToolUse backup hook (`track_and_export_plans_early`) needs to fire for Gemini ExitPlanMode
    - **Verification after fix**:
      - Re-run test 18: `uv run pytest plugins/clautorun/tests/test_plan_export_hooks.py::test_gemini_before_tool_matcher_includes_exit_plan_mode -v`
      - Expected: PASS
    - **Document**: git diff showing the change

26. **Fix README.md stage markers (CODE FIX)**
    - **Condition**: If test 17 (AUTOMATED) or manual test 11 confirmed wrong markers
    - **File**: `README.md`
    - **Edits required**:
      1. Line ~561 (mermaid diagram): `AUTORUN_STAGE1_COMPLETE` → `AUTORUN_INITIAL_TASKS_COMPLETED`
      2. Line ~565 (mermaid diagram): `AUTORUN_STAGE2_COMPLETE` → `CRITICALLY_EVALUATING_PREVIOUS_WORK_AND_CONTINUING_TASKS_AS_NEEDED`
      3. Line ~569 (mermaid diagram): `AUTORUN_STAGE3_COMPLETE` → `AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY`
      4. Line ~575 (prose): Update Stage 1 description with correct marker
      5. Line ~577 (prose): Update Stage 2 description with correct marker
      6. Line ~579 (prose): Update Stage 3 description with correct marker
      7. Add `AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP` to emergency stop section (search for "/cr:sos")
    - **Add missing commands to quick reference table** (search for "Command Reference"):
      - `/cr:blocks` — Show session blocks
      - `/cr:globalstatus` — Show global blocks
      - `/cr:globalclear` — Clear global blocks
      - `/task-status` `/ts` `/tasks` — Show task states
      - `/task-ignore` `/ti` — Mark task ignored
      - `/cr:reload` — Reload integrations
    - **Verification**: `grep -n "AUTORUN_STAGE[123]_COMPLETE" README.md` → should find 0 matches
    - **Document**: Lines changed + git diff output

25. **Fix Gemini BeforeTool matcher**
    - **Condition**: If test 18 FAILED (confirms exit_plan_mode missing)
    - **File**: `plugins/clautorun/hooks/hooks.json`
    - **Line**: ~27 (in BeforeTool hooks section)
    - **Current matcher**:
      ```json
      "matcher": "write_file|run_shell_command|replace|read_file|glob|grep_search"
      ```
    - **Fixed matcher**:
      ```json
      "matcher": "write_file|run_shell_command|replace|read_file|glob|grep_search|exit_plan_mode"
      ```
    - **Why**: PreToolUse backup hook (`track_and_export_plans_early`) needs to fire for Gemini ExitPlanMode
    - **Verification after fix**:
      - Re-run test 18: `uv run pytest plugins/clautorun/tests/test_plan_export_hooks.py::test_gemini_before_tool_matcher_includes_exit_plan_mode -v`
      - Expected: PASS
    - **Document**: git diff showing the change

26. **Fix plan export regression**
    - **Condition**: If manual tests 12/13 FAILED (plan not copied to notes/)
    - **Diagnosis procedure**:
      1. Check config: `cat ~/.claude/plan-export.config.json` → verify `enabled: true`
      2. Check debug log: `tail -100 ~/.claude/plan-export-debug.log` → search for "export_on_exit_plan_mode"
      3. Check shelve state: `python3 -c "import shelve; db=shelve.open('/Users/athundt/.claude/sessions/plugin___plan_export__.db'); print('active_plans:', dict(db.get('active_plans', {}))); db.close()"`
      4. Check hook fired: grep log for "PostToolUse(ExitPlanMode)" or "AfterTool(exit_plan_mode)"
    - **Root cause identification** (one of 6 suspects from lines 96-102):
      1. `tool_result.filePath` missing → check log for get_current_plan() return value
      2. Stale content-hash → check tracking DB for incorrect "already exported" entry
      3. `config.enabled: false` → verify config file
      4. Silent exception → check log for ERROR or exception traceback
      5. `project_dir` mismatch → check log for ctx.cwd value
      6. `record_write()` not called → check if Write/Edit hooks fired for plan file
    - **Fix based on findings**:
      - If cause 1: Update get_current_plan() to handle missing filePath
      - If cause 2: Clear tracking DB or add mtime check
      - If cause 3: Set enabled: true in config
      - If cause 4: Add try/except logging, fix exception
      - If cause 5: Fix cwd detection in EventContext
      - If cause 6: Add explicit plan tracking outside Write/Edit hooks
    - **Verification after fix**:
      - Create trivial plan
      - Accept it
      - Verify: `ls -la notes/` shows new file
    - **Document**: Root cause found + fix applied + verification result

27. **Add rejected plan detection**
    - **Condition**: If manual test 13 shows gap (rejected plans not auto-exporting)
    - **Investigation**:
      1. Read `plan_export.py:671-745` → understand `export(rejected=True)` parameter
      2. Find where rejection happens → likely NOT via ExitPlanMode (that's acceptance)
      3. Check SessionStart recovery → `recover_unexported_plans()` at line 956
      4. Determine: how to distinguish "rejected" from "abandoned"?
    - **Likely solution**:
      - Modify `recover_unexported_plans()` to export unexported plans as rejected
      - Logic: if plan in active_plans but NOT in tracking → was never exported → treat as rejected
    - **Code change**:
      - **File**: `plugins/clautorun/src/clautorun/plan_export.py`
      - **Location**: `recover_unexported_plans()` method (line ~956)
      - **Change**: When exporting recovered plans, use `rejected=True` parameter
      - **Before**:
        ```python
        result = self.export(plan_path)
        ```
      - **After**:
        ```python
        result = self.export(plan_path, rejected=True)
        ```
    - **Verification after fix**:
      1. Create a plan (enter plan mode)
      2. Abandon it (exit plan mode WITHOUT accepting)
      3. Start new Claude Code session (triggers SessionStart)
      4. Verify: `ls -la notes/rejected/` shows the abandoned plan
    - **Document**: Code change + verification result

28. **Run full test suite after all fixes**
    - **Command**: `uv run pytest plugins/clautorun/tests/ -v --tb=short 2>&1 | tee test-results-final.log`
    - **Expected**: All tests PASS (including new tests added in tasks 17-21)
    - **Verification**:
      - Test 17 (README markers): should now PASS (README fixed)
      - Test 18 (Gemini matcher): should now PASS (hooks.json fixed)
      - Test 19 (hook timeouts): should PASS (timeouts are adequate)
      - Test 20 (plan export e2e): should PASS (export logic works)
      - Test 21 (shell syntax): should PASS (str.replace handles it)
      - All original tests: should still PASS (no regressions)
    - **Document in notes file**:
      - Total test count
      - PASS count (expect ~1500+)
      - FAIL count (expect 0)
      - If any FAIL: list test names + reasons
      - Compare to baseline from task 22 → verify no new regressions
    - **Save**: `test-results-final.log` for reference

### Phase 5: Commit & Verify (Tasks 30-32)

30. **Commit all fixes** — Follow `/cr:gc` 17-step requirements
    - **Files to stage** (list ALL files modified):
      ```bash
      git add README.md
      git add plugins/clautorun/hooks/hooks.json
      git add plugins/clautorun/src/clautorun/plan_export.py  # If modified
      git add plugins/clautorun/tests/test_readme_accuracy.py
      git add plugins/clautorun/tests/test_plan_export_hooks.py
      git add plugins/clautorun/tests/test_hook_config.py
      git add plugins/clautorun/tests/test_plan_export_e2e.py
      git add plugins/clautorun/tests/test_core.py  # If modified
      ```
    - **Pre-commit verification**:
      ```bash
      git status  # Verify staging
      git diff --staged  # Review all changes
      git log -5  # Check commit style
      ```
    - **Commit message structure** (follow conventional commits):
      ```
      fix(docs,hooks,tests): correct README stage markers and add regression tests

      PROBLEM:
      - README.md documented wrong stage markers (AUTORUN_STAGE[123]_COMPLETE)
        at lines 561,565,569,575,577,579
      - Users following README output wrong markers → autorun never completes
      - Gemini BeforeTool matcher missing exit_plan_mode (hooks.json:27)
      - No tests validating README accuracy or hook configuration

      CHANGES:
      1. README.md: Replace 6 wrong stage marker strings with correct ones from config.py
         - AUTORUN_STAGE1_COMPLETE → AUTORUN_INITIAL_TASKS_COMPLETED
         - AUTORUN_STAGE2_COMPLETE → CRITICALLY_EVALUATING_PREVIOUS_WORK_AND_CONTINUING_TASKS_AS_NEEDED
         - AUTORUN_STAGE3_COMPLETE → AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY
         - Added AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP to emergency section
         - Added missing commands to quick reference: /cr:blocks, /cr:globalstatus, etc.

      2. hooks/hooks.json: Add exit_plan_mode to Gemini BeforeTool matcher (line 27)
         - Enables PreToolUse backup hook for Gemini ExitPlanMode

      3. plan_export.py: [If modified] Fix rejected plan detection
         - recover_unexported_plans() now uses rejected=True for abandoned plans

      TESTS ADDED:
      - test_readme_accuracy.py: Validates README stage markers match config.py
      - test_plan_export_hooks.py: Validates Gemini BeforeTool includes exit_plan_mode
      - test_hook_config.py: Validates hook timeouts are adequate
      - test_plan_export_e2e.py: E2E tests for plan export workflows
      - test_core.py: Shell syntax handling in format_suggestion()

      VERIFICATION:
      - All tests pass: [paste test count]
      - Manual tests confirmed: AutoFile policies, safety guards, plan export
      - README accuracy test now passes
      - Gemini hook test now passes

      Files: README.md, hooks/hooks.json, [plan_export.py if changed], 5 test files
      ```
    - **Execute commit**:
      ```bash
      git commit  # Opens editor with message above
      # OR
      git commit -F /tmp/commit-message.txt  # If message saved to file
      ```
    - **Post-commit verification**:
      ```bash
      git log -1  # Verify commit created
      git show HEAD  # Review commit content
      ```
    - **Document in notes file**:
      - Commit hash (from `git log -1 --oneline`)
      - Files changed count
      - Insertions/deletions count

30. **Verify plan export works on THIS plan** — Critical end-to-end regression check
    - **Pre-test state**:
      - Record current notes/ directory: `ls -la notes/ > /tmp/notes-before.txt`
      - Record plan file: `ls -la ~/.claude/plans/dazzling-foraging-gray.md`
    - **Execute**:
      - Call ExitPlanMode to signal plan approval
      - User accepts this plan (IMPORTANT: use Option 2 "Continue in this session" NOT Option 1)
    - **Expected outcome**:
      - PostToolUse(ExitPlanMode) hook fires
      - `export_on_exit_plan_mode()` executes
      - Plan exported to `notes/{datetime}_audit-clautorun-capabilities-vs-readme-md-plan-export-regression.md`
      - Debug log shows: "Exported plan to notes/..."
    - **Verification**:
      ```bash
      ls -la notes/ | diff /tmp/notes-before.txt -  # Should show 1 new file
      ls -la notes/*.md | tail -1  # Should show this plan's export
      cat notes/<exported-file>.md | head -20  # Verify content matches this plan
      grep "AUTORUN_INITIAL_TASKS_COMPLETED" notes/<exported-file>.md  # Verify correct markers
      ```
    - **If export FAILS**:
      - Check debug log: `tail -50 ~/.claude/plan-export-debug.log`
      - Check shelve state: active_plans and tracking dicts
      - Identify root cause (one of 6 suspects)
      - Fix and re-test
    - **Document in notes file**:
      - PASS/FAIL
      - Exported filename (if success)
      - Root cause (if failure)
      - Timestamp of export

31. **Cleanup tmux test session**
    - **Purpose**: Clean up the separate Claude session (keep /tmp/ data for analysis)
    - **Commands**:
      ```bash
      # Gracefully exit Claude in test session
      byobu send-keys -t clautorun-test "/exit"
      byobu send-keys -t clautorun-test C-m
      sleep 2

      # Kill the session
      byobu kill-session -t clautorun-test

      # Verify cleaned up
      byobu list-sessions | grep clautorun-test || echo "Session successfully terminated"

      # DO NOT delete temp test directory - keep for analysis:
      # /tmp/clautorun-audit-2026-02-18/ — preserved for review
      # /tmp/test-*-output.txt — preserved for review
      # User can delete manually when done analyzing
      ```
    - **Verification**: `byobu list-sessions` should NOT show `clautorun-test`
    - **Preserved for analysis**:
      - `/tmp/clautorun-audit-2026-02-18/` — test files and git repo
      - `/tmp/test-*-output.txt` — captured outputs from all tests
      - `/tmp/notes-*.txt` — baseline snapshots
    - **Document in notes**: Tmux session terminated, /tmp/ data preserved for analysis

32. **Final verification and summary**
    - **Run all verification commands**:
      ```bash
      # 1. Verify README fixed
      grep -n "AUTORUN_STAGE[123]_COMPLETE" README.md  # Expect 0 matches
      grep -n "AUTORUN_INITIAL_TASKS_COMPLETED" README.md  # Expect 3+ matches

      # 2. Verify Gemini hooks fixed
      grep "BeforeTool" plugins/clautorun/hooks/hooks.json -A3  # Expect exit_plan_mode in matcher

      # 3. Verify all tests pass
      uv run pytest plugins/clautorun/tests/ --tb=short -q  # Quick summary

      # 4. Verify plan export config
      cat ~/.claude/plan-export.config.json

      # 5. Verify notes/ has exported plans
      ls -la notes/*.md | tail -5
      ```
    - **Create final summary in notes file**:
      - Total capabilities tested: 11 areas
      - Manual tests executed: 16 tests
      - Automated tests added: 5 new tests
      - Bugs fixed: list with file:line references
      - All tests passing: YES/NO
      - Plan export working: YES/NO
      - Remaining issues: list or "None"
    - **Document**: Final state of all systems

---

**Critical bugs found**:
- 🔴 **README.md stage markers wrong** (6 locations) — users will output wrong markers → autorun never completes
- 🟡 **Gemini BeforeTool missing exit_plan_mode** — PreToolUse backup doesn't fire (AfterTool still works)
- 🟡 **Rejected plan detection** — code path exists but never called
- 🔴 **Plan export regression** (current) — plans not copying to notes/ on acceptance (6 suspected causes)
- 🟡 **Skills not registering** — Directory name `tmux-automation` doesn't match frontmatter `name: automated-cli-testing-sessions` → command `/tmux-automation` doesn't work

**Files to modify**:
1. `README.md` — fix stage marker strings (lines 561,565,569,575,577,579)
2. `hooks/hooks.json` — add `exit_plan_mode` to Gemini BeforeTool matcher (line 27)
3. `plan_export.py` — add rejected plan detection call path (investigate)
4. `notes/{YYYY}_{MM}_{DD}_{HH}{mm}_capability_audit_findings.md` — CREATE (execution notes)
5. `plugins/clautorun/tests/test_readme_accuracy.py` — CREATE (new test)

## Plan Execution Steps

When execution begins with fresh context:

1. **Read this plan completely** — all context is self-contained here
2. **Verify current state** — run commands from "Verification" section below
3. **Enable debug logging** — `echo '{"enabled": true, "debug_logging": true}' > ~/.claude/plan-export.config.json`
4. **Execute manual tests** — follow test procedures in each capability section
5. **Maintain notes file** — use template at lines 619-688, create at `notes/{datetime}_capability_audit_findings.md`
6. **Fix bugs found** — prioritize: README stage markers → Gemini BeforeTool → rejected plan detection
7. **Run automated tests** — verify no regressions: `uv run pytest plugins/clautorun/tests/ -v`
8. **Commit fixes** — follow `/cr:gc` 17-step requirements
9. **Verify plan export** — accept THIS plan and confirm it copies to notes/

---

## Verification

```bash
# 1. Stage marker discrepancy
grep -n "AUTORUN_STAGE[123]_COMPLETE" README.md    # Should find 0 after fix
grep -n "stage[123]_message" plugins/clautorun/src/clautorun/config.py

# 2. Hook timeout
grep -n "timeout" plugins/clautorun/hooks/claude-hooks.json
grep -n "timeout" plugins/clautorun/hooks/hooks.json

# 3. Gemini BeforeTool matcher
grep "BeforeTool" plugins/clautorun/hooks/hooks.json -A3

# 4. Run automated tests
uv run pytest plugins/clautorun/tests/test_core.py::TestFormatSuggestion -v
uv run pytest plugins/clautorun/tests/ -v --tb=short

# 5. Time hook execution (regression check)
time uv run --quiet --project plugins/clautorun python plugins/clautorun/hooks/hook_entry.py --cli claude <<< '{}'

# 6. Check plan export config
/cr:pe

# 7. Check notes/ for prior exports
ls -la notes/
ls -la notes/rejected/ 2>/dev/null || echo "rejected dir does not exist"

# 8. Install and verify in session
/test-clautorun
/cr:st
```
