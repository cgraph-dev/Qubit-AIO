#!/usr/bin/env python3
"""Read Messenger chat list via CDP (deterministic, 0 LLM tokens).

Usage: read_messenger.py [N]   # top N chats (default 3)
Output: JSON [{name, preview, href}]
"""
import json, sys, time, urllib.request, websocket

CDP = "http://127.0.0.1:9222"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 3

tabs = json.load(urllib.request.urlopen(CDP + "/json"))
target = None
for t in tabs:
    if t.get("type") == "page" and "messenger.com" in t.get("url", ""):
        target = t["webSocketDebuggerUrl"]
        break
if not target:
    print(json.dumps({"error": "no messenger tab"})); sys.exit(1)

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

# navigate to the plain chat list first (avoids e2ee redirect into a thread)
cmd("Page.navigate", {"url": "https://www.messenger.com/"})
time.sleep(5)

data = js(f"""
(() => {{
  const links = [...document.querySelectorAll('a[href*="/t/"]')]
    .filter(a => !a.href.includes('/marketplace') && !a.href.includes('/requests') && !a.href.includes('/archived'));
  const seen = new Set(); const out = [];
  for (const a of links) {{
    const href = a.href;
    const m = href.match(/\\/t\\/(\\d+)/);
    if (!m || seen.has(m[1])) continue;
    seen.add(m[1]);
    const row = a.closest('[role="row"], li, div') || a;
    const name = (a.getAttribute('aria-label') || a.innerText.split('\\n')[0] || '?').trim().slice(0, 40);
    out.push({{ name, href, preview: row.innerText.replace(/\\n/g, ' | ').slice(0, 140) }});
    if (out.length >= {N}) break;
  }}
  return out;
}})()
""")

# fallback: rendered rows
if not data:
    data = js(f"""
    (() => {{
      const rows = [...document.querySelectorAll('li[role="row"], [role="listitem"]')].slice(0, {N});
      return rows.map(r => ({{ name: (r.innerText.split('\\n')[0]||'?').slice(0,40), preview: r.innerText.replace(/\\n/g,' | ').slice(0,140) }}));
    }})()
    """)

print(json.dumps(data or [], ensure_ascii=False, indent=2))
ws.close()
