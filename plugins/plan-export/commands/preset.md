---
description: Apply a plan export preset configuration
allowed-tools: Bash(python3:*)
args: name
---

# Apply Preset: $ARGUMENTS

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/config.py preset "$ARGUMENTS"`
