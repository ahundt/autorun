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
#   - claude CLI available
#   - plan-export plugin installed
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
TIMEOUT_SHORT=5
TIMEOUT_MEDIUM=15
TIMEOUT_LONG=30

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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

cleanup() {
    if [[ "$CLEANUP" == "true" ]]; then
        print_step "Cleaning up..."
        tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true
        rm -rf "$TEST_DIR"
        print_success "Cleanup complete"
    else
        print_info "Skipping cleanup (--no-cleanup flag set)"
        print_info "Test directory: $TEST_DIR"
        print_info "Tmux session: $TMUX_SESSION"
        print_info "To cleanup manually:"
        print_info "  tmux kill-session -t $TMUX_SESSION"
        print_info "  rm -rf $TEST_DIR"
    fi
}

trap cleanup EXIT

wait_for_claude() {
    local max_wait=$1
    local waited=0
    while [[ $waited -lt $max_wait ]]; do
        local output
        output=$(tmux capture-pane -t "$TMUX_SESSION" -p 2>/dev/null || echo "")
        if echo "$output" | grep -q "Claude Code v"; then
            return 0
        fi
        sleep 1
        ((waited++))
    done
    return 1
}

wait_for_plan_prompt() {
    local max_wait=$1
    local waited=0
    while [[ $waited -lt $max_wait ]]; do
        local output
        output=$(tmux capture-pane -t "$TMUX_SESSION" -p 2>/dev/null || echo "")
        if echo "$output" | grep -q "Would you like to proceed"; then
            return 0
        fi
        sleep 1
        ((waited++))
    done
    return 1
}

wait_for_execution() {
    local max_wait=$1
    local waited=0
    while [[ $waited -lt $max_wait ]]; do
        local output
        output=$(tmux capture-pane -t "$TMUX_SESSION" -p 2>/dev/null || echo "")
        if echo "$output" | grep -qE "(Complete|successfully|verified)"; then
            return 0
        fi
        sleep 1
        ((waited++))
    done
    return 1
}

send_keys() {
    tmux send-keys -t "$TMUX_SESSION" "$1"
    sleep 0.5
}

send_enter() {
    tmux send-keys -t "$TMUX_SESSION" C-m
    sleep 0.5
}

get_notes_count() {
    find "$TEST_DIR/notes" -name "*.md" -type f 2>/dev/null | wc -l | tr -d ' '
}

get_latest_note() {
    find "$TEST_DIR/notes" -name "*.md" -type f -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-
}

check_debug_log_entry() {
    local pattern=$1
    local since_time=$2
    if [[ -f "$DEBUG_LOG" ]]; then
        # Check for entries after the given timestamp
        grep -A5 "Timestamp: $since_time" "$DEBUG_LOG" 2>/dev/null | grep -q "$pattern"
        return $?
    fi
    return 1
}

# =============================================================================
# Usage
# =============================================================================

