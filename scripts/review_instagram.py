#!/usr/bin/env python3
"""Fast Instagram review — CDP (social-inbox-check) -> cache -> graceful."""
from __future__ import annotations
import json, sys, subprocess, time
from pathlib import Path

CACHE_DIR = Path.home() / ".cache" / "openclaw-voice-bridge" / "inboxes"
CACHE_FILE = CACHE_DIR / "instagram.json"
CACHE_TTL_S = 300
VENV_PY = sys.executable  # portable: the bridge venv python invokes this script
CDP_SCRIPT = Path.home() / ".hermes/skills/communication/social-inbox-check/scripts/read_instagram.py"

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
    if not CDP_SCRIPT.exists():
        return None
    try:
        proc = subprocess.run([str(VENV_PY), str(CDP_SCRIPT), str(n)], capture_output=True, text=True, timeout=12)
        if proc.returncode != 0:
            return None
        raw = (proc.stdout or "").strip()
        if not raw:
            return None
        data = json.loads(raw)
        if isinstance(data, dict) and "error" in data:
            return None
        if not isinstance(data, list):
            return None
        return data
    except Exception:
        return None

def normalize(items):
    out=[]
    for r in items:
        out.append({
            "name": (r.get("name") or "?")[:40],
            "preview": (r.get("text") or r.get("preview") or r.get("snippet") or "")[:120],
            "time": r.get("time") or r.get("date") or "",
            "unread": bool(r.get("unread")),
        })
    return out

def print_report(items, source, cache_age=None):
    n=len(items)
    unread=sum(1 for x in items if x.get("unread"))
    hdr=f"INSTAGRAM — {n} DM{'s' if n!=1 else ''}"
    if source=="cache" and cache_age is not None:
        hdr+=f" (cached {cache_age}s ago)"
    elif source:
        hdr+=f" ({source})"
    cat_line=f"Categories: Primary {n} | Promotions 0 | Social 0 | Important {unread} | Unread {unread}"
    counts=f"Counts: total={n} unread={unread} important={unread}"
    lines=[hdr, cat_line, counts, ""]
    if not items:
        lines.append("(no DMs found)")
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
    print("INSTAGRAM — not configured yet")
    print("Categories: Primary 0 | Promotions 0 | Social 0 | Important 0 | Unread 0")
    print("Counts: total=0 unread=0 important=0")
    print("")
    print("No Instagram access found. To enable:")
    print("  1) Launch Chromium with CDP and log into Instagram:")
    print("     DISPLAY=:0 /snap/bin/chromium --user-data-dir=<your-chromium-profile> --profile-directory=Default --remote-debugging-port=9222 --remote-allow-origins=* https://www.instagram.com/direct/inbox/")
    print("  2) Verify: curl -s http://127.0.0.1:9222/json | grep instagram.com")
    print("  3) Then say 'check instagram' again.")
    if q:
        print(f"  (query was: {q[:80]})")
    return 0

if __name__=="__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        print(f"INSTAGRAM — error: {e}")
        print("Launch Chromium with CDP and log into https://www.instagram.com/direct/inbox/ to enable.")
        sys.exit(0)
