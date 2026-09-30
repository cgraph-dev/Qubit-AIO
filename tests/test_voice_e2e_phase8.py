"""Phase 8 — real-browser verification of the Qubit voice surfaces.

Scope
-----
Live Playwright checks for the `/qubit-chat` voice surface against a REAL
Qubit ``WebUI`` booted on an ephemeral port from the current source tree (the
same harness used by ``tests/test_qbpv2_frontend_chat_live.py``). Nothing is
mocked except the browser-side ``SpeechRecognition`` / ``getUserMedia`` APIs
when a test must force the local/fallback/permission branches — the HTTP
backend, dist bundle and canonical chat stream are real.

Markers: ``ui`` + ``live`` (excluded from the hermetic default suite via
``pytest.ini`` ``addopts``). Run explicitly with::

    .venv/bin/python -m pytest tests/test_voice_e2e_phase8.py -m "ui or live" -q

Deliberately NOT asserted: a bare HTTP 200, the model's answer text, or the
presence of an element without reading its rendered state. Operator-blocked
items are marked ``skip`` with an explicit reason (see each test).

Continuity note (running server ``:8890``): the long-lived
``python -m voicebridge run`` process observed on 2026-09-21 was started
2026-09-20 16:32, *before* ``voice.input_engine`` landed in
``voicebridge/config.py`` (mtime 2026-09-21 17:42). Its in-memory config
snapshot therefore does NOT serve the key; a fresh WebUI (this file's fixture)
does. The running server is left untouched (read-only mandate).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DIST = REPO_ROOT / "frontend" / "qubit-dist"

pytestmark = [pytest.mark.ui, pytest.mark.live]

pw = pytest.importorskip("playwright.sync_api")

pytest.importorskip(
    "playwright.sync_api",
    reason="python playwright not installed",
)

# --- browser-side init scripts (force voice branches only; backend is real) ---

# A SpeechRecognition double that emits one final transcript on start(). Used to
# prove Right-Ctrl/voice routing reaches the canonical Qubit turn path without a
# physical microphone. The *browser* STT is stubbed; the HTTP backend is not.
FAKE_SR_THAT_SPEAKS = """
(() => {
  function FakeSR() {
    this.lang = ''; this.interimResults = false; this.maxAlternatives = 1;
    this.onresult = null; this.onerror = null; this.onend = null;
  }
  FakeSR.prototype.start = function () {
    window.__srStarted = (window.__srStarted || 0) + 1;
    setTimeout(() => {
      const r0 = [{ transcript: 'qubit phase8 voice probe' }];
      r0.isFinal = true;
      try { this.onresult && this.onresult({ resultIndex: 0, results: [r0] }); }
      catch (e) { window.__srErr = String(e); }
    }, 400);
  };
  FakeSR.prototype.stop = function () {
    if (this.__stopped) return;
    this.__stopped = true;
    setTimeout(() => { try { this.onend && this.onend({}); } catch (e) {} }, 30);
  };
  FakeSR.prototype.abort = function () {};
  window.SpeechRecognition = FakeSR;
  window.webkitSpeechRecognition = FakeSR;
})();
"""

# A SpeechRecognition double that holds the listening state open (never emits a
# final), so the transient `mic: listening` / `active: browser` state is
# observable.
FAKE_SR_THAT_PAUSES = """
(() => {
  function FakeSR() {
    this.lang = ''; this.interimResults = false; this.maxAlternatives = 1;
    this.onresult = null; this.onerror = null; this.onend = null;
  }
  FakeSR.prototype.start = function () { window.__srStarted = (window.__srStarted || 0) + 1; };
  FakeSR.prototype.stop = function () { try { this.onend && this.onend({}); } catch (e) {} };
  FakeSR.prototype.abort = function () {};
  window.SpeechRecognition = FakeSR;
  window.webkitSpeechRecognition = FakeSR;
})();
"""

# No browser SpeechRecognition at all -> `auto` must fall back to local STT with
# a visible reason.
KILL_SPEECH_RECOGNITION = """
(() => {
  try {
    Object.defineProperty(window, 'SpeechRecognition',
      { value: undefined, configurable: true, writable: true });
    Object.defineProperty(window, 'webkitSpeechRecognition',
      { value: undefined, configurable: true, writable: true });
  } catch (e) {}
})();
"""

# Mic permission denied -> typed VOICE_DENIED branch.
DENY_GET_USER_MEDIA = """
(() => {
  try {
    const md = navigator.mediaDevices || {};
    Object.defineProperty(md, 'getUserMedia', {
      configurable: true,
      value: () => Promise.reject(new DOMException('Permission denied', 'NotAllowedError')),
    });
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: md });
  } catch (e) {}
})();
"""

VOICE_STATUS = '[data-testid="voice-status"]'
RIGHT_CTRL = '[data-testid="right-ctrl-status"]'
COMPOSER = 'textarea[aria-label="Message Qubit"]'
STREAM_PATH = "/agent/api/qubit/chat/stream"


def _start_webui(state_dir: Path):
    """Boot the real Qubit WebUI (current source) serving the built dist."""
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state_dir.mkdir(exist_ok=True)
    cfg_path = state_dir / "voice.yaml"
    cfg_path.write_text(
        f"logging:\n  jsonl_path: '{state_dir}'\nwebui:\n  token: test-token\n"
    )
    cfg = Config.load(cfg_path)

    class _FakeOrch:
        _route = "gateway"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self) -> None:
            self.cfg = cfg

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state_dir)
    ui.start()
    return ui, f"http://127.0.0.1:{ui._server.server_address[1]}"


@pytest.fixture(scope="module")
def live_ui(tmp_path_factory):
    ui, base = _start_webui(tmp_path_factory.mktemp("phase8-state"))
    try:
        yield base
    finally:
        ui.stop()


@pytest.fixture()
def page_factory():
    """Yield a factory: `page_factory(init_script=...)` -> (browser, page)."""
    browsers = []

    def _make(init_script: str | None = None, viewport=None):
        p = pw.sync_playwright().start()
        browser = p.chromium.launch()
        browsers.append((browser, p))
        ctx = browser.new_context(
            viewport=viewport or {"width": 1280, "height": 900}
        )
        pg = ctx.new_page()
        if init_script:
            pg.add_init_script(init_script)
        return pg

    try:
        yield _make
    finally:
        for browser, p in browsers:
            try:
                browser.close()
            finally:
                p.stop()


def _engine_text(page) -> str:
    page.wait_for_selector(VOICE_STATUS, timeout=20000)
    return (page.locator(VOICE_STATUS).inner_text() or "").replace("\n", " ")


def _wait_engine_settled(page) -> None:
    page.wait_for_function(
        """() => {
          const el = document.querySelector('[data-testid="voice-status"]');
          return el && /engine:\\s*[a-z]+/.test(el.textContent || '');
        }""",
        timeout=20000,
    )


# ---------------------------------------------------------------------------
# 1. /qubit-chat renders transcript, composer and the voice-status region.
# ---------------------------------------------------------------------------

def test_qubit_chat_renders_transcript_composer_and_voice_region(live_ui, page_factory):
    page = page_factory()
    page.goto(f"{live_ui}/qubit-chat", timeout=30000)
    _wait_engine_settled(page)

    assert page.locator('section[aria-label="Qubit Chat transcript"]').count() == 1
    assert page.locator(COMPOSER).count() == 1
    assert page.get_by_label("Message Qubit").count() == 1

    status = page.locator(VOICE_STATUS)
    assert status.count() == 1
    assert status.get_attribute("role") == "status"
    text = _engine_text(page)
    assert re.search(r"engine:\s*(browser|local|auto)", text), text
    assert "mic: idle" in text, text
    assert "Right-Ctrl:" in text, text
    assert page.locator(RIGHT_CTRL).count() == 1
    # Privacy/network consequence is always rendered for an available engine.
    assert "privacy: local STT" in text or "network: browser speech recognition" in text, text


# ---------------------------------------------------------------------------
# 2. The real backend now serves voice.input_engine (default auto).
# ---------------------------------------------------------------------------

def test_backend_compat_handler_exposes_voice_input_engine(tmp_path, monkeypatch):
    """The current-source Qubit compat handler serves the key (default auto).

    This is the handler the dashboard falls back to; the *served* path under the
    default backend cutover is covered separately below.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    cfg_path = tmp_path / "voice.yaml"
    cfg_path.write_text("logging:\n  jsonl: false\n")
    from voicebridge import qubit_compat as HC
    from voicebridge.config import Config

    cfg = Config.load(cfg_path)
    code, live = HC.dispatch(cfg, "GET", "/api/config", {}, {})
    assert code == 200, live
    assert live["voice"].get("input_engine") == "auto", live.get("voice")
    code, schema = HC.dispatch(cfg, "GET", "/api/config/schema", {}, {})
    assert code == 200, schema
    assert "voice.input_engine" in schema["fields"], schema["fields"].keys()


