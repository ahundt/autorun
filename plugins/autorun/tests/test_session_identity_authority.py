"""Session identity must isolate state without inventing user authority."""

from autorun.core import EventContext, resolve_session_identity


def test_payload_and_history_identities_may_grant_task_pause(tmp_path):
    payload = resolve_session_identity(
        pid=11,
        process_started_at_units=22,
        fallback_id="session-1",
    )
    history = resolve_session_identity(
        pid=11,
        process_started_at_units=22,
        fallback_id="unknown",
        transcript_path=str(tmp_path / "session.jsonl"),
        use_history_identity=True,
    )

    assert payload.authority == "payload"
    assert payload.may_grant_task_pause
    assert history.authority == "history"
    assert history.may_grant_task_pause


def test_same_transcript_basename_in_different_directories_does_not_collide(tmp_path):
    first = resolve_session_identity(
        pid=11,
        process_started_at_units=22,
        fallback_id="unknown",
        transcript_path=str(tmp_path / "a" / "session.jsonl"),
        use_history_identity=True,
    )
    second = resolve_session_identity(
        pid=11,
        process_started_at_units=22,
        fallback_id="unknown",
        transcript_path=str(tmp_path / "b" / "session.jsonl"),
        use_history_identity=True,
    )

    assert first.key != second.key


def test_process_birth_prevents_pid_reuse_but_never_grants_pause_authority():
    first = resolve_session_identity(
        pid=11,
        process_started_at_units=22,
        fallback_id="unknown",
    )
    reused = resolve_session_identity(
        pid=11,
        process_started_at_units=23,
        fallback_id="unknown",
    )

    assert first.key != reused.key
    assert first.authority == reused.authority == "process-birth"
    assert not first.may_grant_task_pause
    assert not reused.may_grant_task_pause


def test_malformed_process_birth_is_weak_and_cannot_grant_pause():
    for value in (None, True, 0, -1, "22"):
        identity = resolve_session_identity(
            pid=11,
            process_started_at_units=value,
            fallback_id="unknown",
        )
        assert identity.authority == "weak-process"
        assert not identity.may_grant_task_pause


def test_event_context_defaults_to_unresolved_identity_authority():
    ctx = EventContext("manual", "UserPromptSubmit")

    assert ctx.session_identity_authority == "unresolved"
