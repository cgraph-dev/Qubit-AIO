#!/usr/bin/env python3
"""Weather for a city — voice-bridge local-script. Uses wttr.in (free, no key)."""
import sys, json, urllib.request, urllib.parse, re

def wttr_forecast(query: str) -> str:
    m = re.search(r"weather\s+(?:in|for|at|near)?\s*([A-Za-z\s'\-]+)", query, re.I)
    city = (m.group(1).strip() if m else query).strip()
    city = re.sub(r"\b(today|tomorrow|tommorrow|tommorow|now|please|thanks|corest)\b.*", "", city, flags=re.I).strip()
    # mangled ASR "Corest" -> Bucharest fallback
    if not city or len(city) < 3 or "corest" in query.lower() or "search was" in query.lower():
        # if query mentions weather at all, assume Bucharest (user's timezone)
        if "bucharest" in query.lower() or "corest" in query.lower() or not city:
            city = "Bucharest"
    if not city:
        city = "Bucharest"
    url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1"
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            j = json.loads(r.read())
        cc = (j.get("current_condition") or [{}])[0]
        today = (j.get("weather") or [{}])[0]
        area = (j.get("nearest_area") or [{}])[0]
        name = (area.get("areaName") or [{}])[0].get("value") or city
        temp_c = cc.get("temp_C","?")
        feels = cc.get("FeelsLikeC","?")
        desc = (cc.get("weatherDesc") or [{}])[0].get("value","")
        wind = cc.get("windspeedKmph","?") + " km/h " + (cc.get("winddir16Point") or "")
        hum = cc.get("humidity","?") + "%"
        maxt = today.get("maxtempC","?")
        mint = today.get("mintempC","?")
        return f"{name}: {temp_c}°C (feels {feels}°C), {desc}, wind {wind}, humidity {hum}. Today {mint}-{maxt}°C."
    except Exception as e:
        # wttr.in can 500 for "City tomorrow" style queries — caller (patched strip) should prevent this,
        # but keep a human fallback so the user hears something useful, not "The search failed."
        err = str(e)[:120]
        if "500" in err or "HTTP Error" in err:
            return f"{city}: forecast temporarily unavailable — try again in a moment. Current service wttr.in reports an error for this request."
        return ""

def main() -> int:
    q = sys.argv[1] if len(sys.argv) > 1 else ""
    if not q:
        print("usage: weather.py <query>", file=sys.stderr)
        return 2
    # Retry: if first try returns fallback 500 message, try stripped city again
    ans = wttr_forecast(q)
    if ans and "temporarily unavailable" in ans and "tomorrow" in q.lower():
        # retry with tomorrow stripped
        import re as _re2
        q2 = _re2.sub(r"\b(tomorrow|tommorrow|tommorow)\b", "", q, flags=_re2.I)
        ans2 = wttr_forecast(q2)
        if ans2 and "temporarily unavailable" not in ans2:
            ans = ans2
    if ans:
        print(ans)
        return 0
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
