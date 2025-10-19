# clautorun

[![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Code Style](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

**clautorun** - Claude Agent SDK Command Interceptor

A lightweight, efficient command interceptor for Claude Code that saves tokens by processing autorun commands locally before they reach the AI. 100% compatible with autorun5.py functionality.

## ✨ Features

- 🚀 **Zero AI Token Consumption** - Autorun commands processed instantly locally
- ⚡ **Instant Responses** - No AI processing delay for configuration commands
- 🎯 **100% autorun5.py Compatible** - All prompts, strings, and behavior identical
- 🔧 **Three Integration Methods** - Interactive, Hook, Plugin modes
- 💾 **State Management** - Persistent session state with shelve
- 🛡️ **Safe Operation** - Clean async/sync boundary, no infinite loops
- 📦 **Drop-in Replacement** - Can replace autorun5.py completely

## 🚀 Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/clautorun.git
cd clautorun

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -e .
```

## 🔧 Three Integration Methods

### 🎯 Method 1: Plugin Mode (Recommended)
**Best for most users - full Claude Code experience with smart token savings**

```bash
# 1. Copy plugin to Claude Code commands directory
cp src/clautorun/claude_code_plugin.py ~/.claude/commands/clautorun

# 2. Make executable
chmod +x ~/.claude/commands/clautorun

# 3. Test in Claude Code
# Type: /clautorun help
```

**How it works:**
- ✅ **Full Claude Code interface** - All tools, features, conversations work normally
- ✅ **Smart interception** - Only autorun commands processed locally
- ✅ **Zero configuration** - Works with existing setup
- ✅ **Token savings** - `/afs`, `/afa`, `/afj` commands bypass AI completely

**Example Claude Code session:**
```
User: /afs
Claude: AutoFile policy: strict-search - STRICT SEARCH: ONLY modify existing files...

User: Help me refactor this Python code to be more efficient
Claude: [Full AI response with code analysis, file edits, explanations...]

User: /afa
Claude: AutoFile policy: allow-all - ALLOW ALL: Full permission to create/modify files...
```

---

### 🔌 Method 2: Hook Mode (Drop-in Replacement)
**Replace autorun5.py completely - automatic token savings**

```bash
# 1. Backup existing autorun5.py
cp ~/.claude/hooks/autorun5.py ~/.claude/hooks/autorun5.py.backup

# 2. Replace with clautorun hook
cp src/clautorun/agent_sdk_hook.py ~/.claude/hooks/autorun5.py

# 3. Test in Claude Code
# Type: /afs
```

**Example settings.json configuration:**
```json
{
  "hooks": {
    "hooks": [
      {
        "command": "~/.claude/hooks/autorun5.py"
      }
    ]
  }
}
```

**How it works:**
- ✅ **Automatic interception** - All Claude Code prompts go through clautorun first
- ✅ **Transparent operation** - No change to your workflow
- ✅ **Complete compatibility** - Works with existing autorun5.py setup
- ✅ **Maximum token savings** - Commands never reach AI

---

### 🖥️ Method 3: Interactive Mode (Standalone)
**Run separately - command processor with Agent SDK integration**

```bash
# 1. Navigate to clautorun directory
cd /path/to/clautorun

# 2. Activate virtual environment
source .venv/bin/activate

# 3. Run interactive mode
AGENT_MODE=SDK_ONLY python clautorun.py
```

**How it works:**
- ✅ **Standalone application** - Runs independently of Claude Code
- ✅ **Command processing** - Local commands processed instantly
- ✅ **Agent SDK integration** - Non-commands sent to Claude Code via SDK
- ✅ **Separate workflow** - Run alongside Claude Code

**Example session:**
```
🚀 Agent SDK Command Interceptor - Interactive Mode
✅ Ready for commands...
💡 One Ctrl+C = interrupt, two Ctrl+C = goodbye

❓ /afs
✅ AutoFile policy: strict-search - STRICT SEARCH: ONLY modify existing files...

❓ What is the weather today?
🤖 Processing with Claude Code...
[Claude's weather response appears here]
```

---

## 🎮 Available Commands

### autorun Commands (Zero Tokens)
- `/afs` - Set file policy to **STRICT SEARCH** (only modify existing files)
- `/afa` - Set file policy to **ALLOW ALL** (create/modify any files)
- `/afj` - Set file policy to **JUSTIFY** (justify new file creation)
- `/afst` - Show current file policy status
- `/autostop` - Stop autorun session
- `/estop` - Emergency stop activation
- `/autorun <task>` - Activate autorun with full injection template

### Exit Options
- `quit`, `exit`, or `q` - Exit cleanly
- **Ctrl+C** (interrupt), **Ctrl+C, Ctrl+C** (exit) - Smart Ctrl+C handling
- **Ctrl+D** (EOF) - Immediate exit

## 📁 Project Structure

```
clautorun/
├── src/
│   └── clautorun/
│       ├── __init__.py          # Package initialization and exports
│       ├── main.py              # Core command interceptor logic
│       ├── agent_sdk_hook.py    # Hook integration (autorun5.py replacement)
│       ├── mcp_server.py        # MCP server for external applications
│       └── claude_code_plugin.py # Claude Code plugin (slash command)
├── tests/
│   ├── test_autorun_compatibility.py  # Complete autorun5.py compatibility tests
│   ├── test_interactive.py           # Interactive mode tests
│   ├── simple_test.py                # Simple command tests
│   └── test_interceptor.py           # Hook integration tests
├── docs/
│   └── INTEGRATION_GUIDE.md           # Detailed integration instructions
├── clautorun.py                       # Entry point script (interactive mode)
├── requirements.txt                   # Python dependencies
├── pyproject.toml                    # Package configuration
├── README.md                          # This file
└── .gitignore                        # Git ignore rules
```

## 🔧 Configuration Examples

### Basic Plugin Setup
```bash
# Copy plugin to Claude Code
cp src/clautorun/claude_code_plugin.py ~/.claude/commands/clautorun

# Test in Claude Code by typing:
/clautorun help
```

### Hook Integration Setup
```bash
# Backup and replace autorun5.py
cp ~/.claude/hooks/autorun5.py ~/.claude/hooks/autorun5.py.backup
cp src/clautorun/agent_sdk_hook.py ~/.claude/hooks/autorun5.py
```

### Example settings.json for Hook Mode
```json
{
  "cleanupPeriodDays": 99999,
  "env": {
    "DISABLE_ERROR_REPORTING": "1",
    "DISABLE_TELEMETRY": "1"
  },
  "permissions": {
    "deny": [
      "Bash(git push --force origin master:*)",
      "Bash(git push --force origin main:*)",
      "Bash(rm -rf /*)",
      "Bash(sudo rm -rf /)"
    ]
  },
  "hooks": {
    "hooks": [
      {
        "command": "~/.claude/hooks/autorun5.py"
      }
    ]
  }
}
```

### Interactive Mode Setup
```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e .

# Run interactive mode
AGENT_MODE=SDK_ONLY python clautorun.py
```

## 🧪 Testing

### Run All Tests
```bash
# Test complete autorun5.py compatibility
source .venv/bin/activate
python tests/test_autorun_compatibility.py

# Test interactive functionality
python tests/test_interactive.py

# Test hook integration
echo '{"hook_event_name": "UserPromptSubmit", "session_id": "test", "prompt": "/afs"}' | python src/clautorun/agent_sdk_hook.py

# Test plugin functionality
echo '{"prompt": "/afs", "session_id": "test"}' | python src/clautorun/claude_code_plugin.py
```

### Expected Test Output
```
🧪 Testing clautorun vs autorun5.py compatibility
============================================================
✅ Completion marker matches autorun5.py
✅ Emergency stop phrase matches autorun5.py
✅ All policy descriptions match autorun5.py exactly
✅ All policy blocked messages match autorun5.py exactly
✅ Injection template contains all autorun5.py components
✅ Recheck template matches autorun5.py exactly
✅ All command mappings match autorun5.py exactly
✅ Configuration values match autorun5.py exactly
✅ All command handlers produce correct autorun5.py responses
✅ Log info function works correctly

🎯 All tests passed! clautorun is 100% compatible with autorun5.py
```

## ⚡ Performance & Compatibility

### autorun5.py Compatibility
- ✅ **100% String Compatibility** - All prompts, responses, and messages identical
- ✅ **Complete Feature Parity** - All autorun5.py functionality preserved
- ✅ **State Management** - Identical session state handling
- ✅ **Configuration Values** - Exact matching of all config parameters
- ✅ **Command Responses** - Perfect match for all command outputs

### Performance Metrics
- **O(1) Command Detection** - Same efficient pattern as autorun5.py
- **Zero Latency** - Local command processing (instant responses)
- **Token Savings** - Commands never reach AI (up to 100% savings on configuration)
- **Memory Efficient** - Minimal state management overhead
- **Safe Operation** - No infinite loops, proper error handling

## 📖 Documentation

- [Integration Guide](docs/INTEGRATION_GUIDE.md) - Detailed integration instructions
- [API Reference](src/clautorun/__init__.py) - Package documentation and exports
- [Compatibility Tests](tests/test_autorun_compatibility.py) - Complete autorun5.py validation

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- Built with [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk)
- Inspired by and compatible with [autorun5.py](https://github.com/anthropics/claude-code)
- Follows efficient dispatch patterns from autorun5.py
- 100% autorun5.py string and behavior compatibility verified