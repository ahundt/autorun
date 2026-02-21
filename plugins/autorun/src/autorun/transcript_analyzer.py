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
"""Enhanced Transcript Analyzer for autorun - Advanced evidence detection and analysis"""

import re
import json
import time
from typing import Dict, List, Any
from dataclasses import dataclass, asdict
from enum import Enum

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
        """Fallback logging - file-only when AUTORUN_DEBUG=1"""
        try:
            from .logging_utils import get_logger
            logger = get_logger(__name__)
            logger.info(message)
        except ImportError:
            # If logging_utils not available, do nothing (don't print to avoid stderr)
            pass

# Follow main.py pattern for handlers
ANALYSIS_HANDLERS = {}
def analysis_handler(name):
    """Decorator to register analysis handlers - following main.py pattern"""
    def dec(f):
        ANALYSIS_HANDLERS[name] = f
        return f
    return dec

class EvidenceType(Enum):
    """Types of evidence that can be extracted"""
    FILE_OPERATION = "file_operation"
    CODE_CHANGE = "code_change"
    TEST_RESULT = "test_result"
    ERROR_MESSAGE = "error_message"
    SUCCESS_INDICATOR = "success_indicator"
    TOOL_USAGE = "tool_usage"
    COMMAND_EXECUTION = "command_execution"
    USER_FEEDBACK = "user_feedback"
    SYSTEM_OUTPUT = "system_output"

class ConfidenceLevel(Enum):
    """Confidence levels for evidence"""
    VERY_LOW = 0.2
    LOW = 0.4
    MEDIUM = 0.6
    HIGH = 0.8
    VERY_HIGH = 1.0

@dataclass
class Evidence:
    """Individual piece of evidence from transcript"""
    id: str
    evidence_type: EvidenceType
    content: str
    context: str
    confidence: ConfidenceLevel
    position: int
    timestamp: float
    source: str = "transcript"
    metadata: Dict[str, Any] = None

    def to_dict(self) -> Dict:
        """Convert evidence to dictionary"""
        result = asdict(self)
        result['evidence_type'] = self.evidence_type.value
        result['confidence'] = self.confidence.value
        return result

@dataclass
class AnalysisResult:
    """Result of transcript analysis"""
    session_id: str
    total_evidence: int
    evidence_by_type: Dict[EvidenceType, List[Evidence]]
    summary: Dict[str, Any]
    confidence_score: float
    timestamp: float

