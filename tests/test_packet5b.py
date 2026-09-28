"""Packet 5B tests: Mem0-derived contract (R8) + local extensions.

Covers add/search/update/correct/forget/export/purge, history, dedupe,
scope, expiry, tombstone, supersedes, restart, concurrent writes,
migration, private-file modes, staged inferred memories, secret rejection,
FTS5 search + fallback, and the explicit Letta non-use.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from voicebridge.memory import (  # noqa: E402
    MemoryStore, MemoryStoreError, SecretRejectedError, TIERS, norm_hash)


@pytest.fixture()
def store(tmp_path):
    s = MemoryStore(tmp_path / "mem.db")
    yield s
    s.close()


# ------------------------------------------------------------ core ops
def test_remember_and_get(store):
    rec = store.remember("my favorite demo color is teal")
    assert rec["tier"] == "recall"
    assert rec["scope"] == "default"
    got = store.get(rec["id"])
    assert got["text"] == "my favorite demo color is teal"
    assert rec["norm_hash"] == norm_hash("my favorite demo color is teal")


def test_mem0_contract_dedupe_skips_duplicates(store):
    r1 = store.remember("My  favorite   demo color is Teal")
    r2 = store.remember("my favorite demo color is teal")
    assert r1["id"] == r2["id"], "normalized-hash duplicate must be skipped"
    assert store.export().__len__() == 1
    actions = [h["action"] for h in store.history(r1["id"])]
    assert "dup_skip" in actions


def test_remember_empty_rejected(store):
    with pytest.raises(MemoryStoreError, match="empty"):
        store.remember("   ")


def test_tiers_all_valid(store):
    for tier in TIERS:
        rec = store.remember(f"fact in {tier}", tier=tier)
        assert rec["tier"] == tier
    with pytest.raises(MemoryStoreError, match="unknown tier"):
        store.remember("x", tier="nope")


# ---------------------------------------------------------------- search
def test_search_fts5_ranked_and_scoped(store):
    store.remember("user lives in Bucharest-demo", scope="work")
    store.remember("the demo editor is helix", scope="default")
    hits = store.search("Bucharest")
    assert any("Bucharest-demo" in h["text"] for h in hits)
    work = store.search("Bucharest", scope="work")
    assert len(work) == 1 and work[0]["scope"] == "work"
    default = store.search("editor", scope="default")
    assert any("helix" in h["text"] for h in default)


def test_search_excludes_tombstoned_and_staged(store):
    rec = store.remember("old project location is v1")
    store.forget(rec["id"])
    assert store.search("project location") == []
    sid = store.stage_inferred("inferred candidate fact", confidence=0.7)
    assert store.search("inferred candidate") == []
    assert store.staged()[0]["id"] == sid


def test_search_fallback_without_fts(monkeypatch):
    monkeypatch.setattr("voicebridge.memory.store.sqlite3",
                        sqlite3)  # no-op: emulate by forcing fts off
    from voicebridge.memory.store import MemoryStore as MS
    s = MS.__new__(MS)
    # easiest: open real store then toggle flag
    path = str(Path.cwd() / ".tmp-ms-fallback.db")
    try:
        st = MemoryStore(path)
        st._fts5 = False
        st.remember("plain fallback phrase for the like scan")
        assert len(st.search("fallback phrase")) == 1
        st.close()
    finally:
        try:
            Path(path).unlink()
            Path(path + "-wal").unlink()
            Path(path + "-shm").unlink()
        except OSError:
            pass


# ------------------------------------------------- correct / supersede
def test_correct_supersedes_and_outranks(store):
    store.remember("favorite demo color is blue")
    new = store.correct("favorite demo color is blue",
                        "favorite demo color is teal")
    assert new["supersedes"] is not None
    assert store.search("favorite demo color")[0]["text"].endswith("teal")
    # stale is tombstoned, not searchable
    stale = store.search("is blue")
    assert not any("blue" in s["text"] and "teal" not in s["text"]
                   for s in stale)
    hist = store.history(new["id"])
    assert any(h["action"] == "correct" for h in hist)


def test_correct_missing_raises(store):
    with pytest.raises(MemoryStoreError, match="no active memory"):
        store.correct("never remembered fact", "replacement")


# ------------------------------------------------- forget / purge
def test_forget_tombstone_and_purge(store):
    rec = store.remember("temporary note to remove")
    store.forget(rec["id"])
    assert store.search("temporary note") == []
    assert any(h["action"] == "forget" for h in store.history(rec["id"]))
    # export excludes it by default
    assert store.export() == []
    assert len(store.export(include_tombstoned=True)) == 1
    # purge hard-deletes
    store.purge(rec["id"])
    assert store.export(include_tombstoned=True) == []


def test_what_do_i_know(store):
    store.remember("call me demo", tier="core")
    store.remember("editor is helix", tier="recall")
    text = store.what_do_i_know()
    assert "call me demo" in text and "editor is helix" in text


# ------------------------------------------------- scope & sensitivity
def test_scope_isolation(store):
    store.remember("private work secret detail", scope="work")
    store.remember("personal favorite color teal", scope="personal")
    assert store.search("secret", scope="personal") == []
    assert store.search("color", scope="work") == []
    assert len(store.search("color", scope="personal")) == 1


# ----------------------------------------------------- expiry & secret
def test_expiry_filtered_from_search(store):
    rec = store.remember("expiring fact xyz", expires_at=time.time() - 5)
    assert store.search("expiring fact") == []
    assert store.get(rec["id"])["expires_at"] is not None


def test_secret_rejected_at_write_and_promote(store):
    with pytest.raises(SecretRejectedError):
        store.remember("my api key is sk-demo12345678901234567890")
    with pytest.raises(SecretRejectedError):
        store.remember("password: hunter2!")
    sid = store.stage_inferred("candidate with sk-abcdefghijklmnopqrstuvwxyz",
                               confidence=0.9)
    with pytest.raises(SecretRejectedError):
        store.promote(sid)


# ------------------------------------------- staged inferred memories
def test_inferred_staged_not_promoted_until_explicit(store):
    sid = store.stage_inferred("user might like dark theme", confidence=0.6,
                               source_ids=["ev-1", "ev-2"])
    assert store.search("dark theme") == []
    assert store.staged()[0]["provenance"] == ["ev-1", "ev-2"]
    rec = store.promote(sid)
    assert rec["staged"] == 0 and rec["kind"] == "fact"
    assert store.search("dark theme") != []
    with pytest.raises(MemoryStoreError):
        store.promote(sid)  # already promoted


# -------------------------------------------- history append-only
def test_history_append_only_across_ops(store):
    rec = store.remember("versioned fact one")
    store.correct("versioned fact one", "versioned fact two")
    store.forget(rec["id"])
    hs = store.history(rec["id"])
    actions = [h["action"] for h in hs]
    assert "corrected" in actions and "forget" in actions
    # append-only: later ops don't erase earlier
    assert len(hs) >= 3


# ------------------------------------------------ restart persistence
def test_restart_persistence(tmp_path):
    path = tmp_path / "mem.db"
    s1 = MemoryStore(path)
    s1.remember("durable across restart")
    s1.close()
    s2 = MemoryStore(path)
    assert s2.search("durable across restart")
    s2.close()


# -------------------------------------------------- concurrent writes
def test_concurrent_writes(tmp_path):
    path = tmp_path / "mem.db"
    s = MemoryStore(path)
    errors = []

    def worker(i):
        try:
            for j in range(10):
                s.remember(f"thread-{i}-fact-{j}")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len(s.export()) == 40
    s.close()


# ------------------------------------------------------- migration
def test_migration_user_version(tmp_path):
    path = tmp_path / "mem.db"
    s = MemoryStore(path)
    v = s._conn.execute("PRAGMA user_version").fetchone()[0]
    assert v >= 1
    s.close()
    # reopen migrates to current idempotently
    s2 = MemoryStore(path)
    assert s2._conn.execute("PRAGMA user_version").fetchone()[0] == v
    s2.close()


# ------------------------------------------------ private file modes
def test_private_file_modes(tmp_path):
    path = tmp_path / "mem.db"
    s = MemoryStore(path)
    assert (path.stat().st_mode & 0o777) == 0o600
    exp = tmp_path / "export.json"
    s.remember("exportable fact")
    s.export(path=exp)
    assert (exp.stat().st_mode & 0o777) == 0o600
    s.close()


# -------------------------------------------------------- FTS5 mode
def test_fts5_only_mode_available():
    # this runtime has FTS5; default mode is FTS5-only and works
    path = str(Path.cwd() / ".tmp-ms-fts.db")
    try:
        s = MemoryStore(path)
        s.remember("fts only phrase to retrieve")
        assert s.search("phrase")[0]["text"] == "fts only phrase to retrieve"
        s.close()
    finally:
        try:
            Path(path).unlink()
            Path(path + "-wal").unlink()
            Path(path + "-shm").unlink()
        except OSError:
            pass


# ----------------------------------------------------- Letta non-use
def test_letta_is_never_imported():
    import sys
    src = Path("voicebridge/memory/store.py").read_text()
    # documentation references are fine; IMPORTS/usage are forbidden
    assert "import letta" not in src.lower()
    assert "from letta" not in src.lower()
    assert "import mem0" not in src.lower()
    assert "from mem0" not in src.lower()
    assert not any(m.startswith("letta") or m.startswith("mem0")
                   for m in sys.modules)


# -------------------------------------------------------- Mem0 vectors
def test_mem0_hash_match_vector(tmp_path):
    """Mem0 contract vector: identical content -> 'hash match' skip."""
    s = MemoryStore(tmp_path / "mem.db")
    a = s.remember("the sky appears blue to me")
    b = s.remember("the sky appears blue to me")
    assert a["id"] == b["id"]
    assert any(h["action"] == "dup_skip" for h in s.history(a["id"]))
    s.close()