show_usage() {
    cat << EOF
Plan Export Hook Test Script

Tests the plan-export PostToolUse hook behavior with different accept options.

USAGE:
    $(basename "$0") [OPTIONS]

OPTIONS:
    --no-cleanup    Keep test artifacts after completion for inspection
    --help, -h      Show this help message

EXAMPLES:
    $(basename "$0")                 # Run tests with automatic cleanup
    $(basename "$0") --no-cleanup    # Keep test files for debugging

WHAT THIS TESTS:
    1. Option 1 (fresh context) - Known to NOT fire PostToolUse hooks
    2. Option 2 (regular accept) - Should fire PostToolUse hooks correctly

EXPECTED RESULTS:
    - Test 1 (fresh context): Plan NOT exported (bug)
    - Test 2 (regular accept): Plan exported (correct behavior)

EOF
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

# =============================================================================
# Setup Test Environment
# =============================================================================

print_header "Setting Up Test Environment"

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
# Test 1: Fresh Context Accept (Option 1) - Expected to FAIL
# =============================================================================

print_header "Test 1: Fresh Context Accept (Option 1)"
print_info "This tests the BUGGY path - PostToolUse hooks should NOT fire"

print_step "Starting Claude with haiku model..."
send_keys "claude --model haiku"
send_enter
sleep 2

# Trust the folder
print_step "Trusting folder..."
send_enter
sleep 3

if ! wait_for_claude $TIMEOUT_MEDIUM; then
    print_failure "Claude failed to start"
    exit 1
fi
print_success "Claude started"

print_step "Entering plan mode..."
send_keys "/plan"
send_enter
sleep 2
print_success "Plan mode enabled"

print_step "Creating test plan..."
send_keys "create file test1.txt with content 'test 1'"
send_enter

if ! wait_for_plan_prompt $TIMEOUT_LONG; then
    print_failure "Plan was not created"
    exit 1
fi
print_success "Plan created"

NOTES_BEFORE=$(get_notes_count)
print_info "Notes count before accept: $NOTES_BEFORE"

print_step "Accepting with Option 1 (fresh context)..."
send_keys "1"
send_enter

if ! wait_for_execution $TIMEOUT_LONG; then
    print_failure "Plan execution failed"
    exit 1
fi
print_success "Plan executed"

sleep 3  # Give hooks time to run (if they do)

NOTES_AFTER_TEST1=$(get_notes_count)
print_info "Notes count after accept: $NOTES_AFTER_TEST1"

if [[ "$NOTES_AFTER_TEST1" -gt "$NOTES_BEFORE" ]]; then
    print_success "Test 1 Result: Plan WAS exported (unexpected - bug may be fixed!)"
    TEST1_RESULT="EXPORTED"
else
    print_failure "Test 1 Result: Plan NOT exported (confirms bug)"
    TEST1_RESULT="NOT_EXPORTED"
fi

# =============================================================================
# Test 2: Regular Accept (Option 2) - Expected to SUCCEED
# =============================================================================

print_header "Test 2: Regular Accept (Option 2)"
print_info "This tests the CORRECT path - PostToolUse hooks SHOULD fire"

print_step "Entering plan mode again..."
send_keys "/plan"
send_enter
sleep 2
print_success "Plan mode enabled"

print_step "Creating second test plan..."
send_keys "create file test2.txt with content 'test 2'"
send_enter

if ! wait_for_plan_prompt $TIMEOUT_LONG; then
    print_failure "Plan was not created"
    exit 1
fi
print_success "Plan created"

NOTES_BEFORE_TEST2=$(get_notes_count)
print_info "Notes count before accept: $NOTES_BEFORE_TEST2"

print_step "Accepting with Option 2 (regular accept)..."
send_keys "2"
send_enter

if ! wait_for_execution $TIMEOUT_LONG; then
    print_failure "Plan execution failed"
    exit 1
fi
print_success "Plan executed"

sleep 3  # Give hooks time to run

NOTES_AFTER_TEST2=$(get_notes_count)
print_info "Notes count after accept: $NOTES_AFTER_TEST2"

if [[ "$NOTES_AFTER_TEST2" -gt "$NOTES_BEFORE_TEST2" ]]; then
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

# =============================================================================
# Final Report
# =============================================================================

print_header "Test Results Summary"

echo -e "┌─────────────────────────────────────────────────────────────────┐"
echo -e "│ Test                          │ Expected      │ Actual         │"
echo -e "├─────────────────────────────────────────────────────────────────┤"

if [[ "$TEST1_RESULT" == "NOT_EXPORTED" ]]; then
    echo -e "│ Test 1 (fresh context)        │ NOT_EXPORTED  │ ${RED}NOT_EXPORTED${NC}   │"
else
    echo -e "│ Test 1 (fresh context)        │ NOT_EXPORTED  │ ${GREEN}EXPORTED${NC}       │"
fi

if [[ "$TEST2_RESULT" == "EXPORTED" ]]; then
    echo -e "│ Test 2 (regular accept)       │ EXPORTED      │ ${GREEN}EXPORTED${NC}       │"
else
    echo -e "│ Test 2 (regular accept)       │ EXPORTED      │ ${RED}NOT_EXPORTED${NC}   │"
fi

echo -e "└─────────────────────────────────────────────────────────────────┘"
echo ""

# Determine overall status
if [[ "$TEST1_RESULT" == "NOT_EXPORTED" && "$TEST2_RESULT" == "EXPORTED" ]]; then
    print_info "Bug Status: CONFIRMED - Fresh context does not fire PostToolUse hooks"
    print_info "Workaround: Use Option 2 or 3 when plan export is needed"
    EXIT_CODE=0
elif [[ "$TEST1_RESULT" == "EXPORTED" && "$TEST2_RESULT" == "EXPORTED" ]]; then
    print_success "Bug Status: FIXED - Both paths now export correctly!"
    EXIT_CODE=0
else
    print_failure "Bug Status: REGRESSION - Option 2 also failing"
    EXIT_CODE=1
fi

# Show test artifacts location if not cleaning up
if [[ "$CLEANUP" == "false" ]]; then
    echo ""
    print_info "Test artifacts preserved:"
    print_info "  Test directory: $TEST_DIR"
    print_info "  Notes folder: $TEST_DIR/notes/"
    print_info "  Tmux session: $TMUX_SESSION"
    print_info "  Debug log: $DEBUG_LOG"
    echo ""
    print_info "Commands to inspect:"
    print_info "  ls -la $TEST_DIR/notes/"
    print_info "  tmux attach -t $TMUX_SESSION"
    print_info "  tail -50 $DEBUG_LOG"
fi

exit $EXIT_CODE
