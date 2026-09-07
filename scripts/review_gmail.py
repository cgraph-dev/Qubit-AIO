#!/usr/bin/env python3
"""Fast Gmail review — himalaya -> CDP -> cache -> graceful message. <2s when cached."""
from __future__ import annotations
import json, os, sys, subprocess, time
from pathlib import Path

CACHE_DIR = Path.home() / ".cache" / "qubit-voicebridge" / "inboxes"
CACHE_FILE = CACHE_DIR / "gmail.json"
CACHE_TTL_S = 300  # 5 min fresh
HIMALAYA = os.path.expanduser("~/.local/bin/himalaya")
VENV_PY = sys.executable  # portable: the bridge venv python invokes this script
CDP_SCRIPT = Path.home() / ".hermes" / "skills" / "communication" / "social-inbox-check" / "scripts" / "read_gmail.py"

def cache_load(max_age_s: int = 3600):
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

def try_himalaya(n=12):
    """Try himalaya envelope list --json (fast IMAP). Returns list or None."""
    cfg = Path.home() / ".config" / "himalaya" / "config.toml"
    if not cfg.exists():
        return None
    if not Path(HIMALAYA).exists():
        return None
    try:
        proc = subprocess.run(
            [HIMALAYA, "envelope", "list", "--json", "-s", str(n)],
            capture_output=True, text=True, timeout=6
        )
        if proc.returncode != 0:
            return None
        raw = (proc.stdout or "").strip()
        if not raw:
            return None
        data = json.loads(raw)
        # himalaya JSON is list of envelopes or {envelopes: [...]}
        if isinstance(data, dict) and "envelopes" in data:
            data = data["envelopes"]
        if not isinstance(data, list):
            return None
        out = []
        for e in data[:n]:
            # envelope fields vary by version; be defensive
            sender = ""
            if isinstance(e.get("from"), list) and e["from"]:
                sender = e["from"][0].get("name") or e["from"][0].get("address") or str(e["from"][0])
            elif isinstance(e.get("from"), dict):
                sender = e["from"].get("name") or e["from"].get("address") or ""
            elif e.get("sender"):
                sender = str(e["sender"])
            out.append({
                "sender": sender or (e.get("from") or "?"),
                "subject": e.get("subject") or "(no subject)",
                "snippet": (e.get("snippet") or e.get("preview") or "")[:120],
                "date": e.get("date") or e.get("internal_date") or "",
                "seen": "seen" in str(e.get("flags") or "").lower() or bool(e.get("seen")),
                "flagged": "flagged" in str(e.get("flags") or "").lower() or bool(e.get("flagged")),
            })
        return out
    except Exception:
        return None

def try_cdp(n=10):
    if not CDP_SCRIPT.exists():
        return None
    try:
        proc = subprocess.run([VENV_PY, str(CDP_SCRIPT), str(n)], capture_output=True, text=True, timeout=12)
        if proc.returncode != 0:
            return None
        data = json.loads((proc.stdout or "").strip() or "[]")
        if isinstance(data, dict) and "error" in data:
            return None
        if not isinstance(data, list) or not data:
            return None
        out = []
        for r in data[:n]:
            out.append({
                "sender": r.get("sender") or r.get("from") or "?",
                "subject": r.get("subject") or "(no subject)",
                "snippet": (r.get("snippet") or "")[:120],
                "date": r.get("time") or r.get("date") or "",
                "seen": False,  # CDP rows don't expose seen reliably
                "flagged": False,
            })
        return out
    except Exception:
        return None

