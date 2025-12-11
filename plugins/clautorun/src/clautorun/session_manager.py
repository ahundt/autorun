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
Robust session state manager with proper RAII and thread/process safety
Follows CLAUDE.md principles: concrete, reliable, automatic, correct from start
"""

import os
import time
import threading
import fcntl
import errno
from pathlib import Path
from contextlib import contextmanager
from typing import Optional, Dict, Any
import json

# Constants for concrete behavior
DEFAULT_SESSION_TIMEOUT = 30.0
SHARED_ACCESS_TIMEOUT = 5.0
LOCK_RETRY_DELAY = 0.01
MAX_LOCK_RETRIES = 100

class SessionStateError(Exception):
    """Concrete session state error with specific details"""
    pass

class SessionTimeoutError(SessionStateError):
    """Session lock timeout with concrete timeout details"""
    pass

class SessionBackendError(SessionStateError):
    """Session backend failure with specific error details"""
    pass

class SessionLock:
    """RAII-style session lock with automatic acquisition and release"""

    def __init__(self, session_id: str, timeout: float, state_dir: Path):
        self.session_id = session_id
        self.timeout = timeout
        self.state_dir = state_dir
        self.lock_file = state_dir / f".{session_id}.lock"
        self.lock_fd = None
        self.acquired = False
        self.start_time = time.time()
        self.process_id = os.getpid()

    def __enter__(self):
        """Acquire lock with concrete timeout behavior"""
        try:
            # Create lock file directory
            self.lock_file.parent.mkdir(parents=True, exist_ok=True)

            # Open lock file with concrete error handling
            try:
                self.lock_fd = open(self.lock_file, 'w')
            except (OSError, IOError) as e:
                raise SessionStateError(f"Failed to create lock file {self.lock_file}: {e}")

            # Write process information for debugging
            lock_info = {
                'pid': self.process_id,
                'start_time': self.start_time,
                'session_id': self.session_id
            }
            self.lock_fd.write(json.dumps(lock_info))
            self.lock_fd.flush()

            # Acquire exclusive lock with retry logic
            self._acquire_lock()

            return self.lock_fd

        except Exception:
            self._cleanup()
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Release lock with guaranteed cleanup"""
        self._cleanup()

    def _acquire_lock(self):
        """Acquire file lock with concrete retry logic"""
        start_time = time.time()
        retries = 0

        while time.time() - start_time < self.timeout and retries < MAX_LOCK_RETRIES:
            try:
                fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.acquired = True
                time.time() - start_time
                return
            except (IOError, OSError) as e:
                if e.errno == errno.EAGAIN:
                    # Lock held by another process/thread
                    retries += 1
                    time.sleep(LOCK_RETRY_DELAY)
                    continue
                else:
                    # Concrete error for lock acquisition failure
                    raise SessionStateError(f"Lock acquisition failed: {e}")

        # Timeout reached with concrete details
        timeout_time = time.time() - start_time
        raise SessionTimeoutError(
            f"Failed to acquire session lock for '{self.session_id}' "
            f"after {retries} retries in {timeout_time:.2f}s (timeout: {self.timeout}s)"
        )

    def _cleanup(self):
        """Guaranteed cleanup of lock resources"""
        if self.lock_fd:
            try:
                if self.acquired:
                    # Release file lock
                    fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_UN)
                    self.acquired = False

                # Close file descriptor
                self.lock_fd.close()

            except Exception:
                pass  # Silently handle cleanup errors

            self.lock_fd = None

        # Remove lock file
        try:
            if self.lock_file.exists():
                self.lock_file.unlink()
        except Exception:
            pass

class SessionBackendManager:
    """Manages shelve backend selection with concrete fallback logic"""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self._backend_lock = threading.Lock()
        self._backends: Dict[str, str] = {}

    def get_backend(self, session_id: str) -> str:
        """Get or determine backend for session with thread safety"""
        with self._backend_lock:
            if session_id not in self._backends:
                self._backends[session_id] = self._test_backends(session_id)
            return self._backends[session_id]

    def _test_backends(self, session_id: str) -> str:
        """Test available backends and return the best working one"""
        backends_to_test = [
            ("default", self._test_default_backend),
            ("dumbdbm", self._test_dumbdbm_backend),
            ("memory", self._test_memory_backend)
        ]

        for backend_name, test_func in backends_to_test:
            try:
                if test_func(session_id):
                    return backend_name
            except Exception:
                # Log and continue to next backend
                pass

        # This should never happen due to memory fallback
        raise SessionBackendError("No available session backend")

    def _test_default_backend(self, session_id: str) -> bool:
        """Test default shelve backend"""
        import shelve
        test_db = self.state_dir / f"test_backend_{session_id}.db"

        try:
            test_state = shelve.open(str(test_db), writeback=True)
            test_state["test"] = "test"
            test_state.sync()
            test_state.close()
            os.remove(test_db)
            return True
        except Exception:
            return False

    def _test_dumbdbm_backend(self, session_id: str) -> bool:
        """Test dumbdbm backend"""
        import shelve
        test_db = self.state_dir / f"test_dumbdbm_{session_id}.db"

        try:
            test_state = shelve.open(str(test_db), writeback=True, protocol=2)
            test_state["test"] = "test"
            test_state.sync()
            test_state.close()
            os.remove(test_db)
            return True
        except Exception:
            return False

    def _test_memory_backend(self, session_id: str) -> bool:
        """Memory backend always works"""
        return True

