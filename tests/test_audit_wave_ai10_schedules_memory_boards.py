"""Audit wave AI10 — cron timezone/retry, memory update/pagination, board paging.

Hermetic: isolated XDG/state dirs, frozen timestamps, injected executors. The
three capabilities port the reference schedule/retry semantics and add the
missing memory update + listing pagination over the one authoritative store.

Upstream references:
* ``hermes_time.py:45 _resolve_timezone_name`` / ``:91 get_timezone``
* ``cron/jobs.py:1137 compute_next_run``
* ``cron/unreachable_retry.py:32 RETRY_DELAYS_SECONDS`` / ``:85 plan_retry``
* ``cron/scheduler_preflight.py:27-77 _is_transient_provider_resolve_error``
* ``hermes_cli/kanban_db.py:627 list_boards`` / ``:1514 list_tasks``
* holographic ``plugins/memory/holographic/store.py:161 update_fact`` and
  Mem0 ``plugins/memory/mem0/_backend.py:32 update``
"""
from __future__ import annotations

import time

import pytest


def _cfg(tmp_path, monkeypatch, extra: str = ""):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.delenv("QUBIT_TIMEZONE", raising=False)
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n" + extra)
    return C.Config.load(p)


def _ok(task_text, *, session, estop_engaged):
    return {"state": "OK", "code": "OK", "detail": "did: " + task_text[:40]}


def _transient(task_text, *, session, estop_engaged):
    return {"state": "FAILED", "code": "ConnectError",
            "detail": "nodename nor servname provided"}


def _hard_fail(task_text, *, session, estop_engaged):
    return {"state": "FAILED", "code": "ValueError",
            "detail": "synthetic non-transient failure"}


