# tests/test_rebase_n26_record_home_readers.py
"""N26 regression: every gateway identity reader must accept a ``qubit_home``-only record.

The N26 de-brand moved the gateway identity writer from ``hermes_home`` to
``qubit_home`` (``gateway.status``). The Step C review found readers still keyed
on ``hermes_home``; because their tests built ``hermes_home``-only records, the
mismatch stayed green. This gate pins the writer/reader contract:

* ``gateway.run._replace_target_belongs_to_other_profile`` accepts a
  ``qubit_home``-only pid record for ``gateway run --replace``;
* the sibling readers (host-attach identity, cron scheduler ownership, the
  shared-install warning) resolve the home through ``gateway.status.record_home``
  so the current key wins and the legacy key still reads (read-old).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from rebase_bucket_support import runtime_on_path  # noqa: F401


def _identity(pid: int, home: str) -> dict:
    return {"pid": pid, "kind": "qubit-gateway", "qubit_home": home}


def test_record_home_prefers_qubit_and_reads_old_legacy(runtime_on_path):
    import gateway.status as status

    assert status.record_home({"qubit_home": "/q", "hermes_home": "/h"}) == "/q"
    assert status.record_home({"hermes_home": "/h"}) == "/h"
    assert status.record_home({}) is None
    assert status.record_home(None) is None


def test_replace_ownership_accepts_qubit_home_only_record(runtime_on_path, tmp_path):
    """The exact Step C failure: a current-daemon record only carries qubit_home."""
    from gateway.run import _replace_target_belongs_to_other_profile

    home = tmp_path / "home"
    home.mkdir()
    record = {
        "pid": 424242,
        "kind": "qubit-gateway",
        "argv": ["python", "-m", "qubit_cli.main", "gateway", "run"],
        "start_time": 111222333,
        "qubit_home": str(home),
    }
    with (
        patch("gateway.status._read_pid_record", return_value=record),
        patch("gateway.status._get_pid_path", return_value=home / "gateway.pid"),
        patch("gateway.status._get_process_start_time", return_value=111222333),
        patch("gateway.status._read_process_cmdline", return_value=None),
        patch("gateway.status._get_process_qubit_home", return_value=home),
    ):
        assert _replace_target_belongs_to_other_profile(424242) is False


def test_replace_ownership_still_refuses_foreign_and_legacy_key(
    runtime_on_path, tmp_path
):
    """Read-old must not weaken the cross-home refusal."""
    from gateway.run import _replace_target_belongs_to_other_profile

    home = tmp_path / "home"
    home.mkdir()
    other = tmp_path / "other"
    other.mkdir()

    def probe(record, target_home):
        with (
            patch("gateway.status._read_pid_record", return_value=record),
            patch("gateway.status._get_pid_path", return_value=target_home / "gateway.pid"),
            patch("gateway.status._get_process_start_time", return_value=111222333),
            patch("gateway.status._read_process_cmdline", return_value=None),
            patch("gateway.status._get_process_qubit_home", return_value=target_home),
        ):
            return _replace_target_belongs_to_other_profile(424242)

    foreign = {
        "pid": 424242,
        "kind": "qubit-gateway",
        "argv": ["python", "-m", "qubit_cli.main", "gateway", "run"],
        "start_time": 111222333,
        "qubit_home": str(other),
    }
    assert probe(foreign, home) is True

    # Legacy-only key still resolves for a same-home record (read-old).
    legacy = {
        "pid": 424242,
        "kind": "hermes-gateway",
        "argv": ["python", "-m", "qubit_cli.main", "gateway", "run"],
        "start_time": 111222333,
        "hermes_home": str(home),
    }
    assert probe(legacy, home) is False

    # A record with no home at all proves nothing -> refuse.
    bare = {
        "pid": 424242,
        "kind": "qubit-gateway",
        "argv": ["python", "-m", "qubit_cli.main", "gateway", "run"],
        "start_time": 111222333,
    }
    assert probe(bare, home) is True


def test_host_attach_identity_matches_qubit_home_only(runtime_on_path, tmp_path):
    import gateway.host_attach as host_attach

    home = tmp_path / "home"
    home.mkdir()
    record = SimpleNamespace(pid=7)
    assert host_attach._identity_matches(_identity(7, str(home)), record, home) is True
    assert (
        host_attach._identity_matches(_identity(7, str(tmp_path / "other")), record, home)
        is False
    )


def test_scheduler_ownership_reads_qubit_home_only(runtime_on_path, tmp_path):
    from cron.scheduler_ownership import record_serves_profile

    home = tmp_path / "home"
    home.mkdir()
    record = {"pid": 7, "kind": "qubit-gateway", "qubit_home": str(home), "served_profiles": []}
    assert record_serves_profile(record, home) is True


def test_shared_profile_warning_reads_qubit_home_only(runtime_on_path, tmp_path):
    import qubit_cli.shared_profile_warning as spw
    from qubit_constants import qubit_home_key

    home = tmp_path / "home"
    home.mkdir()
    home_key = qubit_home_key(home)
    entry = {"install": "other-install", "qubit_home": home_key}
    with (
        patch.object(spw, "install_id", return_value="own-install"),
        patch.object(spw, "ledger_entries", return_value=[entry]),
    ):
        warning = spw.shared_profile_warning(home=home)
    assert "Another Qubit installation is using this profile" in warning
