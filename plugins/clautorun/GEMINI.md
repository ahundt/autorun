# Clautorun Plugin - Gemini CLI

**clautorun plugin** provides safety features, file policies, and autonomous execution capabilities for Gemini CLI.

## Commands

All clautorun commands use the `/cr:` prefix:

```bash
/cr:st              # Show current AutoFile policy
/cr:a               # Allow all file creation
/cr:j               # Justify new files
/cr:f               # Find and modify existing files only (strictest)
/cr:go <task>       # Start autonomous execution
/cr:sos             # Emergency stop
```

## Safety Features

- **File Policies**: Control file creation with `/cr:a`, `/cr:j`, `/cr:f`
- **Command Blocking**: Prevents dangerous operations (rm, git reset --hard, etc.)
- **Plan Export**: Auto-save plans to notes/ directory
- **Three-Stage Verification**: Ensures thorough task completion
- **Task Tracking**: Monitor task completion across sessions

## Installation

This plugin is installed as part of the clautorun marketplace.

For full documentation, see: https://github.com/ahundt/clautorun
