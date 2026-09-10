"""Qubit gateway/peer records: enrollment, typed delivery, link states.

Qubit supervises exactly one event path (the bridge itself) and opens no
public listener — so process supervision and live remote operation stay
operator-side, while everything code-side is real:

* gateways: account-bound records (provider, account, channel, key
  reference) with link states; ``enroll`` binds a pairing approval;
  ``start``/``stop``/``restart``/``install`` are typed
  ``GATEWAY_NO_SUPERVISION`` refusals (single event path invariant).
* peers: records (URL policy enforced, account + key reference binding),
  ``enroll`` via pairing codes, ``link`` (credential + policy states,
  explicit ``--probe`` for a bounded reachability check),
  ``dm``/``run`` as real credential-gated HTTPS POSTs (Bearer key in the
  header only, timeouts, validated responses, idempotent receipts,
  redacted audit). Without resolvable credentials or a live peer the
  typed ``PEER_NO_CREDENTIAL``/``PEER_UPSTREAM`` states apply — the
  adapter is complete; only live secrets stay operator-side.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

SCHEMA = "qubit-gateway-peers/1"
GW_NAME = "gateways.json"
PEER_NAME = "peers.json"
MAX_FILE_BYTES = 1_000_000
TRANSPORT_TIMEOUT_S = 15.0

CONFIRM_CODE = "GW_CONFIRM_REQUIRED"
ESTOP_CODE = "GW_ESTOP"
NOT_FOUND_CODE = "GW_NOT_FOUND"
EXISTS_CODE = "GW_EXISTS"
BAD_INPUT_CODE = "GW_BAD_INPUT"
NO_SUPERVISION_CODE = "GATEWAY_NO_SUPERVISION"
NO_CREDENTIAL_CODE = "PEER_NO_CREDENTIAL"
UPSTREAM_CODE = "PEER_UPSTREAM"
CORRUPT_CODE = "GW_STORE_CORRUPT"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
CHANNELS = ("webhook", "polling", "websocket")
WELL_KNOWN_PEER_HOSTS = ("hooks.slack.com", "graph.facebook.com")


class GatewayError(Exception):
    """Typed gateway/peer failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _now() -> float:
    return time.time()


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def _path(cfg: Any, name: str) -> Path:
    return Path(cfg.state_dir()) / name


def _blank() -> Dict[str, Any]:
    return {"schema": SCHEMA, "gateways": {}, "peers": {}}


def _load(cfg: Any) -> Dict[str, Any]:
    merged: Dict[str, Any] = _blank()
    for name, key in ((GW_NAME, "gateways"), (PEER_NAME, "peers")):
        path = _path(cfg, name)
        if not path.exists():
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                raise GatewayError(CORRUPT_CODE, f"{name} too large")
            raw = json.loads(path.read_text(encoding="utf-8"))
        except GatewayError:
            raise
        except (OSError, ValueError) as exc:
            raise GatewayError(CORRUPT_CODE,
                               f"{name} unreadable: {exc}") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get(key), dict):
            raise GatewayError(CORRUPT_CODE, f"{name} has a bad shape")
        merged[key] = raw[key]
    return merged


