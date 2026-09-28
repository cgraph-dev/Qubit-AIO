"""Audit 2026-09-23 follow-up — surface store capabilities through routes.

The stores already implement these (see
``tests/test_audit_wave_ai10_schedules_memory_boards.py``); this file proves the
dashboard/voice ROUTES now expose them truthfully:

* ``GET /api/cron/jobs[/{id}]`` carries the configured timezone, the pending
  retry instant/attempt, whether the retry ladder is armed, and the real
  ``last_error`` — plus the store's own ``next_run_at`` projection.
* ``GET /api/recall`` accepts ``limit``/``offset`` (active-memory pagination).
* ``POST /api/recall/update`` is a real, confirmation-gated in-place memory
  update (holographic ``update_fact`` / Mem0 ``update`` semantics).
* ``GET /api/plugins/kanban/boards`` and ``.../assignees`` accept
  ``limit``/``offset`` and report the paging envelope.
* ``POST /api/files/upload`` writes a genuinely-binary payload byte-for-byte
  through the brokered ``local.files_upload`` capability (no silent gate).

Hermetic: temp XDG/QUBIT_HOME, injected executor, no network.
"""
from __future__ import annotations

import base64
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.unit


def _cfg(tmp_path, monkeypatch, extra: str = ""):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.delenv("QUBIT_TIMEZONE", raising=False)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n" + extra)
    return C.Config.load(p)


def _transient(task_text, *, session, estop_engaged):
    return {"state": "FAILED", "code": "ConnectError",
            "detail": "nodename nor servname provided"}


