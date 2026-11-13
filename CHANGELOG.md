# Changelog

Notable changes to the autorun marketplace (`ar`, `autorun-skills`, and
`pdf-extractor`).

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions are the plugin versions in `.claude-plugin/marketplace.json`; the
marketplace itself carries a separate `version` field.

## [1.0.0rc4] - 2026-11-07

autorun 1.0.0rc4 changes command names, skill ownership, task-state handling,
harness hooks, tmux targeting, and same-version Claude cache repair. It ships
three marketplace plugins and supports Claude Code, legacy Gemini CLI, Google
Antigravity, Qwen Code, Codex CLI, Pi, Prime Agent, ForgeCode, and OpenCode.
Runtime blocking works only in harnesses that expose prompt, tool-call, and Stop
hooks. ForgeCode and Prime Agent 0.10 receive guidance and skills but cannot
enforce those policies.

### Upgrading from 1.0.0rc3

1. Run the generated package upgrade commands. `autorun --install` publishes
   `ar`, `autorun-skills`, and `pdf-extractor`. A Claude marketplace-only
   install must also run `claude plugin install autorun-skills@autorun`; the
   generated marketplace command updates `ar` only. After the upgrade, quit and
   relaunch each installed coding CLI or app, then start a new session.
2. Replace retired command names in instructions and automation:
   `/ar:task-status` becomes `/ar:task status`; `/ar:task-ignore` becomes
   `/ar:task ignore`; `/ar:st`, `/ar:afst`, `/ar:blocks`, and
   `/ar:globalstatus` become `/ar:status`; `/ar:a`, `/ar:allow`, and `/ar:afa`
   become `/ar:file allow`; `/ar:j`, `/ar:justify`, and `/ar:afj` become
   `/ar:file justify`; `/ar:f`, `/ar:find`, and `/ar:afs` become
   `/ar:file existing-only`; `/ar:run` and `/ar:autorun` become `/ar:go`;
   `/ar:gp`, `/ar:proc`, and `/ar:autoproc` become `/ar:goprocess`; `/ar:x`
   becomes `/ar:stop`; `/ar:sos` becomes `/ar:estop`; `/ar:pn` becomes
   `/ar:plannew`; `/ar:pr` becomes `/ar:planrefine`; `/ar:pu` becomes
   `/ar:planupdate`; `/ar:pp` becomes `/ar:planprocess`; `/ar:pe` becomes
   `/ar:planexport`; and `/ar:globalok`, `/ar:globalno`, and `/ar:globalclear`
   become `/ar:ok --global`, `/ar:no --global`, and `/ar:clear --global`.
   Retired spellings remain accepted only where the harness forwards them;
   help and command output use the current names. The deleted `/ar:gc` and
   `/ar:ph` command documents are replaced by the `/ar:commit` and
   `/ar:philosophy` skills. The deleted `/ar:tm`, `/ar:tmux`, `/ar:tt`, and
   `/ar:ttest` command documents are replaced by the `/ar:tmux` skill or
   `autorun tmux list` and `autorun tmux send`. The deleted `/ar:gemini` and
   `/ar:test` commands have no replacement.
3. Remove obsolete entries from `$AUTORUN_HOME/autorun.config.json`:
   `stage1_completion`, `stage2_completion`, `stage3_completion`,
   `recheck_template`, `plan_accepted_marker`, `monitor_stop_delay_seconds`,
   `task_staleness_no_tasks_threshold`, `task_staleness_threshold`,
   `shared_agents_plugins_subdir`, `delegation_ttl_seconds`,
   `ghost_clear_reason`, and `ghost_clear_injection_template`. Also remove
   `ghost_clear_min_consecutive_blocks` and `ghost_clear_hash_length` from
   `$AUTORUN_HOME/task-lifecycle.config.json`.
4. Update skill references. Eleven general skills now belong to the
   `autorun-skills` plugin, so Claude uses names such as
   `/autorun-skills:streamline-text`. Rename `parallel-subagent` references to
   `test-fixes-in-parallel`.
5. Replace the retired tmux commands with the `/ar:tmux` skill or the CLI. Run
   `autorun tmux list`; its A, B, and C labels change when panes open or close,
   so target a pane id such as `%3`. Send with
   `autorun tmux send --to %3 -- continue`. `--to` accepts one pane id,
   comma-separated pane ids, `all`, or `awaiting-input`; letter targets are
   refused.
6. Review policies for plain `grep`, `find`, `cat`, `head`, `tail`, and
   `echo ... > file`: autorun no longer changes those commands by default, so
   they run normally unless another rule blocks them. To block every `cat`
   command, run `/ar:no --global cat`. The `find` guard still blocks `-delete`,
   `-fprint`, `-fprint0`, `-fprintf`, and `-fls`.
7. If you installed an earlier rc4 build under the same `1.0.0rc4` version
   number, run `autorun --install --force`. The installer refreshes the cache in
   place, keeps its version directory and `.venv`, and backs up files detected
   as edited or unproven when the refresh selects them. It prints every backup
   path. If another install is refreshing the same cache, it prints a waiting
   notice and waits up to 600 seconds by default; after a timeout, wait for the
   other install to finish and rerun this command.

### Highlights

1. When work needs your decision, autorun recognizes
   `AUTORUN_TASK_AWAITING_USER(<id>): <decision>`, saves the question, and lets
   the Stop request continue through the remaining completion checks. Your next
   prompt or an explicit task update restores the work's previous status.
2. Work delegated to a subagent stays assigned until SubagentStop, its matching
   process exits, or a task update provides new evidence. A fork receives only
   work that existed when it was created, while work started in a side
   conversation stays there. The fourth consecutive Stop with no tool call may
   pass the loop guard, but it never marks work complete or ignored. A user
   prompt or a tool call in the main conversation resets the sequence.
3. SQLite remains readable by rc3 with `user_version` 2 while rc4 records schema
   revision 3. Startup changes supported task definitions without copying all
   task rows. If SQLite refuses that change, autorun stores newer statuses in a
   form older builds can read. `autorun --state-status` reports repair state;
   after stopping every daemon that uses the store,
   `autorun --state-rebuild-tasks` performs the optional offline rebuild.
4. Qwen shows allowed-Stop notices through `systemMessage`; Antigravity routes
   prompt policy and deferred Stop notices through `PreInvocation`; Pi and
   Prime Agent versions before 0.10 carry lifecycle notices; Codex installs
   compaction and parent-conversation delegation hooks; and OpenCode handles
   prompt, Stop, and compaction events. Prime Agent 0.10 receives guidance and
   skills only.
5. Permission-changing chat commands accept user-authored input only. Models,
   subagents, replayed automation, and OpenCode's model-callable bridge cannot
   grant `/ar:ok`, change file policy, disable the cache check, or pause task
   enforcement.
6. Prompt-time cache expiry checks default to a warning at 100,000 input tokens
   where the harness supplies usable lifetime and transcript data.
   `/ar:cache on` requests blocking where supported; resending the same blocked
   prompt lets it proceed.
7. `autorun tmux list` discovers foreground panes for every registered harness.
   `autorun tmux send` types literal text, presses Enter separately, refuses
   dialogs and panes mid-turn, and fails when submission or readback cannot be
   confirmed.
8. Wheel and source installs make `ar`, `autorun-skills`, and `pdf-extractor`
   available together. General skills are under `autorun-skills`, while skills
   used by autorun commands remain available through `ar`.
9. Same-version Claude repair has three rc4 limits. A file changed after backup
   verification but before replacement can lose that last change without a
   matching backup. A hook that runs while a changed build reinstalls the cache
   `.venv` can fail to import autorun; tool gates deny, while lifecycle events
   continue with an `[autorun]` notice. After an interrupted refresh, bytes that
   equal the previous autorun version are treated as untouched. Stop cache
   writers before repair, wait for an active install to finish, and copy any
   previous-version customization elsewhere before rerunning.

### Changed

1. The former default redirects for `grep`, `find`, `cat`, `head`, `tail`, and
   `echo ... > file` were removed. Ordinary reads and redirections now follow
   the shell and harness permissions unless another rule matches. Use a custom
   or global `/ar:no` rule when a command-wide block is required.
2. `git stash clear`, which deletes every stash at once, is now blocked while a
   stash exists, as `git stash drop` already was. Allow one use with
   `/ar:ok 'git stash clear'`.
3. `/ar:no` reads its pattern the way `/ar:ok` does: `/ar:no git push` blocks
   `git push` rather than `git`, and removes an `/ar:ok git push` allow. A
   custom message needs a quoted, `regex:`, or `glob:` pattern:
   `/ar:no "rm" use trash instead`.
4. A shell that runs a script straight from `curl` or `wget`
   (`curl ... | sh`, `wget -qO- ... | bash`, `bash <(curl ...)`,
   `bash -c "$(curl ...)"`) is now blocked, because autorun cannot check a
   script it never sees. Download the file, read it, then run it; `/ar:ok bash`
   (or the shell you use) allows one run.
5. Rule files (`.claude/autorun.*.local.md`) are now read from the session's
   directory and each parent up to the repository root, nearest first, then
   `~/.claude/`. Outside a repository only the session's directory is read.
6. Skills install into each harness's native plugin, extension, or package
   folder instead of defaulting to the shared `~/.agents/skills` tree.
   General-purpose skills now belong to the separate `autorun-skills` plugin;
   edited or unowned copies are retained and reported. `--skill-placement both`
   still also writes the shared skill tree.
7. `autorun-skills` ships eleven general-purpose skills. `ar` keeps seven
   integration skills used by autorun commands: `plannew`, `planrefine`,
   `planupdate`, `planprocess`, `commit`, `philosophy`, and `tmux`.
8. Safety guards are a mitigation for cooperative models, not an operating
   system sandbox or a security boundary. Host permissions, harness behavior,
   and commands outside configured rules still apply.
9. Prime Agent 0.10 uses a Rust runtime without Pi's extension API. autorun
   installs guidance and skills there, but cannot enforce prompt, tool, Stop,
   subagent, permission, or compaction policy at runtime. Use Pi or another
   harness with prompt, tool-call, and Stop hooks when you need runtime
   enforcement.
10. OpenCode exposes no native prompt-origin field. autorun classifies a
    non-synthetic root prompt as user input by checking `parentID` and synthetic
    parts; another plugin or API client can submit the same shape, so this
    classification is not an authentication boundary.
11. OpenCode 0.6.0 and newer can start a new assistant turn after a blocked Stop
    through `client.session.prompt`; 1.0.111 and newer use
    `client.session.promptAsync` first. A client with neither method can only
    show the denial through its warning toast or structured log API. Upgrade
    OpenCode; until then the Stop block is a warning only and the user must
    continue manually.
12. Blocking on an expired prompt cache has not been checked against recorded
    cache lifetimes from real sessions. Prompt-time cache expiry therefore
    remains `warn` by default; missing lifetime, usage, or transcript data
    passes without a block. Run `/ar:cache status` to see the current policy;
    run `/ar:cache on` to request blocking where the harness supports it.
13. The suite includes synthetic tests with 200 clients sending requests at the
    same time. No quiet-machine capacity, latency, or stability measurement is
    published for this release.

### Removed

1. Configuration keys with no current behavior were removed from
   `$AUTORUN_HOME/autorun.config.json`: `stage1_completion`, `stage2_completion`,
   `stage3_completion`, `recheck_template`, `plan_accepted_marker`,
   `monitor_stop_delay_seconds`, `task_staleness_no_tasks_threshold`,
   `task_staleness_threshold`, `shared_agents_plugins_subdir`,
   `delegation_ttl_seconds`, `ghost_clear_reason`, and
   `ghost_clear_injection_template`. `ghost_clear_min_consecutive_blocks` and
   `ghost_clear_hash_length` were removed from
   `$AUTORUN_HOME/task-lifecycle.config.json`.
2. Legacy unprefixed command registrations and duplicate command documents were
   removed. Retired `/ar:` spellings remain input tolerance only; current help,
   manifests, and command output use one advertised name per command.
3. The old `autorun.main`, `autorun.ai_monitor`, `autorun.tmux_injector`, and
   shadowing `plugins/autorun/autorun.py` launchers were removed. Use `autorun`
   or `python -m autorun`.

### Fixed

1. When `~/plugins/ar` already existed as an empty directory,
   `autorun --install` kept it as yours, registered Codex's `ar` plugin there
   anyway, and reported success, leaving Codex with a plugin that had no files
   (`ls -A ~/plugins/ar` printed nothing). It now fills an empty directory, and
   still leaves alone any directory that has content in it or is reached through
   a symlink.
2. On a machine where Qwen Code had never run, the first `autorun --install`
   left Qwen's task tool off (`tools.todoWrite.enabled` in
   `~/.qwen/settings.json`) and said nothing, so Qwen sessions had no
   `todo_write` and autorun did not enforce task tracking there until a second
   install. One install now turns it on.
