# Troubleshooting Clautorun Hooks

This guide helps resolve common issues with clautorun hooks in both Claude Code and Gemini CLI.

---

## Table of Contents

- [Hooks Not Firing](#hooks-not-firing)
- [Hook Execution Errors](#hook-execution-errors)
- [Wrong Hook Version Installed](#wrong-hook-version-installed)
- [Command Blocking Not Working](#command-blocking-not-working)
- [Gemini CLI Specific Issues](#gemini-cli-specific-issues)
- [Claude Code Specific Issues](#claude-code-specific-issues)
- [Debug Logging](#debug-logging)
- [Known Issues](#known-issues)

---

## Hooks Not Firing

**Symptoms**: No hook execution in output, hooks appear to be ignored

### Gemini CLI Checklist

1. **Check Gemini CLI Version**
   ```bash
   gemini --version
   ```
   **Required**: v0.28.0 or later

   **Fix**: Update Gemini CLI
   ```bash
   # Using Bun (recommended, 2x faster)
   bun install -g @google/gemini-cli@latest

   # Or using npm
   npm install -g @google/gemini-cli@latest
   ```

2. **Check Settings Enabled**
   ```bash
   cat ~/.gemini/settings.json | grep -A3 "tools"
   ```

   **Expected Output**:
   ```json
   "tools": {
     "enableHooks": true,
     "enableMessageBusIntegration": true
   }
   ```

   **Fix**: Edit `~/.gemini/settings.json` and add the tools section (see [GEMINI.md](GEMINI.md#required-settings))

3. **Check Extension Installed**
   ```bash
   ls ~/.gemini/extensions/cr/
   ```

   **Expected**: Directory exists with `hooks/hooks.json` and other files

   **Fix**: Reinstall extension
   ```bash
   cd /path/to/clautorun
   uv run python -m plugins.clautorun.src.clautorun.install --install --gemini-only --force
   ```

4. **Check hooks.json Exists**
   ```bash
   cat ~/.gemini/extensions/cr/hooks/hooks.json | head -10
   ```

   **Expected**: Valid JSON with Gemini-format hooks

   **Fix**: If missing or corrupted, reinstall extension (step 3)

5. **Check Hook Registry**

   Start Gemini and look for this line in output:
   ```
   Hook registry initialized with N hook entries
   ```

   **Expected**: N should be > 0 (typically 6 for cr extension)

   **Fix**: If 0, reinstall extension

### Claude Code Checklist

1. **Check Plugin Installed**
   ```bash
   claude plugin list | grep clautorun
   ```

   **Expected**: Shows clautorun plugin

   **Fix**: Reinstall plugin
   ```bash
   cd /path/to/clautorun
   uv run python -m plugins.clautorun.src.clautorun.install --install --claude-only --force
   ```

2. **Check Hook File Format**
   ```bash
   cat ~/.claude/plugins/cache/clautorun/clautorun/*/hooks/hooks.json | grep CLAUDE_PLUGIN_ROOT
   ```

   **Expected**: Should find `${CLAUDE_PLUGIN_ROOT}` in commands

   **Fix**: If not found, source hooks.json is wrong format (see [Wrong Hook Version](#wrong-hook-version-installed))

3. **Check Daemon Running**
   ```bash
   ps aux | grep "clautorun.*daemon"
   ```

   **Expected**: Daemon process running

   **Fix**: Restart daemon
   ```bash
   uv run python plugins/clautorun/scripts/restart_daemon.py
   ```

---

## Hook Execution Errors

**Symptoms**: Hook fires but Python script fails with error

### Debug Steps

1. **Check Gemini/Claude Output for Error Messages**

   Look for Python tracebacks or error messages in the CLI output

2. **Test Hook Script Manually**

   For Gemini CLI:
   ```bash
   cd /tmp/test-directory
   echo '{"hook_event_name":"SessionStart"}' | python3 ~/.gemini/extensions/cr/hooks/hook_entry.py
   ```

   For Claude Code:
   ```bash
   cd /tmp/test-directory
   CLAUDE_PLUGIN_ROOT=~/.claude/plugins/cache/clautorun/clautorun/0.8.0 \
   echo '{"hook_event_name":"PreToolUse"}' | python3 ~/.claude/plugins/cache/clautorun/clautorun/0.8.0/hooks/hook_entry.py
   ```

   **Expected**: JSON output like `{"continue":true}`

3. **Test get_plugin_root() Function**

   ```python
   import sys
   sys.path.insert(0, '/path/to/hooks/')
   from hook_entry import get_plugin_root
   print(get_plugin_root())
   ```

   **Expected**: Returns plugin root directory path

4. **Check Python Version**
   ```bash
   python3 --version
   ```

   **Required**: Python 3.8 or later

### Common Errors

**Error**: `ModuleNotFoundError: No module named 'clautorun'`

**Cause**: Hook script can't find clautorun module

**Fix**: Ensure get_plugin_root() returns correct path, then check sys.path setup in hook_entry.py

---

**Error**: `PermissionError: [Errno 13] Permission denied`

**Cause**: Hook script not executable or file permissions wrong

**Fix**:
```bash
chmod +x ~/.gemini/extensions/cr/hooks/hook_entry.py
# Or for Claude Code:
chmod +x ~/.claude/plugins/cache/clautorun/clautorun/*/hooks/hook_entry.py
```

---

**Error**: `SyntaxError` or `IndentationError`

**Cause**: Corrupted hook script

**Fix**: Reinstall extension/plugin

---

## Wrong Hook Version Installed

**Symptoms**: Gemini extension has Claude-format hooks or vice versa

### Check Hook Format

**Gemini CLI hooks** should use:
- `${extensionPath}` variable
- Tool names: `write_file`, `run_shell_command`, `replace`
- Events: `SessionStart`, `SessionEnd`, `BeforeTool`, `AfterTool`

**Claude Code hooks** should use:
- `${CLAUDE_PLUGIN_ROOT}` variable
- Tool names: `Write`, `Bash`, `Edit`, `TaskCreate`
- Events: `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `Stop`

### Fix Wrong Format

1. **Uninstall Extension/Plugin**

   Gemini:
   ```bash
   gemini extensions uninstall cr
   ```

   Claude Code:
   ```bash
   claude plugin uninstall clautorun
   ```

2. **Verify Source hooks.json**

   Check that source file has correct format:
   ```bash
   cat ~/.claude/clautorun/plugins/clautorun/hooks/hooks.json | head -5
   ```

   Should show `"clautorun v0.8 - unified daemon-based hook handler"` (Claude Code version)

   Gemini version is in `hooks/gemini-hooks.json`

3. **Reinstall**

   Gemini:
   ```bash
   cd ~/.claude/clautorun
   uv run python -m plugins.clautorun.src.clautorun.install --install --gemini-only --force
   ```

   Claude Code:
   ```bash
   cd ~/.claude/clautorun
   uv run python -m plugins.clautorun.src.clautorun.install --install --claude-only --force
   ```

---

## Command Blocking Not Working

**Symptoms**: Dangerous commands (cat, rm, etc.) not being blocked

### Verify BeforeTool Hooks Fire

For Gemini CLI, start session and check output:
```
Created execution plan for BeforeTool: 1 hook(s) to execute
```

If not appearing, hooks aren't firing → See [Hooks Not Firing](#hooks-not-firing)

### Check Hook Logic

The blocking logic is in `plugins/clautorun/src/clautorun/main.py` in the `handle_hook()` function.

To verify it's loaded correctly:
```bash
grep -n "def handle_hook" ~/.claude/clautorun/plugins/clautorun/src/clautorun/main.py
```

### Test with Known Dangerous Command

Try a command that should definitely be blocked:
```bash
# In Gemini or Claude session
rm -rf /tmp/test
```

Expected: Should be blocked or redirected to `trash`

If not blocked, check:
1. Hook fires (see above)
2. Logic in main.py correct
3. Daemon up to date (Claude Code only)

---

## Gemini CLI Specific Issues

### Issue: Extensions Load But Hooks Don't Fire

**Check Hook Execution Logs**

Gemini CLI v0.28.0+ shows detailed hook logs. Start Gemini and look for:
```
Loading extension: cr
Hook registry initialized with 6 hook entries
```

Then when hooks fire:
```
Created execution plan for SessionStart: 1 hook(s) to execute in parallel
Expanding hook command: python3 /Users/.../.gemini/extensions/cr/hooks/hook_entry.py
Hook execution for SessionStart: 1 hooks executed successfully, total duration: 395ms
```

**If "Created execution plan" never appears**: Hooks not firing despite being registered

**Possible Causes**:
1. Settings not enabled (see checklist above)
2. Hook matcher pattern doesn't match tool name
3. Gemini CLI bug (check [Known Issues](#known-issues))

### Issue: Trust Folder Prompt Blocks Hook Testing

When testing in new directory, Gemini shows trust prompt. This restarts session.

**Workaround**: Test in already-trusted directory or trust folder first:
```bash
# Start Gemini, select "Trust folder" when prompted
# Then test hooks in that directory
```

---

## Claude Code Specific Issues

### Issue: Daemon Not Reloading After Code Changes

**Symptom**: Made changes to hook code but behavior unchanged

**Fix**: Restart daemon
```bash
uv run python plugins/clautorun/scripts/restart_daemon.py
```

Or use the `/cr:restart-daemon` command in Claude Code session

### Issue: CLAUDE_PLUGIN_ROOT Not Set

**Symptom**: Hook script fails with "plugin root not found"

**Check**:
```bash
# In Claude Code session, check environment
echo $CLAUDE_PLUGIN_ROOT
```

Should show plugin path

**Fix**: Claude Code should set this automatically. If not:
1. Reinstall plugin
2. Restart Claude Code application

---

## Debug Logging

### Enable Verbose Gemini Logging

Create debug version of hooks.json:

```json
{
  "hooks": {
    "BeforeTool": [{
      "matcher": ".*",
      "hooks": [{
        "name": "debug-hook",
        "type": "command",
        "command": "python3 ${extensionPath}/hooks/debug_hook.py",
        "timeout": 10000
      }]
    }]
  }
}
```

Debug hook script (`debug_hook.py`):
```python
#!/usr/bin/env python3
import sys, json, os
from datetime import datetime

stdin_data = sys.stdin.read()
timestamp = datetime.now().isoformat()

with open("/tmp/gemini-hook-debug.log", "a") as f:
    f.write(f"\n{'='*60}\n")
    f.write(f"DEBUG HOOK: {timestamp}\n")
    f.write(f"CWD: {os.getcwd()}\n")
    f.write(f"Input: {stdin_data}\n")
    f.write(f"{'='*60}\n")

print(json.dumps({"continue": True}))
```

Then check `/tmp/gemini-hook-debug.log` after running commands

### Check Hook Input/Output

Hooks receive JSON via stdin and must output JSON to stdout.

**Input Format** (example):
```json
{
  "hook_event_name": "BeforeTool",
  "tool_name": "write_file",
  "arguments": {"file_path": "/path/to/file.txt", "content": "..."},
  "session_id": "...",
  "cwd": "/current/directory"
}
```

**Output Format** (example):
```json
{
  "continue": true,
  "systemMessage": "Optional message to user"
}
```

Or to block:
```json
{
  "continue": false,
  "reason": "Why blocked",
  "systemMessage": "⚠️ Command blocked by clautorun"
}
```

---

## Known Issues

### Gemini CLI Issues

1. **BeforeTool Hooks May Not Fire for Some Commands** (v0.27.x)
   - Issue: [#14932](https://github.com/google-gemini/gemini-cli/issues/14932)
   - Status: Open
   - Workaround: Update to v0.28.0+

2. **Hooks Require Explicit Settings** (v0.26.0+)
   - Issue: [#13155](https://github.com/google-gemini/gemini-cli/issues/13155)
   - Status: Fixed, settings must be enabled
   - Fix: Add `enableHooks` and `enableMessageBusIntegration` to settings.json

3. **Extension Hooks Lower Priority Than Project Hooks**
   - Behavior: If both `.gemini/settings.json` and extension hooks exist, project settings take precedence
   - Impact: Extension hooks may be overridden
   - Workaround: Remove project hooks if you want extension hooks to work

### Claude Code Issues

1. **Daemon Caching Old Code**
   - Symptom: Changes to hook code not taking effect
   - Fix: Always restart daemon after code changes

2. **Hook Timeouts**
   - Default: 10 seconds
   - If hook takes too long, it will be killed
   - Fix: Optimize hook code or increase timeout in hooks.json

---

## Getting Help

If none of these solutions work:

1. **Check GitHub Issues**
   - Clautorun: https://github.com/ahundt/clautorun/issues
   - Gemini CLI: https://github.com/google-gemini/gemini-cli/issues

2. **File a Bug Report**

   Include:
   - CLI version (`gemini --version` or `claude --version`)
   - Python version (`python3 --version`)
   - Hook configuration (hooks.json content)
   - Full error message and logs
   - Steps to reproduce

3. **Useful Information for Bug Reports**
   ```bash
   # Gemini CLI
   gemini --version
   cat ~/.gemini/settings.json
   ls -la ~/.gemini/extensions/cr/hooks/
   cat ~/.gemini/extensions/cr/hooks/hooks.json

   # Claude Code
   claude --version
   ls -la ~/.claude/plugins/cache/clautorun/clautorun/*/hooks/
   ps aux | grep clautorun
   ```

---

## Related Documentation

- [GEMINI.md](GEMINI.md) - Gemini CLI specific documentation
- [CLAUDE.md](CLAUDE.md) - Claude Code specific documentation
- [README.md](../../README.md) - Full clautorun documentation
- [GitHub Issues](https://github.com/ahundt/clautorun/issues)

---

**Last Updated**: 2026-02-10
**Version**: 1.0
