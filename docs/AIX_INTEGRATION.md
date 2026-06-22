# AIX Integration Guide

## What is AIX?

[AIX](https://github.com/thoreinstein/aix) is a unified AI assistant configuration management tool that provides "write once, deploy everywhere" for skills, MCP servers, and slash commands.

**Supported Platforms:**
- Claude Code
- Gemini CLI
- OpenCode
- Codex CLI

As of AIX 0.8.1, the CLI uses singular resource commands such as
`aix skill install` and `aix command install`. Autorun treats AIX as a
resource translation layer for skills and commands; direct autorun installers
still run afterward to set up hooks, desktop-app-adjacent files, plugin caches,
and Antigravity imports.

## Critical: Hook Registration

**Hooks are essential for autorun functionality.** They power:
- File policy enforcement (allow/justify/find modes)
- Command blocking (dangerous command prevention)
- Plan export automation
- Task lifecycle tracking

### AIX + Hooks: How It Works

AIX does not fully manage Claude Code, Gemini CLI, Codex, or Antigravity hook
trust/cache details. **Autorun handles this automatically:**

1. **AIX Installation**: `autorun --install --aix` installs skills/commands through AIX.
2. **Direct Installation**: the same command continues into direct Claude, Gemini,
   Codex, Antigravity, and ForgeCode installers.
3. **Hook Registration**: direct installers register hooks and plugin/cache assets.
4. **Bootstrap Fallback**: if hooks fail to register, autorun auto-detects on first use.
5. **Background Fix**: bootstrap runs in background and registers hooks automatically.

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
# Install from this repository clone
autorun --install --aix

# AIX installs skills/commands, then autorun direct installers finish setup
# Example output:
# ✓ Installation via AIX completed successfully
# Continuing with direct platform installers to verify hooks, apps, skills, and plugin caches
# ✓ Claude Code: Installed ...
# ✓ Gemini CLI: Plugins installed ...
# ✓ Google Antigravity: imported Gemini ar plugin with commands, skills, and hooks
# ✓ Codex CLI: hooks installed ...
# ✓ ForgeCode: commands + AGENTS.md installed
#
```

### Verify Installation

```bash
# List installed skills
aix skill list
# Should show autorun-owned skills such as ai-session-tools or tmux-automation

# Check platform-specific installations
claude plugin list | grep autorun
gemini extensions list | grep autorun
codex plugin list | grep autorun
agy plugin list
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
autorun --install --claude       # Claude Code only
autorun --install --gemini       # Gemini CLI only
autorun --install --codex        # Codex CLI only
autorun --install --antigravity  # Antigravity CLI import only
```

## Managing autorun via AIX

### Update

```bash
# Refresh AIX resources and direct platform setup
autorun --update --update-method aix

# Inspect installed resources
aix skill list
aix command list
```

### Uninstall

```bash
# Remove individual AIX-managed skills or commands
aix skill remove ai-session-tools --force
aix command remove st --force

# Platform-specific removal
aix skill remove ai-session-tools --platform claude --force
```

### Configuration

```bash
# Show autorun info
aix skill show ai-session-tools

# View available commands
aix command list
```

## AIX vs Direct Installation

| Feature | AIX | Direct |
|---------|-----|--------|
| **Multi-platform** | ✅ Auto-detects all CLIs | ⚠️ Manual flags required |
| **Updates** | ⚠️ resource refresh via `autorun --update --update-method aix` | ✅ full direct setup |
| **Uninstall** | ⚠️ per-resource `aix skill remove` / `aix command remove` | ⚠️ manual cleanup |
| **Community registry** | ✅ Share via AIX registry | ❌ GitHub only |
| **Version management** | ✅ Built-in | ⚠️ Manual git tags |
| **Hook registration** | ❌ handled by direct autorun installers | ✅ Direct via --install |
| **Development mode** | ⚠️ Limited | ✅ Full flexibility |

## Hook Registration Deep Dive

### How Hooks Get Registered

1. **Via AIX plus direct install**:
   ```toml
   # aix.toml defines post_install
   [install.claude_code]
   post_install = ["autorun", "--install"]
   ```
   - AIX installs translated skills/commands
   - Autorun direct installers then register hooks and caches

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
4. **Development Workflow**: AIX 0.8.1 has no `aix skill reload`; rerun
   `autorun --install --aix --force`
   - **Solution**: Use direct installation for development

**Recommendation:**
- **Production**: Use AIX for unified multi-platform management
- **Development**: Use direct installation for full flexibility

## Development Workflow with AIX

```bash
# Clone for development
git clone https://github.com/ahundt/autorun.git
cd autorun

# Install translated skills/commands plus direct hook/plugin setup
autorun --install --aix --force

# Make changes to code
# Edit plugins/autorun/src/autorun/main.py

# Re-run setup after changes
autorun --install --aix --force

# Test on specific platform
autorun --status

# Publish updates (MANUAL - not automatic)
git push
# Then manually publish to AIX registry:
# aix publish autorun
```

**Note**: Publishing to AIX registry is MANUAL. The code never calls `aix publish`.

## Conductor Integration (Gemini CLI)

When installing through `autorun --install --aix`, the direct Gemini installer
still installs Conductor for Gemini CLI:

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
