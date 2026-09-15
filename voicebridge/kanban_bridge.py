"""Compatibility layer between Qubit's two Kanban stores.

Qubit ships a Qubit-native Kanban store (``voicebridge.kanban`` at
``<state>/kanban.db``, board ``main``) used by the CLI, voice, the operations
catalog and the ``/qubit-boards`` dashboard tab. The vendored Hermes kanban
dashboard plugin (``qubit_hermes/plugins/kanban``) uses the fork's own
``hermes_cli.kanban_db`` store (``<HERMES_HOME>/kanban.db``, board ``default``).

The two use different schemas, so instead of a risky cross-schema rewrite this
module is the single, explicit compatibility boundary required by the owner:

* :func:`stores` reports BOTH stores truthfully (path, default board, task
  count, authoritative flag), so no surface silently reads a different store;
  the vendored kanban responses embed this marker;
* :func:`migrate` idempotently imports vendored tasks into the authoritative
  Qubit-native store so an operator can converge the data onto one board.

The Qubit-native store is authoritative: it is what CLI, voice, the operations
catalog, task ledger and ``/qubit-boards`` already read and write.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from . import kanban as N

#: Vendored kanban-board status -> Qubit-native status.
_STATUS_MAP = {
    "triage": "open",
    "todo": "open",
    "scheduled": "open",
    "ready": "open",
    "running": "claimed",
    "blocked": "blocked",
    "review": "claimed",
    "done": "done",
    "archived": "archived",
}

_STATE_NAME = "kanban-migration.json"

#: The single writable board authority contract. Qubit-native ``kanban.py`` is
#: the only surface that mutates board state; the vendored Hermes kanban store
#: is a read-only legacy source whose data converges onto the native store only
#: through :func:`migrate`. The dashboard plugin's vendored write routes are
#: typed-refused with :data:`VENDORED_WRITE_CODE`; :func:`vendored_write` is the
#: in-process equivalent for any caller that reaches the store directly.
BOARD_AUTHORITY = {
    "schema": "qubit-store-authority/1",
    "domain": "boards",
    "authoritative_store": "qubit-native",
    "writable": ["qubit-native"],
    "read_only": ["hermes-vendored"],
    "vendored_write_code": "KANBAN_WRITE_BROKERED",
    "convergence": "kanban_bridge.migrate",
}
VENDORED_WRITE_CODE = BOARD_AUTHORITY["vendored_write_code"]
_VENDORED_COUNTERPART = "CLI `qubit kanban` + dashboard /qubit-boards"


def _priority(raw: str) -> str:
    value = str(raw or "normal").strip().lower()
    return value if value in N.PRIORITIES else "normal"


def _vendored(cfg: Any):
    from . import hermes_plugins as HP

    engine = HP._kanban_engine(cfg)
    return engine, engine.kanban_db


def native_store(cfg: Any) -> Dict[str, Any]:
    return {
        "store": "qubit-native",
        "authoritative": True,
        "writable": True,
        "db_path": str(N._db_path(cfg)),
        "default_board": "main",
        "statuses": list(N.STATUSES),
    }


def vendored_store(cfg: Any) -> Dict[str, Any]:
    engine, kd = _vendored(cfg)
    return {
        "store": "hermes-vendored",
        "authoritative": False,
        "writable": False,
        "read_only": True,
        "db_path": str(kd.kanban_db_path(None)),
        "default_board": kd.DEFAULT_BOARD,
        "statuses": list(engine.BOARD_COLUMNS),
    }


def vendored_write(operation: str, *, board: str = "",
                   **fields: Any) -> Dict[str, Any]:
    """Refuse any write routed at the vendored board store.

    The vendored store is read-only in Qubit: every mutation must target the
    native store (``voicebridge.kanban``) or the same operations broker the
    dashboard uses. Returns a typed refusal that names the single writable
    authority so no caller can silently diverge the two stores.
    """
    return {
        "ok": False,
        "state": "REFUSED",
        "code": VENDORED_WRITE_CODE,
        "operation": str(operation),
        "board": str(board or ""),
        "authoritative_store": "qubit-native",
        "writable_store": "qubit-native",
        "qubit_counterpart": _VENDORED_COUNTERPART,
        "detail": ("the upstream vendored kanban store is read-only in Qubit; "
                   "mutate boards through the native store "
                   "(voicebridge.kanban) or kanban_bridge.migrate"),
    }


def _count(path: Path) -> int:
    import sqlite3

    if not path.exists():
        return 0
    try:
        conn = sqlite3.connect(str(path))
        try:
            return int(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return 0


def stores(cfg: Any) -> Dict[str, Any]:
    """Truthful status of both Kanban stores and the authoritative choice."""
    native = native_store(cfg)
    try:
        ven = vendored_store(cfg)
    except Exception as exc:  # pragma: no cover - degraded fork only
        ven = {"store": "hermes-vendored", "authoritative": False,
               "unavailable": True,
               "detail": f"{type(exc).__name__}: {exc}"[:200]}
    native["task_count"] = _count(Path(native["db_path"]))
    if not ven.get("unavailable"):
        ven["task_count"] = _count(Path(ven["db_path"]))
    return {
        "state": "OK",
        "authoritative": "qubit-native",
        "writable": "qubit-native",
        "authority": dict(BOARD_AUTHORITY),
        "native": native,
        "vendored": ven,
        "migration": "qubit kanban migrate",
        "note": ("Qubit-native is authoritative; the legacy kanban store has "
                 "its own schema and is converged by an explicit migration, "
                 "never silently."),
    }


def _state_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / _STATE_NAME


def _load_imported(cfg: Any) -> set:
    p = _state_path(cfg)
    try:
        return set(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_imported(cfg: Any, ids: set) -> None:
    p = _state_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sorted(ids)), encoding="utf-8")


def migrate(cfg: Any, *, dry_run: bool = False) -> Dict[str, Any]:
    """Idempotently import vendored Kanban tasks into the native store.

    Deduplication uses a durable set of already-imported vendored task ids.
    ``dry_run`` reports the plan without writing. Returns a receipt with the
    imported/skipped counts and the target board.
    """
    engine, _kd = _vendored(cfg)
    conn = engine._conn(board=None)
    try:
        rows = conn.execute(
            "SELECT id, title, body, status, priority, assignee "
            "FROM tasks ORDER BY id").fetchall()
    finally:
        conn.close()

    done = _load_imported(cfg)
    imported: List[Any] = []
    skipped: List[Any] = []
    failed: List[Dict[str, Any]] = []
    for r in rows:
        vid = str(r["id"])
        if vid in done:
            skipped.append(vid)
            continue
        title = str(r["title"] or "").strip()
        if not title:
            skipped.append(vid)
            continue
        status = _STATUS_MAP.get(str(r["status"] or ""), "open")
        priority = _priority(str(r["priority"] or "normal"))
        if dry_run:
            imported.append(vid)
            continue
        try:
            out = N.create(cfg, title, board="main",
                           body=str(r["body"] or ""),
                           assignee=str(r["assignee"] or ""),
                           priority=priority,
                           requested_by="kanban:migrate", confirmed=True)
            task = out.get("task", {})
            tid = task.get("id")
            if status != "open" and tid is not None:
                try:
                    N.move_task(cfg, tid, status=status,
                                requested_by="kanban:migrate", confirmed=True)
                except Exception:
                    pass
            imported.append(vid)
            done.add(vid)
        except Exception as exc:  # keep migrating the rest
            failed.append({"vendor_id": vid,
                           "detail": f"{type(exc).__name__}: {exc}"[:160]})
    if not dry_run:
        _save_imported(cfg, done)
    return {
        "state": "OK",
        "dry_run": bool(dry_run),
        "imported": len(imported),
        "skipped": len(skipped),
        "failed": failed,
        "target_board": "main",
        "receipt": {"migrated_ids": imported, "skipped_ids": skipped},
    }
