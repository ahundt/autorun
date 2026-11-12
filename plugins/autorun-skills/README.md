# autorun-skills

`autorun-skills` is a plugin of eleven general-purpose agent skills. None of
them needs autorun's hooks, commands or daemon, so they live apart from the
`ar` plugin, which keeps only the skills its own commands use (`plannew`,
`planrefine`, `planupdate`, `planprocess`, `commit`, `philosophy`, `tmux`).

## Skills

| Skill | What it does |
| --- | --- |
| `ai-skill-builder` | Creates, audits and improves portable Agent Skills (`SKILL.md`), with scaffold and audit scripts. |
| `cli-demo-recorder` | Records demo videos and GIFs of CLI and TUI programs with asciinema, agg, ffmpeg and tmux. |
| `function-signature-and-parameter-guidance` | Designs and reviews caller-facing names: functions, CLI flags, config keys, parameters, errors and help text. |
| `mermaid-diagrams` | Renders Mermaid diagrams to SVG with beautiful-mermaid and bun. |
| `parallel-subagent` | Tests 8 to 10 different fixes for one stubborn bug side by side with parallel subagents and reports which work and why. |
| `streamline-text` | Shortens and clarifies prose while keeping every fact, number, command and qualifier. |
| `synthesize-project-requirements` | Collects requirements, decisions and gotchas from notes, git history and past AI sessions into one evidence-backed note. |
| `task-lifecycle-workflow` | Runs long, correction-heavy work from evidence to a verified finish: plans, parallel agents, reviews and recovery. |
| `tmux-automation` | Runs, watches and types into terminal programs in tmux or byobu panes without disturbing other sessions. |
| `write-decision-ready-technical-reports` | Turns investigations, plans and status updates into reports that state what is true, what changed and what must be decided. |
| `write-durable-goal-prompts` | Writes goal prompts for `/goal`, autorun or other long-running agents that stay valid as the repository changes. |

## Installing

`autorun --install` installs this plugin together with `ar` on every harness
it finds. To install or remove it alone:

```bash
autorun --install autorun-skills      # this plugin only
autorun --uninstall autorun-skills    # remove this plugin; ar stays
autorun --install ar                  # ar only, without these skills
```

Remove it if you keep your own copies of these skills, for example under
`~/.agents/skills`, so a harness does not list a name twice. On Claude Code
alone you can also use the marketplace:
`claude plugin install autorun-skills@autorun`.

An install that finds a copy of one of these skills that `ar` installed under
an earlier release takes it over: the copy is replaced by this plugin's, so
each skill stays listed once. A copy you edited is kept, and the install
report names it.

## How each harness names these skills

| Harness | Where the skills go | What you type |
| --- | --- | --- |
| Claude Code | the plugin's package in Claude's plugin cache | `/autorun-skills:<skill>` |
| Codex | `~/plugins/autorun-skills/skills` | `$<skill>` |
| Gemini CLI | the `autorun-skills` extension's `skills/` | `<skill>`, listed under the extension |
| Qwen Code | the `autorun-skills` extension's `skills/` | the skill name |
| Antigravity | the `autorun-skills` plugin's `skills/` | the skill name |
| Pi | `~/.pi/agent/packages/autorun-skills/skills` | `/skill:<skill>` |
| Prime Agent | `~/.prime/agent/packages/autorun-skills/skills` | `/skill:<skill>` |
| OpenCode | `~/.config/opencode/skills` | the skill name |
| ForgeCode | `~/forge/skills` | through its `skill` tool |

Every harness also starts a skill on its own when your request matches the
skill's description, so typing the name is optional.

## License

Apache-2.0, in `LICENSE`.
