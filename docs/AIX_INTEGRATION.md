# AIX Integration Guide

## What is AIX?

[AIX](https://github.com/thoreinstein/aix) is a unified AI assistant configuration management tool that provides "write once, deploy everywhere" for skills, MCP servers, and slash commands.

**Supported Platforms:**
- Claude Code
- Gemini CLI
- OpenCode
- Codex CLI

## Critical: Hook Registration

**Hooks are essential for clautorun functionality.** They power:
- File policy enforcement (allow/justify/find modes)
- Command blocking (dangerous command prevention)
- Plan export automation
- Task lifecycle tracking

### AIX + Hooks: How It Works

AIX may not fully understand Claude Code/Gemini CLI hook systems. **Clautorun handles this automatically:**

1. **AIX Installation**: Runs `clautorun --install` in post_install hooks
2. **Hook Registration**: `clautorun --install` registers hooks with each CLI
3. **Bootstrap Fallback**: If hooks fail to register, clautorun auto-detects on first use
4. **Background Fix**: Bootstrap mechanism runs in background, registers hooks automatically

**You don't need to do anything** - hooks will work correctly either way.

### Verification

After AIX installation, verify hooks are registered:

```bash
# Claude Code
cat ~/.claude/hooks.json | grep clautorun

# Gemini CLI
cat ~/.config/gemini-cli/config.json | grep clautorun
```

If hooks aren't registered, they'll auto-register on first use via bootstrap mechanism.

## Installation via AIX

### Prerequisites

```bash
# Install AIX (macOS/Linux)
brew install thoreinstein/tap/aix

# Verify installation
aix --version
```

### Install clautorun via AIX

```bash
# Install from GitHub
aix skills install ahundt/clautorun

# AIX auto-detects and installs for ALL available platforms
# Example output:
# ✓ Installed to Claude Code
# ✓ Installed to Gemini CLI (+ Conductor extension)
# ✓ Installed to OpenCode
#
# Verifying hook registration...
# ✓ Claude Code hooks registered
# ✓ Gemini CLI hooks registered
```

### Verify Installation

```bash
# List installed skills
aix skills list
# Should show: clautorun (v0.8.0)

# Check platform-specific installations
claude plugin list | grep clautorun
gemini extensions list | grep clautorun
```

## Direct Installation (Alternative)

If AIX is not available or you prefer manual control:

```bash
# Clone repository
git clone https://github.com/ahundt/clautorun.git
cd clautorun

# Install Python package
uv pip install .

# Register with platforms
clautorun --install                # All detected platforms
clautorun --install --claude-only  # Claude Code only
clautorun --install --gemini-only  # Gemini CLI only
```

## Managing clautorun via AIX

### Update

```bash
# Update clautorun across ALL platforms
aix skills update clautorun

# Update all skills
aix skills update
```

### Uninstall

```bash
# Remove from ALL platforms
aix skills remove clautorun

# Platform-specific removal
aix skills remove clautorun --platform claude_code
```

### Configuration

```bash
# Show clautorun info
aix skills info clautorun

# View available commands
aix skills info clautorun --commands
```

## AIX vs Direct Installation

| Feature | AIX | Direct |
|---------|-----|--------|
| **Multi-platform** | ✅ Auto-detects all CLIs | ⚠️ Manual flags required |
| **Updates** | ✅ `aix skills update` | ⚠️ Per-platform commands |
| **Uninstall** | ✅ `aix skills remove` | ⚠️ Manual cleanup |
| **Community registry** | ✅ Share via AIX registry | ❌ GitHub only |
| **Version management** | ✅ Built-in | ⚠️ Manual git tags |
| **Hook registration** | ✅ Auto via post_install + bootstrap | ✅ Direct via --install |
| **Development mode** | ⚠️ Limited | ✅ Full flexibility |

## Hook Registration Deep Dive

### How Hooks Get Registered

1. **Via AIX** (automatic):
   ```toml
   # aix.toml defines post_install
   [install.claude_code]
   post_install = ["clautorun", "--install"]
   ```
   - AIX runs `clautorun --install` after package installation
   - This registers hooks in `~/.claude/hooks.json`

2. **Via Bootstrap** (automatic fallback):
   - If hooks aren't registered, first hook invocation detects this
   - Background bootstrap process runs: `clautorun --install`
   - Next hook invocation finds hooks registered
   - User sees no interruption (fail-open design)

3. **Manual** (if needed):
   ```bash
   clautorun --install
   ```

### Bootstrap Mechanism

Located in `plugins/clautorun/hooks/hook_entry.py`:

```python
def spawn_background_bootstrap() -> bool:
    """Spawn bootstrap in background using nohup.

    Runs: uv pip install clautorun && clautorun --install
    Returns immediately; next invocation finds deps/hooks installed.
    """
```

**Why this matters:**
- Hooks have 10s timeout (Claude enforced)
- Bootstrap can take 5-10s with UV
- Background spawn ensures no timeout
- Fail-open design: Claude continues even if hooks fail

## Limitations

AIX may not fully support:

1. **Hook Configuration**: Hooks are platform-specific (Claude vs Gemini event models)
   - **Solution**: post_install + bootstrap ensure registration
2. **Dynamic Bash Output**: Commands using `!` prefix for bash output
   - **Solution**: Native extension manifest handles this
3. **Agent Definitions**: Agents may need platform-specific customization
   - **Solution**: Native extension manifest includes agents
4. **Development Workflow**: Local edits require `aix skills reload`
   - **Solution**: Use direct installation for development

**Recommendation:**
- **Production**: Use AIX for unified multi-platform management
- **Development**: Use direct installation for full flexibility

## Development Workflow with AIX

```bash
# Clone for development
git clone https://github.com/ahundt/clautorun.git
cd clautorun

# Install in dev mode
aix skills install . --dev-mode

# Make changes to code
# Edit plugins/clautorun/src/clautorun/main.py

# Reload changes across platforms
aix skills reload clautorun

# Test on specific platform
aix test clautorun --platform gemini_cli

# Publish updates (MANUAL - not automatic)
git push
# Then manually publish to AIX registry:
# aix publish clautorun
```

**Note**: Publishing to AIX registry is MANUAL. The code never calls `aix publish`.

## Conductor Integration (Gemini CLI)

When installing via AIX for Gemini CLI, Conductor extension is automatically installed to provide plan mode functionality:

```bash
# AIX automatically runs in post_install:
# clautorun --install --gemini --conductor

# Verify Conductor
gemini extensions list | grep conductor

# Use Conductor with clautorun
/conductor:setup           # Initialize context
/cr:plannew <task>         # Create plan (clautorun)
/conductor:newTrack <task> # Create track (Conductor)
```

## Troubleshooting

### AIX reports "platform not supported"

Check if the platform's CLI is in your PATH:
```bash
which claude   # Claude Code
which gemini   # Gemini CLI
which opencode # OpenCode
which codex    # Codex CLI
```

### clautorun commands not working after AIX install

First, check if hooks are registered:
```bash
# Claude Code
cat ~/.claude/hooks.json | grep clautorun

# Gemini CLI
cat ~/.config/gemini-cli/config.json | grep clautorun
```

If missing, the bootstrap mechanism will auto-register on first use. Or run manually:
```bash
clautorun --install
```

### Hooks not triggering

1. **Verify registration** (see above)
2. **Check bootstrap status**:
   ```bash
   # Check if bootstrap is running
   ps aux | grep clautorun

   # Check bootstrap lockfile
   ls -la /tmp/clautorun_bootstrap.lock
   ```
3. **Manual registration**:
   ```bash
   clautorun --install --force
   ```

### Updates via AIX not taking effect

Some platforms cache commands/skills. Try:
```bash
# Clear cache (platform-specific)
claude plugin reload
gemini extensions reload

# Or restart the CLI
```

## Bootstrap Disable (Advanced)

If you need to disable automatic bootstrap:

```bash
# Disable via environment variable
export CLAUTORUN_NO_BOOTSTRAP=1

# Or via flag
clautorun --no-bootstrap

# Re-enable
clautorun --enable-bootstrap
```

**Warning**: Only disable if you know what you're doing. Bootstrap ensures hooks are registered.

## Sources

- [AIX GitHub Repository](https://github.com/thoreinstein/aix)
- [AIX Homebrew Tap](https://github.com/thoreinstein/homebrew-tap)
- [Conductor Extension](https://github.com/gemini-cli-extensions/conductor)
- [clautorun Repository](https://github.com/ahundt/clautorun)
- [Claude Code Hooks Documentation](https://docs.claude.com/en/docs/claude-code/hooks)
