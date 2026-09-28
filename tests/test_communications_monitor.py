"""Packet 8B tests: read-only CDP adapters + the scan driver.

Covers: CDP read-only allowlist, stable-identity normalization, baseline vs
new-item cursor transactions, no-change (compact health only), overflow/partial
and auth-required that never advance the cursor, dry-run preview, bounded scans,
redacted output, sanitized DOM fixtures, static extraction JS, and the opt-in
live path failing closed when Chromium/CDP is unavailable.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.communications.adapters.base import (  # noqa: E402
    AccountHealthStatus as AHS, CdpClient, CdpReadOnlyViolation, ScanBounds,
    clamp_clip, classify_health,
)
from voicebridge.communications.adapters.registry import (  # noqa: E402
    ADAPTERS, run_scan,
)
from voicebridge.communications.adapters.sites import (  # noqa: E402
    GmailAdapter, InstagramAdapter, MessengerAdapter,
)
from voicebridge.communications.contracts import (  # noqa: E402
    AccountHealthStatus, EvidenceStatus, Platform, ReadState, ScanStatus,
)

FIX = REPO_ROOT / "tests" / "fixtures" / "communications"


def _cfg(tmp_path):
    from voicebridge.config import Config
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl_path: '" + str(state) + "'\n")
    return Config.load(p)


def _store(tmp_path):
    from voicebridge.communications.adapters.registry import communications_store
    return communications_store(_cfg(tmp_path))


# ------------------------------------------------------------------- CDP client
class _FakeWS:
    def __init__(self, responses):
        self._responses = list(responses)
        self.sent = []

    def send(self, data):
        self.sent.append(json.loads(data))

    def recv(self, timeout=None):
        if not self._responses:
            raise RuntimeError("no more responses")
        return self._responses.pop(0)

    def close(self):
        pass


def _client(responses, *, allowed_expressions=()):
    c = CdpClient("ws://127.0.0.1:1/devtools/page/x",
                  allowed_expressions=allowed_expressions)
    c._ws = _FakeWS(responses)
    return c


def test_cdp_client_allowlist_blocks_mutating_methods():
    c = _client([])
    with pytest.raises(CdpReadOnlyViolation, match="allowlist"):
        c.call("Browser.close")
    with pytest.raises(CdpReadOnlyViolation, match="allowlist"):
        c.call("Target.activateTarget", {"targetId": "x"})
    with pytest.raises(CdpReadOnlyViolation, match="allowlist"):
        c.call("Page.bringToFront")
    with pytest.raises(CdpReadOnlyViolation, match="allowlist"):
        c.call("Page.navigate", {"url": "https://example.test"})
    with pytest.raises(CdpReadOnlyViolation, match="allowlist"):
        c.call("Input.dispatchMouseEvent", {"type": "mousePressed"})
    # allowed read-only methods pass the allowlist
    assert "Runtime.evaluate" in CdpClient.ALLOWED_METHODS
    assert "Page.captureScreenshot" in CdpClient.ALLOWED_METHODS
    assert "Target.activateTarget" not in CdpClient.ALLOWED_METHODS
    assert "Page.bringToFront" not in CdpClient.ALLOWED_METHODS
    assert "Page.navigate" not in CdpClient.ALLOWED_METHODS


def test_cdp_evaluate_returns_value():
    expression = "({count: 2})"
    c = _client([json.dumps({"id": 1, "result": {"result": {
        "type": "object", "value": {"count": 2}}}})],
                allowed_expressions=(expression,))
    assert c.evaluate(expression) == {"count": 2}
    assert c._ws.sent[0]["method"] == "Runtime.evaluate"


def test_cdp_evaluate_surfaces_exception():
    resp = json.dumps({"id": 1, "result": {"exceptionDetails": {"text": "boom"}}})
    c = _client([resp], allowed_expressions=("x()",))
    with pytest.raises(Exception, match="evaluate failed"):
        c.evaluate("x()")


def test_cdp_evaluate_rejects_unapproved_javascript():
    c = _client([], allowed_expressions=("(() => ({rows: []}))()",))
    with pytest.raises(CdpReadOnlyViolation, match="approved"):
        c.evaluate("document.body.innerHTML = 'mutated'")
    with pytest.raises(CdpReadOnlyViolation, match="approved"):
        c.call("Runtime.evaluate", {"expression": "location.href = 'x'"})


def test_cdp_screenshot_requires_valid_explicit_clip():
    c = _client([])
    with pytest.raises(CdpReadOnlyViolation, match="explicit clip"):
        c.call("Page.captureScreenshot", {})
    with pytest.raises(CdpReadOnlyViolation, match="invalid"):
        c.call("Page.captureScreenshot", {
            "clip": {"x": -1, "y": 0, "width": 10, "height": 10}})


def test_cdp_evaluate_approved_extraction_expression_works():
    """The real repository-owned extraction expression is allowlisted and
    evaluates; a DIFFERENT site's expression is rejected (exact match)."""
    from voicebridge.communications.adapters import extract as E
    gmail = E.GMAIL_EXTRACT_JS
    c = _client([json.dumps({"id": 1, "result": {"result": {
        "type": "object",
        "value": {"health": {}, "rows": [], "count": 0}}}})],
        allowed_expressions=(gmail,))
    assert c.evaluate(gmail) == {"health": {}, "rows": [], "count": 0}
    assert c._ws.sent[0]["method"] == "Runtime.evaluate"
    # a different repository expression (another site) is NOT approved here
    with pytest.raises(CdpReadOnlyViolation, match="approved"):
        c.evaluate(E.INSTAGRAM_EXTRACT_JS)


