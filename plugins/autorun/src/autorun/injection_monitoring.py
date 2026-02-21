#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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
"""Injection effectiveness monitoring system for autorun"""

import time
import json
import threading
import statistics
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum
from pathlib import Path
import sys
import os

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

try:
    from .diagnostics import DiagnosticLogger, LogLevel
    from .transcript_analyzer import TranscriptAnalyzer, ConfidenceLevel
    DIAGNOSTICS_AVAILABLE = True
    TRANSCRIPT_ANALYZER_AVAILABLE = True
except ImportError:
    # Fallback for when running as script
    try:
        from diagnostics import DiagnosticLogger, LogLevel
        from transcript_analyzer import TranscriptAnalyzer, ConfidenceLevel
        DIAGNOSTICS_AVAILABLE = True
        TRANSCRIPT_ANALYZER_AVAILABLE = True
    except ImportError:
        DIAGNOSTICS_AVAILABLE = False
        TRANSCRIPT_ANALYZER_AVAILABLE = False


class InjectionMethod(Enum):
    """Methods of prompt injection"""
    API_DIRECT = "api_direct"
    TMUX_INJECTION = "tmux_injection"
    HOOK_INTEGRATION = "hook_integration"
    PLUGIN_COMMAND = "plugin_command"


class InjectionOutcome(Enum):
    """Possible outcomes of injection attempts"""
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    PARTIAL = "partial"
    REJECTED = "rejected"


@dataclass
class InjectionAttempt:
    """Record of a single injection attempt"""
    attempt_id: str
    timestamp: float
    method: InjectionMethod
    session_id: str
    prompt_type: str  # 'continue', 'verification', 'forced_compliance'
    prompt_content: str
    outcome: InjectionOutcome
    response_time_ms: float
    success_indicators: List[str]
    error_message: Optional[str] = None
    context_size: int = 0
    transcript_length: int = 0
    follow_up_required: bool = False
    user_intervention: bool = False


@dataclass
class InjectionEffectivenessMetrics:
    """Metrics for injection effectiveness"""
    total_attempts: int
    successful_attempts: int
    failed_attempts: int
    timeout_attempts: int
    partial_attempts: int
    average_response_time_ms: float
    success_rate: float
    method: InjectionMethod
    prompt_type: str
    time_range: Tuple[float, float]  # (start_time, end_time)


@dataclass
class InjectionReliabilityReport:
    """Comprehensive reliability report"""
    report_id: str
    generated_at: float
    time_period_hours: float
    overall_metrics: Dict[str, InjectionEffectivenessMetrics]
    method_comparison: Dict[str, Any]
    reliability_trends: List[Dict[str, Any]]
    failure_analysis: Dict[str, Any]
    recommendations: List[str]


