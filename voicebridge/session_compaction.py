"""Canonical Qubit session compaction / checkpoint / lineage service.

This is the ONE place the OpenClaw-dialect sessions methods reach Qubit's
compaction and lineage behavior. It is a strict layer over the canonical
:class:`~voicebridge.qubit_session_store.QubitSessionStore` (the vendored
``SessionDB`` in ``state.db``); it never opens a second store and never
fabricates a summary.

Real primitives used:

* :meth:`SessionDB.get_compression_lineage` / ``_session_lineage_root_to_tip``
  — the real compression-continuation lineage;
* :meth:`SessionDB.archive_and_compact` — soft-archive the active rows and
  insert the compacted set atomically (originals stay on disk, recoverable);
* :meth:`SessionDB.replace_messages` with ``archive_dropped=True`` — restore a
  transcript while soft-archiving the rows it replaces;
* :class:`agent.context_compressor.ContextCompressor` — the vendored
  model-backed summariser (its own deterministic fallback when the auxiliary
  model is unavailable). Qubit does not invent a summary.

Checkpoints are a Qubit-owned registry in the SAME ``state.db``: each records
the session id and the active-message watermark at compaction time, so a
checkpoint's transcript is reconstructed from the real (soft-archived) rows —
never a duplicate copy.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

CHECKPOINT_TABLE = "qubit_compaction_checkpoints"
DEFAULT_COMPACT_MODEL = "gpt-4o-mini"

#: typed outcomes
COMPACTION_MODEL_REQUIRED = "COMPACTION_MODEL_REQUIRED"
CHECKPOINT_NOT_FOUND = "COMPACTION_CHECKPOINT_NOT_FOUND"
CHECKPOINT_EMPTY = "COMPACTION_CHECKPOINT_EMPTY"
SESSION_NOT_FOUND = "SESSION_NOT_FOUND"

_MIN_MESSAGES = 6


def _db(store: Any) -> Any:
    return store._db


def _ensure_table(store: Any) -> None:
    def _do(conn: Any) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS qubit_compaction_checkpoints ("
            "id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
            "created_at REAL NOT NULL DEFAULT 0, "
            "watermark INTEGER NOT NULL DEFAULT 0, "
            "message_count INTEGER NOT NULL DEFAULT 0, "
            "data TEXT NOT NULL DEFAULT '{}')")
    _db(store)._execute_write(_do)


def _model_config(session: Dict[str, Any]) -> Dict[str, Any]:
    raw = session.get("model_config")
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def lineage(store: Any, session_id: str) -> List[str]:
    """Compression lineage root..tip for *session_id* (real vendored walk)."""
    db = _db(store)
    try:
        chain = list(db.get_compression_lineage(session_id) or [])
    except Exception:
        chain = []
    if not chain:
        try:
            chain = list(db._session_lineage_root_to_tip(session_id) or [])
        except Exception:
            chain = []
    return chain or [session_id]


def _row_to_checkpoint(row: Any) -> Dict[str, Any]:
    try:
        data = json.loads(row[5] or "{}")
    except (TypeError, ValueError):
        data = {}
    return {"id": row[0], "sessionId": row[1], "createdAt": float(row[2] or 0),
            "watermark": int(row[3] or 0), "messageCount": int(row[4] or 0),
            **(data if isinstance(data, dict) else {})}


def record_checkpoint(store: Any, session_id: str, *, watermark: int,
                      message_count: int) -> Dict[str, Any]:
    _ensure_table(store)
    cid = f"ckpt-{uuid.uuid4().hex[:12]}"
    now = time.time()
    data = {"lineage": lineage(store, session_id)}

    def _do(conn: Any) -> None:
        conn.execute(
            "INSERT INTO qubit_compaction_checkpoints "
            "(id, session_id, created_at, watermark, message_count, data) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cid, session_id, now, int(watermark), int(message_count),
             json.dumps(data)))
    _db(store)._execute_write(_do)
    return {"id": cid, "sessionId": session_id, "createdAt": now,
            "watermark": int(watermark), "messageCount": int(message_count),
            **data}


def list_checkpoints(store: Any, session_id: str) -> List[Dict[str, Any]]:
    _ensure_table(store)
    ids = lineage(store, session_id)
    placeholders = ",".join("?" for _ in ids)
    rows = _db(store)._read_all(
        "SELECT id, session_id, created_at, watermark, message_count, data "
        f"FROM qubit_compaction_checkpoints WHERE session_id IN ({placeholders}) "
        "ORDER BY created_at ASC", ids)
    return [_row_to_checkpoint(r) for r in rows]


def find_checkpoint(store: Any, session_id: str,
                    checkpoint_id: str) -> Optional[Dict[str, Any]]:
    _ensure_table(store)
    row = _db(store)._read_one(
        "SELECT id, session_id, created_at, watermark, message_count, data "
        "FROM qubit_compaction_checkpoints WHERE id = ?", (checkpoint_id,))
    if row is None:
        return None
    ckpt = _row_to_checkpoint(row)
    if ckpt["sessionId"] not in lineage(store, session_id):
        return None
    return ckpt


def checkpoint_messages(store: Any, checkpoint: Dict[str, Any]
                        ) -> List[Dict[str, Any]]:
    """The real rows (active or soft-archived) up to the checkpoint watermark."""
    watermark = int(checkpoint.get("watermark") or 0)
    # ``include_inactive`` returns every row (active, rewind, compaction) in id
    # order with NO display de-duplication, so the checkpoint reconstructs the
    # exact pre-compaction transcript up to its watermark.
    rows = _db(store).get_messages(checkpoint["sessionId"],
                                   include_inactive=True)
    return [m for m in rows if int(m.get("id") or 0) <= watermark]


def _vendored_summarize(store: Any, session: Dict[str, Any],
                        messages: List[Dict[str, Any]],
                        focus_topic: Optional[str]
                        ) -> Optional[List[Dict[str, Any]]]:
    """Run the real vendored compressor; ``None`` when it cannot run."""
    try:
        from agent.context_compressor import ContextCompressor  # type: ignore
    except Exception:
        return None
    model = str(session.get("model") or "").strip() or DEFAULT_COMPACT_MODEL
    try:
        compressor = ContextCompressor(model=model, quiet_mode=True)
        out = compressor.compress(messages, focus_topic=focus_topic, force=True)
    except Exception:
        return None
    if not isinstance(out, list) or not out:
        return None
    return out


def compact(store: Any, session_id: str, *, max_lines: Optional[int] = None,
            focus_topic: Optional[str] = None
            ) -> Dict[str, Any]:
    """Real transcript compaction (manual trim or vendored model summariser)."""
    db = _db(store)
    rec = db.get_session(session_id)
    if rec is None:
        return {"state": "MISSING", "code": SESSION_NOT_FOUND,
                "detail": f"no chat session {session_id!r}"}
    active = db.get_messages(session_id)
    n = len(active)
    if n < _MIN_MESSAGES:
        return {"state": "OK", "compacted": False,
                "reason": "insufficient_messages", "message_count": n}

    watermark = int(db.get_active_message_watermark(session_id))
    checkpoint = record_checkpoint(store, session_id, watermark=watermark,
                                   message_count=n)

    if max_lines is not None:
        keep = max(1, min(int(max_lines), n))
        if keep >= n:
            return {"state": "OK", "compacted": False, "reason": "already_small",
                    "message_count": n, "checkpoint": checkpoint}
        compressed = active[-keep:]
        reason = "manual_trim"
    else:
        compressed = _vendored_summarize(store, rec, active, focus_topic)
        if compressed is None:
            return {"state": "UNAVAILABLE", "code": COMPACTION_MODEL_REQUIRED,
                    "detail": "the vendored context compressor is unavailable "
                              "(no importable summariser)",
                    "checkpoint": checkpoint}
        if len(compressed) >= n:
            return {"state": "OK", "compacted": False,
                    "reason": "no_compressible_window", "message_count": n,
                    "checkpoint": checkpoint}
        reason = "model"

    after = int(db.archive_and_compact(
        session_id, compressed, tail_count=max(0, len(compressed) - 1)))
    return {"state": "OK", "compacted": True, "reason": reason,
            "before": n, "after": after, "archived": n,
            "focus_topic": focus_topic or None, "checkpoint": checkpoint,
            "authority": "voicebridge.session_compaction "
                         "(SessionDB.archive_and_compact + vendored "
                         "ContextCompressor)"}


def branch(store: Any, session_id: str, checkpoint_id: str) -> Dict[str, Any]:
    """Create a real branch session from a checkpoint transcript."""
    ckpt = find_checkpoint(store, session_id, checkpoint_id)
    if ckpt is None:
        return {"state": "MISSING", "code": CHECKPOINT_NOT_FOUND,
                "detail": f"no compaction checkpoint {checkpoint_id!r} for "
                          f"session {session_id!r}"}
    messages = checkpoint_messages(store, ckpt)
    if not messages:
        return {"state": "FAILED", "code": CHECKPOINT_EMPTY,
                "detail": f"checkpoint {checkpoint_id!r} has no messages"}
    db = _db(store)
    new_sid = f"{session_id[:40]}-branch-{uuid.uuid4().hex[:8]}"
    db.create_session(new_sid, source="dashboard",
                      model=ckpt.get("model"),
                      display_name=f"branch of {session_id}"[:80])
    inserted = int(db.append_messages_batch(new_sid, messages) or 0)
    db.patch_session_model_config(new_sid, {
        "_branched_from": ckpt["sessionId"],
        "_compaction_checkpoint": ckpt["id"],
    })
    return {"state": "OK", "sourceSessionId": session_id,
            "sessionId": new_sid, "checkpoint": ckpt,
            "messageCount": inserted,
            "authority": "voicebridge.session_compaction.branch"}


def restore(store: Any, session_id: str, checkpoint_id: str) -> Dict[str, Any]:
    """Restore a checkpoint transcript into *session_id* (recoverable)."""
    ckpt = find_checkpoint(store, session_id, checkpoint_id)
    if ckpt is None:
        return {"state": "MISSING", "code": CHECKPOINT_NOT_FOUND,
                "detail": f"no compaction checkpoint {checkpoint_id!r} for "
                          f"session {session_id!r}"}
    messages = checkpoint_messages(store, ckpt)
    if not messages:
        return {"state": "FAILED", "code": CHECKPOINT_EMPTY,
                "detail": f"checkpoint {checkpoint_id!r} has no messages"}
    db = _db(store)
    db.replace_messages(session_id, messages, archive_dropped=True)
    return {"state": "OK", "sessionId": session_id, "restored": len(messages),
            "message_count": int(db.message_count(session_id)),
            "checkpoint": ckpt,
            "authority": "voicebridge.session_compaction.restore "
                         "(SessionDB.replace_messages archive_dropped=True)"}


def switch_branch(store: Any, session_id: str, leaf_id: str) -> Dict[str, Any]:
    """Switch the active branch by restoring the named branch/checkpoint.

    Upstream flips a leaf pointer; Qubit's single transcript authority realizes
    the same observable state by restoring the target transcript in place
    (recoverable via the soft-archived rows).
    """
    db = _db(store)
    leaf = db.get_session(leaf_id)
    if leaf is not None and _model_config(leaf).get("_branched_from"):
        messages = db.get_messages(leaf_id)
        if not messages:
            return {"state": "FAILED", "code": CHECKPOINT_EMPTY,
                    "detail": f"branch {leaf_id!r} has no messages"}
        db.replace_messages(session_id, messages, archive_dropped=True)
        db.patch_session_model_config(session_id, {"_switched_from": leaf_id})
        return {"state": "OK", "sessionId": session_id, "switchedTo": leaf_id,
                "message_count": int(db.message_count(session_id)),
                "authority": "voicebridge.session_compaction.switch_branch "
                             "(restore branch transcript in place)"}
    out = restore(store, session_id, leaf_id)
    if out.get("state") == "OK":
        out["switchedTo"] = leaf_id
        out["authority"] = ("voicebridge.session_compaction.switch_branch "
                            "(checkpoint restore in place)")
    return out
