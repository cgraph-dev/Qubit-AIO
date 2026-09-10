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

Hermes implements tab supervision, cloud browsers, and remote CDP
endpoints (``tools/browser_tool*.py``, ``browser_camofox*.py``); Qubit
implements only loopback tab operations natively. Provenance and the
deliberate divergence are recorded in ``docs/UPSTREAM_REUSE.md``.
"""
from __future__ import annotations

import json
import os
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
MAX_TABS = 100
MAX_TEXT_CHARS = 8000
MAX_SNIPPETS = 3
MAX_URL_LEN = 2048
MAX_SELECTOR_LEN = 256
MAX_FILL_LEN = 2000


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
            "current_target": str(led.get("current_target", "")),
            "approved_launch": bool(launch_approved),
            "profiles": "user-managed; never cloned or inspected"}
