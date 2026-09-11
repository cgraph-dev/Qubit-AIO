"""Qubit inbound channel framework breadth (OpenClaw audit #19/#20).

Qubit recognizes the OpenClaw-breadth channel set and gives each an honest,
credential-reference-derived state. It starts NO socket listener and opens
NO outbound connection: inbound delivery is expected to arrive through the
dashboard/CLI HTTP ingress, which calls :func:`receive` with an
already-authenticated payload. Until a channel has both a configured
credential *reference* and a real transport adapter, every state resolves to a
typed ``CHANNEL_UNAVAILABLE`` -- never a fabricated ``CONNECTED``.

* ``catalog``/``status`` project the static :data:`CHANNEL_CAPS` registry; a
  channel is ``UNCONFIGURED``, ``NO_CREDENTIAL``, ``UNAVAILABLE``, or
  ``DISABLED``, and the ``connected`` list is always empty.
* ``configure``/``unconfigure`` record only an ``env:``/``file:`` credential
  reference (never a value), with events, confirmation, estop, and bounds.
* ``receive`` reuses the webhook ingress kernel: body cap, event allowlist,
  HMAC-SHA256 signature, timestamp freshness, and a bounded replay nonce
  store. A fully verified delivery still resolves to the typed
  ``CHANNEL_UNAVAILABLE`` gate (signature evidence only) because no listener
  transport exists; nothing is dispatched or executed.

References/state live in ``<state_dir>/channels-inbound.json``; replay nonces
in ``channel-nonces.json``; redacted receipts in ``channel-log.jsonl``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = "qubit-channels-inbound/1"
STORE_NAME = "channels-inbound.json"
NONCE_NAME = "channel-nonces.json"
LOG_NAME = "channel-log.jsonl"
MAX_FILE_BYTES = 1_000_000
MAX_BODY_BYTES = 1_000_000
MAX_SENDER_LEN = 160
MAX_EVENT_LEN = 64
MAX_NONCES = 5000
NONCE_TTL_S = 3600.0
TS_TOLERANCE_S = 300.0
MAX_LOG_LINES = 2000

CONFIRM_CODE = "CHANNEL_CONFIRM_REQUIRED"
ESTOP_CODE = "CHANNEL_ESTOP"
UNKNOWN_CODE = "CHANNEL_UNKNOWN"
EXISTS_CODE = "CHANNEL_EXISTS"
NOT_FOUND_CODE = "CHANNEL_NOT_FOUND"
BAD_INPUT_CODE = "CHANNEL_BAD_INPUT"
NO_CREDENTIAL_CODE = "CHANNEL_NO_CREDENTIAL"
UNAVAILABLE_CODE = "CHANNEL_UNAVAILABLE"
BAD_SIGNATURE_CODE = "CHANNEL_BAD_SIGNATURE"
STALE_CODE = "CHANNEL_STALE"
REPLAY_CODE = "CHANNEL_REPLAY"
TOO_LARGE_CODE = "CHANNEL_TOO_LARGE"
EVENT_REFUSED_CODE = "CHANNEL_EVENT_REFUSED"
CORRUPT_CODE = "CHANNEL_STORE_CORRUPT"

_EVENT_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")

SIGNATURE_HEADERS = ("X-Qubit-Signature", "X-Hub-Signature-256", "X-Signature")
TIMESTAMP_HEADERS = ("X-Qubit-Timestamp", "X-Timestamp")
DELIVERY_HEADERS = ("X-Qubit-Delivery", "X-Delivery-Id", "X-Request-Id")

# Honest per-channel capability matrix. ``available`` is False for every
# channel because Qubit ships no inbound listener adapter; a credential
# reference alone can never turn this into a live transport.
CHANNEL_CAPS: Dict[str, Dict[str, Any]] = {
    "discord": {"label": "Discord", "transport": "bot-gateway",
                "credential_kind": "bot-token", "available": False,
                "detail": "no Discord gateway listener adapter in Qubit"},
    "telegram": {"label": "Telegram", "transport": "bot-api",
                 "credential_kind": "bot-token", "available": False,
                 "detail": "no Telegram Bot API receive adapter in Qubit"},
    "signal": {"label": "Signal", "transport": "signal-cli",
               "credential_kind": "linked-device", "available": False,
               "detail": "no signal-cli linked-device receive adapter"},
    "matrix": {"label": "Matrix", "transport": "client-server",
               "credential_kind": "access-token", "available": False,
               "detail": "no Matrix client-server sync adapter"},
    "msteams": {"label": "Microsoft Teams", "transport": "bot-framework",
                "credential_kind": "bot-credentials", "available": False,
                "detail": "no Azure Bot Framework listener adapter"},
    "imessage": {"label": "iMessage", "transport": "local-bridge",
                 "credential_kind": "local-bridge", "available": False,
                 "detail": "no macOS Messages bridge adapter"},
    "whatsapp": {"label": "WhatsApp (paired device)",
                 "transport": "paired-session",
                 "credential_kind": "paired-session", "available": False,
                 "detail": "no on-device WhatsApp session listener"},
    "slack": {"label": "Slack", "transport": "events-api",
              "credential_kind": "signing-secret", "available": False,
              "detail": "verification only; no Slack Events ingress adapter"},
    "sms": {"label": "SMS", "transport": "twilio-webhook",
            "credential_kind": "auth-token", "available": False,
            "detail": "no SMS provider webhook adapter"},
    "email": {"label": "Email", "transport": "imap",
              "credential_kind": "imap-password", "available": False,
              "detail": "no IMAP polling/listener adapter"},
    "irc": {"label": "IRC", "transport": "irc-socket",
            "credential_kind": "server-password", "available": False,
            "detail": "no IRC client adapter"},
    "xmpp": {"label": "XMPP", "transport": "xmpp-socket",
             "credential_kind": "jid-password", "available": False,
             "detail": "no XMPP client adapter"},
    "mattermost": {"label": "Mattermost", "transport": "websocket",
                   "credential_kind": "bot-token", "available": False,
                   "detail": "no Mattermost websocket adapter"},
    "rocketchat": {"label": "Rocket.Chat", "transport": "realtime",
                   "credential_kind": "auth-token", "available": False,
                   "detail": "no Rocket.Chat realtime adapter"},
    "twilio": {"label": "Twilio", "transport": "webhook",
               "credential_kind": "auth-token", "available": False,
               "detail": "no Twilio webhook adapter"},
    "vonage": {"label": "Vonage", "transport": "webhook",
               "credential_kind": "api-key", "available": False,
               "detail": "no Vonage webhook adapter"},
    "line": {"label": "LINE", "transport": "webhook",
             "credential_kind": "channel-token", "available": False,
             "detail": "no LINE Messaging API adapter"},
    "wechat": {"label": "WeChat", "transport": "webhook",
               "credential_kind": "app-secret", "available": False,
               "detail": "no WeChat Official Account adapter"},
    "kakaotalk": {"label": "KakaoTalk", "transport": "webhook",
                  "credential_kind": "channel-token", "available": False,
                  "detail": "no KakaoTalk channel adapter"},
    "googlechat": {"label": "Google Chat", "transport": "webhook",
                   "credential_kind": "service-account", "available": False,
                   "detail": "no Google Chat app adapter"},
    "webex": {"label": "Webex", "transport": "webhook",
              "credential_kind": "bot-token", "available": False,
              "detail": "no Webex bot adapter"},
    "zoom": {"label": "Zoom", "transport": "webhook",
             "credential_kind": "bot-token", "available": False,
             "detail": "no Zoom chatbot adapter"},
    "messenger": {"label": "Facebook Messenger", "transport": "webhook",
                  "credential_kind": "page-token", "available": False,
                  "detail": "no Messenger Platform adapter"},
    "instagram": {"label": "Instagram", "transport": "webhook",
                  "credential_kind": "page-token", "available": False,
                  "detail": "no Instagram Messaging adapter"},
    "twitter": {"label": "X / Twitter", "transport": "api",
                "credential_kind": "bearer-token", "available": False,
                "detail": "no X API activity adapter"},
    "reddit": {"label": "Reddit", "transport": "websocket",
               "credential_kind": "oauth", "available": False,
               "detail": "no Reddit streaming adapter"},
    "guilded": {"label": "Guilded", "transport": "bot-gateway",
                "credential_kind": "bot-token", "available": False,
                "detail": "no Guilded gateway adapter"},
    "zulip": {"label": "Zulip", "transport": "events-api",
              "credential_kind": "bot-credentials", "available": False,
              "detail": "no Zulip event-queue adapter"},
    "nostr": {"label": "Nostr", "transport": "relay",
              "credential_kind": "nsec-key", "available": False,
              "detail": "no Nostr relay adapter"},
}

# Single source of truth for channel membership (order preserved).
CHANNELS = tuple(CHANNEL_CAPS)

LISTENER = {
    "state": "NONE",
    "detail": "Qubit starts no socket listener; the dashboard/CLI provides "
              "the HTTP ingress and calls channels_inbound.receive with an "
              "already-authenticated payload",
}


class ChannelError(Exception):
    """Typed inbound-channel failure (never a traceback on the CLI)."""

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


def _nonce_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / NONCE_NAME


def _log_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / LOG_NAME


def _blank() -> Dict[str, Any]:
    return {"schema": SCHEMA, "channels": {}}


def _load(cfg: Any) -> Dict[str, Any]:
    path = _store_path(cfg)
    if not path.exists():
        return _blank()
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ChannelError(CORRUPT_CODE, "channel store too large")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ChannelError:
        raise
    except (OSError, ValueError) as exc:
        raise ChannelError(CORRUPT_CODE,
                           f"channel store unreadable: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA \
            or not isinstance(raw.get("channels"), dict):
        raise ChannelError(CORRUPT_CODE, "channel store has an unknown schema")
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
        raise ChannelError(CORRUPT_CODE,
                           f"channel store write failed: {exc}") from exc


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    from .logging_setup import log_event

    try:
        log_event(f"channel.{op}", actor=actor, **detail)
    except Exception:
        pass


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the plugins surface (fail closed)."""
    from .plugins import estop_engaged as _engaged

    return _engaged(cfg)


