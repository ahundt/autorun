---
description: Disable automatic plan export to project notes
allowed-tools: Bash(python3:*)
---

# Disable Plan Export

Disable automatic export of plan files when exiting plan mode.

Run the disable script:
! python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan_export_config.py disable

Plan files will no longer be automatically copied to the project's `notes/` directory.
