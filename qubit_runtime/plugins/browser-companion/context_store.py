"""Owner-scoped, consume-on-read Browser Context Protocol storage.

Ported and narrowed from the companion plugin of ``hermes-browser-extension``
(https://github.com/abundantbeing/hermes-browser-extension) at pin
16d42430ff45d5833cb6176b401db867b591c2f2, MIT, Copyright (c) 2026 Jon Komet.
See PROVENANCE.json beside this file.

The companion plugin receives Browser Context Protocol (BCP) v2 turn messages as
untrusted user content, so a record is bound to the trusted Qubit execution
owner before any tool can observe or consume it. The process-local store is
TTL- and LRU-bounded and deliberately has no unscoped "latest context"
accessor.

Local divergence from upstream (documented in PROVENANCE.json):
* Nothing is persisted. Upstream kept durable redacted tool receipts and a
  rotation journal in the plugin data dir; both are dropped from this port
  (``docs/EXT_COLLISION_NOTE.md``), so the store is memory-only and carries no
  write path to disk.
* Protocol ids are rebranded to ``qubit.browser.*``.
* The expiry worker is a daemon thread started on first use rather than on
  construction, so importing the plugin costs nothing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import time
from typing import Any, Callable, Optional

BCP_TURN_PROTOCOL_ID = "qubit.browser.turn.v2"
BCP_CONTEXT_PROTOCOL_ID = "qubit.browser.context.v1"
DEFAULT_TTL_SECONDS = 300.0
DEFAULT_MAX_ENTRIES = 100
DEFAULT_MAX_ENTRIES_PER_PRINCIPAL = 10
DEFAULT_MAX_EVENTS = 200
_MAX_BCP_TURN_CHARS = 64_000
_MAX_IDENTIFIER_CHARS = 256

_UNAVAILABLE = {"available": False, "reason": "Browser context unavailable."}

# Browser-supplied metadata copied into diagnostics must look like an opaque
# identifier: a UUID, a hex hash or a short slug. The extension redacts the whole
# envelope before sending it, but this plugin does not rely on that: anything
# with spaces, URL punctuation, redaction placeholders or a well-known
# credential prefix is dropped instead of being shown to the agent.
_OPAQUE_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_OPAQUE_HEX = re.compile(r"[0-9a-fA-F]{8,128}")
_OPAQUE_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\-]{0,63}")
_CREDENTIAL_SHAPE = re.compile(
    r"(?i)^(?:(?:sk|pk|rk|api|key|tok|token|secret|pass|pwd|bearer|basic|auth)[-_.:]"
    r"|gh[pousr]_|github_pat_|xox[abprs]-|akia[0-9a-z]{8}|asia[0-9a-z]{8}|eyj[a-z0-9_-]{6})"
)

# Per-process salt: owner correlation values shown to the agent cannot be
# brute-forced back into a private session id, yet stay stable within a run.
_FINGERPRINT_SALT = secrets.token_bytes(16)


@dataclass(frozen=True)
class ContextOwner:
    """Trusted Qubit execution identity for a browser context record."""

    principal_id: str
    session_id: str
    turn_id: str
    task_id: str


def _trusted_identifier(value: Any) -> str:
    """Normalize one trusted hook value without consulting model/tool input."""
    if value is None:
        return ""
    value = str(value).strip()
    return value[:_MAX_IDENTIFIER_CHARS]


def owner_from_hook_kwargs(kwargs: dict[str, Any]) -> Optional[ContextOwner]:
    """Build an owner only from trusted hook kwargs.

    Tool arguments and browser-provided BCP data are intentionally ignored.
    Session, turn and task ids are required because they are the trusted
    execution identifiers shared by the pre-call and pre-tool hooks.

    The turn/task ids are optional in upstream Qubit payloads that predate them,
    so a payload missing either one yields ``None`` (no context is stored) rather
    than an owner built from model-controlled text.
    """
    session_id = _trusted_identifier(kwargs.get("session_id"))
    turn_id = _trusted_identifier(kwargs.get("turn_id"))
    task_id = _trusted_identifier(kwargs.get("task_id"))
    if not session_id or not turn_id or not task_id:
        return None
    return ContextOwner(
        principal_id=f"session:{session_id}",
        session_id=session_id,
        turn_id=turn_id,
        task_id=task_id,
    )


def _is_plain_dict(value: Any) -> bool:
    return isinstance(value, dict)


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list)


def _bounded_identifier(value: Any, limit: int = 160) -> str:
    """Return ``value`` only when it is a short opaque token, else ``""``."""
    if not isinstance(value, str) or not value or len(value) > limit:
        return ""
    if _OPAQUE_UUID.fullmatch(value) or _OPAQUE_HEX.fullmatch(value):
        return value
    if _OPAQUE_SLUG.fullmatch(value) and not _CREDENTIAL_SHAPE.match(value):
        return value
    return ""


def _unknown_control() -> dict[str, Any]:
    return {
        "availability": "unknown",
        "lease_owned": False,
        "controller_id": "",
        "browser_profile_id": "",
        "tab_id": 0,
        "frame_id": 0,
        "document_generation": 0,
    }


def _stored_int(value: Any) -> Optional[int]:
    """``value`` when it is a plain int (bools are not ints here), else ``None``."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _normalize_browser_control(value: Any) -> dict[str, Any]:
    """Retain only validated historical control metadata from an untrusted turn."""
    if not isinstance(value, dict):
        return _unknown_control()
    availability = value.get("availability")
    if value.get("route") == "extension-controller" and availability == "unsupported":
        # The gateway has no extension control; no exclusivity rule rides on this state.
        return {**_unknown_control(), "availability": "unsupported"}
    if value.get("route") != "extension-controller" or value.get("isolated_fallback") != "forbidden":
        return _unknown_control()
    if availability == "unavailable":
        return {**_unknown_control(), "availability": "unavailable"}
    if availability == "not_requested":
        return {**_unknown_control(), "availability": "not_requested"}
    if availability != "available":
        return _unknown_control()
    controller_id = _bounded_identifier(value.get("controller_id"))
    browser_profile_id = _bounded_identifier(value.get("browser_profile_id"))
    tab_id = _stored_int(value.get("tab_id"))
    frame_id = _stored_int(value.get("frame_id"))
    document_generation = _stored_int(value.get("document_generation"))
    lease_owned = value.get("lease_owned")
    if (
        not controller_id
        or not browser_profile_id
        or tab_id is None
        or frame_id is None
        or document_generation is None
        or tab_id <= 0
        or frame_id < 0
        or document_generation <= 0
        or not isinstance(lease_owned, bool)
    ):
        return _unknown_control()
    return {
        "availability": "available",
        "lease_owned": lease_owned,
        "controller_id": controller_id,
        "browser_profile_id": browser_profile_id,
        "tab_id": tab_id,
        "frame_id": frame_id,
        "document_generation": document_generation,
    }


