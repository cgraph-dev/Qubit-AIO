"""Qubit inbound channel framework breadth (Qubit audit #19/#20).

Qubit recognizes the Qubit-breadth channel set and gives each an honest,
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
E2EE_UNAVAILABLE_CODE = "CHANNEL_E2EE_UNAVAILABLE"

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
                "detail": "bounded Discord receive adapter (normalize_event/"
                          "ingest) shipped; the live gateway transport stays "
                          "credential-gated"},
    "telegram": {"label": "Telegram", "transport": "bot-api",
                 "credential_kind": "bot-token", "available": False,
                 "detail": "bounded Telegram update receiver (normalize_event/"
                           "ingest) shipped; the live Bot API transport stays "
                           "credential-gated"},
    "signal": {"label": "Signal", "transport": "signal-cli",
               "credential_kind": "linked-device", "available": False,
               "detail": "bounded signal-cli envelope receiver (normalize_event/"
                         "ingest) shipped; the live linked-device transport "
                         "stays gated"},
    "matrix": {"label": "Matrix", "transport": "client-server",
               "credential_kind": "access-token", "available": False,
               "detail": "bounded Matrix sync receiver (normalize_event/"
                         "decrypt_event/ingest) shipped; the live homeserver "
                         "sync transport stays credential-gated"},
    "msteams": {"label": "Microsoft Teams", "transport": "bot-framework",
                "credential_kind": "bot-credentials", "available": False,
                "detail": "bounded Bot Framework activity receiver "
                          "(normalize_event/ingest) shipped; the live Azure "
                          "Bot transport stays credential-gated"},
    "imessage": {"label": "iMessage", "transport": "local-bridge",
                 "credential_kind": "local-bridge", "available": False,
                 "detail": "bounded macOS Messages bridge event receiver "
                           "(normalize_event/ingest) shipped; the live bridge "
                           "stays gated"},
    "whatsapp": {"label": "WhatsApp (paired device)",
                 "transport": "paired-session",
                 "credential_kind": "paired-session", "available": False,
                 "detail": "no on-device WhatsApp session listener"},
    "slack": {"label": "Slack", "transport": "events-api",
              "credential_kind": "signing-secret", "available": False,
              "detail": "signature-verified receive adapter (normalize_event/"
                        "ingest) shipped; the live Events API transport stays "
                        "credential-gated"},
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
    "feishu": {"label": "Feishu / Lark", "transport": "open-platform",
               "credential_kind": "app-secret", "available": False,
               "detail": "bounded open-platform event receiver (normalize_event/"
                         "ingest) shipped; the live event subscription stays "
                         "credential-gated"},
    "gmail": {"label": "Gmail (Pub/Sub push)", "transport": "pubsub-push",
              "credential_kind": "oidc-bearer", "available": False,
              "detail": "Pub/Sub push envelope verified; no mailbox watcher "
                        "transport runs in Qubit"},
    # ---- Qubit wave-5 extension breadth (extensions/* bridges) ----
    # Every remaining Qubit channel extension is recognized with a real
    # bounded receive adapter (normalize_event + the injected ingest sink) and
    # an honest, typed credential/transport gate. No listener starts and no
    # channel is ever reported connected.
    "a2a": {"label": "A2A (agent-to-agent)", "transport": "agent-bridge",
            "credential_kind": "agent-card", "available": False,
            "detail": "bounded agent-card envelope normalized; live peer "
                      "transport stays credential/owner gated"},
    "buzz": {"label": "Buzz", "transport": "relay",
             "credential_kind": "relay-token", "available": False,
             "detail": "bounded relay envelope normalized; no relay transport"},
    "clickclack": {"label": "ClickClack", "transport": "workspace-api",
                   "credential_kind": "app-token", "available": False,
                   "detail": "bounded workspace event normalized; no transport"},
    "imap": {"label": "IMAP mailbox", "transport": "imap",
             "credential_kind": "imap-password", "available": False,
             "detail": "bounded mail envelope normalized; no IMAP poller runs"},
    "nextcloud-talk": {"label": "Nextcloud Talk", "transport": "ocs-api",
                       "credential_kind": "app-password", "available": False,
                       "detail": "bounded OCS activity normalized; no adapter"},
    "qa-channel": {"label": "QA channel harness", "transport": "test-harness",
                   "credential_kind": "test-fixture", "available": False,
                   "detail": "bounded QA fixture envelope; live channel is a "
                             "credential/owner gate"},
    "raft": {"label": "Raft", "transport": "relay",
             "credential_kind": "relay-token", "available": False,
             "detail": "bounded relay envelope normalized; no relay transport"},
    "reef": {"label": "Reef", "transport": "relay",
             "credential_kind": "relay-token", "available": False,
             "detail": "bounded relay envelope normalized; no relay transport"},
    "synology-chat": {"label": "Synology Chat", "transport": "webhook",
                      "credential_kind": "bot-token", "available": False,
                      "detail": "bounded webhook envelope normalized; no "
                                "inbound webhook adapter"},
    "tlon": {"label": "Tlon / Urbit", "transport": "urbit",
             "credential_kind": "urbit-code", "available": False,
             "detail": "bounded Urbit message normalized; no ship transport"},
    "twitch": {"label": "Twitch", "transport": "irc-socket",
               "credential_kind": "oauth", "available": False,
               "detail": "bounded IRC chat message normalized; no socket"},
    "zalo": {"label": "Zalo", "transport": "oa-webhook",
             "credential_kind": "oa-token", "available": False,
             "detail": "bounded OA message normalized; no Zalo transport"},
    "zalouser": {"label": "Zalo User", "transport": "oa-webhook",
                 "credential_kind": "oa-token", "available": False,
                 "detail": "bounded user message normalized; no Zalo transport"},
}

# Single source of truth for channel membership (order preserved).
CHANNELS = tuple(CHANNEL_CAPS)

LISTENER = {
    "state": "NONE",
    "detail": "Qubit starts no socket listener; the dashboard/CLI provides "
              "the HTTP ingress and calls channels_inbound.receive with an "
              "already-authenticated payload",
}

# Documented root cause for "no catalog channel can reach connected" (audit
# fix-channels defect 3): the dashboard writes channel config to
# ``msg-platforms.json`` (``voicebridge.qubit_compat``), but NO transport
# adapter reads it. The outbound transport resolves accounts from
# ``msg-accounts.json`` (``voicebridge.messaging``) and the vendored gateway
# reads its own ``config.yaml``/``.env``; ``channels_inbound`` starts no
# listener. Until an operator/architecture bridge joins the dashboard store to
# a real transport, a dashboard-configured channel honestly stays
# ``UNAVAILABLE`` rather than a fabricated ``connected``.
DASHBOARD_CONFIG_WIRING = {
    "dashboard_store": "msg-platforms.json",
    "dashboard_store_writer": "voicebridge.qubit_compat.msg_update",
    "transport_store": "msg-accounts.json",
    "transport_reader": "voicebridge.messaging",
    "consumed_by_adapter": False,
    "operator_prerequisite": "bridge the dashboard channel config "
                             "(msg-platforms.json) to the transport account "
                             "store / gateway config, or make the gateway read "
                             "msg-platforms.json; no Qubit transport reads it "
                             "today",
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
            "dashboard_config_wiring": dict(DASHBOARD_CONFIG_WIRING),
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


def _verify(cfg: Any, *, channel: str, payload: bytes, signature: str = "",
            timestamp: Optional[float] = None, delivery_id: str = "",
            sender: str = "", event: str = "message",
            verdict: str = "VERIFIED_UNAVAILABLE",
            detail: str = "") -> Dict[str, Any]:
    """Ordered signed-delivery kernel shared by receive/verify_delivery.

    Validation order: estop -> channel configured -> body cap -> event
    allowlist -> credential reference resolvable -> signature presence ->
    timestamp freshness -> HMAC -> replay nonce. On success records a
    redacted receipt plus replay nonce and returns the verified facts; the
    caller decides whether to gate (receive) or dispatch (a listener).
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
    _receipt(cfg, rec["channel"], verdict, detail)
    return {"channel": rec["channel"], "event": clean_event,
            "bytes": len(body), "sender": clean_sender,
            "transport": CHANNEL_CAPS.get(rec["channel"], {}).get(
                "transport", "none")}


