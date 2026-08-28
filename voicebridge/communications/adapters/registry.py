"""Packet 8B: adapter registry + scan driver over the Packet 8A store.

Runs one bounded, read-only scan for a platform and drives the atomic
baseline/new-item cursor transaction:

- first activation (no cursor) -> ``commit_baseline`` (records visible items as
  seen, no old-message alert);
- later scans -> ``commit_new`` ONLY when at least one genuinely new identity is
  present; a no-change/all-seen scan records compact health only (no report, no
  cursor advance);
- auth-required / unavailable / selector-drift / overflow scans record their
  typed scan + health and NEVER advance the cursor;
- dry-run computes the would-be new count without persisting.

The output is redacted metadata only (no senders/subjects/message bodies).
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..contracts import (AccountHealthRecord, AccountHealthStatus,
                         CommunicationItem, Platform, ReportManifest,
                         ReportStatus, ScanRecord, ScanStatus)
from ..store import CommunicationsStore
from .base import (CDP_LOOPBACK, AccountHealthStatus as _AHS, AdapterScan,
                   CdpClient, CdpTargetNotFound, ScanBounds, find_target,
                   find_target_preferring)
from .sites import GmailAdapter, InstagramAdapter, MessengerAdapter

ADAPTERS = {
    Platform.GMAIL: GmailAdapter,
    Platform.INSTAGRAM: InstagramAdapter,
    Platform.MESSENGER: MessengerAdapter,
}

PLATFORM_NAMES = {p.value: p for p in Platform}


def communications_store(cfg) -> CommunicationsStore:
    return CommunicationsStore(cfg.state_dir() / "communications.db")


def _redacted_item(it: CommunicationItem) -> Dict[str, Any]:
    """Metadata-only view: NO sender/subject/preview/message text."""
    return {
        "identity": it.identity,
        "identity_source": it.identity_source.value,
        "read_state": it.read_state.value,
        "evidence_status": it.evidence_status.value,
        "captured_at": round(it.captured_at, 3),
    }


def _would_be_new(store: CommunicationsStore, items) -> int:
    return sum(1 for it in items
               if not store.item_exists(it.platform, it.account_scope,
                                        it.identity))


def _manifest_for(scan, items, assets, report_id: str,
                  platform: Platform, account_scope: str) -> ReportManifest:
    return ReportManifest.create(
        window_start=scan.started_at, window_end=scan.finished_at,
        status=ReportStatus.SUCCESS,
        new_count=scan.new_count,
        platforms=(platform.value,), accounts=(account_scope,),
        item_identities=tuple(it.identity for it in items),
        assets=tuple(a.asset_id for a in assets),
        report_id=report_id)


def _publish_bundle(cfg, store, report_id: str) -> Optional[Dict[str, Any]]:
    """Render a committed report bundle from the store (Packet 8D).

    Uses the durable asset bytes persisted at scan-commit time so live scans
    produce real .webp evidence, never metadata-only bundles. Returns the
    published bundle stats or None when the store has no such report.
    """
    try:
        from ..reports import ReportStore
        rstore = ReportStore(cfg.state_dir() / "reports")
        return rstore.render_from_store(store, report_id)
    except Exception:
        return None


def _health(platform: Platform, account_scope: str, scan_res: AdapterScan,
            version: str) -> AccountHealthRecord:
    return AccountHealthRecord.create(
        platform=platform, account_scope=account_scope,
        status=scan_res.health_status, adapter_version=version,
        auth_ok=scan_res.auth_ok, reason=scan_res.reason)


def run_scan(cfg, platform: str, *, account_scope: str = "default",
             source: str = "fixture", fixture: Optional[Path] = None,
             live: bool = False, dry_run: bool = False,
             lease_ttl_s: float = 120.0,
             store: Optional[CommunicationsStore] = None) -> Dict[str, Any]:
    """Run one bounded scan. `platform` is 'gmail'|'instagram'|'messenger'."""
    pl = PLATFORM_NAMES.get(platform)
    if pl is None:
        return {"state": "failed", "detail": f"unknown platform {platform!r}"}
    st = store or communications_store(cfg)
    adapter_cls = ADAPTERS[pl]
    adapter = adapter_cls(account_scope=account_scope)
    started = time.time()
    lease_name = f"scan:{platform}:{account_scope}"
    lease_owner = f"monitor-{uuid.uuid4().hex[:8]}"
    if not st.acquire_lease(lease_name, lease_owner, ttl_s=lease_ttl_s):
        return {"state": "degraded", "code": "OVERLAP",
                "detail": "another scan holds the lease for this account"}
    try:
        report_id = f"report_{uuid.uuid4().hex[:16]}"
        bounds = ScanBounds()
        scan_res = _obtain(cfg, adapter, pl, account_scope, source, fixture,
                           live, report_id, bounds)
    finally:
        st.release_lease(lease_name, lease_owner)

    # typed scan + health records
    scan = ScanRecord.create(
        platform=pl, account_scope=account_scope, status=scan_res.status,
        adapter=adapter.adapter_name, adapter_version=adapter.version,
        started_at=started, finished_at=time.time(),
        new_count=0, seen_count=0, total_count=len(scan_res.items),
        overflow=scan_res.overflow, reason=scan_res.reason,
        report_id=report_id)
    health = _health(pl, account_scope, scan_res, adapter.version)

    # Decide the store action from health/status. OVERFLOW/PARTIAL/degraded/
    # auth-required/unavailable NEVER advance the cursor, and a healthy scan
    # with nothing new records compact health only.
    cur = st.peek_cursor(pl, account_scope)
    committable = (
        scan_res.health_status == AccountHealthStatus.HEALTHY
        and not scan_res.overflow
        and scan_res.status not in (
            ScanStatus.OVERFLOW, ScanStatus.PARTIAL, ScanStatus.DEGRADED,
            ScanStatus.AUTH_REQUIRED, ScanStatus.FAILED, ScanStatus.UNAVAILABLE)
    )
    if not committable:
        commit_path = "no-commit"
    elif not scan_res.items:
        commit_path = "no-change"
    elif cur is None:
        commit_path = "baseline"
    elif dry_run:
        commit_path = "preview"
    else:
        commit_path = "new-or-no-change"

    result: Dict[str, Any] = {
        "state": "OK",
        "platform": platform,
        "account_scope": account_scope,
        "source": source,
        "dry_run": dry_run,
        "status": scan_res.status.value,
        "health": scan_res.health_status.value,
        "health_reason": scan_res.reason,
        "auth_ok": scan_res.auth_ok,
        "overflow": scan_res.overflow,
        "items_total": len(scan_res.items),
        "new_count": 0,
        "commit": commit_path,
        "would_commit": None,
        "report_id": report_id if commit_path in ("baseline", "new") else None,
        "cursor_advanced": False,
        "items": [_redacted_item(it) for it in scan_res.items],
        "evidence": [{"asset": a.asset_id,
                      "status": a.redaction_status.value}
                     for a in scan_res.assets],
    }

    if dry_run:
        if commit_path in ("no-commit", "no-change"):
            result["commit"] = "preview"
            result["would_commit"] = commit_path
        elif commit_path == "baseline":
            result["commit"] = "preview"
            result["would_commit"] = "baseline"
            result["new_count"] = 0
        else:
            result["commit"] = "preview"
            result["would_commit"] = "new"
            result["new_count"] = _would_be_new(st, scan_res.items)
        return result

    if commit_path == "baseline":
        manifest = _manifest_for(scan, scan_res.items, scan_res.assets,
                                 report_id, pl, account_scope)
        st.commit_baseline(scan=scan, items=scan_res.items, manifest=manifest,
                           assets=scan_res.assets,
                           asset_bytes=scan_res.asset_bytes)
        result["commit"] = "baseline"
        result["cursor_advanced"] = True
        result["bundle"] = _publish_bundle(cfg, st, report_id)
    elif commit_path == "new-or-no-change":
        new_items = [it for it in scan_res.items
                     if not st.item_exists(it.platform, it.account_scope,
                                           it.identity)]
        if new_items:
            scan = ScanRecord(
                scan_id=scan.scan_id, platform=scan.platform,
                account_scope=scan.account_scope, started_at=scan.started_at,
                finished_at=scan.finished_at, status=ScanStatus.NEW_ITEMS,
                adapter=scan.adapter, adapter_version=scan.adapter_version,
                captured_at=scan.captured_at, new_count=len(new_items),
                seen_count=len(scan_res.items) - len(new_items),
                total_count=len(scan_res.items), overflow=scan.overflow,
                reason=scan.reason, cursor_before="", cursor_after="",
                report_id=report_id)
            manifest = _manifest_for(scan, scan_res.items, scan_res.assets,
                                     report_id, pl, account_scope)
            committed = st.commit_new(scan=scan, items=scan_res.items,
                                      manifest=manifest, assets=scan_res.assets,
                                      asset_bytes=scan_res.asset_bytes)
            result["new_count"] = committed
            result["commit"] = "new"
            result["report_id"] = report_id
            result["cursor_advanced"] = True
            result["bundle"] = _publish_bundle(cfg, st, report_id)
        else:
            scan = ScanRecord(
                scan_id=scan.scan_id, platform=scan.platform,
                account_scope=scan.account_scope, started_at=scan.started_at,
                finished_at=scan.finished_at, status=ScanStatus.NO_CHANGE,
                adapter=scan.adapter, adapter_version=scan.adapter_version,
                captured_at=scan.captured_at, new_count=0,
                seen_count=len(scan_res.items), total_count=len(scan_res.items),
                overflow=scan.overflow, reason="all items already seen",
                cursor_before="", cursor_after="", report_id="")
            st.record_scan(scan)
            result["commit"] = "no-change"
    elif commit_path == "no-change":
        st.record_scan(scan)
        result["commit"] = "no-change"
    else:
        # auth-required / unavailable / degraded / overflow: record typed
        # scan + health only; NEVER advance the cursor.
        st.record_scan(scan)
        result["commit"] = "no-commit"

    # compact health is recorded on every scan (no-change scans are silent but
    # leave a bounded health record).
    st.record_health(health)
    return result


def _obtain(cfg, adapter, pl, account_scope, source, fixture, live,
            report_id, bounds) -> AdapterScan:
    if source == "fixture" or (fixture is not None and not live):
        if fixture is None:
            return AdapterScan(
                status=ScanStatus.DEGRADED,
                health_status=AccountHealthStatus.DEGRADED,
                reason="fixture source requires --fixture <json>")
        try:
            data = json.loads(Path(fixture).read_text())
        except Exception as exc:
            return AdapterScan(
                status=ScanStatus.FAILED,
                health_status=AccountHealthStatus.FAILED,
                reason=f"cannot read fixture: {str(exc)[:120]}")
        rows = data.get("rows") or data.get("items") or []
        return adapter.run_fixture(rows, bounds, report_id=report_id)
    if live:
        if adapter.preferred_route:
            target, all_targets = find_target_preferring(
                adapter.origin, adapter.preferred_route)
            if target is None and len(all_targets) > 1:
                return AdapterScan(
                    status=ScanStatus.UNAVAILABLE,
                    health_status=AccountHealthStatus.UNAVAILABLE,
                    reason=f"{pl.value}: multiple targets but none is the "
                           f"{adapter.preferred_route} route; refusing to guess")
        else:
            target = find_target(adapter.origin)
        if target is None:
            return AdapterScan(
                status=ScanStatus.UNAVAILABLE,
                health_status=AccountHealthStatus.UNAVAILABLE,
                reason=f"no {pl.value} target in Chromium ({adapter.origin})")
        ws = target.get("webSocketDebuggerUrl")
        if not ws:
            return AdapterScan(
                status=ScanStatus.UNAVAILABLE,
                health_status=AccountHealthStatus.UNAVAILABLE,
                reason="target has no debugger websocket")
        client = CdpClient(ws, allowed_expressions=(adapter.extract_js,))
        try:
            client.connect()
            return adapter.run(client, bounds, report_id=report_id)
        finally:
            client.close()
    return AdapterScan(status=ScanStatus.FAILED,
                       health_status=AccountHealthStatus.FAILED,
                       reason="no source selected")
