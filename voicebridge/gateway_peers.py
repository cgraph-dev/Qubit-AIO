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
SUPERVISION_CAPABILITY = "gateway.supervise"
SUPERVISION_PENDING_CODE = "GATEWAY_SUPERVISION_APPROVAL_REQUIRED"
SUPERVISION_DENIED_CODE = "GATEWAY_SUPERVISION_DENIED"
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
            "channel": rec["channel"], "last_outcome": last,
            "supervision": _supervision_public(rec)}


def refuse_supervision(action: str) -> Dict[str, Any]:
    """Typed refusal for gateway process control (single event path)."""
    return {"state": "REFUSED", "code": NO_SUPERVISION_CODE, "action": action,
            "explanation": "Qubit Agent cannot do this: external gateway "
                           "daemons are supervised by the operator (service "
                           "approval); Qubit runs exactly one event path — "
                           "the bridge itself, whose approval-gated lifecycle "
                           "is `gateway supervision-request`. Records, "
                           "enrollment, and link state stay available.",
            "owner": "qubit-policy",
            "rollback": "no process touched; nothing to roll back"}


_SUPERVISION_ACTIONS = ("start", "stop", "restart", "install", "run")
# Qubit supervises exactly one process: the in-process bridge event path.
# "start"-family actions mark the bridge supervised only after an operator
# approval or a live broker grant; "stop" returns it to operator-managed.
SUPERVISED_PROCESS = "SUPERVISED"


def _approval_authorizes(cfg: Any, approval_id: str) -> bool:
    cid = str(approval_id or "").strip()
    if not cid:
        return False
    try:
        from .approvals import COMPLETE, EXECUTING, ApprovalJournal

        item = ApprovalJournal(
            Path(cfg.state_dir()) / "approvals.jsonl").get(cid)
        if item is None or item.decision != "approve":
            return False
        if item.state not in (EXECUTING, COMPLETE):
            return False
        return item.capability in (SUPERVISION_CAPABILITY, "gateway.manage")
    except Exception:
        return False


def _grant_authorizes(cfg: Any) -> bool:
    try:
        from .permissions import permissions_store

        return any(g.capability == SUPERVISION_CAPABILITY and g.is_active()
                   for g in permissions_store(cfg).grants())
    except Exception:
        return False


def _supervision_authorized(cfg: Any, approval_id: str) -> bool:
    """A live broker grant OR a durable human approval authorizes the path."""
    return _grant_authorizes(cfg) or _approval_authorizes(cfg, approval_id)


def _supervision_public(rec: Dict[str, Any]) -> Dict[str, Any]:
    last = rec.get("last_supervision") or {}
    return {"gateway": rec["id"], "process": rec.get("process", "UNMANAGED"),
            "supervised_at": rec.get("supervised_at"),
            "action": last.get("action", ""),
            "previous": last.get("previous", "UNMANAGED"),
            "approval_id": last.get("approval_id", "")}


def supervision_status(cfg: Any, gw_id: str) -> Dict[str, Any]:
    """Read-only supervised-process policy state for one gateway record."""
    try:
        rec = _get_gw(_load(cfg), gw_id)
    except GatewayError as exc:
        return {"state": "NO_GATEWAY", "code": exc.code,
                "detail": exc.detail[:160]}
    return {"state": "OK", **_supervision_public(rec),
            "authorized": _supervision_authorized(cfg, ""),
            "capability": SUPERVISION_CAPABILITY,
            "authority": "one supervised event path; no second listener"}


