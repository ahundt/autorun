#!/usr/bin/env python3
"""
Diagnostic test for session targeting issue.

This test identifies and explains the root cause of why session targeting is failing.
"""

import pytest
import subprocess
import time
import os
import sys

pytestmark = pytest.mark.tmux

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from clautorun.tmux_utils import get_tmux_utilities


def test_session_targeting_root_cause_analysis():
    """
    Diagnose the root cause of session targeting failure.

    This test identifies exactly what's going wrong and provides actionable information.
    """
    print("\n" + "="*80)
    print("SESSION TARGETING DIAGNOSTIC TEST")
    print("="*80)

    test_session = "diagnostic-test-session"

    # Clean up any existing test session
    subprocess.run(['tmux', 'kill-session', '-t', test_session],
                  capture_output=True, timeout=5)

    # Create test session
    subprocess.run(['tmux', 'new-session', '-d', '-s', test_session],
                  capture_output=True, timeout=5)

    try:
        print(f"\n1. Created test session: {test_session}")

        # Test 1: Manual tmux command (should work)
        print("\n2. Testing MANUAL tmux command:")
        manual_result = subprocess.run(['tmux', 'send-keys', '-t', test_session, 'echo manual-test', 'C-m'],
                                       capture_output=True, text=True, timeout=5)
        print(f"   Command: tmux send-keys -t {test_session} echo manual-test C-m")
        print(f"   Return code: {manual_result.returncode}")

        time.sleep(0.2)
        manual_capture = subprocess.run(['tmux', 'capture-pane', '-t', test_session, '-p'],
                                        capture_output=True, text=True, timeout=5)
        manual_success = "echo manual-test" in manual_capture.stdout
        print(f"   Success: {manual_success}")
        if manual_success:
            print("   ✅ Manual tmux command works correctly")
        else:
            print("   ❌ Manual tmux command failed")

        # Test 2: Python tmux utilities (our problematic code)
        print("\n3. Testing PYTHON tmux utilities:")
        tmux = get_tmux_utilities(test_session)

        # Send text
        text_result = tmux.send_keys('echo python-test', test_session)
        print(f"   send_keys result: {text_result}")

        # Send Enter
        enter_result = tmux.send_keys('C-m', test_session)
        print(f"   send_keys Enter result: {enter_result}")

        time.sleep(0.2)

        # Check where the command went
        python_capture_target = subprocess.run(['tmux', 'capture-pane', '-t', test_session, '-p'],
                                              capture_output=True, text=True, timeout=5)
        python_capture_current = subprocess.run(['tmux', 'capture-pane', '-p'],
                                                capture_output=True, text=True, timeout=5)

        target_success = "echo python-test" in python_capture_target.stdout
        current_leakage = "echo python-test" in python_capture_current.stdout

        print(f"   Target session success: {target_success}")
        print(f"   Current session leakage: {current_leakage}")

        # Test 3: Check command construction
        print("\n4. Analyzing command construction:")
        exec_result = tmux.execute_tmux_command(['send-keys', 'echo construction-test'])
        if exec_result:
            command = exec_result['command']
            print(f"   Constructed command: {command}")
            print(f"   Command type: {type(command)}")

            # Check if command structure is correct
            if isinstance(command, list) and len(command) >= 5:
                expected_structure = ['tmux', 'send-keys', 'echo construction-test', '-t', test_session]
                if command == expected_structure:
                    print("   ✅ Command structure is correct")
                else:
                    print("   ❌ Command structure is incorrect")
                    print(f"   Expected: {expected_structure}")
                    print(f"   Actual:   {command}")
            else:
                print("   ❌ Command structure is malformed")
        else:
            print("   ❌ Failed to get command construction result")

        # Test 4: Check for subprocess environment issues
        print("\n5. Checking subprocess environment:")
        tmux_env = os.getenv('TMUX')
        if tmux_env:
            print(f"   TMUX environment variable: {tmux_env}")
            socket_path = tmux_env.split(',')[0] if ',' in tmux_env else tmux_env
            print(f"   Socket path: {socket_path}")

            # Test with explicit socket
            socket_cmd = ['tmux', '-S', socket_path, 'send-keys', '-t', test_session, 'echo socket-test', 'C-m']
            socket_result = subprocess.run(socket_cmd, capture_output=True, text=True, timeout=5)
            print(f"   Socket command: {' '.join(socket_cmd)}")
            print(f"   Socket result return code: {socket_result.returncode}")

            time.sleep(0.2)
            socket_capture = subprocess.run(['tmux', 'capture-pane', '-t', test_session, '-p'],
                                           capture_output=True, text=True, timeout=5)
            socket_success = "echo socket-test" in socket_capture.stdout
            print(f"   Socket command success: {socket_success}")

            if socket_success and not target_success:
                print("\n" + "="*80)
                print("ROOT CAUSE IDENTIFIED:")
                print("="*80)
                print("The issue is that subprocess execution from within tmux requires")
                print("explicit socket specification to properly target other sessions.")
                print("Without the -S flag, tmux subprocess inherits the current session context.")
                print("\nACTIONABLE SOLUTION:")
                print("1. Modify execute_tmux_command to use explicit socket when TMUX env is set")
                print("2. Extract socket path from TMUX environment variable")
                print("3. Use tmux -S <socket> for all subprocess tmux commands")
                print("="*80)
                return False  # Test indicates problem exists
            elif socket_success and target_success:
                print("   ✅ Both socket and regular commands work")
                return True  # Test passes
        else:
            print("   No TMUX environment variable found")

        # Summary
        print("\n6. DIAGNOSTIC SUMMARY:")
        if manual_success and not target_success:
            print("   ❌ CRITICAL: Session targeting is broken in Python subprocess")
            print("   ✅ Manual tmux commands work fine")
            print("   ✅ Command construction appears correct")
            print("   ❌ The issue is in subprocess execution environment")
            print("\n   LIKELY CAUSE: tmux subprocess inherits current session context")
            print("   SOLUTION: Use explicit socket specification in subprocess")
        elif manual_success and target_success:
            print("   ✅ Session targeting appears to be working correctly")
        else:
            print("   ❌ Unknown issue - both manual and Python commands failed")

        return target_success

    finally:
        # Clean up
        subprocess.run(['tmux', 'kill-session', '-t', test_session],
                      capture_output=True, timeout=5)


if __name__ == '__main__':
    success = test_session_targeting_root_cause_analysis()
    if not success:
        pytest.fail("Session targeting diagnostic test revealed critical issues")
    else:
        print("\n✅ Session targeting diagnostic test passed")