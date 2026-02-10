"""Test daemon transcript truncation for memory efficiency.

session_transcript can be 200MB+ in long sessions. The daemon only searches
for recent patterns (stage markers, justification tags) so we truncate to
last ~64KB of messages, dramatically reducing memory usage and parse time.
"""
import json
import pytest


class TestTranscriptTruncation:
    """Test daemon truncates large transcripts efficiently."""

    def test_truncates_transcript_over_64kb(self):
        """TEST: Large transcripts truncated to ~64KB of recent messages."""
        from clautorun.core import normalize_hook_payload

        # Create large transcript (simulate 200MB session)
        large_msg = {"role": "assistant", "content": "x" * 50000}  # 50KB message
        transcript = [large_msg] * 100  # 5MB total

        payload = {
            "hook_event_name": "PostToolUse",
            "session_id": "test-session",
            "session_transcript": transcript,
            "tool_name": "Edit",
            "tool_input": {},
        }

        # Normalize (includes truncation)
        normalized = normalize_hook_payload(payload)

        # Verify truncation occurred
        truncated = normalized["session_transcript"]
        assert len(truncated) < len(transcript), "Should truncate large transcript"

        # Verify STRICT size limit (64KB hard cap)
        # With 50KB messages, we can only fit 1 message in 64KB (strict limit)
        size = len(json.dumps(truncated))
        assert size <= 64 * 1024, f"Truncated transcript must be <= 64KB, got {size//1024}KB"

        # Verify kept recent messages (most important for pattern matching)
        assert truncated == transcript[-len(truncated):], "Should keep most recent messages"

        # With huge messages (50KB each), we might only keep 1-2 messages - that's OK
        # Size limit takes absolute priority over message count
        assert len(truncated) >= 1, "Should keep at least 1 message"

    def test_keeps_small_transcripts_intact(self):
        """TEST: Small transcripts (<64KB) not truncated."""
        from clautorun.core import normalize_hook_payload

        # Small transcript (15 messages, <10KB total)
        transcript = [{"role": "user", "content": f"Message {i}"} for i in range(15)]

        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "test-session",
            "session_transcript": transcript,
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }

        normalized = normalize_hook_payload(payload)
        assert normalized["session_transcript"] == transcript, "Small transcript should be unchanged"

    def test_truncated_transcript_preserves_recent_messages(self):
        """TEST: Truncation keeps RECENT messages (where patterns appear)."""
        from clautorun.core import normalize_hook_payload

        # Create transcript with marker in last message
        old_messages = [{"role": "user", "content": "x" * 40000}] * 50  # Old messages (2MB)
        marker_message = {"role": "assistant", "content": "AUTORUN_STAGE1_COMPLETE"}
        recent_messages = [{"role": "user", "content": f"Recent {i}"} for i in range(5)]

        transcript = old_messages + [marker_message] + recent_messages

        payload = {
            "hook_event_name": "Stop",
            "session_id": "test-session",
            "session_transcript": transcript,
        }

        normalized = normalize_hook_payload(payload)
        truncated = normalized["session_transcript"]

        # Verify marker message preserved (it's recent)
        transcript_str = json.dumps(truncated)
        assert "AUTORUN_STAGE1_COMPLETE" in transcript_str, \
            "Recent marker should be preserved after truncation"

    def test_empty_transcript_handled(self):
        """TEST: Empty transcript doesn't crash."""
        from clautorun.core import normalize_hook_payload

        payload = {
            "hook_event_name": "SessionStart",
            "session_id": "test-session",
            "session_transcript": [],
        }

        normalized = normalize_hook_payload(payload)
        assert normalized["session_transcript"] == []

    def test_none_transcript_handled(self):
        """TEST: Missing transcript field doesn't crash."""
        from clautorun.core import normalize_hook_payload

        payload = {
            "hook_event_name": "SessionStart",
            "session_id": "test-session",
            # No session_transcript field
        }

        normalized = normalize_hook_payload(payload)
        assert isinstance(normalized["session_transcript"], list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