def _repo(tmp_path, name="work"):
    import shutil
    import subprocess

    assert shutil.which("git"), "git is required"
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    env = {"GIT_CONFIG_NOSYSTEM": "1", "HOME": str(tmp_path),
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
    return root, env


def _project(cfg, folder):
    from voicebridge import projects as PJ

    created = PJ.create(cfg, "Work", [str(folder)], requested_by="t",
                        confirmed=True, use=True)
    assert created.get("state") == "OK", created
    return created


# ============================================================ cron: tz/retry
def test_cron_routes_surface_timezone_retry_and_last_error(tmp_path,
                                                           monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("QUBIT_TIMEZONE", "Asia/Tokyo")
    job_id = SC.create(cfg, "net job", "do it", "every 2 hours",
                       confirmed=True)["job"]["id"]
    out = SC.run_due(cfg, executor=_transient, now=time.time() + 121 * 60)
    assert out["ran"] == 1 and out["results"][0]["retry"]["scheduled"] is True

    code, jobs = HC.dispatch(cfg, "GET", "/api/cron/jobs", {}, {})
    assert code == 200 and isinstance(jobs, list)
    row = next(j for j in jobs if j["id"] == str(job_id))
    assert row["timezone"] == "Asia/Tokyo"
    assert row["retry_enabled"] is True
    assert row["retry_attempt"] == 1
    assert row["retry_at"] is not None
    assert row["next_run_at"] == row["retry_at"], row
    assert row["last_error"], row
    assert row["last_status"] == "FAILED"

    code, one = HC.dispatch(cfg, "GET", f"/api/cron/jobs/{job_id}", {}, {})
    assert code == 200, (code, one)
    assert one["timezone"] == "Asia/Tokyo"
    assert one["retry_attempt"] == 1 and one["last_error"]


# ========================================================== recall: paging
def test_recall_route_paginates_and_updates(tmp_path, monkeypatch):
    from voicebridge import active_memory as AM
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(AM, "estop_engaged", lambda _cfg: False)
    assert AM.policy_set(cfg, enabled=True, min_score=0.0, max_items=10,
                         session_scoped=False,
                         confirmed=True)["state"] == "OK"
    store = AM._open_store(cfg)
    for i in range(5):
        store.remember(f"pageable fact {i}", scope="default", confidence=0.5)
    store.close()

    code, page = HC.dispatch(cfg, "GET", "/api/recall",
                             {"query": "pageable fact", "limit": "2",
                              "offset": "2"}, {})
    assert code == 200 and page["state"] == "OK", (code, page)
    assert page["offset"] == 2 and len(page["results"]) == 2
    assert [r["id"] for r in page["results"]]

    target = page["results"][0]["id"]
    code, out = HC.dispatch(cfg, "POST", "/api/recall/update", {},
                            {"memory_id": target,
                             "text": "updated pageable fact", "reason": "fix"})
    assert (code, out["code"]) == (409, "CONFIRM_REQUIRED"), (code, out)
    code, out = HC.dispatch(cfg, "POST", "/api/recall/update", {},
                            {"memory_id": target,
                             "text": "updated pageable fact", "reason": "fix",
                             "confirm": True})
    assert code == 200 and out["state"] == "OK", (code, out)
    check = AM._open_store(cfg)
    assert check.get(target)["text"] == "updated pageable fact"
    check.close()

    # Unknown id stays typed and never mutates.
    code, out = HC.dispatch(cfg, "POST", "/api/recall/update", {},
                            {"memory_id": 999999, "text": "x", "reason": "r",
                             "confirm": True})
    assert code == 403 and out["code"] == AM.NOT_FOUND_CODE, (code, out)


# ======================================================== kanban: paging
def test_kanban_boards_and_assignees_paginate(tmp_path, monkeypatch):
    from voicebridge import kanban as N
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    N.init(cfg)
    N.boards_create(cfg, "alpha", "Alpha", confirmed=True)
    N.boards_create(cfg, "beta", "Beta", confirmed=True)
    N.boards_create(cfg, "gamma", "Gamma", confirmed=True)

    code, full = HC.dispatch(cfg, "GET", "/api/plugins/kanban/boards", {}, {})
    assert code == 200 and full["total"] >= 3, (code, full)
    code, page = HC.dispatch(cfg, "GET", "/api/plugins/kanban/boards",
                             {"limit": "1", "offset": "1"}, {})
    assert code == 200 and len(page["boards"]) == 1, (code, page)
    assert page["boards"][0]["slug"] == full["boards"][1]["slug"]
    assert page["has_more"] is True and page["offset"] == 1

    for i in range(3):
        N.create(cfg, f"task {i}", board="main", assignee=f"user{i}",
                 confirmed=True)
    code, asg = HC.dispatch(cfg, "GET", "/api/plugins/kanban/assignees",
                            {"limit": "2", "offset": "0"}, {})
    assert code == 200 and asg["total"] == 3, (code, asg)
    assert len(asg["assignees"]) == 2 and asg["has_more"] is True
    assert asg["offset"] == 0


# ==================================================== files: binary upload
def test_files_upload_writes_binary_through_broker(tmp_path, monkeypatch):
    from voicebridge import file_patch as FP
    from voicebridge import qubit_compat as HC

    if not FP.confinement_available()[0]:
        pytest.skip("bubblewrap confinement unavailable")
    cfg = _cfg(tmp_path, monkeypatch)
    root, _ = _repo(tmp_path)
    _project(cfg, root)
    target = root / "blob.bin"
    raw = b"\x00\x01\x02PNG\xff\xfe\x00binary\x80\x81payload\x00"

    code, out = HC.dispatch(cfg, "POST", "/api/files/upload", {},
                            {"path": str(target), "content_base64":
                             base64.b64encode(raw).decode(), "confirm": True})
    assert (code, out["code"]) == (409, "CONFIRM_REQUIRED"), (code, out)
    assert not target.exists(), "unconfirmed binary must not write"
    cid = out["confirmation_id"]
    code, out = HC.dispatch(cfg, "POST", "/api/files/upload", {},
                            {"path": str(target), "content_base64":
                             base64.b64encode(raw).decode(),
                             "confirmation_id": cid})
    assert code == 200 and out["ok"] is True, (code, out)
    assert target.read_bytes() == raw
    assert out["sha256"] == FP._sha256(raw)
