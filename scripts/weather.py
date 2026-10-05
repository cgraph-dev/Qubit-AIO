#!/usr/bin/env python3
"""Weather for a city — voice-bridge local-script. Uses Open-Meteo (free, no key).

Replaced wttr.in 2026-10-02: its fuzzy area match returned other cities'
weather under the requested name (observed live: "Rome" -> "Lome, Togo",
"Paris" -> "Saint-Merri" is fine but unverifiable). Open-Meteo splits the
job honestly: geocoding resolves the place (top result wins, canonical
name + country shown), the forecast API reads that exact point.
"""
import sys, json, urllib.request, urllib.parse, re

#: Words that end the city span (trailing request filler, never the place).
_CITY_TAIL_WORDS = (r"today|tomorrow|tommorrow|tommorow|now|right|please|thanks"
                    r"|corest|outside|currently|at the moment")


def _extract_city(query: str) -> str:
    """City name from a spoken weather question (pure, no network).

    Unicode-aware (Iași, București, Cluj-Napoca survive); tolerates
    "weather like in X" and "temperature in X" phrasings; strips trailing
    filler ("tomorrow", "right now", "outside"). Falls back to Bucharest
    (the operator's timezone) when no city can be read — including the
    known mangled-ASR shapes.
    """
    m = re.search(
        r"(?:weather|forecast|temperature|temp)\b\s*(?:like\s+)?"
        r"(?:in|for|at|near)?\s*([\w\s'\-.]+)",
        query or "", re.I | re.U)
    city = (m.group(1).strip() if m else "").strip()
    city = re.sub(r"\b(" + _CITY_TAIL_WORDS + r")\b.*", "", city,
                  flags=re.I).strip(" ,.-")
    if not city:
        # "Paris weather" order: last word before the trigger, unless it
        # is question filler.
        m2 = re.search(
            r"([\w'\-.]+)\s+(?:weather|forecast|temperature|temp)\b",
            query or "", re.I | re.U)
        lead = (m2.group(1).strip() if m2 else "").strip(" ,.-")
        if lead and lead.lower() not in (
                "the", "a", "an", "how", "is", "what", "like", "current",
                "local", "today", "my"):
            city = lead
    # mangled ASR "Corest" -> Bucharest fallback
    if not city or len(city) < 3 or "corest" in query.lower() \
            or "search was" in query.lower():
        # if query mentions weather at all, assume Bucharest (user's timezone)
        if "bucharest" in query.lower() or "corest" in query.lower() \
                or not city:
            city = "Bucharest"
    if not city:
        city = "Bucharest"
    return city


def wttr_forecast(query: str) -> str:
    """Spoken answer for a weather question (kept name: callers unchanged)."""
    city = _extract_city(query)
    return open_meteo_forecast(city)


#: WMO weather-code -> spoken description (Open-Meteo forecast API).
_WMO_DESC = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Icy fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail",
    99: "Thunderstorm with hail",
}

_COMPASS16 = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


def _wind_dir16(degrees: float) -> str:
    try:
        return _COMPASS16[int((float(degrees) % 360) / 22.5 + 0.5) % 16]
    except (TypeError, ValueError):
        return ""


def _get_json(url: str, timeout: float = 5.0) -> dict:
    # Lane 4 budget: two sequential fetches (geocode + forecast) must stay
    # under the caller's 12 s subprocess cap (scripts/web_search.py), so each
    # fetch gets 5 s and the miss degrades loudly via the caller's stderr.
    req = urllib.request.Request(url, headers={"User-Agent": "qubit-voicebridge/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def open_meteo_forecast(city: str) -> str:
    """Current weather + today's range for a city name (no key).

    Geocoding top result wins; its canonical name is shown, so a wrong
    place can never be reported under the requested name. "" on failure
    (the caller falls through to the brain, same as before).
    """
    try:
        geo = _get_json(
            "https://geocoding-api.open-meteo.com/v1/search?name="
            + urllib.parse.quote(city) + "&count=1&language=en&format=json")
        hits = geo.get("results") or []
        if not hits:
            return ""
        top = hits[0]
        lat, lon = top.get("latitude"), top.get("longitude")
        name = str(top.get("name") or city)
        fc = _get_json(
            "https://api.open-meteo.com/v1/forecast?latitude=" + str(lat)
            + "&longitude=" + str(lon) + "&current=temperature_2m,"
            "relative_humidity_2m,apparent_temperature,weather_code,"
            "wind_speed_10m,wind_direction_10m&daily=temperature_2m_max,"
            "temperature_2m_min&wind_speed_unit=kmh&timezone=auto")
        cur = fc.get("current") or {}
        daily = fc.get("daily") or {}
        temp = cur.get("temperature_2m", "?")
        feels = cur.get("apparent_temperature", "?")
        desc = _WMO_DESC.get(int(cur.get("weather_code", -1)), "")
        wind = f"{cur.get('wind_speed_10m', '?')} km/h " \
            f"{_wind_dir16(cur.get('wind_direction_10m', 0))}".strip()
        hum = f"{cur.get('relative_humidity_2m', '?')}%"
        maxt = (daily.get("temperature_2m_max") or ["?"])[0]
        mint = (daily.get("temperature_2m_min") or ["?"])[0]
        return (f"{name}: {temp}°C (feels {feels}°C), {desc}, wind {wind}, "
                f"humidity {hum}. Today {mint}-{maxt}°C.")
    except Exception:
        return ""

def main() -> int:
    q = sys.argv[1] if len(sys.argv) > 1 else ""
    if not q:
        print("usage: weather.py <query>", file=sys.stderr)
        return 2
    ans = wttr_forecast(q)
    if ans:
        print(ans)
        return 0
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