3. `extract-pdfs` on a folder whose subfolders held PDFs with the same name,
   such as `a/report.pdf` and `b/report.pdf`, wrote a single `report.md`: the
   second PDF was skipped as already extracted, or with `--no-resume` it
   overwrote the first. Output now mirrors the input folders, and folder mode
   honors `--format txt`, which it used to ignore.
4. With only the `pdf-llm` or `pdf-gpu` extra installed, `extract-pdfs` said "No
   extraction backend installed" although `--list-backends` listed one. It now
   tries every installed backend.
5. With two sessions planning in one project, leaving plan mode in one session
   backed up the other session's plan. After an accept that clears context, the
   other session's unfinished plan was exported as accepted and the plan you
   accepted was filed as not accepted. Each session now uses its own newest
   plan.
6. `/ar:planexport dir` with an absolute folder archived the plan but reported
   "Plan export FAILED ... NOT archived", and a `~/...` folder created a
   directory literally named `~` inside the project. Both now export where you
   asked.
7. With `/ar:planexport rejected off`, a plan you never accepted was written to
   the accepted `notes/` folder. It is no longer written anywhere.
8. `/ar:planexport status` showed `notes` as the export folder for a
    configuration using the older `output_dir` key, while exports went to that
    folder. Status now shows where exports actually go.
9. Commands started through a wrapper or a shell skipped every guard:
    `nohup rm -rf x`, `time rm -rf x`, `echo x | xargs rm -rf`,
    `bash -lc 'rm -rf x'`, a multi-line `bash -c`, `bash <<EOF`,
    `echo '...' | sh`, `eval`, and `find ... -exec rm` all ran unchecked.
    autorun now checks the command inside these, and inside `command`, `exec`,
    `doas`, `pkexec`, `stdbuf`, `strace`, `ltrace`, `watch`, `fakeroot`,
    `firejail`, and `bwrap`.
10. `git clean --force` and `git add --all` skipped the `git clean -f` and
    `git add -A` rules. The long forms now match, and `/ar:ok 'git clean -f'`
    also allows `--force`.
11. A commit message passed through a heredoc with a quoted delimiter, such as
    `<<-'EOF'` or `<<'END-MSG'`, was read line by line as commands, so a
    message line like `rm stale helper` was blocked as `rm`.
12. A Bash command starting with `/ar:` was approved without any check, so
    `/ar:x; rm -rf ~/important` ran the `rm`.
13. An `/ar:ok` allow approved the whole command line: after `/ar:ok rm`,
    `rm -rf build && git push --force origin main` ran, force push included.
    An allow now covers only the commands in the line that it matches.
14. A one-use `/ar:ok` could be spent more than once: by two hooks at the same
    moment, or by repeating the same call less than a second apart, without
    limit.
15. Codex: setting `codex_plugin_source_dir` moved autorun's Codex plugin but
    not its entry in `~/.agents/plugins/marketplace.json`, so Codex looked for
    the plugin where it no longer was. The entry now follows the setting. A
    location outside your home folder, which Codex refuses, is refused before
    anything is written, and a `~/plugins/ar` that autorun did not create is
    left alone, kept out of the marketplace, and named in a warning.
16. Some installs removed what another harness or plugin still used:
    `autorun --install --gemini` removed Antigravity's
    `~/.gemini/config/plugins/ar`; with a Codex-type custom harness,
    `autorun --install --claude` removed `~/plugins/ar` while the marketplace
    still listed it; an install without `--custom-harness` removed the
    extension of a Gemini-type harness given only by that flag; and
    `autorun --uninstall pdf-extractor` removed `ar`'s old marketplace entry.
    Each run now changes only the harnesses and plugins it was given.
17. An interrupt or a full disk at the wrong moment of an install, or of
    `autorun --uninstall --force`, could delete the only copy of a directory
    being replaced, such as your own `commands/` folder. It is now put back, or
    saved under `~/.autorun/installer/backups/` when its place was taken.
18. A `.DS_Store` that Finder wrote into an installed folder made every later
    install report "you edited files we installed (.DS_Store)", and uninstall
    kept the folder. `.DS_Store`, `._*`, `Thumbs.db`, and `desktop.ini` are now
    ignored. A Codex plugin installed from a checkout no longer includes its
    `tests/` folder.
19. Setting `AUTORUN_BUG_CLAUDE_CODE_NO_TOKEN_COUNT_FOR_HOOKS_BUG_54673_WORKAROUND_ENABLED`
    to false left the block an earlier install wrote into `~/.claude/CLAUDE.md`.
    The next install now removes it.
20. On Windows, every `autorun --install` ended with "Daemon did not start" and
    exit code 1: the daemon's start-up program was invalid Python for any path
    containing `\U`, as in `C:\Users\...`. A path containing `'` broke it on
    any system.
21. On Python 3.11 and later, a daemon slower than the response timeout was
    sent the same hook again and again, and the hook then reported "Daemon
    failed to start". The Pi, Prime Agent, and OpenCode bridges likewise sent a
    timed-out tool call a second time. A hook is now sent once, and a slow
    answer is reported as "Daemon accepted the request but did not answer".
22. When two hooks wrote autorun's state at the same moment, the one that
    waited reported that its value "has been discarded", and message delivery
    did not retry. It is now treated as lock contention and retried.
23. In Pi, Prime Agent, and OpenCode, `AUTORUN_DISABLE=1`, the way out that
    every block message names, did nothing while the daemon was running, and
    block messages said to retry, which does not help. Both now match the
    other harnesses.
24. Antigravity: when an autorun handler failed or timed out on an event other
    than a tool call, such as Stop, Antigravity was sent Claude Code's
    response fields. Failures now use Antigravity's own format.
25. When autorun installed its own dependencies in the background and that
    took longer than 60 seconds, the next hook started a second install
    alongside the first. It now waits for the first to finish.
26. A hook that fell back to running the `autorun` command wrote that
    command's whole output to `~/.autorun/hook_entry_debug.log`; it now logs a
    bounded excerpt.
27. `autorun task gc` deleted sessions whose tasks were paused or delegated.
    It now keeps any session with a task it cannot prune.
28. Codex's `update_plan` and Gemini's `write_todos` overwrote the statuses
    autorun sets, so a task you ignored, paused, or delegated went back to
    pending or in progress. Those statuses are now kept unless the harness
    reports the task completed or deleted.
29. On Pi and Prime Agent, autorun read its own Stop message, which shows the
    stale-task marker filled in with real task ids, as the agent asking to
    clear those tasks, and cleared them. On Claude Code, the same marker in the
    agent's final reply was not read. Markers now count only in the newest
    assistant message or a tool result.
30. A lock timeout in `~/.autorun/task-lifecycle.config.json` that was a
    string, zero, negative, or infinite turned the Stop gate off. Such a value
    now falls back to the default.
31. The session-start task summary listed only in-progress and pending tasks,
    so a delegation waiting for you to verify it showed only as "and 1 more".
    Every blocking task is now named, and a returned delegation says to verify
    the results.
32. Bug-workaround keys in `autorun.config.json` accepted only `true` or
    `false`; `"always"`, `"never"`, `"auto"`, and version ranges, which the
    documentation lists, were dropped without a word. They now load, and the
    installer honors them too: `"never"` for the CLAUDE.md workaround now
    keeps the block out.
33. Gemini CLI started an autorun hook process after every model response, and
    Gemini CLI and Qwen Code at every session end, for events autorun does
    nothing with. Those events are no longer subscribed.
34. `autorun --no-bootstrap` wrote its flag inside the quoted script path in
    Claude Code's `hooks.json`, so every hook failed to start and every tool
    call was blocked. The flag now goes after the path, and
    `autorun --enable-bootstrap` restores the shipped command.
35. `autorun --update` compared your version with the newest release, then
    installed whatever was on the default branch, moving a PyPI install onto
    unreleased code. It now installs the release it reported: the PyPI version
    for a PyPI install, the release tag for a git install.
36. With a wheel install (`uv tool install autorun-ai`), Antigravity hooks ran
    `autorun --cli ... --event <name>`, which `autorun` rejected with "invalid
    choice", so they failed. `--event` is now accepted, and a misspelled flag
    such as `--codex-hook-sorce` is now an error instead of being ignored.
    `AUTORUN_UPDATE_METHOD` now sets `--update-method`.
37. `autorun file allow`, `justify`, and `search` printed success and changed
    nothing: they wrote a policy the file gate never reads. They now write the
    session's policy (pick one with `--session`); while hooks go through the
    autorun daemon, they exit 1 and name the slash command to run in the
    session instead. `--global` now says there is no global policy.
38. `autorun --uninstall --custom-harness ...` ignored the custom harness and
    left everything the matching install put there. A custom harness named
    after a built-in one, such as `gemini`, is now refused at install; before,
    every later uninstall failed on it.
39. Uninstalling the retired `pdf-extractor` package with pip from autorun's
    own environment also deleted `extract-pdfs`, which autorun now provides. It
    is now kept and reported with the command to remove it, as the uv route
    already did.
40. `autorun --install --tool` from a wheel inside a uv tool environment failed
    before installing anything.
41. With `--codex-hook-source plugin` on a wheel install, Codex's plugin hook ran
    without autorun's dependencies.
42. Antigravity's post-install check treated "No plugins are installed." as
    proof that `ar` was registered, because "ar" is inside "are".
43. A `hook_wrapper_timeouts_seconds` value in `autorun.config.json` changed how
    long autorun planned to wait, but the hook still gave up at the built-in
    time, cutting requests short.
44. With a wheel install, Antigravity received no output from `/ar` commands
    and no other message autorun added to a prompt or model turn: the
    message was converted to Antigravity's format twice, and the second pass
    dropped it.
45. Block suggestions were built from the whole line, so
    `cd /tmp && rm -rf build` suggested `trash /tmp && rm build`. They now use
    the blocked command's own arguments.
46. With `/ar:file justify`, Claude Code, Codex, and the Gemini family had
    every new file refused whatever justification the agent wrote, because
    autorun read it only from the field Pi sends. On Pi, the example
    justification in autorun's own message counted as one.
47. Guards added or changed under `default_integrations` in
    `autorun.config.json` were ignored; the shipped table always applied.
48. `/ar:no regex:terraform\s+destroy` stored `terraforms+destroy`, so nothing
    was blocked, and an invalid or catastrophically slow regex was stored and then
    matched as plain text. Backslashes are now kept, and a bad regex is refused
    when you add it.
49. File guards, such as `/ar:no secrets.env` and the version-bump manifest
    guards, missed paths containing a space or Windows backslashes.
50. Project rule files (`.claude/autorun.*.local.md`) applied only when the
    daemon happened to start in that project, and then to every session. Rules
    now come from the session's project, then `~/.claude/`. README.md named
    `~/.claude/hookify.*.local.md`, which was never read.
51. A prompt that began with one of autorun's internal command names ran that
    command: "stop using sed and use the Edit tool instead" stopped autorun, and
    "activate the venv" started it.
52. Scope words such as `5m`, `3`, and `perm` were read five different ways:
    `/ar:ok rm 5min` allowed the pattern `rm 5min`, and `/ar:cache ok for 5m`
    ignored "for". Every command now uses one grammar and refuses a word it does
    not know.
53. On Codex, `ar:task resume` followed by more text in the same prompt was
    refused, and `ar:cache off` tried to read the next sentence as a duration.
    Commands now take their arguments from their own line.
54. `/ar:cache global ok` reported success while the cache gate kept blocking,
    and `/ar:cache off 1h` given twice left the gate off for good once the hour
    ended.
55. `/ar:task pause Let's discuss the release` failed with "No closing
    quotation".
56. `/ar:no format` stored the advice for `rm`, because "rm" is inside
    "format".
57. Commands inside `bash -c`, `sh -c`, `eval`, a heredoc fed to a shell, or
    `find -exec` skipped the conditions some guards check:
    `bash -c 'git restore notes.txt'` ran on a file with unsaved changes, and
    `bash -c 'cat notes.txt'` ran although `cat notes.txt` is blocked. They are
    now judged like the bare command.
58. On `rm a && rm -rf b`, the "Use instead" suggestion named `a`; it now
    comes from the command the rule matched, inside wrappers too.
59. A prompt starting with `ar` and then text that is not a command, such as
    "ar 2 tests fail on main, why?", showed the help list instead of reaching
    the agent. Only `ar` alone opens help now.
60. `extract-pdfs` on a folder exited 0 even when every PDF failed. It now
    exits 1 when any PDF failed and lists each failure.
61. `extract-pdfs` replaced a `.md` you wrote that had its output's name
    (always for a single PDF, and with `--no-resume` for a folder). Your file
    is now kept as `<name>.md.~1~`, numbered so no earlier copy is replaced.
62. When a session started, plan export could take another session's plan in
    the same project: accepting a plan with "clear context" exported or
    archived other sessions' plans, and a new, resumed, or compacted session
    archived plans still being written, its own included. Recovery now takes
    only the cleared session's plans, and leaves alone any plan whose session
    is still running.
63. Asking about a tmux session that did not exist created it, so listing
    windows could leave empty sessions behind; creating a session on purpose
    could report success without creating it.
