"""Boot the REAL compat dispatch and run the agents panel's DOM check.

Hermetic: an ephemeral loopback port, the repo's own modules, the repo's own
React/jsdom. No external network and no provider call.

WHAT IS REAL HERE
-----------------
  * the server is a real ``ThreadingHTTPServer`` on 127.0.0.1:0;
  * ``/agent/api/office/habblaud/agents/panel`` is answered by the REAL
    ``voicebridge.qubit_compat.dispatch`` — the same table the dashboard serves,
    with the same handler and the same ``voicebridge.office.agent_panel``
    projection. Nothing is stubbed and no payload is fabricated;
  * the route is gated exactly as the production listener gates it: a request
    without the session token is a 401, so the panel's own token header is
    exercised rather than assumed (a bare ``/api/...`` from the iframe is the
    failure this probe is looking for);
  * the check itself (``_agents_panel_domcheck.mjs``) mounts the panel's real
    React component and reads the text a reader would see.

Prints the rendered text, then ``ALL CHECKS PASS`` or the failing assertions.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

APP_DIR = REPO / "frontend/qubit-app/plugins/qubit-office/game/app"
WEB = REPO / "frontend/qubit-web"

TOKEN = "agents-panel-probe-token"
ROUTE = "/agent/api/office/habblaud/agents/panel"


def _make_handler(HC):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: D401 — keep the probe silent
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 — http.server's name
            path, _, raw_query = self.path.partition("?")
            query = dict(parse_qsl(raw_query))
            if path.startswith("/agent/api/"):
                # The production gate: no session token, no answer.
                if self.headers.get("X-Qubit-Session-Token", "") != TOKEN:
                    self._json({"ok": False, "code": "UNAUTHORIZED",
                                "detail": "the session token was not accepted"},
                               401)
                    return
                try:
                    code, out = HC.dispatch(None, "GET", path[len("/agent"):],
                                            query, {})
                except Exception as exc:  # noqa: BLE001
                    import traceback
                    traceback.print_exc()
                    code, out = 500, {"ok": False, "code": "PROBE",
                                      "detail": str(exc)}
                self._json(out, code)
                return
            self._json({"ok": False, "code": "NOT_FOUND"}, 404)

    return Handler


def main() -> int:
    # A scratch config/state home so the probe never touches the owner's store.
    scratch = Path(tempfile.mkdtemp(prefix="agents-panel-"))
    os.environ.update(XDG_CONFIG_HOME=str(scratch / "c"),
                      XDG_STATE_HOME=str(scratch / "s"),
                      XDG_DATA_HOME=str(scratch / "d"))

    from voicebridge import qubit_compat as HC  # noqa: E402

    box: list = []

    def serve():
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(HC))
        box.append(httpd.server_address[1])
        httpd.serve_forever()

    threading.Thread(target=serve, daemon=True).start()
    while not box:
        pass
    base = f"http://127.0.0.1:{box[0]}"

    # ---- the gate is real: no token -> 401 ------------------------------
    import urllib.error
    import urllib.request
    try:
        urllib.request.urlopen(base + ROUTE, timeout=5)
        print("PROBE DEFECT: the tokenless request was NOT refused")
        return 1
    except urllib.error.HTTPError as exc:
        print(f"ok   the tokenless request is refused (HTTP {exc.code})")
    raw = urllib.request.Request(base + ROUTE,
                                 headers={"X-Qubit-Session-Token": TOKEN})
    with urllib.request.urlopen(raw, timeout=30) as res:
        served = json.loads(res.read())
    print(f"ok   the real route answered {served['counts']['headcount']} seats "
          f"in {served['counts']['divisions']} divisions")

    # ---- transpile the panel's two modules with the repo's own tsc -------
    from _office_toolchain import ensure_tsc, node_path  # noqa: E402

    node = node_path()
    tsc = ensure_tsc()
    if not node or tsc is None:
        print("PROBE DEFECT: no node/tsc toolchain")
        return 2
    stage = scratch / "modules"
    stage.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [node, str(tsc), "--module", "es2020", "--target", "es2020",
         "--moduleResolution", "node", "--ignoreDeprecations", "6.0",
         "--outDir", str(stage), "--skipLibCheck", "--noEmitOnError", "false",
         str(APP_DIR / "agents-panel.ts"), str(APP_DIR / "AgentsPanel.tsx")],
        capture_output=True, text=True, timeout=300, cwd=str(APP_DIR))
    if not (stage / "agents-panel.js").is_file() or \
            not (stage / "AgentsPanel.js").is_file():
        print("PROBE DEFECT: the panel did not compile")
        print(proc.stdout[-2000:])
        print(proc.stderr[-3000:])
        return 2
    # The check imports `./agents-panel.mjs` / `./AgentsPanel.mjs`.
    for name in ("agents-panel", "AgentsPanel"):
        shutil.move(str(stage / f"{name}.js"), str(stage / f"{name}.mjs"))
    # `AgentsPanel` imports `./agents-panel` with no extension; Node's ESM
    # loader needs one, so the import is rewritten in the transpiled copy.
    tsx = (stage / "AgentsPanel.mjs").read_text()
    tsx = tsx.replace('from "./agents-panel"', 'from "./agents-panel.mjs"')
    (stage / "AgentsPanel.mjs").write_text(tsx)
    # A package.json makes `createRequire` resolve react/jsdom from the web
    # package (its node_modules is the repo's only React).
    (stage / "package.json").write_text(json.dumps({"type": "module"}))

    spec = {"base": base, "token": TOKEN}
    specp = scratch / "spec.json"
    specp.write_text(json.dumps(spec))

    env = dict(os.environ)
    env["AGENTS_PANEL_DIR"] = str(stage)
    env["NODE_PATH"] = str(WEB / "node_modules")
    # React 19 exposes `act` only in a non-production build; without this the
    # check renders without a flush harness (and react-dom/test-utils throws).
    env["NODE_ENV"] = "test"
    run = subprocess.run(
        [node, str(REPO / "tests" / "_agents_panel_domcheck.mjs"), str(specp)],
        cwd=str(WEB), env=env, text=True, capture_output=True, timeout=300)
    print(run.stdout)
    if run.stderr.strip():
        print("--- stderr ---")
        print(run.stderr[-4000:])
    return run.returncode


if __name__ == "__main__":
    raise SystemExit(main())
