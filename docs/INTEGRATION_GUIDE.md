# Claude Code Integration Guide

The Agent SDK can be integrated with Claude Code in multiple ways, each offering different levels of integration and efficiency.

## Integration Options

### 1. Standalone Agent SDK Application

**Use Case**: Maximum efficiency, independent operation
**Setup**:
```bash
cd clautorun
uv pip install claude-agent-sdk

# Run in SDK-Only mode (most efficient)
AGENT_MODE=SDK_ONLY python -m clautorun
```

**Pros**:
- Zero AI token consumption
- Instant responses
- Full independence from Claude Code
- Can run anywhere

**Cons**:
- Separate from Claude Code workflow
- Requires separate setup

### 2. Claude Code Slash Commands

**Use Case**: Add new slash commands to Claude Code
**Setup**:
```bash
# Create symlink to commands directory
ln -sf /path/to/clautorun/claude_code_plugin.py /Users/athundt/.claude/hooks/

# Use as slash command
/agent-sdk-interceptor sdk-only
```

**Pros**:
- Integrated with Claude Code workflow
- Familiar command interface
- Can use existing Claude Code features

**Cons**:
- Still goes through Claude Code's hook system
- Some token consumption for routing

### 3. MCP Server Integration

**Use Case**: External applications and cross-platform compatibility
**Setup**:
```bash
# Install MCP server
uv pip install mcp

# Run MCP server
python mcp_server.py --server
```

**Configuration**:
```json
{
  "mcpServers": {
    "agent-sdk": {
      "command": "python",
      "args": ["/path/to/clautorun/mcp_server.py", "--server"],
      "env": {
        "AGENT_MODE": "SDK_ONLY"
      }
    }
  }
}
```

**Pros**:
- Cross-platform compatibility
- Can be used by external applications
- Standard MCP protocol

**Cons**:
- Requires MCP client
- Additional setup complexity

### 4. Hook Replacement (Enhanced autorun5.py)

**Use Case**: Drop-in replacement for existing autorun system
**Setup**:
```bash
# Replace autorun5.py with agent_sdk_hook.py
cp autorun5.py autorun5.py.backup
cp agent_sdk_hook.py /Users/athundt/.claude/hooks/autorun5.py
```

**Pros**:
- Drop-in compatibility
- Maintains all existing behavior
- No breaking changes
- Better efficiency

**Cons**:
- Still uses hook infrastructure
- Some token consumption for non-commands

### 5. Hybrid Mode (Best of Both Worlds)

**Use Case**: Maximum efficiency with backward compatibility
**Setup**:
```bash
# Run in hybrid mode (if implemented)
AGENT_MODE=HYBRID python -m clautorun
```

**Pros**:
- SDK handles command responses (no tokens)
- Existing hooks handle enforcement
- Maximum compatibility
- Gradual migration path

**Cons**:
- Slightly more complex setup
- Two systems to maintain

## Recommendation

### For Maximum Efficiency:
Use **Option 1: Standalone Agent SDK Application** with:
```bash
AGENT_MODE=SDK_ONLY python -m clautorun
```

### For Seamless Integration:
Use **Option 4: Hook Replacement** or **Option 2: Slash Commands**

### For External Applications:
Use **Option 3: MCP Server Integration**

## Migration Path

1. **Current State**: `/afs` → Claude Code → AI → Hook (wastes tokens)
2. **Phase 1**: Run standalone Agent SDK alongside (dual operation)
3. **Phase 2**: Test slash command integration
4. **Phase 3**: Replace hooks with Agent SDK version
5. **Phase 4**: Run entirely in SDK mode

## Configuration

Set these environment variables to customize behavior:

```bash
# Mode selection
AGENT_MODE=SDK_ONLY          # Maximum efficiency
AGENT_MODE=HYBRID           # SDK + hooks

# Hook integration
USE_EXISTING_HOOKS=true     # Call existing autorun5.py
USE_EXISTING_HOOKS=false    # Pure SDK operation

# Debugging
DEBUG=true                  # Enable debug logging
```

## Testing Each Integration

1. **Standalone**:
```bash
cd clautorun
AGENT_MODE=SDK_ONLY python -m clautorun
```

2. **Slash Command**:
```bash
/agent-sdk-interceptor sdk-only
```

3. **MCP Server**:
```bash
python mcp_server.py
```

4. **Hook Replacement**:
```bash
# Test with existing commands
/afs
/afa
```

All integrations provide the same command interface and behavior, just with different integration depths.