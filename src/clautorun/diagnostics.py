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
"""Comprehensive Diagnostic and Logging Tools for clautorun - System health monitoring"""

import os
import sys
import json
import time
import psutil
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, asdict
from enum import Enum
from collections import defaultdict, deque
import traceback
import hashlib

# Import main.py patterns for consistency
try:
    from .main import CONFIG, session_state, log_info
except ImportError:
    # Fallback if running standalone
    CONFIG = {}
    def session_state(session_id):
        """Fallback session state"""
        class DummyState:
            def __enter__(self):
                return {}
            def __exit__(self, *args):
                pass
        return DummyState()
    def log_info(message):
        """Fallback logging"""
        print(f"INFO: {message}")

# Follow main.py pattern for handlers
DIAGNOSTIC_HANDLERS = {}
def diagnostic_handler(name):
    """Decorator to register diagnostic handlers - following main.py pattern"""
    def dec(f):
        DIAGNOSTIC_HANDLERS[name] = f
        return f
    return dec

class LogLevel(Enum):
    """Logging levels"""
    DEBUG = 0
    INFO = 1
    WARNING = 2
    ERROR = 3
    CRITICAL = 4

class HealthStatus(Enum):
    """System health status"""
    HEALTHY = "healthy"
    WARNING = "warning"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    UNKNOWN = "unknown"

@dataclass
class LogEntry:
    """Structured log entry"""
    timestamp: float
    level: LogLevel
    category: str
    message: str
    session_id: str
    metadata: Dict[str, Any] = None
    thread_id: int = None
    process_id: int = None

    def to_dict(self) -> Dict:
        """Convert log entry to dictionary"""
        result = asdict(self)
        result['level'] = self.level.value
        return result

@dataclass
class SystemMetric:
    """System performance metric"""
    name: str
    value: float
    unit: str
    timestamp: float
    category: str
    tags: Dict[str, str] = None

@dataclass
class HealthCheck:
    """Health check result"""
    name: str
    status: HealthStatus
    message: str
    timestamp: float
    duration: float
    details: Dict[str, Any] = None

