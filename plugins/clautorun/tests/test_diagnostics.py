#!/usr/bin/env python3
"""Unit tests for diagnostic and logging tools"""

import pytest
import tempfile
import json
import time
import threading
import os
from pathlib import Path
import sys

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

try:
    from clautorun.diagnostics import (
        DiagnosticLogger,
        SystemMonitor,
        HealthChecker,
        DiagnosticManager,
        LogLevel,
        HealthStatus,
        LogEntry,
        SystemMetric,
        HealthCheck
    )
    DIAGNOSTICS_AVAILABLE = True
except ImportError:
    DIAGNOSTICS_AVAILABLE = False
    pytest.skip("Diagnostics not available", allow_module_level=True)


class TestDiagnosticLogger:
    """Test suite for diagnostic logger"""

    def setup_method(self):
        """Set up test environment"""
        if DIAGNOSTICS_AVAILABLE:
            self.logger = DiagnosticLogger(max_entries=100)

    def test_basic_logging(self):
        """Test basic logging functionality"""
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

    def test_log_entry_creation(self):
        """Test log entry creation and structure"""
        if not DIAGNOSTICS_AVAILABLE:
            pytest("Diagnostics not available")

        timestamp = time.time()
        metadata = {"key": "value", "number": 42}

        self.logger.log(
            level=LogLevel.INFO,
            category="test_category",
            message="Test message",
            session_id="test_session",
            metadata=metadata
        )

        assert len(self.logger.logs) == 1
        entry = self.logger.logs[0]

        assert entry.level == LogLevel.INFO
        assert entry.category == "test_category"
        assert entry.message == "Test message"
        assert entry.session_id == "test_session"
        assert entry.metadata == metadata
        assert entry.timestamp >= timestamp
        assert entry.thread_id is not None
        assert entry.process_id == os.getpid()

    def test_log_to_dict_conversion(self):
        """Test log entry to dictionary conversion"""
        if not DIAGNOSTICS_AVAILABLE:
            pytest.skip("Diagnostics not available")

        self.logger.info("test", "Test message", "test_session", extra="data")
        entry = self.logger.logs[0]

        entry_dict = entry.to_dict()

        assert entry_dict["level"] == LogLevel.INFO.value
        assert entry_dict["category"] == "test"
        assert entry_dict["message"] == "Test message"
        assert entry_dict["session_id"] == "test_session"
        assert entry_dict["metadata"]["extra"] == "data"

    def test_log_retrieval_with_filters(self):
        """Test log retrieval with filters"""
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

    def test_session_summary_generation(self):
        """Test session summary generation"""
        if not DIAGNOSTICS_AVAILABLE:
            pytest.skip("Diagnostics not available")

        # Add logs for a session
        start_time = time.time()
        self.logger.info("test", "First message", "test_session")
        self.logger.error("test", "Second message", "test_session")
        self.logger.warning("test", "Third message", "test_session")

        summary = self.logger.get_session_summary("test_session")

        assert summary["total_logs"] == 3
        assert summary["level_distribution"]["INFO"] == 1
        assert summary["level_distribution"]["ERROR"] == 1
        assert summary["level_distribution"]["WARNING"] == 1
        assert summary["category_distribution"]["test"] == 3
        assert summary["first_log_time"] >= start_time
        assert summary["last_log_time"] >= start_time
        assert summary["duration"] > 0

    def test_log_max_entries_limit(self):
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

    def test_concurrent_logging_safety(self):
        """Test concurrent logging safety"""
        if not DIAGNOSTICS_AVAILABLE:
            pytest.skip("Diagnostics not available")

        # Create a logger with enough capacity for all concurrent messages
        concurrent_logger = DiagnosticLogger(max_entries=200)

        def log_worker(worker_id):
            for i in range(50):
                concurrent_logger.info(f"worker_{worker_id}", f"Message {i}", f"session_{worker_id}")

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
        assert len(concurrent_logger.logs) == 150  # 3 workers × 50 messages each

        # Check session distribution
        session_counts = {}
        for log in concurrent_logger.logs:
            session_id = log.session_id
            session_counts[session_id] = session_counts.get(session_id, 0) + 1

        assert len(session_counts) == 3
        assert all(count == 50 for count in session_counts.values())


