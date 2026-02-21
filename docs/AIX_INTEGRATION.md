# AIX Integration Guide

## What is AIX?

[AIX](https://github.com/thoreinstein/aix) is a unified AI assistant configuration management tool that provides "write once, deploy everywhere" for skills, MCP servers, and slash commands.

**Supported Platforms:**
- Claude Code
- Gemini CLI
- OpenCode
- Codex CLI

## Critical: Hook Registration

**Hooks are essential for autorun functionality.** They power:
- File policy enforcement (allow/justify/find modes)
- Command blocking (dangerous command prevention)
- Plan export automation
- Task lifecycle tracking

### AIX + Hooks: How It Works

AIX may not fully understand Claude Code/Gemini CLI hook systems. **Autorun handles this automatically:**

1. **AIX Installation**: Runs `autorun --install` in post_install hooks
2. **Hook Registration**: `autorun --install` registers hooks with each CLI
3. **Bootstrap Fallback**: If hooks fail to register, autorun auto-detects on first use
4. **Background Fix**: Bootstrap mechanism runs in background, registers hooks automatically

**You don't need to do anything** - hooks will work correctly either way.

### Verification

After AIX installation, verify hooks are registered:

```bash
# Claude Code
cat ~/.claude/hooks.json | grep autorun

# Gemini CLI
cat ~/.config/gemini-cli/config.json | grep autorun
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

### Install autorun via AIX

```bash
# Install from GitHub
aix skills install ahundt/autorun

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
# Should show: autorun (v0.8.0)

# Check platform-specific installations
claude plugin list | grep autorun
gemini extensions list | grep autorun
```

## Direct Installation (Alternative)

If AIX is not available or you prefer manual control:

```bash
# Clone repository
git clone https://github.com/ahundt/autorun.git
cd autorun

# Install Python package
uv pip install .

# Register with platforms
autorun --install                # All detected platforms
autorun --install --claude-only  # Claude Code only
autorun --install --gemini-only  # Gemini CLI only
```

## Managing autorun via AIX

### Update

```bash
# Update autorun across ALL platforms
aix skills update autorun

# Update all skills
aix skills update
```

### Uninstall

```bash
# Remove from ALL platforms
aix skills remove autorun

# Platform-specific removal
aix skills remove autorun --platform claude_code
```

### Configuration

```bash
# Show autorun info
aix skills info autorun

# View available commands
aix skills info autorun --commands
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
   post_install = ["autorun", "--install"]
   ```
   - AIX runs `autorun --install` after package installation
   - This registers hooks in `~/.claude/hooks.json`

2. **Via Bootstrap** (automatic fallback):
   - If hooks aren't registered, first hook invocation detects this
   - Background bootstrap process runs: `autorun --install`
   - Next hook invocation finds hooks registered
   - User sees no interruption (fail-open design)

3. **Manual** (if needed):
   ```bash
   autorun --install
   ```

### Bootstrap Mechanism

Located in `plugins/autorun/hooks/hook_entry.py`:

```python
def spawn_background_bootstrap() -> bool:
    """Spawn bootstrap in background using nohup.

    Runs: uv pip install autorun && autorun --install
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
git clone https://github.com/ahundt/autorun.git
cd autorun

# Install in dev mode
aix skills install . --dev-mode

# Make changes to code
# Edit plugins/autorun/src/autorun/main.py

# Reload changes across platforms
aix skills reload autorun

# Test on specific platform
aix test autorun --platform gemini_cli

# Publish updates (MANUAL - not automatic)
git push
# Then manually publish to AIX registry:
# aix publish autorun
```

**Note**: Publishing to AIX registry is MANUAL. The code never calls `aix publish`.

## Conductor Integration (Gemini CLI)

When installing via AIX for Gemini CLI, Conductor extension is automatically installed to provide plan mode functionality:

```bash
# AIX automatically runs in post_install:
# autorun --install --gemini --conductor

# Verify Conductor
gemini extensions list | grep conductor

# Use Conductor with autorun
/conductor:setup           # Initialize context
/ar:plannew <task>         # Create plan (autorun)
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

### autorun commands not working after AIX install

First, check if hooks are registered:
```bash
# Claude Code
cat ~/.claude/hooks.json | grep autorun

# Gemini CLI
cat ~/.config/gemini-cli/config.json | grep autorun
```

If missing, the bootstrap mechanism will auto-register on first use. Or run manually:
```bash
autorun --install
```

### Hooks not triggering

1. **Verify registration** (see above)
2. **Check bootstrap status**:
   ```bash
   # Check if bootstrap is running
   ps aux | grep autorun

   # Check bootstrap lockfile
   ls -la /tmp/autorun_bootstrap.lock
   ```
3. **Manual registration**:
   ```bash
   autorun --install --force
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
export AUTORUN_NO_BOOTSTRAP=1

# Or via flag
autorun --no-bootstrap

# Re-enable
autorun --enable-bootstrap
```

**Warning**: Only disable if you know what you're doing. Bootstrap ensures hooks are registered.

## Sources

- [AIX GitHub Repository](https://github.com/thoreinstein/aix)
- [AIX Homebrew Tap](https://github.com/thoreinstein/homebrew-tap)
- [Conductor Extension](https://github.com/gemini-cli-extensions/conductor)
- [autorun Repository](https://github.com/ahundt/autorun)
- [Claude Code Hooks Documentation](https://docs.claude.com/en/docs/claude-code/hooks)