def verify_delivery(cfg: Any, *, channel: str, payload: bytes,
                    signature: str = "", timestamp: Optional[float] = None,
                    delivery_id: str = "", sender: str = "",
                    event: str = "message") -> Dict[str, Any]:
    """Validate a signed delivery and return the verified facts.

    Identical ordered kernel to :func:`receive`, but returns the verified
    sender/event/bytes so a real listener can dispatch the delivery through
    the one communications ingress path. Starts no socket and dispatches
    nothing itself: verification evidence only.
    """
    info = _verify(cfg, channel=channel, payload=payload,
                   signature=signature, timestamp=timestamp,
                   delivery_id=delivery_id, sender=sender, event=event,
                   verdict="VERIFIED",
                   detail=f"{len(payload or b'')} bytes; signature valid")
    _audit("verify", "channel-ingress",
           {"channel": info["channel"], "event": info["event"],
            "bytes": info["bytes"]})
    info["state"] = "VERIFIED"
    info["schema"] = SCHEMA
    info["verified"] = True
    return info


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
    info = _verify(cfg, channel=channel, payload=payload,
                   signature=signature, timestamp=timestamp,
                   delivery_id=delivery_id, sender=sender, event=event,
                   verdict="VERIFIED_UNAVAILABLE",
                   detail=f"{len(payload or b'')} bytes; signature valid, "
                          "no listener transport")
    _audit("receive", "channel-ingress",
           {"channel": info["channel"], "event": info["event"],
            "bytes": info["bytes"]})
    return {"state": "UNAVAILABLE", "code": UNAVAILABLE_CODE,
            "schema": SCHEMA, "channel": info["channel"],
            "event": info["event"], "verified": True, "bytes": info["bytes"],
            "sender": _redact(info["sender"])[:MAX_SENDER_LEN],
            "transport": info["transport"],
            "note": "signature/timestamp/replay validated; no listener "
                    "transport exists, nothing dispatched or executed"}


