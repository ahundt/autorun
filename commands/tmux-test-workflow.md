---
name: tmux-test-workflow
description: Simple and safe CLI testing automation. Tests commands and applications in isolated tmux sessions without affecting your current work.
model: sonnet
---

# CLI Testing Workflow

**Simple, safe, and focused CLI testing** that runs tests in isolated tmux sessions without affecting your current Claude Code session.

## Quick Start (Easy to Use Correctly)

### Test Basic Commands
```bash
# Test basic functionality (most common use case)
/clautorun tmux-test-workflow basic

# Test help system
/clautorun tmux-test-workflow help

# Test specific command
/clautorun tmux-test-workflow npm test
```

### Test CLI Applications
```bash
# Test Claude CLI commands
/clautorun tmux-test-workflow claude --test-categories basic

# Test your own CLI tool
/clautorun tmux-test-workflow mytool --help
```

## Available Test Types

### `basic` - Basic Functionality Testing
```bash
/clautorun tmux-test-workflow basic
```

Tests:
- Command discovery and availability
- Basic command execution
- Help system functionality
- Error handling

### `help` - Help System Testing
```bash
/clautorun tmux-test-workflow help
```

Tests:
- Help commands work correctly
- Documentation is accessible
- Help text is clear and useful

### `integration` - Integration Testing
```bash
/clautorun tmux-test-workflow integration
```

Tests:
- Plugin compatibility
- External system integration
- Environment interaction

### `performance` - Performance Testing
```bash
/clautorun tmux-test-workflow performance
```

Tests:
- Command response times
- Resource usage
- Performance under load

### Custom Command Testing
```bash
/clautorun tmux-test-workflow <command>
```

Examples:
```bash
/clautorun tmux-test-workflow "git status"
/clautorun tmux-test-workflow "npm test"
/clautorun tmux-test-workflow "python --version"
```

## Test Execution

### What Happens During Testing

1. **Safe Session Creation**: Creates isolated tmux session for testing
2. **Command Execution**: Runs tests in complete isolation
3. **Result Collection**: Captures output and verifies results
4. **Health Monitoring**: Ensures tests don't get stuck or consume resources
5. **Automatic Cleanup**: Removes test sessions when complete

### Test Results

Tests provide clear, actionable feedback:

#### ✅ Success Example
```
✅ Basic functionality test PASSED
   Commands tested: help, version, status
   All commands executed successfully
   Response time: < 1 second average
```

#### ❌ Failure Example
```
❌ Help system test FAILED
   Issue: 'claude --help' returned non-zero exit code
   Error: "command not found"
   Suggestion: Check if claude CLI is installed and in PATH
```

## Safety Features (Hard to Use Incorrectly)

### ✅ Always Safe
- **Isolated testing**: Tests run in separate tmux sessions
- **Resource limits**: Tests are monitored and stopped if they consume too much
- **Timeout protection**: Tests automatically stop after reasonable time
- **No interference**: Tests never affect your current session or files

### 🛡️ Built-in Protections
- **No destructive operations**: Won't modify files or systems without permission
- **Safe commands**: Only runs safe, non-destructive test commands
- **Clear warnings**: Shows exactly what will be tested
- **Easy cancellation**: Can stop tests at any time

### ⚠️ What We Prevent
- Tests affecting your current work
- Commands that could damage your system
- Tests that run forever or consume excessive resources
- Complex test configurations that could cause issues

## Test Categories Explained

### Basic Tests
- **What**: Core functionality and command availability
- **When**: Use for quick health checks
- **Why**: Ensures basic system is working

### Help Tests
- **What**: Help system and documentation
- **When**: Use to verify help is accessible
- **Why**: Ensures users can get help when needed

### Integration Tests
- **What**: Plugin and external system compatibility
- **When**: Use after making changes
- **Why**: Ensures everything works together

### Performance Tests
- **What**: Response times and resource usage
- **When**: Use when performance matters
- **Why**: Ensures acceptable performance

## Example Workflows

### Quick Health Check
```bash
# Run basic tests to ensure system is working
/clautorun tmux-test-workflow basic
```

### Test New Installation
```bash
# Test Claude CLI after installation
/clautorun tmux-test-workflow claude --test-categories basic,help
```

### Test Your Application
```bash
# Test your CLI application
/clautorun tmux-test-workflow "myapp --version"
/clautorun tmux-test-workflow "myapp --help"
```

### Comprehensive Testing
```bash
# Run all test types
/clautorun tmux-test-workflow --test-categories basic,help,integration,performance
```

## Error Handling

### Common Issues and Solutions

#### Command Not Found
```
❌ Test FAILED: command 'mytool' not found
   ✅ Solution: Install mytool or check PATH
```

#### Permission Denied
```
❌ Test FAILED: permission denied
   ✅ Solution: Check file permissions or run with appropriate user
```