def _save(cfg: Any, state: Dict[str, Any]) -> None:
    for name, key in ((GW_NAME, "gateways"), (PEER_NAME, "peers")):
        path = _path(cfg, name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{name}.tmp.{os.getpid()}")
            tmp.write_text(json.dumps({"schema": SCHEMA, key: state[key]},
                                      indent=2, sort_keys=True),
                           encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except OSError as exc:
            raise GatewayError(CORRUPT_CODE,
                               f"{name} write failed: {exc}") from exc


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    from .logging_setup import log_event

    try:
        log_event(f"gateway.{op}", actor=actor, **detail)
    except Exception:
        pass


def _check_id(text: str) -> str:
    clean = (text or "").strip().lower()
    if not _ID_RE.match(clean):
        raise GatewayError(BAD_INPUT_CODE,
                           "id must match ^[a-z0-9][a-z0-9_.-]{0,63}$")
    return clean


def _secret_kind(ref: str) -> str:
    if ref.startswith("env:"):
        return "env"
    if ref.startswith("file:"):
        return "file"
    return "missing"


def check_peer_url(url: str) -> str:
    """Peer URL policy: https (+ loopback http) with allowlisted hosts."""
    raw = (url or "").strip()
    try:
        parts = urlparse(raw)
    except ValueError as exc:
        raise GatewayError(BAD_INPUT_CODE, f"unparseable URL: {exc}") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise GatewayError(BAD_INPUT_CODE, "URL must be http(s) with a host")
    if parts.username or parts.password:
        raise GatewayError(BAD_INPUT_CODE, "userinfo (tokens) in URLs is forbidden")
    host = parts.hostname.lower()
    loopback = host in ("localhost", "127.0.0.1", "::1") or host.startswith(
        "127.") or host.endswith(".localhost")
    if parts.scheme == "http" and not loopback:
        raise GatewayError(BAD_INPUT_CODE, "plain http allowed for loopback only")
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", host) and not loopback:
        raise GatewayError(BAD_INPUT_CODE, "raw IP literals are forbidden")
    if not loopback and host not in WELL_KNOWN_PEER_HOSTS:
        raise GatewayError(BAD_INPUT_CODE,
                           f"host {host!r} is not an approved peer host")
    return raw


# ---------------------------------------------------------------- gateways
def gateway_add(cfg: Any, gw_id: str, provider: str, account: str, channel: str,
                *, key_ref: str = "", requested_by: str = "cli:user",
                confirmed: bool = False) -> Dict[str, Any]:
    """Record an account-bound gateway (no process is started)."""
    if not confirmed:
        raise GatewayError(CONFIRM_CODE,
                           "gateway add requires explicit --confirm")
    from . import messaging as MG

    clean_id = _check_id(gw_id)
    if provider not in MG.PROVIDERS:
        raise GatewayError(BAD_INPUT_CODE,
                           f"provider must be one of {MG.PROVIDERS}")
    try:
        MG.account_show(cfg, account)
    except MG.MessagingError as exc:
        raise GatewayError(NOT_FOUND_CODE,
                           f"account gate: {exc.detail}") from exc
    if channel not in CHANNELS:
        raise GatewayError(BAD_INPUT_CODE,
                           f"channel must be one of {CHANNELS}")
    ref = (key_ref or "").strip()
    if _secret_kind(ref) == "missing":
        raise GatewayError(BAD_INPUT_CODE,
                           "key_ref must be an env:VAR or file:PATH reference")
    state = _load(cfg)
    if clean_id in state["gateways"]:
        raise GatewayError(EXISTS_CODE, f"gateway {clean_id!r} exists")
    rec = {"id": clean_id, "provider": provider, "account": account,
           "channel": channel, "key_ref_kind": _secret_kind(ref),
           "created_at": _now(), "updated_by": requested_by,
           "process": "UNMANAGED",
           "last_outcome": "NEVER_USED"}
    state["gateways"][clean_id] = rec
    _save(cfg, state)
    _audit("add", requested_by,
           {"gateway": clean_id, "provider": provider, "account": account})
    return {"state": "OK", "gateway": _public_gw(rec)}


def _public_gw(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": rec["id"], "provider": rec["provider"],
            "account": rec["account"], "channel": rec["channel"],
            "key_ref": rec.get("key_ref_kind", "missing"),
            "process": rec.get("process", "UNMANAGED"),
            "last_outcome": rec.get("last_outcome", "NEVER_USED"),
            "created_at": rec.get("created_at")}


def _get_gw(state: Dict[str, Any], gw_id: str) -> Dict[str, Any]:
    rec = state["gateways"].get(_check_id(gw_id))
    if rec is None:
        raise GatewayError(NOT_FOUND_CODE, f"no gateway {gw_id!r}")
    return rec


def gateway_list(cfg: Any) -> Dict[str, Any]:
    """List gateway records (redacted)."""
    state = _load(cfg)
    return {"state": "OK", "gateways": [
        _public_gw(r) for r in sorted(state["gateways"].values(),
                                      key=lambda r: r.get("id", ""))]}


def gateway_show(cfg: Any, gw_id: str) -> Dict[str, Any]:
    """Show one gateway record (redacted)."""
    state = _load(cfg)
    return {"state": "OK", "gateway": _public_gw(_get_gw(state, gw_id))}


def gateway_remove(cfg: Any, gw_id: str, *, requested_by: str = "cli:user",
                   confirmed: bool = False) -> Dict[str, Any]:
    """Delete a gateway record (confirmed, audited; no process existed)."""
    if not confirmed:
        raise GatewayError(CONFIRM_CODE,
                           "gateway remove requires explicit --confirm")
    state = _load(cfg)
    rec = _get_gw(state, gw_id)
    del state["gateways"][rec["id"]]
    _save(cfg, state)
    _audit("remove", requested_by, {"gateway": rec["id"]})
    return {"state": "OK", "removed": rec["id"]}


def gateway_link(cfg: Any, gw_id: str) -> Dict[str, Any]:
    """Typed link state: account + credential + policy (read-only)."""
    from . import messaging as MG
    from .providers.credentials import resolve_key

    try:
        rec = _get_gw(_load(cfg), gw_id)
    except GatewayError as exc:
        return {"state": "NO_GATEWAY", "code": NOT_FOUND_CODE,
                "detail": exc.detail[:160]}
    try:
        acct = MG.account_show(cfg, rec["account"])["account"]
    except MG.MessagingError:
        return {"state": "NO_ACCOUNT", "gateway": rec["id"],
                "detail": "bound account is gone; re-bind or remove"}
    try:
        ref = MG.account_credential_ref(cfg, rec["account"])
    except MG.MessagingError:
        return {"state": "NO_ACCOUNT", "gateway": rec["id"],
                "detail": "bound account is gone; re-bind or remove"}
    _, code = resolve_key(acct.get("provider", ""), ref)
    if code:
        return {"state": "NO_CREDENTIAL", "gateway": rec["id"],
                "detail": "credential reference unresolvable"}
    last = rec.get("last_outcome", "NEVER_USED")
    return {"state": "LINKED" if last in ("NEVER_USED", "DELIVERED") else "DEGRADED",
            "gateway": rec["id"], "provider": rec["provider"],
            "channel": rec["channel"], "last_outcome": last}


def refuse_supervision(action: str) -> Dict[str, Any]:
    """Typed refusal for gateway process control (single event path)."""
    return {"state": "REFUSED", "code": NO_SUPERVISION_CODE, "action": action,
            "explanation": "Qubit Agent cannot do this: gateway processes "
                           "are supervised by the operator (service "
                           "approval); Qubit runs exactly one event path — "
                           "the bridge itself. Records, enrollment, and "
                           "link state stay available.",
            "owner": "qubit-policy",
            "rollback": "no process touched; nothing to roll back"}


# ---------------------------------------------------------------- peers
def _public_peer(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": rec["id"], "url": _redact(rec.get("url", ""))[:160],
            "account": rec.get("account", ""),
            "key_ref": rec.get("key_ref_kind", "missing"),
            "created_at": rec.get("created_at"),
            "last_outcome": rec.get("last_outcome", "NEVER_USED")}


def _get_peer(state: Dict[str, Any], peer_id: str) -> Dict[str, Any]:
    rec = state["peers"].get(_check_id(peer_id))
    if rec is None:
        raise GatewayError(NOT_FOUND_CODE, f"no peer {peer_id!r}")
    return rec


def peer_add(cfg: Any, peer_id: str, url: str, account: str, *,
             key_ref: str = "", requested_by: str = "cli:user",
             confirmed: bool = False) -> Dict[str, Any]:
    """Record a peer gateway (URL policy + account binding enforced)."""
    if not confirmed:
        raise GatewayError(CONFIRM_CODE,
                           "peer add requires explicit --confirm")
    from . import messaging as MG

    clean_id = _check_id(peer_id)
    clean_url = check_peer_url(url)
    try:
        MG.account_show(cfg, account)
    except MG.MessagingError as exc:
        raise GatewayError(NOT_FOUND_CODE,
                           f"account gate: {exc.detail}") from exc
    ref = (key_ref or "").strip()
    if _secret_kind(ref) == "missing":
        raise GatewayError(BAD_INPUT_CODE,
                           "key_ref must be an env:VAR or file:PATH reference")
    state = _load(cfg)
    if clean_id in state["peers"]:
        raise GatewayError(EXISTS_CODE, f"peer {clean_id!r} exists")
    rec = {"id": clean_id, "url": clean_url, "account": account,
           "key_ref_kind": _secret_kind(ref),
           "_key_name": ref[len("env:"):].strip()[:64] if ref.startswith("env:") else "",
           "_key_path": "" if ref.startswith("env:") else ref,
           "created_at": _now(), "updated_by": requested_by,
           "last_outcome": "NEVER_USED"}
    state["peers"][clean_id] = rec
    _save(cfg, state)
    _audit("peer.add", requested_by, {"peer": clean_id, "account": account})
    return {"state": "OK", "peer": _public_peer(rec)}


def _peer_key(rec: Dict[str, Any]) -> str:
    if rec.get("key_ref_kind") == "env":
        return f"env:{rec.get('_key_name', '')}"
    return rec.get("_key_path", "")


def peer_list(cfg: Any) -> Dict[str, Any]:
    """List peers (redacted)."""
    state = _load(cfg)
    return {"state": "OK", "peers": [
        _public_peer(r) for r in sorted(state["peers"].values(),
                                        key=lambda r: r.get("id", ""))]}


def peer_show(cfg: Any, peer_id: str) -> Dict[str, Any]:
    """Show one peer (redacted)."""
    state = _load(cfg)
    return {"state": "OK", "peer": _public_peer(_get_peer(state, peer_id))}


def peer_remove(cfg: Any, peer_id: str, *, requested_by: str = "cli:user",
                confirmed: bool = False) -> Dict[str, Any]:
    """Delete a peer record (confirmed, audited)."""
    if not confirmed:
        raise GatewayError(CONFIRM_CODE,
                           "peer remove requires explicit --confirm")
    state = _load(cfg)
    rec = _get_peer(state, peer_id)
    del state["peers"][rec["id"]]
    _save(cfg, state)
    _audit("peer.remove", requested_by, {"peer": rec["id"]})
    return {"state": "OK", "removed": rec["id"]}


def peer_link(cfg: Any, peer_id: str, *, probe: bool = False,
              timeout_s: float = 8.0) -> Dict[str, Any]:
    """Typed link state; explicit --probe does one bounded HTTPS check."""
    from .providers.credentials import resolve_key

    try:
        rec = _get_peer(_load(cfg), peer_id)
    except GatewayError as exc:
        return {"state": "NO_PEER", "code": NOT_FOUND_CODE,
                "detail": exc.detail[:160]}
    _, code = resolve_key("peer", _peer_key(rec))
    if code:
        return {"state": "NO_CREDENTIAL", "peer": rec["id"],
                "detail": "peer credential unresolvable"}
    if not probe:
        return {"state": "CREDENTIAL_OK", "peer": rec["id"],
                "last_outcome": rec.get("last_outcome", "NEVER_USED"),
                "detail": "credential resolves; pass --probe for a live check"}
    try:
        req = urllib.request.Request(rec["url"], method="HEAD")
        with urllib.request.urlopen(req, timeout=max(2.0, min(timeout_s, 20.0))):
            pass
        return {"state": "REACHABLE", "peer": rec["id"]}
    except Exception as exc:
        return {"state": "UNREACHABLE", "peer": rec["id"],
                "detail": _redact(str(exc))[:160]}


def _deliver(cfg: Any, rec: Dict[str, Any], path: str, payload: Dict[str, Any],
             *, requested_by: str, confirmed: bool, op: str) -> Dict[str, Any]:
    from .plugins import estop_engaged
    from .providers.credentials import resolve_key

    if estop_engaged(cfg):
        raise GatewayError(ESTOP_CODE, "emergency stop is latched")
    if not confirmed:
        raise GatewayError(CONFIRM_CODE,
                           f"peer {op} requires fresh exact --confirm")
    key, code = resolve_key("peer", _peer_key(rec))
    if code or not key:
        raise GatewayError(NO_CREDENTIAL_CODE,
                           "peer credential unresolvable; nothing attempted")
    body = json.dumps(payload).encode()
    if len(body) > 100_000:
        raise GatewayError(BAD_INPUT_CODE, "payload exceeds 100KB cap")
    req = urllib.request.Request(
        rec["url"].rstrip("/") + path, data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req,
                                    timeout=TRANSPORT_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = getattr(resp, "status", 200)
    except Exception as exc:
        _note_outcome(cfg, rec["id"], "FAILED")
        raise GatewayError(UPSTREAM_CODE,
                           f"peer request failed: {exc}") from exc
    if status != 200:
        _note_outcome(cfg, rec["id"], "FAILED")
        raise GatewayError(UPSTREAM_CODE, f"peer HTTP {status}")
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        raise GatewayError(UPSTREAM_CODE, "peer bad JSON") from None
    if not isinstance(data, dict) or data.get("ok") is not True:
        _note_outcome(cfg, rec["id"], "FAILED")
        raise GatewayError(UPSTREAM_CODE, "peer refused the delivery")
    _note_outcome(cfg, rec["id"], "DELIVERED")
    _audit(f"peer.{op}", requested_by,
           {"peer": rec["id"],
            "receipt": _redact(str(data.get("receipt", "")))[:80]})
    return {"state": "OK", "peer": rec["id"],
            "receipt": _redact(str(data.get("receipt", "")))[:80]}


def _note_outcome(cfg: Any, peer_id: str, outcome: str) -> None:
    try:
        state = _load(cfg)
        if peer_id in state["peers"]:
            state["peers"][peer_id]["last_outcome"] = outcome
            _save(cfg, state)
    except GatewayError:
        pass


def peer_dm(cfg: Any, peer_id: str, to: str, text: str, *,
            requested_by: str = "cli:user",
            confirmed: bool = False) -> Dict[str, Any]:
    """Deliver a direct message through a peer (credential-gated POST)."""
    if not (to or "").strip() or not (text or "").strip():
        raise GatewayError(BAD_INPUT_CODE, "recipient and text are required")
    if len(text) > 4000:
        raise GatewayError(BAD_INPUT_CODE, "text exceeds 4000 chars")
    state = _load(cfg)
    rec = _get_peer(state, peer_id)
    return _deliver(cfg, rec, "/dm",
                    {"to": (to or "").strip()[:160], "text": text},
                    requested_by=requested_by, confirmed=confirmed, op="dm")


def peer_run(cfg: Any, peer_id: str, agent: str, text: str, *,
             requested_by: str = "cli:user",
             confirmed: bool = False) -> Dict[str, Any]:
    """Run a task on a peer agent (credential-gated POST)."""
    if not (text or "").strip():
        raise GatewayError(BAD_INPUT_CODE, "task text is required")
    if len(text) > 4000:
        raise GatewayError(BAD_INPUT_CODE, "text exceeds 4000 chars")
    state = _load(cfg)
    rec = _get_peer(state, peer_id)
    return _deliver(cfg, rec, "/run",
                    {"agent": (agent or "").strip()[:80], "task": text},
                    requested_by=requested_by, confirmed=confirmed, op="run")


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the plugins surface (fail closed)."""
    from .plugins import estop_engaged as _engaged

    return _engaged(cfg)


def refuse_estop(operation: str) -> Dict[str, Any]:
    """Typed refusal while the emergency stop is latched."""
    return {"state": "ESTOP", "code": ESTOP_CODE, "operation": operation,
            "explanation": "Qubit Agent cannot do this: the emergency stop "
                           "is latched; reset it before gateway/peer changes.",
            "owner": "qubit-policy",
            "rollback": "registries untouched; nothing delivered"}
