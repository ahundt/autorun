---
description: Disable automatic plan export to project notes
allowed-tools: Bash(uv *)
---

# Disable Plan Export

Disable automatic export of plan files when exiting plan mode.

Run the disable script:
! uv run --project ${CLAUDE_PLUGIN_ROOT} python ${CLAUDE_PLUGIN_ROOT}/scripts/plan_export_config.py disable

Plan files will no longer be automatically copied to the project's `notes/` directory.
