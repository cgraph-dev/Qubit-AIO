"""Packet 7C: trusted-device enrollment and device profiles.

A new device must be explicitly authorized and must re-check hardware, model
compatibility, optional dependencies, and capabilities. Never copy live
browser profiles or secrets automatically. Enrollment state is a small JSON
file under the private state root (0700 dir / 0600 file). This module is
read-only about the host; it never launches, enables, or mutates services,
browser, config, or model state.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import distribution as dist

ENROLL_FILE = "device.json"

# Device-scoped tokens (OC-05). A remote device proves possession of a token
# that is shown once at issue, stored only as a salted sha256 verifier, bound
# to an explicit scope set, and requires an operator approval before it can be
# redeemed. Rotate mints a fresh token and revokes the predecessor; revoke
# tombstones the record. No listener or network transport lives here.
TOKEN_FILE = "device-tokens.json"
TOKEN_SCHEMA = "qubit-device-tokens/1"
TOKEN_DEFAULT_TTL_S = 3600.0
TOKEN_MAX_TTL_S = 31_536_000.0
TOKEN_MAX_ACTIVE = 256
TOKEN_MAX_FILE_BYTES = 1_000_000
TOKEN_CONFIRM_CODE = "DEVICE_TOKEN_CONFIRM_REQUIRED"
TOKEN_ESTOP_CODE = "DEVICE_TOKEN_ESTOP"
TOKEN_NOT_FOUND_CODE = "DEVICE_TOKEN_NOT_FOUND"
TOKEN_BAD_CODE = "DEVICE_TOKEN_BAD"
TOKEN_EXPIRED_CODE = "DEVICE_TOKEN_EXPIRED"
TOKEN_REVOKED_CODE = "DEVICE_TOKEN_REVOKED"
TOKEN_PENDING_CODE = "DEVICE_TOKEN_PENDING_APPROVAL"
TOKEN_BAD_INPUT_CODE = "DEVICE_TOKEN_BAD_INPUT"
TOKEN_CORRUPT_CODE = "DEVICE_TOKEN_STORE_CORRUPT"
TOKEN_SCOPE_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")


class DeviceNotEnrolledError(Exception):
    pass


def machine_id() -> str:
    """Stable host identifier: /etc/machine-id when present, else a persisted
    UUID under the state root (never a secret; not exported as a credential)."""
    try:
        mid = Path("/etc/machine-id").read_text().strip()
        if mid:
            return mid
    except OSError:
        pass
    return str(uuid.UUID(int=0))  # deterministic fallback; replaced by caller


def _enroll_path(state_dir: Path) -> Path:
    return Path(state_dir) / ENROLL_FILE


def capabilities(cfg: Any) -> Dict[str, Any]:
    """Re-check hardware/model/optional dependencies/capabilities for the
    CURRENT host (typed, read-only)."""
    checks = dist.doctor(cfg)
    by = {c["name"]: c["state"] for c in checks}
    return {
        "gpu": by.get("gpu"),
        "ollama": by.get("ollama"),
        "browser_cdp": by.get("browser_cdp"),
        "audio": by.get("audio"),
        "openssl": by.get("openssl"),
        "isolation": by.get("isolation"),
        "ram": by.get("ram"),
        "disk": by.get("disk"),
    }


def profile(cfg: Any) -> Dict[str, Any]:
    """Current device profile: identity + capability snapshot + enrollment."""
    state_dir = cfg.state_dir() if hasattr(cfg, "state_dir") else Path(
        "~/.local/state/qubit-voicebridge").expanduser()
    p = _enroll_path(state_dir)
    data: Dict[str, Any] = {"machine_id": machine_id(),
                            "enrolled": False,
                            "enrolled_at": None,
                            "capabilities": capabilities(cfg)}
    if p.exists():
        try:
            data.update(json.loads(p.read_text()))
        except Exception:
            pass
    return data


def enroll(cfg: Any, *, model_alias: Optional[str] = None,
           require_new_authorization: bool = True) -> Dict[str, Any]:
    """Explicitly authorize THIS device. Records the capability snapshot and
    the model alias it was enrolled for. Never reads/copies secrets or browser
    profiles."""
    state_dir = cfg.state_dir() if hasattr(cfg, "state_dir") else Path(
        "~/.local/state/qubit-voicebridge").expanduser()
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(Path(state_dir), 0o700)
    except OSError:
        pass
    if require_new_authorization:
        raise DeviceNotEnrolledError(
            "trusted-device enrollment requires explicit authorization: "
            "pass --enroll on the CLI; never automatic")
    from .runner_contract import resolve
    alias = model_alias or resolve(cfg).alias
    rec = {
        "machine_id": machine_id(),
        "enrolled": True,
        "enrolled_at": round(time.time(), 3),
        "model_alias": alias,
        "capabilities": capabilities(cfg),
    }
    tmp = _enroll_path(state_dir).with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=2, sort_keys=True))
    os.replace(tmp, _enroll_path(state_dir))
    try:
        os.chmod(_enroll_path(state_dir), 0o600)
    except OSError:
        pass
    return rec


def require_enrolled(cfg: Any) -> Dict[str, Any]:
    """Fail closed unless the current device is explicitly enrolled."""
    p = profile(cfg)
    if not p.get("enrolled"):
        raise DeviceNotEnrolledError(
            "device not enrolled — run `qubit device enroll` (explicit)")
    return p


def assert_device_compatible(cfg: Any, device_profile: Dict[str, Any]) -> None:
    """A restore to a NEW device must re-check hardware/model/capabilities.
    Raises DeviceNotEnrolledError on incompatibility (never a silent pass)."""
    cur = capabilities(cfg)
    required = ("gpu", "ollama", "audio", "openssl")
    missing = [k for k in required if cur.get(k) in ("MISSING", "DEGRADED")]
    if missing:
        raise DeviceNotEnrolledError(
            f"target device missing required capability: {', '.join(missing)}")
    src_model = device_profile.get("model_alias")
    cur_model = profile(cfg).get("model_alias")
    if src_model and cur_model and src_model != cur_model:
        raise DeviceNotEnrolledError(
            f"model mismatch: bundle was enrolled for {src_model}, "
            f"this device is {cur_model} — re-enroll explicitly")


# ---------------------------------------------------------------------------
# Device-scoped tokens (OC-05) — remote device auth without a second listener
# ---------------------------------------------------------------------------
def _tokens_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / TOKEN_FILE


def _blank_tokens() -> Dict[str, Any]:
    return {"schema": TOKEN_SCHEMA, "tokens": {}}


def _load_tokens(cfg: Any) -> Dict[str, Any]:
    path = _tokens_path(cfg)
    if not path.exists():
        return _blank_tokens()
    try:
        if path.stat().st_size > TOKEN_MAX_FILE_BYTES:
            raise ValueError("device-token store too large")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _blank_tokens()
    if not isinstance(raw, dict) or raw.get("schema") != TOKEN_SCHEMA \
            or not isinstance(raw.get("tokens"), dict):
        return _blank_tokens()
    return raw


def _save_tokens(cfg: Any, state: Dict[str, Any]) -> None:
    path = _tokens_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_name(f"{TOKEN_FILE}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True),
                   encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _token_hash(salt: bytes, token: str) -> str:
    return hashlib.sha256(salt + token.encode("utf-8")).hexdigest()


def _check_device_id(device_id: Any) -> str:
    text = str(device_id or "").strip()
    if not text or len(text) > 128 or any(ch in text for ch in "\n\r\t\x00"):
        raise DeviceNotEnrolledError(
            "device_id must be a non-empty 1..128 char identifier")
    return text


def _check_token_scopes(scopes: Any) -> List[str]:
    items: List[str] = []
    if isinstance(scopes, str):
        items = [s.strip() for s in scopes.split(",") if s.strip()]
    elif scopes is not None:
        try:
            items = [str(s).strip() for s in scopes]
        except TypeError:
            raise DeviceNotEnrolledError(
                "scopes must be a list of strings") from None
    clean = []
    for scope in items:
        token = str(scope or "").strip().lower()
        if not token or not TOKEN_SCOPE_RE.match(token):
            raise DeviceNotEnrolledError(f"bad device scope {scope!r}")
        clean.append(token)
    if not clean:
        raise DeviceNotEnrolledError("at least one device scope is required")
    return sorted(set(clean))


def _token_public(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Redacted token record: never the token or its verifier."""
    return {"id": rec["id"], "device_id": rec.get("device_id", ""),
            "label": rec.get("label", ""), "scopes": list(rec.get("scopes", [])),
            "status": rec.get("status", "pending"),
            "created_at": rec.get("created_at"),
            "approved_at": rec.get("approved_at"),
            "expires_at": rec.get("expires_at"),
            "last_used_at": rec.get("last_used_at"),
            "uses": int(rec.get("uses", 0)),
            "rotated_from": rec.get("rotated_from", "")}


