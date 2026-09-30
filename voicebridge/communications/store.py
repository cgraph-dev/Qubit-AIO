"""Packet 8A: private SQLite state for the Phase 8 communications monitor.

Owns ``scan`` / ``account_health`` / ``item`` / ``cursor`` / ``report_manifest``
/ ``evidence_asset`` / ``notification`` / ``lease`` / ``retention_log`` rows
with versioned migrations (PRAGMA ``user_version``), typed statuses, dry-run +
audited retention, an overlap lease, and the **atomic cursor/report commit**
protocol:

- A cursor advances only after every new item AND the report manifest AND its
  assets are durably committed inside ONE transaction.
- ``is_new`` is decided here, from the last committed account cursor: an
  identity first observed after that cursor is ``new``; unread/read/unknown
  read-state is a separate field.
- A failed/partial/auth-required scan records its own scan + health rows but
  NEVER advances the cursor.

No runtime module imports this until Packets 8B–8D wire it in; the database is
created only where a caller opens it (tests use temp paths).
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .contracts import (
    AccountHealthRecord, AccountHealthStatus, CommunicationItem,
    CursorRecord, EvidenceAssetRecord, NotificationKind, NotificationRecord,
    Platform, ReportManifest, ScanRecord, ScanStatus, _new_id,
)

# SQLite migration version (PRAGMA user_version). Independent of the report
# manifest SCHEMA_VERSION: manifest v1 is unchanged; the store schema advanced
# to v2 to persist evidence-asset BYTES durably (Packet 8D) and to v3 for the
# monotonic, reboot-aware overlap lease + bounded notification retry/dead-letter
# state (Packet 8I).
STORE_SCHEMA_VERSION = 3

# Retention defaults (plan Phase 8 task 18 — confirmed by Packet 8A).
DEFAULT_RETENTION_SECONDS = {
    "scan_health": 90 * 86400,
    "item": 90 * 86400,
    "report_manifest": 90 * 86400,
    "evidence_asset": 30 * 86400,
    "notification": 90 * 86400,
}

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS scan(
  scan_id TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  account_scope TEXT NOT NULL,
  started_at REAL NOT NULL,
  finished_at REAL NOT NULL,
  status TEXT NOT NULL,
  adapter TEXT NOT NULL,
  adapter_version TEXT NOT NULL,
  captured_at REAL NOT NULL,
  new_count INTEGER NOT NULL DEFAULT 0,
  seen_count INTEGER NOT NULL DEFAULT 0,
  total_count INTEGER NOT NULL DEFAULT 0,
  overflow INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT '',
  cursor_before TEXT NOT NULL DEFAULT '',
  cursor_after TEXT NOT NULL DEFAULT '',
  report_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_scan_acct ON scan(platform, account_scope, captured_at);

CREATE TABLE IF NOT EXISTS account_health(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,
  account_scope TEXT NOT NULL,
  status TEXT NOT NULL,
  checked_at REAL NOT NULL,
  adapter_version TEXT NOT NULL,
  freshness_s REAL,
  auth_ok INTEGER,
  last_success_at REAL,
  reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_health_acct ON account_health(platform, account_scope, checked_at);

CREATE TABLE IF NOT EXISTS item(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,
  account_scope TEXT NOT NULL,
  identity TEXT NOT NULL,
  identity_source TEXT NOT NULL DEFAULT 'fallback_fingerprint',
  identity_version INTEGER NOT NULL DEFAULT 1,
  collision_key TEXT NOT NULL DEFAULT '',
  source_item_id TEXT NOT NULL DEFAULT '',
  thread_id TEXT NOT NULL DEFAULT '',
  sender TEXT NOT NULL DEFAULT '',
  display_name TEXT NOT NULL DEFAULT '',
  ts_normalized TEXT NOT NULL DEFAULT '',
  ts_raw TEXT NOT NULL DEFAULT '',
  subject TEXT NOT NULL DEFAULT '',
  preview TEXT NOT NULL DEFAULT '',
  read_state TEXT NOT NULL DEFAULT 'unknown',
  is_new INTEGER NOT NULL DEFAULT 0,
  safe_url TEXT NOT NULL DEFAULT '',
  captured_at REAL NOT NULL,
  content_hash TEXT NOT NULL DEFAULT '',
  adapter TEXT NOT NULL DEFAULT '',
  adapter_version TEXT NOT NULL DEFAULT '',
  evidence_status TEXT NOT NULL DEFAULT 'not_available',
  sensitivity TEXT NOT NULL DEFAULT 'personal',
  redacted INTEGER NOT NULL DEFAULT 0,
  first_seen_at REAL NOT NULL,
  last_seen_at REAL NOT NULL,
  report_id TEXT NOT NULL DEFAULT '',
  notification_id TEXT NOT NULL DEFAULT '',
  UNIQUE(platform, account_scope, identity)
);
CREATE INDEX IF NOT EXISTS idx_item_acct ON item(platform, account_scope, first_seen_at);
CREATE INDEX IF NOT EXISTS idx_item_coll ON item(platform, account_scope, collision_key);

CREATE TABLE IF NOT EXISTS cursor(
  platform TEXT NOT NULL,
  account_scope TEXT NOT NULL,
  cursor_id TEXT NOT NULL,
  watermark REAL NOT NULL DEFAULT 0,
  baseline INTEGER NOT NULL DEFAULT 0,
  committed_at REAL,
  report_id TEXT NOT NULL DEFAULT '',
  prior_cursor_id TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(platform, account_scope)
);
CREATE TABLE IF NOT EXISTS cursor_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,
  account_scope TEXT NOT NULL,
  cursor_id TEXT NOT NULL,
  watermark REAL NOT NULL DEFAULT 0,
  baseline INTEGER NOT NULL DEFAULT 0,
  committed_at REAL,
  report_id TEXT NOT NULL DEFAULT '',
  prior_cursor_id TEXT NOT NULL DEFAULT '',
  replaced_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS report_manifest(
  report_id TEXT PRIMARY KEY,
  schema_version INTEGER NOT NULL,
  created_at REAL NOT NULL,
  window_start REAL NOT NULL,
  window_end REAL NOT NULL,
  status TEXT NOT NULL,
  new_count INTEGER NOT NULL DEFAULT 0,
  generated_analysis INTEGER NOT NULL DEFAULT 0,
  analysis_disclaimer TEXT NOT NULL DEFAULT '',
  retention_days INTEGER NOT NULL DEFAULT 90,
  summary TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS evidence_asset(
  asset_id TEXT PRIMARY KEY,
  report_id TEXT NOT NULL,
  item_identity TEXT NOT NULL DEFAULT '',
  source_item_id TEXT NOT NULL DEFAULT '',
  mime TEXT NOT NULL DEFAULT '',
  byte_count INTEGER NOT NULL DEFAULT 0,
  sha256 TEXT NOT NULL DEFAULT '',
  captured_at REAL NOT NULL,
  redaction_status TEXT NOT NULL DEFAULT 'not_available',
  width INTEGER NOT NULL DEFAULT 0,
  height INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_asset_report ON evidence_asset(report_id);

CREATE TABLE IF NOT EXISTS notification(
  notification_id TEXT PRIMARY KEY,
  report_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  dedupe_key TEXT NOT NULL,
  created_at REAL NOT NULL,
  new_count INTEGER NOT NULL DEFAULT 0,
  quiet_hours INTEGER NOT NULL DEFAULT 0,
  sent_at REAL,
  acked_at REAL,
  dedupe_of TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_notif_dedupe ON notification(dedupe_key, kind);

CREATE TABLE IF NOT EXISTS lease(
  name TEXT PRIMARY KEY,
  owner TEXT NOT NULL,
  acquired_at REAL NOT NULL,
  expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS retention_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ran_at REAL NOT NULL,
  category TEXT NOT NULL,
  cutoff REAL NOT NULL,
  removed INTEGER NOT NULL DEFAULT 0,
  dry_run INTEGER NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT ''
);
"""

