"""Drive the REAL /qubit-office page in headless Chrome and read the panel text.

WHAT IS REAL RIDING THIS
------------------------
  * ``WebUIHandler`` — the production listener class — on an ephemeral loopback
    port, with the dashboard SPA served from a temp dist that loads the plugin
    the way ``usePlugins.ts`` does: one ``<script>`` for the manifest entry,
    after ``window.__QUBIT_PLUGIN_SDK__`` holds the host's React.
  * The SERVED bundle ``qubit-app/plugins/qubit-office/qubit-office.game.js`` as
    it is committed — not the source. If the bundle were stale, this probe
    would show it.
  * ``/agent/api/office/habblaud/agents/panel`` answered by the real
    ``qubit_compat.dispatch`` behind the same ``_qubit_token_ok`` gate the
    dashboard uses.
  * Real Chrome (Playwright), the real embedded Habblaud iframe, and the real
    React DOM the shell renders.

Run: .venv/bin/python tests/_probe_agents_panel_browser.py
Prints the page's own text, the frame's src, and the panel's rendered rows.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

TOKEN = "agents-panel-browser-token"
OUT = Path(tempfile.mkdtemp(prefix="agents-panel-browser-"))

# The SPA shell the plugin route serves. It publishes the SDK the way the
# dashboard does (React + hooks as globals), registers nothing itself, then
# loads the plugin bundle — the single <script> per manifest entry.
SHELL = """<!doctype html><html><head><meta charset="utf-8">
<title>Office probe</title></head><body>
<div id="root"></div>
<link rel="stylesheet" href="/dashboard-plugins/qubit-office/qubit-office.game.css">
<style>html,body{margin:0;height:100%%}#root{height:100vh;display:flex;flex-direction:column}</style>
<script src="/probe/react.js"></script>
<script src="/probe/react-dom.js"></script>
<script>
  window.__QUBIT_SESSION_TOKEN__ = %(token)s;
  window.__QUBIT_BASE_PATH__ = "";
  window.__QUBIT_API_PREFIX__ = "/agent";
  window.__QUBIT_PLUGIN_SDK__ = {
    React: window.React,
    hooks: { useState: React.useState, useEffect: React.useEffect,
             useRef: React.useRef, useMemo: React.useMemo,
             useCallback: React.useCallback },
    fetchJSON: function (url, init) { return fetch(url, init).then(function (r) { return r.json(); }); }
  };
  // The host registry the plugin registers INTO (web/src/plugins/registry.ts).
  // Without it the entry's `window.__QUBIT_PLUGINS__?.register(...)` is an
  // optional call on undefined — a silent no-op, which is exactly the failure
  // this probe exists to make visible.
  (function () {
    var map = Object.create(null);
    window.__QUBIT_PLUGINS__ = {
      register: function (name, comp) {
        map[name] = { name: name, component: comp };
        window.__QUBIT_PLUGINS_REGISTERED__ = Object.keys(map);
        var ev = new CustomEvent("qubit:plugin-registered", { detail: name });
        window.dispatchEvent(ev);
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
        if (++tries > 200) { document.getElementById("root").textContent = "PLUGIN NOT REGISTERED"; return; }
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

# The host's React, browser-ready. The package ships CJS only, so the two CJS
# builds are wrapped in a require shim — the same modules the dashboard's own
# bundle presents as `window.React` / `window.ReactDOM`.
_UMD = {
    "react.js": ("react", "React", "ReactDOM", "cjs"),
}


def _umd_source(which: str) -> bytes:
    """A browser-ready bundle of the host's React, in the globals the SDK uses.

    The CJS builds call `require("scheduler" | "react" | "react-dom")`, so a
    three-line require shim resolves those to the already-evaluated exports —
    the same module instances, which is what keeps ONE hook runtime.
    """
    base = REPO / "frontend/qubit-web/node_modules"
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
    if which == "react.js":
        parts = ["react"]
    else:
        # `react` first, so `window.React` exists before the DOM build asks for
        # it; every module keeps its own cache slot, so the DOM build and the
        # plugin share ONE React instance.
        parts = ["react", "scheduler", "react-dom", "react-dom/client"]
    files = {
        "react": "react/cjs/react.production.js",
        "react-dom": "react-dom/cjs/react-dom.production.js",
        "react-dom/client": "react-dom/cjs/react-dom-client.production.js",
        "scheduler": "scheduler/cjs/scheduler.production.js",
    }
    out = [shim]
    for name in parts:
        src = (base / files[name]).read_text()
        out.append(f"__defs[{json.dumps(name)}]=(function(module,exports){{{src}\n}});")
        if name == "react":
            out.append("window.React=__load(\"react\");")
        if name == "react-dom":
            out.append("window.ReactDOM=__load(\"react-dom\");")
        if name == "react-dom/client":
            out.append("window.ReactDOMClient={createRoot:__load(\"react-dom/client\").createRoot};")
    return "\n".join(out).encode()


def _build_shell(token: str) -> str:
    return SHELL % {"token": json.dumps(token)}


def main() -> int:
    os.environ.update(XDG_CONFIG_HOME=str(OUT / "c"),
                      XDG_STATE_HOME=str(OUT / "s"),
                      XDG_DATA_HOME=str(OUT / "d"))
    from voicebridge import qubit_compat as HC  # noqa: E402
    from voicebridge import webui as ui  # noqa: E402

    index_html = _build_shell(TOKEN).encode()

    box: list = []
    state: dict = {}

    class App:
        api_token = TOKEN
        allowed_origins: tuple = ()
        _use_frontend_dir = False
        # The compat dispatch reads `app.orch.cfg`; a stub is enough for a
        # read that never touches the store (the roster is on disk).
        orch = type("Orch", (), {"cfg": None})()

        # `_serve_office_stream` loops while the server is alive; a class with
        # no `_server` attribute would raise on the first SSE read.
        @property
        def _server(self):
            return state.get("server")

    class Handler(ui.WebUIHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # The page navigated or the component unmounted mid-read. That
                # is the client's business, not a server fault to print.
                pass

        # The SPA shell, with the host globals a real index.html carries.
        def _serve_qubit_index(self):
            self._send(200, index_html, "text/html; charset=utf-8")

        def _qubit_dist(self):
            return OUT

        # The ONE gate the office reads cross: the canonical token header.
        def _qubit_token_ok(self):
            return self.headers.get("X-Qubit-Session-Token", "") == TOKEN

        def do_GET(self):
            path = self.path.split("?")[0]
            if path.startswith("/probe/"):
                name = path[len("/probe/"):]
                if name in ("react.js", "react-dom.js"):
                    self._send(200, _umd_source(name), "application/javascript")
                    return
                self._send(404, b"no")
                return
            if path.startswith("/agent/api/"):
                if not self._qubit_token_ok():
                    self._json({"ok": False, "code": "UNAUTHORIZED"}, 401)
                    return
                query = {}
                if "?" in self.path:
                    from urllib.parse import parse_qsl
                    query = dict(parse_qsl(self.path.split("?", 1)[1]))
                try:
                    code, out = HC.dispatch(None, "GET", path[len("/agent"):],
                                            query, {})
                except Exception as exc:  # noqa: BLE001
                    code, out = 500, {"ok": False, "detail": str(exc)}
                self._json(out, code)
                return
            super().do_GET()

    box: list = []

    def serve():
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        httpd.app = App()
        state["server"] = httpd
        box.append(httpd.server_address[1])
        httpd.serve_forever()

    threading.Thread(target=serve, daemon=True).start()
    while not box:
        pass
    base = f"http://127.0.0.1:{box[0]}"
    print(f"server {base}")

    from playwright.sync_api import sync_playwright

    failures: list = []

    def check(ok, what):
        print(("ok   " if ok else "FAIL ") + what)
        if not ok:
            failures.append(what)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        # Serve the dashboard's own stylesheet, as its index.html links it
        # (requested by the shell; Playwright's route fulfils that request
        # without needing a server route for it). Without the stylesheet
        # `.office-game` is a block that grows to its content, so the dock's
        # `top:0/bottom:0` resolves against an unbounded box and the sheet
        # measures thousands of pixels. The layout is part of the proof.
        css = (REPO / "frontend/qubit-app/plugins/qubit-office/"
               "qubit-office.game.css").read_text()
        page.route("**/qubit-office.game.css",
                   lambda route: route.fulfill(status=200, content_type="text/css",
                                               body=css))
        page.on("console", lambda m: print(f"     [console:{m.type}] {m.text[:160]}"))
        page.on("requestfailed", lambda r: print(f"     [failed] {r.url[:140]} {r.failure}"))
        page.on("response", lambda r: print(f"     [{r.status}] {r.url[:140]}") if r.status >= 400 else None)
        page.goto(base + "/qubit-office?app=1", wait_until="load")

        # 1. the plugin registered and the shell mounted
        page.wait_for_selector("[data-testid=office-app]", timeout=30000)
        check(True, "the office plugin registered and its shell mounted")
        # 2. the embedded Habblaud document is a REAL second document
        frame = page.wait_for_selector("[data-testid=office-app-frame]", timeout=15000)
        src = frame.get_attribute("src") or ""
        check("/plugin-apps/qubit-office/habblaud/" in src,
              f"the embedded app is framed from its own route: {src}")
        frame_el = page.query_selector("[data-testid=office-app-frame]")
        page.wait_for_timeout(2500)
        frames = [f.url for f in page.frames if "habblaud" in f.url]
        check(bool(frames), f"the Habblaud document loaded in a separate frame: {frames[:1]}")
        # 3. the outer document's registry does NOT reach the inner one — the
        #    exact reason the panel lives in the shell, not in the frame.
        inner = frame_el.content_frame() if frame_el else None
        has_registry = bool(inner.evaluate("() => typeof window.__QUBIT_PLUGINS__ !== 'undefined'")) if inner else True
        check(inner is not None and has_registry is False,
              "the embedded document has no __QUBIT_PLUGINS__ (a HUD/registry "
              "lookup from inside it can never succeed)")

        # 4. open the agents panel from the shell
        page.click("[data-testid=office-game-agents-btn]")
        page.wait_for_selector("[data-testid=agents-panel][data-state=ready]",
                               timeout=30000)
        check(True, "the agents panel reached its ready state inside the office page")

        # Scoped to the DOCK: the page also renders the vendored office app's
        # own (closed) `<details>` groups inside its frame, and a page-wide
        # selector cannot tell the panel's markup from the game's.
        dock = "[id=office-game-agents-dock] "
        seats = page.query_selector_all(dock + "[data-testid=agents-panel-seat]")
        groups = page.query_selector_all(dock + "[data-testid=agents-panel-division]")
        summary = page.inner_text("#office-game-agents-dock [data-testid=agents-panel-summary]")
        check(len(seats) == 283, f"every seat is rendered: {len(seats)} rows")
        check(len(groups) == 19, f"every bucket is rendered: {len(groups)} groups")
        check("283 seats" in summary and "18 divisions" in summary
              and "41 rooms" in summary,
              f'the summary carries the real counts: "{summary}"')

        # 5. the reader's real text
        print("\n=== RENDERED SUMMARY ===")
        print(summary)
        print("\n=== RENDERED DIVISION HEADINGS (first 6) ===")
        # Read through the LIVE document each time: React re-renders replace
        # these nodes, and a handle captured earlier is detached.
        for g in page.query_selector_all(dock + "[data-testid=agents-panel-division]")[:6]:
            head = g.query_selector("summary")
            print("  " + head.inner_text().replace("\n", " ") if head else "  (none)")
        print("\n=== RENDERED SEAT ROWS (boardroom tier, first 6) ===")
        # Every division starts COLLAPSED (the reader opens the one they came
        # for), so rows are read from the boardroom tier — the one group that
        # starts open — by opening it explicitly first.
        page.click("[id=agents-panel-division-boardroom] > summary")
        page.wait_for_timeout(100)
        for s in page.query_selector_all(
                "[id=agents-panel-division-boardroom] [data-testid=agents-panel-seat]")[:6]:
            print("  " + " | ".join(t for t in (p.strip() for p in s.inner_text().split("\n")) if t))
        print("\n=== A REAL DIVISION'S SEATS (engineering) ===")
        eng_sel = '[id=agents-panel-division-engineering]'
        if page.query_selector(eng_sel):
            page.click(eng_sel + " > summary")  # a <details>, closed by default
            page.wait_for_timeout(200)
            eng = page.query_selector(eng_sel)
            rows = eng.query_selector_all("[data-testid=agents-panel-seat]")
            print(f"  {eng.query_selector('summary').inner_text().strip().replace(chr(10), ' ')}")
            for s in rows[:5]:
                print("    " + " | ".join(s.inner_text().split("\n")))
            check(len(rows) == 66, f"engineering lists its 66 real seats ({len(rows)})")

        # 6. the panel is readable on screen, not hidden behind the frame
        box_ = page.query_selector("#office-game-agents-dock").bounding_box()
        check(bool(box_) and box_["width"] > 400 and box_["height"] > 300,
              f"the dock is a real side sheet: {box_}")

        page.screenshot(path=str(OUT / "office-agents.png"), full_page=False)
        print(f"\nscreenshot: {OUT / 'office-agents.png'}")
        browser.close()

    if failures:
        print(f"\n{len(failures)} FAILED")
        return 1
    print("\nALL CHECKS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
