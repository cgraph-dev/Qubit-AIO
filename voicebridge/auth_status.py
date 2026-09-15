"""Qubit credential-reference inventory + session lifecycle.

Read-only surfaces behind Qubit contracts: credential REFERENCES (never
values) per configured provider, and action sessions from the receipt store
(redacted). The session lifecycle layer adds idempotent, audited, redacted
``search``/``show``/``rename``/``archive``/``delete``/``resume`` operations
over the append-only action receipts: receipts are never mutated (rename,
archive, and delete are metadata tombstones), ``delete`` requires explicit
confirmation and is recoverable through ``resume``, and every mutation lands
in a bounded, redacted audit. No keys, tokens, cookies, or private bodies
ever leave this module.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SCHEMA = "qubit-sessions/1"
LIFECYCLE_SCHEMA = "qubit-session-lifecycle/1"
AUDIT_CAP = 1000
DETAIL_LIMIT = 160
NAME_LIMIT = 120

#: Explicit store boundary: one writable authority per domain. The chat
#: transcript store (``voicebridge.chat_transport.ChatSessionStore``) owns
#: conversation sessions + turns in ``chat_sessions.json``; this module's
#: :class:`SessionStore` owns action-receipt lifecycle metadata in
#: ``sessions.json``. They never share a file, a schema, or a record id
#: namespace, and neither writes the other's records.
CHAT_TRANSCRIPT_STORE = "chat_sessions.json"
CHAT_TRANSCRIPT_SCHEMA = "qubit-chat-session/1"
RECEIPT_SESSION_STORE = "sessions.json"
STORE_BOUNDARY = {
    "schema": "qubit-store-boundary/1",
    "sessions": {
        "authority": "voicebridge.auth_status.SessionStore",
        "domain": "action-receipt session lifecycle",
        "file": RECEIPT_SESSION_STORE,
        "schema_id": LIFECYCLE_SCHEMA,
        "backing_source": "actions.json",
        "owns": ["display names", "archive/deleted tombstones", "resume marks",
                 "bounded redacted audit"],
        "not_owner_of": [CHAT_TRANSCRIPT_STORE, "chat conversation turns"],
    },
    "chat": {
        "authority": "voicebridge.chat_transport.ChatSessionStore",
        "domain": "chat transcript",
        "file": CHAT_TRANSCRIPT_STORE,
        "schema_id": CHAT_TRANSCRIPT_SCHEMA,
        "owns": ["chat sessions", "conversation messages"],
        "not_owner_of": [RECEIPT_SESSION_STORE, "action receipts",
                         "receipt lifecycle metadata"],
    },
}


def store_boundary() -> Dict[str, Any]:
    """Return the explicit, non-overlapping store ownership contract."""
    return dict(STORE_BOUNDARY)


def auth_status(cfg: Any) -> Dict[str, Any]:
    """Inventory credential references for every configured provider.

    Reports reference KIND (env/file/missing) and configured-ness only.
    Values are never read into output; file checks use existence + size.
    """
    from .providers.policy import FrontierPolicy

    policy = FrontierPolicy.from_config(cfg)
    providers = []
    for name in sorted(policy.providers.keys()):
        ref = str((policy.providers.get(name) or {}).get("key_ref", "") or "")
        kind, configured = "missing", False
        if ref.startswith("env:"):
            kind = "env"
            var = ref[len("env:"):].strip()
            configured = bool(var and os.environ.get(var, ""))
        elif ref.startswith("file:"):
            kind = "file"
            try:
                p = Path(ref[len("file:"):].strip()).expanduser()
                configured = p.is_file() and p.stat().st_size > 0
            except OSError:
                configured = False
        allowed = {a.split("/")[0] for a in policy.allowlist}
        providers.append({"provider": name, "key_ref": kind,
                          "configured": configured,
                          "allowlisted": name in allowed})
    return {"schema": "qubit-auth-status/1", "frontier_enabled": policy.enabled,
            "providers": providers}


class SessionStore:
    """Persistent lifecycle metadata layered over append-only action receipts.

    The receipt store (``actions.json``) is the source of truth for which
    sessions exist and what they did. This store adds a private
    (``0600``/``0700``) ``sessions.json`` with display names, archive marks,
    delete tombstones, and a bounded, redacted audit. It never rewrites or
    deletes a receipt, so a deleted session stays recoverable via ``resume``.

    Store boundary: this authority owns receipt lifecycle metadata only. Chat
    conversation sessions + turns are owned by
    ``voicebridge.chat_transport.ChatSessionStore`` (`chat_sessions.json`);
    this store never reads or writes that file. See :data:`STORE_BOUNDARY`.
    """

    STORE_FILE = RECEIPT_SESSION_STORE

    def __init__(self, state_dir: Path | str, *,
                 clock: Callable[[], float] = time.time) -> None:
        self._dir = Path(state_dir)
        self._path = self._dir / self.STORE_FILE
        self._receipts_path = self._dir / "actions.json"
        self._clock = clock
        self._lock = threading.RLock()
        self._state: Dict[str, Any] = {"schema": LIFECYCLE_SCHEMA,
                                       "sessions": {}, "audit": []}
        if self._path.exists():
            try:
                loaded = json.loads(self._path.read_text())
                if isinstance(loaded, dict):
                    self._state = loaded
            except (OSError, ValueError):
                pass   # corrupt store -> fail closed to empty defaults
        if not isinstance(self._state.get("sessions"), dict):
            self._state["sessions"] = {}
        if not isinstance(self._state.get("audit"), list):
            self._state["audit"] = []

    @property
    def path(self) -> Path:
        return self._path

    # ------------------------------------------------------------ persistence
    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._path.parent, 0o700)
        except OSError:
            pass
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent),
                                   prefix=".sessions.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(self._state, fh, indent=2)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass

    def _audit(self, op: str, session_id: str, **fields: Any) -> None:
        from .logging_setup import log_event
        from .redaction import redact_obj

        entry = redact_obj({"ts": self._clock(), "op": op,
                            "session_id": session_id, **fields})
        with self._lock:
            self._state.setdefault("audit", []).append(entry)
            if len(self._state["audit"]) > AUDIT_CAP:
                del self._state["audit"][:len(self._state["audit"]) - AUDIT_CAP]
        log_event("sessions." + op, session_id=session_id, **fields)

    def _commit(self, op: str, session_id: str, **fields: Any) -> None:
        with self._lock:
            self._audit(op, session_id, **fields)
            self._save()

    def audit(self, limit: int = AUDIT_CAP) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in
                    self._state.get("audit", [])[-max(1, int(limit)):]]

    # ------------------------------------------------------------ receipts
    def _receipts(self) -> Optional[Dict[str, Any]]:
        try:
            raw = json.loads(self._receipts_path.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        recs = raw.get("receipts", {})
        return recs if isinstance(recs, dict) else {}

    def _find_receipt(self, session_id: str) -> Optional[Dict[str, Any]]:
        recs = self._receipts()
        if not recs:
            return None
        raw = recs.get(session_id)
        if isinstance(raw, dict):
            return raw
        for cand in recs.values():
            if isinstance(cand, dict) and \
                    str(cand.get("action_id", "")) == session_id:
                return cand
        return None

    @staticmethod
    def _rid(raw: Dict[str, Any]) -> str:
        return str(raw.get("receipt_id") or raw.get("action_id") or "")

    def meta(self, session_id: str) -> Dict[str, Any]:
        return self._state.get("sessions", {}).get(session_id, {}) or {}

    def _row(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        from .redaction import redact_text

        rid = self._rid(raw)
        m = self.meta(rid)
        name = str(m.get("name") or "")
        return {
            "receipt_id": rid or str(raw.get("receipt_id", "")),
            "action_id": str(raw.get("action_id", "")),
            "state": str(raw.get("state", "")),
            "code": str(raw.get("code", "")),
            "runtime": str(raw.get("runtime", "qubit-broker")),
            "created_at": raw.get("created_at", 0),
            "detail": redact_text(str(raw.get("detail", "")))[:DETAIL_LIMIT],
            "name": name or None,
            "archived": bool(m.get("archived")),
            "archived_at": m.get("archived_at"),
            "deleted": bool(m.get("deleted")),
            "deleted_at": m.get("deleted_at"),
        }

    def _rows(self) -> Optional[List[Dict[str, Any]]]:
        recs = self._receipts()
        if recs is None:
            return None
        rows = [self._row(r) for r in recs.values() if isinstance(r, dict)]
        rows.sort(key=lambda r: float(r.get("created_at", 0) or 0),
                  reverse=True)
        return rows

    # ------------------------------------------------------------ operations
    def list(self, limit: int = 20, *, include_archived: bool = True,
             include_deleted: bool = False) -> Dict[str, Any]:
        rows = self._rows()
        if rows is None:
            return {"schema": SCHEMA, "state": "UNAVAILABLE",
                    "detail": "no readable receipt store", "sessions": []}
        kept = [r for r in rows
                if (include_deleted or not r["deleted"])
                and (include_archived or not r["archived"])]
        return {"schema": SCHEMA, "state": "OK", "count": len(kept),
                "sessions": kept[:max(1, int(limit))]}

    def search(self, query: str, limit: int = 20) -> Dict[str, Any]:
        rows = self._rows()
        if rows is None:
            return {"schema": SCHEMA, "state": "UNAVAILABLE",
                    "detail": "no readable receipt store", "sessions": []}
        needle = str(query or "").strip().lower()
        if not needle:
            return {"schema": SCHEMA, "state": "FAILED",
                    "code": "EMPTY_QUERY", "detail": "query required",
                    "sessions": []}
        hits = []
        for r in rows:
            if r["deleted"]:
                continue
            hay = " ".join(str(r.get(k, "")) for k in
                           ("receipt_id", "action_id", "name", "detail",
                            "state", "code")).lower()
            if needle in hay:
                hits.append(r)
        return {"schema": SCHEMA, "state": "OK", "count": len(hits),
                "query": str(query),
                "sessions": hits[:max(1, int(limit))]}

    def show(self, session_id: str) -> Dict[str, Any]:
        rec = self._find_receipt(session_id)
        if rec is None:
            if not self.meta(session_id):
                return {"schema": SCHEMA, "state": "NOT_FOUND",
                        "code": "SESSION_NOT_FOUND",
                        "detail": f"no session {session_id!r}"}
            return {"schema": SCHEMA, "state": "NOT_FOUND",
                    "code": "RECEIPT_MISSING",
                    "detail": "lifecycle record has no backing receipt"}
        return {"schema": SCHEMA, "state": "OK", "session": self._row(rec)}

    def rename(self, session_id: str, name: str) -> Dict[str, Any]:
        from .redaction import redact_text

        rec = self._find_receipt(session_id)
        if rec is None:
            return {"schema": SCHEMA, "state": "NOT_FOUND",
                    "code": "SESSION_NOT_FOUND",
                    "detail": f"no session {session_id!r}"}
        rid = self._rid(rec)
        clean = redact_text(str(name or "").strip())[:NAME_LIMIT]
        if not clean:
            return {"schema": SCHEMA, "state": "FAILED",
                    "code": "INVALID_NAME", "detail": "name required"}
        with self._lock:
            m = self._state["sessions"].setdefault(rid, {})
            changed = str(m.get("name") or "") != clean
            if changed:
                m["id"] = rid
                m["name"] = clean
                m["renamed_at"] = self._clock()
        self._commit("rename", rid, name=clean, changed=changed)
        return {"schema": SCHEMA, "state": "OK",
                "code": "OK" if changed else "ALREADY_NAMED",
                "changed": changed, "session": self._row(rec)}

    def archive(self, session_id: str) -> Dict[str, Any]:
        rec = self._find_receipt(session_id)
        if rec is None:
            return {"schema": SCHEMA, "state": "NOT_FOUND",
                    "code": "SESSION_NOT_FOUND",
                    "detail": f"no session {session_id!r}"}
        rid = self._rid(rec)
        with self._lock:
            m = self._state["sessions"].setdefault(rid, {})
            if m.get("deleted"):
                return {"schema": SCHEMA, "state": "FAILED",
                        "code": "SESSION_DELETED",
                        "detail": "session is deleted; resume before archiving",
                        "session": self._row(rec)}
            changed = not bool(m.get("archived"))
            if changed:
                m["id"] = rid
                m["archived"] = True
                m["archived_at"] = self._clock()
        self._commit("archive", rid, changed=changed)
        return {"schema": SCHEMA, "state": "OK",
                "code": "OK" if changed else "ALREADY_ARCHIVED",
                "changed": changed, "session": self._row(rec)}

    def delete(self, session_id: str, *, confirm: bool = False) -> Dict[str, Any]:
        rec = self._find_receipt(session_id)
        if rec is None:
            return {"schema": SCHEMA, "state": "NOT_FOUND",
                    "code": "SESSION_NOT_FOUND",
                    "detail": f"no session {session_id!r}"}
        rid = self._rid(rec)
        if not confirm:
            self._commit("delete_refused", rid, code="CONFIRM_REQUIRED")
            return {"schema": SCHEMA, "state": "CONFIRMATION_REQUIRED",
                    "code": "CONFIRM_REQUIRED", "changed": False,
                    "detail": "delete is destructive; re-run with --confirm "
                              "(recoverable via resume)",
                    "session": self._row(rec)}
        with self._lock:
            m = self._state["sessions"].setdefault(rid, {})
            already = bool(m.get("deleted"))
            if not already:
                m["id"] = rid
                m["deleted"] = True
                m["deleted_at"] = self._clock()
        self._commit("delete", rid, changed=not already,
                     code="OK" if not already else "ALREADY_DELETED")
        if already:
            return {"schema": SCHEMA, "state": "ALREADY_DELETED",
                    "code": "ALREADY_DELETED", "changed": False,
                    "detail": "session already deleted; resume to restore",
                    "session": self._row(rec)}
        return {"schema": SCHEMA, "state": "OK", "code": "OK",
                "changed": True, "session": self._row(rec)}

    def resume(self, session_id: str) -> Dict[str, Any]:
        rec = self._find_receipt(session_id)
        if rec is None:
            return {"schema": SCHEMA, "state": "NOT_FOUND",
                    "code": "SESSION_NOT_FOUND",
                    "detail": f"no session {session_id!r}"}
        rid = self._rid(rec)
        with self._lock:
            m = self._state["sessions"].get(rid)
            changed = bool(m and (m.get("deleted") or m.get("archived")))
            if changed:
                m.pop("deleted", None)
                m.pop("deleted_at", None)
                m.pop("archived", None)
                m.pop("archived_at", None)
                m["id"] = rid
                m["resumed_at"] = self._clock()
        self._commit("resume", rid, changed=changed,
                     code="OK" if changed else "ALREADY_ACTIVE")
        return {"schema": SCHEMA, "state": "OK",
                "code": "OK" if changed else "ALREADY_ACTIVE",
                "changed": changed, "session": self._row(rec)}


def session_store(cfg: Any) -> SessionStore:
    """Open the session lifecycle store in the config's state dir."""
    state_dir = cfg.state_dir() if hasattr(cfg, "state_dir") else Path(".")
    return SessionStore(Path(state_dir))


