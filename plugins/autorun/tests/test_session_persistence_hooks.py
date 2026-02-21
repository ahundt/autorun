#!/usr/bin/env python3
"""Test session state persistence across hook invocations (simulated as subprocesses)

This tests the fix for Issue #29 - session_state must persist across different
Python processes (each hook invocation is a separate process).
"""
import pytest
import subprocess
import sys
import uuid
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from conftest import register_test_session


class TestSessionPersistenceAcrossHooks:
    """Test that session state persists when accessed from separate processes (hook invocations)"""

    def setup_method(self):
        """Create unique session ID for each test"""
        self.session_id = f"test_hook_persist_{uuid.uuid4().hex[:8]}"
        register_test_session(self.session_id)

    def test_policy_persists_across_hook_calls(self):
        """Test policy written in one hook invocation is readable in another"""
        # Simulate first hook invocation (UserPromptSubmit setting policy)
        write_result = subprocess.run([
            sys.executable, "-c",
            f"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path('{Path(__file__).parent.parent / "src"}').resolve()))
from autorun.main import session_state

with session_state('{self.session_id}') as state:
    state['file_policy'] = 'JUSTIFY'
    state.sync()
print('WRITE_OK')
"""
        ], capture_output=True, text=True, timeout=5)

        assert write_result.returncode == 0, f"Write failed: {write_result.stderr}"
        assert 'WRITE_OK' in write_result.stdout

        # Simulate second hook invocation (PreToolUse reading policy)
        read_result = subprocess.run([
            sys.executable, "-c",
            f"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path('{Path(__file__).parent.parent / "src"}').resolve()))
from autorun.main import session_state

with session_state('{self.session_id}') as state:
    policy = state.get('file_policy', 'NOT_FOUND')
print(policy)
"""
        ], capture_output=True, text=True, timeout=5)

        assert read_result.returncode == 0, f"Read failed: {read_result.stderr}"
        assert read_result.stdout.strip() == 'JUSTIFY', \
            f"Expected 'JUSTIFY', got '{read_result.stdout.strip()}'"

    def test_multiple_policies_sequential(self):
        """Test setting different policies sequentially persists correctly"""
        policies = ['ALLOW', 'JUSTIFY', 'SEARCH']

        for policy in policies:
            # Write policy in subprocess
            subprocess.run([
                sys.executable, "-c",
                f"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path('{Path(__file__).parent.parent / "src"}').resolve()))
from autorun.main import session_state

with session_state('{self.session_id}') as state:
    state['file_policy'] = '{policy}'
"""
            ], check=True, timeout=5)

            # Read policy in different subprocess
            result = subprocess.run([
                sys.executable, "-c",
                f"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path('{Path(__file__).parent.parent / "src"}').resolve()))
from autorun.main import session_state

with session_state('{self.session_id}') as state:
    print(state.get('file_policy', 'NOT_FOUND'))
"""
            ], capture_output=True, text=True, check=True, timeout=5)

            assert result.stdout.strip() == policy, \
                f"Policy {policy} not persisted correctly"

    def test_concurrent_sessions_isolated(self):
        """Test that different sessions don't interfere with each other"""
        session_a = f"test_session_a_{uuid.uuid4().hex[:8]}"
        session_b = f"test_session_b_{uuid.uuid4().hex[:8]}"

        register_test_session(session_a)
        register_test_session(session_b)

        # Set JUSTIFY in session A
        subprocess.run([
            sys.executable, "-c",
            f"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path('{Path(__file__).parent.parent / "src"}').resolve()))
from autorun.main import session_state

with session_state('{session_a}') as state:
    state['file_policy'] = 'JUSTIFY'
"""
        ], check=True, timeout=5)

        # Set SEARCH in session B
        subprocess.run([
            sys.executable, "-c",
            f"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path('{Path(__file__).parent.parent / "src"}').resolve()))
from autorun.main import session_state

with session_state('{session_b}') as state:
    state['file_policy'] = 'SEARCH'
"""
        ], check=True, timeout=5)

        # Verify A still has JUSTIFY
        result_a = subprocess.run([
            sys.executable, "-c",
            f"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path('{Path(__file__).parent.parent / "src"}').resolve()))
from autorun.main import session_state

with session_state('{session_a}') as state:
    print(state.get('file_policy', 'NOT_FOUND'))
"""
        ], capture_output=True, text=True, check=True, timeout=5)

        # Verify B still has SEARCH
        result_b = subprocess.run([
            sys.executable, "-c",
            f"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path('{Path(__file__).parent.parent / "src"}').resolve()))
from autorun.main import session_state

with session_state('{session_b}') as state:
    print(state.get('file_policy', 'NOT_FOUND'))
"""
        ], capture_output=True, text=True, check=True, timeout=5)

        assert result_a.stdout.strip() == 'JUSTIFY', "Session A policy corrupted"
        assert result_b.stdout.strip() == 'SEARCH', "Session B policy corrupted"