def _parse_date(date_str: str):
    """Parse many email date formats to datetime, or None."""
    if not date_str: return None
    s = date_str.strip().replace(",", "")
    # Try common formats
    for fmt in ("%a %b %d %H:%M:%S %Y", "%a %d %b %Y %H:%M:%S %z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%d %b %Y %H:%M"):
        try:
            import datetime as _dt
            d = _dt.datetime.strptime(s[:len(fmt)+10], fmt)
            if d.tzinfo is None: d = d.replace(tzinfo=_dt.timezone.utc)
            return d
        except: pass
    # Fallback: try email.utils
    try:
        import email.utils as _eu
        t = _eu.parsedate_tz(s)
        if t:
            import datetime as _dt
            ts = _eu.mktime_tz(t)
            return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc)
    except: pass
    return None

def _human_age(date_str: str) -> str:
    d = _parse_date(date_str or "")
    if not d:
        # fallback: normalize date_str truncated
        s = (date_str or "").strip()[:24]
        return s if s else "recently"
    import datetime as _dt
    now = _dt.datetime.now(tz=_dt.timezone.utc)
    try:
        delta = now - d
        secs = int(delta.total_seconds())
        if secs < 0: secs = 0
        if secs < 90: return "just now"
        if secs < 3600: return f"{secs//60} minutes ago"
        if secs < 7200: return "an hour ago"
        if secs < 86400: return f"{secs//3600} hours ago"
        if secs < 172800: return "yesterday"
        if secs < 604800: return f"{secs//86400} days ago"
        # older: show date
        return d.strftime("%b %d")
    except: return date_str[:24]

def categorize(items):
    promo_domains = ("noreply", "no-reply", "marketing", "newsletter", "promo", "unsubscribe")
    social_domains = ("facebook", "instagram", "linkedin", "twitter", "x.com", "tiktok", "youtube")
    cats = {"primary": 0, "promotions": 0, "social": 0, "important": 0, "unread": 0}
    for it in items:
        s = (it.get("sender","") + " " + it.get("subject","") + " " + it.get("snippet","")).lower()
        is_promo = any(k in s for k in promo_domains) or "unsubscribe" in s
        is_social = any(k in s for k in social_domains)
        if is_promo:
            cats["promotions"] += 1
        elif is_social:
            cats["social"] += 1
        else:
            cats["primary"] += 1
        if it.get("flagged"):
            cats["important"] += 1
        if not it.get("seen"):
            # if we have no seen info, don't overcount — only count explicit unread
            # CDP path sets seen=False for all, so skip unread there
            pass
    # For himalaya we have accurate seen; for CDP we treat all as unknown
    # simpler: count where seen==False and we have real flag
    # Actually just use seen field when available
    real_unread = sum(1 for it in items if it.get("seen") is False)
    # If all are False (CDP), don't report unread as total
    if items and all(not it.get("seen") for it in items) and len(items) > 1:
        # likely CDP — suppress unread count
        cats["unread"] = 0
    else:
        cats["unread"] = real_unread
    # important from flagged or keywords
    important_kw = ("urgent", "payment", "invoice", "action required", "deadline", "verify", "security")
    for it in items:
        s = (it.get("subject","") + " " + it.get("snippet","")).lower()
        if any(k in s for k in important_kw):
            cats["important"] += 1
    # dedupe important (cap at len)
    cats["important"] = min(cats["important"], len(items))
    return cats

