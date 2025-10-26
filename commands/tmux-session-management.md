---
name: tmux-session-management
description: Simple and safe tmux session management for development workflows. Creates isolated sessions that won't interfere with your current Claude Code session.
model: sonnet
---

# Tmux Session Management

**Simple, safe, and focused tmux session management** that creates isolated environments for development and testing without affecting your current Claude Code session.

## Quick Start (Easy to Use Correctly)

### For Development Projects
```bash
# Create a development session (most common use case)
/clautorun tmux-session-management create my-project

# Check status of your sessions
/clautorun tmux-session-management list

# Clean up when done (optional)
/clautorun tmux-session-management cleanup
```

### For Testing
```bash
# Create an isolated testing environment
/clautorun tmux-session-management create test-env --template testing

# Run automated tests in isolation
/clautorun tmux-test-workflow npm test
```

## Available Actions

### `create` - Create New Session
```bash
/clautorun tmux-session-management create <session-name> [--template <type>]
```

**Session Templates:**
- `basic` (default) - Simple single window session
- `development` - 3 windows: code, terminal, testing
- `testing` - 2 windows: tests, logs

**Examples:**
```bash
/clautorun tmux-session-management create my-project
/clautorun tmux-session-management create api-dev --template development
/clautorun tmux-session-management create test-runner --template testing
```

### `list` - Show Active Sessions
```bash
/clautorun tmux-session-management list
```

Shows:
- Session names and status
- Number of windows
- Last activity time
- Health status

### `cleanup` - Remove Old Sessions
```bash
/clautorun tmux-session-management cleanup [--older-than <time>]
```

**Examples:**
```bash
/clautorun tmux-session-management cleanup                    # Clean up old sessions
/clautorun tmux-session-management cleanup --older-than 1h  # Clean up sessions older than 1 hour
```

## Safety Features (Hard to Use Incorrectly)

### ✅ Always Safe
- **Never affects current session**: Commands only target created sessions
- **Automatic cleanup**: Old sessions are automatically cleaned up
- **Isolated environments**: Each session runs in complete isolation
- **Health monitoring**: Sessions are monitored for responsiveness

### 🛡️ Built-in Protections
- **No destructive operations**: Won't kill or modify sessions you didn't create
- **Safe defaults**: Uses conservative settings by default
- **Clear feedback**: Shows exactly what actions will be taken
- **Rollback capability**: Can undo accidental changes

### ⚠️ What We Prevent
- Commands interfering with your current Claude Code session
- Accidentally deleting important sessions
- Creating sessions that consume excessive resources
- Complex configuration that could cause issues

## Example Workflow

### 1. Start a New Project
```bash
# Create a development session
/clautorun tmux-session-management create my-web-app --template development
```

### 2. Work in the Session
- Session is created with 3 windows: code, terminal, testing
- Session runs in isolation, won't affect your current work
- All commands execute safely within the session

### 3. Check Status
```bash
/clautorun tmux-session-management list
```

### 4. Clean Up When Done
```bash
/clautorun tmux-session-management cleanup
```

## Session Details

### Development Template (Most Popular)
- **Window 1**: Code editor/workspace
- **Window 2**: Terminal/commands
- **Window 3**: Testing/logs
- **Use Case**: Software development projects
- **Safety**: Monitored for resource usage

### Testing Template
- **Window 1**: Test runner
- **Window 2**: Logs/output
- **Use Case**: Running automated tests
- **Safety**: Automatic cleanup on completion

### Basic Template
- **Window 1**: Single workspace
- **Use Case**: Simple tasks and quick testing
- **Safety**: Minimal resource usage

## Session Safety Rules

1. **No interference**: Sessions never affect your current Claude Code session
2. **Resource limits**: Sessions are monitored for excessive resource usage
3. **Automatic cleanup**: Old sessions are automatically removed
4. **Health checks**: Sessions are monitored for responsiveness
5. **Isolation**: Each session runs in complete isolation from others

## Error Handling

### What Happens If Something Goes Wrong

- **Session unresponsive**: Automatic recovery attempts
- **High resource usage**: Warning and automatic cleanup
- **Session stuck**: Safe termination and restart
- **Command fails**: Clear error message with suggested fix

