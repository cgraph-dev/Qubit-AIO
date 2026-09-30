"""D4 — OpenClaw reports rows closed by real capability + negative proof.

Covers the one-authority ``voicebridge/evaluation`` modules:

* OC-50 analytics/usage  — ``evaluation/usage_analytics.py`` (token/cost
  accounting over an append-only, content-addressed ledger);
* OC-52 immutable execution-identity receipts — ``evaluation/receipts.py``
  (hash-chained receipts bound to an execution identity);
* OC-D32 OpenTelemetry traces/spans + model-call metrics — ``evaluation/
  telemetry.py`` (opt-in gated, caller-supplied transport, OTLP-shaped).

Each row also proves the security negative: no secrets/content, tamper
detection, and fail-closed export.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from voicebridge.evaluation import receipts as R
from voicebridge.evaluation import telemetry as T
from voicebridge.evaluation import usage_analytics as U


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# ------------------------------------------------------------------ OC-50
def test_oc50_usage_ledger_totals_and_breakdown(tmp_path):
    ledger = U.UsageLedger(tmp_path / "usage.jsonl")
    ledger.record(U.UsageRecord(product="qubit", model="brain4",
                                provider="local", input_tokens=10,
                                output_tokens=5, cost_usd=0.02,
                                run_id="r1", ts=1_700_000_000))
    ledger.record(U.UsageRecord(product="qubit", model="brain4",
                                provider="local", input_tokens=1,
                                output_tokens=2, cost_usd=0.01,
                                run_id="r2", ts=1_700_000_000))
    ledger.record(U.UsageRecord(product="qubit", model="cloud",
                                provider="api", input_tokens=4,
                                output_tokens=0, cost_usd=0.5,
                                run_id="r3", ts=1_700_086_400))
    report = ledger.analytics()
    assert report["calls"] == 3
    assert report["input_tokens"] == 15
    assert report["output_tokens"] == 7
    assert report["total_tokens"] == 22
    assert report["by_model"]["brain4"]["calls"] == 2
    assert report["by_provider"]["api"]["cost_usd"] == pytest.approx(0.5)
    assert set(report["by_day"]) == {"2023-11-14", "2023-11-15"}
    # content-addressed, immutable record identity
    recs = ledger.read()
    assert recs[0].record_id != recs[1].record_id
    assert all(len(r.record_id) == 64 for r in recs)


def test_oc50_rejects_negative_and_skips_corruption(tmp_path):
    with pytest.raises(U.UsageError) as exc:
        U.UsageRecord(product="p", model="m", input_tokens=-1)
    assert exc.value.code == "USAGE_BAD_RECORD"
    path = tmp_path / "usage.jsonl"
    path.write_text('{"product":"p","model":"m"}\nNOT JSON\n')
    ledger = U.UsageLedger(path)
    assert len(ledger.read()) == 1
    assert ledger.rejections == 1


def test_oc50_status_carries_no_secret_material():
    rec = U.UsageRecord(product="p", model="m", input_tokens=1,
                        output_tokens=1)
    body = json.dumps(rec.as_dict())
    assert "sk-" not in body
    assert "prompt" not in body and "response" not in body


# ------------------------------------------------------------------ OC-52
def test_oc52_receipt_chain_binds_execution_identity():
    ident = R.ExecutionIdentity.create(run_id="run-1", actor="user",
                                       workspace="/ws", tool="files.patch",
                                       session="s1")
    chain = R.ReceiptChain(ident)
    chain.append(status="started", inputs_digest="a" * 64)
    chain.append(status="succeeded", outputs_digest="b" * 64,
                 approval_id="appr-1")
    out = chain.verify()
    assert out["valid"] is True and out["length"] == 2
    serialized = chain.receipts()
    assert serialized[1]["prev_hash"] == serialized[0]["content_hash"]
    assert serialized[0]["identity_id"] == ident.identity_id
    assert R.verify_receipts(serialized)["valid"] is True


def test_oc52_tamper_reorder_delete_detected():
    ident = R.ExecutionIdentity.create(run_id="run-2", actor="user")
    chain = R.ReceiptChain(ident)
    chain.append(status="started")
    chain.append(status="succeeded")
    serialized = chain.receipts()

    tampered = json.loads(json.dumps(serialized))
    tampered[0]["status"] = "succeeded"
    assert R.verify_receipts(tampered)["valid"] is False

    reordered = [serialized[1], serialized[0]]
    assert R.verify_receipts(reordered)["valid"] is False

    deleted = [serialized[1]]
    assert R.verify_receipts(deleted)["valid"] is False

    leg = chain.receipts()[0]
    bogus = R.ExecutionReceipt(identity_id=leg["identity_id"],
                               run_id=leg["run_id"], seq=leg["seq"],
                               status=leg["status"],
                               prev_hash=leg["prev_hash"], ts=leg["ts"])
    bogus.status = "REWRITTEN"
    assert bogus.verify() is False


def test_oc52_chain_requires_an_identity_and_never_stores_content():
    with pytest.raises(R.ReceiptError) as exc:
        R.ReceiptChain("not-an-identity")
    assert exc.value.code == "RECEIPT_BAD_IDENTITY"
    ident = R.ExecutionIdentity.create(run_id="run-3", actor="user")
    body = json.dumps(ident.as_dict())
    assert "sk-" not in body


# ----------------------------------------------------------------- OC-D32
def test_ocd32_telemetry_defaults_off_and_export_fails_closed():
    tracer = T.Tracer(service="qubit")
    with tracer.start_span("model.call", attributes={"model": "brain4"}) as span:
        span.add_event("token", {"count": 3})
    tracer.counter("model.calls", 1, attributes={"model": "brain4"})
    assert tracer.spans()[0]["name"] == "model.call"
    assert tracer.metric_summary()["model.calls"]["count"] == 1
    assert T.telemetry_enabled(None, environ={}) is False
    with pytest.raises(T.TelemetryError) as exc:
        T.export_payload(tracer, None, environ={})
    assert exc.value.code == T.OTEL_DISABLED


def test_ocd32_opt_in_exports_through_injected_transport_only():
    tracer = T.Tracer(service="qubit")
    tracer.counter("model.calls", 2, attributes={"model": "cloud"})
    env = {"QUBIT_OTEL_ENABLED": "1",
           "QUBIT_OTEL_ENDPOINT": "http://collector.test:4318"}
    assert T.telemetry_enabled(None, environ=env) is True
    dry = T.export(tracer, lambda payload: pytest.fail("must not send"),
                   environ=env)
    assert dry["state"] == "DRY_RUN" and dry["exported"] is False
    sent = {}

    def _transport(payload):
        sent["payload"] = payload
        return {"status": 202}

    live = T.export(tracer, _transport, environ=env, dry_run=False)
    assert live["state"] == "EXPORTED" and live["exported"] is True
    assert sent["payload"]["endpoint"] == "http://collector.test:4318"
    assert sent["payload"]["schema"] == T.SCHEMA
    assert "prompt" not in json.dumps(sent["payload"]).lower()


def test_ocd32_traceparent_roundtrip_and_malformed_rejected():
    header = T.traceparent("a" * 32, "b" * 16, sampled=True)
    parsed = T.parse_traceparent(header)
    assert parsed == {"trace_id": "a" * 32, "span_id": "b" * 16,
                      "sampled": True}
    for bad in ("", "not-a-trace", "ff-" + "a" * 32 + "-" + "b" * 16 + "-01",
                "00-" + "0" * 32 + "-" + "b" * 16 + "-01",
                "00-" + "a" * 32 + "-" + "0" * 16 + "-01"):
        assert T.parse_traceparent(bad) is None
