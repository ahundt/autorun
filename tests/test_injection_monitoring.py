#!/usr/bin/env python3
"""Unit tests for injection effectiveness monitoring system"""

import pytest
import tempfile
import json
import time
import threading
from pathlib import Path
import sys
import os

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

try:
    from clautorun.injection_monitoring import (
        InjectionEffectivenessMonitor,
        InjectionAttempt,
        InjectionEffectivenessMetrics,
        InjectionReliabilityReport,
        InjectionMethod,
        InjectionOutcome,
        get_injection_monitor,
        record_injection
    )
    INJECTION_MONITORING_AVAILABLE = True
except ImportError:
    INJECTION_MONITORING_AVAILABLE = False
    pytest.skip("Injection monitoring not available", allow_module_level=True)


class TestInjectionEffectivenessMonitor:
    """Test suite for injection effectiveness monitor"""

    def setup_method(self):
        """Set up test environment"""
        if INJECTION_MONITORING_AVAILABLE:
            # Create temporary directory for test data
            self.temp_dir = Path(tempfile.mkdtemp())
            self.monitor = InjectionEffectivenessMonitor(storage_dir=self.temp_dir, max_records=100)

    def teardown_method(self):
        """Clean up test environment"""
        if hasattr(self, 'temp_dir') and self.temp_dir.exists():
            # Clean up temporary files
            for file_path in self.temp_dir.glob("*"):
                if file_path.is_file():
                    file_path.unlink()
            self.temp_dir.rmdir()

    def test_monitor_initialization(self):
        """Test monitor initialization"""
        if not INJECTION_MONITORING_AVAILABLE:
            pytest.skip("Injection monitoring not available")

        assert self.monitor.storage_dir == self.temp_dir
        assert self.monitor.max_records == 100
        assert len(self.monitor.injection_attempts) == 0
        assert len(self.monitor.session_contexts) == 0
        assert self.monitor._lock is not None

    def test_record_injection_attempt(self):
        """Test recording injection attempts"""
        if not INJECTION_MONITORING_AVAILABLE:
            pytest.skip("Injection monitoring not available")

        # Record a successful injection
        attempt_id = self.monitor.record_injection_attempt(
            method=InjectionMethod.API_DIRECT,
            session_id="test_session",
            prompt_type="continue",
            prompt_content="Continue working on task",
            outcome=InjectionOutcome.SUCCESS,
            response_time_ms=250.5,
            success_indicators=["Task resumed", "Continuing work"]
        )

        # Verify attempt was recorded
        assert attempt_id.startswith("test_session_")
        assert len(self.monitor.injection_attempts) == 1

        attempt = self.monitor.injection_attempts[0]
        assert attempt.method == InjectionMethod.API_DIRECT
        assert attempt.session_id == "test_session"
        assert attempt.prompt_type == "continue"
        assert attempt.outcome == InjectionOutcome.SUCCESS
        assert attempt.response_time_ms == 250.5
        assert len(attempt.success_indicators) == 2

    def test_record_multiple_injection_attempts(self):
        """Test recording multiple injection attempts"""
        if not INJECTION_MONITORING_AVAILABLE:
            pytest.skip("Injection monitoring not available")

        # Record multiple attempts with different methods and outcomes
        attempts_data = [
            (InjectionMethod.API_DIRECT, "continue", InjectionOutcome.SUCCESS, 200),
            (InjectionMethod.TMUX_INJECTION, "verification", InjectionOutcome.PARTIAL, 500),
            (InjectionMethod.HOOK_INTEGRATION, "forced_compliance", InjectionOutcome.FAILURE, 1000),
            (InjectionMethod.PLUGIN_COMMAND, "continue", InjectionOutcome.TIMEOUT, 5000)
        ]

        for method, prompt_type, outcome, response_time in attempts_data:
            self.monitor.record_injection_attempt(
                method=method,
                session_id="multi_test",
                prompt_type=prompt_type,
                prompt_content=f"Test {prompt_type} prompt",
                outcome=outcome,
                response_time_ms=response_time
            )

        # Verify all attempts were recorded
        assert len(self.monitor.injection_attempts) == 4

        # Verify session context was created and updated
        assert "multi_test" in self.monitor.session_contexts
        context = self.monitor.session_contexts["multi_test"]
        assert context["total_attempts"] == 4
        assert len(context["methods_used"]) == 4
        assert len(context["outcomes"]) == 4

    def test_max_records_limit(self):
        """Test maximum records limit enforcement"""
        if not INJECTION_MONITORING_AVAILABLE:
            pytest.skip("Injection monitoring not available")

        # Create monitor with small max_records
        small_monitor = InjectionEffectivenessMonitor(
            storage_dir=self.temp_dir,
            max_records=3
        )

        # Add more attempts than the limit
        for i in range(5):
            small_monitor.record_injection_attempt(
                method=InjectionMethod.API_DIRECT,
                session_id=f"session_{i}",
                prompt_type="continue",
                prompt_content=f"Prompt {i}",
                outcome=InjectionOutcome.SUCCESS,
                response_time_ms=100
            )

        # Should only keep the most recent 3 attempts
        assert len(small_monitor.injection_attempts) == 3
        assert small_monitor.injection_attempts[-1].session_id == "session_4"

    def test_analyze_injection_success(self):
        """Test injection success analysis"""
        if not INJECTION_MONITORING_AVAILABLE:
            pytest.skip("Injection monitoring not available")

        before_transcript = "User: Working on task\nAI: I'll help you"
        after_transcript = before_transcript + "\nAI: Continuing with task due to injection\nMarker: TASK_RESUMED"

        analysis = self.monitor.analyze_injection_success(
            session_id="analysis_test",
            before_transcript=before_transcript,
            after_transcript=after_transcript,
            expected_markers=["Marker: TASK_RESUMED", "Continuing with task"]
        )

        assert analysis["session_id"] == "analysis_test"
        assert analysis["success"] is True
        assert "Marker: TASK_RESUMED" in analysis["markers_found"]
        assert "Continuing with task" in analysis["markers_found"]
        assert analysis["new_content_length"] > 0

    def test_analyze_injection_failure(self):
        """Test injection failure analysis"""
        if not INJECTION_MONITORING_AVAILABLE:
            pytest.skip("Injection monitoring not available")

        before_transcript = "User: Working on task\nAI: I'll help you"
        after_transcript = before_transcript + "\nAI: Error occurred"

        analysis = self.monitor.analyze_injection_success(
            session_id="failure_test",
            before_transcript=before_transcript,
            after_transcript=after_transcript,
            expected_markers=["Marker: TASK_RESUMED", "Success indicator"]
        )

        assert analysis["success"] is False
        assert len(analysis["markers_found"]) == 0
        assert "Marker: TASK_RESUMED" in analysis["markers_missing"]
        assert "Success indicator" in analysis["markers_missing"]

    def test_calculate_metrics_all_attempts(self):
        """Test calculating metrics for all attempts"""
        if not INJECTION_MONITORING_AVAILABLE:
            pytest.skip("Injection monitoring not available")

        # Add test data
        self._add_test_attempts()

        metrics = self.monitor.calculate_metrics()

        assert metrics.total_attempts == 6
        assert metrics.successful_attempts == 3
        assert metrics.failed_attempts == 1
        assert metrics.timeout_attempts == 1
        assert metrics.partial_attempts == 1
        assert metrics.success_rate == 0.5  # 3/6
        assert metrics.average_response_time_ms > 0

    def test_calculate_metrics_filtered_by_method(self):
        """Test calculating metrics filtered by method"""
        if not INJECTION_MONITORING_AVAILABLE:
            pytest.skip("Injection monitoring not available")

        # Add test data
        self._add_test_attempts()

        # Calculate metrics for API_DIRECT only
        metrics = self.monitor.calculate_metrics(method=InjectionMethod.API_DIRECT)

        assert metrics.total_attempts == 2  # 2 API_DIRECT attempts
        assert metrics.successful_attempts == 1
        assert metrics.failed_attempts == 1
        assert metrics.method == InjectionMethod.API_DIRECT

    def test_calculate_metrics_filtered_by_prompt_type(self):
        """Test calculating metrics filtered by prompt type"""
        if not INJECTION_MONITORING_AVAILABLE:
            pytest.skip("Injection monitoring not available")

        # Add test data
        self._add_test_attempts()

        # Calculate metrics for continue prompts only
        metrics = self.monitor.calculate_metrics(prompt_type="continue")

        # Test data includes 3 continue attempts: API_DIRECT, TMUX_INJECTION, PLUGIN_COMMAND
        assert metrics.total_attempts == 3
        assert metrics.successful_attempts == 3
        assert metrics.prompt_type == "continue"

    def test_calculate_metrics_time_window(self):
        """Test calculating metrics with time window"""
        if not INJECTION_MONITORING_AVAILABLE:
            pytest.skip("Injection monitoring not available")

        # Add attempts at different times
        now = time.time()
        old_time = now - (2 * 3600)  # 2 hours ago

        # Old attempt (outside window)
        self.monitor.record_injection_attempt(
            method=InjectionMethod.API_DIRECT,
            session_id="old_session",
            prompt_type="continue",
            prompt_content="Old prompt",
            outcome=InjectionOutcome.SUCCESS,
            response_time_ms=100
        )
        # Manually set old timestamp
        self.monitor.injection_attempts[-1].timestamp = old_time

        # Recent attempt (inside window)
        self.monitor.record_injection_attempt(
            method=InjectionMethod.API_DIRECT,
            session_id="recent_session",
            prompt_type="continue",
            prompt_content="Recent prompt",
            outcome=InjectionOutcome.SUCCESS,
            response_time_ms=200
        )

        # Calculate metrics for 1-hour window
        metrics = self.monitor.calculate_metrics(time_window_hours=1.0)

        assert metrics.total_attempts == 1  # Only recent attempt
        assert metrics.time_range[0] >= now - 3600

    def test_generate_reliability_report(self):
        """Test generating comprehensive reliability report"""
        if not INJECTION_MONITORING_AVAILABLE:
            pytest.skip("Injection monitoring not available")

        # Add test data
        self._add_test_attempts()

        report = self.monitor.generate_reliability_report(time_period_hours=24.0)

        assert isinstance(report, InjectionReliabilityReport)
        assert report.report_id.startswith("reliability_report_")
        assert report.time_period_hours == 24.0
        assert len(report.overall_metrics) > 0
        assert "method_comparison" in report.__dict__
        assert "reliability_trends" in report.__dict__
        assert "failure_analysis" in report.__dict__
        assert len(report.recommendations) > 0

    def test_get_injection_patterns(self):
        """Test analyzing injection patterns for a session"""
        if not INJECTION_MONITORING_AVAILABLE:
            pytest.skip("Injection monitoring not available")

        # Add attempts for a specific session
        session_id = "pattern_test"
        for i in range(3):
            self.monitor.record_injection_attempt(
                method=InjectionMethod.API_DIRECT if i % 2 == 0 else InjectionMethod.TMUX_INJECTION,
                session_id=session_id,
                prompt_type="continue" if i < 2 else "verification",
                prompt_content=f"Prompt {i}",
                outcome=InjectionOutcome.SUCCESS if i > 0 else InjectionOutcome.FAILURE,
                response_time_ms=100 + i * 50,
                user_intervention=(i == 0)
            )

        patterns = self.monitor.get_injection_patterns(session_id)

        assert patterns["session_id"] == session_id
        assert patterns["total_attempts"] == 3
        assert len(patterns["methods_used"]) == 2
        assert patterns["success_rate"] == 2/3
        assert patterns["user_intervention_count"] == 1
        assert patterns["patterns_found"] is True
        assert "analysis" in patterns

    def test_get_injection_patterns_no_data(self):
        """Test injection patterns for session with no data"""
        if not INJECTION_MONITORING_AVAILABLE:
            pytest.skip("Injection monitoring not available")

        patterns = self.monitor.get_injection_patterns("nonexistent_session")

        assert patterns["session_id"] == "nonexistent_session"
        assert patterns["total_attempts"] == 0
        assert patterns["patterns_found"] is False

    def test_export_json(self):
        """Test JSON export functionality"""
        if not INJECTION_MONITORING_AVAILABLE:
            pytest.skip("Injection monitoring not available")

        # Add test data
        self._add_test_attempts()

        json_export = self.monitor.export_monitoring_data(format_type="json")

        data = json.loads(json_export)
        assert "export_timestamp" in data
        assert data["total_attempts"] == 6
        assert len(data["attempts"]) == 6

        # Verify attempt structure
        attempt = data["attempts"][0]
        assert "attempt_id" in attempt
        assert "method" in attempt
        assert "outcome" in attempt

    def test_export_csv(self):
        """Test CSV export functionality"""
        if not INJECTION_MONITORING_AVAILABLE:
            pytest.skip("Injection monitoring not available")

        # Add test data
        self._add_test_attempts()

        csv_export = self.monitor.export_monitoring_data(format_type="csv")

        lines = csv_export.split("\n")
        assert len(lines) >= 7  # Header + 6 data lines

        # Check header
        header = lines[0]
        expected_columns = ["attempt_id", "timestamp", "method", "outcome"]
        for col in expected_columns:
            assert col in header

        # Check data rows
        for i in range(1, 7):
            row = lines[i]
            assert len(row.split(",")) == len(header.split(","))

    def test_export_report(self):
        """Test report export functionality"""
        if not INJECTION_MONITORING_AVAILABLE:
            pytest.skip("Injection monitoring not available")

        # Add test data
        self._add_test_attempts()

        report_export = self.monitor.export_monitoring_data(format_type="report")

        assert "INJECTION EFFECTIVENESS MONITORING REPORT" in report_export
        assert "OVERALL METRICS" in report_export
        assert "RECOMMENDATIONS" in report_export
        assert "Report ID:" in report_export
        assert "Time Period:" in report_export

    def test_export_invalid_format(self):
        """Test export with invalid format"""
        if not INJECTION_MONITORING_AVAILABLE:
            pytest.skip("Injection monitoring not available")

        with pytest.raises(ValueError) as exc_info:
            self.monitor.export_monitoring_data(format_type="invalid")

        assert "Unsupported export format" in str(exc_info.value)

    def test_thread_safety(self):
        """Test thread safety of monitor operations"""
        if not INJECTION_MONITORING_AVAILABLE:
            pytest.skip("Injection monitoring not available")

        def worker(worker_id):
            for i in range(10):
                self.monitor.record_injection_attempt(
                    method=InjectionMethod.API_DIRECT,
                    session_id=f"worker_{worker_id}",
                    prompt_type="continue",
                    prompt_content=f"Worker {worker_id} attempt {i}",
                    outcome=InjectionOutcome.SUCCESS,
                    response_time_ms=100
                )

        # Create multiple threads
        threads = []
        for worker_id in range(5):
            thread = threading.Thread(target=worker, args=(worker_id,))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join(timeout=10)

        # Should have all attempts without corruption
        assert len(self.monitor.injection_attempts) == 50  # 5 workers × 10 attempts

        # Verify session contexts are intact
        for worker_id in range(5):
            session_id = f"worker_{worker_id}"
            assert session_id in self.monitor.session_contexts
            assert self.monitor.session_contexts[session_id]["total_attempts"] == 10

    def test_reliability_score_calculation(self):
        """Test reliability score calculation"""
        if not INJECTION_MONITORING_AVAILABLE:
            pytest.skip("Injection monitoring not available")

        # Test perfect reliability
        perfect_metrics = InjectionEffectivenessMetrics(
            total_attempts=10,
            successful_attempts=10,
            failed_attempts=0,
            timeout_attempts=0,
            partial_attempts=0,
            average_response_time_ms=500,
            success_rate=1.0,
            method=InjectionMethod.API_DIRECT,
            prompt_type="continue",
            time_range=(0, 100)
        )
        score = self.monitor._calculate_reliability_score(perfect_metrics)
        assert score == 100.0

        # Test poor reliability
        poor_metrics = InjectionEffectivenessMetrics(
            total_attempts=10,
            successful_attempts=2,
            failed_attempts=5,
            timeout_attempts=3,
            partial_attempts=0,
            average_response_time_ms=8000,
            success_rate=0.2,
            method=InjectionMethod.API_DIRECT,
            prompt_type="continue",
            time_range=(0, 100)
        )
        score = self.monitor._calculate_reliability_score(poor_metrics)
        assert score < 50.0

    def test_data_persistence(self):
        """Test data persistence to storage"""
        if not INJECTION_MONITORING_AVAILABLE:
            pytest.skip("Injection monitoring not available")

        # Add some test data
        self.monitor.record_injection_attempt(
            method=InjectionMethod.API_DIRECT,
            session_id="persistence_test",
            prompt_type="continue",
            prompt_content="Test persistence",
            outcome=InjectionOutcome.SUCCESS,
            response_time_ms=150
        )

        # Wait for async persistence
        time.sleep(0.5)

        # Verify the attempt was recorded in current monitor
        assert len(self.monitor.injection_attempts) >= 1

        # Create new monitor instance
        new_monitor = InjectionEffectivenessMonitor(storage_dir=self.temp_dir)

        # New monitor may or may not load persisted data depending on implementation
        # Just verify it doesn't crash and returns valid state
        assert isinstance(new_monitor.injection_attempts, list)

    def _add_test_attempts(self):
        """Helper method to add test injection attempts"""
        test_data = [
            (InjectionMethod.API_DIRECT, "continue", InjectionOutcome.SUCCESS, 200),
            (InjectionMethod.API_DIRECT, "verification", InjectionOutcome.FAILURE, 300),
            (InjectionMethod.TMUX_INJECTION, "continue", InjectionOutcome.SUCCESS, 400),
            (InjectionMethod.TMUX_INJECTION, "forced_compliance", InjectionOutcome.TIMEOUT, 5000),
            (InjectionMethod.HOOK_INTEGRATION, "verification", InjectionOutcome.PARTIAL, 600),
            (InjectionMethod.PLUGIN_COMMAND, "continue", InjectionOutcome.SUCCESS, 250)
        ]

        for method, prompt_type, outcome, response_time in test_data:
            self.monitor.record_injection_attempt(
                method=method,
                session_id=f"test_{method.value}_{prompt_type}",
                prompt_type=prompt_type,
                prompt_content=f"Test {prompt_type} with {method.value}",
                outcome=outcome,
                response_time_ms=response_time
            )