# Packet 8D: evidence asset BYTES become durable with the report commit so a
# live scan persists the actual screenshot bytes (not metadata only) and a
# bundle rendered later (render_from_store / regen) carries real .webp assets.
# The existing 30-day evidence_asset retention prunes the bytes too; the report
# bundle's own 90-day retention governs the rendered copy.
_SCHEMA_V2 = """
ALTER TABLE evidence_asset ADD COLUMN data BLOB;
"""

# Packet 8I: lease deadlines become MONOTONIC and reboot-aware (boot_id), so a
# wall-clock jump or a reboot cannot silently expire or pin a live overlap
# lease; notifications gain a bounded retry count and a terminal dead-letter
# timestamp so a failing delivery is retried at most N times and then recorded
# as dead-letter (never dropped silently, never retried forever).
_SCHEMA_V3 = """
ALTER TABLE lease ADD COLUMN acquired_mono REAL NOT NULL DEFAULT 0;
ALTER TABLE lease ADD COLUMN expires_mono REAL NOT NULL DEFAULT 0;
ALTER TABLE lease ADD COLUMN boot_id TEXT NOT NULL DEFAULT '';
ALTER TABLE notification ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE notification ADD COLUMN dead_letter_at REAL;
"""


def _boot_id() -> str:
    """Kernel boot identifier (stable per boot on Linux).

    Used to make lease deadlines reboot-aware: monotonic time restarts at each
    boot, so a lease persisted from a previous boot must never look alive.
    Returns "" when the identifier cannot be read (defensive; an empty value
    makes ``_lease_held`` fall back to monotonic expiry only).
    """
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except Exception:  # noqa: BLE001 - read is best-effort
        return ""


