"""Wave-19 cron forward-port: the 0.21.4 modules ported from the pinned source.

Each test pins one genuinely-missing module/behaviour ported into
``qubit_runtime/cron/**`` from the pinned upstream ``cron/**`` (pin
``d337b736``), with the hermes->qubit rename map applied and the local Qubit
overlays preserved (``cron.ledger``, ``record_home``, ``cron_env_setting``).
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "qubit_runtime") not in sys.path:
    sys.path.insert(0, str(_ROOT / "qubit_runtime"))


# --------------------------------------------------------------- occurrences
def test_occurrences_pending_slot_roundtrip(monkeypatch):
    from cron import occurrences as occ

    now = datetime.now(timezone.utc)
    stamp = occ.pending_slot_stamp("2026-01-01T00:00:00+00:00", now)
    assert stamp["scheduled_at"] == "2026-01-01T00:00:00+00:00"
    assert stamp["at"] == now.isoformat()
    assert stamp["by"]

    # A recurring job with an orphaned stamp (other machine, lease long expired).
    job = {
        "id": "abcdef012345",
        "schedule": {"kind": "interval"},
        "pending_slot": {"scheduled_at": "2026-01-01T00:00:00+00:00", "at": now.isoformat(),
                         "by": "some-other-machine:1234"},
    }
    far_future = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert occ.unclaimed_pending_slot(job, far_future) == "2026-01-01T00:00:00+00:00"

    # Non-recurring jobs and malformed stamps never fire.
    assert occ.unclaimed_pending_slot({"schedule": {"kind": "once"}}, now) is None
    assert occ.unclaimed_pending_slot({"schedule": {"kind": "interval"}}, now) is None


# ----------------------------------------------------------------- incidents
@pytest.fixture
def _isolated_ledger(tmp_path, monkeypatch):
    from cron import executions as ex
    from cron import incidents as inc

    db = tmp_path / "executions.db"
    monkeypatch.setattr(ex, "EXECUTIONS_FILE", db)
    monkeypatch.setattr(inc, "EXECUTIONS_FILE", db)
    return db


def test_incidents_resolved_then_reopen(_isolated_ledger):
    from cron import incidents as inc

    incident_id, is_new = inc.upsert_incident("job1", "boom")
    assert is_new is True
    assert inc.get_incident(incident_id)["state"] == "detected"

    assert inc.set_incident_state(incident_id, "alerted") is True
    assert inc.get_incident(incident_id)["alerted_at"] is not None

    assert inc.close_incidents_for_recovered_job("job1") == 1
    assert inc.get_incident(incident_id)["state"] == "resolved"

    # A repeat of the same error re-opens a resolved incident.
    same_id, reopened = inc.upsert_incident("job1", "boom")
    assert same_id == incident_id
    assert reopened is True
    assert inc.get_incident(incident_id)["state"] == "detected"


def test_incidents_closed_stays_silent(_isolated_ledger):
    from cron import incidents as inc

    incident_id, _ = inc.upsert_incident("job2", "boom")
    assert inc.set_incident_state(incident_id, "closed") is True
    _, reopened = inc.upsert_incident("job2", "boom")
    assert reopened is False
    assert inc.get_incident(incident_id)["state"] == "closed"


# ------------------------------------------------------------ delivery_queue
def test_delivery_queue_suppressed_disposition(tmp_path, monkeypatch):
    from cron import delivery_queue as dq

    monkeypatch.setattr(dq, "DELIVERY_DB", tmp_path / "deliveries.db")
    assert dq.queue_path() == tmp_path / "deliveries.db"

    job = {"id": "j1", "_notification_all_targets_suppressed": True}
    dq.enqueue("exec-suppressed", job, "body")
    assert dq.drain(lambda job, content, for_failure: None) == 1
    assert dq.get_status("exec-suppressed")["status"] == "suppressed"

    dq.enqueue("exec-ok", {"id": "j2"}, "body")
    dq.drain(lambda job, content, for_failure: None)
    assert dq.get_status("exec-ok")["status"] == "delivered"


# ---------------------------------------------------------------- executions
def test_executions_stale_bound_and_delivery_outcome(tmp_path, monkeypatch):
    from cron import executions as ex

    monkeypatch.setattr(ex, "EXECUTIONS_FILE", tmp_path / "executions.db")

    # A live-owner claim bound is derived, never a bare constant.
    bound = ex._live_owner_stale_after_seconds()
    assert bound is None or bound >= ex.LIVE_OWNER_STALE_CLAIM_FLOOR_SECONDS

    record = ex.create_execution("jobX", source="tick")
    assert ex._claim_age_seconds(record["claimed_at"]) >= 0.0
    finished = ex.finish_execution(record["id"], success=True, delivery_outcome="delivered")
    assert finished is not None
    assert ex.get_execution(record["id"])["delivery_outcome"] == "delivered"


# ------------------------------------------------------- diagnostics / worker
def test_external_worker_stderr_tail_redacts_and_bounds(tmp_path):
    from cron.scheduler_diagnostics import external_worker_stderr_tail

    missing = tmp_path / "nope.log"
    assert external_worker_stderr_tail(missing) == ""

    log = tmp_path / "worker.log"
    log.write_text("https://user:sekret@example.test/path\n" + "x" * 4000, encoding="utf-8")
    tail = external_worker_stderr_tail(log)
    assert tail.startswith("; worker stderr: ")
    assert "sekret" not in tail


def test_reap_terminal_worker_in_background():
    from cron.scheduler_detached_worker import reap_terminal_worker_in_background

    proc = subprocess.Popen(["true"])
    assert reap_terminal_worker_in_background(proc) is None
    assert proc.wait(timeout=10) == 0


# --------------------------------------------------------- scheduler_provider
def test_provider_fire_due_accepts_signature():
    from cron.scheduler_provider import provider_fire_due_accepts

    class Modern:
        def fire_due(self, force=False):
            return None

    class Legacy:
        def fire_due(self):
            return None

    class Kwargs:
        def fire_due(self, **kwargs):
            return None

    assert provider_fire_due_accepts(Modern(), "force") is True
    assert provider_fire_due_accepts(Legacy(), "force") is False
    assert provider_fire_due_accepts(Kwargs(), "force") is True


# ----------------------------------------------------------- scheduler_prompt
def test_archive_answer_and_context_clip():
    from cron.scheduler_prompt import _archive_answer, _clip_to_context_budget, _MAX_CONTEXT_CHARS

    assert _archive_answer("prompt noise\n## Response\n\nreal answer") == "real answer"
    assert _archive_answer("script output without heading") == "script output without heading"
    assert _archive_answer("## Response\n\n[SILENT]") is None

    clipped = _clip_to_context_budget("z" * (_MAX_CONTEXT_CHARS + 500))
    assert "chars omitted" in clipped
    assert len(clipped) < _MAX_CONTEXT_CHARS + 100


# -------------------------------------------------------- scheduler_preflight
def test_credential_store_scope_label_names_home(monkeypatch, tmp_path):
    from cron.scheduler_preflight import _credential_store_scope_label

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    label = _credential_store_scope_label()
    assert "profile" in label and "QUBIT_HOME" in label


# ----------------------------------------------------------- lifecycle_guard
@pytest.mark.parametrize("command,expected", [
    ("pkill -9 python3", True),
    ("taskkill /F /IM python.exe", True),
    ("killall python", True),
    ("kill $(pidof python3)", True),
    ("pgrep python | xargs kill", True),
    ("Get-Process python | Stop-Process", True),
    ("pkill -u bob chrome", False),
    ("python mt_add.py", False),
    ("taskkill /F /IM agent-browser.exe", False),
    ("echo hello", False),
])
def test_host_interpreter_kill_branch_e(command, expected):
    from cron.lifecycle_guard import contains_gateway_lifecycle_command

    assert contains_gateway_lifecycle_command(command) is expected
