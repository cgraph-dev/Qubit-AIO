"""Qubit messaging/webhook ingress bridge (Agent 6).

One inbound path for the Qubit channel set and verified webhook
subscriptions into the existing Qubit communications package. It adds the
three pieces the 2026-09-11 audit left open without starting a second
listener or duplicating the verification kernels:

* **DM access control** — an explicit per-channel allow/deny policy. Unknown
  senders fail closed (no policy => denied); an explicit deny always wins;
  wildcard allow/deny supported. Configuration is confirm-gated, estop-aware,
  audited, and stores sender identifiers only (never credentials/values).
* **Rich messages + albums** — a bounded parser normalizes text, captions,
  attachments (image/video/audio/file/location/sticker/button/card) and album
  groups into typed parts carrying mime/bytes/sha256. Oversized payloads,
  over-count/over-size parts, malformed base64, bad MIME, and unknown kinds
  are refused with typed errors.
* **One broker** — :func:`dispatch` is the single call the channel listener
  and the webhook listener feed. It starts no socket, never re-implements
  signature verification, and persists bounded redacted receipts only.

Listeners delegate verification to the real kernels:

* :class:`ChannelListener` -> ``channels_inbound.verify_delivery`` (HMAC,
  freshness, replay, credential reference).
* :class:`WebhookListener` -> ``webhooks.verify`` (HMAC, freshness, replay).

State stays user-private (0700 dir / 0600 files):
``<state_dir>/communications-inbound.jsonl`` (bounded redacted receipts) and
``<state_dir>/dm-policy.json``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = "qubit-communications-ingress/1"
DM_SCHEMA = "qubit-dm-policy/1"
INBOUND_NAME = "communications-inbound.jsonl"
DM_NAME = "dm-policy.json"

MAX_FILE_BYTES = 1_000_000
MAX_BODY_BYTES = 256_000
MAX_PARTS = 10
MAX_PART_BYTES = 128_000
MAX_TEXT_CHARS = 4000
MAX_CAPTION_CHARS = 1000
MAX_SENDER = 160
MAX_CHANNEL = 64
MAX_MIME = 120
MAX_NAME = 120
MAX_ALBUM = 64
MAX_LINES = 2000

ESTOP_CODE = "INGRESS_ESTOP"
CONFIRM_CODE = "INGRESS_CONFIRM_REQUIRED"
BAD_INPUT_CODE = "INGRESS_BAD_INPUT"
UNKNOWN_CODE = "INGRESS_UNKNOWN_CHANNEL"
DM_DENIED_CODE = "INGRESS_DM_DENIED"
NO_POLICY_CODE = "INGRESS_NO_POLICY"
TOO_LARGE_CODE = "INGRESS_TOO_LARGE"
BAD_ATTACHMENT_CODE = "INGRESS_BAD_ATTACHMENT"
BAD_TRANSPORT_CODE = "INGRESS_BAD_TRANSPORT"

PART_KINDS = frozenset({
    "text", "image", "video", "audio", "file", "location", "sticker",
    "button", "card",
})
TRANSPORTS = frozenset({"channel", "webhook"})

_CHANNEL_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")
_MIME_RE = re.compile(r"^[a-z0-9][a-z0-9.+-]{0,63}/[a-z0-9][a-z0-9.+-]{0,63}$")
_ALBUM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_SENDER_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,160}$")
_B64_RE = re.compile(r"^[A-Za-z0-9+/=_-]*$")


class IngressError(Exception):
    """Typed ingress failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _redact(text: str) -> str:
    from ..redaction import redact_text

    return redact_text(text)


def _now() -> float:
    return time.time()


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    from ..logging_setup import log_event

    try:
        log_event(f"communications.ingress.{op}", actor=actor, **detail)
    except Exception:
        pass


def _estop(cfg: Any) -> bool:
    from ..plugins import estop_engaged

    return bool(estop_engaged(cfg))


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# ------------------------------------------------------------- DM policy
def _dm_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / DM_NAME