64. When the daemon failed during a permission request in Qwen Code (or Claude
    Code or Codex set up to send one), autorun's deny was in a shape the tool
    ignores, so the usual permission prompt appeared instead. The deny now
    uses the tool's own format on every path.
65. In Pi and Prime Agent with `AUTORUN_DISABLE=1`, task results told you to
    restart the daemon; they now say autorun is disabled.
66. Under concurrent load, one slow session could make unrelated sessions see
    `daemon_dispatch_contained` and restart advice, causing repeated restarts.
    Reads no longer take write transactions, slow-call containment is scoped to
    its session, overload messages distinguish a busy daemon from an
    unreachable one, and restart refuses to replace a recently started daemon
    that answers its health probe.
67. Codex blocked plain file reads behind a wrapper (`rtk cat f`,
    `env LC_ALL=C head f`, `LC_ALL=C tail -n 40 log`) and told the agent to
    use a "shell file inspection tool" with a `file_path=` parameter Codex
    does not have. Those reads now run, and Codex's messages for `cat`,
    `head`, `tail`, `awk`, `sed` and `echo >` name tools it has: `apply_patch`,
    `rg -n` and `sed -n`. Still blocked: a line that runs `sudo`, `doas`,
    `su`, `run0`, `runuser` or `setpriv`, however quoted and even after a `#`;
    a read that also writes a file, `<>` included; and `tail -f` anywhere in
    the line.
68. `awk` filtering a pipe, such as `ls -l | awk '{print $5}'`, was blocked.
    Simple filters on a pipe are now allowed, as `cat`, `grep` and `find`
    are: printing fields and strings, an `NR` or field condition, a column
    sum, and `!seen[$0]++`. Other `awk` programs stay blocked, because awk
    can run commands, read and write files, and load code in ways the guard
    cannot see in the program text.
69. For `rm -rf "$tmp"` the `rm` guard suggested `trash '$tmp'`, which names
    a file literally called `$tmp`; it now suggests `trash "$tmp"`.
    Suggestions also keep `~` and `~/` paths expandable, keep an operand after
    `--`, and keep a literal `'$(...)'` quoted so it cannot run. The
    `git stash` suggestions for `git reset --hard`, `git checkout` and
    `git restore` no longer copy the blocked command's arguments into the
    stash message, where a `$(...)` would have run.
70. A subagent's tool call could receive its parent session's "CANNOT STOP"
    task list, which it could not act on and which the parent then never
    saw. The list now waits for the parent's next tool call.
71. `run0`, `runuser` and `setpriv` were not recognised as wrappers, so
    `run0 rm -rf x` and `run0 git push --force` passed the guards that block
    the same command run directly. They are now treated like `sudo`.
72. On the JSON state backend, a full disk while clearing an expired task
    pause failed the tool call with a raw `OSError`; it is now reported as a
    state error and tasks are enforced normally. Loading a session's stored
    state no longer replaces a newer in-memory value of the same field.
73. The `sed` guard missed in-place edits whose `-i` was not the first
    option, such as `sed -E -i 's/a/b/' f` and `sed 's/a/b/' -i f`; it now
    reads every option.
74. The daemon ignored a `/ar:task pause` or resume written by another
    process (the hook's fallback when the daemon is unreachable, or a second
    daemon during a restart) until the value aged out of its memory. It now
    reads the pause from disk.
75. After a state write failed with a `sqlite3` or `OSError` error, the daemon
    kept returning the field's default in place of its saved value. These
    failures are now reported as state errors and the saved value is read
    again.
76. Every tool call wrote a staleness flag to disk even when it was already
    clear, and each write queued behind other sessions' writes. The flag is
    now written only when it changes, and dropping a value held only in
    memory no longer forces the daemon to re-read the session's saved state
    unless that value hid a saved one.
77. When an event's handlers ran past their 2-3 s budget on a loaded machine,
    the daemon told waiting sessions it had stalled and should be restarted,
    though those handlers were still finishing. A handler now counts as stuck
    only `daemon_dispatch_stall_grace_seconds` (default 30) past its budget. A
    session whose previous handler for that event is still overdue is told the
    daemon is busy and does not take a second slot.
78. Hooks that call the daemon directly from `hooks/hook_entry.py` sent no
    deadline, so the daemon could run a request after its hook had given up.
    They now send one.
79. On that direct path, a request the daemon accepted but never answered
    reported "autorun CLI timed out" with no next step. It now gets the same
    advice as the client: retry once, then run `autorun --restart-daemon`. A
    connection the daemon closed while restarting said retrying would not
    help; it now says to retry. A malformed answer still says retrying will
    not help.
80. The Pi, Prime and OpenCode bridge sent the daemon no deadline, so the
    daemon could finish a request those harnesses had already stopped waiting
    for. Every sender now passes its deadline as wall-clock seconds, which the
    daemon converts to its own clock on arrival: Node's and Python's
    monotonic clocks differ on macOS once the machine has slept. A hook
    wrapper from an older release sends a value the new daemon ignores, so it
    falls back to its own time budget.
81. On Windows a hook payload containing characters such as `ā`, `の` or `с`
    (in a command or a path) crashed the hook before any guard ran, and the
    harness treated the crash as a non-blocking error, so the command ran;
    `é` arrived garbled. Python reads piped input in the Windows code page,
    and harnesses send UTF-8. autorun now reads hook input as UTF-8 on every
    system, and its hook wrapper and CLI talk to each other in UTF-8.
82. When autorun could not be imported, or `AUTORUN_DISABLE` was set, the
    hook wrapper picked the harness from environment variables alone. A
    Claude Code session started from a Codex terminal (with `CODEX_THREAD_ID`
    inherited) then got Codex's deny at exit 0, which Claude Code ignores, so
    the tool ran. The wrapper now reads the harness from the hook's own
    payload first, as the main path does.
83. The hook wrapper and the autorun package could name different harnesses
    for the same hook: the wrapper checked ForgeCode and OpenCode variables
    before Pi's, and only it ignored `GEMINI_PROJECT_DIR` beside
    `CLAUDE_PROJECT_DIR`. Both now use the platform registry's order and one
    rule: a `*_PROJECT_DIR` variable names no harness while
    `CLAUDE_PROJECT_DIR` is set, since Gemini CLI sets that as an alias of its
    own. A transcript under `.claude/` now identifies Claude Code before any
    inherited variable does.
84. When the hook wrapper could not reach autorun on a non-tool event (a
    timeout or a missing install), Codex, Pi, Prime, OpenCode and Antigravity
    users saw nothing, the same as a hook with nothing to say. The wrapper
    now reports it in the field each harness shows, as the daemon client
    does. Qwen's Stop and SubagentStop no-op is now `{}`, the only reply its
    Stop schema keeps.
