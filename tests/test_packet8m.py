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
    # web/tool facts cannot close the user fact; tool outranks web and closes it
    assert {r["text"] for r in sm.current_facts("alice")} == {
        "alice: lives_in: Tokyo", "alice: lives_in: Kyoto"}
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
    assert not p.with_suffix(".json.tmp").exists()
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
