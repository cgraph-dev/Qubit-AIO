"""M8 — per-agent memory: a seat remembers, ranks, and never crosses another.

The office's whole premise (L2) is that a seat stops forgetting everything each
turn. A5 lands that as ONE memory authority — ``voicebridge/office/agent_memory``
— writing through the existing context engine (``voicebridge/context_store``),
never a second store (C3). This file proves exactly that, and it is written so
every assertion can fail:

1. **Round-trip, per agent.** ``remember`` then ``recall`` returns the text, for
   the seat that wrote it and for NO other seat — the split-brain bug §5.5
   forbids, caught at the memory boundary.
2. **The ranking is real.** rank = relevance + recency + importance (ai-town
   ``memory.ts``). Three memories are built with controlled importance and age
   so the predicted order is checkable; then a query term moves the order, which
   is the half a pure recency sort cannot fake.
3. **No store was added.** ``sqlite3.connect`` appears in exactly one office
   module (``store.py``) — a second store is the exact defect this milestone
   exists to prevent.
4. **``memory_written`` is emitted by the write itself** and reaches
   ``tower.state()``, so the ``glow`` pose is reachable *because a memory was
   really written* (C7: no event, no motion).
5. **Honesty.** A seat with no memories returns an empty list and ``known:
   False``; an empty text writes nothing and emits nothing.

Hermetic: tmp XDG state only; no network, no provider call, no live service.
"""
from __future__ import annotations

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _org(cfg):
    """A CEO above a worker — the smallest org ``org.hire`` will accept."""
    from voicebridge.office import ledger, org as O

    ledger.health(cfg)                       # creates the store
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    w1 = O.hire(cfg, "wk-1", seat_type="worker", supervisor_id=ceo,
                confirmed=True)["id"]
    w2 = O.hire(cfg, "wk-2", seat_type="worker", supervisor_id=ceo,
                confirmed=True)["id"]
    return {"ceo": ceo, "w1": w1, "w2": w2}


def _kinds(cfg, agent_id=None):
    from voicebridge.office import store

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        if agent_id is None:
            rows = conn.execute("SELECT kind FROM office_event ORDER BY id")
        else:
            rows = conn.execute("SELECT kind FROM office_event WHERE agent_id=?"
                                " ORDER BY id", (int(agent_id),))
        return [r["kind"] for r in rows]
    finally:
        conn.close()


# ================================================= 1. round-trip, per agent
def test_a_memory_round_trips_for_the_seat_that_wrote_it(tmp_path, monkeypatch):
    from voicebridge.office import agent_memory as M

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)

    out = M.remember(cfg, ids["w1"], kind="tick",
                     text="I finished the schema migration", importance=9)
    assert out["state"] == "OK"
    assert out["stored"] is True, "the memory bytes did not reach the context engine"

    got = M.recall(cfg, ids["w1"])
    assert got["known"] is True
    assert [m["text"] for m in got["memories"]] == ["I finished the schema migration"]
    assert got["memories"][0]["kind"] == "tick"
    assert got["memories"][0]["importance"] == 9


def test_a_memory_never_crosses_to_another_agent(tmp_path, monkeypatch):
    """The split-brain bug: seat B must not recall seat A's memory."""
    from voicebridge.office import agent_memory as M

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)

    M.remember(cfg, ids["w1"], kind="tick", text="the secret plan for w1",
               importance=8)
    M.remember(cfg, ids["w2"], kind="tick", text="something else entirely",
               importance=5)

    a = [m["text"] for m in M.recall(cfg, ids["w1"])["memories"]]
    b = [m["text"] for m in M.recall(cfg, ids["w2"])["memories"]]
    assert a == ["the secret plan for w1"]
    assert b == ["something else entirely"]
    assert not (set(a) & set(b)), "a memory leaked between two seats"

    # and a seat that wrote nothing recalls nothing
    assert M.recall(cfg, ids["ceo"])["memories"] == []