def parse_bcp_v2_turn(text: str) -> Optional[dict[str, Any]]:
    """Return a normalized BCP v2 browser payload, or ``None`` fail-closed.

    The current user message must be one JSON BCP v2 turn envelope. Legacy prose
    blocks, reference-only turns, malformed JSON, and arbitrary JSON objects are
    deliberately not cached.
    """
    raw = str(text or "")
    if not raw or len(raw) > _MAX_BCP_TURN_CHARS:
        return None
    try:
        envelope = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not _is_plain_dict(envelope) or envelope.get("protocol") != BCP_TURN_PROTOCOL_ID:
        return None

    human_input = envelope.get("human_input")
    browser_context = envelope.get("browser_context")
    attachment_context = envelope.get("attachment_context")
    receipt = envelope.get("source_receipt")
    if not all(_is_plain_dict(value) for value in (human_input, browser_context, attachment_context, receipt)):
        return None
    if human_input.get("source") != "composer" or not isinstance(human_input.get("text"), str):
        return None
    if not _is_string_list(attachment_context.get("items")):
        return None
    if receipt.get("protocol") != BCP_TURN_PROTOCOL_ID or receipt.get("version") != 2:
        return None
    if browser_context.get("delivery") != "full" or receipt.get("delivery") != "full":
        return None

    payload = browser_context.get("payload")
    if not _is_plain_dict(payload) or payload.get("protocol") != BCP_CONTEXT_PROTOCOL_ID:
        return None
    if not all(_is_plain_dict(payload.get(key)) for key in ("contextScope", "settings", "activeTab", "pageContext")):
        return None
    if not _is_string_list(payload.get("tabs")) or not _is_string_list(payload.get("selectedTabs")):
        return None

    context_hash = receipt.get("context_hash", "")
    if not isinstance(context_hash, str):
        return None
    scope = payload["contextScope"].get("mode", "unknown")
    if not isinstance(scope, str):
        return None
    context_hash = _bounded_identifier(context_hash, 80)
    scope = _bounded_identifier(scope, 80) or "unknown"

    # json.loads already created only JSON-safe values. Deep-copy so a caller
    # cannot mutate a cached payload after this function returns.
    return {
        "payload": deepcopy(payload),
        "browser_control": _normalize_browser_control(envelope.get("browser_control")),
        "provenance": {
            "protocol": BCP_TURN_PROTOCOL_ID,
            "delivery": "full",
            "context_hash": context_hash,
        },
        "payload_hash": context_hash,
        "scope": scope,
    }


