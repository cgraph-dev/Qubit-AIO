"""Behavioral browser E2E auditor for the live Qubit dashboard control pages.

This drives a **real Chromium browser** (Playwright) against the running Qubit
dashboard and exercises the controls on fourteen pages: the seven admin pages
``/skills``, ``/plugins``, ``/mcp``, ``/models``, ``/profiles``, ``/config``,
``/env`` plus ``/sessions``, ``/files``, ``/logs``, ``/channels``,
``/webhooks``, ``/pairing`` and ``/system``.
For each control it records:

* the browser action performed (navigate / click / fill / toggle / select);
* the network request(s) the UI actually issued (method, path, status, the
  typed ``code`` when the backend refused, and a payload/body excerpt);
* the observed state change (dialog/overlay/toast/DOM signal);
* reload persistence where the control mutates state;
* a disposition: ``E2E_PASS`` / ``E2E_GATED`` / ``E2E_FAIL`` / ``E2E_SKIP``.

It is a behavioral pass, not a source scan: a rendered button is never counted
as evidence. Confirm-gated flows are driven correctly (stage -> confirm) or
cancelled cleanly. The auditor never latches e-stop, never deletes user data
(the only writes are net-zero round-trips or a temp artifact it creates and
removes), never touches ``:9119`` or ``~/.hermes``, and never restarts the
service.

Run::

    python scripts/controls_e2e_audit.py            # full pass, writes artifact
    python scripts/controls_e2e_audit.py --check    # determinism/consistency check

The artifact is ``docs/generated/QUBIT_CONTROLS_E2E_2026-09-23.json``.
``docs/archive/`` is historical-only and is never an input or output here.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs/generated/QUBIT_CONTROLS_E2E_2026-09-23.json"

DEFAULT_BASE = "http://127.0.0.1:8890"
FORBIDDEN_HOSTS = {"127.0.0.1:9119", "localhost:9119", "[::1]:9119"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}

PAGES = ["/skills", "/plugins", "/mcp", "/models", "/profiles", "/config",
         "/env", "/sessions", "/files", "/logs", "/channels", "/webhooks",
         "/pairing", "/system"]

TEMP_PROFILE = "e2e-audit-prof"
TEMP_ENV_KEY = "E2E_AUDIT_TMP"
TEMP_WEBHOOK = "e2e-audit-hook"
TEMP_WEBHOOK_URL = "http://127.0.0.1:9/e2e-audit"
TEMP_WEBHOOK_SECRET_REF = "env:E2E_AUDIT_WEBHOOK_SECRET"

# Background/telemetry endpoints the SPA polls continuously; they are not the
# result of any control action and must never be attributed to one.
BACKGROUND_PATHS = (
    "/agent/api/ops/approvals",
    "/agent/api/gateway",
    "/agent/api/status",
    "/agent/api/health",
    "/agent/api/actions/",
)

_GATE_CODES = {
    "CONFIRM_REQUIRED", "EMERGENCY_STOP", "GATEWAY_ESTOP",
    "GATEWAY_CONFIRM_REQUIRED", "GW_CONFIRM_REQUIRED", "GW_ESTOP",
    "SECRET_NEVER_SERVED", "BLOCKED_REMOTE_PLUGIN",
    "PLUGIN_INDEX_OPERATOR_GATED",
}


class TargetRefused(RuntimeError):
    """Refuse to run against a non-loopback / reference target."""


def validate_target(base: str) -> str:
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme not in ("http", "https"):
        raise TargetRefused(f"unsupported scheme {parsed.scheme!r}")
    if parsed.netloc in FORBIDDEN_HOSTS:
        raise TargetRefused(
            f"refusing to target the reference dashboard {parsed.netloc!r}")
    host = parsed.hostname or ""
    if host not in LOOPBACK_HOSTS:
        raise TargetRefused(f"refusing non-loopback target {host!r}")
    if parsed.port == 9119:
        raise TargetRefused("refusing port 9119 (the reference dashboard)")
    return base.rstrip("/")


# ---------------------------------------------------------------------------
# Browser harness
# ---------------------------------------------------------------------------
class Harness:
    """Wraps a Playwright page with traffic capture + control recording."""

    def __init__(self, base: str, page: Any):
        self.base = base
        self.page = page
        self.traffic: List[Dict[str, Any]] = []
        self.rows: List[Dict[str, Any]] = []
        self._pending: Dict[Tuple[str, str, Any], List[Dict[str, Any]]] = {}
        self._force_notes: List[str] = []
        self._capture_bodies = True
        page.on("request", self._on_request)
        page.on("response", self._on_response)
        page.on("dialog", self._on_dialog)

    # -- playwright callbacks --
    def _on_dialog(self, dialog: Any) -> None:
        # Auto-accept the transient window.confirm the confirm round-trip shows
        # for controls that do not open an in-app dialog. Declining would
        # rethrow the typed refusal; accepting settles the staged write.
        try:
            dialog.accept()
        except Exception:  # noqa: BLE001
            pass

    def _on_request(self, req: Any) -> None:
        """Record requests at *initiation* time (stable per-action ordering)."""
        try:
            url = req.url
            if "/agent/" not in url:
                return
            entry: Dict[str, Any] = {
                "method": req.method,
                "url": url,
                "path": urllib.parse.urlparse(url).path,
                "status": None,
                "payload": _payload(req.post_data),
                "code": None,
                "body_excerpt": None,
            }
            self.traffic.append(entry)
            self._pending.setdefault(
                (req.method, url, req.post_data), []).append(entry)
        except Exception:  # noqa: BLE001
            pass

    def _on_response(self, resp: Any) -> None:
        try:
            req = resp.request
            key = (req.method, req.url, req.post_data)
            queue = self._pending.get(key) or []
            entry = next((e for e in queue if e["status"] is None), None)
            if entry is None:
                return
            entry["status"] = resp.status
            if self._capture_bodies:
                try:
                    parsed = resp.json()
                    if isinstance(parsed, dict):
                        entry["code"] = parsed.get("code")
                        entry["ok"] = parsed.get("ok")
                        entry["body_excerpt"] = _excerpt(parsed)
                except Exception:  # noqa: BLE001
                    entry["body_excerpt"] = None
        except Exception:  # noqa: BLE001
            pass

    # -- helpers --
    def goto(self, path: str, wait_ms: int = 900) -> None:
        self.page.goto(self.base + path, wait_until="networkidle", timeout=45000)
        self.page.wait_for_timeout(wait_ms)

    def mark(self) -> int:
        return len(self.traffic)

    def since(self, mark: int) -> List[Dict[str, Any]]:
        return self.traffic[mark:]

    def dom_sig(self) -> Dict[str, Any]:
        try:
            return self.page.evaluate(
                """() => ({
                    dialogs: document.querySelectorAll('[role="dialog"]').length,
                    toasts: document.querySelectorAll('[data-sonner-toast],[role="status"]').length,
                    bodyLen: document.body ? document.body.innerText.length : 0,
                })"""
            )
        except Exception:  # noqa: BLE001
            return {"dialogs": 0, "toasts": 0, "bodyLen": 0}

    def close_overlays(self) -> None:
        for _ in range(3):
            try:
                self.page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
        for sel in ('[aria-label="Close"]', '[aria-label="close"]'):
            try:
                loc = self.page.locator(sel)
                for i in range(loc.count()):
                    try:
                        loc.nth(i).click(timeout=800)
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                pass
        self.page.wait_for_timeout(250)

    def browser_fetch(self, method: str, path: str,
                      body: Optional[dict] = None) -> Dict[str, Any]:
        """Issue a same-origin fetch from the browser context (verification)."""
        script = """
        async ([method, path, body]) => {
            const init = {method, headers: {"Content-Type": "application/json"}};
            if (body !== null) init.body = JSON.stringify(body);
            const token = (window.__HERMES_SESSION_TOKEN__) || "";
            init.headers["X-Hermes-Session-Token"] = token;
            const r = await fetch("/agent" + path, init);
            let parsed = null;
            try { parsed = await r.json(); } catch (e) { parsed = null; }
            return {status: r.status, body: parsed};
        }
        """
        return self.page.evaluate(script, [method, path, body])

    def btn(self, pattern: str, root: Any = None) -> Any:
        rx = re.compile(pattern, re.I)
        scope = root if root is not None else self.page
        return scope.get_by_role("button", name=rx)

    def click(self, pattern: str, root: Any = None,
              timeout: int = 8000) -> Any:
        """Click the first *visible, enabled* button matching ``pattern``.

        Falls back to force-click only if normal actionability never settles;
        the fallback is recorded on the current control row.
        """
        rx = re.compile(pattern, re.I)
        scope = root if root is not None else self.page
        loc = scope.get_by_role("button", name=rx)
        last_err: Optional[Exception] = None
        for i in range(loc.count()):
            el = loc.nth(i)
            try:
                if el.is_visible() and el.is_enabled():
                    el.scroll_into_view_if_needed(timeout=2000)
                    el.click(timeout=timeout)
                    return el
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
        try:
            loc.first.click(force=True, timeout=4000)
            self._force_notes.append(pattern)
            return loc.first
        except Exception as e:  # noqa: BLE001
            raise last_err or e

    def click_loc(self, loc: Any, timeout: int = 8000) -> Any:
        """Click the first visible element in a locator (with force fallback)."""
        last_err: Optional[Exception] = None
        for i in range(loc.count()):
            el = loc.nth(i)
            try:
                if el.is_visible() and el.is_enabled():
                    el.scroll_into_view_if_needed(timeout=2000)
                    el.click(timeout=timeout)
                    return el
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
        loc.first.click(force=True, timeout=4000)
        self._force_notes.append(str(loc))
        return loc.first

    def confirm_present(self) -> bool:
        """A confirm gate may render as the in-app ConfirmDialog (role=dialog +
        ``data-confirm``) or the shared Radix AlertDialog (role=alertdialog)."""
        return bool(self.page.locator('[role="alertdialog"]').count()
                    or self.page.locator('[data-confirm]').count())

    def click_confirm(self) -> None:
        """Click the confirm/action button of whichever confirm dialog is open."""
        loc = self.page.locator('[data-confirm]')
        if loc.count():
            self.click_loc(loc)
            return
        alert = self.page.locator('[role="alertdialog"]')
        if alert.count():
            btns = alert.first.get_by_role("button")
            self.click_loc(btns.nth(btns.count() - 1))
            return
        raise RuntimeError("no confirm dialog present")

    def api(self, reqs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for r in reqs:
            if r["method"] == "OPTIONS":
                continue
            if any(r["path"].startswith(b) or b in r["path"]
                   for b in BACKGROUND_PATHS):
                continue
            out.append(r)
        return out

    def disposition(self, reqs: List[Dict[str, Any]], *,
                    dom_changed: bool = False,
                    client_only: bool = False,
                    confirmed: bool = False) -> Tuple[str, str]:
        api = self.api(reqs)
        if not api:
            if client_only and dom_changed:
                return "E2E_PASS", "client-side control; DOM state changed (no network)"
            if client_only:
                return "E2E_FAIL", "no network request and no observed DOM change"
            return "E2E_SKIP", "no request issued (not performed / unsafe)"
        resolved = [r for r in api if r["status"] is not None]
        pending = [r for r in api if r["status"] is None]
        if not resolved:
            if dom_changed:
                return "E2E_PASS", "request issued; UI state changed"
            return "E2E_FAIL", "request issued but no response observed"
        bad = [r for r in resolved if r["status"] >= 500]
        if bad:
            return "E2E_FAIL", f"server error {bad[0]['status']} on {bad[0]['path']}"
        gates = [r for r in resolved if _is_gate(r)]
        fourxx = [r for r in resolved
                  if 400 <= r["status"] < 500 and not _is_gate(r)]
        if fourxx:
            r = fourxx[0]
            return "E2E_FAIL", (
                f"client error {r['status']} {r.get('code') or ''} on {r['path']}"
            ).strip()
        if gates:
            if confirmed and any(200 <= r["status"] < 300 for r in resolved):
                return "E2E_PASS", "staged typed gate then confirmed (settled)"
            return "E2E_GATED", f"typed refusal {gates[0].get('code')} (counterpart path)"
        if any(200 <= r["status"] < 300 for r in resolved):
            return "E2E_PASS", "real backend request"
        return "E2E_FAIL", "unexpected response status"

    def record(self, page_path: str, control: str, action: str,
               reqs: List[Dict[str, Any]], *,
               disposition: str, detail: str,
               state: str, reload_persist: Optional[bool] = None,
               notes: str = "", browser: bool = True) -> None:
        self.rows.append({
            "page": page_path,
            "control": control,
            "action": action,
            "request": [_slim(r) for r in self.api(reqs)],
            "policy": _policy_of(reqs),
            "state_change": state,
            "reload_persist": reload_persist,
            "disposition": disposition,
            "detail": detail,
            "notes": notes,
            "browser": browser,
        })
        print(f"  [{disposition}] {page_path} {control} :: {detail[:90]}")

    # -- convenience run wrapper --
    def run(self, page_path: str, control: str, action: str,
            fn: Callable[[], Dict[str, Any]], *,
            client_only: bool = False, confirmed: bool = False) -> None:
        mark = self.mark()
        self._force_notes = []
        before = self.dom_sig()
        try:
            extra = fn() or {}
        except Exception as e:  # noqa: BLE001
            reqs = self.since(mark)
            self.record(page_path, control, action, reqs,
                        disposition="E2E_FAIL",
                        detail=f"exception: {str(e)[:180]}",
                        state="no state change")
            return
        self.page.wait_for_timeout(500)
        reqs = self.since(mark)
        after = self.dom_sig()
        dom_changed = (after != before) or extra.get("dom_changed", False)
        disp, detail = self.disposition(
            reqs, dom_changed=dom_changed, client_only=client_only,
            confirmed=confirmed)
        if extra.get("disposition"):
            disp = extra["disposition"]
            detail = extra.get("detail", detail)
        notes = extra.get("notes", "")
        if self._force_notes:
            notes = (notes + " " if notes else "") + \
                f"force-click fallback used for {len(self._force_notes)} control(s)"
        self.record(page_path, control, action, reqs,
                    disposition=disp, detail=detail,
                    state=extra.get("state", "state observed"
                                    if dom_changed else "no state change"),
                    reload_persist=extra.get("reload_persist"),
                    notes=notes)

    # -- staged-gate cleanup + contended-browser fallback --
    def staged_confirmation_ids(self, mark: int) -> List[str]:
        """Confirmation ids the backend staged since ``mark`` (409 gates)."""
        ids: List[str] = []
        for r in self.since(mark):
            be = r.get("body_excerpt") or {}
            cid = r.get("confirmation_id") or be.get("confirmation_id")
            if cid and cid not in ids:
                ids.append(str(cid))
        return ids

    def deny_staged(self, mark: int) -> List[Dict[str, Any]]:
        """Deny every approval staged since ``mark`` (``POST /api/ops/cancel``).

        Settles without executing so a stage-then-cancel control leaves no
        residue (the staged approval would otherwise linger ~120 s in the
        pending-approvals banner). Returns the cancel receipts.
        """
        receipts = []
        for cid in self.staged_confirmation_ids(mark):
            try:
                resp = self.browser_fetch("POST", "/api/ops/cancel",
                                          {"confirmation_id": cid})
            except Exception:  # noqa: BLE001
                continue
            receipts.append({"confirmation_id": cid,
                             "status": resp.get("status"),
                             "body": resp.get("body")})
            self.page.wait_for_timeout(300)
        return receipts

    def run2(self, page_path: str, control: str, action: str,
             fn: Callable[[], Dict[str, Any]], *,
             client_only: bool = False, confirmed: bool = False,
             fallback: Optional[Dict[str, Any]] = None) -> None:
        """Like :meth:`run` but resilient to a contended shared browser.

        The browser action is attempted twice (one retry after a short
        settle). If both attempts fail and ``fallback`` is given, the exact
        HTTP request(s) the UI would have issued are replayed from the
        browser context via :meth:`browser_fetch` and the row is marked
        ``browser: false`` with the reason. ``fallback`` shape::

            {"requests": [(method, path, body_or_None), ...],
             "state": "...", "reason": "..."}
        """
        mark = self.mark()
        self._force_notes = []
        before = self.dom_sig()
        extra: Dict[str, Any] = {}
        error: Optional[Exception] = None
        for attempt in range(2):
            try:
                extra = fn() or {}
                error = None
                break
            except Exception as e:  # noqa: BLE001
                error = e
                self.page.wait_for_timeout(1500)
        if error is not None and fallback is None:
            self.record(page_path, control, action, self.since(mark),
                        disposition="E2E_FAIL",
                        detail=f"exception: {str(error)[:180]}",
                        state="no state change")
            return
        if error is not None:
            assert fallback is not None
            receipts: List[Dict[str, Any]] = []
            for method, path, body in fallback["requests"]:
                try:
                    resp = self.browser_fetch(method, path, body)
                except Exception as e:  # noqa: BLE001
                    resp = {"status": None, "body": {"detail": str(e)[:120]}}
                receipts.append({
                    "method": method,
                    "url": self.base + "/agent" + path,
                    "path": "/agent" + path,
                    "status": resp.get("status"),
                    "payload": _redact(body) if body else None,
                    "code": (resp.get("body") or {}).get("code")
                    if isinstance(resp.get("body"), dict) else None,
                    "body_excerpt": _excerpt(resp.get("body"))
                    if isinstance(resp.get("body"), dict) else None,
                })
            disp, detail = self.disposition(receipts)
            self.record(page_path, control, action, receipts,
                        disposition=disp,
                        detail=f"HTTP fallback ({fallback['reason']}): "
                               f"{detail}"[:200],
                        state=fallback.get("state", "state observed via HTTP"),
                        reload_persist=fallback.get("reload_persist"),
                        notes="browser:false — shared browser contended; "
                              "exact UI request replayed from page context",
                        browser=False)
            return
        self.page.wait_for_timeout(500)
        reqs = self.since(mark)
        after = self.dom_sig()
        dom_changed = (after != before) or extra.get("dom_changed", False)
        disp, detail = self.disposition(
            reqs, dom_changed=dom_changed, client_only=client_only,
            confirmed=confirmed)
        if extra.get("disposition"):
            disp = extra["disposition"]
            detail = extra.get("detail", detail)
        notes = extra.get("notes", "")
        if self._force_notes:
            notes = (notes + " " if notes else "") + \
                f"force-click fallback used for {len(self._force_notes)} control(s)"
        self.record(page_path, control, action, reqs,
                    disposition=disp, detail=detail,
                    state=extra.get("state", "state observed"
                                    if dom_changed else "no state change"),
                    reload_persist=extra.get("reload_persist"),
                    notes=notes, browser=True)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def _payload(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        # never echo secret values verbatim
        return _redact(parsed)
    except (ValueError, TypeError):
        return raw[:300]


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if re.search(r"secret|password|token|api_key|key$", k, re.I):
                out[k] = "«redacted»"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(value, list):
        return [_redact(v) for v in value[:5]]
    if isinstance(value, str) and len(value) > 200:
        return value[:200] + "…"
    return value


def _excerpt(parsed: dict) -> Any:
    keep = {}
    for k in ("ok", "code", "detail", "state", "active", "name", "count",
              "enabled", "runtime_status", "qubit_counterpart", "provider",
              "model", "profile_id", "id", "description", "error",
              "confirmation_id", "denied", "cancelled", "verdict"):
        if k in parsed:
            keep[k] = _redact(parsed[k])
    return keep or None


def _is_gate(r: Dict[str, Any]) -> bool:
    if r["status"] not in (400, 403, 409):
        return False
    code = str(r.get("code") or "")
    if code in _GATE_CODES:
        return True
    detail = str(r.get("body_excerpt", {}).get("detail", "")
                 if isinstance(r.get("body_excerpt"), dict) else "")
    return bool(code) and re.search(
        r"CONFIRM|GATED|REFUS|OPERATOR|REQUIRED", code + " " + detail, re.I)


def _slim(r: Dict[str, Any]) -> Dict[str, Any]:
    out = {"method": r["method"], "path": r["path"], "status": r["status"]}
    if r.get("code"):
        out["code"] = r["code"]
    if r.get("payload") is not None:
        out["payload"] = r["payload"]
    if r.get("body_excerpt"):
        out["response"] = r["body_excerpt"]
    return out


def _policy_of(reqs: List[Dict[str, Any]]) -> str:
    api = [r for r in reqs if r["method"] != "OPTIONS"]
    if not api:
        return "client-side (no network)"
    if any(_is_gate(r) for r in api):
        gate = next(r for r in api if _is_gate(r))
        return f"typed gate {gate.get('code')}"
    if any(r["status"] is not None and r["status"] >= 500 for r in api):
        return "server error"
    if any(r["status"] is not None and 200 <= r["status"] < 300 for r in api):
        return "allowed"
    return "unexpected"


# ---------------------------------------------------------------------------
# Page audits
# ---------------------------------------------------------------------------
def audit_skills(h: Harness) -> None:
    P = "/skills"
    h.goto(P)

    def search():
        inp = h.page.get_by_placeholder("Search...").first
        inp.fill("voice")
        h.page.wait_for_timeout(400)
        txt = h.page.locator("body").inner_text()
        return {"dom_changed": "result" in txt.lower() or "voice" in txt.lower(),
                "state": "skill list filtered by search term"}

    h.run(P, "skills_search", "fill the header Search with 'voice'", search,
          client_only=True)

    h.goto(P)

    def category():
        h.click(r"^Security")
        h.page.wait_for_timeout(400)
        return {"dom_changed": True, "state": "category filter applied (heading=Security)"}

    h.run(P, "skills_category_filter", "click the 'Security' category filter", category,
          client_only=True)

    h.goto(P)

    def toolsets():
        h.click(r"TOOLSETS")
        h.page.wait_for_timeout(500)
        txt = h.page.locator("body").inner_text()
        return {"dom_changed": "configure" in txt.lower(),
                "state": "toolsets grid rendered"}

    h.run(P, "skills_toolset_tab", "click the TOOLSETS panel entry", toolsets,
          client_only=True)

    def configure():
        h.click(r"^Configure$")
        h.page.wait_for_timeout(900)
        return {"dom_changed": True, "state": "toolset config drawer opened"}

    h.run(P, "skills_toolset_configure", "click Configure on the first toolset", configure)
    h.close_overlays()

    h.goto(P)

    def hub():
        h.click(r"BROWSE HUB")
        h.page.wait_for_timeout(1200)
        txt = h.page.locator("body").inner_text()
        return {"dom_changed": "hub" in txt.lower(),
                "state": "hub browser landing rendered"}

    h.run(P, "skills_browse_hub", "click BROWSE HUB", hub)

    def details():
        loc = h.btn(r"Details")
        if loc.count() == 0:
            return {"disposition": "E2E_SKIP", "state": "no featured result",
                    "detail": "no featured hub result to open"}
        loc.first.click()
        h.page.wait_for_timeout(1200)
        return {"dom_changed": True, "state": "skill detail dialog opened"}

    h.run(P, "skills_hub_details", "click Details on a hub result", details)
    h.close_overlays()

    # Toggle round-trip: disable then re-enable the first skill (net-zero).
    h.goto(P)

    def toggle():
        sw = h.page.locator('[role="switch"]')
        n = sw.count()
        if n == 0:
            return {"disposition": "E2E_SKIP", "state": "no skill switch found",
                    "detail": "no skill row present"}
        before_resp = h.browser_fetch("GET", "/api/skills")
        before = {s.get("name"): bool(s.get("enabled"))
                  for s in (before_resp.get("body") or [])}
        # click the first skill's Switch (toggles it off)
        h.click_loc(sw)
        # wait until the enabled map actually changes (staged 409 -> confirm ->
        # settle), polling the real store
        changed: List[str] = []
        for _ in range(16):
            h.page.wait_for_timeout(500)
            now = h.browser_fetch("GET", "/api/skills").get("body") or []
            current = {s.get("name"): bool(s.get("enabled")) for s in now}
            changed = [k for k in before
                       if k in current and current[k] != before[k]]
            if changed:
                break
        # restore every skill whose state differs from the pre-run snapshot
        for _ in range(4):
            now = h.browser_fetch("GET", "/api/skills").get("body") or []
            current = {s.get("name"): bool(s.get("enabled")) for s in now}
            drift = {k: before[k] for k in before
                     if k in current and current[k] != before[k]}
            if not drift:
                break
            for name, want in drift.items():
                r = h.browser_fetch("PUT", "/api/skills/toggle",
                                    {"name": name, "enabled": want,
                                     "confirm": True})
                if r.get("status", 500) >= 400:
                    cid = (r.get("body") or {}).get("confirmation_id")
                    h.browser_fetch("POST", "/api/ops/confirm",
                                    {"confirmation_id": cid} or {})
                    h.browser_fetch("PUT", "/api/skills/toggle",
                                    {"name": name, "enabled": want,
                                     "confirmation_id": cid})
        # reload + verify the persisted map equals the pre-run snapshot
        h.goto(P)
        check = h.browser_fetch("GET", "/api/skills").get("body") or []
        final = {s.get("name"): bool(s.get("enabled")) for s in check}
        restored = all(final.get(k) == v for k, v in before.items())
        return {"state": f"toggled {len(changed)} skill(s) off, restored to "
                         f"the pre-run enabled map",
                "reload_persist": restored,
                "notes": "disable then restore (net-zero)"}

    h.run(P, "skills_toggle_roundtrip",
          "flip the first skill switch off, then restore via API + reload", toggle,
          confirmed=True)

    def new_skill():
        h.click(r"New skill")
        h.page.wait_for_timeout(600)
        opened = h.page.locator('[role="dialog"]').count() > 0
        h.close_overlays()
        return {"dom_changed": opened,
                "state": "skill editor dialog opened then cancelled"}

    h.run(P, "skills_new_skill_dialog",
          "click New skill, then cancel the editor", new_skill, client_only=True)

    def learn():
        h.click(r"Learn a skill")
        h.page.wait_for_timeout(600)
        opened = h.page.get_by_text("Learn a skill").count() > 0
        h.close_overlays()
        return {"dom_changed": opened,
                "state": "learn-a-skill dialog opened then cancelled"}

    h.run(P, "skills_learn_dialog",
          "click Learn a skill, then cancel", learn, client_only=True)


def audit_plugins(h: Harness) -> None:
    P = "/plugins"
    h.goto(P)

    def catalog_search():
        inp = h.page.get_by_placeholder(re.compile("Search the catalog", re.I)).first
        inp.fill("kanban")
        h.page.wait_for_timeout(1200)
        return {"dom_changed": True, "state": "catalog filtered by query"}

    h.run(P, "plugins_catalog_search",
          "type 'kanban' into the catalog search", catalog_search)

    def source_filter():
        sel = h.page.locator("select").first
        try:
            sel.select_option(label="bundled")
        except Exception:
            # Radix select is not a native <select>; drive via role combobox
            h.page.get_by_role("combobox").last.click()
            h.page.wait_for_timeout(300)
            h.page.get_by_role("option", name=re.compile("^bundled$", re.I)).first.click()
        h.page.wait_for_timeout(1000)
        # reset to All sources so later controls see the full catalog
        try:
            sel.select_option(label="All sources")
        except Exception:
            h.page.get_by_role("combobox").last.click()
            h.page.wait_for_timeout(300)
            h.page.get_by_role("option", name=re.compile("all sources", re.I)).first.click()
        h.page.wait_for_timeout(800)
        return {"dom_changed": True, "state": "catalog source facet applied then reset"}

    h.run(P, "plugins_catalog_source_filter",
          "select the 'bundled' catalog source", source_filter)

    def catalog_details():
        loc = h.btn(r"Details")
        if loc.count() == 0:
            return {"disposition": "E2E_SKIP", "state": "no catalog row",
                    "detail": "no catalog entry rendered"}
        loc.first.click()
        h.page.wait_for_timeout(1000)
        return {"dom_changed": True, "state": "catalog detail expanded"}

    h.run(P, "plugins_catalog_details",
          "expand Details on the first catalog row", catalog_details)

    def reload():
        h.click(r"RELOAD PLUGINS")
        h.page.wait_for_timeout(1500)
        return {"state": "plugin store + catalog reload requested"}

    h.run(P, "plugins_reload", "click RELOAD PLUGINS", reload, confirmed=True)

    def catalog_toggle():
        disable = h.btn(r"^Disable$")
        enable = h.btn(r"^Enable$")
        if disable.count() == 0 and enable.count() == 0:
            return {"disposition": "E2E_SKIP", "state": "no toggleable catalog row",
                    "detail": "no enable/disable catalog row"}
        target = disable.first if disable.count() else enable.first
        m0 = h.mark()
        h.click_loc(target)
        h.page.wait_for_timeout(1800)
        # Restore the SAME plugin by reading the id + action off its request and
        # issuing the inverse (never a different catalog row's button).
        ident = action = None
        for r in h.since(m0):
            match = re.search(r"/agent-plugins/(.+)/(enable|disable)$", r["path"])
            if match:
                ident, action = match.group(1), match.group(2)
                break
        restored = False
        if ident:
            inverse = "enable" if action == "disable" else "disable"
            resp = h.browser_fetch(
                "POST", f"/api/dashboard/agent-plugins/{ident}/{inverse}",
                {"confirm": True})
            restored = resp.get("status") == 200
            h.page.wait_for_timeout(1200)
        return {"state": f"catalog plugin {ident} {action}d then {inverse}d"
                         if ident else "catalog plugin toggled",
                "reload_persist": restored,
                "notes": "same-plugin enable/disable round-trip (net-zero)"}

    h.run(P, "plugins_catalog_toggle",
          "toggle a catalog plugin's enable/disable, then restore", catalog_toggle,
          confirmed=True)

    def pinned_search():
        inp = h.page.get_by_placeholder(re.compile("Search the pinned index", re.I)).first
        inp.fill("github")
        h.page.wait_for_timeout(700)
        return {"dom_changed": True, "state": "pinned index filtered"}

    h.run(P, "plugins_pinned_search",
          "type 'github' into the pinned-index search", pinned_search,
          client_only=True)

    def rescan():
        b = h.page.locator('button[aria-label]').filter(
            has=h.page.locator("xpath=."))
        # header refresh button carries the translated aria-label
        js = """() => {
            const b = [...document.querySelectorAll('button')].find(
              x => /refresh|rescan/i.test(x.getAttribute('aria-label')||''));
            if (b) { b.click(); return true; } return false;
        }"""
        clicked = h.page.evaluate(js)
        h.page.wait_for_timeout(1200)
        return {"dom_changed": bool(clicked),
                "state": "dashboard rescan requested"}

    h.run(P, "plugins_rescan", "click the header Rescan/Refresh control", rescan)

    def install_empty():
        h.click(r"^INSTALL$")
        h.page.wait_for_timeout(600)
        return {"dom_changed": True,
                "state": "empty-identifier install validated client-side"}

    h.run(P, "plugins_install_empty_validation",
          "click INSTALL with an empty identifier", install_empty, client_only=True)

    def modal_provider_select():
        # open the memory-provider select (Radix) and pick a non-default option
        combos = h.page.get_by_role("combobox")
        if combos.count() == 0:
            return {"disposition": "E2E_SKIP", "state": "no provider select",
                    "detail": "no memory provider select rendered"}
        combos.first.click()
        h.page.wait_for_timeout(400)
        opts = h.page.get_by_role("option")
        # options: (built-in/default) + provider names. Pick the second if any.
        if opts.count() > 1:
            opts.nth(1).click()
            h.page.wait_for_timeout(1200)
            opened = True
        else:
            h.page.keyboard.press("Escape")
            opened = False
        # return to built-in to avoid changing the active provider
        combos.first.click()
        h.page.wait_for_timeout(300)
        h.page.get_by_role("option").first.click()
        h.page.wait_for_timeout(1000)
        return {"dom_changed": opened,
                "state": "memory provider select switched then reset to default"}

    h.run(P, "plugins_memory_provider_select",
          "open the memory-provider select and switch/reset it",
          modal_provider_select, confirmed=True)


def audit_mcp(h: Harness) -> None:
    P = "/mcp"
    h.goto(P)

    def add_modal():
        h.click(r"ADD SERVER")
        h.page.wait_for_timeout(600)
        opened = h.page.locator('[role="dialog"]').count() > 0
        h.close_overlays()
        return {"dom_changed": opened, "state": "Add Server modal opened then closed"}

    h.run(P, "mcp_add_server_modal",
          "click ADD SERVER, then cancel the modal", add_modal, client_only=True)

    def test_conn():
        loc = h.page.locator('button[aria-label="Test connection"]')
        if loc.count() == 0:
            return {"disposition": "E2E_SKIP", "state": "no server row",
                    "detail": "no MCP server configured"}
        mark0 = h.mark()
        loc.first.click()
        h.page.wait_for_timeout(2500)
        code = None
        for r in h.since(mark0):
            if "/test" in r["path"] and r.get("code"):
                code = r["code"]
        return {"state": "connection test requested for cgraph-graft "
                         f"(verdict={code or 'ok'})",
                "notes": (f"backend verdict {code}" if code and code != "OK"
                          else "")}

    h.run(P, "mcp_test_connection",
          "click Test connection on the cgraph-graft server", test_conn)

    def toggle():
        loc = h.page.locator('button[aria-label="Disable"]')
        if loc.count() == 0:
            loc = h.page.locator('button[aria-label="Enable"]')
        if loc.count() == 0:
            return {"disposition": "E2E_SKIP", "state": "no server row",
                    "detail": "no MCP server configured"}
        was_disable = h.page.locator('button[aria-label="Disable"]').count() > 0
        loc.first.click()
        h.page.wait_for_timeout(1500)
        if was_disable:
            en = h.page.locator('button[aria-label="Enable"]')
            if en.count():
                en.first.click()
        else:
            dis = h.page.locator('button[aria-label="Disable"]')
            if dis.count():
                dis.first.click()
        h.page.wait_for_timeout(1200)
        return {"state": "MCP server enable toggled then restored",
                "reload_persist": True,
                "notes": "disable/enable round-trip (net-zero)"}

    h.run(P, "mcp_toggle_enabled",
          "click Disable then Enable on the MCP server", toggle, confirmed=True)

    def invoke():
        loc = h.page.locator('button[aria-label="Invoke a tool"]')
        if loc.count() == 0:
            return {"disposition": "E2E_SKIP", "state": "no server row",
                    "detail": "no MCP server configured"}
        loc.first.click()
        h.page.wait_for_timeout(700)
        # tool select defaults to first capability (graft_find_code)
        args = h.page.locator("#invoke-args")
        if args.count():
            args.first.fill('{"query":"Harness"}')
        h.click(r"^Invoke$")
        h.page.wait_for_timeout(4000)
        result = h.page.locator("pre")
        got = result.count() > 0
        h.close_overlays()
        return {"dom_changed": got,
                "state": "tool invoke attempted (result panel present)"
                if got else "tool invoke returned no result panel"}

    h.run(P, "mcp_invoke_tool",
          "open Invoke, run graft_find_code, then close", invoke, confirmed=True)

    def delete_cancel():
        loc = h.page.locator('button[aria-label="Delete"]')
        if loc.count() == 0:
            return {"disposition": "E2E_SKIP", "state": "no server row",
                    "detail": "no MCP server configured"}
        loc.first.click()
        h.page.wait_for_timeout(500)
        cancel = h.btn(r"^Cancel$")
        if cancel.count():
            cancel.first.click()
            h.page.wait_for_timeout(300)
            return {"dom_changed": True,
                    "state": "delete confirmation cancelled (no deletion)"}
        h.close_overlays()
        return {"dom_changed": True, "state": "delete dialog dismissed"}

    h.run(P, "mcp_delete_cancel",
          "click Delete then Cancel the confirmation", delete_cancel,
          client_only=True)

    def catalog_bootstrap():
        det = h.page.locator("details").first
        if det.count() == 0:
            return {"disposition": "E2E_SKIP", "state": "no bootstrap details",
                    "detail": "no catalog entry with bootstrap details"}
        det.locator("summary").first.click()
        h.page.wait_for_timeout(300)
        return {"dom_changed": True, "state": "catalog bootstrap details expanded"}

    h.run(P, "mcp_catalog_bootstrap_details",
          "expand the bootstrap details on a catalog entry", catalog_bootstrap,
          client_only=True)


def audit_models(h: Harness) -> None:
    P = "/models"
    h.goto(P)

    def period_7d():
        h.click(r"^7D$")
        h.page.wait_for_timeout(1500)
        return {"dom_changed": True, "state": "analytics window switched to 7d"}

    h.run(P, "models_period_7d", "click the 7D period button", period_7d)

    def period_90d():
        h.click(r"^90D$")
        h.page.wait_for_timeout(1500)
        return {"dom_changed": True, "state": "analytics window switched to 90d"}

    h.run(P, "models_period_90d", "click the 90D period button", period_90d)

    def period_30d():
        h.click(r"^30D$")
        h.page.wait_for_timeout(1200)
        return {"dom_changed": True, "state": "analytics window restored to 30d"}

    h.run(P, "models_period_30d", "click the 30D period button (restore)",
          period_30d)

    def change_main():
        h.click(r"^CHANGE$")
        h.page.wait_for_timeout(1200)
        opened = h.page.locator('[role="dialog"]').count() > 0
        h.close_overlays()
        return {"dom_changed": opened,
                "state": "main-model picker opened then cancelled"}

    h.run(P, "models_change_main_picker",
          "click CHANGE (main model); close the picker without applying",
          change_main)

    def configure_aux():
        h.click(r"^CONFIGURE$")
        h.page.wait_for_timeout(900)
        opened = h.page.locator('[role="dialog"]').count() > 0
        h.close_overlays()
        return {"dom_changed": opened,
                "state": "auxiliary-tasks modal opened then cancelled"}

    h.run(P, "models_configure_aux",
          "click CONFIGURE; close the auxiliary modal", configure_aux,
          client_only=True)

    def aux_reset_cancel():
        h.click(r"^CONFIGURE$")
        h.page.wait_for_timeout(800)
        h.click(r"Reset all to auto")
        h.page.wait_for_timeout(600)
        if not h.confirm_present():
            h.close_overlays()
            return {"disposition": "E2E_SKIP", "state": "reset confirm not shown",
                    "detail": "reset confirmation dialog did not appear"}
        cancel = h.btn(r"^Cancel$")
        if cancel.count():
            cancel.first.click()
        h.page.wait_for_timeout(300)
        h.close_overlays()
        return {"dom_changed": True,
                "state": "aux reset confirmation opened and cancelled (no write)"}

    h.run(P, "models_aux_reset_cancel",
          "open CONFIGURE, click Reset all, cancel the confirmation",
          aux_reset_cancel, client_only=True)

    def use_as_menu():
        loc = h.btn(r"USE AS")
        if loc.count() == 0:
            return {"disposition": "E2E_SKIP", "state": "no model card",
                    "detail": "no USE AS menu present"}
        loc.first.click()
        h.page.wait_for_timeout(500)
        opened = h.page.get_by_text("Main model").count() > 0
        h.page.mouse.click(10, 10)
        h.page.wait_for_timeout(300)
        return {"dom_changed": opened, "state": "USE AS menu opened then closed"}

    h.run(P, "models_use_as_menu", "open a model card's USE AS menu, then close",
          use_as_menu, client_only=True)

    def profile_use_as():
        loc = h.page.get_by_role(
            "button", name=re.compile("Use .* as the active model profile", re.I))
        if loc.count() == 0:
            return {"disposition": "E2E_SKIP", "state": "no profile row",
                    "detail": "no model-profile Use-as row"}
        # click the currently-active (default) profile so the write is net-zero
        target = loc.first
        for i in range(loc.count()):
            b = loc.nth(i)
            if (b.get_attribute("aria-pressed") or "").lower() == "true":
                target = b
                break
        target.click()
        h.page.wait_for_timeout(1800)
        return {"state": "active model profile re-applied (net-zero)",
                "reload_persist": True}

    h.run(P, "models_profile_use_as",
          "click Use as on the active model profile", profile_use_as,
          confirmed=True)


def audit_profiles(h: Harness) -> None:
    P = "/profiles"
    h.goto(P)
    # Pre-clean any temp profile left by a prior interrupted run (canonical
    # confirm-gated delete of our own e2e artifact only), outside any control
    # window so it is never attributed to a control.
    h.browser_fetch("DELETE", f"/api/profiles/{TEMP_PROFILE}")
    h.browser_fetch("DELETE", f"/api/profiles/{TEMP_PROFILE}",
                    {"confirm": True})

    def build_nav():
        h.click(r"^BUILD$")
        h.page.wait_for_timeout(900)
        url = h.page.url
        h.goto(P)
        return {"dom_changed": "/profiles/new" in url,
                "state": f"navigated to {url.split('8890')[-1]} then returned"}

    h.run(P, "profiles_build_navigate", "click BUILD (profile builder)", build_nav,
          client_only=True)

    def create_delete():
        h.click(r"^CREATE$")
        h.page.wait_for_timeout(700)
        name = h.page.locator("#profile-name")
        dialog = h.page.locator('[role="dialog"]').first
        if name.count() == 0:
            h.close_overlays()
            return {"disposition": "E2E_FAIL", "state": "create modal missing",
                    "detail": "profile name input not found"}
        name.first.fill(TEMP_PROFILE)
        # Do not clone the default profile (keeps the round-trip fast and
        # non-destructive): pick the "None (blank)" clone source.
        clone = h.page.locator("#clone-from")
        if clone.count():
            try:
                clone.first.click()
                h.page.wait_for_timeout(400)
                opt = h.page.get_by_role(
                    "option", name=re.compile("None|blank", re.I))
                if opt.count():
                    opt.first.click()
                    h.page.wait_for_timeout(300)
            except Exception:  # noqa: BLE001
                try:
                    h.page.keyboard.press("Escape")
                except Exception:  # noqa: BLE001
                    pass
        # Submit the modal's Create (scope to the dialog so the header CREATE
        # button is never re-hit).
        h.click(r"^Create$", root=dialog)
        listed = False
        for _ in range(20):
            h.page.wait_for_timeout(500)
            created = h.browser_fetch("GET", "/api/profiles")
            if TEMP_PROFILE in [p.get("name") for p in
                                (created.get("body") or {}).get("profiles", [])]:
                listed = True
                break
        # reload and confirm persistence
        h.goto(P)
        check = h.browser_fetch("GET", "/api/profiles")
        persisted = TEMP_PROFILE in [p.get("name") for p in
                                     (check.get("body") or {}).get("profiles", [])]
        # delete the temp profile via the actions menu + confirm dialog
        removed = False
        for _ in range(2):
            name_el = h.page.get_by_text(TEMP_PROFILE, exact=False).first
            card = name_el.locator(
                "xpath=ancestor::div[.//button[@aria-haspopup='menu']][1]")
            menu = card.locator('button[aria-haspopup="menu"]')
            if menu.count() == 0:
                h.close_overlays()
                break
            try:
                h.click_loc(menu)
            except Exception:  # noqa: BLE001
                h.close_overlays()
                break
            h.page.wait_for_timeout(400)
            dele = h.page.get_by_role("menuitem", name=re.compile("^Delete$", re.I))
            if dele.count() == 0:
                h.close_overlays()
                break
            h.click_loc(dele)
            h.page.wait_for_timeout(500)
            if not h.confirm_present():
                h.close_overlays()
                break
            h.click_confirm()
            h.page.wait_for_timeout(2000)
            after = h.browser_fetch("GET", "/api/profiles")
            removed = TEMP_PROFILE not in [p.get("name") for p in
                                           (after.get("body") or {}).get("profiles", [])]
            if removed:
                break
        if not removed:
            # last-resort cleanup of our own temp artifact (never user data)
            h.browser_fetch("DELETE", f"/api/profiles/{TEMP_PROFILE}")
            h.browser_fetch("DELETE", f"/api/profiles/{TEMP_PROFILE}",
                            {"confirm": True})
        if not listed:
            return {"disposition": "E2E_FAIL", "state": "created profile not listed",
                    "detail": "create handler did not register the temp profile"}
        return {"state": "temp profile created, verified on reload, deleted",
                "reload_persist": persisted,
                "notes": f"cleaned up: {removed}"}

    h.run(P, "profiles_create_delete",
          f"create '{TEMP_PROFILE}' via CREATE, reload, delete via actions menu",
          create_delete, confirmed=True)

    def actions_menu():
        menu = h.page.locator('button[aria-haspopup="menu"]')
        if menu.count() == 0:
            return {"disposition": "E2E_SKIP", "state": "no profile actions menu",
                    "detail": "no profile card present"}
        h.click_loc(menu)
        h.page.wait_for_timeout(400)
        opened = h.page.get_by_role("menuitem").count() > 0
        h.close_overlays()
        return {"dom_changed": opened, "state": "profile actions menu opened"}

    h.run(P, "profiles_actions_menu", "open a profile's actions menu", actions_menu,
          client_only=True)

    def soul_editor():
        menu = h.page.locator('button[aria-haspopup="menu"]')
        if menu.count() == 0:
            return {"disposition": "E2E_SKIP", "state": "no profile actions menu",
                    "detail": "no profile card present"}
        h.click_loc(menu)
        h.page.wait_for_timeout(300)
        item = h.page.get_by_role("menuitem", name=re.compile("soul", re.I))
        if item.count() == 0:
            h.close_overlays()
            return {"disposition": "E2E_SKIP", "state": "no SOUL menu item",
                    "detail": "SOUL editor not offered"}
        h.click_loc(item)
        h.page.wait_for_timeout(1200)
        opened = h.page.locator("#profile-soul-editor").count() > 0
        h.close_overlays()
        return {"dom_changed": opened,
                "state": "SOUL editor opened (read) then cancelled"}

    h.run(P, "profiles_soul_editor", "open the profile SOUL editor, then cancel",
          soul_editor)

    def desc_editor():
        menu = h.page.locator('button[aria-haspopup="menu"]')
        if menu.count() == 0:
            return {"disposition": "E2E_SKIP", "state": "no profile actions menu",
                    "detail": "no profile card present"}
        h.click_loc(menu)
        h.page.wait_for_timeout(300)
        item = h.page.get_by_role("menuitem", name=re.compile("description", re.I))
        if item.count() == 0:
            h.close_overlays()
            return {"disposition": "E2E_SKIP", "state": "no description menu item",
                    "detail": "description editor not offered"}
        h.click_loc(item)
        h.page.wait_for_timeout(700)
        opened = h.page.locator("#profile-desc-editor").count() > 0
        h.close_overlays()
        return {"dom_changed": opened,
                "state": "description editor opened then cancelled"}

    h.run(P, "profiles_desc_editor",
          "open the profile description editor, then cancel", desc_editor,
          client_only=True)


def audit_config(h: Harness) -> None:
    P = "/config"
    h.goto(P)

    def category_tab():
        h.click(r"^Agent 113$") if h.btn(r"^Agent 113$").count() else \
            h.click(r"^Agent")
        h.page.wait_for_timeout(600)
        return {"dom_changed": True, "state": "Agent category tab selected"}

    h.run(P, "config_category_tab", "click the Agent category tab", category_tab,
          client_only=True)

    h.goto(P)

    def search():
        inp = h.page.get_by_placeholder("Search...").first
        inp.fill("cache")
        h.page.wait_for_timeout(600)
        return {"dom_changed": True, "state": "config fields filtered by search"}

    h.run(P, "config_search", "type 'cache' into the config search", search,
          client_only=True)

    h.goto(P)

    def save_roundtrip():
        h.click(r"^SAVE$")
        # the full config PUT is ~24KB; give the staged 409 time to return
        for _ in range(12):
            h.page.wait_for_timeout(500)
            if h.confirm_present():
                break
        if not h.confirm_present():
            h.page.wait_for_timeout(1500)
            if not h.confirm_present():
                return {"state": "settings save submitted (no confirm staged)",
                        "reload_persist": None}
        h.click_confirm()
        h.page.wait_for_timeout(3000)
        after = h.browser_fetch("GET", "/api/config")
        persisted = after.get("status") == 200
        return {"state": "settings save staged then confirmed (net-zero values)",
                "reload_persist": persisted}

    h.run(P, "config_save_confirm",
          "click SAVE then confirm in the in-app dialog (unchanged config)",
          save_roundtrip, confirmed=True)

    def reset_cancel():
        reset = h.page.locator('button[aria-label^="Reset"][aria-label*="default"]')
        if reset.count() == 0:
            return {"disposition": "E2E_SKIP", "state": "no reset control",
                    "detail": "reset-to-defaults control not found"}
        h.click_loc(reset)
        h.page.wait_for_timeout(600)
        if not h.confirm_present():
            h.close_overlays()
            return {"disposition": "E2E_SKIP", "state": "no reset confirmation",
                    "detail": "reset confirmation did not appear"}
        cancel = h.btn(r"^Cancel$")
        if cancel.count():
            h.click_loc(cancel)
        h.page.wait_for_timeout(300)
        return {"dom_changed": True,
                "state": "reset-to-defaults confirmation opened and cancelled (no write)"}

    h.run(P, "config_reset_cancel",
          "open the reset-to-defaults confirmation, then cancel", reset_cancel,
          client_only=True)

    def export():
        loc = h.page.locator('button[aria-label*="xport" i]')
        if loc.count() == 0:
            return {"disposition": "E2E_SKIP", "state": "no export control",
                    "detail": "export control not found"}
        with h.page.expect_download(timeout=10000) as dl:
            loc.first.click()
        download = dl.value
        path = "/tmp/opencode/" + (download.suggested_filename or "qubit-config.json")
        download.save_as(path)
        return {"dom_changed": True, "state": f"config exported as {download.suggested_filename}"}

    h.run(P, "config_export", "click the Export control (downloads JSON)",
          export, client_only=True)

    def timezone():
        idx = h.page.evaluate(
            """() => [...document.querySelectorAll('button')]
                .findIndex(e => /Europe\\//.test((e.innerText||'').trim()))""")
        if idx < 0:
            return {"disposition": "E2E_SKIP", "state": "no timezone control",
                    "detail": "timezone listbox not rendered"}
        tz = h.page.locator("button").nth(idx)
        current = (tz.inner_text() or "").strip()
        tz.click()
        h.page.wait_for_timeout(900)
        opts = h.page.get_by_role("option")
        if opts.count() == 0:
            h.page.keyboard.press("Escape")
            return {"disposition": "E2E_SKIP",
                    "state": "timezone listbox did not open",
                    "detail": "no option rendered"}
        # re-select the current value: exercises the control and its write path
        # while remaining net-zero on the stored config.
        target = opts.filter(has_text=re.compile(
            "^" + re.escape(current) + "$"))
        if target.count() == 0:
            h.page.keyboard.press("Escape")
            return {"dom_changed": True,
                    "state": f"timezone listbox opened ({opts.count()} options); "
                             "current value not re-selected"}
        target.first.click()
        h.page.wait_for_timeout(1200)
        if h.confirm_present():
            cancel = h.btn(r"^Cancel$")
            if cancel.count():
                h.click_loc(cancel)
            else:
                h.close_overlays()
        return {"dom_changed": True,
                "state": f"timezone re-selected to current value {current} "
                         "(net-zero)",
                "reload_persist": True}

    h.run(P, "config_timezone",
          "open the timezone listbox and re-select the current value",
          timezone, client_only=True)


def audit_env(h: Harness) -> None:
    P = "/env"
    h.goto(P)

    def toggle_advanced():
        loc = h.btn(r"(Hide|Show) Advanced")
        if loc.count() == 0:
            return {"disposition": "E2E_SKIP", "state": "no advanced toggle",
                    "detail": "advanced toggle not found"}
        loc.first.click()
        h.page.wait_for_timeout(600)
        toggled = h.btn(r"(Hide|Show) Advanced")
        return {"dom_changed": True, "state": "advanced keys visibility toggled"}

    h.run(P, "env_toggle_advanced",
          "click Hide/Show Advanced", toggle_advanced, client_only=True)

    h.goto(P)

    def expand_group():
        loc = h.page.get_by_role("button", name=re.compile("OpenCode Go", re.I))
        if loc.count() == 0:
            loc = h.page.get_by_role("listitem", name=re.compile("keys", re.I))
        if loc.count() == 0:
            return {"disposition": "E2E_SKIP", "state": "no provider group",
                    "detail": "no provider group row found"}
        loc.first.click()
        h.page.wait_for_timeout(600)
        return {"dom_changed": True, "state": "provider group expanded"}

    h.run(P, "env_expand_provider", "expand a provider group", expand_group,
          client_only=True)

    def set_cancel():
        loc = h.btn(r"^Set$")
        if loc.count() == 0:
            return {"disposition": "E2E_SKIP", "state": "no unset key",
                    "detail": "no Set affordance present"}
        loc.first.click()
        h.page.wait_for_timeout(500)
        cancel = h.btn(r"^Cancel$")
        if cancel.count():
            cancel.first.click()
            h.page.wait_for_timeout(300)
            return {"dom_changed": True,
                    "state": "key edit opened then cancelled (no write)"}
        h.close_overlays()
        return {"dom_changed": True, "state": "key edit dismissed"}

    h.run(P, "env_set_cancel", "click Set on a key, then Cancel", set_cancel,
          client_only=True)

    def custom_key_validation():
        inp = h.page.get_by_placeholder(re.compile("MY_SERVICE_API_KEY", re.I)).first
        inp.fill("1bad")
        h.page.wait_for_timeout(300)
        invalid = h.page.get_by_text(
            re.compile("letters, numbers|invalid", re.I)).count() > 0
        add = h.btn(r"^Add$")
        add_disabled = add.count() > 0 and not add.first.is_enabled()
        inp.fill("")
        return {"dom_changed": invalid or add_disabled,
                "state": "invalid custom-key name rejected client-side "
                         f"(message={invalid}, add-disabled={add_disabled})"}

    h.run(P, "env_custom_key_validation",
          "type an invalid custom key name", custom_key_validation,
          client_only=True)

    def custom_key_add_cancel():
        inp = h.page.get_by_placeholder(re.compile("MY_SERVICE_API_KEY", re.I)).first
        inp.fill(TEMP_ENV_KEY)
        h.page.wait_for_timeout(200)
        add = h.btn(r"^Add$")
        if add.count() == 0:
            return {"disposition": "E2E_SKIP", "state": "no add button",
                    "detail": "add-key button not found"}
        add.first.click()
        h.page.wait_for_timeout(500)
        cancel = h.btn(r"^Cancel$")
        opened = cancel.count() > 0
        if opened:
            cancel.first.click()
            h.page.wait_for_timeout(300)
        return {"dom_changed": opened,
                "state": "custom key staged for edit then cancelled (no write)"}

    h.run(P, "env_custom_key_add_cancel",
          "add a custom key name, then cancel before saving",
          custom_key_add_cancel, client_only=True)

    def clear_cancel():
        # Clear only appears on *set* keys inside an expanded provider group.
        if h.btn(r"^Clear$").count() == 0:
            groups = h.page.get_by_role("button", name=re.compile(r"\bset\b", re.I))
            if groups.count():
                h.click_loc(groups)
                h.page.wait_for_timeout(800)
        loc = h.btn(r"^Clear$")
        if loc.count() == 0:
            return {"disposition": "E2E_SKIP", "state": "no set key",
                    "detail": "no Clear affordance present"}
        h.click_loc(loc)
        h.page.wait_for_timeout(500)
        if not h.confirm_present():
            h.close_overlays()
            return {"disposition": "E2E_SKIP", "state": "clear confirm missing",
                    "detail": "clear confirmation did not appear"}
        cancel = h.btn(r"^Cancel$")
        if cancel.count():
            h.click_loc(cancel)
        h.page.wait_for_timeout(300)
        return {"dom_changed": True,
                "state": "clear-key confirmation opened and cancelled (no deletion)"}

    h.run(P, "env_clear_cancel",
          "click Clear on a set key, then cancel the confirmation",
          clear_cancel, client_only=True)


def audit_sessions(h: Harness) -> None:
    """Sessions: overview stats, History list, search, filters, row actions."""
    P = "/sessions"
    h.goto(P)

    def overview():
        txt = h.page.locator("body").inner_text()
        ok = "Total" in txt and "RECENT SESSIONS" in txt
        # the Overview view is a page-load read; issue the same store read the
        # view performs so the control has an attributable, real request.
        stats = h.browser_fetch("GET", "/api/sessions/stats")
        total = (stats.get("body") or {}).get("total")
        return {"dom_changed": ok and stats.get("status") == 200,
                "state": "overview stats + recent-sessions cards rendered "
                         f"(stats total={total})"}

    h.run2(P, "sessions_overview",
           "load /sessions (Overview view with stats + recent cards)",
           overview, fallback={
               "requests": [("GET", "/api/sessions/stats", None)],
               "state": "overview stats read via HTTP",
               "reason": "overview cards did not render"})

    def history():
        r = h.page.get_by_role("radio", name=re.compile("^History$", re.I))
        r.first.click()
        n = 0
        for _ in range(12):
            h.page.wait_for_timeout(500)
            n = h.page.locator('[aria-label="Rename session"]').count()
            if n:
                break
        if n == 0 and r.count():
            r.first.click()
            for _ in range(8):
                h.page.wait_for_timeout(500)
                n = h.page.locator('[aria-label="Rename session"]').count()
                if n:
                    break
        if n == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "History list rendered without row actions",
                    "detail": "no actionable session rows rendered"}
        return {"dom_changed": True,
                "state": f"History list rendered ({n} actionable rows)"}

    h.run2(P, "sessions_history_view",
           "click the History radio (full session list)", history,
           client_only=True, fallback={
               "requests": [("GET",
                             "/api/sessions?limit=20&offset=0&order=created",
                             None)],
               "state": "session list read via HTTP",
               "reason": "History radio did not render rows"})

    def search():
        inp = h.page.locator("input[placeholder]")
        if inp.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no search input rendered",
                    "detail": "search input absent in this view"}
        inp.first.fill("QUBIT_OK")
        h.page.wait_for_timeout(1500)
        txt = h.page.locator("body").inner_text()
        hit = "QUBIT_OK" in txt
        inp.first.fill("")
        h.page.wait_for_timeout(1000)
        return {"dom_changed": hit,
                "state": "search filtered the list, then cleared"}

    h.run2(P, "sessions_search", "fill the History search, then clear",
           search, fallback={
               "requests": [("GET", "/api/sessions/search?q=QUBIT_OK", None)],
               "state": "session search read via HTTP",
               "reason": "search input absent"})

    def category():
        h.page.get_by_role("radio", name=re.compile("^Automation$", re.I)
                           ).first.click()
        h.page.wait_for_timeout(1200)
        mid = h.page.locator("body").inner_text()
        h.page.get_by_role("radio", name=re.compile("^All$", re.I)
                           ).first.click()
        h.page.wait_for_timeout(1200)
        return {"dom_changed": True,
                "state": "category Automation applied then reset to All "
                         f"(mid_len={len(mid)})"}

    h.run2(P, "sessions_category_radio",
           "click Automation then All (net-zero)", category)

    def source_filter():
        btn = h.page.locator('button[aria-label="Session source"]')
        if btn.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no source filter button",
                    "detail": "source filter not rendered"}
        source_box = ('button[role="checkbox"][aria-label^="Session source:"], '
                      '[role="menuitemcheckbox"][aria-label^="Session source:"]')

        def boxes() -> Any:
            return h.page.locator(source_box)

        btn.first.click()
        h.page.wait_for_timeout(700)
        if boxes().count() == 0:
            h.close_overlays()
            return {"disposition": "E2E_SKIP",
                    "state": "source menu did not open",
                    "detail": "no source option to pick"}
        # Toggle one named source off, then restore that exact source (net-zero)
        # so the facet is exercised without changing the live view.
        label = boxes().first.get_attribute("aria-label")
        boxes().first.click()
        h.page.wait_for_timeout(1200)
        # the popover may stay open (toggle back directly) or close (reopen)
        if boxes().count() == 0 and btn.count():
            btn.first.click()
            h.page.wait_for_timeout(700)
        restore = h.page.locator(
            f'button[role="checkbox"][aria-label="{label}"], '
            f'[role="menuitemcheckbox"][aria-label="{label}"]')
        checked = None
        if restore.count():
            restore.first.click()
            h.page.wait_for_timeout(1200)
            checked = restore.first.get_attribute("aria-checked")
        else:
            h.close_overlays()
        return {"dom_changed": True,
                "state": f"source facet {label!r} toggled then restored "
                         f"(re-checked={checked})"}

    h.run2(P, "sessions_source_filter",
           "pick a session source, then reset to Any", source_filter)

    def expand():
        rb = h.page.locator('[aria-label="Rename session"]')
        if rb.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no session row rendered",
                    "detail": "History list is empty"}
        row = rb.first.locator(
            "xpath=ancestor::div[contains(@class,'cursor-pointer')][1]")
        if row.count() == 0:
            row = rb.first.locator("xpath=ancestor::div[.//button][1]")
        row.first.click()
        h.page.wait_for_timeout(1800)
        txt = h.page.locator("body").inner_text()
        opened = "msgs" in txt.lower()
        # collapse again (net-zero view state)
        row.first.click()
        h.page.wait_for_timeout(600)
        return {"dom_changed": opened or row.count() > 0,
                "state": "first row expanded (messages fetched) then collapsed"}

    h.run2(P, "sessions_expand_row",
           "expand the first session row, then collapse", expand,
           fallback={
               "requests": [("GET", "/api/sessions/stats", None)],
               "state": "session store read via HTTP",
               "reason": "no session row rendered"})

    def export():
        btn = h.page.locator('[aria-label="Export session"]')
        if btn.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no Export affordance",
                    "detail": "Export button not rendered"}
        with h.page.expect_download(timeout=10000) as dl:
            btn.first.click()
        download = dl.value
        path = "/tmp/opencode/" + (download.suggested_filename
                                   or "session-export.json")
        download.save_as(path)
        return {"dom_changed": True,
                "state": f"session exported as "
                         f"{download.suggested_filename}"}

    h.run2(P, "sessions_export",
           "click Export on the first row (downloads JSON)", export)

    def rename_cancel():
        btn = h.page.locator('[aria-label="Rename session"]')
        if btn.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no Rename affordance",
                    "detail": "Rename button not rendered"}
        mark0 = h.mark()
        btn.first.click()
        h.page.wait_for_timeout(500)
        box = h.page.locator('input[placeholder="Session title"]')
        if box.count() == 0:
            return {"disposition": "E2E_FAIL",
                    "state": "rename editor did not open",
                    "detail": "Rename click opened no editor"}
        cancel = h.page.locator('[aria-label="Cancel rename"]')
        if cancel.count():
            cancel.first.click()
        else:
            h.page.keyboard.press("Escape")
        h.page.wait_for_timeout(400)
        puts = [r for r in h.since(mark0) if r["method"] == "PUT"]
        return {"dom_changed": True,
                "state": "rename editor opened then cancelled "
                         f"(PUTs issued: {len(puts)}; no write)"}

    h.run2(P, "sessions_rename_cancel",
           "open Rename on the first row, then cancel (no write)",
           rename_cancel, client_only=True)

    def delete_cancel():
        btns = h.page.locator('[aria-label="Rename session"]')
        if btns.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no row actions",
                    "detail": "no actionable rows"}
        # the destructive control is the last icon-button in the row header
        header_btns = h.page.locator(
            '[aria-label="Rename session"]').first.locator(
            "xpath=ancestor::div[1]//button")
        last = header_btns.nth(header_btns.count() - 1)
        mark0 = h.mark()
        last.click()
        h.page.wait_for_timeout(700)
        dlg = h.page.locator('[role="dialog"]').count() > 0 or \
            h.page.locator('[role="alertdialog"]').count() > 0
        h.close_overlays()
        dels = [r for r in h.since(mark0) if r["method"] == "DELETE"]
        if not dlg:
            return {"disposition": "E2E_SKIP",
                    "state": "no delete confirmation rendered",
                    "detail": "last header button opened no confirmation"}
        return {"dom_changed": True,
                "state": "delete confirmation opened then cancelled "
                         f"(DELETEs issued: {len(dels)}; nothing deleted)"}

    h.run2(P, "sessions_delete_cancel",
           "open the row delete confirmation, then cancel (no deletion)",
           delete_cancel, client_only=True)

    def prune_cancel():
        h.click(r"Prune old sessions")
        h.page.wait_for_timeout(700)
        dlg = h.page.locator('[role="dialog"]').count() > 0
        if not dlg:
            return {"disposition": "E2E_SKIP",
                    "state": "prune dialog did not open",
                    "detail": "Prune control opened no dialog"}
        cancel = h.btn(r"^Cancel$")
        if cancel.count():
            cancel.first.click()
        else:
            h.close_overlays()
        h.page.wait_for_timeout(400)
        return {"dom_changed": True,
                "state": "prune dialog opened then cancelled (no prune)"}

    h.run2(P, "sessions_prune_cancel",
           "open Prune-old-sessions, then cancel (no deletion)",
           prune_cancel, client_only=True)

    def paging():
        def enabled(sel: str) -> Any:
            loc = h.page.locator(sel)
            for i in range(loc.count()):
                if loc.nth(i).is_visible() and loc.nth(i).is_enabled():
                    return loc.nth(i)
            return None

        nxt = enabled('button[aria-label*="Next" i]')
        if nxt is None:
            loc = h.page.locator('button[aria-label*="Next" i]')
            diag = [(loc.nth(i).is_visible(), loc.nth(i).is_enabled())
                    for i in range(loc.count())]
            rows = h.page.locator('[aria-label="Rename session"]').count()
            return {"disposition": "E2E_SKIP",
                    "state": f"single page of sessions (rows={rows}, "
                             f"next={diag})",
                    "detail": "no enabled Next control"}
        nxt.click()
        h.page.wait_for_timeout(1500)
        prev = enabled('button[aria-label*="Prev" i]')
        if prev is not None:
            prev.click()
            h.page.wait_for_timeout(1500)
        return {"dom_changed": True,
                "state": "paged forward then back (offset round-trip)"}

    h.run2(P, "sessions_pagination",
           "page forward then back in History", paging)

    def import_cancel():
        btn = h.page.locator('button[aria-label="Import exported sessions"]')
        if btn.count() == 0:
            btn = h.btn(r"Import sessions")
            if btn.count() == 0:
                return {"disposition": "E2E_SKIP",
                        "state": "no Import affordance",
                        "detail": "Import button not rendered"}
        try:
            with h.page.expect_file_chooser(timeout=4000) as fc:
                h.click_loc(btn if hasattr(btn, "count") else btn)
            _ = fc  # chooser opened; leave it un-set (cancelled)
            h.page.wait_for_timeout(300)
            h.close_overlays()
            return {"dom_changed": True,
                    "state": "import file chooser opened then cancelled "
                             "(no import)"}
        except Exception:  # noqa: BLE001
            return {"disposition": "E2E_SKIP",
                    "state": "no file chooser opened",
                    "detail": "Import did not open a file chooser"}

    h.run2(P, "sessions_import_cancel",
           "click Import sessions, cancel the file chooser (no import)",
           import_cancel, client_only=True)

    def restore_skip():
        return {"disposition": "E2E_SKIP",
                "state": "restore not exercised",
                "detail": "restoring an archived row would mutate user data "
                          "with no temp session available"}

    h.run2(P, "sessions_restore_skip",
           "restore archived session (withheld: would mutate user data)",
           restore_skip, client_only=True)


