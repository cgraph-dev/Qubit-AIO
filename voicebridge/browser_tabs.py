"""Packet 8U — Qubit-native browser tab operations over loopback CDP.

Tab listing/activation/opening use the DevTools HTTP API; page read, find,
form fill, and click use per-target CDP sessions through
:mod:`voicebridge.cdp` (stdlib-only, loopback-enforced). Personal profiles
are never touched: Qubit attaches to the user's already-running Chromium
and never clones profiles, imports cookies, or touches passwords.

Read operations (tabs/read/find/doctor) execute directly. Anything that
changes the user's browser (open/navigate/activate/fill/click/close)
requires explicit confirmation (``confirmed=True`` from the CLI
``--confirm`` path, the voice journal, or the dashboard staged
confirmation) and is audited. Page text is untrusted evidence: bounded,
redacted, and marked as such — it can never widen a permission.

Qubit implements tab supervision over loopback CDP only
endpoints (``tools/browser_tool*.py``, ``browser_camofox*.py``); Qubit
implements only loopback tab operations natively. Provenance and the
deliberate divergence are recorded in ``docs/archive/2026-09-21/UPSTREAM_REUSE.md``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import cdp
from .logging_setup import log_event
from .redaction import redact_text as _redact_text

CONFIRM_CODE = "BROWSER_TABS_CONFIRM_REQUIRED"
ESTOP_CODE = "BROWSER_TABS_ESTOP"
NOT_FOUND_CODE = "BROWSER_TABS_NOT_FOUND"
BAD_INPUT_CODE = "BROWSER_TABS_BAD_INPUT"
UNAVAILABLE_CODE = "BROWSER_UNAVAILABLE"
SCREENSHOT_TOO_LARGE_CODE = "BROWSER_SCREENSHOT_TOO_LARGE"
MAX_TABS = 100
MAX_TEXT_CHARS = 8000
MAX_SNIPPETS = 3
MAX_URL_LEN = 2048
MAX_SELECTOR_LEN = 256
MAX_FILL_LEN = 2000
MAX_SCREENSHOT_BYTES = 2 * 1024 * 1024
MAX_SCREENSHOT_DIM = 10000
SCREENSHOT_TIMEOUT_S = 15.0
SCREENCAST_MAX_FRAMES = 8
SCREENCAST_TIMEOUT_S = 5.0
SCREENCAST_MAX_BYTES = 512 * 1024
SCREENCAST_FRAME_METHOD = "Page.screencastFrame"


class BrowserTabsError(Exception):
    """Typed browser-tabs failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def redact_spec(text: Any) -> str:
    try:
        return _redact_text(str(text or ""))[:300]
    except Exception:
        return "?"


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the plugins surface (fail closed)."""
    from .plugins import estop_engaged as _engaged

    return _engaged(cfg)


def refuse_estop(operation: str) -> Dict[str, Any]:
    return {"state": "denied", "code": ESTOP_CODE,
            "detail": f"emergency stop latched; {operation} refused",
            "owner": "qubit-policy",
            "rollback": "browser untouched; nothing to roll back"}


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    try:
        log_event(f"browser_tabs.{op}", actor=actor,
                  **{k: (str(v)[:160]) for k, v in detail.items()})
    except Exception:
        pass


# ------------------------------------------------------------- validation
def _check_target(target: str) -> str:
    text = str(target or "").strip()
    if not text or len(text) > 128:
        raise BrowserTabsError(BAD_INPUT_CODE, "target id is required")
    if any(ch not in "ABCDEFabcdef0123456789.-_" for ch in text):
        raise BrowserTabsError(BAD_INPUT_CODE,
                               "target id has unexpected characters")
    return text


def _check_url(url: str) -> str:
    text = str(url or "").strip()
    if not text or len(text) > MAX_URL_LEN:
        raise BrowserTabsError(BAD_INPUT_CODE, "URL is required (<=2048)")
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError as exc:
        raise BrowserTabsError(BAD_INPUT_CODE, f"bad URL ({exc})")
    if parts.scheme not in ("http", "https"):
        raise BrowserTabsError(BAD_INPUT_CODE,
                               "only http(s) URLs may be opened")
    if parts.username or parts.password:
        raise BrowserTabsError(BAD_INPUT_CODE,
                               "credentials in URLs are never accepted")
    if not parts.hostname:
        raise BrowserTabsError(BAD_INPUT_CODE, "URL has no host")
    return text


def _check_selector(selector: str) -> str:
    text = str(selector or "").strip()
    if not text or len(text) > MAX_SELECTOR_LEN:
        raise BrowserTabsError(BAD_INPUT_CODE,
                               "CSS selector is required (<=256)")
    if "\x00" in text:
        raise BrowserTabsError(BAD_INPUT_CODE, "bad selector")
    return text


def _require_confirmed(confirmed: bool, op: str) -> None:
    if not confirmed:
        raise BrowserTabsError(CONFIRM_CODE,
                               f"browser {op} changes the live browser; "
                               f"re-run with --confirm")


# ---------------------------------------------------------------- ledger
def _ledger_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / "browser_tabs.json"


def _load_ledger(cfg: Any) -> Dict[str, Any]:
    try:
        raw = _ledger_path(cfg).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_ledger(cfg: Any, data: Dict[str, Any]) -> None:
    path = _ledger_path(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _remember(cfg: Any, target_id: str) -> None:
    led = _load_ledger(cfg)
    led["current_target"] = target_id
    _save_ledger(cfg, led)


# ------------------------------------------------------------------ tabs
def _public_tab(item: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": str(item.get("id", "")),
            "title": redact_spec(item.get("title", ""))[:120],
            "url": redact_spec(item.get("url", ""))[:300],
            "type": str(item.get("type", ""))}


def _list_targets() -> List[Dict[str, Any]]:
    try:
        items = cdp.http_json("GET", "/json/list")
    except cdp.CdpError as exc:
        raise BrowserTabsError(exc.code, exc.detail)
    if not isinstance(items, list):
        raise BrowserTabsError(cdp.PROTOCOL_CODE, "CDP /json/list: not a list")
    pages = [i for i in items if isinstance(i, dict)
             and i.get("type") in ("page",)]
    return pages[:MAX_TABS]


def tabs(cfg: Any) -> Dict[str, Any]:
    """List page targets (read-only)."""
    try:
        pages = _list_targets()
    except BrowserTabsError as exc:
        if exc.code == cdp.UNAVAILABLE_CODE:
            return {"state": "UNAVAILABLE", "code": exc.code,
                    "detail": "no loopback CDP endpoint; start Chromium with "
                              "--remote-debugging-port=9222 or run "
                              "`qubit monitor launch` (approved launch only)",
                    "owner": "qubit-policy",
                    "rollback": "browser untouched; nothing to roll back"}
        raise
    led = _load_ledger(cfg)
    current = str(led.get("current_target", ""))
    ids = {p.get("id") for p in pages}
    if current not in ids:
        current = str(pages[0].get("id", "")) if pages else ""
    out = [_public_tab(p) for p in pages]
    for entry in out:
        entry["current"] = (entry["id"] == current and bool(current))
    return {"state": "OK", "tabs": out, "current_target": current,
            "authority": "loopback CDP read-only; personal profiles untouched"}


def _resolve_target(cfg: Any, target: str = "") -> Dict[str, Any]:
    """Resolve a tab by exact id, by title/URL fragment, or fall back to the
    Qubit-current tab (then the first page). Fragments make voice ("switch
    to the inbox tab") and CLI usable without pasting target ids; multiple
    fragment hits fail closed with the candidate list."""
    pages = _list_targets()
    if not pages:
        raise BrowserTabsError(NOT_FOUND_CODE, "no open page targets")
    query = str(target or "").strip()
    if query:
        if len(query) <= 128 and all(
                ch in "ABCDEFabcdef0123456789.-_" for ch in query):
            for page in pages:
                if str(page.get("id", "")) == query:
                    return page
        if len(query) > 128 or "\x00" in query:
            raise BrowserTabsError(BAD_INPUT_CODE, "bad tab reference")
        low = query.lower()
        hits = [p for p in pages
                if low in str(p.get("title", "")).lower()
                or low in str(p.get("url", "")).lower()]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise BrowserTabsError(NOT_FOUND_CODE,
                                   f"no open tab matches {query[:64]!r}")
        names = ", ".join(redact_spec(p.get("title", p.get("id", "")))[:40]
                          for p in hits[:5])
        raise BrowserTabsError(BAD_INPUT_CODE,
                               f"tab reference matches {len(hits)} tabs "
                               f"({names}); be more specific")
    led = _load_ledger(cfg)
    current = str(led.get("current_target", ""))
    for page in pages:
        if str(page.get("id", "")) == current:
            return page
    return pages[0]


def _target_session(page: Dict[str, Any]) -> cdp.WsSession:
    ws_url = str(page.get("webSocketDebuggerUrl", ""))
    if not ws_url:
        raise BrowserTabsError(cdp.PROTOCOL_CODE,
                               "CDP target has no debugger URL")
    try:
        return cdp.WsSession.connect(ws_url)
    except cdp.CdpError as exc:
        raise BrowserTabsError(exc.code, exc.detail)


_READ_EXPR = (
    "(() => { const b = document.body; const t = b ? (b.innerText || '') : '';"
    " return {title: document.title || '', url: location.href || '',"
    " text: t.slice(0, %d)}; })()" % MAX_TEXT_CHARS
)


def read(cfg: Any, target: str = "") -> Dict[str, Any]:
    """Read the current (or named) tab: title, URL, bounded page text.

    Page text is untrusted evidence and marked as such.
    """
    page = _resolve_target(cfg, target)
    session = _target_session(page)
    try:
        try:
            result = session.call("Runtime.evaluate",
                                  {"expression": _READ_EXPR,
                                   "returnByValue": True})
        except cdp.CdpError as exc:
            raise BrowserTabsError(exc.code, exc.detail)
        value = result.get("result", {}).get("value", {})
        if not isinstance(value, dict):
            raise BrowserTabsError(cdp.PROTOCOL_CODE,
                                   "CDP Runtime.evaluate: no value")
        _remember(cfg, str(page.get("id", "")))
        body = {"state": "OK", "target": str(page.get("id", "")),
                "title": redact_spec(value.get("title", ""))[:120],
                "url": redact_spec(value.get("url", ""))[:300],
                "text": redact_spec(value.get("text", ""))[:MAX_TEXT_CHARS],
                "trust": "untrusted-page-text",
                "authority": "loopback CDP read-only; "
                             "personal profiles untouched"}
        _audit("read", "qubit", {"target": body["target"]})
        return body
    finally:
        session.close()


def find(cfg: Any, query: str, target: str = "") -> Dict[str, Any]:
    """Count occurrences of ``query`` in the tab text + bounded snippets."""
    term = str(query or "").strip()
    if not term or len(term) > 200:
        raise BrowserTabsError(BAD_INPUT_CODE,
                               "search text is required (<=200 chars)")
    page = _resolve_target(cfg, target)
    session = _target_session(page)
    try:
        expr = (
            "(q => { const b = document.body; const t = b ? "
            "(b.innerText || '') : ''; const low = t.toLowerCase(); "
            "const needle = q.toLowerCase(); let i = 0, n = 0; "
            "const at = []; let p = low.indexOf(needle); "
            "while (p !== -1 && n < 50) { at.push(p); n++; "
            "p = low.indexOf(needle, p + needle.length); } "
            "const snip = at.slice(0, %d).map(s => t.slice("
            "Math.max(0, s - 40), s + 120)); "
            "return {count: n, snippets: snip}; })(%s)"
            % (MAX_SNIPPETS, json.dumps(term))
        )
        try:
            result = session.call("Runtime.evaluate",
                                  {"expression": expr, "returnByValue": True})
        except cdp.CdpError as exc:
            raise BrowserTabsError(exc.code, exc.detail)
        value = result.get("result", {}).get("value", {})
        if not isinstance(value, dict):
            raise BrowserTabsError(cdp.PROTOCOL_CODE,
                                   "CDP Runtime.evaluate: no value")
        snippets = value.get("snippets", [])
        body = {"state": "OK", "target": str(page.get("id", "")),
                "query": redact_spec(term)[:200],
                "count": int(value.get("count", 0) or 0),
                "snippets": [redact_spec(s)[:160] for s in snippets
                             if isinstance(s, str)][:MAX_SNIPPETS],
                "trust": "untrusted-page-text"}
        _audit("find", "qubit",
               {"target": body["target"], "count": str(body["count"])})
        return body
    finally:
        session.close()


# ------------------------------------------------------------- screenshots
def _check_clip(clip: Any) -> Optional[Dict[str, float]]:
    """Validate an optional viewport clip ``(x, y, width, height)``.

    ``None`` captures the visible viewport only (``captureBeyondViewport`` is
    forced off); malformed geometry fails closed rather than reaching CDP.
    """
    if clip is None:
        return None
    try:
        x, y, width, height = (float(value) for value in clip)
    except (TypeError, ValueError):
        raise BrowserTabsError(BAD_INPUT_CODE,
                               "clip must be four numbers (x, y, w, h)")
    if (not all(math.isfinite(value) for value in (x, y, width, height))
            or x < 0 or y < 0 or width <= 0 or height <= 0):
        raise BrowserTabsError(BAD_INPUT_CODE, "clip geometry is invalid")
    if width > MAX_SCREENSHOT_DIM or height > MAX_SCREENSHOT_DIM:
        raise BrowserTabsError(BAD_INPUT_CODE,
                               f"clip exceeds {MAX_SCREENSHOT_DIM}px")
    return {"x": x, "y": y, "width": width, "height": height, "scale": 1}


def _bounded_cap(max_bytes: Any) -> int:
    """Clamp a requested byte cap into ``(0, MAX_SCREENSHOT_BYTES]``."""
    try:
        cap = int(max_bytes)
    except (TypeError, ValueError):
        cap = MAX_SCREENSHOT_BYTES
    if cap <= 0:
        cap = MAX_SCREENSHOT_BYTES
    return min(cap, MAX_SCREENSHOT_BYTES)


def _screenshot_unavailable(detail: str) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": UNAVAILABLE_CODE,
            "detail": (detail or "no loopback CDP endpoint")[:300],
            "owner": "qubit-policy",
            "rollback": "browser untouched; nothing to roll back"}


def screenshot(cfg: Any, target: str = "", *, clip: Any = None,
               fmt: str = "jpeg", quality: Optional[int] = 70,
               max_bytes: int = MAX_SCREENSHOT_BYTES,
               timeout: float = SCREENSHOT_TIMEOUT_S) -> Dict[str, Any]:
    """Capture a bounded screenshot of an already-attached tab (read-only).

    Reuses the existing loopback CDP session API and never launches, attaches,
    or focuses a browser on its own: with no attached (loopback) CDP target the
    call is inert and returns the typed ``BROWSER_UNAVAILABLE`` state instead of
    raising. The decoded image is hard-capped at ``MAX_SCREENSHOT_BYTES``
    (``max_bytes`` only lowers it) and the tab URL/title are redacted; captured
    pixels are untrusted evidence. Any other CDP failure fails closed with a
    typed :class:`BrowserTabsError`.
    """
    clean_fmt = str(fmt or "jpeg").lower()
    if clean_fmt not in ("png", "jpeg"):
        raise BrowserTabsError(BAD_INPUT_CODE, "format must be png or jpeg")
    cap = _bounded_cap(max_bytes)
    params: Dict[str, Any] = {"format": clean_fmt,
                              "captureBeyondViewport": False}
    clean_clip = _check_clip(clip)
    if clean_clip is not None:
        params["clip"] = clean_clip
    if clean_fmt == "jpeg" and quality is not None:
        try:
            params["quality"] = max(0, min(100, int(quality)))
        except (TypeError, ValueError):
            params["quality"] = 70
    try:
        wait = max(0.1, min(float(timeout), SCREENSHOT_TIMEOUT_S))
    except (TypeError, ValueError):
        wait = SCREENSHOT_TIMEOUT_S
    try:
        page = _resolve_target(cfg, target)
    except BrowserTabsError as exc:
        if exc.code == cdp.UNAVAILABLE_CODE:
            return _screenshot_unavailable(exc.detail)
        raise
    tid = str(page.get("id", ""))
    try:
        session = _target_session(page)
    except BrowserTabsError as exc:
        if exc.code == cdp.UNAVAILABLE_CODE:
            return _screenshot_unavailable(exc.detail)
        raise
    try:
        try:
            result = session.call("Page.captureScreenshot", params,
                                  timeout=wait)
        except cdp.CdpError as exc:
            if exc.code == cdp.UNAVAILABLE_CODE:
                return _screenshot_unavailable(exc.detail)
            raise BrowserTabsError(exc.code, exc.detail)
        payload = result.get("data", "") if isinstance(result, dict) else ""
        if not isinstance(payload, str) or not payload:
            raise BrowserTabsError(cdp.PROTOCOL_CODE,
                                   "CDP Page.captureScreenshot: no data")
        try:
            raw = base64.b64decode(payload, validate=True)
        except Exception:
            raise BrowserTabsError(
                cdp.PROTOCOL_CODE,
                "CDP Page.captureScreenshot: invalid base64")
        if len(raw) > cap:
            _audit("screenshot_too_large", "qubit",
                   {"target": tid, "bytes": str(len(raw))})
            return {"state": "denied", "code": SCREENSHOT_TOO_LARGE_CODE,
                    "detail": f"capture {len(raw)} bytes exceeds cap {cap}",
                    "max_bytes": cap, "owner": "qubit-policy",
                    "rollback": "browser untouched; nothing to roll back"}
        _remember(cfg, tid)
        _audit("screenshot", "qubit",
               {"target": tid, "bytes": str(len(raw))})
        return {"state": "OK", "target": tid, "format": clean_fmt,
                "bytes": len(raw), "max_bytes": cap,
                "image_base64": payload,
                "url": redact_spec(page.get("url", ""))[:300],
                "title": redact_spec(page.get("title", ""))[:120],
                "trust": "untrusted-page-image",
                "authority": "loopback CDP read-only; "
                             "personal profiles untouched"}
    finally:
        session.close()


# -------------------------------------------------------------- screencast
def _bounded_frames(value: Any) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = 1
    return max(1, min(count, SCREENCAST_MAX_FRAMES))


def _pop_screencast_frames(session: cdp.WsSession, limit: int,
                           deadline: float) -> List[Dict[str, Any]]:
    """Boundedly drain ``Page.screencastFrame`` events, ACKing each one.

    Uses a harmless ``Runtime.evaluate`` as the socket pump so a pending
    frame is read while waiting for a command reply; never blocks past
    ``deadline`` and never fabricates a frame.
    """
    frames: List[Dict[str, Any]] = []
    while len(frames) < limit and time.monotonic() < deadline:
        found: Optional[Dict[str, Any]] = None
        for index, event in enumerate(list(session.events)):
            if (isinstance(event, dict)
                    and event.get("method") == SCREENCAST_FRAME_METHOD):
                found = session.events.pop(index)
                break
        if found is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                session.call("Runtime.evaluate",
                             {"expression": "0", "returnByValue": True},
                             timeout=max(0.1, remaining))
            except cdp.CdpError:
                break
            continue
        params = found.get("params") if isinstance(found, dict) else None
        params = params if isinstance(params, dict) else {}
        sid = params.get("sessionId")
        if isinstance(sid, int) and sid > 0:
            try:
                session.call(SCREENCAST_FRAME_METHOD + "Ack",
                             {"sessionId": sid})
            except cdp.CdpError:
                pass
        frames.append(params)
    return frames


def screencast(cfg: Any, target: str = "", *, frames: int = 1,
               fmt: str = "jpeg", quality: Optional[int] = 70,
               max_bytes: int = SCREENCAST_MAX_BYTES,
               timeout: float = SCREENCAST_TIMEOUT_S,
               fallback: bool = True) -> Dict[str, Any]:
    """Bounded live-panel capture over loopback CDP with screenshot fallback.

    Starts ``Page.startScreencast`` and drains up to ``frames`` screencast
    frames (each ACKed), stopping the stream afterwards. When the endpoint
    does not support screencast (or yields no frame) and ``fallback`` is
    true, the call degrades to the existing bounded :func:`screenshot` and
    reports ``mode: screenshot-fallback`` — never a fake frame. A missing
    endpoint returns the typed ``BROWSER_UNAVAILABLE`` state; other CDP
    failures fail closed.
    """
    clean_fmt = str(fmt or "jpeg").lower()
    if clean_fmt not in ("png", "jpeg"):
        raise BrowserTabsError(BAD_INPUT_CODE, "format must be png or jpeg")
    count = _bounded_frames(frames)
    cap = _bounded_cap(max_bytes)
    try:
        wait = max(0.1, min(float(timeout), SCREENCAST_TIMEOUT_S))
    except (TypeError, ValueError):
        wait = SCREENCAST_TIMEOUT_S
    try:
        page = _resolve_target(cfg, target)
    except BrowserTabsError as exc:
        if exc.code == cdp.UNAVAILABLE_CODE:
            return _screenshot_unavailable(exc.detail)
        raise
    tid = str(page.get("id", ""))
    try:
        session = _target_session(page)
    except BrowserTabsError as exc:
        if exc.code == cdp.UNAVAILABLE_CODE:
            return _screenshot_unavailable(exc.detail)
        raise
    start_params: Dict[str, Any] = {"format": clean_fmt}
    if clean_fmt == "jpeg" and quality is not None:
        try:
            start_params["quality"] = max(0, min(100, int(quality)))
        except (TypeError, ValueError):
            start_params["quality"] = 70
    fallback_reason = ""
    raw_frames: List[Dict[str, Any]] = []
    try:
        try:
            session.call("Page.startScreencast", start_params, timeout=wait)
        except cdp.CdpError as exc:
            if exc.code == cdp.UNAVAILABLE_CODE:
                return _screenshot_unavailable(exc.detail)
            fallback_reason = f"screencast unavailable ({exc.detail[:120]})"
        else:
            deadline = time.monotonic() + wait
            raw_frames = _pop_screencast_frames(session, count, deadline)
            try:
                session.call("Page.stopScreencast", {})
            except cdp.CdpError:
                pass
            if not raw_frames:
                fallback_reason = "screencast produced no frame"
    finally:
        session.close()
    if fallback_reason and fallback:
        out = screenshot(cfg, target, fmt=clean_fmt, quality=quality,
                         max_bytes=cap, timeout=wait)
        if isinstance(out, dict):
            out = dict(out)
            out["mode"] = "screenshot-fallback"
            out["fallback_reason"] = fallback_reason[:200]
        _audit("screencast_fallback", "qubit",
               {"target": tid, "reason": fallback_reason[:120]})
        return out
    if fallback_reason:
        return {"state": "UNAVAILABLE", "code": UNAVAILABLE_CODE,
                "detail": fallback_reason[:300],
                "owner": "qubit-policy",
                "rollback": "browser untouched; nothing to roll back"}
    decoded: List[Dict[str, Any]] = []
    total = 0
    for item in raw_frames:
        payload = item.get("data") if isinstance(item, dict) else None
        if not isinstance(payload, str) or not payload:
            continue
        try:
            raw = base64.b64decode(payload, validate=True)
        except Exception:
            continue
        if total + len(raw) > cap:
            _audit("screencast_too_large", "qubit",
                   {"target": tid, "bytes": str(total + len(raw))})
            return {"state": "denied", "code": SCREENSHOT_TOO_LARGE_CODE,
                    "detail": f"capture {total + len(raw)} bytes exceeds cap "
                              f"{cap}",
                    "max_bytes": cap, "owner": "qubit-policy",
                    "rollback": "browser untouched; nothing to roll back"}
        total += len(raw)
        decoded.append({"image_base64": payload, "bytes": len(raw),
                        "format": clean_fmt})
    if not decoded:
        if fallback:
            out = screenshot(cfg, target, fmt=clean_fmt, quality=quality,
                             max_bytes=cap, timeout=wait)
            if isinstance(out, dict):
                out = dict(out)
                out["mode"] = "screenshot-fallback"
                out["fallback_reason"] = "screencast frames undecodable"
            return out
        return {"state": "UNAVAILABLE", "code": UNAVAILABLE_CODE,
                "detail": "no screencast frame captured",
                "owner": "qubit-policy",
                "rollback": "browser untouched; nothing to roll back"}
    _remember(cfg, tid)
    _audit("screencast", "qubit",
           {"target": tid, "frames": str(len(decoded)), "bytes": str(total)})
    return {"state": "OK", "mode": "screencast", "target": tid,
            "format": clean_fmt, "frames": decoded, "frame_count": len(decoded),
            "bytes": total, "max_bytes": cap,
            "url": redact_spec(page.get("url", ""))[:300],
            "title": redact_spec(page.get("title", ""))[:120],
            "trust": "untrusted-page-image",
            "authority": "loopback CDP read-only; personal profiles untouched"}


# --------------------------------------------------------------- mutations
def activate(cfg: Any, target: str, *, requested_by: str = "cli:user",
             confirmed: bool = False) -> Dict[str, Any]:
    """Bring a tab to front (focus change: confirmation required)."""
    _require_confirmed(confirmed, "activate")
    if estop_engaged(cfg):
        return refuse_estop("browser activate")
    page = _resolve_target(cfg, target)
    tid = str(page.get("id", ""))
    try:
        cdp.http_json("GET", f"/json/activate/{tid}")
    except cdp.CdpError as exc:
        raise BrowserTabsError(exc.code, exc.detail)
    _remember(cfg, tid)
    _audit("activate", requested_by, {"target": tid})
    return {"state": "OK", "target": tid, "activated": True}


def open(cfg: Any, url: str, *, requested_by: str = "cli:user",
         confirmed: bool = False) -> Dict[str, Any]:
    """Open a URL in a new tab (confirmation required)."""
    _require_confirmed(confirmed, "open")
    if estop_engaged(cfg):
        return refuse_estop("browser open")
    clean = _check_url(url)
    try:
        created = cdp.http_json("PUT", "/json/new",
                                query="url=" + urllib.parse.quote(
                                    clean, safe=""))
    except cdp.CdpError as exc:
        raise BrowserTabsError(exc.code, exc.detail)
    if not isinstance(created, dict) or not created.get("id"):
        raise BrowserTabsError(cdp.PROTOCOL_CODE,
                               "CDP /json/new: no target id")
    tid = str(created["id"])
    _remember(cfg, tid)
    _audit("open", requested_by,
           {"target": tid, "url": redact_spec(clean)[:160]})
    return {"state": "OK", "target": tid, "url": redact_spec(clean)[:300]}


def navigate(cfg: Any, target: str, url: str, *,
             requested_by: str = "cli:user",
             confirmed: bool = False) -> Dict[str, Any]:
    """Navigate an existing tab (confirmation required)."""
    _require_confirmed(confirmed, "navigate")
    if estop_engaged(cfg):
        return refuse_estop("browser navigate")
    clean = _check_url(url)
    page = _resolve_target(cfg, target)
    session = _target_session(page)
    try:
        try:
            session.call("Page.navigate", {"url": clean})
        except cdp.CdpError as exc:
            raise BrowserTabsError(exc.code, exc.detail)
        tid = str(page.get("id", ""))
        _remember(cfg, tid)
        _audit("navigate", requested_by,
               {"target": tid, "url": redact_spec(clean)[:160]})
        return {"state": "OK", "target": tid,
                "url": redact_spec(clean)[:300]}
    finally:
        session.close()


def fill(cfg: Any, target: str, selector: str, text: str, *,
         requested_by: str = "cli:user",
         confirmed: bool = False) -> Dict[str, Any]:
    """Type ``text`` into the element matching ``selector``.

    Typed interaction only (focus + key events + insertText): no page
    script is executed by Qubit. Confirmation required.
    """
    _require_confirmed(confirmed, "fill")
    if estop_engaged(cfg):
        return refuse_estop("browser fill")
    clean_selector = _check_selector(selector)
    clean_text = str(text or "")
    if len(clean_text) > MAX_FILL_LEN:
        raise BrowserTabsError(BAD_INPUT_CODE, "fill text too long (<=2000)")
    page = _resolve_target(cfg, target)
    session = _target_session(page)
    try:
        try:
            doc = session.call("DOM.getDocument", {"depth": 1})
            root = (doc.get("root") or {}).get("nodeId", 0)
            found = session.call("DOM.querySelector",
                                 {"nodeId": root, "selector": clean_selector})
            node = int(found.get("nodeId", 0) or 0)
            if not node:
                raise BrowserTabsError(NOT_FOUND_CODE,
                                       "no element matches the selector")
            session.call("DOM.focus", {"nodeId": node})
            session.call("Input.dispatchKeyEvent",
                         {"type": "keyDown", "modifiers": 2,
                          "windowsVirtualKeyCode": 65, "key": "a",
                          "code": "KeyA"})
            session.call("Input.dispatchKeyEvent",
                         {"type": "keyUp", "modifiers": 2,
                          "windowsVirtualKeyCode": 65, "key": "a",
                          "code": "KeyA"})
            session.call("Input.insertText", {"text": clean_text})
        except cdp.CdpError as exc:
            raise BrowserTabsError(exc.code, exc.detail)
        tid = str(page.get("id", ""))
        _audit("fill", requested_by,
               {"target": tid, "selector": redact_spec(clean_selector)[:80]})
        return {"state": "OK", "target": tid, "filled": True}
    finally:
        session.close()


def click(cfg: Any, target: str, selector: str, *,
          requested_by: str = "cli:user",
          confirmed: bool = False) -> Dict[str, Any]:
    """Click the element matching ``selector`` (confirmation required)."""
    _require_confirmed(confirmed, "click")
    if estop_engaged(cfg):
        return refuse_estop("browser click")
    clean_selector = _check_selector(selector)
    page = _resolve_target(cfg, target)
    session = _target_session(page)
    try:
        try:
            doc = session.call("DOM.getDocument", {"depth": 1})
            root = (doc.get("root") or {}).get("nodeId", 0)
            found = session.call("DOM.querySelector",
                                 {"nodeId": root, "selector": clean_selector})
            node = int(found.get("nodeId", 0) or 0)
            if not node:
                raise BrowserTabsError(NOT_FOUND_CODE,
                                       "no element matches the selector")
            box = session.call("DOM.getBoxModel", {"nodeId": node})
            model = box.get("model", {}) if isinstance(box, dict) else {}
            quad = model.get("content", []) if isinstance(model, dict) else []
            if len(quad) < 8:
                raise BrowserTabsError(NOT_FOUND_CODE,
                                       "element has no clickable box")
            xs = quad[0::2]
            ys = quad[1::2]
            x, y = sum(xs) / len(xs), sum(ys) / len(ys)
            for kind in ("mousePressed", "mouseReleased"):
                session.call("Input.dispatchMouseEvent",
                             {"type": kind, "x": x, "y": y,
                              "button": "left", "clickCount": 1})
        except cdp.CdpError as exc:
            raise BrowserTabsError(exc.code, exc.detail)
        tid = str(page.get("id", ""))
        _audit("click", requested_by,
               {"target": tid, "selector": redact_spec(clean_selector)[:80]})
        return {"state": "OK", "target": tid, "clicked": True}
    finally:
        session.close()


def close(cfg: Any, target: str, *, requested_by: str = "cli:user",
          confirmed: bool = False) -> Dict[str, Any]:
    """Close a tab (confirmation required; profiles never touched)."""
    _require_confirmed(confirmed, "close")
    if estop_engaged(cfg):
        return refuse_estop("browser close")
    page = _resolve_target(cfg, target)
    tid = str(page.get("id", ""))
    try:
        cdp.http_json("GET", f"/json/close/{tid}")
    except cdp.CdpError as exc:
        raise BrowserTabsError(exc.code, exc.detail)
    led = _load_ledger(cfg)
    if led.get("current_target") == tid:
        led.pop("current_target", None)
        _save_ledger(cfg, led)
    _audit("close", requested_by, {"target": tid})
    return {"state": "OK", "target": tid, "closed": True}


def open_browser(cfg: Any, *, requested_by: str = "cli:user",
                 confirmed: bool = False) -> Dict[str, Any]:
    """"Open the browser": if the loopback endpoint is up, bring the
    Qubit-current tab forward and report READY with the tab list
    (confirmation required: this focuses the user's browser). If no
    endpoint answers, return typed UNAVAILABLE naming the exact approved
    launch path — never fake an open, never launch anything unapproved."""
    _require_confirmed(confirmed, "open-browser")
    if estop_engaged(cfg):
        return refuse_estop("browser open-browser")
    try:
        listed = tabs(cfg)
    except BrowserTabsError as exc:
        raise
    if listed.get("state") != "OK":
        _audit("open_browser_unavailable", requested_by, {})
        return {"state": "UNAVAILABLE", "code": "BROWSER_DOWN",
                "detail": "no loopback CDP endpoint; start Chromium with "
                          "--remote-debugging-port=9222, or run "
                          "`qubit monitor launch` (approved launch only)",
                "handoff": "qubit monitor launch",
                "owner": "qubit-policy",
                "rollback": "browser untouched; nothing to roll back"}
    current = str(listed.get("current_target", ""))
    if current:
        activate(cfg, current, requested_by=requested_by, confirmed=True)
    _audit("open_browser", requested_by, {"target": current})
    return {"state": "OK", "verb": "opened", "target": current,
            "tabs": listed.get("tabs", [])}


def doctor(cfg: Any) -> Dict[str, Any]:
    """Browser bridge health: endpoint, version, tabs, ledger, launch config.

    Read-only. Profiles are reported as user-managed, never inspected.
    """
    try:
        info = cdp.probe()
    except cdp.CdpError as exc:
        return {"state": "UNAVAILABLE", "code": exc.code,
                "detail": exc.detail[:300],
                "profiles": "user-managed; never cloned or inspected",
                "owner": "qubit-policy",
                "rollback": "browser untouched; nothing to roll back"}
    try:
        pages = _list_targets()
    except BrowserTabsError:
        pages = []
    led = _load_ledger(cfg)
    try:
        launch_approved = bool(cfg.get("monitor", "cdp_launch", default=False))
    except Exception:
        launch_approved = False
    return {"state": "OK",
            "browser": redact_spec(info.get("Browser", ""))[:120],
            "protocol": redact_spec(info.get("Protocol-Version", ""))[:40],
            "tabs": len(pages),
            "screencast": {"supported": True,
                           "max_frames": SCREENCAST_MAX_FRAMES,
                           "max_bytes": SCREENCAST_MAX_BYTES,
                           "screenshot_fallback": True},
            "current_target": str(led.get("current_target", "")),
            "approved_launch": bool(launch_approved),
            "profiles": "user-managed; never cloned or inspected"}


# ----------------------------------------------------- Annotate / Inspect
# OC-D27: the live-panel Annotate/Inspect affordances. Inspect is a read-only
# element probe; annotate applies a transient, bounded CSS outline (a DOM
# mutation) and therefore requires confirmation + the e-stop.
MAX_INSPECT_ATTRS = 8
MAX_INSPECT_TEXT = 500
ANNOTATION_ATTR = "data-qubit-annotation"

_INSPECT_EXPR = (
    "(sel => { const el = document.querySelector(sel);"
    " if (!el) { return {found: false}; }"
    " const r = el.getBoundingClientRect();"
    " const attrs = {}; let n = 0;"
    " for (const a of el.attributes) {"
    "  if (n >= %d) { break; } attrs[a.name] = String(a.value).slice(0, 200);"
    "  n++; }"
    " const cs = getComputedStyle(el);"
    " return {found: true, tag: el.tagName.toLowerCase(),"
    "  id: String(el.id || '').slice(0, 128),"
    "  classes: String(el.className || '').slice(0, 256),"
    "  text: String(el.textContent || '').trim().slice(0, %d),"
    "  role: String(el.getAttribute('role') || '').slice(0, 64),"
    "  visible: !!(r.width && r.height) && cs.visibility !== 'hidden',"
    "  rect: {x: r.x, y: r.y, width: r.width, height: r.height},"
    "  attrs: attrs}; })(%%s)"
    % (MAX_INSPECT_ATTRS, MAX_INSPECT_TEXT)
)

_ANNOTATE_EXPR = (
    "(args => { const el = document.querySelector(args.sel);"
    " if (!el) { return {found: false}; }"
    " el.style.outline = '3px solid ' + args.color;"
    " el.style.outlineOffset = '2px';"
    " el.setAttribute('data-qubit-annotation', args.label);"
    " const r = el.getBoundingClientRect();"
    " return {found: true, tag: el.tagName.toLowerCase(),"
    "  rect: {x: r.x, y: r.y, width: r.width, height: r.height}}; })(%s)"
)

_CLEAR_EXPR = (
    "(() => { const els = document.querySelectorAll('[' + %s + ']');"
    " for (const el of els) { el.style.outline = '';"
    "  el.style.outlineOffset = '';"
    "  el.removeAttribute('data-qubit-annotation'); }"
    " return {cleared: els.length}; })()"
    % json.dumps(ANNOTATION_ATTR)
)


def inspect_element(cfg: Any, selector: str, target: str = "") -> Dict[str, Any]:
    """Read-only element probe (tag/id/classes/text/rect/attrs) over CDP.

    The result is untrusted page evidence: bounded, redacted, and never a
    permission grant. Missing selectors return a typed ``NOT_FOUND`` instead
    of raising.
    """
    clean = _check_selector(selector)
    page = _resolve_target(cfg, target)
    session = _target_session(page)
    tid = str(page.get("id", ""))
    try:
        expr = _INSPECT_EXPR.replace("%s", json.dumps(clean))
        try:
            result = session.call("Runtime.evaluate",
                                  {"expression": expr, "returnByValue": True})
        except cdp.CdpError as exc:
            if exc.code == cdp.UNAVAILABLE_CODE:
                return _screenshot_unavailable(exc.detail)
            raise BrowserTabsError(exc.code, exc.detail)
        value = result.get("result", {}).get("value", {})
        if not isinstance(value, dict):
            raise BrowserTabsError(cdp.PROTOCOL_CODE,
                                   "CDP Runtime.evaluate: no value")
        if not value.get("found"):
            return {"state": "NOT_FOUND", "code": NOT_FOUND_CODE,
                    "detail": f"no element matches {clean[:120]!r}",
                    "target": tid, "selector": redact_spec(clean)[:256],
                    "owner": "qubit-policy",
                    "rollback": "browser untouched; nothing to roll back"}
        attrs = value.get("attrs", {})
        if not isinstance(attrs, dict):
            attrs = {}
        _remember(cfg, tid)
        _audit("inspect", "qubit", {"target": tid})
        return {"state": "OK", "target": tid,
                "selector": redact_spec(clean)[:256],
                "tag": str(value.get("tag", ""))[:64],
                "id": redact_spec(value.get("id", ""))[:128],
                "classes": redact_spec(value.get("classes", ""))[:256],
                "role": redact_spec(value.get("role", ""))[:64],
                "text": redact_spec(value.get("text", ""))[:MAX_INSPECT_TEXT],
                "visible": bool(value.get("visible", False)),
                "rect": value.get("rect", {}),
                "attrs": {redact_spec(str(k))[:64]: redact_spec(str(v))[:200]
                          for k, v in list(attrs.items())[:MAX_INSPECT_ATTRS]},
                "trust": "untrusted-page-element",
                "authority": "loopback CDP read-only; "
                             "personal profiles untouched"}
    finally:
        session.close()


def annotate(cfg: Any, selector: str, *, label: str = "", color: str = "#e11d48",
             target: str = "", requested_by: str = "cli:user",
             confirmed: bool = False) -> Dict[str, Any]:
    """Apply a transient CSS outline annotation to one element (confirmed).

    This is the only browser annotation path; it mutates the page DOM (style
    + a data attribute) and is therefore confirmation- and e-stop-gated. It
    never injects scripts, never navigates, and is reversible with
    :func:`clear_annotation`.
    """
    clean = _check_selector(selector)
    clean_label = re.sub(r"[^\w .:-]", "", str(label or ""))[:64]
    if color not in ("#e11d48", "#2563eb", "#16a34a", "#f59e0b", "#7c3aed"):
        raise BrowserTabsError(BAD_INPUT_CODE, "unsupported annotation color")
    if estop_engaged(cfg):
        return refuse_estop("browser annotate")
    _require_confirmed(confirmed, "annotate")
    page = _resolve_target(cfg, target)
    session = _target_session(page)
    tid = str(page.get("id", ""))
    try:
        expr = _ANNOTATE_EXPR.replace(
            "%s", json.dumps({"sel": clean, "label": clean_label,
                              "color": color}))
        try:
            result = session.call("Runtime.evaluate",
                                  {"expression": expr, "returnByValue": True})
        except cdp.CdpError as exc:
            if exc.code == cdp.UNAVAILABLE_CODE:
                return _screenshot_unavailable(exc.detail)
            raise BrowserTabsError(exc.code, exc.detail)
        value = result.get("result", {}).get("value", {})
        if not isinstance(value, dict) or not value.get("found"):
            return {"state": "NOT_FOUND", "code": NOT_FOUND_CODE,
                    "detail": f"no element matches {clean[:120]!r}",
                    "target": tid, "owner": "qubit-policy",
                    "rollback": "page DOM unchanged; nothing to roll back"}
        _remember(cfg, tid)
        _audit("annotate", requested_by,
               {"target": tid, "label": clean_label})
        return {"state": "OK", "target": tid, "selector": clean[:256],
                "label": clean_label, "color": color,
                "tag": str(value.get("tag", ""))[:64],
                "rect": value.get("rect", {}), "transient": True,
                "reverse": "browser clear-annotation"}
    finally:
        session.close()


def clear_annotation(cfg: Any, target: str = "", *,
                     requested_by: str = "cli:user",
                     confirmed: bool = False) -> Dict[str, Any]:
    """Remove every Qubit annotation from the tab (confirmed, reversible)."""
    if estop_engaged(cfg):
        return refuse_estop("browser clear-annotation")
    _require_confirmed(confirmed, "clear-annotation")
    page = _resolve_target(cfg, target)
    session = _target_session(page)
    tid = str(page.get("id", ""))
    try:
        try:
            result = session.call("Runtime.evaluate",
                                  {"expression": _CLEAR_EXPR,
                                   "returnByValue": True})
        except cdp.CdpError as exc:
            if exc.code == cdp.UNAVAILABLE_CODE:
                return _screenshot_unavailable(exc.detail)
            raise BrowserTabsError(exc.code, exc.detail)
        value = result.get("result", {}).get("value", {})
        count = int(value.get("cleared", 0)) if isinstance(value, dict) else 0
        _audit("clear_annotation", requested_by,
               {"target": tid, "cleared": str(count)})
        return {"state": "OK", "target": tid, "cleared": count}
    finally:
        session.close()


# --------------------------------------------- Remote / extension attach (OC-12)
# Qubit's only sanctioned remote CDP path is a loopback bridge fronted by a
# paired node host: the browser stays on the remote device and Qubit talks to
# 127.0.0.1. No non-loopback endpoint is ever contacted directly.
REMOTE_UNAVAILABLE_CODE = "BROWSER_REMOTE_UNAVAILABLE"
EXTENSION_BAD_ID_CODE = "BROWSER_EXTENSION_BAD_ID"
_EXTENSION_RE = re.compile(r"^[a-p]{32}$")


def _bridge_plan(cfg: Any, *, node_id: str, port: int,
                 extension_id: str = "") -> Dict[str, Any]:
    try:
        from . import node_host
    except Exception:
        node_host = None  # type: ignore
    if node_host is None:
        return {"state": "UNAVAILABLE", "code": REMOTE_UNAVAILABLE_CODE,
                "detail": "node-host module unavailable",
                "owner": "qubit-policy",
                "rollback": "nothing started; nothing to roll back"}
    try:
        shown = node_host.show(cfg, node_id)
    except Exception as exc:  # pragma: no cover - defensive
        return {"state": "UNAVAILABLE", "code": REMOTE_UNAVAILABLE_CODE,
                "detail": redact_spec(str(exc))[:200],
                "owner": "qubit-policy",
                "rollback": "nothing started; nothing to roll back"}
    if shown.get("state") not in ("OK",):
        return {"state": "UNAVAILABLE", "code": REMOTE_UNAVAILABLE_CODE,
                "detail": f"node {node_id!r} is not enrolled",
                "owner": "qubit-policy",
                "rollback": "nothing started; nothing to roll back"}
    node = shown.get("node", {}) if isinstance(shown, dict) else {}
    caps = set(node.get("capabilities", []))
    if "browser.cdp" not in caps:
        return {"state": "UNAVAILABLE", "code": REMOTE_UNAVAILABLE_CODE,
                "detail": f"node {node_id!r} does not declare browser.cdp",
                "owner": "qubit-policy",
                "rollback": "nothing started; nothing to roll back"}
    return {"state": "READY", "code": "",
            "bridge": {"local_host": cdp.CDP_HOST, "local_port": int(port),
                       "remote_endpoint": node.get("endpoint", ""),
                       "extension_id": extension_id},
            "authority": "loopback bridge to a paired node; no direct "
                         "non-loopback CDP is ever contacted"}


def remote_attach(cfg: Any, node_id: str, *, port: int = cdp.CDP_PORT,
                  requested_by: str = "cli:user") -> Dict[str, Any]:
    """Resolve the sanctioned remote-CDP attach plan via a paired node host.

    Returns READY with a loopback bridge descriptor when the node is enrolled
    and declares ``browser.cdp``, otherwise a typed
    ``BROWSER_REMOTE_UNAVAILABLE``. It never opens a socket or starts a
    tunnel on its own.
    """
    if estop_engaged(cfg):
        return refuse_estop("browser remote-attach")
    try:
        port_i = int(port)
    except (TypeError, ValueError):
        raise BrowserTabsError(BAD_INPUT_CODE, "port must be an integer")
    if not (1024 <= port_i <= 65535):
        raise BrowserTabsError(BAD_INPUT_CODE,
                               "port must be an unprivileged 1024..65535")
    plan = _bridge_plan(cfg, node_id=str(node_id or "").strip(),
                        port=port_i)
    _audit("remote_attach", requested_by,
           {"node": str(node_id)[:64], "state": plan.get("state", "")})
    return plan


def extension_attach(cfg: Any, extension_id: str, *,
                     port: int = cdp.CDP_PORT,
                     node_id: str = "",
                     requested_by: str = "cli:user") -> Dict[str, Any]:
    """Validate a Chrome-extension attach id and resolve its bridge plan.

    The extension id must be the canonical 32-char ``a``..``p`` form. With a
    paired node the plan is the same loopback bridge as :func:`remote_attach`;
    without one the call is an honest typed ``BROWSER_REMOTE_UNAVAILABLE``
    (Qubit never reaches a non-loopback bridge directly).
    """
    if estop_engaged(cfg):
        return refuse_estop("browser extension-attach")
    ext = str(extension_id or "").strip().lower()
    if not _EXTENSION_RE.match(ext):
        return {"state": "FAILED", "code": EXTENSION_BAD_ID_CODE,
                "detail": "extension id must be 32 chars a-p",
                "owner": "qubit-policy",
                "rollback": "nothing started; nothing to roll back"}
    if node_id:
        plan = remote_attach(cfg, node_id, port=port,
                             requested_by=requested_by)
        if isinstance(plan, dict):
            plan = dict(plan)
            plan.setdefault("bridge", {})
            if isinstance(plan["bridge"], dict):
                plan["bridge"]["extension_id"] = ext
        return plan
    return {"state": "UNAVAILABLE", "code": REMOTE_UNAVAILABLE_CODE,
            "detail": "no paired node host providing a loopback CDP bridge; "
                      "enroll a node with browser.cdp (`qubit node enroll`)",
            "extension_id": ext, "owner": "qubit-policy",
            "rollback": "nothing started; nothing to roll back"}


# ------------------------------------------- Bounded metadata ops (A38)
# Two bounded browser-metadata operations close the Qubit delta rows
# ``browser_favicon_script``, ``browser_panel_download`` and the link-favicon
# cache. Both run over the existing loopback CDP session on an already
# attached tab and are confined to that tab's own approved http(s) origin:
# the page itself performs a same-origin fetch (``credentials: same-origin``),
# so there is never a host-side network egress to an arbitrary URL and no
# personal profile is touched. ``favicon`` is read-only (like ``read``) and is
# e-stop-gated; ``download`` writes bytes and therefore additionally requires
# ``--confirm`` and resolves the destination through ``files.py`` so bytes can
# only land inside an approved project root.
METADATA_SCHEMA = "qubit-browser-metadata/1"
FAVICON_BAD_INPUT_CODE = "BROWSER_FAVICON_BAD_INPUT"
FAVICON_NOT_FOUND_CODE = "BROWSER_FAVICON_NOT_FOUND"
FAVICON_ORIGIN_CODE = "BROWSER_FAVICON_ORIGIN_DENIED"
FAVICON_ESTOP_CODE = "BROWSER_FAVICON_ESTOP"
DOWNLOAD_CONFIRM_CODE = "BROWSER_DOWNLOAD_CONFIRM_REQUIRED"
DOWNLOAD_BAD_INPUT_CODE = "BROWSER_DOWNLOAD_BAD_INPUT"
DOWNLOAD_ORIGIN_CODE = "BROWSER_DOWNLOAD_ORIGIN_DENIED"
DOWNLOAD_OUTSIDE_ROOT_CODE = "BROWSER_DOWNLOAD_OUTSIDE_ROOT"
DOWNLOAD_ESTOP_CODE = "BROWSER_DOWNLOAD_ESTOP"
DOWNLOAD_UNAVAILABLE_CODE = "BROWSER_DOWNLOAD_UNAVAILABLE"
DOWNLOAD_TOO_LARGE_CODE = "BROWSER_DOWNLOAD_TOO_LARGE"
DOWNLOAD_FAILED_CODE = "BROWSER_DOWNLOAD_FAILED"
MAX_FAVICON_BYTES = 256 * 1024
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
FAVICON_CACHE_MAX_ENTRIES = 64
FAVICON_CACHE_FILE = "favicon-cache.json"


def _origin_of(url: str, code: str) -> str:
    """Canonicalize an attached tab's origin, refusing non-http(s)/hostless."""
    text = str(url or "").strip()
    try:
        parts = urllib.parse.urlsplit(text)
        port = parts.port
    except ValueError:
        raise BrowserTabsError(code, "the attached tab has an invalid URL")
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower().strip(".")
    if scheme not in ("http", "https") or not host:
        raise BrowserTabsError(
            code, "the attached tab is not an http(s) origin")
    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443)
    suffix = "" if port is None or default_port else f":{port}"
    return f"{scheme}://{host}{suffix}"


def _check_origin_allowed(url: str, origins: Any, code: str) -> str:
    """Return the tab origin, refusing it when it is not in ``origins``.

    ``origins`` is ``None`` (the tab's own origin is the only allowed origin,
    enforced again inside the page) or an explicit iterable of approved
    origins. Any non-http(s) tab is always refused.
    """
    origin = _origin_of(url, code)
    if origins is None:
        return origin
    try:
        allowed = {_origin_of(str(entry), code) for entry in origins}
    except TypeError:
        raise BrowserTabsError(code, "approved origins must be a sequence")
    if origin not in allowed:
        raise BrowserTabsError(
            code, f"origin {origin} is not in the approved origin list")
    return origin


def _favicon_expr(max_bytes: int) -> str:
    """Same-origin favicon probe: candidate order matches the upstream script.

    Every candidate is rejected unless its URL origin equals the page origin,
    so a page cannot steer the fetch at a third-party or loopback host.
    """
    return (
        "(async (maxBytes) => {"
        " const abs = (u) => { try { return new URL(u, location.href).href; }"
        " catch (e) { return ''; } };"
        " const same = (u) => { try { return new URL(u, location.href).origin"
        " === location.origin; } catch (e) { return false; } };"
        " const links = Array.from(document.querySelectorAll("
        " 'link[rel~=\"icon\"],link[rel=\"apple-touch-icon\"],"
        "link[rel=\"shortcut icon\"]'));"
        " const preferred = links.find((l) =>"
        " /(?:^|\\s)32x32(?:\\s|$)/i.test(l.getAttribute('sizes') || ''));"
        " const ordered = [...(preferred ? [preferred] : []),"
        " ...links.filter((l) => l !== preferred), {href: '/favicon.ico'}];"
        " const seen = new Set();"
        " for (const link of ordered) {"
        "  const raw = link.getAttribute ? (link.getAttribute('href') || '')"
        " : (link.href || '');"
        "  const href = abs(raw);"
        "  if (!href || seen.has(href) || !same(href)) { continue; }"
        "  seen.add(href);"
        "  try {"
        "   const resp = await fetch(href, {credentials: 'same-origin',"
        " mode: 'cors'});"
        "   if (!resp.ok) { continue; }"
        "   const blob = await resp.blob();"
        "   const mime = (blob.type || '').split(';')[0].trim().toLowerCase();"
        "   if (!/^image\\/[a-z0-9.+-]+$/.test(mime) || blob.size === 0"
        " || blob.size > maxBytes) { continue; }"
        "   const dataUrl = await new Promise((resolve) => {"
        "    const reader = new FileReader();"
        "    reader.onload = () => resolve(typeof reader.result === 'string'"
        " ? reader.result : null);"
        "    reader.onerror = reader.onabort = () => resolve(null);"
        "    reader.readAsDataURL(blob); });"
        "   if (dataUrl) { return {found: true, origin: location.origin,"
        " icon: href, mime: mime, data_url: dataUrl}; }"
        "  } catch (e) { }"
        " }"
        " return {found: false, origin: location.origin};"
        "})(" + json.dumps(int(max_bytes)) + ")"
    )


def _download_expr(max_bytes: int) -> str:
    """Same-origin document snapshot: fetch the displayed document's bytes."""
    return (
        "(async (maxBytes) => {"
        " try {"
        "  const resp = await fetch(location.href,"
        " {credentials: 'same-origin'});"
        "  if (!resp.ok) { return {found: false,"
        " reason: 'status ' + resp.status}; }"
        "  const blob = await resp.blob();"
        "  if (blob.size === 0 || blob.size > maxBytes) {"
        " return {found: false, reason: 'size ' + blob.size}; }"
        "  const buf = await blob.arrayBuffer();"
        "  const bytes = new Uint8Array(buf); let bin = '';"
        "  for (let i = 0; i < bytes.length; i++) {"
        " bin += String.fromCharCode(bytes[i]); }"
        "  return {found: true, url: location.href, origin: location.origin,"
        " mime: (blob.type || 'application/octet-stream'),"
        " size: blob.size, data_base64: btoa(bin)};"
        " } catch (e) {"
        "  return {found: false, reason: String(e && e.message || e)};"
        " }"
        "})(" + json.dumps(int(max_bytes)) + ")"
    )


def _bounded_favicon_cap(max_bytes: Any) -> int:
    try:
        cap = int(max_bytes)
    except (TypeError, ValueError):
        cap = MAX_FAVICON_BYTES
    if cap <= 0:
        cap = MAX_FAVICON_BYTES
    return min(cap, MAX_FAVICON_BYTES)


def _bounded_download_cap(max_bytes: Any) -> int:
    try:
        cap = int(max_bytes)
    except (TypeError, ValueError):
        cap = MAX_DOWNLOAD_BYTES
    if cap <= 0:
        cap = MAX_DOWNLOAD_BYTES
    return min(cap, MAX_DOWNLOAD_BYTES)


# ------------------------------------------------------- favicon cache
def _cache_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / FAVICON_CACHE_FILE


def _load_cache(cfg: Any) -> Dict[str, Any]:
    try:
        raw = _cache_path(cfg).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(cfg: Any, data: Dict[str, Any]) -> None:
    entries = data.get("entries", {})
    if isinstance(entries, dict) and len(entries) > FAVICON_CACHE_MAX_ENTRIES:
        ordered = sorted(entries.items(),
                         key=lambda kv: float(kv[1].get("updated_at", 0) or 0),
                         reverse=True)
        data = dict(data)
        data["entries"] = dict(ordered[:FAVICON_CACHE_MAX_ENTRIES])
    try:
        path = _cache_path(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _cache_get(cfg: Any, origin: str) -> Optional[Dict[str, Any]]:
    entry = _load_cache(cfg).get("entries", {}).get(origin)
    return entry if isinstance(entry, dict) else None


def _cache_put(cfg: Any, origin: str, entry: Dict[str, Any]) -> None:
    data = _load_cache(cfg)
    entries = data.get("entries")
    if not isinstance(entries, dict):
        entries = {}
    entries[origin] = entry
    data["entries"] = entries
    data["schema"] = METADATA_SCHEMA
    _save_cache(cfg, data)


def favicon_cache(cfg: Any, *, limit: int = 32) -> Dict[str, Any]:
    """Read the bounded favicon cache (read-only; no network, no CDP)."""
    try:
        cap = max(1, min(int(limit), FAVICON_CACHE_MAX_ENTRIES))
    except (TypeError, ValueError):
        cap = 32
    entries = _load_cache(cfg).get("entries", {})
    rows = [dict(entry, origin=origin)
            for origin, entry in sorted(entries.items())
            if isinstance(entry, dict)][:cap]
    return {"state": "OK", "schema": METADATA_SCHEMA, "entries": rows,
            "count": len(rows), "max_entries": FAVICON_CACHE_MAX_ENTRIES,
            "authority": "read-only cache; favicon bytes are untrusted page "
                         "images and never a permission grant"}


def favicon(cfg: Any, target: str = "", *, max_bytes: int = MAX_FAVICON_BYTES,
            origins: Any = None, refresh: bool = False,
            requested_by: str = "cli:user") -> Dict[str, Any]:
    """Capture the attached tab's favicon as a bounded ``data:`` URL (A38).

    The page performs the fetch so the request is inherently same-origin; the
    returned icon must be an ``image/*`` blob within ``max_bytes``. The tab's
    origin must be http(s) and, when ``origins`` is given, explicitly approved.
    Read-only (no confirmation), e-stop-gated, audited, and cached by origin.
    """
    if estop_engaged(cfg):
        return {"state": "denied", "code": FAVICON_ESTOP_CODE,
                "detail": "emergency stop latched; favicon capture refused",
                "owner": "qubit-policy",
                "rollback": "browser untouched; nothing to roll back"}
    cap = _bounded_favicon_cap(max_bytes)
    try:
        page = _resolve_target(cfg, target)
    except BrowserTabsError as exc:
        if exc.code == cdp.UNAVAILABLE_CODE:
            return _screenshot_unavailable(exc.detail)
        raise
    origin = _check_origin_allowed(str(page.get("url", "")), origins,
                                   FAVICON_ORIGIN_CODE)
    if not refresh:
        cached = _cache_get(cfg, origin)
        if cached and str(cached.get("data_url", "")).startswith("data:image/"):
            body = dict(cached)
            body.update({"state": "OK", "schema": METADATA_SCHEMA,
                         "cached": True, "target": str(page.get("id", "")),
                         "origin": origin})
            return body
    tid = str(page.get("id", ""))
    try:
        session = _target_session(page)
    except BrowserTabsError as exc:
        if exc.code == cdp.UNAVAILABLE_CODE:
            return _screenshot_unavailable(exc.detail)
        raise
    try:
        try:
            result = session.call("Runtime.evaluate",
                                  {"expression": _favicon_expr(cap),
                                   "returnByValue": True, "awaitPromise": True})
        except cdp.CdpError as exc:
            if exc.code == cdp.UNAVAILABLE_CODE:
                return _screenshot_unavailable(exc.detail)
            raise BrowserTabsError(exc.code, exc.detail)
        value = result.get("result", {}).get("value", {})
        if not isinstance(value, dict):
            raise BrowserTabsError(cdp.PROTOCOL_CODE,
                                   "CDP Runtime.evaluate: no value")
        if not value.get("found"):
            return {"state": "NOT_FOUND", "code": FAVICON_NOT_FOUND_CODE,
                    "detail": "no readable same-origin favicon on this tab",
                    "target": tid, "origin": origin, "owner": "qubit-policy",
                    "rollback": "browser untouched; nothing to roll back"}
        data_url = str(value.get("data_url", ""))
        mime = str(value.get("mime", "")).split(";")[0].strip().lower()
        if (not data_url.startswith("data:image/") or "," not in data_url
                or not re.match(r"^image/[a-z0-9.+-]+$", mime)):
            return {"state": "FAILED", "code": FAVICON_BAD_INPUT_CODE,
                    "detail": "the page returned a non-image favicon",
                    "target": tid, "origin": origin, "owner": "qubit-policy",
                    "rollback": "browser untouched; nothing to roll back"}
        try:
            raw = base64.b64decode(data_url.split(",", 1)[1], validate=True)
        except Exception:
            raw = b""
        if not raw or len(raw) > cap:
            return {"state": "denied", "code": FAVICON_BAD_INPUT_CODE,
                    "detail": f"favicon exceeds the {cap} byte cap",
                    "target": tid, "origin": origin, "owner": "qubit-policy",
                    "rollback": "browser untouched; nothing to roll back"}
        _remember(cfg, tid)
        _cache_put(cfg, origin, {"origin": origin, "icon": value.get("icon", ""),
                                 "mime": mime, "bytes": len(raw),
                                 "data_url": data_url, "updated_at": time.time()})
        _audit("favicon", requested_by,
               {"target": tid, "origin": origin, "bytes": str(len(raw))})
        return {"state": "OK", "schema": METADATA_SCHEMA, "target": tid,
                "origin": origin, "icon": redact_spec(value.get("icon", ""))[:300],
                "mime": mime, "bytes": len(raw), "data_url": data_url,
                "cache_key": origin, "cached": False,
                "trust": "untrusted-page-image",
                "authority": "loopback CDP same-origin fetch; personal "
                             "profiles untouched"}
    finally:
        session.close()


# ------------------------------------------------- document download
def _download_filename(page_url: str) -> str:
    try:
        path = urllib.parse.urlsplit(str(page_url or "")).path
    except ValueError:
        path = ""
    name = os.path.basename(path.rstrip("/")) or "document.html"
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:120] or "document.html"
    if "." not in name:
        name += ".html"
    return name


def _resolve_download_dest(cfg: Any, dest: Any,
                           page_url: str = "") -> Path:
    """Resolve ``dest`` strictly inside an approved root via ``files.py``.

    The shared :func:`voicebridge.files._resolve_inside` root gate is the only
    path resolver used here; an existing directory destination derives a
    bounded filename from the tab URL. Symlinks and non-regular files are
    refused so a download can never escape the approved root.
    """
    text = str(dest or "").strip()
    if not text or len(text) > 4096 or "\x00" in text:
        raise BrowserTabsError(DOWNLOAD_BAD_INPUT_CODE,
                               "a destination path is required")
    try:
        from . import files as FL
    except Exception as exc:  # pragma: no cover - defensive
        raise BrowserTabsError(
            DOWNLOAD_BAD_INPUT_CODE,
            f"approved-root gate unavailable ({redact_spec(exc)[:120]})")
    try:
        resolved = FL._resolve_inside(cfg, text)
    except FL.FilesError as exc:
        code = (DOWNLOAD_OUTSIDE_ROOT_CODE if exc.code == FL.OUTSIDE_ROOT_CODE
                else DOWNLOAD_BAD_INPUT_CODE)
        raise BrowserTabsError(code, exc.detail)
    except Exception as exc:  # pragma: no cover - defensive
        raise BrowserTabsError(DOWNLOAD_BAD_INPUT_CODE,
                               redact_spec(str(exc))[:200])
    if resolved.exists() and resolved.is_dir():
        resolved = resolved / _download_filename(page_url)
    if resolved.is_symlink():
        raise BrowserTabsError(DOWNLOAD_OUTSIDE_ROOT_CODE,
                               "destination is a symlink; refusing to follow")
    if resolved.exists() and not resolved.is_file():
        raise BrowserTabsError(DOWNLOAD_BAD_INPUT_CODE,
                               "destination is not a regular file")
    return resolved


def _write_bytes_atomic(path: Path, raw: bytes) -> None:
    tmp = path.with_name(path.name + ".qubit-download")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        os.replace(str(tmp), str(path))
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def download(cfg: Any, dest: str, target: str = "", *, origins: Any = None,
             max_bytes: int = MAX_DOWNLOAD_BYTES, confirmed: bool = False,
             requested_by: str = "cli:user") -> Dict[str, Any]:
    """Save the displayed document into an approved root (A38).

    Confirmation-gated and e-stop-gated. The bytes come from a same-origin
    fetch performed by the attached page; the destination is resolved by the
    shared ``files.py`` root gate so nothing can be written outside an
    approved project folder. Returns a hash-linked receipt.
    """
    if estop_engaged(cfg):
        return {"state": "denied", "code": DOWNLOAD_ESTOP_CODE,
                "detail": "emergency stop latched; document download refused",
                "owner": "qubit-policy",
                "rollback": "browser untouched; nothing written"}
    cap = _bounded_download_cap(max_bytes)
    _require_confirmed(confirmed, "download")
    try:
        page = _resolve_target(cfg, target)
    except BrowserTabsError as exc:
        if exc.code == cdp.UNAVAILABLE_CODE:
            return _screenshot_unavailable(exc.detail)
        raise
    tid = str(page.get("id", ""))
    page_url = str(page.get("url", ""))
    origin = _check_origin_allowed(page_url, origins, DOWNLOAD_ORIGIN_CODE)
    dest_path = _resolve_download_dest(cfg, dest, page_url)
    try:
        session = _target_session(page)
    except BrowserTabsError as exc:
        if exc.code == cdp.UNAVAILABLE_CODE:
            return _screenshot_unavailable(exc.detail)
        raise
    try:
        try:
            result = session.call("Runtime.evaluate",
                                  {"expression": _download_expr(cap),
                                   "returnByValue": True, "awaitPromise": True})
        except cdp.CdpError as exc:
            if exc.code == cdp.UNAVAILABLE_CODE:
                return _screenshot_unavailable(exc.detail)
            raise BrowserTabsError(exc.code, exc.detail)
        value = result.get("result", {}).get("value", {})
        if not isinstance(value, dict):
            raise BrowserTabsError(cdp.PROTOCOL_CODE,
                                   "CDP Runtime.evaluate: no value")
        if not value.get("found"):
            return {"state": "UNAVAILABLE", "code": DOWNLOAD_UNAVAILABLE_CODE,
                    "detail": ("the displayed document could not be fetched "
                               "same-origin"),
                    "target": tid, "origin": origin, "owner": "qubit-policy",
                    "rollback": "nothing written; nothing to roll back"}
        payload = value.get("data_base64", "")
        try:
            size = int(value.get("size", 0) or 0)
        except (TypeError, ValueError):
            size = 0
        if not isinstance(payload, str) or not payload:
            return {"state": "UNAVAILABLE", "code": DOWNLOAD_UNAVAILABLE_CODE,
                    "detail": "the displayed document had no readable bytes",
                    "target": tid, "origin": origin, "owner": "qubit-policy",
                    "rollback": "nothing written; nothing to roll back"}
        try:
            raw = base64.b64decode(payload, validate=True)
        except Exception:
            return {"state": "FAILED", "code": DOWNLOAD_FAILED_CODE,
                    "detail": "the document payload was not valid base64",
                    "target": tid, "origin": origin, "owner": "qubit-policy",
                    "rollback": "nothing written; nothing to roll back"}
        if not raw:
            return {"state": "UNAVAILABLE", "code": DOWNLOAD_UNAVAILABLE_CODE,
                    "detail": "the displayed document was empty",
                    "target": tid, "origin": origin, "owner": "qubit-policy",
                    "rollback": "nothing written; nothing to roll back"}
        if len(raw) > cap or size > cap:
            return {"state": "denied", "code": DOWNLOAD_TOO_LARGE_CODE,
                    "detail": f"document {max(len(raw), size)} bytes exceeds "
                              f"cap {cap}", "max_bytes": cap,
                    "target": tid, "origin": origin, "owner": "qubit-policy",
                    "rollback": "nothing written; nothing to roll back"}
        try:
            _write_bytes_atomic(dest_path, raw)
        except OSError as exc:
            return {"state": "FAILED", "code": DOWNLOAD_FAILED_CODE,
                    "detail": f"could not write the download ({exc})",
                    "target": tid, "origin": origin, "owner": "qubit-policy",
                    "rollback": "nothing written; nothing to roll back"}
        digest = hashlib.sha256(raw).hexdigest()
        _remember(cfg, tid)
        _audit("download", requested_by,
               {"target": tid, "origin": origin, "bytes": str(len(raw)),
                "dest": redact_spec(dest_path.name)[:120]})
        return {"state": "OK", "schema": METADATA_SCHEMA, "target": tid,
                "origin": origin, "dest": redact_spec(str(dest_path))[:400],
                "bytes": len(raw), "max_bytes": cap,
                "mime": redact_spec(value.get("mime", ""))[:80],
                "url": redact_spec(page_url)[:300],
                "receipt": {"kind": "browser.document.download",
                            "target": tid, "origin": origin,
                            "bytes": len(raw), "sha256": digest},
                "trust": "untrusted-page-bytes",
                "authority": "loopback CDP same-origin fetch; approved root "
                             "write via files.py; personal profiles untouched",
                "rollback": "delete the downloaded file to reverse"}
    finally:
        session.close()