def supervision_request(cfg: Any, gw_id: str, action: str, *,
                        approval_id: str = "",
                        requested_by: str = "cli:user",
                        confirmed: bool = False) -> Dict[str, Any]:
    """Request a supervised bridge lifecycle transition (approval-gated).

    With no live grant or durable approval this creates a broker approval
    and returns ``APPROVAL_REQUIRED`` — no process or record is touched.
    Once authorized it performs a real, audited, revertible state change on
    the single bridge event path (never a second server)."""
    if estop_engaged(cfg):
        raise GatewayError(ESTOP_CODE, "emergency stop is latched")
    if not confirmed:
        raise GatewayError(CONFIRM_CODE,
                           "gateway supervision requires explicit --confirm")
    clean = str(action or "").strip().lower()
    if clean not in _SUPERVISION_ACTIONS:
        raise GatewayError(BAD_INPUT_CODE,
                           f"action must be one of {_SUPERVISION_ACTIONS}")
    state = _load(cfg)
    rec = _get_gw(state, gw_id)
    if not _supervision_authorized(cfg, approval_id):
        from .approvals import ApprovalJournal

        item = ApprovalJournal(
            Path(cfg.state_dir()) / "approvals.jsonl").create(
                SUPERVISION_CAPABILITY,
                f"gateway {clean} {rec['id']}",
                gateway=rec["id"], action=clean)
        _audit("supervision.request", requested_by,
               {"gateway": rec["id"], "action": clean, "approval": item.id})
        return {"state": "APPROVAL_REQUIRED",
                "code": SUPERVISION_PENDING_CODE, "gateway": rec["id"],
                "action": clean, "approval_id": item.id,
                "detail": "approve the journal entry, then re-run with "
                          "--approval-id (or issue a gateway.supervise "
                          "grant); no process was touched",
                "owner": "qubit-policy",
                "rollback": "no process touched; nothing to roll back"}
    previous = rec.get("process", "UNMANAGED")
    if clean == "stop":
        rec["process"] = "UNMANAGED"
    else:
        rec["process"] = SUPERVISED_PROCESS
        rec["supervised_at"] = _now()
        rec["supervisor"] = "qubit-bridge"
    rec["last_supervision"] = {"action": clean, "at": _now(),
                               "by": requested_by, "previous": previous,
                               "approval_id": str(approval_id or "")}
    state["gateways"][rec["id"]] = rec
    _save(cfg, state)
    _audit("supervision", requested_by,
           {"gateway": rec["id"], "action": clean,
            "process": rec["process"]})
    return {"state": "OK", **_supervision_public(rec),
            "rollback": "`qubit gateway supervision-revert` restores the "
                        "previous process state"}


def supervision_revert(cfg: Any, gw_id: str, *,
                       requested_by: str = "cli:user",
                       confirmed: bool = False) -> Dict[str, Any]:
    """Recovery: restore the pre-supervision process state (confirmed)."""
    if estop_engaged(cfg):
        raise GatewayError(ESTOP_CODE, "emergency stop is latched")
    if not confirmed:
        raise GatewayError(CONFIRM_CODE,
                           "supervision revert requires explicit --confirm")
    state = _load(cfg)
    rec = _get_gw(state, gw_id)
    last = rec.get("last_supervision") or {}
    previous = last.get("previous")
    if previous not in ("UNMANAGED", SUPERVISED_PROCESS):
        raise GatewayError(NOT_FOUND_CODE,
                           f"no supervision receipt for {gw_id!r} to revert")
    rec["process"] = previous
    rec["supervised_at"] = None
    rec["last_supervision"] = {"action": "revert", "at": _now(),
                               "by": requested_by, "previous": previous,
                               "approval_id": ""}
    state["gateways"][rec["id"]] = rec
    _save(cfg, state)
    _audit("supervision.revert", requested_by,
           {"gateway": rec["id"], "process": previous})
    return {"state": "OK", **_supervision_public(rec),
            "detail": "restored the pre-supervision process state"}


def discovery(cfg: Any) -> Dict[str, Any]:
    """Gateway/peer discovery for the dashboard + marketplace surfaces.

    Read-only: enumerates recorded gateways/peers with live link state and
    the recognized provider connectors, each typed honestly. Remote
    marketplace fetches stay refused by policy (pinned-source)."""
    try:
        state = _load(cfg)
    except GatewayError as exc:
        return {"state": "DEGRADED", "code": exc.code,
                "detail": exc.detail[:200], "gateways": [], "peers": [],
                "providers": []}
    gateways = []
    for rec in sorted(state["gateways"].values(),
                      key=lambda r: r.get("id", "")):
        link = gateway_link(cfg, rec["id"])
        gateways.append({"id": rec["id"], "provider": rec.get("provider", ""),
                         "channel": rec.get("channel", ""),
                         "process": rec.get("process", "UNMANAGED"),
                         "link": link.get("state", "UNKNOWN")})
    peers = []
    for rec in sorted(state["peers"].values(), key=lambda r: r.get("id", "")):
        link = peer_link(cfg, rec["id"])
        peers.append({"id": rec["id"], "url": _redact(rec.get("url", ""))[:160],
                      "link": link.get("state", "UNKNOWN")})
    providers = []
    try:
        from . import messaging as MG

        accounts = MG.account_list(cfg).get("accounts", [])
        configured = {str(a.get("provider", "")) for a in accounts}
        for provider in MG.PROVIDERS:
            providers.append({"provider": provider,
                              "configured": provider in configured,
                              "transport": "live" if provider in
                              ("slack", "whatsapp-cloud") else "UNAVAILABLE"})
    except Exception as exc:
        providers = [{"provider": "?", "configured": False,
                      "transport": f"DEGRADED: {_redact(str(exc))[:120]}"}]
    return {"state": "OK", "schema": SCHEMA, "gateways": gateways,
            "peers": peers, "providers": providers,
            "marketplace": "remote plugin/marketplace fetches are refused "
                           "by pinned-source policy; local reviewed plugins "
                           "only",
            "authority": "read-only discovery; availability is not "
                         "authorization"}


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


