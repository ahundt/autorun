#!/usr/bin/env python3
"""Unit tests for verification engine"""

import pytest
import tempfile
import json
import time
from pathlib import Path
import sys
import os

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

try:
    from clautorun.verification_engine import (
        RequirementVerificationEngine,
        VerificationRequirement,
        VerificationResult,
        RequirementEvidence,
        VerificationStatus,
        RequirementType,
        TranscriptAnalyzer
    )
    VERIFICATION_ENGINE_AVAILABLE = True
except ImportError:
    VERIFICATION_ENGINE_AVAILABLE = False
    pytest.skip("Verification engine not available", allow_module_level=True)


class TestRequirementVerificationEngine:
    """Test suite for requirement verification engine"""

    def setup_method(self):
        """Set up test environment"""
        if VERIFICATION_ENGINE_AVAILABLE:
            self.engine = RequirementVerificationEngine("test_session")
            self.analyzer = TranscriptAnalyzer()

    def test_parse_requirements_functional(self):
        """Test parsing functional requirements from task description"""
        if not VERIFICATION_ENGINE_AVAILABLE:
            pytest.skip("Verification engine not available")

        task = "Create a user authentication system with login and registration"
        requirements = self.engine.parse_requirements_from_task(task)

        assert len(requirements) > 0

        # Check for functional requirements
        functional_reqs = [r for r in requirements if r.requirement_type == RequirementType.FUNCTIONAL]
        assert len(functional_reqs) > 0

        # Verify requirement structure
        for req in requirements:
            assert req.id
            assert req.description
            assert req.requirement_type
            assert isinstance(req.mandatory, bool)

    def test_parse_requirements_mixed_types(self):
        """Test parsing requirements with mixed types"""
        if not VERIFICATION_ENGINE_AVAILABLE:
            pytest.skip("Verification engine not available")

        task = """
        Build a secure API with performance optimization and comprehensive testing.
        Document all endpoints and ensure authentication is properly implemented.
        """
        requirements = self.engine.parse_requirements_from_task(task)

        # Should find multiple requirement types
        types_found = {r.requirement_type for r in requirements}
        expected_types = {
            RequirementType.FUNCTIONAL,
            RequirementType.SECURITY,
            RequirementType.PERFORMANCE,
            RequirementType.TESTING,
            RequirementType.DOCUMENTATION
        }

        # Should find at least functional and security requirements
        assert RequirementType.FUNCTIONAL in types_found
        assert RequirementType.SECURITY in types_found

    def test_analyze_transcript_evidence(self):
        """Test transcript analysis for evidence"""
        if not VERIFICATION_ENGINE_AVAILABLE:
            pytest.skip("Verification engine not available")

        # Create sample transcript with evidence
        transcript = """
        I created a new authentication system with login functionality.
        The user login form was implemented in auth/login.py
        I also added comprehensive tests for the authentication module.

        Created: auth/models.py, auth/views.py, auth/tests/test_auth.py
        All tests passed successfully with 100% coverage.
        """

        # First add some requirements
        task = "Create authentication system with tests"
        requirements = self.engine.parse_requirements_from_task(task)

        # Analyze transcript
        evidence = self.engine.analyze_transcript_evidence(transcript)

        assert len(evidence) > 0

        # Check that evidence was found for requirements
        for req_id, evidence_list in evidence.items():
            if evidence_list:  # If evidence was found
                for ev in evidence_list:
                    assert ev.requirement_id == req_id
                    assert ev.evidence_type
                    assert ev.evidence_data
                    assert 0 <= ev.confidence_score <= 1

    def test_verify_single_requirement_with_evidence(self):
        """Test verifying a single requirement with evidence"""
        if not VERIFICATION_ENGINE_AVAILABLE:
            pytest.skip("Verification engine not available")

        # Create a requirement
        req = VerificationRequirement(
            id="test_req_1",
            description="Create authentication system",
            requirement_type=RequirementType.FUNCTIONAL,
            mandatory=True
        )
        self.engine.requirements[req.id] = req

        # Create evidence
        evidence = [
            RequirementEvidence(
                requirement_id=req.id,
                evidence_type="file_operation",
                evidence_data="Created auth/login.py",
                confidence_score=0.9,
                timestamp=time.time(),
                source_location="file:auth/login.py"
            ),
            RequirementEvidence(
                requirement_id=req.id,
                evidence_type="transcript_match",
                evidence_data="Authentication system created successfully",
                confidence_score=0.8,
                timestamp=time.time(),
                source_location="transcript:100-150"
            )
        ]

        # Verify requirement
        result = self.engine.verify_single_requirement(req.id, evidence)

        assert result.requirement_id == req.id
        assert result.status == VerificationStatus.COMPLETED
        assert result.confidence_score > 0.8
        assert len(result.evidence) == 2

    def test_verify_requirement_insufficient_evidence(self):
        """Test verifying requirement with insufficient evidence"""
        if not VERIFICATION_ENGINE_AVAILABLE:
            pytest.skip("Verification engine not available")

        # Create a mandatory requirement
        req = VerificationRequirement(
            id="test_req_2",
            description="Implement secure authentication",
            requirement_type=RequirementType.SECURITY,
            mandatory=True
        )
        self.engine.requirements[req.id] = req

        # Create weak evidence
        evidence = [
            RequirementEvidence(
                requirement_id=req.id,
                evidence_type="transcript_match",
                evidence_data="Maybe should add security later",
                confidence_score=0.3,
                timestamp=time.time(),
                source_location="transcript:200"
            )
        ]

        # Verify requirement
        result = self.engine.verify_single_requirement(req.id, evidence)

        assert result.requirement_id == req.id
        assert result.status == VerificationStatus.FAILED
        assert result.confidence_score < 0.6

    def test_force_compliance(self):
        """Test forced compliance mechanism"""
        if not VERIFICATION_ENGINE_AVAILABLE:
            pytest.skip("Verification engine not available")

        # Create some failed mandatory requirements
        req1 = VerificationRequirement(
            id="failed_req_1",
            description="Critical functionality",
            requirement_type=RequirementType.FUNCTIONAL,
            mandatory=True
        )
        req2 = VerificationRequirement(
            id="failed_req_2",
            description="Security implementation",
            requirement_type=RequirementType.SECURITY,
            mandatory=True
        )

        self.engine.requirements[req1.id] = req1
        self.engine.requirements[req2.id] = req2

        # Mark them as failed
        self.engine.results[req1.id] = VerificationResult(
            requirement_id=req1.id,
            status=VerificationStatus.FAILED,
            confidence_score=0.3,
            evidence=[]
        )
        self.engine.results[req2.id] = VerificationResult(
            requirement_id=req2.id,
            status=VerificationStatus.FAILED,
            confidence_score=0.5,
            evidence=[]
        )

        # Force compliance
        forced_results = self.engine.force_requirement_compliance([req1.id, req2.id])

        assert len(forced_results) == 2
        assert forced_results[req1.id].status == VerificationStatus.FORCED_COMPLIANCE
        assert forced_results[req2.id].status == VerificationStatus.FORCED_COMPLIANCE
        assert forced_results[req1.id].forced_compliance is True
        assert forced_results[req1.id].confidence_score == 1.0

    def test_generate_verification_report(self):
        """Test verification report generation"""
        if not VERIFICATION_ENGINE_AVAILABLE:
            pytest.skip("Verification engine not available")

        # Create mixed requirements
        req1 = VerificationRequirement(
            id="req_1",
            description="Completed feature",
            requirement_type=RequirementType.FUNCTIONAL,
            mandatory=True,
            weight=1.0
        )
        req2 = VerificationRequirement(
            id="req_2",
            description="Failed feature",
            requirement_type=RequirementType.FUNCTIONAL,
            mandatory=True,
            weight=1.0
        )
        req3 = VerificationRequirement(
            id="req_3",
            description="Optional feature",
            requirement_type=RequirementType.DOCUMENTATION,
            mandatory=False,
            weight=0.5
        )

        self.engine.requirements.update({req1.id: req1, req2.id: req2, req3.id: req3})

        # Add results
        self.engine.results[req1.id] = VerificationResult(
            requirement_id=req1.id,
            status=VerificationStatus.COMPLETED,
            confidence_score=0.9,
            evidence=[]
        )
        self.engine.results[req2.id] = VerificationResult(
            requirement_id=req2.id,
            status=VerificationStatus.FAILED,
            confidence_score=0.4,
            evidence=[]
        )
        self.engine.results[req3.id] = VerificationResult(
            requirement_id=req3.id,
            status=VerificationStatus.FORCED_COMPLIANCE,
            confidence_score=1.0,
            evidence=[],
            forced_compliance=True
        )

        # Generate report
        report = self.engine.generate_verification_report()

        # Verify report structure
        assert "session_id" in report
        assert "summary" in report
        assert "requirements" in report
        assert "recommendations" in report

        summary = report["summary"]
        assert summary["total_requirements"] == 3
        assert summary["completed"] == 1
        assert summary["failed"] == 1
        assert summary["forced_compliance"] == 1
        assert 0 <= summary["overall_score"] <= 1

        # Check recommendations
        recommendations = report["recommendations"]
        assert len(recommendations) > 0
        assert any("failed" in rec for rec in recommendations)
        assert any("forced" in rec for rec in recommendations)

    def test_evidence_pattern_generation(self):
        """Test evidence pattern generation for different requirement types"""
        if not VERIFICATION_ENGINE_AVAILABLE:
            pytest.skip("Verification engine not available")

        # Test different requirement types
        test_cases = [
            (RequirementType.FUNCTIONAL, "create a login system"),
            (RequirementType.SECURITY, "implement secure authentication"),
            (RequirementType.PERFORMANCE, "optimize database queries"),
            (RequirementType.DOCUMENTATION, "write API documentation"),
            (RequirementType.TESTING, "add unit tests")
        ]

        for req_type, description in test_cases:
            patterns = self.engine._generate_evidence_patterns(req_type, description)
            assert len(patterns) > 0

            # Patterns should be valid regex
            for pattern in patterns:
                assert isinstance(pattern, str)
                assert len(pattern) > 0
                # Should not raise exception
                re.compile(pattern)


