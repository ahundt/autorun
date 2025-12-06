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
"""Ultra-compact Agent SDK - Enhanced autorun command interceptor with efficient dispatch"""
import os
import json
import shelve
import sys
import time
import threading
import asyncio
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

# Import Agent SDK
try:
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, query
except ImportError:
    print("❌ Claude Agent SDK not installed. Run: pip install claude-agent-sdk")
    sys.exit(1)

# Import the ai_monitor library, with a fallback if it's not found.
try:
    from . import ai_monitor
except ImportError:
    try:
        # Fallback for when running as script directly
        import ai_monitor
    except ImportError:
        # Silent fallback - don't print as it pollutes hook output
        ai_monitor = None

# Import verification engine for enhanced two-stage verification
try:
    from .verification_engine import (
        RequirementVerificationEngine,
        VerificationStatus,
        RequirementType,
        RequirementEvidence
    )
    VERIFICATION_ENGINE_AVAILABLE = True
except ImportError:
    # Fallback if verification engine not available
    VERIFICATION_ENGINE_AVAILABLE = False
    RequirementVerificationEngine = None
    VerificationStatus = None
    RequirementType = None
    RequirementEvidence = None

# Import enhanced transcript analyzer
try:
    from .transcript_analyzer import (
        TranscriptAnalyzer,
        EvidenceType,
        ConfidenceLevel
    )
    TRANSCRIPT_ANALYZER_AVAILABLE = True
except ImportError:
    # Fallback if transcript analyzer not available
    TRANSCRIPT_ANALYZER_AVAILABLE = False
    TranscriptAnalyzer = None
    EvidenceType = None
    ConfidenceLevel = None

# Import injection effectiveness monitoring system
try:
    from .injection_monitoring import (
        InjectionEffectivenessMonitor,
        InjectionMethod,
        InjectionOutcome,
        record_injection
    )
    INJECTION_MONITORING_AVAILABLE = True
except ImportError:
    # Fallback if injection monitoring not available
    INJECTION_MONITORING_AVAILABLE = False
    InjectionEffectivenessMonitor = None
    InjectionMethod = None
    InjectionOutcome = None

# Import centralized tmux utilities for DRY compliance and standards enforcement
try:
    from .tmux_utils import get_tmux_utilities
    TMUX_UTILS_AVAILABLE = True
except ImportError:
    # Fallback if tmux utilities not available
    TMUX_UTILS_AVAILABLE = False
    get_tmux_utilities = None

# Global injection monitor instance
_injection_monitor: Optional[InjectionEffectivenessMonitor] = None

# Import centralized configuration (DRY principle)
from .config import CONFIG

# Global tmux utilities instance for session management
_tmux_utilities = None

def get_global_tmux_utils():
    """Get global tmux utilities instance with proper session management"""
    global _tmux_utilities
    if _tmux_utilities is None and TMUX_UTILS_AVAILABLE:
        _tmux_utilities = get_tmux_utilities()  # Uses default "clautorun" session
        log_info("Centralized tmux utilities initialized with default session")
    return _tmux_utilities

def get_injection_monitor() -> Optional[InjectionEffectivenessMonitor]:
    """Get or create global injection monitor instance"""
    global _injection_monitor
    if _injection_monitor is None and INJECTION_MONITORING_AVAILABLE:
        try:
            _injection_monitor = InjectionEffectivenessMonitor()
            log_info("Injection monitoring initialized")
        except Exception as e:
            log_info(f"Failed to initialize injection monitoring: {e}")
    return _injection_monitor

def update_injection_outcome(state, outcome: InjectionOutcome, error_message: Optional[str] = None):
    """Update the outcome of the last injection attempt with response time"""
    if not INJECTION_MONITORING_AVAILABLE:
        return

    monitor = get_injection_monitor()
    if not monitor:
        return

    state.get("session_id", "unknown")
    last_injection_id = state.get("last_injection_attempt_id")
    start_time = state.get("last_injection_start_time")

    if not last_injection_id or not start_time:
        return

    try:
        # Calculate response time
        response_time_ms = (time.time() - start_time) * 1000

        # Update the injection attempt with actual outcome and response time
        for attempt in monitor.injection_attempts:
            if attempt.attempt_id == last_injection_id and attempt.response_time_ms == 0:
                attempt.outcome = outcome
                attempt.response_time_ms = response_time_ms
                attempt.error_message = error_message
                break

        log_info(f"Updated injection outcome: {outcome.value} ({response_time_ms:.1f}ms)")

    except Exception as e:
        log_info(f"Failed to update injection outcome: {e}")

# State management - copied from autorun5.py
STATE_DIR = Path.home() / ".claude" / "sessions"
STATE_DIR.mkdir(parents=True, exist_ok=True)

