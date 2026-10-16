#!/usr/bin/env python3
"""Evaluate the vendored __vite__mapDeps helper in a real page to prove doubled URLs."""
import json, subprocess, sys, time, tempfile, os, urllib.request
import websocket

PORT = 9503
CHROME = "/opt/google/chrome/chrome"


def http(path, method="GET"):
    return json.load(urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}", method=method), timeout=10))


prof = tempfile.mkdtemp(prefix="cdp-eval-")
proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
                         f"--user-data-dir={prof}", f"--remote-debugging-port={PORT}",
                         "--remote-allow-origins=*", "about:blank"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        env={**os.environ, "TMPDIR": "/tmp"})
try:
    for _ in range(60):
        try:
            http("/json/version"); break
        except Exception:
            time.sleep(0.3)
    tab = http("/json/new?about:blank", method="PUT")
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=30, suppress_origin=True)
    mid = [0]

    def send(method, params=None):
        mid[0] += 1
        ws.send(json.dumps({"id": mid[0], "method": method, "params": params or {}}))
        return mid[0]

    def wait(i, t=10):
        t0 = time.time()
        while time.time() - t0 < t:
            try:
                msg = json.loads(ws.recv())
            except Exception:
                continue
            if msg.get("id") == i:
                return msg
        return None

    send("Page.enable"); send("Runtime.enable")
    wait(send("Page.navigate", {"url": "http://127.0.0.1:8890/qubit-office"}), 12)
    time.sleep(5)

    def ev(expr):
        r = wait(send("Runtime.evaluate", {"expression": expr, "returnByValue": True}), 10)
        v = (r or {}).get("result", {}).get("result", {})
        return v.get("value", v.get("description"))

    # Evaluate the EXACT served helper against a relative dep name, inside the
    # iframe's origin+path context (the iframe is same-origin).
    print("== resolve relative dep ==")
    print(ev("""(function(){
      const loc = {origin: location.origin, pathname: '/plugin-apps/qubit-office/habblaud/'};
      const rel = 'bundle/app-U4Pwh2XA.js';
      return new URL(rel, loc.origin + `/plugin-apps/qubit-office/habblaud/`).href;
    })()"""))
    print("== resolve absolute dep (what the client's own resolver yields) ==")
    print(ev("""(function(){
      const base = location.origin + `/plugin-apps/qubit-office/habblaud/`;
      const abs = new URL('bundle/app-U4Pwh2XA.js', location.origin + '/plugin-apps/qubit-office/habblaud/bundle/main-DND_b4_E.js').href;
      return JSON.stringify({abs, doubled: new URL(abs, base).href});
    })()"""))
finally:
    try:
        proc.terminate(); proc.wait(timeout=5)
    except Exception:
        proc.kill()