### Recovery Options

```bash
# Check session health
/clautorun tmux-session-management list

# Force cleanup if needed (rare)
/clautorun tmux-session-management cleanup --force
```

This focused session management system provides **simple, safe, and reliable** tmux session control that's **easy to use correctly and hard to use incorrectly**.

## Session Templates

### Basic Template
```
Layout: Single window with even-horizontal split
Windows: 1-2 windows
Use Case: Simple tasks and quick testing
Configuration: Default settings with basic monitoring
```

### Development Template
```
Layout: 3 windows (code, terminal, testing)
Windows: 3+ windows with custom naming
Use Case: Software development workflows
Configuration: Enhanced monitoring and git integration
```

### Testing Template
```
Layout: 4 windows (test, logs, monitoring, results)
Windows: 4+ windows with specialized layouts
Use Case: Automated testing and QA workflows
Configuration: Resource monitoring and test result tracking
```

### Monitoring Template
```
Layout: 6 windows (system, logs, metrics, alerts, recovery, backup)
Windows: 6+ windows with comprehensive monitoring
Use Case: System monitoring and DevOps workflows
Configuration: Advanced monitoring and alerting
```

## Interactive Workflows

### Session Creation Workflow

**1. Session Configuration**
```
Enter session name (or press Enter for auto-generated): my-project-session
Select template [basic/development/testing/monitoring]: development
Number of windows [1-10]: 3
Enable persistence? [y/N]: y
Enable health monitoring? [Y/n]: y
```

**2. Session Setup Process**
1. Create tmux session with specified name and configuration
2. Apply selected template with appropriate window layout
3. Configure monitoring and persistence settings
4. Set up session-specific environment variables
5. Verify session responsiveness and health status

**3. Post-Creation Verification**
- Confirm all windows are properly configured
- Test session connectivity and responsiveness
- Initialize monitoring systems
- Display session access instructions

### Session Monitoring Workflow

**1. Health Check Analysis**
```python
from clautorun.tmux_utils import get_tmux_utilities
import time
import json
import threading

def perform_comprehensive_health_check(session_name):
    """Perform detailed session health analysis"""
    tmux = get_tmux_utilities(session_name)

    health_checks = [
        # Basic responsiveness
        {'test': 'basic_responsiveness', 'cmd': ['echo', 'health-check']},

        # Process activity
        {'test': 'process_activity', 'cmd': ['ps', 'aux']},

        # Resource usage
        {'test': 'resource_usage', 'cmd': ['top', '-b', '-n', '1']},

        # Memory status
        {'test': 'memory_status', 'cmd': ['free', '-h']},

        # Disk usage
        {'test': 'disk_usage', 'cmd': ['df', '-h']}
    ]

    results = {}
    for check in health_checks:
        result = tmux.execute_tmux_command(check['cmd'])
        results[check['test']] = {
            'success': result and result['returncode'] == 0,
            'output': result['stdout'] if result else '',
            'timestamp': time.time(),
            'error': result['stderr'] if result else ''
        }

    return analyze_health_results(results)

def analyze_health_results(results):
    """Analyze health check results and return status"""
    total_checks = len(results)
    passed_checks = sum(1 for r in results.values() if r['success'])

    if passed_checks == total_checks:
        return 'healthy', passed_checks, total_checks
    elif passed_checks >= total_checks * 0.7:
        return 'warning', passed_checks, total_checks
    else:
        return 'critical', passed_checks, total_checks
```

**2. Real-time Monitoring**
- Track session responsiveness every 30 seconds
- Monitor resource usage patterns and trends
- Detect hanging processes and infinite loops
- Alert on performance degradation or resource exhaustion
- Maintain historical health data for trend analysis