def log_info(message):
    """Log info message to file with DEBUG environment variable control"""
    # Only log if DEBUG environment variable is set to true
    # Handle various forms of "true": true, True, TRUE, 1, yes, YES, etc.
    debug_value = os.getenv("DEBUG", "false").lower().strip()
    true_values = {"true", "1", "yes", "on", "enabled"}
    if debug_value not in true_values:
        return

    try:
        # Ensure directory exists
        STATE_DIR.mkdir(parents=True, exist_ok=True)

        # Log to main autorun log
        with open(STATE_DIR / "autorun.log", "a") as f:
            log_time = time.strftime('%Y-%m-%d %H:%M:%S')
            pid = os.getpid()
            f.write(f"[{log_time}] {pid}: {message}\n")
            f.flush()

        # Separate log for PreToolUse debugging
        if "PreToolUse" in message:
            with open(STATE_DIR / "pretooluse_debug.log", "a") as debug_f:
                debug_f.write(f"[{log_time}] {pid}: {message}\n")
                debug_f.flush()

    except Exception as e:
        # Fallback logging to stderr in case of write failure
        print(f"Log write failed: {e}", file=sys.stderr)

# Global lock to ensure only one backend selection happens at a time
_backend_selection_lock = threading.Lock()
# Registry to track which backend works for each session_id
_session_backends = {}

@contextmanager
def session_state(session_id: str):
    """Session state with shelve - copied from autorun5.py with thread-safe backend selection"""
    # Thread-safe backend selection (happens once per session_id)
    with _backend_selection_lock:
        if session_id not in _session_backends:
            # Test different backends and pick one that works for this platform
            try:
                # Try default shelve backend first - but be more robust about testing
                test_db = STATE_DIR / f"test_backend_{session_id}.db"
                test_state = shelve.open(str(test_db), writeback=True)
                test_state["test"] = "test"  # Actually write something to test
                test_state.sync()
                test_state.close()
                os.remove(test_db)  # Clean up test file
                _session_backends[session_id] = "default"
                log_info(f"Session {session_id}: Using default shelve backend")
            except Exception as e:
                log_info(f"Session {session_id}: Default backend failed: {e}")
                try:
                    # Try dumbdbm fallback
                    test_db = STATE_DIR / f"test_dumbdbm_{session_id}.db"
                    test_state = shelve.open(str(test_db), writeback=True)
                    test_state["test"] = "test"  # Actually write something to test
                    test_state.sync()
                    test_state.close()
                    os.remove(test_db)  # Clean up test file
                    _session_backends[session_id] = "dumbdbm"
                    log_info(f"Session {session_id}: Using dumbdbm backend")
                except Exception as e2:
                    log_info(f"Session {session_id}: Dumbdbm failed: {e2}")
                    # Try to use default shelve anyway without testing (some systems have issues with test/create/delete)
                    try:
                        _session_backends[session_id] = "default"
                        log_info(f"Session {session_id}: Trying default shelve without test")
                    except Exception:
                        # Last resort: use in-memory with thread-safe dict
                        _session_backends[session_id] = "memory"
                        log_info(f"Session {session_id}: Using in-memory fallback")

    # Use the selected backend consistently for this session_id
    backend = _session_backends[session_id]

    state = None
    try:
        if backend == "default":
            state = shelve.open(str(STATE_DIR / f"{session_id}.db"), writeback=True)
        elif backend == "dumbdbm":
            state = shelve.open(str(STATE_DIR / f"{session_id}_dumb.db"), writeback=True)
        else:  # memory
            state = {}

        yield state

    finally:
        if state and hasattr(state, 'sync'):
            state.sync()
            state.close()

# Response builders - follow autorun5.py pattern exactly (lines 118-128)
def build_hook_response(continue_execution=True, stop_reason="", system_message=""):
    """Build standardized JSON hook response - autorun5.py line 118-121"""
    return {"continue": continue_execution, "stopReason": stop_reason,
            "suppressOutput": False, "systemMessage": system_message}