85. On Windows with `HOME` set to something other than the user profile
    (a mapped `H:\` drive is common), `autorun --install` wrote into `HOME`
    while Claude Code, Gemini CLI and the autorun daemon read the profile
    folder, so the install looked successful and did nothing. The installer
    now uses the same home as Python's `Path.home()` and Node's
    `os.homedir()`: `HOME` on Linux and macOS, `USERPROFILE` on Windows.

86. On Windows, Gemini CLI runs hook commands in PowerShell, which printed
    autorun's POSIX-quoted hook command as a string instead of running it, so
    no Gemini CLI hook ran. On Windows `autorun --install` now writes the Gemini
    CLI, Qwen Code and Antigravity hook commands in PowerShell form
    (`& 'C:\path\python.exe' 'arg'`). Qwen Code also needs the explicit
    shell selection described below; Antigravity's Windows shell remains
    unverified.
    Claude Code keeps the POSIX form that Git Bash runs. Codex's separate
    Windows command is item 117.
87. On Windows a virtual environment keeps its interpreter in
    `.venv\Scripts\python.exe`, but `hooks/hooks.json`, the hook wrapper and
    `autorun --restart-daemon` looked only in `.venv/bin`. Each Claude Code
    hook then paid a `uv run` start, and the daemon restarted under whichever
    Python ran the command. All three now check both locations.

88. On Windows, a user rule's `when:` command ran in `cmd.exe`, and on Debian
    and Ubuntu in `dash`, so a predicate such as `test -f package.json` or
    `[[ ... ]]` failed and the rule never applied. `when:` commands now run in
    bash: Git for Windows' bash on Windows, then the `bash` on `PATH`. Without
    any bash they run in the system shell as before.
89. On Windows, writing autorun's state or the daemon's port file failed with
     "Access is denied" whenever another process had the file open, such as
     a hook reading the port. autorun now retries the rename for up to one
     second, the bound pip uses for the same error.
90. On Windows, autorun read its own JSON files and the output of `uv`,
     `git` and `autorun --install` in the system code page (usually cp1252),
     so a path with characters outside it, such as a user name with `ā`,
     came back garbled or stopped the background dependency install. Every
     text file and child process is now read as UTF-8.
91. On Windows, where the daemon listens on `127.0.0.1` because Python there
     has no Unix sockets, any local process, including another user's,
     could send it requests such as an `/ar:ok` grant. The daemon now writes
     a random token to `daemon.token` beside `daemon.port` at each start and
     serves only connections that send it first; the hook wrapper, the
     autorun client and the Pi, Prime and OpenCode bridges all send it. On
     macOS and Linux the Unix socket in the owner-only `~/.autorun` is
     unchanged.
92. A hook reported the process that started it as its harness. Where a
     harness runs hooks through a shell that stays alive only for the hook
     (PowerShell for Gemini CLI on Windows, Git Bash for Claude Code, `sh -c`
     for the Gemini family elsewhere), that was the shell, so plan export took
     a running session for an ended one and could recover its plan into
     another session. autorun now walks past launcher shells to the harness.
93. Qwen Code never ran autorun's subagent check: its hooks are translated
     from the Gemini CLI template, which has no `SubagentStop`, although Qwen
     fires it and lets a hook block a subagent from finishing. Qwen now
     subscribes to `SubagentStop` with the same handler as `Stop`.
94. Codex's registry entry did not list `SubagentStop`, which autorun
     registers on Codex and Codex fires, so the two records of what Codex
     sends disagreed. The entry now lists it, and a test fails when the
     installer registers an event the registry does not.
95. When the daemon's reply to an event other than a tool call was not JSON,
     the client printed it to stdout as it was. Gemini CLI, Qwen Code and
     Antigravity read hook stdout as JSON and reported a hook error. The
     client now logs that reply and answers as if the daemon had nothing to
     say.
96. `/ar:cache status` reported the harness that started the daemon, not the
     session that asked, so a Codex session sharing a daemon with Claude Code
     saw `cli: claude`. It now reports the calling session's harness.
97. On Windows, `autorun --restart-daemon` killed the daemon before graceful
     cleanup could run, leaving its port, token, and PID files behind. Windows
     restarts now request shutdown over the existing authenticated IPC transport,
     then fall back to terminate and kill. On POSIX, where terminate delivers the
     daemon's graceful SIGTERM, restarts send that ownership-checked signal first
     and use the IPC request if signalling is denied or shutdown times out.
98. In a checkout where Git stores symlinks as text files
     (`core.symlinks=false`, common on Windows), the source distribution and
     wheel left out the `pdf_extraction` package, so `extract-pdfs` could not
     import. This included git and local-path installs, which build the wheel
     without an sdist. Both builds now read the link target from that file and
     copy the package, and refuse to stage into the source tree.
99. On Windows, Qwen Code ran autorun's PowerShell hook command through
     `cmd.exe` or Git Bash, so the hook failed. Generated Qwen hook entries
     now explicitly select PowerShell, including for custom Qwen flavors.
100. A hook started through `uv run` reported the short-lived `uv` process as
     its harness, and on Windows the venv's `python.exe` redirector as well.
     After the hook exited, plan recovery treated the running session as ended.
     autorun now walks past both to the owning harness.
101. A TCP connection that sent no valid token could make the daemon wait for
     a newline while buffering up to the 1 GiB request limit. Authentication
     now reads only the token and its terminating newline, rejects a mismatch,
     and closes incomplete or timed-out connections.
102. On Windows, starting the daemon failed with "Access is denied" if a
     client still had the old `daemon.token` open. Removing that token now
     uses the same bounded retry as replacing state and port files.
103. A stopping daemon kept alive by a worker thread ran its cleanup a second
     time at exit and deleted the port, token, and PID files of the daemon that
     had replaced it, leaving that daemon unreachable. Cleanup now runs only
     while the daemon holds its lifecycle lock, and removes the PID file before
     releasing it.
104. On Windows, a `claude`, `gemini`, or `qwen` registration command could
     fail with `FileNotFoundError` when the CLI was installed through npm,
     which provides a `.cmd` shim. autorun now resolves the command on `PATH`
     before running it.
105. A `when:` command could run past its 2-second limit by up to 10 seconds
     per bash candidate, because finding bash had its own timeout. On Windows a
     slow WSL launcher could outlast the hook. Finding bash and running the
     command now share the one limit.
106. On Windows, Codex runs a hook's `commandWindows` through `cmd.exe /C`,
     falling back to `command`, but autorun wrote only the POSIX `command`.
     The user hooks file and the bundled plugin now both include
     `commandWindows`, which runs PowerShell with an encoded script so
     `cmd.exe` cannot reinterpret path quoting. The plugin's POSIX command now
     reads `"$CLAUDE_PLUGIN_ROOT"`, which Codex exports, so a plugin root with
     spaces or apostrophes survives.
107. If a replacement daemon acquired `daemon.flock` during the old daemon's
     cleanup, the old daemon could unlink the replacement's lock file. Cleanup
     no longer unlinks that path after releasing the lock.
108. Installing from a source checkout left Claude's cached hooks running
     whatever the cache's `.venv` already held, such as a stale editable
     install that followed checkout edits. Installation now puts a
     non-editable copy of the checkout in that `.venv`, built from staged
     inputs so no build artifacts land in the checkout, and points the cached
     hook commands at its interpreter with Python path overrides cleared. A
     matching receipt skips reinstall only when its schema and non-editable
     mode match, the venv was not created or repaired in this run, and that
     venv's interpreter imports autorun from inside the venv. Existing managed
     files are kept; a foreign interpreter or a cache directory that is a
     symlink is refused. Wheel installs still run hooks from the tool's own
     non-editable interpreter.

109. Install settings written in `autorun.config.json`, such as
     `codex_hook_source`, `skill_placement`, `shared_skills_bridge` and
     `hook_python`, were ignored: the file only applied keys the runtime
     declares, so every install setting resolved to its default. Because
     `autorun --update` and the hook bootstrap reinstall with no flags, a
     choice made with a flag was reverted by the next update. The installer
     now reads those keys from the file, below flags and `AUTORUN_*`
     variables; an invalid value falls back to the default, and an absent
     file, as before a first install, changes nothing.
110. Stop treated every open dependency and delegated task as work the current
     agent could do, while timer-based delegation expiry could return live work
     to pending. Stop now separates actionable work from waiting work and ends
     a delegation only on a matching completion, process-liveness, or task
     update signal.
111. A task waiting for a user decision either blocked Stop indefinitely or
     required a global pause. The `awaiting-user` status now stores the
     decision and previous status, survives Pi reprojection and downgrade, and
     is restored by a newer user prompt or an explicit task update.
112. Forks and side conversations could receive a parent's task list without a
     durable ownership boundary. A fork now inherits only task ids that existed
     at the fork boundary; tasks it creates remain enforced in that
     conversation, and an inherited task becomes its responsibility only after
     it updates the task.
113. The task table's status constraint could reject a new task status, and a
     defensive layout repair could copy every task during daemon startup.
     Schema revision 3 removes status policy from the table definition, rewrites
     supported metadata in place, and uses a compatible fallback encoding when
     SQLite refuses the rewrite. The optional full rebuild is offline only.
114. Rolling state authority back from SQLite to JSON omitted task rows stored
     only in SQLite. `autorun --state-rollback` now exports current task rows to
     JSON before changing the backend.
115. Qwen and Antigravity could lose autorun notices on an allowed Stop because
     their native response carriers differed from Claude's. Qwen now uses
     `systemMessage`, and Antigravity preserves deferred notices through its
     own output contract.
116. OpenCode missed prompt, Stop, and compaction enforcement or could classify
     an originless frame as user input. The bridge now handles those lifecycle
     routes, treats lookup failure and originless frames as automation, and
     caches bounded session-parent lookups.
117. Prime 0.10 rejected the Pi extension package as incompatible. Installation
     now detects that runtime and publishes supported guidance and skills
     without claiming hook enforcement.
118. A forced same-version Claude registration could withdraw the active cache,
     replace its version tree or `.venv`, and lose edited files. Refresh now
     updates proven payloads in place, records ownership in `.autorun-owned`,
     backs up files detected as edited or unproven when selection runs,
     recovers interrupted refreshes, and leaves harness-owned metadata alone.
119. `autorun task gc` rejected SQLite state, and the JSON path could accept a
     negative TTL or require a session identity in a plain terminal. SQLite GC
     now expires eligible whole sessions with archive-before-delete; it refuses
     negative TTL, non-default patterns, and `--no-archive`, while protecting
     unfinished and identifiable current sessions.
120. The Claude cache-parent lock could wait forever while another installer
     ran pip. Its timeout now resolves from the command line, environment,
     `autorun.config.json`, then a 600-second default. Accepted values are
     greater than zero and at most 86400 seconds; invalid values name the key,
     rejected value, and range instead of silently using the default. The
     installer prints the cache path while waiting and, on timeout, names the
     lock, elapsed time, and wait-and-rerun action.
121. Uninstall left every proven file when a cache version also contained an
     edited or unproven file, then treated the preserved tree as a failure. It
     now removes hash-proven unchanged payload and autorun metadata from regular
     version directories, keeps edited and unproven files, and keeps `.venv`
     when the version tree remains. Symlinked and Windows reparse-point versions
     are reported and never traversed; manifest files beneath a replaced linked
     directory are also kept and reported. The cache `.autorun-install.lock`
     and its parent directories remain after install, refresh, and uninstall so
     concurrent installers retain one lock authority. I/O failures still fail.
122. Two installs that reached daemon restart together could leave one install
     exiting 1 because the other held the restart lock. Each install now
     snapshots the validated daemon record before waiting and keeps a healthy
     daemon only when that record changed while it waited, proving the
     concurrent installer replaced it. An unchanged or unproven daemon is
     replaced regardless of age, source mtimes, or wall-clock movement. A
     remaining failure carries its reason and tells the user to wait and rerun.
123. A second identical plain install printed no Claude cache result. It now
     prints an ok line naming the cache directory as already current.
124. A forced cache refresh that created backups and then failed during a later
     runtime step omitted those backup paths from the failure output. It now
     reports every backup it created on both success and failure paths.
125. A session whose handler had just timed out could take a second handler
     slot when the daemon was busy, because the slot's deadline was recorded
     after the handler was scheduled, later than the deadline the request
     gave up on. The slot now carries the request's own deadline, so the
     session's next request is turned away until the hung handler ends.
126. A restart while many sessions were active asked the loaded daemon's event
     loop to shut down before sending POSIX SIGTERM. Stopping the old daemon
     took longer, so more hook calls reached their 5-second deadline. POSIX
     restarts now send the ownership-checked graceful signal first and use IPC
     only if signalling is denied or shutdown times out. Windows remains IPC
     first because terminate there cannot run the daemon's cleanup.

### For contributors

1. `installer/fs.py:decide` publishes into an existing directory that has no
   ownership marker and no entries, and reports `adopting empty directory`.
   `test_install_fs.py` covers it, including that an empty directory reached
   through a symlink stays kept and that uninstall is unchanged.
2. `installer/orchestrate.py:perform` asks `steps.switches_for` again, after
   registration in an install, for each harness that had no config directory
   before it; `qwen extensions install` is what creates `~/.qwen`. A harness
   whose directory existed still gets its switch before registration, so autorun
   stays the creator of a settings file such as `~/.codex/config.toml` and
   uninstall still removes it. `test_install_steps.py` covers both, and that no
   harness directory is created for the switch alone.
3. Facts that had several hand-kept copies now have one owner each: the
   daemon's lock paths (`ipc.daemon_flock_path`,
   `ipc.daemon_restart_lock_path`), the tool-gate events and the payload
   detection table (the platform registry, with `hooks/hook_entry.py`'s stdlib
   copies pinned to it by tests), the harness no-op in `hook_entry.py`
   (`_unhandled_response`), the bug-workaround grammar
   (`config.workaround_applies`, now also used by the installer), the Codex hook
   events (`steps._CODEX_EVENTS`), link ownership (`fs._link_is_ours`),
   per-file collisions (`fs._collisions`), the default PDF backend order
   (`BACKEND_REGISTRY`), task statuses (`task_status.STATUS_POLICY`), the
   `/ar:ok` and `/ar:no` pattern grammar (`plugins._parse_pattern`), and the
   scope grammar (`scoped_allow.parse_scope_tokens`), the env-toggled guards
   (`config.ENV_TOGGLED_GUARDS`), the process-name match
   (`tmux_utils.is_named_process`), and uv's tool directory
   (`installer.discovery.uv_tool_root`). `main.py`'s unused
   copies of the pattern parser, regex check, matcher, and justification test,
   which the tests exercised instead of the live guard, are deleted.
4. `hooks/hook_entry.py:main` now runs `try_cli`; it carried an inline copy, so
   the tests of `try_cli` tested code no hook ran. Both of its paths send the
   launching process as `_pid`, and the three detection cases
   `test_multi_platform_detection.py` pinned as strict xfails now pass.
5. `config.detect_cli_type` no longer reads the generic `session_id` key as a
   Gemini CLI payload, and `python_check.check_python_version` returns False on
   Python 3.0 to 3.9, as it already did on Python 2.
6. `EventContext.harness_pid` carries the hook payload's `_pid` (the process
    that launched the hook) from the daemon and `run_direct`; plan export
    records it on each pending plan. `execute_tmux_command` never creates a
    session (`ensure_session_exists` is the one creator) and keeps a caller's
    `-t`. When an integration's `when` predicate rejects one of its patterns,
    the next pattern is still checked, so a user rule file with several
    patterns and a `when` can now block more often, never less.
    `pdf_to_txt`'s unused `remove_empty` parameter is gone and its options
    after `resume` are keyword-only.
7. Antigravity's PascalCase tool-argument names moved from a
   `cli_type == "antigravity"` branch in `core.normalize_hook_payload` to
   `Platform.tool_input_aliases`. `test_bridge_timeout_order.py` pins the
   JavaScript bridge's 5 s timer above the hook wrapper and daemon-response
   timeouts in `CONFIG` for Pi, Prime and OpenCode.
8. Windows CI runs tests it used to skip. The skill-audit tests run
   `audit-skill.sh` under Git for Windows' bash (`platforms.posix_shell`), and
   the script accepts Python 3 as `python3` or `python`, passing over a
   `python3` that cannot run it, such as the Windows Store alias. The Pi
   bridge tests' stub daemon listens on loopback TCP with `daemon.port` and
   `daemon.token` where AF_UNIX is missing. Subprocess tests build their
   environment with `conftest.minimal_env`, which keeps `SYSTEMROOT`; without
   it `import asyncio` fails on Windows with WinError 10106. The
   release-artifacts job runs on Windows as well as Ubuntu, and `.gitattributes`
   keeps `*.sh` and `*.md` at LF in Windows checkouts. The tmux detection test
   now runs its inside-tmux branch in CI too.
9. `test_windows_hook_launch.py` writes each harness's hook through its install
   route, then runs the command the way that harness does: Claude Code's
   (Git) Bash, Codex's shell or `cmd.exe /C`, Gemini CLI's and Qwen Code's
   PowerShell or bash, each cited to its source. The Windows CI job runs the
   Windows launchers. Antigravity is listed as unverified: neither its
   documentation nor any public source states how it starts a hook on
   Windows.
10. Pytest now redirects `HOME`, `USERPROFILE`, and Pi's config directory
    before importing autorun, and clears inherited harness relocation,
    detection, session, version, task-tool, and autorun runtime overrides.
    Explicit E2E test controls remain available. Clearing the environment in
    a test preserves those redirected homes.
    The documented shell sandbox starts with `env -i` and shares uv's cache,
    so a live session's `CODEX_HOME` or other override cannot escape it.
    Daemon tracking reads the worker's birth-validated PID record instead of
    claiming every process that appeared during a global scan. A recorded PID
    with a different runtime is spared; an unreadable environment requires a
    current matching record. Pi, Prime, and OpenCode installer fixtures reuse
    the worker's runtime instead of naming shared `/tmp` directories.
    The socket-path guard measures encoded bytes, including non-ASCII paths.
    Multiprocess tests use loopback manager proxies so a long scratch path
    cannot prevent the lock assertions from running, and shut each manager down
    after use. IPC tests keep token creation and cleanup private, and lifecycle
    probes publish stand-in artifacts in their own fixture directories.
    The inherited Claude project alias and uv project, tool and Python paths
    are cleared before they can redirect a test into a developer installation;
    the shared uv cache stays available.

## [1.0.0rc3] - 2026-10-06

Coding agents have started shipping without their task tools by default, and
autorun's task gates could then deny every tool call until the agent made a
task call it had no way to make. This hit Claude Code on current models, Qwen
Code, Gemini CLI, Codex, and Pi or Prime Agent subagents without task tools. The
gates now enforce only where autorun can tell the session has the tool, and
`autorun --install` turns the tools back on where the agent has a setting for
it. 1.0.0rc2 reached TestPyPI but was never released, so its changes ship here.

### Upgrading from 1.0.0rc1

`autorun --install` republishes the hooks and extensions each harness loads,
restarts the autorun daemon, and turns on the task tools of Claude Code, Qwen
Code and Codex through one setting each, unless you set them yourself (see
Changed).
`autorun --status` then lists each setting as `current`, or as
`kept your setting` when the value is yours. Upgrading the package without it
leaves every harness on 1.0.0rc1. For a Claude Code marketplace-only install,
the setting to add is `"CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"` in the `env` object
of `~/.claude/settings.json` (create `env` if it is missing). If your Qwen or
Codex settings file cannot be parsed (for example, a settings file with
comments), the install leaves it alone and its output names the setting to add.
Task state, configuration, and `/ar` commands carry over unchanged.

Whether autorun enforces task tracking after the upgrade:

1. Claude Code: yes, once `autorun --install` has set
   `CLAUDE_CODE_ENABLE_TODO_TOOLS`. Without it, yes from a session's first task
   call, which only sessions on older models can make.
2. Qwen Code: yes while `tools.todoWrite.enabled` is `true`, or from a session's
   first `todo_write` call.
3. Gemini CLI: from a session's first `write_todos` call.
4. Codex: no. autorun records Codex checklists but never requires them.
5. Pi and Prime Agent: yes, in any session that has the task tools.
6. OpenCode: yes, as in 1.0.0rc1; its `todowrite` tool is built in.
7. ForgeCode: no. It has no task tools to track.

### Changed

1. Claude Code: `autorun --install` adds `CLAUDE_CODE_ENABLE_TODO_TOOLS=1` to
   the `env` object in `~/.claude/settings.json`. Claude Code 2.1.268 and later
   offer TaskCreate and the other task tools only to older models without it. A
   value you already set is kept, and `autorun --uninstall` removes only the one
   autorun added. To keep the tools off, set it to `"0"`; deleting the line does
   not opt out, because the next install adds it back. Claude Code versions
   before 2.1.268 ignore the setting. A session that starts without task
   tracking enforced is told so.
2. Qwen Code: `autorun --install` sets `tools.todoWrite.enabled` to `true` in
   `~/.qwen/settings.json`, keeping a value you set, because Qwen Code 0.24.0 and later
   register `todo_write` only with it, and autorun now counts `todo_write` as
   task progress. autorun enforces Qwen task tracking only while that setting
   is `true` or the session has made a task call; set it to `false` to keep the
   tool off. A settings file that cannot be parsed, such as one with comments,
   is left untouched, and the install output names the setting to add.
3. Gemini CLI 0.36.0 and later give the default model no `write_todos`, so
   autorun requires it only once the session has called it.
4. Codex: `autorun --install` sets `[tools.update_plan] enabled = true` in
   `~/.codex/config.toml`, keeping your comments and layout, because Codex
   0.152.0 and later leave `update_plan` off without it. autorun no longer asks
   for or requires `update_plan` calls: Plan mode rejects the tool even when it
   is on, and Codex hooks cannot tell, so a required call could leave every
   following tool call denied. autorun still records the checklists an agent
   keeps, and its stage completion markers and destructive-command guards
   still apply.
5. The instructions behind `/ar:plannew` and the other plan commands tell the
   agent to use task tools only when the session has them. They previously said
   Pi's task tools were always present.
6. When autorun blocks a stop, it now tells an agent that believes a task
   cannot be done to re-check that first, and that discarding the task with a
   stated reason is then allowed. Agents had looped on such tasks, reading the
   instructions as "only completed tasks may be cleared".

### Fixed

1. A Pi or Prime Agent subagent without `TaskCreate` or `TaskUpdate` is no
   longer blocked. The task gates required one of those calls, so every tool
   call after that was denied. The gates now skip a session that lacks the tool
   they would require, with or without the daemon running.
2. Clearing a Codex checklist now clears autorun's record of it. An
   `update_plan` call with an empty list was ignored, so autorun kept the old
   tasks.
3. A subagent that has finished is no longer told to keep working. autorun sent
   it the instructions for continuing through its stages while the parent
   session was already waiting for its result.
4. Pi's `TaskCreate` needs only a subject. It required `description` and
   `activeForm`, models often left them out, and the call failed without
   creating a task.
5. Plan commands keep the text typed after them. `/ar:pn add a login page` could
   produce plan instructions without "add a login page". The same applied to
   `/ar:pr`, `/ar:pu`, `/ar:pp`, and their long names such as `/ar:plannew`.
6. `autorun --status` no longer lists `would merge ~/.codex/hooks.json` right
   after an install. A hooks file that needs no change now reads `current`.
7. With `AUTORUN_HOME` set, the hook debug log and the backups
   `autorun --install --force` makes now go there. They went to `~/.autorun`,
   where recovery from an interrupted install did not look for the backups.
8. `autorun --uninstall` removes the `autorun` marketplace entry it added to
   Claude Code's settings when you uninstall every autorun plugin. It stayed
   behind before; a partial uninstall still keeps it for the other plugins.
9. The Pi, Prime Agent and OpenCode extensions no longer exit right after
   allowing a tool call while the autorun daemon is unreachable.
10. The first hook after the autorun daemon stops no longer runs out of time
    on Windows. On every platform it started one duplicate daemon every tenth
    of a second while the first was still loading; on a Windows CI runner cold
    starts measured 1.3 to 3.3 seconds against a 4-second budget and now take
    0.6 to 1.2.
11. When a Qwen Code or Gemini CLI hook runs out of time, it now says
    `[autorun] autorun CLI timed out after 4s` instead of silently letting the
    agent continue, as Claude Code's already did.
12. A hook that runs without the daemon (while it starts, or with
    `AUTORUN_USE_DAEMON=0`) takes about a third less time: the shell-command
    parser now loads only when a shell command needs checking.
13. autorun no longer drops a session setting (such as whether autorun is
    active) when several hooks write state at once. The write gave up after
    half a second instead of using the rest of the hook's time.
14. On Qwen Code, Gemini CLI and OpenCode, autorun's stop and reminder
    messages name the agent's own list tool (`todo_write`, `write_todos`,
    `todowrite`) and ask for the full list. They named per-task calls such as
    `tracker_update_task(id="X", ...)`, which those agents do not have by
    default, so a blocked stop could ask for an action the agent could not
    take.
15. `autorun --status` no longer hangs for two minutes and then reports a
    healthy hook as `FAIL hook command` when run from a script, CI job or an
    agent's shell tool. Its hook check waited for input that never came.
16. `autorun --install` lists every harness registration it made, such as
    `ok   claude: claude plugin install ar@autorun`. A successful Claude Code
    plugin install printed nothing, and a repeat install printed "merged" for
    a Codex hooks file it had not changed.
17. When a skill is not updated because a copy autorun does not own is in the
    way, the install prints that directory's full path and how to take
    autorun's copy instead. It named only the skill.
18. A Claude Code session that switches with `/model` is no longer held to
    task tracking by task calls the earlier model made, which kept a model
    without task tools blocked. Tracking resumes once the new model makes a
    task call, or at once when `CLAUDE_CODE_ENABLE_TODO_TOOLS` is set.
19. A Qwen Code `settings.json` with comments is read the way Qwen Code reads
    it, so `tools.todoWrite.enabled` set there counts.
20. `autorun --install` stops with an error naming the file when a
    `settings.json`, `hooks.json`, `AGENTS.md` or `CLAUDE.md` it edits is a
    symlink to a missing file; it had created the missing file wherever the
    link pointed. Uninstall skips such a file and removes everything else, and
    an uninstall from a linked memory file that held only autorun's section
    keeps the link.
21. `autorun --install` keeps the indentation, line endings, final newline and
    `\u` escaping of the settings files it edits, and `autorun --uninstall`
    leaves an empty `env` or `[tools]` you already had. Each edit had
    reformatted the whole file with two-space indents and LF endings, and
    uninstall removed those empty sections. JSON arrays written on one line
    inside an indented file are still spread over several.
22. Claude Code hooks from a PyPI install now run in the Claude desktop app
    and anywhere else uv's tool directory (`~/.local/bin`) is not on PATH.
    They ran `autorun --cli claude` through PATH, failed with "command not
    found" before autorun could log anything, and so enforced nothing there.
    1.0.0rc1 had the same defect. `autorun --install` now writes the full path.
23. On Windows, the Claude Code hooks autorun installs from a source checkout
    no longer break the hooks file. The plugin's path was pasted into it
    unescaped, and its backslashes made the file invalid JSON.

### For contributors

1. The release-consistency scans skip nested checkouts (any directory with its
   own `.git`), so a `git worktree` under `.claude/worktrees/` no longer fails
   them.
2. The TestPyPI rehearsal installs the uploaded wheel and sdist by exact URL
   after matching their SHA-256 digests to the build. The 1.0.0rc2 rehearsal
   failed because `uv` resolved 1.0.0rc1 from PyPI instead.
3. Pi bridge tests run under Bun and fall back to Node; CI pins the Bun version.
4. `scripts/release_notes.py` generates `docs/releases/<version>.md` from the
   release's CHANGELOG section and checks that section's structure.
5. RELEASING.md tags with `--cleanup=verbatim` so the annotation keeps the
   notes' headings, rehearses the sdist as well as the wheel, requires the
   release date to be the tag day, and rehearses the upgrade from the previous
   release in a scratch home.
6. The suite no longer writes the developer's real `~/.autorun` or leaves
   daemons and temporary directories behind, and it fails the run if it creates
   `~/.autorun`. Tests clear the environment with
   `isolated_environ.isolation_only()`.
7. Gate tests state whether the session has its task tools
   (`tests/task_tool_evidence.py`); they had assumed every Claude session has
   TaskCreate. The switch variables are cleared at startup, so a run inside an
   agent session gives the same results.
8. Release dates are New York calendar days.
   `scripts/release_notes.py --today` prints the day wherever the releaser is;
   RELEASING.md uses it in place of `date +%F` and tags under
   `TZ=America/New_York`.
9. The test suite finds and stops test daemons on Windows too; before, every
   daemon a Windows test started outlived the suite.
10. The release-artifact tests reuse uv's cache instead of downloading every
    dependency into a new one: the full suite runs in about 40 seconds on 12
    workers instead of five minutes, and those tests no longer time out under
    load.

## [1.0.0rc1] - 2026-08-23

### Fixed

- **Retiring a superseded distribution no longer deletes the `autorun` and
  `extract-pdfs` commands.** `uv tool uninstall` removes console scripts by name
  across the one shared uv bin directory, so retiring an old name took the copy
  the current distribution provides. With `autorun-ai` installed, `uv tool
  uninstall autorun` reported "Uninstalled 3 executables: autorun,
  autorun-install, extract-pdfs". The install-time sweep now keeps any
  uv-installed distribution whose script is one of autorun's own, and prints the
  command that reclaims its disk. The pip route still sweeps, since a
  pip-installed package owns its own environment. No published release carried
  the defect; `autorun-ai` is the first.

- **The TestPyPI rehearsal publishes instead of reporting success and
  skipping.** `publish.yml` skips its `test` job on a `workflow_dispatch` run
  and lets `build` through with `always()`. A skipped job propagates down the
  whole `needs` graph rather than only to its direct dependents, so
  `publish-testpypi` (which carried no condition of its own) was skipped along
  with `test` while the run still concluded `success`. The rehearsal exists to
  exercise trusted publishing before the permanent write to the real index, and
  it uploaded nothing: a green run proving only that nothing had been
  attempted. Both publishing jobs now name the result they require instead of
  inheriting one, and `test_publishing_jobs_state_their_own_precondition` fails
  on any job that reaches an index behind an inherited gate.

- **A config file that names one thing no longer erases the rest.** The user
  config tier overlaid a file's value by assignment, so for the settings that
  hold a dict, naming one key replaced every other key. Two of those dicts make
  that severe. `default_integrations` is the safety-guard table, so a file
  adding a single integration of the user's own silently removed all 48 shipped
  guards (`rm`, `dd if=`, `fdisk`, `bfg` among them) and nothing reported it; a
  disarmed autorun is indistinguishable from a working one until something is
  already gone. The two per-harness timeout dicts failed the opposite way: a
  file naming `gemini` left `CONFIG[key]["claude"]` raising `KeyError` for
  every harness including `gemini`, and because that failure denies on a
  tool-gate event, a plausible config file blocked every tool on every harness.
  Dict settings are now merged onto their declared defaults, so omission means
  "no opinion" instead of "delete", while naming a key still overrides that key
  outright. Per-harness lookups additionally resolve through one helper that
  falls back to a declared value rather than indexing and raising.

- **An unresponsive harness CLI costs one timeout, not one per command.** The
  removal path runs with "never stop at a failure" so that withdrawing from a
  harness that no longer has the plugin is not an error, and a timeout arrived
  as an ordinary failure. An install on a machine whose `codex` binary answered
  nothing (not even `codex --version`) therefore spent four consecutive
  120-second timeouts on four `codex plugin remove` calls, eight minutes that
  told the user nothing the first failure had not. `Outcome` now distinguishes
  a timeout from a failure, and the commands that were skipped are named rather
  than dropped silently. This is the behavior `registration.py`'s own docstring
  already promised.

- **A daemon restart no longer makes Pi block every command.** The shared
  JavaScript bridge fell back to spawning `hooks/hook_entry.py` whenever the
  daemon was unreachable, then ran `JSON.parse` over its stdout. An allow
  writes nothing and exits 0 (silence is how the hook protocol spells "no
  decision") so `JSON.parse("")` threw and the catch denied the tool with
  "[autorun] hook entry returned an invalid response". Every restart, install
  or crash therefore turned into a wall of blocks. Empty stdout is now a
  non-decision; output that is present but unparseable still blocks, and a
  silent exit 2 still denies so the issue
  [#4669](https://github.com/anthropics/claude-code/issues/4669) workaround's
  stderr-only reason is not lost. One fix covers Pi, Prime and OpenCode, which
  share the bridge.

- **A blocked session is told an exit it can actually take.** When the daemon
  could not evaluate a permission gate, the deny said to run `autorun
  --restart-daemon` and retry: a Bash call the same gate was blocking, and a
  retry for a state that does not clear by waiting. The reason now names
  `AUTORUN_DISABLE=1`, the one exit that does not require a tool call.
  `hooks/hook_entry.py` already had this guidance; the client path, which is
  the one a state-backend failure actually takes, had its own copy that never
  received it. A test pins the two equal.
- **A full disk no longer switches every hook off.** `Handler.handleError`
  writes a traceback to `stderr` by default, and Claude Code discards the
  response of any hook that writes to stderr, so an out-of-space log write
  silently disabled command blocking, file policies and task enforcement while
  the session still looked healthy. All logging now goes through one
  exhaustion-tolerant handler; a spec check fails the build if a raw
  `RotatingFileHandler` is constructed anywhere in `src/` or `hooks/`. It found
  a third call site whose own comment already promised no stderr handler.
- **A storage failure says what to do about it.** SQLite reports a full disk, a
  missing directory and a permission problem with overlapping wording, so
  "unable to open database file" was not actionable. The message now names the
  plausible causes and states that recovery is automatic. Nothing latches, and
  the restart a reader would otherwise reach for is itself a blocked tool call.
- **Bug-workaround flags resolve through one grammar.** Four hand-written
  copies existed and two had drifted: one decided applicability with a
  hardcoded `cli_type == "claude"` instead of asking the platform registry, and
  one lowercased without stripping, so ` always ` worked for two flags and not
  the third. The same fix closed a second site that gated the deferred
  task-tool instruction by name.
- **Recovery messages name paths that exist.** `core.py` pointed at
  `plugins/autorun/scripts/restart_daemon.py`, which has never existed and
  which an installed user has no checkout to run anyway. A spec check now
  AST-walks every string in `src/` and `hooks/` and fails on a repo-relative
  path that does not resolve.
- **Orphaned SQLite stage sidecars are swept.** A `-wal`/`-shm` pair whose
  staged database is gone proved nothing and no later run recognized it,
  because each migration picks a fresh generation suffix. A failed migration's
  stage is still kept as evidence.

### Added

- **Bug-workaround flags accept harness version ranges** (`>=2.1.233,<2.2`),
  compared with `packaging` and resolved per invocation, since different
  harness builds run concurrently on one machine. A range only ever narrows an
  affected platform; an unknown version keeps the previous behavior and a
  malformed range falls through like a typo, so neither can silently disable a
  workaround.
- `log_file_max_bytes` and `log_file_backup_count` in CONFIG, replacing two
  duplicated copies of the same literals.
- `AUTORUN_HARNESS_VERSION` tells autorun which harness build it is running
  under, which is what makes version-ranged workarounds usable. Automatic
  detection is declared per harness in the platform registry and is empty for
  every harness today: Claude Code publishes no version in its environment or
  hook payload, and the SDK version that is present reports a different
  component. An unknown version keeps the pre-range behavior rather than
  guessing from a number that merely looks related.

### Changed

- `session_manager.state_directory` is the one place that decides where session
  state lives. Four modules each re-derived it, two with character-identical
  code, so the production default was stated in four places and the test
  isolation depended on each of them honoring the same variable. The default is
  unchanged: relocating it is a daemon-quiesced migration, not an edit. The
  same sweep fixed a diagnostic that told users to `chown ~/.claude/sessions/`
  even when the failing directory was somewhere else.
- A version range written into a CONFIG entry now means what the same value
  means in the environment. CONFIG was only tested for truthiness, so a range
  there silently stayed on for every version.
- Settings resolve **CLI parameter > environment variable > config file >
  default**. The file tier is new: an optional `autorun.config.json` under
  `AUTORUN_HOME` is overlaid onto the defaults at import, so it reaches every
  setting at once rather than wherever a call site remembered to consult it. An
  absent file (the normal case) changes nothing. An unknown key or a mismatched
  type is declined rather than accepted, because a typo that silently became a
  setting would make `autorun --status` report a value nothing reads, and an
  unreadable file leaves the defaults alone rather than stopping autorun from
  starting.

### Changed

- Upstream issue references carry full URLs. autorun integrates with several
  projects whose issue numbers are sequential and overlapping, so a bare
  `#80305` identified nothing; a spec check now requires any file naming an
  issue number to resolve it.
- The advisory-state sweep stops at the first live entry instead of scanning
  every entry on every write. Advisory writes happen on the hook path, so the
  cost of one write no longer grows with how many entries the daemon holds.

- **An interrupted install no longer deletes a file or link it moved aside.**
  Recovery from a killed publication put the staged copy back when the target
  was gone, and parked it under `~/.autorun/installer/backups/` when the target
  had been recreated, but only for a directory, because the parker copied with
  `copytree`. A regular file or a symlink was deleted with the abandoned stage
  instead. Antigravity's `import_manifest.json`, which lists every plugin the
  user has imported, reaches that path through the native-CLI rollback.
- **A tree written at the target while an install was copying is kept.**
  Ownership was decided before the source copy, which for a real skills bundle
  takes seconds, and anything the user wrote at the target during that window
  was renamed into the staging directory and deleted with it. The target is now
  renamed aside first and identified afterwards, so a replacement is parked
  rather than discarded. The same change removes the window between
  `withdraw_link`'s final ownership check and its removal: the link is renamed
  out of the way and judged there, then restored or parked if it turns out not
  to be autorun's.
- **`/ar:ok <pattern> <count>` counts concurrent commands correctly.** The
  remaining-use count was read, decremented, and only then written inside the
  state lock, so two commands arriving together both read the same value and
  both were allowed. One use admitted two commands. The read, the validity
  check, and the decrement now happen in one locked transaction, and a call
  that loses the race falls through to the block rules. The parallel-invocation
  grace window for a single command is unchanged.
- **`git checkout <file>` is judged in the session's repository.** The
  backward-compatible `_checkout_targets_file_with_changes` predicate, which a
  user's own integration file may name, tested `os.path.exists` against the
  daemon's own working directory rather than the session's, compared the first
  argument against the literal `git` so `/usr/bin/git` and `git.exe` slipped
  past, and split the command string so only its first segment was examined.
  All three allowed a destructive checkout. It now delegates to the hardened
  parser the shipped rules already use.
- **An editable install on a network share resolves its own source.**
  `pip install -e \\server\share\autorun` records `file://server/share/autorun`,
  and the decoder discarded the URL authority, producing a local path on the
  wrong machine. The host is preserved; an empty authority and `localhost` still
  mean the local filesystem.

- **A nested test run no longer kills the outer run's private tmux server.**
  `conftest.py` gives the suite a private tmux server and reuses an inherited
  socket so one run never starts a dozen servers, but its teardown ran
  `kill-server` on that socket whether it had created the server or inherited
  it. A pytest process spawned by a test therefore destroyed the server its
  parent was still using: the outer run reported `server exited unexpectedly`
  or `no server running on <socket>`, windows created moments earlier were
  gone, and `send_keys` returned False. Three tmux workflow tests failed this
  way in roughly two of every five full parallel runs. Only the process that
  created the socket now tears the server down, and it also removes the socket
  file, which `kill-server` leaves behind whenever no server was listening.

### Changed

- **`plugins/autorun/Makefile` runs the documented commands.** Its test targets
  called `run_tests.py`, which `.gitignore` excludes and which was never
  committed, so they failed in any fresh clone; `install-deps` requested a `dev`
  extra that no longer exists, which pip reports as a warning and an exit code
  of zero, so `make ci` installed nothing and said nothing. Every target is now
  a thin alias over the `uv` commands CI and the release checklist already use,
  including CI's exact marker selection and its two `ruff` passes.
- **Every `uv run` in `RELEASING.md` passes `--locked`.** The rule that a
  candidate is validated against the graph `uv.lock` commits applied to CI
  alone, so a maintainer's pre-flight (the full suite, the artifact build, the
  benchmark, the post-install cache check) could resolve a different graph. One
  test now enforces it on the workflow and the runbook together.