def refuse_estop(operation: str) -> Dict[str, Any]:
    """Typed refusal while the emergency stop is latched."""
    return {"state": "ESTOP", "code": ESTOP_CODE, "operation": operation,
            "explanation": "Qubit Agent cannot do this: the emergency stop "
                           "is latched; reset it before changing channels.",
            "owner": "qubit-policy",
            "rollback": "channel registry untouched; nothing dispatched"}


def _secret_kind(ref: str) -> str:
    if ref.startswith("env:"):
        return "env"
    if ref.startswith("file:"):
        return "file"
    return "missing"


def _redact_secret_kind(ref: str) -> str:
    kind = _secret_kind(ref)
    return kind if kind == "missing" else f"{kind}:<reference>"


def _resolve_ref(rec: Dict[str, Any]) -> str:
    kind = rec.get("secret_ref_kind", "missing")
    if kind == "env":
        return "env:" + rec.get("_secret_name", "")
    if kind == "file":
        return rec.get("_secret_path", "")
    return ""


def _check_events(events: Optional[List[str]]) -> List[str]:
    clean: List[str] = []
    for event in events or []:
        text = str(event or "").strip().lower()
        if not _EVENT_RE.match(text):
            raise ChannelError(BAD_INPUT_CODE, f"bad event {event!r}")
        clean.append(text)
    return sorted(set(clean))[:32] or ["message"]


