"""Qubit webhooks via verified-only subscriptions.

Qubit ``webhook {subscribe,add,list,ls,remove,rm,test}`` manages
event-driven agent subscriptions. Qubit runs no public listener and
auto-executes nothing — so this module provides the verification kernel
plus a subscription registry, both fully hermetic:

* ``add`` — record a subscription (URL + events + credential reference
  for the signing secret). URL policy: loopback ``http(s)`` or ``https``
  with an allowlisted host; userinfo, non-loopback ``http``, and raw IPs
  outside loopback are refused (SSRF/token-in-URL rules). The secret is
  an ``env:``/``file:`` reference (never a value). Explicit ``--confirm``.
* ``list``/``show``/``remove`` — registry operations (redacted; secrets
  render kind-only).
* ``verify`` — real HMAC-SHA256 verification of an inbound delivery:
  signature format, timestamp tolerance (±300 s), nonce replay store
  (bounded, TTL-pruned), secret resolution (values never logged). Returns
  ``VALID`` or a typed failure; verified deliveries are recorded as
  redacted receipts — nothing is executed, dispatched, or stored beyond
  the receipt.
* ``test`` — dry-run delivery plan for a subscription (what WOULD be
  checked, no secret resolution, no network).

Replay nonces live in ``state/webhook-nonces.json`` (bounded 5000,
1-hour TTL, pruned on write). Receipts live in ``state/webhook-log.jsonl``
(bounded tail, redacted).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

SCHEMA = "qubit-webhooks/1"
STORE_NAME = "webhooks.json"
NONCE_NAME = "webhook-nonces.json"
LOG_NAME = "webhook-log.jsonl"
MAX_SUBS = 64
MAX_NONCES = 5000
NONCE_TTL_S = 3600.0
TS_TOLERANCE_S = 300.0
MAX_FILE_BYTES = 1_000_000
MAX_BODY_BYTES = 1_000_000
MAX_LOG_LINES = 2000

CONFIRM_CODE = "WEBHOOK_CONFIRM_REQUIRED"
ESTOP_CODE = "WEBHOOK_ESTOP"
NOT_FOUND_CODE = "WEBHOOK_NOT_FOUND"
EXISTS_CODE = "WEBHOOK_EXISTS"
BAD_URL_CODE = "WEBHOOK_BAD_URL"
BAD_INPUT_CODE = "WEBHOOK_BAD_INPUT"
BAD_SIGNATURE_CODE = "WEBHOOK_BAD_SIGNATURE"
STALE_CODE = "WEBHOOK_STALE"
REPLAY_CODE = "WEBHOOK_REPLAY"
AUTH_CODE = "WEBHOOK_AUTH_MISSING"
TOO_LARGE_CODE = "WEBHOOK_TOO_LARGE"
CORRUPT_CODE = "WEBHOOK_STORE_CORRUPT"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_EVENT_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")

# Accepted inbound signature/timestamp/delivery-id header names (case
# insensitive). The signature is hex HMAC-SHA256 over ``f"{timestamp}.{body}"``
# and may carry a ``sha256=`` prefix (GitHub-style).
SIGNATURE_HEADERS = ("X-Qubit-Signature", "X-Hub-Signature-256", "X-Signature")
TIMESTAMP_HEADERS = ("X-Qubit-Timestamp", "X-Timestamp")
DELIVERY_HEADERS = ("X-Qubit-Delivery", "X-Delivery-Id", "X-Request-Id")

# HTTPS hosts Qubit will deliver webhook checks to without extra config.
# Anything else needs an explicit config allowlist entry (documented in the
# refusal); loopback http(s) is always allowed for hermetic use.
WELL_KNOWN_HOSTS = ("hooks.slack.com", "graph.facebook.com")


class WebhookError(Exception):
    """Typed webhook failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def _now() -> float:
    return time.time()


def _store_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / STORE_NAME


def _blank() -> Dict[str, Any]:
    return {"schema": SCHEMA, "subscriptions": {}}


