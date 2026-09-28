"""OC-50 — real token/cost usage analytics tests.

Hermetic append-only ledger; proves content-addressed records, real
aggregation by model/provider/day, `0600` storage, and typed rejection of
invalid or corrupt records.
"""
from __future__ import annotations

import json
import stat

import pytest

from voicebridge.evaluation import usage_analytics as UA


def test_record_is_content_addressed_and_stable():
    a = UA.UsageRecord(product="chat", model="m", input_tokens=3,
                       output_tokens=4, cost_usd=0.5, ts=1000.0)
    b = UA.UsageRecord(product="chat", model="m", input_tokens=3,
                       output_tokens=4, cost_usd=0.5, ts=1000.0)
    assert a.record_id == b.record_id
    c = UA.UsageRecord(product="chat", model="m", input_tokens=3,
                       output_tokens=5, cost_usd=0.5, ts=1000.0)
    assert c.record_id != a.record_id
    assert a.total_tokens == 7


def test_invalid_record_is_typed():
    with pytest.raises(UA.UsageError) as ei:
        UA.UsageRecord(product="x", model="m", input_tokens=-1)
    assert ei.value.code == "USAGE_BAD_RECORD"


def test_ledger_roundtrip_and_mode(tmp_path):
    led = UA.UsageLedger(tmp_path / "usage.jsonl")
    rec = UA.UsageRecord(product="chat", model="m", provider="p",
                         input_tokens=10, output_tokens=5, cost_usd=0.01,
                         run_id="r", ts=1_700_000_000.0)
    led.record(rec)
    read = led.read()
    assert len(read) == 1 and read[0].record_id == rec.record_id
    mode = stat.S_IMODE((tmp_path / "usage.jsonl").stat().st_mode)
    assert mode == 0o600


def test_analytics_aggregates_by_model_provider_day():
    recs = [
        UA.UsageRecord(product="chat", model="m1", provider="p1",
                       input_tokens=10, output_tokens=5, cost_usd=0.01,
                       ts=1_700_000_000.0),
        UA.UsageRecord(product="chat", model="m1", provider="p1",
                       input_tokens=20, output_tokens=10, cost_usd=0.02,
                       ts=1_700_000_100.0),
        UA.UsageRecord(product="chat", model="m2", provider="p2",
                       input_tokens=1, output_tokens=1, cost_usd=0.03,
                       ts=1_700_100_000.0),
    ]
    out = UA.analyze(recs)
    assert out["calls"] == 3
    assert out["total_tokens"] == 47
    assert out["cost_usd"] == pytest.approx(0.06)
    assert out["by_model"]["m1"]["calls"] == 2
    assert out["by_model"]["m1"]["cost_usd"] == pytest.approx(0.03)
    assert out["by_provider"]["p2"]["calls"] == 1
    assert len(out["by_day"]) == 2


def test_corrupt_ledger_lines_are_counted_not_raised(tmp_path):
    path = tmp_path / "usage.jsonl"
    path.write_text("not json\n" + json.dumps(
        UA.UsageRecord(product="chat", model="m", ts=1.0).as_dict()) + "\n")
    led = UA.UsageLedger(path)
    assert len(led.read()) == 1
    assert led.rejections == 1


def test_empty_analytics_is_zeroed():
    out = UA.analyze([])
    assert out["calls"] == 0 and out["total_tokens"] == 0
    assert out["cost_usd"] == 0.0


def test_analytics_never_contains_prompt_or_secret():
    rec = UA.UsageRecord(product="chat", model="m", input_tokens=1,
                         output_tokens=1)
    blob = json.dumps(UA.analyze([rec]))
    assert "prompt" not in blob and "secret" not in blob