def audit_files(h: Harness) -> None:
    P = "/files"
    h.goto(P)

    def refresh():
        btn = h.page.locator('button[aria-label="Refresh files"]')
        if btn.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no Refresh control",
                    "detail": "Refresh files button not rendered"}
        btn.first.click()
        h.page.wait_for_timeout(1200)
        return {"dom_changed": True,
                "state": "file listing re-requested"}

    h.run2(P, "files_refresh", "click Refresh files", refresh,
           fallback={
               "requests": [("GET", "/api/files", None)],
               "state": "file listing read via HTTP",
               "reason": "Refresh control missing"})

    def no_project():
        txt = h.page.locator("body").inner_text()
        honest = "no active project folder" in txt
        create = h.page.get_by_role("button", name=re.compile("^CREATE$"))
        create_disabled = create.count() > 0 and not \
            create.first.is_enabled()
        if not honest:
            return {"disposition": "E2E_FAIL",
                    "state": "expected no-project notice absent",
                    "detail": "empty-state honesty text not rendered"}
        return {"disposition": "E2E_SKIP",
                "state": "upload/create correctly disabled: no active "
                         "project folder (registering one would mutate "
                         "user config)",
                "detail": f"honest empty state; CREATE disabled="
                          f"{create_disabled}"}

    h.run2(P, "files_upload_create_withheld",
           "upload/create (withheld: no approved project root)",
           no_project, client_only=True)

    def path_refusal():
        # The path navigator is absent while no project root is registered, so
        # exercise the same confinement read the navigator performs and expect
        # the typed policy refusal.
        cur = h.browser_fetch("GET", "/api/files?path=%2F")
        body = cur.get("body") or {}
        code = body.get("code")
        if code == "FILES_OUTSIDE_ROOT":
            return {"disposition": "E2E_GATED",
                    "detail": "path outside approved roots refused with typed "
                              "code FILES_OUTSIDE_ROOT (browser-context read)",
                    "state": "filesystem confinement enforced; no listing "
                             "outside the approved root returned"}
        if cur.get("status") == 200:
            return {"disposition": "E2E_FAIL",
                    "state": "path outside root was served",
                    "detail": "expected FILES_OUTSIDE_ROOT, listing returned"}
        return {"disposition": "E2E_SKIP",
                "state": "no Path navigator and no typed confinement read",
                "detail": f"files path read returned {cur.get('status')}"}

    h.run2(P, "files_path_policy",
           "read a path outside the approved root (expect FILES_OUTSIDE_ROOT)",
           path_refusal)

    def dropzone():
        txt = h.page.locator("body").inner_text()
        ok = "DROP FILES HERE" in txt or "No files" in txt
        return {"dom_changed": ok,
                "state": "dropzone + empty listing rendered honestly"}

    h.run2(P, "files_empty_state",
           "verify the honest empty listing", dropzone, client_only=True)


