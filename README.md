# autorun

[![Python Version](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-Apache%20v2-green.svg)](LICENSE)

An AI coding agent left alone stops mid-task, calls a first draft done, and runs commands you would never approve. autorun adds hooks that send it back to work and block those commands.

It works with Claude Code, Codex, Qwen Code, Pi and five more agents, from one install.

## Why autorun

| When your agent... | autorun... | Try |
|---|---|---|
| stops mid-task and waits for you to type "continue" | refuses the stop while tracked tasks are open and sends the agent back to them | `/ar:tasks` |
| says "done" after a first draft | makes it implement, then critically evaluate, then verify before it may stop | `/ar:go <task>` |
| leaves scratch files all over the repository | limits it to editing existing files, or to new files it justifies | `/ar:f`, `/ar:j` |
| runs `rm -rf`, `git reset --hard`, or `curl … \| sh` | blocks the command and names the safer one: `trash`, `git stash`, download-read-run | `/ar:no`, `/ar:ok` |
| pushes, opens a PR, or bumps a version | blocks each one until you allow it | `/ar:ok 'git push'` |
| runs in a terminal you need to close | run it in byobu so it survives; `/ar:tabs` lists and steers those sessions | `/ar:tabs` |

![autorun Architecture](autorun-architecture.svg)

## Quick Start

```bash
uv tool install autorun-ai   # installs the `autorun` command
autorun --install            # adds hooks, commands and skills to every agent it finds
# "autorun: command not found": uv's bin directory is not on PATH yet;
# run `uv tool update-shell` and open a new terminal.
```

Start a new agent session (agents read hooks when a session starts), then:

```text
/ar:st                                  # prints "AutoFile policy: allow-all": hooks are live
/ar:help                                # every command, spelled the way this agent accepts it
/ar:go Build a login form with tests    # implement, evaluate, verify
/ar:f                                   # this session: edit existing files only
/ar:sos                                 # emergency stop
```

Codex hides slash commands it does not know, so type `ar:st` there. Every
agent's spelling is in [Command spelling](#command-spelling).

