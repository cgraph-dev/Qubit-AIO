import json
import subprocess
from datetime import datetime, timezone

from recipes import morning_digest
from recipes.morning_digest import (
    MORNING_DIGEST,
    SectionUnavailable,
    collect_calendar,
    collect_health,
    collect_sections,
    collect_unread_mail,
    collect_weather,
    render_brief,
    render_facts,
)
from recipes.sections import available, unavailable


def _ok(section_id, title, facts):
    return lambda now: available(section_id, title, facts)


def test_all_sections_degrade_independently():
    def calendar(now):
        raise SectionUnavailable("missing_credential", "token missing")

    collectors = {
        "weather": _ok("weather", "Weather", ["Sunny 20C"]),
        "calendar": calendar,
        "unread_mail": _ok("unread_mail", "Unread mail", ["2 unread"]),
        "health": _ok("health", "Health", ["7h sleep"]),
    }
    results = collect_sections(collectors, now=datetime(2026, 10, 27, tzinfo=timezone.utc))

    by_id = {result.id: result for result in results}
    assert by_id["weather"].available
    assert by_id["unread_mail"].facts == ("2 unread",)
    assert by_id["health"].facts == ("7h sleep",)
    assert not by_id["calendar"].available
    assert by_id["calendar"].reason == "missing_credential"
    assert by_id["calendar"].detail == "token missing"


def test_unexpected_collector_error_is_typed_and_redacted():
    def boom(now):
        raise RuntimeError("tool exploded with sk-abcdefghijklmnop")

    results = collect_sections(
        {"weather": boom}, now=datetime(2026, 10, 27, tzinfo=timezone.utc),
        sections=("weather",))
    assert results[0].status == "unavailable"
    assert results[0].reason == "tool_error"
    assert "sk-abcdefghijklmnop" not in results[0].detail
    assert "sk-abc" in results[0].detail  # masked, never the raw credential


def test_sections_render_facts_and_unavailable_reasons():
    sections = (
        available("weather", "Weather", ["Sunny 20C"]),
        unavailable("health", "Health", "connector_not_configured",
                    "no health connector is installed"),
    )
    facts = render_facts(sections)
    assert "## Weather" in facts
    assert "- Sunny 20C" in facts
    assert "- unavailable (connector_not_configured): no health connector is installed" in facts
    brief = render_brief(MORNING_DIGEST, sections)
    assert brief.startswith("# Morning digest")
    assert "Health" in brief


def test_health_is_an_honest_gap_not_fabricated_data():
    results = collect_sections({"health": collect_health}, sections=("health",))
    result = results[0]
    assert not result.available
    assert result.reason == "connector_not_configured"


def test_weather_missing_script_is_a_typed_gap(monkeypatch):
    monkeypatch.setattr(morning_digest, "_weather_script", lambda: None)
    result = collect_sections({"weather": collect_weather}, sections=("weather",))[0]
    assert result.reason == "missing_connector"


def test_weather_reading_is_bounded(monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(morning_digest, "_weather_script", lambda: Path("/tmp/weather.py"))
    monkeypatch.setattr(
        morning_digest, "_run_script",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "Bucharest: 9C, clear.\n", ""))
    result = collect_weather()
    assert result.available
    assert result.facts == ("Bucharest: 9C, clear.",)


def test_missing_token_is_a_credential_gap(monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(morning_digest, "_google_api_script", lambda: Path("/tmp/google_api.py"))
    monkeypatch.setattr(morning_digest, "_google_token", lambda: Path("/tmp/does-not-exist-token.json"))
    results = collect_sections({"calendar": collect_calendar}, sections=("calendar",))
    result = results[0]
    assert result.reason == "missing_credential"


def _google_ready(monkeypatch, payload):
    from pathlib import Path

    monkeypatch.setattr(morning_digest, "_google_api_script", lambda: Path("/tmp/google_api.py"))
    monkeypatch.setattr(morning_digest, "_google_token", lambda: Path("/tmp/token.json"))
    monkeypatch.setattr(morning_digest.Path, "is_file", lambda self: True)
    out = json.dumps(payload)
    monkeypatch.setattr(
        morning_digest, "_run_script",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, out, ""))


def test_calendar_formats_events_and_redacts_secrets(monkeypatch):
    _google_ready(monkeypatch, [
        {"summary": "Standup", "start": "2026-10-27T09:00:00+02:00"},
        {"summary": "token sk-abcdefghijklmnop", "start": "2026-10-27"},
    ])
    result = collect_calendar(datetime(2026, 10, 27, tzinfo=timezone.utc))
    assert result.available
    assert result.facts[0] == "09:00 — Standup"
    assert "sk-abcdefghijklmnop" not in result.facts[1]
    assert "all day" in result.facts[1]


def test_empty_calendar_is_available_with_an_honest_line(monkeypatch):
    _google_ready(monkeypatch, [])
    result = collect_calendar(datetime(2026, 10, 27, tzinfo=timezone.utc))
    assert result.available
    assert result.facts == ("No events scheduled today.",)


def test_unread_mail_formats_and_bounds(monkeypatch):
    _google_ready(monkeypatch, [
        {"subject": "Invoice", "from": "billing@example.com"},
        {"subject": "Secret", "from": "sk-abcdefghijklmnop@example.com"},
    ])
    result = collect_unread_mail()
    assert result.available
    # PII policy: the brief is spoken/journalled, so raw addresses are not kept.
    assert result.facts[0] == "Invoice — from [email]"
    assert "billing@example.com" not in result.facts[0]
    assert "sk-abcdefghijklmnop" not in result.facts[1]


def test_mail_and_calendar_contacts_are_redacted_from_the_spoken_brief(monkeypatch):
    _google_ready(monkeypatch, [
        {"subject": "Call +1 (555) 123-4567 about the invoice",
         "from": "Alice <alice@example.com>"},
    ])
    mail = collect_unread_mail()
    assert mail.available
    assert "[email]" in mail.facts[0]
    assert "[phone]" in mail.facts[0]
    assert "alice@example.com" not in mail.facts[0]
    assert "555" not in mail.facts[0]

    _google_ready(monkeypatch, [
        {"summary": "Sync with bob@example.com at +40712345678",
         "start": "2026-10-27T09:00:00+02:00"},
    ])
    calendar = collect_calendar(datetime(2026, 10, 27, tzinfo=timezone.utc))
    assert calendar.available
    assert "[email]" in calendar.facts[0]
    assert "bob@example.com" not in calendar.facts[0]
    # The monitoring scrub may fully mark a phone or mask its middle; either
    # way the raw number never reaches the spoken brief.
    assert "40712345678" not in calendar.facts[0]
    assert "[phone]" in calendar.facts[0] or "****" in calendar.facts[0]


def test_empty_inbox_is_available(monkeypatch):
    _google_ready(monkeypatch, [])
    result = collect_unread_mail()
    assert result.available
    assert result.facts == ("No unread mail.",)


def test_script_failure_is_a_tool_error(monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(morning_digest, "_weather_script", lambda: Path("/tmp/weather.py"))
    monkeypatch.setattr(
        morning_digest, "_run_script",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 2, "", "network down"))
    result = collect_sections({"weather": collect_weather}, sections=("weather",))[0]
    assert result.reason == "tool_error"
    assert "network down" in result.detail