# ---------------------------------------------------------------------------
# OC-19/OC-20: per-channel receive adapters + bounded ingress loop
# ---------------------------------------------------------------------------
MAX_NORMALIZED_TEXT = 4000
MAX_INGRESS_BATCH = 200


def _decode_json_payload(payload: Any) -> Any:
    raw = payload if isinstance(payload, (bytes, bytearray)) else \
        str(payload or "").encode("utf-8")
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        raise ChannelError(BAD_INPUT_CODE,
                           "channel payload is not valid JSON") from None


def normalize_event(channel: str, payload: Any) -> Dict[str, Any]:
    """Normalize one channel-native inbound payload into a bounded message.

    This is the receive half of each channel adapter: it reads only the
    sender/text/event-id fields it needs, caps every string, redacts through
    the shared redactor, and raises a typed ``CHANNEL_BAD_INPUT`` for an
    unrecognized shape instead of guessing. Nothing is executed.
    """
    clean = (channel or "").strip().lower()
    if clean not in CHANNELS:
        raise ChannelError(UNKNOWN_CODE, f"unknown channel {channel!r}")
    data = _decode_json_payload(payload)
    if not isinstance(data, dict):
        raise ChannelError(BAD_INPUT_CODE,
                           "channel payload must be a JSON object")
    sender = ""
    text = ""
    event_id = ""
    encrypted = False
    if clean == "discord":
        author = data.get("author") if isinstance(data.get("author"), dict) else {}
        sender = str(author.get("id") or data.get("user_id") or "")
        text = str(data.get("content") or "")
        event_id = str(data.get("id") or "")
    elif clean == "telegram":
        msg = data.get("message") if isinstance(data.get("message"), dict) else data
        frm = msg.get("from") if isinstance(msg.get("from"), dict) else {}
        sender = str(frm.get("id") or frm.get("username") or "")
        text = str(msg.get("text") or msg.get("caption") or "")
        event_id = str(msg.get("message_id") or data.get("update_id") or "")
    elif clean == "matrix":
        sender = str(data.get("sender") or "")
        content = data.get("content") if isinstance(data.get("content"), dict) else {}
        text = str(content.get("body") or "")
        event_id = str(data.get("event_id") or "")
        if not text and content.get("ciphertext"):
            # E2EE event: no plaintext without a crypto boundary. Normalize the
            # envelope metadata only; decrypt_event must be used to read it.
            encrypted = True
            event_id = event_id or str(content.get("session_id") or "")
    elif clean == "feishu":
        ev = data.get("event") if isinstance(data.get("event"), dict) else data
        msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
        esender = ev.get("sender")
        if isinstance(esender, dict):
            sid = esender.get("sender_id")
            sender = str(sid.get("open_id", "") if isinstance(sid, dict)
                         else sid or "")
        content = msg.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except ValueError:
                content = {}
        text = str(content.get("text", "") if isinstance(content, dict) else "")
        event_id = str(msg.get("message_id") or ev.get("event_id") or "")
    elif clean == "slack":
        ev = data.get("event") if isinstance(data.get("event"), dict) else data
        sender = str(ev.get("user") or ev.get("bot_id") or
                     data.get("sender") or "")
        text = str(ev.get("text") or ev.get("body") or "")
        event_id = str(ev.get("client_msg_id") or ev.get("ts") or
                       data.get("event_id") or "")
    elif clean == "signal":
        env = data.get("envelope") if isinstance(data.get("envelope"), dict) \
            else data
        dm = env.get("dataMessage") if isinstance(env.get("dataMessage"),
                                                   dict) else {}
        sender = str(env.get("source") or env.get("sourceNumber") or
                     data.get("sender") or data.get("from") or "")
        text = str(dm.get("message") or env.get("message") or
                   data.get("text") or data.get("body") or "")
        event_id = str(env.get("timestamp") or dm.get("timestamp") or
                       data.get("id") or data.get("event_id") or "")
    elif clean == "msteams":
        frm = data.get("from") if isinstance(data.get("from"), dict) else {}
        sender = str(frm.get("id") or frm.get("aadObjectId") or
                     data.get("sender") or "")
        text = str(data.get("text") or data.get("message") or
                   data.get("body") or "")
        event_id = str(data.get("id") or data.get("event_id") or "")
    elif clean == "imessage":
        sender = str(data.get("sender") or data.get("from") or
                     data.get("handle") or "")
        text = str(data.get("text") or data.get("body") or
                   data.get("message") or "")
        event_id = str(data.get("id") or data.get("guid") or
                       data.get("event_id") or "")
    elif clean == "imap":
        sender = str(data.get("from") or data.get("sender") or "")
        text = str(data.get("body") or data.get("text") or
                   data.get("subject") or "")
        event_id = str(data.get("uid") or data.get("message_id") or
                       data.get("id") or "")
    elif clean == "twitch":
        sender = str(data.get("username") or data.get("sender") or "")
        text = str(data.get("message") or data.get("text") or "")
        event_id = str(data.get("id") or data.get("event_id") or "")
    elif clean in ("zalo", "zalouser"):
        sender = str(data.get("from_id") or data.get("sender") or
                     data.get("from") or "")
        text = str(data.get("message") or data.get("text") or "")
        event_id = str(data.get("message_id") or data.get("id") or "")
    elif clean == "tlon":
        sender = str(data.get("author") or data.get("sender") or "")
        text = str(data.get("content") or data.get("text") or "")
        event_id = str(data.get("id") or data.get("event_id") or "")
    elif clean == "nextcloud-talk":
        actor = data.get("actor") if isinstance(data.get("actor"), dict) else {}
        sender = str(actor.get("id") or data.get("sender") or "")
        text = str(data.get("message") or data.get("text") or "")
        event_id = str(data.get("id") or data.get("event_id") or "")
    elif clean == "a2a":
        sender = str(data.get("from") or data.get("sender") or "")
        text = str(data.get("text") or data.get("message") or "")
        event_id = str(data.get("task_id") or data.get("id") or "")
    elif clean in ("buzz", "raft", "reef"):
        sender = str(data.get("from") or data.get("sender") or "")
        text = str(data.get("text") or data.get("message") or "")
        event_id = str(data.get("id") or data.get("event_id") or "")
    else:
        sender = str(data.get("sender") or data.get("from") or "")
        text = str(data.get("text") or data.get("body") or "")
        event_id = str(data.get("id") or data.get("event_id") or "")
    if not text and not event_id:
        # Generic fallback for a channel-shaped payload the adapter above did
        # not recognize (e.g. a plain bridge envelope); still bounded.
        sender = sender or str(data.get("sender") or data.get("from") or "")
        text = str(data.get("text") or data.get("body") or "")
        event_id = str(data.get("id") or data.get("event_id") or "")
    if not text and not event_id:
        raise ChannelError(BAD_INPUT_CODE,
                           f"payload has no recognizable {clean} message")
    return {"channel": clean, "sender": _redact(sender)[:MAX_SENDER_LEN],
            "text": _redact(text)[:MAX_NORMALIZED_TEXT],
            "event_id": str(event_id)[:200], "encrypted": bool(encrypted)}