From now on autorun blocks commands such as `rm`, `sed -i`, and `cat` or `grep`
outside a pipe, and names the safer command or tool for each.
[Command guards](#command-guards) lists all 50; `/ar:ok <command>` allows one run.

**Upgrade** with the same install plus `--force`:

```bash
uv tool install --force autorun-ai && autorun --install
```

Write `'autorun-ai[pdf]'` instead if you installed the PDF backends, or the
upgrade removes them. Then restart open agent sessions, since each reads its
hooks only when it starts. If you installed only through the Claude Code
marketplace, run `claude plugin marketplace update autorun && claude plugin
update ar@autorun` instead. Each release's notes give the version-pinned
command.

## Table of Contents

- [Install](#install)
  - [Supported agents](#supported-agents)
  - [Other ways to install](#other-ways-to-install)
  - [Codex CLI](#codex-cli) · [Pi and Prime Agent](#pi-and-prime-agent) · [Qwen Code](#qwen-code) · [Legacy Gemini CLI](#legacy-gemini-cli)
  - [Skills](#skills)
- [Commands](#commands)
  - [Command spelling](#command-spelling)
  - [File policies](#file-policies)
  - [Command guards](#command-guards)
  - [Autonomous runs](#autonomous-runs)
  - [Plans](#plans)
  - [Task tracking](#task-tracking)
  - [Other commands](#other-commands)
- [Keep sessions alive with byobu](#keep-sessions-alive-with-byobu)
- [CLI Reference](#cli-reference)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)

## Install

### Supported agents

`autorun --install` sets up every agent it finds, or one agent with a flag below. It writes hooks, commands and skills, and turns on each agent's task tool in its settings (Claude's `~/.claude/settings.json`, Codex's `~/.codex/config.toml`, Qwen's `~/.qwen/settings.json`). The hooks talk to a background autorun daemon, which starts with the first hook and serves every session.

Preview the writes with `autorun --install-dry-run` and check them with `autorun --status`. Config keys named below go in `~/.autorun/autorun.config.json`.

| Agent | How autorun connects | Install only this one | Type commands as |
|---|---|---|---|
| Claude Code | native hooks | `--claude` | `/ar:st` |
| Google Antigravity | native hooks (plugin bundle) | `--antigravity` | `/ar:st` |
| Qwen Code | native hooks (extension) | `--qwen` | `/ar:st` |
| Codex CLI | native hooks | `--codex` | `ar:st` |
| Pi | in-process extension that vetoes tool calls | `--pi` | `/ar st` |
| Prime Agent | the Pi extension | `--prime` | `/ar st` |
| OpenCode | in-process plugin that vetoes tool calls; no prompt or Stop hooks | when found | `/ar-st` |
| ForgeCode | installed guidance only; ForgeCode sends autorun no events | when found | `/ar-st` |
| Gemini CLI (legacy) | native hooks; never installed unless you pass the flag | `--gemini` | `/ar:st` |

### Other ways to install

```bash
# From GitHub, without PyPI
uv tool install 'git+https://github.com/ahundt/autorun.git#subdirectory=plugins/autorun'
autorun --install

# Claude Code only, through its plugin marketplace
claude plugin marketplace add https://github.com/ahundt/autorun.git
claude plugin install ar@autorun

# From a clone
git clone https://github.com/ahundt/autorun.git && cd autorun
uv tool install --editable plugins/autorun && autorun --install

# Without uv
python -m pip install -e plugins/autorun && autorun --install

# PDF extraction: `extract-pdfs` always installs; its backends are this extra
uv tool install --force 'autorun-ai[pdf]' && extract-pdfs --list-backends
```

No uv yet? `curl -LsSf https://astral.sh/uv/install.sh | sh`, `brew install
uv`, or on Windows `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`.

The PyPI package carries the `ar` plugin. The `pdf-extractor` plugin's slash
commands install from the Claude Code marketplace or a clone (`autorun
--install pdf-extractor`); its extraction code is the `[pdf]` extra above.

Changing autorun itself? Development installs run in a sandbox: see
[AGENTS.md](AGENTS.md).

### Codex CLI

Codex gets hooks in `~/.codex/hooks.json` and a local plugin, `ar@personal`,
with its files in `~/plugins/ar` (where Codex keeps personal plugins) and its
entry in `~/.agents/plugins/marketplace.json`. To use the folder Codex's
documentation names instead, set the `codex_plugin_source_dir` config key to
`~/.codex/plugins`. If Codex asks, run `/hooks` once so it trusts the hooks.

Codex would run the same hook twice if two places declare it, so pick one:

```bash
autorun --install --codex --codex-hook-source user      # default: ~/.codex/hooks.json only
autorun --install --codex --codex-hook-source plugin    # hooks inside ar@personal only
autorun --install --codex --codex-hook-source both      # both, on purpose
autorun --install --codex --codex-hook-source none      # no hooks; keep skills and guidance
autorun --install --codex --codex-plugin-marketplace github   # ar@autorun from ahundt/autorun instead of ar@personal
```

`AUTORUN_CODEX_HOOK_SOURCE` sets the source for unattended installs, and a
reinstall after switching leaves no duplicate hooks behind.

In Codex, autorun maps task progress to `update_plan`, search to `rg -n` and
`rg --files`, and edits to `apply_patch`. Skills appear through `/skills`, a
`$mermaid-diagrams` mention, or the `@autorun` plugin; Codex does not turn
skills into slash commands such as `/mermaid`. Hook schema:
[docs/codex-cli-hooks-api.md](docs/codex-cli-hooks-api.md).

### Pi and Prime Agent

```bash
autorun --install --pi --force    # or --prime for Prime Agent
pi
/ar st
```

The extension in `~/.pi/agent/extensions/ar/` (`~/.prime/agent/extensions/ar/`
for Prime Agent) passes Pi's tool calls, prompts, results and session events to
the same autorun daemon the other agents use. A blocked tool call comes back as
Pi's `{ block: true, reason }`. When autorun keeps the agent from finishing
(`agent_settled`), it starts the next turn with a hidden
`autorun-continuation` message, so the transcript never shows it as something
you typed. Pi also accepts `ar:st`, `ar-st` and `/ar:st`, and loads skills with
`/skill:<name>`.

Pi gets `TaskCreate`, `TaskUpdate`, `TaskList` and `TaskGet` tools backed by
the same task tracking as Claude. `TaskUpdate` takes one `taskId` or an atomic
`taskUpdates` array, including `addBlockedBy` and `addBlocks`;
`status="deleted"` deletes.

autorun checks the tool calls the model makes. It does not check a `!` shell
line you type, or a child `pi --no-extensions`. `pi-subagents` starts a child
that way when an agent definition lists its own `extensions:` (or a capability
ceiling denies extensions), so add `~/.pi/agent/extensions/ar/index.ts` (or the
`~/.prime` path) to that list. Agents without an `extensions:` key inherit the
guard.

### Qwen Code

```bash
brew install qwen-code
autorun --install --qwen --force
qwen extensions list
```

Qwen gets the Gemini extension with its hooks set to `--cli qwen`, so Qwen
sessions get Qwen's response format.

### Legacy Gemini CLI

autorun installs into the standalone Gemini CLI only when you pass `--gemini`.
It needs v0.28.0 or later with hooks enabled in `~/.gemini/settings.json`; the
settings and checks are in
[TROUBLESHOOTING.md](plugins/autorun/TROUBLESHOOTING.md#gemini-cli-checklist).

```bash
autorun --install --gemini --force
autorun --status --gemini      # lists ar@1.0.0rc4
```

### Skills

Skills are instructions an agent loads when your request matches one. An install
adds these, among others:

| Skill | Use it to |
|-------|---------|
| `cache` | guard against prompt-cache misses and compaction |
| `ai-skill-builder` | create and review portable Agent Skills |
| `cli-demo-recorder` | record reproducible CLI and TUI demos |
| `mermaid-diagrams` | render Mermaid diagrams |
| `parallel-subagent` | investigate an ambiguous failure with parallel approaches |
| `pdf-extractor` | extract text and data from PDFs, trying backends in turn |
| `synthesize-project-requirements` | recover cumulative requirements, corrections, and gotchas into an evidence-backed handoff |
| `task-lifecycle-workflow` | coordinate long, correction-heavy work from evidence through readiness and recovery |
| `tmux-automation` | test terminal programs in isolated tmux sessions |
| `write-decision-ready-technical-reports` | turn investigations and plans into an actionable technical decision brief |
| `write-durable-goal-prompts` | write long-running goals that stay valid as project state changes |

Use `plannew`, `planrefine`, `planupdate`, or `planprocess` for one explicit
planning operation. `task-lifecycle-workflow` is the larger coordinator for
long, correction-heavy work that needs evidence joins, recovery, cold review,
and a readiness or release gate; it does not replace the focused plan commands.

`autorun --capability-snapshot` prints the full inventory as JSON. Use each
agent's own skill picker: `/skills` or `$skill-name` in Codex, `/skill:<name>`
in Pi. A skill is not an `/ar:*` command.

#### Choosing where skills are installed

Some agents read two skills folders, the shared `~/.agents/skills` and their
own. A skill copied into both shows up twice. `--skill-placement` picks one
route per agent:

```bash
autorun --install                                     # auto (default)
autorun --install --skill-placement native            # never use ~/.agents/skills
autorun --install --skill-placement both              # shared AND native where supported
autorun --install --skill-placement native --skill-placement codex=both
```

| Mode | Effect |
|---|---|
| `auto` | One owned route per agent: plugin, extension, or package skills when supported; otherwise the agent's own global skills folder. Autorun does not write `~/.agents/skills` in this mode. |
| `native` | The same harness-owned route, explicitly. ForgeCode uses `~/forge/skills`; OpenCode uses its XDG-aware `skills/` directory. |
| `both` | Shared **and** native where the agent reads both. The only mode that can list a skill twice, and the two copies can drift apart. |

The shared copy in `both` applies to Codex, legacy Gemini, Qwen Code, Pi,
Prime Agent, ForgeCode, and OpenCode because those harnesses also read
`~/.agents/skills`.

A bare mode applies to every selected agent; `HARNESS=MODE` overrides one, and
the flag repeats. Valid harness names are `antigravity`, `claude`, `codex`,
`forgecode`, `gemini`, `opencode`, `pi`, `prime`, and `qwen`. An unknown name
or mode is rejected before anything is written, with the valid list.

`AUTORUN_SKILL_PLACEMENT` takes the same values, space- or comma-separated
(`AUTORUN_SKILL_PLACEMENT="native codex=both"`). If both are set, the flag is
used. A bad variable value is ignored, while a bad flag stops the install.
`autorun --install-dry-run` prints the resolved mode, where it came from, and
each agent's target folders.

Under `auto`, Claude and Codex use their plugin packages; Gemini, Qwen, and
Antigravity use their extension/plugin bundles; Pi and Prime use one local
package containing the adapter and skills; ForgeCode uses `~/forge/skills`;
and OpenCode uses `~/.config/opencode/skills` (or its `XDG_CONFIG_HOME`
equivalent). The user's cross-client `~/.agents/skills` authoring directory is
left alone.

An upgrade preserves an unowned or edited legacy copy. Autorun refreshes a
stale ownership receipt when the installed content now exactly matches current
source, but it never copies a divergent installed edit back into the repository
or overwrites it with `--force`. Use `--install-dry-run` to review every keep,
publish, and retirement decision before applying an upgrade.

#### Sharing skills with Claude Code

Claude Code reads `~/.claude/skills/` only, so a skill you write in
`~/.agents/skills/` is invisible to it until you bridge it:

```bash
autorun --install --claude --claude-agents-skills link  # symlink each shared skill into ~/.claude/skills
autorun --install --claude --claude-agents-skills copy  # copy instead (Windows without Developer Mode)
autorun --install --claude --claude-agents-skills none  # default: leave ~/.claude/skills alone
```

`AUTORUN_CLAUDE_AGENTS_SKILLS` sets the mode for unattended installs, and the flag overrides it. The bridge skips skills a plugin already provides (Claude Code would list them twice) and never replaces an existing directory. It links each skill folder separately, because a symlinked `skills/` folder stops Claude Code loading user skills ([anthropics/claude-code#38051](https://github.com/anthropics/claude-code/issues/38051)); if `~/.claude/skills` is itself a symlink, the bridge writes nothing into it and does not say so. Restart Claude Code to see new skills.

`autorun --uninstall` removes only links it created. The `shared_agents_dir` and `shared_agents_skills_subdir` config keys move the shared folder for both install and uninstall.

## Commands

`ar` is the command prefix: short forms such as `/ar:st` for speed, long forms
such as `/ar:status` for reading. `/ar:help` lists every command with its
description, `/ar:help <command>` shows one command's arguments, and typing
`ar` alone opens the same list.

### Command spelling

The grammar is `ar:<command> [arguments]` everywhere; agents differ in which
spellings they pass to autorun:

| You type | Claude Code, Antigravity, Qwen Code | Pi, Prime Agent | Codex CLI | ForgeCode, OpenCode |
|---|---|---|---|---|
| `/ar:st` | runs, and appears in the slash menu | runs | never arrives | never arrives |
| `ar:st`, `ar st`, `ar-st` | runs | runs | runs | never arrives |
| `/ar-st` | never arrives | runs | never arrives | runs, for the installed files named below |
| `ar:task-status`, `ar:task-ignore`, under any prefix above | runs as `ar:task status` and `ar:task ignore` | same | same | never arrives |

"Never arrives" means the agent handles the text itself and it never reaches autorun. Codex keeps its slash menu closed; Claude Code and Qwen Code answer with their own "Unknown skill" or "Unknown command" error; ForgeCode sends autorun no events. On ForgeCode and OpenCode the installed files `ar-go`, `ar-st`, `ar-allow`, `ar-find`, `ar-commit` and `ar-ph` are the commands.

autorun's replies use each agent's spelling: `/ar:` on Claude Code, Gemini CLI, Qwen Code and Antigravity, `/ar ` on Pi, `ar:` on Codex, and `/ar-` on ForgeCode and OpenCode. Examples below use the Claude spelling.

### File policies

Every session starts at allow-all; `/ar:st` reports it as the AutoFile policy.
Tighten it when an agent fills the repository with experiments:

| Command | Policy | Effect |
|---|---|---|
| `/ar:a`, `/ar:allow` | allow-all | create files freely (default) |
| `/ar:j`, `/ar:justify` | justify-create | search existing files first; a new file needs an `<AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION>` tag |
| `/ar:f`, `/ar:find` | strict-search | edit existing files only; the agent is pointed at its own search tools (`Glob`/`Grep` on Claude, `glob`/`grep_search` on Gemini, `rg --files`/`rg -n` on Codex) |
| `/ar:st`, `/ar:status` | | show the policy, plus session and global guards |

```text
/ar:f
/ar:go Refactor the auth module to use the new database schema
```

### Command guards

Before a shell command runs, autorun checks it against its guards. A blocked
agent is told why and what to run instead, so it can switch to the safer
command. 50 guards ship enabled:

| Guarding against | Commands | The block message suggests |
|---|---|---|
| deleted files | `rm`, `rm -rf` | `trash` (macOS `brew install trash`, Linux `uv tool install trash-cli`) |
| lost uncommitted work | `git reset --hard`, `git checkout .`, `git checkout [--] <file>`, `git restore`, `git clean -f`, `git stash drop`, `git stash clear`, `git reset HEAD~` | `git stash`, a backup branch, `git clean -n` first; checkout, restore and stash guards block only when something would be lost |
| secrets staged by accident | `git add -A`, `git add .` | naming the files |
| rewritten history | `git filter-repo`, `git filter-branch`, `bfg`, `git rebase -i`, `git push --force`/`-f` | `--force-with-lease`, or your permission |
| public actions you did not ask for | `git push`, `gh pr create`/`merge`/`edit`/`comment`, `gh issue create`/`edit`/`comment`, `gh release create`, `gh repo create`/`edit` | your permission |
| unrequested releases | version bumps by command or manifest edit, publishing to a registry | your consent |
| disks and backups | `dd if=`, `mkfs`, `fdisk`, Time Machine changes through `tmutil` | backing up first |
| scripts nobody read | `curl` or `wget` piped into a shell, including `bash <(curl …)` | download, read, then run; `/ar:ok bash` allows one run |
| shell text tools | `sed` that edits files, `awk` except a plain field filter on a pipe (`ls -l \| awk '{print $5}'`), `echo >`, and `grep`, `find`, `cat`, `head`, `tail` outside a pipe | the agent's own edit, read and search tools |
| commit quality | `git` (warning only) | the commit-message rules |

The guards catch the commands an agent writes in ordinary shell; they are not
a sandbox. A command spelled to hide what it runs can get past them: the name
in a variable (`R=rm; $R -rf x`), ANSI-C quoting (`$'rm' -rf x`), `env -S`,
`trap`, `alias`, or syntax the shell parser cannot read. Run an agent you do
not trust in a container or a virtual machine.

Add, allow and list guards per session or for every session:

| This session | Every session | Effect |
|---|---|---|
| `/ar:no <pattern>`, `/ar:no "<pattern>" <message>` | `/ar:globalno` with the same arguments | block; a message needs a quoted, `regex:` or `glob:` pattern, or it becomes part of the pattern |
| `/ar:ok <pattern> [N\|5m\|perm]` | `/ar:globalok <pattern> [N\|5m\|perm]` | allow `N` times, for a duration (`5m`, `2h30m`, `2d`), or `perm` (session: until it ends; global: until cleared); default one use |
| `/ar:blocks` | `/ar:globalstatus` | list blocks and allows |
| `/ar:clear` | `/ar:globalclear` | remove all blocks and allows |

```text
/ar:no rm                                         # block, with the built-in suggestion
/ar:no "exec(" unsafe exec function: use alternatives
/ar:no regex:eval\( dangerous eval usage: blocked for security
/ar:no glob:*.tmp temporary files are not allowed in this session
/ar:no /eval\(.*assert/ no eval( followed by assert
/ar:globalno "git reset --hard" PERMANENTLY DESTRUCTIVE: use git stash instead
/ar:ok 'git push' 1h
```

A pattern is a literal substring by default; `regex:` and `glob:` change that,
and `/.../` is read as a regular expression when it contains metacharacters.
The first match in this order decides:

1. allows: `/ar:ok`, `/ar:globalok`
2. session blocks (a deny overrides a warning)
3. global blocks
4. rule files: `.claude/autorun.*.local.md` in the working directory and each
   parent up to the repository root (outside a repository, the working
   directory only), nearest first, then `~/.claude/autorun.*.local.md`; for the
   same pattern the nearest file's rule applies
5. the built-in guards above (`DEFAULT_INTEGRATIONS` in
   `plugins/autorun/src/autorun/config.py`)

`/ar:reload` rereads the rule files.

### Autonomous runs

`/ar:go <task>` (`/ar:run`) starts a run that may not end at the first "done".
The agent must print each stage's marker before moving on:

1. **Implement**, then print `AUTORUN_INITIAL_TASKS_COMPLETED`.
2. **Critically evaluate** the work, fix the gaps it finds, then print
   `CRITICALLY_EVALUATING_PREVIOUS_WORK_AND_CONTINUING_TASKS_AS_NEEDED`.
3. **Verify** against the requirements, then print
   `AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY`.

Each time the agent tries to stop before printing the marker, autorun sends it
the next instruction. After 3 rechecks (`max_recheck_count`) the instruction
becomes a direct demand to finish verification. Approving a plan in Claude
Code's plan mode starts the same run.

| Command | Effect |
|---|---|
| `/ar:go <task>`, `/ar:run` | start a three-stage run |
| `/ar:gp <task>`, `/ar:proc` | the same, plus a "Wait," review after every step: critique, failure modes, several fixes, pick the best |
| `/ar:x`, `/ar:stop` | stop the run |
| `/ar:sos`, `/ar:estop` | stop the run and print `AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP` |

### Plans

| Command | Effect |
|---|---|
| `/ar:pn`, `/ar:plannew` | write a plan with checkbox steps, dependencies, and how each step is verified |
| `/ar:pr`, `/ar:planrefine` | critique the plan against the code and fill its gaps |
| `/ar:pu`, `/ar:planupdate` | fold new requirements into the plan |
| `/ar:pp`, `/ar:planprocess` | carry the plan out in a three-stage run, reviewing after each step |

```text
/ar:plannew Design a REST API with authentication and tests
/ar:planrefine
/ar:planprocess
```

Plan export saves your plans as files in your project, so they outlive the
session that wrote them. It is on by default. Each plan you accept goes to
`notes/`, each plan you reject to `notes/rejected/`, named
`{datetime}_{name}.md`; `/ar:pe off` stops it for this project.

| Command | Effect |
|---|---|
| `/ar:pe`, `/ar:planexport` | show whether export is on and which setting decided it |
| `/ar:pe on\|off` | pin export for this project; the pin overrides the global default |
| `/ar:pe globalon\|globaloff` | set the default for every project |
| `/ar:pe dir <path>` | export folder (template variables allowed) |
| `/ar:pe pattern <template>` | file name pattern |
| `/ar:pe <component> [on\|off\|dir <path>]` | per-component switch and folder for `accepted` and `rejected` plans; a bare name toggles it, and a component writes only when export is also on |
| `/ar:pe reset` | restore defaults and clear project pins |

### Task tracking

While tracked tasks are open, autorun's Stop hook lists them and sends the agent
back to work. To talk something through first, run `/ar:tasks pause <reason>`.
Reminders and task-based Stop enforcement pause; task status and command guards
stay as they are.

| Command | Effect |
|---|---|
| `/ar:task`, `/ar:tasks` | show tasks, pause, reminder and recovery status |
| `/ar:task pause [N] [duration] [reason]` | pause for `N` Stop attempts, a duration (`s`, `m`, `h`), or both, whichever ends first. Bare `pause` lasts five minutes; a reason alone lasts until the agent prints the recovery marker autorun gives it, or you resume |
| `/ar:task resume` | resume now; `/ar:go` and `/ar:proc` also resume |
| `/ar:task ignore <id> [reason]` | stop one task from blocking Stop |
| `/ar:task prompts on\|off\|<N>` | turn staleness reminders on or off, or set both intervals to `N` (legacy `/ar:task on\|off\|<N>`); Stop enforcement is unaffected |
| `/ar:task prompts initial <N>`, `subsequent <N>` | set one interval for this session |
| `/ar:task prompts scope all\|user\|subagent` | which agents get reminders |
| `/ar:task recovery on\|off\|min <N>` | stale-task recovery, and how many identical Stops trigger it |

**Staleness reminders.** If the agent stops updating its tasks, autorun reminds
it after 25 tool calls in a new session and every 50 calls after that. Any real
task or plan update (`TaskCreate`, `TaskUpdate`, `TodoWrite`, Codex
`update_plan`, or the agent's equivalent) restarts the count. Main agents and
subagents count separately. The defaults are the
`task_staleness_initial_threshold`, `task_staleness_subsequent_threshold` and
`task_staleness_agent_scope` config keys. Resume and compaction keep the current
phase; a fresh start or clear begins again at 25.

**Stale tasks.** A harness can lose a task that autorun still tracks, and that
task would keep blocking Stop. If the same task IDs block Stop twice in a row with
only task updates in between, the Stop message tells the agent to print
`AUTORUN_TASKS_CLEAR_STALE_TASK(<id>)` for each task its harness no longer has,
and autorun marks that task ignored. For a real task that needs discussion, use
`/ar:tasks pause <reason>`.

**Bounded Stops.** autorun blocks the first 3 consecutive Stops
(`stop_block_max_count`) while tasks remain; the next one may end that
exchange without completing, ignoring, deleting or pausing any task. Tool
activity, a new prompt, or a new session starts a fresh count.

Settings live in `~/.autorun/task-lifecycle.config.json`:

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | task tracking on |
| `max_resume_tasks` | `20` | tasks listed in a resume or Stop message |
| `stop_block_max_count` | `3` | consecutive Stops blocked before one may end the exchange |
| `task_ttl_days` | `30` | completed tasks pruned after this many days |
| `debug_logging` | `false` | audit logging |
| `ghost_clear_enabled` | `true` | stale-task recovery on |
| `ghost_clear_min_consecutive_blocks` | `2` | identical Stops before recovery is offered |
| `ghost_clear_hash_length` | `12` | hex characters in the task-set digest |

Task state is in `~/.claude/sessions/daemon_state.json` (or its SQLite store after
`autorun --state-migrate`); per-session audit logs
are in `~/.autorun/task-tracking/<session_id>/audit.log`. The `autorun task`
commands in the [CLI Reference](#cli-reference) inspect, export and clean them.

### Other commands

| Command | Effect |
|---|---|
| `/ar:gc`, `/ar:commit` | the 17-step commit process: `<files>:` or `type(scope):` subjects, concrete wording, a secrets check |
| `/ar:ph`, `/ar:philosophy` | 17 system design principles, such as "Automatic and Correct" and "One Problem, One Solution" |
| `/ar:tm`, `/ar:tmux` | create, list and clean up isolated tmux sessions (Claude Code subagent: `ar:tmux-session-automation`) |
| `/ar:tt`, `/ar:ttest` | test a CLI or plugin in an isolated tmux session (Claude Code subagent: `ar:cli-test-automation`) |
| `/ar:tabs` | list the agent sessions running in your tmux windows, lettered A, B, C, with directory, purpose and status |
| `/ar:tabw` | type into those sessions after stating what it will send, e.g. `/ar:tabw awaiting:continue` or `/ar:tabw A:git status, B:pwd` |
| `/ar:cache` | off by default; blocks tool use while Claude Code's prompt cache is cold or the context is near compaction, so a resumed session does not pay to rebuild it: `on\|off [5m\|1h\|perm]` (a timed one reverts when it ends), `set ratio\|read\|age\|full <value>` (tokens `50k`, `.5M`; `85%`; `5m`, `2h30m`, `2d`), `ok [5m\|N\|perm]`, `no`, `global <subcommand>` |
| `/ar:restart-daemon` | restart the daemon for this install |
| `/ar:gemini` | when to hand a task to Gemini: images, PDFs, video, search |
| `/ar:test` | check that autorun's commands are loaded |

`/ar:tabs` shows each session as `awaiting input`, `working`, `plan approval`,
`tool permission`, `idle`, or `error`. Answer with selections such as `A, B:git
status, all:continue`; `all:escape` interrupts every working session.

The commands from before the `ar` prefix still work: `/afa`, `/afj`, `/afs`,
`/afst`, `/autorun`, `/autoproc`, `/estop`.

## Keep sessions alive with byobu

An agent running in a plain terminal dies when the terminal closes or SSH drops.
Inside [byobu](https://www.byobu.org/) (a tmux front end) it keeps running, and
you can reattach from another machine or a phone over SSH or
[Mosh](https://mosh.org/):

```bash
brew install byobu                   # Linux: sudo apt install byobu
byobu new-session -s autorun-work    # then start your agent inside it and give it /ar:go ...
# F6 detaches; the agent keeps working
byobu attach -t autorun-work         # later, from any terminal: ssh or mosh in first
```

F2 opens a window, F3 and F4 move between windows, and F9 opens the menu. More
in the [byobu documentation](https://www.byobu.org/documentation).

## CLI Reference

The `autorun` command installs and inspects autorun outside an agent session.
`autorun --help` prints every option.

**Install and remove:**

```bash
autorun --install                    # every embedded plugin, every detected agent
autorun --install ar --codex         # one plugin (ar; autorun is an alias), one agent
autorun --install --claude           # also --gemini, --antigravity, --qwen, --pi, --prime, --codex
autorun --install --custom-harness 'lab=qwen:qwen-lab:/path/to/config::Qwen Lab'   # repeatable
autorun --install-dry-run --codex    # preview every write
autorun --install --force            # republish even at the same version
autorun --install --tool             # also run uv tool install for the CLI
autorun --uninstall                  # everything incl. the autorun-ai tool, or a comma-separated plugin list; keeps ~/.autorun
```

**Inspect:**

```bash
autorun --status                     # what is installed where, files that differ from this version, one test run of the hook
autorun --status --gemini            # include the retired Gemini CLI
autorun --status --custom-harness 'lab=codex:codex-lab:/path/to/config::Codex Lab'
autorun --version
autorun --capability-snapshot FILE   # platforms, commands, skills and hooks as JSON ('-' or no FILE: stdout)
statusline-command | autorun --cache-snapshot   # pipe your Claude Code statusline JSON in so /ar:cache can see cache state
```

**Maintain:**

```bash
autorun --restart-daemon             # restart the daemon for this install
autorun --restart-all-daemons        # stale or mixed-version daemons: also stops other installs' daemons, interrupting their sessions
autorun --state-status               # state backend in use, and any conversion
autorun --state-migrate              # convert JSON state to SQLite (needed before switching to SQLite) while this install's daemon is stopped
autorun --state-rollback             # export SQLite state back to daemon_state.json so state_backend can be json again
autorun --state-maintenance          # SQLite database, WAL and reclaimable bytes; deletes nothing
autorun --update                     # check for and install an update
autorun --update-method uv           # force the update method
autorun --no-bootstrap               # hooks stop installing missing dependencies in the background
autorun --enable-bootstrap           # turn that back on
```

**File policy** (`/ar:a`, `/ar:j`, `/ar:f`, `/ar:st` in a session):

```bash
autorun file status                  # aliases st, s
autorun file status --session ID
autorun file allow                   # alias a
autorun file justify                 # alias j
autorun file search                  # aliases find, f
```

Each session starts at allow-all, and there is no global policy; `--global` says
so and exits 1 with `allow`, `justify` or `search`. While hooks run through the
daemon (the default), the daemon owns session state, so `allow`, `justify` and
`search` exit 1 and print the slash command to run in the session instead. With
`AUTORUN_USE_DAEMON=0` they write the policy. Without `--session`, autorun uses
`$AUTORUN_SESSION_ID`, then the only active session if there is exactly one.

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

`--pattern` defaults to `*` and `--ttl` to `task_ttl_days`. On the SQLite backend `task gc` expires whole
sessions with no unfinished tasks, archives them before deleting, and refuses
`--pattern` and `--no-archive`, which it cannot honor.

**Custom agents.** `--custom-harness` takes
`name=flavor:binary:config_dir[::display]`. Flavors are `claude`, `gemini`,
`qwen`, `antigravity`, `agy` (an alias for `antigravity`), and `codex`. The
`claude` flavor installs the portable Markdown commands and AGENTS.md without
hooks, which suits Claude-compatible agents such as OpenCode. Keep permanent
targets in the `custom_harnesses` config key with the same grammar; a flag
overrides a config entry of the same name, and entries may share a flavor with
different folders (`codex-home=codex:codex:~/.codex-home` and
`codex-work=codex:codex:~/.codex-work`). The `::` before the display name lets
`config_dir` itself contain `:`.

**Config folders.** The `harness_config_dirs` config key (for example `{"codex":
"~/.codex-work"}`) overrides the agent's own variable (`CLAUDE_CONFIG_DIR`,
`CODEX_HOME`, `QWEN_HOME`, `FORGE_CONFIG`), which overrides the default. The
desktop apps share the CLI folders: the ChatGPT/Codex app (`com.openai.codex`)
uses `~/.codex`, and Claude Desktop's local Code sessions use `~/.claude`.

**Rarely needed:**

```bash
autorun --exit2-mode auto            # Claude Code bug #4669: deny by exit code 2 + stderr instead of JSON
autorun --conductor                  # install the Conductor extension for Gemini (default)
autorun --no-conductor               # skip it
autorun --cli claude                 # which agent is calling; hooks pass this
```

`--exit2-mode` works around
[anthropics/claude-code#4669](https://github.com/anthropics/claude-code/issues/4669),
where Claude Code ignored a deny returned with exit code 0.

Accepted values: `--codex-hook-source: user|plugin|both|none`;
`--codex-plugin-marketplace: personal|github`;
`--claude-agents-skills: link|copy|none`;
`--skill-placement: auto|native|both` or `HARNESS=auto|native|both`, repeatable;
`--exit2-mode: auto|always|never`;
`--cli: claude|gemini|antigravity|qwen|codex|pi|prime|opencode`;
`--update-method: auto|claude|gemini|plugin|uv|pip`;
`--format: text|json|table` for `task status`;
`--format: json|csv|markdown` for `task export`.

To keep an install choice across `autorun --update` and automatic
reinstalls, which run with no flags, put it in `autorun.config.json` under the
flag's name with underscores: `{"codex_hook_source": "both", "skill_placement":
{"codex": "native"}}`. A flag or `AUTORUN_<NAME>` variable still wins, and an
invalid value is ignored in favor of the default.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `autorun: command not found` after `uv tool install` | `uv tool update-shell`, then open a new terminal; `uv tool dir --bin` prints the folder. Installed hooks call autorun by full path, so they work either way. |
| Commands do nothing | `autorun --status` checks every detected agent and runs the hook command once (`ok hook command`). Hook calls are logged to `~/.autorun/hook_entry_debug.log`, the daemon to `~/.autorun/daemon.log`. Restart the agent session after any install. |
| Claude's task tools are missing or vanish | Claude Code 2.1.268 and later offer them to newer models only with `CLAUDE_CODE_ENABLE_TODO_TOOLS=1`. `autorun --install` sets it in `~/.claude/settings.json` unless you already did; start a new session. Without the tools autorun does not enforce tasks, and says so at session start. [Details and other agents](plugins/autorun/TROUBLESHOOTING.md#claude-task-tools-are-missing-or-vanished). |
| Claude Code plugin will not load | `claude --debug` shows plugin loading. Reinstall: `claude plugin uninstall ar@autorun`, then the marketplace commands in [Other ways to install](#other-ways-to-install). The installed copy is under `~/.claude/plugins/cache/autorun/ar/`. |
| Anything else | `autorun --install --force` republishes hooks, commands and skills. Full guide: [TROUBLESHOOTING.md](plugins/autorun/TROUBLESHOOTING.md). |

## Related tools

A correction you give agents again and again is a candidate `/ar:no` guard or
rule file. [AI Session Search](https://github.com/ahundt/ai-session-search)
(`aise`) searches your past agent sessions, and its `corrections` report lists
those repeats:

```bash
aise skills run corrections --when 30d --limit 50
```

## Contributing

Report bugs and request features in
[GitHub Issues](https://github.com/ahundt/autorun/issues); pull requests are
welcome. [AGENTS.md](AGENTS.md) has the sandboxed development install and the
test commands, [plugins/autorun/AGENTS.md](plugins/autorun/AGENTS.md) the code
conventions, and [INTEGRATION_GUIDE.md](plugins/autorun/docs/INTEGRATION_GUIDE.md)
where to add a command, handler or guard. Hook internals:
[HOOK_ARCHITECTURE.md](plugins/autorun/HOOK_ARCHITECTURE.md); state storage and locking:
[developer-internals.md](docs/developer-internals.md); hook APIs:
[Claude Code](docs/claude-code-hooks-api.md),
[Gemini CLI](docs/gemini-cli-hooks-api.md),
[Codex CLI](docs/codex-cli-hooks-api.md),
[all events](docs/hooks_api_reference.md).

## License

Apache License 2.0. See [LICENSE](LICENSE).
