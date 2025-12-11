---
description: Enable automatic plan export to project notes
allowed-tools: Bash(python3:*)
---

# Enable Plan Export

Enable automatic export of plan files to the project's `note/` directory when exiting plan mode.

Run the enable script:
!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/config.py enable`

After enabling, plans will be automatically copied to `note/YYYY_MM_DD_<name>.md` when you exit plan mode.
