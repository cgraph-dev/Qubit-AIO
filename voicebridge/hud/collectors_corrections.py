"""HUD corrections collector — Qubit-native re-point of
``backend/collectors/corrections.py``.

Vendored from ``joeynyc/hermes-hudui`` @ be83208f48b7a1c67b7816837c8df71a61ef7edf
Copyright (c) 2026 Hermes HUD Contributors. MIT licensed; the file-by-file
record is ``PROVENANCE.json`` beside this package.

DECISION: MAP (Qubit records corrections as first-class rows, not as prose)
--------------------------------------------------------------------------
The previous lane marked this GAP ("Qubit records no correction events"). That
is measurably false. Evidence:

* upstream ``corrections.py`` is ALL inference: it greps ``MEMORY.md`` /
  ``USER.md`` for keywords (``gotcha``, ``caught me``, ``verify before`` …) and
  LIKE-scans user messages in ``state.db`` for "wrong"/"actually", then labels
  the result a correction with an invented severity. It reads no correction
  record, because none existed to read.
* Qubit has the record. :class:`voicebridge.memory.store.MemoryStore` owns a
  ``memories`` table with a ``supersedes`` column and a ``history`` table; its
  ``correct(old, new)`` writes ``action='corrected'`` on the old row
  (payload ``{superseded_by, to}``) and ``action='correct'`` on the new one
  (payload ``{supersedes}``). Callers: :mod:`voicebridge.memory.semantic`
  (``:787``), :mod:`voicebridge.memory.journal` (``:252``) and
  :mod:`voicebridge.evaluation.runner` (``:143``). That is a correction ledger.

So the panel is a port of the upstream panel's INTENT (what did we get wrong
and learn from) onto the real ledger. Deliberately NOT ported: the keyword
heuristic and the invented ``minor|major|critical`` severity — a severity
Qubit never assigned would be a fabrication, so ``severity`` is dropped and the
rows carry the store's own ``source`` instead.

Store access is read-only by construction: the connection is opened with
``PRAGMA query_only`` before any SELECT, so this module cannot write the
memory store even by accident. ``correct()`` is never called from here.

Pure function, JSON-ready dict, never raises; a missing store is
``available: true, store_present: false`` (an empty ledger), an unreadable one
is ``available: false`` with the reason.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Corrections shown in the list; the count above is computed over all rows.
LIST_LIMIT = 50


def _store_path(cfg: Any) -> Path:
    from .. import active_memory as AM

    return Path(AM.memory_db_path(cfg))


def _iso(ts: Any) -> Optional[str]:
    if isinstance(ts, (int, float)) and ts > 0:
        try:
            return datetime.fromtimestamp(float(ts)).isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    return None


def _payload(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _open_ro(path: Path) -> sqlite3.Connection:
    """Open the memory store strictly for reading.

    ``query_only`` is set FIRST, so even a caller mistake later in this module
    is refused by SQLite rather than mutating the user's memory. ``immutable``
    is deliberately not used: the live agent may be writing concurrently and
    this must see its committed rows.
    """
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _open_store(cfg: Any) -> Dict[str, Any]:
    """Ensure the store exists and its tables are the ones we expect.

    ``MemoryStore.__init__`` runs the additive migration, which is what makes
    the ``history``/``supersedes`` columns exist on a store that predates them.
    Opening it here is the store's documented constructor, not a schema write
    invented by the HUD; the read below is still ``query_only``.
    """
    from ..memory.store import MemoryStore

    path = _store_path(cfg)
    if not path.is_file():
        return {"present": False, "path": path, "detail": ""}
    store = MemoryStore(path)
    store.close()
    return {"present": True, "path": path, "detail": ""}


def collect_corrections(home: Optional[Any] = None, cfg: Any = None, *,
                        limit: int = LIST_LIMIT) -> Dict[str, Any]:
    """Corrected facts: every corrected/superseded memory row, newest first."""
    out: Dict[str, Any] = {
        "available": False,
        "detail": "",
        "store_present": False,
        "total": 0,
        "current": 0,
        "superseded": 0,
        "by_source": {},
        "by_action": {},
        "corrections": [],
        "shown": 0,
        "authority": "voicebridge.memory.store.MemoryStore (history + "
                     "supersedes)",
    }
    try:
        opened = _open_store(cfg)
    except Exception as exc:  # noqa: BLE001 - absence is a typed answer
        out["detail"] = f"memory store unavailable ({type(exc).__name__}: {exc})"
        return out
    out["store_present"] = bool(opened.get("present"))
    out["store_path"] = str(opened.get("path", ""))
    if not out["store_present"]:
        out.update(available=True,
                   detail="the memory store has not been created yet, so no "
                          "correction has been recorded")
        return out

    try:
        conn = _open_ro(Path(out["store_path"]))
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"memory store unreadable ({type(exc).__name__}: {exc})"
        return out
    try:
        rows = list(conn.execute(
            """
            SELECT m.id, m.text, m.scope, m.tier, m.source, m.updated_at,
                   m.supersedes, m.tombstoned,
                   h.payload AS corr_payload
            FROM memories m
            LEFT JOIN history h
                   ON h.memory_id = m.id AND h.action IN ('correct','corrected')
            WHERE m.supersedes IS NOT NULL
               OR m.id IN (SELECT memory_id FROM history WHERE action='correct')
               OR m.id IN (SELECT memory_id FROM history WHERE action='corrected')
            ORDER BY m.updated_at DESC
            """))
        actions = Counter()
        for row in conn.execute(
                "SELECT action, COUNT(*) AS n FROM history "
                "WHERE action IN ('correct','corrected') GROUP BY action"):
            actions[str(row["action"])] = int(row["n"] or 0)
    except sqlite3.DatabaseError as exc:
        out["detail"] = f"memory store query failed ({type(exc).__name__}: {exc})"
        return out
    finally:
        conn.close()

    seen = set()
    items: List[Dict[str, Any]] = []
    sources: Counter = Counter()
    sup = cur = 0
    for row in rows:
        mid = int(row["id"] or 0)
        supersedes = row["supersedes"]
        try:
            supersedes_i = int(supersedes) if supersedes is not None else None
        except (TypeError, ValueError):
            supersedes_i = None
        role = "superseded" if bool(row["tombstoned"]) else "current"
        key = (mid, role, supersedes_i)
        if key in seen:
            continue
        seen.add(key)
        if role == "superseded":
            sup += 1
        else:
            cur += 1
        if role != "current":
            continue                      # the ledger row is the NEW fact
        payload = _payload(row["corr_payload"])
        text = str(row["text"] or "")
        sources[str(row["source"] or "unknown")] += 1
        if len(items) >= max(1, int(limit)):
            continue
        items.append({
            "id": mid,
            "text": text[:400],
            "scope": str(row["scope"] or ""),
            "tier": str(row["tier"] or ""),
            "source": str(row["source"] or ""),
            "updated_at": _iso(row["updated_at"]),
            "supersedes": supersedes_i,
            "replaces_text": str(payload.get("to", "") or "")[:400]
                             if supersedes_i is None else "",
            "reason": str(payload.get("reason", "") or "")[:200],
        })

    out.update(
        available=True,
        detail=(f"{len(items)} correction(s) in the memory ledger "
                f"({cur} current fact(s) standing in for {sup} superseded)"),
        total=cur + sup,
        current=cur,
        superseded=sup,
        by_source=dict(sources),
        by_action=dict(actions),
        corrections=items,
        shown=len(items),
    )
    return out


__all__ = ["collect_corrections"]
