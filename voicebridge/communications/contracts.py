"""Packet 8A: versioned communications-monitor contracts.

Replaces the legacy text-snapshot inbox/report surfaces with typed, versioned
records for ``scan``, ``account_health``, ``communication_item``, ``cursor``,
``evidence_asset``, ``report_manifest``, and ``notification``. These are
contract-and-store units only; Packets 8B–8D wire them into runtime behavior.

Facts enforced here:

- ``new`` means "first observed after the last committed account cursor", never
  "currently unread". Unread and unknown read-state are separate.
- Stable identity uses platform item/thread IDs first; the versioned fallback
  fingerprint (account scope + normalized sender/thread + timestamp + bounded
  content) covers items without platform IDs and records collision buckets.
- Every message-derived field is bounded and redaction-labeled at the write
  boundary; private message bodies never enter fixtures or this module.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = 1
IDENTITY_VERSION = 1
PREVIEW_MAX = 200
SENDER_MAX = 80

REDACTED_MARK = "[REDACTED]"


class Platform(str, Enum):
    GMAIL = "gmail"
    INSTAGRAM = "instagram"
    MESSENGER = "messenger"


SUPPORTED_PLATFORMS = frozenset(p.value for p in Platform)


class ReadState(str, Enum):
    UNREAD = "unread"
    READ = "read"
    UNKNOWN = "unknown"


class ScanStatus(str, Enum):
    """Typed scan outcome. Distinct from account health and item new-ness."""

    NEW_ITEMS = "new_items"           # new item(s) found; report committed
    NO_CHANGE = "no_change"           # healthy, nothing new
    PARTIAL = "partial"               # some accounts/items readable, some not
    OVERFLOW = "overflow"             # scan bound hit before cursor; partial
    AUTH_REQUIRED = "auth_required"   # logged-out / 2FA / CAPTCHA / consent
    STALE = "stale"                   # only cached/old data available
    DEGRADED = "degraded"             # degraded but not auth-failed
    FAILED = "failed"                 # untyped failure, no data
    UNAVAILABLE = "unavailable"       # browser/CDP/lease precondition missing


class AccountHealthStatus(str, Enum):
    HEALTHY = "healthy"
    NO_CHANGE = "no_change"
    PARTIAL = "partial"
    AUTH_REQUIRED = "auth_required"
    STALE = "stale"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class ReportStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    DEGRADED = "degraded"


class EvidenceStatus(str, Enum):
    AVAILABLE = "available"
    NOT_AVAILABLE = "not_available"
    TARGET_HIDDEN = "target_hidden"
    CLIP_FAILED = "clip_failed"
    PRIVACY_REDACTED = "privacy_redacted"


class IdentitySource(str, Enum):
    PLATFORM_ID = "platform_id"
    THREAD_ID = "thread_id"
    FALLBACK_FINGERPRINT = "fallback_fingerprint"


class NotificationKind(str, Enum):
    DASHBOARD = "dashboard"
    DESKTOP = "desktop"
    VOICE = "voice"


class NotificationStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    ACKED = "acked"
    DEDUPED = "deduped"
    SUPPRESSED_QUIET_HOURS = "suppressed_quiet_hours"


class Sensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    PERSONAL = "personal"
    SECRET = "secret"


class CommunicationsSchemaError(Exception):
    """A record failed versioned schema validation (never a silent pass)."""


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _bound(text: str, limit: int) -> str:
    if not text:
        return ""
    return text[:limit]


def bound_preview(text: Optional[str]) -> str:
    return _bound(text or "", PREVIEW_MAX)


def content_hash(*parts: Optional[str]) -> str:
    joined = "|".join(p or "" for p in parts)
    return hashlib.sha256(joined.encode("utf-8", "replace")).hexdigest()


def fallback_fingerprint(*, platform: Platform, account_scope: str,
                         sender: str, thread: str, ts_normalized: str,
                         content: str,
                         identity_version: int = IDENTITY_VERSION) -> str:
    """Versioned deterministic fallback identity.

    Includes account scope and normalized fields so identical snippets in
    different accounts/threads do not collide. Record ``collision_key`` in the
    item so a future collision is visible instead of silently merged.
    """
    src = "|".join([
        "vb-item", str(identity_version), platform.value, account_scope,
        sender.strip().lower(), thread.strip().lower(),
        ts_normalized.strip(), content.strip().lower(),
    ])
    return "fp:" + hashlib.sha256(src.encode("utf-8", "replace")).hexdigest()


def stable_item_identity(*, platform: Platform, account_scope: str,
                         source_item_id: Optional[str],
                         thread_id: Optional[str],
                         sender: str, ts_normalized: str,
                         content: str,
                         identity_version: int = IDENTITY_VERSION,
                         ) -> Tuple[str, IdentitySource, str, str]:
    """Resolve the stable identity for an item.

    Returns ``(identity, source, fingerprint, collision_key)``. Platform
    item/thread IDs win when present; otherwise the versioned fallback
    fingerprint is the identity and doubles as its collision bucket.
    """
    fingerprint = fallback_fingerprint(
        platform=platform, account_scope=account_scope, sender=sender,
        thread=thread_id or "", ts_normalized=ts_normalized, content=content,
        identity_version=identity_version)
    if source_item_id:
        return (f"{platform.value}:{account_scope}:{source_item_id}",
                IdentitySource.PLATFORM_ID, fingerprint, fingerprint)
    if thread_id:
        return (f"{platform.value}:{account_scope}:t:{thread_id}",
                IdentitySource.THREAD_ID, fingerprint, fingerprint)
    return (fingerprint, IdentitySource.FALLBACK_FINGERPRINT,
            fingerprint, fingerprint)


@dataclass(frozen=True)
class CommunicationItem:
    """One normalized inbox row. Immutable and validated at construction."""

    platform: Platform
    account_scope: str
    identity: str
    identity_source: IdentitySource
    identity_version: int
    collision_key: str
    sender: str
    display_name: str
    ts_normalized: str
    ts_raw: str
    subject: str
    preview: str
    is_new: bool
    read_state: ReadState
    safe_url: str
    captured_at: float
    content_hash: str
    adapter: str
    adapter_version: str
    evidence_status: EvidenceStatus
    sensitivity: Sensitivity
    redacted: bool
    source_item_id: str = ""
    thread_id: str = ""
    fingerprint: str = ""

    @classmethod
    def create(cls, *, platform: Platform, account_scope: str,
               sender: str, display_name: str = "", ts_normalized: str = "",
               ts_raw: str = "", subject: str = "", preview: str = "",
               is_new: bool, read_state: ReadState,
               safe_url: str = "", captured_at: Optional[float] = None,
               adapter: str = "", adapter_version: str = "",
               evidence_status: EvidenceStatus = EvidenceStatus.NOT_AVAILABLE,
               sensitivity: Sensitivity = Sensitivity.PERSONAL,
               source_item_id: Optional[str] = None,
               thread_id: Optional[str] = None,
               identity_version: int = IDENTITY_VERSION,
               ) -> "CommunicationItem":
        """Build a validated item, resolving identity and hashes centrally."""
        if platform not in Platform:
            raise CommunicationsSchemaError(f"unknown platform {platform!r}")
        if not account_scope or not account_scope.strip():
            raise CommunicationsSchemaError("account_scope required")
        if not sender or not sender.strip():
            raise CommunicationsSchemaError("sender required")
        now = time.time() if captured_at is None else float(captured_at)
        subject = _bound(subject or "", 240)
        preview = bound_preview(preview)
        sender_b = _bound(sender or "", SENDER_MAX)
        display_b = _bound(display_name or "", SENDER_MAX)
        identity, source, fingerprint, collision = stable_item_identity(
            platform=platform, account_scope=account_scope,
            source_item_id=source_item_id, thread_id=thread_id,
            sender=sender_b, ts_normalized=ts_normalized or "",
            content=f"{subject} {preview}")
        h = content_hash(platform.value, account_scope, identity,
                         sender_b, subject, preview, ts_normalized)
        return cls(
            platform=platform, account_scope=account_scope,
            identity=identity, identity_source=source,
            identity_version=identity_version, collision_key=collision,
            sender=sender_b, display_name=display_b,
            ts_normalized=ts_normalized or "", ts_raw=ts_raw or "",
            subject=subject, preview=preview, is_new=bool(is_new),
            read_state=read_state, safe_url=safe_url or "",
            captured_at=now, content_hash=h, adapter=adapter,
            adapter_version=adapter_version,
            evidence_status=evidence_status, sensitivity=sensitivity,
            redacted=bool(subject or preview),
            source_item_id=source_item_id or "",
            thread_id=thread_id or "", fingerprint=fingerprint)

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["platform"] = self.platform.value
        d["identity_source"] = self.identity_source.value
        d["read_state"] = self.read_state.value
        d["evidence_status"] = self.evidence_status.value
        d["sensitivity"] = self.sensitivity.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CommunicationItem":
        return cls(
            platform=Platform(d["platform"]),
            account_scope=d["account_scope"],
            identity=d["identity"],
            identity_source=IdentitySource(d["identity_source"]),
            identity_version=int(d["identity_version"]),
            collision_key=d["collision_key"],
            sender=d["sender"], display_name=d["display_name"],
            ts_normalized=d["ts_normalized"], ts_raw=d["ts_raw"],
            subject=d["subject"], preview=d["preview"],
            is_new=bool(d["is_new"]), read_state=ReadState(d["read_state"]),
            safe_url=d["safe_url"], captured_at=float(d["captured_at"]),
            content_hash=d["content_hash"], adapter=d["adapter"],
            adapter_version=d["adapter_version"],
            evidence_status=EvidenceStatus(d["evidence_status"]),
            sensitivity=Sensitivity(d["sensitivity"]),
            redacted=bool(d["redacted"]),
            source_item_id=d.get("source_item_id", ""),
            thread_id=d.get("thread_id", ""), fingerprint=d.get("fingerprint", ""))


@dataclass(frozen=True)
class ScanRecord:
    scan_id: str
    platform: Platform
    account_scope: str
    started_at: float
    finished_at: float
    status: ScanStatus
    adapter: str
    adapter_version: str
    captured_at: float
    new_count: int = 0
    seen_count: int = 0
    total_count: int = 0
    overflow: bool = False
    reason: str = ""
    cursor_before: str = ""
    cursor_after: str = ""
    report_id: str = ""

    def __post_init__(self):
        if not isinstance(self.platform, Platform):
            raise CommunicationsSchemaError("platform must be a Platform")
        if not self.account_scope:
            raise CommunicationsSchemaError("account_scope required")
        if not isinstance(self.status, ScanStatus):
            raise CommunicationsSchemaError("status must be a ScanStatus")
        if self.finished_at < self.started_at:
            raise CommunicationsSchemaError("finished_at before started_at")

    @classmethod
    def create(cls, *, platform: Platform, account_scope: str,
               status: ScanStatus, adapter: str, adapter_version: str,
               started_at: Optional[float] = None,
               finished_at: Optional[float] = None,
               new_count: int = 0, seen_count: int = 0,
               total_count: int = 0, overflow: bool = False,
               reason: str = "", cursor_before: str = "",
               cursor_after: str = "", report_id: str = "",
               scan_id: Optional[str] = None) -> "ScanRecord":
        now = time.time()
        return cls(
            scan_id=scan_id or _new_id("scan"),
            platform=platform, account_scope=account_scope,
            started_at=now if started_at is None else float(started_at),
            finished_at=now if finished_at is None else float(finished_at),
            status=status, adapter=adapter, adapter_version=adapter_version,
            captured_at=now, new_count=int(new_count),
            seen_count=int(seen_count), total_count=int(total_count),
            overflow=bool(overflow), reason=_bound(reason, 300),
            cursor_before=cursor_before, cursor_after=cursor_after,
            report_id=report_id)


@dataclass(frozen=True)
class AccountHealthRecord:
    platform: Platform
    account_scope: str
    status: AccountHealthStatus
    checked_at: float
    adapter_version: str
    freshness_s: Optional[float] = None
    auth_ok: Optional[bool] = None
    last_success_at: Optional[float] = None
    reason: str = ""

    def __post_init__(self):
        if not isinstance(self.platform, Platform):
            raise CommunicationsSchemaError("platform must be a Platform")
        if not self.account_scope:
            raise CommunicationsSchemaError("account_scope required")
        if not isinstance(self.status, AccountHealthStatus):
            raise CommunicationsSchemaError("status must be AccountHealthStatus")

    @classmethod
    def create(cls, *, platform: Platform, account_scope: str,
               status: AccountHealthStatus, adapter_version: str,
               checked_at: Optional[float] = None, freshness_s: Optional[float] = None,
               auth_ok: Optional[bool] = None,
               last_success_at: Optional[float] = None,
               reason: str = "") -> "AccountHealthRecord":
        return cls(
            platform=platform, account_scope=account_scope, status=status,
            checked_at=time.time() if checked_at is None else float(checked_at),
            adapter_version=adapter_version, freshness_s=freshness_s,
            auth_ok=auth_ok, last_success_at=last_success_at,
            reason=_bound(reason, 300))


@dataclass(frozen=True)
class CursorRecord:
    platform: Platform
    account_scope: str
    cursor_id: str
    watermark: float
    baseline: bool = False
    committed_at: Optional[float] = None
    report_id: str = ""
    prior_cursor_id: str = ""

    def __post_init__(self):
        if not isinstance(self.platform, Platform):
            raise CommunicationsSchemaError("platform must be a Platform")
        if not self.account_scope:
            raise CommunicationsSchemaError("account_scope required")
        if self.watermark < 0:
            raise CommunicationsSchemaError("negative watermark")


@dataclass(frozen=True)
class EvidenceAssetRecord:
    asset_id: str
    report_id: str
    item_identity: str
    mime: str
    byte_count: int
    sha256: str
    captured_at: float
    redaction_status: EvidenceStatus
    source_item_id: str = ""
    width: int = 0
    height: int = 0

    def __post_init__(self):
        if not isinstance(self.redaction_status, EvidenceStatus):
            raise CommunicationsSchemaError(
                "redaction_status must be an EvidenceStatus")
        if not self.sha256:
            raise CommunicationsSchemaError("asset sha256 required")
        if self.byte_count < 0:
            raise CommunicationsSchemaError("negative byte_count")

    @classmethod
    def create(cls, *, report_id: str, item_identity: str, mime: str,
               byte_count: int, sha256: str,
               redaction_status: EvidenceStatus,
               captured_at: Optional[float] = None,
               source_item_id: str = "", width: int = 0, height: int = 0,
               asset_id: Optional[str] = None) -> "EvidenceAssetRecord":
        return cls(
            asset_id=asset_id or _new_id("asset"),
            report_id=report_id, item_identity=item_identity, mime=mime,
            byte_count=int(byte_count), sha256=sha256,
            captured_at=time.time() if captured_at is None else float(captured_at),
            redaction_status=redaction_status, source_item_id=source_item_id,
            width=int(width), height=int(height))


@dataclass(frozen=True)
class ReportManifest:
    report_id: str
    schema_version: int
    created_at: float
    window_start: float
    window_end: float
    status: ReportStatus
    new_count: int
    generated_analysis: bool = False
    analysis_disclaimer: str = ""
    retention_days: int = 90
    summary: str = ""
    platforms: Tuple[str, ...] = ()
    accounts: Tuple[str, ...] = ()
    item_identities: Tuple[str, ...] = ()
    assets: Tuple[str, ...] = ()
    partial_failures: Tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.status, ReportStatus):
            raise CommunicationsSchemaError("status must be a ReportStatus")
        if self.schema_version != SCHEMA_VERSION:
            raise CommunicationsSchemaError(
                f"unsupported schema_version {self.schema_version}")
        if self.window_end < self.window_start:
            raise CommunicationsSchemaError("window_end before window_start")
        if self.new_count < 0:
            raise CommunicationsSchemaError("negative new_count")

    @classmethod
    def create(cls, *, window_start: float, window_end: float,
               status: ReportStatus, new_count: int,
               created_at: Optional[float] = None,
               generated_analysis: bool = False,
               analysis_disclaimer: str = "",
               retention_days: int = 90, summary: str = "",
               platforms: Tuple[str, ...] = (),
               accounts: Tuple[str, ...] = (),
               item_identities: Tuple[str, ...] = (),
               assets: Tuple[str, ...] = (),
               partial_failures: Tuple[str, ...] = (),
               report_id: Optional[str] = None) -> "ReportManifest":
        return cls(
            report_id=report_id or _new_id("report"),
            schema_version=SCHEMA_VERSION,
            created_at=time.time() if created_at is None else float(created_at),
            window_start=float(window_start), window_end=float(window_end),
            status=status, new_count=int(new_count),
            generated_analysis=bool(generated_analysis),
            analysis_disclaimer=_bound(analysis_disclaimer, 300),
            retention_days=int(retention_days), summary=_bound(summary, 600),
            platforms=tuple(platforms), accounts=tuple(accounts),
            item_identities=tuple(item_identities), assets=tuple(assets),
            partial_failures=tuple(partial_failures))

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass(frozen=True)
class NotificationRecord:
    notification_id: str
    report_id: str
    kind: NotificationKind
    status: NotificationStatus
    dedupe_key: str
    created_at: float
    new_count: int = 0
    quiet_hours: bool = False
    sent_at: Optional[float] = None
    acked_at: Optional[float] = None
    dedupe_of: str = ""

    def __post_init__(self):
        if not isinstance(self.kind, NotificationKind):
            raise CommunicationsSchemaError("kind must be a NotificationKind")
        if not isinstance(self.status, NotificationStatus):
            raise CommunicationsSchemaError("status must be a NotificationStatus")
        if not self.dedupe_key:
            raise CommunicationsSchemaError("dedupe_key required")

    @classmethod
    def create(cls, *, report_id: str, kind: NotificationKind,
               status: NotificationStatus, dedupe_key: str,
               created_at: Optional[float] = None, new_count: int = 0,
               quiet_hours: bool = False, sent_at: Optional[float] = None,
               acked_at: Optional[float] = None, dedupe_of: str = "",
               notification_id: Optional[str] = None) -> "NotificationRecord":
        return cls(
            notification_id=notification_id or _new_id("notif"),
            report_id=report_id, kind=kind, status=status,
            dedupe_key=_bound(dedupe_key, 160),
            created_at=time.time() if created_at is None else float(created_at),
            new_count=int(new_count), quiet_hours=bool(quiet_hours),
            sent_at=sent_at, acked_at=acked_at, dedupe_of=dedupe_of)


def record_as_dict(record: Any) -> Dict[str, Any]:
    """Generic typed→dict for JSON boundaries (platform/status enums expanded)."""
    if isinstance(record, CommunicationItem):
        return record.as_dict()
    if isinstance(record, ReportManifest):
        return record.as_dict()
    if isinstance(record, ScanRecord):
        d = asdict(record)
        d["platform"] = record.platform.value
        d["status"] = record.status.value
        return d
    if isinstance(record, AccountHealthRecord):
        d = asdict(record)
        d["platform"] = record.platform.value
        d["status"] = record.status.value
        return d
    if isinstance(record, CursorRecord):
        d = asdict(record)
        d["platform"] = record.platform.value
        return d
    if isinstance(record, EvidenceAssetRecord):
        d = asdict(record)
        d["redaction_status"] = record.redaction_status.value
        return d
    if isinstance(record, NotificationRecord):
        d = asdict(record)
        d["kind"] = record.kind.value
        d["status"] = record.status.value
        return d
    raise CommunicationsSchemaError(f"unsupported record {type(record).__name__}")


def json_dumps(record: Any) -> str:
    return json.dumps(record_as_dict(record), ensure_ascii=False, sort_keys=True)