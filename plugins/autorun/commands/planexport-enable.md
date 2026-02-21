---
description: Enable automatic plan export to project notes
allowed-tools: Bash(uv *)
---

# Enable Plan Export

Enable automatic export of plan files to the project's `notes/` directory when exiting plan mode.

Run the enable script:
! uv run --project ${CLAUDE_PLUGIN_ROOT} python ${CLAUDE_PLUGIN_ROOT}/scripts/plan_export_config.py enable

After enabling, plans will be automatically copied to `notes/YYYY_MM_DD_<name>.md` when you exit plan mode.