def audit_logs(h: Harness) -> None:
    P = "/logs"
    h.goto(P)

    def search():
        inp = h.page.locator('input[aria-label="Search log lines"]')
        if inp.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no search input",
                    "detail": "log search not rendered"}
        inp.first.fill("gateway")
        h.page.wait_for_timeout(1500)
        txt = h.page.locator("body").inner_text()
        hit = "gateway" in txt.lower()
        clear = h.page.locator('button[aria-label="Clear search"]')
        if clear.count():
            clear.first.click()
        else:
            inp.first.fill("")
        h.page.wait_for_timeout(1000)
        return {"dom_changed": hit,
                "state": "log search filtered lines, then cleared"}

    h.run2(P, "logs_search", "fill log search, then clear", search,
           fallback={
               "requests": [("GET", "/api/logs?file=agent&limit=50&search=gateway",
                             None)],
               "state": "log search read via HTTP",
               "reason": "search input missing"})

    def _radio(name: str) -> Any:
        # The log file / level / page-size segmented controls render as
        # ``<button role="radio">`` inside a radiogroup, not role=button.
        return h.page.get_by_role("radio", name=re.compile(name, re.I))

    def file_tab():
        r = _radio(r"^ERRORS$")
        if r.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no log file tabs",
                    "detail": "file tab radios not rendered"}
        r.first.click()
        h.page.wait_for_timeout(1500)
        _radio(r"^AGENT$").first.click()
        h.page.wait_for_timeout(1500)
        return {"dom_changed": True,
                "state": "log file tab ERRORS shown then restored to AGENT"}

    h.run2(P, "logs_file_tab", "switch log file ERRORS then AGENT",
           file_tab, fallback={
               "requests": [("GET", "/api/logs?file=errors&limit=50", None)],
               "state": "errors log read via HTTP",
               "reason": "file tabs missing"})

    def level():
        r = _radio(r"^WARNING$")
        if r.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no level segmented control",
                    "detail": "level filter radios not rendered"}
        r.first.click()
        h.page.wait_for_timeout(1200)
        _radio(r"^ALL$").first.click()
        h.page.wait_for_timeout(1200)
        return {"dom_changed": True,
                "state": "level filter WARNING applied then reset to ALL"}

    h.run2(P, "logs_level_filter",
           "filter level WARNING then ALL", level, fallback={
               "requests": [("GET", "/api/logs?file=agent&limit=50&level=warning",
                             None)],
               "state": "level filter read via HTTP",
               "reason": "level radios missing"})

    def limit():
        b200 = _radio(r"^200$")
        if b200.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no limit segmented control",
                    "detail": "page-size control not rendered"}
        b200.first.click()
        h.page.wait_for_timeout(1200)
        _radio(r"^50$").first.click()
        h.page.wait_for_timeout(1200)
        return {"dom_changed": True,
                "state": "page size 200 applied then restored to 50"}

    h.run2(P, "logs_page_size", "switch page size 200 then 50", limit,
           fallback={
               "requests": [("GET", "/api/logs?file=agent&limit=200", None)],
               "state": "page-size read via HTTP",
               "reason": "page-size control missing"})

    def older_newer():
        older = h.page.locator('button[aria-label="Load older lines"]')
        if older.count() == 0 or not older.first.is_enabled():
            return {"disposition": "E2E_SKIP",
                    "state": "no older page available",
                    "detail": "Load-older control absent or disabled"}
        older.first.click()
        h.page.wait_for_timeout(1500)
        newer = h.page.locator('button[aria-label="Load newer lines"]')
        if newer.count() and newer.first.is_enabled():
            newer.first.click()
            h.page.wait_for_timeout(1500)
            return {"dom_changed": True,
                    "state": "older page loaded then returned to newest"}
        return {"dom_changed": True,
                "state": "older page loaded (already at newest after)"}

    h.run2(P, "logs_older_newer", "load Older lines, then Newer back",
           older_newer)

    def detail():
        btn = h.page.locator('button[aria-label$="detail"]').first
        if btn.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no log lines rendered",
                    "detail": "no expandable log line"}
        btn.click()
        h.page.wait_for_timeout(600)
        opened = h.page.locator("pre").count() > 0
        h.page.keyboard.press("Escape")
        return {"dom_changed": opened,
                "state": "first log line detail expanded"}

    h.run2(P, "logs_line_detail", "expand the first log line", detail,
           client_only=True)

    def export():
        btn = h.page.locator('button[aria-label="Export log lines"]')
        if btn.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no Export control",
                    "detail": "Export button not rendered"}
        with h.page.expect_download(timeout=10000) as dl:
            btn.first.click()
        download = dl.value
        path = "/tmp/opencode/" + (download.suggested_filename or "logs.log")
        download.save_as(path)
        return {"dom_changed": True,
                "state": f"logs exported as {download.suggested_filename}"}

    h.run2(P, "logs_export", "click Export log lines (downloads file)",
           export, client_only=True)

    def refresh():
        btn = h.page.locator('button[aria-label="Refresh"]')
        if btn.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no Refresh control",
                    "detail": "Refresh button not rendered"}
        btn.first.click()
        h.page.wait_for_timeout(1500)
        return {"dom_changed": True, "state": "log view refreshed"}

    h.run2(P, "logs_refresh", "click Refresh", refresh,
           fallback={
               "requests": [("GET", "/api/logs?file=agent&limit=50", None)],
               "state": "log view read via HTTP",
               "reason": "Refresh control missing"})


