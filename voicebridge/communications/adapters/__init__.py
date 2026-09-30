"""Packet 8B — read-only CDP adapters for the communications monitor.

Strictly read-only with respect to accounts and user tabs: the CDP client
exposes only ``Runtime.evaluate`` (repository-owned extraction JS) and
``Page.captureScreenshot``; the site adapters have no click/type/send/react/
mark-read/navigate/download surface. Never Playwright profiles for the monitored
accounts, never focus/close/navigate user tabs, never enable scheduling.
"""
from .base import (CDP_LOOPBACK, AdapterScan, CdpClient, CdpError,
                   CdpReadOnlyViolation, CdpTargetNotFound, ReadOnlyAdapter,
                   ScanBounds, browser_ws_url, classify_health, find_target,
                   list_targets)
from .sites import (GmailAdapter, InstagramAdapter, MessengerAdapter,
                    SiteAdapter)
from .registry import (ADAPTERS, PLATFORM_NAMES, communications_store,
                       run_scan)

__all__ = [
    "ADAPTERS", "CDP_LOOPBACK", "AdapterScan", "CdpClient", "CdpError",
    "CdpReadOnlyViolation", "CdpTargetNotFound", "GmailAdapter",
    "InstagramAdapter", "MessengerAdapter", "PLATFORM_NAMES",
    "ReadOnlyAdapter", "ScanBounds", "SiteAdapter", "browser_ws_url",
    "classify_health", "communications_store", "find_target", "list_targets",
    "run_scan",
]