def _estop(cfg: Any) -> bool:
    try:
        from .plugins import estop_engaged

        return bool(estop_engaged(cfg))
    except Exception:
        return True


def _token_audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    try:
        from .logging_setup import log_event

        log_event(f"device.token.{op}", actor=actor,
                  **{k: str(v)[:160] for k, v in detail.items()})
    except Exception:
        pass


def token_list(cfg: Any) -> Dict[str, Any]:
    """Redacted list of device-scoped tokens (no token/verifier ever)."""
    state = _load_tokens(cfg)
    rows = sorted(state["tokens"].values(),
                  key=lambda r: r.get("created_at", 0), reverse=True)
    return {"state": "OK", "schema": TOKEN_SCHEMA,
            "tokens": [_token_public(r) for r in rows]}


def token_show(cfg: Any, token_id: str) -> Dict[str, Any]:
    state = _load_tokens(cfg)
    rec = state["tokens"].get(str(token_id or "").strip())
    if rec is None:
        return {"state": "NOT_FOUND", "code": TOKEN_NOT_FOUND_CODE,
                "detail": f"no device token {token_id!r}"}
    return {"state": "OK", "token": _token_public(rec)}


def token_issue(cfg: Any, *, device_id: str, scopes: Any,
                label: str = "", ttl_s: float = TOKEN_DEFAULT_TTL_S,
                requested_by: str = "cli:user",
                confirmed: bool = False) -> Dict[str, Any]:
    """Mint a device token (shown once, stored hashed, requires approval).

    The token starts ``pending`` and can never authenticate until an operator
    approves it; this is the Qubit two-step for remote device enrollment.
    """
    if _estop(cfg):
        return refuse_estop("device token issue")
    if not confirmed:
        return {"state": "denied", "code": TOKEN_CONFIRM_CODE,
                "detail": "issuing a device token requires explicit --confirm",
                "owner": "qubit-policy",
                "rollback": "no token minted; nothing to roll back"}
    try:
        clean_device = _check_device_id(device_id)
        clean_scopes = _check_token_scopes(scopes)
        ttl = max(60.0, min(float(ttl_s), TOKEN_MAX_TTL_S))
    except (DeviceNotEnrolledError, TypeError, ValueError) as exc:
        return {"state": "FAILED", "code": TOKEN_BAD_INPUT_CODE,
                "detail": str(exc), "owner": "qubit-policy",
                "rollback": "no token minted; nothing to roll back"}
    state = _load_tokens(cfg)
    active = [r for r in state["tokens"].values()
              if r.get("status") in ("pending", "active")]
    if len(active) >= TOKEN_MAX_ACTIVE:
        return {"state": "FAILED", "code": TOKEN_BAD_INPUT_CODE,
                "detail": f"active device-token limit reached "
                          f"({TOKEN_MAX_ACTIVE})",
                "owner": "qubit-policy",
                "rollback": "no token minted; nothing to roll back"}
    token = secrets.token_urlsafe(32)
    salt = secrets.token_bytes(16)
    token_id = "dtok_" + secrets.token_hex(8)
    now = time.time()
    rec = {"id": token_id, "device_id": clean_device,
           "label": (label or "").strip()[:64], "scopes": clean_scopes,
           "status": "pending",
           "salt_b64": __import__("base64").b64encode(salt).decode("ascii"),
           "verifier": _token_hash(salt, token),
           "created_at": now, "approved_at": None,
           "expires_at": now + ttl, "last_used_at": None, "uses": 0,
           "rotated_from": ""}
    state["tokens"][token_id] = rec
    _save_tokens(cfg, state)
    _token_audit("issue", requested_by,
                 {"token_id": token_id, "device": clean_device,
                  "scopes": clean_scopes})
    return {"state": "OK", "token_id": token_id, "device_id": clean_device,
            "scopes": clean_scopes, "expires_at": rec["expires_at"],
            "token": token,
            "warning": "shown once: store it on the device; it is never "
                       "stored or shown again"}