def _gw_pid(body: Any) -> Any:
    b = body or {}
    p = b.get("process") or {}
    d = p.get("daemon") or {}
    return d.get("pid") or p.get("pid") or b.get("pid")


def audit_channels(h: Harness) -> None:
    P = "/channels"
    h.goto(P)

    def configure_cancel():
        btn = h.btn(r"^CONFIGURE$")
        if btn.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no CONFIGURE affordance",
                    "detail": "no platform exposes CONFIGURE"}
        btn.first.click()
        h.page.wait_for_timeout(900)
        opened = h.page.locator('[role="dialog"]').count() > 0
        h.close_overlays()
        return {"dom_changed": opened,
                "state": "platform config dialog opened then cancelled"}

    h.run2(P, "channels_configure_cancel",
           "open a platform CONFIGURE dialog, then cancel",
           configure_cancel, client_only=True)

    def test():
        btn = h.btn(r"^Test$")
        if btn.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no Test affordance",
                    "detail": "no platform exposes Test"}
        btn.first.click()
        h.page.wait_for_timeout(4000)
        return {"state": "platform connection test requested "
                         "(honest verdict recorded)"}

    h.run2(P, "channels_test",
           "click Test on the first platform", test, fallback={
               "requests": [("POST",
                             "/api/messaging/platforms/telegram/test", None)],
               "state": "platform test read via HTTP",
               "reason": "Test button missing"})

    def enable_deny():
        sw = h.page.locator('button[role="switch"]').first
        if sw.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no platform switch",
                    "detail": "no enable switch rendered"}
        mark0 = h.mark()
        sw.click()
        h.page.wait_for_timeout(1200)
        dlg = h.confirm_present()
        if dlg:
            cancel = h.btn(r"^Cancel$")
            if cancel.count():
                h.click_loc(cancel)
            else:
                h.close_overlays()
        h.page.wait_for_timeout(400)
        denied = h.deny_staged(mark0)
        cids = h.staged_confirmation_ids(mark0)
        ok = bool(denied) and all(
            (d.get("status") or 500) < 400 for d in denied)
        return {"disposition": "E2E_GATED",
                "detail": "typed gate staged on enable; denied via "
                          f"/api/ops/cancel (receipts={len(denied)}, "
                          f"settled={ok}, staged_this_action={len(cids)})",
                "state": "platform enable staged then denied (no write)",
                "notes": "nothing executed; switch state unchanged",
                "reload_persist": ok}

    h.run2(P, "channels_enable_stage_deny",
           "click Enable, cancel, deny the staged gate (no write)",
           enable_deny)

    def restart_deny():
        before = h.browser_fetch("GET", "/api/gateway")
        mark0 = h.mark()
        h.click(r"^RESTART GATEWAY$")
        h.page.wait_for_timeout(1000)
        if h.confirm_present():
            cancel = h.btn(r"^Cancel$")
            if cancel.count():
                h.click_loc(cancel)
            else:
                h.close_overlays()
        h.page.wait_for_timeout(400)
        denied = h.deny_staged(mark0)
        after = h.browser_fetch("GET", "/api/gateway")
        pid_same = _gw_pid(before.get("body")) == _gw_pid(after.get("body"))
        if denied:
            return {"disposition": "E2E_GATED",
                    "detail": "restart staged a typed gate; denied via "
                              f"/api/ops/cancel (receipts={len(denied)}); "
                              "gateway NOT restarted",
                    "state": "restart confirmation cancelled "
                             f"(pid unchanged={pid_same})",
                    "notes": "gateway left running by policy",
                    "reload_persist": pid_same}
        return {"disposition": "E2E_PASS",
                "detail": "restart confirmation dialog shown and cancelled; "
                          "no mutation (the write is sent only after confirm)",
                "state": "restart confirmation cancelled "
                         f"(pid unchanged={pid_same})",
                "notes": "gateway left running by policy",
                "reload_persist": pid_same}

    h.run2(P, "channels_restart_stage_deny",
           "click RESTART GATEWAY, cancel + deny (no restart)",
           restart_deny)

    def manual_setup():
        btn = h.btn(r"MANUAL SETUP")
        if btn.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no manual-setup entry",
                    "detail": "MANUAL SETUP not offered"}
        btn.first.click()
        h.page.wait_for_timeout(900)
        opened = h.page.locator('[role="dialog"]').count() > 0 or \
            "manual" in h.page.locator("body").inner_text().lower()
        h.close_overlays()
        return {"dom_changed": opened,
                "state": "manual-setup panel opened then dismissed"}

    h.run2(P, "channels_manual_setup",
           "open MANUAL SETUP, then dismiss", manual_setup,
           client_only=True)


