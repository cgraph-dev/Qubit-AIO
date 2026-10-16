#!/usr/bin/env python3
"""Inspect the /qubit-office iframe: document readiness, script errors inside it, store state."""
import json, subprocess, sys, time, tempfile, os, urllib.request
import websocket

PORT = int(os.environ.get("CDP_PORT", "9502"))
CHROME = "/opt/google/chrome/chrome"


def http(path, method="GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    return json.load(urllib.request.urlopen(req, timeout=10))


prof = tempfile.mkdtemp(prefix="cdp-office2-")
proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
                         "--window-size=1305,800", f"--user-data-dir={prof}",
                         f"--remote-debugging-port={PORT}", "--remote-allow-origins=*",
                         "about:blank"], stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, env={**os.environ, "TMPDIR": "/tmp"})
events = []
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

    def wait(i, t=8):
        t0 = time.time()
        while time.time() - t0 < t:
            try:
                msg = json.loads(ws.recv())
            except Exception:
                continue
            events.append(msg)
            if msg.get("id") == i:
                return msg
        return None

    send("Page.enable"); send("Runtime.enable"); send("Network.enable"); send("Log.enable")
    wait(send("Page.navigate", {"url": "http://127.0.0.1:8890/qubit-office"}), 12)
    time.sleep(6)

    def ev(expr):
        r = wait(send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": False}), 8)
        return (r or {}).get("result", {}).get("result", {}).get("value")

    frames = ev("""(() => {
      const f = document.querySelector('iframe');
      if (!f) return 'NO_IFRAME';
      const d = f.contentDocument;
      let scripts = [], errs = [];
      if (d) {
        scripts = [...d.querySelectorAll('script')].map(s => s.src || '(inline)');
        errs = window.__IFRAME_ERRORS__ || null;
      }
      return JSON.stringify({
        src: f.src,
        contentDocument: !!d,
        url: d && d.location.href,
        readyState: d && d.readyState,
        lang: d && d.documentElement.lang,
        bodyText: d && d.body ? d.body.innerText.slice(0, 400) : null,
        scriptCount: scripts.length,
        scripts: scripts,
        manifest: d && d.querySelector('link[rel=manifest]') && d.querySelector('link[rel=manifest]').href,
        appScript: d && [...d.querySelectorAll('script')].map(s=>s.src).filter(s=>s.includes('app-')),
        globals: d ? Object.keys(d.defaultView || {}).filter(k => /office|store|snapshot|qubit/i.test(k)).slice(0,20) : null
      }, null, 1);
    })()""")
    print("=== IFRAME ===")
    print(frames)
    print("=== FRAME TREE ===")
    print(ev("(()=>{const f=document.querySelector('iframe');return f?JSON.stringify({w:f.clientWidth,h:f.clientHeight}):'none'})()"))
finally:
    try:
        proc.terminate(); proc.wait(timeout=5)
    except Exception:
        proc.kill()