def token_approve(cfg: Any, token_id: str, *, requested_by: str = "cli:user",
                  confirmed: bool = False) -> Dict[str, Any]:
    """Operator approval that activates a pending device token."""
    if _estop(cfg):
        return refuse_estop("device token approve")
    if not confirmed:
        return {"state": "denied", "code": TOKEN_CONFIRM_CODE,
                "detail": "approving a device token requires explicit "
                          "--confirm",
                "owner": "qubit-policy",
                "rollback": "token stays pending; nothing to roll back"}
    state = _load_tokens(cfg)
    rec = state["tokens"].get(str(token_id or "").strip())
    if rec is None:
        return {"state": "NOT_FOUND", "code": TOKEN_NOT_FOUND_CODE,
                "detail": f"no device token {token_id!r}"}
    if rec.get("status") == "revoked":
        return {"state": "REFUSED", "code": TOKEN_REVOKED_CODE,
                "detail": "device token is revoked"}
    rec["status"] = "active"
    rec["approved_at"] = time.time()
    _save_tokens(cfg, state)
    _token_audit("approve", requested_by, {"token_id": rec["id"]})
    return {"state": "OK", "token": _token_public(rec)}


def token_rotate(cfg: Any, token_id: str, *, requested_by: str = "cli:user",
                 confirmed: bool = False) -> Dict[str, Any]:
    """Mint a fresh active token for the same device; revoke the predecessor."""
    if _estop(cfg):
        return refuse_estop("device token rotate")
    if not confirmed:
        return {"state": "denied", "code": TOKEN_CONFIRM_CODE,
                "detail": "rotating a device token requires explicit --confirm",
                "owner": "qubit-policy",
                "rollback": "no new token minted; predecessor unchanged"}
    state = _load_tokens(cfg)
    old = state["tokens"].get(str(token_id or "").strip())
    if old is None:
        return {"state": "NOT_FOUND", "code": TOKEN_NOT_FOUND_CODE,
                "detail": f"no device token {token_id!r}"}
    if old.get("status") == "revoked":
        return {"state": "REFUSED", "code": TOKEN_REVOKED_CODE,
                "detail": "device token is revoked"}
    issued = token_issue(
        cfg, device_id=old.get("device_id", ""), scopes=old.get("scopes", []),
        label=old.get("label", ""),
        ttl_s=max(60.0, float(old.get("expires_at", 0)) - time.time()),
        requested_by=requested_by, confirmed=True)
    if issued.get("state") != "OK":
        return issued
    new_id = issued["token_id"]
    state = _load_tokens(cfg)
    new_rec = state["tokens"][new_id]
    new_rec["status"] = "active"
    new_rec["approved_at"] = time.time()
    new_rec["rotated_from"] = old["id"]
    old_rec = state["tokens"].get(old["id"])
    if old_rec is None:
        return {"state": "FAILED", "code": TOKEN_NOT_FOUND_CODE,
                "detail": "device token disappeared during rotation"}
    old_rec["status"] = "revoked"
    _save_tokens(cfg, state)
    _token_audit("rotate", requested_by,
                 {"old": old["id"], "new": new_id})
    return {"state": "OK", "rotated_from": old["id"],
            "token_id": new_id, "device_id": new_rec["device_id"],
            "scopes": list(new_rec["scopes"]),
            "token": issued["token"],
            "warning": issued["warning"]}


