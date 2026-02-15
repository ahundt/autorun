---
description: Set rejected plan output directory
allowed-tools: Bash(uv *)
args: path
---

# Set Rejected Plan Directory

! uv run --project ${CLAUDE_PLUGIN_ROOT} python ${CLAUDE_PLUGIN_ROOT}/scripts/plan_export_config.py rejected-dir "$ARGUMENTS"
