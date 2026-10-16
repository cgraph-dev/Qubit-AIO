#!/usr/bin/env python3
"""Load a page in headless Chrome over CDP; report console + network + DOM.

Usage: cdp_probe.py <url> [wait_seconds] [--eval "js expression"]
"""
import json
import sys
import time
import urllib.request
from websocket import create_connection

CDP_PORT = 9511
TARGET_URL = sys.argv[1]
WAIT = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0
EVAL = ""
if "--eval" in sys.argv:
    EVAL = sys.argv[sys.argv.index("--eval") + 1]


def http(path, method="GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{CDP_PORT}{path}",
                                 method=method)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())


def main():
    tgt = http(f"/json/new?{TARGET_URL}", method="PUT")
    ws = create_connection(tgt["webSocketDebuggerUrl"], timeout=30)
    mid = [0]
    console = []
    requests = []
    failed = []

    def send(method, params=None):
        mid[0] += 1
        ws.send(json.dumps({"id": mid[0], "method": method,
                            "params": params or {}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == mid[0]:
                return msg

    send("Runtime.enable")
    send("Network.enable")
    send("Page.enable")
    send("Page.reload", {"ignoreCache": True})

    deadline = time.time() + WAIT
    while time.time() < deadline:
        ws.settimeout(max(0.1, deadline - time.time()))
        try:
            msg = json.loads(ws.recv())
        except Exception:
            break
        if "method" not in msg:
            continue
        m = msg["method"]
        p = msg.get("params", {})
        if m == "Runtime.consoleAPICalled":
            console.append({"type": p.get("type"),
                            "args": [a.get("value", a.get("description", ""))
                                     for a in p.get("args", [])]})
        elif m == "Network.responseReceived":
            requests.append({"url": p["response"]["url"],
                             "status": p["response"]["status"]})
            if p["response"]["status"] >= 400:
                failed.append({"url": p["response"]["url"],
                               "status": p["response"]["status"]})
        elif m == "Network.loadingFailed":
            failed.append({"error": p.get("errorText"),
                           "url": p.get("requestId")})

    out = {"url": TARGET_URL, "console": console[-60:],
           "failures": failed,
           "apiRequests": [r for r in requests if "/api/" in r["url"]],
           "requestCount": len(requests)}
    if EVAL:
        probe = send("Runtime.evaluate",
                     {"expression": EVAL, "returnByValue": True,
                      "awaitPromise": True})
        out["eval"] = probe.get("result", {}).get("result", {}).get("value")
    print(json.dumps(out, indent=2))
    ws.close()


if __name__ == "__main__":
    main()