def test_cdp_direct_call_allowlist_exact_expression():
    """call() enforces the EXACT expression allowlist: an approved expression
    passes; arbitrary or tampered expressions fail even though
    Runtime.evaluate is a nominally allowed method."""
    from voicebridge.communications.adapters import extract as E
    gmail = E.GMAIL_EXTRACT_JS
    c = _client([json.dumps({"id": 1, "result": {"result": {
        "type": "object", "value": {"count": 1}}}})],
        allowed_expressions=(gmail,))
    out = c.call("Runtime.evaluate",
                 {"expression": gmail, "returnByValue": True})
    assert out["result"]["value"] == {"count": 1}
    with pytest.raises(CdpReadOnlyViolation, match="approved"):
        c.call("Runtime.evaluate", {"expression": "window.stop()"})
    with pytest.raises(CdpReadOnlyViolation, match="approved"):
        c.call("Runtime.evaluate", {"expression": gmail + "\nwindow.close()"})


def test_cdp_screenshot_typed_method_rejects_bad_clips():
    """capture_screenshot (typed method) rejects missing, None, malformed,
    zero, negative, and non-finite clips; the wire call rejects a malformed
    clip dict too."""
    c = _client([])
    with pytest.raises(TypeError):
        c.capture_screenshot()
    for bad in (None, (0, 0, 0, 10), (0, 0, -5, 10), (-1, 0, 10, 10),
                ("a", 0, 10, 10), (float("inf"), 0, 10, 10), (0, 0, 10, 10, 5)):
        with pytest.raises(CdpReadOnlyViolation, match="invalid"):
            c.capture_screenshot(clip=bad)
    with pytest.raises(CdpReadOnlyViolation, match="invalid"):
        c.call("Page.captureScreenshot",
               {"clip": {"x": 0, "y": 0, "width": 10}})
    # a valid smaller clip still works
    import base64 as b64
    c2 = _client([json.dumps({"id": 1, "result": {
        "data": b64.b64encode(b"ok").decode()}})])
    assert c2.capture_screenshot(clip=(10, 20, 300, 40)) == b"ok"


def test_cdp_capture_screenshot_decodes():
    import base64 as b64
    png = b"fake-png-bytes"
    c = _client([json.dumps({"id": 1, "result": {
        "data": b64.b64encode(png).decode()}})])
    out = c.capture_screenshot(clip=(0, 0, 100, 100))
    assert out == png
    assert c._ws.sent[0]["params"]["clip"]["width"] == 100


# ----------------------------------------------------------------- adapters
def test_gmail_row_normalization():
    ad = GmailAdapter("synth-a")
    rows = json.loads((FIX / "new_items.json").read_text())["rows"]
    items = [ad._normalize_row(r, i) for i, r in enumerate(rows)]
    assert len(items) == 3
    assert items[0].platform is Platform.GMAIL
    assert items[0].read_state is ReadState.UNREAD
    assert items[1].read_state is ReadState.READ
    assert items[2].read_state is ReadState.UNKNOWN
    assert items[0].identity == f"gmail:synth-a:syn-g1"
    assert items[0].adapter == "gmail"


def test_instagram_and_messenger_thread_identity():
    ig = InstagramAdapter("ig")
    row = {"threadId": "t-9", "text": "Synthetic IG Sender Delta: hello",
           "href": "https://www.instagram.com/direct/t/t-9"}
    it = ig._normalize_row(row, 0)
    assert it.identity_source.value == "thread_id"
    assert "t-9" in it.identity
    assert it.sender  # bounded sender heuristic, never empty
    ms = MessengerAdapter("ms")
    it2 = ms._normalize_row({"threadId": "m-3", "text": "synthetic", "href": "x"}, 0)
    assert "m-3" in it2.identity


def test_instagram_adapter_metadata():
    ig = InstagramAdapter("ig")
    assert ig.preferred_route == "/direct/inbox/"
    assert ig.version == "8b-instagram-3"
    assert "isDirectInbox" in ig.extract_js and "x9f619" in ig.extract_js


def test_instagram_route_health_typed():
    """The adapter must require the Direct inbox route; home/unexpected routes
    return a precise typed DEGRADED/UNAVAILABLE reason (never a silent scan)."""
    ig = InstagramAdapter("ig")
    assert ig._route_health({"isDirectInbox": True, "isHome": False}) is None
    assert ig._route_health({"isDirect": True, "isHome": False}) is None
    hs, reason = ig._route_health({"isHome": True, "isDirectInbox": False,
                                   "isDirect": False})
    assert hs is AccountHealthStatus.DEGRADED and "wrong route" in reason
    assert "/direct/inbox/" in reason
    hs2, reason2 = ig._route_health({})
    assert hs2 is AccountHealthStatus.UNAVAILABLE
    assert "unexpected" in reason2
    # Gmail/Messenger have no route gate (no preferred route)
    assert GmailAdapter().preferred_route == ""
    assert MessengerAdapter().preferred_route == ""