- **Which tests cost money is now a query, not a file name.** Seven test
  modules each re-derived
  `os.environ.get("AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY", "0") == "1"`, so
  a paid test could be added with no gate and nothing would notice, and the
  only evidence that none had run was the absence of skip lines in a log. File
  names were no help in either direction: most tests in
  `test_*_e2e_real_money.py` are free local hook subprocesses, and paid tests
  also live in `test_claude_e2e.py`, `test_demo.py`,
  `test_gemini_before_tool_hooks.py` and `test_gemini_e2e_improved.py`.
  `tests/e2e_support.py:requires_real_money` is now the single gate and applies
  a registered `real_money` marker, so
  `pytest -m real_money --collect-only` lists the 26 billable tests by name and
  `pytest -m "not real_money"` proves a run collected none of them.
  `tests/test_real_money_gate.py` fails the build if a second copy of the gate
  appears.

### Added

- **Native Pi integration.** Pi installs an owned extension under
  `~/.pi/agent/extensions/ar/` with in-process tool vetoes, display-only
  commands, attributed continuation messages, bounded transcript projection,
  session lifecycle hooks, compaction events, and daemon-backed `TaskCreate`,
  `TaskUpdate`, and `TaskList` tools. Python remains the policy, task,
  persistence, ordering, and Stop-enforcement owner.