class TestSystemMonitor:
    """Test suite for system monitor"""

    def setup_method(self):
        """Set up test environment"""
        if DIAGNOSTICS_AVAILABLE:
            self.logger = DiagnosticLogger()
            self.monitor = SystemMonitor(self.logger)

    def test_metric_collection(self):
        """Test metric collection functionality"""
        if not DIAGNOSTICS_AVAILABLE:
            pytest.skip("Diagnostics not available")

        # Collect metrics
        self.monitor.collect_metrics()

        # Should have collected some metrics
        assert len(self.monitor.metrics) > 0

        # Check for expected metric types
        metric_names = [m.name for m in self.monitor.metrics]
        expected_metrics = ["cpu_percent", "memory_percent", "disk_percent"]

        for expected_metric in expected_metrics:
            assert expected_metric in metric_names

        # Check metric structure
        for metric in self.monitor.metrics:
            assert isinstance(metric, SystemMetric)
            assert metric.name
            assert isinstance(metric.value, (int, float))
            assert metric.unit
            assert metric.timestamp > 0
            assert metric.category

    def test_metric_retrieval_with_filters(self):
        """Test metric retrieval with filters"""
        if not DIAGNOSTICS_AVAILABLE:
            pytest.skip("Diagnostics not available")

        # Collect some metrics first
        self.monitor.collect_metrics()

        # Test category filter
        system_metrics = self.monitor.get_metrics(category="system")
        assert all(m.category == "system" for m in system_metrics)

        # Test limit
        limited_metrics = self.monitor.get_metrics(limit=5)
        assert len(limited_metrics) <= 5

    def test_metric_summary_generation(self):
        """Test metric summary generation"""
        if not DIAGNOSTICS_AVAILABLE:
            pytest.skip("Diagnostics not available")

        # Collect some metrics
        for _ in range(3):
            self.monitor.collect_metrics()
            time.sleep(0.1)  # Small delay

        summary = self.monitor.get_metric_summary(hours=1)

        # Should have summary data
        assert isinstance(summary, dict)

        if "message" not in summary:  # Only check if we have metrics
            assert "cpu_percent" in summary
            cpu_summary = summary["cpu_percent"]
            assert "count" in cpu_summary
            assert "min" in cpu_summary
            assert "max" in cpu_summary
            assert "avg" in cpu_summary

    def test_monitoring_start_stop(self):
        """Test monitoring start and stop functionality"""
        if not DIAGNOSTICS_AVAILABLE:
            pytest.skip("Diagnostics not available")

        # Initially not monitoring
        assert self.monitor.monitoring is False

        # Start monitoring
        self.monitor.start_monitoring(interval=1)  # 1 second for testing
        assert self.monitor.monitoring is True
        assert self.monitor.monitor_interval == 1

        # Let it collect some metrics
        time.sleep(1.5)

        # Stop monitoring
        self.monitor.stop_monitoring()
        assert self.monitor.monitoring is False

        # Should have collected some metrics during monitoring
        assert len(self.monitor.metrics) > 0