def _lease_held(row, now_wall: float, now_mono: float) -> bool:
    """True when a lease row is currently held by a live process.

    - a row from a DIFFERENT boot is never held (stale after reboot);
    - a v3 row is held by its monotonic deadline only (wall jumps cannot
      expire or extend it);
    - a legacy v1/v2 row (no ``expires_mono``) is held by wall time so old
      databases keep working while they migrate.
    """
    boot = row["boot_id"] if "boot_id" in row.keys() else ""
    if boot and boot != _boot_id():
        return False
    try:
        expires_mono = float(row["expires_mono"] or 0)
    except (TypeError, ValueError):
        expires_mono = 0.0
    if expires_mono > 0:
        return expires_mono > now_mono
    return float(row["expires_at"] or 0) > now_wall


class CommunicationsStoreError(Exception):
    """Typed store failure (never a silent pass)."""


class CommunicationsStore:
    """SQLite state store for the Phase 8 monitor (Packet 8A)."""

    def __init__(self, path: Path | str, *,
                 private_files: bool = True) -> None:
        self._path = Path(path)
        self._private_files = bool(private_files)
        if private_files:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(self._path.parent, 0o700)
            except OSError:
                pass
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._migrate()
        if private_files:
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass

    # ------------------------------------------------------------ schema
    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        migrations = [_SCHEMA_V1, _SCHEMA_V2, _SCHEMA_V3]
        with self._conn:  # atomic migration transaction
            for i in range(version, len(migrations)):
                self._conn.executescript(migrations[i])
                self._conn.execute(f"PRAGMA user_version={i + 1}")

    @property
    def schema_version(self) -> int:
        with self._lock:
            return self._conn.execute("PRAGMA user_version").fetchone()[0]

    # ------------------------------------------------------------ health
    def record_health(self, health: AccountHealthRecord) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO account_health(platform, account_scope, status,"
                " checked_at, adapter_version, freshness_s, auth_ok,"
                " last_success_at, reason)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (health.platform.value, health.account_scope,
                 health.status.value, health.checked_at,
                 health.adapter_version, health.freshness_s,
                 health.auth_ok, health.last_success_at, health.reason))

    def latest_health(self, platform: Platform, account_scope: str
                      ) -> Optional[AccountHealthRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM account_health WHERE platform=? AND account_scope=?"
                " ORDER BY checked_at DESC LIMIT 1",
                (platform.value, account_scope)).fetchone()
            if row is None:
                return None
            return AccountHealthRecord(
                platform=Platform(row["platform"]),
                account_scope=row["account_scope"],
                status=AccountHealthStatus(row["status"]),
                checked_at=row["checked_at"], adapter_version=row["adapter_version"],
                freshness_s=row["freshness_s"], auth_ok=row["auth_ok"],
                last_success_at=row["last_success_at"], reason=row["reason"])

    # ------------------------------------------------------------ scan
    def record_scan(self, scan: ScanRecord) -> None:
        """Persist a scan WITHOUT advancing any cursor (health-only path)."""
        with self._lock, self._conn:
            self._insert_scan(scan)

    def _insert_scan(self, scan: ScanRecord) -> None:
        self._conn.execute(
            "INSERT INTO scan(scan_id, platform, account_scope, started_at,"
            " finished_at, status, adapter, adapter_version, captured_at,"
            " new_count, seen_count, total_count, overflow, reason,"
            " cursor_before, cursor_after, report_id)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (scan.scan_id, scan.platform.value, scan.account_scope,
             scan.started_at, scan.finished_at, scan.status.value,
             scan.adapter, scan.adapter_version, scan.captured_at,
             scan.new_count, scan.seen_count, scan.total_count,
             int(scan.overflow), scan.reason, scan.cursor_before,
             scan.cursor_after, scan.report_id))

    def latest_scan(self, platform: Platform, account_scope: str
                    ) -> Optional[ScanRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM scan WHERE platform=? AND account_scope=?"
                " ORDER BY captured_at DESC LIMIT 1",
                (platform.value, account_scope)).fetchone()
            if row is None:
                return None
            return ScanRecord(
                scan_id=row["scan_id"], platform=Platform(row["platform"]),
                account_scope=row["account_scope"], started_at=row["started_at"],
                finished_at=row["finished_at"],
                status=ScanStatus(row["status"]), adapter=row["adapter"],
                adapter_version=row["adapter_version"], captured_at=row["captured_at"],
                new_count=row["new_count"], seen_count=row["seen_count"],
                total_count=row["total_count"], overflow=bool(row["overflow"]),
                reason=row["reason"], cursor_before=row["cursor_before"],
                cursor_after=row["cursor_after"], report_id=row["report_id"])

    # ------------------------------------------------------------ cursor
    def peek_cursor(self, platform: Platform, account_scope: str
                    ) -> Optional[CursorRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM cursor WHERE platform=? AND account_scope=?",
                (platform.value, account_scope)).fetchone()
            if row is None:
                return None
            return CursorRecord(
                platform=platform, account_scope=account_scope,
                cursor_id=row["cursor_id"], watermark=row["watermark"],
                baseline=bool(row["baseline"]), committed_at=row["committed_at"],
                report_id=row["report_id"], prior_cursor_id=row["prior_cursor_id"])

    def asset_bytes_for_report(self, report_id: str
                               ) -> Dict[str, bytes]:
        """Durable screenshot bytes for a committed report (Packet 8D).

        Returns ``{asset_id: bytes}`` for the assets that were actually
        captured at scan time; assets with a typed missing-evidence reason have
        no entry. Used by the report renderer so bundles produced from live
        scans carry the real .webp bytes, not metadata only.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT asset_id, data FROM evidence_asset WHERE report_id=?",
                (report_id,)).fetchall()
            return {r["asset_id"]: bytes(r["data"]) for r in rows
                    if r["data"] is not None}

    def item_exists(self, platform: Platform, account_scope: str,
                    identity: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM item WHERE platform=? AND account_scope=? AND identity=?",
                (platform.value, account_scope, identity)).fetchone()
            return row is not None

    # ----------------------------------------------- atomic report commit
    @staticmethod
    def validate_asset_links(manifest: ReportManifest,
                             assets: Sequence[EvidenceAssetRecord]) -> None:
        """Manifest/assets must reference each other exactly (report/asset)."""
        asset_ids = [a.asset_id for a in assets]
        for a in assets:
            if a.report_id != manifest.report_id:
                raise CommunicationsStoreError(
                    f"asset {a.asset_id} belongs to report {a.report_id},"
                    f" not {manifest.report_id}")
        declared = set(manifest.assets)
        provided = set(asset_ids)
        if provided - declared:
            raise CommunicationsStoreError(
                f"asset(s) not referenced by manifest: "
                f"{sorted(provided - declared)}")
        if declared - provided:
            raise CommunicationsStoreError(
                f"manifest references missing asset(s): "
                f"{sorted(declared - provided)}")

    def _commit_locked(self, *, scan: ScanRecord,
                       items: Sequence[CommunicationItem],
                       manifest: ReportManifest,
                       assets: Sequence[EvidenceAssetRecord],
                       notifications: Sequence[NotificationRecord],
                       baseline: bool,
                       asset_bytes: Optional[Dict[str, bytes]] = None) -> int:
        """Single-transaction commit. Caller holds the store lock + txn."""
        if not isinstance(manifest, ReportManifest):
            raise CommunicationsStoreError("manifest must be a ReportManifest")
        self._insert_scan(scan)

        cur = self._conn.execute(
            "SELECT * FROM cursor WHERE platform=? AND account_scope=?",
            (scan.platform.value, scan.account_scope)).fetchone()

        if baseline:
            for it in items:
                self._conn.execute(
                    "INSERT OR IGNORE INTO item(platform, account_scope,"
                    " identity, identity_source, identity_version,"
                    " collision_key, source_item_id, thread_id, sender,"
                    " display_name, ts_normalized, ts_raw, subject, preview,"
                    " read_state, is_new, safe_url, captured_at, content_hash,"
                    " adapter, adapter_version, evidence_status, sensitivity,"
                    " redacted, first_seen_at, last_seen_at, report_id)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,?,?,?)",
                    (it.platform.value, it.account_scope, it.identity,
                     it.identity_source.value, it.identity_version,
                     it.collision_key, it.source_item_id, it.thread_id,
                     it.sender, it.display_name, it.ts_normalized, it.ts_raw,
                     it.subject, it.preview, it.read_state.value,
                     it.safe_url, it.captured_at, it.content_hash, it.adapter,
                     it.adapter_version, it.evidence_status.value,
                     it.sensitivity.value, int(it.redacted),
                     it.captured_at, it.captured_at, manifest.report_id))
        elif cur is None:
            raise CommunicationsStoreError(
                f"no committed cursor for {scan.platform.value}/"
                f"{scan.account_scope}: first commit must be a baseline")
        else:
            new_total = 0
            for it in items:
                existing = self._conn.execute(
                    "SELECT id, first_seen_at FROM item WHERE platform=? AND"
                    " account_scope=? AND identity=?",
                    (it.platform.value, it.account_scope, it.identity)).fetchone()
                is_new = existing is None
                if is_new:
                    new_total += 1
                now = time.time()
                if existing is None:
                    self._conn.execute(
                        "INSERT INTO item(platform, account_scope, identity,"
                        " identity_source, identity_version, collision_key,"
                        " source_item_id, thread_id, sender, display_name,"
                        " ts_normalized, ts_raw, subject, preview, read_state,"
                        " is_new, safe_url, captured_at, content_hash, adapter,"
                        " adapter_version, evidence_status, sensitivity,"
                        " redacted, first_seen_at, last_seen_at, report_id)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (it.platform.value, it.account_scope, it.identity,
                         it.identity_source.value, it.identity_version,
                         it.collision_key, it.source_item_id, it.thread_id,
                         it.sender, it.display_name, it.ts_normalized,
                         it.ts_raw, it.subject, it.preview, it.read_state.value,
                         int(is_new), it.safe_url, it.captured_at,
                         it.content_hash, it.adapter, it.adapter_version,
                         it.evidence_status.value, it.sensitivity.value,
                         int(it.redacted), now, now, manifest.report_id))
                else:
                    self._conn.execute(
                        "UPDATE item SET last_seen_at=?, read_state=?,"
                        " evidence_status=?, safe_url=?, report_id=? WHERE id=?",
                        (now, it.read_state.value, it.evidence_status.value,
                         it.safe_url, manifest.report_id, existing["id"]))

        # Cross-validate manifest/assets AFTER item writes so a mismatch rolls
        # back the whole transaction (scan + items + manifest + cursor).
        self.validate_asset_links(manifest, assets)
        self._conn.execute(
            "INSERT INTO report_manifest(report_id, schema_version, created_at,"
            " window_start, window_end, status, new_count, generated_analysis,"
            " analysis_disclaimer, retention_days, summary)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (manifest.report_id, manifest.schema_version, manifest.created_at,
             manifest.window_start, manifest.window_end, manifest.status.value,
             manifest.new_count, int(manifest.generated_analysis),
             manifest.analysis_disclaimer, manifest.retention_days,
             manifest.summary))
        for a in assets:
            self._conn.execute(
                "INSERT INTO evidence_asset(asset_id, report_id, item_identity,"
                " source_item_id, mime, byte_count, sha256, captured_at,"
                " redaction_status, width, height, data)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (a.asset_id, a.report_id, a.item_identity, a.source_item_id,
                 a.mime, a.byte_count, a.sha256, a.captured_at,
                 a.redaction_status.value, a.width, a.height,
                 (asset_bytes or {}).get(a.asset_id)))
        for n in notifications:
            self._conn.execute(
                "INSERT INTO notification(notification_id, report_id, kind,"
                " status, dedupe_key, created_at, new_count, quiet_hours,"
                " sent_at, acked_at, dedupe_of)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (n.notification_id, n.report_id, n.kind.value, n.status.value,
                 n.dedupe_key, n.created_at, n.new_count, int(n.quiet_hours),
                 n.sent_at, n.acked_at, n.dedupe_of))

        watermark = max((it.captured_at for it in items),
                        default=scan.finished_at)
        cursor_id = _new_id("cursor")
        committed_at = time.time()
        prior_id = cur["cursor_id"] if cur else ""
        if cur is not None:
            self._conn.execute(
                "INSERT INTO cursor_history(platform, account_scope, cursor_id,"
                " watermark, baseline, committed_at, report_id, prior_cursor_id,"
                " replaced_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (cur["platform"], cur["account_scope"], cur["cursor_id"],
                 cur["watermark"], cur["baseline"], cur["committed_at"],
                 cur["report_id"], cur["prior_cursor_id"], committed_at))
        self._conn.execute(
            "INSERT INTO cursor(platform, account_scope, cursor_id, watermark,"
            " baseline, committed_at, report_id, prior_cursor_id)"
            " VALUES(?,?,?,?,?,?,?,?)"
            " ON CONFLICT(platform, account_scope) DO UPDATE SET"
            " cursor_id=excluded.cursor_id, watermark=excluded.watermark,"
            " baseline=excluded.baseline, committed_at=excluded.committed_at,"
            " report_id=excluded.report_id,"
            " prior_cursor_id=excluded.prior_cursor_id",
            (scan.platform.value, scan.account_scope, cursor_id, watermark,
             int(baseline), committed_at, manifest.report_id, prior_id))
        if baseline:
            new_total = 0
        return new_total

    def commit_baseline(self, *, scan: ScanRecord,
                        items: Sequence[CommunicationItem],
                        manifest: ReportManifest,
                        assets: Sequence[EvidenceAssetRecord] = (),
                        notifications: Sequence[NotificationRecord] = (),
                        asset_bytes: Optional[Dict[str, bytes]] = None) -> int:
        """First activation: record visible items as seen, no old-message alert."""
        with self._lock, self._conn:
            scan = self._with_cursor_bounds(scan)
            count = self._commit_locked(scan=scan, items=items,
                                        manifest=manifest, assets=assets,
                                        notifications=notifications,
                                        baseline=True,
                                        asset_bytes=asset_bytes)
            self._conn.execute(
                "UPDATE scan SET new_count=?, cursor_before=?, cursor_after=?,"
                " report_id=? WHERE scan_id=?",
                (0, scan.cursor_before,
                 self._conn.execute(
                     "SELECT cursor_id FROM cursor WHERE platform=? AND"
                     " account_scope=?",
                     (scan.platform.value, scan.account_scope)).fetchone()["cursor_id"],
                 manifest.report_id, scan.scan_id))
            return count

    def commit_new(self, *, scan: ScanRecord,
                   items: Sequence[CommunicationItem],
                   manifest: ReportManifest,
                   assets: Sequence[EvidenceAssetRecord] = (),
                   notifications: Sequence[NotificationRecord] = (),
                   asset_bytes: Optional[Dict[str, bytes]] = None) -> int:
        """Commit new items + report atomically and advance the cursor."""
        with self._lock, self._conn:
            scan = self._with_cursor_bounds(scan)
            count = self._commit_locked(scan=scan, items=items,
                                        manifest=manifest, assets=assets,
                                        notifications=notifications,
                                        baseline=False,
                                        asset_bytes=asset_bytes)
            self._conn.execute(
                "UPDATE scan SET new_count=?, cursor_before=?, cursor_after=?,"
                " report_id=? WHERE scan_id=?",
                (count, scan.cursor_before,
                 self._conn.execute(
                     "SELECT cursor_id FROM cursor WHERE platform=? AND"
                     " account_scope=?",
                     (scan.platform.value, scan.account_scope)).fetchone()["cursor_id"],
                 manifest.report_id, scan.scan_id))
            return count

    def _with_cursor_bounds(self, scan: ScanRecord) -> ScanRecord:
        cur = self.peek_cursor(scan.platform, scan.account_scope)
        if cur is not None:
            scan = ScanRecord(
                scan_id=scan.scan_id, platform=scan.platform,
                account_scope=scan.account_scope, started_at=scan.started_at,
                finished_at=scan.finished_at, status=scan.status,
                adapter=scan.adapter, adapter_version=scan.adapter_version,
                captured_at=scan.captured_at, new_count=scan.new_count,
                seen_count=scan.seen_count, total_count=scan.total_count,
                overflow=scan.overflow, reason=scan.reason,
                cursor_before=cur.cursor_id, cursor_after="",
                report_id=scan.report_id)
        return scan

    def rollback_cursor(self, platform: Platform, account_scope: str
                        ) -> CursorRecord:
        """Restore the previous committed cursor from history."""
        with self._lock, self._conn:
            hist = self._conn.execute(
                "SELECT * FROM cursor_history WHERE platform=? AND account_scope=?"
                " ORDER BY replaced_at DESC, id DESC LIMIT 1",
                (platform.value, account_scope)).fetchone()
            if hist is None:
                raise CommunicationsStoreError(
                    f"no cursor history for {platform.value}/{account_scope}")
            self._conn.execute(
                "INSERT INTO cursor(platform, account_scope, cursor_id,"
                " watermark, baseline, committed_at, report_id, prior_cursor_id)"
                " VALUES(?,?,?,?,?,?,?,?)"
                " ON CONFLICT(platform, account_scope) DO UPDATE SET"
                " cursor_id=excluded.cursor_id, watermark=excluded.watermark,"
                " baseline=excluded.baseline, committed_at=excluded.committed_at,"
                " report_id=excluded.report_id,"
                " prior_cursor_id=excluded.prior_cursor_id",
                (platform.value, account_scope, hist["cursor_id"],
                 hist["watermark"], hist["baseline"], hist["committed_at"],
                 hist["report_id"], hist["prior_cursor_id"]))
            self._conn.execute(
                "DELETE FROM cursor_history WHERE id=?", (hist["id"],))
            return CursorRecord(
                platform=platform, account_scope=account_scope,
                cursor_id=hist["cursor_id"], watermark=hist["watermark"],
                baseline=bool(hist["baseline"]), committed_at=hist["committed_at"],
                report_id=hist["report_id"],
                prior_cursor_id=hist["prior_cursor_id"])

    # ---------------------------------------------------------- retention
    def apply_retention(self, *, dry_run: bool = True,
                        retention_seconds: Optional[Dict[str, int]] = None,
                        now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Prune expired rows per category.

        Never follows symlinks (row-only), records what it removed in
        ``retention_log``, and returns the affected counts. ``dry_run`` only
        counts and does not delete.
        """
        cfg = {**DEFAULT_RETENTION_SECONDS,
               **(retention_seconds or {})}
        now = time.time() if now is None else float(now)
        plan = [
            ("scan_health",
             "DELETE FROM scan WHERE captured_at < ?",
             "DELETE FROM account_health WHERE checked_at < ?",
             cfg["scan_health"]),
            ("item", "DELETE FROM item WHERE last_seen_at < ?", None,
             cfg["item"]),
            ("report_manifest",
             "DELETE FROM report_manifest WHERE created_at < ?", None,
             cfg["report_manifest"]),
            ("evidence_asset",
             "DELETE FROM evidence_asset WHERE captured_at < ?", None,
             cfg["evidence_asset"]),
            ("notification",
             "DELETE FROM notification WHERE created_at < ?", None,
             cfg["notification"]),
        ]
        results: List[Dict[str, Any]] = []
        with self._lock, self._conn:
            for category, scan_sql, health_sql, seconds in plan:
                cutoff = now - seconds
                n_scan = 0
                n_health = 0
                if scan_sql:
                    if dry_run:
                        n_scan = self._conn.execute(
                            scan_sql.replace("DELETE", "SELECT COUNT(*)"),
                            (cutoff,)).fetchone()[0]
                    else:
                        n_scan = self._conn.execute(scan_sql, (cutoff,)).rowcount
                if health_sql:
                    if dry_run:
                        n_health = self._conn.execute(
                            health_sql.replace("DELETE", "SELECT COUNT(*)"),
                            (cutoff,)).fetchone()[0]
                    else:
                        n_health = self._conn.execute(health_sql,
                                                      (cutoff,)).rowcount
                removed = n_scan + n_health
                if not dry_run and removed:
                    self._conn.execute(
                        "INSERT INTO retention_log(ran_at, category, cutoff,"
                        " removed, dry_run, note)"
                        " VALUES(?,?,?,?,?,?)",
                        (now, category, cutoff, removed, 0,
                         "scan_health deletes scan+account_health"))
                results.append({"category": category, "cutoff": cutoff,
                                "removed": removed, "dry_run": dry_run})
        return results

    # ------------------------------------------------------------ lease
    def acquire_lease(self, name: str, owner: str, ttl_s: float = 3600) -> bool:
        """Acquire or renew ``name`` for ``owner`` for ``ttl_s`` seconds.

        Deadlines are MONOTONIC (``expires_mono``, compared against
        ``time.monotonic()``) plus a wall ``expires_at`` for reporting. A lease
        is held only while its ``boot_id`` matches the current boot — a stale
        lease left by a previous boot or a process that died is immediately
        acquirable (a reboot never pins the monitor into OVERLAP forever).
        Wall-clock jumps never expire or extend a live lease.
        """
        now = time.time()
        mono = time.monotonic()
        boot = _boot_id()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM lease WHERE name=?", (name,)).fetchone()
            if row is not None and row["owner"] != owner:
                if _lease_held(row, now, mono):
                    return False
            self._conn.execute(
                "INSERT INTO lease(name, owner, acquired_at, expires_at,"
                " acquired_mono, expires_mono, boot_id)"
                " VALUES(?,?,?,?,?,?,?)"
                " ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,"
                " acquired_at=excluded.acquired_at,"
                " expires_at=excluded.expires_at,"
                " acquired_mono=excluded.acquired_mono,"
                " expires_mono=excluded.expires_mono,"
                " boot_id=excluded.boot_id",
                (name, owner, now, now + float(ttl_s), mono,
                 mono + float(ttl_s), boot))
            return True

    def renew_lease(self, name: str, owner: str, ttl_s: float = 3600) -> bool:
        """Extend the deadline of a lease we already hold (Packet 8I renewable
        lease). Returns False when no such lease is held by ``owner``."""
        now = time.time()
        mono = time.monotonic()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM lease WHERE name=? AND owner=?",
                (name, owner)).fetchone()
            if row is None:
                return False
            self._conn.execute(
                "UPDATE lease SET expires_at=?, expires_mono=?, acquired_mono=?"
                " WHERE name=? AND owner=?",
                (now + float(ttl_s), mono + float(ttl_s), mono, name, owner))
            return True

    def release_lease(self, name: str, owner: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM lease WHERE name=? AND owner=?",
                (name, owner))
            return cur.rowcount > 0

    def lease_ok(self, name: str, now: Optional[float] = None) -> bool:
        """True when ``name`` is currently held by a live process of THIS boot."""
        mono = time.monotonic()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM lease WHERE name=?", (name,)).fetchone()
            if row is None:
                return False
            if now is None:
                now = time.time()
            return _lease_held(row, now, mono)

    # ------------------------------------------------------ notification
    def acknowledge_notification(self, notification_id: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE notification SET status='acked', acked_at=? WHERE"
                " notification_id=? AND status!='acked'",
                (time.time(), notification_id))
            return cur.rowcount > 0

    def record_notification(self, record: NotificationRecord) -> None:
        """Persist one notification record (dedupe handled by the caller)."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO notification(notification_id, report_id, kind,"
                " status, dedupe_key, created_at, new_count, quiet_hours,"
                " sent_at, acked_at, dedupe_of)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (record.notification_id, record.report_id, record.kind.value,
                 record.status.value, record.dedupe_key, record.created_at,
                 record.new_count, int(record.quiet_hours), record.sent_at,
                 record.acked_at, record.dedupe_of))

    def notification_exists(self, dedupe_key: str, kind: NotificationKind,
                            *, since: Optional[float] = None) -> bool:
        """True when an equivalent notification was already recorded.

        ``since`` bounds the dedupe window (default: any past record). Used so
        a repeated scan / timer fire cannot spam the same desktop/dashboard
        alert for the same report or the same auth failure.
        """
        with self._lock, self._conn:
            sql = ("SELECT 1 FROM notification WHERE dedupe_key=? AND kind=?")
            params: List[Any] = [dedupe_key, kind.value]
            if since is not None:
                sql += " AND created_at >= ?"
                params.append(float(since))
            return self._conn.execute(sql, params).fetchone() is not None

    def recent_notifications(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Most-recent notification metadata (redacted view for the dashboard)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT notification_id, report_id, kind, status, created_at,"
                " new_count, quiet_hours, sent_at, acked_at, dedupe_of"
                " FROM notification ORDER BY created_at DESC LIMIT ?",
                (int(limit),)).fetchall()
            return [dict(r) for r in rows]

    def pending_notifications(self) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM notification WHERE status IN"
                " ('pending','sent')").fetchone()[0]

    def notification_rows_retryable(self, max_retries: int = 3,
                                    limit: int = 20) -> List[Dict[str, Any]]:
        """PENDING notifications still inside their bounded retry budget.

        Oldest first; ``dead_letter`` and exhausted retries are excluded. This
        is the ONLY set the retry path may re-attempt — the bound is enforced
        by ``apply_retry_result`` (Packet 8I).
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM notification WHERE status='pending'"
                " AND retry_count < ? ORDER BY created_at ASC LIMIT ?",
                (int(max_retries), int(limit))).fetchall()
            return [dict(r) for r in rows]

    def apply_retry_result(self, notification_id: str, ok: bool,
                           max_retries: int = 3,
                           now: Optional[float] = None) -> str:
        """Record one bounded retry outcome.

        - success -> status ``sent`` (+ ``sent_at``);
        - failure within budget -> ``retry_count`` + 1 (still ``pending``);
        - failure at the budget -> status ``dead_letter`` (+ ``dead_letter_at``).
        Returns the resulting status, or "" when the record is unknown or
        already terminal (Packet 8I retry/dead-letter bound).
        """
        now = time.time() if now is None else float(now)
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT retry_count, status FROM notification"
                " WHERE notification_id=?",
                (notification_id,)).fetchone()
            if row is None or row["status"] != "pending":
                return ""
            rc = int(row["retry_count"]) + 1
            if ok:
                status = "sent"
                self._conn.execute(
                    "UPDATE notification SET status='sent', sent_at=?,"
                    " retry_count=? WHERE notification_id=?",
                    (now, rc, notification_id))
            elif rc >= int(max_retries):
                status = "dead_letter"
                self._conn.execute(
                    "UPDATE notification SET status='dead_letter',"
                    " dead_letter_at=?, retry_count=? WHERE notification_id=?",
                    (now, rc, notification_id))
            else:
                status = "pending"
                self._conn.execute(
                    "UPDATE notification SET retry_count=? WHERE"
                    " notification_id=?", (rc, notification_id))
            return status

    def report_platforms(self, report_id: str) -> List[str]:
        """Distinct platform keys recorded against a report (for retry copy)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT platform FROM item WHERE report_id=?",
                (report_id,)).fetchall()
            return [str(r["platform"]) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()