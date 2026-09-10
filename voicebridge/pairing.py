"""Qubit device pairing via hashed one-time codes + grants.

Qubit ``pairing {list,approve,revoke,clear-pending}`` gates dashboard user
access with pairing codes. Qubit issues no network listener and runs no
second authority; instead this module provides the code-side enrollment
kernel behind the Qubit broker:

* ``create`` — issue a one-time code (Crockford base32, 8 chars) shown
  ONLY at creation; stored is a salted sha256 verifier, never the code.
  Codes carry scopes, a TTL (default 10 min, cap 24 h), an optional
  account binding, and a 5-attempt rate limit. Explicit ``--confirm``.
* ``approve`` — constant-time code verification (expiry, revocation, and
  attempt limits fail closed with typed errors); on success the code is
  consumed (single-use) and a REAL scoped grant lands in the 7E
  permission store (TTL-capped), so enrollment confers broker-enforced
  authority and nothing else.
* ``list`` — redacted records (no codes, no verifiers, ever).
* ``revoke`` — tombstones the code and revokes its grant (confirmed,
  audited; a spent grant reports ``grant_gone`` instead of failing).

No listener is opened, no URL carries a token, and no secret reaches a
log: audit events carry code ids and scopes only.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = "qubit-pairing/1"
STORE_NAME = "pairing.json"
CODE_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LEN = 8
DEFAULT_TTL_S = 600.0
MAX_TTL_S = 86400.0
MAX_ATTEMPTS = 5
MAX_CODES = 256
MAX_FILE_BYTES = 1_000_000

CONFIRM_CODE = "PAIRING_CONFIRM_REQUIRED"
ESTOP_CODE = "PAIRING_ESTOP"
NOT_FOUND_CODE = "PAIRING_NOT_FOUND"
BAD_CODE = "PAIRING_BAD_CODE"
EXPIRED_CODE = "PAIRING_EXPIRED"
REVOKED_CODE = "PAIRING_REVOKED"
USED_CODE = "PAIRING_USED"
LOCKED_CODE = "PAIRING_LOCKED"
CORRUPT_CODE = "PAIRING_STORE_CORRUPT"

_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
_SCOPE_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")


class PairingError(Exception):
    """Typed pairing failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _now() -> float:
    return time.time()


def _new_code() -> str:
    return "".join(secrets.choice(CODE_CHARS) for _ in range(CODE_LEN))


def _hash(salt: bytes, code: str) -> str:
    return hashlib.sha256(salt + code.encode("utf-8")).hexdigest()


def _store_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / STORE_NAME


def _blank() -> Dict[str, Any]:
    return {"schema": SCHEMA, "codes": {}}


