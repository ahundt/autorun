# Autorun Plugin - Gemini CLI

**autorun plugin** provides safety features, file policies, and autonomous execution capabilities for Gemini CLI.

## Commands

All autorun commands use the `/ar:` prefix:

```bash
/ar:st              # Show current AutoFile policy
/ar:a               # Allow all file creation
/ar:j               # Justify new files
/ar:f               # Find and modify existing files only (strictest)
/ar:go <task>       # Start autonomous execution
/ar:sos             # Emergency stop
```

## Safety Features

- **File Policies**: Control file creation with `/ar:a`, `/ar:j`, `/ar:f`
- **Command Blocking**: Prevents dangerous operations (rm, git reset --hard, etc.)
- **Plan Export**: Auto-save plans to notes/ directory
- **Three-Stage Verification**: Ensures thorough task completion
- **Task Tracking**: Monitor task completion across sessions

## Required Settings

**IMPORTANT**: Hooks require explicit enablement in Gemini CLI settings.

Edit `~/.gemini/settings.json` and add:

```json
{
  "tools": {
    "enableHooks": true,
    "enableMessageBusIntegration": true
  }
}
```

**Why Required**: Without these settings, autorun hooks will not execute even if properly installed. The safety features (command blocking, file policies) depend on hooks.

**Version Requirement**: Gemini CLI v0.28.0 or later recommended.

Update Gemini CLI:
```bash
# Using Bun (faster)
bun install -g @google/gemini-cli@latest

# Or using npm
npm install -g @google/gemini-cli@latest
```

Verify version:
```bash
gemini --version  # Should show 0.28.0 or later
```

## Installation

This plugin is installed as part of the autorun marketplace.

For full documentation, see: https://github.com/ahundt/autorun
