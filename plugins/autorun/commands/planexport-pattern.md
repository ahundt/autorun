---
description: Set the filename pattern for exported plans
allowed-tools: Bash(uv *)
args: pattern
---

# Set Filename Pattern

! uv run --project ${CLAUDE_PLUGIN_ROOT} python ${CLAUDE_PLUGIN_ROOT}/scripts/plan_export_config.py pattern "$ARGUMENTS"