class TestTranscriptAnalyzer:
    """Test suite for transcript analyzer"""

    def setup_method(self):
        """Set up test environment"""
        if VERIFICATION_ENGINE_AVAILABLE:
            self.analyzer = TranscriptAnalyzer()

    def test_extract_file_operations(self):
        """Test extraction of file operations from transcript"""
        if not VERIFICATION_ENGINE_AVAILABLE:
            pytest.skip("Verification engine not available")

        transcript = """
        I created a new file called auth/user.py with the user model.
        Then I modified the existing file auth/views.py to add login functionality.
        Finally, I deleted the old auth/legacy.py file that was no longer needed.
        File config/settings.py was updated with new database settings.
        """

        operations = self.analyzer.extract_file_operations(transcript)

        assert len(operations) >= 3

        # Check specific operations
        filenames = [op["filename"] for op in operations]
        assert "auth/user.py" in filenames
        assert "auth/views.py" in filenames
        assert "auth/legacy.py" in filenames
        assert "config/settings.py" in filenames

        # Check operation structure
        for op in operations:
            assert "filename" in op
            assert "operation" in op
            assert "context" in op
            assert "position" in op

    def test_extract_test_results(self):
        """Test extraction of test results from transcript"""
        if not VERIFICATION_ENGINE_AVAILABLE:
            pytest.skip("Verification engine not available")

        transcript = """
        Running the test suite...
        All tests passed successfully.
        ✓ User authentication tests passed
        ✓ Database connection tests passed
        ❌ File upload test failed
        Test results: 15 tests passed, 1 failed
        The test suite execution was completed with warnings.
        """

        results = self.analyzer.extract_test_results(transcript)

        assert len(results) >= 4

        # Check that different test result formats are captured
        result_texts = [r["result"] for r in results]
        assert any("passed successfully" in text for text in result_texts)
        assert any("✓" in text for text in result_texts)
        assert any("❌" in text for text in result_texts)
        assert any("15 tests passed" in text for text in result_texts)


if __name__ == "__main__":
    pytest.main([__file__])