- **Prime Agent support as a Pi variant.** `prime-agent` (PrimeIntellect's
  build of the Pi coding agent) installs the same extension under
  `~/.prime/agent/extensions/ar/` with its own `cliType: "prime"` identity,
  `--cli prime` hook fallback, and `~/.prime/agent/AGENTS.md` guidance block.
  One template, one installer step, and one wire protocol serve both
  harnesses; `--prime` selects it alone.
- **Day units in scope durations.** `/ar:ok`, `/ar:globalok`, `/ar:cache`, and
  `/ar:task pause` durations accept `d` alongside `h`, `m`, and `s` (for
  example `2d` or `1d12h`) through the one shared `parse_duration` grammar. A
  zero count (`/ar:ok rm 0`, `/ar:cache ok 0`) is rejected with an error
  instead of granting an allow that could never activate.
- **OpenCode todo state enters the task lifecycle.** The OpenCode plugin
  forwards `todo.updated` events as `todowrite` receipts, so OpenCode's native
  todo list is mirrored into autorun's task status with its own ids and content;
  `cancelled` maps to `deleted`, an empty list clears only OpenCode-sourced
  records, and explicit task records are untouched.
- **Pi `TaskGet` and atomic `TaskUpdate` batches.** Pi and Prime gain a
  `TaskGet` tool, and `TaskUpdate` accepts a `taskUpdates` array applied in one
  lifecycle transaction with per-task confirmations.