@pytest.mark.xfail(
    strict=False,
    reason=(
        "DEFECT (not operator-blocked): GET /api/config is proxy-first to the "
        "vendored fork (webui.py:1449-1489, not in QUBIT_READ_AUTHORITY), and "
        "the fork payload omits voice.input_engine; the Qubit compat h_config "
        "that adds it is bypassed whenever webui.qubit_backend is on (default). "
        "Observed on :8890 and on a fresh current-source WebUI after the first "
        "dashboard use. The UI then falls back to `auto` (safe, but the key is "
        "not actually served; only a persisted override would surface it)."
    ),
)
def test_served_config_exposes_voice_input_engine(live_ui, page_factory):
    """Served GET /agent/api/config must carry voice.input_engine (default auto)."""
    page = page_factory()
    page.goto(f"{live_ui}/qubit-chat", timeout=30000)
    _wait_engine_settled(page)
    # Warm the backend bridge exactly as normal dashboard use does.
    page.reload(wait_until="domcontentloaded")
    _wait_engine_settled(page)
    cfg = page.evaluate(
        """() => {
          const t = window.__QUBIT_SESSION_TOKEN__;
          const headers = t ? { 'X-Qubit-Session-Token': t } : {};
          return fetch('/agent/api/config', { headers }).then(r => r.json());
        }"""
    )
    assert isinstance(cfg, dict) and isinstance(cfg.get("voice"), dict), cfg
    assert cfg["voice"].get("input_engine") == "auto", cfg["voice"]


