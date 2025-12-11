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
"""Enhanced Verification Engine for clautorun - Two-stage verification with forced compliance"""

import re
import time
from typing import Dict, List
from dataclasses import dataclass
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
        """Fallback logging"""
        print(f"INFO: {message}")

# Follow main.py pattern for handlers
VERIFICATION_HANDLERS = {}
def verification_handler(name):
    """Decorator to register verification handlers - following main.py pattern"""
    def dec(f):
        VERIFICATION_HANDLERS[name] = f
        return f
    return dec

class VerificationStatus(Enum):
    """Verification status enumeration"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    FORCED_COMPLIANCE = "forced_compliance"

class RequirementType(Enum):
    """Types of requirements to verify"""
    FUNCTIONAL = "functional"  # Must work as specified
    SECURITY = "security"      # Must be secure/safe
    PERFORMANCE = "performance" # Must meet performance criteria
    DOCUMENTATION = "documentation" # Must be documented
    TESTING = "testing"       # Must have tests

@dataclass
class VerificationRequirement:
    """Individual verification requirement"""
    id: str
    description: str
    requirement_type: RequirementType
    mandatory: bool = True
    verification_method: str = "automated"
    evidence_patterns: List[str] = None
    success_criteria: str = ""
    weight: float = 1.0

@dataclass
class RequirementEvidence:
    """Evidence collected for requirement verification"""
    requirement_id: str
    evidence_type: str  # "file_content", "test_output", "code_analysis", etc.
    evidence_data: str
    confidence_score: float
    timestamp: float
    source_location: str = ""

@dataclass
class VerificationResult:
    """Result of requirement verification"""
    requirement_id: str
    status: VerificationStatus
    confidence_score: float
    evidence: List[RequirementEvidence]
    notes: str = ""
    forced_compliance: bool = False

class RequirementVerificationEngine:
    """Enhanced verification engine with forced compliance capabilities"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.requirements: Dict[str, VerificationRequirement] = {}
        self.results: Dict[str, VerificationResult] = {}
        self.transcript_analyzer = TranscriptAnalyzer()

    @verification_handler("parse_requirements")
    def parse_requirements_from_task(self, task_description: str) -> List[VerificationRequirement]:
        """Parse verification requirements from task description using NLP patterns"""
        requirements = []

        # Pattern matching for different requirement types
        patterns = {
            RequirementType.FUNCTIONAL: [
                r"create\s+(?:a\s+)?(\w+(?:\s+\w+)*)\s+(?:that|which|who)",
                r"implement\s+(\w+(?:\s+\w+)*)",
                r"build\s+(?:a\s+)?(\w+(?:\s+\w+)*)",
                r"develop\s+(?:a\s+)?(\w+(?:\s+\w+)*)"
            ],
            RequirementType.SECURITY: [
                r"secure(?:ly)?",
                r"authentication",
                r"authorization",
                r"protect(?:ion|ed)?",
                r"validate(?:ion)?",
                r"sanitize(?:ation)?"
            ],
            RequirementType.PERFORMANCE: [
                r"fast(?:er|est)?",
                r"efficient(?:ly)?",
                r"optimize(?:d|ation)?",
                r"performance",
                r"speed",
                r"latency",
                r"throughput"
            ],
            RequirementType.DOCUMENTATION: [
                r"document(?:ation|ed)?",
                r"readme",
                r"api\s+docs?",
                r"usage\s+instruction",
                r"examples?"
            ],
            RequirementType.TESTING: [
                r"test(?:s|ing|ed)?",
                r"unit\s+test",
                r"integration\s+test",
                r"spec(?:ification)?",
                r"validate(?:ion)?",
                r"verify(?:ication)?"
            ]
        }

        # Extract requirements using patterns
        for req_type, pattern_list in patterns.items():
            for pattern in pattern_list:
                matches = re.finditer(pattern, task_description, re.IGNORECASE)
                for match in matches:
                    req_id = f"{req_type.value}_{len(requirements) + 1}"
                    description = match.group(0) if match.groups() else match.group(0)

                    requirement = VerificationRequirement(
                        id=req_id,
                        description=description,
                        requirement_type=req_type,
                        mandatory=req_type in [RequirementType.FUNCTIONAL, RequirementType.SECURITY],
                        verification_method="automated",
                        evidence_patterns=self._generate_evidence_patterns(req_type, description),
                        weight=1.0 if req_type == RequirementType.FUNCTIONAL else 0.8
                    )
                    requirements.append(requirement)

        # Store requirements
        for req in requirements:
            self.requirements[req.id] = req

        log_info(f"Parsed {len(requirements)} verification requirements from task")
        return requirements

    @verification_handler("analyze_transcript")
    def analyze_transcript_evidence(self, transcript_text: str) -> Dict[str, List[RequirementEvidence]]:
        """Analyze transcript for evidence of requirement fulfillment"""
        evidence_by_requirement = {}

        for req_id, requirement in self.requirements.items():
            evidence = []

            # Look for evidence patterns in transcript
            if requirement.evidence_patterns:
                for pattern in requirement.evidence_patterns:
                    matches = re.finditer(pattern, transcript_text, re.IGNORECASE | re.MULTILINE)
                    for match in matches:
                        # Extract context around the match
                        start = max(0, match.start() - 200)
                        end = min(len(transcript_text), match.end() + 200)
                        context = transcript_text[start:end].strip()

                        evidence_item = RequirementEvidence(
                            requirement_id=req_id,
                            evidence_type="transcript_match",
                            evidence_data=context,
                            confidence_score=0.7,  # Base confidence for transcript matches
                            timestamp=time.time(),
                            source_location=f"transcript:{match.start()}-{match.end()}"
                        )
                        evidence.append(evidence_item)

            # Look for file creation/modification evidence
            file_patterns = [
                r"(?:created|wrote|modified|updated)\s+([`']?[\w\./-]+[`']?)",
                r"file\s+([`']?[\w\./-]+[`']?)\s+(?:created|written|modified)",
                r"Add(?:ed|ing)?\s+([`']?[\w\./-]+[`']?)"
            ]

            for pattern in file_patterns:
                matches = re.finditer(pattern, transcript_text, re.IGNORECASE)
                for match in matches:
                    filename = match.group(1).strip('`\'"')
                    if filename and '.' in filename:  # Likely a real file
                        evidence_item = RequirementEvidence(
                            requirement_id=req_id,
                            evidence_type="file_operation",
                            evidence_data=f"File {filename} referenced in transcript",
                            confidence_score=0.8,
                            timestamp=time.time(),
                            source_location=f"file:{filename}"
                        )
                        evidence.append(evidence_item)

            evidence_by_requirement[req_id] = evidence

        log_info(f"Analyzed transcript evidence for {len(evidence_by_requirement)} requirements")
        return evidence_by_requirement

    @verification_handler("verify_requirement")
    def verify_single_requirement(self, requirement_id: str, evidence: List[RequirementEvidence]) -> VerificationResult:
        """Verify a single requirement based on collected evidence"""
        if requirement_id not in self.requirements:
            return VerificationResult(
                requirement_id=requirement_id,
                status=VerificationStatus.FAILED,
                confidence_score=0.0,
                evidence=[],
                notes="Requirement not found"
            )

        requirement = self.requirements[requirement_id]

        # Calculate confidence based on evidence
        if not evidence:
            confidence = 0.0
            status = VerificationStatus.FAILED
            notes = "No evidence found for requirement"
        else:
            # Weight evidence by type and confidence
            total_weight = 0.0
            weighted_confidence = 0.0

            for evidence_item in evidence:
                weight = 1.0
                if evidence_item.evidence_type == "file_operation":
                    weight = 1.2  # File operations are strong evidence
                elif evidence_item.evidence_type == "test_output":
                    weight = 1.5  # Test results are very strong evidence
                elif evidence_item.evidence_type == "transcript_match":
                    weight = 0.8  # Transcript matches are moderate evidence

                total_weight += weight
                weighted_confidence += evidence_item.confidence_score * weight

            confidence = weighted_confidence / total_weight if total_weight > 0 else 0.0

            # Determine status based on confidence and requirement type
            if requirement.mandatory:
                threshold = 0.8
            else:
                threshold = 0.6

            if confidence >= threshold:
                status = VerificationStatus.COMPLETED
                notes = f"Requirement verified with {confidence:.2f} confidence"
            else:
                status = VerificationStatus.FAILED
                notes = f"Insufficient evidence: {confidence:.2f} < {threshold}"

        result = VerificationResult(
            requirement_id=requirement_id,
            status=status,
            confidence_score=confidence,
            evidence=evidence,
            notes=notes
        )

        self.results[requirement_id] = result
        return result

    @verification_handler("force_compliance")
    def force_requirement_compliance(self, requirement_ids: List[str] = None) -> Dict[str, VerificationResult]:
        """Force compliance for failed requirements - emergency bypass mechanism"""
        if requirement_ids is None:
            # Force compliance for all failed mandatory requirements
            requirement_ids = [
                req_id for req_id, req in self.requirements.items()
                if req.mandatory and (
                    req_id not in self.results or
                    self.results[req_id].status != VerificationStatus.COMPLETED
                )
            ]

        forced_results = {}
        for req_id in requirement_ids:
            if req_id in self.requirements:
                # Create forced compliance result
                forced_result = VerificationResult(
                    requirement_id=req_id,
                    status=VerificationStatus.FORCED_COMPLIANCE,
                    confidence_score=1.0,  # Forced compliance gives full confidence
                    evidence=self.results.get(req_id, VerificationResult(req_id, VerificationStatus.FAILED, 0.0, [])).evidence,
                    notes="FORCED COMPLIANCE: Requirement auto-approved by system override",
                    forced_compliance=True
                )

                self.results[req_id] = forced_result
                forced_results[req_id] = forced_result

                log_info(f"Forced compliance for requirement {req_id}")

        return forced_results

    @verification_handler("generate_verification_report")
    def generate_verification_report(self) -> Dict:
        """Generate comprehensive verification report"""
        total_requirements = len(self.requirements)
        completed_requirements = sum(1 for r in self.results.values() if r.status == VerificationStatus.COMPLETED)
        failed_requirements = sum(1 for r in self.results.values() if r.status == VerificationStatus.FAILED)
        forced_requirements = sum(1 for r in self.results.values() if r.forced_compliance)

        overall_score = 0.0
        if total_requirements > 0:
            weighted_scores = []
            for req_id, result in self.results.items():
                if req_id in self.requirements:
                    weight = self.requirements[req_id].weight
                    score = result.confidence_score
                    weighted_scores.append(weight * score)

            if weighted_scores:
                total_weight = sum(self.requirements[req_id].weight for req_id in self.results.keys() if req_id in self.requirements)
                overall_score = sum(weighted_scores) / total_weight if total_weight > 0 else 0.0

        report = {
            "session_id": self.session_id,
            "timestamp": time.time(),
            "summary": {
                "total_requirements": total_requirements,
                "completed": completed_requirements,
                "failed": failed_requirements,
                "forced_compliance": forced_requirements,
                "overall_score": overall_score,
                "completion_rate": completed_requirements / total_requirements if total_requirements > 0 else 0.0
            },
            "requirements": {},
            "recommendations": []
        }

        # Add detailed requirement results
        for req_id, requirement in self.requirements.items():
            result = self.results.get(req_id, VerificationResult(req_id, VerificationStatus.PENDING, 0.0, []))
            report["requirements"][req_id] = {
                "description": requirement.description,
                "type": requirement.requirement_type.value,
                "mandatory": requirement.mandatory,
                "status": result.status.value,
                "confidence": result.confidence_score,
                "evidence_count": len(result.evidence),
                "notes": result.notes,
                "forced_compliance": result.forced_compliance
            }

        # Generate recommendations
        if failed_requirements > 0:
            report["recommendations"].append(f"{failed_requirements} requirements failed verification")

        if forced_requirements > 0:
            report["recommendations"].append(f"{forced_requirements} requirements required forced compliance")

        if overall_score < 0.8:
            report["recommendations"].append("Overall verification score below acceptable threshold")

        return report

    def _generate_evidence_patterns(self, req_type: RequirementType, description: str) -> List[str]:
        """Generate evidence patterns based on requirement type and description"""
        patterns = []

        if req_type == RequirementType.FUNCTIONAL:
            # Look for implementation evidence
            patterns.extend([
                r"(?:created|implemented|built|developed)\s+.*?(?:class|function|method|component)",
                r"(?:wrote|added)\s+.*?(?:code|logic|implementation)",
                r"(?:test(?:ing|ed)?)\s+.*?(?:passed|works?|functioning)",
                r"(?:success|complete|finished)\s+.*?(?:implementation|creation|build)"
            ])

        elif req_type == RequirementType.SECURITY:
            patterns.extend([
                r"(?:secured|protected|validated|sanitized)",
                r"(?:authentication|authorization|access control)",
                r"(?:security|safe|protected)",
                r"(?:input\s+validation|output\s+encoding|sql\s+injection|xss)"
            ])

        elif req_type == RequirementType.PERFORMANCE:
            patterns.extend([
                r"(?:optimized|improved|enhanced)\s+.*?(?:performance|speed|efficiency)",
                r"(?:fast|quick|responsive)",
                r"(?:benchmark|measure(?:ment)?)",
                r"(?:cache|async|parallel)"
            ])

        elif req_type == RequirementType.DOCUMENTATION:
            patterns.extend([
                r"(?:documented|documented?|explained)",
                r"(?:readme|doc|guide|tutorial)",
                r"(?:usage|example|instruction)",
                r"(?:api\s+doc(?:umentation)?)"
            ])

        elif req_type == RequirementType.TESTING:
            patterns.extend([
                r"(?:test(?:ed|ing)?|spec(?:ified)?|verified)",
                r"(?:unit\s+test|integration\s+test|test\s+case)",
                r"(?:assert|expect|should|must)",
                r"(?:pass|passed|success|successful)"
            ])

        return patterns

