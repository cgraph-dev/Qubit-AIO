"""Unified background task ledger (OpenClaw row #30) — real-store behavior.

Hermetic: isolated XDG/state dirs only. Every ledger source row is created
through the real underlying store API (``kanban`` boards/tasks/events,
``schedules`` jobs/runs, and the ``actions`` receipt store the OpenCode runner
persists to), then the read-only ledger is asserted to surface, merge, sort,
filter, bound, redact, and degrade honestly. No skips, no xfail.
"""
from __future__ import annotations

import datetime as _dt
import json
import time
from pathlib import Path

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# ---------------------------------------------------------------------------
# Real-store fixtures
# ---------------------------------------------------------------------------
def _kanban(cfg, title, body="", *, complete=False, comment=None):
    from voicebridge import kanban as K

    K.init(cfg)
    try:
        K.boards_create(cfg, "team", confirmed=True)
    except K.KanbanError as exc:
        if exc.code != K.EXISTS_CODE:
            raise
    tid = K.create(cfg, title, board="team", body=body,
                   confirmed=True)["task"]["id"]
    if complete:
        K.claim(cfg, tid, confirmed=True)
        K.complete(cfg, tid, confirmed=True)
    if comment:
        K.comment(cfg, tid, comment, confirmed=True)
    return tid


def _schedule(cfg, name, *, state="OK", detail="done",
              task="check inbox", when="once in 1 minutes"):
    from voicebridge import schedules as SC

    job_id = SC.create(cfg, name, task, when, confirmed=True)["job"]["id"]

    def _exec(_task, *, session, estop_engaged):
        return {"state": state, "code": state, "detail": detail}

    SC.run_due(cfg, executor=_exec, now=time.time() + 10 ** 6,
               estop_engaged=lambda: False)
    runs = SC.history(cfg, job_id=job_id, limit=10)["runs"]
    return job_id, [r["id"] for r in runs]


def _opencode(cfg, rid, created, *, state="OK", detail="did work",
              target="/work"):
    """Persist one OpenCode run receipt through the real action store."""
    from voicebridge import actions as A

    store = A.ActionStore(Path(cfg.state_dir()) / "actions.json")
    store.save_receipt(A.ActionReceipt(
        receipt_id=rid, correlation_id=f"corr-{rid}",
        action_id="act.opencode.run", version="1.0.0",
        adapter=A.ADAPTER_OPENCODE, action_hash="ab" * 8, args_digest="dd",
        args_redacted={"task": "x"}, target=target, account_scope="",
        scope="code", state=state, code=state, detail=detail,
        preview=f"opencode run in {target}", confirmation_id="cf",
        idempotency_key="", rollback_kind=A.RB_IMPOSSIBLE, compensation="",
        created_at=created, finished_at=created + 5.0, duration_ms=5000.0))
    store.close()
    return rid


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
def test_status_empty_and_missing_state_never_raises(tmp_path, monkeypatch):
    from voicebridge import task_ledger as TL

    cfg = _cfg(tmp_path, monkeypatch)
    out = TL.status(cfg)
    assert out["state"] == "OK"
    assert out["total"] == 0
    assert out["sources"] == {"opencode": 0, "schedules": 0, "kanban": 0}
    assert set(out["states"]) == set(TL.CANONICAL_STATES)
    # a missing/unreadable cfg is an empty ledger, never an exception
    assert TL.status(None)["state"] == "OK"


# ---------------------------------------------------------------------------
# merge / sort / schema
# ---------------------------------------------------------------------------
def test_merge_sources_sort_and_stable_schema(tmp_path, monkeypatch):
    from voicebridge import task_ledger as TL

    cfg = _cfg(tmp_path, monkeypatch)
    tid = _kanban(cfg, "Ship ledger", complete=True)
    _job, run_ids = _schedule(cfg, "nightly", state="OK")
    _opencode(cfg, "actr_old", 1.0)
    _opencode(cfg, "actr_new", 2.0)

    out = TL.list_tasks(cfg)
    assert out["state"] == "OK"
    tasks = out["tasks"]
    assert {t["source"] for t in tasks} == {"opencode", "schedules", "kanban"}
    for t in tasks:
        for key in ("id", "source", "kind", "state", "title", "created_at",
                    "updated_at", "correlation_id", "evidence_ref",
                    "source_state", "native_id"):
            assert key in t, key
        assert isinstance(t["id"], str) and ":" in t["id"]
    times = [t["updated_at"] for t in tasks]
    assert times == sorted(times, reverse=True)
    # the oldest opencode receipt (created=1.0) sorts last
    assert tasks[-1]["source"] == "opencode"
    assert tasks[-1]["native_id"] == "actr_old"
    ids = {t["id"] for t in tasks}
    assert f"kanban:{tid}" in ids
    assert f"schedules:{run_ids[0]}" in ids
    assert "opencode:actr_old" in ids and "opencode:actr_new" in ids

    st = TL.status(cfg)
    assert st["total"] == out["count"]
    assert st["sources"]["opencode"] == 2
    assert st["sources"]["kanban"] >= 1
    assert st["sources"]["schedules"] >= 1