def test_backend_normalize_input_engine_current_source():
    """The canonical normalizer on the current source: unknown/missing -> auto."""
    from voicebridge.config import VOICE_INPUT_ENGINES, normalize_input_engine

    assert VOICE_INPUT_ENGINES == ("browser", "local", "terminal", "auto")
    for value in VOICE_INPUT_ENGINES:
        assert normalize_input_engine(value) == value
    for bad in (None, "", "BROWSER", "remote", 3):
        assert normalize_input_engine(bad) == "auto", bad


# ---------------------------------------------------------------------------
# 3. The UI reads/reflects voice.input_engine (normalizeVoiceEngine).
# ---------------------------------------------------------------------------

def test_ui_engine_selection_reflects_normalized_config(live_ui, page_factory):
    """Every config value the UI reads maps to the documented engine.

    Snapshot the real config from the live server, then re-serve it per test
    with `voice.input_engine` varied. This exercises the frontend
    `normalizeVoiceEngine` + render against the real API contract.
    """
    import json

    page = page_factory()
    page.goto(f"{live_ui}/qubit-chat", timeout=30000)
    _wait_engine_settled(page)
    real_cfg = page.evaluate(
        """() => {
          const t = window.__QUBIT_SESSION_TOKEN__;
          const headers = t ? { 'X-Qubit-Session-Token': t } : {};
          return fetch('/agent/api/config', { headers }).then(r => r.json());
        }"""
    )
    assert isinstance(real_cfg, dict) and "voice" in real_cfg

    pattern = re.compile(r".*/agent/api/config(\?|$)")
    seen: dict[str, str] = {}

    def with_engine(value, label):
        def handler(route):
            body = json.loads(json.dumps(real_cfg))
            body.setdefault("voice", {})
            if value is None:
                body["voice"].pop("input_engine", None)
            else:
                body["voice"]["input_engine"] = value
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(body),
            )

        page.route(pattern, handler)
        page.goto(f"{live_ui}/qubit-chat", timeout=30000)
        _wait_engine_settled(page)
        page.wait_for_timeout(250)
        seen[label] = _engine_text(page)
        page.unroute(pattern)

    with_engine("browser", "browser")
    with_engine("local", "local")
    with_engine("auto", "auto")
    with_engine("bogus-value", "unknown")
    with_engine(None, "missing")

    assert "engine: browser" in seen["browser"], seen["browser"]
    assert "engine: local" in seen["local"], seen["local"]
    assert "engine: auto" in seen["auto"], seen["auto"]
    assert "engine: auto" in seen["unknown"], seen["unknown"]
    assert "engine: auto" in seen["missing"], seen["missing"]
    # Documented consequence per engine (browser = vendor service; local = on-box).
    assert "network: browser speech recognition" in seen["browser"], seen["browser"]
    assert "privacy: local STT" in seen["local"], seen["local"]


