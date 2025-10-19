# Agent SDK Command Interceptor

Ultra-compact Agent SDK application that intercepts autorun commands before they reach Claude Code, saving tokens and providing instant responses.

**Design Principles**: Follows the same efficient dispatch patterns as autorun5.py for maximum performance and minimal code duplication.

## Features

- **Command Interception**: Handles `/afs`, `/afa`, `/afj`, `/afst`, `/autostop`, `/estop` before AI
- **Token Savings**: Commands never reach the AI, saving tokens
- **Instant Response**: No AI processing delay for configuration commands
- **Ultra-Compact Design**: Uses same efficient dispatch system as autorun5.py
- **Drop-in Compatible**: Works with existing autorun5.py hook system
- **Dual Mode Operation**: Standalone or Claude Code integration

## Installation

Since you prefer `uv` over pip:

```bash
cd clautorun
uv pip install claude-agent-sdk
```

Alternatively with pip:
```bash
pip install claude-agent-sdk
```

## Setup

1. Copy environment file:
```bash
cp .env.example .env
```

2. Add your API key:
```bash
# Edit .env file with your actual API key
ANTHROPIC_API_KEY=your_actual_api_key_here
```

3. Install the package in development mode:
```bash
pip install -e .
```

## Usage

The Agent SDK application will automatically intercept these commands:

- `/afs` - Set file policy to STRICT SEARCH
- `/afa` - Set file policy to ALLOW ALL
- `/afj` - Set file policy to JUSTIFY creation
- `/afst` - Show current file policy
- `/autostop` - Stop autorun session
- `/estop` - Emergency stop

## Ultra-Efficient Dispatch System

The implementation uses the same efficient dispatch patterns as autorun5.py:

### Command Dispatch
```python
COMMAND_HANDLERS = {
    "SEARCH": lambda s: f"AutoFile policy: strict-search - {CONFIG['policies']['SEARCH'][1]}",
    "ALLOW": lambda s: f"AutoFile policy: allow-all - {CONFIG['policies']['ALLOW'][1]}",
    "JUSTIFY": lambda s: f"AutoFile policy: justify-create - {CONFIG['policies']['JUSTIFY'][1]}",
    "STATUS": lambda s: f"Current policy: {CONFIG['policies'].get(s.get('file_policy', 'ALLOW'), ('allow-all', ''))[0]}",
    "STOP": lambda s: "Autorun stopped",
    "EMERGENCY_STOP": lambda s: "Emergency stop activated"
}
```

### Handler Registration
```python
@handler("UserPromptSubmit")
async def intercept_commands(input_data, context):
    # Efficient command detection - same pattern as autorun5.py line 144
    command = next((v for k, v in CONFIG["command_mappings"].items() if k == prompt), None)
```

## Dual Operation Modes

### 1. Standalone Mode (Maximum Efficiency)
```bash
AGENT_MODE=SDK_ONLY python main.py
```
- Commands handled entirely by Agent SDK
- Zero AI token consumption
- Instant responses
- Full independence

### 2. Hook Integration Mode (Drop-in Compatible)
```bash
AGENT_MODE=HOOK_INTEGRATION python main.py
```
- Runs as Claude Code hook
- Maintains compatibility with existing system
- Same behavior as autorun5.py but more efficient

## Integration with Existing System

The Agent SDK can replace or augment autorun5.py:

1. **Standalone**: Complete replacement - maximum efficiency
2. **Hook Integration**: Drop-in replacement - maintains existing behavior
3. **Hybrid**: SDK handles responses, hooks handle enforcement

## Testing

### Standalone Mode
```bash
cd clautorun
uv pip install claude-agent-sdk
AGENT_MODE=SDK_ONLY python main.py
```

### Hook Integration Mode
```bash
# Replace autorun5.py with main.py
cp /path/to/autorun5.py /path/to/autorun5.py.backup
cp main.py /path/to/autorun5.py
```

The responses should be immediate without AI token consumption.