def test_a_note_is_a_note_event_and_a_memory_is_a_memory_event(
        tmp_path, monkeypatch):
    from voicebridge.office import agent_memory as M

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)

    M.remember(cfg, ids["w1"], kind="tick", text="did work", importance=5)
    M.note(cfg, ids["w1"], text="ask about the envelope")
    kinds = _kinds(cfg, ids["w1"])
    assert "memory_written" in kinds and "note_written" in kinds


# ============================================================= 2. the ranking
def test_recall_ranks_by_relevance_recency_and_importance(tmp_path, monkeypatch):
    """Three memories with identical text: importance decides.

    Recency is equal (written the same moment, no sleep) and relevance is equal
    (the same words), so the ONLY thing that can order them is importance — and
    the composite must put the highest importance first.
    """
    from voicebridge.office import agent_memory as M

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)

    M.remember(cfg, ids["w1"], kind="tick", text="identical text",
               importance=1)
    M.remember(cfg, ids["w1"], kind="tick", text="identical text",
               importance=5)
    M.remember(cfg, ids["w1"], kind="tick", text="identical text",
               importance=10)

    got = [m["importance"] for m in M.recall(cfg, ids["w1"], limit=3)["memories"]]
    assert got == [10, 5, 1], got


def test_recency_breaks_a_tie_between_equal_importance(tmp_path, monkeypatch):
    """Same importance, different age: the newer memory leads.

    The older row is back-dated in the store (a plain UPDATE, no clock mock), so
    recency — not write order — is the thing that decides.
    """
    from voicebridge.office import agent_memory as M, store

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)

    M.remember(cfg, ids["w1"], kind="tick", text="the older memory",
               importance=5)
    M.remember(cfg, ids["w1"], kind="tick", text="the newer memory",
               importance=5)

    # age the FIRST memory by 30 days (a week is ~40x its recency score)
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        oldest = conn.execute(
            "SELECT MIN(id) m FROM office_event WHERE kind='memory_written'"
            " AND agent_id=?", (ids["w1"],)).fetchone()["m"]
        conn.execute(
            "UPDATE office_event SET ts=ts-? WHERE id=?",
            (30 * 86400.0, int(oldest)))
        conn.commit()
    finally:
        conn.close()

    texts = [m["text"] for m in M.recall(cfg, ids["w1"])["memories"]]
    assert texts[0] == "the newer memory", texts


def test_a_query_moves_the_order_by_relevance(tmp_path, monkeypatch):
    """Relevance is a real term: a keyword match outranks a fresher memory."""
    from voicebridge.office import agent_memory as M, store

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)

    M.remember(cfg, ids["w1"], kind="tick", text="I migrated the ledger schema",
               importance=5)
    M.remember(cfg, ids["w1"], kind="tick", text="unrelated chatter",
               importance=5)
    # age the relevant one so only relevance can lift it back to the top
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        relevant = conn.execute(
            "SELECT MIN(id) m FROM office_event WHERE kind='memory_written'"
            " AND agent_id=?", (ids["w1"],)).fetchone()["m"]
        conn.execute("UPDATE office_event SET ts=ts-? WHERE id=?",
                     (10 * 86400.0, int(relevant)))
        conn.commit()
    finally:
        conn.close()

    matched = M.recall(cfg, ids["w1"], query="ledger schema")["memories"]
    assert matched[0]["text"] == "I migrated the ledger schema", \
        [(m["text"], m["score"]) for m in matched]
    assert matched[0]["relevance"] > matched[1]["relevance"]


# ========================================================== 3. one store only
def test_no_second_store_was_added_anywhere_in_the_office(tmp_path, monkeypatch):
    """C3/§5.5: exactly ONE sqlite connection authority in ``office/``."""
    import re
    from pathlib import Path

    office = (Path(__file__).resolve().parent.parent
              / "voicebridge" / "office")
    hits = {}
    for py in sorted(office.glob("*.py")):
        txt = py.read_text()
        n = len(re.findall(r"sqlite3\.connect", txt))
        if n:
            hits[py.name] = n
    assert set(hits) == {"store.py"}, (
        "a second memory/store authority appeared in office/: %r" % hits)


