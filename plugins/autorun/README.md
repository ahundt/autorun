# autorun

An AI coding agent left alone stops mid-task, calls a first draft done, and runs
commands such as `rm -rf` or `git reset --hard`. autorun adds hooks that send it
back to work and block those commands. It works with Claude Code, Codex, Qwen
Code, Pi and five more agents.

## Install

```bash
uv tool install autorun-ai   # installs the `autorun` command
autorun --install            # adds hooks, commands and skills to every agent it finds
autorun --status             # checks each install and runs the hook command once
```

Then start a new agent session and type `/ar:help` (`ar:help` in Codex) for
every command.
`autorun --install-dry-run` previews the writes. `autorun --uninstall` removes
what autorun installed, including the `autorun-ai` tool, and stops its
background daemon; it keeps `~/.autorun` (session state, task history and logs).

Other routes:

```bash
uv tool install 'git+https://github.com/ahundt/autorun.git#subdirectory=plugins/autorun'   # from GitHub
claude plugin marketplace add https://github.com/ahundt/autorun.git                     # Claude Code only
claude plugin install ar@autorun
uv tool install 'autorun-ai[pdf]'    # adds the PDF backends for `extract-pdfs`
```

The `pdf-extractor` agent plugin's extraction code ships in this package, behind
the `pdf` extra.

## What each agent gets

| Agent | Safety hook | Keeps working past Stop | Commands and skills |
|---|---|---|---|
| Claude Code | native hooks | yes | native |
| Codex | native hooks | yes | Codex plugin and skills |
| Qwen Code | native hooks | yes | native extension |
| Antigravity | native hooks | the events Antigravity offers | native extension |
| OpenCode | in-process tool veto | yes on 0.6.0+, through `session.idle`; older clients only show the reason. Its todo list is mirrored into task status | command files and skills |
| Pi | in-process tool veto | `agent_settled` continuation and task tools | one local package containing the extension and skills |
| Prime Agent | the Pi extension below 0.10.0; none on 0.10+ | as Pi below 0.10.0; no on 0.10+ | below 0.10.0, one local package containing the extension and skills; on 0.10+, guidance and skills |
| ForgeCode | advice only | no | command files and skills |
| Legacy Gemini CLI | native hooks | yes | native extension |

autorun's messages use each agent's spelling: `/ar:` in Claude, Gemini CLI,
Qwen Code and Antigravity, `/ar ` in Pi, `ar:` in Codex, and `/ar-` in ForgeCode and OpenCode.

Documentation: <https://github.com/ahundt/autorun>. Development setup and
tests: [AGENTS.md](https://github.com/ahundt/autorun/blob/main/AGENTS.md).
Licensed under Apache 2.0; see `LICENSE`.