def decrypt_event(channel: str, payload: Any, *, decrypt: Any = None) -> Dict[str, Any]:
    """Decrypt one encrypted channel event over an injected crypto boundary.

    Matrix E2EE (``m.room.encrypted``) requires an olm/megolm boundary Qubit
    does not ship. Without an injected ``decrypt(mapping) -> {"body": ...}``
    callable an encrypted event is a typed ``CHANNEL_E2EE_UNAVAILABLE``; the
    ciphertext is never treated as plaintext and is never logged.
    """
    clean = (channel or "").strip().lower()
    if clean not in CHANNELS:
        raise ChannelError(UNKNOWN_CODE, f"unknown channel {channel!r}")
    data = _decode_json_payload(payload)
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, dict) or not content.get("ciphertext"):
        raise ChannelError(BAD_INPUT_CODE, "event is not an encrypted payload")
    if not callable(decrypt):
        raise ChannelError(
            E2EE_UNAVAILABLE_CODE,
            "encrypted events need an injected olm/megolm crypto boundary; "
            "nothing was decrypted")
    try:
        out = decrypt({"ciphertext": content.get("ciphertext"),
                       "algorithm": content.get("algorithm"),
                       "sender_key": content.get("sender_key")})
    except ChannelError:
        raise
    except Exception as exc:
        raise ChannelError(
            E2EE_UNAVAILABLE_CODE,
            f"crypto boundary failed: {_redact(str(exc))[:160]}") from exc
    if not isinstance(out, dict) or "body" not in out:
        raise ChannelError(E2EE_UNAVAILABLE_CODE,
                           "crypto boundary returned no plaintext body")
    return {"state": "OK", "schema": SCHEMA, "channel": clean,
            "encrypted": True,
            "algorithm": str(content.get("algorithm", ""))[:64],
            "sender": _redact(str(data.get("sender", "")))[:MAX_SENDER_LEN],
            "text": _redact(str(out["body"]))[:MAX_NORMALIZED_TEXT]}