def _load(cfg: Any) -> Dict[str, Any]:
    path = _store_path(cfg)
    if not path.exists():
        return _blank()
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise PairingError(CORRUPT_CODE, "pairing store too large")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except PairingError:
        raise
    except (OSError, ValueError) as exc:
        raise PairingError(CORRUPT_CODE,
                           f"pairing store unreadable: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA \
            or not isinstance(raw.get("codes"), dict):
        raise PairingError(CORRUPT_CODE, "pairing store has an unknown schema")
    return raw


def _save(cfg: Any, state: Dict[str, Any]) -> None:
    path = _store_path(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{STORE_NAME}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True),
                       encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as exc:
        raise PairingError(CORRUPT_CODE,
                           f"pairing store write failed: {exc}") from exc


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    from .logging_setup import log_event

    try:
        log_event(f"pairing.{op}", actor=actor, **detail)
    except Exception:
        pass


def _public(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": rec["id"], "label": rec["label"],
            "scopes": list(rec.get("scopes", [])),
            "account": rec.get("account", ""),
            "expires_at": rec.get("expires_at"),
            "used": bool(rec.get("used", False)),
            "revoked": bool(rec.get("revoked", False)),
            "attempts": int(rec.get("attempts", 0)),
            "grant_id": rec.get("grant_id") or "",
            "created_at": rec.get("created_at")}


def _check_label(label: str) -> str:
    text = (label or "").strip()
    if not _LABEL_RE.match(text):
        raise PairingError("PAIRING_BAD_LABEL",
                           "label must match ^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
    return text


def _check_scopes(scopes: List[str]) -> List[str]:
    clean = []
    for scope in scopes or []:
        text = str(scope or "").strip().lower()
        if not _SCOPE_RE.match(text):
            raise PairingError("PAIRING_BAD_SCOPE",
                               f"bad scope {scope!r}")
        clean.append(text)
    if not clean:
        raise PairingError("PAIRING_BAD_SCOPE",
                           "at least one scope is required")
    return sorted(set(clean))


# ---------------------------------------------------------------- operations
def create(cfg: Any, label: str, scopes: List[str], *, account: str = "",
           ttl_s: float = DEFAULT_TTL_S, requested_by: str = "cli:user",
           confirmed: bool = False) -> Dict[str, Any]:
    """Issue a one-time pairing code (shown once, stored hashed)."""
    if not confirmed:
        raise PairingError(CONFIRM_CODE,
                           "pairing create requires explicit --confirm")
    clean_label = _check_label(label)
    clean_scopes = _check_scopes(scopes)
    try:
        ttl = max(60.0, min(float(ttl_s), MAX_TTL_S))
    except (TypeError, ValueError):
        raise PairingError("PAIRING_BAD_TTL",
                           "ttl must be 60..86400 seconds") from None
    state = _load(cfg)
    live = [r for r in state["codes"].values()
            if not r.get("used") and not r.get("revoked")]
    if len(live) >= MAX_CODES:
        raise PairingError("PAIRING_LIMIT", f"code limit reached ({MAX_CODES})")
    code = _new_code()
    salt = secrets.token_bytes(16)
    code_id = "pair_" + secrets.token_hex(8)
    rec = {"id": code_id, "label": clean_label, "scopes": clean_scopes,
           "account": (account or "").strip()[:80],
           "salt_b64": __import__("base64").b64encode(salt).decode("ascii"),
           "verifier": _hash(salt, code),
           "created_at": _now(), "expires_at": _now() + ttl,
           "attempts": 0, "used": False, "revoked": False, "grant_id": ""}
    state["codes"][code_id] = rec
    _save(cfg, state)
    _audit("create", requested_by,
           {"code_id": code_id, "scopes": clean_scopes,
            "account_bound": bool(account)})
    return {"state": "OK", "code": code,
            "code_id": code_id, "expires_at": rec["expires_at"],
            "warning": "shown once: deliver it out-of-band; it is never "
                       "stored or shown again"}


def _find_live(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [r for r in state["codes"].values()
            if not r.get("used") and not r.get("revoked")]


def approve(cfg: Any, code: str, *, requested_by: str = "cli:user",
            confirmed: bool = False) -> Dict[str, Any]:
    """Verify a code (constant-time) and issue its scoped grant."""
    if not confirmed:
        raise PairingError(CONFIRM_CODE,
                           "pairing approve requires explicit --confirm")
    from .permissions import permissions_store

    attempt = (code or "").strip().upper().replace("-", "").replace(" ", "")
    state = _load(cfg)
    now = _now()
    match = None
    for rec in _find_live(state):
        import base64 as _b64

        try:
            salt = _b64.b64decode(rec["salt_b64"])
        except Exception:
            continue
        if hmac.compare_digest(_hash(salt, attempt), rec["verifier"]):
            match = rec
            break
    if match is None:
        raise PairingError(BAD_CODE, "unknown or consumed pairing code")
    if match.get("attempts", 0) >= MAX_ATTEMPTS:
        raise PairingError(LOCKED_CODE, "code locked after too many attempts")
    if now > float(match.get("expires_at", 0)):
        raise PairingError(EXPIRED_CODE, "pairing code expired")
    store = permissions_store(cfg)
    ttl = max(60.0, min(float(match["expires_at"]) - now, MAX_TTL_S))
    try:
        grant = store.grant("gateway.access", requested_by=requested_by,
                            confirmed=True, scopes=tuple(match["scopes"]),
                            ttl_s=ttl,
                            note=f"pairing {match['id']}")
    except Exception as exc:
        match["attempts"] = int(match.get("attempts", 0)) + 1
        _save(cfg, state)
        raise PairingError("PAIRING_GRANT_FAILED",
                           f"grant issue failed: {exc}") from exc
    match["used"] = True
    match["grant_id"] = grant.id
    _save(cfg, state)
    _audit("approve", requested_by,
           {"code_id": match["id"], "grant_id": grant.id,
            "scopes": match["scopes"]})
    return {"state": "OK", "approved": match["id"],
            "grant_id": grant.id, "scopes": match["scopes"],
            "account": match.get("account", "")}


def list_codes(cfg: Any) -> Dict[str, Any]:
    """Redacted code records (codes/verifiers never shown)."""
    state = _load(cfg)
    recs = sorted(state["codes"].values(),
                  key=lambda r: r.get("created_at", 0), reverse=True)
    return {"state": "OK", "codes": [_public(r) for r in recs]}


def revoke(cfg: Any, code_id: str, *, requested_by: str = "cli:user",
           confirmed: bool = False) -> Dict[str, Any]:
    """Tombstone a code and revoke its grant (confirmed, audited)."""
    if not confirmed:
        raise PairingError(CONFIRM_CODE,
                           "pairing revoke requires explicit --confirm")
    from .permissions import permissions_store

    state = _load(cfg)
    rec = state["codes"].get((code_id or "").strip())
    if rec is None:
        raise PairingError(NOT_FOUND_CODE, f"no pairing {code_id!r}")
    rec["revoked"] = True
    grant_gone = not rec.get("grant_id")
    if rec.get("grant_id"):
        try:
            permissions_store(cfg).revoke(rec["grant_id"],
                                          requested_by=requested_by,
                                          confirmed=True)
        except Exception:
            grant_gone = True
    _save(cfg, state)
    _audit("revoke", requested_by,
           {"code_id": rec["id"], "grant_gone": grant_gone})
    return {"state": "OK", "revoked": rec["id"], "grant_gone": grant_gone}


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the plugins surface (fail closed)."""
    from .plugins import estop_engaged as _engaged

    return _engaged(cfg)


def refuse_estop(operation: str) -> Dict[str, Any]:
    """Typed refusal while the emergency stop is latched."""
    return {"state": "ESTOP", "code": ESTOP_CODE, "operation": operation,
            "explanation": "Qubit Agent cannot do this: the emergency stop "
                           "is latched; reset it before pairing changes.",
            "owner": "qubit-policy",
            "rollback": "registry untouched; nothing to roll back"}