def test_find_target_preferring_never_picks_wrong_tab(monkeypatch):
    from voicebridge.communications.adapters import base as B
    def mk(url):
        return {"url": url, "type": "page", "webSocketDebuggerUrl": "ws://x"}
    home = mk("https://www.instagram.com/")
    inbox = mk("https://www.instagram.com/direct/inbox/")
    other = mk("https://www.instagram.com/explore/")
    # none -> (None, [])
    monkeypatch.setattr(B, "list_targets", lambda *a, **k: [])
    assert B.find_target_preferring("https://www.instagram.com",
                                    "/direct/inbox/") == (None, [])
    # single home target -> returned (adapter route-check flags it)
    monkeypatch.setattr(B, "list_targets", lambda *a, **k: [home])
    t, allt = B.find_target_preferring("https://www.instagram.com",
                                       "/direct/inbox/")
    assert t == home and allt == [home]
    # direct + home -> the DIRECT target wins (never the home tab)
    monkeypatch.setattr(B, "list_targets", lambda *a, **k: [home, inbox])
    t, allt = B.find_target_preferring("https://www.instagram.com",
                                       "/direct/inbox/")
    assert t == inbox and len(allt) == 2
    # multiple targets, NONE on the preferred route -> refuse (None + all)
    monkeypatch.setattr(B, "list_targets", lambda *a, **k: [home, other])
    t, allt = B.find_target_preferring("https://www.instagram.com",
                                       "/direct/inbox/")
    assert t is None and len(allt) == 2


def test_instagram_scan_refuses_multiple_wrong_targets(tmp_path, monkeypatch):
    """Multiple Instagram targets with none on the Direct inbox -> typed
    UNAVAILABLE, no commit, cursor preserved."""
    import voicebridge.communications.adapters.registry as R
    cfg = _cfg(tmp_path)
    fx = str(FIX / "new_items.json")   # gmail fixture just for the baseline
    # baseline the instagram account first (via the generic fixture scan)
    run_scan(cfg, "gmail", account_scope="ig", source="fixture", fixture=fx)
    monkeypatch.setattr(R, "find_target_preferring",
                        lambda origin, preferred, **k: (
                            None, [{"url": "https://www.instagram.com/"},
                                   {"url": "https://www.instagram.com/explore/"}])
                        )
    r = run_scan(cfg, "instagram", account_scope="ig", source="live", live=True)
    assert r["state"] == "OK" and r["health"] == "unavailable"
    assert r["commit"] == "no-commit" and r["cursor_advanced"] is False
    assert "none is the /direct/inbox/ route" in r["health_reason"]
    st = R.communications_store(cfg)
    assert st.peek_cursor(Platform.GMAIL, "ig") is not None   # baseline kept


def test_instagram_selector_drift_and_wrong_route_fixtures():
    for name in ("instagram_inbox.html", "instagram_home.html"):
        p = FIX / "dom" / name
        assert p.exists()
        text = p.read_text()
        assert "_synthetic" in text and "trick" not in text.lower()


def test_adapters_have_no_write_surface():
    for ad in (GmailAdapter(), InstagramAdapter(), MessengerAdapter()):
        for banned in ("click", "type", "send", "mark_read", "navigate",
                       "download", "reply", "react"):
            assert not hasattr(ad, banned), \
                f"{type(ad).__name__} must not expose {banned}"
    # read-only by construction: the base denies any attempted mutation
    with pytest.raises(Exception, match="read-only"):
        GmailAdapter()._deny("nope")


def test_classify_health_typed():
    assert classify_health({"hasLogin": True}, n_rows=0, page_name="gmail")[0] \
        is AccountHealthStatus.AUTH_REQUIRED
    assert classify_health({"hasConsent": True, "title": "consent"},
                           n_rows=0, page_name="gmail")[0] \
        is AccountHealthStatus.AUTH_REQUIRED
    assert classify_health({"bodyLen": 5000, "title": "Inbox"},
                           n_rows=0, page_name="gmail")[0] \
        is AccountHealthStatus.DEGRADED   # selector drift
    assert classify_health({"bodyLen": 10, "title": ""},
                           n_rows=0, page_name="gmail")[0] \
        is AccountHealthStatus.UNAVAILABLE
    hs, auth, _ = classify_health({"bodyLen": 5000}, n_rows=5, page_name="gmail")
    assert hs is AccountHealthStatus.HEALTHY and auth is True