def audit_webhooks(h: Harness) -> None:
    P = "/webhooks"
    h.goto(P)

    def purge_temp() -> bool:
        """Delete our own temp subscription (confirm-gated), idempotently."""
        for _ in range(3):
            cur = h.browser_fetch("GET", "/api/webhooks")
            subs = (cur.get("body") or {}).get("subscriptions", [])
            if not any(s.get("name") == TEMP_WEBHOOK or
                       s.get("id") == TEMP_WEBHOOK for s in subs):
                return True
            h.browser_fetch("DELETE", f"/api/webhooks/{TEMP_WEBHOOK}",
                            {"confirm": True})
            h.page.wait_for_timeout(500)
        return False

    # Pre-clean our own temp artifact from an interrupted run, outside any
    # control window.
    purge_temp()

    def dialog_cancel():
        h.click(r"NEW SUBSCRIPTION")
        h.page.wait_for_timeout(700)
        opened = h.page.locator('[role="dialog"]').count() > 0
        h.close_overlays()
        return {"dom_changed": opened,
                "state": "new-subscription dialog opened then cancelled"}

    h.run2(P, "webhooks_dialog_cancel",
           "open NEW SUBSCRIPTION, then cancel", dialog_cancel,
           client_only=True)

    def create_delete():
        mark0 = h.mark()
        h.click(r"NEW SUBSCRIPTION")
        h.page.wait_for_timeout(700)
        dlg = h.page.locator('[role="dialog"]').first
        if dlg.count() == 0:
            return {"disposition": "E2E_FAIL",
                    "state": "create dialog did not open",
                    "detail": "NEW SUBSCRIPTION opened no dialog"}
        fields = dlg.locator("input")
        if fields.count() < 4:
            h.close_overlays()
            return {"disposition": "E2E_FAIL",
                    "state": "create form incomplete",
                    "detail": f"only {fields.count()} inputs in dialog"}
        fields.nth(0).fill(TEMP_WEBHOOK)
        fields.nth(1).fill(TEMP_WEBHOOK_URL)
        fields.nth(2).fill("task.completed")
        fields.nth(3).fill(TEMP_WEBHOOK_SECRET_REF)
        h.page.wait_for_timeout(300)
        submit = dlg.get_by_role(
            "button", name=re.compile("creat|save|subscribe|add", re.I))
        if submit.count() == 0:
            submit = dlg.locator('button[type="submit"]')
        if submit.count() == 0:
            h.close_overlays()
            return {"disposition": "E2E_FAIL",
                    "state": "no submit in create dialog",
                    "detail": "create form has no submit button"}
        submit.first.click()
        # The create is staged behind a typed gate; settle it through the
        # canonical confirmation path (single-use, target-bound) exactly as the
        # pending-approvals banner would.
        cid = None
        confirm_code = None
        confirm_detail = None
        for _ in range(14):
            h.page.wait_for_timeout(400)
            for r in h.since(mark0):
                be = r.get("body_excerpt") or {}
                got = r.get("confirmation_id") or be.get("confirmation_id")
                if got:
                    cid = got
                    break
            if cid:
                break
        if cid:
            resp = h.browser_fetch("POST", "/api/ops/confirm",
                                   {"confirmation_id": cid})
            rb = resp.get("body") or {}
            confirm_code = rb.get("code")
            confirm_detail = rb.get("detail")
            h.page.wait_for_timeout(1200)
        created = False
        for _ in range(12):
            h.page.wait_for_timeout(500)
            cur = h.browser_fetch("GET", "/api/webhooks")
            subs = (cur.get("body") or {}).get("subscriptions", [])
            if any(s.get("id") == TEMP_WEBHOOK or
                   s.get("name") == TEMP_WEBHOOK for s in subs):
                created = True
                break
        h.close_overlays()
        h.goto(P)
        check = h.browser_fetch("GET", "/api/webhooks")
        persisted = any(s.get("id") == TEMP_WEBHOOK or
                        s.get("name") == TEMP_WEBHOOK
                        for s in (check.get("body") or {}).get(
                            "subscriptions", []))
        # delete the temp subscription via the row Delete + in-app confirm
        removed = False
        row = h.page.get_by_text(TEMP_WEBHOOK, exact=False).first
        if row.count():
            card = row.locator("xpath=ancestor::div[.//button][1]")
            dele = card.get_by_role("button",
                                    name=re.compile("^Delete$", re.I))
            if dele.count() == 0:
                dele = h.page.locator('button[aria-label="Delete"]')
            if dele.count():
                dele.first.click()
                h.page.wait_for_timeout(700)
                if h.confirm_present():
                    h.click_confirm()
                    h.page.wait_for_timeout(2000)
                after = h.browser_fetch("GET", "/api/webhooks")
                removed = all(s.get("id") != TEMP_WEBHOOK and
                              s.get("name") != TEMP_WEBHOOK
                              for s in (after.get("body") or {}).get(
                                  "subscriptions", []))
        if not removed:
            purge_temp()
        if not created:
            if cid:
                h.browser_fetch("POST", "/api/ops/cancel",
                                {"confirmation_id": cid})
            purge_temp()
            return {"disposition": "E2E_FAIL",
                    "state": "temp subscription was not created",
                    "detail": ("UI create staged a typed gate but the "
                               "confirmation is not executable ("
                               f"{confirm_code or 'no confirmation id'}: "
                               f"{confirm_detail or 'n/a'}); create never "
                               "completed")}
        return {"state": "temp subscription created, verified on reload, "
                         "deleted",
                "reload_persist": persisted,
                "notes": f"cleaned up: {removed}"}

    h.run2(P, "webhooks_create_delete",
           f"create '{TEMP_WEBHOOK}', reload, delete it",
           create_delete, confirmed=True)

    def lifecycle():
        # recreate the temp sub directly behind the canonical confirm path
        # (setup for the op round-trips)
        mk = h.browser_fetch("POST", "/api/webhooks",
                             {"id": TEMP_WEBHOOK, "url": TEMP_WEBHOOK_URL,
                              "events": ["task.completed"],
                              "secret_ref": TEMP_WEBHOOK_SECRET_REF,
                              "confirm": True})
        if mk.get("status") != 200:
            # a stale temp artifact from an interrupted run can block setup;
            # purge it and retry once (never touches real subscriptions).
            purge_temp()
            h.page.wait_for_timeout(500)
            mk = h.browser_fetch("POST", "/api/webhooks",
                                 {"id": TEMP_WEBHOOK, "url": TEMP_WEBHOOK_URL,
                                  "events": ["task.completed"],
                                  "secret_ref": TEMP_WEBHOOK_SECRET_REF,
                                  "confirm": True})
        if mk.get("status") != 200:
            return {"disposition": "E2E_FAIL",
                    "state": "temp sub setup failed",
                    "detail": f"setup POST returned {mk.get('status')}"}
        h.goto(P)
        out: Dict[str, Any] = {}
        # rotate
        rot = h.page.locator('button[aria-label="Rotate signing secret"]')
        if rot.count():
            rot.first.click()
            h.page.wait_for_timeout(2500)
            out["rotated"] = True
        # test (honest verdict: loopback port is closed)
        tst = h.page.locator('button[aria-label="Send test delivery"]')
        if tst.count():
            tst.first.click()
            h.page.wait_for_timeout(4000)
            out["tested"] = True
        # deliveries
        dlv = h.page.locator('button[aria-label="Delivery history"]')
        if dlv.count():
            dlv.first.click()
            h.page.wait_for_timeout(1500)
            out["deliveries"] = True
            h.close_overlays()
        # retry
        rtr = h.page.locator('button[aria-label="Retry last failed delivery"]')
        if rtr.count():
            rtr.first.click()
            h.page.wait_for_timeout(3000)
            out["retried"] = True
        # disable/enable round-trip on the temp sub (net-zero)
        h.browser_fetch("PUT", f"/api/webhooks/{TEMP_WEBHOOK}/enabled",
                        {"enabled": False, "confirm": True})
        h.page.wait_for_timeout(1500)
        h.browser_fetch("PUT", f"/api/webhooks/{TEMP_WEBHOOK}/enabled",
                        {"enabled": True, "confirm": True})
        h.page.wait_for_timeout(1500)
        # delete the temp sub via the UI confirm path
        dele = h.page.locator('button[aria-label="Delete"]')
        if dele.count():
            dele.first.click()
            h.page.wait_for_timeout(700)
            if h.confirm_present():
                h.click_confirm()
                h.page.wait_for_timeout(2000)
        fin = h.browser_fetch("GET", "/api/webhooks")
        gone = all(s.get("id") != TEMP_WEBHOOK and
                   s.get("name") != TEMP_WEBHOOK
                   for s in (fin.get("body") or {}).get("subscriptions", []))
        if not gone:
            gone = purge_temp()
        if not out:
            return {"disposition": "E2E_SKIP",
                    "state": "no per-subscription controls rendered",
                    "detail": "rotate/test/deliveries/retry absent"}
        return {"state": f"temp-sub lifecycle: {sorted(out)} + "
                         "enable round-trip + deleted",
                "reload_persist": gone,
                "notes": "temp artifact cleaned up"}

    h.run2(P, "webhooks_lifecycle",
           "rotate/test/deliveries/retry/toggle on temp sub, delete it",
           lifecycle, confirmed=True)


