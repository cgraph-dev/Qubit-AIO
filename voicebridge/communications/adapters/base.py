"""Packet 8B: read-only CDP client + adapter base for the communications monitor.

Attaches to the user's ALREADY RUNNING real Chromium on loopback CDP
(127.0.0.1) and is strictly read-only with respect to accounts and user tabs:

- Base CDP allowlist: ``Runtime.evaluate`` (repository-owned extraction
  expressions only) and ``Page.captureScreenshot`` (clipped, best-effort).
- No click / type / submit / send / react / mark-read / download, no
  ``Target.activateTarget`` / ``Page.bringToFront``, no navigation of a user
  tab, no storage/cookie mutation, no profile launch (never Playwright for the
  monitored accounts).
- Only static, versioned, repository-owned extraction JavaScript is ever
  evaluated; page/model/message text is never interpolated into it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..contracts import (AccountHealthRecord, AccountHealthStatus,
                         CommunicationItem, EvidenceAssetRecord, EvidenceStatus,
                         Platform, ReadState, ScanStatus)

CDP_LOOPBACK = "http://127.0.0.1:9222"


class CdpError(Exception):
    """Typed CDP failure (never a silent pass)."""


class CdpTargetNotFound(Exception):
    """No matching target in the browser."""


class CdpReadOnlyViolation(Exception):
    """A mutating CDP method was requested — refused by the allowlist."""


class CdpClient:
    """Minimal loopback CDP client exposing ONLY the read-only allowlist."""

    ALLOWED_METHODS = frozenset({"Runtime.evaluate", "Page.captureScreenshot"})

    def __init__(self, ws_url: str, *, timeout_s: float = 15.0,
                 allowed_expressions: Iterable[str] = ()) -> None:
        self._ws_url = ws_url
        self._timeout = float(timeout_s)
        self._ws: Any = None
        self._next_id = 0
        # Runtime.evaluate is dangerous even though the CDP method itself is
        # nominally read-oriented: arbitrary JavaScript can click, navigate,
        # or mutate a page.  The registry supplies the exact repository-owned
        # extraction expression for the selected adapter; everything else is
        # refused at this boundary.
        self._allowed_expression_hashes = frozenset(
            hashlib.sha256(expr.encode("utf-8")).hexdigest()
            for expr in allowed_expressions
            if isinstance(expr, str))

    def connect(self) -> "CdpClient":
        from websockets.sync.client import connect
        self._ws = connect(self._ws_url, open_timeout=self._timeout)
        return self

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if method not in self.ALLOWED_METHODS:
            raise CdpReadOnlyViolation(
                f"CDP method {method!r} is not in the read-only allowlist")
        payload = params or {}
        if method == "Runtime.evaluate":
            expression = payload.get("expression")
            digest = (hashlib.sha256(expression.encode("utf-8")).hexdigest()
                      if isinstance(expression, str) else "")
            if digest not in self._allowed_expression_hashes:
                raise CdpReadOnlyViolation(
                    "Runtime.evaluate expression is not an approved "
                    "repository-owned extraction")
        elif method == "Page.captureScreenshot":
            clip = payload.get("clip")
            if not isinstance(clip, dict):
                raise CdpReadOnlyViolation(
                    "Page.captureScreenshot requires an explicit clip")
            try:
                values = tuple(float(clip[key])
                               for key in ("x", "y", "width", "height"))
            except (KeyError, TypeError, ValueError):
                raise CdpReadOnlyViolation("screenshot clip is invalid")
            if (not all(math.isfinite(value) for value in values)
                    or values[0] < 0 or values[1] < 0
                    or values[2] <= 0 or values[3] <= 0):
                raise CdpReadOnlyViolation("screenshot clip is invalid")
        self._next_id += 1
        mid = self._next_id
        self._ws.send(json.dumps({"id": mid, "method": method,
                                  "params": payload}))
        while True:
            raw = self._ws.recv(timeout=self._timeout)
            msg = json.loads(raw)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise CdpError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def evaluate(self, expression: str, *, await_promise: bool = True) -> Any:
        """Evaluate a REPOSITORY-OWNED expression and return its value."""
        res = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True,
            "awaitPromise": await_promise})
        if res.get("exceptionDetails"):
            raise CdpError(f"evaluate failed: {res['exceptionDetails'].get('text', '')}")
        if res.get("result", {}).get("subtype") == "error":
            raise CdpError(f"evaluate error: {res['result'].get('description', '')}")
        return res.get("result", {}).get("value")

    def capture_screenshot(self, clip: Tuple[float, float, float, float], *,
                           fmt: str = "webp") -> bytes:
        """Clipped capture. `clip` is REQUIRED — the full-viewport capture is
        never allowed (it could grab unrelated private content). The clip is
        validated/clamped by the caller via ``clamp_clip``."""
        try:
            x, y, w, h = (float(value) for value in clip)
        except (TypeError, ValueError):
            raise CdpReadOnlyViolation("screenshot clip is invalid")
        if (not all(math.isfinite(value) for value in (x, y, w, h))
                or x < 0 or y < 0 or w <= 0 or h <= 0):
            raise CdpReadOnlyViolation("screenshot clip is invalid")
        params: Dict[str, Any] = {"format": fmt,
                                  "clip": {"x": x, "y": y, "width": w,
                                           "height": h, "scale": 1}}
        res = self.call("Page.captureScreenshot", params)
        try:
            return base64.b64decode(res.get("data", ""))
        except Exception as exc:
            raise CdpError(f"screenshot decode failed: {exc}") from exc

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None


def list_targets(base_url: str = CDP_LOOPBACK) -> List[Dict[str, Any]]:
    with urllib.request.urlopen(base_url + "/json/list", timeout=5) as r:
        return json.loads(r.read().decode())


def find_target(origin_prefix: str, base_url: str = CDP_LOOPBACK) -> Optional[Dict[str, Any]]:
    """Find an already-open target whose URL starts with `origin_prefix`.

    Never activates, focuses, closes, or navigates the target.
    """
    try:
        for t in list_targets(base_url):
            if (t.get("url") or "").startswith(origin_prefix):
                return t
    except Exception:
        return None
    return None


def find_target_preferring(origin_prefix: str, preferred: str,
                           base_url: str = CDP_LOOPBACK
                           ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Target selection that never silently picks the WRONG tab.

    Returns ``(target, all_matching)``:
    - prefer a target whose URL contains ``preferred`` (e.g. the Direct inbox
      route) when exactly that is available;
    - a single non-preferred target is returned (its adapter/route check then
      reports the precise wrong-route reason);
    - MULTIPLE non-preferred targets (none on the preferred route) return
      ``(None, all_matching)`` — the caller must refuse rather than guess.
    Read-only; never activates, focuses, closes, or navigates a target.
    """
    try:
        targets = [t for t in list_targets(base_url)
                   if (t.get("url") or "").startswith(origin_prefix)]
    except Exception:
        return None, []
    if not targets:
        return None, []
    if preferred:
        chosen = [t for t in targets if preferred in (t.get("url") or "")]
        if chosen:
            return chosen[0], targets
    if len(targets) == 1:
        return targets[0], targets
    return None, targets   # multiple targets, none on the preferred route