# =========================================================== cron: timezone
def test_daily_next_run_honors_configured_timezone(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    job = {"kind": "daily", "hour": 9, "minute": 0, "enabled": True,
           "created_at": 0.0, "last_run_at": 0.0}
    now = 1700000000.0  # 2023-11-14 22:13:20 UTC

    monkeypatch.setenv("QUBIT_TIMEZONE", "UTC")
    utc = SC.next_run_at(job, cfg=cfg, now=now)
    monkeypatch.setenv("QUBIT_TIMEZONE", "Asia/Tokyo")
    tokyo = SC.next_run_at(job, cfg=cfg, now=now)

    # Same wall-clock 09:00 is a different instant in each zone.
    assert utc != tokyo
    assert utc == 1700038800.0  # 2023-11-15 09:00 UTC
    assert tokyo == 1700006400.0  # 2023-11-15 09:00 JST = 00:00 UTC


def test_scheduler_status_reports_timezone_and_degrades_invalid(tmp_path,
                                                               monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    SC.create(cfg, "daily", "do it", "daily 09:00", confirmed=True)

    monkeypatch.setenv("QUBIT_TIMEZONE", "Asia/Tokyo")
    st = SC.scheduler_status(cfg)
    assert st["timezone"] == "Asia/Tokyo"
    assert st["jobs"][0]["next_run_at"] is not None

    # An invalid zone never crashes; it degrades to server-local and is named.
    monkeypatch.setenv("QUBIT_TIMEZONE", "Not/AZone")
    st = SC.scheduler_status(cfg)
    assert st["timezone"] == "Not/AZone"
    assert st["jobs"][0]["next_run_at"] is not None


def test_timezone_env_fallback_order(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch, "timezone: Europe/Paris\n")
    assert SC._tz_name(cfg) == "Europe/Paris"
    monkeypatch.setenv("QUBIT_TIMEZONE", "Asia/Tokyo")
    assert SC._tz_name(cfg) == "Asia/Tokyo"


# ======================================================== cron: retry ladder
def test_transient_failure_schedules_bounded_retry_ladder(tmp_path,
                                                          monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    job_id = SC.create(cfg, "net job", "do it", "every 2 hours",
                       confirmed=True)["job"]["id"]
    t0 = time.time() + 121 * 60

    out = SC.run_due(cfg, executor=_transient, now=t0)
    assert out["ran"] == 1
    retry = out["results"][0]["retry"]
    assert retry["scheduled"] is True and retry["attempt"] == 1
    assert retry["delay"] == SC.RETRY_DELAYS_SECONDS[0]

    # The pending retry is projected earlier than the natural occurrence.
    st = SC.scheduler_status(cfg, now=t0)
    entry = st["jobs"][0]
    assert entry["retry_attempt"] == 1
    assert entry["retry_at"] == t0 + SC.RETRY_DELAYS_SECONDS[0]
    assert entry["next_run_at"] == entry["retry_at"]
    assert entry["last_state"] == "FAILED" and entry["last_error"]

    # Second and third rungs, then the ladder is exhausted.
    out = SC.run_due(cfg, executor=_transient, now=entry["retry_at"])
    assert out["results"][0]["retry"]["attempt"] == 2
    assert out["results"][0]["retry"]["delay"] == SC.RETRY_DELAYS_SECONDS[1]
    second = SC.scheduler_status(cfg, now=entry["retry_at"])["jobs"][0]
    out = SC.run_due(cfg, executor=_transient, now=second["retry_at"])
    assert out["results"][0]["retry"]["attempt"] == 3
    assert out["results"][0]["retry"]["delay"] == SC.RETRY_DELAYS_SECONDS[2]
    third = SC.scheduler_status(cfg, now=second["retry_at"])["jobs"][0]

    out = SC.run_due(cfg, executor=_transient, now=third["retry_at"])
    assert out["results"][0]["retry"]["scheduled"] is False
    assert out["results"][0]["retry"]["reason"] == SC.RETRY_EXHAUSTED
    assert SC.scheduler_status(cfg, now=third["retry_at"])["jobs"][0][
        "retry_attempt"] == 0

    # A run that reaches the model clears the ladder (history stays truthful).
    last = SC.scheduler_status(cfg, now=third["retry_at"])["jobs"][0]
    nxt = last["next_run_at"]
    out = SC.run_due(cfg, executor=_ok, now=nxt)
    assert out["results"][0]["retry"]["reason"] == SC.RETRY_REACHED_MODEL
    hist = SC.history(cfg, job_id)
    states = [r["state"] for r in hist["runs"]]
    assert states.count("FAILED") == 4 and states.count("OK") == 1


def test_retry_disabled_and_non_transient_do_not_rearm(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch,
               "schedules:\n  retry_backoff: false\n")
    assert SC.retry_enabled(cfg) is False
    SC.create(cfg, "net", "do it", "every 30 minutes", confirmed=True)
    out = SC.run_due(cfg, executor=_transient, now=time.time() + 31 * 60)
    assert out["results"][0]["retry"]["scheduled"] is False
    assert out["results"][0]["retry"]["reason"] == SC.RETRY_DISABLED

    cfg2 = _cfg(tmp_path / "b", monkeypatch)
    assert SC.retry_enabled(cfg2) is True
    SC.create(cfg2, "hard", "do it", "every 30 minutes", confirmed=True)
    out = SC.run_due(cfg2, executor=_hard_fail, now=time.time() + 31 * 60)
    assert out["results"][0]["retry"]["scheduled"] is False
    assert out["results"][0]["retry"]["reason"] == SC.RETRY_NOT_TRANSIENT


def test_oneshot_never_retries(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    SC.create(cfg, "once", "do it", "once in 10 minutes", confirmed=True)
    out = SC.run_due(cfg, executor=_transient, now=time.time() + 11 * 60)
    assert out["results"][0]["retry"]["scheduled"] is False
    assert out["results"][0]["retry"]["reason"] == SC.RETRY_ONESHOT


# ========================================================= memory: update
def test_store_update_partial_history_and_guards(tmp_path):
    from voicebridge.memory.store import MemoryStore, MemoryStoreError

    store = MemoryStore(tmp_path / "mem.db")
    rec = store.remember("original fact", scope="default",
                         confidence=0.3, tier="recall")
    other = store.remember("second fact", scope="default")

    updated = store.update(rec["id"], text="revised fact", tier="working",
                           confidence=0.7, reason="operator fix")
    assert updated["text"] == "revised fact"
    assert updated["tier"] == "working"
    assert abs(updated["confidence"] - 0.7) < 1e-9
    assert any(h["action"] == "update" for h in store.history(rec["id"]))
    assert any(r["id"] == rec["id"] for r in store.search("revised"))

    # Secret-shaped content is refused at the same write boundary.
    with pytest.raises(MemoryStoreError):
        store.update(rec["id"], text="sk-abcdefghijklmnopqrstuvwxyz")
    # Colliding with another active record's content is refused.
    with pytest.raises(MemoryStoreError):
        store.update(other["id"], text="revised fact")
    # Empty update and unknown tier/id are typed.
    with pytest.raises(MemoryStoreError):
        store.update(rec["id"])
    with pytest.raises(MemoryStoreError):
        store.update(rec["id"], tier="bogus")
    with pytest.raises(MemoryStoreError):
        store.update(999999, text="x")
    store.close()


def test_store_search_pagination_is_stable(tmp_path):
    from voicebridge.memory.store import MemoryStore

    store = MemoryStore(tmp_path / "mem.db")
    for i in range(5):
        store.remember(f"paged topic item {i}", scope="default")

    everything = store.search("paged topic", limit=100)
    page1 = store.search("paged topic", limit=2, offset=0)
    page2 = store.search("paged topic", limit=2, offset=2)
    assert [r["id"] for r in page1] == [r["id"] for r in everything[:2]]
    assert [r["id"] for r in page2] == [r["id"] for r in everything[2:4]]
    assert store.search("paged topic", limit=0) == []
    with pytest.raises(Exception):
        store.search("paged topic", offset=-1)
    store.close()


def test_active_memory_update_and_recall_pagination(tmp_path, monkeypatch):
    from voicebridge import active_memory as AM

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(AM, "estop_engaged", lambda _cfg: False)
    assert AM.policy_set(cfg, enabled=True, min_score=0.0, max_items=10,
                         session_scoped=False, confirmed=True)["state"] == "OK"

    store = AM._open_store(cfg)
    for i in range(5):
        store.remember(f"pageable fact {i}", scope="default", confidence=0.5)
    store.close()

    full = AM.recall(cfg, "pageable fact")
    assert full["state"] == "OK" and len(full["results"]) == 5
    page1 = AM.recall(cfg, "pageable fact", limit=2, offset=0)
    page2 = AM.recall(cfg, "pageable fact", limit=2, offset=2)
    assert [r["id"] for r in page1["results"]] == \
        [r["id"] for r in full["results"][:2]]
    assert [r["id"] for r in page2["results"]] == \
        [r["id"] for r in full["results"][2:4]]
    assert page1["offset"] == 0 and page2["offset"] == 2
    assert AM.recall(cfg, "pageable fact", offset=-1)["code"] == \
        AM.BAD_INPUT_CODE

    target = full["results"][0]["id"]
    # Confirmation is required before any mutation.
    out = AM.update(cfg, target, text="updated pageable fact", reason="fix")
    assert out["state"] == "CONFIRM_REQUIRED" and out["code"] == AM.CONFIRM_CODE
    # Bad input / not found are typed and never touch the store.
    assert AM.update(cfg, target, reason="fix",
                     confirmed=True)["code"] == AM.BAD_INPUT_CODE
    assert AM.update(cfg, 0, text="x", reason="r",
                     confirmed=True)["code"] == AM.BAD_INPUT_CODE
    assert AM.update(cfg, 999999, text="x", reason="r",
                     confirmed=True)["code"] == AM.NOT_FOUND_CODE
    # Real effect + history.
    out = AM.update(cfg, target, text="updated pageable fact", reason="fix",
                    confirmed=True)
    assert out["state"] == "OK" and out["id"] == target
    check = AM._open_store(cfg)
    assert check.get(target)["text"] == "updated pageable fact"
    assert any(h["action"] == "update" for h in check.history(target))
    check.close()

    # Session-scoped policy cannot mutate a record in another scope.
    assert AM.policy_set(cfg, session_scoped=True, confirmed=True)["state"] == "OK"
    out = AM.update(cfg, target, text="x", reason="r", session="other",
                    confirmed=True)
    assert out["code"] == AM.NOT_FOUND_CODE


# =========================================================== boards paging
def test_boards_list_pagination(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    KB.init(cfg)
    KB.boards_create(cfg, "alpha", "Alpha", confirmed=True)
    KB.boards_create(cfg, "beta", "Beta", confirmed=True)
    KB.boards_create(cfg, "gamma", "Gamma", confirmed=True)

    everything = KB.boards_list(cfg)
    total = everything["total"]
    assert total == len(everything["boards"]) >= 3

    page = KB.boards_list(cfg, limit=1, offset=1)
    assert page["boards"] == everything["boards"][1:2]
    assert page["total"] == total and page["has_more"] is True

    tail = KB.boards_list(cfg, limit=10, offset=total)
    assert tail["boards"] == [] and tail["has_more"] is False

    with pytest.raises(KB.KanbanError):
        KB.boards_list(cfg, offset=-1)
    with pytest.raises(KB.KanbanError):
        KB.boards_list(cfg, limit=-1)


def test_list_tasks_pagination(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    KB.init(cfg)
    for i in range(5):
        KB.create(cfg, f"task {i}", board="main", confirmed=True)

    everything = KB.list_tasks(cfg, board="main", limit=100)
    total = everything["total"]
    assert total == len(everything["tasks"]) == 5

    page1 = KB.list_tasks(cfg, board="main", limit=2, offset=0)
    page2 = KB.list_tasks(cfg, board="main", limit=2, offset=2)
    assert [t["id"] for t in page1["tasks"]] == \
        [t["id"] for t in everything["tasks"][:2]]
    assert [t["id"] for t in page2["tasks"]] == \
        [t["id"] for t in everything["tasks"][2:4]]
    assert page1["total"] == total and page2["has_more"] is True

    with pytest.raises(KB.KanbanError):
        KB.list_tasks(cfg, board="main", offset=-1)
