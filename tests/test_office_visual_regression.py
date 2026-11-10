"""Opt-in visual goldens for the Qubit-owned Office chrome.

WHAT THIS PINS
--------------
The theme upgrade changed the parts of the office page Qubit owns: the Agents
dock (including the dialogue-style agent card and its focus ring) and the two
icons injected into the embedded app's own Panels group. Those are visual
contracts, so they need pixel evidence — but the purchased LimeZu art must never
be committed, and a live office is not deterministic.

This probe therefore boots a hermetic loopback ``WebUI`` and stubs only the
data seams: ``/agent/api/office/tower``, ``/api/stream`` and the agents-panel
route return fixed JSON. The theme is a **synthetic** pack generated in a
throwaway ``$QUBIT_HOME`` (solid placeholder PNGs plus Qubit's own tokens), and
the embedded app document is a minimal same-origin shell carrying the real
``.ui-root`` + ``.ui-btn-group[aria-label="Panels"]`` contract that the menu
injector targets. No purchased art, no live service, no operator session.

Playwright runs Chromium at 1440x900 with DPR 1 and 2, animations/transitions
off, the caret hidden and a frozen clock. Element screenshots are compared to
the committed baselines in ``tests/fixtures/office_visual/`` with a small
per-pixel threshold.

RUNNING
-------
    QUBIT_OFFICE_VISUAL=1 python -m pytest tests/test_office_visual_regression.py -q

Baselines are only written by an explicit developer command, never by the test:

    python tests/test_office_visual_regression.py --write

If Playwright or a browser is unavailable, the probe skips with the reason.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (REPO_ROOT / "qubit_runtime", REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

BASELINE_DIR = Path(__file__).resolve().parent / "fixtures" / "office_visual"
VISUAL_GATE = "QUBIT_OFFICE_VISUAL"

pytestmark = pytest.mark.skipif(
    os.environ.get(VISUAL_GATE) != "1",
    reason=f"opt-in visual probe; set {VISUAL_GATE}=1 to run",
)

TOKEN = "office-visual-token"
THEME_ID = "demo"
VIEWPORT = {"width": 1440, "height": 900}
#: Fraction of pixels allowed to differ; anti-aliasing and font hinting are not
#: identical across hosts, but a layout or token regression moves far more.
MAX_DIFF_RATIO = 0.003
#: A pixel counts as different when any RGB channel moves by more than this.
PIXEL_THRESHOLD = 12

VISUAL_TOKENS = {
    "color-surface": "#101828",
    "color-surface-soft": "#141d30",
    "color-surface-raised": "#182236",
    "color-surface-solid": "#182236",
    "color-surface-hover": "#223049",
    "color-border": "#64748b",
    "color-border-strong": "#94a3b8",
    "color-text-primary": "#e9edf7",
    "color-text-muted": "#93a3bb",
    "color-text-faint": "#7b8598",
    "color-accent": "#7cc8ff",
    "color-accent-soft": "#7cc8ff",
    "radius-panel": "8px",
    "radius-control": "4px",
    "color-status-working": "#38bdf8",
    "color-status-waiting": "#fbbf24",
    "color-status-done": "#4ade80",
    "color-status-idle": "#94a3b8",
    "color-status-offline": "#ef4444",
    "color-status-shell": "#a78bfa",
    "color-level-ok": "#22c55e",
    "color-level-warn": "#f59e0b",
    "color-level-crit": "#ef4444",
}


# --------------------------------------------------------------------------
# Synthetic theme fixture (colors + placeholder PNGs, generated in tmp)
# --------------------------------------------------------------------------
def _write_synthetic_pack(home: Path) -> Path:
    from PIL import Image

    root = home / "office" / "themes" / THEME_ID
    root.mkdir(parents=True, exist_ok=True)

    def _write(relative: str, data: bytes) -> dict:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {"sha256": __import__("hashlib").sha256(data).hexdigest(),
                "bytes": len(data)}

    portrait = root / "portraits" / "atlas32.png"
    portrait.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (64, 32), (0, 0, 0, 0))
    image.paste((124, 200, 255, 255), (0, 0, 32, 32))
    image.paste((233, 237, 247, 255), (8, 8, 24, 24))
    image.paste((251, 191, 36, 255), (32, 0, 64, 32))
    image.save(portrait, format="PNG")

    body = root / "bodies" / "atlas32.png"
    body.parent.mkdir(parents=True, exist_ok=True)
    body_image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    body_image.paste((124, 200, 255, 255), (0, 0, 32, 64))
    body_image.paste((34, 48, 73, 255), (8, 24, 24, 56))
    body_image.save(body, format="PNG")

    slice_img = root / "ui" / "card.png"
    slice_img.parent.mkdir(parents=True, exist_ok=True)
    card = Image.new("RGBA", (16, 16), (124, 200, 255, 255))
    card.paste((24, 34, 54, 255), (5, 5, 11, 11))
    card.save(slice_img, format="PNG")

    files = {
        "portraits/atlas32.png": _write("portraits/atlas32.png",
                                       portrait.read_bytes()),
        "bodies/atlas32.png": _write("bodies/atlas32.png", body.read_bytes()),
        "ui/card.png": _write("ui/card.png", slice_img.read_bytes()),
        "portraits/index.json": _write(
            "portraits/index.json",
            json.dumps({"schema": 1, "size": 32, "cols": 2, "rows": 1,
                        "cells": {"ceo": [0, 0], "worker": [1, 0]}}).encode()),
        "bodies/index.json": _write(
            "bodies/index.json",
            json.dumps({"schema": 1, "size": 32, "frame": {"w": 32, "h": 64},
                        "cols": 2, "rows": 1,
                        "cells": {"ceo": [0, 0], "worker": [1, 0]}}).encode()),
        "agents.json": _write(
            "agents.json",
            json.dumps({"schema": 1, "templateVersion": 1,
                        "recipes": {
                            "ceo": {"skin": 0, "eyes": 0, "hair": 0,
                                    "outfit": 0, "accessory": 0},
                            "worker": {"skin": 0, "eyes": 0, "hair": 0,
                                       "outfit": 0, "accessory": 0},
                        }}).encode()),
    }
    manifest = {
        "format": "qubit-office-theme",
        "schema": 1,
        "id": THEME_ID,
        "label": "Synthetic",
        "version": "1.0.0",
        "credit": "LimeZu — https://limezu.itch.io",
        "license": "LimeZu licence; redistribution prohibited",
        "source": "synthetic fixture generated by the visual probe",
        "template_version": 1,
        "tokens": VISUAL_TOKENS,
        "slices": {
            "card": {"src": "ui/card.png", "slice": [6, 6, 6, 6],
                     "width": "6px", "repeat": "stretch"},
        },
        "files": files,
        "limits": {"max_total_bytes": 10_000_000, "max_files": 20},
        "portraits": {"atlas": "portraits/atlas32.png",
                      "index": "portraits/index.json",
                      "size": 32, "cols": 2, "rows": 1},
        "bodies": {"atlas": "bodies/atlas32.png",
                   "index": "bodies/index.json",
                   "size": 32, "frame": {"w": 32, "h": 64},
                   "cols": 2, "rows": 1},
        "parts": {"skin": 1, "eyes": 1, "hair": 1, "outfit": 1,
                  "accessory": 1},
    }
    (root / "theme.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


# --------------------------------------------------------------------------
# Fixed data seams
# --------------------------------------------------------------------------
def _seat(slug: str, name: str, division: str, seat_type: str,
          portrait=(0, 0), body=(0, 0), room="boardroom") -> dict:
    return {
        "slug": slug,
        "name": name,
        "seat_type": seat_type,
        "supervisor": None,
        "division": division,
        "room": room,
        "emoji": "",
        "color": "#7cc8ff",
        "portrait": list(portrait),
        "body": list(body),
    }


def _panel_payload() -> dict:
    boardroom = [_seat("ceo", "Ada Chair", "", "ceo",
                       portrait=(0, 0), body=(0, 0))]
    engineering = [
        _seat("worker-one", "Bo Builder", "engineering", "worker",
              portrait=(1, 0), body=(1, 0), room="engineering"),
        _seat("worker-two", "Cy Coder", "engineering", "worker",
              portrait=(0, 0), body=(0, 0), room="engineering"),
    ]
    seat_types: Dict[str, int] = {}
    for row in boardroom + engineering:
        seat_types[row["seat_type"]] = seat_types.get(row["seat_type"], 0) + 1
    return {
        "ok": True,
        "schema": "qubit-office-agents-panel/1",
        "counts": {
            "headcount": 3,
            "divisions": 2,
            "roster_divisions": 1,
            "rooms": 2,
            "floors": 1,
            "boardroom_seats": 1,
            "seat_types": seat_types,
            "per_division": {"": 1, "engineering": 2},
        },
        "divisions": [
            {"slug": "", "label": "Boardroom (executive tier)",
             "kind": "boardroom", "floor": 1, "headcount": 1,
             "seats": boardroom},
            {"slug": "engineering", "label": "Engineering",
             "kind": "division", "floor": 2, "headcount": 2,
             "seats": engineering},
        ],
        "seats": boardroom + engineering,
        "detail": "",
    }


def _tower_payload() -> dict:
    characters = [
        {"agent_id": 1, "slug": "ceo"},
        {"agent_id": 2, "slug": "worker-one"},
        {"agent_id": 3, "slug": "worker-two"},
    ]
    return {
        "ok": True,
        "schema": "qubit-office-tower/1",
        "now": 1762732800,
        "active_window_s": 0,
        "walk_s": 0,
        "empty": False,
        "empty_reason": "",
        "hud": {
            "headcount": 3,
            "revenue_cents": 0,
            "cost_cents": 0,
            "net_cents": 0,
            "burn_cents_per_day": 0,
            "runway_days": None,
            "runway_is_derived": False,
            "floors": [],
            "survival": {"floor_days": 0},
            "pnl_source": "fixture",
        },
        "floorplan": {
            "schema": "qubit-office-floorplan/1",
            "grid": {"cols": 8, "rows": 8, "tile": 32},
            "floors": [
                {"id": 1, "number": 1, "name": "Ground", "role": "ops",
                 "theme": "classic", "z": 0,
                 "rooms": [
                     {"id": 1, "slug": "boardroom", "kind": "boardroom",
                      "department_id": 0, "x": 0, "y": 0, "w": 4, "h": 4,
                      "seats": []},
                     {"id": 2, "slug": "engineering", "kind": "division",
                      "department_id": 1, "x": 4, "y": 0, "w": 4, "h": 4,
                      "seats": []},
                 ]},
            ],
        },
        "characters": characters,
        "activity": [],
        "history": [],
        "history_totals": {"revenue_cents": 0, "cost_cents": 0,
                           "net_cents": 0},
        "grid": {"cols": 8, "rows": 8, "tile": 32},
        "motion_rule": "fixture",
        "read_only": True,
        "preview": False,
    }


# --------------------------------------------------------------------------
# React UMD shim and the two documents
# --------------------------------------------------------------------------
def _umd_source(which: str) -> bytes:
    base = REPO_ROOT / "frontend" / "qubit-web" / "node_modules"
    shim = (
        "var __defs=Object.create(null),__cache=Object.create(null);"
        "function __load(n){ n=String(n);"
        " if(__cache[n]) return __cache[n].exports;"
        " if(!__defs[n]) throw new Error('no '+n);"
        " var m=__cache[n]={exports:{}}; __defs[n].call(m.exports,m,m.exports); return m.exports; }"
        "function require(n){"
        " if(n==='react'&&window.React) return window.React;"
        " if(n==='react-dom'&&window.ReactDOM) return window.ReactDOM;"
        " return __load(n); }"
    )
    parts = ["react"] if which == "react.js" else [
        "react", "scheduler", "react-dom", "react-dom/client"]
    files = {
        "react": "react/cjs/react.production.js",
        "react-dom": "react-dom/cjs/react-dom.production.js",
        "react-dom/client": "react-dom/cjs/react-dom-client.production.js",
        "scheduler": "scheduler/cjs/scheduler.production.js",
    }
    out = [shim]
    for name in parts:
        src = (base / files[name]).read_text(encoding="utf-8")
        out.append(
            f"__defs[{json.dumps(name)}]=(function(module,exports){{{src}\n}});")
        if name == "react":
            out.append('window.React=__load("react");')
        if name == "react-dom":
            out.append('window.ReactDOM=__load("react-dom");')
        if name == "react-dom/client":
            out.append(
                'window.ReactDOMClient={createRoot:__load("react-dom/client")'
                '.createRoot};')
    return "\n".join(out).encode()


SHELL = """<!doctype html><html><head><meta charset="utf-8">
<title>Office visual probe</title>
<link rel="stylesheet" href="/dashboard-plugins/qubit-office/qubit-office.game.css">
<style>html,body{margin:0;height:100%%;background:#101828;}
#root{height:100vh;display:flex;flex-direction:column;}</style>
</head><body><div id="root"></div>
<script src="/probe/react.js"></script>
<script src="/probe/react-dom.js"></script>
<script>
  window.__QUBIT_SESSION_TOKEN__ = %(token)s;
  window.__QUBIT_BASE_PATH__ = "";
  window.__QUBIT_API_PREFIX__ = "/agent";
  window.__QUBIT_OFFICE_THEME__ = %(theme)s;
  window.__QUBIT_PLUGIN_SDK__ = {
    React: window.React,
    hooks: { useState: React.useState, useEffect: React.useEffect,
             useRef: React.useRef, useMemo: React.useMemo,
             useCallback: React.useCallback },
    fetchJSON: function (url, init) {
      return fetch(url, init).then(function (r) { return r.json(); });
    }
  };
  (function () {
    var map = Object.create(null);
    window.__QUBIT_PLUGINS__ = {
      register: function (name, comp) {
        map[name] = { name: name, component: comp };
        window.__QUBIT_PLUGINS_REGISTERED__ = Object.keys(map);
      },
      getPlugin: function (name) { return map[name] && map[name].component; },
      get: function (name) { return map[name]; },
      list: function () { return Object.keys(map); }
    };
  })();
</script>
<script src="/dashboard-plugins/qubit-office/qubit-office.game.js"></script>
<script>
  (function () {
    var tries = 0;
    function mount() {
      var reg = window.__QUBIT_PLUGINS__;
      var get = (reg && (reg.getPlugin || reg.get));
      var Comp = get ? get("qubit-office") : null;
      if (!Comp) {
        if (++tries > 300) {
          document.getElementById("root").textContent = "PLUGIN NOT REGISTERED";
          return;
        }
        return setTimeout(mount, 50);
      }
      var root = ReactDOMClient.createRoot(document.getElementById("root"));
      root.render(React.createElement(Comp));
    }
    mount();
  })();
</script>
</body></html>
"""

APP = """<!doctype html><html><head><meta charset="utf-8">
%(theme_style)s
<style>
html,body{margin:0;width:100%%;height:100%%;background:var(--ui-bg,#101828);
  color:var(--ui-text,#e9edf7);font:12px/1.2 monospace;}
.ui-root{height:100%%;box-sizing:border-box;padding:18px;background:var(--ui-bg);}
.ui-btn-group{display:inline-flex;gap:6px;padding:6px;background:var(--ui-bg-solid);
  border:1px solid var(--ui-line);border-radius:var(--radius);}
.ui-btn-group button{font:inherit;padding:5px 9px;color:var(--ui-text);
  background:var(--ui-bg-soft);border:1px solid var(--ui-line);
  border-radius:var(--radius-sm);}
</style></head><body>
<div class="ui-root" data-office-root>
  <div class="ui-btn-group" aria-label="Panels">
    <button title="Channels">Channels</button>
    <button title="Registry">Registry</button>
    <button title="Board">Board</button>
    <button title="Ledger">Ledger</button>
    <button title="Feed">Feed</button>
    <button title="Memory">Memory</button>
    <button title="Settings">Settings</button>
  </div>
</div>
</body></html>
"""


# --------------------------------------------------------------------------
# Loopback WebUI with fixed data seams
# --------------------------------------------------------------------------
class _FakeOrch:
    _route = "gateway"
    _web_active = 0
    task = type("T", (), {"active": False})()

    def __init__(self, cfg):
        self.cfg = cfg
        self._t0 = time.time()


def _start_ui(tmp_root: Path, monkeypatch):
    from voicebridge import webui as W
    from voicebridge.config import Config
    from voicebridge.office import theme as T

    home = tmp_root / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUBIT_HOME", str(home))
    monkeypatch.delenv("QUBIT_OFFICE_THEME", raising=False)
    _write_synthetic_pack(home)

    state = tmp_root / "state"
    state.mkdir(exist_ok=True)
    cfg_path = tmp_root / "voice.yaml"
    cfg_path.write_text(
        "logging:\n"
        f"  jsonl_path: '{state}'\n"
        "webui:\n"
        f"  token: {TOKEN}\n"
        "terminal:\n"
        "  min_context_tokens: 4096\n",
        encoding="utf-8",
    )
    cfg = Config.load(cfg_path)

    payload = T.theme_payload(cfg, requested=THEME_ID)
    assert payload.get("active") is True
    theme_style = T.theme_style_html(cfg, requested=THEME_ID)
    assert theme_style
    panel_json = json.dumps(_panel_payload()).encode("utf-8")
    tower_json = json.dumps(_tower_payload()).encode("utf-8")
    shell_html = (SHELL % {
        "token": json.dumps(TOKEN),
        "theme": json.dumps(payload),
    }).encode("utf-8")
    app_html = (APP % {"theme_style": theme_style}).encode("utf-8")

    original_do_get = W.WebUIHandler.do_GET

    def _patched_do_get(self):
        path = self.path.split("?")[0]
        if path == "/favicon.ico":
            # The shell has no favicon; answer it so the probe does not treat a
            # known cosmetic 404 as an unexpected console/page error.
            self._send(204, b"")
            return
        if path.startswith("/probe/"):
            name = path[len("/probe/"):]
            if name in ("react.js", "react-dom.js"):
                self._send(200, _umd_source(name),
                           "application/javascript")
                return
            self._send(404, b"no")
            return
        if path == "/agent/api/office/tower":
            self._json(json.loads(tower_json), 200)
            return
        if path == "/agent/api/office/habblaud/agents/panel":
            self._json(json.loads(panel_json), 200)
            return
        if path == "/api/stream":
            self._send(200, b"data: {}\n\n", "text/event-stream")
            return
        return original_do_get(self)

    def _patched_index(self):
        self._send(200, shell_html, "text/html; charset=utf-8")

    def _patched_plugin_app(self, sub):
        bare = str(sub).split("?")[0]
        if bare.startswith("qubit-office/habblaud"):
            # The office app is the one intentional same-origin iframe.
            self._allow_same_origin_frame = True
            try:
                self._send(200, app_html, "text/html; charset=utf-8")
            finally:
                self._allow_same_origin_frame = False
            return
        self._send(404, b"not found")

    monkeypatch.setattr(W.WebUIHandler, "do_GET", _patched_do_get)
    monkeypatch.setattr(W.WebUIHandler, "_serve_qubit_index", _patched_index)
    monkeypatch.setattr(W.WebUIHandler, "_serve_plugin_app",
                        _patched_plugin_app)

    ui = W.WebUI(_FakeOrch(cfg), host="127.0.0.1", port=0, state_dir=state)
    ui._prewarm_stt = lambda: None  # type: ignore[method-assign]
    ui._prewarm_reads = lambda: None  # type: ignore[method-assign]
    ui.start()
    port = ui._server.server_address[1]
    return ui, f"http://127.0.0.1:{port}"


# --------------------------------------------------------------------------
# Browser capture
# --------------------------------------------------------------------------
#: The baseline renderer. The committed goldens were produced with the system
#: Chrome channel; the bundled Playwright Chromium is deliberately refused so a
#: machine without it skips instead of comparing two different rendering stacks.
BASELINE_BROWSER = {
    "channel": "chrome",
    "note": ("goldens are generated with the system Chrome channel; "
             "bundled Chromium is not a substitute"),
}


def _launch_browser(p):
    try:
        return p.chromium.launch(headless=True, channel="chrome")
    except Exception as exc:  # noqa: BLE001 - a named skip, never a fake pass
        pytest.skip(
            "visual baselines require the system Chrome channel "
            f"(channel='chrome'); bundled Chromium is not used: {exc}")


def _colour_contrast(first: str, second: str) -> float:
    from voicebridge.office.theme import contrast_ratio

    def _hex(value: str) -> str:
        match = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", value)
        assert match, f"unexpected colour {value!r}"
        return "#" + "".join(f"{int(part):02x}" for part in match.groups())

    return contrast_ratio(_hex(first), _hex(second))


def _capture(tmp_root: Path, monkeypatch) -> Dict[str, Path]:
    pw = pytest.importorskip("playwright.sync_api")
    if not (REPO_ROOT / "frontend" / "qubit-web" / "node_modules" / "react"
            / "cjs" / "react.production.js").is_file():
        pytest.skip("frontend/qubit-web/node_modules absent; run npm ci there")

    out_dir = tmp_root / "shots"
    out_dir.mkdir(parents=True, exist_ok=True)
    ui, base = _start_ui(tmp_root, monkeypatch)
    shots: Dict[str, Path] = {}
    try:
        with pw.sync_playwright() as driver:
            browser = _launch_browser(driver)
            try:
                for dpr in (1, 2):
                    context = browser.new_context(
                        viewport=VIEWPORT, device_scale_factor=dpr)
                    page = context.new_page()
                    page.add_init_script(
                        "(() => { const fixed = 1762732800000;"
                        " const RealDate = Date;"
                        " class FixedDate extends RealDate {"
                        "  constructor(...args) {"
                        "   if (args.length === 0) super(fixed);"
                        "   else super(...args);"
                        "  }"
                        "  static now() { return fixed; }"
                        " }"
                        " window.Date = FixedDate;"
                        " try { performance.now = () => 0; } catch (e) {}"
                        "})();")
                    page.emulate_media(reduced_motion="reduce")
                    console_errors: list[str] = []
                    page_errors: list[str] = []
                    bad_responses: list[str] = []
                    failed_requests: list[str] = []

                    def _on_console(message) -> None:
                        if message.type != "error":
                            return
                        location = message.location or {}
                        url = str(location.get("url") or "")
                        if url.endswith("/favicon.ico"):
                            return
                        console_errors.append(message.text[:400])
                        if os.environ.get("QUBIT_OFFICE_VISUAL_DEBUG"):
                            print(f"[console:error] {message.text[:300]}",
                                  file=sys.stderr)

                    def _on_response(response) -> None:
                        if response.status < 400:
                            return
                        if response.url.endswith("/favicon.ico"):
                            return
                        bad_responses.append(f"{response.status} {response.url}")
                        if os.environ.get("QUBIT_OFFICE_VISUAL_DEBUG"):
                            print(f"[{response.status}] {response.url}",
                                  file=sys.stderr)

                    page.on("console", _on_console)
                    page.on("pageerror", lambda error: page_errors.append(
                        str(error)[:400]))
                    page.on("response", _on_response)
                    page.on("requestfailed", lambda request: failed_requests.append(
                        f"{request.url} {request.failure}"))
                    page.goto(base + "/qubit-office?theme=demo",
                              wait_until="load")
                    page.wait_for_selector("[data-testid=office-app]",
                                           timeout=30000)
                    _no_motion(page)
                    # The host theme is applied asynchronously: wait for the
                    # attribute AND a real computed token before any pixel is
                    # captured, or the first screenshot races the classic paint.
                    page.wait_for_function(
                        "() => document.documentElement.getAttribute("
                        "'data-office-theme') === 'demo'", timeout=20000)
                    page.wait_for_function(
                        "([expected]) => getComputedStyle(document."
                        "documentElement).getPropertyValue('--ui-bg')"
                        ".trim().toLowerCase() === expected",
                        arg=[VISUAL_TOKENS["color-surface"]], timeout=20000)
                    frame = page.frame_locator(
                        "[data-testid=office-app-frame]")
                    frame.locator('.ui-btn-group[aria-label="Panels"]'
                                  ).wait_for(timeout=20000)
                    inner = page.query_selector(
                        "[data-testid=office-app-frame]").content_frame()
                    assert inner is not None
                    inner.add_style_tag(content=(
                        "*,*::before,*::after{animation:none!important;"
                        "transition:none!important;caret-color:transparent"
                        "!important;}"))
                    # The menu contract: our two icons joined the app's group.
                    frame.locator("[data-testid=office-menu-agents]"
                                  ).wait_for(timeout=10000)
                    frame.locator("[data-testid=office-menu-hud]"
                                  ).wait_for(timeout=10000)
                    group = frame.locator(
                        '.ui-btn-group[aria-label="Panels"]')
                    shots[f"menu-group-dpr{dpr}"] = out_dir / (
                        f"menu-group-dpr{dpr}.png")
                    group.screenshot(path=str(shots[f"menu-group-dpr{dpr}"]),
                                     animations="disabled", caret="hide")

                    frame.locator("[data-testid=office-menu-agents]").click()
                    page.wait_for_selector(
                        "[data-testid=agents-panel][data-state=ready]",
                        timeout=30000)
                    page.wait_for_selector("#agents-panel-card",
                                           timeout=10000)
                    dock = page.locator("#office-game-agents-dock")
                    dock.wait_for(timeout=10000)
                    shots[f"agents-panel-dpr{dpr}"] = out_dir / (
                        f"agents-panel-dpr{dpr}.png")
                    dock.screenshot(
                        path=str(shots[f"agents-panel-dpr{dpr}"]),
                        animations="disabled", caret="hide")

                    card = page.locator("#agents-panel-card")
                    # REAL keyboard focus: click the card (its tabIndex makes
                    # it focusable), step out and back with Shift+Tab/Tab so the
                    # browser's focus-visible heuristic engages, then verify the
                    # ring is actually painted (width + style), not just coloured.
                    card.click()
                    page.keyboard.press("Shift+Tab")
                    page.keyboard.press("Tab")
                    focused = card.evaluate(
                        "el => el === document.activeElement"
                        " && el.matches(':focus-visible')")
                    assert focused, (
                        "the agent card did not enter focus-visible through "
                        "keyboard navigation")
                    outline_width = card.evaluate(
                        "el => parseFloat(getComputedStyle(el).outlineWidth)"
                        " || 0")
                    outline_style = card.evaluate(
                        "el => getComputedStyle(el).outlineStyle")
                    assert outline_width > 0, "focus ring has zero width"
                    assert outline_style not in ("none", "hidden", ""), (
                        f"focus ring style is {outline_style!r}")
                    outline = card.evaluate(
                        "el => getComputedStyle(el).outlineColor")
                    background = card.evaluate(
                        "el => getComputedStyle(el).backgroundColor")
                    ratio = _colour_contrast(outline, background)
                    assert ratio >= 3.0, (
                        f"focus ring contrast {ratio:.2f}:1 < 3.0 "
                        f"({outline} on {background})")
                    box = card.bounding_box()
                    assert box is not None
                    pad = 6
                    shots[f"agent-card-focus-dpr{dpr}"] = out_dir / (
                        f"agent-card-focus-dpr{dpr}.png")
                    page.screenshot(
                        path=str(shots[f"agent-card-focus-dpr{dpr}"]),
                        animations="disabled", caret="hide",
                        clip={
                            "x": max(0, box["x"] - pad),
                            "y": max(0, box["y"] - pad),
                            "width": box["width"] + 2 * pad,
                            "height": box["height"] + 2 * pad,
                        })
                    assert not page_errors, (
                        "unexpected page error(s): " + " | ".join(page_errors))
                    assert not console_errors, (
                        "unexpected console error(s): "
                        + " | ".join(console_errors))
                    assert not bad_responses, (
                        "unexpected HTTP error response(s): "
                        + " | ".join(bad_responses))
                    assert not failed_requests, (
                        "unexpected failed request(s): "
                        + " | ".join(failed_requests))
                    context.close()
            finally:
                browser.close()
    finally:
        ui.stop()
    return shots


def _no_motion(page) -> None:
    page.add_style_tag(content=(
        "*,*::before,*::after{animation:none!important;"
        "transition:none!important;caret-color:transparent!important;}"))


# --------------------------------------------------------------------------
# Comparison / baselines
# --------------------------------------------------------------------------
def _compare(actual: Path, golden: Path) -> Tuple[int, int, float]:
    from PIL import Image, ImageChops

    if not golden.is_file():
        pytest.fail(
            f"visual baseline missing: {golden.relative_to(REPO_ROOT)}; "
            "generate it with `python tests/test_office_visual_regression.py "
            "--write` and review the image before committing")
    actual_img = Image.open(actual).convert("RGB")
    golden_img = Image.open(golden).convert("RGB")
    assert actual_img.size == golden_img.size, (
        f"{actual.name}: size {actual_img.size} != baseline "
        f"{golden_img.size}")
    diff = ImageChops.difference(actual_img, golden_img)
    data = diff.tobytes()
    total = len(data) // 3
    changed = sum(
        1 for index in range(0, len(data), 3)
        if max(data[index:index + 3]) > PIXEL_THRESHOLD)
    return changed, total, changed / total if total else 0.0


def test_office_visual_goldens(tmp_path, monkeypatch):
    metadata_path = BASELINE_DIR / "browser.json"
    if not metadata_path.is_file():
        pytest.fail(
            "visual baseline browser metadata missing: "
            f"{metadata_path.relative_to(REPO_ROOT)}; regenerate baselines "
            "with the explicit --write command and review them")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata.get("channel") == BASELINE_BROWSER["channel"], (
        f"baselines were generated for {metadata.get('channel')!r}, this probe "
        f"pins {BASELINE_BROWSER['channel']!r}")
    shots = _capture(tmp_path, monkeypatch)
    failures: list[str] = []
    for name, actual in sorted(shots.items()):
        golden = BASELINE_DIR / f"{name}.png"
        changed, total, ratio = _compare(actual, golden)
        if ratio > MAX_DIFF_RATIO:
            failures.append(
                f"{name}: {changed}/{total} pixels differ ({ratio:.3%} > "
                f"{MAX_DIFF_RATIO:.3%})")
    assert not failures, "\n".join(failures)


def _write_baselines(write: bool) -> int:
    if not write:
        raise SystemExit("nothing to do; pass --write")
    if not os.environ.get(VISUAL_GATE):
        os.environ[VISUAL_GATE] = "1"
    from pytest import MonkeyPatch

    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_root = Path(tempfile.mkdtemp(prefix="office-visual-baseline-"))
    mp = MonkeyPatch()
    try:
        shots = _capture(tmp_root, mp)
    finally:
        mp.undo()
    for name, source in shots.items():
        target = BASELINE_DIR / f"{name}.png"
        target.write_bytes(source.read_bytes())
        print(f"wrote {target.relative_to(REPO_ROOT)}")
    (BASELINE_DIR / "browser.json").write_text(
        json.dumps(BASELINE_BROWSER, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print(f"wrote {(BASELINE_DIR / 'browser.json').relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_write_baselines("--write" in sys.argv))
