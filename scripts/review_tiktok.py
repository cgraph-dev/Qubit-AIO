#!/usr/bin/env python3
"""Fast TikTok review — CDP (if available) -> cache -> graceful."""
from __future__ import annotations
import json, sys, time, urllib.request
from pathlib import Path

CACHE_DIR = Path.home() / ".cache" / "qubit-voicebridge" / "inboxes"
CACHE_FILE = CACHE_DIR / "tiktok.json"
CACHE_TTL_S = 300
CDP = "http://127.0.0.1:9222"

def cache_load(max_age_s=3600):
    try:
        if not CACHE_FILE.exists():
            return None
        age = time.time() - CACHE_FILE.stat().st_mtime
        if age > max_age_s:
            return None
        j = json.loads(CACHE_FILE.read_text())
        j["_cache_age_s"] = int(age)
        return j
    except Exception:
        return None

def cache_save(data):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.rename(CACHE_FILE)
    except Exception:
        pass

def try_cdp(n=10):
    """Best-effort TikTok inbox via CDP tab at tiktok.com."""
    try:
        import websocket  # type: ignore
    except Exception:
        return None
    try:
        tabs = json.loads(urllib.request.urlopen(CDP + "/json", timeout=3).read())
    except Exception:
        return None
    target=None
    for t in tabs:
        url = t.get("url","")
        if t.get("type")=="page" and "tiktok.com" in url:
            target=t.get("webSocketDebuggerUrl")
            break
    if not target:
        return None
    try:
        ws = websocket.create_connection(target, timeout=10)
        _id=0
        def cmd(m, p=None):
            nonlocal _id
            _id+=1
            ws.send(json.dumps({"id":_id,"method":m,"params":p or {}}))
            while True:
                msg=json.loads(ws.recv())
                if msg.get("id")==_id:
                    return msg.get("result",{})
        def js(expr):
            r=cmd("Runtime.evaluate",{"expression":expr,"returnByValue":True})
            return r.get("result",{}).get("value")
        # Ensure on inbox/messages
        href=js("location.href") or ""
        if "tiktok.com" not in href:
            cmd("Page.navigate",{"url":"https://www.tiktok.com/messages"})
            time.sleep(4)
        # Try common selectors for TikTok inbox/messages
        data=js(f"""
(() => {{
  const rows=[...document.querySelectorAll('[data-e2e=\"message-item\"], [data-e2e=\"notification-item\"], li, [role=\"listitem\"]')].slice(0,{n});
  if(!rows.length) return [];
  return rows.map(r=>{{
    const txt=(r.innerText||'').replace(/\\n/g,' | ').slice(0,160);
    const name=(txt.split(' | ')[0]||'?').slice(0,40);
    return {{name, text: txt, time: ''}};
  }}).filter(x=>x.text && x.text.length>3);
}})()
""")
        ws.close()
        if not data:
            return None
        return data
    except Exception:
        try: ws.close()  # type: ignore
        except Exception: pass
        return None

def normalize(items):
    out=[]
    for r in items:
        out.append({
            "name": (r.get("name") or r.get("sender") or "?")[:40],
            "preview": (r.get("text") or r.get("preview") or r.get("snippet") or "")[:120],
            "time": r.get("time") or r.get("date") or "",
            "unread": bool(r.get("unread")),
        })
    return out

def print_report(items, source, cache_age=None):
    n=len(items)
    unread=sum(1 for x in items if x.get("unread"))
    hdr=f"TIKTOK — {n} notification{'s' if n!=1 else ''}"
    if source=="cache" and cache_age is not None:
        hdr+=f" (cached {cache_age}s ago)"
    elif source:
        hdr+=f" ({source})"
    cat_line=f"Categories: Primary {n} | Promotions 0 | Social 0 | Important {unread} | Unread {unread}"
    counts=f"Counts: total={n} unread={unread} important={unread}"
    lines=[hdr, cat_line, counts, ""]
    if not items:
        lines.append("(no notifications found)")
    else:
        for i,it in enumerate(items[:12],1):
            flag=" [UNREAD]" if it.get("unread") else ""
            lines.append(f"{i}.{flag} From: {it['name']} | Preview: {it['preview'][:90]} | Date: {it['time'][:24]}")
    lines.append("")
    lines.append(f"Source: {source}" + (f" (cache {cache_age}s old)" if cache_age is not None else ""))
    print("\n".join(lines))

def main():
    q=sys.argv[1] if len(sys.argv)>1 else ""
    fresh=cache_load(CACHE_TTL_S)
    if fresh and isinstance(fresh.get("items"),list) and fresh["items"]:
        print_report(normalize(fresh["items"]), source="cache", cache_age=fresh.get("_cache_age_s"))
        return 0
    data=try_cdp(10)
    if data:
        cache_save({"items": data, "ts": time.time(), "source":"cdp"})
        print_report(normalize(data), source="live (browser)")
        return 0
    stale=cache_load(86400*7)
    if stale and isinstance(stale.get("items"),list) and stale["items"]:
        print_report(normalize(stale["items"]), source="cache", cache_age=stale.get("_cache_age_s"))
        return 0
    print("TIKTOK — not configured yet")
    print("Categories: Primary 0 | Promotions 0 | Social 0 | Important 0 | Unread 0")
    print("Counts: total=0 unread=0 important=0")
    print("")
    print("No TikTok access found. To enable:")
    print("  1) Launch Chromium with CDP and log into TikTok:")
    print("     DISPLAY=:0 /snap/bin/chromium --user-data-dir=<your-chromium-profile> --profile-directory=Default --remote-debugging-port=9222 --remote-allow-origins=* https://www.tiktok.com/messages")
    print("  2) Verify: curl -s http://127.0.0.1:9222/json | grep tiktok.com")
    print("  3) Then say 'check tiktok' again.")
    print("  (TikTok inbox is limited — browser login is the only supported method)")
    if q:
        print(f"  (query was: {q[:80]})")
    return 0

if __name__=="__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        print(f"TIKTOK — error: {e}")
        print("Launch Chromium with CDP and log into https://www.tiktok.com/messages to enable.")
        sys.exit(0)
