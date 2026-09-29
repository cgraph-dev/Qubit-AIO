"""Qubit messaging: accounts + brokered outbox.

Qubit ``send``/``whatsapp``/``whatsapp-cloud``/``slack`` deliver through
live messaging transports. Qubit implements the complete code-side
adapter; the live network is the last mile and stays credential-gated:

* ``account-add`` — provider records (``whatsapp`` | ``whatsapp-cloud``
  | ``slack`` plus the Qubit-breadth channels ``discord`` | ``telegram``
  | ``signal`` | ``matrix`` | ``msteams`` | ``imessage``) with label,
  endpoint (provider allowlist enforced, lookalike hosts refused), origin
  binding, and a vault-only key reference (never a value). Explicit
  ``--confirm``. See :data:`PROVIDER_CAPS`: every recognized channel ships a
  bounded send/receive adapter seam (build the provider request, parse the
  event) behind an operator-injected transport. Without that transport (or
  with an unresolvable credential) the call is a typed ``UNAVAILABLE`` state
  (``MSG_TRANSPORT_UNAVAILABLE`` / ``MSG_NO_CREDENTIAL``), never a fake send.
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
import urllib.parse
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
        "detail": "Qubit ships a bounded REST send shaper for Discord "
                  "messages, but catalog availability stays UNAVAILABLE until "
                  "a live bot credential is resolved; no gateway voice/"
                  "transcripts",
    },
    "telegram": {
        "label": "Telegram",
        "transport": "bot-api",
        "available": False,
        "credential_kind": "bot-token",
        "detail": "Qubit ships a bounded Telegram sendMessage shaper, but "
                  "catalog availability stays UNAVAILABLE until a live bot "
                  "credential is resolved; no mini-app receiver",
    },
    "signal": {
        "label": "Signal",
        "transport": "signal-cli",
        "available": False,
        "credential_kind": "linked-device",
        "detail": "Qubit ships a bounded signal-cli send/receive adapter over "
                  "an injected transport; the live signal-cli linked device is "
                  "the credential/bridge gate",
    },
    "matrix": {
        "label": "Matrix",
        "transport": "client-server",
        "available": False,
        "credential_kind": "access-token",
        "detail": "Qubit ships a bounded Matrix client-server send/receive "
                  "adapter (client helper + E2EE seam) over an injected "
                  "transport; the live homeserver credential is the gate",
    },
    "msteams": {
        "label": "Microsoft Teams",
        "transport": "bot-framework",
        "available": False,
        "credential_kind": "bot-credentials",
        "detail": "Qubit ships a bounded Bot Framework send/receive adapter "
                  "over an injected transport; the Azure Bot registration/"
                  "credential is the gate",
    },
    "imessage": {
        "label": "iMessage",
        "transport": "local-bridge",
        "available": False,
        "credential_kind": "local-bridge",
        "detail": "Qubit ships a bounded macOS Messages bridge send/receive "
                  "adapter over an injected transport; the macOS bridge is the "
                  "gate",
    },
    "feishu": {
        "label": "Feishu / Lark",
        "transport": "webhook",
        "available": False,
        "credential_kind": "app-secret",
        "detail": "Qubit ships a bounded open-platform send/receive adapter and "
                  "dynamic-agent routing over an injected transport; the hosted "
                  "app credential is the gate",
    },
}

# Single source of truth for provider membership (order preserved).
PROVIDERS = tuple(PROVIDER_CAPS)

# Gateway/dashboard catalog spellings that differ from the Qubit provider ids
# in :data:`PROVIDER_CAPS`. The dashboard Channels page and the vendored
# gateway catalog address platforms by these ids, while the Qubit transport
# registry (and therefore ``msg-accounts.json``) keys accounts by the provider
# id. ``link``/``test_platform`` translate through this map so the test path
# probes the platform's real bound account instead of looking for an account
# whose id literally equals the platform id (which never matched).
PLATFORM_ALIASES: Dict[str, str] = {
    "whatsapp_cloud": "whatsapp-cloud",
    "teams": "msteams",
    "bluebubbles": "imessage",
}


def resolve_platform_provider(platform_id: str) -> str:
    """Map a catalog/gateway platform id to a Qubit provider id (``""`` if none)."""
    pid = (platform_id or "").strip().lower()
    if not pid:
        return ""
    if pid in PROVIDER_CAPS:
        return pid
    return PLATFORM_ALIASES.get(pid, "")

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


def _accounts_for_provider(cfg: Any, provider: str) -> List[Dict[str, Any]]:
    """Every recorded account bound to one provider (redacted records)."""
    if not provider:
        return []
    state = _load_json(_accounts_path(cfg), _blank_accounts, "accounts")
    return [rec for rec in state["accounts"].values()
            if isinstance(rec, dict) and rec.get("provider") == provider]


def link(cfg: Any, platform_id: str) -> Dict[str, Any]:
    """Typed link state: credential presence + transport + history.

    ``platform_id`` is first resolved as a Qubit account id (the CLI shape);
    when no such account exists it is resolved as a catalog/gateway *platform*
    id and probed against the accounts bound to that provider. A platform with
    no bound account is an honest typed ``NO_ACCOUNT`` (never a fabricated
    LINKED), and an id that is neither a known account nor a known platform is
    also ``NO_ACCOUNT``. No active network probing is performed: ``LINKED``
    means the credential reference resolves *and* Qubit ships a transport
    adapter for the provider (slack / whatsapp-cloud); every other provider
    resolves to the typed ``UNAVAILABLE`` state.
    """
    from .providers.credentials import resolve_key

    rec: Optional[Dict[str, Any]] = None
    try:
        rec = _get_account(cfg, platform_id)
    except MessagingError as exc:
        provider = resolve_platform_provider(platform_id)
        if provider:
            accounts = _accounts_for_provider(cfg, provider)
            if accounts:
                rec = accounts[0]
            else:
                return {"state": "NO_ACCOUNT", "code": NOT_FOUND_CODE,
                        "provider": provider, "platform": platform_id,
                        "detail": f"no Qubit account is bound to platform "
                                  f"{platform_id!r}; configure it before a "
                                  "connection can be tested"}
        else:
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


# ------------------------------------------------- connection test (dashboard)
def platform_test_verdict(info: Dict[str, Any]) -> Dict[str, Any]:
    """Honest connection-test verdict for one channel (reference contract).

    Ported from the vendored reference ``test_messaging_platform``
    (``web_routers/messaging.py:916``): a channel is reported ``ok`` **only**
    when a real connection is proven (``state == "connected"``). Every other
    outcome -- disabled, missing setup, gateway down, or a configured channel
    with no Qubit transport adapter -- is ``ok: false`` with an operator-facing
    ``message``. Nothing is sent and no provider socket is opened here.
    """
    name = str(info.get("name") or info.get("id") or "This channel")
    if not info.get("enabled"):
        return {"ok": False,
                "message": f"{name} is disabled. Enable it, then restart the "
                           "gateway."}
    if not info.get("configured"):
        missing = [str(k) for k in (info.get("missing_env") or ()) if k]
        if missing:
            return {"ok": False,
                    "message": "Missing required setup: " + ", ".join(missing)}
        return {"ok": False, "message": "Platform setup is incomplete."}
    if not info.get("gateway_running"):
        return {"ok": False,
                "message": "Gateway is not running. Restart the gateway to "
                           "connect this platform."}
    if str(info.get("state", "")) == "connected":
        return {"ok": True, "message": f"{name} is connected."}
    error_message = str(info.get("error_message") or "").strip()
    if error_message:
        return {"ok": False, "message": error_message}
    return {"ok": False,
            "message": "Setup looks complete, but the gateway has not reported "
                       "a connection yet. Restart the gateway."}


def platform_connection_test(cfg: Any, platform_id: str,
                             resolve: Any) -> tuple[int, Dict[str, Any]]:
    """Run the dashboard channel test with an honest, non-fabricated result.

    ``resolve(cfg, platform_id)`` returns the SAME payload the platform list
    serves (``name``/``enabled``/``configured``/``gateway_running``/``state``/
    ``error_message``/``missing_env``/``error_code``) or ``None`` for an unknown
    id. An unknown id is a typed ``404 MSG_NOT_FOUND``; a known id is ``200``
    with ``ok`` true **only** when the resolved state is a real ``connected``.
    The wire field is ``message`` (the shape the SPA and the reference use).
    """
    pid = str(platform_id or "").strip()
    if not pid:
        return 400, {"ok": False, "code": BAD_INPUT_CODE,
                     "detail": "platform_id required",
                     "qubit_counterpart": "CLI `qubit messaging accounts`"}
    info = resolve(cfg, pid) if callable(resolve) else None
    if not isinstance(info, dict):
        return 404, {"ok": False, "code": NOT_FOUND_CODE,
                     "detail": f"no channel {pid!r}",
                     "qubit_counterpart": "GET /api/messaging/platforms "
                                          "(real catalog)"}
    verdict = platform_test_verdict(info)
    state = str(info.get("state", "") or "")
    return 200, {
        "ok": bool(verdict["ok"]), "platform": pid, "state": state,
        "reachable": state == "connected", "message": verdict["message"],
        "error_code": info.get("error_code"),
        "link": info.get("link") if isinstance(info.get("link"), dict) else {},
        "detail": verdict["message"],
    }


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


# Provider transports that may be driven over an operator-injected boundary
# (the injected callable is the credential/network/bridge adapter). Every
# recognized channel that has a bounded Qubit send shaper is injectable; the
# remaining prerequisite is the live credential/bridge, never missing Qubit
# code. Classic WhatsApp stays session-based and is the only recognized channel
# with no request shaper at all.
_INJECTABLE_TRANSPORTS = frozenset({
    "slack", "whatsapp-cloud", "discord", "telegram", "matrix", "feishu",
    "signal", "msteams", "imessage"})


def _provider_request(account_provider: str, endpoint: str,
                      fields: Dict[str, str], to: str,
                      body: str) -> Dict[str, Any]:
    """Normalize a provider send into one bounded request mapping.

    Used by the injected-transport boundary (and as the single description of
    each send's method/url/body). The secret is never part of the request
    mapping: a real transport pulls it from the resolved credential.
    """
    if account_provider == "slack":
        return {"provider": "slack", "method": "POST", "url": endpoint,
                "headers": {"Content-Type": "application/json"},
                "body": {"channel": to, "text": body}}
    if account_provider == "whatsapp-cloud":
        return {"provider": "whatsapp-cloud", "method": "POST",
                "url": endpoint, "headers": {"Content-Type": "application/json"},
                "body": {"messaging_product": "whatsapp", "to": to,
                         "type": "text", "text": {"body": body}}}
    if account_provider == "discord":
        return {"provider": "discord", "method": "POST",
                "url": ("https://discord.com/api/v10/channels/"
                        f"{urllib.parse.quote(str(to), safe='')}/messages"),
                "headers": {"Content-Type": "application/json"},
                "body": {"content": body}}
    if account_provider == "telegram":
        return {"provider": "telegram", "method": "POST",
                "url": "https://api.telegram.org/bot<token>/sendMessage",
                "headers": {"Content-Type": "application/json"},
                "body": {"chat_id": to, "text": body}}
    if account_provider == "matrix":
        homeserver = str(fields.get("homeserver", "")).strip().rstrip("/")
        if not homeserver:
            return {}
        txn = uuid.uuid4().hex
        return {"provider": "matrix", "method": "PUT",
                "url": (f"{homeserver}/_matrix/client/v3/rooms/"
                        f"{urllib.parse.quote(str(to), safe='')}"
                        f"/send/m.room.message/{txn}"),
                "headers": {"Content-Type": "application/json"},
                "body": {"msgtype": "m.text", "body": body}}
    if account_provider == "feishu":
        webhook = str(fields.get("webhook_url", "")).strip()
        if not webhook:
            return {}
        return {"provider": "feishu", "method": "POST", "url": webhook,
                "headers": {"Content-Type": "application/json"},
                "body": {"msg_type": "text", "content": {"text": body}}}
    if account_provider == "signal":
        base = (str(fields.get("signal_cli_url", "")).strip().rstrip("/")
                or "http://127.0.0.1:8080")
        return {"provider": "signal", "type": "send", "method": "POST",
                "url": f"{base}/api/v1/rpc",
                "headers": {"Content-Type": "application/json"},
                "body": {"jsonrpc": "2.0", "method": "send",
                         "params": {"recipient": [str(to)], "message": body},
                         "id": uuid.uuid4().hex}}
    if account_provider == "msteams":
        service = (str(fields.get("service_url", "")).strip().rstrip("/")
                   or "https://smba.trafficmanager.net/teams")
        return {"provider": "msteams", "type": "message", "method": "POST",
                "url": (f"{service}/v3/conversations/"
                        f"{urllib.parse.quote(str(to), safe='')}/activities"),
                "headers": {"Content-Type": "application/json"},
                "body": {"type": "message", "text": body}}
    if account_provider == "imessage":
        base = (str(fields.get("bridge_url", "")).strip().rstrip("/")
                or "http://127.0.0.1:8080")
        return {"provider": "imessage", "type": "send", "method": "POST",
                "url": f"{base}/api/send",
                "headers": {"Content-Type": "application/json"},
                "body": {"to": str(to), "text": body}}
    return {}


def _receipt_from(account_provider: str, data: Any) -> Dict[str, Any]:
    """Map a provider/injected response to a bounded ``provider_msg_id``."""
    if not isinstance(data, dict):
        raise MessagingError(UPSTREAM_CODE,
                             "provider response was not a JSON object")
    if data.get("provider_msg_id"):
        return {"provider_msg_id": _redact(str(data["provider_msg_id"]))[:120]}
    if account_provider == "discord" and data.get("id"):
        return {"provider_msg_id": str(data["id"])}
    if account_provider == "telegram":
        result = data.get("result")
        if isinstance(result, dict) and result.get("message_id") is not None:
            return {"provider_msg_id": str(result["message_id"])}
        if data.get("message_id") is not None:
            return {"provider_msg_id": str(data["message_id"])}
    if account_provider == "slack" and data.get("ts"):
        return {"provider_msg_id": str(data["ts"])}
    if account_provider == "matrix" and data.get("event_id"):
        return {"provider_msg_id": str(data["event_id"])}
    if account_provider == "feishu":
        inner = data.get("data")
        mid = (inner.get("message_id") if isinstance(inner, dict)
               else data.get("message_id"))
        if mid:
            return {"provider_msg_id": str(mid)}
    if account_provider == "signal":
        result = data.get("result") if isinstance(data.get("result"), dict) \
            else data
        for key in ("timestamp", "id", "message_id"):
            if result.get(key) is not None:
                return {"provider_msg_id": str(result[key])[:120]}
    if account_provider == "msteams" and data.get("id"):
        return {"provider_msg_id": str(data["id"])[:120]}
    if account_provider == "imessage":
        for key in ("id", "guid", "message_id"):
            if data.get(key):
                return {"provider_msg_id": str(data[key])[:120]}
    msgs = data.get("messages")
    if isinstance(msgs, list) and msgs and isinstance(msgs[0], dict) \
            and "id" in msgs[0]:
        return {"provider_msg_id": str(msgs[0]["id"])}
    raise MessagingError(UPSTREAM_CODE, "provider returned no message id")


def _transport(account_provider: str, endpoint: str, fields: Dict[str, str],
               to: str, body: str, key: str,
               transport: Any = None) -> Dict[str, Any]:
    """Real provider POST (key in Authorization header only).

    When ``transport`` is supplied it is the operator-injected boundary: the
    normalized request is handed to ``transport(request)`` and no network is
    opened by Qubit. Only channels with a real Qubit-side adapter are
    injectable; the rest stay typed ``UNAVAILABLE`` (never a fabricated send).
    """
    import json as _json

    if transport is not None:
        if account_provider not in _INJECTABLE_TRANSPORTS:
            caps = PROVIDER_CAPS.get(account_provider, {})
            raise MessagingError(
                UNAVAILABLE_CODE,
                f"{caps.get('label', account_provider)} has no Qubit-injectable "
                f"adapter ({caps.get('detail', 'transport unavailable')}); "
                "nothing was sent")
        request = _provider_request(account_provider, endpoint, fields, to,
                                    body)
        if not request:
            raise MessagingError(
                UNAVAILABLE_CODE,
                f"{account_provider} transport is missing its endpoint/host "
                "fields; nothing was sent")
        try:
            data = transport(request)
        except MessagingError:
            raise
        except Exception as exc:
            detail = _redact(str(exc))
            if key:
                detail = detail.replace(key, "<redacted>")
            raise MessagingError(UPSTREAM_CODE,
                                 f"provider request failed: {detail}") from exc
        return _receipt_from(account_provider, data)

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
    elif account_provider == "discord":
        payload = _json.dumps({"content": body}).encode()
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bot {key}",
                   "User-Agent": "Qubit (https://localhost, 1.0)"}
        url = ("https://discord.com/api/v10/channels/"
               f"{urllib.parse.quote(str(to), safe='')}/messages")
        req = urllib.request.Request(url, data=payload, headers=headers)
    elif account_provider == "telegram":
        payload = _json.dumps({"chat_id": to, "text": body}).encode()
        headers = {"Content-Type": "application/json"}
        url = f"https://api.telegram.org/bot{key}/sendMessage"
        req = urllib.request.Request(url, data=payload, headers=headers)
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
        detail = _redact(str(exc))
        if key:
            detail = detail.replace(key, "<redacted>")
        raise MessagingError(UPSTREAM_CODE,
                             f"provider request failed: {detail}") from exc
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
    if account_provider == "telegram":
        result = data.get("result") if isinstance(data, dict) else None
        if not (isinstance(data, dict) and data.get("ok") is True
                and isinstance(result, dict)):
            raise MessagingError(UPSTREAM_CODE,
                                 "telegram refused the message")
        return {"provider_msg_id": str(result.get("message_id", ""))}
    if account_provider == "discord":
        if not (isinstance(data, dict) and data.get("id")):
            raise MessagingError(UPSTREAM_CODE,
                                 "discord refused the message")
        return {"provider_msg_id": str(data.get("id", ""))}
    msgs = data.get("messages", []) if isinstance(data, dict) else []
    if not msgs or not isinstance(msgs[0], dict) or "id" not in msgs[0]:
        raise MessagingError(UPSTREAM_CODE, "provider bad receipt")
    return {"provider_msg_id": str(msgs[0]["id"])}


def send(cfg: Any, msg_id: str, *, requested_by: str = "cli:user",
         confirmed: bool = False, transport: Any = None) -> Dict[str, Any]:
    """Send with fresh exact confirmation through every gate in order.

    ``transport`` is an optional operator-injected provider boundary; when
    omitted the shipped HTTPS adapter is used for the channels that have one.
    """
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
    caps = PROVIDER_CAPS.get(rec["provider"], {})
    if transport is None and rec["provider"] != "whatsapp" \
            and not caps.get("available", False):
        # The catalog reports no SHIPPED transport for this channel; the
        # default send must agree with it rather than silently POSTing. An
        # operator-injected transport may still drive the adapter contract.
        rec["state"] = QUEUED
        rec["updated_at"] = _now()
        _save_msg(cfg, state)
        raise MessagingError(
            UNAVAILABLE_CODE,
            f"{caps.get('label', rec['provider'])} has no shipped Qubit "
            f"transport adapter ({caps.get('detail', 'transport unavailable')}); "
            "inject a transport to drive the adapter contract")
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
                            rec["body"], key, transport)
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
          confirmed: bool = False, transport: Any = None) -> Dict[str, Any]:
    """Re-attempt a queued/failed message (same gates as send)."""
    if not confirmed:
        raise MessagingError(CONFIRM_CODE,
                             "message retry requires explicit --confirm")
    state, rec = _get_msg(cfg, msg_id)
    if rec["state"] not in (QUEUED, FAILED):
        raise MessagingError(BAD_INPUT_CODE,
                             f"message is {rec['state']}; nothing to retry")
    return send(cfg, rec["id"], requested_by=requested_by, confirmed=True,
                transport=transport)


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


# ---------------------------------------------------------------------------
# OC-D12/D13/D17: channel feature adapters (voice / rich media / E2EE / agents)
#
# The code-side half of the named Qubit channel features. Qubit performs no
# network or crypto I/O itself: the caller injects the transport (the live
# bot/crypto boundary). Without one every call is a typed UNAVAILABLE, never a
# fabricated success; each call is estop + confirmation + credential-reference
# gated and audited redacted. No credential value is stored or returned.
# ---------------------------------------------------------------------------
MATRIX_E2EE_CODE = "MSG_MATRIX_E2EE_UNAVAILABLE"
MAX_RICH_ITEMS = 10
MAX_RICH_BYTES = 16_000
MAX_TRANSCRIPT_CHARS = 8000
FEISHU_AGENTS_NAME = "feishu-agents.json"


def _bounded_items(items: Any, *, label: str,
                   max_items: int = MAX_RICH_ITEMS) -> List[Any]:
    """Validate a bounded list of JSON-serializable rich items."""
    if items is None:
        return []
    if not isinstance(items, (list, tuple)):
        raise MessagingError(BAD_INPUT_CODE, f"{label} must be a list")
    if len(items) > max_items:
        raise MessagingError(BAD_INPUT_CODE,
                             f"{label} exceeds {max_items} items")
    out: List[Any] = []
    for item in items:
        if not isinstance(item, dict):
            raise MessagingError(BAD_INPUT_CODE,
                                 f"{label} items must be mappings")
        try:
            raw = json.dumps(item, default=str)
        except (TypeError, ValueError) as exc:
            raise MessagingError(
                BAD_INPUT_CODE, f"{label} not serializable: {exc}") from exc
        if len(raw.encode("utf-8")) > MAX_RICH_BYTES:
            raise MessagingError(BAD_INPUT_CODE,
                                 f"{label} item exceeds the size cap")
        out.append(item)
    return out


def _require_id(value: Any, label: str, *, max_len: int = 200) -> str:
    text = str(value or "").strip()
    if not text or len(text) > max_len:
        raise MessagingError(BAD_INPUT_CODE,
                             f"{label} must be 1..{max_len} chars")
    return text


def _feature_call(cfg: Any, account_id: str, provider: str, build: Any, *,
                  operation: str, confirmed: bool, transport: Any,
                  unavailable: str,
                  requested_by: str = "cli:user") -> tuple:
    """Shared gate for a channel feature adapter.

    Order: estop -> confirmation -> account/provider match -> credential
    reference resolvable -> injected transport present -> dispatch. The build
    callable receives the account record and returns the bounded request. A
    missing transport or credential is a typed refusal with nothing attempted.
    """
    from .plugins import estop_engaged
    from .providers.credentials import resolve_key

    if estop_engaged(cfg):
        raise MessagingError(ESTOP_CODE,
                             f"{operation}: emergency stop is latched")
    if not confirmed:
        raise MessagingError(CONFIRM_CODE,
                             f"{operation} requires explicit --confirm")
    rec = _get_account(cfg, account_id)
    if rec.get("provider") != provider:
        raise MessagingError(
            BAD_INPUT_CODE,
            f"account {account_id!r} is {rec.get('provider')!r}, not "
            f"{provider!r}")
    key, code = resolve_key(provider, _resolve_account_ref(rec))
    if code or not key:
        raise MessagingError(
            NO_CREDENTIAL_CODE,
            f"{operation}: credential reference unresolvable; nothing "
            "attempted")
    if transport is None:
        raise MessagingError(UNAVAILABLE_CODE, unavailable)
    if not callable(transport):
        raise MessagingError(BAD_INPUT_CODE, "transport must be callable")
    request = build(rec) if callable(build) else build
    if not isinstance(request, dict) or not request:
        raise MessagingError(UNAVAILABLE_CODE,
                             f"{operation}: no bounded request could be built")
    try:
        data = transport(request)
    except MessagingError:
        raise
    except Exception as exc:
        detail = _redact(str(exc))
        if key:
            detail = detail.replace(key, "<redacted>")
        raise MessagingError(UPSTREAM_CODE,
                             f"{operation} failed: {detail}") from exc
    if not isinstance(data, dict):
        raise MessagingError(UPSTREAM_CODE,
                             f"{operation}: transport returned a non-object")
    _audit(operation, requested_by,
           {"account": rec["id"], "provider": provider})
    return rec, data


# ------------------------------------------------------------------ Discord
def discord_voice_state(cfg: Any, account_id: str, *, channel_id: Any,
                        guild_id: Any = "", join: bool = True,
                        self_mute: bool = True, self_deaf: bool = True,
                        confirmed: bool = False, transport: Any = None,
                        requested_by: str = "cli:user") -> Dict[str, Any]:
    """Join/leave a Discord voice channel (OC-D12) over a bot transport."""
    ch = _require_id(channel_id, "channel_id")
    request = {"provider": "discord", "type": "voice.state_update", "op": 4,
               "d": {"guild_id": str(guild_id or "")[:64],
                     "channel_id": ch if join else None,
                     "self_mute": bool(self_mute),
                     "self_deaf": bool(self_deaf)}}
    rec, data = _feature_call(
        cfg, account_id, "discord", request,
        operation="discord.voice", confirmed=confirmed, transport=transport,
        unavailable="Discord voice needs an injected gateway/bot transport; "
                    "no voice state was sent",
        requested_by=requested_by)
    return {"state": "OK", "provider": "discord",
            "operation": "voice.state_update", "channel_id": ch,
            "joined": bool(join),
            "provider_msg_id": str(data.get("id", ""))[:120]}


def discord_voice_follow(cfg: Any, account_id: str, *, channel_id: Any,
                         user_id: Any, confirmed: bool = False,
                         transport: Any = None,
                         requested_by: str = "cli:user") -> Dict[str, Any]:
    """Follow a user across Discord voice channels (OC-D12)."""
    ch = _require_id(channel_id, "channel_id")
    uid = _require_id(user_id, "user_id")
    request = {"provider": "discord", "type": "voice.follow",
               "channel_id": ch, "user_id": uid, "d": {"auto_follow": True}}
    rec, data = _feature_call(
        cfg, account_id, "discord", request,
        operation="discord.voice_follow", confirmed=confirmed,
        transport=transport,
        unavailable="Discord voice-follow needs an injected voice transport; "
                    "no follow was requested",
        requested_by=requested_by)
    return {"state": "OK", "provider": "discord", "channel_id": ch,
            "following": uid}


def discord_voice_transcript(cfg: Any, account_id: str, *, channel_id: Any,
                             confirmed: bool = False, transport: Any = None,
                             requested_by: str = "cli:user") -> Dict[str, Any]:
    """Fetch a bounded voice transcript (OC-D12) over a bot transport."""
    ch = _require_id(channel_id, "channel_id")
    request = {"provider": "discord", "type": "voice.transcript",
               "channel_id": ch}
    rec, data = _feature_call(
        cfg, account_id, "discord", request,
        operation="discord.voice_transcript", confirmed=confirmed,
        transport=transport,
        unavailable="Discord voice transcripts need an injected voice "
                    "transport; nothing was captured",
        requested_by=requested_by)
    text = data.get("transcript") or data.get("text") or ""
    return {"state": "OK", "provider": "discord", "channel_id": ch,
            "transcript": _redact(str(text))[:MAX_TRANSCRIPT_CHARS],
            "segments": int(data.get("segments") or 0)}


def discord_rich_message(cfg: Any, account_id: str, *, channel_id: Any,
                         content: str = "", embeds: Any = None,
                         components: Any = None, confirmed: bool = False,
                         transport: Any = None,
                         requested_by: str = "cli:user") -> Dict[str, Any]:
    """Send a bounded rich Discord message (embeds/components, OC-D12)."""
    ch = _require_id(channel_id, "channel_id")
    body = {"content": _redact(str(content or ""))[:MAX_BODY_CHARS],
            "embeds": _bounded_items(embeds, label="embeds"),
            "components": _bounded_items(components, label="components",
                                         max_items=5)}
    if not body["content"] and not body["embeds"] and not body["components"]:
        raise MessagingError(BAD_INPUT_CODE,
                             "rich message needs content, embeds, or "
                             "components")
    request = {"provider": "discord", "type": "message.create",
               "method": "POST",
               "url": ("https://discord.com/api/v10/channels/"
                       f"{urllib.parse.quote(ch, safe='')}/messages"),
               "body": body}
    rec, data = _feature_call(
        cfg, account_id, "discord", request,
        operation="discord.rich_message", confirmed=confirmed,
        transport=transport,
        unavailable="Discord rich messages need an injected bot transport; "
                    "nothing was sent",
        requested_by=requested_by)
    return {"state": "OK", "provider": "discord",
            "provider_msg_id": str(data.get("id", ""))[:120]}


# ----------------------------------------------------------------- Telegram
def telegram_album(cfg: Any, account_id: str, *, chat_id: Any, media: Any,
                   caption: str = "", confirmed: bool = False,
                   transport: Any = None,
                   requested_by: str = "cli:user") -> Dict[str, Any]:
    """Send a bounded Telegram photo/video album via sendMediaGroup (OC-D13)."""
    chat = _require_id(chat_id, "chat_id", max_len=64)
    items = _bounded_items(media, label="media", max_items=10)
    if not items:
        raise MessagingError(BAD_INPUT_CODE,
                             "album needs at least one media item")
    for item in items:
        kind = str(item.get("type", "")).strip().lower()
        ref = str(item.get("media", "")).strip()
        if kind not in ("photo", "video", "document", "audio"):
            raise MessagingError(
                BAD_INPUT_CODE, f"unsupported album item type {kind!r}")
        if not ref or len(ref) > 1024:
            raise MessagingError(BAD_INPUT_CODE,
                                 "album media reference is invalid")
    request = {"provider": "telegram", "type": "sendMediaGroup",
               "body": {"chat_id": chat, "media": items,
                        "caption": _redact(str(caption or ""))[:1024]}}
    rec, data = _feature_call(
        cfg, account_id, "telegram", request,
        operation="telegram.album", confirmed=confirmed,
        transport=transport,
        unavailable="Telegram albums need an injected bot transport; nothing "
                    "was sent",
        requested_by=requested_by)
    result = data.get("result")
    first = result[0] if isinstance(result, list) and result else {}
    mid = first.get("message_id") if isinstance(first, dict) else ""
    return {"state": "OK", "provider": "telegram", "count": len(items),
            "provider_msg_id": str(mid) if mid != "" else ""}


def telegram_rich(cfg: Any, account_id: str, *, chat_id: Any, text: str,
                  parse_mode: str = "", entities: Any = None,
                  confirmed: bool = False, transport: Any = None,
                  requested_by: str = "cli:user") -> Dict[str, Any]:
    """Send a bounded rich Telegram message (parse_mode/entities, OC-D13)."""
    chat = _require_id(chat_id, "chat_id", max_len=64)
    body: Dict[str, Any] = {
        "chat_id": chat,
        "text": _redact(str(text or ""))[:MAX_BODY_CHARS]}
    if parse_mode:
        if parse_mode not in ("Markdown", "MarkdownV2", "HTML"):
            raise MessagingError(
                BAD_INPUT_CODE,
                "parse_mode must be Markdown, MarkdownV2, or HTML")
        body["parse_mode"] = parse_mode
    ents = _bounded_items(entities, label="entities", max_items=50)
    if ents:
        body["entities"] = ents
    request = {"provider": "telegram", "type": "sendMessage", "body": body}
    rec, data = _feature_call(
        cfg, account_id, "telegram", request,
        operation="telegram.rich", confirmed=confirmed, transport=transport,
        unavailable="Telegram rich messages need an injected bot transport; "
                    "nothing was sent",
        requested_by=requested_by)
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    return {"state": "OK", "provider": "telegram",
            "provider_msg_id": str(result.get("message_id", ""))}


def telegram_mini_app(cfg: Any, account_id: str, *, chat_id: Any, text: str,
                      mini_app_url: Any, confirmed: bool = False,
                      transport: Any = None,
                      requested_by: str = "cli:user") -> Dict[str, Any]:
    """Send a Telegram mini-app (WebApp) button (OC-D13)."""
    chat = _require_id(chat_id, "chat_id", max_len=64)
    url = str(mini_app_url or "").strip()
    if not url.startswith("https://") or len(url) > 1024:
        raise MessagingError(BAD_INPUT_CODE,
                             "mini_app_url must be a bounded https URL")
    body = {"chat_id": chat,
            "text": _redact(str(text or ""))[:MAX_BODY_CHARS],
            "reply_markup": {"inline_keyboard": [
                [{"text": "Open", "web_app": {"url": url}}]]}}
    request = {"provider": "telegram", "type": "sendMessage", "body": body}
    rec, data = _feature_call(
        cfg, account_id, "telegram", request,
        operation="telegram.mini_app", confirmed=confirmed,
        transport=transport,
        unavailable="Telegram mini-apps need an injected bot transport; "
                    "nothing was sent",
        requested_by=requested_by)
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    return {"state": "OK", "provider": "telegram",
            "mini_app_url": url,
            "provider_msg_id": str(result.get("message_id", ""))}


def telegram_validate_init_data(init_data: Any, bot_token: Any) -> Dict[str, Any]:
    """Validate Telegram WebApp ``initData`` with the bot token (OC-D13).

    Pure, local HMAC-SHA256 verification of the documented Telegram scheme
    (``secret = HMAC_SHA256(bot_token, "WebAppData")``). Never returns the
    token and never makes a network call; a malformed or unsigned blob is a
    typed INVALID result rather than an exception.
    """
    import hmac as _hmac

    raw = str(init_data or "")
    token = str(bot_token or "")
    if not raw or not token:
        return {"state": "INVALID", "valid": False, "code": BAD_INPUT_CODE,
                "reason": "initData and bot token are required"}
    try:
        pairs = urllib.parse.parse_qsl(raw, keep_blank_values=True)
    except ValueError:
        return {"state": "INVALID", "valid": False, "code": BAD_INPUT_CODE,
                "reason": "initData is not a query string"}
    fields = {str(k): str(v) for k, v in pairs}
    presented = fields.pop("hash", "")
    if not presented:
        return {"state": "INVALID", "valid": False, "code": BAD_INPUT_CODE,
                "reason": "initData has no hash"}
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = _hmac.new(b"WebAppData", token.encode("utf-8"),
                       hashlib.sha256).digest()
    expected = _hmac.new(secret, check.encode("utf-8"),
                         hashlib.sha256).hexdigest()
    valid = _hmac.compare_digest(expected, presented.lower())
    auth_date: Any = fields.get("auth_date", "")
    try:
        auth_date = int(auth_date)
    except (TypeError, ValueError):
        auth_date = 0
    return {"state": "OK" if valid else "INVALID", "valid": bool(valid),
            "auth_date": auth_date,
            "user": _redact(fields.get("user", ""))[:400]}


# ------------------------------------------------------------------- Matrix
def matrix_accounts(cfg: Any) -> Dict[str, Any]:
    """Read-only multi-account Matrix homeserver inventory (OC-D17)."""
    state = _load_json(_accounts_path(cfg), _blank_accounts, "accounts")
    rows: List[Dict[str, Any]] = []
    for rec in state["accounts"].values():
        if rec.get("provider") != "matrix":
            continue
        fields = rec.get("fields", {}) if isinstance(rec.get("fields"), dict) \
            else {}
        rows.append({"account": rec.get("id", ""),
                     "homeserver": _redact(
                         str(fields.get("homeserver", "")))[:200],
                     "device_id": _redact(
                         str(fields.get("device_id", "")))[:64],
                     "key_ref_kind": rec.get("key_ref_kind", "missing")})
    return {"state": "OK", "accounts": rows,
            "homeservers": sorted({r["homeserver"] for r in rows
                                   if r["homeserver"]})}


def matrix_e2ee_send(cfg: Any, account_id: str, *, room_id: Any, body: str,
                     algorithm: str = "m.megolm.v1.aes-sha2",
                     crypto: Any = None, transport: Any = None,
                     confirmed: bool = False,
                     requested_by: str = "cli:user") -> Dict[str, Any]:
    """Send an end-to-end-encrypted Matrix event (OC-D17).

    E2EE requires an injected olm/megolm crypto boundary (Qubit ships no
    crypto stack) *and* an injected access-token transport. Without the crypto
    boundary the call is a typed ``MSG_MATRIX_E2EE_UNAVAILABLE``; without the
    transport it is the shared UNAVAILABLE gate. Plaintext is never sent as a
    fallback.
    """
    room = _require_id(room_id, "room_id")
    if not callable(crypto):
        raise MessagingError(
            MATRIX_E2EE_CODE,
            "Matrix E2EE needs an injected olm/megolm crypto boundary; "
            "nothing was sent")
    try:
        envelope = crypto({"body": str(body or ""), "room_id": room,
                           "algorithm": algorithm})
    except Exception as exc:
        raise MessagingError(
            UPSTREAM_CODE,
            f"matrix crypto boundary failed: {_redact(str(exc))[:160]}"
        ) from exc
    if not isinstance(envelope, dict) or not envelope.get("ciphertext"):
        raise MessagingError(UPSTREAM_CODE,
                             "crypto boundary returned no ciphertext")

    def _build(rec: Dict[str, Any]) -> Dict[str, Any]:
        fields = rec.get("fields", {}) if isinstance(rec.get("fields"), dict) \
            else {}
        homeserver = str(fields.get("homeserver", "")).strip().rstrip("/")
        txn = uuid.uuid4().hex
        return {"provider": "matrix", "type": "m.room.encrypted",
                "method": "PUT",
                "url": (f"{homeserver}/_matrix/client/v3/rooms/"
                        f"{urllib.parse.quote(room, safe='')}"
                        f"/send/m.room.encrypted/{txn}"),
                "body": {
                    "algorithm": str(envelope.get("algorithm")
                                     or algorithm)[:64],
                    "ciphertext": str(envelope["ciphertext"])[:100_000],
                    "sender_key": str(envelope.get("sender_key", ""))[:128],
                    "device_id": str(envelope.get("device_id", ""))[:64],
                    "session_id": str(envelope.get("session_id", ""))[:128]}}

    rec, data = _feature_call(
        cfg, account_id, "matrix", _build,
        operation="matrix.e2ee_send", confirmed=confirmed,
        transport=transport,
        unavailable="Matrix E2EE needs an injected access-token transport; "
                    "nothing was sent",
        requested_by=requested_by)
    return {"state": "OK", "provider": "matrix", "room_id": room,
            "encrypted": True,
            "provider_msg_id": str(data.get("event_id", ""))[:200]}


# ------------------------------------------------------------------- Feishu
def _feishu_agents_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / FEISHU_AGENTS_NAME


def _blank_agents() -> Dict[str, Any]:
    return {"schema": SCHEMA, "agents": {}}


def _check_allowlist(allowlist: Any) -> List[str]:
    if allowlist is None:
        return []
    if isinstance(allowlist, str):
        allowlist = [a for a in allowlist.replace(",", " ").split() if a]
    if not isinstance(allowlist, (list, tuple)):
        raise MessagingError(BAD_INPUT_CODE, "allowlist must be a list")
    out: List[str] = []
    for item in allowlist:
        text = str(item or "").strip()[:120]
        if text and text not in out:
            out.append(text)
    if len(out) > 50:
        raise MessagingError(BAD_INPUT_CODE, "allowlist exceeds 50 entries")
    return out


def feishu_agent_add(cfg: Any, agent_id: Any, *, app_id_ref: str,
                     allowlist: Any = None, commands: Any = None,
                     confirmed: bool = False,
                     requested_by: str = "cli:user") -> Dict[str, Any]:
    """Record a dynamic Feishu agent route (OC-D16).

    Stores only a routing record: the open-platform ``app_id`` *reference*
    (``env:``/``file:``), an optional chat allowlist, and optional command
    allowlist. The secret value is never stored. Explicit confirmation.
    """
    if not confirmed:
        raise MessagingError(CONFIRM_CODE,
                             "feishu agent add requires explicit --confirm")
    clean = _check_id(agent_id)
    ref = (app_id_ref or "").strip()
    if _secret_kind(ref) == "missing":
        raise MessagingError(BAD_INPUT_CODE,
                             "app_id_ref must be an env:VAR or file:PATH "
                             "reference")
    cmds = _check_allowlist(commands)
    state = _load_json(_feishu_agents_path(cfg), _blank_agents, "feishu agents")
    if clean in state["agents"]:
        raise MessagingError(EXISTS_CODE, f"agent {clean!r} exists")
    rec = {"id": clean, "app_id_ref_kind": _secret_kind(ref),
           "allowlist": _check_allowlist(allowlist), "commands": cmds,
           "enabled": True, "created_at": _now(),
           "updated_by": requested_by}
    if ref.startswith("env:"):
        rec["_app_id_name"] = ref[len("env:"):].strip()[:64]
    else:
        rec["_app_id_path"] = ref
    state["agents"][clean] = rec
    _save_json(_feishu_agents_path(cfg), state, "feishu agents")
    _audit("feishu.agent_add", requested_by, {"agent": clean})
    return {"state": "OK", "agent": {k: v for k, v in rec.items()
                                     if not k.startswith("_app_id")}}


def feishu_agents(cfg: Any) -> Dict[str, Any]:
    """Read-only dynamic-agent inventory (redacted references)."""
    state = _load_json(_feishu_agents_path(cfg), _blank_agents, "feishu agents")
    return {"state": "OK",
            "agents": [{k: v for k, v in rec.items()
                        if not k.startswith("_app_id")}
                       for rec in sorted(state["agents"].values(),
                                         key=lambda r: r.get("id", ""))]}


def feishu_agent_remove(cfg: Any, agent_id: Any, *, confirmed: bool = False,
                        requested_by: str = "cli:user") -> Dict[str, Any]:
    """Remove a dynamic Feishu agent route (confirmed)."""
    if not confirmed:
        raise MessagingError(CONFIRM_CODE,
                             "feishu agent remove requires explicit --confirm")
    state = _load_json(_feishu_agents_path(cfg), _blank_agents, "feishu agents")
    clean = _check_id(agent_id)
    if clean not in state["agents"]:
        raise MessagingError(NOT_FOUND_CODE, f"no agent {agent_id!r}")
    del state["agents"][clean]
    _save_json(_feishu_agents_path(cfg), state, "feishu agents")
    _audit("feishu.agent_remove", requested_by, {"agent": clean})
    return {"state": "OK", "removed": clean}


def feishu_send_as_agent(cfg: Any, account_id: str, *, to: Any, body: str,
                         agent_id: Any, confirmed: bool = False,
                         transport: Any = None,
                         requested_by: str = "cli:user") -> Dict[str, Any]:
    """Send as a configured dynamic Feishu agent (OC-D16).

    Routes the send through the stored agent record and the injected
    open-platform transport. An unknown/disabled agent is a typed refusal;
    a chat outside the agent's allowlist is refused before any transport call.
    """
    clean = _check_id(agent_id)
    agents = _load_json(_feishu_agents_path(cfg), _blank_agents,
                        "feishu agents")["agents"]
    agent = agents.get(clean)
    if agent is None:
        raise MessagingError(NOT_FOUND_CODE, f"no agent {agent_id!r}")
    if agent.get("enabled") is False:
        raise MessagingError(BAD_INPUT_CODE, f"agent {agent_id!r} is disabled")
    allow = agent.get("allowlist", [])
    target = _require_id(to, "recipient", max_len=MAX_TO_LEN)
    if allow and target not in allow and "*" not in allow:
        raise MessagingError(BAD_INPUT_CODE,
                             f"recipient {target!r} is not in agent "
                             f"{agent_id!r}'s allowlist")

    def _build(rec: Dict[str, Any]) -> Dict[str, Any]:
        fields = rec.get("fields", {}) if isinstance(rec.get("fields"), dict) \
            else {}
        webhook = str(fields.get("webhook_url", "")).strip()
        return {"provider": "feishu", "type": "message.create",
                "agent_id": clean, "url": webhook,
                "body": {"msg_type": "text",
                         "content": {"text": _redact(str(body or ""))
                                     [:MAX_BODY_CHARS]},
                         "agent_id": clean}}

    rec, data = _feature_call(
        cfg, account_id, "feishu", _build,
        operation="feishu.send_as_agent", confirmed=confirmed,
        transport=transport,
        unavailable="Feishu dynamic agents need an injected open-platform "
                    "transport; nothing was sent",
        requested_by=requested_by)
    inner = data.get("data") if isinstance(data.get("data"), dict) else {}
    mid = inner.get("message_id") or data.get("message_id", "")
    return {"state": "OK", "provider": "feishu", "agent_id": clean,
            "provider_msg_id": str(mid)[:120]}