def build_pretooluse_response(decision="allow", reason=""):
    """Build PreToolUse hook response - autorun5.py line 123-128"""
    return {"continue": True, "stopReason": "", "suppressOutput": False,
            "systemMessage": json.dumps(reason)[1:-1] if reason else "",
            "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                  "permissionDecisionReason": json.dumps(reason)[1:-1] if reason else ""}}

# Ultra-efficient dispatch system - using autorun5.py patterns
HANDLERS = {}
def handler(name):
    """Decorator to register handlers - copied from autorun5.py"""
    def dec(f):
        HANDLERS[name] = f
        return f
    return dec

def _manage_monitor(state: dict, action: str):
    """Centralized helper for all ai-monitor process management."""
    if not ai_monitor or "session_id" not in state:
        return
    session_id = state["session_id"]
    if action == 'start':
        if state.get("ai_monitor_pid"):
            ai_monitor.stop_monitor(session_id)
        pid = ai_monitor.start_monitor(
            session_id=session_id, prompt="continue working",
            stop_marker=CONFIG["stage3_confirmation"], max_cycles=20, prompt_on_start=True
        )
        log_info(f"Started ai-monitor for session {session_id} with PID: {pid}")
        state["ai_monitor_pid"] = pid
    elif action == 'stop':
        if state.get("ai_monitor_pid"):
            ai_monitor.stop_monitor(session_id)
            log_info(f"Stopped monitor for session {session_id}")
            state["ai_monitor_pid"] = None


# Command handlers - copied from autorun5.py
def handle_search(state):
    """Handle SEARCH command - update state and return response"""
    state["file_policy"] = "SEARCH"
    return f"AutoFile policy: strict-search - {CONFIG['policies']['SEARCH'][1]}"

def handle_allow(state):
    """Handle ALLOW command - update state and return response"""
    state["file_policy"] = "ALLOW"
    return f"AutoFile policy: allow-all - {CONFIG['policies']['ALLOW'][1]}"

def handle_justify(state):
    """Handle JUSTIFY command - update state and return response"""
    state["file_policy"] = "JUSTIFY"
    return f"AutoFile policy: justify-create - {CONFIG['policies']['JUSTIFY'][1]}"

def handle_status(state):
    """Handle STATUS command - return current policy"""
    policy = state.get("file_policy", "ALLOW")
    policy_name = CONFIG["policies"][policy][0]
    return f"Current policy: {policy_name}"

def handle_stop(state):
    """Handle STOP command - update state and return response"""
    # Note: session_id must be in state (added by intercept_commands caller)
    _manage_monitor(state, 'stop')
    state["session_status"] = "stopped"
    return "Autorun stopped"

def handle_emergency_stop(state):
    """Handle EMERGENCY_STOP command - update state and return response"""
    log_info("Emergency stop: autorun session")
    # Note: session_id must be in state (added by intercept_commands caller)
    _manage_monitor(state, 'stop')
    state["session_status"] = "emergency_stopped"
    return "Emergency stop activated"

def handle_activate(state, prompt=""):
    """Handle AUTORUN activation - complete autorun setup with injection template and tmux standards"""
    log_info("Activating autorun: autorun session")

    # Preserve file_policy and session_id before clearing
    old_file_policy = state.get("file_policy", "ALLOW")
    old_session_id = state.get("session_id")

    # Enforce tmux session standards if available
    if TMUX_UTILS_AVAILABLE:
        try:
            tmux_utils = get_global_tmux_utils()
            if tmux_utils:
                # Ensure default "clautorun" session exists and is available
                tmux_utils.ensure_session_exists()
                session_info = tmux_utils.get_session_info()
                log_info(f"tmux session ensured: {session_info['session']} (tmux active: {session_info['tmux_active']})")

                # Store tmux session info in state for monitoring integration
                state["tmux_session"] = session_info["session"]
                state["tmux_active"] = session_info["tmux_active"]
        except Exception as e:
            log_info(f"tmux session setup failed: {e}")

    # Clear and setup state like autorun5.py
    state.clear()
    state.update({
        "session_status": "active",
        "autorun_stage": "INITIAL",
        "activation_prompt": prompt,
        "verification_attempts": 0,
        "file_policy": old_file_policy,
        "session_id": old_session_id  # Restore session_id for monitor
    })

    # Start monitor AFTER state is set up with session_id
    _manage_monitor(state, 'start')

    # Generate injection template with current policy
    policy = state["file_policy"]
    policy_instructions = CONFIG["policies"][policy][1]

    injection = CONFIG["injection_template"].format(
        emergency_stop=CONFIG["emergency_stop"],
        stage1_instruction=CONFIG["stage1_instruction"],
        stage1_confirmation=CONFIG["stage1_confirmation"],
        stage2_instruction=CONFIG["stage2_instruction"],
        stage2_confirmation=CONFIG["stage2_confirmation"],
        stage3_instruction=CONFIG["stage3_instruction"],
        stage3_confirmation=CONFIG["stage3_confirmation"],
        stage3_instructions=get_stage3_instructions(state),
        policy_instructions=policy_instructions
    )

    return injection

# Command handlers - clean dispatch like autorun5.py
COMMAND_HANDLERS = {
    "SEARCH": handle_search,
    "ALLOW": handle_allow,
    "JUSTIFY": handle_justify,
    "STATUS": handle_status,
    "status": handle_status,  # Add lowercase version for /afst command
    "stop": handle_stop,         # Add lowercase version for /autostop command
    "STOP": handle_stop,
    "emergency_stop": handle_emergency_stop,  # Add lowercase version for /estop command
    "EMERGENCY_STOP": handle_emergency_stop,
    "activate": handle_activate
}


# Claude Code hook handlers - ultra-compact
@handler("UserPromptSubmit")
def claude_code_handler(ctx):
    """Claude Code UserPromptSubmit hook - sync version like autorun5.py"""
    prompt = ctx.prompt.strip()
    session_id = ctx.session_id

    # Efficient command detection - autorun5.py line 144 pattern
    command = next((v for k, v in CONFIG["command_mappings"].items() if k == prompt), None)
    if not command:
        # Check for commands that support arguments (autorun)
        command = next((v for k, v in CONFIG["command_mappings"].items() if prompt.startswith(k)), None)

    if command and command in COMMAND_HANDLERS:
        # Handle command locally, don't send to AI
        with session_state(session_id) as state:
            state['session_id'] = session_id
            if command == "activate":
                response = COMMAND_HANDLERS[command](state, prompt)
                # Autorun command should NOT continue to AI - injection template is complete
                return build_hook_response(False, "", response)
            elif command in ["stop", "emergency_stop"]:
                response = COMMAND_HANDLERS[command](state)
                # Stop commands should NOT continue to AI
                return build_hook_response(False, "", response)
            else:
                response = COMMAND_HANDLERS[command](state)
                # Policy and status commands should continue to AI
                return build_hook_response(True, "", response)

    # Let AI handle non-commands
    return build_hook_response()

@handler("PreToolUse")
def pretooluse_handler(ctx):
    """PreToolUse hook - enhanced policy enforcement based on test expectations"""
    # Extract file path - autorun5.py line 117
    file_path = ctx.tool_input.get("file_path", "")

    # Debug logging using log_info for consistent logging
    log_info(f"PreToolUse Debug: Tool Name: {ctx.tool_name}")
    log_info(f"PreToolUse Debug: File Path: {file_path}")
    log_info(f"PreToolUse Debug: Tool Input: {ctx.tool_input}")

    # Apply file creation policies - enhanced based on test expectations
    session_id = ctx.session_id
    with session_state(session_id) as state:
        file_policy = state.get("file_policy", "ALLOW")

        # For non-Write tools, only apply policy if there's no file path
        if ctx.tool_name != "Write":
            if not file_path:
                # No file path - apply policy restrictions
                if file_policy == "SEARCH":
                    return build_pretooluse_response("deny", f"SEARCH policy: {CONFIG['policies']['SEARCH'][1]}")
                elif file_policy == "JUSTIFY":
                    justification_found = state.get("autofile_justification_detected", False) or \
                                        "AUTOFILE_JUSTIFICATION" in str(ctx.session_transcript)
                    if not justification_found:
                        return build_pretooluse_response("deny", f"JUSTIFY policy: {CONFIG['policies']['JUSTIFY'][1]}")
                # ALLOW policy or default - allow
                return build_pretooluse_response("allow", "Non-Write tool without file path allowed")
            else:
                # Non-Write tool with file path - always allow
                return build_pretooluse_response("allow", "Non-Write tool with file path allowed")

        # Write tools - always apply policy
        if file_policy == "SEARCH":
            # Extensive logging for SEARCH policy enforcement
            log_info("PreToolUse SEARCH Policy Debug:")
            log_info(f"  Current Policy: {file_policy}")
            log_info(f"  File Path: {file_path}")
            log_info(f"  Path Exists: {Path(file_path).exists() if file_path else 'No file path'}")
            log_info(f"  Session State: {state}")

            # SEARCH policy blocks new file creation but allows editing existing files
            if file_path and Path(file_path).exists():
                # File exists - allow editing
                log_info("  Decision: ALLOW (Existing file modification)")
                return build_pretooluse_response("allow", "Existing file modification allowed under SEARCH policy")
            else:
                # No file path or file doesn't exist - block new file creation
                # Use policy description which contains "NO new files" as expected by tests
                log_info("  Decision: DENY (No file or does not exist)")
                return build_pretooluse_response("deny", f"SEARCH policy: {CONFIG['policies']['SEARCH'][1]}")

        elif file_policy == "JUSTIFY":
            justification_found = state.get("autofile_justification_detected", False) or \
                                "AUTOFILE_JUSTIFICATION" in str(ctx.session_transcript)
            if not justification_found:
                return build_pretooluse_response("deny", f"JUSTIFY policy: {CONFIG['policies']['JUSTIFY'][1]}")

        # ALLOW policy - allow all operations
        return build_pretooluse_response("allow", "File operations allowed under ALLOW policy")

# ai_monitor integration: Continuation enforcement functions
def inject_continue_prompt(state):
    """Inject continue working prompt - ai_monitor functionality with monitoring"""
    log_info("Injecting continue working prompt - preventing premature stop")

    # Get injection monitor
    monitor = get_injection_monitor()
    injection_start_time = time.time()
    session_id = state.get("session_id", "unknown")

    # Determine injection method based on current context
    injection_method = InjectionMethod.HOOK_INTEGRATION
    if ai_monitor and state.get("ai_monitor_pid"):
        injection_method = InjectionMethod.TMUX_INJECTION

    # CRITICAL: Use full injection template with stop signal instructions
    # This is NOT a simple continue message - it includes critical stop conditions
    policy = state.get("file_policy", "ALLOW")
    policy_instructions = CONFIG["policies"][policy][1]

    continue_message = CONFIG["injection_template"].format(
        emergency_stop=CONFIG["emergency_stop"],
        stage1_instruction=CONFIG["stage1_instruction"],
        stage1_confirmation=CONFIG["stage1_confirmation"],
        stage2_instruction=CONFIG["stage2_instruction"],
        stage2_confirmation=CONFIG["stage2_confirmation"],
        stage3_instruction=CONFIG["stage3_instruction"],
        stage3_confirmation=CONFIG["stage3_confirmation"],
        stage3_instructions=get_stage3_instructions(state),
        policy_instructions=policy_instructions
    )

    # Record injection attempt
    if monitor:
        try:
            # Calculate context size from transcript
            transcript_length = len(str(state.get("transcript", "")))

            monitor.record_injection_attempt(
                method=injection_method,
                session_id=session_id,
                prompt_type="continue",
                prompt_content=continue_message[:200] + "...",  # Truncate for storage
                outcome=InjectionOutcome.SUCCESS,  # Assume success initially
                response_time_ms=0,  # Will be updated after response
                success_indicators=["Continue prompt injected"],
                context_size=0,
                transcript_length=transcript_length,
                follow_up_required=False,
                user_intervention=False
            )

            # Store injection attempt ID for later response time tracking
            state["last_injection_attempt_id"] = f"{session_id}_{int(injection_start_time * 1000)}"
            state["last_injection_start_time"] = injection_start_time

        except Exception as e:
            log_info(f"Failed to record injection attempt: {e}")

    return build_hook_response(
        continue_execution=True,
        system_message=continue_message
    )

def inject_verification_prompt(state):
    """Inject verification prompt - enhanced two-stage verification with forced compliance and monitoring"""
    verification_attempts = state.get('verification_attempts', 1)
    log_info(f"Injecting verification prompt - attempt {verification_attempts}")

    # Get injection monitor
    monitor = get_injection_monitor()
    injection_start_time = time.time()
    session_id = state.get("session_id", "unknown")

    # Determine injection method and prompt type
    injection_method = InjectionMethod.HOOK_INTEGRATION
    if ai_monitor and state.get("ai_monitor_pid"):
        injection_method = InjectionMethod.TMUX_INJECTION

    # Determine prompt type based on verification attempts
    if verification_attempts > CONFIG["max_recheck_count"] - 1:
        prompt_type = "forced_compliance"
    else:
        prompt_type = "verification"

    # Initialize verification engine if available
    verification_requirements = ""
    if VERIFICATION_ENGINE_AVAILABLE and verification_attempts == 1:
        try:
            engine = RequirementVerificationEngine(state.get("session_id", "default"))
            activation_prompt = state.get("activation_prompt", "")

            # Parse requirements from original task
            requirements = engine.parse_requirements_from_task(activation_prompt)

            if requirements:
                # Format requirements for prompt
                req_text = "8. SPECIFIC REQUIREMENTS TO VERIFY:\n"
                for i, req in enumerate(requirements[:5], 1):  # Limit to 5 requirements
                    req_text += f"   {i}. {req.description} (Type: {req.requirement_type.value}, Mandatory: {req.mandatory})\n"
                verification_requirements = req_text

                # Store engine in state for later use
                state["verification_engine"] = engine

        except Exception as e:
            log_info(f"Verification engine initialization failed: {e}")

    # Choose template based on verification attempts
    if verification_attempts > CONFIG["max_recheck_count"] - 1:
        # Force compliance on final attempt
        template = CONFIG["forced_compliance_template"]
        log_info("Using forced compliance template")
        success_indicators = ["Forced compliance activated"]
    else:
        template = CONFIG["recheck_template"]
        success_indicators = [f"Verification prompt injected (attempt {verification_attempts})"]

    verification_prompt = template.format(
        activation_prompt=state.get("activation_prompt", "original task"),
        stage3_confirmation=CONFIG["stage3_confirmation"],
        recheck_count=verification_attempts,
        max_recheck_count=CONFIG["max_recheck_count"],
        verification_requirements=verification_requirements
    )

    # Record injection attempt
    if monitor:
        try:
            # Calculate context size from transcript
            transcript_length = len(str(state.get("transcript", "")))

            monitor.record_injection_attempt(
                method=injection_method,
                session_id=session_id,
                prompt_type=prompt_type,
                prompt_content=verification_prompt[:200] + "...",  # Truncate for storage
                outcome=InjectionOutcome.SUCCESS,  # Assume success initially
                response_time_ms=0,  # Will be updated after response
                success_indicators=success_indicators,
                context_size=0,
                transcript_length=transcript_length,
                follow_up_required=verification_attempts < CONFIG["max_recheck_count"],
                user_intervention=False
            )

            # Store injection attempt ID for later response time tracking
            state["last_injection_attempt_id"] = f"{session_id}_{int(injection_start_time * 1000)}"
            state["last_injection_start_time"] = injection_start_time

        except Exception as e:
            log_info(f"Failed to record verification injection attempt: {e}")

    return build_hook_response(
        continue_execution=True,
        system_message=verification_prompt
    )

def analyze_verification_results(state, transcript):
    """Analyze verification results using enhanced verification engine and transcript analyzer"""
    if not VERIFICATION_ENGINE_AVAILABLE or "verification_engine" not in state:
        return None

    try:
        engine = state["verification_engine"]
        activation_prompt = state.get("activation_prompt", "")

        # Enhanced transcript analysis using transcript analyzer
        transcript_analysis = None
        if TRANSCRIPT_ANALYZER_AVAILABLE:
            try:
                analyzer = TranscriptAnalyzer()
                transcript_analysis = analyzer.analyze_full_transcript(transcript, state.get("session_id", "default"))

                # Analyze task completion specifically
                task_completion = analyzer.analyze_task_completion(transcript, activation_prompt)
                state["task_completion_analysis"] = task_completion

                log_info(f"Transcript analysis found {transcript_analysis.total_evidence} evidence items")
            except Exception as e:
                log_info(f"Enhanced transcript analysis failed: {e}")

        # Analyze transcript for evidence using verification engine
        evidence_by_requirement = engine.analyze_transcript_evidence(transcript)

        # Enhance evidence with transcript analyzer results
        if transcript_analysis:
            evidence_by_requirement = _enhance_evidence_with_analysis(
                evidence_by_requirement, transcript_analysis
            )

        # Verify each requirement
        results = {}
        for req_id, evidence in evidence_by_requirement.items():
            result = engine.verify_single_requirement(req_id, evidence)
            results[req_id] = result

        # Generate verification report
        report = engine.generate_verification_report()

        # Enhance report with transcript analysis data
        if transcript_analysis:
            report["transcript_analysis"] = {
                "total_evidence": transcript_analysis.total_evidence,
                "confidence_score": transcript_analysis.confidence_score,
                "evidence_summary": transcript_analysis.summary,
                "high_confidence_evidence": transcript_analysis.summary.get("high_confidence_evidence", 0)
            }

        # Check if forced compliance is needed
        failed_mandatory = [
            req_id for req_id, result in results.items()
            if (engine.requirements.get(req_id) and
                engine.requirements[req_id].mandatory and
                result.status != VerificationStatus.COMPLETED)
        ]

        # Use enhanced completion confidence to determine forced compliance
        completion_confidence = state.get("task_completion_analysis", {}).get("completion_confidence", 0.0)
        should_force_compliance = (
            (failed_mandatory and state.get('verification_attempts', 1) >= CONFIG["max_recheck_count"]) or
            (completion_confidence > 0.7 and state.get('verification_attempts', 1) >= CONFIG["max_recheck_count"] - 1)
        )

        if should_force_compliance and failed_mandatory:
            log_info("Forcing compliance for failed mandatory requirements")
            engine.force_requirement_compliance(failed_mandatory)
            report["forced_compliance_reason"] = "Failed mandatory requirements after max attempts"

        # Store enhanced report in state
        state["verification_report"] = report
        state["transcript_analysis_result"] = transcript_analysis

        log_info(f"Enhanced verification analysis complete: {report['summary']['completed']}/{report['summary']['total_requirements']} completed")
        if transcript_analysis:
            log_info(f"Transcript confidence score: {transcript_analysis.confidence_score:.2f}")

        return report

    except Exception as e:
        log_info(f"Enhanced verification analysis failed: {e}")
        return None

def _enhance_evidence_with_analysis(evidence_by_requirement, transcript_analysis):
    """Enhance verification evidence with transcript analyzer results"""
    if not transcript_analysis:
        return evidence_by_requirement

    # Map transcript analyzer evidence types to verification engine evidence
    type_mapping = {
        EvidenceType.FILE_OPERATION: "file_operation",
        EvidenceType.TEST_RESULT: "test_result",
        EvidenceType.SUCCESS_INDICATOR: "success_indicator"
    }

    for req_id, evidence_list in evidence_by_requirement.items():
        # Add high-confidence evidence from transcript analyzer
        for evidence_type, mapped_type in type_mapping.items():
            if evidence_type in transcript_analysis.evidence_by_type:
                for analyzer_evidence in transcript_analysis.evidence_by_type[evidence_type]:
                    if analyzer_evidence.confidence in [ConfidenceLevel.HIGH, ConfidenceLevel.VERY_HIGH]:
                        # Convert analyzer evidence to verification engine format
                        enhanced_evidence = RequirementEvidence(
                            requirement_id=req_id,
                            evidence_type=mapped_type,
                            evidence_data=analyzer_evidence.content,
                            confidence_score=analyzer_evidence.confidence.value,
                            timestamp=analyzer_evidence.timestamp,
                            source_location=f"transcript_analyzer:{analyzer_evidence.position}"
                        )
                        evidence_list.append(enhanced_evidence)

    return evidence_by_requirement

def is_premature_stop(ctx, state):
    """Check if this is a premature stop - ai_monitor logic"""
    # Only active autorun sessions are protected
    if state.get("session_status") != "active":
        return False

    # Get transcript for analysis
    transcript = str(getattr(ctx, 'session_transcript', []))

    # Check if ANY stage confirmation is present
    if (CONFIG["stage1_confirmation"] in transcript or
        CONFIG["stage2_confirmation"] in transcript or
        CONFIG["stage3_confirmation"] in transcript):
        return False  # Proper completion of some stage

    # Check if emergency stop was used
    if CONFIG["emergency_stop"] in transcript:
        return False  # Intentional emergency stop

    return True  # Premature stop - needs intervention

def get_stage3_instructions(state):
    """Get stage 3 instructions based on current state"""
    stage = state.get("autorun_stage", "INITIAL")
    hook_call_count = state.get("hook_call_count", 0)

    if stage == "STAGE2_COMPLETED":
        # After stage 2, start countdown for stage 3
        remaining_calls = CONFIG["stage3_countdown_calls"] - hook_call_count
        if remaining_calls > 0:
            return f"After {remaining_calls} more hook calls, Stage 3 instructions will be revealed. Continue with evaluation."
        else:
            return f"STAGE 3: {CONFIG['stage3_instruction']}. Output **{CONFIG['stage3_confirmation']}** to complete."
    else:
        return "Complete Stage 1 before proceeding to Stage 2."

def should_trigger_stage2(state):
    """Check if we should trigger stage 2"""
    return (state.get("autorun_stage") == "INITIAL" and
            state.get("stage1_completed", False))

def should_trigger_verification(state):
    """Check if we should trigger verification stage"""
    return (state.get("autorun_stage") == "INITIAL" and
            state.get("verification_attempts", 0) < CONFIG["max_recheck_count"])

@handler("Stop")
@handler("SubagentStop")
def stop_handler(ctx):
    """Enhanced stop handler with three-stage completion system and AI monitor lifecycle management"""
    session_id = getattr(ctx, 'session_id', 'default')
    transcript = str(getattr(ctx, 'session_transcript', []))

    with session_state(session_id) as state:
        # Ensure session_id is in state for _manage_monitor
        state['session_id'] = session_id
        # Increment hook call count for stage 3 countdown
        state['hook_call_count'] = state.get('hook_call_count', 0) + 1

        # Only intervene in active autorun sessions
        if state.get("session_status") != "active":
            # Normal cleanup for non-autorun sessions
            state.clear()
            return build_hook_response()

        current_stage = state.get("autorun_stage", "INITIAL")
        log_info(f"Three-stage system: stage={current_stage}, hook_calls={state['hook_call_count']}")

        # STAGE 1: Initial work - check for stage 1 confirmation
        if current_stage == "INITIAL":
            # Check for stage 1 confirmation (AI outputs this to complete stage 1)
            if CONFIG["stage1_confirmation"] in transcript:
                log_info(f"Stage 1 completion detected for session {session_id}")
                state["autorun_stage"] = "STAGE2"
                state["stage1_completed"] = True
                state["stage1_completion_timestamp"] = time.time()

                # Inject stage 2 instructions
                stage2_prompt = f"STAGE 2: {CONFIG['stage2_instruction']}. Output **{CONFIG['stage2_confirmation']}** when complete."
                return build_hook_response(True, "", stage2_prompt)

            # Handle premature stage 3 attempt in stage 1
            elif CONFIG["stage3_confirmation"] in transcript:
                log_info(f"Premature stage 3 attempt detected in stage 1 for session {session_id}")
                stage1_continuation = f"You must complete Stage 1 first. Output **{CONFIG['stage1_confirmation']}** when done."
                return build_hook_response(True, "", stage1_continuation)

            # Handle premature stop (no completion markers)
            elif is_premature_stop(ctx, state):
                log_info(f"Premature stop detected in Stage 1 for session {session_id}")
                return inject_continue_prompt(state)

        # STAGE 2: Critical evaluation
        elif current_stage == "STAGE2":
            # Check for stage 2 confirmation (AI outputs this to complete stage 2)
            if CONFIG["stage2_confirmation"] in transcript:
                log_info(f"Stage 2 completion detected for session {session_id}")
                state["autorun_stage"] = "STAGE2_COMPLETED"
                state["stage2_completion_timestamp"] = time.time()
                state["hook_call_count"] = 0  # Reset countdown for stage 3

                # Start countdown for stage 3
                remaining_calls = CONFIG["stage3_countdown_calls"]
                countdown_msg = f"Stage 2 complete. Continue working for {remaining_calls} more cycles before Stage 3 instructions are revealed."
                return build_hook_response(True, "", countdown_msg)

            # Handle premature stop in stage 2
            elif is_premature_stop(ctx, state):
                log_info(f"Premature stop detected in Stage 2 for session {session_id}")
                stage2_continuation = f"Continue with Stage 2: {CONFIG['stage2_instruction']}. Output **{CONFIG['stage2_confirmation']}** when complete."
                return build_hook_response(True, "", stage2_continuation)

        # STAGE 2 COMPLETED: Countdown to stage 3
        elif current_stage == "STAGE2_COMPLETED":
            hook_call_count = state.get("hook_call_count", 0)
            remaining_calls = CONFIG["stage3_countdown_calls"] - hook_call_count

            # Check if stage 3 confirmation was attempted
            if CONFIG["stage3_confirmation"] in transcript:
                if remaining_calls > 0:
                    # Early attempt - reset to STAGE2 (not INITIAL) to preserve progress
                    log_info(f"Early stage 3 attempt detected, {remaining_calls} calls remaining")
                    reset_msg = f"Too early for Stage 3. Continue with Stage 2: {CONFIG['stage2_instruction']}"
                    state["autorun_stage"] = "STAGE2"
                    # Don't reset stage1_completed - preserve progress
                    return build_hook_response(True, "", reset_msg)
                else:
                    log_info(f"Stage 3 completion detected for session {session_id}")
                    # Proper stage 3 completion - stop monitor and cleanup
                    log_info("Stopping AI monitor after successful stage 3 completion")
                    _manage_monitor(state, 'stop')
                    state.clear()
                    return build_hook_response(False, "", "✅ Three-stage completion successful!")

            # Continue countdown and provide status updates
            elif remaining_calls > 0:
                if hook_call_count % 2 == 0:  # Provide updates every 2 calls
                    status_msg = f"Stage 3 countdown: {remaining_calls} calls remaining. Continue with evaluation."
                    return build_hook_response(True, "", status_msg)
                else:
                    return inject_continue_prompt(state)
            else:
                # Reveal stage 3 instructions
                stage3_instructions = f"STAGE 3: {CONFIG['stage3_instruction']}. Output **{CONFIG['stage3_confirmation']}** to complete."
                return build_hook_response(True, "", stage3_instructions)

        # Fallback: unknown stage or state
        log_info(f"Unknown state in three-stage stop_handler: stage={current_stage}, session={session_id}")
        return inject_continue_prompt(state)

# Default handler
def default_handler(ctx): return build_hook_response()

def main():
    """Entry point - unified with efficient dispatch"""
    operation_mode = os.getenv("AGENT_MODE", "HOOK_INTEGRATION").upper()

    if operation_mode == "HOOK_INTEGRATION":
        # Run as Claude Code hook - same as autorun5.py main()
        try:
            payload = json.loads(sys.stdin.read())
            event = payload.get("hook_event_name", "?")
            _session_id = payload.get("session_id", "?")

            # DEBUG: Log all hook calls to track when script is called
            debug_log = STATE_DIR / "hook_debug.log"
            with open(debug_log, "a") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] HOOK_CALLED: {event} | session: {_session_id} | prompt: {payload.get('prompt', '')[:50]}...\n")

            # Context object - same as autorun5.py
            class Ctx:
                def __init__(self, p):
                    self.hook_event_name = p.get("hook_event_name", "")
                    self.session_id = p.get("session_id", "")
                    self.prompt = p.get("prompt", "")
                    self.tool_name = p.get("tool_name", "")
                    self.tool_input = p.get("tool_input", {})
                    self.session_transcript = p.get("session_transcript", [])

            ctx = Ctx(payload)
            handler = HANDLERS.get(event, default_handler)
            response = handler(ctx)

            print(json.dumps(response, sort_keys=True))
            sys.stdout.flush()

        except Exception:
            print(json.dumps(build_hook_response()))
            sys.exit(1)

    else:
        # Run as standalone Agent SDK - Interactive mode
        run_interactive_sdk(operation_mode)