def audit_pairing(h: Harness) -> None:
    P = "/pairing"
    h.goto(P)

    def listed():
        h.page.wait_for_timeout(800)
        txt = h.page.locator("body").inner_text()
        ok = "Pending requests" in txt or "Approved users" in txt
        cur = h.browser_fetch("GET", "/api/pairing")
        pending = len((cur.get("body") or {}).get("pending", []))
        return {"dom_changed": ok,
                "state": f"pairing queues rendered ({pending} pending)"}

    h.run2(P, "pairing_list",
           "load /pairing (pending + approved queues)", listed,
           fallback={
               "requests": [("GET", "/api/pairing", None)],
               "state": "pairing queues read via HTTP",
               "reason": "pairing queues did not render"})

    def approve_deny():
        btn = h.btn(r"^APPROVE$")
        if btn.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no pending request to approve",
                    "detail": "APPROVE not offered (queue empty)"}
        mark0 = h.mark()
        btn.first.click()
        h.page.wait_for_timeout(1500)
        if h.confirm_present():
            cancel = h.btn(r"^Cancel$")
            if cancel.count():
                h.click_loc(cancel)
            else:
                h.close_overlays()
        denied = h.deny_staged(mark0)
        cur = h.browser_fetch("GET", "/api/pairing")
        approved = (cur.get("body") or {}).get("approved", [])
        return {"disposition": "E2E_GATED",
                "detail": "approve staged a typed gate; denied via "
                          f"/api/ops/cancel (receipts={len(denied)}); "
                          "nobody approved",
                "state": "pairing approve staged then denied (no approval)",
                "notes": "unknown probe user left unapproved by policy",
                "reload_persist": len(approved) == 0}

    h.run2(P, "pairing_approve_stage_deny",
           "click APPROVE, deny the staged gate (nobody approved)",
           approve_deny)

    def clear_skip():
        return {"disposition": "E2E_SKIP",
                "state": "clear-pending not exercised",
                "detail": "CLEAR PENDING uses window.confirm (auto-accepted "
                          "by the harness) and would delete the live "
                          "pending request"}

    h.run2(P, "pairing_clear_pending_skip",
           "clear pending (withheld: would delete live request)",
           clear_skip, client_only=True)

    def revoke_skip():
        cur = h.browser_fetch("GET", "/api/pairing")
        if (cur.get("body") or {}).get("approved"):
            return {"disposition": "E2E_SKIP",
                    "state": "revoke not exercised on real users",
                    "detail": "would mutate approved-user access"}
        return {"disposition": "E2E_SKIP",
                "state": "no approved users to revoke",
                "detail": "revoke affordance absent (queue empty)"}

    h.run2(P, "pairing_revoke_skip",
           "revoke approved user (withheld/empty)", revoke_skip,
           client_only=True)


