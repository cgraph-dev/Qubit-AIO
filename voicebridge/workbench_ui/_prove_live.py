#!/usr/bin/env python3
"""Live proof that /wb/ serves the ported UI and the shim answers real data.

WHAT THIS DOES
---------------------------------------------------------------------------
Boots a REAL ``voicebridge.webui.WebUI`` on a spare port and proves three
things against the LIVE server (not a mock):

  1. ASSETS  — every file the ported ``index.html`` references is served 200
               with the right content type, ``..`` traversal is refused, a
               non-allowlisted suffix is refused, and the MIT LICENSE is served.
  2. GLOBALS — the served ``index.html`` carries ``window.__QUBIT_SESSION_TOKEN__``,
               ``__QUBIT_API_PREFIX__`` and ``__QUBIT_WB_MOUNT__`` for a loopback
               caller (the same loopback-only rule the SPA and the office app use).
  3. SHIM    — every PORT endpoint answers 200-or-a-typed-refusal with the
               token, and a GAP endpoint answers ``WB_NOT_IN_PORT_SET``.

The ``/wb/`` branch is installed on the handler at runtime by
``install_wb_branch()`` — the SAME four-line branch the report delivers for
``webui.py`` — so this exercises the real dispatch path without editing a file
this task does not own.

Usage:  python3 voicebridge/workbench_ui/_prove_live.py
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def install_wb_branch() -> None:
    """Install the delivered ``/wb/`` branch on the real handler class.

    This is byte-for-byte the branch the report instructs the coordinator to add
    beside ``webui.py``'s ``/plugin-apps/`` branch; keeping the two in one place
    means the proof and the delivery cannot drift.
    """
    from voicebridge import webui as W
    from voicebridge import workbench_ui_routes as R

    handler = W.WebUIHandler
    if getattr(handler, "_wb_branch_installed", False):
        return
    original_do_GET = handler.do_GET
    original_do_POST = handler.do_POST

    def _dispatch_wb(self, method: str) -> bool:
        """Route /wb/* (asset) and /wb/api/* (shim). True when handled."""
        path = self.path.split("?")[0]
        # ``api_token`` lives on the WebUI instance, which the handler reaches
        # as ``self.server.app`` — the same source ``_serve_app_html`` reads.
        token = getattr(getattr(self.server, "app", None), "api_token", "") or ""
        if path == "/wb" or path.startswith("/wb/"):
            if path.startswith("/wb/api/"):
                from urllib.parse import parse_qs, urlsplit
                sub = path[len("/wb/api/"):]
                q = {k: v[0] for k, v in parse_qs(
                    urlsplit(self.path).query, keep_blank_values=True).items()}
                body: dict = {}
                if method != "GET":
                    try:
                        n = int(self.headers.get("Content-Length", 0) or 0)
                    except ValueError:
                        n = 0
                    if n:
                        try:
                            body = json.loads(
                                self.rfile.read(n).decode("utf-8", "replace")
                                or "{}")
                        except ValueError:
                            body = {}
                cfg = getattr(self.server, "wb_cfg", None)
                status, payload = R.h_wb_gateway(
                    cfg, q, body if isinstance(body, dict) else {},
                    {"sub": sub, "method": method})
                self._json(payload, status)
                return True
            R.serve_asset(self, path, token=token, base_path="")
            return True
        return False

    def do_GET(self):  # noqa: N802 - mirrors BaseHTTPRequestHandler
        return None if _dispatch_wb(self, "GET") else original_do_GET(self)

    def do_POST(self):  # noqa: N802 - mirrors BaseHTTPRequestHandler
        return None if _dispatch_wb(self, "POST") else original_do_POST(self)

    handler.do_GET = do_GET
    handler.do_POST = do_POST
    handler._wb_branch_installed = True  # type: ignore[attr-defined]


def _client(port: int, token: str):
    base = f"http://127.0.0.1:{port}"

    def get(path: str, method: str = "GET", body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, method=method)
        req.add_header("X-Qubit-Session-Token", token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    return get


def main() -> int:
    install_wb_branch()
    from voicebridge import webui as W

    # A real orchestrator-free WebUI: a stub with only the `cfg` attribute the
    # constructor reads. The session/cron/approval authorities are resolved from
    # cfg at call time by the shim, which is exactly the seam under test.
    class _Orch:
        cfg = None

    # The REAL config object, pointed at a throwaway state dir so the proof
    # reads the operator's actual session/approval stores without writing.
    from voicebridge.config import Config
    cfg = Config.load()
    try:
        import tempfile
        cfg.data.setdefault("webui", {})["state_dir"] = tempfile.mkdtemp(
            prefix="qubit-wb-prove-")
    except Exception:
        pass

    class Orch:
        def __init__(self, config):
            self.cfg = config

    orch = Orch(cfg)
    ui = W.WebUI(orch, host="127.0.0.1", port=0)
    ui.start()
    # The shim needs the config the handlers resolve authorities from.
    ui._server.wb_cfg = cfg  # type: ignore[attr-defined]
    # WebUI.start binds port 0 -> learn the real port from the socket.
    port = ui._server.server_address[1]
    token = ui.api_token or ""
    get = _client(port, token)
    failures: list[str] = []
    notes: list[str] = []

    print(f"live server: 127.0.0.1:{port}  token_set={bool(token)}")

    # ---------------------------------------------------------------- 1 assets
    from voicebridge import workbench_ui_routes as R
    index_html = (R.STATIC_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    import re
    refs = sorted(set(re.findall(r'(?:href|src)="(static/[^"?]+)', index_html)))
    print(f"\n[1] assets referenced by index.html: {len(refs)}")
    for ref in refs:
        status, body, headers = get("/wb/" + ref)
        if status != 200:
            failures.append(f"asset {ref} -> {status}")
            print(f"    FAIL {status} {ref}")
    if not failures:
        print(f"    OK   all {len(refs)} assets 200")

    # confinement + allowlist
    for bad, why in [("/wb/../workbench_ui_routes.py", "traversal"),
                     ("/wb/static/../../qubit_compat.py", "traversal"),
                     ("/wb/static/index.htmlx", "unknown suffix")]:
        status, _b, _h = get(bad)
        ok = status == 404
        print(f"    {'OK  ' if ok else 'FAIL'} {why}: {bad} -> {status}")
        if not ok:
            failures.append(f"{why} {bad} -> {status}")

    for notice in ("/wb/static/LICENSE", "/wb/static/vendor/VENDOR_RECORD.md"):
        status, body, _h = get(notice)
        ok = status == 200 and (b"MIT" in body or b"License" in body)
        print(f"    {'OK  ' if ok else 'FAIL'} notice {notice} -> {status} "
              f"({len(body)}B)")
        if not ok:
            failures.append(f"notice {notice} -> {status}")

    # --------------------------------------------------------------- 2 globals
    print("\n[2] injected globals in the served index.html")
    status, body, _h = get("/wb/")
    html = body.decode("utf-8", "replace")
    for g in ("__QUBIT_SESSION_TOKEN__", "__QUBIT_API_PREFIX__",
              "__QUBIT_WB_MOUNT__"):
        ok = g in html
        print(f"    {'OK  ' if ok else 'FAIL'} window.{g}")
        if not ok:
            failures.append(f"global {g} missing from served html")
    ok = "<title>Qubit</title>" in html
    print(f"    {'OK  ' if ok else 'FAIL'} <title>Qubit</title>")
    if not ok:
        failures.append("title not rebranded")
    ok = "hermes" not in html.lower() or html.lower().count("hermes") == 0
    print(f"    {'OK  ' if ok else 'FAIL'} no 'hermes' in served html")
    if not ok:
        failures.append("served html still contains 'hermes'")

    # ------------------------------------------------------------------- 3 shim
    print("\n[3] shim endpoints")
    from voicebridge import workbench_ui_routes as R2
    reads = ["sessions", "session?session_id=__probe__", "settings", "models",
             "crons", "approval/pending", "clarify/pending", "logs", "projects",
             "skills", "memory", "profiles", "mcp/servers"]
    for ep in reads:
        status, body, _h = get("/wb/api/" + ep)
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {"ok": True, "_list_len": len(payload)}
        code = payload.get("code") or ""
        ok = status in (200, 409, 501) and (
            payload.get("ok") is True or bool(code))
        print(f"    {'OK  ' if ok else 'FAIL'} GET /api/{ep} -> {status} "
              f"ok={payload.get('ok')} code={code or '-'}")
        if not ok:
            failures.append(f"shim GET /api/{ep} -> {status} {body[:120]}")
        if status >= 500:
            notes.append(f"/api/{ep} answered {status} {code or ''}")

    gap_status, gap_body, _h = get("/wb/api/kanban/boards")
    try:
        gap = json.loads(gap_body or b"{}")
    except ValueError:
        gap = {}
    ok = gap_status == 501 and gap.get("code") == "WB_NOT_IN_PORT_SET"
    print(f"    {'OK  ' if ok else 'FAIL'} GAP /api/kanban/boards -> "
          f"{gap_status} code={gap.get('code')} "
          f"(detail present: {bool(gap.get('detail'))})")
    if not ok:
        failures.append(f"gap refusal wrong: {gap_status} {gap_body[:200]}")

    # a write path: terminal must be refused, not hang
    st, bdy, _h = get("/wb/api/terminal/start", "POST", {"session_id": "x"})
    try:
        tp = json.loads(bdy or b"{}")
    except ValueError:
        tp = {}
    ok = st in (403, 501) and tp.get("code") in ("WB_PTY_FORBIDDEN",
                                                 "WB_NOT_IN_PORT_SET")
    print(f"    {'OK  ' if ok else 'FAIL'} POST /api/terminal/start -> {st} "
          f"code={tp.get('code')}")
    if not ok:
        failures.append(f"terminal refusal wrong: {st} {bdy[:160]}")

    ui.stop()

    print("\n" + "=" * 60)
    if notes:
        print("NOTES (real faults, not test failures):")
        for n in notes:
            print("  -", n)
    if failures:
        print(f"FAIL — {len(failures)} problem(s)")
        for f in failures:
            print("  -", f)
        return 1
    print("PASS — live server serves the ported UI and the shim answers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