**3. Automated Recovery Procedures**
```python
def execute_recovery_procedures(session_name, issue_type):
    """Execute automated recovery based on issue type"""
    tmux = get_tmux_utilities(session_name)

    recovery_strategies = {
        'hanging_process': [
            lambda: tmux.send_keys('C-c'),  # Interrupt current process
            lambda: tmux.send_keys('C-z'),  # Suspend process
            lambda: tmux.execute_tmux_command(['kill', '-9', '$(pgrep -f "hanging_process")'])
        ],
        'memory_exhaustion': [
            lambda: tmux.send_keys('C-c'),  # Stop memory-intensive process
            lambda: tmux.execute_tmux_command(['pkill', '-f', 'memory_intensive']),
            lambda: tmux.execute_tmux_command(['sync']),  # Flush buffers
        ],
        'session_unresponsive': [
            lambda: tmux.execute_tmux_command(['display-message', '-p', 'ping']),
            lambda: tmux.send_keys('C-l'),  # Clear terminal
            lambda: tmux.execute_tmux_command(['refresh-client'])  # Refresh session
        ]
    }

    strategies = recovery_strategies.get(issue_type, [])
    for i, strategy in enumerate(strategies):
        try:
            if strategy():
                # Verify recovery success
                if verify_session_health(tmux):
                    return True, f"Recovery successful using strategy {i+1}"
        except Exception as e:
            continue

    return False, "All recovery strategies failed"
```

### Session Organization Workflow

**1. Categorization System**
```
Active Sessions by Category:

📁 Development Projects
├── my-project-session (3 windows, healthy)
├── api-development (2 windows, monitoring)
└── frontend-workflow (4 windows, warning: high memory)

📁 Testing Environments
├── integration-tests (6 windows, running)
├── performance-testing (2 windows, idle)
└── qa-workflow (3 windows, healthy)

📁 System Administration
├── server-monitoring (4 windows, critical)
├── log-analysis (2 windows, healthy)
└── backup-operations (1 window, scheduled)
```

**2. Batch Operations**
- Create multiple sessions from template
- Apply configuration changes across sessions
- Perform health checks on all sessions
- Clean up terminated or orphaned sessions
- Backup and restore session configurations

### Session Backup and Recovery

**1. Configuration Backup**
```python
def backup_session_configuration(session_name):
    """Backup complete session configuration"""
    tmux = get_tmux_utilities(session_name)

    backup_data = {
        'session_info': tmux.get_session_info(),
        'window_layout': tmux.execute_tmux_command(['list-windows']),
        'pane_contents': capture_all_pane_contents(tmux),
        'environment': tmux.execute_tmux_command(['show-environment']),
        'options': tmux.execute_tmux_command(['show-options']),
        'timestamp': time.time(),
        'session_name': session_name
    }

    # Save backup to file
    backup_file = f"./backups/{session_name}_{int(time.time())}.json"
    with open(backup_file, 'w') as f:
        json.dump(backup_data, f, indent=2)

    return backup_file
```

**2. Session Restoration**
```python
def restore_session_from_backup(backup_file):
    """Restore session from backup configuration"""
    with open(backup_file, 'r') as f:
        backup_data = json.load(f)

    session_name = backup_data['session_name']
    tmux = get_tmux_utilities(session_name)

    # Recreate session
    if not tmux.ensure_session_exists(session_name):
        return False, "Failed to recreate session"

    # Restore window layout
    for window_info in backup_data['window_layouts']:
        tmux.execute_win_op('new-window', [window_info['name']])

    # Restore environment variables
    for env_var in backup_data['environment']:
        tmux.send_keys(f"export {env_var}")

    # Restore session options
    for option in backup_data['options']:
        tmux.execute_tmux_command(['set-option', option])

    return True, f"Session {session_name} restored successfully"
```

## Implementation Details

### Session State Management

```python
class SessionManager:
    def __init__(self):
        self.tmux = get_tmux_utilities()
        self.session_states = {}
        self.monitoring_active = {}

    def create_managed_session(self, name, template='basic'):
        """Create session with automatic management"""
        session_id = self.create_session(name, template)
        self.session_states[session_id] = {
            'name': name,
            'template': template,
            'created_at': time.time(),
            'health_status': 'healthy',
            'last_health_check': time.time()
        }

        # Start monitoring if enabled
        if template in ['development', 'testing', 'monitoring']:
            self.start_health_monitoring(session_id)

        return session_id

    def start_health_monitoring(self, session_id):
        """Start continuous health monitoring"""
        self.monitoring_active[session_id] = True

        def monitor_loop():
            while self.monitoring_active.get(session_id, False):
                health_status = self.check_session_health(session_id)
                self.session_states[session_id]['last_health_check'] = time.time()
                self.session_states[session_id]['health_status'] = health_status

                if health_status in ['warning', 'critical']:
                    self.handle_health_issue(session_id, health_status)

                time.sleep(30)  # Check every 30 seconds

        # Start monitoring in background
        threading.Thread(target=monitor_loop, daemon=True).start()
```