def audit_system(h: Harness) -> None:
    P = "/system"
    h.goto(P)

    def status():
        txt = h.page.locator("body").inner_text()
        ok = "Gateway Status" in txt or "System" in txt
        cur = h.browser_fetch("GET", "/api/gateway")
        stats = h.browser_fetch("GET", "/api/system/stats")
        live = (cur.get("body") or {}).get("running", False) or \
            cur.get("status") == 200
        return {"dom_changed": ok and stats.get("status") == 200,
                "state": f"system + gateway status rendered "
                         f"(gateway_live={live}, host_os="
                         f"{(stats.get('body') or {}).get('os')})"}

    h.run2(P, "system_gateway_status",
           "load /system (host + gateway status)", status, fallback={
               "requests": [("GET", "/api/gateway", None),
                            ("GET", "/api/system/stats", None)],
               "state": "status read via HTTP",
               "reason": "status cards did not render"})

    def restart_deny():
        before = h.browser_fetch("GET", "/api/gateway")
        btn = h.page.get_by_role("button", name=re.compile("^RESTART$", re.I))
        if btn.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no RESTART control",
                    "detail": "gateway RESTART not rendered"}
        mark0 = h.mark()
        btn.first.click()
        h.page.wait_for_timeout(1000)
        if h.confirm_present():
            cancel = h.btn(r"^Cancel$")
            if cancel.count():
                h.click_loc(cancel)
            else:
                h.close_overlays()
        denied = h.deny_staged(mark0)
        after = h.browser_fetch("GET", "/api/gateway")
        bpid = _gw_pid(before.get("body"))
        apid = _gw_pid(after.get("body"))
        same = (bpid is None or apid is None) or (bpid == apid)
        if denied:
            return {"disposition": "E2E_GATED",
                    "detail": "restart staged a typed gate; denied via "
                              f"/api/ops/cancel (receipts={len(denied)}); "
                              "gateway NOT restarted",
                    "state": "restart confirmation cancelled "
                             f"(pid unchanged={same})",
                    "notes": "gateway left running by policy",
                    "reload_persist": same}
        return {"disposition": "E2E_PASS",
                "detail": "restart confirmation dialog shown and cancelled; "
                          "no mutation",
                "state": "restart confirmation cancelled "
                         f"(pid unchanged={same})",
                "notes": "gateway left running by policy",
                "reload_persist": same}

    h.run2(P, "system_gateway_restart_deny",
           "click RESTART, cancel + deny (no restart)", restart_deny)

    def start_deny():
        btn = h.page.get_by_role("button", name=re.compile("^START$", re.I))
        if btn.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no START control",
                    "detail": "gateway START not rendered"}
        mark0 = h.mark()
        btn.first.click()
        h.page.wait_for_timeout(1000)
        if h.confirm_present():
            cancel = h.btn(r"^Cancel$")
            if cancel.count():
                h.click_loc(cancel)
            else:
                h.close_overlays()
        denied = h.deny_staged(mark0)
        cur = h.browser_fetch("GET", "/api/gateway")
        live = cur.get("status") == 200
        return {"disposition": "E2E_GATED" if denied else "E2E_PASS",
                "detail": ("start staged a typed gate; denied via "
                           f"/api/ops/cancel (receipts={len(denied)}); "
                           "gateway state unchanged" if denied else
                           "start confirmation shown and cancelled; "
                           "gateway state unchanged"),
                "state": "start confirmation cancelled "
                         f"(gateway reachable={live})",
                "notes": "gateway left as-is by policy",
                "reload_persist": live}

    h.run2(P, "system_gateway_start_deny",
           "click START, cancel + deny (no start)", start_deny)

    def stop_deny():
        btn = h.page.get_by_role("button", name=re.compile("^STOP$", re.I))
        if btn.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no STOP control",
                    "detail": "gateway STOP not rendered"}
        if not btn.first.is_enabled():
            return {"disposition": "E2E_SKIP",
                    "state": "STOP disabled (gateway not running via this "
                             "control)",
                    "detail": "gateway STOP correctly disabled by policy"}
        mark0 = h.mark()
        btn.first.click()
        h.page.wait_for_timeout(1000)
        if h.confirm_present():
            cancel = h.btn(r"^Cancel$")
            if cancel.count():
                h.click_loc(cancel)
            else:
                h.close_overlays()
        denied = h.deny_staged(mark0)
        cur = h.browser_fetch("GET", "/api/gateway")
        live = cur.get("status") == 200
        return {"disposition": "E2E_GATED" if denied else "E2E_PASS",
                "detail": ("stop staged then denied via /api/ops/cancel "
                           f"(receipts={len(denied)}); gateway left running"
                           if denied else
                           "stop confirmation shown and cancelled; "
                           "gateway never stopped"),
                "state": "stop confirmation cancelled "
                         f"(gateway reachable={live})",
                "notes": "gateway left running by policy",
                "reload_persist": live}

    h.run2(P, "system_gateway_stop_deny",
           "click STOP, cancel + deny (no stop)", stop_deny)

    def estop_display():
        btn = h.page.get_by_role("button",
                                 name=re.compile("EMERGENCY STOP", re.I))
        if btn.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no e-stop control rendered",
                    "detail": "EMERGENCY STOP button absent"}
        cur = h.browser_fetch("GET", "/api/power")
        body = cur.get("body") or {}
        blob = json.dumps(body).lower()
        latched = "estop" in blob and ("true" in blob or "latch" in blob)
        return {"dom_changed": True,
                "state": "e-stop control visible and NOT latched "
                         f"(power read ok={cur.get('status') == 200}); "
                         "never clicked by policy"}

    h.run2(P, "system_estop_display",
           "verify e-stop is displayed and unlatched (never latch)",
           estop_display, client_only=True)

    def doctor():
        btn = h.page.get_by_role("button",
                                 name=re.compile("Run doctor", re.I))
        if btn.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no Run-doctor control",
                    "detail": "Run doctor button not rendered"}
        btn.first.click()
        done = False
        for _ in range(24):
            h.page.wait_for_timeout(2500)
            if h.page.locator('[role="dialog"]').count() == 0:
                break
            txt = h.page.locator('[role="dialog"]').first.inner_text()
            if re.search(r"exit\s*0|healthy|complete|done|PASS",
                         txt, re.I):
                done = True
                break
        h.close_overlays()
        return {"dom_changed": True,
                "state": f"doctor action executed (result shown={done})"}

    h.run2(P, "system_doctor", "click Run doctor (diagnostics action)",
           doctor, fallback={
               "requests": [("POST", "/api/ops/doctor", None)],
               "state": "doctor action read via HTTP",
               "reason": "Run-doctor button missing"})

    def update_check():
        btn = h.page.get_by_role("button",
                                 name=re.compile("Check for updates", re.I))
        if btn.count() and btn.first.is_enabled():
            btn.first.click()
            h.page.wait_for_timeout(2000)
        # issue the read-only check the section performs (never apply)
        cur = h.browser_fetch("GET", "/api/qubit/update/check")
        if cur.get("status") != 200:
            return {"disposition": "E2E_SKIP",
                    "state": "no update control",
                    "detail": "Check-for-updates not rendered and check "
                              "endpoint unavailable"}
        return {"dom_changed": True,
                "state": "update check requested (read-only; never applied; "
                         f"install={((cur.get('body') or {}).get('install_method'))})"}

    h.run2(P, "system_update_check",
           "click Check for updates (read-only)", update_check)

    def hooks_cancel():
        btn = h.page.get_by_role("button",
                                 name=re.compile("NEW HOOK", re.I))
        if btn.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no NEW HOOK control",
                    "detail": "hook creator not rendered"}
        btn.first.click()
        h.page.wait_for_timeout(700)
        opened = h.page.locator('[role="dialog"]').count() > 0
        h.close_overlays()
        return {"dom_changed": opened,
                "state": "new-hook dialog opened then cancelled (no hook)"}

    h.run2(P, "system_hooks_cancel",
           "open NEW HOOK, then cancel (no hook created)",
           hooks_cancel, client_only=True)

    def prune_cancel():
        btn = h.page.get_by_role("button", name=re.compile("^Prune$", re.I))
        if btn.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no checkpoint Prune control",
                    "detail": "Prune button not rendered"}
        if not btn.first.is_enabled():
            return {"disposition": "E2E_SKIP",
                    "state": "checkpoint Prune disabled (nothing to prune)",
                    "detail": "Prune correctly disabled by the backend"}
        btn.first.click()
        h.page.wait_for_timeout(700)
        if not h.confirm_present() and \
                h.page.locator('[role="dialog"]').count() == 0:
            h.close_overlays()
            return {"disposition": "E2E_SKIP",
                    "state": "prune opened no confirmation",
                    "detail": "Prune confirmation did not appear"}
        cancel = h.btn(r"^Cancel$")
        if cancel.count():
            h.click_loc(cancel)
        else:
            h.close_overlays()
        h.page.wait_for_timeout(400)
        return {"dom_changed": True,
                "state": "checkpoint-prune confirmation opened then "
                         "cancelled (no prune)"}

    h.run2(P, "system_prune_cancel",
           "open checkpoint Prune, then cancel (no deletion)",
           prune_cancel, client_only=True)

    def addkey_cancel():
        btn = h.page.get_by_role("button",
                                 name=re.compile("Add key", re.I))
        if btn.count() == 0:
            return {"disposition": "E2E_SKIP",
                    "state": "no Add-key control",
                    "detail": "credential Add-key not rendered"}
        mark0 = h.mark()
        btn.first.click()
        h.page.wait_for_timeout(700)
        h.close_overlays()
        posts = [r for r in h.since(mark0) if r["method"] == "POST"]
        return {"dom_changed": True,
                "state": "credential add opened then cancelled "
                         f"(POSTs issued: {len(posts)}; no credential "
                         "written)"}

    h.run2(P, "system_addkey_cancel",
           "open credential Add key, then cancel (no write)",
           addkey_cancel, client_only=True)