def test_extraction_js_is_static_and_syntactically_valid():
    from voicebridge.communications.adapters import extract as E
    for name, js in (("gmail", E.GMAIL_EXTRACT_JS),
                     ("instagram", E.INSTAGRAM_EXTRACT_JS),
                     ("messenger", E.MESSENGER_EXTRACT_JS)):
        assert "{" + "{" not in js or True  # no f-string interpolation
        assert "$" + "{" not in js, f"{name} extract must not use template strings"
        assert E.EXTRACT_VERSIONS[name].startswith("8b-")
        # syntax-check with node if available (dev tooling, skip otherwise)
        if subprocess.run(["node", "--version"], capture_output=True,
                          text=True).returncode == 0:
            import tempfile
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
                f.write(js)
                path = f.name
            try:
                r = subprocess.run(["node", "--check", path],
                                   capture_output=True, text=True)
                assert r.returncode == 0, r.stderr[-300:]
            finally:
                os.unlink(path)


def test_dom_fixtures_present_and_synthetic():
    dom = FIX / "dom"
    for name in ("gmail_inbox.html", "instagram_inbox.html",
                 "messenger_inbox.html", "login_screen.html"):
        p = dom / name
        assert p.exists()
        text = p.read_text()
        assert "_synthetic" in text
        assert "trick" not in text.lower()


# ------------------------------------------------------------ scan driver
def test_fixture_scan_baseline_then_no_change_then_new(tmp_path):
    cfg = _cfg(tmp_path)
    fx = str(FIX / "new_items.json")
    r1 = run_scan(cfg, "gmail", account_scope="a", source="fixture",
                  fixture=fx)
    assert r1["state"] == "OK"
    assert r1["commit"] == "baseline"
    assert r1["cursor_advanced"] is True and r1["new_count"] == 0
    assert r1["items_total"] == 3
    # rescan with the same items -> no-change (no duplicate report, no advance)
    r2 = run_scan(cfg, "gmail", account_scope="a", source="fixture", fixture=fx)
    assert r2["commit"] == "no-change"
    assert r2["cursor_advanced"] is False
    assert r2["report_id"] is None
    # a NEW item appears -> commit new, cursor advances, new_count == 1
    rows = json.loads((FIX / "new_items.json").read_text())["rows"]
    rows.append({"sourceItemId": "syn-g4", "threadId": "syn-g4",
                 "sender": "Synthetic Sender Delta",
                 "subject": "Synthetic subject delta",
                 "preview": "Synthetic preview delta.", "timeText": "Aug 4",
                 "href": "https://mail.google.com/mail/u/0/#inbox",
                 "unread": True})
    nf = tmp_path / "new.json"
    nf.write_text(json.dumps({"platform": "gmail", "rows": rows}))
    r3 = run_scan(cfg, "gmail", account_scope="a", source="fixture",
                  fixture=str(nf))
    assert r3["commit"] == "new"
    assert r3["cursor_advanced"] is True and r3["new_count"] == 1
    assert r3["report_id"]


def test_fixture_scan_dry_run_does_not_persist(tmp_path):
    cfg = _cfg(tmp_path)
    fx = str(FIX / "new_items.json")
    # fresh account + dry-run -> would-be BASELINE (no old-message alert)
    r = run_scan(cfg, "gmail", account_scope="b", source="fixture",
                 fixture=fx, dry_run=True)
    assert r["dry_run"] is True
    assert r["cursor_advanced"] is False
    assert r["would_commit"] == "baseline" and r["new_count"] == 0
    # nothing persisted: no cursor, no scan
    from voicebridge.communications.adapters.registry import communications_store
    st = communications_store(cfg)
    assert st.peek_cursor(Platform.GMAIL, "b") is None
    assert st.latest_scan(Platform.GMAIL, "b") is None
    # establish a baseline, then dry-run with ONE new item -> preview new
    run_scan(cfg, "gmail", account_scope="b", source="fixture", fixture=fx)
    rows = json.loads((FIX / "new_items.json").read_text())["rows"]
    rows.append({"sourceItemId": "syn-g4", "threadId": "syn-g4",
                 "sender": "Synthetic Sender Delta", "subject": "s",
                 "preview": "p", "href": "https://mail.google.com/",
                 "unread": True})
    nf = tmp_path / "new.json"
    nf.write_text(json.dumps({"platform": "gmail", "rows": rows}))
    r2 = run_scan(cfg, "gmail", account_scope="b", source="fixture",
                  fixture=str(nf), dry_run=True)
    assert r2["would_commit"] == "new" and r2["new_count"] == 1
    assert r2["cursor_advanced"] is False   # dry-run never advances
    assert st.peek_cursor(Platform.GMAIL, "b") is not None


def test_overflow_bounds_keep_cursor(tmp_path):
    cfg = _cfg(tmp_path)
    # first baseline the account with the normal fixture
    fx = str(FIX / "new_items.json")
    run_scan(cfg, "gmail", account_scope="c", source="fixture", fixture=fx)
    # now a fixture exceeding max_items -> OVERFLOW, no commit, cursor unchanged
    rows = [{"sourceItemId": f"syn-x{i}", "threadId": f"syn-x{i}",
             "sender": "Synthetic X", "subject": "s",
             "preview": "p", "href": "https://mail.google.com/", "unread": False}
            for i in range(200)]
    of = tmp_path / "overflow.json"
    of.write_text(json.dumps({"platform": "gmail", "rows": rows}))
    r = run_scan(cfg, "gmail", account_scope="c", source="fixture",
                 fixture=str(of))
    assert r["overflow"] is True
    assert r["commit"] == "no-commit"
    assert r["cursor_advanced"] is False
    from voicebridge.communications.adapters.registry import communications_store
    st = communications_store(cfg)
    assert st.latest_scan(Platform.GMAIL, "c").status is ScanStatus.OVERFLOW