#### Timeout
```
❌ Test FAILED: command timed out (30s)
   ✅ Solution: Command may be stuck, check system status
```

### Recovery Options

```bash
# Check test status
/clautorun tmux-test-workflow --status

# Clean up stuck tests
/clautorun tmux-test-workflow --cleanup
```

This focused testing system provides **simple, safe, and reliable** CLI testing that's **easy to use correctly and hard to use incorrectly**.

## Workflow Process

### 1. Initial Environment Setup

**Task**: Prepare isolated testing environment

1. Create new tmux session with unique identifier
2. Verify session responsiveness and proper configuration
3. Set up test data directories and configuration files
4. Establish baseline performance metrics
5. Configure logging and monitoring systems

### 2. Test Discovery and Planning

**Task**: Analyze target and create test plan

1. Discover available commands, options, and features
2. Analyze help documentation and man pages
3. Identify test scenarios based on CLI capabilities
4. Create comprehensive test matrix covering all aspects
5. Prioritize tests based on risk and criticality

### 3. Systematic Test Execution

**Task**: Execute tests with comprehensive coverage

1. **Basic Functionality Testing**
   - Test all core commands and options
   - Verify help system accessibility and accuracy
   - Test configuration management and persistence
   - Validate basic error handling

2. **Integration Testing**
   - Test plugin installation and removal
   - Verify marketplace integration functionality
   - Test external system interactions
   - Validate compatibility with different environments

3. **Performance Testing**
   - Measure command execution times
   - Monitor resource usage patterns
   - Test performance under load conditions
   - Identify bottlenecks and optimization opportunities

4. **Error Condition Testing**
   - Test invalid commands and arguments
   - Verify error message clarity and helpfulness
   - Test recovery mechanisms and graceful degradation
   - Validate permission and security handling

### 4. Real-time Monitoring and Recovery

**Task**: Monitor test execution and handle issues

1. Track test progress and completion rates
2. Detect hanging tests and implement timeout recovery
3. Monitor resource usage and prevent system overload
4. Capture detailed logs for failed tests
5. Implement automatic retry mechanisms for transient failures

### 5. Result Analysis and Reporting

**Task**: Generate comprehensive test report

1. Aggregate test results from all categories
2. Calculate success rates and identify failure patterns
3. Analyze performance metrics and compare to baselines
4. Identify critical issues and improvement opportunities
5. Generate actionable recommendations

## Implementation Details

### Session Management

The workflow uses centralized tmux utilities for reliable session management:

```python
from clautorun.tmux_utils import get_tmux_utilities
import time
import uuid

def setup_test_session(session_name=None):
    """Create and configure test session"""
    if not session_name:
        session_name = f"test-workflow-{int(time.time())}"
    tmux = get_tmux_utilities(session_name)

    # Ensure session exists and is responsive
    if not tmux.ensure_session_exists():
        raise Exception("Failed to create test session")

    # Configure session for testing
    if not tmux.send_keys("export TEST_MODE=1"):
        raise Exception("Failed to configure test environment")
    if not tmux.send_keys("export TEST_SESSION_ID=$(uuidgen)"):
        raise Exception("Failed to set test session ID")

    return tmux
```

### Automated Test Execution

Tests are executed using systematic patterns with proper error handling:

```python
def execute_test_sequence(tmux, test_commands):
    """Execute sequence of tests with monitoring"""
    results = []

    for test in test_commands:
        start_time = time.time()

        # Send test command
        tmux.send_keys(test['command'])

        # Wait for completion with timeout
        if wait_for_command_completion(tmux, timeout=test.get('timeout', 30)):
            # Capture and analyze results
            output = tmux.capture_current_input()
            success = analyze_test_output(output, test.get('expected_patterns'))

            results.append({
                'test': test['name'],
                'command': test['command'],
                'success': success,
                'execution_time': time.time() - start_time,
                'output': output
            })
        else:
            # Handle timeout
            results.append({
                'test': test['name'],
                'command': test['command'],
                'success': False,
                'error': 'Timeout exceeded',
                'execution_time': time.time() - start_time
            })

    return results
```

### Integration with AI-Monitor

For extended testing sessions, the workflow integrates with ai-monitor:

```python
def start_extended_testing(session_id, target_cli, duration_minutes=30):
    """Start AI-monitored extended testing"""
    from clautorun.ai_monitor import start_monitor

    prompt = f"""
    Execute comprehensive CLI testing for {target_cli} over {duration_minutes} minutes:

    1. Systematically test all discovered commands and options
    2. Verify error handling and edge cases thoroughly
    3. Document any unexpected behavior, bugs, or usability issues
    4. Test performance characteristics and resource usage
    5. Validate help documentation accuracy and completeness
    6. Test integration with external systems and plugins
    7. Generate detailed findings and recommendations

    Focus on: reliability, usability, performance, and comprehensive coverage.
    Use structured testing methodology and document all findings.
    """

    return start_monitor(
        session_id,
        prompt=prompt,
        stop_marker="COMPREHENSIVE_TESTING_COMPLETED",
        max_cycles=20
    )
```