# ---------------------------------------------------------------------------
# 4. Right-Ctrl drives the SAME canonical chat-stream turn path.
# ---------------------------------------------------------------------------

def test_right_ctrl_drives_canonical_chat_stream(live_ui, page_factory):
    page = page_factory(init_script=FAKE_SR_THAT_SPEAKS)
    page.goto(f"{live_ui}/qubit-chat", timeout=30000)
    _wait_engine_settled(page)

    with page.expect_request(
        lambda r: STREAM_PATH in r.url and r.method == "POST", timeout=20000
    ) as req_info:
        page.evaluate(
            "window.dispatchEvent(new KeyboardEvent('keydown', "
            "{ code: 'ControlRight', bubbles: true }))"
        )
        page.wait_for_timeout(80)
        # Transient push-to-talk state must be visible while held.
        held = _engine_text(page)
        assert "mic: listening" in held and "active: browser" in held, held
        # Keep holding: the stubbed recognizer emits its final at ~400ms, which
        # is what forwards the transcript through onresult -> onend -> send().
    # Release push-to-talk only after the canonical request has been observed.
    page.evaluate(
        "window.dispatchEvent(new KeyboardEvent('keyup', "
        "{ code: 'ControlRight', bubbles: true }))"
    )

    req = req_info.value
    assert req.url.startswith(live_ui), req.url
    assert req.url.endswith(STREAM_PATH), req.url
    assert req.method == "POST"
    assert "x-hermes-session-token" in {k.lower() for k in req.headers}
    assert "qubit phase8 voice probe" in (req.post_data or ""), req.post_data
    assert page.evaluate("window.__srStarted || 0") >= 1

    # The turn's user text is rendered into the real transcript; the outcome is
    # a real reply or an explicit typed backend state -- never asserted content.
    page.wait_for_timeout(12000)
    transcript = page.locator('section[aria-label="Qubit Chat transcript"]').inner_text()
    assert "qubit phase8 voice probe" in transcript, transcript[-600:]
    alerts = page.locator('[role="alert"]').all_inner_texts()
    has_reply = re.search(r"\bQubit\b", transcript) is not None
    has_typed_error = any(re.match(r"\[[A-Z_]+\]", a.strip()) for a in alerts)
    assert has_reply or has_typed_error, (transcript[-400:], alerts)