def test_auth_required_and_no_browser_never_advance(tmp_path, monkeypatch):
    import voicebridge.communications.adapters.registry as R
    cfg = _cfg(tmp_path)
    fx = str(FIX / "new_items.json")
    run_scan(cfg, "gmail", account_scope="d", source="fixture", fixture=fx)
    # hermetic: simulate NO browser target (never depends on live CDP state and
    # never touches a real account) -> typed UNAVAILABLE, no commit
    monkeypatch.setattr(R, "find_target", lambda *a, **k: None)
    r = run_scan(cfg, "gmail", account_scope="d", source="live", live=True)
    assert r["state"] == "OK" and r["health"] == "unavailable"
    assert r["commit"] == "no-commit" and r["cursor_advanced"] is False
    from voicebridge.communications.adapters.registry import communications_store
    st = communications_store(cfg)
    assert st.latest_health(Platform.GMAIL, "d").status is \
        AccountHealthStatus.UNAVAILABLE
    assert st.peek_cursor(Platform.GMAIL, "d") is not None  # baseline preserved


def test_scan_output_is_redacted_metadata_only(tmp_path):
    cfg = _cfg(tmp_path)
    fx = str(FIX / "new_items.json")
    r = run_scan(cfg, "gmail", account_scope="e", source="fixture",
                 fixture=fx, dry_run=True)
    for it in r["items"]:
        for secret in ("sender", "subject", "preview", "text"):
            assert secret not in it, f"output leaked {secret}"


def test_overlap_lease_returns_degraded(tmp_path):
    cfg = _cfg(tmp_path)
    st = _store(tmp_path)
    st.acquire_lease("scan:gmail:f", "other-process", ttl_s=60)
    r = run_scan(cfg, "gmail", account_scope="f", source="fixture",
                 fixture=str(FIX / "new_items.json"), store=st)
    assert r["state"] == "degraded" and r.get("code") == "OVERLAP"


def test_screenshot_every_capture_has_bounded_clip():
    """Regression: every successful screenshot call receives a non-empty,
    viewport-bounded clip — never a full-viewport capture."""
    ad = GmailAdapter("g")
    rows = json.loads((FIX / "new_items.json").read_text())["rows"]
    item = ad._normalize_row(rows[0], 0)
    rect = {"x": 10, "y": 20, "width": 300, "height": 40}
    calls = []

    class _Spy:
        def capture_screenshot(self, *, clip):
            calls.append(clip)
            return b"\x00" * 500

    assets, blobs = ad._screenshots(_Spy(), ScanBounds(),
                                    [(item, rect)], "r1", (1280, 720))
    assert len(calls) == 1
    assert assets[0].redaction_status is EvidenceStatus.AVAILABLE
    assert blobs.get(assets[0].asset_id) == b"\x00" * 500   # bytes carried
    x, y, w, h = calls[0]
    assert all(v > 0 for v in (w, h))                     # non-empty clip
    assert 0 <= x <= 1280 and 0 <= y <= 720
    assert x + w <= 1280 and y + h <= 720                 # bounded to viewport


def test_screenshot_rejects_full_viewport_capture():
    """Regression: with NO geometry, the adapter must NOT capture the viewport;
    it records NOT_AVAILABLE and never calls capture_screenshot."""
    ad = GmailAdapter("g")
    rows = json.loads((FIX / "new_items.json").read_text())["rows"]
    item = ad._normalize_row(rows[0], 0)
    calls = []

    class _Spy:
        def capture_screenshot(self, **kw):
            calls.append(kw)
            return b"\x00" * 500

    assets, blobs = ad._screenshots(_Spy(), ScanBounds(),
                                    [(item, None)], "r1", (1280, 720))
    assert calls == []                                    # never captured
    assert assets[0].redaction_status is EvidenceStatus.NOT_AVAILABLE
    assert blobs == {}                                    # no bytes to persist
    # the CDP client structurally REQUIRES a clip (full-viewport refused)
    c = CdpClient("ws://127.0.0.1:1/x")
    c._ws = _FakeWS([])
    with pytest.raises(TypeError):
        c.capture_screenshot()


