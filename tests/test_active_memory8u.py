"""Packet 8U — active-recall memory layer (OpenClaw v2026.9.4 delta D4).

Real behavior over the real ``MemoryStore`` in temp state dirs: bounded policy
persistence + confirmation, session-scoped recall with ordering/cap/redaction,
resolve context budgeting, typed escalation, and estop fail-closed.
"""
from __future__ import annotations

import json

import pytest

from voicebridge import active_memory as AM


def _cfg(tmp_path, monkeypatch, extra: str = ""):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n" + extra)
    return C.Config.load(p)


def _no_estop(monkeypatch):
    monkeypatch.setattr(AM, "estop_engaged", lambda _cfg: False)


def _store(cfg):
    from voicebridge.memory import MemoryStore

    return MemoryStore(AM.memory_db_path(cfg))


def _enable(cfg, **kw):
    out = AM.policy_set(cfg, enabled=True, confirmed=True, **kw)
    assert out["state"] == "OK", out
    return out


# ------------------------------------------------------------------- policy
def test_policy_set_requires_confirmation_and_persists(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _no_estop(monkeypatch)

    out = AM.policy_set(cfg, enabled=True, max_items=3)
    assert out["state"] == "CONFIRM_REQUIRED"
    assert out["code"] == AM.CONFIRM_CODE
    assert not AM.policy_path(cfg).exists()

    out = AM.policy_set(cfg, enabled=True, min_score=0.25, max_items=3,
                        session_scoped=True, confirmed=True)
    assert out["state"] == "OK"
    path = AM.policy_path(cfg)
    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600

    got = AM.policy_get(cfg)
    assert got["state"] == "OK"
    assert got["policy"]["enabled"] is True
    assert got["policy"]["min_score"] == 0.25
    assert got["policy"]["max_items"] == 3
    assert got["policy"]["session_scoped"] is True

    # a second Config over the same state dir observes the persisted policy
    cfg2 = _cfg(tmp_path, monkeypatch)
    assert AM.policy_get(cfg2)["policy"]["max_items"] == 3


def test_policy_set_bounds_and_noop_rejected(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _no_estop(monkeypatch)
    for bad in ({"min_score": 2.0}, {"min_score": -0.1},
                {"max_items": 0}, {"max_items": AM.MAX_ITEMS_LIMIT + 1},
                {"session_scoped": "yes"}, {"enabled": 1}):
        out = AM.policy_set(cfg, confirmed=True, **bad)
        assert out["code"] == AM.BAD_INPUT_CODE, bad
    assert not AM.policy_path(cfg).exists()
    assert AM.policy_set(cfg, confirmed=True)["code"] == AM.BAD_INPUT_CODE


def test_corrupt_policy_fails_closed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _no_estop(monkeypatch)
    path = AM.policy_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json")

    got = AM.policy_get(cfg)
    assert got["state"] == "CORRUPT" and got["code"] == AM.CORRUPT_CODE
    assert AM.recall(cfg, "anything")["code"] == AM.CORRUPT_CODE
    # never silently overwritten
    assert AM.policy_set(cfg, enabled=True,
                         confirmed=True)["code"] == AM.CORRUPT_CODE
    assert path.read_text() == "{not-json"


# ------------------------------------------------------------------- status
def test_status_honest_and_read_only(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _no_estop(monkeypatch)

    st = AM.status(cfg)
    assert st["state"] == "OK" and st["enabled"] is False
    assert st["memories"] is None and st["store_state"] == "absent"

    store = _store(cfg)
    store.remember("status countable fact", scope="default")
    store.close()

    st = AM.status(cfg)
    assert st["memories"] == 1 and st["store_state"] == "ok"

    _enable(cfg)
    assert AM.status(cfg)["enabled"] is True


# ------------------------------------------------------------------- recall
def test_disabled_policy_gates_recall_and_resolve(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _no_estop(monkeypatch)
    store = _store(cfg)
    store.remember("gated memory fact", scope="default")
    store.close()

    out = AM.recall(cfg, "gated memory")
    assert out["state"] == "DISABLED"
    assert out["code"] == AM.UNAVAILABLE_CODE
    assert out["results"] == []

    res = AM.resolve(cfg, "gated memory")
    assert res["state"] == "DISABLED" and res["code"] == AM.UNAVAILABLE_CODE
    assert res["context"] == "" and res["blocks"] == []


def test_recall_ordering_cap_redaction_session_scope(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _no_estop(monkeypatch)
    _enable(cfg, min_score=0.0, max_items=3, session_scoped=True)

    store = _store(cfg)
    store.remember("alpha topic low", scope="s1", confidence=0.2)
    store.remember("alpha topic high", scope="s1", confidence=0.9)
    store.remember("alpha topic mid", scope="s1", confidence=0.5)
    store.remember("alpha topic other session", scope="s2", confidence=0.99)
    secret = "alpha topic secret sk-abcdefghijklmnopqrstuvwxyz"
    cur = store._conn.execute(
        "INSERT INTO memories(mkey,tier,scope,text,norm_hash,source,confidence)"
        " VALUES(?,?,?,?,?,?,?)",
        ("s1\x00legacy-secret", "recall", "s1", secret, "legacy-secret",
         "user", 0.95))
    store._index_fts(cur.lastrowid, "recall", "s1", secret)
    store._conn.commit()
    store.close()

    out = AM.recall(cfg, "alpha topic", session="s1")
    assert out["state"] == "OK"
    assert len(out["results"]) == 3
    assert all(r["scope"] == "s1" for r in out["results"])
    # ordering: secret 0.95, high 0.9, mid 0.5 (low and s2 dropped)
    assert out["results"][0]["snippet"].startswith("alpha topic secret")
    assert out["results"][1]["snippet"] == "alpha topic high"
    assert out["results"][2]["snippet"] == "alpha topic mid"
    scores = [r["score"] for r in out["results"]]
    assert scores == sorted(scores, reverse=True)
    # redaction: the raw secret never leaves and is masked
    blob = json.dumps(out)
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in blob
    assert "REDACTED" in blob

    # session scoping off: the same query now reaches every scope
    _enable(cfg, min_score=0.0, max_items=5, session_scoped=False)
    allscopes = AM.recall(cfg, "alpha topic", session="s1")
    assert any(r["scope"] == "s2" for r in allscopes["results"])


def test_recall_threshold_dedupe_and_limit(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _no_estop(monkeypatch)
    _enable(cfg, min_score=0.55, max_items=5, session_scoped=False)

    store = _store(cfg)
    store.remember("beta topic strong", scope="default", confidence=0.9)
    store.remember("beta topic weak", scope="default", confidence=0.2)
    store.remember("Beta   TOPIC strong", scope="default", confidence=0.8)
    store.close()

    out = AM.recall(cfg, "beta topic")
    assert out["state"] == "OK"
    assert [r["snippet"] for r in out["results"]] == ["beta topic strong"]
    # a caller limit narrows, never widens, the configured cap
    _enable(cfg, min_score=0.0, max_items=5, session_scoped=False)
    assert len(AM.recall(cfg, "beta topic", limit=1)["results"]) == 1
    assert len(AM.recall(cfg, "beta topic", limit=5)["results"]) == 2


def test_recall_empty_and_bad_input_typed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _no_estop(monkeypatch)
    _enable(cfg, max_items=5)

    out = AM.recall(cfg, "zzz nothing here zzz")
    assert out["state"] == "EMPTY" and out["code"] == AM.EMPTY_CODE
    assert out["results"] == []
    assert AM.recall(cfg, "   ")["code"] == AM.BAD_INPUT_CODE
    assert AM.recall(cfg, "x", limit=0)["code"] == AM.BAD_INPUT_CODE
    assert AM.recall(cfg, "x", limit=True)["code"] == AM.BAD_INPUT_CODE


# ------------------------------------------------------------------ resolve
def test_resolve_context_is_bounded_and_refs_present(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _no_estop(monkeypatch)
    _enable(cfg, min_score=0.0, max_items=4)

    store = _store(cfg)
    for i in range(4):
        store.remember("gamma topic " + ("detail " * 100) + str(i),
                       scope="default", provenance=[f"ev-{i}"])
    store.close()

    res = AM.resolve(cfg, "gamma topic")
    assert res["state"] == "OK" and res["blocks"]
    assert len(res["blocks"]) <= 4
    assert len(res["context"]) <= AM.MAX_CONTEXT_CHARS
    assert res["refs"] and all(r.startswith("memory:") or r.startswith("ev-")
                               for r in res["refs"])
    assert all(b["refs"] for b in res["blocks"])

    empty = AM.resolve(cfg, "zzz nothing here zzz")
    assert empty["state"] == "EMPTY" and empty["code"] == AM.EMPTY_CODE
    assert empty["context"] == "" and empty["refs"] == []


# ----------------------------------------------------------------- escalate
def test_escalate_typed_gates_and_effect(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _no_estop(monkeypatch)

    # disabled policy gates escalation
    out = AM.escalate(cfg, 1, reason="important", confirmed=True)
    assert out["state"] == "DISABLED" and out["code"] == AM.UNAVAILABLE_CODE

    _enable(cfg)
    store = _store(cfg)
    rec = store.remember("delta escalatable fact", scope="default",
                         confidence=0.5, tier="recall")
    # confirmation gate
    out = AM.escalate(cfg, rec["id"], reason="important")
    assert out["code"] == AM.CONFIRM_CODE
    # bad input / not found
    assert AM.escalate(cfg, 0, reason="x",
                       confirmed=True)["code"] == AM.BAD_INPUT_CODE
    assert AM.escalate(cfg, rec["id"], reason="",
                       confirmed=True)["code"] == AM.BAD_INPUT_CODE
    assert AM.escalate(cfg, 10_000_000, reason="x",
                       confirmed=True)["code"] == AM.NOT_FOUND_CODE
    # real effect
    out = AM.escalate(cfg, rec["id"], reason="important", confirmed=True)
    assert out["state"] == "OK" and out["previous_tier"] == "recall"
    assert out["tier"] == "working"
    assert store.get(rec["id"])["tier"] == "working"
    assert any(h["action"] == "update" for h in store.history(rec["id"]))

    # escalation reasons are redacted into history
    AM.escalate(cfg, rec["id"], reason="bump sk-abcdefghijklmnopqrstuvwxyz",
                confirmed=True)
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in json.dumps(
        store.history(rec["id"]))
    store.close()


# -------------------------------------------------------------------- estop
def test_estop_fail_closed_every_operation(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(AM, "estop_engaged", lambda _cfg: True)

    st = AM.status(cfg)
    assert st["state"] == "ESTOP" and st["estop"] is True
    assert AM.recall(cfg, "x")["code"] == AM.ESTOP_CODE
    assert AM.resolve(cfg, "x")["code"] == AM.ESTOP_CODE
    assert AM.escalate(cfg, 1, reason="x",
                       confirmed=True)["code"] == AM.ESTOP_CODE
    assert AM.policy_set(cfg, enabled=True,
                         confirmed=True)["code"] == AM.ESTOP_CODE


def test_estop_probe_fails_closed_on_error(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)

    def boom(_cfg):
        raise RuntimeError("latch unreadable")

    monkeypatch.setattr("voicebridge.plugins.estop_engaged", boom)
    assert AM.estop_engaged(cfg) is True