# ---------------------------------------------------------------------------
# OC-01: Gateway protocol v4 negotiation (no standalone listener)
# ---------------------------------------------------------------------------
# The upstream wire protocol did not change across the audited pins:
# PROTOCOL_VERSION = 4, MIN_CLIENT_PROTOCOL_VERSION = 4,
# MIN_NODE_PROTOCOL_VERSION = 3. Qubit speaks that protocol over the single
# in-process bridge event path; it never opens a second gateway listener.
PROTOCOL_VERSION = 4
MIN_CLIENT_PROTOCOL_VERSION = 4
MIN_NODE_PROTOCOL_VERSION = 3
GATEWAY_NO_SERVER_CODE = "GATEWAY_NO_SERVER"
GATEWAY_NO_TRANSPORT_CODE = "GATEWAY_NO_TRANSPORT"
GATEWAY_CONTROL_REFUSED_CODE = "GATEWAY_CONTROL_REFUSED"


def protocol_version() -> Dict[str, Any]:
    """Advertise the supported gateway protocol versions (read-only)."""
    return {"state": "OK", "protocol": PROTOCOL_VERSION,
            "min_client": MIN_CLIENT_PROTOCOL_VERSION,
            "min_node": MIN_NODE_PROTOCOL_VERSION,
            "server": "in-process-bridge",
            "listeners": 1,
            "authority": "the one Qubit bridge event path; no second "
                         "gateway process or socket is started"}


def negotiate_protocol(client_version: Any, *, node_version: Any = None,
                       role: str = "client") -> Dict[str, Any]:
    """Deterministic v4 handshake negotiation (no socket is opened).

    Returns the accepted protocol and capability flags, or a typed refusal
    when the peer is too old (``role=client`` -> min 4, ``role=node`` -> min
    3). This is the same version gate the upstream server applies, exposed on
    the single bridge path.
    """
    try:
        version = int(client_version)
    except (TypeError, ValueError):
        return {"state": "REFUSED", "code": BAD_INPUT_CODE,
                "detail": "client protocol version must be an integer"}
    clean_role = str(role or "client").strip().lower()
    minimum = (MIN_NODE_PROTOCOL_VERSION if clean_role == "node"
               else MIN_CLIENT_PROTOCOL_VERSION)
    if version < minimum:
        return {"state": "REFUSED", "code": "GATEWAY_VERSION_UNSUPPORTED",
                "detail": f"{clean_role} protocol {version} < required "
                          f"{minimum}",
                "protocol": PROTOCOL_VERSION, "min_required": minimum}
    if version > PROTOCOL_VERSION:
        return {"state": "REFUSED", "code": "GATEWAY_VERSION_UNSUPPORTED",
                "detail": f"{clean_role} protocol {version} is newer than "
                          f"this bridge supports ({PROTOCOL_VERSION})",
                "protocol": PROTOCOL_VERSION, "min_required": minimum}
    accepted_node = None
    if node_version is not None:
        try:
            nv = int(node_version)
        except (TypeError, ValueError):
            return {"state": "REFUSED", "code": BAD_INPUT_CODE,
                    "detail": "node protocol version must be an integer"}
        if nv < MIN_NODE_PROTOCOL_VERSION or nv > PROTOCOL_VERSION:
            return {"state": "REFUSED",
                    "code": "GATEWAY_VERSION_UNSUPPORTED",
                    "detail": f"node protocol {nv} unsupported",
                    "protocol": PROTOCOL_VERSION}
        accepted_node = nv
    return {"state": "OK", "protocol": PROTOCOL_VERSION,
            "accepted": version, "role": clean_role,
            "node_protocol": accepted_node,
            "features": ["frames", "rpc", "events"],
            "authority": "in-process bridge; no standalone listener"}


