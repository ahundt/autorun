---
description: Set the output directory for exported plans
allowed-tools: Bash(uv *)
args: path
---

# Set Output Directory

! uv run --project ${CLAUDE_PLUGIN_ROOT} python ${CLAUDE_PLUGIN_ROOT}/scripts/plan_export_config.py dir "$ARGUMENTS"
