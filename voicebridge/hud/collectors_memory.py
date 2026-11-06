"""HUD memory collector — Qubit-native re-point of ``backend/collectors/memory.py``.

Vendored from ``joeynyc/hermes-hudui`` @ be83208f48b7a1c67b7816837c8df71a61ef7edf
Copyright (c) 2026 Hermes HUD Contributors. MIT licensed; the file-by-file
record is ``PROVENANCE.json`` beside this package.

DECISION: MAP (Qubit's memory is a store, not two markdown files)
----------------------------------------------------------------
The previous lane marked this GAP: "Qubit's memory providers are plugin-owned
and expose no single read authority." Evidence says otherwise:

* :mod:`voicebridge.active_memory` is a single module that owns the policy, the
  store path and a ``status(cfg)`` returning ``{state, enabled, min_score,
  max_items, session_scoped, store_state, store_path, memories, estop}``.
  ``GET /api/memory`` serves exactly that.
* :class:`voicebridge.memory.store.MemoryStore` owns the rows: tiers
  (core/working/recall/archive), ``scope``, ``kind``, ``confidence``,
  ``sensitivity``, ``tombstoned`` and ``staged``.

Upstream ``collect_memory`` parsed ``MEMORY.md`` / ``USER.md``: it split on
``§``, then ran regex tables (``CORRECTION_PATTERNS``, ``ENVIRONMENT_PATTERNS``,
``TODO_PATTERNS``, ...) to CATEGORISE each line, and compared the character
count against a system-prompt budget (2200 / 1375). None of that is ported:

* the markdown files and the §-splitting do not exist in this product;
* the regex categorisation invented a category Qubit never assigned — the store
  carries a real ``kind`` and ``tier`` instead, which are shown as they are;
* the char budget is an upstream prompt-injection limit, not a Qubit limit, so
  reporting "1800/2200 chars" here would be a claim about a budget this product
  does not enforce. The panel shows counts and per-tier/scope breakdowns.

Store access is ``PRAGMA query_only`` before any SELECT (see
``collectors_corrections._open_ro``): this module cannot write the store.
``remember``/``correct``/``forget`` are never called.

Pure function, JSON-ready dict, never raises.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .collectors_corrections import _open_ro, _store_path

#: Rows listed in the panel. The counts above are over the whole store.
LIST_LIMIT = 100


def _iso(ts: Any) -> Optional[str]:
    if isinstance(ts, (int, float)) and ts > 0:
        try:
            return datetime.fromtimestamp(float(ts)).isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    return None


def _status(cfg: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"available": False, "detail": "", "status": {}}
    try:
        from .. import active_memory as AM

        st = AM.status(cfg)
    except Exception as exc:  # noqa: BLE001 - absence is a typed answer
        out["detail"] = f"active_memory unavailable ({type(exc).__name__}: {exc})"
        return out
    out.update(available=True, status=st if isinstance(st, dict) else {},
               detail="voicebridge.active_memory.status")
    return out


def _rows(cfg: Any, limit: int) -> Dict[str, Any]:
    """Live (non-tombstoned, non-staged) rows plus the staged queue."""
    out: Dict[str, Any] = {
        "available": False, "detail": "", "store_present": False, "rows": [],
        "staged": [], "total": 0, "by_tier": {}, "by_scope": {}, "by_kind": {},
        "staged_count": 0, "tombstoned": 0, "shown": 0,
    }
    try:
        path = Path(_store_path(cfg))
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"{type(exc).__name__}: {exc}"
        return out
    out["store_present"] = path.is_file()
    out["store_path"] = str(path)
    if not out["store_present"]:
        out.update(available=True,
                   detail="the memory store has not been created yet")
        return out
    try:
        conn = _open_ro(path)
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"memory store unreadable ({type(exc).__name__}: {exc})"
        return out
    try:
        tiers: Counter = Counter()
        scopes: Counter = Counter()
        kinds: Counter = Counter()
        total = staged_count = tombstoned = 0
        for row in conn.execute(
                "SELECT tier, scope, kind, COUNT(*) AS n FROM memories "
                "GROUP BY tier, scope, kind"):
            n = int(row["n"] or 0)
            tiers[str(row["tier"] or "")] += n
            scopes[str(row["scope"] or "")] += n
            kinds[str(row["kind"] or "")] += n
            total += n
        for row in conn.execute(
                "SELECT staged, tombstoned, COUNT(*) AS n FROM memories "
                "GROUP BY staged, tombstoned"):
            n = int(row["n"] or 0)
            if bool(row["staged"]):
                staged_count += n
            elif bool(row["tombstoned"]):
                tombstoned += n
        rows = conn.execute(
            """
            SELECT id, tier, scope, kind, text, confidence, sensitivity,
                   source, created_at, updated_at, expires_at, supersedes
            FROM memories
            WHERE tombstoned = 0 AND staged = 0
            ORDER BY updated_at DESC
            LIMIT ?
            """, (max(1, int(limit)),)).fetchall()
        staged_rows = conn.execute(
            "SELECT id, tier, scope, kind, text, confidence FROM memories "
            "WHERE staged = 1 AND tombstoned = 0 ORDER BY created_at DESC "
            "LIMIT ?", (max(1, int(limit)),)).fetchall()
    except sqlite3.DatabaseError as exc:
        out["detail"] = f"memory store query failed ({type(exc).__name__}: {exc})"
        return out
    finally:
        conn.close()

    active = total - staged_count - tombstoned
    out.update(
        available=True,
        detail=(f"{active} active memor{'y' if active == 1 else 'ies'}"
                f" in {len(tiers)} tier(s)" if active else
                "the memory store is readable and holds no active memory yet"),
        total=active,
        all_rows=total,
        by_tier=dict(tiers),
        by_scope=dict(scopes),
        by_kind=dict(kinds),
        staged_count=staged_count,
        tombstoned=tombstoned,
        shown=len(rows),
    )
    items: List[Dict[str, Any]] = []
    for row in rows:
        items.append({
            "id": int(row["id"] or 0),
            "tier": str(row["tier"] or ""),
            "scope": str(row["scope"] or ""),
            "kind": str(row["kind"] or ""),
            "text": str(row["text"] or "")[:400],
            "confidence": (float(row["confidence"])
                           if row["confidence"] is not None else None),
            "sensitivity": str(row["sensitivity"] or ""),
            "source": str(row["source"] or ""),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
            "supersedes": row["supersedes"],
        })
    out["rows"] = items
    out["staged"] = [{
        "id": int(r["id"] or 0),
        "tier": str(r["tier"] or ""),
        "scope": str(r["scope"] or ""),
        "text": str(r["text"] or "")[:300],
        "confidence": (float(r["confidence"])
                       if r["confidence"] is not None else None),
    } for r in staged_rows]
    return out


def collect_memory(home: Optional[Any] = None, cfg: Any = None, *,
                   limit: int = LIST_LIMIT) -> Dict[str, Any]:
    """Active-memory policy/status plus the live store's rows and breakdowns."""
    status = _status(cfg)
    store = _rows(cfg, limit)
    return {
        "available": bool(status.get("available") or store.get("available")),
        "detail": store.get("detail") or status.get("detail") or "",
        "status": status.get("status", {}),
        "store": store,
        "not_ported": {
            "markdown_files": "Qubit's memory is a SQLite store "
                              "(voicebridge.memory.store), not MEMORY.md/USER.md",
            "char_budget": "the 2200/1375 character budgets were an upstream "
                           "prompt-injection limit; Qubit enforces none, so "
                           "none is reported",
            "regex_categories": "rows carry a real tier/kind; no keyword "
                                "categorisation is invented here",
        },
        "authority": "voicebridge.active_memory + voicebridge.memory.store",
    }


__all__ = ["collect_memory"]