class InjectionEffectivenessMonitor:
    """Monitor and analyze injection effectiveness"""

    def __init__(self, storage_dir: Optional[Path] = None, max_records: int = 10000):
        """Initialize injection effectiveness monitor

        Args:
            storage_dir: Directory to store monitoring data
            max_records: Maximum number of injection records to keep in memory
        """
        self.storage_dir = storage_dir or Path.home() / ".claude" / "autorun" / "injection_monitoring"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.max_records = max_records
        self.injection_attempts: List[InjectionAttempt] = []
        self.session_contexts: Dict[str, Dict[str, Any]] = {}

        # Thread safety
        self._lock = threading.RLock()

        # Initialize diagnostic logger
        if DIAGNOSTICS_AVAILABLE:
            self.logger = DiagnosticLogger(
                log_file_path=self.storage_dir / "injection_monitoring.log"
            )
        else:
            self.logger = None

        # Initialize transcript analyzer for outcome validation
        if TRANSCRIPT_ANALYZER_AVAILABLE:
            self.transcript_analyzer = TranscriptAnalyzer()
        else:
            self.transcript_analyzer = None

        self._load_historical_data()

    def record_injection_attempt(self,
                               method,
                               session_id: str,
                               prompt_type: str,
                               prompt_content: str,
                               outcome,
                               response_time_ms: float,
                               success_indicators: List[str] = None,
                               error_message: Optional[str] = None,
                               context_size: int = 0,
                               transcript_length: int = 0,
                               follow_up_required: bool = False,
                               user_intervention: bool = False) -> str:
        """Record a single injection attempt

        Args:
            method: The injection method used
            session_id: Session identifier
            prompt_type: Type of prompt injected
            prompt_content: The actual prompt content
            outcome: Result of the injection attempt
            response_time_ms: Time taken for the injection in milliseconds
            success_indicators: List of indicators showing success
            error_message: Error message if injection failed
            context_size: Size of the context at injection time
            transcript_length: Length of transcript at injection time
            follow_up_required: Whether follow-up injection was needed
            user_intervention: Whether user intervention was required

        Returns:
            Unique attempt identifier
        """
        # Convert string values to enums if needed for backward compatibility
        if isinstance(method, str):
            try:
                method = InjectionMethod(method)
            except ValueError:
                # If invalid string, default to API_DIRECT
                method = InjectionMethod.API_DIRECT
                if self.logger:
                    self.logger.warning(
                        "enum_conversion",
                        f"Invalid method string '{method}', defaulting to API_DIRECT",
                        session_id
                    )

        if isinstance(outcome, str):
            try:
                outcome = InjectionOutcome(outcome)
            except ValueError:
                # If invalid string, default to FAILURE
                outcome = InjectionOutcome.FAILURE
                if self.logger:
                    self.logger.warning(
                        "enum_conversion",
                        f"Invalid outcome string '{outcome}', defaulting to FAILURE",
                        session_id
                    )

        attempt_id = f"{session_id}_{int(time.time() * 1000)}"

        attempt = InjectionAttempt(
            attempt_id=attempt_id,
            timestamp=time.time(),
            method=method,
            session_id=session_id,
            prompt_type=prompt_type,
            prompt_content=prompt_content,
            outcome=outcome,
            response_time_ms=response_time_ms,
            success_indicators=success_indicators or [],
            error_message=error_message,
            context_size=context_size,
            transcript_length=transcript_length,
            follow_up_required=follow_up_required,
            user_intervention=user_intervention
        )

        with self._lock:
            self.injection_attempts.append(attempt)

            # Maintain maximum records
            if len(self.injection_attempts) > self.max_records:
                self.injection_attempts = self.injection_attempts[-self.max_records:]

            # Update session context
            if session_id not in self.session_contexts:
                self.session_contexts[session_id] = {
                    "first_attempt": attempt.timestamp,
                    "last_attempt": attempt.timestamp,
                    "total_attempts": 0,
                    "methods_used": set(),
                    "outcomes": []
                }

            context = self.session_contexts[session_id]
            context["last_attempt"] = attempt.timestamp
            context["total_attempts"] += 1
            context["methods_used"].add(method.value)
            context["outcomes"].append(outcome.value)

        # Log the attempt
        if self.logger:
            self.logger.info(
                "injection_attempt",
                f"Injection recorded: {method.value} {prompt_type} -> {outcome.value} ({response_time_ms:.1f}ms)",
                session_id,
                metadata={
                    "attempt_id": attempt_id,
                    "method": method.value,
                    "prompt_type": prompt_type,
                    "outcome": outcome.value,
                    "response_time_ms": response_time_ms,
                    "error_message": error_message
                }
            )

        # Persist data asynchronously
        self._persist_data_async()

        return attempt_id

    def analyze_injection_success(self,
                                 session_id: str,
                                 before_transcript: str,
                                 after_transcript: str,
                                 expected_markers: List[str]) -> Dict[str, Any]:
        """Analyze whether injection was successful by comparing transcripts

        Args:
            session_id: Session identifier
            before_transcript: Transcript before injection
            after_transcript: Transcript after injection
            expected_markers: Markers that should appear after successful injection

        Returns:
            Analysis results with success determination
        """
        analysis = {
            "session_id": session_id,
            "timestamp": time.time(),
            "success": False,
            "markers_found": [],
            "markers_missing": [],
            "new_content_length": len(after_transcript) - len(before_transcript),
            "evidence_indicators": []
        }

        # Check for expected markers
        for marker in expected_markers:
            if marker in after_transcript and marker not in before_transcript:
                analysis["markers_found"].append(marker)
                analysis["success"] = True
            elif marker not in after_transcript:
                analysis["markers_missing"].append(marker)

        # Use transcript analyzer if available for deeper analysis
        if self.transcript_analyzer:
            try:
                # Analyze the difference transcript
                diff_transcript = after_transcript[len(before_transcript):]
                if diff_transcript.strip():
                    transcript_analysis = self.transcript_analyzer.analyze_full_transcript(
                        diff_transcript, session_id
                    )

                    analysis["evidence_indicators"] = [
                        evidence.content for evidence in
                        transcript_analysis.evidence_by_type.get(
                            "success_indicator", []
                        )[:3]  # Top 3 indicators
                    ]

                    # High confidence evidence strengthens success determination
                    if transcript_analysis.confidence_score > 0.7:
                        analysis["success"] = True

            except Exception as e:
                if self.logger:
                    self.logger.warning(
                        "transcript_analysis",
                        f"Transcript analysis failed: {e}",
                        session_id
                    )

        # Log analysis results
        if self.logger:
            self.logger.debug(
                "injection_analysis",
                f"Injection success analysis: {analysis['success']}",
                session_id,
                metadata=analysis
            )

        return analysis

    def calculate_metrics(self,
                         method: Optional[InjectionMethod] = None,
                         prompt_type: Optional[str] = None,
                         time_window_hours: Optional[float] = None) -> InjectionEffectivenessMetrics:
        """Calculate effectiveness metrics for filtered injection attempts

        Args:
            method: Filter by injection method
            prompt_type: Filter by prompt type
            time_window_hours: Only consider attempts within this time window

        Returns:
            Calculated metrics
        """
        with self._lock:
            filtered_attempts = self._filter_attempts(method, prompt_type, time_window_hours)

            if not filtered_attempts:
                return InjectionEffectivenessMetrics(
                    total_attempts=0,
                    successful_attempts=0,
                    failed_attempts=0,
                    timeout_attempts=0,
                    partial_attempts=0,
                    average_response_time_ms=0.0,
                    success_rate=0.0,
                    method=method or InjectionMethod.API_DIRECT,
                    prompt_type=prompt_type or "all",
                    time_range=(0.0, 0.0)
                )

            # Count outcomes
            successful = sum(1 for a in filtered_attempts if a.outcome == InjectionOutcome.SUCCESS)
            failed = sum(1 for a in filtered_attempts if a.outcome == InjectionOutcome.FAILURE)
            timeout = sum(1 for a in filtered_attempts if a.outcome == InjectionOutcome.TIMEOUT)
            partial = sum(1 for a in filtered_attempts if a.outcome == InjectionOutcome.PARTIAL)

            # Calculate response time statistics
            response_times = [a.response_time_ms for a in filtered_attempts if a.response_time_ms > 0]
            avg_response_time = statistics.mean(response_times) if response_times else 0.0

            # Calculate time range
            timestamps = [a.timestamp for a in filtered_attempts]
            time_range = (min(timestamps), max(timestamps))

            return InjectionEffectivenessMetrics(
                total_attempts=len(filtered_attempts),
                successful_attempts=successful,
                failed_attempts=failed,
                timeout_attempts=timeout,
                partial_attempts=partial,
                average_response_time_ms=avg_response_time,
                success_rate=successful / len(filtered_attempts) if filtered_attempts else 0.0,
                method=method or InjectionMethod.API_DIRECT,
                prompt_type=prompt_type or "all",
                time_range=time_range
            )

    def generate_reliability_report(self, time_period_hours: float = 24.0) -> InjectionReliabilityReport:
        """Generate comprehensive reliability report

        Args:
            time_period_hours: Time period to analyze in hours

        Returns:
            Comprehensive reliability report
        """
        report_id = f"reliability_report_{int(time.time())}"
        generated_at = time.time()

        # Calculate overall metrics by method and prompt type
        overall_metrics = {}

        for method in InjectionMethod:
            for prompt_type in ['continue', 'verification', 'forced_compliance', 'all']:
                metrics = self.calculate_metrics(method, prompt_type, time_period_hours)
                if metrics.total_attempts > 0:
                    key = f"{method.value}_{prompt_type}"
                    overall_metrics[key] = metrics

        # Method comparison analysis
        method_comparison = self._compare_methods(time_period_hours)

        # Reliability trends
        reliability_trends = self._calculate_reliability_trends(time_period_hours)

        # Failure analysis
        failure_analysis = self._analyze_failures(time_period_hours)

        # Generate recommendations
        recommendations = self._generate_recommendations(overall_metrics, method_comparison, failure_analysis)

        return InjectionReliabilityReport(
            report_id=report_id,
            generated_at=generated_at,
            time_period_hours=time_period_hours,
            overall_metrics=overall_metrics,
            method_comparison=method_comparison,
            reliability_trends=reliability_trends,
            failure_analysis=failure_analysis,
            recommendations=recommendations
        )

    def get_injection_patterns(self, session_id: str) -> Dict[str, Any]:
        """Analyze injection patterns for a specific session

        Args:
            session_id: Session identifier

        Returns:
            Session-specific injection pattern analysis
        """
        with self._lock:
            session_attempts = [
                attempt for attempt in self.injection_attempts
                if attempt.session_id == session_id
            ]

        if not session_attempts:
            return {
                "session_id": session_id,
                "total_attempts": 0,
                "patterns_found": False,
                "analysis": "No injection attempts recorded for this session"
            }

        # Sort by timestamp
        session_attempts.sort(key=lambda x: x.timestamp)

        # Analyze patterns
        patterns = {
            "session_id": session_id,
            "total_attempts": len(session_attempts),
            "session_duration": session_attempts[-1].timestamp - session_attempts[0].timestamp,
            "methods_used": list(set(a.method.value for a in session_attempts)),
            "prompt_types": list(set(a.prompt_type for a in session_attempts)),
            "outcomes": [a.outcome.value for a in session_attempts],
            "success_rate": sum(1 for a in session_attempts if a.outcome == InjectionOutcome.SUCCESS) / len(session_attempts),
            "avg_response_time": statistics.mean([a.response_time_ms for a in session_attempts if a.response_time_ms > 0]),
            "patterns_found": True,
            "follow_up_required_count": sum(1 for a in session_attempts if a.follow_up_required),
            "user_intervention_count": sum(1 for a in session_attempts if a.user_intervention)
        }

        # Identify patterns
        patterns["analysis"] = self._identify_session_patterns(session_attempts)

        return patterns

    def export_monitoring_data(self,
                             format_type: str = "json",
                             time_window_hours: Optional[float] = None) -> str:
        """Export monitoring data in specified format

        Args:
            format_type: Export format ("json", "csv", "report")
            time_window_hours: Time window to filter data

        Returns:
            Exported data as string
        """
        with self._lock:
            filtered_attempts = self._filter_attempts(time_window_hours=time_window_hours)

        if format_type.lower() == "json":
            return self._export_json(filtered_attempts)
        elif format_type.lower() == "csv":
            return self._export_csv(filtered_attempts)
        elif format_type.lower() == "report":
            return self._export_report(time_window_hours or 24.0)
        else:
            raise ValueError(f"Unsupported export format: {format_type}")

    # Private helper methods

    def _filter_attempts(self,
                        method: Optional[InjectionMethod] = None,
                        prompt_type: Optional[str] = None,
                        time_window_hours: Optional[float] = None) -> List[InjectionAttempt]:
        """Filter injection attempts based on criteria"""
        filtered = self.injection_attempts.copy()

        if method:
            filtered = [a for a in filtered if a.method == method]

        if prompt_type and prompt_type != "all":
            filtered = [a for a in filtered if a.prompt_type == prompt_type]

        if time_window_hours:
            cutoff_time = time.time() - (time_window_hours * 3600)
            filtered = [a for a in filtered if a.timestamp >= cutoff_time]

        return filtered

    def _compare_methods(self, time_window_hours: float) -> Dict[str, Any]:
        """Compare effectiveness across different injection methods"""
        comparison = {}

        for method in InjectionMethod:
            metrics = self.calculate_metrics(method, None, time_window_hours)
            if metrics.total_attempts > 0:
                comparison[method.value] = {
                    "success_rate": metrics.success_rate,
                    "avg_response_time_ms": metrics.average_response_time_ms,
                    "total_attempts": metrics.total_attempts,
                    "reliability_score": self._calculate_reliability_score(metrics)
                }

        # Find best and worst performing methods
        if comparison:
            best_method = max(comparison.keys(), key=lambda k: comparison[k]["reliability_score"])
            worst_method = min(comparison.keys(), key=lambda k: comparison[k]["reliability_score"])

            comparison["best_method"] = best_method
            comparison["worst_method"] = worst_method
            comparison["performance_spread"] = (
                comparison[best_method]["reliability_score"] -
                comparison[worst_method]["reliability_score"]
            )

        return comparison

    def _calculate_reliability_trends(self, time_window_hours: float) -> List[Dict[str, Any]]:
        """Calculate reliability trends over time"""
        # Group attempts by hour buckets
        bucket_size = max(1, time_window_hours / 24)  # Create up to 24 buckets
        trends = []

        current_time = time.time()
        for i in range(24):
            bucket_start = current_time - ((i + 1) * bucket_size * 3600)
            bucket_end = current_time - (i * bucket_size * 3600)

            bucket_attempts = [
                a for a in self.injection_attempts
                if bucket_start <= a.timestamp < bucket_end
            ]

            if bucket_attempts:
                successful = sum(1 for a in bucket_attempts if a.outcome == InjectionOutcome.SUCCESS)
                success_rate = successful / len(bucket_attempts)
                avg_response = statistics.mean([a.response_time_ms for a in bucket_attempts if a.response_time_ms > 0])

                trends.append({
                    "time_bucket": i,
                    "timestamp_start": bucket_start,
                    "timestamp_end": bucket_end,
                    "success_rate": success_rate,
                    "avg_response_time_ms": avg_response,
                    "total_attempts": len(bucket_attempts)
                })

        return list(reversed(trends))  # Most recent first

    def _analyze_failures(self, time_window_hours: float) -> Dict[str, Any]:
        """Analyze failure patterns"""
        with self._lock:
            recent_failures = [
                a for a in self.injection_attempts
                if a.timestamp >= time.time() - (time_window_hours * 3600)
                and a.outcome in [InjectionOutcome.FAILURE, InjectionOutcome.TIMEOUT]
            ]

        if not recent_failures:
            return {
                "total_failures": 0,
                "failure_rate": 0.0,
                "common_errors": [],
                "failure_patterns": "No failures in the analyzed time period"
            }

        # Analyze error patterns
        error_messages = [a.error_message for a in recent_failures if a.error_message]
        error_counts = {}
        for error in error_messages:
            error_counts[error] = error_counts.get(error, 0) + 1

        # Method-specific failure rates
        method_failures = {}
        for method in InjectionMethod:
            method_attempts = [a for a in recent_failures if a.method == method]
            if method_attempts:
                failure_rate = len(method_attempts) / sum(
                    1 for a in self.injection_attempts
                    if a.method == method and a.timestamp >= time.time() - (time_window_hours * 3600)
                )
                method_failures[method.value] = failure_rate

        return {
            "total_failures": len(recent_failures),
            "failure_rate": len(recent_failures) / max(1, len([
                a for a in self.injection_attempts
                if a.timestamp >= time.time() - (time_window_hours * 3600)
            ])),
            "common_errors": sorted(error_counts.items(), key=lambda x: x[1], reverse=True)[:5],
            "method_failure_rates": method_failures,
            "failure_patterns": self._identify_failure_patterns(recent_failures)
        }

    def _calculate_reliability_score(self, metrics: InjectionEffectivenessMetrics) -> float:
        """Calculate a composite reliability score (0-100)"""
        if metrics.total_attempts == 0:
            return 0.0

        # Weight factors
        success_weight = 0.6
        speed_weight = 0.2
        consistency_weight = 0.2

        # Success component (0-100)
        success_score = metrics.success_rate * 100

        # Speed component (faster is better, but with diminishing returns)
        # Target: <1000ms = 100 points, 5000ms = 50 points, 10000ms = 0 points
        if metrics.average_response_time_ms <= 1000:
            speed_score = 100
        elif metrics.average_response_time_ms <= 5000:
            speed_score = 100 - (metrics.average_response_time_ms - 1000) * 12.5 / 1000
        elif metrics.average_response_time_ms <= 10000:
            speed_score = 50 - (metrics.average_response_time_ms - 5000) * 10 / 1000
        else:
            speed_score = 0

        # Consistency component (based on failure types)
        total_failures = metrics.failed_attempts + metrics.timeout_attempts
        consistency_score = max(0, 100 - (total_failures / metrics.total_attempts) * 100)

        # Weighted composite score
        reliability_score = (
            success_score * success_weight +
            speed_score * speed_weight +
            consistency_score * consistency_weight
        )

        return min(100, max(0, reliability_score))

    def _identify_session_patterns(self, session_attempts: List[InjectionAttempt]) -> str:
        """Identify patterns in session injection attempts"""
        if len(session_attempts) < 2:
            return "Insufficient data for pattern analysis"

        patterns = []

        # Check for method switching
        methods = [a.method for a in session_attempts]
        if len(set(methods)) > 1:
            patterns.append("Multiple injection methods used")

        # Check for escalating failures
        failures = [i for i, a in enumerate(session_attempts) if a.outcome != InjectionOutcome.SUCCESS]
        if len(failures) > len(session_attempts) * 0.5:
            patterns.append("High failure rate detected")

        # Check for response time degradation
        response_times = [a.response_time_ms for a in session_attempts if a.response_time_ms > 0]
        if len(response_times) > 1:
            if response_times[-1] > response_times[0] * 2:
                patterns.append("Response time degradation detected")

        # Check for user intervention patterns
        interventions = [a for a in session_attempts if a.user_intervention]
        if len(interventions) > 0:
            patterns.append("User intervention required")

        return "; ".join(patterns) if patterns else "Normal injection pattern"

    def _identify_failure_patterns(self, failures: List[InjectionAttempt]) -> str:
        """Identify patterns in injection failures"""
        if not failures:
            return "No failures to analyze"

        patterns = []

        # Check for method-specific failures
        method_counts = {}
        for failure in failures:
            method_counts[failure.method] = method_counts.get(failure.method, 0) + 1

        if len(method_counts) == 1:
            patterns.append(f"Failures isolated to {list(method_counts.keys())[0].value} method")

        # Check for timeout patterns
        timeouts = [f for f in failures if f.outcome == InjectionOutcome.TIMEOUT]
        if len(timeouts) > len(failures) * 0.5:
            patterns.append("Majority of failures are timeouts")

        # Check for error message patterns
        error_messages = [f.error_message for f in failures if f.error_message]
        if error_messages:
            most_common_error = max(set(error_messages), key=error_messages.count)
            if error_messages.count(most_common_error) > len(failures) * 0.3:
                patterns.append(f"Common error pattern: {most_common_error[:100]}...")

        return "; ".join(patterns) if patterns else "No clear failure patterns identified"

    def _generate_recommendations(self,
                                 overall_metrics: Dict[str, InjectionEffectivenessMetrics],
                                 method_comparison: Dict[str, Any],
                                 failure_analysis: Dict[str, Any]) -> List[str]:
        """Generate recommendations based on analysis"""
        recommendations = []

        # Method performance recommendations
        if "best_method" in method_comparison and "worst_method" in method_comparison:
            best = method_comparison["best_method"]
            worst = method_comparison["worst_method"]
            spread = method_comparison["performance_spread"]

            if spread > 30:  # Significant performance difference
                recommendations.append(
                    f"Consider using {best} method preferentially over {worst} "
                    f"(reliability difference: {spread:.1f} points)"
                )

        # Success rate recommendations
        low_success_methods = [
            method for method, metrics in overall_metrics.items()
            if metrics.success_rate < 0.8 and metrics.total_attempts >= 5
        ]

        if low_success_methods:
            recommendations.append(
                f"Low success rates detected for: {', '.join(low_success_methods)}. "
                "Investigate root causes and consider fallback mechanisms."
            )

        # Response time recommendations
        slow_methods = [
            method for method, metrics in overall_metrics.items()
            if metrics.average_response_time_ms > 5000 and metrics.total_attempts >= 3
        ]

        if slow_methods:
            recommendations.append(
                f"Slow response times for: {', '.join(slow_methods)}. "
                "Consider optimization or alternative methods."
            )

        # Failure pattern recommendations
        if failure_analysis["total_failures"] > 0:
            if failure_analysis["failure_rate"] > 0.2:
                recommendations.append(
                    "High overall failure rate detected. Implement robust error handling "
                    "and automatic retry mechanisms."
                )

            common_errors = failure_analysis.get("common_errors", [])
            if common_errors:
                top_error = common_errors[0][0]
                recommendations.append(
                    f"Most common error: {top_error[:100]}... Address this root cause."
                )

        # General recommendations
        if not recommendations:
            recommendations.append("Injection performance is within acceptable ranges.")

        recommendations.append(
            "Continue monitoring injection effectiveness and adjust strategies based on data."
        )

        return recommendations

    def _export_json(self, attempts: List[InjectionAttempt]) -> str:
        """Export data as JSON"""
        export_data = {
            "export_timestamp": time.time(),
            "total_attempts": len(attempts),
            "attempts": [asdict(attempt) for attempt in attempts]
        }
        return json.dumps(export_data, indent=2, default=str)

    def _export_csv(self, attempts: List[InjectionAttempt]) -> str:
        """Export data as CSV"""
        if not attempts:
            return "attempt_id,timestamp,method,session_id,prompt_type,outcome,response_time_ms\n"

        headers = [
            "attempt_id", "timestamp", "method", "session_id", "prompt_type",
            "outcome", "response_time_ms", "context_size", "transcript_length",
            "follow_up_required", "user_intervention"
        ]

        lines = [",".join(headers)]

        for attempt in attempts:
            row = [
                attempt.attempt_id,
                str(attempt.timestamp),
                attempt.method.value,
                attempt.session_id,
                attempt.prompt_type,
                attempt.outcome.value,
                str(attempt.response_time_ms),
                str(attempt.context_size),
                str(attempt.transcript_length),
                str(attempt.follow_up_required),
                str(attempt.user_intervention)
            ]
            lines.append(",".join(row))

        return "\n".join(lines)

    def _export_report(self, time_window_hours: float) -> str:
        """Export comprehensive report"""
        report = self.generate_reliability_report(time_window_hours)

        report_lines = [
            "INJECTION EFFECTIVENESS MONITORING REPORT",
            "=" * 50,
            f"Report ID: {report.report_id}",
            f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(report.generated_at))}",
            f"Time Period: {report.time_period_hours:.1f} hours",
            "",
            "OVERALL METRICS",
            "-" * 20
        ]

        for key, metrics in report.overall_metrics.items():
            report_lines.extend([
                f"{key}:",
                f"  Total Attempts: {metrics.total_attempts}",
                f"  Success Rate: {metrics.success_rate:.1%}",
                f"  Avg Response Time: {metrics.average_response_time_ms:.1f}ms",
                ""
            ])

        report_lines.extend([
            "METHOD COMPARISON",
            "-" * 20
        ])

        for method, data in report.method_comparison.items():
            if method not in ["best_method", "worst_method", "performance_spread"]:
                report_lines.append(
                    f"{method}: {data['reliability_score']:.1f}/100 reliability score"
                )

        if "best_method" in report.method_comparison:
            report_lines.extend([
                f"Best Method: {report.method_comparison['best_method']}",
                f"Worst Method: {report.method_comparison['worst_method']}",
                ""
            ])

        report_lines.extend([
            "RECOMMENDATIONS",
            "-" * 20
        ])

        for i, rec in enumerate(report.recommendations, 1):
            report_lines.append(f"{i}. {rec}")

        return "\n".join(report_lines)

    def _load_historical_data(self):
        """Load historical monitoring data from storage"""
        data_file = self.storage_dir / "injection_data.json"

        if data_file.exists():
            try:
                with open(data_file, 'r') as f:
                    data = json.load(f)

                # Load attempts (convert back to dataclass objects)
                for attempt_data in data.get("attempts", []):
                    try:
                        attempt = InjectionAttempt(
                            attempt_id=attempt_data["attempt_id"],
                            timestamp=attempt_data["timestamp"],
                            method=InjectionMethod(attempt_data["method"]),
                            session_id=attempt_data["session_id"],
                            prompt_type=attempt_data["prompt_type"],
                            prompt_content=attempt_data["prompt_content"],
                            outcome=InjectionOutcome(attempt_data["outcome"]),
                            response_time_ms=attempt_data["response_time_ms"],
                            success_indicators=attempt_data.get("success_indicators", []),
                            error_message=attempt_data.get("error_message"),
                            context_size=attempt_data.get("context_size", 0),
                            transcript_length=attempt_data.get("transcript_length", 0),
                            follow_up_required=attempt_data.get("follow_up_required", False),
                            user_intervention=attempt_data.get("user_intervention", False)
                        )
                        self.injection_attempts.append(attempt)
                    except (KeyError, ValueError) as e:
                        if self.logger:
                            self.logger.warning(
                                "data_loading",
                                f"Failed to load injection attempt: {e}",
                                "system"
                            )

                # Load session contexts and convert methods_used back to set
                raw_contexts = data.get("session_contexts", {})
                self.session_contexts = {}
                for session_id, context in raw_contexts.items():
                    # Convert methods_used back to set if it exists
                    if "methods_used" in context and isinstance(context["methods_used"], list):
                        context["methods_used"] = set(context["methods_used"])
                    self.session_contexts[session_id] = context

                if self.logger:
                    self.logger.info(
                        "data_loading",
                        f"Loaded {len(self.injection_attempts)} historical injection records",
                        "system"
                    )

            except Exception as e:
                if self.logger:
                    self.logger.error(
                        "data_loading",
                        f"Failed to load historical data: {e}",
                        "system"
                    )

    def _persist_data_async(self):
        """Persist monitoring data asynchronously"""
        def persist_worker():
            try:
                data_file = self.storage_dir / "injection_data.json"

                # Convert session_contexts to JSON-serializable format
                serializable_contexts = {}
                for session_id, context in self.session_contexts.items():
                    serializable_context = context.copy()
                    # Convert set to list for JSON serialization
                    if "methods_used" in serializable_context and isinstance(serializable_context["methods_used"], set):
                        serializable_context["methods_used"] = list(serializable_context["methods_used"])
                    serializable_contexts[session_id] = serializable_context

                data = {
                    "last_updated": time.time(),
                    "attempts": [asdict(attempt) for attempt in self.injection_attempts],
                    "session_contexts": serializable_contexts
                }

                # Atomic write
                temp_file = data_file.with_suffix(".tmp")
                with open(temp_file, 'w') as f:
                    json.dump(data, f, indent=2, default=str)

                temp_file.replace(data_file)

            except Exception as e:
                if self.logger:
                    self.logger.error(
                        "data_persistence",
                        f"Failed to persist monitoring data: {e}",
                        "system"
                    )

        # Run in background thread
        thread = threading.Thread(target=persist_worker, daemon=True)
        thread.start()


# Global instance for easy access
_global_monitor: Optional[InjectionEffectivenessMonitor] = None

def get_injection_monitor() -> InjectionEffectivenessMonitor:
    """Get or create global injection monitor instance"""
    global _global_monitor
    if _global_monitor is None:
        _global_monitor = InjectionEffectivenessMonitor()
    return _global_monitor

def record_injection(method: InjectionMethod,
                    session_id: str,
                    prompt_type: str,
                    prompt_content: str,
                    outcome: InjectionOutcome,
                    response_time_ms: float,
                    **kwargs) -> str:
    """Convenience function to record injection attempt"""
    monitor = get_injection_monitor()
    return monitor.record_injection_attempt(
        method=method,
        session_id=session_id,
        prompt_type=prompt_type,
        prompt_content=prompt_content,
        outcome=outcome,
        response_time_ms=response_time_ms,
        **kwargs
    )