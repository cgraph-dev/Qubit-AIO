"""The ``morning_digest`` recipe.

Four deterministic collectors gather facts from existing Qubit surfaces:

* weather — the bundled no-key ``scripts/weather.py`` reader (Open-Meteo),
* calendar — the ``google-workspace`` skill's ``google_api.py`` and its
  existing ``google_token.json`` credential path,
* unread mail — the same Google Workspace credential path, read-only search,
* health — no Qubit health connector exists yet, so this section is an honest
  typed ``connector_not_configured`` gap rather than fabricated data.

The model only writes the final brief from the collected facts. A missing
connector, credential, or failing tool degrades that one section; the others
still run.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

from agent.redact import redact_for_egress
from recipes.sections import (
    REASON_CONNECTOR_NOT_CONFIGURED,
    REASON_MISSING_CONNECTOR,
    REASON_MISSING_CREDENTIAL,
    REASON_TOOL_ERROR,
    SectionResult,
    SectionUnavailable,
    available,
    unavailable,
)
from recipes.sections import collect_sections as _collect_sections
from recipes.sections import render_brief, render_facts
from recipes.spec import RecipeSpec

SECTION_TITLES = {
    "weather": "Weather",
    "calendar": "Calendar",
    "unread_mail": "Unread mail",
    "health": "Health",
}
SECTION_ORDER = tuple(SECTION_TITLES)

_SCRIPT_TIMEOUT_S = 20.0


def _redact(value: object) -> str:
    try:
        return redact_for_egress(str(value or ""))
    except Exception:
        return ""


def _qubit_home() -> Path:
    try:
        from qubit_constants import get_qubit_home

        return Path(get_qubit_home())
    except Exception:
        return Path(os.path.expanduser("~/.qubit"))


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _weather_script() -> Path | None:
    candidate = _repo_root() / "scripts" / "weather.py"
    return candidate if candidate.is_file() else None


def _google_api_script() -> Path | None:
    candidates = (
        _qubit_home() / "skills" / "productivity" / "google-workspace" / "scripts" / "google_api.py",
        _repo_root() / "qubit_runtime" / "skills" / "productivity" / "google-workspace" / "scripts" / "google_api.py",
    )
    return next((path for path in candidates if path.is_file()), None)


def _google_token() -> Path:
    return _qubit_home() / "google_token.json"


def _run_script(script: Path, args: Sequence[str], *, timeout: float = _SCRIPT_TIMEOUT_S):
    try:
        return subprocess.run(
            [sys.executable, str(script), *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise SectionUnavailable(REASON_TOOL_ERROR, f"{script.name} timed out") from exc
    except OSError as exc:
        raise SectionUnavailable(REASON_TOOL_ERROR, f"{script.name} could not run: {exc}") from exc


def _script_error(script_name: str, proc: subprocess.CompletedProcess) -> SectionUnavailable:
    detail = _redact((proc.stderr or proc.stdout or "").strip())[:300]
    return SectionUnavailable(
        REASON_TOOL_ERROR, f"{script_name} exited {proc.returncode}: {detail or 'no detail'}")


def collect_weather(now: datetime | None = None) -> SectionResult:
    script = _weather_script()
    if script is None:
        raise SectionUnavailable(REASON_MISSING_CONNECTOR, "scripts/weather.py is not installed")
    proc = _run_script(script, ["weather"])
    if proc.returncode != 0:
        raise _script_error("weather.py", proc)
    line = _redact((proc.stdout or "").strip())
    if not line:
        raise SectionUnavailable(REASON_TOOL_ERROR, "weather.py returned no reading")
    return available("weather", SECTION_TITLES["weather"], [line], source="open-meteo")


def _day_window(now: datetime) -> tuple[str, str]:
    local = now.astimezone()
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _google_ready() -> tuple[Path, Path]:
    script = _google_api_script()
    if script is None:
        raise SectionUnavailable(
            REASON_MISSING_CONNECTOR,
            "the google-workspace skill is not installed")
    token = _google_token()
    if not token.is_file():
        raise SectionUnavailable(
            REASON_MISSING_CREDENTIAL,
            "google_token.json is not configured; run the google-workspace setup")
    return script, token


def _parse_json_output(script_name: str, proc: subprocess.CompletedProcess):
    if proc.returncode != 0:
        raise _script_error(script_name, proc)
    try:
        return json.loads((proc.stdout or "").strip() or "[]")
    except (TypeError, ValueError) as exc:
        raise SectionUnavailable(REASON_TOOL_ERROR, f"{script_name} returned invalid JSON") from exc


def _event_time(value: object) -> str:
    text = str(value or "").strip()
    if "T" in text and len(text) >= 16:
        return text[11:16]
    return "all day" if text else "time unknown"


def collect_calendar(now: datetime | None = None) -> SectionResult:
    script, _token = _google_ready()
    day = now or datetime.now(timezone.utc)
    start, end = _day_window(day)
    proc = _run_script(script, ["calendar", "list", "--start", start, "--end", end, "--max", "10"])
    data = _parse_json_output("google_api.py calendar", proc)
    if not isinstance(data, list):
        raise SectionUnavailable(REASON_TOOL_ERROR, "calendar output was not a list")
    facts = []
    for event in data[:10]:
        if not isinstance(event, dict):
            continue
        summary = _redact(event.get("summary") or "(no title)")[:160]
        when = _event_time(event.get("start"))
        facts.append(f"{when} — {summary}")
    return available(
        "calendar", SECTION_TITLES["calendar"],
        facts or ["No events scheduled today."], source="google-workspace")


def collect_unread_mail(now: datetime | None = None) -> SectionResult:
    script, _token = _google_ready()
    proc = _run_script(script, ["gmail", "search", "is:unread", "--max", "5"])
    data = _parse_json_output("google_api.py gmail", proc)
    if not isinstance(data, list):
        raise SectionUnavailable(REASON_TOOL_ERROR, "mail output was not a list")
    facts = []
    for item in data[:5]:
        if not isinstance(item, dict):
            continue
        subject = _redact(item.get("subject") or "(no subject)")[:160]
        sender = _redact(item.get("from") or "unknown sender")[:120]
        facts.append(f"{subject} — from {sender}")
    return available(
        "unread_mail", SECTION_TITLES["unread_mail"],
        facts or ["No unread mail."], source="google-workspace")


def collect_health(now: datetime | None = None) -> SectionResult:
    raise SectionUnavailable(
        REASON_CONNECTOR_NOT_CONFIGURED,
        "no health connector is installed, so no health data was read")


DEFAULT_COLLECTORS: dict[str, Callable[[datetime], SectionResult]] = {
    "weather": collect_weather,
    "calendar": collect_calendar,
    "unread_mail": collect_unread_mail,
    "health": collect_health,
}


def collect_sections(
    collectors: Mapping[str, Callable[[datetime], SectionResult]] | None = None,
    *,
    now: datetime | None = None,
    sections: Sequence[str] = SECTION_ORDER,
) -> tuple[SectionResult, ...]:
    return _collect_sections(
        collectors, now=now, sections=sections,
        titles=SECTION_TITLES, defaults=DEFAULT_COLLECTORS)


MORNING_DIGEST_PROMPT = """\
Write the user's morning digest for {today}.

