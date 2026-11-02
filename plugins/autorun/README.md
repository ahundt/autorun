# autorun

autorun adds hooks to AI coding agents (Claude Code, Codex, Qwen Code, Pi and
others) that keep them working until a task is verified, stop destructive shell
commands such as `rm -rf` and `git reset --hard` before they run, and limit file
creation to the policy you set.

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

The `pdf-extractor` agent plugin is not a separate package: its extraction code
ships in this one behind the `pdf` extra.

## What each agent gets

| Agent | Safety hook | Keeps working past Stop | Commands and skills |
|---|---|---|---|
| Claude Code | native hooks | yes | native |
| Codex | native hooks | yes | Codex plugin and skills |
| Qwen Code | native hooks | yes | native extension |
| Antigravity | native hooks | the events Antigravity offers | native extension |
| OpenCode | in-process tool veto | no Stop event; its todo list is mirrored into task status | command files and skills |
| Pi | in-process tool veto | `agent_settled` continuation and task tools | native extension and shared skills |
| Prime Agent | the Pi extension | as Pi | as Pi |
| ForgeCode | advice only | no | command files and skills |
| Legacy Gemini CLI | native hooks | yes | native extension |

autorun's messages use each agent's spelling: `/ar:` in Claude, Gemini CLI,
Qwen Code and Antigravity, `/ar ` in Pi, `ar:` in Codex, and `/ar-` in ForgeCode and OpenCode.

Documentation: <https://github.com/ahundt/autorun>. Development setup and
tests: [AGENTS.md](https://github.com/ahundt/autorun/blob/main/AGENTS.md).
Licensed under Apache 2.0; see `LICENSE`.