def ingest(cfg: Any, *, channel: str, payload: Any, signature: str = "",
           timestamp: Optional[float] = None, delivery_id: str = "",
           sender: str = "", event: str = "message",
           sink: Any = None) -> Dict[str, Any]:
    """Authenticate a delivery, normalize it, and hand it to a sink.

    The HMAC/freshness/replay kernel is identical to :func:`receive`; the
    difference is the injected ``sink(message)`` boundary: an
    operator-provided consumer (channel bridge/agent). Without a sink the
    verified delivery is the typed ``CHANNEL_UNAVAILABLE`` gate, never a
    fabricated dispatch.
    """
    info = _verify(cfg, channel=channel, payload=payload,
                   signature=signature, timestamp=timestamp,
                   delivery_id=delivery_id, sender=sender, event=event,
                   verdict="VERIFIED_INBOUND",
                   detail=f"{len(payload or b'')} bytes; signature valid")
    normalized = normalize_event(info["channel"], payload)
    if sink is None:
        return {"state": "UNAVAILABLE", "code": UNAVAILABLE_CODE,
                "schema": SCHEMA, "channel": info["channel"],
                "event": info["event"], "verified": True,
                "message": normalized,
                "note": "verified; no sink injected, nothing dispatched"}
    if not callable(sink):
        raise ChannelError(BAD_INPUT_CODE, "sink must be callable")
    try:
        result = sink(normalized)
    except ChannelError:
        raise
    except Exception as exc:
        _receipt(cfg, info["channel"], "SINK_ERROR", str(exc))
        raise ChannelError(
            UNAVAILABLE_CODE,
            f"channel sink failed: {_redact(str(exc))[:160]}") from exc
    _audit("ingest", "channel-ingress",
           {"channel": info["channel"], "event": info["event"]})
    return {"state": "DELIVERED", "schema": SCHEMA,
            "channel": info["channel"], "event": info["event"],
            "verified": True, "message": normalized,
            "sink_result": result if isinstance(
                result, (dict, list, str, int, float, bool, type(None)))
            else None,
            "note": "authenticated delivery handed to the injected sink"}


