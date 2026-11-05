"""Browser-level contract for the three dashboard chat navigation paths.

The dashboard exposes two chat surfaces:

* ``/qubit-chat`` — the agent-backed native chat. Its right panel is the
  ``ChatSessionList`` (``#chat-side-panel``). "New chat" mints a canonical
  session through ``POST /api/qubit/chat/sessions`` and points the surface at
  ``/qubit-chat?resume=<id>``; clicking a recent row resumes that transcript.
* ``/chat`` — the xterm terminal. The ``/sessions`` page's "Resume in Chat"
  control navigates to ``/chat?resume=<id>``, which verifies the id and dials
  the PTY with that canonical session as its resume target.

This test drives the real served SPA (real routes, real buttons) against a
hermetic ``WebUI`` listener and a throwaway ``QUBIT_HOME``. Sessions are
created in that throwaway store only; no operator session is read or written.

For the terminal leg the PTY *spawn* is a controlled test double (the same
seam the vendored ``pty_ws`` tests use). Everything the browser does is real:
the control is clicked, the route changes, the resume probe runs, the
single-use WS ticket is minted with the resume id, and the server asks its
spawn layer to resume that exact id. The double only avoids booting a full
Ink TUI in a unit test; it records the resume target.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DIST = REPO_ROOT / "frontend" / "qubit-dist"

# The vendored runtime must win over ``tests/qubit_state`` (the root pytest.ini
# puts ``tests`` on pythonpath, and that directory shares the package name).
for _path in (REPO_ROOT / "qubit_runtime", REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

pytestmark = pytest.mark.skipif(
    not (DIST / "index.html").is_file(),
    reason="dashboard bundle is not built (run scripts/build_dashboard.sh)",
)

pw = pytest.importorskip("playwright.sync_api")


def _launch_browser(p):
    """Launch a real Chromium: installed Chrome first, bundled second.

    The host's Playwright package may not have its browser download populated
    while a system Chrome exists. Either is a real browser; there is no
    DOM-only or network-only fallback.
    """
    errors = []
    for kwargs in (
        {"channel": "chrome"},
        {},
    ):
        try:
            return p.chromium.launch(headless=True, **kwargs)
        except Exception as exc:  # noqa: BLE001 - try the next real browser
            errors.append(f"{kwargs or 'bundled'}: {exc}")
    pytest.skip("no Chromium/Chrome available for Playwright: " + " | ".join(errors))


def _start(tmp_path):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    # Fail closed if the test-isolation fixture ever stops redirecting the
    # runtime home: this suite must never read or write an operator session.
    home = Path(os.environ.get("QUBIT_HOME", "")).resolve()
    assert tmp_path.resolve() in home.parents, (
        f"QUBIT_HOME is not this test's throwaway home: {home}")

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    cfg_path = tmp_path / "voice.yaml"
    cfg_path.write_text(
        "logging:\n"
        f"  jsonl_path: '{state}'\n"
        "webui:\n"
        "  token: e2e10-token\n"
        "terminal:\n"
        "  min_context_tokens: 4096\n",
        encoding="utf-8",
    )
    cfg = Config.load(cfg_path)

    class _FakeOrch:
        _route = "gateway"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg
            self._t0 = time.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    port = ui._server.server_address[1]
    return ui, f"http://127.0.0.1:{port}", cfg


def _session_token(base: str) -> str:
    """Extract the token from the served shell exactly as the SPA does.

    ``WebUI`` injects ``window.__QUBIT_SESSION_TOKEN__`` into the loopback
    document; the browser reads it from ``window``. We scrape the same document
    so the test can assert the store from outside without inventing auth.
    """
    with urllib.request.urlopen(base + "/chat", timeout=20) as resp:
        html = resp.read().decode("utf-8", "replace")
    match = re.search(
        r"window\.__QUBIT_SESSION_TOKEN__=(\"(?:[^\"\\]|\\.)*\")", html)
    assert match, "served shell did not inject __QUBIT_SESSION_TOKEN__"
    token = json.loads(match.group(1))
    assert token, "served shell injected an empty session token"
    return token


def _api_get(base, token, path):
    """Authenticated GET using the session token scraped from the shell."""
    req = urllib.request.Request(
        base + path, headers={"X-Qubit-Session-Token": token})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _seed_session(cfg, name: str, messages):
    from voicebridge.qubit_session_store import QubitSessionStore

    store = QubitSessionStore(cfg)
    sid = f"e2e10-{uuid.uuid4().hex[:12]}"
    made = store.create(name, session_id=sid, identity="terminal:chat")
    assert made.get("state") == "OK", made
    for role, text in messages:
        appended = store.append(sid, role, text)
        assert appended.get("state") == "OK", appended
    return store, sid


def _archive(store, *session_ids):
    for sid in session_ids:
        if sid:
            try:
                store.delete(sid, confirm=True)
            except Exception:  # noqa: BLE001 - temp store teardown only
                pass


def _close_store(store):
    """Release the process-wide cached SessionDB handle for this temp store."""
    try:
        from voicebridge.qubit_session_store import close_cached_db
        close_cached_db(store._path)
    except Exception:  # noqa: BLE001 - temp store teardown only
        pass


def _collect_errors(page):
    errors = []
    missing = []
    page.on("console", lambda m: errors.append(m.text)
            if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("response", lambda r: missing.append(f"{r.status} {r.url}")
            if r.status == 404 else None)
    return errors, missing


def test_right_panel_recent_click_then_new_chat(tmp_path):
    """Right-panel recent row resumes its transcript; New chat mints a fresh one."""
    ui, base, cfg = _start(tmp_path)
    name = f"E2E right panel {uuid.uuid4().hex[:8]}"
    user_marker = f"RIGHT_PANEL_USER_{uuid.uuid4().hex[:10]}"
    assistant_marker = f"RIGHT_PANEL_ASSISTANT_{uuid.uuid4().hex[:10]}"
    store, seeded = _seed_session(
        cfg, name, [("user", user_marker), ("assistant", assistant_marker)])
    store, other = _seed_session(
        cfg, f"E2E other {uuid.uuid4().hex[:8]}",
        [("user", "OTHER_SESSION"), ("assistant", "OTHER_SESSION_REPLY")])
    created = []
    try:
        token = _session_token(base)
        assert token
        with pw.sync_playwright() as p:
            browser = _launch_browser(p)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors, missing = _collect_errors(page)
            try:
                # Open a DIFFERENT session first so the recent-row click is a
                # real switch, and so a fresh store never probes the implicit
                # default session (whose 404 is unrelated to these controls).
                page.goto(base + f"/qubit-chat?resume={other}",
                          wait_until="domcontentloaded", timeout=30000)
                panel = page.locator("#chat-side-panel")
                panel.wait_for(state="visible", timeout=30000)
                page.wait_for_url(
                    lambda url: f"resume={other}" in url, timeout=30000)

                # --- path 2: recent-session click resumes that session ---
                row = panel.get_by_text(name, exact=True).first
                row.wait_for(state="visible", timeout=30000)
                row.click()
                page.wait_for_url(
                    lambda url: f"resume={seeded}" in url, timeout=30000)
                transcript = page.locator(".qchat__transcript")
                transcript.get_by_text(assistant_marker, exact=False).wait_for(
                    state="visible", timeout=30000)
                transcript.get_by_text(user_marker, exact=False).wait_for(
                    state="visible", timeout=30000)

                # --- path 1: New chat creates and opens a fresh chat ---
                new_chat = panel.get_by_role(
                    "button", name="New chat", exact=True)
                with page.expect_response(
                    lambda r: r.request.method == "POST"
                    and "/api/qubit/chat/sessions" in r.url,
                    timeout=30000,
                ) as response_info:
                    new_chat.click()
                response = response_info.value
                assert response.ok, f"New chat create failed: {response.status}"
                payload = response.json()
                new_id = str((payload.get("session") or {}).get("id") or "")
                created.append(new_id)
                assert new_id, f"create response had no session id: {payload!r}"
                assert new_id != seeded, "New chat reused the open session"
                page.wait_for_url(
                    lambda url: f"resume={new_id}" in url, timeout=30000)
                # The fresh surface is genuinely empty: the previously
                # resumed transcript is not carried over.
                page.locator(".qchat__empty[data-state='empty']").wait_for(
                    state="visible", timeout=30000)
                assert transcript.get_by_text(assistant_marker).count() == 0
                assert transcript.get_by_text(user_marker).count() == 0
                # The new id is the canonical store's row, not a client-only
                # path param: the served API (authenticated with the token
                # scraped from the shell, exactly like the SPA) resolves it.
                status, body = _api_get(
                    base, token,
                    f"/agent/api/qubit/chat/sessions/{new_id}")
                assert status == 200, status
                assert (body.get("session") or {}).get("id") == new_id, body

                fatal = [e for e in errors if "favicon" not in e.lower()]
                assert not fatal, f"console/page errors: {fatal[:8]}; 404s: {missing[:8]}"
                assert not missing, f"unexpected 404s: {missing[:8]}"
            finally:
                browser.close()
    finally:
        _archive(store, seeded, other, *created)
        _close_store(store)
        ui.stop()


class _SpawnBridge:
    """Controlled PTY double: records nothing, stays alive, never echoes input."""

    def __init__(self):
        self.pid = 424242
        self.closed = False
        self.input: list[bytes] = []

    def read(self, timeout):
        # Block like a real PTY read instead of returning immediately (a busy
        # loop would peg a core for the duration of the test).
        time.sleep(min(float(timeout or 0.1), 0.1))
        return b"" if not self.closed else None

    def write(self, payload):
        self.input.append(payload)
        return True

    def isalive(self):
        return not self.closed

    def close(self):
        self.closed = True

    def resize(self, **_kw):
        return True


def test_sessions_page_resume_opens_chat_terminal_for_that_session(tmp_path):
    """`/sessions` → Resume in Chat resumes the selected canonical session."""
    ui, base, cfg = _start(tmp_path)
    name = f"E2E sessions {uuid.uuid4().hex[:8]}"
    marker = f"SESSIONS_PAGE_RESUME_{uuid.uuid4().hex[:10]}"
    store, seeded = _seed_session(
        cfg, name, [("user", marker), ("assistant", "resumed")])
    # Install the PTY double BEFORE the browser can dial. `pty_ws` treats an
    # overridden `_spawn` as a test double (no context-floor guard), exactly
    # like the vendored transport tests.
    spawn_calls = _install_spawn_double(ui, tmp_path)
    try:
        token = _session_token(base)
        assert token
        with pw.sync_playwright() as p:
            browser = _launch_browser(p)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors, missing = _collect_errors(page)
            ticket_posts: list[dict] = []
            detail_status: list[int] = []

            def _record_request(request):
                if request.method == "POST" and "/api/auth/ws-ticket" in request.url:
                    try:
                        ticket_posts.append(json.loads(request.post_data or "{}"))
                    except ValueError:
                        ticket_posts.append({})

            def _record_response(response):
                if (response.request.method == "GET"
                        and f"/api/sessions/{seeded}" in response.url
                        and not response.url.endswith("/messages")):
                    detail_status.append(response.status)

            page.on("request", _record_request)
            page.on("response", _record_response)
            try:
                page.goto(base + "/sessions", wait_until="domcontentloaded",
                          timeout=30000)
                # The page opens on Overview; the per-session Resume control
                # lives on the History list.
                page.get_by_text("History", exact=True).click()
                row = page.get_by_test_id(f"session-row-{seeded}")
                row.wait_for(state="visible", timeout=30000)
                assert row.get_by_text(name, exact=False).count() == 1

                resume = row.get_by_role(
                    "button", name="Resume in Chat", exact=True)
                assert resume.count() == 1
                resume.click()

                page.wait_for_url(
                    lambda url: f"/chat?resume={seeded}" in url, timeout=30000)
                # Real UI state: the /chat right panel lights the resumed row.
                page.locator("#chat-side-panel").wait_for(
                    state="visible", timeout=30000)
                active = page.locator(
                    "#chat-side-panel [aria-current='true']")
                active.wait_for(state="visible", timeout=30000)
                assert name in active.inner_text()

                # The page verified the id through the real read-only probe...
                deadline = time.time() + 20
                while not (detail_status and spawn_calls) and time.time() < deadline:
                    page.wait_for_timeout(100)
                assert detail_status, "resume probe never read /api/sessions/<id>"
                assert detail_status[0] == 200, detail_status
                # ...minted a PTY ticket carrying the resume id...
                assert ticket_posts, "terminal never minted a PTY ticket"
                assert any(str(body.get("resume") or body.get("session") or "")
                           == seeded for body in ticket_posts), ticket_posts
                # ...and asked the server's spawn layer to resume THAT id.
                assert spawn_calls == [seeded], spawn_calls

                fatal = [e for e in errors if "favicon" not in e.lower()]
                assert not fatal, f"console/page errors: {fatal[:8]}; 404s: {missing[:8]}"
                assert not missing, f"unexpected 404s: {missing[:8]}"
            finally:
                browser.close()
    finally:
        _archive(store, seeded)
        _close_store(store)
        ui.stop()


def _install_spawn_double(ui, tmp_path):
    calls: list[str] = []

    def _spawn(cols, rows, resume, profile, workspace_cwd=""):
        calls.append(str(resume or ""))
        return _SpawnBridge(), {
            "session_id": str(resume or ""),
            "argv_head": ["node", "e2e-double"],
            "cwd": str(tmp_path),
            "home": str(tmp_path),
            "bridge": {},
        }

    ui.pty_ws._spawn = _spawn
    return calls


def _wait_for(predicate, page, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        page.wait_for_timeout(100)
    return False


def test_terminal_right_panel_recent_click_then_new_chat(tmp_path):
    """/chat right panel: recent row resumes; New chat opens a fresh terminal.

    The PTY spawn is a controlled double, so this asserts the terminal's real
    resume/fresh contract (route, active row, ticket, WS URL, spawn target)
    without booting the Ink TUI. Native-chat session *creation* is asserted by
    ``test_right_panel_recent_click_then_new_chat`` above.
    """
    ui, base, cfg = _start(tmp_path)
    name = f"E2E terminal {uuid.uuid4().hex[:8]}"
    marker = f"TERMINAL_RIGHT_PANEL_{uuid.uuid4().hex[:10]}"
    store, seeded = _seed_session(
        cfg, name, [("user", marker), ("assistant", "reply")])
    store, other = _seed_session(
        cfg, f"E2E terminal other {uuid.uuid4().hex[:8]}",
        [("user", "OTHER"), ("assistant", "OTHER_REPLY")])
    calls = _install_spawn_double(ui, tmp_path)
    try:
        token = _session_token(base)
        assert token
        with pw.sync_playwright() as p:
            browser = _launch_browser(p)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors, missing = _collect_errors(page)
            ws_urls: list[str] = []
            page.on("websocket", lambda ws: ws_urls.append(ws.url))
            try:
                page.goto(base + f"/chat?resume={other}",
                          wait_until="domcontentloaded", timeout=30000)
                panel = page.locator("#chat-side-panel")
                panel.wait_for(state="visible", timeout=30000)
                assert _wait_for(lambda: other in calls, page), calls

                # --- right-panel recent-session click resumes ---
                row = panel.get_by_text(name, exact=True).first
                row.wait_for(state="visible", timeout=30000)
                row.click()
                page.wait_for_url(
                    lambda url: f"/chat?resume={seeded}" in url, timeout=30000)
                assert _wait_for(lambda: calls[-1:] == [seeded], page), calls
                active = page.locator(
                    "#chat-side-panel [aria-current='true']")
                active.wait_for(state="visible", timeout=30000)
                assert name in active.inner_text()

                # --- right-panel New chat opens a genuinely fresh terminal ---
                before = len(ws_urls)
                panel.get_by_role("button", name="New chat", exact=True).click()
                page.wait_for_url(
                    lambda url: "resume=" not in url, timeout=30000)
                assert _wait_for(lambda: calls[-1:] == [""], page), calls
                assert _wait_for(
                    lambda: any(
                        "fresh=1" in u and "resume=" not in u
                        for u in ws_urls[before:]),
                    page,
                ), ws_urls[before:]
                # The deliberate-fresh sentinel is not a stored row, so no
                # session stays highlighted.
                page.wait_for_timeout(500)
                assert page.locator(
                    "#chat-side-panel [aria-current='true']").count() == 0

                fatal = [e for e in errors if "favicon" not in e.lower()]
                assert not fatal, f"console/page errors: {fatal[:8]}; 404s: {missing[:8]}"
                assert not missing, f"unexpected 404s: {missing[:8]}"
            finally:
                browser.close()
    finally:
        _archive(store, seeded, other)
        _close_store(store)
        ui.stop()
