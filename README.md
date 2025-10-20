# clautorun

[![Python Version](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**clautorun** - Claude Agent SDK Command Interceptor

A command interceptor for Claude Code that processes specific commands locally to reduce API usage. Commands like file policy changes are handled without making API calls.

## What It Does

- Processes file policy commands locally (`/afs`, `/afa`, `/afj`, `/afst`)
- Sends other commands to Claude Code normally
- Maintains session state between commands
- Provides multiple integration options
- Uses the Claude Agent SDK for communication

## Installation

### Option A: UV with Claude Code Integration (Recommended)

```bash
# Clone the repository
git clone https://github.com/yourusername/clautorun.git
cd clautorun

# Install with UV and Claude Code integration
uv sync --extra claude-code
python -m clautorun install
```

### Option B: UV Development Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/clautorun.git
cd clautorun

# Install with UV (includes dev dependencies for testing)
uv sync --dev
python -m clautorun install
```

### Option C: Traditional pip

```bash
# Clone the repository
git clone https://github.com/yourusername/clautorun.git
cd clautorun

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -e ".[dev]"
python -m clautorun install
```

## Integration Options

### Option 1: Plugin Mode (Recommended & Automatic)

This method adds clautorun as a slash command in Claude Code using automatic installation.

**Setup:**
```bash
# Automatic installation (recommended)
python -m clautorun install

# Manual installation (if needed)
cp src/clautorun/claude_code_plugin.py ~/.claude/commands/clautorun
chmod +x ~/.claude/commands/clautorun
```

**Usage in Claude Code:**
```
User: /clautorun /afs
Response: AutoFile policy: strict-search - STRICT SEARCH: ONLY modify existing files...

User: /clautorun /afa
Response: AutoFile policy: allow-all - ALLOW ALL: Full permission to create/modify files.
```

**What happens:**
- Creates symlink from installed package to Claude Code commands directory
- Commands are processed locally without API calls
- Other prompts are handled normally by Claude Code
- Session state is preserved between commands
- Package updates automatically update the plugin

**Installation Management:**
```bash
# Check installation status
python -m clautorun check

# Uninstall plugin
python -m clautorun uninstall

# Force reinstall
python -m clautorun install --force
```

### Option 2: Hook Integration

This method intercepts all Claude Code prompts through the hook system.

**Setup:**
```bash
# Copy to hooks directory
cp src/clautorun/agent_sdk_hook.py ~/.claude/hooks/clautorun_hook.py
```

**Update settings.json:**
```json
{
  "hooks": {
    "hooks": [
      {
        "command": "~/.claude/hooks/clautorun_hook.py"
      }
    ]
  }
}
```

**What happens:**
- All prompts go through clautorun first
- File policy commands are handled locally
- Other prompts continue to Claude Code normally

### Option 3: Interactive Mode

Run as a standalone application that communicates with Claude Code via the Agent SDK.

**Setup:**
```bash
# Navigate to clautorun directory
cd /path/to/clautorun

# Activate virtual environment
source .venv/bin/activate

# Run interactive mode
AGENT_MODE=SDK_ONLY python clautorun.py
```

**Example session:**
```
🚀 Agent SDK Command Interceptor - Interactive Mode
✅ Ready for commands...

❓ /afs
✅ AutoFile policy: strict-search - STRICT SEARCH: ONLY modify existing files...

❓ help me understand this codebase
🤖 Processing with Claude Code...
[Claude's response appears here]
```

## Available Commands

### File Policy Commands
- `/afs` - Set policy to strict search (only modify existing files)
- `/afa` - Set policy to allow all (create/modify any files)
- `/afj` - Set policy to justify (require justification for new files)
- `/afst` - Show current file policy

### Control Commands
- `/autostop` - Stop the current session
- `/estop` - Emergency stop
- `/autorun <task description>` - Start automated task execution

### Exit Commands (Interactive Mode)
- `quit`, `exit`, `q` - Exit the application
- Ctrl+C - Interrupt, Ctrl+C twice - Exit
- Ctrl+D - Exit immediately

## File Policy Details

**STRICT SEARCH** (`/afs`):
- Response: "AutoFile policy: strict-search - STRICT SEARCH: ONLY modify existing files. Use Glob/Grep. NO new files."
- Can only modify existing files
- Must search for similar functionality first

**ALLOW ALL** (`/afa`):
- Response: "AutoFile policy: allow-all - ALLOW ALL: Full permission to create/modify files."
- Can create or modify any files
- No restrictions on file operations

**JUSTIFY** (`/afj`):
- Response: "AutoFile policy: justify-create - JUSTIFIED: Search existing first. Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> for new files."
- Must search existing files first
- Must provide justification for creating new files

## Testing

clautorun includes a comprehensive pytest testing suite to verify functionality and compatibility.

### Quick Test (Core Functionality)

**With UV (Recommended):**
```bash
uv run pytest tests/test_unit_simple.py tests/test_autorun_compatibility.py -v
```

**With Traditional pip:**
```bash
source .venv/bin/activate
pytest tests/test_unit_simple.py tests/test_autorun_compatibility.py -v
```

**Using Makefile:**
```bash
make test-quick
```

**Expected output:**
```
============================= test session starts ==============================
collected 29 items

tests/test_unit_simple.py::TestConfiguration::test_completion_marker PASSED [  3%]
tests/test_unit_simple.py::TestConfiguration::test_emergency_stop_phrase PASSED [  6%]
...
tests/test_autorun_compatibility.py::test_completion_marker PASSED [ 84%]
tests/test_autorun_compatibility.py::test_emergency_stop_phrase PASSED [ 87%]
...
============================== 29 passed in 0.15s ==============================
```

### Full Test Suite

**Run all tests with coverage:**
```bash
# With UV
uv run pytest --cov=src/clautorun --cov-report=term-missing

# With make
make test-all

# With traditional pip
pytest --cov=src/clautorun --cov-report=term-missing
```

### Test Categories

**Unit Tests** (`test_unit_simple.py`):
- Configuration constants and mappings
- Command handler functionality
- Command detection logic
- Basic functionality validation

**Compatibility Tests** (`test_autorun_compatibility.py`):
- autorun5.py string compatibility
- Policy descriptions and blocked messages
- Injection and recheck templates
- Configuration verification

**Integration Tests** (`test_interceptor.py`, `test_interactive.py`):
- Command processing validation
- Interactive mode functionality

### Running Specific Test Categories

```bash
# Unit tests only
uv run pytest tests/test_unit_simple.py -v

# Compatibility tests only
uv run pytest tests/test_autorun_compatibility.py -v

# With markers
uv run pytest -m unit -v
uv run pytest -m compatibility -v
```

### Test Coverage Report

After running tests with coverage, view detailed reports:

```bash
# HTML report (opens in browser)
open htmlcov/index.html

# Terminal summary
cat coverage.txt
```

### Manual Testing

**Test interactive commands:**
```bash
uv run python src/clautorun/main.py
# Then try: /afs, /afa, /afj, /afst, quit
```

**Test hook integration:**
```bash
echo '{"hook_event_name": "UserPromptSubmit", "session_id": "test", "prompt": "/afs"}' | uv run python src/clautorun/agent_sdk_hook.py
```

**Test plugin mode:**
```bash
echo '{"prompt": "/afa"}' | uv run python src/clautorun/claude_code_plugin.py
```

## Project Structure

```
clautorun/
├── src/
│   └── clautorun/
│       ├── __init__.py          # Package exports
│       ├── main.py              # Core command processing logic
│       ├── agent_sdk_hook.py    # Hook integration
│       ├── mcp_server.py        # MCP server for external apps
│       └── claude_code_plugin.py # Claude Code plugin
├── tests/
│   ├── test_autorun_compatibility.py  # Command compatibility tests
│   ├── test_interactive.py           # Interactive mode tests
│   ├── simple_test.py                # Basic functionality tests
│   └── test_interceptor.py           # Hook integration tests
├── docs/
│   └── INTEGRATION_GUIDE.md           # Detailed setup instructions
├── clautorun.py                       # Entry point for interactive mode
├── requirements.txt                   # Python dependencies
├── pyproject.toml                    # Package configuration
├── README.md                          # This file
└── .gitignore                        # Git ignore rules
```

## Dependencies

- `claude-agent-sdk>=0.1.4` - For Claude Code communication
- `ruff>=0.14.1` - Code formatting and linting
- Python 3.8+ - Required for type hints and async support

## Configuration Notes

**Session Storage:**
- Uses shelve database for session persistence
- Located in `~/.claude/sessions/`
- State includes file policies and session status

**Agent SDK Integration:**
- Uses ClaudeAgentClient for communication
- Session IDs maintain conversation context
- Costs are tracked when using Claude Code APIs

## Installation Management

The installation system provides comprehensive management capabilities:

```bash
# Install Claude Code plugin (creates symlink)
python -m clautorun install

# Check installation status and validate plugin
python -m clautorun check

# Uninstall plugin (removes symlink)
python -m clautorun uninstall

# Force reinstall (overwrites existing)
python -m clautorun install --force

# Show help for all commands
python -m clautorun install --help
```

## Troubleshooting

**Installation Issues:**
```bash
# Check if Claude Code is detected
python -m clautorun check

# Verify plugin symlink exists and is valid
ls -la ~/.claude/commands/clautorun

# Test plugin manually
echo '{"prompt": "/afs", "session_id": "test"}' | ~/.claude/commands/clautorun
```

**Plugin not working:**
- Ensure dependencies are installed: `uv sync --extra claude-code`
- Verify the symlink is valid: `python -m clautorun check`
- Check permissions: `ls -la ~/.claude/commands/`
- Test with UV environment activated

**Hook integration issues:**
- Verify settings.json format is valid
- Check hook file permissions
- Look for errors in Claude Code logs

**Interactive mode problems:**
- Ensure virtual environment is activated
- Run: `python -m clautorun` (starts interactive mode)

**Common Solutions:**
- Force reinstall: `python -m clautorun install --force`
- Check installation: `python -m clautorun check`
- Ensure UV environment is active for dependency access
- Check Agent SDK installation: `pip list | grep claude-agent-sdk`
- Verify Python version: `python --version`

## License

MIT License - see [LICENSE](LICENSE) file for details.