# Plan: Persistent Metro Development Server Daemon

## Problem Statement

**User Need**: Run Metro bundler persistently so iOS devices can connect anytime without manually starting `yarn start`.

**Current Pain Points**:
1. Must manually run `yarn start` after each reboot
2. Terminal must stay open
3. iOS devices lose connection when Metro stops
4. Manual IP discovery for Tailscale vs local network

**Goal**: One command installs a daemon that "just works" - automatic, persistent, self-healing.

---

## Design Philosophy (from CLAUDE.md)

| Principle | Application |
|-----------|-------------|
| **Automatic and Correct** | `yarn metro:daemon:install` does everything |
| **Easy to Use Correctly** | Single command, no manual editing |
| **Solve Problems FOR Users** | Auto-detect IPs, auto-generate configs |
| **Specific Feedback** | Show exact IPs to use, clear status |
| **Hard to Use Incorrectly** | Validate paths, check prerequisites |

---

## Solution: Single TypeScript Management Script

### Architecture

```
sources/scripts/metro-daemon.ts    ← Main script (all logic here)
notes/metro-daemon-guide.md        ← User documentation

Generated at install time:
  ~/Library/LaunchAgents/com.happy.metro-server.plist
  ~/.happy/logs/metro-*.log
```

### Package.json Scripts (lines ~34, after tauri scripts)

```json
"// ==== Metro Daemon (Persistent Development Server) ====": "",
"metro:daemon:install": "tsx sources/scripts/metro-daemon.ts install",
"metro:daemon:uninstall": "tsx sources/scripts/metro-daemon.ts uninstall",
"metro:daemon:start": "tsx sources/scripts/metro-daemon.ts start",
"metro:daemon:stop": "tsx sources/scripts/metro-daemon.ts stop",
"metro:daemon:status": "tsx sources/scripts/metro-daemon.ts status",
"metro:daemon:logs": "tsx sources/scripts/metro-daemon.ts logs"
```

---

## Implementation: `sources/scripts/metro-daemon.ts`

### Core Design

```typescript
#!/usr/bin/env tsx
/**
 * Metro Daemon Manager
 *
 * Manages a persistent Metro bundler for iOS development.
 * - Auto-detects project path and network IPs
 * - Generates launchd plist with correct absolute paths
 * - Provides simple start/stop/status commands
 *
 * Usage:
 *   yarn metro:daemon:install   # Set up and start daemon
 *   yarn metro:daemon:status    # Check status and show IPs
 *   yarn metro:daemon:stop      # Stop daemon
 *   yarn metro:daemon:uninstall # Remove daemon completely
 */

import { execSync, spawn } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
```

### Key Functions

#### 1. `getNetworkIPs(): { local: string | null, tailscale: string | null }`
- Runs `ipconfig getifaddr en0` for local IP
- Runs `tailscale ip -4 2>/dev/null` for Tailscale IP
- Returns both, null if unavailable