def browser_ws_url(base_url: str = CDP_LOOPBACK) -> Optional[str]:
    try:
        with urllib.request.urlopen(base_url + "/json/version", timeout=5) as r:
            return json.loads(r.read().decode()).get("webSocketDebuggerUrl")
    except Exception:
        return None


@dataclass
class ScanBounds:
    """Bounded-scan caps + a total deadline (never an unbounded loop)."""

    max_items: int = 60
    max_rows: int = 200
    deadline_s: float = 30.0
    _started: float = field(default_factory=time.time)

    @property
    def expired(self) -> bool:
        return time.time() > self._started + float(self.deadline_s)


@dataclass
class AdapterScan:
    """Typed result of one adapter pass (no store side effects here).

    ``assets`` carries the evidence METADATA records; ``asset_bytes`` maps
    ``asset_id`` -> raw bytes for the assets that were actually captured
    (Packet 8D byte-persistence hook). Assets without a byte entry render with
    an explicit typed missing-evidence reason.
    """

    items: List[CommunicationItem] = field(default_factory=list)
    status: ScanStatus = ScanStatus.NO_CHANGE
    health_status: AccountHealthStatus = AccountHealthStatus.HEALTHY
    auth_ok: Optional[bool] = None
    overflow: bool = False
    reason: str = ""
    assets: List[EvidenceAssetRecord] = field(default_factory=list)
    asset_bytes: Dict[str, bytes] = field(default_factory=dict)