class TranscriptAnalyzer:
    """Helper class for analyzing conversation transcripts"""

    def __init__(self):
        self.file_operations = []
        self.code_changes = []
        self.test_results = []

    def extract_file_operations(self, transcript: str) -> List[Dict]:
        """Extract file creation/modification operations from transcript"""
        operations = []

        patterns = [
            r"(?:created|wrote|added)\s+([`']?[\w\./-]+[`']?)",
            r"(?:modified|updated|edited)\s+([`']?[\w\./-]+[`']?)",
            r"(?:deleted|removed)\s+([`']?[\w\./-]+[`']?)",
            r"File\s+([`']?[\w\./-]+[`']?)\s+(?:created|written|modified|deleted)"
        ]

        for pattern in patterns:
            matches = re.finditer(pattern, transcript, re.IGNORECASE | re.MULTILINE)
            for match in matches:
                operations.append({
                    "filename": match.group(1).strip('`\'"'),
                    "operation": "file_operation",
                    "context": match.group(0),
                    "position": match.start()
                })

        return operations

    def extract_test_results(self, transcript: str) -> List[Dict]:
        """Extract test execution results from transcript"""
        results = []

        patterns = [
            r"(?:test|tests?|spec|specs?)\s+(?:passed|passing|failed|failing|successful|success)",
            r"(?:All\s+tests?|All\s+specs?)\s+(?:passed|passed\s+successfully)",
            r"(\d+)\s+(?:tests?|specs?)\s+(?:passed|failed)",
            r"Test\s+(?:suite|run|execution)\s+(?:completed|finished|passed|failed)",
            r"(?:✓|✅|❌|×)\s+.*?(?:test|spec|assertion)"
        ]

        for pattern in patterns:
            matches = re.finditer(pattern, transcript, re.IGNORECASE | re.MULTILINE)
            for match in matches:
                results.append({
                    "result": match.group(0),
                    "operation": "test_result",
                    "context": match.group(0),
                    "position": match.start()
                })

        return results

# Export main functions
__all__ = [
    'RequirementVerificationEngine',
    'VerificationRequirement',
    'VerificationResult',
    'RequirementEvidence',
    'VerificationStatus',
    'RequirementType',
    'TranscriptAnalyzer'
]