def run_ingress(cfg: Any, *, channel: str, receiver: Any, sink: Any,
                max_items: int = MAX_INGRESS_BATCH,
                event: str = "message") -> Dict[str, Any]:
    """Bounded inbound loop over an operator-injected receiver (OC-20).

    ``receiver()`` returns the next delivery mapping (or ``None``/raises
    ``StopIteration`` to end). Each item is authenticated through
    :func:`ingest` and handed to ``sink``. Qubit opens no socket: the receiver
    owns the transport, and the loop is capped at :data:`MAX_INGRESS_BATCH`.
    """
    if not callable(receiver) or not callable(sink):
        return {"state": "UNAVAILABLE", "code": UNAVAILABLE_CODE,
                "schema": SCHEMA, "channel": (channel or "").strip().lower(),
                "detail": "a receiver and sink callable are required",
                "owner": "qubit-policy",
                "rollback": "no listener started; nothing to roll back"}
    try:
        budget = max(1, min(int(max_items), MAX_INGRESS_BATCH))
    except (TypeError, ValueError):
        budget = MAX_INGRESS_BATCH
    delivered = 0
    failed = 0
    results: List[Dict[str, Any]] = []
    for _ in range(budget):
        try:
            item = receiver()
        except StopIteration:
            break
        except Exception as exc:
            failed += 1
            results.append({"state": "FAILED", "code": UNAVAILABLE_CODE,
                            "detail": _redact(str(exc))[:160]})
            break
        if item is None:
            break
        if not isinstance(item, dict):
            failed += 1
            results.append({"state": "FAILED", "code": BAD_INPUT_CODE,
                            "detail": "receiver item must be a mapping"})
            continue
        payload = item.get("payload", b"")
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        try:
            out = ingest(cfg, channel=item.get("channel", channel),
                         payload=payload,
                         signature=str(item.get("signature", "")),
                         timestamp=item.get("timestamp"),
                         delivery_id=str(item.get("delivery_id", "")),
                         sender=str(item.get("sender", "")),
                         event=str(item.get("event", event)), sink=sink)
            delivered += 1
            results.append({"state": out["state"], "channel": out["channel"]})
        except ChannelError as exc:
            failed += 1
            results.append({"state": "FAILED", "code": exc.code})
    state = ("OK" if delivered and not failed else
             "DEGRADED" if delivered or failed else "OK")
    return {"state": state, "schema": SCHEMA,
            "channel": (channel or "").strip().lower(),
            "delivered": delivered, "failed": failed, "results": results,
            "authority": "operator-injected receiver/sink; Qubit opens no "
                         "socket"}


# ---------------------------------------------------------------------------
# OC-25: Gmail Pub/Sub push transport
# ---------------------------------------------------------------------------
import base64 as _base64  # noqa: E402  (local to this section)

