from __future__ import annotations

from agent.runtime_port import RuntimeCapabilities, TurnEvent
from recipes.morning_digest import MORNING_DIGEST
from recipes.runner import run_recipe
from recipes.sections import available, unavailable
from traces.store import TraceStore


class _Backend:
    name = "fake"
    capabilities = RuntimeCapabilities(True, True, False, False)

    def __init__(self, events):
        self.events = events

    def run_turn(self, request, cancel=None):
        yield from self.events


class _Registry:
    def __init__(self, backend):
        self.backend = backend

    def get(self, name):
        return self.backend


def _sections():
    return (
        available("weather", "Weather", ["Sunny 20C"]),
        available("calendar", "Calendar", ["09:00 Standup"]),
        available("unread_mail", "Unread mail", ["No unread mail."]),
        unavailable("health", "Health", "missing_connector", "no connector"),
    )


def test_recipe_writes_one_redacted_trace_when_enabled(tmp_path, monkeypatch):
    db = tmp_path / "traces.db"
    monkeypatch.setenv("QUBIT_TRACES_ENABLED", "1")
    monkeypatch.setenv("QUBIT_TRACES_DB", str(db))
    backend = _Backend([
        TurnEvent("tool", text="weather", data={"name": "weather_lookup"}),
        TurnEvent("usage", data={"prompt_tokens": 10, "completion_tokens": 7,
                                "cost_usd": 0.0002}),
        TurnEvent("done", text="Good morning. Sunny, standup at 9."),
    ])
    run = run_recipe(MORNING_DIGEST, registry=_Registry(backend), engine="fake",
                     fixture=_sections())
    assert run.exit_code == 0

    with TraceStore(db) as store:
        rows = store.list()
    assert len(rows) == 1
    row = rows[0]
    assert row.engine == "fake"
    assert row.session_id.startswith("recipe-morning_digest-")
    assert row.tool_calls == ("weather_lookup",)
    assert row.success is True
    assert row.latency_ms is not None and row.latency_ms >= 0
    assert (row.prompt_tokens, row.completion_tokens) == (10, 7)
    assert row.cost_usd == 0.0002
    assert row.response_excerpt == "Good morning. Sunny, standup at 9."
    assert "Sunny 20C" in row.prompt_excerpt


def test_recipe_writes_nothing_by_default(tmp_path, monkeypatch):
    db = tmp_path / "traces.db"
    monkeypatch.delenv("QUBIT_TRACES_ENABLED", raising=False)
    monkeypatch.setenv("QUBIT_TRACES_DB", str(db))
    backend = _Backend([TurnEvent("done", text="Good morning.")])
    assert run_recipe(MORNING_DIGEST, registry=_Registry(backend), engine="fake",
                      fixture=_sections()).exit_code == 0
    assert not db.exists()


def test_failed_recipe_records_the_error_code(tmp_path, monkeypatch):
    db = tmp_path / "traces.db"
    monkeypatch.setenv("QUBIT_TRACES_ENABLED", "1")
    monkeypatch.setenv("QUBIT_TRACES_DB", str(db))
    backend = _Backend([TurnEvent("error", text="boom", data={"code": "provider_outage"})])
    run = run_recipe(MORNING_DIGEST, registry=_Registry(backend), engine="fake",
                     fixture=_sections())
    assert run.exit_code == 1
    with TraceStore(db) as store:
        row = store.list()[0]
    assert row.success is False
    assert row.error_code == "provider_outage"