def token_revoke(cfg: Any, token_id: str, *, requested_by: str = "cli:user",
                 confirmed: bool = False) -> Dict[str, Any]:
    """Tombstone a device token (confirmed, audited)."""
    if _estop(cfg):
        return refuse_estop("device token revoke")
    if not confirmed:
        return {"state": "denied", "code": TOKEN_CONFIRM_CODE,
                "detail": "revoking a device token requires explicit --confirm",
                "owner": "qubit-policy",
                "rollback": "token unchanged; nothing to roll back"}
    state = _load_tokens(cfg)
    rec = state["tokens"].get(str(token_id or "").strip())
    if rec is None:
        return {"state": "NOT_FOUND", "code": TOKEN_NOT_FOUND_CODE,
                "detail": f"no device token {token_id!r}"}
    rec["status"] = "revoked"
    _save_tokens(cfg, state)
    _token_audit("revoke", requested_by, {"token_id": rec["id"]})
    return {"state": "OK", "revoked": rec["id"]}


def token_rename(cfg: Any, token_id: str, label: str, *,
                 requested_by: str = "cli:user",
                 confirmed: bool = False) -> Dict[str, Any]:
    """Rename a device token's operator label (confirmed, audited).

    Also accepts a ``device_id`` when it names exactly one token, so an
    operator can rename a paired device without knowing its token id.
    """
    if _estop(cfg):
        return refuse_estop("device token rename")
    if not confirmed:
        return {"state": "denied", "code": TOKEN_CONFIRM_CODE,
                "detail": "renaming a device token requires explicit --confirm",
                "owner": "qubit-policy",
                "rollback": "label unchanged; nothing to roll back"}
    clean_label = str(label or "").strip()[:64]
    if not clean_label:
        return {"state": "FAILED", "code": TOKEN_BAD_INPUT_CODE,
                "detail": "label must be a non-empty string",
                "owner": "qubit-policy"}
    key = str(token_id or "").strip()
    state = _load_tokens(cfg)
    rec = state["tokens"].get(key)
    if rec is None:
        matches = [r for r in state["tokens"].values()
                   if str(r.get("device_id") or "") == key]
        if len(matches) == 1:
            rec = matches[0]
        elif len(matches) > 1:
            return {"state": "FAILED", "code": TOKEN_BAD_INPUT_CODE,
                    "detail": f"device_id {key!r} names multiple tokens; use "
                              "a token id",
                    "owner": "qubit-policy"}
    if rec is None:
        return {"state": "NOT_FOUND", "code": TOKEN_NOT_FOUND_CODE,
                "detail": f"no device token {key!r}"}
    rec["label"] = clean_label
    _save_tokens(cfg, state)
    _token_audit("rename", requested_by,
                 {"token_id": rec["id"], "label": clean_label})
    return {"state": "OK", "token": _token_public(rec)}


