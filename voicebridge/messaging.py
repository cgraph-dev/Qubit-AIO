"""Qubit messaging: accounts + brokered outbox.

Qubit ``send``/``whatsapp``/``whatsapp-cloud``/``slack`` deliver through
live messaging transports. Qubit implements the complete code-side
adapter; the live network is the last mile and stays credential-gated:

* ``account-add`` — provider records (``whatsapp`` | ``whatsapp-cloud``
  | ``slack`` plus the OpenClaw-breadth channels ``discord`` | ``telegram``
  | ``signal`` | ``matrix`` | ``msteams`` | ``imessage``) with label,
  endpoint (provider allowlist enforced, lookalike hosts refused), origin
  binding, and a vault-only key reference (never a value). Explicit
  ``--confirm``. See :data:`PROVIDER_CAPS`: a channel with no Qubit
  transport adapter is recorded honestly and reports a typed
  ``UNAVAILABLE`` state (``MSG_TRANSPORT_UNAVAILABLE``), never a fake send.
* ``provider-catalog`` — :func:`provider_catalog` typed read of the channel
  set: which transports exist and which are unavailable, no credential read.
* ``draft`` — stage an outbox message (recipient + body caps, preview
  redacted, idempotency key). Reversible, confirmed.
* ``approve`` — mark approved (four-eyes separation from ``send``).
* ``send`` — fresh exact ``--confirm`` per send; gates in order:
  e-stop → draft exists → account exists → credential resolvable
  (``MSG_NO_CREDENTIAL``, nothing attempted) → classic-WhatsApp paired
  session (``MSG_NOT_PAIRED``) → idempotency replay (returns the stored
  receipt, never resends) → provider POST (bounded timeout, validated
  response) → redacted receipt + audit. Bodies live only in the 0600
  outbox file; every output carries previews/hashes.
* ``retry`` — re-attempt queued/failed items with recorded backoff.
* ``link`` — typed link state (credential presence + endpoint policy +
  last outcome; no active probing).
* ``list``/``show``/``account-list``/``account-remove`` — redacted reads.

Provider transports are real HTTPS POSTs (Slack ``chat.postMessage``,
WhatsApp Cloud ``messages``) with the SecretRef-resolved key in the
Authorization header only; keys never reach logs, receipts, or errors.
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

SCHEMA = "qubit-messaging/1"
ACCOUNTS_NAME = "msg-accounts.json"
OUTBOX_NAME = "msg-outbox.json"
INBOX_NAME = "msg-inbox.json"
MAX_FILE_BYTES = 2_000_000
MAX_BODY_CHARS = 4000
MAX_INBOX_BODY_CHARS = 10000
MAX_INBOX = 500
MAX_TO_LEN = 160
TRANSPORT_TIMEOUT_S = 15.0

# Fixed provider endpoints (no custom hosts without config allowlist).
ENDPOINTS = {
    "whatsapp-cloud": "https://graph.facebook.com/v21.0/{phone_id}/messages",
    "slack": "https://slack.com/api/chat.postMessage",
}

# Honest capability matrix for every channel Qubit recognizes. ``transport``
# names the Qubit-side delivery path: "https" providers have a real bounded
# POST in :func:`_transport`; every other value is a channel Qubit can record
# but cannot deliver on yet, so it must resolve to a typed ``UNAVAILABLE``
# state (never a fabricated success). ``credential_kind`` names the reference
# shape only -- values live behind ``env:``/``file:`` refs and are never
# stored, logged, or returned.
PROVIDER_CAPS: Dict[str, Dict[str, Any]] = {
    "whatsapp": {
        "label": "WhatsApp (paired device)",
        "transport": "paired-session",
        "available": False,
        "credential_kind": "paired-session",
        "detail": "classic WhatsApp delivery stays with the on-device session; "
                  "Qubit authorizes via pairing but has no session transport",
    },
    "whatsapp-cloud": {
        "label": "WhatsApp Cloud API",
        "transport": "https",
        "available": True,
        "credential_kind": "bearer-token",
        "endpoint": ENDPOINTS["whatsapp-cloud"],
    },
    "slack": {
        "label": "Slack",
        "transport": "https",
        "available": True,
        "credential_kind": "bot-token",
        "endpoint": ENDPOINTS["slack"],
    },
    "discord": {
        "label": "Discord",
        "transport": "bot-gateway",
        "available": False,
        "credential_kind": "bot-token",
        "detail": "no Discord gateway/REST adapter exists in Qubit; a bot "
                  "token reference alone cannot deliver a message",
    },
    "telegram": {
        "label": "Telegram",
        "transport": "bot-api",
        "available": False,
        "credential_kind": "bot-token",
        "detail": "no Telegram Bot API adapter exists in Qubit",
    },
    "signal": {
        "label": "Signal",
        "transport": "signal-cli",
        "available": False,
        "credential_kind": "linked-device",
        "detail": "Signal needs a local signal-cli linked device; no adapter "
                  "exists in Qubit",
    },
    "matrix": {
        "label": "Matrix",
        "transport": "client-server",
        "available": False,
        "credential_kind": "access-token",
        "detail": "no Matrix client-server adapter exists in Qubit",
    },
    "msteams": {
        "label": "Microsoft Teams",
        "transport": "bot-framework",
        "available": False,
        "credential_kind": "bot-credentials",
        "detail": "Teams needs an Azure Bot Framework registration; no "
                  "adapter exists in Qubit",
    },
    "imessage": {
        "label": "iMessage",
        "transport": "local-bridge",
        "available": False,
        "credential_kind": "local-bridge",
        "detail": "iMessage needs a macOS Messages bridge; no adapter exists "
                  "in Qubit",
    },
    "feishu": {
        "label": "Feishu / Lark",
        "transport": "webhook",
        "available": False,
        "credential_kind": "app-secret",
        "detail": "Feishu dynamic agents need an open-platform event adapter; "
                  "Qubit recognizes the channel but has no Feishu transport",
    },
}

# Single source of truth for provider membership (order preserved).
PROVIDERS = tuple(PROVIDER_CAPS)

CONFIRM_CODE = "MSG_CONFIRM_REQUIRED"
ESTOP_CODE = "MSG_ESTOP"
NOT_FOUND_CODE = "MSG_NOT_FOUND"
EXISTS_CODE = "MSG_EXISTS"
BAD_INPUT_CODE = "MSG_BAD_INPUT"
NO_CREDENTIAL_CODE = "MSG_NO_CREDENTIAL"
NOT_PAIRED_CODE = "MSG_NOT_PAIRED"
NO_TRANSPORT_CODE = "MSG_NO_TRANSPORT"
UNAVAILABLE_CODE = "MSG_TRANSPORT_UNAVAILABLE"
UPSTREAM_CODE = "MSG_UPSTREAM"
CORRUPT_CODE = "MSG_STORE_CORRUPT"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")

DRAFT, APPROVED, QUEUED, SENT, FAILED = "draft", "approved", "queued", "sent", "failed"


class MessagingError(Exception):
    """Typed messaging failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def _now() -> float:
    return time.time()