- **Sequential Pi and Prime task ids, minted by the daemon.** `TaskCreate` in
  the Pi-family extension asks the daemon for the session's next id
  (`task_next_id_v1`, one above every numeric id recorded, never reused), so
  ids read `1`, `2`, `3` in tool results, `TaskList`, and
  `TaskUpdate(taskId=…)` (the same shape Claude Code's task tools use) instead
  of a 35-character `pi-<uuid>` string per call. When no daemon answers, the
  extension falls back to a `<cli>-<random>` id that the receipt then confirms
  or flags. `TaskList` and `/ar:task` list tasks in creation order rather than
  id-string order, so `10` no longer sorts before `9`.
- **Semantic XML regions in every shipped skill.** All 17 plugin skills, the
  Codex `$ar` catalog skill, the pdf-extractor skill, and the repo-internal
  maintainer skill wrap each major section in balanced, descriptive tags
  (`<purpose>`, `<requirements>`, `<workflow>`, `<output_contract>`, ...),
  Markdown inside and code in fences. `ai-skill-builder` 1.3.0 states the rule
  as SKILL-REQ004, `scripts/audit-skill.sh` fails a body with no region, an
  unbalanced tag, or a `## ` heading outside every region, its scaffolder and
  template emit regions, and `tests/test_skill_docs.py` gates every shipped
  SKILL.md on the same rules. `ai-skill-builder` also absorbs the standalone
  `engineer-agent-skills` package (P0 requirements SKILL-REQ001–013, the
  portable-standard vs runtime-extension matrix, claim audit, and per-host
  validation receipt in `references/portability-and-claim-audit.md`).

### Fixed

- **A counted allow is no longer spent twice by one command.** autorun runs
  twice for a single Bash command (its own hook, and the `rtk hook claude`
  entry that spawns another autorun) which is why grants carry the
  `session:tool:command` fingerprint of the call that consumed them. That
  fingerprint was recorded only on the use that reached zero, so at any higher
  count both invocations took the ordinary path and each decremented: `/ar:ok
  rm 3` bought one command, then a second, then blocked, and `/ar:ok rm 2`
  bought one. Every counted call is stamped now, and a repeat of the stamped
  call inside the grace window refreshes the stamp without decrementing; the
  rule `ScopedGrant.claim_once` already applied. A different command arriving
  in the same window still costs its own use. `/ar:cache ok N` grants are
  counted through the same code and were affected identically.
- **Five wrapper spellings no longer hide the command they wrap.** The
  transparent-wrapper grammar recorded the wrong arity for several options, and
  every mistake pointed the same way: the child command vanished and the guard
  allowed it. `sudo -k <command>` was treated as the credential-only form and
  discarded, though sudo documents that with a command it runs the command;
  `sudo -D <dir>` and `sudo -R <dir>` did not consume their directory, so the
  directory was read as the command; `chroot --skip-chdir` (which takes no
  value) swallowed NEWROOT; `env --block-signal`, `--default-signal` and
  `--ignore-signal` are documented with a bracketed value, so consuming the
  next word ate the command; and `env -a <name>` did not consume its value.
  Each option's arity now matches the tool that owns it.
- **The remaining predicates ask about the session's directory too.**
  `_has_uncommitted_changes` (registered under two names for user integration
  files) kept its pre-v4 body after its sibling became an alias: `git diff
  --quiet` with no directory, no scrubbed environment, and every error read as
  "no changes". It reported on whatever directory the daemon was started in,
  and a staged-only edit did not count as uncommitted work. It delegates to
  `_repo_differs_from_head` now, like `_has_unstaged_changes` already did. A
  user-written bash `when:` (`test -f package.json`) also ran in that same
  shared directory; it runs in the session's.
- **`git stash drop` is judged against the session's repository.** The guard
  that asks whether a stash exists ran `git stash list` in the hook process's
  own working directory, and the daemon serves every session on this machine
  from one process, started wherever it was started. So it answered for a
  repository the user was not in, both ways: a drop went through in a session
  whose stash was full because that other directory had none, and was blocked
  in a session with nothing to lose because it did. The first of those destroys
  work with no way back. It reads `ctx.cwd` now, with the same scrubbed
  environment and work-tree probe `_file_differs_from_ref` uses, and a probe
  that cannot answer blocks rather than permits; the rule the Time Machine
  predicate beside it already followed.
- **A Windows executable suffix no longer bypasses every command block.**
  `rm.exe -rf …`, `git.exe push` and `git.cmd checkout` matched no pattern,
  because the command name was read from argv[0] literally. That is the real
  filename on a platform this project tests in CI, so any block could be walked
  past by spelling it out. The one basename helper now takes off `.exe`,
  `.com`, `.bat` and `.cmd`, and splits on both path separators.
  `os.path.basename` splits backslashes only when it is itself running on
  Windows, and the hook parses the same command strings everywhere. The
  destructive-git and read-command predicates share that helper now instead of
  keeping their own copy.
- **An in-place `sed` is caught after the first `sed` and inside `sh -c`.**
  `_sed_modifies_files` asked for one segment's tokens, so
  `sed -n '1,20p' README.md && sed -i 's/old/new/g' README.md` was judged by
  the read-only invocation and the edit ran; `sh -c "sed -i …"`, whose argv[0]
  is the shell, was not seen at all. It now reads every command the way the
  Time Machine predicate beside it already did, which also recurses into shell
  bodies.
- **A destructive checkout or restore is caught wherever it sits in the line.**
  Both predicates read one segment (the first whose git subcommand is
  `checkout` or `restore`) and let it answer for the whole command. So `git
  checkout -- clean.py && git checkout -- dirty.py` was judged entirely by the
  file that had nothing to lose, and the write that discarded `dirty.py` ran
  unchallenged. The same shape hid behind a leading `git checkout -b` and a
  leading `git restore --staged`. Separately, `git restore` decided
  staged-versus-worktree by scanning the whole command string for the flag, so
  a `--staged` in an unrelated `echo` disarmed it. Every checkout and restore
  segment is judged now, and the restore flags are read from the restore's own
  tokens.
- **A git reached by absolute path no longer bypasses the checkout guard.**
  `command_matches_pattern` matched `/usr/bin/git checkout` by basename, so the
  pattern fired, but the predicate deciding whether the write would discard
  uncommitted changes compared the raw token, answered "nothing would be lost",
  and let the overwrite through. It compares the basename now, like every other
  layer.
- **Creating a branch is no longer blocked as a destructive checkout.** The
  `git reset --hard` block recommends `git checkout -b backup/<stamp>-wip` to
  save the work first, and a dirty repository is exactly the state that
  produces that block, so the recommended recovery was blocked too, with a
  message about discarding changes. `git checkout -b` and `--orphan` create a
  branch at the current commit and cannot overwrite working-tree content; git
  aborts rather than write over a local change. Both are allowed now. `-B`,
  which moves an existing branch ref, and `-f`, which really does overwrite,
  are unchanged.
- **A user-authored shared skill no longer produces a duplicate listing.**
  When `~/.agents/skills/<name>` holds the user's own loadable skill, the
  installer previously published autorun's same-named skill natively (for
  example into `~/.pi/agent/skills/`), so harnesses that read both routes
  listed the name twice. The native fallback now runs only when the blocked
  name would otherwise reach the harness by no route at all; the withheld
  copy is reported as a preserved conflict, and reinstalling retires
  previously published duplicates.
- **A harness-targeted install no longer retires the shared skills other
  harnesses read.** `autorun --install --claude` (or an empty selection)
  claimed nothing under `~/.agents/skills` because Claude loads its skills from
  the plugin package, so the retirement sweep removed every autorun-owned tree
  there as "no longer shipped": the copies Codex, Qwen, Pi, Prime, ForgeCode,
  and OpenCode load. Every shipped skill is now claimed at the shared root on
  every install, and an empty selection sweeps nothing.
- **A tree installed before file hashes were recorded is no longer stuck.**
  Such a tree (owned marker, `files: []`) that had drifted from the source was
  reported as "you edited files we installed" naming every file, and no
  reinstall could refresh it, so harnesses reading `~/.agents/skills` loaded
  stale bodies indefinitely. A plain install now keeps it, names only the
  files that differ from what autorun ships, and says to rerun with
  `--force`; `--force` republishes it after copying the current tree to
  `~/.autorun/installer/backups/<name>-<stamp>/`. The same tree on a route
  autorun no longer uses (or under `--uninstall`) had the same problem with
  no way out; it is now kept with the same explanation and retired by
  `--force` after the same backup. A recorded user edit is still kept by
  every path.
- **Trees on Antigravity's old write root are decided at last.** Installs
  between 2026-07-09 and 2026-08-08 also materialized the bundle under
  `~/.gemini/antigravity-cli/plugins/<plugin>`; today's Antigravity entry
  keeps its config under `~/.gemini/config`, and the retirement sweep visited
  only current config roots, so those trees were never listed by status,
  never kept with a reason, and never retired. A harness now declares roots
  it used to write to (`Platform.retired_config_dirs`), the sweep reads them,
  and such trees follow the rule above: kept with the `--force`
  explanation by a plain install, retired with a backup by `--force`.
- **Antigravity no longer stays on the first bundle it imported.** Agy's
  import manifest names the plugin but no source path, so a copy under
  `~/.gemini/config/plugins/ar` was accepted as autorun's only while it
  matched the generated source byte for byte. Once the source changed, a copy
  made before autorun stamped its marker was skipped on every install without
  a message. The receipt plus autorun's own content in the copy (hook commands
  running its hook entry, or a copied skill carrying its marker) now proves
  ownership, so the copy is refreshed and stamped; a same-name plugin that
  fails that proof is reported as a failed install step naming the path.
- **pdf-extractor is staged for Gemini, Qwen, and Antigravity again.** The
  staging step required a `gemini_template/` directory; pdf-extractor has no
  hooks and keeps `gemini-extension.json` at its root, so since the installer
  rewrite it reached no Gemini-family harness and its earlier
  materializations were never refreshed. A root manifest now makes the plugin
  directory the template, and a hookless bundle gets no `hooks/` tree and no
  `hooks` manifest reference.
- **Status and dry run list each shared skill once.** The walk decided the
  same `~/.agents/skills/<name>` intent once per harness that reads the shared
  root (six lines per skill on a default install) and re-hashed the published
  tree each time; identical intents are now decided once per run.
- **Python 3.10 installs resolve again.** The lock selected onnxruntime
  1.24.3 for the `<3.11` fork (via markitdown → magika in the `pdf` extra),
  a version that ships no cp310 wheels; a workspace constraint now holds
  onnxruntime below 1.24 for Python 3.10.
- **Cross-process message-claim test carries a hook deadline.** Without one,
  the first contended store-lock attempt fails open by design and slow
  Windows runners saw two claim winners.
- **PyPI publishing through one distribution.** `autorun-ai` is the only
  published package: PyPI prohibits the bare name `autorun`, so the packaging
  name carries the `-ai` suffix while the command, the import package, the
  marketplace plugin and the `/ar:` prefix are all unchanged. PDF extraction
  ships inside it: `pdf_extraction` and the `extract-pdfs` script are always
  present, and every extraction backend is an optional extra, so
  `autorun-ai[pdf]` is the ordinary PDF install and nobody else downloads an
  extraction library. `pdf-extractor` remains a separate plugin in every
  harness (its own manifest, command, and skill) it is simply not a separate
  Python package. The remaining extras are `pdf-gpu`, `pdf-llm`,
  `pdf-progress`, and `pdf-all`. The `pdf` extra uses maintained `pypdf` under
  the compatible `pypdf2` backend id. marker-pdf is omitted because it pins
  Pillow below the patched release; docling is constrained off macOS while its
  model stack pins transformers 4.x. `autorun --install` retires the
  `autorun-pdf-extractor` and `pdf-extractor` distributions that earlier
  versions installed. A tag workflow runs the complete CI gate, publishes to
  TestPyPI, then enters the protected PyPI environment through OIDC.
- **Shared Pi/OpenCode daemon transport.** Both in-process adapters use the
  packaged bounded JSON client while retaining their native event and response
  shapes.
- **Agent memory install.** `autorun --install` writes a sentinel-delimited
  guidance block into each harness's memory file: `~/.claude/CLAUDE.md`,
  `~/.codex/AGENTS.md`, `<forge>/AGENTS.md`. Content is per-harness: the Claude
  block covers two measured failure modes (context-capacity claims made without
  measurement, and stop-gate denials) that would be false statements on Codex.
  Only autorun's own block is ever written or removed; surrounding user content
  is untouched. Disable with
  `AUTORUN_BUG_CLAUDE_CODE_NO_TOKEN_COUNT_FOR_HOOKS_BUG_54673_WORKAROUND_ENABLED=false`.
