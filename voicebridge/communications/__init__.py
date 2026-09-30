"""Phase 8 — hourly communications monitor contracts and store (Packet 8A).

Contract-and-store only. This package MUST NOT be imported by any runtime
module until Packets 8B–8D wire it in; importing it here would change live
behavior. ``contracts.py`` holds the versioned typed schemas and stable-identity
logic; ``store.py`` holds the private SQLite store, retention, lease, and the
atomic cursor/report commit protocol.
"""
from .contracts import (
    AccountHealthRecord, AccountHealthStatus, CommunicationsSchemaError,
    CommunicationItem, CursorRecord, EvidenceAssetRecord, EvidenceStatus,
    IdentitySource, NotificationKind, NotificationRecord, NotificationStatus,
    Platform, ReadState, ReportManifest, ReportStatus, SCHEMA_VERSION,
    ScanRecord, ScanStatus, Sensitivity, SUPPORTED_PLATFORMS,
    bound_preview, content_hash, fallback_fingerprint, record_as_dict,
    stable_item_identity,
)
from .store import (
    DEFAULT_RETENTION_SECONDS, CommunicationsStore, CommunicationsStoreError,
)

__all__ = [
    "AccountHealthRecord", "AccountHealthStatus", "CommunicationsSchemaError",
    "CommunicationItem", "CommunicationsStore", "CommunicationsStoreError",
    "CursorRecord", "DEFAULT_RETENTION_SECONDS", "EvidenceAssetRecord",
    "EvidenceStatus", "IdentitySource", "NotificationKind", "NotificationRecord",
    "NotificationStatus", "Platform", "ReadState", "ReportManifest",
    "ReportStatus", "SCHEMA_VERSION", "ScanRecord", "ScanStatus", "Sensitivity",
    "SUPPORTED_PLATFORMS", "bound_preview", "content_hash",
    "fallback_fingerprint", "record_as_dict", "stable_item_identity",
]