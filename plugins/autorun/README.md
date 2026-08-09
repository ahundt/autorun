# autorun

`autorun` supplies task-lifecycle checks, command safety guards, skills, and
native integration assets for AI coding harnesses. The Python distribution
contains the complete `ar` plugin and the `autorun` and `autorun-install`
entry points. The companion `pdf-extractor` distribution is released and
installed separately; a source checkout can manage both repository plugins.

## Install

Install the Python tool from the repository's package subdirectory, then
publish the native assets for the harnesses detected on the machine:

```bash
uv tool install 'git+https://github.com/ahundt/autorun.git#subdirectory=plugins/autorun'
autorun --install
autorun --status
```

Claude Code can instead install the plugin through its marketplace flow:

```bash
claude plugin marketplace add https://github.com/ahundt/autorun.git
claude plugin install ar@autorun
```

Use `autorun --install --install-dry-run` to preview filesystem and
registration work. Use `autorun --uninstall` to remove only autorun-owned
assets; session state, task history, and logs are retained intentionally.

## Harness capabilities

| Harness | Safety hook | Task stop hooks | Commands and skills |
|---|---|---|---|
| Claude Code | Native hooks | Yes | Native |
| Codex | Native hooks | Yes | Codex-native plugin and skills |
| Qwen Code | Native hooks | Yes | Native extension |
| Antigravity | Native hooks | Installed native event subset | Native extension |
| OpenCode | In-process tool veto | No | Command files and skills |
| ForgeCode | Advisory guidance only | No | Command files and skills |
| Legacy Gemini CLI | Native hooks | Yes | Native extension |

The CLI accepts the shared command grammar internally and renders the spelling
native to each harness: `ar:` for Codex, `/ar-` for ForgeCode and OpenCode,
and `/ar:` for Claude and the Gemini-family harnesses.

## Development

From the repository root:

```bash
uv sync --project plugins/autorun
uv run --project plugins/autorun pytest plugins/autorun/tests
```

Tests must redirect `AUTORUN_HOME`, `AUTORUN_TEST_STATE_DIR`, and
`AUTORUN_TEST_RUNTIME_DIR` before importing `autorun`.

The project is licensed under the Apache License 2.0. See `LICENSE` in this
distribution. Full project documentation is available at
<https://github.com/ahundt/autorun>.