- **`--claude-agents-skills {link,copy,none}`.** Bridges skills authored in the
  shared `~/.agents/skills` directory into Claude Code, which reads only its own
  config-dir skills folder. Defaults to `none`, so no install silently rewrites
  a skills directory. Refuses outright when the target skills directory is
  itself a symlink, because Claude Code stops loading user skills in that layout
  ([anthropics/claude-code#38051](https://github.com/anthropics/claude-code/issues/38051)).
- **Health checks in `--status`.** Six probes for install states that
  previously produced no signal at all: guidance written where the harness will
  not read it (`~/.codex/AGENTS.override.md` shadowing), broken skill links, a
  skill reaching one harness by two paths, an orphaned sentinel slug, a stray
  top-level key that makes Codex drop every hook in the file, and artifacts
  left by an interrupted uninstall. Advisory only: it reports, it never
  repairs.
- **Configurable install locations.** `shared_agents_dir`,
  `shared_agents_skills_subdir`, `shared_agents_plugins_subdir` and
  `codex_plugin_source_dir` in CONFIG. Install and uninstall read the same keys,
  so relocating one moves both.

- **Each notes component has its own switch and destination.** Accepted and
  rejected plans are now described by one table, so `/ar:pe accepted off` and
  `/ar:pe accepted dir <path>` work exactly like their `rejected` counterparts,
  a bare `/ar:pe <component>` toggles it, and `/ar:pe` reports every component's
  state and destination. A component writes only when both plan export and that
  component are on. Adding a component is a table row rather than an edit to the
  parser, the status text, the config dataclass and the defaults.

### Fixed

- **Failed installer preflight remains write-free.** The supported `filelock`
  range stops below 3.29.5, whose changed macOS lock-file lifecycle leaves an
  empty `.autorun-install.lock` after malformed shared-file preflight. The
  installer does not unlink potentially live locks.
- **Retired Gemini CLI checks are optional in deterministic runs.** An
  unresponsive externally installed Gemini CLI now skips the free registration
  probe unless legacy paid tests were explicitly enabled; direct hook tests
  remain active.
- **Pi task receipts report authoritative state.** Mutation results now show
  the Python-confirmed status and subject, including normalized and
  ghost-protected transitions. Task lists use one bounded, dependency-aware
  snapshot, and active-branch replay removes only Pi-sourced records.
- **Concurrent Pi task operations preserve lifecycle state.** Threaded,
  overlapping daemon-client, and spawned-process tests cover same-session and
  different-session writes, working-directory changes, state-root isolation,
  persistence, Stop blocking, and Stop release.
- **Direct JSON hook processes retain their lock-acquisition budget under
  contention.** They poll the shared state lock every 5 ms instead of the
  `filelock` default of 50 ms. The 500 ms hook deadline is unchanged, but
  four/eight-writer reminder and message-delivery bursts no longer discard most
  of their budget between attempts under coverage load.
- **Source-checkout installs no longer publish test artifacts.** `.coverage*`,
  `coverage.xml`, `htmlcov`, and `.ruff_cache` are excluded from plugin trees,
  and `--install --force` bounds modified-file detail to ten names plus the
  omitted count.

- **A failed Claude registration no longer deletes the cached hook runtime.**
  The cache fallback introduced at `c09c3b2` ran whenever native registration
  failed, even when Claude's versioned cache already existed. Its atomic tree
  replacement intentionally omitted `.venv`, removing Claude's managed
  packages. Fallback now fills only an absent cache, checks existence while
  holding the cross-process publication lock, uses a non-replacing rename when
  racing Claude's native installer, and preserves a concurrently or previously
  populated cache verbatim.
- **A broken runtime no longer tells every attached session to retry forever.**
  When `import autorun` failed, `fail_closed_tool_gate` correctly denied every
  tool call, but its retry advice could never succeed. Bootstrap-in-flight is
  now the only recoverable state. Other failures keep denying every command and
  print an exact out-of-band command that installs this source into the hook's
  own interpreter. `AUTORUN_DISABLE=1`, read before any autorun import, remains
  the explicit human kill switch when work must continue before repair.
- **A cold start plus a response could overrun the hook wrapper on four of
  seven harnesses.** The two waits were separate constants, each checked
  against the wrapper budget and never checked as a sum: gemini, antigravity
  and qwen spent 0.8s starting the daemon plus 3.5s awaiting a reply against a
  4.0s wrapper, and opencode 0.8 + 4.0 against 4.5. The wrapper fires first, so
  the client's own bound is unreachable and the failure response explaining the
  timeout is never written. The room for a cold start is the wrapper minus the
  response (0.5s on gemini, 1.0s on Claude) so no single constant can serve
  every harness. `client_total_budget()` derives one deadline per harness and
  the client spends it across both phases, which makes the sum correct by
  construction. The retry cap becomes a recursion guard rather than the bound;
  at 8 attempts of 0.1s it ended every cold start at 0.8s with the remaining
  budget unspent, invisible where a daemon becomes reachable in a measured
  0.143s and not where interpreter startup is slower.
- **`can_prompt()` refused to prompt on a real terminal.** The Windows console
  probe treated any stream without a usable `fileno()` as non-interactive.
  `NUL`, which `subprocess.DEVNULL` supplies, always yields a real handle, so
  `GetConsoleMode` refusing it is authoritative; failing to obtain a handle at
  all is missing evidence and now leaves the `isatty()` answer standing. The
  probe moved to `windows_tty_is_a_console()` so it can be exercised off
  Windows. Inside `can_prompt` it sits behind a `sys.platform` guard, so every
  test of it passed vacuously everywhere except the one platform that ran it.
- **The CLI launcher crashed instead of printing repair instructions.**
  `autorun.py` prints its guidance when the package may not be importable, so
  it cannot reach `logging_utils.use_utf8_output()`. On a cp1252 console the
  emoji raised `UnicodeEncodeError` and the user received a traceback in place
  of the steps that would fix the install. Stdout and stderr are reconfigured
  with `errors="replace"` using only the standard library, so every glyph is
  kept and at worst one degrades.
- **A daemon that died during startup left no evidence.** Its stderr went to
  `DEVNULL`, so an import failure, a failed bind and a merely slow start all
  produced the same "failed to start after N attempts". Startup output now
  appends to the daemon log, and the connection error from the last attempt is
  carried into the failure.
- **Plan export did nothing on Windows.** `is_plan_file` tested the raw path
  for the literal `/.claude/plans/`, which a Windows path
  (`C:\Users\<user>\.claude\plans\<name>.md`) never contains, so no plan was
  ever tracked and export, recovery and the rejected-plan backup all skipped
  silently while reporting success.
- **Hooks took the slow path on every Windows event.** `get_autorun_bin`
  looked for `autorun` beside the interpreter and at `.venv/bin/autorun`; a
  Windows venv writes `.venv/Scripts/autorun.exe`, so all three plugin-local
  tiers missed and resolution fell through to the global binary, which the
  direct-daemon check refuses. Both layouts are now accepted on every platform.
- **The daemon never started on Windows.** The spawn string embedded the source
  directory in generated Python without escaping, so `C:\Users\...` made `\U`
  an invalid escape and the interpreter died before importing anything. Every
  hook then fell through to the CLI, which waited on the daemon it had just
  failed to start until the caller gave up, reported as `autorun CLI timed out
  after 5s`. The daemon was also spawned with `start_new_session` alone, which
  is POSIX-only, so even when it did start it was reaped with its parent.
- **The daemon fast path did not exist on Windows.** `try_daemon` returned
  immediately when `socket.AF_UNIX` was absent instead of using the loopback
  endpoint the daemon publishes there, so every event paid a second interpreter
  start.
- **The OpenCode shim could not reach the daemon on Windows.** It connected
  only over a Unix socket, and the installer substituted the socket and port
  paths into JavaScript string literals without escaping, so both were mangled
  by the backslashes in a Windows path.
- **`autorun task` prompted where it was told not to.** `sys.stdin.isatty()`
  is true for `NUL` on Windows, which is what `subprocess.DEVNULL` supplies, so
  a deliberately non-interactive run prompted and then died on EOF instead of
  printing its settings or refusing.
- **A durable checkpoint could undo another process's work.** The task-staleness
  counter was blind-set at one point while other processes advanced it through
  an atomic update, so a process that had crossed the reminder boundary and
  reset the counter was pushed back and the boundary was crossed twice. Three
  of eight concurrent processes emitted a reminder that must be emitted once.
- **`autorun` CLI subcommands crashed on a non-UTF-8 console.** Windows
  reported `'charmap' codec can't encode characters in position 0-1` instead of
  the command's own output, including refusals the user had asked for.
- **Concurrent first-run processes could fail to create the state database.**
  SQLite does not run the busy handler while taking the exclusive lock a
  journal-mode change needs, so a process losing that race was refused with
  "database is locked" even though `busy_timeout` was set.
- **`--uninstall` no longer leaves most of the install behind.** It now removes
  Gemini, Qwen and Antigravity extension directories (asking each harness CLI
  first, so its registry stays consistent), ForgeCode command files, plugin
  skills copied into the shared agents directory, guidance blocks, bridged
  skills, autorun's entry in the Codex personal marketplace and the plugin
  source it points at, and leftover `.autorun-install.lock` files. Every removal
  is gated on an ownership marker autorun writes at creation time, so a
  directory autorun did not create is never deleted, however it is named.
- **`--claude-agents-skills copy` was a one-way door.** Copies are real
  directories, indistinguishable from user-authored ones, so uninstall skipped
  them. They now carry the ownership marker.
- **`--uninstall pdf-extractor` deleted the shared plugin cache and the global
  `autorun` CLI.** Autorun-wide artifacts are now removed only on a full
  uninstall.
- **ForgeCode install destroyed user-authored `<base>/AGENTS.md`.** It used
  `shutil.copy2`; it now merges a sentinel block like every other harness.
  Idempotence tests could not catch this: overwriting with the same template
  yields identical bytes under the same source and toolchain.
- **A stray sentinel marker made memory installs grow the guidance file
  without bound** while leaving the block permanently un-strippable.
- **`AUTORUN_CODEX_*` environment variables overrode explicit CLI flags.**
  argparse supplied its own default, which was indistinguishable from a user's
  explicit choice.
- **`~/.codex/AGENTS.override.md` silently shadows autorun's guidance.** Install
  now warns instead of reporting success for a file Codex will not read.
- **The Stop-block message printed twice**, once from the hook response and once
  from the task-lifecycle echo.
- **Uninstall restarted the daemon it had just removed the code for.** It now
  stops it.
- **A check-then-publish race in `_ensure_codex_plugin_source`** let a
  user-authored directory appear between the ownership check and the write, and
  be replaced.

### Changed

- **Bug #4669 follows the bug-workaround policy.** The exit-2 deny workaround
  now has a bracketed removable block and a CONFIG key
  (`AUTORUN_BUG_CLAUDE_CODE_DENY_IGNORED_AT_EXIT_ZERO_BUG_4669_WORKAROUND_ENABLED`),
  so it can be disabled without an environment variable. `AUTORUN_EXIT2_WORKAROUND`
  and `--exit2-mode` keep working and take precedence. Applicability now comes
  from `Platform.has_exit2_workaround` rather than a hardcoded harness name.
- **Agent memory installs are declarative.** `Platform.memory_filename`,
  `memory_template` and `memory_sentinel_slug` drive one shared installer;
  adding a harness needs no installer code.
- **Uninstall metadata lives beside install metadata** on `Platform`
  (`extensions_subdir`, `uninstall_cmd`), so the two cannot drift.
- `TaskLifecycle.cli_status` / `cli_export` take `output_format=` instead of
  `format=`, which shadowed the builtin.

### Removed

- Dead symbols: `get_python_version_info`, `is_uv_environment`,
  `_verify_gemini_installation`, `ALL_TASK_TOOLS`, `CLAUDE_MODE_CYCLE`,
  `BOOTSTRAP_MSG`, and the unread `plan_acceptance_notify` CONFIG entry.

### Internal

- The Claude E2E module no longer labels regular network-free hook tests as
  real-money tests; paid `claude -p` cases remain explicitly opt-in inside the
  shared harness.
- CI's external actions are pinned to immutable commit SHAs. The release
  checklist gates one exact commit through all eleven jobs, a scratch-home
  marketplace install, immutable remote-tag recovery, and prerelease metadata
  verification.

- `staged_replacement`, an RAII context manager, replaced three copy-pasted
  lock/stage/rollback blocks and gained a `precondition` that runs inside the
  lock.
- `durable_io.atomic_write_text` for user-owned files that are
  read-modify-written rather than regenerated.
- A spec checker (`tests/test_install_location_spec.py`) fails the suite when an
  install or uninstall path is hardcoded, when a teardown function deletes
  without consulting an ownership marker, or when a claimed location has no
  uninstall-side reader.
- **A canary fails the suite when a test modifies autorun's installed copy.**
  `AUTORUN_HOME` and `AUTORUN_TEST_STATE_DIR` redirect state, but nothing
  redirected the installed plugin, the harness settings pointing at it, or the
  shared marketplace registry, so a test that shelled out to an installer, or
  resolved a path from the real home instead of its fixture, edited the user's
  working install and the suite still passed. `tests/conftest.py` now
  fingerprints those artifacts at session start and reports any create, edit,
  or delete at session finish, with a non-zero exit. Only code and
  configuration are watched; sockets, PID files, logs and databases under
  `~/.autorun` are excluded because a user's own daemon writes them while the
  suite runs, and a canary with false positives gets deleted. `e2e` and
  `release` selections install on purpose and are exempt, as is
  `AUTORUN_ALLOW_LIVE_INSTALL_WRITES=1`. `tests/test_live_install_canary.py`
  exercises every branch against a fake home, including an end-to-end pytest
  run whose test writes to the install and must exit non-zero; a canary nobody
  has seen fail reads as coverage while providing none.
- `_expand_home` gives `~` expansion a single seam; install and uninstall
  previously resolved through `Path.home()` and `Path.expanduser()`
  respectively, which differ under test.
- **`uv.lock` is committed.** CI runs `uv sync --locked`, which needs the
  lockfile in the checkout; without it the flag pinned nothing, because the
  matrix jobs happened to run `uv run` first and that writes a lockfile as a
  side effect. Contributors get the same resolution CI does. The lockfile is
  not published in the wheel or sdist, so it does not constrain consumers.
- **The CI matrix covers Python 3.10 through 3.14**, plus dedicated coverage,
  release-artifact, tmux, and state-benchmark jobs. The added versions found
  two real breaks: an unguarded `tomllib` import, which is stdlib only on
  3.11+, and multiprocessing tests that relied on workers inheriting the
  parent's environment, which stops holding when 3.14 makes forkserver the
  default start method on Linux.

## [0.12.0]

Baseline for this changelog. See `git log` for history before it.
