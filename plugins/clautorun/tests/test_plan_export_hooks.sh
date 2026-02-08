#!/usr/bin/env bash
# =============================================================================
# Plan Export Hook Test Script
# =============================================================================
# Tests the plan-export plugin's handling of the Claude Code fresh context bug.
#
# Claude Code Bug: Option 1 (fresh context) does NOT fire PostToolUse hooks.
# Our Workaround: SessionStart handler catches unexported plans on next session.
#
# This test verifies:
#   1. The Claude Code bug exists (informational)
#   2. Our SessionStart workaround catches unexported plans (MUST PASS)
#   3. Normal Option 2 path works correctly (MUST PASS)
#
# Usage:
#   ./test_plan_export_hooks.sh              # Run tests with cleanup
#   ./test_plan_export_hooks.sh --no-cleanup # Keep test artifacts for inspection
#   ./test_plan_export_hooks.sh --help       # Show usage
#
# Requirements:
#   - tmux installed
#   - claude CLI available (uses haiku model to minimize costs)
#   - plan-export plugin installed with SessionStart handler
#
# API Costs:
#   Uses haiku model (~$0.02-0.10 per test run)
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
TIMEOUT_PLAN_CREATE=90
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
CLAUDE_BUG_DETECTED=""      # Is the Claude Code bug present?
WORKAROUND_WORKS=""         # Does our SessionStart workaround work?
OPTION2_WORKS=""            # Does normal Option 2 path work?

# Note counts for each phase
PHASE1_NOTES_BEFORE=0
PHASE1_NOTES_AFTER=0
PHASE2_NOTES_BEFORE=0
PHASE2_NOTES_AFTER=0
PHASE3_NOTES_BEFORE=0
PHASE3_NOTES_AFTER=0

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

print_warn() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

enable_debug_logging() {
    print_step "Enabling debug logging for plan-export..."

    if [[ -f "$CONFIG_FILE" ]]; then
        cp "$CONFIG_FILE" "$CONFIG_BACKUP"
        print_info "Backed up existing config to $CONFIG_BACKUP"
    fi

    : > "$DEBUG_LOG"

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
        rm -f "$CONFIG_FILE"
    fi
}

show_debug_log() {
    print_header "Debug Log Contents"
    if [[ -f "$DEBUG_LOG" && -s "$DEBUG_LOG" ]]; then
        cat "$DEBUG_LOG"
    else
        print_info "Debug log is empty or does not exist"
    fi
}