def _load(cfg: Any) -> Dict[str, Any]:
    path = _store_path(cfg)
    if not path.exists():
        return _blank()
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise WebhookError(CORRUPT_CODE, "webhook store too large")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except WebhookError:
        raise
    except (OSError, ValueError) as exc:
        raise WebhookError(CORRUPT_CODE,
                           f"webhook store unreadable: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA \
            or not isinstance(raw.get("subscriptions"), dict):
        raise WebhookError(CORRUPT_CODE, "webhook store has an unknown schema")
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
        raise WebhookError(CORRUPT_CODE,
                           f"webhook store write failed: {exc}") from exc


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    from .logging_setup import log_event

    try:
        log_event(f"webhook.{op}", actor=actor, **detail)
    except Exception:
        pass


def _secret_kind(ref: str) -> str:
    if ref.startswith("env:"):
        return "env"
    if ref.startswith("file:"):
        return "file"
    return "missing"


def check_url(url: str, extra_hosts: List[str]) -> str:
    """Validate a subscription/delivery URL (SSRF + token-in-URL rules)."""
    raw = (url or "").strip()
    try:
        parts = urlparse(raw)
    except ValueError as exc:
        raise WebhookError(BAD_URL_CODE, f"unparseable URL: {exc}") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise WebhookError(BAD_URL_CODE, "URL must be http(s) with a host")
    if parts.username or parts.password:
        raise WebhookError(BAD_URL_CODE,
                           "userinfo (tokens) in URLs is forbidden")
    host = parts.hostname.lower()
    loopback = host in ("localhost", "127.0.0.1", "::1") or host.startswith(
        "127.") or host.endswith(".localhost")
    if parts.scheme == "http" and not loopback:
        raise WebhookError(BAD_URL_CODE,
                           "plain http allowed for loopback only")
    if not loopback and host not in WELL_KNOWN_HOSTS \
            and host not in {h.lower() for h in extra_hosts}:
        raise WebhookError(BAD_URL_CODE,
                           f"host {host!r} is not allowlisted for webhook "
                           f"delivery")
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", host) and not loopback:
        raise WebhookError(BAD_URL_CODE, "raw IP literals are forbidden")
    return raw


def _check_events(events: List[str]) -> List[str]:
    clean = []
    for event in events or []:
        text = str(event or "").strip().lower()
        if not _EVENT_RE.match(text):
            raise WebhookError(BAD_INPUT_CODE, f"bad event {event!r}")
        clean.append(text)
    if not clean:
        raise WebhookError(BAD_INPUT_CODE, "at least one event is required")
    return sorted(set(clean))[:32]


def _public(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": rec["id"], "url": _redact(rec.get("url", ""))[:160],
            "events": list(rec.get("events", [])),
            "secret": _redact_secret_kind(rec.get("secret_ref", "")),
            "enabled": bool(rec.get("enabled", True)),
            "created_at": rec.get("created_at"),
            "updated_by": rec.get("updated_by")}


def _redact_secret_kind(ref: str) -> str:
    kind = _secret_kind(ref)
    return kind if kind == "missing" else f"{kind}:<reference>"


def _get(state: Dict[str, Any], sub_id: str) -> Dict[str, Any]:
    rec = state["subscriptions"].get((sub_id or "").strip().lower())
    if rec is None:
        raise WebhookError(NOT_FOUND_CODE, f"no subscription {sub_id!r}")
    return rec


# ---------------------------------------------------------------- registry
def add(cfg: Any, sub_id: str, url: str, events: List[str], secret_ref: str,
        *, extra_hosts: Optional[List[str]] = None,
        requested_by: str = "cli:user",
        confirmed: bool = False) -> Dict[str, Any]:
    """Record a subscription (URL policy + reference-only secret)."""
    if not confirmed:
        raise WebhookError(CONFIRM_CODE,
                           "webhook add requires explicit --confirm")
    clean_id = (sub_id or "").strip().lower()
    if not _ID_RE.match(clean_id):
        raise WebhookError(BAD_INPUT_CODE,
                           "id must match ^[a-z0-9][a-z0-9_.-]{0,63}$")
    clean_url = check_url(url, extra_hosts or [])
    clean_events = _check_events(events)
    ref = (secret_ref or "").strip()
    if _secret_kind(ref) == "missing":
        raise WebhookError(BAD_INPUT_CODE,
                           "secret must be an env:VAR or file:PATH reference")
    state = _load(cfg)
    if len(state["subscriptions"]) >= MAX_SUBS:
        raise WebhookError(EXISTS_CODE, f"subscription limit ({MAX_SUBS})")
    if clean_id in state["subscriptions"]:
        raise WebhookError(EXISTS_CODE, f"subscription {clean_id!r} exists")
    rec = {"id": clean_id, "url": clean_url, "events": clean_events,
           "secret_ref": ref, "created_at": _now(), "updated_by": requested_by}
    state["subscriptions"][clean_id] = rec
    _save(cfg, state)
    _audit("add", requested_by, {"id": clean_id, "events": clean_events})
    return {"state": "OK", "subscription": _public(rec)}


def list_subs(cfg: Any) -> Dict[str, Any]:
    """List subscriptions (redacted)."""
    state = _load(cfg)
    recs = sorted(state["subscriptions"].values(), key=lambda r: r.get("id", ""))
    return {"state": "OK", "subscriptions": [_public(r) for r in recs]}


def show(cfg: Any, sub_id: str) -> Dict[str, Any]:
    """Show one subscription (redacted)."""
    state = _load(cfg)
    return {"state": "OK", "subscription": _public(_get(state, sub_id))}


def remove(cfg: Any, sub_id: str, *, requested_by: str = "cli:user",
           confirmed: bool = False) -> Dict[str, Any]:
    """Delete a subscription (confirmed, audited)."""
    if not confirmed:
        raise WebhookError(CONFIRM_CODE,
                           "webhook remove requires explicit --confirm")
    state = _load(cfg)
    rec = _get(state, sub_id)
    del state["subscriptions"][rec["id"]]
    _save(cfg, state)
    _audit("remove", requested_by, {"id": rec["id"]})
    return {"state": "OK", "removed": rec["id"]}


def set_enabled(cfg: Any, enabled: bool, *, requested_by: str = "cli:user",
                confirmed: bool = False) -> Dict[str, Any]:
    """Toggle store-wide webhook delivery (confirmed, audited)."""
    if not confirmed:
        raise WebhookError(CONFIRM_CODE,
                           "webhook enable toggle requires explicit --confirm")
    state = _load(cfg)
    state["enabled"] = bool(enabled)
    _save(cfg, state)
    _audit("set_enabled", requested_by, {"enabled": bool(enabled)})
    return {"state": "OK", "enabled": bool(enabled)}


def set_sub_enabled(cfg: Any, sub_id: str, enabled: bool, *,
                    requested_by: str = "cli:user",
                    confirmed: bool = False) -> Dict[str, Any]:
    """Toggle one subscription (confirmed, audited)."""
    if not confirmed:
        raise WebhookError(CONFIRM_CODE,
                           "webhook subscription toggle requires explicit "
                           "--confirm")
    state = _load(cfg)
    rec = _get(state, sub_id)
    rec["enabled"] = bool(enabled)
    _save(cfg, state)
    _audit("set_sub_enabled", requested_by,
           {"id": rec["id"], "enabled": bool(enabled)})
    return {"state": "OK", "id": rec["id"], "enabled": bool(enabled)}


def is_enabled(cfg: Any) -> bool:
    return bool(_load(cfg).get("enabled", True))


def test_plan(cfg: Any, sub_id: str) -> Dict[str, Any]:
    """Dry-run delivery plan (no secret resolution, no network)."""
    state = _load(cfg)
    rec = _get(state, sub_id)
    return {"state": "OK", "subscription": _public(rec),
            "would_check": ["URL policy (already enforced at add)",
                            "HMAC-SHA256 over timestamp.body",
                            f"timestamp tolerance ±{TS_TOLERANCE_S:.0f}s",
                            "nonce replay store",
                            "secret reference resolution (kind only here)"],
            "would_execute": "nothing: verified deliveries record receipts only",
            "note": "dry-run: no secret resolved, no network touched"}


# ------------------------------------------------- delivery maintenance
# The reference dashboard's webhook surface is create/list/enable/delete
# (``hermes_cli/web_routers/ops.py:132``); its store also carries the per-route
# signing secret. Qubit keeps only a reference (never a value), so the
# maintenance operations below are the Qubit-native realizations of the same
# capability: rotate the signing secret to a fresh Qubit-owned ``file:``
# reference, send a real signed test delivery, read the delivery receipts, and
# retry the last failed delivery. Every mutation is confirm-gated + audited and
# every delivery is a real HMAC-signed HTTP request (never a fabricated
# "delivered").
SECRET_DIR = "webhook-secrets"
DELIVERY_TIMEOUT_S = 8.0


def _secret_dir(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / SECRET_DIR


def _deliver(cfg: Any, rec: Dict[str, Any], *, body: Dict[str, Any], event: str,
             requested_by: str, timeout_s: float = DELIVERY_TIMEOUT_S
             ) -> Dict[str, Any]:
    """Send one real HMAC-SHA256-signed delivery; record a redacted receipt."""
    from .providers.credentials import resolve_key

    key, code = resolve_key("webhook", rec.get("secret_ref", ""))
    if code or not key:
        raise WebhookError(AUTH_CODE,
                           "signing secret unresolvable (check the env:/file: "
                           "reference)")
    ts = _now()
    raw = json.dumps(body, sort_keys=True).encode("utf-8")
    signature = hmac.new(key.encode("utf-8"), f"{ts}.".encode() + raw,
                         hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        rec["url"], data=raw, method="POST",
        headers={"Content-Type": "application/json",
                 "X-Qubit-Signature": f"sha256={signature}",
                 "X-Qubit-Timestamp": str(ts),
                 "X-Qubit-Delivery": f"{rec['id']}-{secrets.token_hex(8)}",
                 "User-Agent": "qubit-webhook/1"})
    status: Optional[int] = None
    err = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status = int(getattr(resp, "status", 0) or 0)
    except urllib.error.HTTPError as exc:
        status = int(getattr(exc, "code", 0) or 0)
        err = f"http {status}"
    except Exception as exc:  # noqa: BLE001 - network failures are typed states
        err = str(exc)[:200]
    delivered = status is not None and 200 <= status < 300
    verdict = "DELIVERED" if delivered else "FAILED"
    _receipt(cfg, rec["id"], verdict, f"status={status} {err}".strip())
    _audit("delivery", requested_by,
           {"id": rec["id"], "event": event, "verdict": verdict,
            "status": status})
    return {"state": "OK", "delivered": delivered, "verdict": verdict,
            "http_status": status, "error": err or None,
            "subscription": rec["id"], "event": event, "timestamp": ts,
            "note": "real HMAC-signed delivery; receipts are redacted"}


def rotate_secret(cfg: Any, sub_id: str, *, requested_by: str = "cli:user",
                  confirmed: bool = False) -> Dict[str, Any]:
    """Rotate a subscription's signing secret to a fresh ``file:`` reference.

    The new 32-byte secret is written 0600 into Qubit's own
    ``state/webhook-secrets/<id>.secret``; the subscription is then pointed at
    that ``file:`` reference. The value is never returned or logged — only the
    reference kind. Confirmed + audited.
    """
    if not confirmed:
        raise WebhookError(CONFIRM_CODE,
                           "secret rotation requires explicit --confirm")
    state = _load(cfg)
    rec = _get(state, sub_id)
    directory = _secret_dir(cfg)
    path = directory / f"{rec['id']}.secret"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
        tmp.write_text(secrets.token_hex(32), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as exc:
        raise WebhookError(CORRUPT_CODE,
                           f"secret rotation write failed: {exc}") from exc
    rec["secret_ref"] = f"file:{path}"
    rec["secret_rotated_at"] = _now()
    state["subscriptions"][rec["id"]] = rec
    _save(cfg, state)
    _audit("rotate", requested_by, {"id": rec["id"]})
    out = _public(rec)
    return {"state": "OK", "subscription": out,
            "reference_kind": _secret_kind(rec["secret_ref"]),
            "rotated_at": rec["secret_rotated_at"],
            "note": "new signing secret written to a Qubit-owned file "
                    "reference; the value is never returned"}


def deliveries(cfg: Any, sub_id: str = "", *, limit: int = 50
               ) -> Dict[str, Any]:
    """Recent delivery receipts (redacted) for one or every subscription."""
    path = Path(cfg.state_dir()) / LOG_NAME
    rows: List[Dict[str, Any]] = []
    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines[-MAX_LOG_LINES:]:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            if sub_id and rec.get("subscription") != sub_id:
                continue
            rows.append(rec)
    try:
        cap = max(1, min(int(limit or 50), 500))
    except (TypeError, ValueError):
        cap = 50
    rows = rows[-cap:]
    return {"state": "OK", "deliveries": rows, "count": len(rows),
            "subscription": sub_id or None}


def test_delivery(cfg: Any, sub_id: str, *, requested_by: str = "cli:user",
                  confirmed: bool = False,
                  timeout_s: float = DELIVERY_TIMEOUT_S) -> Dict[str, Any]:
    """Send a real signed test delivery to the subscription's URL."""
    if not confirmed:
        raise WebhookError(CONFIRM_CODE,
                           "a test delivery sends a real signed request; "
                           "explicit --confirm required")
    state = _load(cfg)
    rec = _get(state, sub_id)
    if not bool(rec.get("enabled", True)):
        raise WebhookError(BAD_INPUT_CODE,
                           f"subscription {rec['id']!r} is disabled")
    if not is_enabled(cfg):
        raise WebhookError(BAD_INPUT_CODE,
                           "webhook delivery is disabled at the store level")
    body = {"event": "webhook.test", "subscription": rec["id"],
            "test": True, "sent_at": _now()}
    out = _deliver(cfg, rec, body=body, event="webhook.test",
                   requested_by=requested_by, timeout_s=timeout_s)
    out["mode"] = "test"
    return out


def retry_delivery(cfg: Any, sub_id: str, *, requested_by: str = "cli:user",
                   confirmed: bool = False,
                   timeout_s: float = DELIVERY_TIMEOUT_S) -> Dict[str, Any]:
    """Re-issue a signed delivery when a prior delivery failed.

    Receipts are redacted and the original payload is not retained, so a retry
    re-sends a fresh signed delivery envelope for the subscription. If no failed
    delivery is on record there is nothing to retry (typed ``WEBHOOK_NOT_FOUND``
    — never a fabricated retry).
    """
    if not confirmed:
        raise WebhookError(CONFIRM_CODE,
                           "retrying a delivery sends a real signed request; "
                           "explicit --confirm required")
    state = _load(cfg)
    rec = _get(state, sub_id)
    history = deliveries(cfg, rec["id"], limit=200).get("deliveries", [])
    failed = [r for r in history if isinstance(r, dict)
              and r.get("verdict") == "FAILED"]
    if not failed:
        raise WebhookError(NOT_FOUND_CODE,
                           f"no failed delivery to retry for {rec['id']!r}")
    body = {"event": "webhook.retry", "subscription": rec["id"],
            "retry": True, "sent_at": _now()}
    out = _deliver(cfg, rec, body=body, event="webhook.retry",
                   requested_by=requested_by, timeout_s=timeout_s)
    out["mode"] = "retry"
    out["retried_after_ts"] = failed[-1].get("ts")
    return out


# ---------------------------------------------------------------- verify
def _nonce_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / NONCE_NAME


def _nonce_seen(cfg: Any, nonce: str) -> bool:
    path = _nonce_path(cfg)
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        raw = {}
    now = _now()
    live = {k: v for k, v in raw.items()
            if isinstance(v, (int, float)) and now - float(v) < NONCE_TTL_S}
    seen = nonce in live
    live[nonce] = now
    pruned = dict(sorted(live.items(), key=lambda kv: kv[1])[-MAX_NONCES:])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(pruned), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        pass
    return seen


def _receipt(cfg: Any, sub_id: str, verdict: str, detail: str) -> None:
    path = Path(cfg.state_dir()) / LOG_NAME
    rec = {"ts": _now(), "subscription": sub_id, "verdict": verdict,
           "detail": _redact(detail)[:200]}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > MAX_LOG_LINES:
                path.write_text("\n".join(lines[-MAX_LOG_LINES:]) + "\n",
                                encoding="utf-8")
        except OSError:
            pass
    except OSError:
        pass


def _header(headers: Any, names: tuple) -> str:
    """Case-insensitive header lookup (HTTPMessage or plain dict)."""
    if headers is None:
        return ""
    getter = getattr(headers, "get", None)
    if getter is not None:
        for name in names:
            try:
                val = getter(name)
            except Exception:
                val = None
            if val:
                return str(val).strip()
    try:
        lower = {str(k).lower(): v for k, v in dict(headers).items()}
    except Exception:
        return ""
    for name in names:
        val = lower.get(name.lower())
        if val:
            return str(val).strip()
    return ""


def ingest(cfg: Any, sub_id: str, body_bytes: bytes, headers: Any, *,
           sender: str = "", record_inbox: bool = False) -> Dict[str, Any]:
    """Signed inbound HTTP webhook ingress.

    Resolves the subscription by id, requires a signature (verified against
    the subscription's ``env:``/``file:`` secret reference — a raw secret is
    never accepted or logged), caps the body, enforces timestamp freshness and
    the replay cache, and records a redacted receipt. Every refusal is a typed
    :class:`WebhookError` code (unknown sub, bad/missing signature, stale,
    replay, too large, bad input, auth missing).
    """
    state = _load(cfg)
    rec = _get(state, sub_id)  # unknown subscription -> WEBHOOK_NOT_FOUND
    body = body_bytes or b""
    if len(body) > MAX_BODY_BYTES:
        _receipt(cfg, rec["id"], "TOO_LARGE",
                 f"{len(body)} bytes exceeds {MAX_BODY_BYTES} byte cap")
        _audit("rejected", "webhook-ingress",
               {"id": rec["id"], "reason": TOO_LARGE_CODE})
        raise WebhookError(TOO_LARGE_CODE,
                           f"payload exceeds {MAX_BODY_BYTES} byte cap")
    signature = _header(headers, SIGNATURE_HEADERS)
    if not signature:
        _receipt(cfg, rec["id"], "AUTH_MISSING", "signature header missing")
        _audit("rejected", "webhook-ingress",
               {"id": rec["id"], "reason": BAD_SIGNATURE_CODE})
        raise WebhookError(BAD_SIGNATURE_CODE, "missing signature header")
    if signature.lower().startswith("sha256="):
        signature = signature[len("sha256="):].strip()
    ts_raw = _header(headers, TIMESTAMP_HEADERS)
    try:
        ts = float(ts_raw)
    except (TypeError, ValueError):
        _receipt(cfg, rec["id"], "STALE", "missing/unparseable timestamp header")
        raise WebhookError(STALE_CODE,
                           "missing or unparseable timestamp header") from None
    delivery_id = _header(headers, DELIVERY_HEADERS)
    return verify(cfg, rec["id"], body, signature, ts, sender=sender,
                  delivery_id=delivery_id, record_inbox=record_inbox)


def verify(cfg: Any, sub_id: str, payload: bytes, signature: str,
           timestamp: float, *, sender: str = "",
           delivery_id: str = "",
           record_inbox: bool = False) -> Dict[str, Any]:
    """Verify an inbound delivery (HMAC, freshness, replay, auth).

    Signature is hex HMAC-SHA256 over ``f"{timestamp}.{body}"``. Secret
    values are resolved for comparison only and never logged. Verified
    deliveries record redacted receipts; with ``record_inbox`` the payload
    is additionally stored as a redacted-output inbound message (explicit
    user issuance at the call site). Nothing executes.
    """
    from .providers.credentials import resolve_key

    state = _load(cfg)
    rec = _get(state, sub_id)
    now = _now()
    try:
        ts = float(timestamp)
    except (TypeError, ValueError):
        _receipt(cfg, rec["id"], "STALE", "unparseable timestamp")
        raise WebhookError(STALE_CODE, "unparseable timestamp") from None
    if abs(now - ts) > TS_TOLERANCE_S:
        _receipt(cfg, rec["id"], "STALE", "timestamp outside tolerance")
        raise WebhookError(STALE_CODE,
                           f"timestamp outside ±{TS_TOLERANCE_S:.0f}s tolerance")
    key, code = resolve_key("webhook", rec.get("secret_ref", ""))
    if code or not key:
        _receipt(cfg, rec["id"], "AUTH_MISSING", "secret unresolvable")
        raise WebhookError(AUTH_CODE,
                           "signing secret unresolvable (check the env:/file: "
                           "reference)")
    body = payload or b""
    if len(body) > MAX_BODY_BYTES:
        raise WebhookError(TOO_LARGE_CODE,
                           f"payload exceeds {MAX_BODY_BYTES} byte cap")
    expected = hmac.new(key.encode("utf-8"),
                        f"{ts}.".encode() + body,
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, (signature or "").strip().lower()):
        _receipt(cfg, rec["id"], "BAD_SIGNATURE", "hmac mismatch")
        raise WebhookError(BAD_SIGNATURE_CODE, "signature mismatch")
    nonce = hashlib.sha256(f"{rec['id']}:{ts}:{hashlib.sha256(body).hexdigest()}"
                           .encode()).hexdigest()
    delivery_nonce = (hashlib.sha256(f"delivery:{delivery_id}".encode()).hexdigest()
                      if delivery_id else "")
    replayed = _nonce_seen(cfg, nonce)
    if delivery_nonce and _nonce_seen(cfg, delivery_nonce):
        replayed = True
    if replayed:
        _receipt(cfg, rec["id"], "REPLAY", "nonce already seen")
        raise WebhookError(REPLAY_CODE, "duplicate delivery (replay)")
    _receipt(cfg, rec["id"], "VALID", f"{len(body)} bytes, event evidence only")
    _audit("verified", "webhook-ingress",
           {"id": rec["id"], "bytes": len(body)})
    out: Dict[str, Any] = {"state": "VALID", "schema": "qubit-webhook-verify/1",
                           "subscription": rec["id"],
                           "events": list(rec.get("events", [])),
                           "bytes": len(body),
                           "note": "receipt recorded; nothing executed or dispatched"}
    if record_inbox:
        from . import messaging as MG

        try:
            stored = MG.record_inbound(cfg, rec["id"], sender or "unknown",
                                       body, requested_by="webhook-ingress",
                                       confirmed=True)
        except MG.MessagingError as exc:
            raise WebhookError(exc.code if hasattr(exc, "code") else "WEBHOOK_BAD_INPUT",
                               f"inbox record failed: {exc}") from exc
        out["inbox_id"] = stored["message"]["id"]
    return out


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the plugins surface (fail closed)."""
    from .plugins import estop_engaged as _engaged

    return _engaged(cfg)


def refuse_estop(operation: str) -> Dict[str, Any]:
    """Typed refusal while the emergency stop is latched."""
    return {"state": "ESTOP", "code": ESTOP_CODE, "operation": operation,
            "explanation": "Qubit Agent cannot do this: the emergency stop "
                           "is latched; reset it before webhook changes.",
            "owner": "qubit-policy",
            "rollback": "registry untouched; nothing to roll back"}
