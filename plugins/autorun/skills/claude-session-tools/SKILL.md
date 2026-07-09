---
name: claude-session-tools
description: (Renamed) Search, recover, and analyze sessions from Claude Code, AI Studio, and Gemini CLI — use the ai-session-tools skill instead.
version: "0.12.0"
user-invocable: true
disable-model-invocation: false
allowed-tools:
  - Bash
---

# Claude Session Tools → AI Session Tools

This skill has been renamed. Use **`$ai-session-tools`** or select
`ai-session-tools` from the current harness skill picker instead.

All commands and workflows are unchanged — only the invoke name is different.

**Invoke this skill with:** `$ai-session-tools`, a harness skill picker, or
natural language such as "search sessions for the auth bug". Avoid treating an
`/ar:*` slash command as the skill activation path; Codex and Claude Code can
expose skills separately from slash commands.
