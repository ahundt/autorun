---
description: Configure plan export settings interactively
allowed-tools: Bash(python3:*)
---

# Configure Plan Export

Current configuration:
!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/config.py status`

## Configuration Options

I can help you configure plan export. What would you like to do?

1. **Set output directory** - Where plans are saved (supports templates)
2. **Set filename pattern** - How plan files are named
3. **Apply a preset** - Quick configuration from presets
4. **Reset to defaults** - Restore default settings

### Template Variables
- `{YYYY}` - 4-digit year (2025)
- `{YY}` - 2-digit year (25)
- `{MM}` - Month (01-12)
- `{DD}` - Day (01-31)
- `{date}` - Full date (YYYY_MM_DD)
- `{name}` - Extracted plan name from heading
- `{original}` - Original plan filename

### Example Configurations
- `note/{YYYY}/{MM}` with `{DD}_{name}` → `note/2025/12/10_my_plan.md`
- `docs/plans` with `{date}_{name}` → `docs/plans/2025_12_10_my_plan.md`
