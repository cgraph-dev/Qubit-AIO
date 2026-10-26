from __future__ import annotations

import json

import pytest

from traces.errors import TraceInputError, TraceStoreError
from traces.recorder import record_trace, try_record_trace
from traces.settings import TraceSettings, resolve_settings
from traces.store import TraceStore


def _settings(path, **kw) -> TraceSettings:
    base = dict(enabled=True, db_path=str(path), max_excerpt_chars=120)
    base.update(kw)
    return TraceSettings(**base)


def test_tracing_is_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("QUBIT_TRACES_ENABLED", raising=False)
    monkeypatch.delenv("QUBIT_TRACES_DB", raising=False)
    db = tmp_path / "should-not-exist.db"
    settings = resolve_settings({}, enabled=None)
    assert settings.enabled is False
    assert record_trace(prompt="hello", settings=TraceSettings(enabled=False, db_path=str(db))) is None
    assert not db.exists()


def test_config_and_env_can_enable_tracing(tmp_path, monkeypatch):
    db = str(tmp_path / "traces.db")
    config = {"traces": {"enabled": True, "db_path": db, "max_excerpt_chars": 40}}
    settings = resolve_settings(config)
    assert settings.enabled is True
    assert settings.db_path == db
    assert settings.max_excerpt_chars == 40

    monkeypatch.setenv("QUBIT_TRACES_ENABLED", "0")
    assert resolve_settings(config).enabled is False
    monkeypatch.setenv("QUBIT_TRACES_ENABLED", "1")
    assert resolve_settings({"traces": {"enabled": False}}).enabled is True


def test_secret_text_never_reaches_db_or_export(tmp_path):
    db = tmp_path / "traces.db"
    out = tmp_path / "export.jsonl"
    secret = "sk-live-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    bearer = "Bearer abcdefghijklmnopqrstuvwxyz012345"
    email = "owner@example.com"
    with TraceStore(db) as store:
        trace_id = record_trace(
            prompt=f"use {secret} and {bearer}",
            response=f"mail {email} with {secret}",
            settings=_settings(db, max_excerpt_chars=200),
            store=store)
        assert trace_id
        row = store.get(trace_id)
        assert secret not in row.prompt_excerpt
        assert secret not in row.response_excerpt
        assert bearer not in row.prompt_excerpt
        assert email not in row.response_excerpt
        export_text = (store.export_jsonl(out) and out.read_text())
    raw_db = db.read_bytes()
    for needle in (secret, bearer, email):
        assert needle.encode() not in raw_db
        assert needle not in export_text
    payload = json.loads(export_text.strip())
    assert payload["id"] == trace_id


def test_prompt_and_response_are_bounded_after_redaction(tmp_path):
    db = tmp_path / "traces.db"
    with TraceStore(db) as store:
        trace_id = record_trace(
            prompt="x" * 5000,
            response="y" * 5000,
            tool_calls=["t" * 500],
            settings=_settings(db, max_excerpt_chars=64),
            store=store)
        row = store.get(trace_id)
    assert len(row.prompt_excerpt) == 64
    assert len(row.response_excerpt) == 64
    assert row.prompt_excerpt.endswith("…")
    assert len(row.tool_calls[0]) <= 120
    assert row.tool_call_count == 1


def test_unknown_metrics_stay_none_and_tool_calls_are_counted(tmp_path):
    db = tmp_path / "traces.db"
    with TraceStore(db) as store:
        trace_id = record_trace(
            prompt="p", response="r", engine="native", model="m",
            tool_calls=["read_file", "terminal"],
            latency_ms=12.5, prompt_tokens=3, completion_tokens=4,
            settings=_settings(db), store=store)
        row = store.get(trace_id)
    assert row.tool_calls == ("read_file", "terminal")
    assert row.tool_call_count == 2
    assert row.cost_usd is None
    assert row.energy_joules is None
    assert row.flops is None
    assert (row.latency_ms, row.prompt_tokens, row.completion_tokens) == (12.5, 3, 4)


def test_invalid_numbers_are_typed_input_errors(tmp_path):
    settings = _settings(tmp_path / "traces.db")
    with pytest.raises(TraceInputError):
        record_trace(prompt="p", latency_ms=-1, settings=settings)
    with pytest.raises(TraceInputError):
        record_trace(prompt="p", prompt_tokens=True, settings=settings)
    with pytest.raises(TraceInputError):
        record_trace(prompt="p", cost_usd=float("inf"), settings=settings)


def test_safe_recorder_absorbs_store_failures(monkeypatch, tmp_path):
    import traces.recorder as recorder

    def boom(*args, **kwargs):
        raise TraceStoreError("store unavailable")

    monkeypatch.setattr(recorder, "TraceStore", boom)
    assert try_record_trace(prompt="p", settings=_settings(tmp_path / "x.db")) is None