GMAIL_PUBSUB_BAD_CODE = "CHANNEL_GMAIL_BAD_ENVELOPE"


def decode_pubsub_envelope(envelope: Any) -> Dict[str, Any]:
    """Decode a Pub/Sub push envelope into its mailbox notification facts.

    Accepts the canonical push shape
    ``{"message": {"data": <b64>, "messageId": ..., "publishTime": ...},
    "subscription": ...}``. Returns the decoded data bytes and message id; a
    malformed envelope is a typed ``CHANNEL_GMAIL_BAD_ENVELOPE`` and never a
    traceback. No network call is made.
    """
    if not isinstance(envelope, dict):
        raise ChannelError(GMAIL_PUBSUB_BAD_CODE,
                           "Pub/Sub envelope must be a JSON object")
    message = envelope.get("message")
    if not isinstance(message, dict):
        raise ChannelError(GMAIL_PUBSUB_BAD_CODE,
                           "Pub/Sub envelope has no message object")
    data_b64 = message.get("data")
    if not isinstance(data_b64, str) or not data_b64:
        raise ChannelError(GMAIL_PUBSUB_BAD_CODE,
                           "Pub/Sub message has no data")
    try:
        payload = _base64.b64decode(data_b64, validate=True)
    except Exception:
        raise ChannelError(GMAIL_PUBSUB_BAD_CODE,
                           "Pub/Sub message data is not valid base64") from None
    if len(payload) > MAX_BODY_BYTES:
        raise ChannelError(TOO_LARGE_CODE,
                           f"Pub/Sub payload exceeds {MAX_BODY_BYTES} bytes")
    return {"payload": payload,
            "message_id": str(message.get("messageId", ""))[:200],
            "publish_time": str(message.get("publishTime", ""))[:64],
            "subscription": str(envelope.get("subscription", ""))[:200]}


def gmail_pubsub(cfg: Any, envelope: Any, *, token: str = "",
                 event: str = "message",
                 sender: str = "") -> Dict[str, Any]:
    """Verify a Gmail Pub/Sub push delivery (OC-25).

    The verification-token boundary (an OIDC bearer in production) is
    compared constant-time against the configured channel secret; the decoded
    mailbox notification is then replay-checked and recorded. As with every
    channel, a fully verified delivery still resolves to the typed
    ``CHANNEL_UNAVAILABLE`` gate because Qubit runs no mailbox watcher, and
    nothing is dispatched.
    """
    if estop_engaged(cfg):
        raise ChannelError(ESTOP_CODE, "emergency stop is latched")
    decoded = decode_pubsub_envelope(envelope)
    rec = _get(cfg, "gmail")
    if rec.get("enabled") is False:
        raise ChannelError(UNAVAILABLE_CODE, "channel 'gmail' is disabled")
    from .providers.credentials import resolve_key

    key, code = resolve_key("channel", _resolve_ref(rec))
    if code or not key:
        raise ChannelError(NO_CREDENTIAL_CODE,
                           "Gmail channel credential reference unresolvable")
    presented = str(token or "").strip()
    if not presented or not hmac.compare_digest(presented, str(key)):
        _receipt(cfg, "gmail", "BAD_SIGNATURE",
                 "Pub/Sub verification token mismatch")
        raise ChannelError(BAD_SIGNATURE_CODE,
                           "Pub/Sub verification token mismatch")
    nonce = hashlib.sha256(
        f"gmail:{decoded['message_id']}".encode()).hexdigest()
    if decoded["message_id"] and _nonce_seen(cfg, nonce):
        _receipt(cfg, "gmail", "REPLAY", "Pub/Sub message replayed")
        raise ChannelError(REPLAY_CODE, "duplicate Pub/Sub delivery (replay)")
    if event not in rec.get("events", []):
        raise ChannelError(EVENT_REFUSED_CODE,
                           f"event {event!r} is not accepted by 'gmail'")
    _receipt(cfg, "gmail", "VERIFIED_UNAVAILABLE",
             f"{len(decoded['payload'])} bytes; Pub/Sub token valid, no "
             "mailbox watcher")
    _audit("gmail_pubsub", "channel-ingress",
           {"channel": "gmail", "bytes": len(decoded["payload"])})
    return {"state": "UNAVAILABLE", "code": UNAVAILABLE_CODE,
            "schema": SCHEMA, "channel": "gmail", "event": event,
            "verified": True, "bytes": len(decoded["payload"]),
            "message_id": decoded["message_id"],
            "publish_time": decoded["publish_time"],
            "subscription": decoded["subscription"],
            "sender": _redact(sender)[:MAX_SENDER_LEN],
            "transport": "pubsub-push",
            "note": "Pub/Sub envelope decoded, token verified, replay "
                    "checked; no mailbox watcher transport exists, nothing "
                    "dispatched or executed"}


