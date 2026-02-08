---
description: Configure plan export settings interactively
allowed-tools: Bash(python3:*)
---

# Configure Plan Export

Current configuration:
! python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan_export_config.py status

## Configuration Options

I can help you configure plan export. What would you like to do?

1. **Set output directory** - Where plans are saved (supports templates)
2. **Set filename pattern** - How plan files are named
3. **Toggle rejected plan export** - Enable/disable exporting rejected plans
4. **Set rejected plans directory** - Where rejected plans are saved
5. **Reset to defaults** - Restore default settings

### Template Variables
- `{YYYY}` - 4-digit year (2025)
- `{YY}` - 2-digit year (25)
- `{MM}` - Month (01-12)
- `{DD}` - Day (01-31)
- `{HH}` - Hour (00-23)
- `{mm}` - Minute (00-59)
- `{ss}` - Second (00-59)
- `{date}` - Full date (YYYY_MM_DD)
- `{datetime}` - Full date+time (YYYY_MM_DD_HHmm)
- `{name}` - Extracted plan name from heading
- `{original}` - Original plan filename

### Example Configurations
- `notes/{YYYY}/{MM}` with `{DD}_{name}` → `notes/2025/12/10_my_plan.md`
- `notes` with `{datetime}_{name}` → `notes/2025_12_10_1430_my_plan.md`
