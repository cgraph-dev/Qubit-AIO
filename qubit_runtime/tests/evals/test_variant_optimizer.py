from __future__ import annotations

import argparse
import io
import json
import socket
from contextlib import redirect_stderr, redirect_stdout

import pytest

from evals.bench.dataset import EvalSample
from evals.optimize.errors import OptimizeError
from evals.optimize.labels import TraceLabel, apply_labels, load_labels
from evals.optimize.report import render_variant_report
from evals.optimize.runner import run_dataset_variants, run_traces_variants
from evals.optimize.scoring import score_variants, wilson_interval
from evals.optimize.variants import (
    PromptVariant,
    build_variant_set,
    load_variant_file,
    load_variants,
    variant_from_arg,
)
from qubit_cli.optimize_cmd import cmd_optimize
from traces.recorder import record_trace
from traces.settings import TraceSettings

CONCISE = "Answer with one short sentence and no preamble."
DETAILED = "Answer with a detailed explanation and include every relevant step."


def _prompt_file(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text + "\n", encoding="utf-8")
    return path


def _scores(tmp_path, rows):
    path = tmp_path / "samples.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n",
                    encoding="utf-8")
    return path


def _rows(variant, correct_count, total):
    rows = []
    for index in range(total):
        rows.append({
            "id": f"{variant}-{index}",
            "variant": variant,
            "response": "right" if index < correct_count else "wrong",
            "reference": "right",
        })
    return rows


def _variant_set():
    return build_variant_set(
        (
            PromptVariant("concise", CONCISE, "one line"),
            PromptVariant("detailed", DETAILED, "long form"),
        ),
        target="schema:answer",
        source="test",
    )


