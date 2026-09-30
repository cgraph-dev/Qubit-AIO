"""OC-D32 — opt-in-gated OpenTelemetry-style telemetry tests.

Hermetic: no network client is ever created. Proves default-off gating, local
span/metric recording, W3C trace-context round-trip, and that export refuses
without opt-in and never calls a transport when disabled.
"""
from __future__ import annotations

import pytest

from voicebridge.evaluation import telemetry as T


@pytest.fixture(autouse=True)
def _no_ambient_otel(monkeypatch):
    monkeypatch.delenv("QUBIT_OTEL_ENABLED", raising=False)
    monkeypatch.delenv("QUBIT_OTEL_ENDPOINT", raising=False)


def _ids(n: int) -> str:
    return "a" * (n * 2)


def _enabled(monkeypatch, endpoint="http://collector.local:4318"):
    monkeypatch.setenv("QUBIT_OTEL_ENABLED", "1")
    monkeypatch.setenv("QUBIT_OTEL_ENDPOINT", endpoint)


# ----------------------------------------------------------------- gate
def test_disabled_by_default():
    assert T.telemetry_enabled() is False


def test_enabled_requires_flag_and_endpoint(monkeypatch):
    monkeypatch.setenv("QUBIT_OTEL_ENABLED", "1")
    assert T.telemetry_enabled() is False
    monkeypatch.setenv("QUBIT_OTEL_ENDPOINT", "http://collector:4318")
    assert T.telemetry_enabled() is True


def test_enabled_via_config(tmp_path):
    from voicebridge import config as C

    p = tmp_path / "voice.yaml"
    p.write_text("telemetry:\n  enabled: true\n  endpoint: http://c:4318\n"
                 "logging:\n  jsonl: false\n")
    cfg = C.Config.load(p)
    assert T.telemetry_enabled(cfg, environ={}) is True
    assert T.endpoint_for(cfg, environ={}) == "http://c:4318"


# ----------------------------------------------------------------- spans
def test_span_records_ids_duration_and_events():
    tr = T.Tracer(enabled=True, id_factory=_ids)
    with tr.start_span("work", attributes={"k": "v"}) as span:
        span.add_event("step", {"n": 1})
    spans = tr.spans()
    assert len(spans) == 1
    s = spans[0]
    assert len(s["trace_id"]) == 32 and len(s["span_id"]) == 16
    assert s["name"] == "work"
    assert s["attributes"] == {"k": "v"}
    assert s["events"][0]["name"] == "step"
    assert s["duration_ms"] >= 0


def test_child_span_inherits_trace_and_sets_parent():
    tr = T.Tracer(enabled=True, id_factory=_ids)
    with tr.start_span("parent") as parent:
        with tr.start_span("child", parent=parent) as child:
            pass
    spans = tr.spans()
    assert spans[0]["trace_id"] == spans[1]["trace_id"]
    assert spans[1]["parent_span_id"] == spans[0]["span_id"]


def test_exception_marks_span_error():
    tr = T.Tracer(enabled=True, id_factory=_ids)
    with pytest.raises(ValueError):
        with tr.start_span("boom"):
            raise ValueError("x")
    assert tr.spans()[0]["status"] == "ERROR"


def test_span_buffer_is_bounded():
    tr = T.Tracer(enabled=True, max_spans=2, id_factory=_ids)
    for i in range(4):
        with tr.start_span(f"s{i}"):
            pass
    assert [s["name"] for s in tr.spans()] == ["s2", "s3"]


# --------------------------------------------------------------- metrics
def test_metric_summary_aggregates():
    tr = T.Tracer(enabled=True)
    tr.counter("calls", 1)
    tr.counter("calls", 2)
    tr.histogram("latency", 10.0)
    tr.histogram("latency", 30.0)
    summ = tr.metric_summary()
    assert summ["calls"]["count"] == 2 and summ["calls"]["sum"] == 3.0
    assert summ["latency"]["min"] == 10.0 and summ["latency"]["max"] == 30.0


# ----------------------------------------------------------- traceparent
def test_traceparent_roundtrip():
    header = T.traceparent("a" * 32, "b" * 16)
    parsed = T.parse_traceparent(header)
    assert parsed == {"trace_id": "a" * 32, "span_id": "b" * 16,
                      "sampled": True}


@pytest.mark.parametrize("bad", ["", "nope", "00-" + "a" * 32,
                                 "00-" + "0" * 32 + "-" + "b" * 16 + "-01",
                                 "00-" + "a" * 32 + "-" + "0" * 16 + "-01",
                                 "ff-" + "a" * 32 + "-" + "b" * 16 + "-01"])
def test_malformed_traceparent_is_none(bad):
    assert T.parse_traceparent(bad) is None


# ---------------------------------------------------------------- export
def test_export_refuses_without_opt_in():
    tr = T.Tracer(enabled=False)
    with pytest.raises(T.TelemetryError) as ei:
        T.export_payload(tr)
    assert ei.value.code == T.OTEL_DISABLED
    assert "nothing was exported" in ei.value.detail


def test_export_dry_run_never_uses_transport(monkeypatch):
    _enabled(monkeypatch)
    calls = []
    tr = T.Tracer(enabled=True, id_factory=_ids)
    with tr.start_span("x"):
        pass
    out = T.export(tr, lambda payload: calls.append(payload),
                   dry_run=True)
    assert out["state"] == "DRY_RUN" and out["exported"] is False
    assert calls == []


def test_export_invokes_injected_transport_when_opted_in(monkeypatch):
    _enabled(monkeypatch)
    seen = []

    def transport(payload):
        seen.append(payload)
        return {"ok": True}

    tr = T.Tracer(enabled=True, id_factory=_ids)
    with tr.start_span("x"):
        pass
    out = T.export(tr, transport, dry_run=False)
    assert out["state"] == "EXPORTED" and out["exported"] is True
    assert len(seen) == 1
    assert seen[0]["endpoint"] == "http://collector.local:4318"


def test_disabled_export_never_calls_transport(monkeypatch):
    calls = []
    tr = T.Tracer(enabled=False)
    with pytest.raises(T.TelemetryError):
        T.export(tr, lambda p: calls.append(p), dry_run=False)
    assert calls == []