### Health Check Algorithms

```python
def analyze_session_health(tmux, session_name):
    """Comprehensive health analysis"""
    health_metrics = {
        'responsiveness': check_responsiveness(tmux),
        'resource_usage': check_resource_usage(tmux),
        'process_activity': check_process_activity(tmux),
        'error_patterns': check_error_patterns(tmux)
    }

    # Calculate overall health score
    health_score = calculate_health_score(health_metrics)

    if health_score >= 90:
        return 'healthy'
    elif health_score >= 70:
        return 'warning'
    else:
        return 'critical'

def calculate_health_score(metrics):
    """Calculate overall health score from metrics"""
    weights = {
        'responsiveness': 0.4,
        'resource_usage': 0.3,
        'process_activity': 0.2,
        'error_patterns': 0.1
    }

    score = 0
    for metric, weight in weights.items():
        metric_score = metrics.get(metric, 0)
        score += metric_score * weight

    return min(100, max(0, score))
```

## Error Handling

### Session Recovery Strategies

1. **Level 1 Recovery** (Soft Recovery)
   - Send interrupt signals (Ctrl+C)
   - Clear terminal and reset state
   - Refresh session connection

2. **Level 2 Recovery** (Process Recovery)
   - Terminate hanging processes
   - Kill resource-intensive processes
   - Restart failed services

3. **Level 3 Recovery** (Session Recovery)
   - Detach and reattach session
   - Recreate session windows
   - Restore from backup if available

4. **Level 4 Recovery** (System Recovery)
   - Restart tmux server
   - Recreate entire session
   - Manual intervention required

## Usage Examples

### Basic Session Management
```bash
# Create new development session
/clautorun tmux-session-management create my-dev-project --template development

# List all active sessions with health status
/clautorun tmux-session-management list

# Start monitoring for specific session
/clautorun tmux-session-management monitor my-dev-project
```

### Advanced Session Operations
```bash
# Create session with custom configuration
/clautorun tmux-session-management create testing-session --template testing --windows 4 --monitor

# Recover stuck session
/clautorun tmux-session-management recover problematic-session

# Backup session configuration
/clautorun tmux-session-management backup important-session

# Clean up old sessions
/clautorun tmux-session-management cleanup --older-than 7days
```

### Batch Operations
```bash
# Organize sessions by project category
/clautorun tmux-session-management organize --category development

# Apply configuration to multiple sessions
/clautorun tmux-session-management configure --pattern "dev-*" --enable-persistence

# Health check all sessions
/clautorun tmux-session-management health-check --all-sessions
```

## Output Format

The command provides detailed output including:

### Session List Output
```
Active Sessions (3 total):

🟢 my-dev-project (development template)
   Windows: 3 | Memory: 245MB | CPU: 2% | Status: Healthy
   Last activity: 2 minutes ago | Uptime: 4h 23m

🟡 testing-session (testing template)
   Windows: 4 | Memory: 1.2GB | CPU: 15% | Status: Warning
   Last activity: 15 minutes ago | Uptime: 1h 45m
   ⚠️ High memory usage detected

🔴 old-session (basic template)
   Windows: 1 | Memory: 0MB | CPU: 0% | Status: Critical
   Last activity: 2 hours ago | Uptime: 2d 14h
   ❌ Session unresponsive - recovery recommended
```

### Health Check Output
```
Session Health Report: my-dev-project

Overall Status: 🟢 Healthy (Score: 94/100)

Detailed Metrics:
✅ Responsiveness: 100% (commands execute within 100ms)
✅ Resource Usage: 85% (memory normal, CPU low)
✅ Process Activity: 95% (healthy process patterns)
✅ Error Patterns: 100% (no errors detected)

Recommendations:
- Session is performing optimally
- Consider backup if important work in progress
- No immediate action required
```

This comprehensive session management system provides reliable, automated control over tmux environments with intelligent monitoring and recovery capabilities.