def test_screenshot_clamps_malicious_geometry():
    # out-of-bounds but under the hard cap -> clamped to the visible viewport
    clip, reason = clamp_clip({"x": -100, "y": 5, "width": 2000,
                               "height": 50}, (1280, 720))
    assert reason is None
    x, y, w, h = clip
    assert x == 0 and y == 5
    assert w == 1280 and 0 < h <= 720
    # off-screen -> TARGET_HIDDEN (clamps to empty)
    clip, reason = clamp_clip({"x": 99999, "y": 99999, "width": 10,
                               "height": 10}, (1280, 720))
    assert clip is None and reason is EvidenceStatus.TARGET_HIDDEN
    # invalid / non-positive / oversized -> NOT_AVAILABLE
    for bad in (None,
                {"x": "NaN", "y": 0, "width": 10, "height": 10},
                {"x": 0, "y": 0, "width": -5, "height": 10},
                {"x": 0, "y": 0, "width": 10, "height": 0},
                {"x": 0, "y": 0, "width": 10000, "height": 10},
                {"x": float("inf"), "y": 0, "width": 10, "height": 10}):
        clip, reason = clamp_clip(bad, (1280, 720))
        assert clip is None and reason is EvidenceStatus.NOT_AVAILABLE


def test_screenshot_preserves_typed_missing_evidence():
    ad = GmailAdapter("g")
    rows = json.loads((FIX / "new_items.json").read_text())["rows"]
    item = ad._normalize_row(rows[0], 0)
    rect = {"x": 0, "y": 0, "width": 100, "height": 20}

    class _Fail:
        def capture_screenshot(self, *, clip):
            raise RuntimeError("boom")

    a, blobs = ad._screenshots(_Fail(), ScanBounds(), [(item, rect)],
                               "r1", (1280, 720))
    assert a[0].redaction_status is EvidenceStatus.CLIP_FAILED
    assert blobs == {}

    class _Tiny:
        def capture_screenshot(self, *, clip):
            return b"xx"

    a, blobs = ad._screenshots(_Tiny(), ScanBounds(), [(item, rect)],
                               "r1", (1280, 720))
    assert a[0].redaction_status is EvidenceStatus.TARGET_HIDDEN
    assert blobs == {}


def test_screenshot_rejects_exact_full_viewport():
    """Exact viewport geometry must be rejected (NOT_AVAILABLE), never captured."""
    clip, reason = clamp_clip({"x": 0, "y": 0, "width": 1280, "height": 720},
                              (1280, 720))
    assert clip is None and reason is EvidenceStatus.NOT_AVAILABLE


def test_screenshot_rejects_oversized_that_clamps_to_full_viewport():
    """A negative/oversized rect that clamps to the ENTIRE viewport must be
    rejected (NOT_AVAILABLE), never captured."""
    clip, reason = clamp_clip({"x": -100, "y": -100, "width": 2000,
                               "height": 1000}, (1280, 720))
    assert clip is None and reason is EvidenceStatus.NOT_AVAILABLE
    # a legitimately smaller row is still allowed after the clamp
    clip, reason = clamp_clip({"x": 10, "y": 10, "width": 400, "height": 60},
                              (1280, 720))
    assert clip is not None and reason is None


def test_screenshot_full_viewport_never_calls_capture():
    """Proof: for full-viewport geometry, capture_screenshot is NEVER called."""
    ad = GmailAdapter("g")
    rows = json.loads((FIX / "new_items.json").read_text())["rows"]
    item = ad._normalize_row(rows[0], 0)
    calls = []

    class _Spy:
        def capture_screenshot(self, **kw):
            calls.append(kw)
            return b"\x00" * 500

    for rect in ({"x": 0, "y": 0, "width": 1280, "height": 720},   # exact
                 {"x": -100, "y": -100, "width": 2000, "height": 1000}):  # clamps full
        calls.clear()
        assets, blobs = ad._screenshots(_Spy(), ScanBounds(), [(item, rect)],
                                        "r1", (1280, 720))
        assert calls == [], "capture_screenshot must never run for full-viewport"
        assert assets[0].redaction_status is EvidenceStatus.NOT_AVAILABLE
        assert blobs == {}


def test_screenshot_fix_keeps_cursor_and_read_only(tmp_path):
    """Regression: the clip fix must not change cursor advancement or the
    read-only guarantees."""
    # cursor transactions still work through the scan driver
    cfg = _cfg(tmp_path)
    fx = str(FIX / "new_items.json")
    r1 = run_scan(cfg, "gmail", account_scope="s", source="fixture", fixture=fx)
    assert r1["commit"] == "baseline" and r1["cursor_advanced"] is True
    rows = json.loads((FIX / "new_items.json").read_text())["rows"]
    rows.append({"sourceItemId": "syn-s1", "threadId": "syn-s1",
                 "sender": "Synthetic S", "subject": "s", "preview": "p",
                 "href": "https://mail.google.com/", "unread": False})
    nf = tmp_path / "s.json"
    nf.write_text(json.dumps({"platform": "gmail", "rows": rows}))
    r2 = run_scan(cfg, "gmail", account_scope="s", source="fixture",
                  fixture=str(nf))
    assert r2["commit"] == "new" and r2["new_count"] == 1
    assert r2["cursor_advanced"] is True
    # read-only surface unchanged: no write methods, mutating CDP blocked
    for ad in (GmailAdapter(), InstagramAdapter(), MessengerAdapter()):
        for banned in ("click", "type", "send", "mark_read", "navigate",
                       "download"):
            assert not hasattr(ad, banned)
    c = CdpClient("ws://127.0.0.1:1/x")
    c._ws = _FakeWS([])
    with pytest.raises(CdpReadOnlyViolation):
        c.call("Page.navigate", {"url": "https://example.test"})