def recent_sessions(cfg: Any, limit: int = 20) -> Dict[str, Any]:
    """Recent action sessions (receipts) from the state tree, redacted.

    Applies the lifecycle overlay: display names, archive marks, and delete
    tombstones. Deleted sessions are excluded; archived sessions are listed
    with ``archived: true``.
    """
    from .redaction import redact_text

    state_dir = cfg.state_dir() if hasattr(cfg, "state_dir") else Path(".")
    path = Path(state_dir) / "actions.json"
    items: List[Dict[str, Any]] = []
    try:
        raw = json.loads(path.read_text())
        receipts = raw.get("receipts", {}) if isinstance(raw, dict) else {}
        store = SessionStore(state_dir)
        cap = max(1, min(int(limit), 100))
        ordered = sorted(receipts.values(),
                         key=lambda r: float(r.get("created_at", 0) or 0),
                         reverse=True)
        for r in ordered:
            if not isinstance(r, dict):
                continue
            rid = str(r.get("receipt_id", "") or r.get("action_id", ""))
            meta = store.meta(rid)
            if meta.get("deleted"):
                continue
            items.append({
                "receipt_id": str(r.get("receipt_id", "")),
                "action_id": str(r.get("action_id", "")),
                "state": str(r.get("state", "")),
                "code": str(r.get("code", "")),
                "runtime": str(r.get("runtime", "qubit-broker")),
                "created_at": r.get("created_at", 0),
                "detail": redact_text(str(r.get("detail", "")))[:DETAIL_LIMIT],
                "name": str(meta.get("name") or "") or None,
                "archived": bool(meta.get("archived")),
                "archived_at": meta.get("archived_at"),
            })
            if len(items) >= cap:
                break
    except (OSError, ValueError) as exc:
        return {"schema": SCHEMA, "state": "UNAVAILABLE",
                "detail": f"no readable receipt store: {exc}"[:160],
                "sessions": []}
    return {"schema": SCHEMA, "state": "OK",
            "count": len(items), "sessions": items}