def parse_bcp_v2_reference(text: str) -> Optional[str]:
    """Return the context hash of a valid BCP v2 *reference* turn, else ``None``.

    A reference turn carries no payload, only the hash of context the extension
    sent earlier. Kept because the upstream contract distinguishes the two; this
    port has no HTTP upload shelf, so the hash is only used for diagnostics.
    """
    raw = str(text or "")
    if not raw or len(raw) > _MAX_BCP_TURN_CHARS:
        return None
    try:
        envelope = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not _is_plain_dict(envelope) or envelope.get("protocol") != BCP_TURN_PROTOCOL_ID:
        return None
    browser_context = envelope.get("browser_context")
    receipt = envelope.get("source_receipt")
    if not _is_plain_dict(browser_context) or not _is_plain_dict(receipt):
        return None
    if receipt.get("protocol") != BCP_TURN_PROTOCOL_ID or receipt.get("version") != 2:
        return None
    if browser_context.get("delivery") != "reference" or receipt.get("delivery") != "reference":
        return None
    named = _bounded_identifier(browser_context.get("context_hash"), 80)
    if not named or named != _bounded_identifier(receipt.get("context_hash"), 80):
        return None
    return named


def _principal_fingerprint(owner: ContextOwner) -> str:
    """Return a diagnostic-safe owner correlation value, never a raw ID."""
    return hmac.new(_FINGERPRINT_SALT, owner.principal_id.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def _owner_fingerprint(owner: ContextOwner) -> str:
    """Bind records to the exact trusted execution owner tuple."""
    material = "\0".join((owner.principal_id, owner.session_id, owner.turn_id, owner.task_id))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass
class BrowserContextRecord:
    """One opaque capability bound to one trusted Qubit owner tuple."""

    context_id: str
    owner: ContextOwner
    payload: Optional[dict[str, Any]]
    provenance: dict[str, Any]
    payload_hash: str
    scope: str
    browser_control: dict[str, Any]
    created_at: float
    expires_at: float
    consumed_at: Optional[float] = None

    @property
    def consumed(self) -> bool:
        return self.consumed_at is not None


@dataclass
class _StoredEvent:
    name: str
    data: dict[str, Any]
    owner: ContextOwner
    ts: float


@dataclass
class BrowserContextStore:
    """Thread-safe owner-scoped, memory-only cache for BCP v2 context records."""

    ttl_seconds: float = DEFAULT_TTL_SECONDS
    max_entries: int = DEFAULT_MAX_ENTRIES
    max_entries_per_principal: int = DEFAULT_MAX_ENTRIES_PER_PRINCIPAL
    max_events: int = DEFAULT_MAX_EVENTS
    clock: Callable[[], float] = time.time
    _records: OrderedDict = field(default_factory=OrderedDict, init=False, repr=False)
    _events: list = field(default_factory=list, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _worker: Optional[threading.Thread] = field(default=None, init=False, repr=False)

    # -- lifecycle ------------------------------------------------------
    def _now(self) -> float:
        return float(self.clock())

    def _ensure_worker(self) -> None:
        """Start the idle expiry worker on first stored record, not at import."""
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._expire_loop, name="browser-context-expiry", daemon=True)
        self._worker.start()

    def _expire_loop(self) -> None:
        while not self._stop.wait(0.05):
            with self._lock:
                self._prune_expired_locked(self._now())

    def close(self) -> None:
        """Drop cached payloads and stop the idle expiry worker."""
        self._stop.set()
        worker, self._worker = self._worker, None
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=1)
        with self._lock:
            for record in self._records.values():
                record.payload = None
            self._records.clear()
            self._events.clear()

    # -- internals ------------------------------------------------------
    def _prune_expired_locked(self, now: float) -> None:
        for context_id, record in list(self._records.items()):
            if record.expires_at <= now:
                record.payload = None
                del self._records[context_id]
                self._record_event_locked("browser.context.expired", {}, record.owner, now)

    def _evict_locked(self) -> None:
        """Keep the store under its global and per-principal bounds (oldest first)."""
        while len(self._records) > max(0, int(self.max_entries)):
            _, record = self._records.popitem(last=False)
            record.payload = None
        counts: dict[str, int] = {}
        for record in reversed(list(self._records.values())):
            counts[record.owner.principal_id] = counts.get(record.owner.principal_id, 0) + 1
            if counts[record.owner.principal_id] > max(1, int(self.max_entries_per_principal)):
                del self._records[record.context_id]
                record.payload = None

    def _record_event_locked(self, name: str, data: dict[str, Any], owner: ContextOwner, now: float) -> None:
        self._events.append(_StoredEvent(name=name, data=dict(data), owner=owner, ts=now))
        excess = len(self._events) - max(0, int(self.max_events))
        if excess > 0:
            del self._events[:excess]

    def _metadata(self, record: BrowserContextRecord) -> dict[str, Any]:
        now = self._now()
        return {
            "available": True,
            "context_id": record.context_id,
            "scope": record.scope,
            "payload_hash": record.payload_hash,
            "protocol": record.provenance.get("protocol", BCP_TURN_PROTOCOL_ID),
            "delivery": record.provenance.get("delivery", "full"),
            "browser_control": dict(record.browser_control),
            "consumed": record.consumed,
            "age_seconds": round(max(0.0, now - record.created_at), 3),
            "expires_in_seconds": round(max(0.0, record.expires_at - now), 3),
            "owner_fingerprint": _principal_fingerprint(record.owner),
        }

    @staticmethod
    def _owned(record: BrowserContextRecord, owner: ContextOwner) -> bool:
        return _owner_fingerprint(record.owner) == _owner_fingerprint(owner)

    # -- store / read ---------------------------------------------------
    def put_bcp_v2(self, text: str, owner: ContextOwner) -> dict[str, Any]:
        """Validate and store one BCP v2 full-context turn for ``owner``."""
        normalized = parse_bcp_v2_turn(text)
        if normalized is None:
            return dict(_UNAVAILABLE)
        return self._store_normalized(normalized, owner)

    def _store_normalized(self, normalized: dict[str, Any], owner: ContextOwner) -> dict[str, Any]:
        """Bind one already-validated payload to the trusted ``owner``."""
        now = self._now()
        ttl = max(0.0, float(self.ttl_seconds))
        record = BrowserContextRecord(
            context_id=secrets.token_hex(16),
            owner=owner,
            payload=normalized["payload"],
            provenance=normalized["provenance"],
            payload_hash=normalized["payload_hash"],
            scope=normalized["scope"],
            browser_control=normalized["browser_control"],
            created_at=now,
            expires_at=now + ttl,
        )
        with self._lock:
            self._prune_expired_locked(now)
            self._records[record.context_id] = record
            self._evict_locked()
            self._record_event_locked(
                "browser.context.updated",
                {"protocol": record.provenance["protocol"], "scope": record.scope},
                owner,
                now,
            )
            return self._metadata(record)

    def status_for_owner(self, owner: ContextOwner) -> dict[str, Any]:
        """Metadata for this owner's newest live record, or the unavailable answer."""
        now = self._now()
        with self._lock:
            self._prune_expired_locked(now)
            for record in reversed(list(self._records.values())):
                if self._owned(record, owner):
                    status = self._metadata(record)
                    status["live"] = False
                    status["count"] = sum(1 for r in self._records.values() if self._owned(r, owner))
                    return status
        return dict(_UNAVAILABLE)

    def consume_for_owner(self, context_id: Any, owner: ContextOwner) -> Optional[dict[str, Any]]:
        """Take one record's payload exactly once, only for its exact owner."""
        wanted = str(context_id or "").strip().lower()
        if not wanted:
            return None
        now = self._now()
        with self._lock:
            self._prune_expired_locked(now)
            record = self._records.get(wanted)
            if record is None or not self._owned(record, owner) or record.consumed:
                return None
            payload = record.payload
            record.consumed_at = now
            record.payload = None
            self._record_event_locked("browser.context.consumed", {"scope": record.scope}, owner, now)
            result = {
                "available": payload is not None,
                "context_id": record.context_id,
                "scope": record.scope,
                "payload_hash": record.payload_hash,
                "protocol": record.provenance.get("protocol", BCP_TURN_PROTOCOL_ID),
                "payload": payload,
            }
            del self._records[record.context_id]
            return result

    def clear_for_owner(self, owner: ContextOwner) -> dict[str, Any]:
        """Drop every record this owner holds; other owners are untouched."""
        now = self._now()
        with self._lock:
            removed = [r for r in self._records.values() if self._owned(r, owner)]
            for record in removed:
                record.payload = None
                del self._records[record.context_id]
            if removed:
                self._record_event_locked("browser.context.cleared", {"count": len(removed)}, owner, now)
            return {"available": True, "cleared": len(removed), "owner_fingerprint": _principal_fingerprint(owner)}
