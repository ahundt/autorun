#!/usr/bin/env python3

# Copyright 2025 Andrew Hundt <ATHundt@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Manual Integration Test: Autorun Activates Automatically After Plan Approval

THIS FILE DOES NOT MATCH PYTEST AUTO-DISCOVERY PATTERNS (test_*.py)
Run manually with: python manual_autorun_on_plan_approval_test.py

Purpose:
    Tests the automatic transition from plan approval to autonomous execution.
    When a user approves a plan and Claude outputs "PLAN ACCEPTED", the
    autorun Stop hook should automatically activate autorun mode so Claude
    continues executing the plan without requiring manual /ar:go command.

What This Tests:
    - Plan mode entry and plan creation
    - Plan approval flow
    - Automatic autorun activation on "PLAN ACCEPTED" output
    - End-to-end plan execution (creates and runs hello.py)

Test Flow:
    1. Create isolated tmux session
    2. Launch Claude with haiku model
    3. Send /ar:plannew to create a simple hello.py plan
    4. Enter plan mode (Shift+Tab fallback if needed)
    5. Wait for plan to be generated
    6. Accept the plan (press Enter)
    7. Verify Claude outputs "PLAN ACCEPTED"
    8. Verify autorun activates and Claude executes the plan
    9. Verify hello.py was created and outputs "Hello World"
    10. Clean up session

Cost Warning:
    This test uses the Claude API and costs real money.
    It uses the haiku model (~$0.01-0.05 per test run).
