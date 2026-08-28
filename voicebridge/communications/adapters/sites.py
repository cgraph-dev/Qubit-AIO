"""Packet 8B: typed, read-only Gmail / Instagram / Messenger adapters.

Each adapter attaches to the user's already-logged-in real Chromium on loopback
CDP, runs ONLY the repository-owned versioned extraction JavaScript, normalizes
rows into the Packet 8A ``CommunicationItem`` contract, and returns a typed
``AdapterScan`` (health/status/overflow/reason + best-effort clipped evidence).
It has no click/type/send/react/mark-read/navigate/download surface and never
focuses or closes a user tab.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Tuple

from .base import (AccountHealthStatus, AdapterScan, CdpClient, EvidenceStatus,
                   EvidenceAssetRecord, Platform, ReadOnlyAdapter, ScanBounds,
                   clamp_clip, classify_health)
from ..contracts import CommunicationItem, ReadState, ScanStatus
from .extract import (EXTRACT_VERSIONS, GMAIL_EXTRACT_JS, INSTAGRAM_EXTRACT_JS,
                      MESSENGER_EXTRACT_JS)


def _sender_from_text(text: str, thread_id: str) -> str:
    t = (text or "").strip()
    if not t:
        return f"thread-{thread_id}" if thread_id else "unknown"
    head = t.split(":")[0].split(",")[0].strip()
    head = " ".join(head.split()[:4])
    return head[:60] or f"thread-{thread_id}"


class SiteAdapter(ReadOnlyAdapter):
    adapter_name: str = ""
    extract_js: str = ""
    preferred_route: str = ""   # e.g. "/direct/inbox/" — required live route

    def __init__(self, account_scope: str = "default") -> None:
        self.account_scope = account_scope or "default"
        self.version = EXTRACT_VERSIONS.get(self.platform.value, "8b-1")

    def _route_health(self, health: Optional[Dict[str, Any]]
                      ) -> Optional[Tuple[AccountHealthStatus, str]]:
        """Return (health, reason) when the page is on the WRONG route for this
        adapter, else None. Subclasses override (Instagram requires the Direct
        inbox; a home page must not be silently scanned)."""
        return None

    # ------------------------------------------------------------ normalize
    def _normalize_row(self, row: Dict[str, Any], index: int) -> CommunicationItem:
        ts = str(row.get("timeText") or row.get("time_raw") or "")
        subject = str(row.get("subject") or "")
        preview = str(row.get("preview") or row.get("text") or "")
        sender = str(row.get("sender") or "").strip()
        display = str(row.get("display") or row.get("display_name") or sender).strip()
        if not sender:
            sender = _sender_from_text(preview, str(row.get("threadId") or ""))
        if not display:
            display = sender
        unread_raw = row.get("unread")
        if unread_raw is True:
            read_state = ReadState.UNREAD
        elif unread_raw is False:
            read_state = ReadState.READ
        else:
            read_state = ReadState.UNKNOWN
        return CommunicationItem.create(
            platform=self.platform, account_scope=self.account_scope,
            sender=sender, display_name=display, subject=subject,
            preview=preview, is_new=False, read_state=read_state,
            safe_url=str(row.get("href") or row.get("safe_url") or ""),
            source_item_id=row.get("sourceItemId") or row.get("source_item_id"),
            thread_id=row.get("threadId") or row.get("thread_id"),
            adapter=self.adapter_name, adapter_version=self.version)

    # ------------------------------------------------------------- screenshots
    def _asset(self, item: CommunicationItem, *, report_id: str,
               status: EvidenceStatus, data: Optional[bytes]) -> EvidenceAssetRecord:
        blob = data or b""
        return EvidenceAssetRecord.create(
            report_id=report_id, item_identity=item.identity, mime="image/webp",
            byte_count=len(blob),
            sha256=hashlib.sha256(blob).hexdigest() if blob else
            hashlib.sha256(b"missing").hexdigest(),
            redaction_status=status, source_item_id=item.source_item_id,
            width=0, height=0)

    def _screenshots(self, client: CdpClient, bounds: ScanBounds,
                     pairs, report_id: str,
                     viewport: Tuple[float, float]
                     ) -> Tuple[List[EvidenceAssetRecord], Dict[str, bytes]]:
        """Best-effort CLIPPED evidence per item.

        Returns ``(records, asset_bytes)``: every capture uses an explicit,
        viewport-bounded clip derived from the row's repository-extracted rect;
        the full viewport is NEVER captured. Missing/invalid/off-screen geometry
        or a capture failure yields a typed EvidenceStatus (NOT_AVAILABLE /
        TARGET_HIDDEN / CLIP_FAILED) with NO byte entry (the text item is
        preserved regardless). Successfully captured bytes are returned keyed by
        asset_id so the scan driver can persist them into the report bundle
        (Packet 8D byte-persistence hook).
        """
        out: List[EvidenceAssetRecord] = []
        blobs: Dict[str, bytes] = {}
        for item, rect in pairs[:3]:
            if bounds.expired:
                break
            clip, reason = clamp_clip(rect, viewport)
            if clip is None:
                out.append(self._asset(item, report_id=report_id,
                                       status=reason or EvidenceStatus.NOT_AVAILABLE,
                                       data=None))
                continue
            try:
                data = client.capture_screenshot(clip=clip)
            except Exception:
                out.append(self._asset(item, report_id=report_id,
                                       status=EvidenceStatus.CLIP_FAILED,
                                       data=None))
                continue
            if not data or len(data) < 100:
                out.append(self._asset(item, report_id=report_id,
                                       status=EvidenceStatus.TARGET_HIDDEN,
                                       data=None))
            else:
                rec = self._asset(item, report_id=report_id,
                                  status=EvidenceStatus.AVAILABLE, data=data)
                out.append(rec)
                blobs[rec.asset_id] = data
        return out, blobs

    # ------------------------------------------------------------ live/fixture
    def run(self, client: CdpClient, bounds: ScanBounds,
            report_id: str = "") -> AdapterScan:
        try:
            res = client.evaluate(self.extract_js)
        except Exception as exc:
            return AdapterScan(status=ScanStatus.DEGRADED,
                               health_status=AccountHealthStatus.DEGRADED,
                               reason=f"evaluation failed: {str(exc)[:120]}")
        if not isinstance(res, dict):
            return AdapterScan(status=ScanStatus.DEGRADED,
                               health_status=AccountHealthStatus.DEGRADED,
                               reason="extraction returned a non-mapping result")
        rows = res.get("rows") or []
        health = res.get("health") or {}
        viewport = (float(health.get("vw") or 0), float(health.get("vh") or 0))
        pairs = [(self._normalize_row(r, i), r.get("rect"))
                 for i, r in enumerate(rows[:bounds.max_rows])]
        items = [p[0] for p in pairs]
        hs, auth, reason = classify_health(
            health, n_rows=len(items), page_name=self.adapter_name)
        route = self._route_health(health)
        if route is not None:
            hs, reason = route
        overflow = bounds.expired or len(rows) > bounds.max_items
        assets: List[EvidenceAssetRecord] = []
        blobs: Dict[str, bytes] = {}
        if hs is AccountHealthStatus.HEALTHY and items:
            assets, blobs = self._screenshots(client, bounds, pairs, report_id,
                                              viewport)
        status = ScanStatus.NEW_ITEMS if items else ScanStatus.NO_CHANGE
        if overflow:
            status = ScanStatus.OVERFLOW if items else ScanStatus.DEGRADED
        return AdapterScan(items=items, status=status, health_status=hs,
                           auth_ok=auth, overflow=overflow, reason=reason,
                           assets=assets, asset_bytes=blobs)

    def run_fixture(self, rows: List[Dict[str, Any]], bounds: ScanBounds,
                    report_id: str = "") -> AdapterScan:
        items = [self._normalize_row(r, i)
                 for i, r in enumerate(rows[:bounds.max_rows])]
        hs = AccountHealthStatus.HEALTHY if items else \
            AccountHealthStatus.DEGRADED
        status = ScanStatus.NEW_ITEMS if items else ScanStatus.NO_CHANGE
        if bounds.expired or len(rows) > bounds.max_items:
            overflow = True
            status = ScanStatus.OVERFLOW if items else ScanStatus.DEGRADED
        else:
            overflow = False
        assets: List[EvidenceAssetRecord] = []
        blobs: Dict[str, bytes] = {}
        if items:
            # Fixtures MAY carry an explicit base64 `screenshot` payload so the
            # byte-persistence hook is fully exercised hermetically. Without a
            # payload the asset is recorded as NOT_AVAILABLE (no bytes).
            for it, r in zip(items, rows[:bounds.max_rows]):
                b64 = r.get("screenshot")
                if not isinstance(b64, str) or not b64:
                    assets.append(self._asset(
                        it, report_id=report_id,
                        status=EvidenceStatus.NOT_AVAILABLE, data=None))
                    continue
                try:
                    import base64
                    data = base64.b64decode(b64)
                except Exception:
                    assets.append(self._asset(
                        it, report_id=report_id,
                        status=EvidenceStatus.CLIP_FAILED, data=None))
                    continue
                if not data or len(data) < 100:
                    assets.append(self._asset(
                        it, report_id=report_id,
                        status=EvidenceStatus.TARGET_HIDDEN, data=None))
                else:
                    rec = self._asset(it, report_id=report_id,
                                      status=EvidenceStatus.AVAILABLE,
                                      data=data)
                    assets.append(rec)
                    blobs[rec.asset_id] = data
        return AdapterScan(items=items, status=status, health_status=hs,
                           auth_ok=True if items else None, overflow=overflow,
                           reason="fixture", assets=assets, asset_bytes=blobs)


class GmailAdapter(SiteAdapter):
    platform = Platform.GMAIL
    adapter_name = "gmail"
    origin = "https://mail.google.com"
    extract_js = GMAIL_EXTRACT_JS


class InstagramAdapter(SiteAdapter):
    platform = Platform.INSTAGRAM
    adapter_name = "instagram"
    origin = "https://www.instagram.com"
    preferred_route = "/direct/inbox/"
    extract_js = INSTAGRAM_EXTRACT_JS

    def _route_health(self, health):
        h = health or {}
        if h.get("isDirectInbox") or h.get("isDirect"):
            return None
        if h.get("isHome"):
            return (AccountHealthStatus.DEGRADED,
                    "wrong route: Instagram home, not the Direct inbox "
                    "(/direct/inbox/)")
        return (AccountHealthStatus.UNAVAILABLE,
                "unexpected Instagram route (not the Direct inbox)")


class MessengerAdapter(SiteAdapter):
    platform = Platform.MESSENGER
    adapter_name = "messenger"
    origin = "https://www.messenger.com"
    extract_js = MESSENGER_EXTRACT_JS