def _get(cfg: Any, channel: str) -> Dict[str, Any]:
    clean = (channel or "").strip().lower()
    if clean not in CHANNELS:
        raise ChannelError(UNKNOWN_CODE, f"unknown channel {channel!r}")
    rec = _load(cfg)["channels"].get(clean)
    if rec is None:
        raise ChannelError(NO_CREDENTIAL_CODE,
                           f"channel {clean!r} is not configured")
    return rec


def _public(rec: Dict[str, Any]) -> Dict[str, Any]:
    caps = CHANNEL_CAPS.get(rec["channel"], {})
    from .providers.credentials import resolve_key

    key, code = resolve_key("channel", _resolve_ref(rec))
    if rec.get("enabled") is False:
        state, state_code = "DISABLED", "CHANNEL_DISABLED"
    elif code or not key:
        state, state_code = "NO_CREDENTIAL", NO_CREDENTIAL_CODE
    else:
        state, state_code = "UNAVAILABLE", UNAVAILABLE_CODE
    return {"channel": rec["channel"], "label": caps.get("label", ""),
            "events": list(rec.get("events", [])),
            "credential": _redact_secret_kind(_resolve_ref(rec)),
            "transport": caps.get("transport", "none"),
            "available": False, "state": state, "code": state_code,
            "configured_at": rec.get("configured_at"),
            "updated_by": rec.get("updated_by", "")}