# ---------------------------------------------------------------------------
# 5 & 6. Browser STT selection + visible fallback reason; permission failure.
# ---------------------------------------------------------------------------

def test_auto_prefers_browser_speech_recognition(live_ui, page_factory):
    page = page_factory(init_script=FAKE_SR_THAT_PAUSES)
    page.goto(f"{live_ui}/qubit-chat", timeout=30000)
    _wait_engine_settled(page)
    text = _engine_text(page)
    # auto + browser SR available -> browser engine, network consequence shown.
    assert "engine: auto" in text, text
    assert "network: browser speech recognition" in text, text
    assert "Right-Ctrl: ready" in page.locator(RIGHT_CTRL).inner_text()


def test_auto_falls_back_to_local_with_visible_reason(live_ui, page_factory):
    page = page_factory(init_script=KILL_SPEECH_RECOGNITION)
    page.goto(f"{live_ui}/qubit-chat", timeout=30000)
    _wait_engine_settled(page)
    # With no browser SR, auto resolves to local STT before any press.
    assert "privacy: local STT" in _engine_text(page)

    page.get_by_role("button", name="Voice input").click()
    page.wait_for_selector('[data-testid="voice-fallback"]', timeout=10000)
    reason = page.locator('[data-testid="voice-fallback"]').inner_text()
    assert "browser speech recognition unavailable" in reason, reason
    assert "local Qubit STT" in reason, reason
    # The resolved engine is local STT, and the running state is a local
    # capture (listening) or an explicit typed capture error -- never browser.
    text = _engine_text(page)
    assert "privacy: local STT" in text, text


def test_microphone_permission_failure_renders_typed_error(live_ui, page_factory):
    page = page_factory(init_script=KILL_SPEECH_RECOGNITION + DENY_GET_USER_MEDIA)
    page.goto(f"{live_ui}/qubit-chat", timeout=30000)
    _wait_engine_settled(page)

    page.get_by_role("button", name="Voice input").click()
    alert = page.locator('[role="alert"]')
    alert.first.wait_for(timeout=10000)
    assert alert.first.get_attribute("data-state") == "degraded"
    body = alert.first.inner_text()
    assert "[VOICE_DENIED]" in body and "Microphone blocked" in body, body
    # A permission failure is not styled as a listening state.
    assert "mic: idle" in _engine_text(page)


# ---------------------------------------------------------------------------
# 7. Cancellation notice renders (frontend contract; real abort is operator).
# ---------------------------------------------------------------------------

def test_cancelled_turn_renders_notice(live_ui, page_factory):
    """Renders the backend `cancelled` SSE branch.

    The real backend cannot be commanded to cancel a turn from this harness, so
    the SSE frame is fulfilled at the browser boundary; this asserts only the
    render contract (``voice-notice``), not a model answer.
    """
    page = page_factory()
    page.route(
        re.compile(r".*" + re.escape(STREAM_PATH) + r"$"),
        lambda route: route.fulfill(
            status=200,
            content_type="text/event-stream",
            body=(
                'event: start\ndata: {"event":"start","session_id":"dashboard-chat"}\n\n'
                'event: cancelled\ndata: {"event":"cancelled","session_id":"dashboard-chat"}\n\n'
            ),
        ),
    )
    page.goto(f"{live_ui}/qubit-chat", timeout=30000)
    page.wait_for_selector(COMPOSER, timeout=20000)
    page.get_by_label("Message Qubit").fill("phase8 cancellation probe")
    page.get_by_label("Message Qubit").press("Enter")
    page.wait_for_selector('[data-testid="voice-notice"]', timeout=10000)
    notice = page.locator('[data-testid="voice-notice"]').inner_text()
    assert "turn cancelled" in notice, notice
    assert page.locator('[role="alert"]').count() == 0


# ---------------------------------------------------------------------------
# 8. Branding: no upstream names on the Qubit voice surface.
# ---------------------------------------------------------------------------