# ---------------------------------------------------------------------------
# filters
# ---------------------------------------------------------------------------
def test_source_and_state_filters(tmp_path, monkeypatch):
    from voicebridge import task_ledger as TL

    cfg = _cfg(tmp_path, monkeypatch)
    _kanban(cfg, "K one", complete=True)          # ok
    _kanban(cfg, "K two")                          # pending (open)
    _schedule(cfg, "s-ok", state="OK")
    _schedule(cfg, "s-fail", state="FAILED")

    only_kanban = TL.list_tasks(cfg, source="kanban")
    assert {t["source"] for t in only_kanban["tasks"]} == {"kanban"}

    only_ok = TL.list_tasks(cfg, state="ok")
    assert only_ok["tasks"]
    assert all(t["state"] == "ok" for t in only_ok["tasks"])
    assert any(t["source"] == "schedules" for t in only_ok["tasks"])
    assert any(t["source"] == "kanban" for t in only_ok["tasks"])

    only_pending = TL.list_tasks(cfg, state="pending")
    assert only_pending["tasks"]
    assert all(t["state"] == "pending" for t in only_pending["tasks"])

    only_failed = TL.list_tasks(cfg, state="failed")
    assert only_failed["tasks"]
    assert all(t["state"] == "failed" for t in only_failed["tasks"])

    multi = TL.list_tasks(cfg, source=["kanban", "opencode"])
    assert {t["source"] for t in multi["tasks"]} <= {"kanban", "opencode"}


# ---------------------------------------------------------------------------
# bounds
# ---------------------------------------------------------------------------
def test_limit_cap_and_absurd_values_rejected(tmp_path, monkeypatch):
    from voicebridge import task_ledger as TL

    cfg = _cfg(tmp_path, monkeypatch)
    for i in range(5):
        _kanban(cfg, f"K{i}")

    capped = TL.list_tasks(cfg, source="kanban", limit=2)
    assert capped["count"] == 2
    assert capped["total_matched"] == 5
    assert capped["capped"] is True

    big = TL.list_tasks(cfg, source="kanban", limit=TL.MAX_LIMIT + 50)
    assert big["limit"] == TL.MAX_LIMIT
    assert len(big["tasks"]) == 5

    with pytest.raises(TL.TaskLedgerError) as exc:
        TL.list_tasks(cfg, limit=TL.ABSURD_LIMIT + 1)
    assert exc.value.code == TL.TASK_BAD_INPUT

    for bad in (0, -3, "x", 1.5, True, None):
        with pytest.raises(TL.TaskLedgerError) as exc:
            TL.list_tasks(cfg, limit=bad)
        assert exc.value.code == TL.TASK_BAD_INPUT


def test_unknown_source_is_typed(tmp_path, monkeypatch):
    from voicebridge import task_ledger as TL

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(TL.TaskLedgerError) as exc:
        TL.list_tasks(cfg, source="bogus")
    assert exc.value.code == TL.TASK_SOURCE_UNKNOWN
    with pytest.raises(TL.TaskLedgerError) as exc2:
        TL.show(cfg, "x", source="bogus")
    assert exc2.value.code == TL.TASK_SOURCE_UNKNOWN


# ---------------------------------------------------------------------------
# show / not-found
# ---------------------------------------------------------------------------
def test_show_normalized_raw_and_typed_not_found(tmp_path, monkeypatch):
    from voicebridge import task_ledger as TL

    cfg = _cfg(tmp_path, monkeypatch)
    tid = _kanban(cfg, "Ship ledger", body="deploy notes")

    out = TL.show(cfg, f"kanban:{tid}")
    assert out["state"] == "OK"
    assert out["source"] == "kanban"
    assert out["task"]["id"] == f"kanban:{tid}"
    assert out["task"]["native_id"] == str(tid)
    assert out["task"]["state"] == "pending"
    assert out["raw"]["id"] == tid

    by_source = TL.show(cfg, tid, source="kanban")
    assert by_source["state"] == "OK"
    assert by_source["task"]["id"] == f"kanban:{tid}"

    missing = TL.show(cfg, "kanban:999999")
    assert missing["state"] == "NOT_FOUND"
    assert missing["code"] == TL.TASK_NOT_FOUND
    assert TL.show(cfg, "opencode:does-not-exist")["code"] == TL.TASK_NOT_FOUND


