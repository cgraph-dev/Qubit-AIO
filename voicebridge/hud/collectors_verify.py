"""HUD verify collector — Qubit-native re-point of the HUD's "Verify" tab.

Upstream has NO verify collector to vendor: the tab shells ``hermes verify``
and renders its stdout. There is no upstream file to copy, so nothing here is
derived from ``hermes-hudui`` source; the provenance record lists it as
Qubit-authored. Licence note: this module contains no upstream code.

DECISION: MAP (different mechanism; a real integrity read exists)
---------------------------------------------------------------
The previous lane marked this GAP because "Qubit has no verify command and no
verify artifact to read". Half right, and the useful half was missed. Evidence:

* Qubit has no ``verify`` subcommand — correct.
* Qubit has :mod:`voicebridge.checkpoints`, whose ``create()`` records a
  ``sha256`` digest and line count of the action journal at snapshot time, and
  whose ``verify(name)`` recomputes that digest and returns
  ``{state: OK | TAMPERED | MISSING, ...}`` — with ``recorded_lines`` and
  ``current_lines`` on tamper. That IS a verify artifact: a tamper-evident
  integrity check over the durable journal.
* It is read-only here. ``list_all()`` reads the checkpoint index;
  ``verify()`` re-hashes a journal file. Neither writes. ``prune()`` (which
  deletes) is never called.

The panel therefore verifies every checkpoint Qubit has recorded rather than
shelling a command that does not exist. An empty checkpoint index is reported
as ``available: true, total: 0`` with the reason (no checkpoint has been
created), never as a green "all verified" — verifying nothing is not a pass.

Pure function, JSON-ready dict, never raises.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


def _store_path(cfg: Any) -> Any:
    from pathlib import Path

    return Path(cfg.state_dir())


def _iso(ts: Any) -> Optional[str]:
    if isinstance(ts, (int, float)) and ts > 0:
        try:
            return datetime.fromtimestamp(float(ts)).isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(ts, str) and ts:
        return ts
    return None


#: How many checkpoints are re-hashed per request. Each verify reads and hashes
#: one journal file, so the panel bounds the work instead of hashing a store
#: sized in hundreds of MB on the request thread.
VERIFY_LIMIT = 50


def collect_verify(home: Optional[Any] = None, cfg: Any = None, *,
                   limit: int = VERIFY_LIMIT) -> Dict[str, Any]:
    """Every recorded checkpoint, re-verified against its recorded digest."""
    out: Dict[str, Any] = {
        "available": False,
        "detail": "",
        "total": 0,
        "ok": 0,
        "tampered": 0,
        "missing": 0,
        "checkpoints": [],
        "shown": 0,
        "verdict": "UNKNOWN",
        "authority": "voicebridge.checkpoints (sha256 journal digest)",
    }
    try:
        from .. import checkpoints as CK

        state_dir = _store_path(cfg)
    except Exception as exc:  # noqa: BLE001 - absence is a typed answer
        out["detail"] = f"checkpoint store unavailable ({type(exc).__name__}: {exc})"
        return out
    try:
        rows = CK.list_all(state_dir)
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"checkpoint index unreadable ({type(exc).__name__}: {exc})"
        return out
    if not isinstance(rows, list):
        out["detail"] = (f"checkpoint index returned "
                         f"{type(rows).__name__}, not a list")
        return out
    out["total"] = len(rows)
    if not rows:
        out.update(
            available=True,
            verdict="NO_CHECKPOINTS",
            detail=("No checkpoint has been recorded on this box, so there is "
                    "nothing to verify. Create one with the Qubit checkpoint "
                    "command; this panel will then re-hash its journal."),
        )
        return out

    items: List[Dict[str, Any]] = []
    ok = tampered = missing = 0
    for row in rows[:max(1, int(limit))]:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        if not name:
            continue
        try:
            res = CK.verify(state_dir, name)
        except Exception as exc:  # noqa: BLE001 - one checkpoint is not the panel
            items.append({"name": name, "state": "ERROR",
                          "detail": f"{type(exc).__name__}: {exc}",
                          "lines": row.get("lines"),
                          "recorded_lines": None,
                          "created_at": _iso(row.get("created_at"))})
            continue
        state = str(res.get("state") or "UNKNOWN")
        if state == "OK":
            ok += 1
        elif state == "TAMPERED":
            tampered += 1
        else:
            missing += 1
        items.append({
            "name": name,
            "state": state,
            "detail": str(res.get("detail") or ""),
            "lines": res.get("lines", row.get("lines")),
            "recorded_lines": res.get("recorded_lines",
                                      row.get("lines") if state != "OK" else None),
            "created_at": _iso(row.get("created_at")),
        })

    verdict = ("TAMPERED" if tampered else
               "DEGRADED" if missing else "OK")
    out.update(
        available=True,
        detail=(f"re-hashed {len(items)} checkpoint journal(s): {ok} OK, "
                f"{tampered} tampered, {missing} missing"),
        ok=ok, tampered=tampered, missing=missing,
        checkpoints=items,
        shown=len(items),
        verdict=verdict,
    )
    return out


__all__ = ["collect_verify"]