def _cli_args(**kw):
    base = dict(
        optimize_action="variants", dataset=None, from_traces=False,
        variant=None, variants_file=None, target="", labels=None,
        scorer="exact", min_samples=2, limit=None, json=True, db=None,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def test_variant_file_and_args_are_bounded_and_deterministic(tmp_path):
    spec = tmp_path / "variants.json"
    spec.write_text(json.dumps({
        "target": "recipe:morning_digest",
        "variants": [
            {"name": "concise", "prompt": CONCISE, "description": "short"},
            {"name": "detailed", "prompt": DETAILED},
            {"name": "plain", "prompt": "Answer the question plainly."},
        ],
    }), encoding="utf-8")
    loaded = load_variant_file(spec)
    assert loaded.target == "recipe:morning_digest"
    assert [v.name for v in loaded.variants] == ["concise", "detailed", "plain"]
    assert loaded.as_dict()["variants"][0]["prompt_sha256"]
    assert "prompt" not in loaded.as_dict()["variants"][0]

    concise = variant_from_arg(f"concise={_prompt_file(tmp_path, 'c.txt', CONCISE)}")
    detailed = variant_from_arg(f"detailed={_prompt_file(tmp_path, 'd.txt', DETAILED)}")
    assert concise.prompt == CONCISE
    assert detailed.name == "detailed"
    with pytest.raises(OptimizeError) as exc:
        load_variants([], variants_file=None, target="x")
    assert exc.value.code == "optimize_variant_count"


def test_variant_count_and_duplicate_names_are_refused(tmp_path):
    with pytest.raises(OptimizeError) as exc:
        build_variant_set((PromptVariant("one", CONCISE),))
    assert exc.value.code == "optimize_variant_count"

    with pytest.raises(OptimizeError) as exc:
        build_variant_set((
            PromptVariant("one", CONCISE),
            PromptVariant("ONE", DETAILED),
        ))
    assert exc.value.code == "optimize_variant_duplicate"


def test_winner_has_sample_sizes_and_a_wilson_confidence(tmp_path):
    rows = _rows("concise", 12, 12) + _rows("detailed", 3, 12)
    report = run_dataset_variants(
        _scores(tmp_path, rows), _variant_set(), min_samples=5)
    assert report["sample_count"] == 24
    assert report["assigned_samples"] == 24
    assert report["labeled_samples"] == 24
    assert report["model_calls"] == 0
    assert report["network"] is False
    assert report["winner"]["name"] == "concise"
    assert report["winner"]["accuracy"] == 1.0
    assert report["winner"]["labeled"] == 12
    assert report["runner_up"]["name"] == "detailed"
    assert report["confidence"]["status"] == "clear"
    assert report["confidence"]["separation"] is True
    assert report["confidence"]["winner_interval"][0] > \
        report["confidence"]["runner_up_interval"][1]
    metrics = {m["name"]: m for m in report["variants"]}
    assert metrics["concise"]["correct"] == 12
    assert metrics["detailed"]["accuracy"] == 0.25
    assert metrics["detailed"]["eligible"] is True
    assert report["optimization"]["status"] == "not_run"
    assert "does not generate prompts" in report["optimization"]["reason"]
    assert render_variant_report(report).startswith("Target: schema:answer")


def test_refuses_without_labels(tmp_path):
    rows = [{"id": "a", "variant": "concise", "response": "x"},
            {"id": "b", "variant": "detailed", "response": "y"}]
    with pytest.raises(OptimizeError) as exc:
        run_dataset_variants(_scores(tmp_path, rows), _variant_set(), min_samples=2)
    assert exc.value.code == "optimize_no_labels"


def test_refuses_when_two_variants_lack_min_samples(tmp_path):
    rows = _rows("concise", 2, 2) + _rows("detailed", 1, 2)
    with pytest.raises(OptimizeError) as exc:
        run_dataset_variants(_scores(tmp_path, rows), _variant_set(), min_samples=5)
    assert exc.value.code == "optimize_insufficient_samples"
    assert "concise=2" in str(exc.value)


def test_supplied_scorer_is_used_and_named():
    samples = [
        EvalSample("a", response="OK", variant="concise"),
        EvalSample("b", response="OK", variant="concise"),
        EvalSample("c", response="NO", variant="detailed"),
        EvalSample("d", response="NO", variant="detailed"),
    ]

    def marker_scorer(sample):
        return sample.response == "OK"

    report = score_variants(samples, _variant_set(), scorer=marker_scorer,
                            min_samples=2)
    assert report["scorer"] == "marker_scorer"
    assert report["winner"]["name"] == "concise"
    assert report["winner"]["accuracy"] == 1.0


def test_trace_prompts_are_matched_and_labels_come_from_a_sidecar(tmp_path):
    db = tmp_path / "traces.db"
    settings = TraceSettings(enabled=True, db_path=str(db), max_excerpt_chars=4000)
    ids = {"concise": [], "detailed": []}
    for index in range(4):
        ids["concise"].append(record_trace(
            prompt=f"{CONCISE}\nFacts: {index}", response="right",
            settings=settings))
    for index in range(4):
        ids["detailed"].append(record_trace(
            prompt=f"{DETAILED}\nFacts: {index}", response="wrong",
            settings=settings))
    labels_path = tmp_path / "labels.jsonl"
    label_rows = [
        {"id": trace_id, "reference": "right"} for trace_id in ids["concise"]
    ] + [
        {"id": trace_id, "correct": False} for trace_id in ids["detailed"]
    ]
    labels_path.write_text("\n".join(json.dumps(row) for row in label_rows) + "\n",
                           encoding="utf-8")
    labels = load_labels(labels_path)
    from traces.store import TraceStore

    with TraceStore(db, create=False) as store:
        report = run_traces_variants(
            store, _variant_set(), labels=labels, min_samples=2,
            label_source=str(labels_path))
    assert report["source"] == "traces"
    assert report["labels_matched"] == 8
    assert report["labels_unmatched"] == 0
    metrics = {m["name"]: m for m in report["variants"]}
    assert metrics["concise"]["accuracy"] == 1.0
    assert metrics["detailed"]["accuracy"] == 0.0
    assert report["winner"]["name"] == "concise"


def test_traces_refuse_without_a_label_sidecar(tmp_path):
    db = tmp_path / "traces.db"
    settings = TraceSettings(enabled=True, db_path=str(db), max_excerpt_chars=4000)
    record_trace(prompt=CONCISE, response="right", settings=settings)
    from traces.store import TraceStore

    with TraceStore(db, create=False) as store:
        with pytest.raises(OptimizeError) as exc:
            run_traces_variants(store, _variant_set(), labels=None)
    assert exc.value.code == "optimize_labels_missing"


def test_cli_json_and_typed_refusals(tmp_path):
    rows = _rows("concise", 4, 4) + _rows("detailed", 1, 4)
    dataset = _scores(tmp_path, rows)
    concise = _prompt_file(tmp_path, "c.txt", CONCISE)
    detailed = _prompt_file(tmp_path, "d.txt", DETAILED)

    out = io.StringIO()
    with redirect_stdout(out):
        rc = cmd_optimize(_cli_args(
            dataset=str(dataset),
            variant=[f"concise={concise}", f"detailed={detailed}"],
            min_samples=2, json=True))
    assert rc == 0
    report = json.loads(out.getvalue())
    assert report["winner"]["name"] == "concise"

    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = cmd_optimize(_cli_args(
            dataset=str(dataset),
            variant=[f"concise={concise}"],
            min_samples=2))
    assert rc == 2
    assert "optimize_variant_count" in err.getvalue()

    empty_db = tmp_path / "empty-traces.db"
    from traces.store import TraceStore

    TraceStore(empty_db).close()
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = cmd_optimize(_cli_args(
            dataset=str(dataset), from_traces=True, db=str(empty_db),
            variant=[f"concise={concise}", f"detailed={detailed}"]))
    assert rc == 2
    assert "optimize_labels_missing" in err.getvalue()


def test_cli_from_traces_reads_the_sidecar(tmp_path):
    db = tmp_path / "traces.db"
    settings = TraceSettings(enabled=True, db_path=str(db), max_excerpt_chars=4000)
    ids = []
    for index in range(3):
        ids.append(record_trace(prompt=f"{CONCISE} {index}", response="right",
                                settings=settings))
        ids.append(record_trace(prompt=f"{DETAILED} {index}", response="wrong",
                                settings=settings))
    labels_path = tmp_path / "labels.jsonl"
    labels_path.write_text("\n".join(
        json.dumps({"id": trace_id, "reference": "right"}) for trace_id in ids
    ) + "\n", encoding="utf-8")
    concise = _prompt_file(tmp_path, "c.txt", CONCISE)
    detailed = _prompt_file(tmp_path, "d.txt", DETAILED)
    out = io.StringIO()
    with redirect_stdout(out):
        rc = cmd_optimize(_cli_args(
            from_traces=True, db=str(db), labels=str(labels_path),
            variant=[f"concise={concise}", f"detailed={detailed}"],
            min_samples=2, json=True))
    assert rc == 0
    report = json.loads(out.getvalue())
    assert report["source"] == "traces"
    assert report["labels_matched"] == 6


def test_cli_parser_registers_variants():
    from qubit_cli.subcommands.optimize import build_optimize_parser

    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command")
    build_optimize_parser(subs, cmd_optimize=lambda args: 0)
    args = parser.parse_args([
        "optimize", "variants", "--dataset", "x.jsonl",
        "--variant", "a=a.txt", "--variant", "b=b.txt", "--json",
    ])
    assert args.optimize_action == "variants"
    assert args.variant == ["a=a.txt", "b=b.txt"]
    assert args.scorer == "exact"
    assert args.json is True


def test_scoring_never_opens_a_socket(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network is not allowed")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    rows = _rows("concise", 3, 3) + _rows("detailed", 1, 3)
    report = run_dataset_variants(_scores(tmp_path, rows), _variant_set(),
                                  min_samples=2)
    assert report["model_calls"] == 0
    assert report["winner"]["name"] == "concise"


def test_wilson_interval_is_bounded():
    assert wilson_interval(0, 0) is None
    low, high = wilson_interval(10, 10)
    assert 0.0 < low <= high <= 1.0
    assert wilson_interval(0, 10)[0] == 0.0


def test_apply_labels_overlays_only_present_fields():
    samples = [EvalSample("a", response="x", reference="old")]
    updated, matched, unmatched = apply_labels(
        samples, {"a": TraceLabel(correct=True)})
    assert matched == 1 and unmatched == 0
    assert updated[0].correct is True
    assert updated[0].reference == "old"