def configure(cfg: Any, channel: str, *, secret_ref: str,
              events: Optional[List[str]] = None, label: str = "",
              confirmed: bool = False,
              requested_by: str = "cli:user") -> Dict[str, Any]:
    """Record an inbound channel credential *reference* (never a value).

    Confirmation-gated, estop-aware, bounded. The reference is the only
    credential material stored; the state it derives is honest and never a
    live-connection claim.
    """
    if estop_engaged(cfg):
        raise ChannelError(ESTOP_CODE, "emergency stop is latched")
    if not confirmed:
        raise ChannelError(CONFIRM_CODE,
                           "channel configure requires explicit --confirm")
    clean = (channel or "").strip().lower()
    if clean not in CHANNELS:
        raise ChannelError(UNKNOWN_CODE, f"unknown channel {channel!r}")
    ref = (secret_ref or "").strip()
    if _secret_kind(ref) == "missing":
        raise ChannelError(BAD_INPUT_CODE,
                           "secret_ref must be an env:VAR or file:PATH "
                           "reference")
    state = _load(cfg)
    if clean in state["channels"]:
        raise ChannelError(EXISTS_CODE, f"channel {clean!r} is configured")
    rec = {"channel": clean, "label": (label or "").strip()[:64],
           "events": _check_events(events),
           "secret_ref_kind": _secret_kind(ref),
           "enabled": True, "configured_at": _now(),
           "updated_by": requested_by}
    if ref.startswith("env:"):
        rec["_secret_name"] = ref[len("env:"):].strip()[:64]
    else:
        rec["_secret_path"] = ref
    state["channels"][clean] = rec
    _save(cfg, state)
    _audit("configure", requested_by, {"channel": clean})
    return {"state": "OK", "channel": _public(rec),
            "note": "reference recorded; no transport starts and no "
                    "connection is claimed"}


def unconfigure(cfg: Any, channel: str, *, confirmed: bool = False,
                requested_by: str = "cli:user") -> Dict[str, Any]:
    """Remove a configured inbound channel (confirmed, audited)."""
    if estop_engaged(cfg):
        raise ChannelError(ESTOP_CODE, "emergency stop is latched")
    if not confirmed:
        raise ChannelError(
            CONFIRM_CODE, "channel unconfigure requires explicit --confirm")
    clean = (channel or "").strip().lower()
    if clean not in CHANNELS:
        raise ChannelError(UNKNOWN_CODE, f"unknown channel {channel!r}")
    state = _load(cfg)
    if clean not in state["channels"]:
        raise ChannelError(NOT_FOUND_CODE,
                           f"channel {clean!r} is not configured")
    del state["channels"][clean]
    _save(cfg, state)
    _audit("unconfigure", requested_by, {"channel": clean})
    return {"state": "OK", "removed": clean}


def accounts(cfg: Any) -> Dict[str, Any]:
    """Configured inbound channels (redacted, honest derived state)."""
    state = _load(cfg)
    rows = [_public(r) for r in
            sorted(state["channels"].values(),
                   key=lambda r: r.get("channel", ""))]
    return {"state": "OK", "schema": SCHEMA, "accounts": rows,
            "connected": []}


def catalog(cfg: Any) -> Dict[str, Any]:
    """Every known channel with a credential-reference-derived typed state."""
    configured = _load(cfg).get("channels", {})
    rows: List[Dict[str, Any]] = []
    for cid, caps in CHANNEL_CAPS.items():
        if cid in configured:
            rows.append(_public(configured[cid]))
        else:
            rows.append({
                "channel": cid, "label": caps.get("label", cid),
                "events": [], "credential": "missing",
                "transport": caps.get("transport", "none"),
                "available": False, "state": "UNCONFIGURED",
                "code": NO_CREDENTIAL_CODE, "configured_at": None,
                "updated_by": "", "detail": caps.get("detail", "")})
    return {"state": "OK", "schema": SCHEMA, "channels": rows,
            "available": [r["channel"] for r in rows
                          if r["state"] == "AVAILABLE"],
            "connected": [],
            "listener": dict(LISTENER)}


def status(cfg: Any) -> Dict[str, Any]:
    """Typed inbound framework state: listener NONE, connected never faked."""
    latched = estop_engaged(cfg)
    try:
        configured = len(_load(cfg).get("channels", {}))
    except ChannelError:
        configured = -1
    return {"state": "ESTOP" if latched else "OK",
            "code": ESTOP_CODE if latched else "",
            "schema": SCHEMA, "channels": len(CHANNELS),
            "configured": configured, "connected": 0,
            "listener": LISTENER["state"],
            "listener_detail": LISTENER["detail"],
            "estop": latched,
            "detail": "real registry/contract with typed CHANNEL_UNAVAILABLE "
                      "until a listener transport is configured"}


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