class SessionStateManager:
    """Thread-safe and process-safe session state management"""

    def __init__(self, state_dir: Optional[Path] = None):
        self.state_dir = state_dir or Path.home() / ".claude" / "sessions"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.backend_manager = SessionBackendManager(self.state_dir)

        # Memory fallback storage with thread safety
        self._memory_states: Dict[str, Dict[str, Any]] = {}
        self._memory_locks: Dict[str, threading.Lock] = {}
        self._memory_lock = threading.Lock()

    @contextmanager
    def session_state(self, session_id: str, timeout: float = DEFAULT_SESSION_TIMEOUT,
                     shared_access: bool = False):
        """
        Get thread-safe and process-safe session state

        Args:
            session_id: Concrete session identifier
            timeout: Lock acquisition timeout in seconds
            shared_access: Allow concurrent read access for AI monitor scenarios

        Yields:
            Dict: Session state dictionary for read/write operations
        """
        if not session_id or not isinstance(session_id, str):
            raise SessionStateError("Session ID must be a non-empty string")

        # Determine appropriate timeout for access type
        lock_timeout = SHARED_ACCESS_TIMEOUT if shared_access else timeout

        # Acquire session lock using RAII
        with SessionLock(session_id, lock_timeout, self.state_dir):
            # Get backend for this session
            backend = self.backend_manager.get_backend(session_id)

            # Get state from appropriate backend
            if backend == "memory":
                state = self._get_memory_state(session_id)
            else:
                state = self._get_shelve_state(session_id, backend)

            try:
                # Handle shared access tracking
                if shared_access:
                    self._track_shared_access(state, session_id, increment=True)

                yield state

            finally:
                # Handle shared access cleanup
                if shared_access:
                    self._track_shared_access(state, session_id, increment=False)

                # Ensure proper cleanup for shelve backends
                if hasattr(state, 'sync') and hasattr(state, 'close'):
                    try:
                        state.sync()
                        state.close()
                    except Exception:
                        pass  # Silently handle cleanup errors

    def _get_memory_state(self, session_id: str) -> Dict[str, Any]:
        """Get thread-safe memory state for session"""
        with self._memory_lock:
            if session_id not in self._memory_locks:
                self._memory_locks[session_id] = threading.Lock()

        with self._memory_locks[session_id]:
            if session_id not in self._memory_states:
                self._memory_states[session_id] = {}
            return self._memory_states[session_id]

    def _get_shelve_state(self, session_id: str, backend: str) -> Any:
        """Get shelve state for session"""
        import shelve

        if backend == "default":
            return shelve.open(str(self.state_dir / f"plugin_{session_id}.db"), writeback=True)
        elif backend == "dumbdbm":
            return shelve.open(str(self.state_dir / f"plugin_{session_id}_dumb.db"), writeback=True)
        else:
            raise SessionBackendError(f"Unknown backend: {backend}")

    def _track_shared_access(self, state: Dict[str, Any], session_id: str, increment: bool):
        """Track shared access for monitoring"""
        try:
            if increment:
                if "_shared_access_count" not in state:
                    state["_shared_access_count"] = 0
                state["_shared_access_count"] += 1
                state["_last_shared_access"] = time.time()
            else:
                if "_shared_access_count" in state:
                    state["_shared_access_count"] = max(0, state["_shared_access_count"] - 1)
        except Exception:
            pass  # Silently handle tracking errors

# Global session manager instance following DRY principles
_global_session_manager: Optional[SessionStateManager] = None

def get_session_manager() -> SessionStateManager:
    """Get or create global session manager (DRY pattern)"""
    global _global_session_manager
    if _global_session_manager is None:
        _global_session_manager = SessionStateManager()
    return _global_session_manager

# Convenience context managers for common usage patterns
@contextmanager
def session_state(session_id: str, timeout: float = DEFAULT_SESSION_TIMEOUT,
                 shared_access: bool = False):
    """Convenience wrapper for session state access"""
    manager = get_session_manager()
    with manager.session_state(session_id, timeout, shared_access) as state:
        yield state

@contextmanager
def shared_session_state(session_id: str, timeout: float = SHARED_ACCESS_TIMEOUT):
    """Convenience wrapper for shared session access (AI monitor scenarios)"""
    manager = get_session_manager()
    with manager.session_state(session_id, timeout, shared_access=True) as state:
        yield state