cleanup() {
    show_debug_log
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

wait_for_claude() {
    local max_wait=$1
    local waited=0
    while [[ $waited -lt $max_wait ]]; do
        local output
        output=$(tmux capture-pane -t "$TMUX_SESSION" -p -S -50 2>/dev/null || echo "")
        if echo "$output" | grep -qE "(accept edits|tokens$|shift\+tab)"; then
            sleep 1
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
        output=$(tmux capture-pane -t "$TMUX_SESSION" -p -S -100 2>/dev/null || echo "")
        if echo "$output" | grep -qE "(ready to execute|Yes, clear context|Yes, auto-accept)"; then
            return 0
        fi
        sleep 2
        ((waited += 2))
    done
    print_info "DEBUG: Timeout waiting for plan prompt. Last 30 lines:"
    echo "$output" | tail -30
    return 1
}

wait_for_execution() {
    local max_wait=$1
    local waited=0
    while [[ $waited -lt $max_wait ]]; do
        local output
        output=$(tmux capture-pane -t "$TMUX_SESSION" -p -S -100 2>/dev/null || echo "")
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

send_keys() {
    tmux send-keys -t "$TMUX_SESSION" "$1"
    sleep "$SLEEP_AFTER_KEYPRESS"
}

send_enter() {
    tmux send-keys -t "$TMUX_SESSION" C-m
    sleep "$SLEEP_AFTER_KEYPRESS"
}

get_notes_count() {
    find "$TEST_DIR/notes" -name "*.md" -type f 2>/dev/null | wc -l | tr -d ' '
}

get_latest_note() {
    ls -t "$TEST_DIR/notes"/*.md 2>/dev/null | head -1
}

# =============================================================================
# Usage
# =============================================================================

show_usage() {
    cat << EOF
Plan Export Hook Test Script

Tests the plan-export plugin's SessionStart workaround for the Claude Code
fresh context bug. Uses the ${CLAUDE_MODEL} model (~\$0.02-0.10 per run).

USAGE:
    $(basename "$0") [OPTIONS]

OPTIONS:
    --no-cleanup    Keep test artifacts after completion for inspection
    --help, -h      Show this help message

WHAT THIS TESTS:
    Phase 1: Detect Claude Code bug (Option 1 doesn't fire PostToolUse)
    Phase 2: Verify our SessionStart workaround catches unexported plans
    Phase 3: Verify normal Option 2 path still works

PASS CRITERIA:
    - Phase 2 MUST export (our workaround works)
    - Phase 3 MUST export (baseline functionality)
    - Phase 1 is informational (documents upstream bug status)

EOF
}

# =============================================================================
# Print Final Summary Report
# =============================================================================

print_summary() {
    print_header "TEST SUMMARY REPORT"

    echo -e "┌─────────────────────────────────────────────────────────────────────┐"
    echo -e "│              PLAN EXPORT SESSIONSTART WORKAROUND TEST               │"
    echo -e "├─────────────────────────────────────────────────────────────────────┤"
    echo -e "│ Model: ${CYAN}${CLAUDE_MODEL}${NC}  │  Test Dir: ${TEST_DIR}       │"
    echo -e "├─────────────────────────────────────────────────────────────────────┤"
    echo -e "│                                                                     │"
    echo -e "│  PHASE 1: Claude Code Bug Detection (Option 1 - fresh context)     │"
    echo -e "│  ───────────────────────────────────────────────────────────────    │"
    echo -e "│    Notes before Option 1: ${PHASE1_NOTES_BEFORE}                                            │"
    echo -e "│    Notes after Option 1:  ${PHASE1_NOTES_AFTER}                                            │"

    if [[ "$CLAUDE_BUG_DETECTED" == "YES" ]]; then
        echo -e "│    Claude Code Bug:       ${YELLOW}PRESENT${NC} (PostToolUse not fired)            │"
        echo -e "│    Status:                ${CYAN}INFORMATIONAL${NC} (upstream bug, not our fault) │"
    elif [[ "$CLAUDE_BUG_DETECTED" == "NO" ]]; then
        echo -e "│    Claude Code Bug:       ${GREEN}FIXED${NC} (PostToolUse now fires!)              │"
        echo -e "│    Status:                ${GREEN}GREAT NEWS${NC} - bug may be fixed upstream      │"
    else
        echo -e "│    Claude Code Bug:       ${YELLOW}UNKNOWN${NC} (test incomplete)                  │"
    fi

    echo -e "│                                                                     │"
    echo -e "│  PHASE 2: SessionStart Workaround (new session catches plan)       │"
    echo -e "│  ───────────────────────────────────────────────────────────────    │"
    echo -e "│    Notes before new session: ${PHASE2_NOTES_BEFORE}                                         │"
    echo -e "│    Notes after new session:  ${PHASE2_NOTES_AFTER}                                         │"

    if [[ "$WORKAROUND_WORKS" == "YES" ]]; then
        echo -e "│    SessionStart Handler:     ${GREEN}WORKING${NC} ✓                                 │"
        echo -e "│    Status:                   ${GREEN}PASS${NC}                                      │"
    elif [[ "$WORKAROUND_WORKS" == "NO" ]]; then
        echo -e "│    SessionStart Handler:     ${RED}BROKEN${NC} ✗                                   │"
        echo -e "│    Status:                   ${RED}FAIL${NC} - workaround not working!             │"
    elif [[ "$WORKAROUND_WORKS" == "SKIPPED" ]]; then
        echo -e "│    SessionStart Handler:     ${YELLOW}SKIPPED${NC} (bug not present)                │"
        echo -e "│    Status:                   ${CYAN}N/A${NC}                                       │"
    else
        echo -e "│    SessionStart Handler:     ${YELLOW}UNKNOWN${NC}                                  │"
    fi

    echo -e "│                                                                     │"
    echo -e "│  PHASE 3: Baseline Check (Option 2 - normal accept)                │"
    echo -e "│  ───────────────────────────────────────────────────────────────    │"
    echo -e "│    Notes before Option 2: ${PHASE3_NOTES_BEFORE}                                            │"
    echo -e "│    Notes after Option 2:  ${PHASE3_NOTES_AFTER}                                            │"

    if [[ "$OPTION2_WORKS" == "YES" ]]; then
        echo -e "│    PostToolUse Hook:       ${GREEN}WORKING${NC} ✓                                   │"
        echo -e "│    Status:                 ${GREEN}PASS${NC}                                        │"
    elif [[ "$OPTION2_WORKS" == "NO" ]]; then
        echo -e "│    PostToolUse Hook:       ${RED}BROKEN${NC} ✗                                     │"
        echo -e "│    Status:                 ${RED}FAIL${NC} - basic functionality broken!           │"
    else
        echo -e "│    PostToolUse Hook:       ${YELLOW}UNKNOWN${NC}                                    │"
    fi

    echo -e "│                                                                     │"
    echo -e "├─────────────────────────────────────────────────────────────────────┤"

    # Overall verdict
    local overall_pass=true
    local fail_reasons=""

    if [[ "$WORKAROUND_WORKS" == "NO" ]]; then
        overall_pass=false
        fail_reasons="SessionStart workaround broken"
    fi
    if [[ "$OPTION2_WORKS" == "NO" ]]; then
        overall_pass=false
        if [[ -n "$fail_reasons" ]]; then
            fail_reasons="$fail_reasons, "
        fi
        fail_reasons="${fail_reasons}Option 2 baseline broken"
    fi

    if [[ "$overall_pass" == "true" ]]; then
        if [[ "$CLAUDE_BUG_DETECTED" == "NO" ]]; then
            echo -e "│  OVERALL: ${GREEN}ALL TESTS PASSED${NC}                                            │"
            echo -e "│  Claude Code bug appears FIXED - both paths work!                  │"
        else
            echo -e "│  OVERALL: ${GREEN}WORKAROUND VERIFIED${NC}                                         │"
            echo -e "│  Claude bug present but SessionStart catches unexported plans      │"
        fi
    else
        echo -e "│  OVERALL: ${RED}FAILED${NC}                                                        │"
        echo -e "│  Reason: ${fail_reasons}                                │"
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

enable_debug_logging

print_step "Creating test directory: $TEST_DIR"
mkdir -p "$TEST_DIR/notes"
echo "# Plan Export Test Project" > "$TEST_DIR/README.md"
print_success "Test directory created"

print_step "Creating tmux session: $TMUX_SESSION"
tmux new-session -d -s "$TMUX_SESSION" -c "$TEST_DIR"
print_success "Tmux session created"

# =============================================================================
# PHASE 1: Detect Claude Code Bug (Option 1 - fresh context)
# =============================================================================

print_header "Phase 1: Claude Code Bug Detection"
print_info "Testing if Option 1 (fresh context) fires PostToolUse hooks"
print_info "This detects the upstream Claude Code bug - NOT a failure on our part"

print_step "Starting Claude with ${CLAUDE_MODEL} model..."
send_keys "claude --model ${CLAUDE_MODEL}"
send_enter
sleep 2

print_step "Trusting folder if prompted..."
send_enter
sleep 3

if ! wait_for_claude $TIMEOUT_CLAUDE_START; then
    print_failure "Claude failed to start"
    CLAUDE_BUG_DETECTED="UNKNOWN"
else
    print_success "Claude started"

    print_step "Entering plan mode..."
    send_keys "/plan"
    send_enter
    sleep 2

    print_step "Creating test plan (plan1)..."
    send_keys "create file plan1.txt with content 'plan 1 test'"
    send_enter

    if ! wait_for_plan_prompt $TIMEOUT_PLAN_CREATE; then
        print_failure "Plan was not created (timeout)"
        CLAUDE_BUG_DETECTED="UNKNOWN"
    else
        print_success "Plan created"

        PHASE1_NOTES_BEFORE=$(get_notes_count)
        print_info "Notes count before Option 1: $PHASE1_NOTES_BEFORE"

        print_step "Accepting with Option 1 (fresh context)..."
        send_keys "1"
        send_enter

        if ! wait_for_execution $TIMEOUT_PLAN_EXECUTE; then
            print_warn "Plan execution timeout (continuing anyway)"
        else
            print_success "Plan executed"
        fi

        sleep 3  # Give hooks time to run

        PHASE1_NOTES_AFTER=$(get_notes_count)
        print_info "Notes count after Option 1: $PHASE1_NOTES_AFTER"

        if [[ "$PHASE1_NOTES_AFTER" -gt "$PHASE1_NOTES_BEFORE" ]]; then
            print_success "PostToolUse hook fired! Claude Code bug may be FIXED"
            CLAUDE_BUG_DETECTED="NO"
        else
            print_warn "PostToolUse hook did NOT fire (Claude Code bug confirmed)"
            CLAUDE_BUG_DETECTED="YES"
        fi
    fi
fi

# =============================================================================
# PHASE 2: Test SessionStart Workaround
# =============================================================================

print_header "Phase 2: SessionStart Workaround Test"
print_info "Starting NEW session to trigger SessionStart hook"
print_info "This should catch the unexported plan from Phase 1"

# Exit current Claude and start fresh session
print_step "Exiting Claude..."
send_keys "/exit"
send_enter
sleep 3

PHASE2_NOTES_BEFORE=$(get_notes_count)
print_info "Notes count before new session: $PHASE2_NOTES_BEFORE"

if [[ "$CLAUDE_BUG_DETECTED" == "NO" ]]; then
    print_info "Claude bug not detected - SessionStart workaround test skipped"
    WORKAROUND_WORKS="SKIPPED"
    PHASE2_NOTES_AFTER=$PHASE2_NOTES_BEFORE
else
    print_step "Starting NEW Claude session (triggers SessionStart hook)..."
    send_keys "claude --model ${CLAUDE_MODEL}"
    send_enter
    sleep 2

    # Trust folder
    send_enter
    sleep 3

    if ! wait_for_claude $TIMEOUT_CLAUDE_START; then
        print_failure "Claude failed to restart"
        WORKAROUND_WORKS="UNKNOWN"
    else
        print_success "Claude restarted - SessionStart hook should have fired"

        # Give SessionStart handler time to export
        sleep 5

        PHASE2_NOTES_AFTER=$(get_notes_count)
        print_info "Notes count after new session: $PHASE2_NOTES_AFTER"

        if [[ "$PHASE2_NOTES_AFTER" -gt "$PHASE2_NOTES_BEFORE" ]]; then
            print_success "SessionStart workaround WORKED - plan was exported!"
            WORKAROUND_WORKS="YES"
        else
            print_failure "SessionStart workaround FAILED - plan not exported"
            WORKAROUND_WORKS="NO"
        fi
    fi
fi

# =============================================================================
# PHASE 3: Baseline Check (Option 2 - normal accept)
# =============================================================================

print_header "Phase 3: Baseline Check (Option 2)"
print_info "Testing that normal Option 2 accept still works correctly"

# If we skipped phase 2, we need to start Claude
if [[ "$WORKAROUND_WORKS" == "SKIPPED" ]]; then
    print_step "Starting Claude for baseline test..."
    send_keys "claude --model ${CLAUDE_MODEL}"
    send_enter
    sleep 2
    send_enter
    sleep 3

    if ! wait_for_claude $TIMEOUT_CLAUDE_START; then
        print_failure "Claude failed to start"
        OPTION2_WORKS="UNKNOWN"
    else
        print_success "Claude started"
    fi
fi

if [[ "$OPTION2_WORKS" != "UNKNOWN" ]]; then
    print_step "Entering plan mode..."
    send_keys "/plan"
    send_enter
    sleep 2

    print_step "Creating test plan (plan2)..."
    send_keys "create file plan2.txt with content 'plan 2 test'"
    send_enter

    if ! wait_for_plan_prompt $TIMEOUT_PLAN_CREATE; then
        print_failure "Plan was not created (timeout)"
        OPTION2_WORKS="UNKNOWN"
    else
        print_success "Plan created"

        PHASE3_NOTES_BEFORE=$(get_notes_count)
        print_info "Notes count before Option 2: $PHASE3_NOTES_BEFORE"

        print_step "Accepting with Option 2 (regular accept)..."
        send_keys "2"
        send_enter

        if ! wait_for_execution $TIMEOUT_PLAN_EXECUTE; then
            print_warn "Plan execution timeout"
        else
            print_success "Plan executed"
        fi

        sleep 3

        PHASE3_NOTES_AFTER=$(get_notes_count)
        print_info "Notes count after Option 2: $PHASE3_NOTES_AFTER"

        if [[ "$PHASE3_NOTES_AFTER" -gt "$PHASE3_NOTES_BEFORE" ]]; then
            print_success "Option 2 PostToolUse hook works correctly"
            OPTION2_WORKS="YES"
        else
            print_failure "Option 2 PostToolUse hook FAILED"
            OPTION2_WORKS="NO"
        fi
    fi
fi

# =============================================================================
# Final Summary Report
# =============================================================================

print_summary

# =============================================================================
# Determine Exit Code
# =============================================================================

# Exit 0 = all critical tests passed
# Exit 1 = critical test failed (our code is broken)

if [[ "$WORKAROUND_WORKS" == "NO" ]]; then
    print_failure "CRITICAL: SessionStart workaround is broken!"
    exit 1
fi

if [[ "$OPTION2_WORKS" == "NO" ]]; then
    print_failure "CRITICAL: Basic Option 2 functionality is broken!"
    exit 1
fi

if [[ "$WORKAROUND_WORKS" == "YES" || "$WORKAROUND_WORKS" == "SKIPPED" ]] && [[ "$OPTION2_WORKS" == "YES" ]]; then
    print_success "All critical tests passed"
    exit 0
fi

# Unknown state - treat as failure
print_warn "Tests incomplete - treating as failure"
exit 1