The facts below were collected deterministically from the user's connected
services. They are the ONLY factual evidence for the brief.

{facts}

Rules:
- Use only the facts above. Never invent events, mail, weather, health values,
  sources, or personal context.
- Cover every configured section in this order: Weather, Calendar, Unread
  mail, Health.
- When a section is marked unavailable, say that it is unavailable and name
  the typed reason. Never omit a gap silently and never guess what it would
  have said.
- Do not offer, claim, or suggest actions.
- No markdown headers, bullets, or emojis. Keep the brief under 180 words.
"""

MORNING_DIGEST = RecipeSpec(
    id="morning_digest",
    title="Morning digest",
    description=(
        "A short, factual start-of-day brief built from weather, today's "
        "calendar, unread mail, and health. Sections degrade independently."),
    prompt=MORNING_DIGEST_PROMPT,
    sections=SECTION_ORDER,
    required_capabilities=("weather", "calendar", "unread_mail", "health"),
    required_tools=(
        "scripts/weather.py",
        "google-workspace:gmail",
        "google-workspace:calendar",
    ),
    default_schedule="0 8 * * *",
    approval_policy="read_only_collectors",
    output_format="short_spoken_brief",
    voice_aliases=(
        "give me my morning digest",
        "morning digest",
        "my morning digest",
        "daily digest",
    ),
    collectors=DEFAULT_COLLECTORS,
)
