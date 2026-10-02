"""Weather-by-voice: trigger + city parsing (no network, no model).

Live 2026-10-02: "weather in <other city>" failed while Bucharest worked.
Two compounding gaps: (1) the voice weather trigger only fired on the
words weather/forecast, so "what's the temperature in Madrid" fell to the
small brain, which stonewalls; (2) the city regex was ASCII-only, so
Romanian cities with diacritics ("Iași", "București") parsed to stubs
("Ia") and wttr.in 404'd into the same stonewall.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from weather import _extract_city, _wind_dir16, _WMO_DESC  # noqa: E402


def test_city_ascii():
    assert _extract_city("what is the weather in Paris") == "Paris"
    assert _extract_city("weather Bucharest") == "Bucharest"


def test_city_unicode_kept():
    assert _extract_city("weather in Iași") == "Iași"
    assert _extract_city("how is the weather in București") == "București"
    assert _extract_city("weather in Cluj-Napoca") == "Cluj-Napoca"


def test_city_strips_filler_words():
    assert _extract_city("how is the weather like in Rome") == "Rome"
    assert _extract_city("weather in New York tomorrow") == "New York"
    assert _extract_city("what is the temperature in Madrid") == "Madrid"
    assert _extract_city("what is the weather in Bucharest right now") == \
        "Bucharest"


def test_city_falls_back_to_bucharest():
    assert _extract_city("what is the weather like outside") == "Bucharest"
    assert _extract_city("weather") == "Bucharest"


def test_service_triggers_on_temperature(monkeypatch):
    from voicebridge.voice_edit import service as svc_mod
    seen = {}

    class Cfg:
        def get(self, *p, default=None):
            return default

    svc = svc_mod.VoiceEditService(
        Cfg(), desktop=None, engine=None, router=None,
        audit=lambda *a: None, estop=lambda cfg: False)

    def fake_weather(q):
        seen["q"] = q
        return "Madrid: 15 degrees."

    monkeypatch.setattr(svc, "_live_weather", fake_weather)
    assert svc._direct_fact_answer("what is the temperature in Madrid") == \
        "Madrid: 15 degrees."
    assert seen["q"] == "what is the temperature in Madrid"


def test_wmo_and_compass_helpers():
    assert _WMO_DESC[0] == "Clear"
    assert _WMO_DESC[95] == "Thunderstorm"
    assert _wind_dir16(0) == "N"
    assert _wind_dir16(90) == "E"
    assert _wind_dir16(225) == "SW"
