"""Qubit-safe session sharing (Qubit v2026.9.4 delta D24).

Qubit publishes a read-only session transcript to a public URL. Qubit has
no public listener and uploads nothing: this module is the Qubit-safe
equivalent — a locally-served, token-scoped, expiring, revocable read-only
view of a redacted action receipt.

* ``publish`` reads the session from the REAL receipt store
  (``voicebridge.auth_status.SessionStore`` over ``actions.json``), snapshots
  the redacted transcript, and records a share (``id``, ``session_id``,
  ``token_hash``, ``created_at``, ``expires_at``, ``revoked``) in
  ``<state_dir>/session-shares.json`` (``0600``, parent ``0700``). The token is
  returned exactly ONCE and only its SHA-256 hash is persisted.
* ``resolve`` is the read-only viewer: a constant-time token check plus
  expiry/revocation checks returning the redacted transcript or a typed
  ``SHARE_*`` refusal. Nothing is uploaded and no external URL is minted.
* ``list_shares`` never renders a token or a token hash; ``revoke`` tombstones
  a share (confirmed); ``purge_expired`` drops expired records.

The whole surface is emergency-stop aware: ``publish``, ``revoke`` and
``resolve`` refuse with ``SHARE_ESTOP`` while the durable latch is engaged.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

SCHEMA = "qubit-session-share/1"
STORE_NAME = "session-shares.json"
URL_PREFIX = "/share"
MAX_SHARES = 1000
MAX_TTL_S = 7 * 86400.0
MIN_TTL_S = 1.0
DEFAULT_TTL_S = 3600.0

UNAVAILABLE_CODE = "SHARE_UNAVAILABLE"
NOT_FOUND_CODE = "SHARE_NOT_FOUND"
CONFIRM_CODE = "SHARE_CONFIRM_REQUIRED"
EXPIRED_CODE = "SHARE_EXPIRED"
REVOKED_CODE = "SHARE_REVOKED"
BAD_TOKEN_CODE = "SHARE_BAD_TOKEN"
ESTOP_CODE = "SHARE_ESTOP"
BAD_INPUT_CODE = "SHARE_BAD_INPUT"
CORRUPT_CODE = "SHARE_CORRUPT"


class ShareError(Exception):
    """Typed share failure carrying a ``SHARE_*`` code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _now() -> float:
    return time.time()


def _store_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / STORE_NAME


def _blank() -> Dict[str, Any]:
    return {"schema": SCHEMA, "shares": {}}


