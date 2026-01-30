# Plan Export Plugin

Automatically exports plan files to your project when exiting plan mode, with configurable output paths and filename patterns.

## Features

- **Automatic Export**: When you exit plan mode, the most recent plan is copied to your project
- **Configurable Output**: Set custom directories and filename patterns with template variables
- **Presets**: Quick configuration with 8 built-in presets
- **Template Variables**: Use `{YYYY}`, `{MM}`, `{DD}`, `{name}`, etc. in paths
- **Enable/Disable**: Control export behavior via slash commands

## Installation

### Option 1: Local Plugin

```bash
/plugin marketplace add ~/.claude/plugins/repos/plan-export
/plugin install plan-export@plan-export
```

### Option 2: Direct Settings Integration

Add to your `~/.claude/settings.json` under `hooks`:

```json
"PostToolUse": [
  {
    "matcher": "ExitPlanMode",
    "hooks": [
      {
        "type": "command",
        "command": "python3 ~/.claude/plugins/repos/plan-export/scripts/plan_export.py",
        "timeout": 30
      }
    ]
  }
]
```

## Commands

| Command | Description |
|---------|-------------|
| `/plan-export:status` | Show current configuration |
| `/plan-export:enable` | Enable automatic export |
| `/plan-export:disable` | Disable automatic export |
| `/plan-export:configure` | Interactive configuration help |
| `/plan-export:presets` | List available presets |
| `/plan-export:preset <name>` | Apply a preset (e.g., `dated`, `docs`) |
| `/plan-export:dir <path>` | Set output directory |
| `/plan-export:pattern <pattern>` | Set filename pattern |
| `/plan-export:reset` | Reset to defaults |

## Template Variables

Use these in both `dir` and `pattern`:

| Variable | Description | Example |
|----------|-------------|---------|
| `{YYYY}` | 4-digit year | 2025 |
| `{YY}` | 2-digit year | 25 |
| `{MM}` | Month (01-12) | 12 |
| `{DD}` | Day (01-31) | 10 |
| `{date}` | Full date | 2025_12_10 |
| `{name}` | Plan name from heading | implement_auth |
| `{original}` | Original filename | fuzzy-dancing-star |

## Presets

| Preset | Output | Description |
|--------|--------|-------------|
| `default` | `notes/YYYY_MM_DD_name.md` | Standard (default) |
| `plans` | `plans/YYYY_MM_DD_name.md` | Plans folder |
| `docs` | `docs/plans/YYYY_MM_DD_name.md` | Documentation |
| `dated` | `notes/YYYY/MM/DD_name.md` | Date hierarchy |
| `yearly` | `notes/YYYY/MM_DD_name.md` | Yearly folders |
| `simple` | `notes/name.md` | Name only (may overwrite!) |
| `archive` | `.archive/plans/YYYY/date_name.md` | Hidden archive |
| `original` | `notes/YYYY_MM_DD_original.md` | Keep original name |

## Examples

### Date-organized hierarchy
```bash
/plan-export:preset dated
# Creates: notes/2025/12/10_my_plan.md
```

### Custom configuration
```bash
/plan-export:dir docs/decisions/{YYYY}
/plan-export:pattern {MM}_{DD}_{name}
# Creates: docs/decisions/2025/12_10_my_plan.md
```

### Keep original Claude plan names
```bash
/plan-export:preset original
# Creates: notes/2025_12_10_fuzzy-dancing-star.md
```

## Configuration File

Settings are stored in `~/.claude/plan-export.config.json`:

```json
{
  "enabled": true,
  "output_dir": "notes",
  "filename_pattern": "{date}_{name}",
  "extension": ".md"
}
```

## How It Works

1. When you exit plan mode (ExitPlanMode tool), the PostToolUse hook fires
2. The script finds the most recently modified plan in `~/.claude/plans/`
3. It extracts a useful name from the plan's first heading
4. Template variables are expanded in the output path
5. The plan is copied to the configured location

## Files

```
plan-export/
├── .claude-plugin/
│   └── plugin.json
├── commands/
│   ├── configure.md
│   ├── dir.md
│   ├── disable.md
│   ├── enable.md
│   ├── pattern.md
│   ├── preset.md
│   ├── presets.md
│   ├── reset.md
│   └── status.md
├── hooks/
│   └── hooks.json
├── scripts/
│   ├── config.py
│   └── plan_export.py
└── README.md
```
