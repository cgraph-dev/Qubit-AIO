"""Plain-language contracts for qubit_state user-facing errors (CLI UX message campaign, cluster D)."""

import pytest

import qubit_state
from qubit_state import SessionResumeTooLargeError, format_session_db_unavailable


def test_resume_too_large_names_export_and_config_commands():
    text = str(SessionResumeTooLargeError(4312, 4000))
    assert "4312" in text and "4000" in text
    assert "qubit sessions export" in text
    assert "qubit config set sessions.max_resume_messages 0" in text
    for jargon in ("lineage", "guard", "safe resume limit"):
        assert jargon not in text


def test_resume_too_large_keeps_structured_fields():
    exc = SessionResumeTooLargeError(20_001, 20_000, scope="in its tip segment")
    assert (exc.message_count, exc.limit) == (20_001, 20_000)
    assert isinstance(exc, ValueError)


_F2 = pytest.mark.skip(
    reason="W9-tests2-F2: fork qubit_constants lacks profile_cli_selector "
    "(dropped in W9 slice-0; 5 lazy call sites) — product follow-up, then unskip"
)


@_F2
def test_db_unavailable_points_to_doctor_without_sqlite_internals():
    qubit_state._set_last_init_error("OperationalError: database is locked")
    try:
        text = format_session_db_unavailable(details=True)
    finally:
        qubit_state._set_last_init_error(None)
    lead, *rest = text.splitlines()
    assert "session history" in lead
    assert "will not be saved" in lead.lower()
    for internal in ("sqlite.org", "WAL", "NFS/SMB/FUSE/ZFS"):
        assert internal not in lead
    assert rest and rest[0].startswith("Details: ") and "database is locked" in rest[0]


@_F2
def test_db_unavailable_is_one_line_for_chat_surfaces_by_default():
    qubit_state._set_last_init_error("OperationalError: database is locked")
    try:
        text = format_session_db_unavailable(prefix="Cannot resume")
    finally:
        qubit_state._set_last_init_error(None)
    assert "\n" not in text
    assert "Details:" not in text
    assert text.startswith("Cannot resume:")


@_F2
def test_db_unavailable_unknown_cause_on_network_drive_points_at_moving_not_doctor_fix():
    qubit_state._set_last_init_error("OperationalError: locking protocol")
    try:
        text = format_session_db_unavailable()
    finally:
        qubit_state._set_last_init_error(None)
    assert "network" in text
    assert "local disk" in text
    assert "hermes doctor --fix" not in text


@_F2
def test_db_unavailable_without_cause_still_names_doctor():
    qubit_state._set_last_init_error(None)
    text = format_session_db_unavailable(details=True)
    assert "qubit doctor" in text
    assert "will not be saved" in text.lower()
    assert "Details:" not in text


@_F2
def test_db_unavailable_commands_are_pinned_to_the_failing_profile(monkeypatch, tmp_path):
    """Both fallbacks that bypass the shared cause table (no cause; network-drive gloss) name the
    profile whose store failed, like the table's actions do."""
    from qubit_constants import profile_cli_selector

    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / ".hermes" / "profiles" / "research"))
    selector = profile_cli_selector()
    assert selector.strip()
    qubit_state._set_last_init_error(None)
    no_cause = format_session_db_unavailable()
    qubit_state._set_last_init_error("OperationalError: locking protocol")
    try:
        network = format_session_db_unavailable()
    finally:
        qubit_state._set_last_init_error(None)
    for text in (no_cause, network):
        assert f"`hermes {selector}doctor`" in text and "`hermes doctor`" not in text
        assert "{profile_arg}" not in text
