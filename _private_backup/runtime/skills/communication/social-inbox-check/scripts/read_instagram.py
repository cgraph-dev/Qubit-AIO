#!/usr/bin/env python3
"""Read Instagram DM inbox via CDP (deterministic, 0 LLM tokens).

Usage: read_instagram.py [N]   # top N conversations (default 3)
Output: JSON [{name, text, unread}]
"""
import json, sys, time, urllib.request, websocket

CDP = "http://127.0.0.1:9222"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 3

tabs = json.load(urllib.request.urlopen(CDP + "/json"))
target = None
for t in tabs:
    if t.get("type") == "page" and "instagram.com" in t.get("url", ""):
        target = t["webSocketDebuggerUrl"]
        break
if not target:
    print(json.dumps({"error": "no instagram tab"})); sys.exit(1)

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

cmd("Page.navigate", {"url": "https://www.instagram.com/direct/inbox/"})
time.sleep(5)

data = js(f"""
(() => {{
  const seen = new Set(); const out = [];
  const items = document.querySelectorAll('div[role="listbox"] > div > div > div, div[role="listbox"] [role="listitem"], div[role="button"]');
  for (const el of items) {{
    const txt = el.innerText || '';
    if (!txt || txt.length < 2) continue;
    const lines = txt.split('\\n').filter(Boolean);
    const name = lines[0].slice(0, 40);
    if (seen.has(name) || ['Primary','General','Requests',"What's new...",'Your note'].includes(name)) continue;
    seen.add(name);
    out.push({{ name, text: txt.replace(/\\n/g, ' | ').slice(0, 160), unread: txt.includes('Unread') }});
    if (out.length >= {N}) break;
  }}
  return out;
}})()
""")

print(json.dumps(data or [], ensure_ascii=False, indent=2))
ws.close()