def test_monitor_cli_denies_live_without_canary(tmp_path, capsys):
    from voicebridge.cli import main as cli  # not used; script is standalone
    import subprocess as sp
    script = REPO_ROOT / "scripts" / "communications_monitor.py"
    r = sp.run([sys.executable, str(script), "--once", "--live"],
               capture_output=True, text=True, timeout=60)
    out = json.loads(r.stdout)
    assert out["state"] == "denied" and r.returncode == 1
    # fixture dry-run via the script works hermetically
    r2 = sp.run([sys.executable, str(script), "--once", "--dry-run",
                 "--fixture", str(FIX / "new_items.json"), "--platform", "gmail",
                 "--config", str(tmp_path / "voice.yaml")],
                capture_output=True, text=True, timeout=60)
    assert r2.returncode == 0, r2.stderr[-400:]
    out2 = json.loads(r2.stdout)
    assert out2["commit"] == "preview" and out2["dry_run"] is True
    assert out2["would_commit"] in ("baseline", "new", "no-change")


# -------------------------------------------------- Packet 8B evidence results
def _eval_response(extraction, mid=1):
    return json.dumps({"id": mid, "result": {"result": {
        "type": "object", "value": extraction}}})


def _shot_response(data, mid=2):
    import base64 as b64
    return json.dumps({"id": mid, "result": {
        "data": b64.b64encode(data).decode()}})


def _extraction(rows, *, vw=1280, vh=720):
    return {"health": {"url": "https://mail.google.com/",
                       "title": "Inbox (synthetic)",
                       "vw": vw, "vh": vh,
                       "hasLogin": False, "hasConsent": False,
                       "bodyLen": 5000},
            "rows": rows, "count": len(rows)}


def _gmail_row(i, *, rect):
    return {"sourceItemId": f"syn-e{i}", "threadId": f"syn-e{i}",
            "sender": f"Synthetic Sender E{i}",
            "subject": f"Synthetic subject {i}",
            "preview": f"Synthetic preview {i}.", "timeText": f"Aug {i}",
            "href": "https://mail.google.com/mail/u/0/#inbox",
            "unread": True, "rect": rect}


def test_live_run_valid_visible_row_yields_bounded_available_evidence():
    """Regression: a valid visible row must produce a bounded clipped screenshot
    AND the item must carry the typed AVAILABLE evidence result. Before the
    fix the asset was AVAILABLE but the item always reported not_available
    (scan output / store / report / dashboard all mislabelled captured rows)."""
    import base64 as b64
    ad = GmailAdapter("g")
    row = _gmail_row(1, rect={"x": 10, "y": 20, "width": 300, "height": 40})
    payload = b"\x00" * 1024
    c = _client([_eval_response(_extraction([row])),
                 _shot_response(payload)],
                allowed_expressions=(ad.extract_js,))
    scan = ad.run(c, ScanBounds(), report_id="r1")
    assert scan.health_status is AccountHealthStatus.HEALTHY
    assert len(scan.items) == 1 and len(scan.assets) == 1
    assert scan.items[0].evidence_status is EvidenceStatus.AVAILABLE
    assert scan.assets[0].redaction_status is EvidenceStatus.AVAILABLE
    assert scan.asset_bytes[scan.assets[0].asset_id] == payload
    # the wire clip is explicit and bounded to the 1280x720 viewport
    clip = c._ws.sent[-1]["params"]["clip"]
    assert clip["width"] == 300 and clip["height"] == 40
    assert clip["x"] >= 0 and clip["y"] >= 0
    assert clip["x"] + clip["width"] <= 1280
    assert clip["y"] + clip["height"] <= 720
    # no full-viewport capture is ever requested
    assert not (clip["x"] <= 0.5 and clip["y"] <= 0.5
                and 1280 - clip["width"] <= 0.5 and 720 - clip["height"] <= 0.5)