def _load(cfg: Any) -> Dict[str, Any]:
    path = _store_path(cfg)
    if not path.exists():
        return _blank()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ShareError(CORRUPT_CODE,
                         f"share store unreadable: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA \
            or not isinstance(raw.get("shares"), dict):
        raise ShareError(CORRUPT_CODE, "share store has an unknown schema")
    return raw


def _save(cfg: Any, state: Dict[str, Any]) -> None:
    path = _store_path(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                                   prefix=".session-shares.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2, sort_keys=True)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError as exc:
        raise ShareError(CORRUPT_CODE,
                         f"share store write failed: {exc}") from exc


def _audit(op: str, **fields: Any) -> None:
    from .logging_setup import log_event

    try:
        log_event(f"session_share.{op}", **fields)
    except Exception:
        pass


def _estop(cfg: Any) -> bool:
    """Durable emergency-stop probe (fail closed)."""
    try:
        from .plugins import estop_engaged as _engaged

        return bool(_engaged(cfg))
    except Exception:
        return True


def _refuse(state: str, code: str, detail: str, **extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"schema": SCHEMA, "state": state, "code": code,
                           "detail": detail}
    out.update(extra)
    return out


def _refuse_estop(operation: str) -> Dict[str, Any]:
    return _refuse(
        "ESTOP", ESTOP_CODE,
        "Qubit Agent cannot do this: the emergency stop is latched; reset it "
        "before session sharing.",
        operation=operation, owner="qubit-policy",
        rollback="share store untouched; nothing to roll back")


def _public(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Render a share WITHOUT its token or token hash."""
    return {
        "id": rec.get("id"),
        "session_id": rec.get("session_id"),
        "created_at": rec.get("created_at"),
        "expires_at": rec.get("expires_at"),
        "revoked": bool(rec.get("revoked")),
        "revoked_at": rec.get("revoked_at"),
        "requested_by": rec.get("requested_by"),
        "url": f"{URL_PREFIX}/{rec.get('id')}",
    }


def _is_expired(rec: Dict[str, Any], now: float) -> bool:
    try:
        return now >= float(rec.get("expires_at"))
    except (TypeError, ValueError):
        return True


def _transcript(row: Dict[str, Any]) -> Dict[str, Any]:
    from .redaction import redact_obj, redact_text

    raw = {
        "session_id": row.get("receipt_id") or row.get("action_id") or "",
        "action_id": str(row.get("action_id", "")),
        "state": str(row.get("state", "")),
        "code": str(row.get("code", "")),
        "runtime": str(row.get("runtime", "")),
        "created_at": row.get("created_at", 0),
        "name": row.get("name"),
        "detail": redact_text(str(row.get("detail", ""))),
        "note": "read-only snapshot of a redacted action receipt; Qubit "
                "sessions are append-only audit records, not chat transcripts",
    }
    return redact_obj(raw)


# ----------------------------------------------------------------------- API
def status(cfg: Any) -> Dict[str, Any]:
    """Report share-store health and counts (never tokens)."""
    try:
        state = _load(cfg)
    except ShareError as exc:
        return _refuse("UNAVAILABLE", exc.code, exc.detail, count=0,
                       active=0, expired=0, revoked=0, estop=_estop(cfg),
                       path_prefix=URL_PREFIX)
    now = _now()
    shares = [r for r in state["shares"].values() if isinstance(r, dict)]
    active = sum(1 for r in shares
                 if not r.get("revoked") and not _is_expired(r, now))
    expired = sum(1 for r in shares if _is_expired(r, now))
    revoked = sum(1 for r in shares if r.get("revoked"))
    return {"schema": SCHEMA, "state": "OK", "code": "OK",
            "count": len(shares), "active": active, "expired": expired,
            "revoked": revoked, "estop": _estop(cfg),
            "path_prefix": URL_PREFIX,
            "detail": "locally-served token-scoped read-only views; nothing "
                      "is uploaded and no external URL is minted"}


def publish(cfg: Any, session_id: str, *, ttl_s: float = DEFAULT_TTL_S,
            confirmed: bool = False,
            requested_by: str = "user") -> Dict[str, Any]:
    """Snapshot a redacted session transcript as a revocable share.

    Requires explicit confirmation. Reads the session from the real receipt
    store, redacts secrets, persists the share (hash only), and returns the
    local URL plus the bearer token EXACTLY ONCE.
    """
    from .auth_status import SessionStore

    if _estop(cfg):
        return _refuse_estop("publish")
    if not confirmed:
        return _refuse("CONFIRMATION_REQUIRED", CONFIRM_CODE,
                       "publish is externally viewable; re-run with --confirm",
                       changed=False)
    sid = str(session_id or "").strip()
    if not sid:
        return _refuse("FAILED", BAD_INPUT_CODE, "session_id required",
                       changed=False)
    try:
        ttl = float(ttl_s)
    except (TypeError, ValueError):
        return _refuse("FAILED", BAD_INPUT_CODE, "ttl_s must be numeric",
                       changed=False)
    if not (MIN_TTL_S <= ttl <= MAX_TTL_S):
        return _refuse("FAILED", BAD_INPUT_CODE,
                       f"ttl_s must be within [{MIN_TTL_S:.0f}, "
                       f"{MAX_TTL_S:.0f}] seconds", changed=False)

    store = SessionStore(cfg.state_dir())
    listing = store.list(limit=1)
    if listing.get("state") == "UNAVAILABLE":
        return _refuse("UNAVAILABLE", UNAVAILABLE_CODE,
                       "no readable receipt store", changed=False)
    shown = store.show(sid)
    if shown.get("state") != "OK" or not isinstance(shown.get("session"), dict):
        return _refuse("NOT_FOUND", NOT_FOUND_CODE,
                       str(shown.get("detail", f"no session {sid!r}"))[:200],
                       changed=False)
    row = shown["session"]
    rid = str(row.get("receipt_id") or row.get("action_id") or sid)

    state = _load(cfg)
    if len(state["shares"]) >= MAX_SHARES:
        return _refuse("UNAVAILABLE", UNAVAILABLE_CODE,
                       f"share limit ({MAX_SHARES}) reached", changed=False)

    now = _now()
    share_id = "shr" + secrets.token_hex(8)
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    rec = {"id": share_id, "session_id": rid, "token_hash": token_hash,
           "created_at": now, "expires_at": now + ttl, "revoked": False,
           "requested_by": str(requested_by or "user")[:120],
           "transcript": _transcript(row)}
    state["shares"][share_id] = rec
    _save(cfg, state)
    _audit("publish", share_id=share_id, session_id=rid,
           expires_at=rec["expires_at"], requested_by=rec["requested_by"])
    return {"schema": SCHEMA, "state": "OK", "code": "OK", "changed": True,
            "share": _public(rec), "path": f"{URL_PREFIX}/{share_id}",
            "url": f"{URL_PREFIX}/{share_id}", "token": token,
            "expires_at": rec["expires_at"],
            "warning": "the token is shown once and never persisted in clear"}


def list_shares(cfg: Any) -> Dict[str, Any]:
    """List shares (redacted; no tokens, no token hashes)."""
    try:
        state = _load(cfg)
    except ShareError as exc:
        return _refuse("UNAVAILABLE", exc.code, exc.detail, count=0, shares=[])
    recs = sorted(state["shares"].values(), key=lambda r: r.get("id", ""))
    return {"schema": SCHEMA, "state": "OK", "code": "OK",
            "count": len(recs), "shares": [_public(r) for r in recs]}


def revoke(cfg: Any, share_id: str, *,
           confirmed: bool = False) -> Dict[str, Any]:
    """Revoke a share (confirmed; tombstone, record retained)."""
    if _estop(cfg):
        return _refuse_estop("revoke")
    if not confirmed:
        return _refuse("CONFIRMATION_REQUIRED", CONFIRM_CODE,
                       "revoke requires explicit --confirm", changed=False)
    sid = str(share_id or "").strip()
    if not sid:
        return _refuse("FAILED", BAD_INPUT_CODE, "share_id required",
                       changed=False)
    try:
        state = _load(cfg)
    except ShareError as exc:
        return _refuse("UNAVAILABLE", exc.code, exc.detail, changed=False)
    rec = state["shares"].get(sid)
    if not isinstance(rec, dict):
        return _refuse("NOT_FOUND", NOT_FOUND_CODE, f"no share {sid!r}",
                       changed=False)
    if rec.get("revoked"):
        return {"schema": SCHEMA, "state": "OK", "code": "OK",
                "changed": False, "detail": "share already revoked",
                "share": _public(rec)}
    rec["revoked"] = True
    rec["revoked_at"] = _now()
    _save(cfg, state)
    _audit("revoke", share_id=sid, session_id=rec.get("session_id"))
    return {"schema": SCHEMA, "state": "OK", "code": "OK", "changed": True,
            "share": _public(rec)}


def revoke_by_token(cfg: Any, token: str, *,
                    confirmed: bool = False) -> Dict[str, Any]:
    """Revoke the share a bearer *token* belongs to (constant-time hash match)."""
    raw = token if isinstance(token, str) else ""
    if not raw:
        return _refuse("FAILED", BAD_INPUT_CODE, "token required",
                       changed=False)
    try:
        state = _load(cfg)
    except ShareError as exc:
        return _refuse("UNAVAILABLE", exc.code, exc.detail, changed=False)
    want = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    match = next((r for r in state["shares"].values()
                  if isinstance(r, dict) and r.get("token_hash") == want), None)
    if match is None:
        return _refuse("NOT_FOUND", NOT_FOUND_CODE, "no share for token",
                       changed=False)
    return revoke(cfg, str(match.get("id") or ""), confirmed=confirmed)


def resolve(cfg: Any, share_id: str, token: str) -> Dict[str, Any]:
    """Read-only view: constant-time token check + expiry/revocation checks."""
    if _estop(cfg):
        return _refuse_estop("resolve")
    sid = str(share_id or "").strip()
    raw_token = token if isinstance(token, str) else ""
    if not sid or not raw_token:
        return _refuse("FAILED", BAD_INPUT_CODE, "share_id and token required")
    try:
        state = _load(cfg)
    except ShareError as exc:
        return _refuse("UNAVAILABLE", exc.code, exc.detail)
    rec = state["shares"].get(sid)
    if not isinstance(rec, dict):
        return _refuse("NOT_FOUND", NOT_FOUND_CODE, f"no share {sid!r}")
    stored_hash = rec.get("token_hash")
    if not isinstance(stored_hash, str) or not stored_hash:
        return _refuse("UNAVAILABLE", CORRUPT_CODE,
                       "share record is corrupt (missing token hash)")
    expected = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(expected, stored_hash):
        return _refuse("DENIED", BAD_TOKEN_CODE, "token does not match")
    if rec.get("revoked"):
        return _refuse("REVOKED", REVOKED_CODE, "share has been revoked",
                       share_id=sid)
    if _is_expired(rec, _now()):
        return _refuse("EXPIRED", EXPIRED_CODE, "share has expired",
                       share_id=sid)
    return {"schema": SCHEMA, "state": "OK", "code": "OK", "share_id": sid,
            "session_id": rec.get("session_id"),
            "created_at": rec.get("created_at"),
            "expires_at": rec.get("expires_at"),
            "transcript": rec.get("transcript"),
            "detail": "read-only redacted view; served locally"}


def purge_expired(cfg: Any) -> Dict[str, Any]:
    """Drop expired share records; keep active and revoked records."""
    try:
        state = _load(cfg)
    except ShareError as exc:
        return _refuse("UNAVAILABLE", exc.code, exc.detail, purged=0,
                       remaining=0)
    now = _now()
    shares = state["shares"]
    expired = [k for k, r in shares.items()
               if isinstance(r, dict) and _is_expired(r, now)]
    for key in expired:
        del shares[key]
    if expired:
        _save(cfg, state)
        _audit("purge_expired", purged=len(expired))
    return {"schema": SCHEMA, "state": "OK", "code": "OK",
            "purged": len(expired), "remaining": len(shares)}


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the plugin/webhook surfaces (fail closed)."""
    return _estop(cfg)
