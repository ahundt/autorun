# autorun

[![Python Version](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-Apache%20v2-green.svg)](LICENSE)

Left alone, an AI coding agent stalls mid-task, calls a first draft done, publishes unfinished code under your name, and destroys data.

autorun redirects risky commands to safe alternatives and keeps agents running until every task is finished and verified. You allow risky commands like `/ar:ok 'git push'` once, N times, for a set time, or permanently.

autorun supports nine [agents](#supported-agents): Claude Code, Codex CLI, Qwen Code, Google Antigravity, Pi, Prime Agent, OpenCode, ForgeCode, and Gemini CLI.

autorun also installs [skills](#skills) to plan before building, keep commit messages reviewable, shorten prose without losing facts, and carry long, correction-heavy work to a verified definition of done.

## Why autorun

| autorun... | When your agent... | You type |
|---|---|---|
| keeps agents running until every task is verified | would stall a long run mid-task, waiting for "continue" | nothing, it auto-runs; `/ar:tasks` lists open tasks if you want them |
| redirects dangerous commands to safe alternatives | attempts to destroy data for good, even the whole machine (`rm -rf`, `git reset --hard`, `git clean`) | `/ar:ok 'git reset HEAD~'` gives permission once, then relocks; the agent undoes a commit and keeps your files |
| holds pushes, PRs, and version bumps until you allow them | attempts to publish unfinished code under your name without permission (push, PR, version bump) | `/ar:ok 'git push'` gives permission once, then relocks; `/ar:ok 'git push' 3` for three pushes; `/ar:ok 'git push' 1h` for an hour |
| loads skills that write and critique a checkable plan | would start a large change with no plan, so wrong turns cost rework | `/ar:plannew` writes a plan, `/ar:planrefine` critiques it |
| loads the commit-message and design-principles skills | would write vague commits nobody can review, or design without stated principles | `/ar:commit` guides the commit message, `/ar:philosophy` applies design principles |
| runs implement, evaluate, and verify stages before the agent finishes | would hand you a first draft as finished work | `/ar:go <task>` starts the three stages |
| limits the agent to existing files, or justified new ones | would bury real changes in scratch files you never asked for | `/ar:file existing-only` or `/ar:file justify` sets the file policy |

Commands use Claude Code's spelling; [Quick Start](#quick-start) lists each agent's.

![How autorun is used: install it, give the agent a task, and it implements, evaluates and verifies while guards and file policies apply](autorun-architecture.svg)

## Quick Start

```bash
uv tool install autorun-ai   # installs the `autorun` command
autorun --install            # adds hooks, commands and skills to every agent it finds
# "autorun: command not found": uv's bin directory is not on PATH yet;
# run `uv tool update-shell` and open a new terminal.
```

This needs [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.10 or later. Start a new agent session afterwards, because agents read hooks when a session starts.

### See a guard work

1. Ask the agent to run `git push`. autorun blocks the command and tells the agent to keep working locally until you allow it.
2. To allow a single push, type the allow command for your agent. Add `3` to allow three pushes or `1h` to allow pushes for an hour:

| Agent | Type |
|---|---|
| Claude Code, Google Antigravity, Qwen Code, Gemini CLI | `/ar:ok 'git push'` |
| Codex CLI | `ar:ok 'git push'` |
| Pi, Prime Agent below 0.10 | `/ar ok 'git push'` |
| OpenCode | `ar:ok 'git push'` |

Deleting files is guarded the same way: `rm` is blocked with a pointer to `trash`, and the block message gives the command that allows it.

The rest of this README writes commands the Claude Code way, for example `/ar:status`. Codex drops the slash (`ar:status`). Pi and Prime Agent use a space (`/ar status`). OpenCode uses a hyphen (`/ar-status`), for `status`, `go`, `file`, `commit` and `philosophy` only.

ForgeCode and Prime Agent 0.10+ get skills and guidance without blocking, so start at [Then try](#then-try) there.

### See it keep the agent working

1. Give the agent a job with several steps. It lists them as tasks, and autorun tracks each one.
2. When the agent tries to stop with a task still open, autorun refuses and names the task, and the agent goes back to it. `/ar:tasks` shows the list, and `/ar:task pause` lets the agent stop for now.

### Then try

1. Ask the agent to "make a plan for" a change you have in mind. That loads the `plannew` skill; "refine the plan" loads `planrefine`.
2. `/ar:go <task>` runs a task through implement, evaluate and verify before the agent may stop.
3. `/ar:help` lists every command in your agent's spelling, and `/ar:estop` is the emergency stop.

If step 1 of the guard check was not blocked, run `/ar:status`. It prints `AutoFile policy: allow-all` when the hooks are live; [Troubleshooting](#troubleshooting) covers the rest.

[Command guards](#command-guards) lists what is blocked and the safer command for each, and [Command spelling](#command-spelling) says which other spellings each agent passes through.

## Table of Contents

- [Skills](#skills)
- [Commands](#commands)
  - [Command spelling](#command-spelling)
  - [File policies](#file-policies)
  - [Command guards](#command-guards)
  - [Autonomous runs](#autonomous-runs)
  - [Plans](#plans)
  - [Task tracking](#task-tracking)
  - [Other commands](#other-commands)
  - [Renamed commands](#renamed-commands)
- [Keep sessions alive with byobu](#keep-sessions-alive-with-byobu)
- [Install](#install)
  - [Supported agents](#supported-agents)
  - [Codex CLI](#codex-cli) · [Pi and Prime Agent](#pi-and-prime-agent) · [OpenCode](#opencode) · [Qwen Code](#qwen-code) · [Legacy Gemini CLI](#legacy-gemini-cli)
  - [Other ways to install](#other-ways-to-install)
  - [Upgrade](#upgrade)
  - [Skill placement](#skill-placement)
- [CLI Reference](#cli-reference)
- [Troubleshooting](#troubleshooting)
- [Related tools](#related-tools)
- [Contributing](#contributing)

## Skills

A skill is a set of instructions an agent loads when your request matches it. `autorun --install` installs 19 of them, in three plugins. Skills need no hooks, so they also work in ForgeCode and in Prime Agent 0.10+, where autorun enforces nothing.

To use one, ask for the job in plain words ("write the commit message"). To name one, use the agent's skill picker: `/skills` or `$skill-name` in Codex, `/skill:<name>` in Pi. In Claude Code, the skills in the first table below are also `/ar:<name>`.

### Skills autorun's commands use

These ship in `ar`, autorun's own plugin:

| Skill | Use it to |
|-------|---------|
| `commit` | write a concrete, reviewable commit message |
| `philosophy` | apply the 17 system design principles |
| `plannew`, `planrefine`, `planupdate`, `planprocess` | write, critique, update and execute a plan |
| `tmux` | list the AI coding sessions in your tmux windows and send text to the ones you choose |

### General-purpose skills

`autorun-skills` carries general-purpose skills that need nothing from autorun ([its README](plugins/autorun-skills/README.md)). On Claude Code they are listed as `autorun-skills:<skill>`; on the other agents the name is unchanged.

| Skill | Use it to |
|-------|---------|
| `ai-skill-builder` | create and review portable Agent Skills |
| `cli-demo-recorder` | record reproducible CLI and TUI demos |
| `function-signature-and-parameter-guidance` | name and document functions, flags, parameters and config keys |
| `mermaid-diagrams` | render Mermaid diagrams |
| `streamline-text` | shorten prose without losing facts |
| `synthesize-project-requirements` | recover cumulative requirements, corrections, and gotchas into an evidence-backed handoff |
| `task-lifecycle-workflow` | coordinate long, correction-heavy work from evidence through readiness and recovery |
| `test-fixes-in-parallel` | test several fixes for one ambiguous failure side by side with parallel subagents |
| `tmux-automation` | test terminal programs in isolated tmux sessions |
| `write-decision-ready-technical-reports` | turn investigations and plans into an actionable technical decision brief |
| `write-durable-goal-prompts` | write long-running goals that stay valid as project state changes |

To install only `ar`, run `autorun --install ar`. If you keep your own copies of the general-purpose skills, remove them with `autorun --uninstall autorun-skills`.

### PDF extraction

`pdf-extractor` adds the `pdf-extractor` skill, which extracts text and data from PDFs, trying backends in turn.

### Choosing between the plan skills and `task-lifecycle-workflow`

Use `plannew`, `planrefine`, `planupdate`, or `planprocess` for one explicit planning operation. `task-lifecycle-workflow` is the larger coordinator for long, correction-heavy work that needs evidence joins, recovery, cold review, and a readiness or release gate.

### Listing and placing skills

`autorun --capability-snapshot` prints the full inventory as JSON. [Skill placement](#skill-placement) covers which folder each agent's copy goes to and how to keep a skill from being listed twice.

## Commands

`ar` is the command prefix, and each command has one name, such as `/ar:status`. `/ar:help` lists every command with its description, `/ar:help <command>` shows one command's arguments, and typing `ar` alone opens the same list. Older names still work where the agent passes the text to autorun; see [Renamed commands](#renamed-commands).

### Command spelling

Find the spelling your agent accepts. The grammar is `ar:<command> [arguments]` everywhere; agents differ in which spellings they pass to autorun:

| You type | Claude Code, Antigravity, Qwen Code | Pi, Prime Agent | Codex CLI | ForgeCode, OpenCode |
|---|---|---|---|---|
| `/ar:status` | runs, and appears in the slash menu | runs | never arrives | never arrives |
| `ar:status`, `ar status`, `ar-status` | runs | runs | runs | never arrives |
| `/ar-status` | never arrives | runs | never arrives | runs, for the installed files named below |
| `ar:task-status`, `ar:task-ignore`, under any prefix above | runs as `ar:task status` and `ar:task ignore` | same | same | never arrives |

"Never arrives" means the agent handles the text itself and it never reaches autorun. Codex keeps its slash menu closed; Claude Code and Qwen Code answer with their own "Unknown skill" or "Unknown command" error; ForgeCode sends autorun no events. On ForgeCode and OpenCode the installed files `ar-go`, `ar-status`, `ar-file`, `ar-commit` and `ar-philosophy` are the commands.

autorun's replies use each agent's spelling: `/ar:` on Claude Code, Gemini CLI, Qwen Code and Antigravity, `/ar ` on Pi, `ar:` on Codex, and `/ar-` on ForgeCode and OpenCode. Examples below use the Claude spelling.

### File policies

Tighten the file policy when an agent fills the repository with experiments. Every session starts at allow-all; `/ar:status` reports it as the AutoFile policy.

| Command | Policy | Effect |
|---|---|---|
| `/ar:file allow` | allow-all | create files freely (default) |
| `/ar:file justify` | justify-create | search existing files first; a new file needs an `<AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION>` tag |
| `/ar:file existing-only` | strict-search | edit existing files only; the agent is told to search existing file names and contents first. `search` and `find` are accepted for this word |
| `/ar:status` | | show the policy, plus session and global guards |

```text
/ar:file existing-only
/ar:go Refactor the auth module to use the new database schema
```

### Command guards

Before a shell command runs, autorun checks it against its guards. A blocked agent is told why and what to run instead, so it can switch to the safer command. 45 guards ship enabled:

| Guarding against | Commands | The block message suggests |
|---|---|---|
| deleted files | `rm`, `rm -rf` | `trash` (macOS `brew install trash`, Linux `uv tool install trash-cli`) |
| lost uncommitted work | `git reset --hard`, `git checkout .`, `git checkout [--] <file>`, `git restore`, `git clean -f`, `git stash drop`, `git stash clear`, `git reset HEAD~` | `git stash`, a backup branch, `git clean -n` first; checkout, restore and stash guards block only when something would be lost |
| secrets staged by accident | `git add -A`, `git add .` | naming the files |
| rewritten history | `git filter-repo`, `git filter-branch`, `bfg`, `git rebase -i`, `git push --force`/`-f` | `--force-with-lease`, or your permission |
| public actions you did not ask for | `git push`, `gh pr create`/`merge`/`edit`/`comment`, `gh issue create`/`edit`/`comment`, `gh release create`, `gh repo create`/`edit` | your permission |
| unrequested releases | version bumps by command or manifest edit, publishing to a registry | your consent |
| disks and backups | `dd if=`, `mkfs`, `fdisk`, Time Machine changes through `tmutil` | backing up first |
| scripts nobody read | `curl` or `wget` piped into a shell, including `bash <(curl …)` | download, read, then run; `/ar:ok bash` allows `bash` for a single run |
| shell text tools | `sed` that edits files, `awk` except a plain field filter on a pipe (`ls -l \| awk '{print $5}'`) | the agent's own edit tool, or `sed -n` and a pipe to print |
| files deleted or overwritten by `find` | `find` with `-delete`, `-fprint`, `-fprint0`, `-fprintf` or `-fls`; a `find` that only lists is allowed | list the matches first, then `-exec trash {} +`, or `> file` to save the list; `/ar:ok find` allows `find` for a single run |
| commit quality | `git` (warning only) | the commit-message rules |

Shell reads run as written: `cat`, `head`, `tail`, `grep`, a `find` that only lists, and `echo >` have no guard.

To block one, for example `cat`, run `/ar:no cat` for this session or `/ar:no --global cat` for every session (`/ar:clear --global` removes it).

The guards catch the commands an agent writes in ordinary shell; they are not a sandbox. A command spelled to hide what it runs can get past them: the name in a variable (`R=rm; $R -rf x`), ANSI-C quoting (`$'rm' -rf x`), `env -S`, `trap`, `alias`, or syntax the shell parser cannot read. Run an agent you do not trust in a container or a virtual machine.

#### Adding, allowing and listing guards

Run these for this session, or put `--global` as the first argument for every session:

| This session | Every session | Effect |
|---|---|---|
| `/ar:no <pattern>`, `/ar:no "<pattern>" <message>` | `/ar:no --global` with the same arguments | block; a message needs a quoted, `regex:` or `glob:` pattern, or it becomes part of the pattern |
| `/ar:ok <pattern> [N\|5m\|perm]` | `/ar:ok --global <pattern> [N\|5m\|perm]` | allow `N` times, for a duration (`5m`, `2h30m`, `2d`), or `perm` (session: until it ends; global: until cleared); with no scope, a single use |
| `/ar:status` | `/ar:status` | list blocks and allows; one view shows both |
| `/ar:clear` | `/ar:clear --global` | remove all blocks and allows |

```text
/ar:no rm                                         # block, with the built-in suggestion
/ar:no "exec(" unsafe exec function: use alternatives
/ar:no regex:eval\( dangerous eval usage: blocked for security
/ar:no glob:*.tmp temporary files are not allowed in this session
/ar:no /eval\(.*assert/ no eval( followed by assert
/ar:no --global "git reset --hard" PERMANENTLY DESTRUCTIVE: use git stash instead
/ar:ok 'git push' 1h
```

A pattern is a literal substring by default; `regex:` and `glob:` change that, and `/.../` is read as a regular expression when it contains metacharacters. The first match in this order decides:

1. allows: `/ar:ok`, `/ar:ok --global`
2. session blocks (a deny overrides a warning)
3. global blocks
4. rule files: `.claude/autorun.*.local.md` in the working directory and each parent up to the repository root (outside a repository, the working directory only), nearest first, then `~/.claude/autorun.*.local.md`; for the same pattern the nearest file's rule applies
5. the built-in guards above (`DEFAULT_INTEGRATIONS` in `plugins/autorun/src/autorun/config.py`)

`/ar:reload` rereads the rule files.

#### Who can change permissions

`/ar:ok`, `/ar:no` and `/ar:clear` (each with or without `--global`), `/ar:file`, `/ar:cache`, `/ar:task pause` and `/ar:task ignore` change what the agent may do, so they take effect only when you type them.

When anything else sends one (a subagent, the model through a skill or a tool, or a turn the agent's harness writes itself), autorun changes nothing and answers "Only the user can run /ar:ok. Ask them to type it." Other commands, such as `/ar:status`, `/ar:task status` and `/ar:go`, run whoever sends them. The table lists what each agent refuses and the routes autorun cannot tell from your typing.

| Agent | You change permissions by | What else can trigger it | Refused |
|---|---|---|---|
| Claude Code | typing `/ar:ok` | none found | the model launching `ar:ok` as a skill; a prompt from inside a subagent; background-task notifications, subagent replies and messages from other sessions |
| Codex CLI | typing `ar:ok` or `ar ok` | none found | a prompt, or a command read back from the transcript, in a subagent's session |
| Qwen Code, Gemini CLI | typing the command | none found | none found |
| Pi, supported Prime Agent releases | `/ar ok`, or typing `ar:ok` or `ar ok` | `ar:ok` and `ar ok` sent over RPC or by another extension | none found |
| OpenCode 1.18.31 | typing `ar:ok` or `ar ok` in a root session | the same non-synthetic root-session `chat.message` sent by another plugin or an API client | child-session prompts, any prompt containing a synthetic part, and the model's `autorun` tool |
| Antigravity | typing the command before the first model invocation | none found | later model-authored invocations |
| ForgeCode | no way: it sends autorun no events, and its guards are advice | none | nothing reaches autorun |

How autorun classifies OpenCode prompts is in [AGENT_NOTES.md](docs/AGENT_NOTES.md#who-may-run-arok). Two routes stay open:

1. In a Codex session that sends no prompt hook, autorun reads commands back from the session's transcript, and a user-role record that begins with `ar:ok` is honored whoever wrote it.
2. Pi runs its registered `/ar` command without saying where it came from, so `/ar ok` sent over Pi's RPC cannot be told from one you typed, and it grants.

### Autonomous runs

`/ar:go <task>` starts a run that may not end at the first "done". The agent must print each stage's marker before moving on:

1. Implement, then print `AUTORUN_INITIAL_TASKS_COMPLETED`.
2. Critically evaluate the work, fix the gaps it finds, then print `CRITICALLY_EVALUATING_PREVIOUS_WORK_AND_CONTINUING_TASKS_AS_NEEDED`.
3. Verify against the requirements, then print `AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY`.

Each time the agent tries to stop before printing the marker, autorun sends it the next instruction. After 3 rechecks (`max_recheck_count`) the instruction becomes a direct demand to finish verification. Approving a plan in Claude Code's plan mode starts the same run.

| Command | Effect |
|---|---|
| `/ar:go <task>` | start a three-stage run |
| `/ar:goprocess <task>` | the same, plus a "Wait," review after every step: critique, failure modes, several fixes, pick the best |
| `/ar:stop` | stop the run |
| `/ar:estop` | stop the run and print `AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP` |

### Plans

Write, refine and carry out a plan with these commands:

| Command | Effect |
|---|---|
| `/ar:plannew` | write a plan with checkbox steps, dependencies, and how each step is verified |
| `/ar:planrefine` | critique the plan against the code and fill its gaps |
| `/ar:planupdate` | fold new requirements into the plan |
| `/ar:planprocess` | carry the plan out in a three-stage run, reviewing after each step |

```text
/ar:plannew Design a REST API with authentication and tests
/ar:planrefine
/ar:planprocess
```

Plan export saves your plans as files in your project, so they outlive the session that wrote them. It is on by default. Each plan you accept goes to `notes/`, each plan you reject to `notes/rejected/`, named `{datetime}_{name}.md`; `/ar:planexport off` stops it for this project.

| Command | Effect |
|---|---|
| `/ar:planexport` | show whether export is on and which setting decided it |
| `/ar:planexport on\|off` | pin export for this project; the pin overrides the global default |
| `/ar:planexport globalon\|globaloff` | set the default for every project |
| `/ar:planexport dir <path>` | export folder (template variables allowed) |
| `/ar:planexport pattern <template>` | file name pattern |
| `/ar:planexport <component> [on\|off\|dir <path>]` | per-component switch and folder for `accepted` and `rejected` plans; a bare name toggles it, and a component writes only when export is also on |
| `/ar:planexport reset` | restore defaults and clear project pins |

### Task tracking

While a tracked task is the agent's to act on, autorun's Stop hook lists it and sends the agent back to work. To talk something through first, run `/ar:tasks pause <reason>`. Reminders and task-based Stop enforcement pause; task status and command guards stay as they are.

| Command | Effect |
|---|---|
| `/ar:task`, `/ar:tasks` | show tasks, pause, reminder and recovery status |
| `/ar:task pause [N] [duration] [reason]` | pause for `N` Stop attempts, a duration (`s`, `m`, `h`), or both, whichever ends first. Bare `pause` lasts five minutes; a reason alone lasts until the agent prints the recovery marker autorun gives it, or you resume |
| `/ar:task resume` | resume now; `/ar:go` and `/ar:goprocess` also resume |
| `/ar:task ignore <id> [reason]` | stop one task from blocking Stop |
| `/ar:task prompts on\|off\|<N>` | turn staleness reminders on or off, or set both intervals to `N` (legacy `/ar:task on\|off\|<N>`); Stop enforcement is unaffected |
| `/ar:task prompts initial <N>`, `subsequent <N>` | set one interval for this session |
| `/ar:task prompts scope all\|user\|subagent` | which agents get reminders |
| `/ar:task recovery on\|off` | turn the stale-task marker on or off |

#### What blocks a Stop

autorun decides from recorded task state. A `pending`, `in_progress` or `delegation-returned` task blocks Stop unless it waits on someone else. The Stop message names the blocking tasks, then lists each waiting task on its own line:

1. Waiting on you: the agent prints `AUTORUN_TASK_AWAITING_USER(7): <the decision it needs>` on its own line. The task stops blocking until your next prompt. A marker with no decision text is refused.
2. Waiting on a subagent: the agent prints `AUTORUN_TASK_DELEGATED(3)`. The task blocks again when the subagent finishes, so the agent checks the result.
3. Waiting on another task: `#2 waits on #3`. Its blockers block Stop themselves if the agent can act on them.
4. Inherited from a fork: a Pi or Prime Agent `/fork` carries the parent's tasks over. The fork blocks on one only after it updates it.

#### Reminders

If the agent stops updating its tasks, autorun reminds it after 25 tool calls in a new session and every 50 calls after that. Any real task or plan update (`TaskCreate`, `TaskUpdate`, `TodoWrite`, Codex `update_plan`, or the agent's equivalent) restarts the count.

#### Stale tasks

For a task its harness no longer lists, the agent may print `AUTORUN_TASKS_CLEAR_STALE_TASK(<id>)` on its own line. For a real task that needs discussion, use `/ar:tasks pause <reason>`.

#### Loop guard

After `stop_block_max_count` consecutive main-agent Stops are blocked with no main-agent tool call between them, the next Stop passes through with a notice naming `/ar:task status`. This means the block reason is not reaching the agent, not that work is finished.

#### Settings and details

Settings live in `~/.autorun/task-lifecycle.config.json`. [docs/TASK_TRACKING.md](docs/TASK_TRACKING.md) has the settings table, state and log paths, every waiting state in full, fork behavior, and the rules behind reminders and the loop guard. The `autorun task` commands in the [CLI Reference](#cli-reference) inspect, export and clean task state.

### Other commands

| Command | Effect |
|---|---|
| `/ar:commit` | the 17-step commit process: `<files>:` or `type(scope):` subjects, concrete wording, a secrets check |
| `/ar:philosophy` | 17 system design principles, such as "Automatic and Correct" and "One Problem, One Solution" |
| `/ar:tmux` | list the AI coding sessions in your tmux panes and type text into the ones you name ([docs/TMUX.md](docs/TMUX.md), [CLI Reference](#cli-reference)) |
| `/ar:cache` | warn before a prompt re-sends an expired prompt cache (details below) |
| `/ar:restart-daemon` | restart the daemon for this install |

`/ar:cache` warns before a prompt re-sends an expired prompt cache of 100,000 tokens or more (Claude Code; Pi and Prime Agent when `age` is set):

1. `on` blocks it until the same text is sent again, `warn` (the default) only tells, and `off` stops it. Each takes `[5m|1h|perm]`.
2. `set expired-min-tokens <tokens>` sets the floor.
3. Optional tool-time thresholds: `set ratio|read|age <value>` (tokens `50k`, `.5M`; `85%`; `5m`, `2h30m`), `ok [5m|N|perm]`, `no`, and `global <subcommand>`. Details: [commands/cache.md](plugins/autorun/commands/cache.md).

### Renamed commands

Each command has one name. An old name still runs the new command wherever the agent passes the text to autorun: on Pi, Prime Agent and Gemini CLI in any spelling, and on Codex as `ar:<name>` or `ar <name>`. Claude Code and Qwen Code answer an old `/ar:` name with "Unknown command"; ForgeCode, Antigravity and OpenCode have no route for it.

| Old name | Use instead |
|---|---|
| `st`, `afst`, `blocks`, `globalstatus` | `/ar:status` |
| `a`, `allow`, `afa` | `/ar:file allow` |
| `j`, `justify`, `afj` | `/ar:file justify` |
| `f`, `find`, `afs` | `/ar:file existing-only` |
| `run`, `autorun` | `/ar:go` |
| `gp`, `proc`, `autoproc` | `/ar:goprocess` |
| `x` | `/ar:stop` |
| `sos` | `/ar:estop` |
| `pn`, `pr`, `pu`, `pp` | `/ar:plannew`, `/ar:planrefine`, `/ar:planupdate`, `/ar:planprocess` |
| `pe` | `/ar:planexport` |
| `globalok`, `globalno`, `globalclear` | `/ar:ok --global`, `/ar:no --global`, `/ar:clear --global` |
| `gc`, `ph` | the `commit` and `philosophy` skills (`/ar:commit`, `/ar:philosophy` on Claude Code) |
| `gemini`, `test`, `claude-code-plugin-help`, `tm`, `tmux`, `tt`, `ttest` | removed as commands; `/ar:tmux` is now the `tmux` skill, and the tmux test procedures are in the `tmux-automation` skill |
| unprefixed `/afs`, `/afa`, `/afj`, `/afst`, `/autorun`, `/autoproc`, `/autostop`, `/estop` | removed; use the names above |

If your own instruction files (`CLAUDE.md`, `AGENTS.md`, saved prompts) name an old command, change them to the new name.

## Keep sessions alive with byobu

An agent running in a plain terminal dies when the terminal closes or SSH drops. Inside [byobu](https://www.byobu.org/) (a tmux front end) it keeps running, and you can reattach from another machine or a phone over SSH or [Mosh](https://mosh.org/):

```bash
brew install byobu                   # Linux: sudo apt install byobu
byobu new-session -s autorun-work    # then start your agent inside it and give it /ar:go ...
# F6 detaches; the agent keeps working
byobu attach -t autorun-work         # later, from any terminal: ssh or mosh in first
```

F2 opens a window, F3 and F4 move between windows, and F9 opens the menu. More in the [byobu documentation](https://www.byobu.org/documentation). To see which sessions are running and type into them, use `autorun tmux list` and `autorun tmux send` ([docs/TMUX.md](docs/TMUX.md)).

## Install

### Supported agents

`autorun --install` sets up every agent it finds, or one agent with a flag from the table. It writes hooks, commands and skills, and turns on each agent's task tool in its settings (Claude's `~/.claude/settings.json`, Codex's `~/.codex/config.toml`, Qwen's `~/.qwen/settings.json`). The hooks talk to a background autorun daemon, which starts with the first hook and serves every session.

Preview the writes with `autorun --install-dry-run` and check them with `autorun --status`. Config keys named in this README go in `~/.autorun/autorun.config.json`; task settings have their own file, named under [Task tracking](#task-tracking).

| Agent | How autorun connects | Install only this one | Type commands as |
|---|---|---|---|
| Claude Code | native hooks | `--claude` | `/ar:status` |
| Google Antigravity | native hooks (plugin bundle) | `--antigravity` | `/ar:status` |
| Qwen Code | native hooks (extension) | `--qwen` | `/ar:status` |
| Codex CLI | native hooks | `--codex` | `ar:status` |
| Pi | in-process extension that vetoes tool calls | `--pi` | `/ar status` |
| Prime Agent | Pi extension below 0.10.0; 0.10+ gets guidance and skills without enforcement hooks | `--prime` | below 0.10: `/ar status`; 0.10+: skills only |
| OpenCode | in-process prompt, tool, Stop and compaction plugin; blocked Stop starts a new turn on 0.6.0+ | when found | `/ar-status` |
| ForgeCode | installed guidance only; ForgeCode sends autorun no events | when found | `/ar-status` |
| Gemini CLI (legacy) | native hooks; installed only when you pass the flag | `--gemini` | `/ar:status` |

Each agent below has a short summary here and more in [docs/AGENT_NOTES.md](docs/AGENT_NOTES.md).

#### Codex CLI

Hooks go in `~/.codex/hooks.json` and in a local `ar@personal` plugin. `--codex-hook-source user|plugin|both|none` picks where they live (default `user`); Codex runs a hook twice if two places declare it. If Codex asks, run `/hooks` once. [More](docs/AGENT_NOTES.md#codex-cli).

#### Pi and Prime Agent

```bash
autorun --install --pi --force    # or --prime for Prime Agent
pi
/ar status
```

A blocked tool call returns Pi's `{ block: true, reason }`. autorun checks the model's tool calls only, not a `!` shell line you type. Prime 0.10+ gets guidance and skills only. [More](docs/AGENT_NOTES.md#pi-and-prime-agent).

#### OpenCode

Stop enforcement needs OpenCode 0.6.0 or newer. With an older client a blocked Stop is a warning only and you must continue manually. [More](docs/AGENT_NOTES.md#opencode).

#### Qwen Code

`brew install qwen-code`, then `autorun --install --qwen --force`, then `qwen extensions list`. [More](docs/AGENT_NOTES.md#qwen-code).

#### Legacy Gemini CLI

autorun installs into the standalone Gemini CLI only when you pass `--gemini`. It needs v0.28.0 or later with hooks enabled in `~/.gemini/settings.json`; the settings and checks are in [TROUBLESHOOTING.md](plugins/autorun/TROUBLESHOOTING.md#gemini-cli-checklist).

```bash
autorun --install --gemini --force
autorun --status --gemini      # lists ar@1.0.0rc4
```

### Other ways to install

```bash
# From GitHub, without PyPI
uv tool install 'git+https://github.com/ahundt/autorun.git#subdirectory=plugins/autorun'
autorun --install

# Claude Code only, through its plugin marketplace
claude plugin marketplace add https://github.com/ahundt/autorun.git
claude plugin install ar@autorun
claude plugin install autorun-skills@autorun

# From a clone
git clone https://github.com/ahundt/autorun.git && cd autorun
uv tool install --editable plugins/autorun && autorun --install

# Without uv, inside the clone above
python -m pip install -e plugins/autorun && autorun --install

# PDF extraction: `extract-pdfs` is included; this extra installs its backends
uv tool install --force 'autorun-ai[pdf]' && extract-pdfs --list-backends
```

No uv yet? `curl -LsSf https://astral.sh/uv/install.sh | sh`, `brew install uv`, or on Windows `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`.

`autorun --install` from the PyPI or git package registers all three plugins, `ar`, `autorun-skills`, and `pdf-extractor`, on every detected agent; `autorun --install pdf-extractor` installs only that one. The `[pdf]` extra adds only the extraction backends.

Changing autorun itself? Development installs run in a sandbox: see [AGENTS.md](AGENTS.md).

### Upgrade

Reinstall the package with `--force`, run `autorun --install` again, then restart open agent sessions, since each reads its hooks only when it starts:

```bash
uv tool install --force autorun-ai && autorun --install
```

1. If you installed the PDF backends, write `'autorun-ai[pdf]'` instead, or the upgrade removes them.
2. If you installed only through the Claude Code marketplace, run `claude plugin marketplace update autorun && claude plugin update ar@autorun`.
3. Each release's notes give the version-pinned command.

### Skill placement

#### Choosing where skills are installed

Some agents read two skills folders, the shared `~/.agents/skills` and their own, so a skill copied into both shows up twice. `--skill-placement` picks one route per agent:

```bash
autorun --install                                     # auto (default)
autorun --install --skill-placement native            # never use ~/.agents/skills
autorun --install --skill-placement both              # shared AND native where supported
autorun --install --skill-placement native --skill-placement codex=both
```

| Mode | Effect |
|---|---|
| `auto` | One owned route per agent: plugin, extension, or package skills when supported; otherwise the agent's own global skills folder. Autorun does not write `~/.agents/skills` in this mode. |
| `native` | The same agent-owned route, chosen explicitly. |
| `both` | Shared **and** native where the agent reads both. The only mode that can list a skill twice, and the two copies can drift apart. |

The owned route per agent under `auto` and `native`:

1. Claude and Codex: their plugin packages.
2. Gemini, Qwen, and Antigravity: their extension or plugin bundles.
3. Pi and Prime: one local package containing the adapter and skills.
4. ForgeCode: `skills/` in the directory it reads (`~/forge` when it exists, else `~/.forge`).
5. OpenCode: its XDG-aware `skills/` directory, `~/.config/opencode/skills` (or the `XDG_CONFIG_HOME` equivalent).

Your cross-client `~/.agents/skills` authoring directory is left alone. The shared copy in `both` applies to Codex, legacy Gemini, Qwen Code, Pi, Prime Agent, ForgeCode, and OpenCode, because those agents also read `~/.agents/skills`.

Setting the mode:

1. A bare mode applies to every selected agent; `HARNESS=MODE` overrides one, and the flag repeats. Valid harness names are `antigravity`, `claude`, `codex`, `forgecode`, `gemini`, `opencode`, `pi`, `prime`, and `qwen`. An unknown name or mode is rejected before anything is written, with the valid list.
2. `AUTORUN_SKILL_PLACEMENT` takes the same values, space- or comma-separated (`AUTORUN_SKILL_PLACEMENT="native codex=both"`). If both are set, the flag is used. A bad variable value is ignored, while a bad flag stops the install.
3. `autorun --install-dry-run` prints the resolved mode, where it came from, and each agent's target folders.

On upgrade, autorun preserves an unowned or edited legacy copy. It refreshes a stale ownership receipt when the installed content now exactly matches current source, but never copies a divergent installed edit back into the repository or overwrites it with `--force`. Review every keep, publish, and retirement decision first with `--install-dry-run`.

#### Sharing skills with Claude Code

autorun's own skills reach Claude Code through its three plugins, with nothing to set up. A skill you write yourself in `~/.agents/skills/` is different: Claude Code reads `~/.claude/skills/` only, so it stays invisible there until you bridge it:

```bash
autorun --install --claude --claude-agents-skills link  # symlink each shared skill into ~/.claude/skills
autorun --install --claude --claude-agents-skills copy  # copy instead (Windows without Developer Mode)
autorun --install --claude --claude-agents-skills none  # default: leave ~/.claude/skills alone
```

`AUTORUN_CLAUDE_AGENTS_SKILLS` sets the mode for unattended installs, and the flag overrides it. Restart Claude Code to see new skills.

1. The bridge skips skills a plugin already provides (Claude Code would list them twice) and never replaces an existing directory.
2. It links each skill folder separately, because a symlinked `skills/` folder stops Claude Code loading user skills ([anthropics/claude-code#38051](https://github.com/anthropics/claude-code/issues/38051)). If `~/.claude/skills` is itself a symlink, the bridge writes nothing into it and does not say so.
3. `autorun --uninstall` removes only links it created. The `shared_agents_dir` and `shared_agents_skills_subdir` config keys move the shared folder for both install and uninstall.

## CLI Reference

The `autorun` command installs and inspects autorun outside an agent session. `autorun --help` prints every option.

**Install and remove:**

```bash
autorun --install                    # every embedded plugin, every detected agent
autorun --install ar --codex         # one plugin (ar; autorun is an alias), one agent
autorun --install --claude           # also --gemini, --antigravity, --qwen, --pi, --prime, --codex
autorun --install --custom-harness 'lab=qwen:qwen-lab:/path/to/config::Qwen Lab'   # repeatable
autorun --install-dry-run --codex    # preview every write
autorun --install --force            # same-version repair; backs up files detected as edited or unproven when selected
autorun --install --tool             # also run uv tool install for the CLI
autorun --uninstall                  # everything incl. the autorun-ai tool, or a comma-separated plugin list; keeps ~/.autorun
                                     # shared config files stay, emptied of autorun's entries (e.g. ~/.codex/hooks.json as {"hooks": {}})
```

**Inspect:**

```bash
autorun --status                     # what is installed where, files that differ from this version, one test run of the hook
autorun --status --gemini            # include the legacy Gemini CLI
autorun --status --custom-harness 'lab=codex:codex-lab:/path/to/config::Codex Lab'
autorun --version
autorun --capability-snapshot FILE   # platforms, commands, skills and hooks as JSON ('-' or no FILE: stdout)
```

**Maintain:**

```bash
autorun --restart-daemon             # restart the daemon for this install
autorun --restart-all-daemons        # stale or mixed-version daemons: also stops other installs' daemons, interrupting their sessions
autorun --state-status               # state backend in use, and any conversion
autorun --state-migrate              # convert JSON state to SQLite (needed before switching to SQLite) while this install's daemon is stopped
autorun --state-rollback             # export SQLite state back to daemon_state.json so state_backend can be json again
autorun --state-maintenance          # SQLite database, WAL and reclaimable bytes; deletes nothing
autorun --state-rebuild-tasks        # offline rebuild only after startup reports SQLite refused the in-place task-definition rewrite
                                     # stop every daemon using the store first; the command checks free space before copying the tasks table
autorun --update                     # check for and install an update
autorun --update-method uv           # force the update method
autorun --no-bootstrap               # hooks stop installing missing dependencies in the background
autorun --enable-bootstrap           # turn that back on
```

**File policy** (`/ar:file allow|justify|existing-only` and `/ar:status` in a session):

```bash
autorun file status                  # aliases st, s
autorun file status --session ID
autorun file allow                   # alias a
autorun file justify                 # alias j
autorun file existing-only           # aliases search, find, f
```

1. Each session starts at allow-all, and there is no global policy; `--global` says so and exits 1 with `allow`, `justify` or `search`.
2. While hooks run through the daemon (the default), the daemon owns session state, so `allow`, `justify` and `search` exit 1 and print the slash command to run in the session instead. With `AUTORUN_USE_DAEMON=0` they write the policy.
3. Without `--session`, autorun uses `$AUTORUN_SESSION_ID`, then the only active session if there is exactly one.

**Tasks:**

```bash
autorun task status --verbose
autorun task status --session ID --format json
autorun task export tasks.json --session ID --format csv --include-completed
autorun task clear --session ID
autorun task clear --all --no-confirm
autorun task gc --dry-run --ttl DAYS --pattern GLOB   # preview cleanup by age and session-ID glob
autorun task gc --no-archive --no-confirm             # delete without archive or prompt
```

`--pattern` defaults to `*` and `--ttl` to `task_ttl_days`. On the SQLite backend `task gc` expires whole sessions with no unfinished tasks, archives them before deleting, and refuses `--pattern` and `--no-archive`, which it cannot honor.

**AI sessions in tmux** (`/ar:tmux` in a session; rules in [docs/TMUX.md](docs/TMUX.md)):

```bash
autorun tmux list                          # read only: letter, pane id, target, agent, directory, status
autorun tmux list --json
autorun tmux send --to %3 -- continue      # type into the pane with id %3, then Enter
autorun tmux send --to %3,%7 -- git status
autorun tmux send --to awaiting-input -- continue   # sessions at their plain input prompt
```

`send` with no `--to` or no text types nothing and exits 1; it also exits 1 when any target was refused or failed. A Claude Code install made through the marketplace alone has no `autorun` on PATH; `commands/tmux-exec` in the plugin takes the same arguments.

**Custom agents.** `--custom-harness` takes `name=flavor:binary:config_dir[::display]`.

1. Flavors are `claude`, `gemini`, `qwen`, `antigravity`, `agy` (an alias for `antigravity`), and `codex`.
2. The `claude` flavor installs the portable Markdown commands and AGENTS.md without hooks, which suits Claude-compatible agents such as OpenCode.
3. Keep permanent targets in the `custom_harnesses` config key with the same grammar; a flag overrides a config entry of the same name.
4. Entries may share a flavor with different folders (`codex-home=codex:codex:~/.codex-home` and `codex-work=codex:codex:~/.codex-work`).
5. The `::` before the display name lets `config_dir` itself contain `:`.

**Config folders.** The `harness_config_dirs` config key (for example `{"codex": "~/.codex-work"}`) overrides the agent's own variable (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `QWEN_HOME`, `FORGE_CONFIG`), which overrides the default. The desktop apps share the CLI folders: the ChatGPT/Codex app (`com.openai.codex`) uses `~/.codex`, and Claude Desktop's local Code sessions use `~/.claude`.

**Rarely needed:**

```bash
autorun --exit2-mode auto            # Claude Code bug #4669: deny by exit code 2 + stderr instead of JSON
autorun --conductor                  # install the Conductor extension for Gemini (default)
autorun --no-conductor               # skip it
autorun --cli claude                 # which agent is calling; hooks pass this
```

`--exit2-mode` works around [anthropics/claude-code#4669](https://github.com/anthropics/claude-code/issues/4669), where Claude Code ignored a deny returned with exit code 0.

**Accepted values:**

- `--codex-hook-source: user|plugin|both|none`
- `--codex-plugin-marketplace: personal|github`
- `--claude-agents-skills: link|copy|none`
- `--skill-placement: auto|native|both` or `HARNESS=auto|native|both`, repeatable
- `--cache-install-lock-timeout-seconds: (0, 86400] seconds` (default `600`)
- `--exit2-mode: auto|always|never`
- `--cli: claude|gemini|antigravity|qwen|codex|pi|prime|opencode`
- `--update-method: auto|claude|gemini|plugin|uv|pip`
- `--format: text|json|table` for `task status`
- `--format: json|csv|markdown` for `task export`

**Keeping an install choice.** `autorun --update` and automatic reinstalls run with no flags. To keep a choice across them, put it in `autorun.config.json` under the flag's name with underscores: `{"codex_hook_source": "both", "skill_placement": {"codex": "native"}}`.

1. A flag or `AUTORUN_<NAME>` variable still wins.
2. An invalid `cache_install_lock_timeout_seconds` value is rejected with its key, value and accepted range.
3. For other install settings, an unrecognized environment or config-file value falls through to the next tier, then the default when no lower tier supplies a valid value.

## Troubleshooting

Start with the symptom table. If the daemon is busy, unreachable or late, tool hooks use a local, stateless check: ordinary shell commands and non-shell tools proceed with a notice, and the built-in destructive-command guards still deny dangerous shell commands. `/ar:no` rules, `/ar:ok` grants, the file policy, and task and plan gating wait until autorun answers again. Do not restart the daemon under load.

Set `overload_policy` in `~/.autorun/autorun.config.json`, or override it with `AUTORUN_OVERLOAD_POLICY`: `critical-guards` (default), `block` (deny every tool-gate call), or `allow` (permit every call without local guards). The full fallback rules are in [docs/TROUBLESHOOTING_DETAILS.md](docs/TROUBLESHOOTING_DETAILS.md#when-the-daemon-is-busy-or-down).

| Symptom | Fix |
|---|---|
| `autorun: command not found` after `uv tool install` | `uv tool update-shell`, then open a new terminal; `uv tool dir --bin` prints the folder. Installed hooks call autorun by full path, so they work either way. |
| Commands do nothing | `autorun --status` checks every detected agent and runs the hook command once (`ok hook command`). Hook calls are logged to `~/.autorun/hook_entry_debug.log`, the daemon to `~/.autorun/daemon.log`. Restart the agent session after any install. |
| Claude's task tools are missing or vanish | Claude Code 2.1.268 and later offer them to newer models only with `CLAUDE_CODE_ENABLE_TODO_TOOLS=1`. `autorun --install` sets it in `~/.claude/settings.json` unless you already did; start a new session. Without the tools autorun does not enforce tasks, and says so at session start. [Details and other agents](plugins/autorun/TROUBLESHOOTING.md#claude-task-tools-are-missing-or-vanished). |
| Claude Code plugin will not load | `claude --debug` shows plugin loading. Reinstall: `claude plugin uninstall ar@autorun`, then the marketplace commands in [Other ways to install](#other-ways-to-install). The installed copy is under `~/.claude/plugins/cache/autorun/ar/`. |
| Claude cache differs from this build | Run `autorun --status`. A plain `autorun --install` changes only hash-proven files; `autorun --install --force` backs up edited or unproven files, prints each backup path, and refreshes the same version directory in place. Uninstall and lock behavior: [details](docs/TROUBLESHOOTING_DETAILS.md#claude-cache-differs-from-this-build). |
| Anything else | `autorun --install --force` republishes hooks, commands and skills. If another install holds the Claude cache lock, autorun prints the cache path and waits up to `cache_install_lock_timeout_seconds` (default 600); on timeout, wait for the other install to finish and rerun. Full guide: [TROUBLESHOOTING.md](plugins/autorun/TROUBLESHOOTING.md). |

Three same-version repair limits remain in rc4:

1. A file changed after backup verification but before replacement can be replaced without a backup of that last change. Stop programs that write the cache, restore the file if needed, and rerun the install.
2. While a changed build reinstalls the cache `.venv`, a hook can briefly fail to import autorun: tool gates deny the tool call, while lifecycle events continue and show an `[autorun]` import notice. Wait for the install to finish, then retry the tool or start a new session.
3. After an interrupted refresh, a file whose bytes equal the previous autorun version is treated as untouched. Copy it elsewhere before rerunning if those bytes are a user customization.

## Related tools

A correction you give agents again and again is a candidate `/ar:no` guard or rule file. [AI Session Search](https://github.com/ahundt/ai-session-search) (`aise`) searches your past agent sessions, and its `corrections` report lists those repeats:

```bash
aise skills run corrections --when 30d --limit 50
```

## Contributing

Report bugs and request features in [GitHub Issues](https://github.com/ahundt/autorun/issues); pull requests are welcome. [AGENTS.md](AGENTS.md) has the sandboxed development install and the test commands, [plugins/autorun/AGENTS.md](plugins/autorun/AGENTS.md) the code conventions, and [INTEGRATION_GUIDE.md](plugins/autorun/docs/INTEGRATION_GUIDE.md) where to add a command, handler or guard. Hook internals: [HOOK_ARCHITECTURE.md](plugins/autorun/HOOK_ARCHITECTURE.md); state storage and locking: [developer-internals.md](docs/developer-internals.md); hook APIs: [Claude Code](docs/claude-code-hooks-api.md), [Gemini CLI](docs/gemini-cli-hooks-api.md), [Codex CLI](docs/codex-cli-hooks-api.md), [all events](docs/hooks_api_reference.md).

## License

Apache License 2.0. See [LICENSE](LICENSE).