class TranscriptAnalyzer:
    """Enhanced transcript analyzer with advanced pattern recognition"""

    def __init__(self):
        self.patterns = self._initialize_patterns()
        self.evidence_id_counter = 0

    def _initialize_patterns(self) -> Dict[EvidenceType, List[Dict]]:
        """Initialize regex patterns for different evidence types"""
        return {
            EvidenceType.FILE_OPERATION: [
                {
                    "pattern": r"(?:created|wrote|added|generated|built)\s+([`'\"]?[\w\./\-]+[`'\"]?)",
                    "confidence": ConfidenceLevel.VERY_HIGH,
                    "description": "File creation"
                },
                {
                    "pattern": r"(?:modified|updated|edited|changed|refactored)\s+([`'\"]?[\w\./\-]+[`'\"]?)",
                    "confidence": ConfidenceLevel.HIGH,
                    "description": "File modification"
                },
                {
                    "pattern": r"(?:deleted|removed|cleaned up)\s+([`'\"]?[\w\./\-]+[`'\"]?)",
                    "confidence": ConfidenceLevel.HIGH,
                    "description": "File deletion"
                },
                {
                    "pattern": r"File\s+([`'\"]?[\w\./\-]+[`'\"]?)\s+(?:created|written|modified|deleted|updated)",
                    "confidence": ConfidenceLevel.VERY_HIGH,
                    "description": "File operation (explicit)"
                },
                {
                    "pattern": r"Add(?:ed|ing)?\s+([`'\"]?[\w\./\-]+[`'\"]?)\s+(?:to|for)",
                    "confidence": ConfidenceLevel.MEDIUM,
                    "description": "File addition"
                }
            ],

            EvidenceType.CODE_CHANGE: [
                {
                    "pattern": r"(?:implemented|wrote|created|added)\s+(?:a\s+)?(?:function|method|class|component)\s+(?:called\s+)?([A-Za-z_]\w*)",
                    "confidence": ConfidenceLevel.HIGH,
                    "description": "Function/class implementation"
                },
                {
                    "pattern": r"(?:refactored|rewrote|improved)\s+(?:the\s+)?([A-Za-z_]\w*)\s+(?:function|method|class)",
                    "confidence": ConfidenceLevel.HIGH,
                    "description": "Code refactoring"
                },
                {
                    "pattern": r"(?:fixed|resolved|debugged)\s+(?:the\s+)?(?:bug|issue|error)\s+(?:in\s+)?([A-Za-z_]\w*)",
                    "confidence": ConfidenceLevel.MEDIUM,
                    "description": "Bug fixing"
                },
                {
                    "pattern": r"(?:imported|required|included)\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)",
                    "confidence": ConfidenceLevel.MEDIUM,
                    "description": "Import statement"
                }
            ],

            EvidenceType.TEST_RESULT: [
                {
                    "pattern": r"(?:All\s+tests?|All\s+specs?)\s+(?:passed|succeeded|completed successfully)",
                    "confidence": ConfidenceLevel.VERY_HIGH,
                    "description": "All tests passed"
                },
                {
                    "pattern": r"(\d+)\s+(?:tests?|specs?)\s+(?:passed|succeeded|failed|errored)",
                    "confidence": ConfidenceLevel.HIGH,
                    "description": "Test count result"
                },
                {
                    "pattern": r"Test\s+(?:suite|run|execution)\s+(?:passed|succeeded|failed|completed)",
                    "confidence": ConfidenceLevel.HIGH,
                    "description": "Test suite result"
                },
                {
                    "pattern": r"[✓✅❌×]\s+.*?(?:test|spec|assertion|requirement)",
                    "confidence": ConfidenceLevel.MEDIUM,
                    "description": "Test indicator (emoji)"
                },
                {
                    "pattern": r"(?:PASSED|FAILED|ERROR|SUCCESS)\s*:?\s*[\w\s\.]+",
                    "confidence": ConfidenceLevel.HIGH,
                    "description": "Test status (uppercase)"
                }
            ],

            EvidenceType.ERROR_MESSAGE: [
                {
                    "pattern": r"(?:Error|Exception|Traceback|Fatal)\s*:?\s*[\w\s\.]+",
                    "confidence": ConfidenceLevel.HIGH,
                    "description": "Error message"
                },
                {
                    "pattern": r"(?:failed|couldn't|unable to|cannot)\s+(?:connect|find|load|read|write|execute)",
                    "confidence": ConfidenceLevel.MEDIUM,
                    "description": "Operation failure"
                },
                {
                    "pattern": r"[Ee]rror\s+(?:code|message|status)?\s*:?\s*\d+",
                    "confidence": ConfidenceLevel.MEDIUM,
                    "description": "Error code"
                }
            ],

            EvidenceType.SUCCESS_INDICATOR: [
                {
                    "pattern": r"(?:success|successful|completed|finished|done|ready)",
                    "confidence": ConfidenceLevel.MEDIUM,
                    "description": "Success indicator"
                },
                {
                    "pattern": r"(?:working|operating|functioning|running)\s+(?:correctly|properly|as expected|successfully)",
                    "confidence": ConfidenceLevel.HIGH,
                    "description": "Functional confirmation"
                },
                {
                    "pattern": r"(?:Great|Perfect|Excellent|Awesome)\s*!?",
                    "confidence": ConfidenceLevel.LOW,
                    "description": "Positive affirmation"
                }
            ],

            EvidenceType.TOOL_USAGE: [
                {
                    "pattern": r"(?:Using|With)\s+(?:the\s+)?(?:[A-Z][a-z]+\s+)?(?:tool|utility|command)",
                    "confidence": ConfidenceLevel.MEDIUM,
                    "description": "Tool usage statement"
                },
                {
                    "pattern": r"(?:Ran|Executed|Called)\s+(?:the\s+)?[A-Z][a-zA-Z]+\s+(?:command|tool|script)",
                    "confidence": ConfidenceLevel.MEDIUM,
                    "description": "Tool execution"
                }
            ],

            EvidenceType.COMMAND_EXECUTION: [
                {
                    "pattern": r"[`'\"]([^`'\"]{10,})[`'\"]",
                    "confidence": ConfidenceLevel.MEDIUM,
                    "description": "Command in quotes"
                },
                {
                    "pattern": r"\$(?:\s+)?([a-zA-Z][a-zA-Z0-9\s\-\./_]+)",
                    "confidence": ConfidenceLevel.MEDIUM,
                    "description": "Shell command"
                }
            ],

            EvidenceType.USER_FEEDBACK: [
                {
                    "pattern": r"(?:I think|I believe|It seems|Looks like|Appears that)",
                    "confidence": ConfidenceLevel.LOW,
                    "description": "User opinion/assessment"
                },
                {
                    "pattern": r"(?:Let me|I'll|I will|I need to|I should)",
                    "confidence": ConfidenceLevel.LOW,
                    "description": "User intention"
                }
            ]
        }

    @analysis_handler("analyze_full_transcript")
    def analyze_full_transcript(self, transcript: str, session_id: str = "default") -> AnalysisResult:
        """Perform comprehensive analysis of full transcript"""
        log_info(f"Starting full transcript analysis for session {session_id}")

        evidence_by_type = {evidence_type: [] for evidence_type in EvidenceType}
        total_evidence = 0

        # Apply all patterns
        for evidence_type, pattern_list in self.patterns.items():
            for pattern_info in pattern_list:
                matches = self._apply_pattern(transcript, pattern_info, evidence_type)
                evidence_by_type[evidence_type].extend(matches)
                total_evidence += len(matches)

        # Post-process and enhance evidence
        evidence_by_type = self._post_process_evidence(evidence_by_type, transcript)

        # Generate summary
        summary = self._generate_summary(evidence_by_type, transcript)

        # Calculate overall confidence score
        confidence_score = self._calculate_confidence_score(evidence_by_type)

        result = AnalysisResult(
            session_id=session_id,
            total_evidence=total_evidence,
            evidence_by_type=evidence_by_type,
            summary=summary,
            confidence_score=confidence_score,
            timestamp=time.time()
        )

        log_info(f"Transcript analysis complete: {total_evidence} evidence items found")
        return result

    @analysis_handler("extract_specific_evidence")
    def extract_specific_evidence(self, transcript: str, evidence_types: List[EvidenceType]) -> Dict[EvidenceType, List[Evidence]]:
        """Extract only specific types of evidence"""
        evidence_by_type = {evidence_type: [] for evidence_type in evidence_types}

        for evidence_type in evidence_types:
            if evidence_type in self.patterns:
                for pattern_info in self.patterns[evidence_type]:
                    matches = self._apply_pattern(transcript, pattern_info, evidence_type)
                    evidence_by_type[evidence_type].extend(matches)

        return evidence_by_type

    @analysis_handler("analyze_task_completion")
    def analyze_task_completion(self, transcript: str, original_task: str) -> Dict[str, Any]:
        """Analyze evidence of task completion"""
        evidence_by_type = self.extract_specific_evidence(
            transcript,
            [EvidenceType.FILE_OPERATION, EvidenceType.TEST_RESULT, EvidenceType.SUCCESS_INDICATOR]
        )

        # Count evidence by type
        file_operations = len(evidence_by_type.get(EvidenceType.FILE_OPERATION, []))
        test_results = len(evidence_by_type.get(EvidenceType.TEST_RESULT, []))
        success_indicators = len(evidence_by_type.get(EvidenceType.SUCCESS_INDICATOR, []))

        # Check for completion markers
        completion_patterns = [
            r"(?:task|project|work)\s+(?:complete|finished|done)",
            r"(?:everything|all)\s+(?:complete|finished|done|implemented)",
            r"(?:ready|complete|finished|done)",
            CONFIG.get("completion_marker", "")
        ]

        completion_matches = []
        for pattern in completion_patterns:
            if pattern:
                matches = list(re.finditer(pattern, transcript, re.IGNORECASE))
                completion_matches.extend(matches)

        # Calculate completion confidence
        completion_confidence = min(1.0, (
            file_operations * 0.3 +
            test_results * 0.4 +
            success_indicators * 0.2 +
            len(completion_matches) * 0.5
        ) / 3.0)

        return {
            "file_operations_count": file_operations,
            "test_results_count": test_results,
            "success_indicators_count": success_indicators,
            "completion_matches": len(completion_matches),
            "completion_confidence": completion_confidence,
            "evidence_summary": {
                evidence_type.value: len(evidence_list)
                for evidence_type, evidence_list in evidence_by_type.items()
                if evidence_list
            }
        }

    def _apply_pattern(self, transcript: str, pattern_info: Dict, evidence_type: EvidenceType) -> List[Evidence]:
        """Apply a single pattern to extract evidence"""
        matches = []
        pattern = pattern_info["pattern"]
        confidence = pattern_info["confidence"]

        try:
            regex_matches = re.finditer(pattern, transcript, re.IGNORECASE | re.MULTILINE | re.DOTALL)
            for match in regex_matches:
                # Generate unique evidence ID
                self.evidence_id_counter += 1
                evidence_id = f"{evidence_type.value}_{self.evidence_id_counter}"

                # Extract context around the match
                start_pos = max(0, match.start() - 100)
                end_pos = min(len(transcript), match.end() + 100)
                context = transcript[start_pos:end_pos].strip()

                # Create evidence object
                evidence = Evidence(
                    id=evidence_id,
                    evidence_type=evidence_type,
                    content=match.group(0),
                    context=context,
                    confidence=confidence,
                    position=match.start(),
                    timestamp=time.time(),
                    metadata={
                        "pattern_description": pattern_info["description"],
                        "match_groups": list(match.groups()),
                        "pattern": pattern
                    }
                )
                matches.append(evidence)

        except re.error as e:
            log_info(f"Regex error in pattern {pattern}: {e}")

        return matches

    def _post_process_evidence(self, evidence_by_type: Dict[EvidenceType, List[Evidence]], transcript: str) -> Dict[EvidenceType, List[Evidence]]:
        """Post-process evidence to remove duplicates and enhance quality"""
        processed_evidence = {evidence_type: [] for evidence_type in EvidenceType}

        for evidence_type, evidence_list in evidence_by_type.items():
            # Remove duplicates based on content similarity
            seen_content = set()
            unique_evidence = []

            for evidence in evidence_list:
                content_key = evidence.content.lower().strip()
                if content_key not in seen_content:
                    seen_content.add(content_key)
                    unique_evidence.append(evidence)

            # Sort by confidence (highest first) then by position
            unique_evidence.sort(key=lambda e: (e.confidence.value, -e.position), reverse=True)

            processed_evidence[evidence_type] = unique_evidence

        return processed_evidence

    def _generate_summary(self, evidence_by_type: Dict[EvidenceType, List[Evidence]], transcript: str) -> Dict[str, Any]:
        """Generate summary statistics from evidence"""
        summary = {
            "total_evidence": sum(len(evidence_list) for evidence_list in evidence_by_type.values()),
            "evidence_counts": {
                evidence_type.value: len(evidence_list)
                for evidence_type, evidence_list in evidence_by_type.items()
                if evidence_list
            },
            "high_confidence_evidence": 0,
            "medium_confidence_evidence": 0,
            "low_confidence_evidence": 0
        }

        # Count by confidence levels
        for evidence_list in evidence_by_type.values():
            for evidence in evidence_list:
                if evidence.confidence in [ConfidenceLevel.HIGH, ConfidenceLevel.VERY_HIGH]:
                    summary["high_confidence_evidence"] += 1
                elif evidence.confidence == ConfidenceLevel.MEDIUM:
                    summary["medium_confidence_evidence"] += 1
                else:
                    summary["low_confidence_evidence"] += 1

        # Add transcript statistics
        summary["transcript_length"] = len(transcript)
        summary["transcript_words"] = len(transcript.split())

        return summary

    def _calculate_confidence_score(self, evidence_by_type: Dict[EvidenceType, List[Evidence]]) -> float:
        """Calculate overall confidence score from evidence"""
        if not any(evidence_by_type.values()):
            return 0.0

        total_weight = 0.0
        weighted_confidence = 0.0

        # Weight different evidence types differently
        type_weights = {
            EvidenceType.FILE_OPERATION: 1.2,
            EvidenceType.TEST_RESULT: 1.5,
            EvidenceType.SUCCESS_INDICATOR: 1.0,
            EvidenceType.CODE_CHANGE: 1.3,
            EvidenceType.ERROR_MESSAGE: 0.8,  # Negative evidence
            EvidenceType.TOOL_USAGE: 0.6,
            EvidenceType.COMMAND_EXECUTION: 0.7,
            EvidenceType.USER_FEEDBACK: 0.4,
            EvidenceType.SYSTEM_OUTPUT: 0.5
        }

        for evidence_type, evidence_list in evidence_by_type.items():
            weight = type_weights.get(evidence_type, 1.0)
            for evidence in evidence_list:
                confidence_value = evidence.confidence.value

                # Reduce confidence for error evidence
                if evidence_type == EvidenceType.ERROR_MESSAGE:
                    confidence_value = 1.0 - confidence_value

                total_weight += weight
                weighted_confidence += confidence_value * weight

        return min(1.0, weighted_confidence / total_weight if total_weight > 0 else 0.0)

    @analysis_handler("export_analysis")
    def export_analysis(self, result: AnalysisResult, format: str = "json") -> str:
        """Export analysis result in specified format"""
        if format.lower() == "json":
            return self._export_json(result)
        elif format.lower() == "csv":
            return self._export_csv(result)
        else:
            return self._export_text(result)

    def _export_json(self, result: AnalysisResult) -> str:
        """Export analysis result as JSON"""
        export_data = {
            "session_id": result.session_id,
            "timestamp": result.timestamp,
            "summary": result.summary,
            "confidence_score": result.confidence_score,
            "total_evidence": result.total_evidence,
            "evidence_by_type": {
                evidence_type.value: [e.to_dict() for e in evidence_list]
                for evidence_type, evidence_list in result.evidence_by_type.items()
                if evidence_list
            }
        }
        return json.dumps(export_data, indent=2)

    def _export_csv(self, result: AnalysisResult) -> str:
        """Export analysis result as CSV"""
        lines = ["evidence_id,evidence_type,content,confidence,position"]

        for evidence_type, evidence_list in result.evidence_by_type.items():
            for evidence in evidence_list:
                lines.append(f"{evidence.id},{evidence.evidence_type.value},\"{evidence.content}\",{evidence.confidence.value},{evidence.position}")

        return "\n".join(lines)

    def _export_text(self, result: AnalysisResult) -> str:
        """Export analysis result as formatted text"""
        lines = [
            "Transcript Analysis Report",
            f"Session: {result.session_id}",
            f"Timestamp: {time.ctime(result.timestamp)}",
            f"Total Evidence: {result.total_evidence}",
            f"Confidence Score: {result.confidence_score:.2f}",
            "",
            "Evidence by Type:"
        ]

        for evidence_type, evidence_list in result.evidence_by_type.items():
            if evidence_list:
                lines.append(f"  {evidence_type.value.title()}: {len(evidence_list)} items")
                for evidence in evidence_list[:5]:  # Show first 5 items
                    lines.append(f"    - {evidence.content[:80]}...")
                if len(evidence_list) > 5:
                    lines.append(f"    ... and {len(evidence_list) - 5} more")

        return "\n".join(lines)

# Export main functions
__all__ = [
    'TranscriptAnalyzer',
    'Evidence',
    'AnalysisResult',
    'EvidenceType',
    'ConfidenceLevel'
]