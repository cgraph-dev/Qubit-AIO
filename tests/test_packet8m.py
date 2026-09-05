"""Packet 8M tests: optional GENOME-compatible semantic memory adapter.

Covers the pinned GENOME contract (docs/UPSTREAM_REUSE.md R23) as adapted over
the existing 5B MemoryStore: disabled-by-default + provider selection, LLM-free
ingest, semantic recall with typed unavailable/degraded states, bi-temporal
belief facts (valid_from/valid_until boundaries, believer-aware invalidation,
origin-bound authority, conflicts), stored-data-only explain, deterministic
hash-chained journal + replay/verify (torn tail, interior corruption, seq gaps,
edits, rollback-to-prefix), correction/dedupe/secret rejection, concurrent
writes, restart persistence, export/import, resource bounds, and CLI parity.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from voicebridge.memory import (  # noqa: E402
    MemoryStore, MemoryStoreError, SemanticMemory, SemanticMemoryError,
    SemanticProviderUnavailable, SecretRejectedError, Journal,
    JournalCorruptionError, snapshot_hash, verify_chain, replay,
    verify_journal, HashProjection, PROVIDERS, norm_hash,
    load_semantic_state, write_semantic_state, redact_secrets,
    SEMANTIC_STATE_SCHEMA)


@pytest.fixture()
def sm(tmp_path):
    s = SemanticMemory(tmp_path / "mem.db", enabled=True, provider="hash",
                       journal_path=tmp_path / "mem.journal")
    yield s
    try:
        s.close()
    except Exception:
        pass


# ------------------------------------------------------ disabled by default
def test_disabled_by_default(tmp_path):
    s = SemanticMemory(tmp_path / "mem.db")
    st = s.status()
    assert st["enabled"] is False
    assert st["provider"] == "none"
    s.close()


def test_unknown_provider_rejected(tmp_path):
    s = SemanticMemory(tmp_path / "mem.db")
    with pytest.raises(SemanticMemoryError, match="unknown provider"):
        s.set_provider("bogus")
    with pytest.raises(SemanticMemoryError, match="unknown provider"):
        SemanticMemory(tmp_path / "m.db", provider="bogus")
    s.close()


def test_unknown_source_rejected(tmp_path):
    s = SemanticMemory(tmp_path / "mem.db")
    with pytest.raises(SemanticMemoryError, match="unknown"):
        s.remember("hello world", source="attacker")
    with pytest.raises(SemanticMemoryError, match="unknown"):
        s.stage_inferred("x", confidence=0.5, source="attacker")
    with pytest.raises(SemanticMemoryError, match="unknown"):
        s.correct("a", "b", source="attacker")
    with pytest.raises(SemanticMemoryError, match="user"):
        s.stage_inferred("x", confidence=0.5, source="user")
    with pytest.raises(SemanticMemoryError, match="unknown"):
        s.forget(1, requested_by="attacker")
    with pytest.raises(SemanticMemoryError, match="user"):
        s.promote(1, requested_by="model")
    s.close()


# ----------------------------------------------------------- LLM-free ingest
def test_ingest_is_llm_free_and_network_free(tmp_path, monkeypatch):
    calls = []

    class SpyEmbed:
        name = "spy"

        def embed(self, texts):
            calls.append(texts)
            return [[1.0, 0.0]] * len(texts)

    s = SemanticMemory(tmp_path / "mem.db", enabled=True, provider="hash")
    s._embed = SpyEmbed()
    s.remember("a user fact without any model")
    s.stage_inferred("an inferred candidate", confidence=0.5)
    s.record_belief("alice", "city", "Tokyo")
    assert calls == [], "ingest must never contact the embedding provider"


# ------------------------------------------------------------- semantic recall
def test_recall_baseline_when_disabled(tmp_path):
    s = SemanticMemory(tmp_path / "mem.db")  # disabled, provider none
    s.remember("favorite demo color is teal")
    out = s.recall("teal")
    assert out["provider"] == "none" and out["engine"] == "fts5"
    assert not out["degraded"] and not out["unavailable"]
    assert any("teal" in r["text"] for r in out["results"])
    s.close()


def test_recall_semantic_reranks_and_bounds(sm):
    for i in range(10):
        sm.remember(f"demo fact about topic number {i}")
    out = sm.recall("demo fact about topic", limit=3)
    assert out["engine"] == "fts5+semantic" and out["provider"] == "hash"
    assert len(out["results"]) <= 3
    scores = [r["score"] for r in out["results"]]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= sc <= 1.0001 for sc in scores)


def test_recall_provider_failure_is_typed(tmp_path):
    s = SemanticMemory(tmp_path / "mem.db", enabled=True, provider="hash")
    s.remember("persisted fact to fall back to")

    class Boom:
        def embed(self, texts):
            raise SemanticProviderUnavailable("embedding endpoint down")

    s._embed = Boom()
    out = s.recall("persisted")
    assert out["unavailable"] is True and out["degraded"] is True
    assert out["engine"] == "fts5"  # honest fallback, never fake semantic
    assert any("persisted" in r["text"] for r in out["results"])
    s.close()


def test_hash_projection_is_deterministic():
    hp = HashProjection()
    a = hp.embed(["the quick brown fox"])
    b = hp.embed(["the quick brown fox"])
    assert a == b
    assert len(a[0]) == 256


# ------------------------------------------------------------ temporal/belief
def test_belief_window_boundaries(sm):
    t0 = 1000.0
    sm.record_belief("alice", "lives_in", "Tokyo", valid_from=t0,
                     valid_until=2000.0)
    assert [r["text"] for r in sm.facts_valid_at("alice", t0)] == [
        "alice: lives_in: Tokyo"]           # valid_from inclusive
    assert sm.facts_valid_at("alice", 2000.0) == []  # valid_until exclusive
    assert [r["text"] for r in sm.facts_valid_at("alice", 1500.0)]


def test_belief_re_record_closes_prior_window(sm):
    t0 = time.time() - 100
    sm.record_belief("alice", "lives_in", "Tokyo", valid_from=t0)
    sm.record_belief("alice", "lives_in", "Osaka")
    assert [r["text"] for r in sm.current_facts("alice")] == [
        "alice: lives_in: Osaka"]
    assert [r["text"] for r in sm.facts_valid_at("alice", t0 + 10)] == [
        "alice: lives_in: Tokyo"]
    tl = sm.entity_timeline("alice")
    assert tl[0]["text"].endswith("Osaka") and tl[1]["text"].endswith("Tokyo")
    assert tl[1]["valid_until"] is not None


def test_invalidate_belief_closes_window(sm):
    rec = sm.record_belief("alice", "lives_in", "Tokyo",
                           valid_from=time.time() - 100)
    assert sm.current_facts("alice")
    sm.invalidate_belief(rec["id"], at=time.time() - 1)
    assert sm.current_facts("alice") == []


def test_believer_aware_invalidation(sm):
    sm.record_belief("alice", "lives_in", "Tokyo", believed_by="agent-a")
    sm.record_belief("alice", "lives_in", "Osaka", believed_by="agent-b")
    # both beliefs coexist because they belong to different believers
    assert len(sm.current_facts("alice")) == 2


def test_origin_bound_authority(sm):
    sm.record_belief("alice", "lives_in", "Tokyo", source="user")
    sm.record_belief("alice", "lives_in", "Osaka", source="web")
    sm.record_belief("alice", "lives_in", "Kyoto", source="tool")
    # external-origin belief VALUES are stored only as non-recoverable refs;
    # web/tool facts cannot close the user fact; tool outranks web and closes it
    texts = {r["text"] for r in sm.current_facts("alice")}
    assert "alice: lives_in: Tokyo" in texts                 # user fact raw
    assert len(texts) == 2
    assert all("lives_in: [external:" in t for t in texts
               if "Tokyo" not in t)                          # external = ref
    assert not any("Osaka" in t or "Kyoto" in t for t in texts)
    # all overlapping validity windows are surfaced (Tokyo coexisted with both)
    assert len(sm.belief_conflicts()) == 2
    # a second USER fact closes every lower-or-equal fact
    sm.record_belief("alice", "lives_in", "Sapporo", source="user")
    assert {r["text"] for r in sm.current_facts("alice")} == {
        "alice: lives_in: Sapporo"}


def test_future_dated_fact_caps_current(sm):
    future = time.time() + 1000
    sm.record_belief("alice", "lives_in", "Futureville",
                     valid_from=future, source="user")
    sm.record_belief("alice", "lives_in", "Nowville", source="user")
    # never two open windows: the current fact is capped at the future fact
    assert [r["text"] for r in sm.current_facts("alice")] == [
        "alice: lives_in: Nowville"]
    assert [r["text"] for r in sm.facts_valid_at("alice", future + 1)] == [
        "alice: lives_in: Futureville"]

    # invalid boundaries rejected
    with pytest.raises(SemanticMemoryError, match="finite"):
        sm.record_belief("alice", "lives_in", "Bad", valid_from=float("nan"))
    with pytest.raises(SemanticMemoryError, match="finite"):
        sm.record_belief("alice", "lives_in", "Bad", confidence=float("inf"))


def test_belief_secret_rejected(sm):
    with pytest.raises(SecretRejectedError):
        sm.record_belief("alice", "password", "hunter2!", source="web")


def test_explain_is_stored_data_only(sm):
    rec = sm.record_belief("alice", "lives_in", "Tokyo",
                           provenance=["ev-1"], source="user")
    ex = sm.explain(rec["id"])
    assert ex["source"] == "user" and ex["source_trust"] == 3
    assert ex["provenance"] == ["ev-1"]
    assert ex["validity"]["valid_from"] is not None
    assert any(h["action"] == "remember" for h in ex["history"])
    assert "history" in ex and ex["kind"] == "belief"


# ------------------------------------------------------------- journal
def test_journal_replay_reproduces_live(sm):
    sm.remember("fact alpha")
    rec = sm.record_belief("alice", "lives_in", "Tokyo")
    sm.correct("fact alpha", "fact alpha corrected")
    sm.forget(rec["id"], reason="test")
    ok, reason = verify_journal(sm._journal_path, sm)
    assert ok, reason
    assert sm.journal_verify()["state"] == "ok"


def test_journal_chain_detects_edit(sm, tmp_path):
    sm.remember("untampered fact")
    jp = Path(sm._journal_path)
    lines = jp.read_text().splitlines()
    rec = json.loads(lines[0])
    rec["text"] = "tampered"
    lines[0] = json.dumps(rec)
    jp.write_text("\n".join(lines) + "\n")
    ok, reason = verify_chain(jp)
    assert not ok and "hash" in reason


def test_journal_chain_detects_removed_line(sm, tmp_path):
    sm.remember("one")
    sm.remember("two")
    sm.remember("three")
    jp = Path(sm._journal_path)
    lines = jp.read_text().splitlines()
    jp.write_text("\n".join(lines[1:]) + "\n")  # drop seq 1 from the middle
    ok, reason = verify_chain(jp)
    assert not ok and "seq" in reason


def test_journal_interior_corruption_raises(tmp_path):
    jp = tmp_path / "mem.journal"
    j = Journal(jp)
    j.append({"op": "remember", "text": "a"})
    with open(jp, "a") as fh:
        fh.write("{not-json\n")
    j.append({"op": "remember", "text": "b"})
    with pytest.raises(JournalCorruptionError):
        Journal.read(jp)


def test_journal_torn_tail_flagged(tmp_path):
    sm = SemanticMemory(tmp_path / "mem.db", journal_path=tmp_path / "mem.journal")
    sm.remember("surviving fact")
    jp = Path(sm._journal_path)
    with open(jp, "a") as fh:
        fh.write('{"op": "remember", "text": "crashe')
    read = Journal.read(jp)
    assert read.torn is True
    # an uncommitted torn tail never became a mutation: verify stays honest ok
    ok, reason = verify_journal(jp, sm)
    assert ok, reason
    sm.close()


def test_journal_lost_committed_line_fails(tmp_path):
    sm = SemanticMemory(tmp_path / "mem.db", journal_path=tmp_path / "mem.journal")
    sm.remember("a")   # line 1, committed
    sm.remember("b")   # line 2, committed
    jp = Path(sm._journal_path)
    # simulate a crash that lost the final committed journal line
    jp.write_text("\n".join(jp.read_text().splitlines()[:1]) + "\n")
    ok, reason = verify_journal(jp, sm)
    assert not ok, "lost committed journal line must fail verification"
    assert "replay does not reproduce" in reason
    sm.close()


def test_journal_replay_to_prefix_is_rollback(sm):
    sm.remember("before rollback")
    sm.remember("after rollback point")
    n = replay(sm._journal_path,
               sm.__class__(Path(tempfile.mkdtemp()) / "r.db"))
    assert n == 2
    read = Journal.read(sm._journal_path)
    replay_until = replay(sm._journal_path,
                          sm.__class__(Path(tempfile.mkdtemp()) / "r2.db"),
                          until_seq=read.ops[0]["seq"])
    assert replay_until == 1


# ------------------------------------------------- correction / dedupe
def test_correction_supersedes_and_dedup(sm):
    r1 = sm.remember("favorite color is blue")
    r2 = sm.remember("favorite color is blue")
    assert r1["id"] == r2["id"]  # Mem0 dedup preserved
    new = sm.correct("favorite color is blue", "favorite color is teal")
    assert new["supersedes"] == r1["id"]
    assert not any("is blue" in x["text"] and "teal" not in x["text"]
                   for x in sm.recall("favorite color")["results"])


# ------------------------------------------------------- restart persistence
def test_restart_persistence(tmp_path):
    db, jp = tmp_path / "mem.db", tmp_path / "mem.journal"
    s1 = SemanticMemory(db, enabled=True, provider="hash", journal_path=jp)
    s1.remember("durable across restart")
    s1.close()
    s2 = SemanticMemory(db, enabled=True, provider="hash", journal_path=jp)
    assert s2.recall("durable across restart")["results"]
    s2.close()


# ------------------------------------------------------------- concurrency
def test_concurrent_writes(tmp_path):
    s = SemanticMemory(tmp_path / "mem.db", enabled=True, provider="hash",
                       journal_path=tmp_path / "mem.journal")
    errors = []

    def worker(i):
        try:
            for j in range(8):
                s.remember(f"thread-{i}-fact-{j}")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len(s.store.export()) == 32
    s.close()


# --------------------------------------------------------- export / import
def test_export_import_roundtrip(tmp_path):
    src = SemanticMemory(tmp_path / "src.db", enabled=True, provider="hash")
    src.remember("portable fact alpha", scope="work")
    src.record_belief("alice", "lives_in", "Tokyo")
    with pytest.raises(SecretRejectedError):
        src.remember("my api key is sk-demo12345678901234567890")
    bundle = src.export()
    dst = SemanticMemory(tmp_path / "dst.db")
    res = dst.import_records(bundle)
    assert res["stored"] == 2 and res["rejected"] == 0
    assert len(dst.store.export()) == 2
    # imported data can never claim source=user
    assert all(r["source"] != "user" for r in dst.store.export()
               if "portable fact" in r["text"] or "lives_in" in r["text"])
    src.close()
    dst.close()


# ------------------------------------------------------------ migration
def test_migration_v2_additive(tmp_path):
    db = tmp_path / "mem.db"
    s1 = MemoryStore(db)
    v = s1._conn.execute("PRAGMA user_version").fetchone()[0]
    assert v >= 3, "store must be at migration 3 (scope-qualified dedup key)"
    cols = {r[1] for r in s1._conn.execute("PRAGMA table_info(memories)")}
    assert {"valid_from", "valid_until", "believed_by"} <= cols
    s1.close()
    # legacy rows stay untouched
    s2 = MemoryStore(db)
    s2.remember("plain fact")
    assert s2._conn.execute("PRAGMA user_version").fetchone()[0] == v
    s2.close()


# ------------------------------------------------------------ resource bound
def test_recall_is_bounded_and_journal_is_append_only(sm):
    for i in range(25):
        sm.remember(f"bounded fact {i}")
    out = sm.recall("bounded fact", limit=4)
    assert len(out["results"]) <= 4
    lines = len(Path(sm._journal_path).read_text().splitlines())
    assert lines == 25  # one journal line per op, append-only


# -------------------------------------------------------------------- CLI
def _cli_env(tmp_path) -> dict:
    cfg = tmp_path / "vb.yaml"
    state = tmp_path / "state"
    cfg.write_text(f"logging:\n  jsonl_path: {state}\n")
    env = dict(os.environ)
    env["VOICE_BRIDGE_CONFIG"] = str(cfg)
    return env


def _cli(tmp_path, *argv):
    return subprocess.run(
        [sys.executable, "-m", "voicebridge", "memory", *argv],
        capture_output=True, text=True, timeout=120,
        env=_cli_env(tmp_path))


def test_cli_status_dry_run_and_belief(tmp_path):
    r = _cli(tmp_path, "status")
    assert r.returncode == 0
    assert json.loads(r.stdout)["enabled"] is False
    r = _cli(tmp_path, "enable", "--provider", "hash")
    assert r.returncode == 0 and json.loads(r.stdout)["state"] == "preview"
    r = _cli(tmp_path, "belief-record", "alice", "lives_in", "Tokyo")
    assert r.returncode == 0
    assert json.loads(r.stdout)["text"] == "alice: lives_in: Tokyo"
    r = _cli(tmp_path, "belief-at", "alice")
    assert r.returncode == 0
    facts = json.loads(r.stdout)["facts"]
    assert facts and facts[0]["text"].endswith("Tokyo")
    r = _cli(tmp_path, "journal-verify")
    assert r.returncode == 0 and json.loads(r.stdout)["state"] == "ok"
    r = _cli(tmp_path, "disable", "--confirm")
    assert r.returncode == 0 and json.loads(r.stdout)["state"] == "ok"

# ============================================================ Packet 8M REPAIR
# Regression tests for every rejected-audit finding (secret boundaries,
# provenance authority, temporal validation, scope isolation, corrupt/newer
# state, deterministic replay re-running checks, no-network status, and no
# unauthorized runtime surface).


# ------------------------------------------------------------ secret boundaries
def test_secret_rejected_at_stage_and_correct(sm):
    with pytest.raises(SecretRejectedError):
        sm.stage_inferred("api key is sk-abcdefghijklmnopqrstuvwxyz",
                          confidence=0.5)
    with pytest.raises(SecretRejectedError):
        sm.correct("never mind", "password: hunter2!")
    sm.remember("base fact for correction")
    with pytest.raises(SecretRejectedError):
        sm.correct("base fact for correction",
                   "updated with ghp_abcdefghijklmnopqrstuvwxyz123456")
    # journal has no secret lines at all (boundary rejection precedes writes)
    raw = Path(sm._journal_path).read_text()
    assert "sk-abcdef" not in raw and "hunter2" not in raw


def test_secret_rejected_in_import(tmp_path):
    s = SemanticMemory(tmp_path / "m.db")
    res = s.import_records([
        {"text": "good fact", "source": "user"},
        {"text": "bad api key sk-abcdefghijklmnopqrstuvwxyz", "source": "web"},
    ])
    assert res["stored"] == 1 and res["rejected"] == 1
    s.close()


def test_history_and_explain_redact_secrets(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    # simulate a legacy/5B history row that already carries a secret shape
    store._history(1, "debug", {"by_fact": "api key is sk-abcdefghijklmnopqrstuvwxyz"})
    h = store.history(1)
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in json.dumps(h)
    assert "REDACTED" in json.dumps(h)
    store.close()


def test_recall_and_explain_redact_legacy_secret(tmp_path):
    db = tmp_path / "m.db"
    store = MemoryStore(db)
    # legacy row with a secret shape that predates boundary rejection
    cur = store._conn.execute(
        "INSERT INTO memories(mkey,tier,scope,text,norm_hash,source)"
        " VALUES(?,?,?,?,?,?)",
        (f"legacy\x00{hash('x')}", "recall", "default",
         "api key is sk-abcdefghijklmnopqrstuvwxyz",
         hash("api key is sk-abcdefghijklmnopqrstuvwxyz"), "user"))
    store._index_fts(cur.lastrowid, "recall", "default",
                     "api key is sk-abcdefghijklmnopqrstuvwxyz")
    store._conn.commit()
    sm = SemanticMemory(store, enabled=True, provider="hash")
    out = sm.recall("api key", limit=5)
    blob = json.dumps(out)
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in blob
    assert "REDACTED" in blob
    rid = out["results"][0]["id"]
    expl = sm.explain(rid)
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in json.dumps(expl)
    sm.close()


# -------------------------------------------------------------- authority
def test_low_trust_cannot_forget_or_purge_user_fact(sm):
    rec = sm.remember("user protected fact", scope="default")
    with pytest.raises(SemanticMemoryError, match="origin-bound"):
        sm.forget(rec["id"], requested_by="web")
    with pytest.raises(SemanticMemoryError, match="origin-bound"):
        sm.purge(rec["id"], requested_by="tool")
    # same-trust can forget
    rec2 = sm.remember("distinctive tool-only phrase", source="tool")
    sm.forget(rec2["id"], requested_by="tool")
    assert all("distinctive tool-only phrase" not in r["text"]
               for r in sm.recall("distinctive tool-only phrase")["results"])
    sm.forget(rec["id"], requested_by="user")


def test_low_trust_cannot_correct_user_fact(sm):
    sm.remember("user city fact is bucharest", scope="default")
    with pytest.raises(SemanticMemoryError, match="origin-bound"):
        sm.correct("user city fact is bucharest",
                   "user city fact is london", source="web")
    # user can correct it
    rec = sm.correct("user city fact is bucharest",
                     "user city fact is london", source="user")
    assert "london" in rec["text"]


def test_promote_requires_user(sm):
    sid = sm.stage_inferred("candidate fact to promote", confidence=0.6)
    with pytest.raises(SemanticMemoryError, match="user"):
        sm.promote(sid, requested_by="model")
    rec = sm.promote(sid, requested_by="user")
    assert rec["staged"] == 0


def test_low_trust_cannot_invalidate_user_belief(sm):
    rec = sm.record_belief("alice", "city", "Tokyo", source="user",
                           valid_from=time.time() - 100)
    with pytest.raises(SemanticMemoryError, match="origin-bound"):
        sm.invalidate_belief(rec["id"], at=time.time() - 50,
                             requested_by="web")
    sm.invalidate_belief(rec["id"], at=time.time() - 50, requested_by="user")


def test_import_never_claims_user(tmp_path):
    s = SemanticMemory(tmp_path / "m.db")
    res = s.import_records([
        {"text": "imported as user? no.", "source": "user"},
        {"text": "imported as tool stays tool", "source": "tool"},
    ])
    assert res["stored"] == 2
    sources = {r["source"] for r in s.store.export()}
    assert "user" not in sources
    s.close()


# ---------------------------------------------------------- temporal validation
def test_temporal_validation_rejects_invalid(tmp_path):
    from voicebridge.memory import MemoryStoreError as MSE
    s = SemanticMemory(tmp_path / "m.db")
    with pytest.raises((SemanticMemoryError, MSE), match="finite"):
        s.remember("x", confidence=float("nan"))
    with pytest.raises((SemanticMemoryError, MSE), match="finite"):
        s.remember("expired", expires_at=float("nan"))
    with pytest.raises(SemanticMemoryError, match="finite"):
        s.record_belief("e", "t", "v", valid_from=float("inf"))
    with pytest.raises((SemanticMemoryError, MSE), match="later than"):
        s.remember("windowed", valid_from=100.0, valid_until=50.0)
    with pytest.raises(SemanticMemoryError, match="later than"):
        s.record_belief("e", "t", "v", valid_from=100.0, valid_until=50.0)
    rec = s.record_belief("e", "t", "v", valid_from=time.time() - 100)
    with pytest.raises(SemanticMemoryError, match="precedes"):
        s.invalidate_belief(rec["id"], at=time.time() - 200)
    with pytest.raises(SemanticMemoryError, match="finite"):
        s.facts_valid_at("e", float("nan"))
    s.invalidate_belief(rec["id"], at=time.time() - 50)
    with pytest.raises(SemanticMemoryError, match="already closed"):
        s.invalidate_belief(rec["id"], at=time.time() - 40)
    s.close()


# -------------------------------------------------------------- scope isolation
def test_scope_isolation_same_text_independent(tmp_path):
    s = SemanticMemory(tmp_path / "m.db")
    a = s.remember("identical cross scope fact", scope="work")
    b = s.remember("identical cross scope fact", scope="personal")
    assert a["id"] != b["id"]
    assert len(s.store.export()) == 2
    # same-scope dedup preserved
    assert s.remember("identical cross scope fact", scope="work")["id"] == a["id"]
    s.close()


def test_concurrent_scoped_writes_same_content(tmp_path):
    db = tmp_path / "m.db"
    s = SemanticMemory(db, enabled=True, provider="hash",
                       journal_path=tmp_path / "mem.journal")
    errors = []

    def worker(scope):
        try:
            for _ in range(10):
                s.remember("the exact same sentence", scope=scope)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"scope-{i}",))
               for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    # one row per scope, no cross-scope UNIQUE collision
    assert len(s.store.export()) == 4
    for r in s.store.export():
        assert r["scope"].startswith("scope-")
    s.close()


def test_concurrent_same_scope_same_content_no_crash(tmp_path):
    s = SemanticMemory(tmp_path / "m.db")
    errors = []

    def worker(_):
        try:
            for _ in range(10):
                s.remember("colliding sentence", scope="default")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len(s.store.export()) == 1  # deduped to a single row
    s.close()


def test_scope_key_migration_from_legacy_db(tmp_path):
    db = tmp_path / "legacy.db"
    c = sqlite3.connect(str(db))
    c.executescript(
        "CREATE TABLE memories(id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " mkey TEXT UNIQUE, tier TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'default',"
        " kind TEXT NOT NULL DEFAULT 'fact', text TEXT NOT NULL, norm_hash TEXT NOT NULL,"
        " confidence REAL DEFAULT 0.5, sensitivity TEXT DEFAULT 'personal', source TEXT,"
        " provenance TEXT DEFAULT '[]', created_at REAL, updated_at REAL, expires_at REAL,"
        " supersedes INTEGER, tombstoned INTEGER DEFAULT 0, staged INTEGER DEFAULT 0,"
        " valid_from REAL, valid_until REAL, believed_by TEXT);"
        "CREATE TABLE history(id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " memory_id INTEGER NOT NULL, action TEXT NOT NULL, payload TEXT, ts REAL);")
    c.execute("PRAGMA user_version=2")
    h = norm_hash("legacy fact")
    c.execute("INSERT INTO memories(mkey,tier,scope,text,norm_hash)"
              " VALUES(?,?,?,?,?)", (h, "recall", "work", "legacy fact", h))
    c.commit()
    c.close()
    s = MemoryStore(db)
    assert s._conn.execute("PRAGMA user_version").fetchone()[0] >= 3
    row = s._conn.execute("SELECT mkey FROM memories WHERE id=1").fetchone()
    assert row["mkey"].startswith("work\x00")
    # migrated legacy row still dedups in its original scope
    assert s.remember("legacy fact", scope="work")["id"] == 1
    # same content in a new scope is now stored independently
    other = s.remember("legacy fact", scope="personal")
    assert other["id"] != 1
    s.close()


# ---------------------------------------------------------- corrupt/newer state
def test_state_corrupt_and_newer_never_silent(tmp_path):
    p = tmp_path / "semantic.json"
    p.write_text("{not-json")
    state, health = load_semantic_state(p)
    assert health["state"] == "degraded"
    p.write_text(json.dumps({"enabled": True, "provider": "hash",
                             "schema_version": SEMANTIC_STATE_SCHEMA + 5}))
    state, health = load_semantic_state(p)
    assert health["state"] == "newer"
    assert "newer" in health["detail"]
    # invalid provider also typed degraded
    p.write_text(json.dumps({"enabled": True, "provider": "telepathy",
                             "schema_version": 1}))
    state, health = load_semantic_state(p)
    assert health["state"] == "degraded"
    assert state["provider"] == "none"  # defaults used, but flagged degraded


def test_state_atomic_write_fsync_mode(tmp_path):
    p = tmp_path / "semantic.json"
    write_semantic_state(p, {"enabled": True, "provider": "hash",
                             "schema_version": 1})
    assert (p.stat().st_mode & 0o777) == 0o600
    assert list(tmp_path.glob("*.json.tmp.*")) == []
    state, health = load_semantic_state(p)
    assert health["state"] == "ok" and state["provider"] == "hash"


def test_journal_corrupt_status_is_degraded(tmp_path):
    jp = tmp_path / "mem.journal"
    # interior corruption (malformed line that is NOT the last line)
    jp.write_text("{\"op\": \"remember\", \"text\": \"x\"}\n"
                  "{\"op\": \"corrupt\"\n"
                  "{\"op\": \"remember\", \"text\": \"y\"}\n")
    s = SemanticMemory(tmp_path / "m.db", journal_path=jp)
    st = s.status()
    assert st["journal_degraded"] is True
    assert st["state"] == "degraded"
    assert st["journal_detail"]
    s.close()


# -------------------------------------------------------- replay re-checks
def test_replay_reruns_authority_and_validation(tmp_path):
    jp = tmp_path / "mem.journal"
    s = SemanticMemory(tmp_path / "m.db", journal_path=jp)
    s.remember("user fact for replay", source="user")
    s.forget(s.recall("user fact for replay")["results"][0]["id"],
             requested_by="user")
    s.close()
    # a forged replay op (unknown source) must fail closed
    fresh = SemanticMemory(tmp_path / "fresh.db")
    from voicebridge.memory.journal import apply_op
    with pytest.raises(SemanticMemoryError, match="unknown"):
        apply_op(fresh, {"op": "remember", "text": "x", "source": "attacker"})
    fresh.close()
    # a forged low-trust forget of a user fact must fail authority re-check
    store = MemoryStore(tmp_path / "m2.db")
    uf = store.remember("protected user fact", source="user")
    sm = SemanticMemory(store, journal_path=tmp_path / "j2")
    with pytest.raises(SemanticMemoryError, match="origin-bound"):
        apply_op(sm, {"op": "forget", "id": uf["id"],
                      "requested_by": "web"})
    sm.close()
    # valid journal replay still reproduces the live store
    ok, reason = verify_journal(jp, SemanticMemory(tmp_path / "m.db"))
    assert ok, reason


# --------------------------------------------- provider disabled / no network
def test_status_never_contacts_provider(tmp_path):
    calls = []

    class SpyEmbed:
        def embed(self, texts):
            calls.append(texts)
            return [[1.0]] * len(texts)

    s = SemanticMemory(tmp_path / "m.db", enabled=True, provider="hash")
    s._embed = SpyEmbed()
    st = s.status()
    assert calls == [], "status() must never contact the embedding provider"
    assert st["embed_available"] is None
    probe = s.check_provider()   # opt-in probe does contact
    assert calls and probe["available"] is True
    s.close()


def test_disabled_ingest_and_status_no_network(tmp_path):
    calls = []

    class SpyEmbed:
        def embed(self, texts):
            calls.append(texts)
            return [[1.0]] * len(texts)

    s = SemanticMemory(tmp_path / "m.db")  # disabled by default
    s._embed = SpyEmbed()
    s.remember("no model should be contacted")
    s.stage_inferred("no model either", confidence=0.5)
    st = s.status()
    assert calls == [] and st["provider"] == "none"
    s.close()


def test_no_unauthorized_runtime_surface(tmp_path):
    import sys
    src = Path("voicebridge/memory/semantic.py").read_text()
    for bad in ("import fastapi", "import uvicorn", "import mcp",
                "import flask", "import torch", "import sentence_transformers",
                "import numpy"):
        assert bad not in src
    assert not any(m.startswith(("fastapi", "uvicorn", "mcp", "flask"))
                   for m in sys.modules)
    s = SemanticMemory(tmp_path / "m.db", enabled=True, provider="hash")
    s.remember("fallback fact")
    # provider unavailability is typed, not a crash
    class Boom:
        def embed(self, texts):
            raise SemanticProviderUnavailable("no model installed")
    s._embed = Boom()
    out = s.recall("fallback")
    assert out["unavailable"] is True and out["degraded"] is True
    assert any(r["text"] == "fallback fact" for r in out["results"])
    s.close()


def test_cli_corrupt_state_degraded_and_refused(tmp_path):
    state = tmp_path / "state"
    mem = state / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "semantic.json").write_text("{corrupt")
    r = _cli(tmp_path, "status")
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["state"] == "degraded" and out["state_error"]
    # mutation refused without explicit confirmation (never silent reset)
    r = _cli(tmp_path, "enable", "--provider", "hash")
    assert r.returncode == 4
    # explicit confirmed overwrite backs up the corrupt file first
    r = _cli(tmp_path, "enable", "--provider", "hash", "--confirm")
    assert r.returncode == 0 and json.loads(r.stdout)["state"] == "ok"
    assert len(list(mem.glob("semantic.json.corrupt-*"))) == 1


def test_cli_newer_state_refused(tmp_path):
    state = tmp_path / "state"
    mem = state / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "semantic.json").write_text(json.dumps(
        {"enabled": True, "provider": "hash", "schema_version": 99}))
    r = _cli(tmp_path, "status")
    assert json.loads(r.stdout)["state"] == "newer"
    # newer state is NEVER overwritten, even with --confirm
    r = _cli(tmp_path, "enable", "--confirm")
    assert json.loads(r.stdout)["state"] == "failed"


def test_fresh_install_status_not_degraded(tmp_path):
    # a disabled store with a configured-but-never-written journal is healthy
    s = SemanticMemory(tmp_path / "m.db",
                       journal_path=tmp_path / "mem.journal")
    st = s.status()
    assert st["state"] == "ok"
    assert st["journal_degraded"] is False
    s.close()


# ============================================================== 8M REJECTION 2
# Repairs: (1) private external-content leak, (2) invalid semantic state
# configuration, (3) directory-fsync/write failure must never report success.

from voicebridge.memory.semantic import (  # noqa: E402
    external_ref, looks_like_external_ref, SemanticStateError,
    STATE_TIMEOUT_MAX, STATE_TOP_K_MAX)

_PRIVATE = ("the rust-colored umbrella on my porch is for "
            "grandma's annual visit")


def _all_surfaces(sm, store) -> str:
    blob = []
    if sm._journal_path is not None and Path(sm._journal_path).is_file():
        blob.append(Path(sm._journal_path).read_text(errors="replace"))
    for row in store._conn.execute("SELECT text FROM memories").fetchall():
        blob.append(str(row["text"]))
    for row in store._conn.execute("SELECT payload FROM history").fetchall():
        blob.append(str(row["payload"] or ""))
    blob.append(json.dumps(sm.recall(_PRIVATE[:20], limit=50)))
    active = store._active_select(scope=None, staged=False)
    if active:
        blob.append(json.dumps([sm.explain(r["id"]) for r in active[:10]]))
    blob.append(json.dumps(sm.export()))
    return "\n".join(blob)


def test_external_private_text_never_leaks(tmp_path):
    for source in ("web", "tool", "model", "observed"):
        db = tmp_path / f"{source}.db"
        s = SemanticMemory(db, enabled=True, provider="hash",
                           journal_path=tmp_path / f"{source}.journal")
        rec = s.remember(_PRIVATE, source=source)
        stored = rec["text"]
        assert looks_like_external_ref(stored), source
        assert _PRIVATE not in stored
        blob = _all_surfaces(s, s.store)
        assert _PRIVATE not in blob, f"{source} leaked into an output surface"
        assert "external:" in blob
        s.close()


def test_external_private_text_never_leaks_via_belief_stage_import(tmp_path):
    s = SemanticMemory(tmp_path / "m.db", enabled=True, provider="hash",
                       journal_path=tmp_path / "mem.journal")
    s.record_belief("alice", "note", _PRIVATE, source="web")
    s.stage_inferred(_PRIVATE, source="model", confidence=0.5)
    s.import_records([{"text": _PRIVATE, "source": "tool"}])
    blob = _all_surfaces(s, s.store)
    assert _PRIVATE not in blob
    # user-origin private memory is preserved raw (legitimate boundary)
    s.remember(_PRIVATE, source="user")
    assert _PRIVATE in json.dumps(s.store.export())
    s.close()


def test_external_private_text_never_leaks_via_correct(tmp_path):
    s = SemanticMemory(tmp_path / "m.db", journal_path=tmp_path / "j")
    s.remember(_PRIVATE, source="tool")            # stored as a ref
    stored = s.store._active_select(scope=None, staged=False)[0]["text"]
    s.correct(stored, "replacement tool value", source="tool")
    blob = _all_surfaces(s, s.store)
    assert _PRIVATE not in blob
    s.close()


def test_external_private_text_absent_from_cli(tmp_path):
    r = _cli(tmp_path, "belief-record", "alice", "note", _PRIVATE,
             "--source", "web")
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert _PRIVATE not in json.dumps(out)
    assert "external:" in out["text"]


def test_export_never_leaks_legacy_external_raw(tmp_path):
    db = tmp_path / "m.db"
    store = MemoryStore(db)
    cur = store._conn.execute(
        "INSERT INTO memories(mkey,tier,scope,text,norm_hash,source)"
        " VALUES(?,?,?,?,?,?)",
        (f"x\x00{hash('q')}", "recall", "default", _PRIVATE,
         hash("q"), "web"))
    store._index_fts(cur.lastrowid, "recall", "default", _PRIVATE)
    store._conn.commit()
    sm = SemanticMemory(store, enabled=True, provider="hash")
    blob = json.dumps(sm.export())
    assert _PRIVATE not in blob
    blob2 = json.dumps(sm.recall(_PRIVATE[:20], limit=5))
    assert _PRIVATE not in blob2
    sm.close()


# ------------------------------------------------------------ state config
@pytest.mark.parametrize("bad", [
    {"enabled": "yes"},
    {"enabled": 1},
    {"provider": "telepathy"},
    {"timeout_s": "abc"},
    {"timeout_s": 0},
    {"timeout_s": -3},
    {"timeout_s": float("inf")},
    {"timeout_s": STATE_TIMEOUT_MAX + 1},
    {"top_k": 5.5},
    {"top_k": 0},
    {"top_k": -2},
    {"top_k": STATE_TOP_K_MAX + 1},
    {"embed_model": ""},
    {"embed_model": "bad model!"},
    {"ollama_url": "not-a-url"},
    {"ollama_url": "ftp://host/x"},
    {"ollama_url": ""},
    {"mystery_field": 1},
])
def test_load_state_rejects_invalid_values(tmp_path, bad):
    p = tmp_path / "semantic.json"
    p.write_text(json.dumps({"schema_version": 1, **bad}))
    state, health = load_semantic_state(p)
    assert health["state"] == "degraded", bad
    assert health["detail"]
    # defaults are returned but flagged; nothing silently coerced
    assert state["enabled"] is False and state["provider"] == "none"


def test_load_state_newer_schema(tmp_path):
    p = tmp_path / "semantic.json"
    p.write_text(json.dumps({"enabled": True, "provider": "hash",
                             "schema_version": SEMANTIC_STATE_SCHEMA + 1}))
    state, health = load_semantic_state(p)
    assert health["state"] == "newer"
    assert "newer" in health["detail"]


def test_load_state_valid_values_ok(tmp_path):
    p = tmp_path / "semantic.json"
    p.write_text(json.dumps({"enabled": True, "provider": "hash",
                             "timeout_s": 12.5, "top_k": 8,
                             "embed_model": "nomic-embed-text",
                             "ollama_url": "http://127.0.0.1:11434",
                             "schema_version": 1}))
    state, health = load_semantic_state(p)
    assert health["state"] == "ok"
    assert state["enabled"] is True and state["top_k"] == 8


def test_write_state_validates_before_writing(tmp_path):
    p = tmp_path / "semantic.json"
    with pytest.raises(SemanticStateError, match="invalid"):
        write_semantic_state(p, {"enabled": "yes", "provider": "hash",
                                 "schema_version": 1})
    assert not p.exists()
    with pytest.raises(SemanticStateError, match="invalid"):
        write_semantic_state(p, {"enabled": True, "provider": "hash",
                                 "timeout_s": 0, "schema_version": 1})
    assert not p.exists()
    # a valid write succeeds and returns the canonical state
    canonical = write_semantic_state(
        p, {"enabled": True, "provider": "hash", "schema_version": 1})
    assert canonical["top_k"] == 5  # unspecified keys default
    assert (p.stat().st_mode & 0o777) == 0o600


# ------------------------------------------------------ write failure paths
def test_write_state_file_fsync_failure(tmp_path, monkeypatch):
    p = tmp_path / "semantic.json"
    real_fsync = os.fsync
    calls = {"n": 0}

    def boom(fd):
        calls["n"] += 1
        if calls["n"] == 1:     # file fsync fails
            raise OSError(5, "I/O error")
        return real_fsync(fd)

    monkeypatch.setattr("voicebridge.memory.semantic.os.fsync", boom)
    with pytest.raises(SemanticStateError, match="write failed"):
        write_semantic_state(p, {"enabled": True, "provider": "hash"})
    assert not p.exists()                       # not persisted
    assert list(tmp_path.glob("*.json.tmp.*")) == []   # temp cleaned up


def test_write_state_rename_failure(tmp_path, monkeypatch):
    p = tmp_path / "semantic.json"
    real_replace = os.replace

    def boom(a, b):
        raise OSError(18, "invalid cross-device link")

    monkeypatch.setattr("voicebridge.memory.semantic.os.replace", boom)
    with pytest.raises(SemanticStateError, match="rename failed"):
        write_semantic_state(p, {"enabled": True, "provider": "hash"})
    assert not p.exists()
    assert list(tmp_path.glob("*.json.tmp.*")) == []


def test_write_state_directory_fsync_failure(tmp_path, monkeypatch):
    p = tmp_path / "semantic.json"
    real_fsync = os.fsync
    calls = {"n": 0}

    def boom(fd):
        calls["n"] += 1
        if calls["n"] == 2:     # second fsync = directory fsync
            raise OSError(5, "I/O error on dir")
        return real_fsync(fd)

    monkeypatch.setattr("voicebridge.memory.semantic.os.fsync", boom)
    with pytest.raises(SemanticStateError) as exc:
        write_semantic_state(p, {"enabled": True, "provider": "hash"})
    assert exc.value.durability_uncertain is True
    assert exc.value.state_path == str(p)
    assert "UNCERTAIN" in str(exc.value) and "recovery" in str(exc.value)
    # the rename already happened: the NEW state is in place, durability
    # uncertain - the previous state is NOT claimed to be preserved
    assert p.exists()
    assert '"provider": "hash"' in p.read_text()
    assert list(tmp_path.glob("*.json.tmp.*")) == []
    # rollback contract: a validated re-write succeeds normally afterwards
    write_semantic_state(p, {"enabled": False, "provider": "none"})
    assert '"provider": "none"' in p.read_text()


def test_write_state_mkdir_failure_is_typed(tmp_path):
    base = tmp_path / "noperm"
    base.mkdir()
    try:
        os.chmod(base, 0o500)
        with pytest.raises(SemanticStateError, match="directory creation"):
            write_semantic_state(base / "sub" / "semantic.json",
                                 {"enabled": True, "provider": "hash"})
    finally:
        os.chmod(base, 0o700)


def test_write_state_invalid_parent_path_is_typed(tmp_path):
    # a regular file in place of the parent directory -> mkdir failure
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    with pytest.raises(SemanticStateError, match="directory creation"):
        write_semantic_state(blocker / "semantic.json",
                             {"enabled": True, "provider": "hash"})
    assert not (blocker / "semantic.json").exists()


def test_write_state_rejects_unknown_fields(tmp_path):
    p = tmp_path / "semantic.json"
    with pytest.raises(SemanticStateError, match="unsupported state field"):
        write_semantic_state(p, {"enabled": True, "provider": "hash",
                                 "sneaky_field": 1, "schema_version": 1})
    assert not p.exists()


def test_load_state_rejects_unknown_fields(tmp_path):
    p = tmp_path / "semantic.json"
    p.write_text(json.dumps({"enabled": True, "provider": "hash",
                             "schema_version": 1, "sneaky_field": 1}))
    state, health = load_semantic_state(p)
    assert health["state"] == "degraded"
    assert "unsupported state field" in health["detail"]


def test_interrupted_state_write_recovers(tmp_path):
    # a stale temp file from a crashed/concurrent process is inert and does
    # not corrupt the persisted state
    p = tmp_path / "semantic.json"
    write_semantic_state(p, {"enabled": False, "provider": "none"})
    stale = p.with_name(f"{p.name}.tmp.99999.deadbeef")
    stale.write_text("{partial garbage")
    state, health = load_semantic_state(p)
    assert health["state"] == "ok"
    assert state["provider"] == "none"
    # recovery: a subsequent validated write succeeds and leaves no remnant
    # of its OWN temp; the stale foreign temp is untouched
    write_semantic_state(p, {"enabled": True, "provider": "hash"})
    assert '"provider": "hash"' in p.read_text()
    assert stale.read_text() == "{partial garbage"
    state2, health2 = load_semantic_state(p)
    assert health2["state"] == "ok" and state2["provider"] == "hash"
    stale.unlink()


def test_cli_malformed_state_config_invalid(tmp_path):
    state = tmp_path / "state"
    mem = state / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "semantic.json").write_text(json.dumps(
        {"enabled": "yes", "provider": "hash", "schema_version": 1}))
    r = _cli(tmp_path, "status")
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["state"] == "degraded" and "enabled" in out["state_error"]
    # refused WITHOUT the explicit confirmation (never silent coercion)
    r = _cli(tmp_path, "enable")
    assert r.returncode == 4
    # the documented explicit recovery contract (--confirm) backs up the
    # malformed file and writes a validated replacement
    r = _cli(tmp_path, "enable", "--confirm")
    assert r.returncode == 0 and json.loads(r.stdout)["state"] == "ok"
    assert len(list(mem.glob("semantic.json.corrupt-*"))) == 1


# -------------------------------------------------- replay after redaction
def test_replay_after_external_redaction_is_honest(tmp_path):
    jp = tmp_path / "mem.journal"
    s = SemanticMemory(tmp_path / "m.db", enabled=True, provider="hash",
                       journal_path=jp)
    s.remember(_PRIVATE, source="web")          # stored as ref
    s.remember("user-owned sentence stays", source="user")
    s.stage_inferred(_PRIVATE, source="model", confidence=0.6)
    ok, reason = s.journal_verify()
    assert ok, reason
    # replay reproduces the refs and the user text; the raw private text is
    # never required, so integrity is claimed honestly for what was stored
    raw = jp.read_text()
    assert _PRIVATE not in raw
    out = s.journal_replay()
    assert out["state"] == "ok"
    s.close()


def test_rejected_secret_leaves_no_journal_line(tmp_path):
    jp = tmp_path / "mem.journal"
    s = SemanticMemory(tmp_path / "m.db", journal_path=jp)
    with pytest.raises(SecretRejectedError):
        s.remember("password: hunter2!")
    s.remember("clean fact after rejection")
    raw = jp.read_text()
    assert "hunter2" not in raw
    ok, reason = s.journal_verify()
    assert ok, reason
    s.close()


def test_adapter_rejects_bad_embed_config(tmp_path):
    s = SemanticMemory(tmp_path / "m.db")
    with pytest.raises(SemanticMemoryError, match="top_k"):
        s.set_top_k(0)
    with pytest.raises(SemanticMemoryError, match="top_k"):
        s.set_top_k(STATE_TOP_K_MAX + 1)
    with pytest.raises(SemanticMemoryError, match="timeout_s"):
        s.set_provider("ollama", timeout_s=0)
    with pytest.raises(SemanticMemoryError, match="timeout_s"):
        s.set_provider("ollama", timeout_s=STATE_TIMEOUT_MAX + 1)
    with pytest.raises(SemanticMemoryError, match="embed_model"):
        s.set_provider("ollama", embed_model="bad model!")
    with pytest.raises(SemanticMemoryError, match="ollama_url"):
        s.set_provider("ollama", ollama_url="not a url")
    s.close()


def test_correct_error_never_echoes_private_text(tmp_path):
    s = SemanticMemory(tmp_path / "m.db", journal_path=tmp_path / "j")
    SHORT = "rust umbrella on the porch for grandma"
    with pytest.raises((SemanticMemoryError, MemoryStoreError)) as exc:
        s.correct(SHORT, "x", source="web")
    assert SHORT not in str(exc.value)
    # the store-level message no longer echoes caller content either
    store = MemoryStore(tmp_path / "m2.db")
    with pytest.raises(MemoryStoreError) as exc2:
        store.correct(SHORT, "x")
    assert SHORT not in str(exc2.value)
    s.close()


def test_belief_identifier_cannot_smuggle_prose(tmp_path):
    s = SemanticMemory(tmp_path / "m.db", journal_path=tmp_path / "j")
    SENT = "the rust colored umbrella on my porch is for grandmas annual visit"
    for name, val in (("entity", SENT), ("fact_type", SENT)):
        with pytest.raises(SemanticMemoryError, match="must be"):
            s.record_belief(name == "entity" and val or "alice",
                            name == "fact_type" and val or "note",
                            "v", source="web")
    # nothing about the prose appears anywhere persisted
    blob = "".join(
        Path(p).read_text(errors="replace")
        for p in tmp_path.iterdir() if p.is_file())
    assert SENT not in blob
    assert "umbrella" not in blob
    s.close()


def test_ollama_url_loopback_policy(tmp_path):
    for u in ("http://127.0.0.1:11434", "http://localhost:11434",
              "http://[::1]:11434"):
        p = tmp_path / "s.json"
        p.write_text(json.dumps({"enabled": True, "provider": "ollama",
                                 "ollama_url": u, "schema_version": 1}))
        _, health = load_semantic_state(p)
        assert health["state"] == "ok", u
    for u in ("http://192.168.1.10:11434", "http://ollama.example.com",
              "https://127.0.0.1:11434", "ftp://127.0.0.1/x"):
        p = tmp_path / "s2.json"
        p.write_text(json.dumps({"enabled": True, "provider": "ollama",
                                 "ollama_url": u, "schema_version": 1}))
        _, health = load_semantic_state(p)
        assert health["state"] == "degraded", u
    # the adapter rejects non-loopback too
    s = SemanticMemory(tmp_path / "m.db")
    with pytest.raises(SemanticMemoryError, match="ollama_url"):
        s.set_provider("ollama", ollama_url="http://10.0.0.9:11434")
    s.close()


def test_private_sentinel_absent_from_everywhere(tmp_path):
    """Sentinel proof: a unique private sentence supplied through every
    external-origin path must not appear in ANY persisted file or printed
    output in the state directory."""
    SENT = "the violet clock on my desk reads three seventeen precisely"
    state = tmp_path / "state"
    mem = state / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    s = SemanticMemory(mem / "memory.db", enabled=True, provider="hash",
                       journal_path=mem / "memory.db.journal")
    s.remember(SENT, source="web")            # value path: stored as a ref
    s.stage_inferred(SENT, source="model", confidence=0.5)
    s.import_records([{"text": SENT, "source": "tool"}])
    with pytest.raises((SemanticMemoryError, MemoryStoreError)):
        s.correct(SENT, "x", source="web")        # error message must be clean
    with pytest.raises(SemanticMemoryError, match="must be"):
        s.record_belief(SENT, "note", "v", source="web")
    s.record_belief("alice", "note", SENT, source="web")  # value -> ref
    blob = []
    for p in state.rglob("*"):
        if p.is_file():
            blob.append(p.read_text(errors="replace"))
    blob.append(json.dumps(s.recall(SENT[:20], limit=50)))
    blob.append(json.dumps(s.export()))
    assert SENT not in "\n".join(blob)
    assert "violet clock" not in "\n".join(blob)
    s.close()
    # CLI printed output is also clean
    r = _cli(tmp_path, "belief-record", "alice", "note", SENT, "--source", "web")
    assert SENT not in (r.stdout + r.stderr)
    assert "external:" in r.stdout


def _mp_state_worker(path, idx, n_writes, q):
    """Real subprocess writer: each process writes a DISTINCT complete state
    (distinct top_k) repeatedly. Every write must fully succeed."""
    from voicebridge.memory import write_semantic_state
    try:
        for _ in range(n_writes):
            write_semantic_state(path, {"enabled": True, "provider": "hash",
                                        "top_k": (idx % 20) + 1,
                                        "schema_version": 1})
        q.put((idx, "ok", None))
    except Exception as exc:  # pragma: no cover
        q.put((idx, "err", repr(exc)))


def test_multiprocess_concurrent_state_writes(tmp_path):
    """Real multi-process concurrent write_semantic_state: no corruption, no
    partial file, no duplicate state, no lost (mixed) state, and no false
    success; recovery afterward works."""
    import multiprocessing as mp
    ctx = mp.get_context("fork")
    p = tmp_path / "semantic.json"
    nprocs, n_writes = 12, 4
    q = ctx.Queue()
    procs = [ctx.Process(target=_mp_state_worker,
                         args=(str(p), i, n_writes, q))
             for i in range(nprocs)]
    for pr in procs:
        pr.start()
    # concurrent reader: the target must ALWAYS be a complete, valid state
    # (atomic rename) - never partial, corrupt, or mixed
    samples = 0
    deadline = time.time() + 20
    while any(pr.is_alive() for pr in procs) and time.time() < deadline:
        if p.exists():
            data = json.loads(p.read_text())
            assert data["provider"] == "hash" and 1 <= data["top_k"] <= 20
            samples += 1
        time.sleep(0.002)
    for pr in procs:
        pr.join(timeout=30)
    results = []
    for _ in procs:
        try:
            results.append(q.get(timeout=10))
        except Exception:  # pragma: no cover
            results.append((None, "err", "no result"))
    assert all(r[1] == "ok" for r in results), results   # no false success
    assert all(pr.exitcode == 0 for pr in procs), [pr.exitcode for pr in procs]
    # the final state is complete and equals EXACTLY one writer's state
    final = json.loads(p.read_text())
    assert final["enabled"] is True and final["provider"] == "hash"
    assert 1 <= final["top_k"] <= 20
    assert (p.stat().st_mode & 0o777) == 0o600
    # no leftover unique temp files and no partial/duplicate state anywhere
    assert list(tmp_path.glob("*.json.tmp.*")) == []
    # recovery afterward: a validated write + load succeed
    write_semantic_state(p, {"enabled": False, "provider": "none"})
    state, health = load_semantic_state(p)
    assert health["state"] == "ok"
    assert state["enabled"] is False and state["provider"] == "none"