def gmail_watch(cfg: Any, *, topic: str = "", transport: Any = None,
                requested_by: str = "cli:user") -> Dict[str, Any]:
    """Register/renew a Gmail Pub/Sub watch over an injected transport (OC-25).

    The watch is the push-path half of the Pub/Sub transport: without an
    injected mailbox transport Qubit cannot reach Gmail and returns a typed
    ``CHANNEL_UNAVAILABLE``. With one, the watch reply's id/expiration/
    historyId are recorded on the channel record and reported back (bounded
    and redacted). No credential value is stored.
    """
    if estop_engaged(cfg):
        raise ChannelError(ESTOP_CODE, "emergency stop is latched")
    rec = _get(cfg, "gmail")
    if rec.get("enabled") is False:
        raise ChannelError(UNAVAILABLE_CODE, "channel 'gmail' is disabled")
    if transport is None:
        return {"state": "UNAVAILABLE", "code": UNAVAILABLE_CODE,
                "schema": SCHEMA, "channel": "gmail",
                "detail": "Gmail watch needs an injected mailbox transport; "
                          "no watch was registered",
                "owner": "qubit-policy",
                "rollback": "no watch registered; nothing to roll back"}
    request = {"type": "gmail.watch", "channel": "gmail",
               "topic": str(topic or "")[:300],
               "events": list(rec.get("events", []))}
    try:
        response = transport(request)
    except Exception as exc:
        return {"state": "FAILED", "code": UNAVAILABLE_CODE,
                "schema": SCHEMA, "channel": "gmail",
                "detail": _redact(str(exc))[:200], "owner": "qubit-policy",
                "rollback": "no watch registered; nothing to roll back"}
    if not isinstance(response, dict) or not response.get("watch_id"):
        return {"state": "FAILED", "code": UNAVAILABLE_CODE,
                "schema": SCHEMA, "channel": "gmail",
                "detail": "transport returned no watch id",
                "owner": "qubit-policy",
                "rollback": "no watch registered; nothing to roll back"}
    expiration = response.get("expiration", "")
    state = _load(cfg)
    stored = state["channels"].get("gmail")
    if stored is not None:
        stored["watch"] = {
            "watch_id": _redact(str(response["watch_id"]))[:120],
            "expiration": str(expiration)[:64],
            "history_id": str(response.get("history_id", ""))[:64],
            "topic": str(topic or "")[:300],
            "registered_at": _now(),
            "registered_by": requested_by}
        _save(cfg, state)
    _audit("gmail_watch", requested_by, {"topic": str(topic or "")[:120]})
    return {"state": "OK", "schema": SCHEMA, "channel": "gmail",
            "watch_id": _redact(str(response["watch_id"]))[:120],
            "expiration": str(expiration)[:64],
            "history_id": str(response.get("history_id", ""))[:64],
            "authority": "injected mailbox transport; no credentials stored"}


def gmail_watch_status(cfg: Any) -> Dict[str, Any]:
    """Read-only view of the recorded Gmail watch (never a fabricated one)."""
    try:
        state = _load(cfg)
    except ChannelError as exc:
        return {"state": "DEGRADED", "code": exc.code, "watch": None}
    stored = state["channels"].get("gmail") or {}
    watch = stored.get("watch")
    return {"state": "OK" if watch else "UNCONFIGURED", "schema": SCHEMA,
            "channel": "gmail", "watch": watch}
