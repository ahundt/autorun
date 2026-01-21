# Happy CLI Daemon Metro Integration & iOS Install Plan

## Objective

1. Fix daemon to start Metro bundler on boot when `--with-metro` flag is used
2. Fix iOS provisioning profile programmatically
3. Build and install Happy app to iPhone
4. Update iOS setup notes with happy-cli daemon as primary pathway

---

## Problem Analysis

### Issue 1: Daemon doesn't start Metro

**Root Cause** (verified by git history and code inspection):
- Original commit `78a89ad` ("feat(daemon): add Metro bundler integration") added all infrastructure:
  - `src/daemon/metro.ts` - Metro service with startMetro(), stopMetro()
  - `src/persistence.ts` - readMetroConfig(), writeMetroConfig()
  - `src/index.ts` - CLI commands and config writing with --with-metro flag
  - `src/daemon/controlServer.ts` - HTTP endpoints
- **BUT: Never added code to `src/daemon/run.ts` to read config and call startMetro() on boot!**
- **Verified**: Recent merge `2a2b054` (last 24h) did NOT affect run.ts - `git diff HEAD~1..HEAD -- src/daemon/run.ts` shows no changes
- This is NOT a merge bug - the feature was incomplete from the original implementation

**Evidence**:
```bash
# Config file exists with correct content:
cat ~/.happy/metro.config.json
# {"enabled":true,"projectPath":"/Users/athundt/src/happy","port":8081}

# But daemon status shows:
happy metro status
# 📱 Metro: Not enabled

# Git shows run.ts was never modified in metro commit:
git show 78a89ad --stat | grep run.ts
# (no output - run.ts wasn't touched)
```

### Issue 2: iOS Provisioning Profile

**Error**:
```
Provisioning profile "iOS Team Provisioning Profile: io.github.ahundt.happy.dev"
doesn't include the currently selected device "Andrew Hundt's iPhone 2023"
```

**Solution**: This can be fixed programmatically by having Xcode regenerate the profile via `xcodebuild` with automatic signing. The device IS registered (user confirmed), just the cached profile needs refresh.

---

## Implementation Plan

### Fix 1: Daemon Metro Startup (src/daemon/run.ts)

**Files**: `/Users/athundt/source/happy-cli/src/daemon/run.ts`

**STATUS: PARTIALLY IMPLEMENTED** (imports and startup added, cleanup pending)

**Step 1** ✅ DONE: Add imports (line 16, 20):
```typescript
// Line 16 - added readMetroConfig to existing import:
import { ..., readMetroConfig } from '@/persistence';

// Line 20 - new import added:
import { startMetro, stopMetro } from './metro';
```

**Step 2** ✅ DONE: After daemon state is written (lines 676-681), start Metro if configured:
```typescript
// Start Metro bundler if configured
const metroConfig = await readMetroConfig();
if (metroConfig && metroConfig.enabled) {
  logger.debug(`[DAEMON RUN] Starting Metro bundler at ${metroConfig.projectPath}`);
  await startMetro(metroConfig);
}
```

**Step 3** ⏳ TODO: In `cleanupAndShutdown` function (line 813), stop Metro after clearing health check interval:
```typescript
// Add after line 812 (health check interval cleared), before updating daemon state:
// Stop Metro if running
stopMetro();
logger.debug('[DAEMON RUN] Metro bundler stopped');
```

### Fix 2: iOS Build with Profile Regeneration

**Approach**: Use `xcodebuild` with `-allowProvisioningUpdates` flag to automatically refresh the provisioning profile.

**Command**:
```bash
cd /Users/athundt/src/happy
xcodebuild -workspace ios/Happydev.xcworkspace \
  -scheme Happydev \
  -configuration Debug \
  -destination 'id=00008120-000A21403A98201E' \
  -allowProvisioningUpdates \
  -allowProvisioningDeviceRegistration \
  build
```

Then install with:
```bash
xcrun devicectl device install app --device 00008120-000A21403A98201E ./ios/build/Build/Products/Debug-iphoneos/Happydev.app
```

Or use Expo with profile refresh:
```bash
EXPO_ALLOW_PROVISIONING_UPDATES=1 yarn ios --device 00008120-000A21403A98201E
```

### Fix 3: Update iOS Setup Notes

**File**: `/Users/athundt/src/happy/notes/iphone-setup-guide.md`

**Changes**:
1. Add happy-cli daemon with metro as the **primary** method
2. Move yarn metro:daemon to secondary/alternative
3. Add troubleshooting for provisioning profile regeneration

---

## Execution Sequence

1. **Build happy-cli** with daemon fix
   ```bash
   cd /Users/athundt/source/happy-cli
   yarn build
   ```

2. **Restart daemon with metro**
   ```bash
   ./bin/happy.mjs daemon stop
   ./bin/happy.mjs daemon start --with-metro --metro-project /Users/athundt/src/happy
   ```

3. **Verify Metro starts**
   ```bash
   ./bin/happy.mjs metro status
   # Should show: Metro: ✅ running
   ```

4. **Build iOS app with profile refresh**
   ```bash
   cd /Users/athundt/src/happy
   xcodebuild -workspace ios/Happydev.xcworkspace \
     -scheme Happydev \
     -configuration Debug \
     -destination 'id=00008120-000A21403A98201E' \
     -allowProvisioningUpdates \
     build
   ```

5. **Install to device**
   ```bash
   # Either via xcodebuild output or:
   yarn ios --device 00008120-000A21403A98201E
   ```

6. **Update documentation**

---

## Verification

1. **Metro Integration**:
   ```bash
   ./bin/happy.mjs daemon stop
   ./bin/happy.mjs daemon start --with-metro --metro-project /Users/athundt/src/happy
   sleep 10
   ./bin/happy.mjs metro status
   curl http://localhost:8081/status  # Should return 200
   ```

2. **iOS App**:
   - App installs successfully
   - App launches on iPhone
   - App connects to Metro (hot reload works)

3. **Documentation**:
   - iphone-setup-guide.md reflects happy daemon as primary
   - Commands are tested and working

---

## Files to Modify

| File | Change |
|------|--------|
| `happy-cli/src/daemon/run.ts` | Add Metro startup/shutdown on daemon boot |
| `happy/notes/iphone-setup-guide.md` | Update with happy daemon primary pathway |

---

## Commit Messages

### happy-cli repo:
```
fix(daemon): start Metro bundler when --with-metro flag is used

Previous behavior:
- `happy daemon start --with-metro` wrote metro.config.json
- Daemon never read the config or started Metro
- Metro status showed "Not enabled"

What changed:
- src/daemon/run.ts: Import readMetroConfig and startMetro/stopMetro
- Read metro config after daemon control server starts
- Start Metro if config.enabled is true
- Stop Metro in cleanupAndShutdown function

Testable:
happy daemon start --with-metro --metro-project /path
happy metro status  # Should show running
```

### happy repo:
```
docs(ios): update setup guide with happy daemon as primary Metro method

- Add happy daemon start --with-metro as primary approach
- Document xcodebuild -allowProvisioningUpdates for profile refresh
- Keep yarn metro:daemon as alternative method
```