class ReadOnlyAdapter:
    """Read-only adapter base. Mutating members are structurally ABSENT; any
    attempt to call a write-like member raises."""

    platform: Platform
    account_scope: str = "default"
    version: str = ""
    origin: str = ""
    EXTRACT_JS: str = ""

    def _deny(self, *a: Any, **k: Any) -> None:
        raise CdpReadOnlyViolation(
            f"{type(self).__name__}: read-only adapter, mutation denied")

    # Write-like surface is intentionally missing: click/type/send/mark_read/
    # navigate/download are NOT defined. This keeps the adapter read-only by
    # construction; a test asserts these names do not exist.

    def run(self, client: CdpClient, bounds: ScanBounds) -> AdapterScan:
        raise NotImplementedError

    def run_fixture(self, rows: List[Dict[str, Any]],
                    bounds: ScanBounds) -> AdapterScan:
        raise NotImplementedError


def classify_health(health: Optional[Dict[str, Any]], *, n_rows: int,
                    page_name: str) -> Tuple[AccountHealthStatus, Optional[bool], str]:
    """Turn the extraction page-health signals into a typed health state.

    login/consent markers -> AUTH_REQUIRED; a loaded page with zero rows ->
    selector drift (DEGRADED); otherwise HEALTHY. Never reports success from a
    stale/absent browser.
    """
    h = health or {}
    title = str(h.get("title", "") or "")
    url = str(h.get("url", "") or "")
    has_login = bool(h.get("hasLogin"))
    has_consent = bool(h.get("hasConsent"))
    body_len = int(h.get("bodyLen") or 0)
    if has_login or "accounts/login" in url or "login" in url.split("/")[-2:]:
        return AccountHealthStatus.AUTH_REQUIRED, False, "login/2FA screen detected"
    if has_consent:
        return AccountHealthStatus.AUTH_REQUIRED, False, "consent screen detected"
    if n_rows == 0:
        if body_len < 40 and not title:
            return AccountHealthStatus.UNAVAILABLE, None, "page did not render"
        return AccountHealthStatus.DEGRADED, None, \
            f"{page_name} selector drift: page loaded but no rows extracted"
    return AccountHealthStatus.HEALTHY, True, ""


CLIP_HARD_MAX = 8192.0
# px tolerance so rounding never turns a near-full capture into an allowed clip
CLIP_EPS = 0.5


def clamp_clip(rect: Any, viewport: Tuple[float, float]
               ) -> Tuple[Optional[Tuple[float, float, float, float]],
                          Optional[EvidenceStatus]]:
    """Validate + clamp a row rectangle to the viewport.

    Returns ``(clip, reason)``: ``clip`` is a viewport-bounded 4-tuple
    (x, y, width, height) in CSS px, or ``None``; ``reason`` is ``None`` on
    success, otherwise the typed EvidenceStatus for missing/unsafe geometry.
    This NEVER returns a full-viewport rectangle and never captures outside the
    visible page: invalid/oversized geometry and any rect that would clamp to
    cover the ENTIRE viewport are rejected as NOT_AVAILABLE.
    """
    if not isinstance(rect, dict):
        return None, EvidenceStatus.NOT_AVAILABLE
    try:
        x, y = float(rect.get("x")), float(rect.get("y"))
        w, h = float(rect.get("width")), float(rect.get("height"))
    except (TypeError, ValueError):
        return None, EvidenceStatus.NOT_AVAILABLE
    if not (math.isfinite(x) and math.isfinite(y)
            and math.isfinite(w) and math.isfinite(h)):
        return None, EvidenceStatus.NOT_AVAILABLE
    if w <= 0 or h <= 0:
        return None, EvidenceStatus.NOT_AVAILABLE
    if w > CLIP_HARD_MAX or h > CLIP_HARD_MAX:
        return None, EvidenceStatus.NOT_AVAILABLE   # reject oversized rect
    vw, vh = float(viewport[0]), float(viewport[1])
    if vw <= 0 or vh <= 0:
        return None, EvidenceStatus.NOT_AVAILABLE
    # clamp to the visible viewport (never capture outside the page)
    cx = max(0.0, min(x, vw))
    cy = max(0.0, min(y, vh))
    cw = max(0.0, min(w, vw - cx))
    ch = max(0.0, min(h, vh - cy))
    if cw < 1 or ch < 1:
        return None, EvidenceStatus.TARGET_HIDDEN   # row off-screen
    # REJECT any clip that covers the ENTIRE viewport — including exact
    # viewport geometry and oversized off-screen geometry that clamps to the
    # full viewport. A "scoped" row capture must never equal the whole screen
    # (it could include unrelated private content).
    if cx <= CLIP_EPS and cy <= CLIP_EPS \
            and (vw - cw) <= CLIP_EPS and (vh - ch) <= CLIP_EPS:
        return None, EvidenceStatus.NOT_AVAILABLE
    return (round(cx, 1), round(cy, 1), round(cw, 1), round(ch, 1)), None