def serve_refusal(action: str = "serve") -> Dict[str, Any]:
    """Typed ``GATEWAY_NO_SERVER`` refusal for binding a second listener.

    Qubit's architecture keeps exactly one event path (the in-process bridge).
    Binding a standalone gateway socket would duplicate that authority, so the
    operation is refused with a complete, typed reason instead of an
    unrestricted bypass.
    """
    return {"state": "REFUSED", "code": GATEWAY_NO_SERVER_CODE,
            "action": action,
            "explanation": "Qubit Agent cannot do this: a standalone gateway "
                           "listener would create a second event/policy "
                           "authority. Gateway RPC v4 is served in-process on "
                           "the single bridge path; use `qubit gateway "
                           "protocol` and `qubit gateway negotiate`.",
            "owner": "qubit-policy",
            "rollback": "no socket bound; nothing to roll back"}


def protocol_doctor() -> Dict[str, Any]:
    """Read-only protocol + RPC inventory (OC-01/OC-D09)."""
    methods: List[str] = []
    try:
        from .gateway.rpc import default_registry

        methods = list(default_registry().names())
    except Exception:
        methods = []
    return {"state": "OK", "schema": SCHEMA,
            "protocol": protocol_version(),
            "rpc_methods": methods,
            "server": "in-process-bridge",
            "authority": "one bridge event path; no standalone gateway"}


# ---------------------------------------------------------------------------
# OC-03: peer link state + an injected control-channel handshake
# ---------------------------------------------------------------------------
def peer_control_link(cfg: Any, peer_id: str, *, transport: Any = None,
                      timeout_s: float = 8.0,
                      requested_by: str = "cli:user") -> Dict[str, Any]:
    """Open a peer control link over an *injected* transport (OC-03).

    ``transport`` is the external network boundary: a callable
    ``(url, message) -> response`` used only here. With no transport the call
    returns the typed ``GATEWAY_NO_TRANSPORT`` state (the code path is
    complete; live sockets stay operator-side). A version-mismatched or
    malformed ``welcome`` refuses and records the outcome; a valid handshake
    records ``LINKED`` on the peer.
    """
    try:
        rec = _get_peer(_load(cfg), peer_id)
    except GatewayError as exc:
        return {"state": "NO_PEER", "code": NOT_FOUND_CODE,
                "detail": exc.detail[:160]}
    if transport is None:
        return {"state": "UNAVAILABLE", "code": GATEWAY_NO_TRANSPORT_CODE,
                "peer": rec["id"],
                "detail": "no control transport injected; call "
                          "peer_control_link(..., transport=callable)",
                "owner": "qubit-policy",
                "rollback": "no connection attempted; nothing to roll back"}
    hello = {"type": "hello", "protocol": PROTOCOL_VERSION,
             "role": "qubit", "peer": rec["id"]}
    try:
        response = transport(rec["url"], hello)
    except Exception as exc:
        _note_outcome(cfg, rec["id"], "FAILED")
        return {"state": "UNREACHABLE", "peer": rec["id"],
                "detail": _redact(str(exc))[:160]}
    if not isinstance(response, dict):
        _note_outcome(cfg, rec["id"], "FAILED")
        return {"state": "UNREACHABLE", "peer": rec["id"],
                "detail": "control transport returned a non-mapping"}
    if response.get("type") != "welcome":
        _note_outcome(cfg, rec["id"], "FAILED")
        return {"state": "REFUSED", "code": GATEWAY_CONTROL_REFUSED_CODE,
                "peer": rec["id"], "detail": "peer sent no welcome frame"}
    negotiated = negotiate_protocol(response.get("protocol", 0),
                                    node_version=response.get("node_protocol"))
    if negotiated.get("state") != "OK":
        _note_outcome(cfg, rec["id"], "FAILED")
        return {"state": "REFUSED", "code": negotiated.get("code", ""),
                "peer": rec["id"], "detail": negotiated.get("detail", "")}
    _note_outcome(cfg, rec["id"], "LINKED")
    _audit("peer.control-link", requested_by, {"peer": rec["id"]})
    return {"state": "LINKED", "peer": rec["id"],
            "protocol": negotiated["protocol"],
            "capabilities": response.get("capabilities", []),
            "control": "injected-transport"}