## Error Handling and Recovery

### Session Recovery

```python
def recover_from_session_failure(session_name):
    """Recover from session failures"""
    tmux = get_tmux_utilities(session_name)

    # Attempt basic recovery
    recovery_actions = [
        # Send interrupt to clear hanging commands
        lambda: tmux.send_keys('C-c') and time.sleep(1),

        # Clear terminal and reset state
        lambda: tmux.send_keys('C-l') and time.sleep(1),

        # Recreate session if needed
        lambda: tmux.ensure_session_exists(session_name)
    ]

    for action in recovery_actions:
        try:
            if action():
                # Verify session is responsive
                test_result = tmux.execute_tmux_command(['echo', 'recovery-test'])
                if test_result and test_result['returncode'] == 0:
                    return True
        except Exception:
            continue

    return False
```

### Test Failure Recovery

```python
def handle_test_failure(test_info, failure_details):
    """Handle individual test failures with intelligent recovery"""

    recovery_strategies = [
        # Retry with different timeout
        lambda: retry_test_with_adjusted_timeout(test_info),

        # Test in clean session
        lambda: execute_test_in_isolated_session(test_info),

        # Skip problematic test and continue
        lambda: mark_test_skipped_and_continue(test_info)
    ]

    for strategy in recovery_strategies:
        try:
            result = strategy()
            if result.get('recovered'):
                return result
        except Exception:
            continue

    return {'recovered': False, 'action': 'manual_intervention_required'}
```

## Report Generation

### Comprehensive Test Report

```python
def generate_workflow_report(test_results, session_info, performance_data):
    """Generate comprehensive test workflow report"""

    return {
        'execution_summary': {
            'session_id': session_info['session_id'],
            'start_time': session_info['start_time'],
            'end_time': session_info['end_time'],
            'total_duration': session_info['end_time'] - session_info['start_time'],
            'total_tests': sum(len(cat.get('tests', [])) for cat in test_results.values()),
            'success_rate': calculate_overall_success_rate(test_results)
        },
        'test_categories': test_results,
        'performance_analysis': performance_data,
        'critical_issues': identify_critical_issues(test_results),
        'recommendations': generate_actionable_recommendations(test_results),
        'next_steps': plan_next_steps(test_results)
    }
```

## Best Practices

### Test Organization

1. **Isolated Environments**: Each test category runs in separate sessions
2. **Clean State**: Ensure clean environments between test runs
3. **Comprehensive Coverage**: Test all aspects of CLI functionality
4. **Systematic Approach**: Follow consistent testing patterns
5. **Detailed Logging**: Capture all test execution details

### Performance Considerations

1. **Resource Monitoring**: Track CPU, memory, and disk usage
2. **Timeout Management**: Set appropriate timeouts for different test types
3. **Parallel Execution**: Use parallel sessions when safe and beneficial
4. **Baseline Comparison**: Compare results against known good baselines

### Error Handling

1. **Graceful Degradation**: Continue testing even if some tests fail
2. **Recovery Mechanisms**: Implement automatic recovery for common issues
3. **Detailed Error Reporting**: Capture sufficient context for debugging
4. **Manual Intervention**: Know when to require human intervention

## Example Usage

### Basic CLI Testing

```bash
# Test claude CLI with default settings
/clautorun tmux-test-workflow claude

# Test with custom configuration
/clautorun tmux-test-workflow claude --test-categories basic,integration,performance --duration-minutes 60
```

### Plugin Testing

```bash
# Test clautorun plugin comprehensively
/clautorun tmux-test-workflow clautorun --test-categories integration,error-handling,regression

# Test with parallel sessions for faster execution
/clautorun tmux-test-workflow claude --parallel-sessions 3 --report-format json
```

### Performance Analysis

```bash
# Focus on performance testing with detailed analysis
/clautorun tmux-test-workflow claude --test-categories performance --duration-minutes 120
```

## Output

The workflow generates detailed reports including:

- **Executive Summary**: High-level overview of test results
- **Detailed Test Results**: Complete breakdown by category
- **Performance Analysis**: Resource usage and timing metrics
- **Issue Identification**: Critical problems and improvement areas
- **Actionable Recommendations**: Specific steps for improvement
- **Next Steps**: Follow-up actions and monitoring recommendations

Reports are saved in `./test-reports/` with timestamps and can be output in JSON, Markdown, or plain text formats for integration with CI/CD pipelines and documentation systems.