class DiagnosticLogger:
    """Enhanced diagnostic logger with structured logging"""

    def __init__(self, max_entries: int = 10000):
        self.max_entries = max_entries
        self.logs = deque(maxlen=max_entries)
        self.session_logs = defaultdict(lambda: deque(maxlen=1000))
        self.category_counts = defaultdict(int)
        self.lock = threading.Lock()
        self.log_file = None
        self._setup_log_file()

    def _setup_log_file(self):
        """Setup log file for persistent logging"""
        try:
            log_dir = Path.home() / ".claude" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)

            log_file = log_dir / f"clautorun_diagnostic_{int(time.time())}.log"
            self.log_file = open(log_file, 'a', encoding='utf-8')

            # Write header
            self.log_file.write(f"# clautorun Diagnostic Log Started at {time.ctime()}\n")
            self.log_file.flush()

        except Exception as e:
            log_info(f"Failed to setup diagnostic log file: {e}")

    @diagnostic_handler("log")
    def log(self, level: LogLevel, category: str, message: str, session_id: str = "default",
             metadata: Dict[str, Any] = None):
        """Log a structured entry"""
        entry = LogEntry(
            timestamp=time.time(),
            level=level,
            category=category,
            message=message,
            session_id=session_id,
            metadata=metadata or {},
            thread_id=threading.get_ident(),
            process_id=os.getpid()
        )

        with self.lock:
            self.logs.append(entry)
            self.session_logs[session_id].append(entry)
            self.category_counts[category] += 1

        # Write to file if available
        if self.log_file:
            try:
                log_line = json.dumps(entry.to_dict())
                self.log_file.write(f"{log_line}\n")
                self.log_file.flush()
            except Exception as e:
                # Don't let logging errors crash the system
                pass

        # Console output for critical errors
        if level == LogLevel.CRITICAL:
            print(f"CRITICAL [{category}] {message}")

    def debug(self, category: str, message: str, session_id: str = "default", **kwargs):
        """Log debug message"""
        self.log(LogLevel.DEBUG, category, message, session_id, kwargs)

    def info(self, category: str, message: str, session_id: str = "default", **kwargs):
        """Log info message"""
        self.log(LogLevel.INFO, category, message, session_id, kwargs)

    def warning(self, category: str, message: str, session_id: str = "default", **kwargs):
        """Log warning message"""
        self.log(LogLevel.WARNING, category, message, session_id, kwargs)

    def error(self, category: str, message: str, session_id: str = "default", **kwargs):
        """Log error message"""
        self.log(LogLevel.ERROR, category, message, session_id, kwargs)

    def critical(self, category: str, message: str, session_id: str = "default", **kwargs):
        """Log critical message"""
        self.log(LogLevel.CRITICAL, category, message, session_id, kwargs)

    def get_logs(self, session_id: str = None, level: LogLevel = None,
                 category: str = None, limit: int = 100) -> List[LogEntry]:
        """Retrieve logs with filters"""
        with self.lock:
            if session_id:
                logs = list(self.session_logs[session_id])
            else:
                logs = list(self.logs)

            # Apply filters
            if level:
                logs = [log for log in logs if log.level == level]
            if category:
                logs = [log for log in logs if log.category == category]

            # Return most recent logs
            return sorted(logs, key=lambda x: x.timestamp, reverse=True)[:limit]

    def get_session_summary(self, session_id: str) -> Dict[str, Any]:
        """Get summary of logs for a session"""
        with self.lock:
            session_logs = list(self.session_logs[session_id])

            if not session_logs:
                return {"total_logs": 0}

            # Calculate statistics
            level_counts = defaultdict(int)
            category_counts = defaultdict(int)

            for log in session_logs:
                level_counts[log.level.name] += 1
                category_counts[log.category] += 1

            return {
                "total_logs": len(session_logs),
                "level_distribution": dict(level_counts),
                "category_distribution": dict(category_counts),
                "first_log_time": session_logs[0].timestamp,
                "last_log_time": session_logs[-1].timestamp,
                "duration": session_logs[-1].timestamp - session_logs[0].timestamp
            }

    def cleanup_old_logs(self, max_age_hours: int = 24):
        """Clean up old log files"""
        try:
            log_dir = Path.home() / ".claude" / "logs"
            if not log_dir.exists():
                return

            current_time = time.time()
            max_age_seconds = max_age_hours * 3600

            for log_file in log_dir.glob("clautorun_diagnostic_*.log"):
                if log_file.stat().st_mtime < current_time - max_age_seconds:
                    log_file.unlink()
                    log_info(f"Cleaned up old log file: {log_file}")

        except Exception as e:
            log_info(f"Error cleaning up old logs: {e}")

