#!/usr/bin/env bash
# =============================================================================
# Plan Export Hook Test Script
# =============================================================================
# Tests the plan-export PostToolUse hook behavior with different accept options.
#
# Known Bug: Claude Code's "fresh context" option (button 1) does NOT fire
# PostToolUse hooks for ExitPlanMode. This script verifies this behavior.
#
# Usage:
#   ./test_plan_export_hooks.sh              # Run tests with cleanup
#   ./test_plan_export_hooks.sh --no-cleanup # Keep test artifacts for inspection
#   ./test_plan_export_hooks.sh --help       # Show usage
#
# Requirements:
#   - tmux installed
#   - claude CLI available (uses haiku model to minimize costs)
#   - plan-export plugin installed
#
# API Costs:
#   Uses haiku model (~$0.01-0.05 per test run)
# =============================================================================

set -euo pipefail

# =============================================================================
# Configuration
# =============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_DIR="/tmp/plan-export-test-$$"
TMUX_SESSION="plan-export-test-$$"
CLEANUP=true
DEBUG_LOG="$HOME/.claude/plan-export-debug.log"
CONFIG_FILE="$HOME/.claude/plan-export.config.json"
CONFIG_BACKUP="$HOME/.claude/plan-export.config.json.bak"

# Timeouts (in seconds)
TIMEOUT_CLAUDE_START=30
TIMEOUT_PLAN_CREATE=90  # Plan creation can take time with haiku
TIMEOUT_PLAN_EXECUTE=90
SLEEP_AFTER_KEYPRESS=0.5

# Model to use (haiku is cheapest)
CLAUDE_MODEL="haiku"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Test results
TEST1_RESULT=""
TEST2_RESULT=""
TEST1_NOTES_BEFORE=0
TEST1_NOTES_AFTER=0
TEST2_NOTES_BEFORE=0
TEST2_NOTES_AFTER=0

# =============================================================================
# Helper Functions
# =============================================================================

print_header() {
    echo -e "\n${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}\n"
}

