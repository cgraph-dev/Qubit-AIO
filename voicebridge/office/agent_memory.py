"""Per-agent memory and personality: what a seat did, learned, and was told.

This is the ONE memory authority for the office (C3, ``90-AGENT-CONTRACT.md``
§2.2). A4's autonomous loop calls :func:`recall` / :func:`remember`; A4 must not
open a store of its own, and neither does this module — the memory bytes are
written through the existing context engine
:mod:`voicebridge.context_store` (``add_memory`` / ``search``), which is the
seam ``95-CONTRACT.md`` names ("Memory -> ``voicebridge/context_store.py``").

The one thing this module *also* does is what makes the memory **visible**:
every real memory write emits a ``memory_written`` row into the existing
``office_event`` stream (through ``runner._event`` — the single office
emitter), so the shelf/glow animation the tower maps becomes reachable
*because a memory was really written* (C7: no event, no motion). There is no
second store, no second event table, no timer: the pointer to the durable text
IS the event payload.

Recall adapts ai-town's ranking idea (``convex/agent/memory.ts``) — a memory's
worth is **relevance + recency + importance**, not any one of them. The
relevance half is real: :func:`recall` asks the context engine's keyword search
for the seat's own text and folds those hits in; recency is an exponential
decay on the row's age; importance is the caller's own 1..10 score (the turn's
cheap proxy for ``calculateImportance``). A seat with no memories recalls
nothing and says so — an empty block, never an invented personality.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Optional

from . import store

#: How many memories a seat keeps in the hot set that recall ranks over.
MAX_MEMORIES = 500

#: Half-life of a memory's recency score, in days. A week-old memory is worth
#: about a fortieth of a fresh one — recent work dominates, without a hard cut.
RECENCY_HALF_LIFE_S = 3 * 86400.0

#: Weights of the composite score. Relevance leads (it is the only term that
#: depends on what is being asked), then importance, then recency.
W_RELEVANCE, W_IMPORTANCE, W_RECENCY = 0.5, 0.3, 0.2

#: The event kinds this module owns (``90-CONTRACT.md`` §2.4). ``memory_written``
#: is emitted by :func:`remember`; ``note_written`` by :func:`note`.
MEMORY_KIND_EVENT = "memory_written"
NOTE_KIND_EVENT = "note_written"

#: Which tool names mean "a skill was used" — the vendored skill tools.
SKILL_TOOLS = ("skill_view", "skill_manage", "skills_list")

#: The MCP wire-name prefix (``qubit_runtime/tools/mcp_tool_schema.py``:
#: ``mcp__<server>__<tool>``).
MCP_PREFIX = "mcp__"

_WORD_RE = re.compile(r"[A-Za-z0-9_]{3,}")


# --------------------------------------------------------------------- emit
def emit_event(cfg: Any, kind: str, *, agent_id: Optional[int],
               department_id: Optional[int] = None,
               payload: Optional[Dict[str, Any]] = None,
               visible: str = "badge") -> None:
    """Write one renderable row through the EXISTING office emitter.

    ``runner._event`` is the office's single event writer (``runner.py:59``);
    this module reuses it rather than duplicating the INSERT, so there is one
    place an ``office_event`` row is shaped. If the runner is unavailable the
    row is written here with an identical column set — never silently dropped,
    because a memory that was written but not shown is a motion the owner paid
    for and cannot see (C7).

    This is the emission seam the other honest call sites (a money posting, an
    escalation, an artifact) use, so A5 owns "what an office event is" in one
    place instead of five hand-copied INSERTs.
    """
    try:
        from . import runner as _runner
    except Exception:                                   # noqa: BLE001
        _runner = None
    if _runner is not None and callable(getattr(_runner, "_event", None)):
        _runner._event(cfg, kind, agent_id=agent_id,
                       department_id=department_id, payload=payload,
                       visible=visible)
        return
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        conn.execute(
            "INSERT INTO office_event(ts, kind, agent_id, department_id,"
            " payload, visible) VALUES(?,?,?,?,?,?)",
            (store.now(), kind, agent_id, department_id,
             json.dumps(payload or {}, sort_keys=True), visible))
        conn.commit()
    finally:
        conn.close()


#: Backwards-compatible private alias (kept for the in-repo call sites).
_emit = emit_event


# ------------------------------------------------------------------ helpers
def _clean(text: Any) -> str:
    return str(text or "").strip()


def _tokens(text: str) -> set:
    return {m.group(0).lower() for m in _WORD_RE.finditer(text or "")}


def _importance(value: Any) -> int:
    """A 1..10 importance. Absent or nonsense -> 5 (the neutral middle).

    Never raises and never invents a high score: a caller that did not judge
    importance does not get to have its memory promoted on a made-up number.
    """
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return 5
    return max(1, min(10, n))


def _ensure(cfg: Any) -> None:
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
    finally:
        conn.close()


def _rows_for(cfg: Any, agent_id: int, *, kind: str = "",
              limit: int = MAX_MEMORIES) -> List[Dict[str, Any]]:
    """The seat's own memory-event rows, newest first. Read-only."""
    _ensure(cfg)
    conn = store.connect(cfg)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, ts, kind, payload FROM office_event"
            " WHERE agent_id=? AND kind IN (?, ?) ORDER BY id DESC LIMIT ?",
            (int(agent_id), MEMORY_KIND_EVENT, NOTE_KIND_EVENT,
             max(1, min(int(limit), 2000))))]
    finally:
        conn.close()
    out = []
    for r in rows:
        try:
            payload = json.loads(r["payload"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if kind and str(payload.get("kind") or "") != kind:
            continue
        out.append({"id": int(r["id"]), "ts": float(r["ts"]),
                    "event": r["kind"], "payload": payload})
    return out


# ------------------------------------------------------------------ remember
def remember(cfg: Any, agent_id: int, *, kind: str, text: str,
             ref: str = "", meta: Optional[Dict[str, Any]] = None,
             importance: Any = None) -> Dict[str, Any]:
    """Write one durable memory for one seat, and emit the event it caused.

    Frozen signature (``90-AGENT-CONTRACT.md`` §2.2): A4 calls exactly this.
    The text is persisted through the context engine (scoped ``agent:<id>`` so
    two seats can never read each other's memories), and a ``memory_written``
    event carrying a JSON-safe pointer is appended to ``office_event`` — the
    same row the tower animates. Returns a JSON-safe dict; never raises on an
    empty or absent store.
    """
    aid = int(agent_id)
    body = _clean(text)
    mkind = _clean(kind) or "note"
    out: Dict[str, Any] = {
        "state": "OK", "agent_id": aid, "kind": mkind,
        "text": body, "ref": _clean(ref),
        "importance": _importance(importance), "stored": False,
        "event_id": None, "viking_uri": "",
    }
    if not body:
        out["state"] = "EMPTY"
        out["reason"] = "an empty memory is not written and emits no event (C7)"
        return out

    # 1 — persist the bytes through the ONE context engine (not a new store).
    uri = ""
    try:
        from .. import context_store as CS

        res = CS.add_memory(cfg, body[:20000], scope=f"agent:{aid}",
                            confirmed=True, requested_by="office:agent_memory")
        if not isinstance(res, dict) or res.get("state") != "OK":
            raise RuntimeError(f"context store did not accept memory: {res!r}")
        uri = str(res.get("viking_uri") or "")
        out["authority"] = str(res.get("authority") or "")
        out["stored"] = True
        out["viking_uri"] = uri
    except Exception as exc:                            # noqa: BLE001
        # A failed context-store write is not a memory and must not emit the
        # success event that recall and the tower consume.
        out["state"] = "FAILED"
        out["store_error"] = str(exc)[:200]
        return out  # No success animation or recallable memory for a failed write.

    payload = {
        "kind": mkind,
        "text": body[:1000],
        "ref": _clean(ref) or uri,
        "importance": out["importance"],
        "meta": dict(meta or {}),
        "persisted": bool(out["stored"]),
    }
    # 2 — the event IS the pointer (no pointer table, no schema change). A note
    # is its own kind of shelf event (`carrying`), a memory is `glow`; the
    # caller says which it wrote, and the row is emitted for exactly that.
    event = NOTE_KIND_EVENT if mkind == "note" else MEMORY_KIND_EVENT
    out["event"] = event
    try:
        _emit(cfg, event, agent_id=aid, payload=payload, visible="badge")
        conn = store.connect(cfg)
        try:
            store.migrate(conn)
            row = conn.execute(
                "SELECT MAX(id) m FROM office_event WHERE agent_id=?"
                " AND kind=?", (aid, event)).fetchone()
            out["event_id"] = int(row["m"]) if row and row["m"] is not None else None
        finally:
            conn.close()
    except Exception as exc:                            # noqa: BLE001
        out["state"] = "FAILED"
        out["error"] = str(exc)[:200]
    store.audit("memory.remember", "office:agent_memory",
                {"agent_id": aid, "kind": mkind, "chars": len(body),
                 "persisted": bool(out["stored"])})
    return out


def note(cfg: Any, agent_id: int, *, text: str, ref: str = "",
         importance: Any = None, meta: Optional[Dict[str, Any]] = None
         ) -> Dict[str, Any]:
    """Shelve a note for one seat — a ``note_written`` (carrying) event.

    A note is a memory of kind ``note``; it is written the same way and by the
    same authority, so a note and a memory can never drift into two stores.
    """
    out = remember(cfg, int(agent_id), kind="note", text=text, ref=ref,
                   importance=importance, meta=meta)
    return out


# -------------------------------------------------------------------- recall
def recall(cfg: Any, agent_id: int, *, kind: str = "",
           limit: int = 20, query: str = "") -> Dict[str, Any]:
    """The seat's most worth-recalling memories, ranked.

    Frozen signature (``90-AGENT-CONTRACT.md`` §2.2): A4 calls exactly this.
    Ranking is the ai-town composite — **relevance + recency + importance** —
    computed here, deterministically, from rows already in the office store and
    the context engine's own keyword search. A seat with no memories returns an
    empty list and ``known: False``; it never invents one.
    """
    aid = int(agent_id)
    lim = max(1, min(int(limit or 20), 200))
    rows = _rows_for(cfg, aid, kind=_clean(kind))
    now = store.now()

    # relevance input: what the seat is being asked to recall about. The caller
    # may pass `query`; otherwise the seat's own recent words are the query, so
    # a recurring memory outranks a one-off with the same importance.
    terms = _tokens(query)
    if not terms:
        for r in rows[:10]:
            terms |= _tokens(str(r["payload"].get("text") or ""))
    # The context engine's own keyword search confirms the terms are real (it
    # knows the file index), but its hits are PER-FILE, and every office memory
    # lands in one file — so a URI hit is a fact about the query, not about any
    # one memory. It therefore gates relevance, it does not score a row.
    ctx_matched_refs = set()
    if terms:
        try:
            from .. import context_store as CS

            res = CS.search(cfg, " ".join(sorted(terms))[:200], limit=50)
            ctx_matched_refs = {str(hit.get("viking_uri") or "")
                                for hit in res.get("hits", [])
                                if isinstance(hit, dict)}
        except Exception:                               # noqa: BLE001
            ctx_matched_refs = set()

    ranked: List[Dict[str, Any]] = []
    for r in rows:
        payload = r["payload"]
        text = str(payload.get("text") or "")
        ref = str(payload.get("ref") or "")
        age = max(0.0, now - float(r["ts"]))
        recency = math.pow(0.5, age / RECENCY_HALF_LIFE_S)
        imp = _importance(payload.get("importance")) / 10.0
        overlap = len(terms & _tokens(text))
        relevance = min(1.0, overlap / float(len(terms))) if terms else 0.0
        # a specific ref (an artifact/viking URI the caller named) that the
        # context engine also knows is direct evidence this exact memory is
        # what was asked for; a shared file URI is not.
        if (ref in ctx_matched_refs and ref.startswith("viking://")
                and ref != "viking://user/memories.md"):
            relevance = max(relevance, min(1.0, 0.5 + overlap / 4.0))
        score = (W_RELEVANCE * relevance + W_IMPORTANCE * imp
                 + W_RECENCY * recency)
        ranked.append({
            "memory_id": int(r["id"]), "ts": float(r["ts"]),
            "kind": str(payload.get("kind") or ""),
            "text": text, "ref": ref,
            "importance": _importance(payload.get("importance")),
            "meta": payload.get("meta") or {},
            "relevance": round(relevance, 4),
            "recency": round(recency, 4),
            "score": round(score, 6),
        })
    ranked.sort(key=lambda m: (m["score"], m["memory_id"]), reverse=True)
    top = ranked[:lim]
    return {
        "state": "OK",
        "agent_id": aid,
        "count": len(top),
        "memories": top,
        "known": bool(top),
        "ranked_by": "relevance + recency + importance (ai-town memory.ts)",
        "source": "office_event (memory_written/note_written) + context_store",
    }


# ---------------------------------------------------------------- personality
#: The dossier dimensions, adapted from starnet's ``dossier.js``. Each is a
#: *derived belief with evidence* — never an authored trait, so a fresh seat
#: has an empty dossier and says so rather than inventing a personality.
DOSSIER_DIMENSIONS = ("identity", "standing_orders", "work", "people",
                      "style", "ambition")


def _seat_row(cfg: Any, agent_id: int) -> Dict[str, Any]:
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        row = conn.execute("SELECT * FROM office_agent WHERE id=?",
                           (int(agent_id),)).fetchone()
        return dict(row) if row is not None else {}
    finally:
        conn.close()


def dossier(cfg: Any, agent_id: int) -> Dict[str, Any]:
    """The seat's personality, as *derived beliefs* — or ``known: False``.

    Every dimension is a projection of data the office already holds: the
    seat's own row, its supervisor, its recent memories, and the money it has
    booked. Nothing here is stored as a personality column and nothing is
    invented: a seat with no evidence gets an empty block and ``known: False``
    (L2's honesty rule, mirroring ``xp.js``'s CONFIDENCE "calibrating").
    """
    aid = int(agent_id)
    seat = _seat_row(cfg, aid)
    beliefs: List[Dict[str, Any]] = []
    if seat:
        beliefs.append({"dimension": "identity",
                        "belief": f"{seat.get('slug')} is a "
                                  f"{seat.get('seat_type')}",
                        "evidence": f"office_agent#{aid}"})
        if seat.get("supervisor_id") is not None:
            beliefs.append({"dimension": "standing_orders",
                            "belief": "reports to seat "
                                      f"#{seat['supervisor_id']}",
                            "evidence": "office_agent.supervisor_id"})
        beliefs.append({"dimension": "standing_orders",
                        "belief": f"autonomy posture: "
                                  f"{seat.get('autonomy')}",
                        "evidence": "office_agent.autonomy"})
    mem = recall(cfg, aid, limit=50)
    if mem["memories"]:
        kinds = sorted({m["kind"] for m in mem["memories"] if m["kind"]})
        beliefs.append({"dimension": "work",
                        "belief": f"{mem['count']} remembered action(s): "
                                  + ", ".join(kinds),
                        "evidence": "office_event memory_written/note_written"})
        top = mem["memories"][0]
        if top.get("text"):
            beliefs.append({"dimension": "style",
                            "belief": f"most recent, \"{top['text'][:80]}\"",
                            "evidence": f"office_event#{top['memory_id']}"})
    known = bool(seat) and bool(mem["memories"])
    return {
        "known": known,
        "agent_id": aid,
        "dimensions": sorted({b["dimension"] for b in beliefs}),
        "beliefs": beliefs,
        "memory_count": int(mem["count"]),
        # An empty dossier is a FACT the caller can render, not a missing value.
        "note": ("no evidence yet: no seat row and no memory"
                 if not known else ""),
    }


def dossiers(cfg: Any, agent_ids: Any = None) -> Dict[int, Dict[str, Any]]:
    """Every seat's dossier in one pass — the payload's per-character field.

    One read of the memory-event table and one of ``office_agent``, so
    ``tower.state()`` stays a cheap read as the org grows (no per-character
    query, no new store).
    """
    if agent_ids is None:
        conn = store.connect(cfg)
        try:
            store.migrate(conn)
            ids = [int(r["id"]) for r in conn.execute(
                "SELECT id FROM office_agent WHERE status != 'terminated'"
                " ORDER BY id")]
        finally:
            conn.close()
    else:
        ids = [int(a) for a in agent_ids]
    return {aid: dossier(cfg, aid) for aid in ids}


# ------------------------------------------------------------------ linkage
def classify_tool(name: Any) -> str:
    """The office event kind a tool name *is*, or "" when it is neither.

    The map is the honest one: an MCP call is the ``mcp__`` wire name, and a
    skill use is one of the vendored skill tools. Anything else (a plain file
    or shell tool) is not promoted into an animation it did not earn.
    """
    n = _clean(name)
    if not n:
        return ""
    if n.startswith(MCP_PREFIX):
        return "mcp_call"
    if n in SKILL_TOOLS:
        return "skill_used"
    return ""


def tool_events(cfg: Any, agent_id: int, *, tools: Any,
                meta: Optional[Dict[str, Any]] = None) -> List[str]:
    """Emit ``skill_used`` / ``mcp_call`` for the tools a turn really used.

    ``tools`` is the turn receipt's own tool list (``receipt["tools"]``). Only
    names that classify are emitted, one row each, so the workbench/comms walks
    fire exactly when the thing they draw actually happened (C7). Returns the
    kinds emitted.
    """
    aid = int(agent_id)
    emitted: List[str] = []
    seen = set()
    for name in (tools or []):
        kind = classify_tool(name)
        key = (kind, _clean(name))
        if not kind or key in seen:
            continue
        seen.add(key)
        _emit(cfg, kind, agent_id=aid,
              payload={"tool": _clean(name)[:120],
                       "meta": dict(meta or {})},
              visible="badge")
        emitted.append(kind)
    return emitted