def _blank_dm() -> Dict[str, Any]:
    return {"schema": DM_SCHEMA, "policies": {}}


def _load_dm(cfg: Any) -> Dict[str, Any]:
    path = _dm_path(cfg)
    if not path.exists():
        return _blank_dm()
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise IngressError(BAD_INPUT_CODE, "dm policy store too large")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except IngressError:
        raise
    except (OSError, ValueError) as exc:
        raise IngressError(BAD_INPUT_CODE,
                           f"dm policy unreadable: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != DM_SCHEMA \
            or not isinstance(raw.get("policies"), dict):
        raise IngressError(BAD_INPUT_CODE, "dm policy has an unknown schema")
    return raw


def _save_dm(cfg: Any, state: Dict[str, Any]) -> None:
    try:
        _atomic_write(_dm_path(cfg),
                      json.dumps(state, indent=2, sort_keys=True))
    except OSError as exc:
        raise IngressError(BAD_INPUT_CODE,
                           f"dm policy write failed: {exc}") from exc


def _clean_channel(channel: str) -> str:
    clean = (channel or "").strip().lower()
    if not _CHANNEL_RE.match(clean):
        raise IngressError(BAD_INPUT_CODE, f"bad channel {channel!r}")
    return clean


def _norm_sender(sender: str) -> str:
    clean = (sender or "").strip()
    if not clean or len(clean) > MAX_SENDER or not _SENDER_RE.match(clean):
        raise IngressError(BAD_INPUT_CODE, "invalid sender identifier")
    return clean.lower()


def _check_list(values: Optional[List[str]], field: str) -> List[str]:
    out: List[str] = []
    for raw in values or []:
        text = str(raw or "").strip().lower()
        if text == "*":
            out.append("*")
            continue
        if not text or len(text) > MAX_SENDER or not _SENDER_RE.match(text):
            raise IngressError(BAD_INPUT_CODE,
                               f"bad {field} entry {raw!r}")
        out.append(text)
    return sorted(set(out))


def dm_policy_configure(cfg: Any, channel: str, *, allow: Optional[List[str]] = None,
                        deny: Optional[List[str]] = None,
                        default_deny: bool = True,
                        requested_by: str = "cli:user",
                        confirmed: bool = False) -> Dict[str, Any]:
    """Create/replace a channel DM policy (confirmed, estop-aware, audited)."""
    if _estop(cfg):
        raise IngressError(ESTOP_CODE, "emergency stop is latched")
    if not confirmed:
        raise IngressError(CONFIRM_CODE,
                           "dm policy configure requires explicit --confirm")
    clean = _clean_channel(channel)
    allow_list = _check_list(allow, "allow")
    deny_list = _check_list(deny, "deny")
    if "*" in deny_list and "*" in allow_list:
        raise IngressError(BAD_INPUT_CODE,
                           "wildcard allow and wildcard deny cannot coexist")
    state = _load_dm(cfg)
    state["policies"][clean] = {
        "channel": clean, "allow": allow_list, "deny": deny_list,
        "default_deny": bool(default_deny),
        "configured_at": _now(), "updated_by": requested_by,
    }
    _save_dm(cfg, state)
    _audit("dm.configure", requested_by,
           {"channel": clean, "allow": len(allow_list),
            "deny": len(deny_list), "default_deny": bool(default_deny)})
    return {"state": "OK", "policy": _public_dm(state["policies"][clean])}


def _public_dm(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {"channel": rec["channel"], "allow": list(rec.get("allow", [])),
            "deny": list(rec.get("deny", [])),
            "default_deny": bool(rec.get("default_deny", True)),
            "configured_at": rec.get("configured_at"),
            "updated_by": rec.get("updated_by", "")}


def dm_policy_show(cfg: Any, channel: str) -> Dict[str, Any]:
    clean = _clean_channel(channel)
    rec = _load_dm(cfg)["policies"].get(clean)
    if rec is None:
        raise IngressError(NO_POLICY_CODE, f"no dm policy for {clean!r}")
    return {"state": "OK", "policy": _public_dm(rec)}


def dm_policy_remove(cfg: Any, channel: str, *,
                     requested_by: str = "cli:user",
                     confirmed: bool = False) -> Dict[str, Any]:
    if _estop(cfg):
        raise IngressError(ESTOP_CODE, "emergency stop is latched")
    if not confirmed:
        raise IngressError(CONFIRM_CODE,
                           "dm policy remove requires explicit --confirm")
    clean = _clean_channel(channel)
    state = _load_dm(cfg)
    if clean not in state["policies"]:
        raise IngressError(NO_POLICY_CODE, f"no dm policy for {clean!r}")
    del state["policies"][clean]
    _save_dm(cfg, state)
    _audit("dm.remove", requested_by, {"channel": clean})
    return {"state": "OK", "removed": clean}


def dm_allowed(cfg: Any, channel: str, sender: str) -> Tuple[bool, str]:
    """Resolve a DM sender against the channel policy (fail closed).

    Returns ``(allowed, code)``. No policy => ``(False, NO_POLICY_CODE)``;
    an explicit deny (or wildcard deny) always wins; an explicit allow (or
    wildcard allow) permits; otherwise ``default_deny`` applies.
    """
    clean = _clean_channel(channel)
    try:
        who = _norm_sender(sender)
    except IngressError:
        return False, DM_DENIED_CODE
    rec = _load_dm(cfg)["policies"].get(clean)
    if rec is None:
        return False, NO_POLICY_CODE
    deny = set(rec.get("deny", []))
    allow = set(rec.get("allow", []))
    if who in deny or "*" in deny:
        return False, DM_DENIED_CODE
    if who in allow or "*" in allow:
        return True, ""
    return (not bool(rec.get("default_deny", True))), (
        "" if not rec.get("default_deny", True) else DM_DENIED_CODE)


# ------------------------------------------------------- rich messages
@dataclass(frozen=True)
class MessagePart:
    """One bounded message part (text or attachment/action)."""

    kind: str
    text: str = ""
    mime: str = ""
    name: str = ""
    byte_count: int = 0
    sha256: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "text": self.text, "mime": self.mime,
                "name": self.name, "byte_count": self.byte_count,
                "sha256": self.sha256}


def _decode_data(raw: Any) -> Tuple[int, str]:
    """Decode bounded base64 attachment data -> (byte_count, sha256)."""
    if raw is None:
        return 0, ""
    if not isinstance(raw, str) or not _B64_RE.match(raw):
        raise IngressError(BAD_ATTACHMENT_CODE, "attachment data is not base64")
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception:
        raise IngressError(BAD_ATTACHMENT_CODE,
                           "attachment data is not valid base64") from None
    if len(blob) > MAX_PART_BYTES:
        raise IngressError(TOO_LARGE_CODE,
                           f"attachment exceeds {MAX_PART_BYTES} byte cap")
    return len(blob), hashlib.sha256(blob).hexdigest()


def _parse_attachment(raw: Any) -> MessagePart:
    if not isinstance(raw, dict):
        raise IngressError(BAD_ATTACHMENT_CODE, "attachment must be an object")
    kind = str(raw.get("kind") or raw.get("type") or "file").strip().lower()
    if kind not in PART_KINDS or kind == "text":
        raise IngressError(BAD_ATTACHMENT_CODE,
                           f"unknown attachment kind {kind!r}")
    mime = str(raw.get("mime") or "").strip().lower()
    if mime and not _MIME_RE.match(mime):
        raise IngressError(BAD_ATTACHMENT_CODE, f"bad MIME type {mime!r}")
    name = str(raw.get("name") or "").strip()[:MAX_NAME]
    text = str(raw.get("text") or raw.get("title") or "").strip()
    if len(text) > MAX_CAPTION_CHARS:
        raise IngressError(TOO_LARGE_CODE, "attachment caption too long")
    count, sha = _decode_data(raw.get("data"))
    declared = raw.get("size")
    if declared is not None:
        try:
            declared = int(declared)
        except (TypeError, ValueError):
            raise IngressError(BAD_ATTACHMENT_CODE,
                               "attachment size is not an integer") from None
        if declared < 0 or declared > MAX_PART_BYTES:
            raise IngressError(TOO_LARGE_CODE,
                               f"attachment exceeds {MAX_PART_BYTES} byte cap")
        if count and declared != count:
            raise IngressError(BAD_ATTACHMENT_CODE,
                               "declared size does not match data")
        count = count or declared
    return MessagePart(kind=kind, text=text, mime=mime, name=name,
                       byte_count=count, sha256=sha)


def parse_message(payload: Any) -> Dict[str, Any]:
    """Normalize a raw inbound payload into bounded typed parts.

    Accepts plain text or a JSON object with ``text``/``caption``,
    ``attachments``/``parts``, ``buttons``/``cards``, and an ``album_id``.
    Everything is bounded; malformed rich payloads raise typed errors rather
    than being silently dropped.
    """
    if isinstance(payload, (bytes, bytearray)):
        raw = bytes(payload)
        if len(raw) > MAX_BODY_BYTES:
            raise IngressError(TOO_LARGE_CODE,
                               f"payload exceeds {MAX_BODY_BYTES} byte cap")
        text = raw.decode("utf-8", "replace")
    elif isinstance(payload, str):
        if len(payload.encode("utf-8", "replace")) > MAX_BODY_BYTES:
            raise IngressError(TOO_LARGE_CODE,
                               f"payload exceeds {MAX_BODY_BYTES} byte cap")
        text = payload
    else:
        raise IngressError(BAD_INPUT_CODE, "payload must be bytes or text")

    stripped = text.strip()
    obj: Optional[Dict[str, Any]] = None
    if stripped.startswith("{"):
        try:
            candidate = json.loads(stripped)
        except ValueError:
            candidate = None
        if isinstance(candidate, dict):
            obj = candidate

    parts: List[MessagePart] = []
    album_id = ""
    if obj is None:
        body_text = text.strip()
        if len(body_text) > MAX_TEXT_CHARS:
            raise IngressError(TOO_LARGE_CODE, "message text too long")
        if body_text:
            parts.append(MessagePart(kind="text", text=body_text))
    else:
        body_text = str(obj.get("text") or obj.get("caption") or "").strip()
        if len(body_text) > MAX_TEXT_CHARS:
            raise IngressError(TOO_LARGE_CODE, "message text too long")
        if body_text:
            parts.append(MessagePart(kind="text", text=body_text))
        raw_parts = obj.get("attachments")
        if raw_parts is None:
            raw_parts = obj.get("parts")
        for raw in raw_parts or []:
            parts.append(_parse_attachment(raw))
        for raw in obj.get("buttons") or []:
            parts.append(_parse_attachment(
                {"kind": "button", "text": raw if isinstance(raw, str)
                 else str(raw.get("text") or raw.get("label") or "")}))
        for raw in obj.get("cards") or []:
            parts.append(_parse_attachment(
                {"kind": "card", "text": raw if isinstance(raw, str)
                 else str(raw.get("title") or raw.get("text") or "")}))
        album_id = str(obj.get("album_id") or "").strip()
        if album_id and not _ALBUM_RE.match(album_id):
            raise IngressError(BAD_INPUT_CODE, f"bad album id {album_id!r}")

    if len(parts) > MAX_PARTS:
        raise IngressError(TOO_LARGE_CODE,
                           f"message has more than {MAX_PARTS} parts")
    return {"text": body_text, "parts": [p.as_dict() for p in parts],
            "part_count": len(parts), "album_id": album_id,
            "is_album": bool(album_id and len(parts) > 1),
            "rich": bool(album_id or any(p.kind != "text" for p in parts))}


# --------------------------------------------------------- one broker
def _receipt_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / INBOUND_NAME


def _receipt(cfg: Any, rec: Dict[str, Any]) -> None:
    path = _receipt_path(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > MAX_LINES:
            _atomic_write(path, "\n".join(lines[-MAX_LINES:]) + "\n")
    except OSError:
        pass


def _public_receipt(rec: Dict[str, Any]) -> Dict[str, Any]:
    parts = []
    for part in rec.get("parts", []):
        parts.append({"kind": part.get("kind", ""),
                      "mime": part.get("mime", ""),
                      "name": _redact(part.get("name", ""))[:MAX_NAME],
                      "byte_count": int(part.get("byte_count", 0)),
                      "sha256": part.get("sha256", ""),
                      "text_preview": _redact(part.get("text", ""))[:120]})
    return {"id": rec.get("id", ""), "transport": rec.get("transport", ""),
            "channel": rec.get("channel", ""),
            "sender": _redact(rec.get("sender", ""))[:MAX_SENDER],
            "source": rec.get("source", ""),
            "message_id": _redact(rec.get("message_id", ""))[:80],
            "album_id": rec.get("album_id", ""),
            "is_album": bool(rec.get("is_album", False)),
            "part_count": int(rec.get("part_count", 0)),
            "text_preview": _redact(rec.get("text", ""))[:200],
            "received_at": rec.get("received_at")}


def dispatch(cfg: Any, *, transport: str, channel: str, sender: str,
             payload: Any, source: str = "", message_id: str = "",
             account: str = "", received_at: Optional[float] = None,
             requested_by: str = "communications-ingress") -> Dict[str, Any]:
    """The single inbound broker for channel + webhook deliveries.

    Estop first (fail closed). For a ``channel`` transport the DM access
    policy is enforced (default deny). The payload is then parsed into
    bounded rich parts and persisted as one redacted receipt. Never opens a
    socket, never executes message content, never grants authority.
    """
    if _estop(cfg):
        raise IngressError(ESTOP_CODE, "emergency stop is latched")
    if transport not in TRANSPORTS:
        raise IngressError(BAD_TRANSPORT_CODE,
                           f"unknown ingress transport {transport!r}")
    clean_channel = _clean_channel(channel)
    clean_sender = (sender or "").strip()
    if len(clean_sender) > MAX_SENDER or not _SENDER_RE.match(clean_sender):
        raise IngressError(BAD_INPUT_CODE, "invalid sender identifier")
    if transport == "channel":
        allowed, why = dm_allowed(cfg, clean_channel, clean_sender)
        if not allowed:
            _audit("dm.refused", requested_by,
                   {"channel": clean_channel, "code": why})
            raise IngressError(why, "sender is not permitted to DM this "
                                    "channel (fail closed)")
    parsed = parse_message(payload)
    rec = {"id": "in_" + hashlib.sha256(
        f"{clean_channel}:{clean_sender}:{_now()}".encode()).hexdigest()[:24],
        "schema": SCHEMA, "transport": transport, "channel": clean_channel,
        "sender": clean_sender, "source": (source or "")[:80],
        "message_id": (message_id or "")[:80],
        "account": (account or "")[:64],
        "text": parsed["text"], "parts": parsed["parts"],
        "part_count": parsed["part_count"], "album_id": parsed["album_id"],
        "is_album": parsed["is_album"], "rich": parsed["rich"],
        "received_at": float(received_at) if received_at else _now(),
        "requested_by": requested_by}
    _receipt(cfg, rec)
    _audit("deliver", requested_by,
           {"transport": transport, "channel": clean_channel,
            "part_count": rec["part_count"], "is_album": rec["is_album"],
            "message_id": rec["message_id"]})
    return {"state": "DELIVERED", "schema": SCHEMA,
            "transport": transport, "channel": clean_channel,
            "message": _public_receipt(rec)}


def records(cfg: Any, *, channel: str = "", limit: int = 100) -> Dict[str, Any]:
    """Redacted receipts, newest first (read-only dashboard/CLI view)."""
    path = _receipt_path(cfg)
    rows: List[Dict[str, Any]] = []
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                if channel and rec.get("channel") != channel:
                    continue
                rows.append(_public_receipt(rec))
        except OSError:
            pass
    rows = rows[-max(0, int(limit)):]
    rows.reverse()
    return {"state": "OK", "schema": SCHEMA, "messages": rows,
            "count": len(rows)}


# --------------------------------------------------------------- listeners
class ChannelListener:
    """In-process channel listener over the existing verification kernel.

    Starts no socket. The dashboard/CLI HTTP ingress hands an already-received
    delivery here; it verifies signature/freshness/replay through
    ``channels_inbound.verify_delivery`` and then dispatches through the one
    :func:`dispatch` broker.
    """

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg

    def deliver(self, *, channel: str, payload: bytes, signature: str = "",
                timestamp: Optional[float] = None, sender: str = "",
                event: str = "message", delivery_id: str = "",
                source: str = "channel-listener") -> Dict[str, Any]:
        from .. import channels_inbound as CI

        # DM access is checked BEFORE verification so a denied sender cannot
        # burn the replay nonce of a later legitimate delivery. Only an
        # allowed sender reaches the signature/freshness/replay kernel; the
        # broker re-checks the policy before dispatch.
        allowed, why = dm_allowed(self.cfg, channel, sender)
        if not allowed:
            _audit("dm.refused", "channel-listener",
                   {"channel": channel, "code": why})
            raise IngressError(why, "sender is not permitted to DM this "
                                    "channel (fail closed)")
        info = CI.verify_delivery(
            self.cfg, channel=channel, payload=payload, signature=signature,
            timestamp=timestamp, sender=sender, event=event,
            delivery_id=delivery_id)
        return dispatch(self.cfg, transport="channel",
                        channel=info["channel"], sender=info["sender"],
                        payload=payload, source=source,
                        message_id=delivery_id)


class WebhookListener:
    """In-process webhook listener over the existing webhook kernel.

    Starts no socket. The existing ``/hooks/<sub_id>`` HTTP route (or a CLI
    replay) hands the delivery here; it verifies through ``webhooks.verify``
    and then dispatches through the one :func:`dispatch` broker.
    """

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg

    def deliver(self, *, subscription: str, payload: bytes,
                signature: str = "", timestamp: float = 0.0,
                sender: str = "webhook", delivery_id: str = "",
                source: str = "webhook-listener") -> Dict[str, Any]:
        from .. import webhooks as WH

        out = WH.verify(self.cfg, subscription, payload, signature,
                        float(timestamp), sender=sender,
                        delivery_id=delivery_id)
        return dispatch(self.cfg, transport="webhook", channel=subscription,
                        sender=sender or "webhook", payload=payload,
                        source=source, message_id=delivery_id,
                        account=out.get("subscription", ""))


__all__ = [
    "BAD_ATTACHMENT_CODE", "BAD_INPUT_CODE", "BAD_TRANSPORT_CODE",
    "CONFIRM_CODE", "ChannelListener", "DM_DENIED_CODE", "DM_SCHEMA",
    "ESTOP_CODE", "IngressError", "MAX_PARTS", "MessagePart", "NO_POLICY_CODE",
    "PART_KINDS", "SCHEMA", "TOO_LARGE_CODE", "TRANSPORTS",
    "UNKNOWN_CODE", "WebhookListener", "dispatch", "dm_allowed",
    "dm_policy_configure", "dm_policy_remove", "dm_policy_show",
    "parse_message", "records",
]