# ============================================== 4. the event and the payload
def test_memory_written_is_emitted_and_the_tower_shows_it(tmp_path, monkeypatch):
    from voicebridge.office import agent_memory as M, tower

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)

    before = _kinds(cfg, ids["w1"]).count("memory_written")
    M.remember(cfg, ids["w1"], kind="tick", text="I shipped the recall ranking",
               importance=7)
    after = _kinds(cfg, ids["w1"]).count("memory_written")
    assert after == before + 1, "remember() did not emit memory_written"

    # the pose the tower maps for memory_written is now reachable
    from voicebridge.office import tower as T
    assert T._ANIMATION["memory_written"] == "glow"

    state = tower.state(cfg)
    ch = [c for c in state["characters"] if c["agent_id"] == ids["w1"]][0]
    assert ch["memory"]["count"] == 1
    assert ch["memory"]["known"] is True
    assert ch["dossier"]["known"] is True
    assert "work" in ch["dossier"]["dimensions"]


def test_the_payload_carries_the_memory_field_for_a_preview_too(
        tmp_path, monkeypatch):
    """Additive + default-safe: the field exists even with nothing running."""
    from voicebridge.office import tower

    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge.office import ledger

    ledger.health(cfg)
    st = tower.state(cfg, preview=True)
    assert st["preview"] is True
    assert st["characters"], "the preview office has no characters"
    for c in st["characters"]:
        assert c["memory"] == {"count": 0, "known": False}
        assert c["dossier"]["known"] is False


# ================================================================= 5. honesty
def test_an_empty_seat_reports_known_false_rather_than_inventing(
        tmp_path, monkeypatch):
    from voicebridge.office import agent_memory as M

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)

    got = M.recall(cfg, ids["ceo"])
    assert got["memories"] == []
    assert got["known"] is False

    d = M.dossier(cfg, ids["ceo"])
    assert d["known"] is False
    assert d["note"], "an empty dossier must say WHY it is empty"


def test_an_empty_memory_writes_nothing_and_emits_nothing(tmp_path, monkeypatch):
    from voicebridge.office import agent_memory as M

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)

    out = M.remember(cfg, ids["w1"], kind="tick", text="   ", importance=9)
    assert out["state"] == "EMPTY"
    assert out["event_id"] is None
    assert _kinds(cfg, ids["w1"]) == [], (
        "an empty memory emitted an event — motion with no action behind it (C7)")


def test_importance_is_clamped_and_never_invented(tmp_path, monkeypatch):
    from voicebridge.office import agent_memory as M

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)

    assert M.remember(cfg, ids["w1"], kind="t", text="a",
                      importance=99)["importance"] == 10
    assert M.remember(cfg, ids["w1"], kind="t", text="b",
                      importance=-4)["importance"] == 1
    # an absent/nonsense score is the neutral middle, not a promotion
    assert M.remember(cfg, ids["w1"], kind="t", text="c")["importance"] == 5


# ============================================ 6. A4's consumer contract holds
def test_the_frozen_api_shape_a4_consumes(tmp_path, monkeypatch):
    """A4 reads ``recall(...)["memories"]`` and calls ``remember(kind=..., ...)``.

    This pins the exact shape A4 depends on, so a rename here fails loudly
    instead of silently emptying every seat's context.
    """
    from voicebridge.office import agent_memory as M

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    M.remember(cfg, ids["w1"], kind="autowork_tick", text="tick text",
               meta={"outcome": "OK"})

    out = M.recall(cfg, ids["w1"])
    assert isinstance(out, dict)
    assert isinstance(out["memories"], list)
    for m in out["memories"]:
        assert isinstance(m, dict)
        assert "text" in m and "summary" not in m      # A4 reads m["text"]
    # A4 may pass only these kwargs
    import inspect

    sig = inspect.signature(M.remember)
    assert {"kind", "text", "ref", "meta"} <= set(sig.parameters)
    assert inspect.signature(M.recall).parameters["limit"].default == 20