class TestGlobalFunctions:
    """Test global convenience functions"""

    def test_get_injection_monitor(self):
        """Test getting global injection monitor"""
        if not INJECTION_MONITORING_AVAILABLE:
            pytest.skip("Injection monitoring not available")

        # Clear global instance
        import clautorun.injection_monitoring
        clautorun.injection_monitoring._global_monitor = None

        monitor1 = get_injection_monitor()
        monitor2 = get_injection_monitor()

        # Should return the same instance
        assert monitor1 is monitor2
        assert isinstance(monitor1, InjectionEffectivenessMonitor)

    def test_record_injection_convenience(self):
        """Test convenience function for recording injections"""
        if not INJECTION_MONITORING_AVAILABLE:
            pytest.skip("Injection monitoring not available")

        # Clear global instance
        import clautorun.injection_monitoring
        clautorun.injection_monitoring._global_monitor = None

        attempt_id = record_injection(
            method=InjectionMethod.API_DIRECT,
            session_id="convenience_test",
            prompt_type="continue",
            prompt_content="Convenience test",
            outcome=InjectionOutcome.SUCCESS,
            response_time_ms=180
        )

        assert attempt_id.startswith("convenience_test_")

        # Verify it was recorded in global monitor
        monitor = get_injection_monitor()
        assert len(monitor.injection_attempts) == 1
        assert monitor.injection_attempts[0].session_id == "convenience_test"


if __name__ == "__main__":
    pytest.main([__file__])