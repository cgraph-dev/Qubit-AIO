#!/usr/bin/env python3
"""Perplexity web search via logged-in browser session (ZERO API cost).

Uses the user's Perplexity Pro login in the Qubit Chromium profile over
CDP. Opens a search, waits for the answer to render, extracts text.

Usage: perplexity_search.py "query string" [max_chars]
Output: plain text (the answer body), ~5-20s latency.
"""
import json, sys, time, urllib.request, urllib.parse, websocket

CDP = "http://127.0.0.1:9222"
QUERY = sys.argv[1] if len(sys.argv) > 1 else ""
MAXC = int(sys.argv[2]) if len(sys.argv) > 2 else 4000
if not QUERY:
    print("usage: perplexity_search.py <query> [max_chars]"); sys.exit(1)

# find a perplexity tab or create one
tabs = json.load(urllib.request.urlopen(CDP + "/json"))
target = None
for t in tabs:
    if t.get("type") == "page" and "perplexity.ai" in t.get("url", ""):
        target = t["webSocketDebuggerUrl"]
        break
if not target:
    req = urllib.request.Request(
        CDP + "/json/new?" + urllib.parse.quote("https://www.perplexity.ai", safe=""),
        method="PUT")
    newtab = json.load(urllib.request.urlopen(req))
    target = newtab["webSocketDebuggerUrl"]
    time.sleep(3)

ws = websocket.create_connection(target, timeout=60)
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

# navigate to search
url = "https://www.perplexity.ai/search?q=" + urllib.parse.quote(QUERY)
cmd("Page.navigate", {"url": url})
print(f"searching: {QUERY}", file=sys.stderr)

# wait for answer content (poll for the prose answer block)
answer = None
for i in range(45):
    time.sleep(2)
    text = (js("document.querySelector('main div.prose') ? "
               "document.querySelector('main div.prose').innerText : ''") or "")
    if text and len(text) > 200:
        answer = text
        break
    text2 = js("document.querySelector('main') ? "
               "document.querySelector('main').innerText : ''") or ""
    if "Sorry" in text2[:300] and "couldn't" in text2[:500]:
        answer = text2
        break
    if i > 25 and len(text2) > 800:
        answer = text2
        break

if not answer:
    answer = js("document.querySelector('main') ? "
                "document.querySelector('main').innerText : ''") or "no answer"

lines = [l.strip() for l in answer.splitlines() if l.strip()]
skip = {"New Thread", "Home", "Discover", "Library", "Sign up", "Log in"}
body = [l for l in lines if l not in skip]
print("\n".join(body)[:MAXC])
ws.close()
