"""Serve the HUD's real endpoints from the real handler, then run the DOM check.

Boots ``WebUIHandler`` on an ephemeral loopback port and adds ONE route the HUD
actually reads — ``GET /agent/api/health`` — answered by the real
``voicebridge.qubit_compat`` health handler (the same one the dashboard serves).
Every other ``/agent/api/<x>`` the HUD asks for is answered by the real compat
dispatch too, or with a typed JSON error; nothing is fabricated.

Writes ``/tmp/hudcheck/spec.json`` = {base, token, hudBundle} and runs node.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO = Path("/tmp/qubit-v2-office-hudfix")
sys.path.insert(0, str(REPO))

TMP = Path(tempfile.mkdtemp())
os.environ.update(XDG_CONFIG_HOME=str(TMP / "c"), XDG_STATE_HOME=str(TMP / "s"),
                  XDG_DATA_HOME=str(TMP / "d"))
(REPO / "tmp").mkdir(exist_ok=True)
_cfgp = TMP / "voice.yaml"
_cfgp.write_text("logging:\n  jsonl: false\n")

from voicebridge import config as C  # noqa: E402
from voicebridge import qubit_compat as HC  # noqa: E402
from voicebridge import webui as ui  # noqa: E402

CFG = C.Config.load(_cfgp)
TOKEN = "hud-probe-token"


class _Orch:
    cfg = CFG


class _App:
    api_token = TOKEN
    allowed_origins: tuple = ()
    orch = _Orch()
    _use_frontend_dir = False


class _H(ui.WebUIHandler):
    def log_message(self, *a):
        pass

    # ---- auth: the probe presents the configured token -------------------
    def _qubit_token_ok(self):
        return self.headers.get("X-Qubit-Session-Token", "") == TOKEN

    # ---- the HUD's GET reads, via the REAL compat dispatch --------------
    def do_GET(self):
        path = self.path.split("?")[0]
        if path.startswith("/agent/api/"):
            # "/agent/api/health" -> the compat table's "/api/health"
            suffix = path[len("/agent"):]
            query = {}
            if "?" in self.path:
                from urllib.parse import parse_qsl
                query = dict(parse_qsl(self.path.split("?", 1)[1]))
            try:
                code, out = HC.dispatch(CFG, "GET", suffix, query, {})
            except Exception as exc:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                code, out = 500, {"ok": False, "code": "PROBE", "detail": str(exc)}
            self._json(out, code)
            return
        super().do_GET()

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    box: list = []

    def serve():
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        httpd.app = _App()
        box.append(httpd.server_address[1])
        httpd.serve_forever()

    threading.Thread(target=serve, daemon=True).start()
    while not box:
        pass
    base = f"http://127.0.0.1:{box[0]}"

    spec = {
        "base": base,
        "token": TOKEN,
        "hudBundle": str(REPO / "frontend/qubit-app/plugins/qubit-hud/qubit-hud.js"),
    }
    specp = Path("/tmp/hudcheck/spec.json")
    specp.write_text(json.dumps(spec))

    sys.path.insert(0, str(REPO / "tests"))
    from _office_toolchain import node_path  # noqa: E402
    nd = node_path()

    # A caller (the pytest) passes the already-compiled dock module; otherwise
    # compile it here so the script stands alone.
    explicit = sys.argv[1] if len(sys.argv) > 1 else ""
    compiled = Path(explicit) if explicit else None
    if compiled is None:
        from _office_toolchain import ensure_tsc
        out = Path(tempfile.mkdtemp()) / "js"
        tsc = ensure_tsc()
        subprocess.run(
            [nd, str(tsc), "--module", "es2020", "--target", "es2020",
             "--moduleResolution", "node", "--ignoreDeprecations", "6.0",
             "--outDir", str(out), "--skipLibCheck", "--noEmitOnError", "false",
             str(REPO / "frontend/qubit-app/plugins/qubit-office/game/ui/"
                 "HudPanel.ts")],
            capture_output=True, text=True, timeout=180,
            cwd=str(REPO / "frontend/qubit-app/plugins/qubit-office/game/ui"))
        compiled = out / "HudPanel.js"

    # jsdom + react resolve from qubit-web/node_modules, so the check must run
    # with its file INSIDE that package. Stage it there, run, remove.
    src = REPO / ("frontend/qubit-app/plugins/qubit-office/game/ui/"
                  "hud-dock.domcheck.mjs")
    stg = REPO / "frontend/qubit-web" / ".hud-domcheck.mjs"
    mod = (REPO / "frontend/qubit-app/plugins/qubit-office/game/ui"
           / "hud-panel.mjs")
    stg.write_text(src.read_text(encoding="utf-8"))
    mod.write_text(compiled.read_text())
    try:
        env = dict(os.environ)
        env["HUD_DOCK_DIR"] = str(src.parent)
        p = subprocess.run([nd, str(stg), str(specp)],
                           cwd=str(REPO / "frontend/qubit-web"),
                           env=env, text=True, capture_output=True, timeout=300)
    finally:
        stg.unlink(missing_ok=True)
        mod.unlink(missing_ok=True)
    print(p.stdout)
    if p.stderr.strip():
        print("--- stderr ---")
        print(p.stderr[-4000:])
    return p.returncode


if __name__ == "__main__":
    raise SystemExit(main())