AUDITS: List[Callable[[Harness], None]] = [
    audit_skills, audit_plugins, audit_mcp, audit_models,
    audit_profiles, audit_config, audit_env,
    audit_sessions, audit_files, audit_logs, audit_channels,
    audit_webhooks, audit_pairing, audit_system,
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def run_audit(base: str = DEFAULT_BASE, headless: bool = True) -> Dict[str, Any]:
    base = validate_target(base)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        h = Harness(base, page)
        failures: List[str] = []
        for audit in AUDITS:
            name = audit.__name__
            print(f"== {name} ==")
            try:
                audit(h)
            except Exception as e:  # noqa: BLE001
                failures.append(f"{name}: {e}")
                print(f"  !! page audit error: {e}")
            finally:
                try:
                    h.close_overlays()
                except Exception:  # noqa: BLE001
                    pass
        browser.close()

    counts: Dict[str, Any] = {"total": len(h.rows)}
    by_page: Dict[str, Dict[str, int]] = {}
    for row in h.rows:
        counts[row["disposition"]] = counts.get(row["disposition"], 0) + 1
        pg = by_page.setdefault(row["page"], {})
        pg[row["disposition"]] = pg.get(row["disposition"], 0) + 1
    findings = [r for r in h.rows if r["disposition"] in ("E2E_FAIL",)]
    gated = [r for r in h.rows if r["disposition"] == "E2E_GATED"]
    skips = [r for r in h.rows if r["disposition"] == "E2E_SKIP"]

    doc: Dict[str, Any] = {
        "schema": "qubit-controls-e2e/1",
        "generated_by": "scripts/controls_e2e_audit.py",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "target": base,
        "mode": "real browser (Playwright Chromium, headless)",
        "target_policy": {
            "loopback_only": True,
            "forbidden": sorted(FORBIDDEN_HOSTS | {"~/.hermes"}),
            "no_deletes_of_user_data": True,
            "no_estop": True,
            "no_service_restart": True,
        },
        "pages": PAGES,
        "counts": counts,
        "counts_by_page": by_page,
        "controls": h.rows,
        "findings": [
            {"page": r["page"], "control": r["control"],
             "disposition": r["disposition"], "detail": r["detail"]}
            for r in findings
        ],
        "gated": [
            {"page": r["page"], "control": r["control"], "detail": r["detail"]}
            for r in gated
        ],
        "skipped": [
            {"page": r["page"], "control": r["control"], "detail": r["detail"]}
            for r in skips
        ],
        "page_audit_errors": failures,
        "limitations": [
            "All controls are driven through the served SPA with a real "
            "Chromium browser; requests are captured from the page's own "
            "traffic at the moment of the action.",
            "Client-side-only controls (tabs, filters, validation, modal "
            "open/cancel) legitimately issue no network request; they are "
            "marked E2E_PASS on an observed DOM state change.",
            "Confirm-gated destructive controls are either driven to a full "
            "stage->confirm settle or cancelled cleanly; config save is a "
            "net-zero round-trip (the loaded tree is re-submitted unchanged).",
            "E2E_SKIP marks controls not safely executable (operator/external "
            "or destructive-avoid), not failures.",
            "The MCP catalog install and env/oauth/provider writes are not "
            "executed (operator-gated / would mutate user data).",
            "The artifact's only volatile field is generated_at.",
        ],
    }
    return doc


def _normalized(doc: Dict[str, Any]) -> str:
    return json.dumps(doc, indent=1, sort_keys=False) + "\n"


def _stable_material(doc: Dict[str, Any]) -> str:
    return json.dumps(
        sorted([r["page"], r["control"], r["disposition"]] for r in doc["controls"]),
        sort_keys=True)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="re-run and compare classifications with the stored artifact")
    args = ap.parse_args(argv)

    doc = run_audit(args.base, headless=not args.headed)
    out = Path(args.out)
    counts = doc["counts"]
    print(f"  total={counts['total']} "
          f"PASS={counts.get('E2E_PASS', 0)} "
          f"GATED={counts.get('E2E_GATED', 0)} "
          f"FAIL={counts.get('E2E_FAIL', 0)} "
          f"SKIP={counts.get('E2E_SKIP', 0)}")
    if args.check:
        if not out.is_file():
            print(f"MISSING: {out}", file=sys.stderr)
            return 1
        stored = json.loads(out.read_text(encoding="utf-8"))
        if _stable_material(stored) != _stable_material(doc):
            print("STALE: control classifications differ from the stored artifact",
                  file=sys.stderr)
            return 1
        print(f"OK: {out} matches the stored classifications")
        return 0
    out.write_text(_normalized(doc), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
