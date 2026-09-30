#!/usr/bin/env python3
"""Read Gmail inbox via CDP against the logged-in Qubit Chromium profile.

Usage: read_gmail.py [N]   # top N emails (default 3)
Output: JSON [{sender, subject, snippet, time}]
"""
import json, sys, time, urllib.request, websocket

CDP = "http://127.0.0.1:9222"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 3

tabs = json.load(urllib.request.urlopen(CDP + "/json"))
target = None
for t in tabs:
    if t.get("type") == "page" and "mail.google.com" in t.get("url", ""):
        target = t["webSocketDebuggerUrl"]
        break
if not target:
    print(json.dumps({"error": "no gmail tab"})); sys.exit(1)

ws = websocket.create_connection(target, timeout=30)
_id = 0
def cmd(method, params=None):
    global _id
    _id += 1
    ws.send(json.dumps({"id": _id, "method": method, "params": params or {}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == _id:
            return m.get("result", {})

def js(expr):
    r = cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    return r.get("result", {}).get("value")

# ensure on inbox
if "mail.google.com" not in (js("location.href") or ""):
    cmd("Page.navigate", {"url": "https://mail.google.com/mail/u/0/#inbox"})
    time.sleep(6)

for _ in range(20):
    if js("document.querySelectorAll('tr.zA').length"):
        break
    time.sleep(1)

rows = js(f"""
(() => {{
  const rows = [...document.querySelectorAll('tr.zA')].slice(0, {N});
  return rows.map(r => {{
    const sender = r.querySelector('.yW span')?.textContent || '';
    const subject = r.querySelector('.bog')?.textContent.trim() || '';
    const snippet = r.querySelector('.y2')?.textContent?.trim() || '';
    const time = r.querySelector('.xT .xW')?.getAttribute('title')
      || r.querySelector('td.xW span')?.textContent || '';
    return {{sender, subject, snippet: snippet.slice(0, 90), time}};
  }});
}})()
""")

print(json.dumps(rows or [], ensure_ascii=False, indent=2))
ws.close()
