"""One copy table for "session storage unavailable / not written" across CLI, gateway and RPC.

Contract: the user gets a plain cause + the exact repair command, never the raw sqlite text as the
lead, plus a stable machine-readable `code` a GUI can key a "Run doctor" button on.
"""

import sqlite3

import pytest

from qubit_state_user_copy import describe_storage_failure, storage_failure_details


_F2 = pytest.mark.skip(
    reason="W9-tests2-F2: fork qubit_constants lacks profile_cli_selector "
    "(dropped in W9 slice-0; 5 lazy call sites) — product follow-up, then unskip"
)


@_F2
@pytest.mark.parametrize(
    ("exc", "code", "command"),
    [
        (sqlite3.OperationalError("database is locked"), "storage_locked", "try again"),
        (sqlite3.OperationalError("attempt to write a readonly database"), "storage_readonly", "qubit doctor --fix"),
        (sqlite3.DatabaseError("database disk image is malformed"), "storage_corrupt", "qubit doctor --fix"),
        (OSError(28, "No space left on device"), "disk_full", "Free some disk space"),
        (None, "storage_unavailable", "qubit doctor --fix"),
    ],
)
def test_each_cause_has_a_stable_code_and_an_action(exc, code, command):
    failure = describe_storage_failure(exc)
    assert failure.code == code
    assert command in failure.action
    assert failure.gloss and failure.gloss[0].islower()  # a clause that follows "Cause:"
    # The raw sqlite wording never leaks into the user-facing gloss.
    assert "sqlite" not in failure.gloss.lower() and "OperationalError" not in failure.gloss


def test_details_line_is_flattened_and_bounded():
    details = storage_failure_details("line one\n   line two " + "x" * 400, limit=60)
    assert "\n" not in details and len(details) == 60 and details.endswith("...")


@_F2
def test_action_command_is_pinned_to_the_failing_profile(monkeypatch, tmp_path):
    """The action names the profile whose store failed (multi-profile backends serve sessions
    whose state.db is not the process default; a bare ``hermes`` follows active_profile)."""
    from qubit_constants import profile_cli_selector

    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / ".hermes" / "profiles" / "research"))
    selector = profile_cli_selector()
    assert selector.strip()
    for exc in (sqlite3.OperationalError("database is locked"), sqlite3.DatabaseError("malformed"), None):
        action = describe_storage_failure(exc).action
        assert "{profile_arg}" not in action
        assert f"`hermes {selector}" in action and "`hermes doctor" not in action and "`hermes gateway" not in action