#### 2. `generatePlist(projectPath: string): string`
- Generates launchd XML with **absolute paths** (no `~` - launchd doesn't expand it)
- Uses `os.homedir()` for home directory
- Embeds the project path detected at install time
- Sets `KeepAlive` with `SuccessfulExit: false` (restart on crash only)
- Sets `ThrottleInterval: 30` (prevent rapid restart loops)

#### 3. `install(): void`
- **Validates**: Node, yarn, project directory exist
- **Kills**: Any existing Metro processes
- **Creates**: `~/.happy/logs/` directory
- **Generates**: plist with absolute paths
- **Writes**: to `~/Library/LaunchAgents/com.happy.metro-server.plist`
- **Loads**: via `launchctl load`
- **Verifies**: daemon started successfully
- **Shows**: IP addresses for iOS configuration

#### 4. `status(): void`
- **Checks**: `launchctl list | grep happy`
- **Shows**: Running/stopped status
- **Shows**: Both local and Tailscale IPs
- **Shows**: Port 8081 status via `lsof`

#### 5. `stop()`, `start()`, `uninstall()`, `logs()`
- Simple wrappers around launchctl commands
- Clear success/failure messages

### Plist Template (generated with real paths)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "...">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.happy.metro-server</string>

    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/yarn</string>
        <string>start</string>
        <string>--non-interactive</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/USERNAME/source/happy</string>  <!-- ABSOLUTE PATH -->

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>StandardOutPath</key>
    <string>/Users/USERNAME/.happy/logs/metro-stdout.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/USERNAME/.happy/logs/metro-stderr.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>

    <key>ThrottleInterval</key>
    <integer>30</integer>
</dict>
</plist>
```

**Critical fix from original plan**: All paths are **absolute** (e.g., `/Users/USERNAME/...`), not `~` which launchd doesn't expand.

---

## User Experience

### Installation (one command)
```bash
$ yarn metro:daemon:install

🚀 Metro Daemon Installer

✓ Verified: Node.js v20.11.0
✓ Verified: yarn 1.22.22
✓ Verified: Project at /Users/dev/source/happy
✓ Created: ~/.happy/logs/
✓ Generated: launchd plist
✓ Loaded: com.happy.metro-server
✓ Started: Metro bundler (PID 12345)

📱 iOS Device Configuration:
   Local Network: 192.168.1.100:8081
   Tailscale:     100.80.15.31:8081

   On your device: Shake → Configure Bundler → Enter IP and port 8081
```

### Status Check
```bash
$ yarn metro:daemon:status

Metro Daemon Status
───────────────────
Status:     ✅ Running (PID 12345)
Port 8081:  ✅ Listening
Uptime:     2 hours 15 minutes

📱 Connect your iOS device:
   Local:     192.168.1.100:8081
   Tailscale: 100.80.15.31:8081
```

### Logs
```bash
$ yarn metro:daemon:logs
# Opens: tail -f ~/.happy/logs/metro-stdout.log
```

---

## Files to Create

| File | Purpose |
|------|---------|
| `sources/scripts/metro-daemon.ts` | Main management script (~200 lines) |
| `notes/metro-daemon-guide.md` | Documentation |

| File to Modify | Change |
|----------------|--------|
| `package.json` | Add 6 yarn scripts (lines ~34) |
| `notes/iphone-setup-guide.md` | Add daemon reference |

---

## Implementation Steps

1. [ ] Create `sources/scripts/metro-daemon.ts`
   - [ ] `getNetworkIPs()` - detect local + Tailscale IPs
   - [ ] `generatePlist()` - create launchd XML with absolute paths
   - [ ] `install()` - validate, generate, load, verify
   - [ ] `uninstall()` - stop, unload, remove files
   - [ ] `start()`, `stop()`, `status()`, `logs()` - management commands
   - [ ] CLI argument parsing (install|uninstall|start|stop|status|logs)

2. [ ] Add package.json scripts (after line 33)

3. [ ] Create `notes/metro-daemon-guide.md` with:
   - Quick start (just `yarn metro:daemon:install`)
   - iOS configuration instructions
   - Troubleshooting section

4. [ ] Update `notes/iphone-setup-guide.md` with daemon option

---

## Edge Cases Handled

| Edge Case | Handling |
|-----------|----------|
| Metro already running | Kill before install, clear message |
| Tailscale not installed | Show local IP only, no error |
| Port 8081 in use | Detect and report, suggest kill |
| Daemon won't start | Check logs, show last 10 lines |
| No network interfaces | Fallback to localhost |
| Intel vs Apple Silicon | Detect PATH (`/opt/homebrew` vs `/usr/local`) |
| Plist already exists | Unload first, then overwrite |

---

## Verification

1. **Install**: `yarn metro:daemon:install` succeeds with clear output
2. **Auto-start**: Reboot Mac, Metro starts automatically
3. **Crash recovery**: `pkill -9 -f "expo start"` → restarts within 30s
4. **iOS local**: Connect via local IP:8081
5. **iOS Tailscale**: Connect via Tailscale IP:8081
6. **Status**: `yarn metro:daemon:status` shows correct state
7. **Logs**: `yarn metro:daemon:logs` shows Metro output
8. **Uninstall**: `yarn metro:daemon:uninstall` removes everything cleanly

---

## Code Quality Checklist

- [ ] TypeScript strict mode compatible
- [ ] No hardcoded paths (all derived from `__dirname`, `os.homedir()`)
- [ ] Clear error messages with actionable fixes
- [ ] Follows existing `sources/scripts/*.ts` patterns
- [ ] No PII in generated files
- [ ] Works on both Intel and Apple Silicon Macs
- [ ] Handles missing Tailscale gracefully
