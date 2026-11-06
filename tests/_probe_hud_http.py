"""Prove the framed-HUD grant on the wire, not in the source.

Boots the REAL ``WebUIHandler`` on an ephemeral loopback port with a minimal
app stub and a temporary dashboard dist, then reads the actual response
headers the browser would see:

  /qubit-hud  -> X-Frame-Options: SAMEORIGIN, frame-ancestors 'self'
  /sessions   -> X-Frame-Options: DENY,       frame-ancestors 'none'
  /qubit-hud/ -> SAMEORIGIN (trailing slash is the same route)
  /qubit-hud-other, /qubit-workbench, /ops -> DENY

Run: .venv/bin/python /tmp/probe_hud_http.py
"""
from __future__ import annotations

import gzip
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, "/tmp/qubit-v2-office-hudfix")

from voicebridge import webui as ui  # noqa: E402


class _App:
    api_token = ""
    allowed_origins: tuple = ()
    orch = None
    _use_frontend_dir = False


class _Handler(ui.WebUIHandler):
    """The real handler, with the four host lookups a stub has to answer."""

    def log_message(self, *a):  # silence
        pass

    def _qubit_dist(self):
        return HUD_DIST

    def _peer_is_loopback(self):
        return True

    def _token_bootstrap_allowed(self):
        return True

    def _issue_office_nonce(self):
        return "probe-nonce"


def _serve(port_box, dist):
    global HUD_DIST
    HUD_DIST = dist
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.app = _App()
    port_box.append(httpd.server_address[1])
    httpd.serve_forever()


def main() -> int:
    dist = Path(tempfile.mkdtemp()) / "dist"
    dist.mkdir(parents=True)
    # Big enough to cross _MIN_GZIP_BYTES so the gzip path is exercised too.
    (dist / "index.html").write_text(
        "<!doctype html><html><head></head><body>"
        + "<p>hud shell</p>" * 200 + "</body></html>")

    box: list = []
    t = threading.Thread(target=_serve, args=(box, dist), daemon=True)
    t.start()
    while not box:
        pass
    base = f"http://127.0.0.1:{box[0]}"

    cases = [
        ("/qubit-hud", "SAMEORIGIN", "'self'"),
        ("/qubit-hud/", "SAMEORIGIN", "'self'"),
        ("/qubit-hud?tab=cost", "SAMEORIGIN", "'self'"),
        ("/sessions", "DENY", "'none'"),
        ("/qubit-hud-other", "DENY", "'none'"),
        ("/qubit-workbench", "DENY", "'none'"),
        ("/ops", "DENY", "'none'"),
        ("/", "DENY", "'none'"),
    ]
    failures = 0
    for path, want_xfo, want_anc in cases:
        req = urllib.request.Request(base + path,
                                     headers={"Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                hdrs = {k.lower(): v for k, v in r.headers.items()}
                body = r.read()
                status = r.status
        except urllib.error.HTTPError as e:  # a refusal is still an answer
            hdrs = {k.lower(): v for k, v in e.headers.items()}
            body = e.read()
            status = e.code
        if hdrs.get("content-encoding") == "gzip":
            body = gzip.decompress(body)
        xfo = hdrs.get("x-frame-options", "")
        csp = hdrs.get("content-security-policy", "")
        ok = (status == 200 and xfo == want_xfo
              and f"frame-ancestors {want_anc}" in csp)
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'} {path:22s} {status} "
              f"XFO={xfo:11s} csp_anc={'self' if "'self'" in csp else 'none'} "
              f"bytes={len(body)}")
        if not ok:
            print("   csp:", csp)
    print(f"\n{len(cases)-failures}/{len(cases)} on-the-wire header cases pass")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