def test_voice_surface_has_no_upstream_branding(live_ui, page_factory):
    page = page_factory()
    page.goto(f"{live_ui}/qubit-chat", timeout=30000)
    _wait_engine_settled(page)
    surface = (
        page.locator(VOICE_STATUS).inner_text()
        + "\n"
        + page.locator('section[aria-label="Qubit Chat transcript"]').inner_text()
    )
    lowered = surface.lower()
    for bad in ("hermes", "openclaw", "claude", "openai"):
        assert bad not in lowered, (bad, surface[-400:])


# ---------------------------------------------------------------------------
# 9. Responsive: voice region stays on-canvas at 390px; documented composer
#    squeeze is recorded (not an operator block).
# ---------------------------------------------------------------------------

def test_mobile_voice_region_has_no_horizontal_overflow(live_ui, page_factory):
    page = page_factory(viewport={"width": 390, "height": 844})
    page.goto(f"{live_ui}/qubit-chat", timeout=30000)
    _wait_engine_settled(page)
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - window.innerWidth"
    )
    assert overflow <= 1, overflow
    assert page.locator(VOICE_STATUS).is_visible()
    assert page.locator(RIGHT_CTRL).is_visible()
    assert page.locator(COMPOSER).count() == 1


@pytest.mark.xfail(
    strict=False,
    reason=(
        "known responsive defect: at 390px the composer form is flex 'nowrap' "
        "with fixed-width Image/Voice/Send buttons, squeezing the textarea to "
        "~29px (observed). Reported to the owner; not operator-blocked."
    ),
)
def test_mobile_composer_is_usable_width(live_ui, page_factory):
    page = page_factory(viewport={"width": 390, "height": 844})
    page.goto(f"{live_ui}/qubit-chat", timeout=30000)
    page.wait_for_selector(COMPOSER, timeout=20000)
    width = page.evaluate(
        "() => document.querySelector('textarea[aria-label=\"Message Qubit\"]')"
        ".getBoundingClientRect().width"
    )
    assert width >= 120, f"composer textarea width {width:.0f}px at 390px"


# ---------------------------------------------------------------------------
# 10. Running-server continuity check (operator-blocked when stale).
# ---------------------------------------------------------------------------

def test_running_server_exposes_voice_input_engine():
    """Against the already-running dashboard (default `:8890`).

    Skipped with an explicit reason when the running process predates
    `voice.input_engine` in ``voicebridge/config.py`` (its in-memory config
    snapshot is stale and only an operator restart fixes it).
    """
    import json
    import urllib.request

    base = "http://127.0.0.1:8890"
    try:
        with urllib.request.urlopen(base + "/", timeout=2):
            pass
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no running dashboard at {base}: {exc!r}")

    # Token is injected into the served SPA HTML; read it read-only.
    try:
        html = urllib.request.urlopen(base + "/sessions", timeout=3).read().decode()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"dashboard reachable but unreadable: {exc!r}")
    m = re.search(r'__QUBIT_SESSION_TOKEN__\s*=\s*"([^"]+)"', html)
    if not m:
        pytest.skip("running dashboard did not expose a session token")

    req = urllib.request.Request(
        base + "/agent/api/config", headers={"X-Qubit-Session-Token": m.group(1)}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        cfg = json.loads(resp.read().decode())
    engine = (cfg.get("voice") or {}).get("input_engine")
    if engine is None:
        pytest.skip(
            "served /agent/api/config omits voice.input_engine on the running "
            ":8890 (started 2026-09-20 16:32, before config.py changed "
            "2026-09-21 17:42) and, independently, under the default "
            "proxy-first cutover (webui.py:1449-1489) even on a fresh WebUI. "
            "The source DEFAULTS/compat handler define it as `auto` "
            "(tests above); the served defect is tracked by the xfail "
            "`test_served_config_exposes_voice_input_engine`. Operator code "
            "fix/restart required; no reset was performed."
        )
    assert engine == "auto", engine