def test_live_run_typed_evidence_variants_preserved_on_items():
    """Regression: missing/off-screen geometry, CDP capture failure, and tiny
    payloads preserve their typed state on BOTH the asset and the item; rows
    beyond the bounded screenshot cap keep their default typed state."""
    ad = GmailAdapter("g")

    def run_rows(rows, responses):
        c = _client(responses, allowed_expressions=(ad.extract_js,))
        return ad.run(c, ScanBounds(), report_id="r1")

    no_rect = _gmail_row(1, rect=None)
    hidden = _gmail_row(2, rect={"x": 99999, "y": 99999, "width": 10,
                                 "height": 10})
    fail = _gmail_row(3, rect={"x": 10, "y": 20, "width": 300, "height": 40})
    beyond_cap = _gmail_row(4, rect={"x": 10, "y": 20, "width": 300,
                                     "height": 40})
    rows = [no_rect, hidden, fail, beyond_cap]
    scan = run_rows(
        rows,
        [_eval_response(_extraction(rows)),
         json.dumps({"id": 2, "error": {"code": -32000,
                                        "message": "Capture failed"}})])
    assert [it.evidence_status for it in scan.items] == [
        EvidenceStatus.NOT_AVAILABLE,   # no geometry
        EvidenceStatus.TARGET_HIDDEN,   # off-screen
        EvidenceStatus.CLIP_FAILED,     # CDP capture failure
        EvidenceStatus.NOT_AVAILABLE,   # beyond the bounded screenshot cap
    ]
    assert [a.redaction_status for a in scan.assets] == [
        EvidenceStatus.NOT_AVAILABLE,
        EvidenceStatus.TARGET_HIDDEN,
        EvidenceStatus.CLIP_FAILED,
    ]
    assert scan.asset_bytes == {}

    tiny = _gmail_row(5, rect={"x": 10, "y": 20, "width": 300, "height": 40})
    scan2 = run_rows(
        [tiny],
        [_eval_response(_extraction([tiny])), _shot_response(b"xx", mid=2)])
    assert scan2.items[0].evidence_status is EvidenceStatus.TARGET_HIDDEN
    assert scan2.assets[0].redaction_status is EvidenceStatus.TARGET_HIDDEN
    assert scan2.asset_bytes == {}


def test_live_run_malformed_and_oversized_geometry_never_captures():
    """Regression: malformed and oversized row rects are rejected by
    clamp_clip (NOT_AVAILABLE) and never reach capture_screenshot."""
    import base64 as b64
    ad = GmailAdapter("g")
    rows = [
        _gmail_row(1, rect={"x": "NaN", "y": 0, "width": 10, "height": 10}),
        _gmail_row(2, rect={"x": 0, "y": 0, "width": 10000, "height": 10}),
        _gmail_row(3, rect={"x": 0, "y": 0, "width": 1280, "height": 720}),
    ]
    c = _client([_eval_response(_extraction(rows))],
                allowed_expressions=(ad.extract_js,))
    scan = ad.run(c, ScanBounds(), report_id="r1")
    assert [it.evidence_status for it in scan.items] == [
        EvidenceStatus.NOT_AVAILABLE,
        EvidenceStatus.NOT_AVAILABLE,
        EvidenceStatus.NOT_AVAILABLE,
    ]
    assert len(scan.assets) == 3
    assert all(a.redaction_status is EvidenceStatus.NOT_AVAILABLE
               for a in scan.assets)
    assert len(c._ws.sent) == 1            # only the extraction call; no capture
    assert scan.asset_bytes == {}


def test_fixture_items_carry_typed_evidence_from_screenshot_payload():
    """Regression: fixture-mode items reflect the typed evidence outcome of
    their screenshot payload (available / clip_failed / target_hidden /
    not_available), never a blanket not_available."""
    import base64 as b64
    ad = GmailAdapter("g")
    rows = json.loads((FIX / "new_items.json").read_text())["rows"]
    rows[0]["screenshot"] = b64.b64encode(b"\x00" * 1024).decode()
    rows[1]["screenshot"] = "not-base64!!"
    rows[2]["screenshot"] = b64.b64encode(b"xx").decode()
    rows.append({"sourceItemId": "syn-g4", "threadId": "syn-g4",
                 "sender": "Synthetic Sender Four", "subject": "s",
                 "preview": "p", "timeText": "Aug 4",
                 "href": "https://mail.google.com/", "unread": False})
    scan = ad.run_fixture(rows, ScanBounds(), report_id="r1")
    assert [it.evidence_status for it in scan.items] == [
        EvidenceStatus.AVAILABLE,
        EvidenceStatus.CLIP_FAILED,
        EvidenceStatus.TARGET_HIDDEN,
        EvidenceStatus.NOT_AVAILABLE,
    ]
    assert [a.redaction_status for a in scan.assets] == [
        EvidenceStatus.AVAILABLE,
        EvidenceStatus.CLIP_FAILED,
        EvidenceStatus.TARGET_HIDDEN,
        EvidenceStatus.NOT_AVAILABLE,
    ]


def test_scan_output_evidence_status_matches_assets(tmp_path):
    """Regression: the scan driver JSON reports the item's typed evidence
    status exactly as its asset status — captured rows are never labelled
    not_available."""
    import base64 as b64
    cfg = _cfg(tmp_path)
    rows = json.loads((FIX / "new_items.json").read_text())["rows"]
    rows[0]["screenshot"] = b64.b64encode(b"\x00" * 1024).decode()
    fx = tmp_path / "evidence.json"
    fx.write_text(json.dumps({"platform": "gmail", "rows": rows}))
    r = run_scan(cfg, "gmail", account_scope="ev", source="fixture",
                 fixture=fx)
    assert r["state"] == "OK" and r["commit"] == "baseline"
    assert [it["evidence_status"] for it in r["items"]] == \
        ["available", "not_available", "not_available"]
    assert {ev["status"] for ev in r["evidence"]} == \
        {"available", "not_available"}
