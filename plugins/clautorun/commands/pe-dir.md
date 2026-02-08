---
description: Set the output directory for exported plans
allowed-tools: Bash(python3:*)
args: path
---

# Set Output Directory

! python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan_export_config.py dir "$ARGUMENTS"
