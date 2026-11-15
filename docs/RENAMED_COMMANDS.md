# Renamed commands

Each command has one name. An old name still runs the new command wherever the agent passes the text to autorun. That covers Pi, Prime Agent, and Gemini CLI in any spelling, and Codex as `ar:<name>` or `ar <name>`. Claude Code and Qwen Code answer an old `/ar:` name with "Unknown command"; ForgeCode, Antigravity, and OpenCode have no route for old names.

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

Change each old command name in your own instruction files (`CLAUDE.md`, `AGENTS.md`, saved prompts) to the new name.
