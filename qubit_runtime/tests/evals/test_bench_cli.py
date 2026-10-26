from __future__ import annotations

import argparse
import io
import json
import socket
from contextlib import redirect_stderr, redirect_stdout

from evals.bench.runner import run_dataset
from qubit_cli.evals_cmd import cmd_evals
from traces.recorder import record_trace
from traces.settings import TraceSettings


def _args(**kw):
    base = dict(evals_action="run", dataset=None, from_traces=False,
                limit=None, json=False, db=None)
    base.update(kw)
    return argparse.Namespace(**base)


def _dataset(tmp_path):
    path = tmp_path / "data.jsonl"
    path.write_text(
        '{"id":"a","response":"yes","reference":"yes","latency_ms":10,'
        '"prompt_tokens":3,"completion_tokens":4,"cost_usd":0.01}\n'
        '{"id":"b","response":"no","reference":"yes","latency_ms":30,'
        '"prompt_tokens":5,"completion_tokens":6,"cost_usd":0.02}\n'
    )
    return path


def test_run_prints_json_report(tmp_path):
    out = io.StringIO()
    with redirect_stdout(out):
        assert cmd_evals(_args(dataset=str(_dataset(tmp_path)), json=True)) == 0
    report = json.loads(out.getvalue())
    assert report["sample_count"] == 2
    assert report["accuracy"]["accuracy"] == 0.5
    assert report["latency_ms"]["p50"] == 20
    assert report["latency_ms"]["p95"] == 29
    assert report["cost_usd"]["total"] == 0.03
    assert report["energy_joules"]["status"] == "unavailable"


def test_run_prints_readable_summary(tmp_path):
    out = io.StringIO()
    with redirect_stdout(out):
        assert cmd_evals(_args(dataset=str(_dataset(tmp_path)))) == 0
    text = out.getvalue()
    assert "Samples: 2" in text
    assert "Accuracy: 0.5" in text
    assert "Energy: source did not report energy" in text
    assert "Optimization run: unavailable" in text


def test_bad_dataset_exits_two_with_a_code(tmp_path, capsys):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\n")
    assert cmd_evals(_args(dataset=str(bad), json=True)) == 2
    assert "dataset_invalid_json" in capsys.readouterr().err


def test_from_traces_reports_counts_and_no_invented_accuracy(tmp_path):
    db = tmp_path / "traces.db"
    settings = TraceSettings(enabled=True, db_path=str(db), max_excerpt_chars=80)
    record_trace(prompt="p1", response="r1", engine="native", latency_ms=10,
                 prompt_tokens=1, completion_tokens=2, settings=settings)
    record_trace(prompt="p2", response="r2", engine="native", latency_ms=30,
                 success=False, error_code="boom", settings=settings)
    out = io.StringIO()
    with redirect_stdout(out):
        assert cmd_evals(_args(from_traces=True, json=True, db=str(db))) == 0
    report = json.loads(out.getvalue())
    assert report["sample_count"] == 2
    assert report["errored_samples"] == 1
    assert report["accuracy"]["status"] == "unavailable"
    assert report["latency_ms"]["p50"] == 20


def test_offline_runner_never_opens_a_socket(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network is not allowed")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    report = run_dataset(_dataset(tmp_path))
    assert report["sample_count"] == 2


def test_parser_registers_run(tmp_path):
    import argparse as ap
    from qubit_cli.subcommands.evals import build_evals_parser

    parser = ap.ArgumentParser()
    subs = parser.add_subparsers(dest="command")
    build_evals_parser(subs, cmd_evals=lambda args: 0)
    args = parser.parse_args(["evals", "run", "--dataset", "x.jsonl", "--json"])
    assert args.evals_action == "run"
    assert args.dataset == "x.jsonl"
    assert args.json is True