def print_report(items, source: str, cache_age=None):
    n = len(items)
    cats = categorize(items)
    hdr = f"GMAIL — {n} message{'s' if n!=1 else ''}"
    if source == "cache" and cache_age is not None:
        hdr += f" (cached {cache_age}s ago)"
    elif source:
        hdr += f" ({source})"
    # categories line
    cat_line = f"Categories: Primary {cats['primary']} | Promotions {cats['promotions']} | Social {cats['social']} | Important {cats['important']} | Unread {cats['unread']}"
    counts = f"Counts: total={n} unread={cats['unread']} important={cats['important']} promotions={cats['promotions']} social={cats['social']}"
    lines = [hdr, cat_line, counts, ""]
    if not items:
        lines.append("(no messages found)")
    else:
        for i, it in enumerate(items[:12], 1):
            flags = []
            if it.get("flagged"):
                flags.append("IMPORTANT")
            if it.get("seen") is False:
                flags.append("UNREAD")
            flag_str = f" [{','.join(flags)}]" if flags else ""
            subj = (it.get("subject") or "(no subject)").strip()[:80]
            sender = (it.get("sender") or "?").strip()[:40]
            age = _human_age(it.get("date") or "")
            date = (it.get("date") or "").strip()[:24]
            snippet = (it.get("snippet") or "").strip()[:90]
            lines.append(f"{i}.{flag_str} From: {sender} | Subject: {subj} | {age}")
            lines.append(f"   When: {age} ({date})" if date != age else f"   When: {age}")
            if snippet:
                lines.append(f"   Preview: {snippet}")
    # Spoken hook: 2-sentence who+when so the voice actually says names and times (not just categories)
    if items:
        top = items[:3]
        parts = []
        for it in top:
            who = (it.get("sender") or "?").split("<")[0].strip()[:32]
            subj = (it.get("subject") or "(no subject)").strip()[:48]
            when = _human_age(it.get("date") or "")
            parts.append(f"{who} — {subj}, {when}")
        lines.append("")
        lines.append("Spoken: Latest — " + "; ".join(parts) + ".")
    lines.append("")
    lines.append(f"Source: {source}" + (f" (cache {cache_age}s old)" if cache_age is not None else ""))
    print("\n".join(lines))

def main():
    q = sys.argv[1] if len(sys.argv) > 1 else ""
    # 1) fresh cache -> instant (<50ms)
    fresh = cache_load(max_age_s=CACHE_TTL_S)
    if fresh and isinstance(fresh.get("items"), list) and fresh["items"]:
        print_report(fresh["items"], source="cache", cache_age=fresh.get("_cache_age_s"))
        return 0
    # 2) try live himalaya
    items = try_himalaya(12)
    if items is not None and len(items) > 0:
        cache_save({"items": items, "ts": time.time(), "source": "himalaya"})
        print_report(items, source="live (himalaya)")
        return 0
    # 2b) try CDP
    if items is None or len(items) == 0:
        cdp_items = try_cdp(10)
        if cdp_items:
            cache_save({"items": cdp_items, "ts": time.time(), "source": "cdp"})
            print_report(cdp_items, source="live (browser)")
            return 0
    # 3) stale cache fallback (even if old)
    stale = cache_load(max_age_s=86400*7)
    if stale and isinstance(stale.get("items"), list) and stale["items"]:
        print_report(stale["items"], source="cache", cache_age=stale.get("_cache_age_s"))
        return 0
    # 4) graceful not-configured message (exit 0 so voice still speaks)
    print("GMAIL — not configured yet")
    print("Categories: Primary 0 | Promotions 0 | Social 0 | Important 0 | Unread 0")
    print("Counts: total=0 unread=0 important=0 promotions=0 social=0")
    print("")
    print("No Gmail access found. To enable:")
    print("  • Option A (IMAP, fastest): run `himalaya account configure` and add your Gmail App Password")
    print("    (needs App Password if 2FA is on — https://myaccount.google.com/apppasswords)")
    print("    Config file: ~/.config/himalaya/config.toml — see ~/.hermes/skills/email/himalaya/references/configuration.md")
    print("  • Option B (browser): launch Chromium with CDP and log into Gmail:")
    print("    DISPLAY=:0 /snap/bin/chromium --user-data-dir=<your-chromium-profile> --profile-directory=Default --remote-debugging-port=9222 --remote-allow-origins=* https://mail.google.com")
    print("    Then `curl -s http://127.0.0.1:9222/json | grep mail.google.com` should list the tab.")
    if q:
        print(f"  (query was: {q[:80]})")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        print(f"GMAIL — error: {e}")
        print("Run `himalaya account configure` or log into Gmail in Chromium to enable.")
        sys.exit(0)