# ---------------------------------------------------------------------------
# redaction
# ---------------------------------------------------------------------------
def test_secrets_are_redacted_across_sources(tmp_path, monkeypatch):
    from voicebridge import task_ledger as TL

    cfg = _cfg(tmp_path, monkeypatch)
    tid = _kanban(cfg, "K", body="password=hunter2")
    _schedule(cfg, "s", state="OK", detail="access_token=supersecret123")
    _opencode(cfg, "actr_secret", 5.0,
              detail="api_key=sk-abcdefghijklmnop")

    blob = json.dumps(TL.list_tasks(cfg))
    assert "hunter2" not in blob
    assert "supersecret123" not in blob
    assert "sk-abcdefghijklmnop" not in blob
    assert "[REDACTED]" in blob

    raw = TL.show(cfg, f"kanban:{tid}")["raw"]
    assert "hunter2" not in json.dumps(raw)
    assert "[REDACTED]" in json.dumps(raw)


# ---------------------------------------------------------------------------
# timeline
# ---------------------------------------------------------------------------
def test_timeline_kanban_and_schedules_are_ordered(tmp_path, monkeypatch):
    from voicebridge import task_ledger as TL

    cfg = _cfg(tmp_path, monkeypatch)
    tid = _kanban(cfg, "K", complete=True, comment="looks good")
    tl = TL.timeline(cfg, f"kanban:{tid}")
    assert tl["state"] == "OK"
    kinds = [e["kind"] for e in tl["events"]]
    for expected in ("create", "claim", "complete", "comment"):
        assert expected in kinds, kinds
    ats = [e["at"] for e in tl["events"]]
    assert ats == sorted(ats)

    _job, run_ids = _schedule(cfg, "s", state="OK")
    stl = TL.timeline(cfg, f"schedules:{run_ids[0]}")
    assert stl["state"] == "OK"
    assert stl["events"]
    assert stl["events"][0]["kind"] == "run"
    assert stl["events"][0]["run_id"] == run_ids[0]
    assert stl["events"][0]["state"] == "ok"


def test_timeline_opencode_is_typed_degraded(tmp_path, monkeypatch):
    from voicebridge import task_ledger as TL

    cfg = _cfg(tmp_path, monkeypatch)
    _opencode(cfg, "actr_tl", 10.0)
    tl = TL.timeline(cfg, "opencode:actr_tl")
    assert tl["state"] == "DEGRADED"
    assert tl["code"] == TL.TASK_LEDGER_UNAVAILABLE
    assert [e["receipt_id"] for e in tl["events"]] == ["actr_tl"]

    nf = TL.timeline(cfg, "kanban:999999")
    assert nf["state"] == "NOT_FOUND"
    assert nf["code"] == TL.TASK_NOT_FOUND


# ---------------------------------------------------------------------------
# since
# ---------------------------------------------------------------------------
def test_since_epoch_iso_and_invalid(tmp_path, monkeypatch):
    from voicebridge import task_ledger as TL

    cfg = _cfg(tmp_path, monkeypatch)
    _kanban(cfg, "K", complete=True)
    _opencode(cfg, "actr_a", 1.0)

    all_rows = TL.list_tasks(cfg)
    assert all_rows["count"] >= 2
    assert TL.list_tasks(cfg, since=time.time() + 10 ** 9)["count"] == 0
    assert TL.list_tasks(cfg, since=0)["count"] == all_rows["count"]
    iso = _dt.datetime(1960, 1, 1).isoformat()
    assert TL.list_tasks(cfg, since=iso)["count"] == all_rows["count"]

    with pytest.raises(TL.TaskLedgerError) as exc:
        TL.list_tasks(cfg, since="not-a-time")
    assert exc.value.code == TL.TASK_BAD_INPUT


# ---------------------------------------------------------------------------
# degradation (never fabricate)
# ---------------------------------------------------------------------------
def test_unreadable_json_store_is_typed_unavailable(tmp_path, monkeypatch):
    from voicebridge import task_ledger as TL

    cfg = _cfg(tmp_path, monkeypatch)
    _kanban(cfg, "K")
    (cfg.state_dir() / "actions.json").write_text("{not-json", encoding="utf-8")

    st = TL.status(cfg)
    assert st["state"] == "DEGRADED"
    assert any(u["source"] == "opencode"
               and u["code"] == TL.TASK_LEDGER_UNAVAILABLE
               for u in st["unavailable"])
    assert st["sources"]["kanban"] >= 1

    out = TL.list_tasks(cfg)
    assert any(u["source"] == "opencode" for u in out["unavailable"])
    assert TL.show(cfg, "opencode:whatever")["code"] == TL.TASK_NOT_FOUND


def test_unreadable_sqlite_store_is_typed_unavailable(tmp_path, monkeypatch):
    from voicebridge import task_ledger as TL

    cfg = _cfg(tmp_path, monkeypatch)
    (cfg.state_dir() / "schedules.db").write_text("garbage", encoding="utf-8")
    st = TL.status(cfg)
    assert st["state"] == "DEGRADED"
    assert any(u["source"] == "schedules"
               and u["code"] == TL.TASK_LEDGER_UNAVAILABLE
               for u in st["unavailable"])
    assert TL.list_tasks(cfg, source="schedules")["count"] == 0