class TestHealthChecker:
    """Test suite for health checker"""

    def setup_method(self):
        """Set up test environment"""
        if DIAGNOSTICS_AVAILABLE:
            self.logger = DiagnosticLogger()
            self.health_checker = HealthChecker(self.logger)

    def test_health_check_registration(self):
        """Test health check registration"""
        if not DIAGNOSTICS_AVAILABLE:
            pytest.skip("Diagnostics not available")

        def dummy_check():
            return HealthCheck(
                name="test_check",
                status=HealthStatus.HEALTHY,
                message="Test check passed",
                timestamp=time.time(),
                duration=0.1
            )

        self.health_checker.register_check("test_check", dummy_check, interval=300)

        # Should have registered the check
        assert len(self.health_checker.health_checks) == 1
        assert self.health_checker.health_checks[0]["name"] == "test_check"
        assert self.health_checker.health_checks[0]["interval"] == 300

    def test_health_check_execution(self):
        """Test health check execution"""
        if not DIAGNOSTICS_AVAILABLE:
            pytest.skip("Diagnostics not available")

        def test_check():
            return HealthCheck(
                name="test_check",
                status=HealthStatus.HEALTHY,
                message="System is healthy",
                timestamp=time.time(),
                duration=0.05
            )

        self.health_checker.register_check("test_check", test_check, interval=1)

        # Run the check
        results = self.health_checker.run_all_checks()

        assert "test_check" in results
        result = results["test_check"]
        assert result.status == HealthStatus.HEALTHY
        assert result.message == "System is healthy"
        assert result.duration == 0.05

    def test_health_check_failure_handling(self):
        """Test health check failure handling"""
        if not DIAGNOSTICS_AVAILABLE:
            pytest.skip("Diagnostics not available")

        def failing_check():
            raise Exception("Simulated failure")

        self.health_checker.register_check("failing_check", failing_check, interval=1)

        # Run the check
        results = self.health_checker.run_all_checks()

        assert "failing_check" in results
        result = results["failing_check"]
        assert result.status == HealthStatus.CRITICAL
        assert "failed" in result.message.lower()
        assert result.duration == 0

    def test_overall_health_calculation(self):
        """Test overall health status calculation"""
        if not DIAGNOSTICS_AVAILABLE:
            pytest.skip("Diagnostics not available")

        # Empty results should be UNKNOWN
        assert self.health_checker.get_overall_health() == HealthStatus.UNKNOWN

        # Register some checks with different statuses
        def healthy_check():
            return HealthCheck("healthy", HealthStatus.HEALTHY, "OK", time.time(), 0)
        def warning_check():
            return HealthCheck("warning", HealthStatus.WARNING, "Warning", time.time(), 0)
        def critical_check():
            return HealthCheck("critical", HealthStatus.CRITICAL, "Critical", time.time(), 0)

        self.health_checker.register_check("healthy", healthy_check, 1)
        self.health_checker.register_check("warning", warning_check, 1)
        self.health_checker.register_check("critical", critical_check, 1)

        # Run checks
        self.health_checker.run_all_checks()

        # Should be CRITICAL due to critical check
        assert self.health_checker.get_overall_health() == HealthStatus.CRITICAL


class TestDiagnosticManager:
    """Test suite for diagnostic manager"""

    def setup_method(self):
        """Set up test environment"""
        if DIAGNOSTICS_AVAILABLE:
            self.manager = DiagnosticManager()

    def test_manager_initialization(self):
        """Test diagnostic manager initialization"""
        if not DIAGNOSTICS_AVAILABLE:
            pytest.skip("Diagnostics not available")

        assert self.manager.logger is not None
        assert self.manager.monitor is not None
        assert self.manager.health_checker is not None

        # Should have default health checks registered
        assert len(self.manager.health_checker.health_checks) >= 3

    def test_manager_start_stop(self):
        """Test manager start and stop functionality"""
        if not DIAGNOSTICS_AVAILABLE:
            pytest.skip("Diagnostics not available")

        # Initially not monitoring
        assert self.manager.monitor.monitoring is False

        # Start diagnostics
        self.manager.start(monitor_interval=1)
        assert self.manager.monitor.monitoring is True

        # Let it run briefly
        time.sleep(1.5)

        # Stop diagnostics
        self.manager.stop()
        assert self.manager.monitor.monitoring is False

    def test_status_generation(self):
        """Test comprehensive status generation"""
        if not DIAGNOSTICS_AVAILABLE:
            pytest.skip("Diagnostics not available")

        status = self.manager.get_status()

        # Check status structure
        assert "overall_health" in status
        assert "timestamp" in status
        assert "health_checks" in status
        assert "monitoring" in status
        assert "logging" in status

        # Check monitoring info
        monitoring = status["monitoring"]
        assert "active" in monitoring
        assert "interval" in monitoring
        assert "metrics_count" in monitoring

        # Check logging info
        logging = status["logging"]
        assert "total_logs" in logging
        assert "active_sessions" in logging

    def test_diagnostics_export(self):
        """Test diagnostic data export"""
        if not DIAGNOSTICS_AVAILABLE:
            pytest.skip("Diagnostics not available")

        # Create temporary file for export
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as temp_file:
            temp_path = temp_file.name

        try:
            # Export diagnostics
            exported_file = self.manager.export_diagnostics(temp_path)
            assert exported_file == temp_path

            # Verify file exists and has content
            assert Path(temp_path).exists()
            with open(temp_path, 'r') as f:
                data = json.load(f)

            # Check exported data structure
            assert "export_timestamp" in data
            assert "status" in data
            assert "recent_logs" in data
            assert "recent_metrics" in data

        finally:
            # Clean up
            if Path(temp_path).exists():
                os.unlink(temp_path)


if __name__ == "__main__":
    pytest.main([__file__])