def _receipt(cfg: Any, channel: str, verdict: str, detail: str) -> None:
    path = _log_path(cfg)
    rec = {"ts": _now(), "channel": channel, "verdict": verdict,
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


def receive(cfg: Any, *, channel: str, payload: bytes, signature: str = "",
            timestamp: Optional[float] = None, delivery_id: str = "",
            sender: str = "", event: str = "message") -> Dict[str, Any]:
    """Validate a signed inbound delivery, then gate with UNAVAILABLE.

    Validation order: estop -> channel configured -> body cap -> event
    allowlist -> credential reference resolvable -> signature presence ->
    timestamp freshness -> HMAC -> replay nonce. A fully valid delivery
    records a redacted receipt plus a replay nonce and returns the typed
    ``CHANNEL_UNAVAILABLE`` gate: signature evidence only, because Qubit runs
    no listener and nothing is dispatched or executed.
    """
    from .providers.credentials import resolve_key

    if estop_engaged(cfg):
        raise ChannelError(ESTOP_CODE, "emergency stop is latched")
    rec = _get(cfg, channel)
    if rec.get("enabled") is False:
        raise ChannelError(UNAVAILABLE_CODE,
                           f"channel {rec['channel']!r} is disabled")
    body = payload or b""
    if len(body) > MAX_BODY_BYTES:
        _receipt(cfg, rec["channel"], "TOO_LARGE",
                 f"{len(body)} bytes exceeds {MAX_BODY_BYTES} byte cap")
        raise ChannelError(TOO_LARGE_CODE,
                           f"payload exceeds {MAX_BODY_BYTES} byte cap")
    clean_event = (event or "").strip().lower()
    if not _EVENT_RE.match(clean_event):
        raise ChannelError(BAD_INPUT_CODE, f"bad event {event!r}")
    if clean_event not in rec.get("events", []):
        raise ChannelError(EVENT_REFUSED_CODE,
                           f"event {clean_event!r} is not accepted by "
                           f"{rec['channel']!r}")
    clean_sender = (sender or "").strip()
    if len(clean_sender) > MAX_SENDER_LEN:
        raise ChannelError(BAD_INPUT_CODE,
                           f"sender exceeds {MAX_SENDER_LEN} chars")
    key, code = resolve_key("channel", _resolve_ref(rec))
    if code or not key:
        raise ChannelError(NO_CREDENTIAL_CODE,
                           "credential reference unresolvable; no transport "
                           "and no verification secret")
    if not signature:
        _receipt(cfg, rec["channel"], "AUTH_MISSING", "signature missing")
        raise ChannelError(BAD_SIGNATURE_CODE, "missing signature")
    sig = (signature or "").strip()
    if sig.lower().startswith("sha256="):
        sig = sig[len("sha256="):].strip()
    try:
        ts = float(timestamp)
    except (TypeError, ValueError):
        _receipt(cfg, rec["channel"], "STALE", "unparseable timestamp")
        raise ChannelError(STALE_CODE,
                           "missing or unparseable timestamp") from None
    if abs(_now() - ts) > TS_TOLERANCE_S:
        _receipt(cfg, rec["channel"], "STALE", "timestamp outside tolerance")
        raise ChannelError(
            STALE_CODE, f"timestamp outside ±{TS_TOLERANCE_S:.0f}s tolerance")
    expected = hmac.new(key.encode("utf-8"), f"{ts}.".encode() + body,
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig.lower()):
        _receipt(cfg, rec["channel"], "BAD_SIGNATURE", "hmac mismatch")
        raise ChannelError(BAD_SIGNATURE_CODE, "signature mismatch")
    nonce = hashlib.sha256(
        f"{rec['channel']}:{ts}:{hashlib.sha256(body).hexdigest()}"
        .encode()).hexdigest()
    delivery_nonce = (hashlib.sha256(f"delivery:{delivery_id}".encode())
                      .hexdigest() if delivery_id else "")
    replayed = _nonce_seen(cfg, nonce)
    if delivery_nonce and _nonce_seen(cfg, delivery_nonce):
        replayed = True
    if replayed:
        _receipt(cfg, rec["channel"], "REPLAY", "nonce already seen")
        raise ChannelError(REPLAY_CODE, "duplicate delivery (replay)")
    _receipt(cfg, rec["channel"], "VERIFIED_UNAVAILABLE",
             f"{len(body)} bytes; signature valid, no listener transport")
    _audit("receive", "channel-ingress",
           {"channel": rec["channel"], "event": clean_event,
            "bytes": len(body)})
    return {"state": "UNAVAILABLE", "code": UNAVAILABLE_CODE,
            "schema": SCHEMA, "channel": rec["channel"],
            "event": clean_event, "verified": True, "bytes": len(body),
            "sender": _redact(clean_sender)[:MAX_SENDER_LEN],
            "transport": CHANNEL_CAPS.get(rec["channel"], {}).get(
                "transport", "none"),
            "note": "signature/timestamp/replay validated; no listener "
                    "transport exists, nothing dispatched or executed"}