class SystemMonitor:
    """System performance monitoring"""

    def __init__(self, logger: DiagnosticLogger):
        self.logger = logger
        self.metrics = deque(maxlen=1000)
        self.monitoring = False
        self.monitor_thread = None
        self.monitor_interval = 30  # 30 seconds

    @diagnostic_handler("start_monitoring")
    def start_monitoring(self, interval: int = 30):
        """Start background system monitoring"""
        if self.monitoring:
            return

        self.monitor_interval = interval
        self.monitoring = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

        self.logger.info("monitor", f"Started system monitoring with {interval}s interval")

    @diagnostic_handler("stop_monitoring")
    def stop_monitoring(self):
        """Stop system monitoring"""
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)

        self.logger.info("monitor", "Stopped system monitoring")

    def _monitor_loop(self):
        """Main monitoring loop"""
        while self.monitoring:
            try:
                self._collect_metrics()
                time.sleep(self.monitor_interval)
            except Exception as e:
                self.logger.error("monitor", f"Error in monitoring loop: {e}")
                time.sleep(5)  # Wait before retrying

    @diagnostic_handler("collect_metrics")
    def collect_metrics(self):
        """Collect system metrics immediately"""
        self._collect_metrics()

    def _collect_metrics(self):
        """Collect current system metrics"""
        timestamp = time.time()

        try:
            # CPU metrics
            cpu_percent = psutil.cpu_percent(interval=1)
            cpu_count = psutil.cpu_count()

            self.metrics.append(SystemMetric(
                name="cpu_percent",
                value=cpu_percent,
                unit="percent",
                timestamp=timestamp,
                category="system",
                tags={"cpu_count": str(cpu_count)}
            ))

            # Memory metrics
            memory = psutil.virtual_memory()
            self.metrics.append(SystemMetric(
                name="memory_percent",
                value=memory.percent,
                unit="percent",
                timestamp=timestamp,
                category="system"
            ))

            self.metrics.append(SystemMetric(
                name="memory_used_mb",
                value=memory.used / 1024 / 1024,
                unit="MB",
                timestamp=timestamp,
                category="system"
            ))

            # Disk metrics
            disk = psutil.disk_usage(Path.cwd().anchor)
            self.metrics.append(SystemMetric(
                name="disk_percent",
                value=(disk.used / disk.total) * 100,
                unit="percent",
                timestamp=timestamp,
                category="storage"
            ))

            # Process metrics for clautorun
            for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
                try:
                    if 'python' in proc.info['name'].lower():
                        self.metrics.append(SystemMetric(
                            name="process_cpu_percent",
                            value=proc.info['cpu_percent'] or 0,
                            unit="percent",
                            timestamp=timestamp,
                            category="process",
                            tags={"pid": str(proc.info['pid'])}
                        ))

                        self.metrics.append(SystemMetric(
                            name="process_memory_percent",
                            value=proc.info['memory_percent'] or 0,
                            unit="percent",
                            timestamp=timestamp,
                            category="process",
                            tags={"pid": str(proc.info['pid'])}
                        ))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

        except Exception as e:
            self.logger.error("monitor", f"Error collecting metrics: {e}")

    def get_metrics(self, category: str = None, limit: int = 100) -> List[SystemMetric]:
        """Get collected metrics with optional filtering"""
        metrics = list(self.metrics)

        if category:
            metrics = [m for m in metrics if m.category == category]

        # Return most recent metrics
        return sorted(metrics, key=lambda x: x.timestamp, reverse=True)[:limit]

    def get_metric_summary(self, hours: int = 1) -> Dict[str, Any]:
        """Get summary of metrics for time period"""
        cutoff_time = time.time() - (hours * 3600)
        recent_metrics = [m for m in self.metrics if m.timestamp > cutoff_time]

        if not recent_metrics:
            return {"message": "No metrics available for specified time period"}

        # Group metrics by name
        metric_groups = defaultdict(list)
        for metric in recent_metrics:
            metric_groups[metric.name].append(metric)

        summary = {}
        for name, metrics_list in metric_groups.items():
            values = [m.value for m in metrics_list]
            summary[name] = {
                "count": len(values),
                "min": min(values),
                "max": max(values),
                "avg": sum(values) / len(values),
                "latest": values[-1],
                "unit": metrics_list[-1].unit
            }

        return summary