def run_interactive_sdk(operation_mode: str):
    """Run interactive Agent SDK with clean async/sync separation - autorun5.py efficiency"""
    print("🚀 Agent SDK Command Interceptor - Interactive Mode")
    print("=" * 55)
    print("Commands handled locally (no AI tokens):")

    for cmd, action in CONFIG["command_mappings"].items():
        policy_info = ""
        if action in CONFIG["policies"]:
            policy_name, policy_desc = CONFIG["policies"][action]
            policy_info = f" - {policy_desc}"
        print(f"  {cmd} → {action}{policy_info}")

    print(f"\nEnvironment: AGENT_MODE={operation_mode}")
    print("Type commands (e.g., '/afs', '/afa', '/afj', '/afst') or 'quit' to exit")
    print("Non-commands will be processed by Claude Code via Agent SDK\n")

    # Initialize session state - autorun5.py pattern
    session_id = "interactive_session"
    with session_state(session_id) as state:
        state["file_policy"] = "ALLOW"

    print("✅ Ready for commands...")
    print("💡 One Ctrl+C = interrupt, two Ctrl+C = goodbye\n")

    # Track Ctrl+C count for double-press detection
    ctrl_c_count = 0
    last_ctrl_c_time = 0

    # Main interactive loop - sync input, async processing
    while True:
        try:
            # Get user input synchronously
            user_input = input("❓ ").strip()

            # Reset Ctrl+C count on successful input
            ctrl_c_count = 0

            # Exit conditions
            if user_input.lower() in ('quit', 'exit', 'q'):
                print("👋 Goodbye!")
                break

            if not user_input:
                continue

            # Efficient command detection - autorun5.py line 144 pattern
            command = next((v for k, v in CONFIG["command_mappings"].items() if k == user_input), None)
            if not command:
                # Check for commands that support arguments (autorun)
                command = next((v for k, v in CONFIG["command_mappings"].items() if user_input.startswith(k)), None)

            if command and command in COMMAND_HANDLERS:
                # Handle locally using dispatch pattern - autorun5.py efficiency
                with session_state(session_id) as state:
                    if command == "activate":
                        # Pass the full prompt for activation
                        response = COMMAND_HANDLERS[command](state, user_input)
                    else:
                        response = COMMAND_HANDLERS[command](state)
                    print(f"✅ {response}")

            else:
                # Send to Claude Code via Agent SDK - async operation
                print("🤖 Processing with Claude Code...")
                try:
                    # Run async operation in sync context
                    asyncio.run(process_with_claude_sdk(user_input, session_id))
                except Exception as e:
                    print(f"❌ Claude Code error: {e}")
                    print("💡 Make sure Claude Code is running and accessible")

        except KeyboardInterrupt:
            import time
            current_time = time.time()

            # Check if this is a rapid second Ctrl+C (within 1 second)
            if current_time - last_ctrl_c_time < 1.0:
                ctrl_c_count += 1
            else:
                ctrl_c_count = 1

            last_ctrl_c_time = current_time

            if ctrl_c_count >= 2:
                print("\n👋 Goodbye!")
                break
            else:
                print("\n⚠️ Interrupted. One more Ctrl+C to exit, or continue with a command.")

        except EOFError:
            print("\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"❌ Error: {e}")

async def process_with_claude_sdk(prompt: str, session_id: str):
    """Process non-command prompts with Claude Code via Agent SDK"""
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

    try:
        # Use ClaudeSDKClient for better control
        options = ClaudeAgentOptions()
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt, session_id=session_id)

            async for message in client.receive_response():
                if hasattr(message, 'content'):
                    for block in message.content:
                        if hasattr(block, 'text'):
                            print(block.text, end='', flush=True)
                elif hasattr(message, 'total_cost_usd'):
                    print(f"\n💰 Cost: ${message.total_cost_usd:.4f}")
                    break  # End of response
            print()  # New line at end

    except Exception as e:
        print(f"❌ Agent SDK error: {e}")
        # Fallback - suggest using Claude Code directly
        print("💡 You can ask this question directly in Claude Code")

if __name__ == "__main__":
    main()