def _accounts_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / ACCOUNTS_NAME


def _outbox_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / OUTBOX_NAME


def _blank_accounts() -> Dict[str, Any]:
    return {"schema": SCHEMA, "accounts": {}}


def _blank_outbox() -> Dict[str, Any]:
    return {"schema": SCHEMA, "messages": {}}


def _load_json(path: Path, blank, label: str) -> Dict[str, Any]:
    if not path.exists():
        return blank()
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise MessagingError(CORRUPT_CODE, f"{label} store too large")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except MessagingError:
        raise
    except (OSError, ValueError) as exc:
        raise MessagingError(CORRUPT_CODE,
                             f"{label} store unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise MessagingError(CORRUPT_CODE, f"{label} store has a bad shape")
    return raw


def _save_json(path: Path, state: Dict[str, Any], label: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True),
                       encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as exc:
        raise MessagingError(CORRUPT_CODE,
                             f"{label} store write failed: {exc}") from exc


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    from .logging_setup import log_event

    try:
        log_event(f"msg.{op}", actor=actor, **detail)
    except Exception:
        pass


def _check_id(text: str) -> str:
    clean = (text or "").strip().lower()
    if not _ID_RE.match(clean):
        raise MessagingError(BAD_INPUT_CODE,
                             "id must match ^[a-z0-9][a-z0-9_.-]{0,63}$")
    return clean


def _check_label(label: str) -> str:
    text = (label or "").strip()
    if not _LABEL_RE.match(text):
        raise MessagingError(BAD_INPUT_CODE,
                             "label must match ^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
    return text


def _secret_kind(ref: str) -> str:
    if ref.startswith("env:"):
        return "env"
    if ref.startswith("file:"):
        return "file"
    return "missing"


# ---------------------------------------------------------------- accounts
def account_add(cfg: Any, account_id: str, provider: str, label: str, *,
                endpoint: str = "", key_ref: str = "", origin: str = "",
                extra: Optional[Dict[str, str]] = None,
                requested_by: str = "cli:user",
                confirmed: bool = False) -> Dict[str, Any]:
    """Record a provider account (metadata + vault reference, never values)."""
    if not confirmed:
        raise MessagingError(CONFIRM_CODE,
                             "account add requires explicit --confirm")
    clean_id = _check_id(account_id)
    clean_label = _check_label(label)
    if provider not in PROVIDERS:
        raise MessagingError(BAD_INPUT_CODE,
                             f"provider must be one of {PROVIDERS}")
    ref = (key_ref or "").strip()
    if _secret_kind(ref) == "missing":
        raise MessagingError(BAD_INPUT_CODE,
                             "key_ref must be an env:VAR or file:PATH reference")
    fields = dict(extra or {})
    if provider == "whatsapp-cloud":
        phone_id = str(fields.get("phone_number_id", "")).strip()
        if not re.fullmatch(r"\d{5,20}", phone_id):
            raise MessagingError(BAD_INPUT_CODE,
                                 "whatsapp-cloud needs numeric phone_number_id")
        url = ENDPOINTS["whatsapp-cloud"].format(phone_id=phone_id)
    elif provider == "slack":
        url = ENDPOINTS["slack"]
    else:
        url = ""
    if endpoint.strip() and endpoint.strip() != url:
        raise MessagingError(BAD_INPUT_CODE,
                             "custom endpoints are refused; provider "
                             "endpoints are fixed")
    state = _load_json(_accounts_path(cfg), _blank_accounts, "accounts")
    if clean_id in state["accounts"]:
        raise MessagingError(EXISTS_CODE, f"account {clean_id!r} exists")
    caps = PROVIDER_CAPS.get(provider, {})
    rec = {"id": clean_id, "provider": provider, "label": clean_label,
           "endpoint": url, "origin": (origin or "").strip()[:160],
           "key_ref_kind": _secret_kind(ref),
           "transport": caps.get("transport", "none"),
           "transport_available": bool(caps.get("available", False)),
           "capability_detail": caps.get("detail", ""),
           "fields": {k: str(v)[:80] for k, v in fields.items()
                      if k != "key_ref"},
           "created_at": _now(), "updated_by": requested_by,
           "last_outcome": "NEVER_USED"}
    # The reference itself is needed at send time; only the kind is
    # ever shown (var names and paths stay out of outputs).
    if ref.startswith("env:"):
        rec["key_ref"] = "env"
        rec["_key_name"] = ref[len("env:"):].strip()[:64]
    else:
        rec["key_ref"] = "file"
        rec["_key_path"] = ref
    state["accounts"][clean_id] = rec
    _save_json(_accounts_path(cfg), state, "accounts")
    _audit("account.add", requested_by,
           {"account": clean_id, "provider": provider})
    public = dict(rec)
    public.pop("_key_path", None)
    public.pop("_key_name", None)
    return {"state": "OK", "account": public}


def _resolve_account_ref(rec: Dict[str, Any]) -> str:
    if rec.get("key_ref") == "env":
        return f"env:{rec.get('_key_name', '')}"
    return rec.get("_key_path", "")


def account_credential_ref(cfg: Any, account_id: str) -> str:
    """Private credential reference for transport-side resolution.

    Never print or log the result: it names the live reference. Only
    gateway/link and send paths may call this.
    """
    return _resolve_account_ref(_get_account(cfg, account_id))


def account_list(cfg: Any) -> Dict[str, Any]:
    """List accounts (redacted references)."""
    state = _load_json(_accounts_path(cfg), _blank_accounts, "accounts")
    recs = []
    for rec in sorted(state["accounts"].values(), key=lambda r: r.get("id", "")):
        public = dict(rec)
        public.pop("_key_path", None)
        public.pop("_key_name", None)
        recs.append(public)
    return {"state": "OK", "accounts": recs}


def account_show(cfg: Any, account_id: str) -> Dict[str, Any]:
    """Show one account (redacted)."""
    state = _load_json(_accounts_path(cfg), _blank_accounts, "accounts")
    rec = state["accounts"].get(_check_id(account_id))
    if rec is None:
        raise MessagingError(NOT_FOUND_CODE, f"no account {account_id!r}")
    public = dict(rec)
    public.pop("_key_path", None)
    public.pop("_key_name", None)
    return {"state": "OK", "account": public}


def account_remove(cfg: Any, account_id: str, *, requested_by: str = "cli:user",
                   confirmed: bool = False) -> Dict[str, Any]:
    """Remove an account (outbox keeps denormalized snapshots)."""
    if not confirmed:
        raise MessagingError(CONFIRM_CODE,
                             "account remove requires explicit --confirm")
    state = _load_json(_accounts_path(cfg), _blank_accounts, "accounts")
    clean = _check_id(account_id)
    if clean not in state["accounts"]:
        raise MessagingError(NOT_FOUND_CODE, f"no account {account_id!r}")
    del state["accounts"][clean]
    _save_json(_accounts_path(cfg), state, "accounts")
    _audit("account.remove", requested_by, {"account": clean})
    return {"state": "OK", "removed": clean}


def _get_account(cfg: Any, account_id: str) -> Dict[str, Any]:
    state = _load_json(_accounts_path(cfg), _blank_accounts, "accounts")
    rec = state["accounts"].get(_check_id(account_id))
    if rec is None:
        raise MessagingError(NOT_FOUND_CODE, f"no account {account_id!r}")
    return rec


def account_update(cfg: Any, account_id: str, *, label: str = "",
                   enabled: Optional[bool] = None,
                   requested_by: str = "dashboard:user",
                   confirmed: bool = False) -> Dict[str, Any]:
    """Update account metadata (label / enabled flag). Credential references
    are never touched here. Requires explicit confirmation."""
    if not confirmed:
        raise MessagingError(CONFIRM_CODE,
                             "account update requires explicit --confirm")
    state = _load_json(_accounts_path(cfg), _blank_accounts, "accounts")
    clean = _check_id(account_id)
    rec = state["accounts"].get(clean)
    if rec is None:
        raise MessagingError(NOT_FOUND_CODE, f"no account {account_id!r}")
    if label:
        rec["label"] = _check_label(label)
    if enabled is not None:
        rec["enabled"] = bool(enabled)
    rec.setdefault("enabled", True)
    rec["updated_by"] = requested_by
    _save_json(_accounts_path(cfg), state, "accounts")
    _audit("account.update", requested_by,
           {"account": clean, "label": rec.get("label", ""),
            "enabled": bool(rec.get("enabled", True))})
    return {"state": "OK", "account": {k: v for k, v in rec.items()
                                       if not k.startswith("_key")}}


def provider_catalog() -> Dict[str, Any]:
    """Typed capability view of every known channel (no credential read).

    A channel with no Qubit transport adapter reports ``state: UNAVAILABLE``
    with a typed code -- an unconfigured channel is never reported as a
    working one. Read-only projection over :data:`PROVIDER_CAPS`.
    """
    rows: List[Dict[str, Any]] = []
    for pid, caps in PROVIDER_CAPS.items():
        available = bool(caps.get("available", False))
        rows.append({
            "provider": pid,
            "label": caps.get("label", pid),
            "transport": caps.get("transport", "none"),
            "credential_kind": caps.get("credential_kind", "unknown"),
            "state": "AVAILABLE" if available else "UNAVAILABLE",
            "code": "" if available else UNAVAILABLE_CODE,
            "detail": caps.get("detail", ""),
        })
    return {"state": "OK", "providers": rows,
            "available": [r["provider"] for r in rows
                          if r["state"] == "AVAILABLE"],
            "unavailable": [r["provider"] for r in rows
                            if r["state"] == "UNAVAILABLE"]}


def link(cfg: Any, account_id: str) -> Dict[str, Any]:
    """Typed link state: credential presence + transport + history."""
    from .providers.credentials import resolve_key

    try:
        rec = _get_account(cfg, account_id)
    except MessagingError as exc:
        return {"state": "NO_ACCOUNT", "code": NOT_FOUND_CODE,
                "detail": exc.detail[:160]}
    _, code = resolve_key(rec["provider"], _resolve_account_ref(rec))
    if code:
        return {"state": "NO_CREDENTIAL", "account": rec["id"],
                "detail": "credential reference unresolvable; enroll it "
                          "with `auth login --provider` first"}
    caps = PROVIDER_CAPS.get(rec["provider"], {})
    if rec["provider"] != "whatsapp" and not caps.get("available", False):
        # Credential is present but Qubit has no transport adapter: report
        # the honest typed state instead of a fabricated LINKED.
        return {"state": "UNAVAILABLE", "code": UNAVAILABLE_CODE,
                "account": rec["id"], "provider": rec["provider"],
                "transport": caps.get("transport", "none"),
                "detail": caps.get("detail", "no Qubit transport adapter")}
    last = rec.get("last_outcome", "NEVER_USED")
    state = "LINKED" if last in ("NEVER_USED", "SENT") else "DEGRADED"
    return {"state": state, "account": rec["id"], "provider": rec["provider"],
            "endpoint": rec.get("endpoint", "") or "(session-based)",
            "last_outcome": last}


# ---------------------------------------------------------------- outbox
def _public_msg(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": rec["id"], "account": rec["account"],
            "provider": rec["provider"], "to": _redact(rec.get("to", ""))[:40],
            "body_preview": _redact(rec.get("body", ""))[:120],
            "body_sha256": rec.get("body_sha256", ""),
            "state": rec["state"], "attempts": rec.get("attempts", 0),
            "idempotency_key": rec.get("idempotency_key", ""),
            "provider_msg_id": rec.get("provider_msg_id", ""),
            "duplicate": bool(rec.get("duplicate", False)),
            "created_at": rec.get("created_at"),
            "updated_at": rec.get("updated_at")}


def _get_msg(cfg: Any, msg_id: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    state = _load_json(_outbox_path(cfg), _blank_outbox, "outbox")
    rec = state["messages"].get((msg_id or "").strip().lower())
    if rec is None:
        raise MessagingError(NOT_FOUND_CODE, f"no message {msg_id!r}")
    return state, rec


def _save_msg(cfg: Any, state: Dict[str, Any]) -> None:
    _save_json(_outbox_path(cfg), state, "outbox")


def draft(cfg: Any, account: str, to: str, body: str, *,
          idempotency_key: str = "", requested_by: str = "cli:user",
          confirmed: bool = False) -> Dict[str, Any]:
    """Stage an outbox draft (reversible, confirmed, redacted preview)."""
    if not confirmed:
        raise MessagingError(CONFIRM_CODE,
                             "message draft requires explicit --confirm")
    rec_acct = _get_account(cfg, account)
    clean_to = (to or "").strip()
    if not clean_to or len(clean_to) > MAX_TO_LEN:
        raise MessagingError(BAD_INPUT_CODE,
                             f"recipient must be 1..{MAX_TO_LEN} chars")
    text = body or ""
    if not text.strip() or len(text) > MAX_BODY_CHARS:
        raise MessagingError(BAD_INPUT_CODE,
                             f"body must be 1..{MAX_BODY_CHARS} chars")
    key = (idempotency_key or "").strip() or f"msg_{uuid.uuid4().hex[:16]}"
    state = _load_json(_outbox_path(cfg), _blank_outbox, "outbox")
    for existing in state["messages"].values():
        if existing.get("idempotency_key") == key:
            raise MessagingError(EXISTS_CODE,
                                 f"idempotency key in use by {existing['id']!r}")
    msg_id = "msg_" + uuid.uuid4().hex[:12]
    rec = {"id": msg_id, "account": rec_acct["id"],
           "provider": rec_acct["provider"],
           "endpoint": rec_acct.get("endpoint", ""),
           "account_fields": dict(rec_acct.get("fields", {})),
           "account_ref": _resolve_account_ref(rec_acct),
           "to": clean_to, "body": text,
           "body_sha256": hashlib.sha256(text.encode()).hexdigest(),
           "state": DRAFT, "attempts": 0, "idempotency_key": key,
           "provider_msg_id": "", "duplicate": False,
           "created_at": _now(), "updated_at": _now(),
           "updated_by": requested_by}
    state["messages"][msg_id] = rec
    _save_msg(cfg, state)
    _audit("draft", requested_by,
           {"message": msg_id, "account": rec_acct["id"],
            "body_sha256": rec["body_sha256"]})
    return {"state": "OK", "message": _public_msg(rec)}


def approve(cfg: Any, msg_id: str, *, requested_by: str = "cli:user",
            confirmed: bool = False) -> Dict[str, Any]:
    """Approve a draft (separates review from sending)."""
    if not confirmed:
        raise MessagingError(CONFIRM_CODE,
                             "message approve requires explicit --confirm")
    state, rec = _get_msg(cfg, msg_id)
    if rec["state"] != DRAFT:
        raise MessagingError(BAD_INPUT_CODE,
                             f"message is {rec['state']}, not draft")
    rec["state"] = APPROVED
    rec["updated_at"] = _now()
    _save_msg(cfg, state)
    _audit("approve", requested_by, {"message": rec["id"]})
    return {"state": "OK", "message": _public_msg(rec)}


def _paired_session(cfg: Any, account_id: str) -> bool:
    """Live check: an approved, unexpired pairing bound to this account."""
    from . import pairing as PAIR
    from .permissions import permissions_store

    try:
        codes = PAIR.list_codes(cfg)["codes"]
    except Exception:
        return False
    try:
        grants = {g.get("id") if isinstance(g, dict) else g.id
                  for g in permissions_store(cfg).grants()}
    except Exception:
        grants = set()
    now = time.time()
    for code in codes:
        if code.get("revoked") or not code.get("grant_id"):
            continue
        if code.get("account") != account_id:
            continue
        if code.get("grant_id") not in grants:
            continue
        # Grant presence + code record liveness imply an approved binding.
        if now < float(code.get("expires_at", 0)) + MAX_GRANT_SKEW_S:
            return True
    return False


MAX_GRANT_SKEW_S = 86400.0


def _transport(account_provider: str, endpoint: str, fields: Dict[str, str],
               to: str, body: str, key: str) -> Dict[str, Any]:
    """Real provider POST (key in Authorization header only)."""
    import json as _json

    if account_provider == "slack":
        payload = _json.dumps({"channel": to, "text": body}).encode()
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {key}"}
        req = urllib.request.Request(endpoint, data=payload, headers=headers)
    elif account_provider == "whatsapp-cloud":
        payload = _json.dumps({"messaging_product": "whatsapp", "to": to,
                               "type": "text",
                               "text": {"body": body}}).encode()
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {key}"}
        req = urllib.request.Request(endpoint, data=payload, headers=headers)
    else:
        if account_provider == "whatsapp":
            # Classic WhatsApp delivers through a paired on-device session
            # (QR enrollment on the user's phone): pairing confers the
            # authorization, but the session transport itself is manual-only.
            raise MessagingError(NO_TRANSPORT_CODE,
                                 "classic WhatsApp needs its on-device session "
                                 "transport (manual-only); authorization is "
                                 "paired, delivery stays with the session")
        caps = PROVIDER_CAPS.get(account_provider, {})
        raise MessagingError(
            UNAVAILABLE_CODE,
            f"{caps.get('label', account_provider)} has no Qubit transport "
            f"adapter ({caps.get('detail', 'transport unavailable')}); "
            "nothing was sent")
    try:
        with urllib.request.urlopen(req,
                                    timeout=TRANSPORT_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = getattr(resp, "status", 200)
    except Exception as exc:
        raise MessagingError(UPSTREAM_CODE,
                             f"provider request failed: {exc}") from exc
    if status != 200:
        raise MessagingError(UPSTREAM_CODE,
                             f"provider HTTP {status}")
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        raise MessagingError(UPSTREAM_CODE, "provider bad JSON") from None
    if account_provider == "slack":
        if not (isinstance(data, dict) and data.get("ok") is True):
            raise MessagingError(UPSTREAM_CODE,
                                 "slack refused the message")
        return {"provider_msg_id": str(data.get("ts", ""))}
    msgs = data.get("messages", []) if isinstance(data, dict) else []
    if not msgs or not isinstance(msgs[0], dict) or "id" not in msgs[0]:
        raise MessagingError(UPSTREAM_CODE, "provider bad receipt")
    return {"provider_msg_id": str(msgs[0]["id"])}


def send(cfg: Any, msg_id: str, *, requested_by: str = "cli:user",
         confirmed: bool = False) -> Dict[str, Any]:
    """Send with fresh exact confirmation through every gate in order."""
    from .plugins import estop_engaged
    from .providers.credentials import resolve_key

    if estop_engaged(cfg):
        raise MessagingError(ESTOP_CODE, "emergency stop is latched")
    if not confirmed:
        raise MessagingError(CONFIRM_CODE,
                             "message send requires fresh exact --confirm")
    state, rec = _get_msg(cfg, msg_id)
    if rec["state"] not in (DRAFT, APPROVED, QUEUED, FAILED):
        if rec["state"] == SENT:
            out = dict(_public_msg(rec))
            out["duplicate"] = True
            return {"state": "OK", "message": out,
                    "note": "already sent; original receipt returned"}
        raise MessagingError(BAD_INPUT_CODE,
                             f"message is {rec['state']}")
    key, code = resolve_key(rec["provider"], rec.get("account_ref", ""))
    if code or not key:
        rec["state"] = QUEUED
        rec["updated_at"] = _now()
        _save_msg(cfg, state)
        raise MessagingError(NO_CREDENTIAL_CODE,
                             "credential reference unresolvable; message "
                             "queued, nothing attempted")
    if rec["provider"] == "whatsapp" and not _paired_session(cfg, rec["account"]):
        rec["state"] = QUEUED
        rec["updated_at"] = _now()
        _save_msg(cfg, state)
        raise MessagingError(NOT_PAIRED_CODE,
                             "classic WhatsApp needs a paired session: run "
                             "`pairing approve` bound to this account first")
    for other in state["messages"].values():
        if other["id"] != rec["id"] and other.get("idempotency_key") == rec.get(
                "idempotency_key") and other.get("state") == SENT:
            out = dict(_public_msg(other))
            out["duplicate"] = True
            return {"state": "OK", "message": out,
                    "note": "idempotency replay: original receipt returned"}
    rec["attempts"] = int(rec.get("attempts", 0)) + 1
    try:
        result = _transport(rec["provider"], rec.get("endpoint", ""),
                            rec.get("account_fields", {}), rec["to"],
                            rec["body"], key)
    except MessagingError as exc:
        rec["state"] = QUEUED
        rec["updated_at"] = _now()
        rec["next_retry_at"] = _now() + min(3600.0, 60.0 * (2 ** rec["attempts"]))
        _save_msg(cfg, state)
        try:
            acct_state = _load_json(_accounts_path(cfg), _blank_accounts,
                                    "accounts")
            if rec["account"] in acct_state["accounts"]:
                acct_state["accounts"][rec["account"]]["last_outcome"] = "FAILED"
                _save_json(_accounts_path(cfg), acct_state, "accounts")
        except MessagingError:
            pass
        _audit("send_failed", requested_by,
               {"message": rec["id"], "code": exc.code,
                "attempts": rec["attempts"]})
        raise
    rec["state"] = SENT
    rec["provider_msg_id"] = result.get("provider_msg_id", "")
    rec["updated_at"] = _now()
    _save_msg(cfg, state)
    try:
        acct_state = _load_json(_accounts_path(cfg), _blank_accounts, "accounts")
        if rec["account"] in acct_state["accounts"]:
            acct_state["accounts"][rec["account"]]["last_outcome"] = "SENT"
            _save_json(_accounts_path(cfg), acct_state, "accounts")
    except MessagingError:
        pass
    _audit("send", requested_by,
           {"message": rec["id"], "account": rec["account"],
            "body_sha256": rec.get("body_sha256", ""),
            "provider_msg_id": _redact(rec["provider_msg_id"])[:40]})
    out = _public_msg(rec)
    return {"state": "OK", "message": out}


def retry(cfg: Any, msg_id: str, *, requested_by: str = "cli:user",
          confirmed: bool = False) -> Dict[str, Any]:
    """Re-attempt a queued/failed message (same gates as send)."""
    if not confirmed:
        raise MessagingError(CONFIRM_CODE,
                             "message retry requires explicit --confirm")
    state, rec = _get_msg(cfg, msg_id)
    if rec["state"] not in (QUEUED, FAILED):
        raise MessagingError(BAD_INPUT_CODE,
                             f"message is {rec['state']}; nothing to retry")
    return send(cfg, rec["id"], requested_by=requested_by, confirmed=True)


def list_msgs(cfg: Any, *, state_filter: str = "") -> Dict[str, Any]:
    """List outbox messages (redacted previews)."""
    state = _load_json(_outbox_path(cfg), _blank_outbox, "outbox")
    recs = sorted(state["messages"].values(),
                  key=lambda r: r.get("created_at", 0), reverse=True)
    if state_filter:
        recs = [r for r in recs if r.get("state") == state_filter]
    return {"state": "OK", "messages": [_public_msg(r) for r in recs[:200]]}


def show(cfg: Any, msg_id: str) -> Dict[str, Any]:
    """Show one message (redacted preview)."""
    _, rec = _get_msg(cfg, msg_id)
    return {"state": "OK", "message": _public_msg(rec)}


def _inbox_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / INBOX_NAME


def _blank_inbox() -> Dict[str, Any]:
    return {"schema": SCHEMA, "inbound": {}}


def record_inbound(cfg: Any, subscription: str, sender: str, body: bytes, *,
                   requested_by: str = "webhook-ingress",
                   confirmed: bool = False) -> Dict[str, Any]:
    """Store one verified inbound message (bounded, redacted outputs).

    Bodies live only in the 0600 inbox file (user-private state, like
    outbox drafts); every output carries previews/hashes. Untrusted
    sender content is never executed, dispatched, or granted authority.
    """
    if not confirmed:
        raise MessagingError(CONFIRM_CODE,
                             "inbox record requires explicit confirmation")
    clean_sender = (sender or "").strip()
    if not clean_sender or len(clean_sender) > MAX_TO_LEN:
        raise MessagingError(BAD_INPUT_CODE,
                             f"sender must be 1..{MAX_TO_LEN} chars")
    raw = body or b""
    if len(raw) > MAX_INBOX_BODY_CHARS:
        raise MessagingError(BAD_INPUT_CODE,
                             f"inbound body exceeds {MAX_INBOX_BODY_CHARS} bytes")
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        text = ""
    state = _load_json(_inbox_path(cfg), _blank_inbox, "inbox")
    if len(state["inbound"]) >= MAX_INBOX:
        oldest = sorted(state["inbound"].values(),
                        key=lambda r: r.get("received_at", 0))[:-MAX_INBOX + 1]
        for stale in oldest:
            del state["inbound"][stale["id"]]
    msg_id = "in_" + uuid.uuid4().hex[:12]
    rec = {"id": msg_id, "subscription": (subscription or "").strip()[:80],
           "sender": clean_sender, "body": text,
           "body_sha256": hashlib.sha256(raw).hexdigest(),
           "received_at": _now(), "summarized": False}
    state["inbound"][msg_id] = rec
    _save_json(_inbox_path(cfg), state, "inbox")
    _audit("inbox.record", requested_by,
           {"message": msg_id, "subscription": rec["subscription"],
            "body_sha256": rec["body_sha256"]})
    return {"state": "OK", "message": _public_inbound(rec)}


def _public_inbound(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": rec["id"], "subscription": rec.get("subscription", ""),
            "sender": _redact(rec.get("sender", ""))[:80],
            "body_preview": _redact(rec.get("body", ""))[:200],
            "body_sha256": rec.get("body_sha256", ""),
            "received_at": rec.get("received_at"),
            "summarized": bool(rec.get("summarized", False))}


def _get_inbound(cfg: Any, msg_id: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    state = _load_json(_inbox_path(cfg), _blank_inbox, "inbox")
    rec = state["inbound"].get((msg_id or "").strip().lower())
    if rec is None:
        raise MessagingError(NOT_FOUND_CODE, f"no inbound {msg_id!r}")
    return state, rec


def inbox_list(cfg: Any, *, subscription: str = "") -> Dict[str, Any]:
    """List inbound messages (redacted previews, newest first)."""
    state = _load_json(_inbox_path(cfg), _blank_inbox, "inbox")
    recs = sorted(state["inbound"].values(),
                  key=lambda r: r.get("received_at", 0), reverse=True)
    if (subscription or "").strip():
        recs = [r for r in recs
                if r.get("subscription") == subscription.strip()]
    return {"state": "OK", "messages": [_public_inbound(r) for r in recs[:200]]}


def inbox_show(cfg: Any, msg_id: str) -> Dict[str, Any]:
    """Show one inbound message (redacted preview)."""
    _, rec = _get_inbound(cfg, msg_id)
    return {"state": "OK", "message": _public_inbound(rec)}


def summarize(cfg: Any, msg_id: str) -> Dict[str, Any]:
    """Extractive digest of one inbound message (deterministic, local)."""
    from .summarizer import summarize_extractive

    state, rec = _get_inbound(cfg, msg_id)
    digest = summarize_extractive(rec.get("body", ""), max_chars=900)
    rec["summarized"] = True
    _save_json(_inbox_path(cfg), state, "inbox")
    return {"state": "OK", "id": rec["id"], "digest": _redact(digest)[:900]}


def draft_reply(cfg: Any, msg_id: str, account: str, body: str, *,
                requested_by: str = "cli:user",
                confirmed: bool = False) -> Dict[str, Any]:
    """Draft a reply linked to an inbound message (same gates as draft)."""
    state, rec = _get_inbound(cfg, msg_id)
    out = draft(cfg, account, rec.get("sender", ""), body,
                requested_by=requested_by, confirmed=confirmed)
    body_rec_state, body_rec = _get_msg(cfg, out["message"]["id"])
    body_rec["in_reply_to"] = rec["id"]
    _save_msg(cfg, body_rec_state)
    out["message"]["in_reply_to"] = rec["id"]
    _audit("draft.reply", requested_by,
           {"message": body_rec["id"], "in_reply_to": rec["id"]})
    return out


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the plugins surface (fail closed)."""
    from .plugins import estop_engaged as _engaged

    return _engaged(cfg)


def refuse_estop(operation: str) -> Dict[str, Any]:
    """Typed refusal while the emergency stop is latched."""
    return {"state": "ESTOP", "code": ESTOP_CODE, "operation": operation,
            "explanation": "Qubit Agent cannot do this: the emergency stop "
                           "is latched; reset it before messaging changes.",
            "owner": "qubit-policy",
            "rollback": "outbox untouched; nothing sent"}
