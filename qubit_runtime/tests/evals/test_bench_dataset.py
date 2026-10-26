from __future__ import annotations

import json

import pytest

from evals.bench.dataset import EvalInputError, load_jsonl, sample_from_mapping


def test_loads_all_supported_fields(tmp_path):
    path = tmp_path / "data.jsonl"
    rows = [
        {"id": "a", "response": "yes", "reference": "yes", "latency_ms": 12,
         "prompt_tokens": 3, "completion_tokens": 4, "cost_usd": 0.01},
        {"record_id": "b", "answer": "no", "reference": "yes", "correct": False,
         "latency_seconds": 0.5, "usage": {"prompt_tokens": 1, "completion_tokens": 2}},
        {"response_excerpt": "maybe", "energy_joules": 3.5, "flops": 1000},
    ]
    path.write_text("\n\n".join(json.dumps(row) for row in rows) + "\n")
    samples = load_jsonl(path)
    assert [s.record_id for s in samples] == ["a", "b", "line-5"]
    assert samples[0].latency_ms == 12
    assert samples[1].latency_ms == 500
    assert samples[1].prompt_tokens == 1
    assert samples[1].correct is False
    assert samples[2].energy_joules == 3.5
    assert samples[2].flops == 1000


def test_blank_lines_are_ignored(tmp_path):
    path = tmp_path / "data.jsonl"
    path.write_text('\n{"response": "x"}\n\n')
    assert len(load_jsonl(path)) == 1


def test_missing_dataset_is_typed(tmp_path):
    with pytest.raises(EvalInputError) as exc:
        load_jsonl(tmp_path / "missing.jsonl")
    assert exc.value.code == "dataset_missing"


def test_invalid_json_names_the_line(tmp_path):
    path = tmp_path / "data.jsonl"
    path.write_text('{"response": "ok"}\nnot json\n')
    with pytest.raises(EvalInputError) as exc:
        load_jsonl(path)
    assert exc.value.code == "dataset_invalid_json"
    assert "line 2" in str(exc.value)


def test_invalid_field_types_are_refused(tmp_path):
    path = tmp_path / "data.jsonl"
    path.write_text('{"correct": "yes"}\n')
    with pytest.raises(EvalInputError) as exc:
        load_jsonl(path)
    assert "boolean" in str(exc.value)

    path.write_text('{"latency_ms": -5}\n')
    with pytest.raises(EvalInputError):
        load_jsonl(path)


def test_mapping_rejects_non_object():
    with pytest.raises(EvalInputError):
        sample_from_mapping(["not", "an", "object"], fallback_id="x")
