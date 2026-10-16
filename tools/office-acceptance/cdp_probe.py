#!/usr/bin/env python3
"""Raw-CDP probe: load a URL, capture network requests, console errors, DOM state."""
import json, subprocess, sys, time, tempfile, os, urllib.request

PORT = int(os.environ.get("CDP_PORT", "9501"))
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8890/qubit-office"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else ""
CHROME = "/opt/google/chrome/chrome"
import websocket  # websocket-client


def http(path, method="GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    return json.load(urllib.request.urlopen(req, timeout=10))


prof = tempfile.mkdtemp(prefix="cdp-office-")
proc = subprocess.Popen(
    [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--window-size=1305,800",
     f"--user-data-dir={prof}", f"--remote-debugging-port={PORT}",
     "--remote-allow-origins=*", "about:blank"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    env={**os.environ, "TMPDIR": "/tmp"})

events = []
try:
    for _ in range(60):
        try:
            http("/json/version"); break
        except Exception:
            time.sleep(0.3)
    tab = http("/json/new?about:blank", method="PUT")
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=30,
                                     suppress_origin=True)
    mid = [0]

    def send(method, params=None):
        mid[0] += 1
        ws.send(json.dumps({"id": mid[0], "method": method, "params": params or {}}))
        return mid[0]

    send("Network.enable")
    send("Page.enable")
    send("Runtime.enable")
    send("Log.enable")
    ws.settimeout(0.4)
    # seed token like the screenshot script does
    send("Page.navigate", {"url": URL})
    t0 = time.time()
    while time.time() - t0 < 12:
        try:
            msg = json.loads(ws.recv())
        except Exception:
            continue
        events.append(msg)

    reqs = {}
    for e in events:
        m = e.get("method")
        p = e.get("params", {})
        if m == "Network.requestWillBeSent":
            reqs[p["requestId"]] = p["request"]["url"]
        elif m == "Network.responseReceived":
            reqs.setdefault(p["requestId"], "")
            reqs[p["requestId"]] += f"  ->  {p['response']['status']} {p['response'].get('mimeType','')}"
        elif m == "Network.loadingFailed":
            reqs.setdefault(p["requestId"], "")
            reqs[p["requestId"]] += f"  ->  FAILED {p.get('errorText')} {p.get('blockedReason','')}"

    def ev(expr):
        i = send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": False})
        t0 = time.time()
        while time.time() - t0 < 6:
            try:
                msg = json.loads(ws.recv())
            except Exception:
                continue
            if msg.get("id") == i:
                return msg.get("result", {}).get("result", {}).get("value")
        return None

    dom = ev("document.body.innerText.slice(0,900)")
    errs = [e["params"]["exceptionDetails"].get("text", "") + " " +
            str(e["params"]["exceptionDetails"].get("exception", {}).get("description", ""))[:200]
            for e in events if e.get("method") == "Runtime.exceptionThrown"]
    con = [e["params"]["args"][0].get("value") if e["params"]["args"] else ""
           for e in events if e.get("method") == "Runtime.consoleAPICalled"
           and e["params"].get("type") in ("error", "warning")]
    logs = [e["params"]["entry"]["text"] for e in events
            if e.get("method") == "Log.entryAdded" and e["params"]["entry"].get("level") == "error"]

    print("=== REQUESTS ===")
    for rid, u in reqs.items():
        print(u[:200])
    print("=== DOM ===")
    print(dom)
    print("=== EXCEPTIONS ===", errs)
    print("=== CONSOLE w/e ===", con[:12])
    print("=== LOG errors ===", logs[:12])
finally:
    try:
        proc.terminate(); proc.wait(timeout=5)
    except Exception:
        proc.kill()