class HealthChecker:
    """System health checking"""

    def __init__(self, logger: DiagnosticLogger):
        self.logger = logger
        self.health_checks = []
        self.last_results = {}

    @diagnostic_handler("register_check")
    def register_check(self, name: str, check_func: Callable[[], HealthCheck], interval: int = 300):
        """Register a health check function"""
        self.health_checks.append({
            "name": name,
            "func": check_func,
            "interval": interval,
            "last_run": 0
        })

        self.logger.info("health", f"Registered health check: {name}")

    @diagnostic_handler("run_all_checks")
    def run_all_checks(self) -> Dict[str, HealthCheck]:
        """Run all registered health checks"""
        results = {}
        current_time = time.time()

        for check_info in self.health_checks:
            # Check if it's time to run this check
            if current_time - check_info["last_run"] >= check_info["interval"]:
                try:
                    start_time = time.time()
                    result = check_info["func"]()
                    duration = time.time() - start_time

                    # Update last run time
                    check_info["last_run"] = current_time

                    results[check_info["name"]] = result
                    self.last_results[check_info["name"]] = result

                    self.logger.info("health",
                        f"Health check '{check_info['name']}': {result.status.value} ({duration:.3f}s)")

                except Exception as e:
                    error_result = HealthCheck(
                        name=check_info["name"],
                        status=HealthStatus.CRITICAL,
                        message=f"Health check failed: {str(e)}",
                        timestamp=current_time,
                        duration=0,
                        details={"error": str(e), "traceback": traceback.format_exc()}
                    )
                    results[check_info["name"]] = error_result
                    self.last_results[check_info["name"]] = error_result

                    self.logger.error("health", f"Health check '{check_info['name']}' failed: {e}")

        return results

    def get_overall_health(self) -> HealthStatus:
        """Get overall system health status"""
        if not self.last_results:
            return HealthStatus.UNKNOWN

        statuses = [result.status for result in self.last_results.values()]

        if any(status == HealthStatus.CRITICAL for status in statuses):
            return HealthStatus.CRITICAL
        elif any(status == HealthStatus.DEGRADED for status in statuses):
            return HealthStatus.DEGRADED
        elif any(status == HealthStatus.WARNING for status in statuses):
            return HealthStatus.WARNING
        elif all(status == HealthStatus.HEALTHY for status in statuses):
            return HealthStatus.HEALTHY
        else:
            return HealthStatus.UNKNOWN