print_step() {
    echo -e "${YELLOW}▶ $1${NC}"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_failure() {
    echo -e "${RED}✗ $1${NC}"
}

print_info() {
    echo -e "  $1"
}

print_debug() {
    if [[ "${DEBUG:-false}" == "true" ]]; then
        echo -e "${CYAN}[DEBUG] $1${NC}"
    fi
}

enable_debug_logging() {
    print_step "Enabling debug logging for plan-export..."

    # Backup existing config if present
    if [[ -f "$CONFIG_FILE" ]]; then
        cp "$CONFIG_FILE" "$CONFIG_BACKUP"
        print_info "Backed up existing config to $CONFIG_BACKUP"
    fi

    # Clear old debug log
    : > "$DEBUG_LOG"

    # Create config with debug_logging enabled
    cat > "$CONFIG_FILE" << 'EOF'
{
    "enabled": true,
    "output_plan_dir": "notes",
    "filename_pattern": "{datetime}_{name}",
    "extension": ".md",
    "export_rejected": true,
    "output_rejected_plan_dir": "notes/rejected",
    "debug_logging": true,
    "notify_claude": true
}
EOF
    print_success "Debug logging enabled"
}

restore_config() {
    if [[ -f "$CONFIG_BACKUP" ]]; then
        mv "$CONFIG_BACKUP" "$CONFIG_FILE"
        print_info "Restored original config"
    else
        # Remove test config if no backup exists
        rm -f "$CONFIG_FILE"
    fi
}

show_debug_log() {
    print_header "Debug Log Contents"
    if [[ -f "$DEBUG_LOG" && -s "$DEBUG_LOG" ]]; then
        cat "$DEBUG_LOG"
    else
        print_info "Debug log is empty or does not exist"
        print_info "(This is EXPECTED for Test 1 - fresh context doesn't fire hooks)"
    fi
}

cleanup() {
    # Show debug log before cleanup
    show_debug_log

    # Restore config
    restore_config

    if [[ "$CLEANUP" == "true" ]]; then
        print_step "Cleaning up..."
        tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true
        rm -rf "$TEST_DIR"
        print_success "Cleanup complete"
    else
        print_info "Skipping cleanup (--no-cleanup flag set)"
        print_info "Test directory: $TEST_DIR"
        print_info "Tmux session: $TMUX_SESSION"
        print_info "Debug log: $DEBUG_LOG"
        print_info "To cleanup manually:"
        print_info "  tmux kill-session -t $TMUX_SESSION"
        print_info "  rm -rf $TEST_DIR"
    fi
}

trap cleanup EXIT

# Wait for Claude to be ready for input
# Must detect when Claude shows its input prompt, not just the startup banner
wait_for_claude() {
    local max_wait=$1
    local waited=0
    while [[ $waited -lt $max_wait ]]; do
        local output
        output=$(tmux capture-pane -t "$TMUX_SESSION" -p -S -50 2>/dev/null || echo "")
        # Look for indicators that Claude is ready for input:
        # - "accept edits" in status bar means Claude is at prompt
        # - "Message:" or similar input indicator
        # - tokens counter at bottom right
        if echo "$output" | grep -qE "(accept edits|tokens$|shift\+tab)"; then
            sleep 1  # Extra buffer to ensure fully ready
            return 0
        fi
        sleep 1
        ((waited++))
    done
    return 1
}

# Wait for plan acceptance dialog to appear
wait_for_plan_prompt() {
    local max_wait=$1
    local waited=0
    while [[ $waited -lt $max_wait ]]; do
        local output
        output=$(tmux capture-pane -t "$TMUX_SESSION" -p -S -100 2>/dev/null || echo "")
        # Look for plan acceptance dialog indicators:
        # - "ready to execute" appears before the options
        # - The numbered options (1. Yes, clear context...)
        if echo "$output" | grep -qE "(ready to execute|Yes, clear context|Yes, auto-accept)"; then
            return 0
        fi
        sleep 2
        ((waited += 2))
    done
    # Show debug output on timeout
    print_info "DEBUG: Timeout waiting for plan prompt. Last 30 lines:"
    echo "$output" | tail -30
    return 1
}

# Wait for plan execution to complete
wait_for_execution() {
    local max_wait=$1
    local waited=0
    while [[ $waited -lt $max_wait ]]; do
        local output
        output=$(tmux capture-pane -t "$TMUX_SESSION" -p -S -100 2>/dev/null || echo "")
        # Look for execution completion indicators:
        # - "successfully implemented" - Claude's typical completion message
        # - "accept edits" in status bar - returned to normal prompt
        # - Summary with checkmarks
        if echo "$output" | grep -qiE "(successfully|implemented|Summary:|accept edits|Done!|Created.*txt)"; then
            return 0
        fi
        sleep 2
        ((waited += 2))
    done
    print_info "DEBUG: Timeout waiting for execution. Last 30 lines:"
    echo "$output" | tail -30
    return 1
}

# Send text to tmux (WITHOUT pressing Enter)
send_keys() {
    tmux send-keys -t "$TMUX_SESSION" "$1"
    sleep "$SLEEP_AFTER_KEYPRESS"
}

# Send Enter key (C-m) - MUST be sent separately from text
send_enter() {
    tmux send-keys -t "$TMUX_SESSION" C-m
    sleep "$SLEEP_AFTER_KEYPRESS"
}

# Get count of markdown files in notes directory
get_notes_count() {
    find "$TEST_DIR/notes" -name "*.md" -type f 2>/dev/null | wc -l | tr -d ' '
}

# Get path to most recent note file (macOS compatible)
get_latest_note() {
    # Use ls with time sort instead of find -printf (which is Linux-only)
    ls -t "$TEST_DIR/notes"/*.md 2>/dev/null | head -1
}

# =============================================================================
# Usage
# =============================================================================

show_usage() {
    cat << EOF
Plan Export Hook Test Script

Tests the plan-export PostToolUse hook behavior with different accept options.
Uses the ${CLAUDE_MODEL} model to minimize API costs (~\$0.01-0.05 per run).

USAGE:
    $(basename "$0") [OPTIONS]

OPTIONS:
    --no-cleanup    Keep test artifacts after completion for inspection
    --debug         Enable verbose debug output
    --help, -h      Show this help message

EXAMPLES:
    $(basename "$0")                 # Run tests with automatic cleanup
    $(basename "$0") --no-cleanup    # Keep test files for debugging

WHAT THIS TESTS:
    Test 1: Option 1 (fresh context) - Known to NOT fire PostToolUse hooks
    Test 2: Option 2 (regular accept) - Should fire PostToolUse hooks correctly

EXPECTED RESULTS:
    - Test 1 (fresh context): Plan NOT exported (confirms bug exists)
    - Test 2 (regular accept): Plan exported (correct behavior)

If both tests pass (Test 1 NOT exported, Test 2 EXPORTED), the bug is confirmed.

EOF
}

# =============================================================================
# Print Final Summary Report
# =============================================================================

print_summary() {
    print_header "TEST SUMMARY REPORT"

    echo -e "┌─────────────────────────────────────────────────────────────────────┐"
    echo -e "│                    PLAN EXPORT HOOK TEST RESULTS                    │"
    echo -e "├─────────────────────────────────────────────────────────────────────┤"
    echo -e "│ Model Used: ${CYAN}${CLAUDE_MODEL}${NC}                                                   │"
    echo -e "│ Test Directory: ${TEST_DIR}                         │"
    echo -e "├─────────────────────────────────────────────────────────────────────┤"
    echo -e "│                                                                     │"
    echo -e "│  TEST 1: Fresh Context Accept (Option 1)                            │"
    echo -e "│  ─────────────────────────────────────────                          │"
    echo -e "│    Notes before: ${TEST1_NOTES_BEFORE}                                                   │"
    echo -e "│    Notes after:  ${TEST1_NOTES_AFTER}                                                   │"

    if [[ "$TEST1_RESULT" == "NOT_EXPORTED" ]]; then
        echo -e "│    Expected:     NOT_EXPORTED                                       │"
        echo -e "│    Actual:       ${GREEN}NOT_EXPORTED${NC} ✓                                        │"
        echo -e "│    Status:       ${GREEN}PASS${NC} (bug confirmed - hook did NOT fire)              │"
    else
        echo -e "│    Expected:     NOT_EXPORTED                                       │"
        echo -e "│    Actual:       ${YELLOW}EXPORTED${NC}                                              │"
        echo -e "│    Status:       ${YELLOW}UNEXPECTED${NC} (bug may be fixed!)                       │"
    fi

    echo -e "│                                                                     │"
    echo -e "│  TEST 2: Regular Accept (Option 2)                                  │"
    echo -e "│  ─────────────────────────────────────────                          │"
    echo -e "│    Notes before: ${TEST2_NOTES_BEFORE}                                                   │"
    echo -e "│    Notes after:  ${TEST2_NOTES_AFTER}                                                   │"

    if [[ "$TEST2_RESULT" == "EXPORTED" ]]; then
        echo -e "│    Expected:     EXPORTED                                           │"
        echo -e "│    Actual:       ${GREEN}EXPORTED${NC} ✓                                             │"
        echo -e "│    Status:       ${GREEN}PASS${NC} (hook fired correctly)                           │"
    elif [[ "$TEST2_RESULT" == "NOT_EXPORTED" ]]; then
        echo -e "│    Expected:     EXPORTED                                           │"
        echo -e "│    Actual:       ${RED}NOT_EXPORTED${NC}                                            │"
        echo -e "│    Status:       ${RED}FAIL${NC} (regression - hook should fire)                   │"
    else
        echo -e "│    Expected:     EXPORTED                                           │"
        echo -e "│    Actual:       ${YELLOW}SKIPPED${NC}                                               │"
        echo -e "│    Status:       ${YELLOW}SKIPPED${NC} (test did not complete)                      │"
    fi

    echo -e "│                                                                     │"
    echo -e "├─────────────────────────────────────────────────────────────────────┤"

    # Overall verdict
    if [[ "$TEST1_RESULT" == "NOT_EXPORTED" && "$TEST2_RESULT" == "EXPORTED" ]]; then
        echo -e "│  OVERALL: ${GREEN}SUCCESS${NC}                                                      │"
        echo -e "│  Bug Status: CONFIRMED - Fresh context bypasses PostToolUse hooks   │"
        echo -e "│  Workaround: SessionStart handler catches unexported plans          │"
    elif [[ "$TEST1_RESULT" == "EXPORTED" && "$TEST2_RESULT" == "EXPORTED" ]]; then
        echo -e "│  OVERALL: ${GREEN}BUG FIXED!${NC}                                                    │"
        echo -e "│  Both paths now correctly export plans.                             │"
    elif [[ "$TEST2_RESULT" == "NOT_EXPORTED" ]]; then
        echo -e "│  OVERALL: ${RED}REGRESSION${NC}                                                     │"
        echo -e "│  Option 2 should export but didn't. Check plugin installation.      │"
    else
        echo -e "│  OVERALL: ${YELLOW}INCOMPLETE${NC}                                                   │"
        echo -e "│  One or more tests did not complete successfully.                   │"
    fi

    echo -e "└─────────────────────────────────────────────────────────────────────┘"
    echo ""
}

# =============================================================================
# Parse Arguments
# =============================================================================

while [[ $# -gt 0 ]]; do
    case $1 in
        --no-cleanup)
            CLEANUP=false
            shift
            ;;
        --debug)
            DEBUG=true
            shift
            ;;
        --help|-h)
            show_usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

# =============================================================================
# Pre-flight Checks
# =============================================================================

print_header "Pre-flight Checks"

print_step "Checking requirements..."

if ! command -v tmux &>/dev/null; then
    print_failure "tmux not found"
    exit 1
fi
print_success "tmux found"

if ! command -v claude &>/dev/null; then
    print_failure "claude CLI not found"
    exit 1
fi
print_success "claude CLI found"

# Check plan-export plugin
if ! claude plugin list 2>/dev/null | grep -q "plan-export"; then
    print_failure "plan-export plugin not installed"
    exit 1
fi
print_success "plan-export plugin installed"

print_info "Model: ${CLAUDE_MODEL} (lowest cost)"

# =============================================================================
# Setup Test Environment
# =============================================================================

print_header "Setting Up Test Environment"

# Enable debug logging first
enable_debug_logging

print_step "Creating test directory: $TEST_DIR"
mkdir -p "$TEST_DIR/notes"
echo "# Plan Export Test Project" > "$TEST_DIR/README.md"
print_success "Test directory created"

print_step "Recording start time for log filtering..."
START_TIME=$(date "+%Y-%m-%d %H:%M")
print_info "Start time: $START_TIME"

print_step "Creating tmux session: $TMUX_SESSION"
tmux new-session -d -s "$TMUX_SESSION" -c "$TEST_DIR"
print_success "Tmux session created"

# =============================================================================
# Test 1: Fresh Context Accept (Option 1) - Expected to NOT export
# =============================================================================

print_header "Test 1: Fresh Context Accept (Option 1)"
print_info "This tests the BUGGY path - PostToolUse hooks should NOT fire"
print_info "Expected result: Plan NOT exported (confirms bug)"

print_step "Starting Claude with ${CLAUDE_MODEL} model..."
send_keys "claude --model ${CLAUDE_MODEL}"
send_enter
sleep 2

# Trust the folder if prompted
print_step "Trusting folder if prompted..."
send_enter
sleep 3

if ! wait_for_claude $TIMEOUT_CLAUDE_START; then
    print_failure "Claude failed to start"
    TEST1_RESULT="SKIPPED"
    exit 1
fi
print_success "Claude started"

print_step "Entering plan mode..."
send_keys "/plan"
send_enter
sleep 2
print_success "Plan mode command sent"

print_step "Creating test plan..."
send_keys "create file test1.txt with content 'test 1'"
send_enter

if ! wait_for_plan_prompt $TIMEOUT_PLAN_CREATE; then
    print_failure "Plan was not created (timeout)"
    TEST1_RESULT="SKIPPED"
    # Continue to summary
else
    print_success "Plan created"

    TEST1_NOTES_BEFORE=$(get_notes_count)
    print_info "Notes count before accept: $TEST1_NOTES_BEFORE"

    print_step "Accepting with Option 1 (fresh context)..."
    send_keys "1"
    send_enter

    if ! wait_for_execution $TIMEOUT_PLAN_EXECUTE; then
        print_failure "Plan execution did not complete (timeout)"
        # Check notes anyway
    else
        print_success "Plan executed"
    fi

    sleep 3  # Give hooks time to run (if they do)

    TEST1_NOTES_AFTER=$(get_notes_count)
    print_info "Notes count after accept: $TEST1_NOTES_AFTER"

    if [[ "$TEST1_NOTES_AFTER" -gt "$TEST1_NOTES_BEFORE" ]]; then
        print_success "Test 1 Result: Plan WAS exported (unexpected - bug may be fixed!)"
        TEST1_RESULT="EXPORTED"
    else
        print_failure "Test 1 Result: Plan NOT exported (confirms bug)"
        TEST1_RESULT="NOT_EXPORTED"
    fi
fi

# =============================================================================
# Test 2: Regular Accept (Option 2) - Expected to export
# =============================================================================

print_header "Test 2: Regular Accept (Option 2)"
print_info "This tests the CORRECT path - PostToolUse hooks SHOULD fire"
print_info "Expected result: Plan EXPORTED"

# After Option 1 (fresh context), we need to exit and restart Claude
# because we're in a fresh context that doesn't have plan mode active
print_step "Exiting Claude (fresh context reset)..."
send_keys "/exit"
send_enter
sleep 3  # Wait for Claude to fully exit

print_step "Restarting Claude for Test 2..."
send_keys "claude --model ${CLAUDE_MODEL}"
send_enter
sleep 3  # Wait for Claude to start loading

# Trust again if needed
send_enter
sleep 2

if ! wait_for_claude $TIMEOUT_CLAUDE_START; then
    print_failure "Claude failed to restart"
    TEST2_RESULT="SKIPPED"
else
    print_success "Claude restarted"
    sleep 2  # Extra buffer before sending commands

    print_step "Entering plan mode..."
    send_keys "/plan"
    send_enter
    sleep 2
    print_success "Plan mode command sent"

    print_step "Creating second test plan..."
    send_keys "create file test2.txt with content 'test 2'"
    send_enter

    if ! wait_for_plan_prompt $TIMEOUT_PLAN_CREATE; then
        print_failure "Plan was not created (timeout)"
        TEST2_RESULT="SKIPPED"
    else
        print_success "Plan created"

        TEST2_NOTES_BEFORE=$(get_notes_count)
        print_info "Notes count before accept: $TEST2_NOTES_BEFORE"

        print_step "Accepting with Option 2 (regular accept)..."
        send_keys "2"
        send_enter

        if ! wait_for_execution $TIMEOUT_PLAN_EXECUTE; then
            print_failure "Plan execution did not complete (timeout)"
        else
            print_success "Plan executed"
        fi

        sleep 3  # Give hooks time to run

        TEST2_NOTES_AFTER=$(get_notes_count)
        print_info "Notes count after accept: $TEST2_NOTES_AFTER"

        if [[ "$TEST2_NOTES_AFTER" -gt "$TEST2_NOTES_BEFORE" ]]; then
            print_success "Test 2 Result: Plan WAS exported (correct behavior)"
            TEST2_RESULT="EXPORTED"
            EXPORTED_FILE=$(get_latest_note)
            if [[ -n "$EXPORTED_FILE" ]]; then
                print_info "Exported file: $(basename "$EXPORTED_FILE")"
            fi
        else
            print_failure "Test 2 Result: Plan NOT exported (unexpected failure)"
            TEST2_RESULT="NOT_EXPORTED"
        fi
    fi
fi

# =============================================================================
# Final Summary Report
# =============================================================================

print_summary

# Determine exit code
if [[ "$TEST1_RESULT" == "NOT_EXPORTED" && "$TEST2_RESULT" == "EXPORTED" ]]; then
    # Bug confirmed, workaround works
    exit 0
elif [[ "$TEST1_RESULT" == "EXPORTED" && "$TEST2_RESULT" == "EXPORTED" ]]; then
    # Bug is fixed!
    exit 0
elif [[ "$TEST2_RESULT" == "NOT_EXPORTED" ]]; then
    # Regression
    exit 1
else
    # Incomplete
    exit 1
fi
