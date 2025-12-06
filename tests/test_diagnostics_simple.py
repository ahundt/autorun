#!/usr/bin/env python3
"""Simplified diagnostic tests without external dependencies"""

import pytest
import tempfile
import json
import time
import threading
import os
import sys

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

try:
    from clautorun.diagnostics import (
        DiagnosticLogger,
        LogLevel,
        HealthStatus,
        LogEntry
    )
    DIAGNOSTICS_AVAILABLE = True
except ImportError as e:
    print(f"Import error: {e}")
    DIAGNOSTICS_AVAILABLE = False
    pytest.skip("Diagnostics not available", allow_module_level=True)


class TestDiagnosticLoggerSimple:
    """Simplified test suite for diagnostic logger"""

    def setup_method(self):
        """Set up test environment"""
        if DIAGNOSTICS_AVAILABLE:
            self.logger = DiagnosticLogger(max_entries=100)

    def test_basic_logging(self):
        """Test basic logging functionality without psutil"""
        if not DIAGNOSTICS_AVAILABLE:
            pytest.skip("Diagnostics not available")

        # Test different log levels
        self.logger.debug("test", "Debug message", "test_session")
        self.logger.info("test", "Info message", "test_session")
        self.logger.warning("test", "Warning message", "test_session")
        self.logger.error("test", "Error message", "test_session")
        self.logger.critical("test", "Critical message", "test_session")

        # Should have 5 entries
        assert len(self.logger.logs) == 5

        # Check level distribution
        level_counts = self.logger.category_counts
        assert level_counts["test"] == 5

    def test_log_retrieval(self):
        """Test log retrieval functionality"""
        if not DIAGNOSTICS_AVAILABLE:
            pytest.skip("Diagnostics not available")

        # Add different types of logs
        self.logger.debug("debug_cat", "Debug message", "session1")
        self.logger.info("info_cat", "Info message", "session1")
        self.logger.error("error_cat", "Error message", "session2")
        self.logger.warning("info_cat", "Warning message", "session1")

        # Test session filter
        session1_logs = self.logger.get_logs(session_id="session1")
        assert len(session1_logs) == 3
        assert all(log.session_id == "session1" for log in session1_logs)

        # Test level filter
        error_logs = self.logger.get_logs(level=LogLevel.ERROR)
        assert len(error_logs) == 1
        assert error_logs[0].level == LogLevel.ERROR

        # Test category filter
        info_cat_logs = self.logger.get_logs(category="info_cat")
        assert len(info_cat_logs) == 2
        assert all(log.category == "info_cat" for log in info_cat_logs)

        # Test limit
        limited_logs = self.logger.get_logs(limit=2)
        assert len(limited_logs) == 2

    def test_concurrent_logging(self):
        """Test concurrent logging safety"""
        if not DIAGNOSTICS_AVAILABLE:
            pytest.skip("Diagnostics not available")

        def log_worker(worker_id):
            for i in range(10):
                self.logger.info(f"worker_{worker_id}", f"Message {i}", f"session_{worker_id}")

        # Create multiple threads
        threads = []
        for worker_id in range(3):
            thread = threading.Thread(target=log_worker, args=(worker_id,))
            threads.append(thread)
            thread.start()

        # Wait for all threads
        for thread in threads:
            thread.join(timeout=10)

        # Should have all logs without corruption
        assert len(self.logger.logs) == 30  # 3 workers × 10 messages each

        # Check session distribution
        session_counts = {}
        for log in self.logger.logs:
            session_id = log.session_id
            session_counts[session_id] = session_counts.get(session_id, 0) + 1

        assert len(session_counts) == 3
        assert all(count == 10 for count in session_counts.values())

    def test_log_file_output(self):
        """Test log file output functionality"""
        if not DIAGNOSTICS_AVAILABLE:
            pytest.skip("Diagnostics not available")

        # Create a temporary file for testing log output
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.log', delete=False) as temp_file:
            temp_path = temp_file.name

            # Write some test data
            test_entry = LogEntry(
                timestamp=time.time(),
                level=LogLevel.INFO,
                category="test_file",
                message="Test file logging",
                session_id="file_test"
            )

            # Should be able to write JSON
            json_line = json.dumps(test_entry.to_dict())
            temp_file.write(f"{json_line}\n")
            temp_file.flush()

        # Read back the file (reopened for reading)
        with open(temp_path, 'r') as f:
            content = f.read()
            assert "Test file logging" in content

        # Cleanup
        import os
        os.unlink(temp_path)

    def test_max_entries_limit(self):
        """Test log maximum entries limit"""
        if not DIAGNOSTICS_AVAILABLE:
            pytest.skip("Diagnostics not available")

        # Create logger with small max_entries
        small_logger = DiagnosticLogger(max_entries=5)

        # Add more logs than the limit
        for i in range(10):
            small_logger.info("test", f"Message {i}", "test_session")

        # Should only keep the most recent 5
        assert len(small_logger.logs) == 5
        assert small_logger.logs[-1].message == "Message 9"


if __name__ == "__main__":
    pytest.main([__file__])