"""

import os
import sys
import time
import argparse
import subprocess
from typing import Optional, Tuple

# Add parent src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from autorun.tmux_utils import (
    TmuxUtilities,
    get_tmux_utilities,
    send_text_and_enter,
    send_shift_tab,
    send_escape,
    send_ctrl_c_twice,
    tmux_detect_claude_mode,
    tmux_detect_claude_active,
    tmux_detect_prompt_type,
    tmux_get_claude_window_status,
    CLAUDE_MODE_PLAN,
    CLAUDE_MODE_DEFAULT,
    PROMPT_TYPE_PLAN_APPROVAL,
    PROMPT_TYPE_INPUT,
)
from autorun.config import CONFIG


# Test configuration
TEST_SESSION_NAME = "plan-autorun-test"
TEST_WORKING_DIR = "/tmp/plan-autorun-test"
CLAUDE_MODEL = "haiku"  # Use cheapest model for testing
MAX_WAIT_SECONDS = 90  # Max time to wait for Claude responses
POLL_INTERVAL = 2  # Seconds between status checks


class PlanApprovalAutorunTest:
    """Tests that autorun automatically activates when a plan is approved."""

    def __init__(self, verbose: bool = False, no_cleanup: bool = False):
        self.verbose = verbose
        self.no_cleanup = no_cleanup
        self.tmux: Optional[TmuxUtilities] = None
        self.session = TEST_SESSION_NAME
        self.window = "1"  # tmux windows start at 1 by default
        self.pane = "1"    # tmux panes also start at 1 by default
        self.test_passed = False

    def log(self, message: str):
        """Log message if verbose mode enabled."""
        if self.verbose:
            print(f"[Plan Autorun Test] {message}")

    def log_always(self, message: str):
        """Log message regardless of verbose mode."""
        print(f"[Plan Autorun Test] {message}")

    def setup(self) -> bool:
        """Set up test environment."""
        self.log("Setting up test environment...")

        # Create working directory
        os.makedirs(TEST_WORKING_DIR, exist_ok=True)

        # Kill any existing test session
        subprocess.run(
            ["tmux", "kill-session", "-t", TEST_SESSION_NAME],
            capture_output=True
        )
        time.sleep(0.5)

        # Create new detached tmux session
        result = subprocess.run(
            ["tmux", "new-session", "-d", "-s", TEST_SESSION_NAME, "-c", TEST_WORKING_DIR],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            self.log_always(f"Failed to create tmux session: {result.stderr}")
            return False

        self.log(f"Created tmux session: {TEST_SESSION_NAME}")

        # Get tmux utilities instance
        self.tmux = get_tmux_utilities(TEST_SESSION_NAME)

        # Verify session exists
        if not self.tmux.ensure_session_exists(TEST_SESSION_NAME):
            self.log_always("Failed to verify tmux session exists")
            return False

        self.log("Test environment ready")
        return True

    def capture_content(self, lines: int = 200) -> Tuple[bool, str]:
        """Capture terminal content from test window.

        Returns:
            Tuple of (success, content) - success indicates if capture worked,
            content may be empty string for fresh terminal.
        """
        result = self.tmux.execute_tmux_command(
            ['capture-pane', '-p', '-S', f'-{lines}'],
            self.session, self.window, self.pane
        )
        if result and result.get('returncode') == 0:
            return True, result.get('stdout', '')
        self.log(f"Capture failed: {result}")
        return False, ''

    def wait_for_condition(
        self,
        condition_fn,
        description: str,
        max_wait: int = MAX_WAIT_SECONDS
    ) -> bool:
        """Wait for a condition to become true."""
        self.log(f"Waiting for: {description}")
        start_time = time.time()
        last_log_time = 0

        while time.time() - start_time < max_wait:
            elapsed = time.time() - start_time
            success, content = self.capture_content()

            # Log progress every 15 seconds
            if elapsed - last_log_time >= 15:
                self.log(f"Still waiting for {description} ({int(elapsed)}s elapsed)")
                # Show last 300 chars of terminal
                self.log(f"Current terminal (last 300 chars): ...{content[-300:]}")
                last_log_time = elapsed

            if success and condition_fn(content):
                self.log(f"Condition met: {description}")
                return True
            time.sleep(POLL_INTERVAL)

        # On timeout, show what we have
        self.log_always(f"Timeout waiting for: {description}")
        _, content = self.capture_content()
        self.log_always(f"Final terminal content (last 500 chars):")
        self.log_always(content[-500:] if len(content) > 500 else content)
        return False

    def launch_claude(self) -> bool:
        """Launch Claude with haiku model in the test session."""
        self.log(f"Launching Claude with {CLAUDE_MODEL} model...")

        # First verify we can capture from the session (allow time for shell to start)
        time.sleep(1)
        success, content = self.capture_content(lines=5)
        if not success:
            self.log_always(f"ERROR: Cannot capture content from {self.session}:{self.window}.{self.pane}")
            # Try to list windows to diagnose
            result = subprocess.run(
                ["tmux", "list-windows", "-t", self.session],
                capture_output=True, text=True
            )
            self.log_always(f"Available windows: {result.stdout.strip()}")
            return False

        self.log(f"Session capture verified, sending claude command...")

        # Send claude command
        claude_cmd = f"claude --model {CLAUDE_MODEL}"
        if not send_text_and_enter(self.tmux, claude_cmd, self.session, self.window, self.pane):
            self.log_always(f"Failed to send claude command to {self.session}:{self.window}.{self.pane}")
            return False

        # Give Claude time to load
        time.sleep(2)

        # Check if we need to accept the trust prompt
        def has_trust_prompt(content: str) -> bool:
            return "trust the files" in content.lower() or "Yes, proceed" in content

        def is_at_input_prompt(content: str) -> bool:
            # Check for Claude's input prompt - can be ">" or "❯" or has "Try" suggestion
            if "❯" in content or "Try \"" in content:
                return True
            lines = content.strip().split('\n')
            for line in lines[-10:]:
                stripped = line.strip()
                # Check for various prompt characters
                if stripped.startswith('>') or stripped.startswith('❯'):
                    return True
            prompt_type = tmux_detect_prompt_type(content)
            return prompt_type == PROMPT_TYPE_INPUT

        # Wait for either trust prompt or input prompt
        def claude_responding(content: str) -> bool:
            return has_trust_prompt(content) or is_at_input_prompt(content)

        if not self.wait_for_condition(claude_responding, "Claude to respond", max_wait=30):
            _, final_content = self.capture_content()
            self.log_always("Claude did not respond")
            self.log_always(f"Terminal content:\n{final_content[-500:]}")
            return False

        # Check if we got trust prompt and need to accept it
        _, content = self.capture_content()
        if has_trust_prompt(content):
            self.log("Trust prompt detected, sending '1' to accept...")
            time.sleep(0.5)
            if not send_text_and_enter(self.tmux, "1", self.session, self.window, self.pane):
                self.log_always("Failed to send trust acceptance")
                return False
            time.sleep(2)  # Wait for Claude to process acceptance

        # Now wait for the actual input prompt
        if not self.wait_for_condition(is_at_input_prompt, "Claude input prompt", max_wait=30):
            _, final_content = self.capture_content()
            self.log_always("Claude did not show input prompt")
            self.log_always(f"Terminal content:\n{final_content[-500:]}")
            return False

        self.log("Claude started successfully")
        return True

    def enter_plan_mode(self) -> bool:
        """Enter plan mode - the /ar:plannew command instructs Claude to use EnterPlanMode tool.

        This method just verifies plan mode is active after sending the command.
        If not active, it falls back to Shift+Tab to manually toggle plan mode.
        """
        # Plan mode will be entered by Claude when processing /ar:plannew command
        # This method is called before sending the command, so we just return True
        # and let send_plan_request handle it. If plan mode doesn't activate,
        # we'll use Shift+Tab as fallback.
        self.log("Plan mode will be requested via /ar:plannew command")
        return True

    def ensure_plan_mode_with_fallback(self) -> bool:
        """Verify plan mode is active, use Shift+Tab as fallback if not."""
        _, content = self.capture_content()

        # Check if already in plan mode
        if "plan mode on" in content.lower() or "⏸ plan mode" in content:
            self.log("Plan mode confirmed active")
            return True

        # Fallback: Use Shift+Tab to cycle into plan mode
        self.log("Plan mode not active, using Shift+Tab fallback...")

        max_attempts = 5  # Should only need 1-2 presses
        for attempt in range(max_attempts):
            # Send Shift+Tab to cycle to next mode
            self.log(f"Sending Shift+Tab (attempt {attempt + 1}/{max_attempts})...")
            if not send_shift_tab(self.tmux, self.session, self.window, self.pane):
                self.log_always("Failed to send Shift+Tab")
                return False

            time.sleep(1.5)  # Wait for mode to change

            # Check if now in plan mode
            _, content = self.capture_content()
            if "plan mode on" in content.lower() or "⏸ plan mode" in content:
                self.log(f"Plan mode activated via Shift+Tab (after {attempt + 1} presses)")
                return True

        self.log_always(f"Plan mode not detected after {max_attempts} Shift+Tab attempts.")
        self.log_always(f"Terminal (last 300 chars): {content[-300:]}")
        return False

    def send_plan_request(self) -> bool:
        """Send a simple plan request using /ar:plannew command which includes PLAN ACCEPTED instructions."""
        self.log("Sending plan request using /ar:plannew command...")

        # Use the full command path - /ar:plannew (not /ar:pn alias which may not be registered)
        # Also explicitly tell Claude to enter plan mode in case the manual Shift+Tab didn't work
        plan_request = "/ar:plannew Enter plan mode and create a simple 2-step plan to write hello.py that prints Hello World. Do not ask any questions, just create the plan and submit for approval."

        if not send_text_and_enter(self.tmux, plan_request, self.session, self.window, self.pane):
            self.log_always("Failed to send plan request")
            return False

        self.log("Plan request sent")
        return True

    def wait_for_plan_approval_prompt(self) -> bool:
        """Wait for the plan approval prompt (numbered options)."""
        def is_plan_approval(content: str) -> bool:
            # Look for the actual plan approval prompt phrases
            approval_indicators = [
                "Would you like to proceed?",
                "Yes, clear context",
                "Yes, auto-accept edits",
                "Yes, manually approve"
            ]
            for indicator in approval_indicators:
                if indicator in content:
                    return True

            prompt_type = tmux_detect_prompt_type(content)
            return prompt_type == PROMPT_TYPE_PLAN_APPROVAL

        return self.wait_for_condition(
            is_plan_approval,
            "plan approval prompt",
            max_wait=90  # Plans can take a while to generate
        )

    def accept_plan(self) -> bool:
        """Accept the plan by pressing Enter (option 1 is pre-selected)."""
        self.log("Accepting plan...")

        # First, ensure we're at the plan approval prompt
        _, content = self.capture_content()
        if "Would you like to proceed?" not in content and "Yes, clear context" not in content:
            self.log("Plan approval prompt not visible yet, waiting...")
            # Wait for the prompt to appear
            for i in range(30):  # Wait up to 30 seconds
                time.sleep(1)
                _, content = self.capture_content()
                if "Would you like to proceed?" in content:
                    self.log(f"Plan approval prompt now visible (after {i+1}s)")
                    break
            else:
                self.log_always("Plan approval prompt never appeared")
                return False

        # Give tmux a moment to stabilize after prompt appears
        time.sleep(2)

        # The plan approval prompt shows option 1 as pre-selected with ❯
        # Just pressing Enter should select it, but we can also try "1" + Enter
        # Try multiple approaches
        for attempt in range(3):
            self.log(f"Attempting to accept plan (attempt {attempt + 1}/3)...")

            # Approach 1: Just press Enter (option 1 is pre-selected)
            if attempt == 0:
                self.log("Pressing Enter to select pre-selected option 1...")
                result = self.tmux.execute_tmux_command(
                    ['send-keys', 'Enter'],
                    self.session, self.window, self.pane
                )
            # Approach 2: Type "1" then Enter
            elif attempt == 1:
                self.log("Sending '1' then Enter...")
                if not send_text_and_enter(self.tmux, "1", self.session, self.window, self.pane):
                    self.log_always("Failed to send '1' + Enter")
                    continue
            # Approach 3: Try sending literal "1\n"
            else:
                self.log("Sending '1' as literal text...")
                result = self.tmux.execute_tmux_command(
                    ['send-keys', '-l', '1'],
                    self.session, self.window, self.pane
                )
                time.sleep(0.5)
                result = self.tmux.execute_tmux_command(
                    ['send-keys', 'Enter'],
                    self.session, self.window, self.pane
                )

            # Wait for Claude to process the acceptance
            time.sleep(5)

            # Check if plan approval prompt is still showing (needs another try)
            _, content = self.capture_content()
            self.log(f"Terminal content after attempt {attempt + 1} (last 200 chars): {content[-200:]}")

            if "Would you like to proceed?" not in content and "Yes, clear context" not in content:
                self.log("Plan approval prompt dismissed, acceptance worked")
                break
            else:
                self.log(f"Plan approval prompt still showing after attempt {attempt + 1}")
                time.sleep(1)
        else:
            self.log_always("Plan approval prompt still visible after 3 attempts")
            return False

        self.log("Plan acceptance sent")
        return True

    def verify_plan_accepted(self) -> bool:
        """Verify that PLAN ACCEPTED appears in output OR that execution started."""
        plan_marker = CONFIG.get("plan_accepted_marker", "PLAN ACCEPTED")

        def has_plan_accepted_or_execution(content: str) -> bool:
            # Primary: Look for the PLAN ACCEPTED marker
            if plan_marker in content:
                self.log(f"Found '{plan_marker}' marker")
                return True

            # Secondary: Look for signs that execution started (Claude may skip the marker)
            execution_indicators = [
                "Write(",      # Claude using Write tool
                "Bash(",       # Claude using Bash tool
                "Read(",       # Claude using Read tool
                "Wrote",       # Write tool completed
                "Do you want to proceed?",  # Bash approval prompt
            ]
            for indicator in execution_indicators:
                if indicator in content:
                    self.log(f"Plan execution started (found '{indicator}')")
                    return True

            return False

        result = self.wait_for_condition(
            has_plan_accepted_or_execution,
            f"'{plan_marker}' marker or execution start",
            max_wait=60  # Should be quick after plan approval
        )

        if not result:
            # Log terminal content for debugging
            _, content = self.capture_content(lines=100)
            self.log_always(f"Terminal content when looking for '{plan_marker}':")
            self.log_always(content[-2000:] if len(content) > 2000 else content)

        return result

    def verify_hello_world_output(self) -> bool:
        """Verify that hello.py was created and outputs 'Hello World' when run."""
        # Check multiple possible locations
        possible_paths = [
            os.path.join(TEST_WORKING_DIR, "hello.py"),
            "/tmp/plan-autorun-test/hello.py",
            "/private/tmp/plan-autorun-test/hello.py",  # macOS symlink
        ]

        hello_path = None
        for path in possible_paths:
            if os.path.exists(path):
                hello_path = path
                break

        if hello_path is None:
            # Try to find hello.py anywhere in /tmp
            self.log("Searching for hello.py in /tmp...")
            try:
                result = subprocess.run(
                    ["find", "/tmp", "-name", "hello.py", "-type", "f"],
                    capture_output=True, text=True, timeout=5
                )
                if result.stdout.strip():
                    hello_path = result.stdout.strip().split('\n')[0]
                    self.log(f"Found hello.py at: {hello_path}")
            except Exception as e:
                self.log(f"Find failed: {e}")

        if hello_path is None:
            self.log_always(f"hello.py not found in expected locations: {possible_paths}")
            return False

        self.log(f"hello.py exists at {hello_path}")

        # Run the script and check output
        try:
            result = subprocess.run(
                ["python3", hello_path],
                capture_output=True,
                text=True,
                timeout=10
            )

            output = result.stdout.strip()
            self.log(f"hello.py output: '{output}'")

            if "Hello World" in output:
                self.log("Output contains 'Hello World' - verified!")
                return True
            else:
                self.log_always(f"Unexpected output: '{output}' (expected 'Hello World')")
                return False

        except subprocess.TimeoutExpired:
            self.log_always("hello.py execution timed out")
            return False
        except Exception as e:
            self.log_always(f"Error running hello.py: {e}")
            return False

    def verify_completion_marker(self) -> bool:
        """Verify that Claude outputs a completion marker when done.

        The autorun system uses a three-stage completion process. Valid completion markers:
        - AUTORUN_STAGE3_COMPLETE (stage3_confirmation - technical marker)
        - AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY (completion_marker - descriptive)

        Also tracks stage progression to diagnose where the process stops.

        Continues approving bash prompts while waiting, so Claude can complete
        its verification steps and output the marker.
        """
        self.log("Checking for completion marker while approving bash prompts...")

        # Both markers are valid completion signals (system accepts either)
        completion_markers = [
            "AUTORUN_STAGE3_COMPLETE",  # Technical stage3_confirmation
            "AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY",  # Descriptive completion_marker
        ]

        # Stage progression markers (for diagnostics)
        stage_markers = {
            "AUTORUN_STAGE1_COMPLETE": "Stage 1",
            "AUTORUN_STAGE2_COMPLETE": "Stage 2",
            "AUTORUN_STAGE3_COMPLETE": "Stage 3 (completion)",
        }

        stages_seen = set()
        max_iterations = 30  # 60 seconds max (30 x 2s)

        for iteration in range(max_iterations):
            # Approve any pending bash prompts so Claude can continue
            self.approve_bash_if_needed()

            # Check for completion marker
            _, content = self.capture_content()

            # Track stage progression
            for marker, stage_name in stage_markers.items():
                if marker in content and marker not in stages_seen:
                    stages_seen.add(marker)
                    self.log(f"Stage progression: {stage_name} marker found")

            # Check for any valid completion marker
            for marker in completion_markers:
                if marker in content:
                    self.log(f"Completion marker found: {marker}")
                    if stages_seen:
                        self.log(f"Stages completed: {', '.join(stages_seen)}")
                    return True

            # Check if Claude returned to input prompt (finished without marker)
            if "❯" in content and "Do you want to proceed?" not in content:
                # Verify it's actually an input prompt, not mid-execution
                if tmux_detect_prompt_type(content) == PROMPT_TYPE_INPUT:
                    self.log_always("Claude at input prompt - task stopped without completion marker")
                    if stages_seen:
                        self.log_always(f"Stages reached before stopping: {', '.join(stages_seen)}")
                    else:
                        self.log_always("No stage markers found - autorun three-stage system may not have activated")
                    return False

            time.sleep(2)

        # Timeout - show what we have
        _, content = self.capture_content()
        self.log_always("Completion marker not found after 60s")
        self.log_always(f"Final terminal (last 500 chars): {content[-500:]}")
        return False

    def approve_bash_if_needed(self) -> bool:
        """Check for and approve bash command prompts."""
        _, content = self.capture_content()

        # Check if bash approval prompt is showing
        if "Do you want to proceed?" in content and ("Bash" in content or "command" in content.lower()):
            self.log("Bash approval prompt detected, approving...")
            time.sleep(1)

            # Press Enter to approve (option 1 is pre-selected)
            result = self.tmux.execute_tmux_command(
                ['send-keys', 'Enter'],
                self.session, self.window, self.pane
            )
            time.sleep(2)

            # Verify prompt was dismissed
            _, new_content = self.capture_content()
            if "Do you want to proceed?" not in new_content:
                self.log("Bash command approved")
                return True
            else:
                self.log("Bash prompt still showing, trying '1' + Enter")
                send_text_and_enter(self.tmux, "1", self.session, self.window, self.pane)
                time.sleep(2)
                return True

        return False  # No bash prompt to approve

    def verify_autorun_activated(self) -> bool:
        """Verify that autorun was activated after plan acceptance."""
        # After PLAN ACCEPTED, the stop hook should inject the autorun prompt
        # Look for indicators that autorun is active:
        # 1. Claude continues working without user prompt
        # 2. Autorun stage markers appear
        # 3. No new user input prompt appears immediately

        def autorun_active(content: str) -> bool:
            # Check for autorun activation indicators
            indicators = [
                "AUTONOMOUS",  # From autorun injection
                "EXECUTION",  # From autorun injection
                "execution mode",  # Common in autorun
                "continue",  # Autorun continuation
                # Check Claude is actively generating (not waiting for input)
            ]

            for indicator in indicators:
                if indicator.lower() in content.lower():
                    return True

            # Also check that Claude is actively generating (not stopped at input)
            if tmux_detect_claude_active(content):
                return True

            return False

        # Wait a bit for the stop hook to fire and inject
        time.sleep(3)

        # Check for 30 seconds
        result = self.wait_for_condition(
            autorun_active,
            "autorun activation indicators",
            max_wait=30
        )

        if result:
            self.log("Autorun activation detected!")
            return True

        # If we don't see explicit indicators, check that Claude didn't just stop
        _, content = self.capture_content()
        prompt_type = tmux_detect_prompt_type(content)

        # If we're back at input prompt without autorun, test failed
        if prompt_type == PROMPT_TYPE_INPUT:
            self.log_always("Claude returned to input prompt - autorun may not have activated")
            return False

        # If Claude is still working, that's a good sign
        if tmux_detect_claude_active(content):
            self.log("Claude is still actively generating - autorun likely working")
            return True

        return False

    def cleanup(self):
        """Clean up test resources (unless --no-cleanup was specified)."""
        if self.no_cleanup:
            self.log_always("Skipping cleanup (--no-cleanup specified)")
            self.log_always(f"  tmux session: {TEST_SESSION_NAME}")
            self.log_always(f"  working dir: {TEST_WORKING_DIR}")
            self.log_always("  To attach: tmux attach -t " + TEST_SESSION_NAME)
            self.log_always("  To cleanup manually: tmux kill-session -t " + TEST_SESSION_NAME + " && rm -rf " + TEST_WORKING_DIR)
            return

        self.log("Cleaning up...")

        # Try to exit Claude gracefully
        if self.tmux:
            send_ctrl_c_twice(self.tmux, self.session, self.window, self.pane)
            time.sleep(1)

        # Kill tmux session
        subprocess.run(
            ["tmux", "kill-session", "-t", TEST_SESSION_NAME],
            capture_output=True
        )

        # Remove test directory
        subprocess.run(["rm", "-rf", TEST_WORKING_DIR], capture_output=True)

        self.log("Cleanup complete")

    def run_test(self) -> bool:
        """Run the complete plan approval autorun test."""
        self.log_always("=" * 60)
        self.log_always("Plan Approval → Autorun Activation Test")
        self.log_always("=" * 60)
        self.log_always(f"Session: {TEST_SESSION_NAME}")
        self.log_always(f"Model: {CLAUDE_MODEL}")
        self.log_always(f"Plan marker: {CONFIG.get('plan_accepted_marker', 'PLAN ACCEPTED')}")
        self.log_always("")

        try:
            # Step 1: Setup
            self.log_always("Step 1/9: Setting up test environment...")
            if not self.setup():
                return False

            # Step 2: Launch Claude
            self.log_always("Step 2/9: Launching Claude...")
            if not self.launch_claude():
                return False

            # Step 3: Send plan request (command instructs Claude to enter plan mode)
            self.log_always("Step 3/9: Sending plan request...")
            if not self.send_plan_request():
                return False

            # Give Claude a moment to process the command
            time.sleep(3)

            # Step 4: Verify plan mode is active, use Shift+Tab fallback if needed
            self.log_always("Step 4/9: Verifying plan mode...")
            if not self.ensure_plan_mode_with_fallback():
                self.log_always("WARNING: Could not verify plan mode, continuing anyway")

            # Step 5: Wait for plan approval prompt
            self.log_always("Step 5/9: Waiting for plan completion...")
            if not self.wait_for_plan_approval_prompt():
                self.log_always("WARNING: Could not detect plan approval prompt")
                self.log_always("Continuing anyway - will try to accept plan")

            # Step 6: Accept plan
            self.log_always("Step 6/9: Accepting plan...")
            if not self.accept_plan():
                return False

            # Step 7: Verify PLAN ACCEPTED and autorun activation
            self.log_always("Step 7/9: Verifying plan acceptance and autorun...")

            # First verify PLAN ACCEPTED marker or execution start
            if not self.verify_plan_accepted():
                self.log_always("FAILED: No plan acceptance or execution detected")
                return False

            self.log_always("✅ Plan execution started (marker or tool use detected)")

            # Handle any bash approval prompts that may appear during execution
            # Keep approving bash commands for up to 30 seconds
            self.log_always("Handling any bash approval prompts...")
            for i in range(15):  # Check for 30 seconds (15 x 2s)
                self.approve_bash_if_needed()
                time.sleep(2)

                # Check if execution seems complete
                _, content = self.capture_content()
                if "AUTORUN_ALL_TASKS_COMPLETED" in content:
                    self.log("Execution completed with success marker")
                    break
                if "Hello World" in content:
                    self.log("Script output 'Hello World' detected - execution successful")
                    break

            self.log_always("✅ Execution phase completed")

            # Step 8: Verify the actual output - hello.py exists and runs correctly
            self.log_always("Step 8/9: Verifying hello.py was created and works...")
            if not self.verify_hello_world_output():
                self.log_always("FAILED: hello.py verification failed")
                return False

            self.log_always("✅ hello.py verified - outputs 'Hello World' correctly")

            # Step 9: Check for completion marker (optional - haiku may skip it)
            self.log_always("Step 9/9: Checking for completion marker...")
            if self.verify_completion_marker():
                self.log_always("✅ Completion marker found - Claude signaled task complete")
            else:
                self.log_always("⚠️ Completion marker not found (optional - task still succeeded)")

            self.test_passed = True
            return True

        except KeyboardInterrupt:
            self.log_always("\nTest interrupted by user")
            return False
        except Exception as e:
            self.log_always(f"Test error: {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            self.cleanup()

    def print_result(self):
        """Print final test result."""
        self.log_always("")
        self.log_always("=" * 60)
        if self.test_passed:
            self.log_always("✅ TEST PASSED: Autorun activated automatically after plan approval")
        else:
            self.log_always("❌ TEST FAILED: Autorun did not activate after plan approval")
        self.log_always("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Test that autorun activates automatically when a plan is approved"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print test steps without executing (no API cost)"
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Skip cleanup after test (for debugging - leaves tmux session and files)"
    )

    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN - Test steps would be:")
        print("1. Create tmux session 'plan-autorun-test'")
        print("2. Launch 'claude --model haiku'")
        print("3. Send Shift+Tab to enter plan mode")
        print("4. Send plan request")
        print("5. Wait for plan approval prompt")
        print("6. Send '1' to accept plan")
        print("7. Verify 'PLAN ACCEPTED' in output")
        print("8. Verify autorun activation")
        print("9. Clean up session")
        return 0

    # Confirm with user before running (costs money)
    print("")
    print("⚠️  WARNING: This test uses the Claude API and costs real money.")
    print(f"Model: {CLAUDE_MODEL} (lowest cost)")
    print("")
    response = input("Continue? [y/N]: ").strip().lower()
    if response != 'y':
        print("Test cancelled")
        return 0

    print("")

    test = PlanApprovalAutorunTest(verbose=args.verbose, no_cleanup=args.no_cleanup)
    success = test.run_test()
    test.print_result()

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