class DiagnosticManager:
    """Main diagnostic management system"""

    def __init__(self):
        self.logger = DiagnosticLogger()
        self.monitor = SystemMonitor(self.logger)
        self.health_checker = HealthChecker(self.logger)
        self._register_default_health_checks()

    def _register_default_health_checks(self):
        """Register default health checks"""

        @self.health_checker.register_check("disk_space", interval=300)
        def check_disk_space() -> HealthCheck:
            try:
                disk = psutil.disk_usage(Path.cwd().anchor)
                percent_used = (disk.used / disk.total) * 100

                if percent_used > 90:
                    status = HealthStatus.CRITICAL
                    message = f"Disk usage critical: {percent_used:.1f}%"
                elif percent_used > 80:
                    status = HealthStatus.WARNING
                    message = f"Disk usage high: {percent_used:.1f}%"
                else:
                    status = HealthStatus.HEALTHY
                    message = f"Disk usage normal: {percent_used:.1f}%"

                return HealthCheck(
                    name="disk_space",
                    status=status,
                    message=message,
                    timestamp=time.time(),
                    duration=0,
                    details={
                        "percent_used": percent_used,
                        "free_gb": disk.free / 1024 / 1024 / 1024,
                        "total_gb": disk.total / 1024 / 1024 / 1024
                    }
                )

            except Exception as e:
                return HealthCheck(
                    name="disk_space",
                    status=HealthStatus.CRITICAL,
                    message=f"Disk check failed: {e}",
                    timestamp=time.time(),
                    duration=0
                )

        @self.health_checker.register_check("memory_usage", interval=60)
        def check_memory_usage() -> HealthCheck:
            try:
                memory = psutil.virtual_memory()
                percent_used = memory.percent

                if percent_used > 90:
                    status = HealthStatus.CRITICAL
                    message = f"Memory usage critical: {percent_used:.1f}%"
                elif percent_used > 80:
                    status = HealthStatus.WARNING
                    message = f"Memory usage high: {percent_used:.1f}%"
                else:
                    status = HealthStatus.HEALTHY
                    message = f"Memory usage normal: {percent_used:.1f}%"

                return HealthCheck(
                    name="memory_usage",
                    status=status,
                    message=message,
                    timestamp=time.time(),
                    duration=0,
                    details={
                        "percent_used": percent_used,
                        "available_gb": memory.available / 1024 / 1024 / 1024,
                        "total_gb": memory.total / 1024 / 1024 / 1024
                    }
                )

            except Exception as e:
                return HealthCheck(
                    name="memory_usage",
                    status=HealthStatus.CRITICAL,
                    message=f"Memory check failed: {e}",
                    timestamp=time.time(),
                    duration=0
                )

        @self.health_checker.register_check("clautorun_processes", interval=120)
        def check_clautorun_processes() -> HealthCheck:
            try:
                python_processes = []
                for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                    try:
                        if 'python' in proc.info['name'].lower():
                            cmdline = ' '.join(proc.info['cmdline'] or [])
                            if 'clautorun' in cmdline:
                                python_processes.append(proc.info['pid'])
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue

                if len(python_processes) > 10:
                    status = HealthStatus.WARNING
                    message = f"High number of clautorun processes: {len(python_processes)}"
                elif len(python_processes) > 0:
                    status = HealthStatus.HEALTHY
                    message = f"clautorun processes running: {len(python_processes)}"
                else:
                    status = HealthStatus.HEALTHY
                    message = "No clautorun processes running (normal)"

                return HealthCheck(
                    name="clautorun_processes",
                    status=status,
                    message=message,
                    timestamp=time.time(),
                    duration=0,
                    details={
                        "process_count": len(python_processes),
                        "process_pids": python_processes
                    }
                )

            except Exception as e:
                return HealthCheck(
                    name="clautorun_processes",
                    status=HealthStatus.CRITICAL,
                    message=f"Process check failed: {e}",
                    timestamp=time.time(),
                    duration=0
                )

    @diagnostic_handler("start")
    def start(self, monitor_interval: int = 30):
        """Start diagnostic system"""
        self.monitor.start_monitoring(monitor_interval)
        self.logger.info("diagnostic", "Diagnostic system started")

    @diagnostic_handler("stop")
    def stop(self):
        """Stop diagnostic system"""
        self.monitor.stop_monitoring()
        self.logger.info("diagnostic", "Diagnostic system stopped")

    @diagnostic_handler("get_status")
    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive diagnostic status"""
        health_results = self.health_checker.run_all_checks()
        overall_health = self.health_checker.get_overall_health()

        return {
            "overall_health": overall_health.value,
            "timestamp": time.time(),
            "health_checks": {
                name: {
                    "status": result.status.value,
                    "message": result.message,
                    "duration": result.duration
                }
                for name, result in health_results.items()
            },
            "monitoring": {
                "active": self.monitor.monitoring,
                "interval": self.monitor.monitor_interval,
                "metrics_count": len(self.monitor.metrics)
            },
            "logging": {
                "total_logs": len(self.logger.logs),
                "active_sessions": len(self.logger.session_logs)
            }
        }

    @diagnostic_handler("export_diagnostics")
    def export_diagnostics(self, output_file: str = None) -> str:
        """Export comprehensive diagnostic report"""
        if output_file is None:
            output_file = f"clautorun_diagnostics_{int(time.time())}.json"

        status = self.get_status()

        # Add recent logs and metrics
        recent_logs = [log.to_dict() for log in self.logger.get_logs(limit=1000)]
        recent_metrics = [
            {
                "name": m.name,
                "value": m.value,
                "unit": m.unit,
                "timestamp": m.timestamp,
                "category": m.category,
                "tags": m.tags
            }
            for m in self.monitor.get_metrics(limit=500)
        ]

        diagnostic_data = {
            "export_timestamp": time.time(),
            "status": status,
            "recent_logs": recent_logs,
            "recent_metrics": recent_metrics,
            "session_summaries": {
                session_id: self.logger.get_session_summary(session_id)
                for session_id in list(self.logger.session_logs.keys())[-10:]  # Last 10 sessions
            }
        }

        try:
            with open(output_file, 'w') as f:
                json.dump(diagnostic_data, f, indent=2)

            self.logger.info("diagnostic", f"Diagnostic report exported to {output_file}")
            return output_file

        except Exception as e:
            self.logger.error("diagnostic", f"Failed to export diagnostics: {e}")
            raise

# Global diagnostic manager instance
diagnostic_manager = DiagnosticManager()

# Export main functions and instances
__all__ = [
    'DiagnosticManager',
    'DiagnosticLogger',
    'SystemMonitor',
    'HealthChecker',
    'diagnostic_manager',
    'LogLevel',
    'HealthStatus'
]