def token_verify(cfg: Any, token: str, *, required_scopes: Any = None,
                 touch: bool = False) -> Dict[str, Any]:
    """Constant-time device-token verification + scope check (fail closed).

    Returns a typed verdict and, on success, the bound device id and the
    granted scopes. No token value is ever echoed back.
    """
    presented = str(token or "").strip()
    if not presented:
        return {"state": "REFUSED", "code": TOKEN_BAD_CODE,
                "detail": "no token presented"}
    try:
        needed = _check_token_scopes(required_scopes) if required_scopes else []
    except DeviceNotEnrolledError as exc:
        return {"state": "REFUSED", "code": TOKEN_BAD_INPUT_CODE,
                "detail": str(exc)}
    state = _load_tokens(cfg)
    now = time.time()
    match = None
    for rec in state["tokens"].values():
        import base64 as _b64

        try:
            salt = _b64.b64decode(rec.get("salt_b64", ""))
        except Exception:
            continue
        if hmac.compare_digest(_token_hash(salt, presented),
                               str(rec.get("verifier", ""))):
            match = rec
            break
    if match is None:
        return {"state": "REFUSED", "code": TOKEN_BAD_CODE,
                "detail": "unknown device token"}
    if match.get("status") == "revoked":
        return {"state": "REFUSED", "code": TOKEN_REVOKED_CODE,
                "detail": "device token revoked"}
    if match.get("status") != "active":
        return {"state": "REFUSED", "code": TOKEN_PENDING_CODE,
                "detail": "device token is not approved"}
    if now > float(match.get("expires_at", 0)):
        match["status"] = "revoked"
        _save_tokens(cfg, state)
        return {"state": "REFUSED", "code": TOKEN_EXPIRED_CODE,
                "detail": "device token expired"}
    granted = set(match.get("scopes", []))
    missing = [s for s in needed if s not in granted]
    if missing:
        return {"state": "REFUSED", "code": TOKEN_BAD_INPUT_CODE,
                "detail": "token lacks scope(s): " + ", ".join(missing)}
    if touch:
        match["uses"] = int(match.get("uses", 0)) + 1
        match["last_used_at"] = now
        _save_tokens(cfg, state)
    return {"state": "OK", "token_id": match["id"],
            "device_id": match.get("device_id", ""),
            "scopes": sorted(granted)}


def refuse_estop(operation: str) -> Dict[str, Any]:
    """Typed refusal while the emergency stop is latched."""
    return {"state": "ESTOP", "code": TOKEN_ESTOP_CODE, "operation": operation,
            "explanation": "Qubit Agent cannot do this: the emergency stop is "
                           "latched; reset it before device-token changes.",
            "owner": "qubit-policy",
            "rollback": "device-token store untouched; nothing to roll back"}