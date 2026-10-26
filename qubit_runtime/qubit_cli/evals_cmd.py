"""``qubit evals`` handler: offline only, exit 0 ok / 2 bad input / 1 failed run."""
from __future__ import annotations

import json
import sys

from evals.bench.dataset import EvalInputError
from evals.bench.report import render_summary
from evals.bench.runner import run_dataset, run_traces
from traces.errors import TraceError
from traces.settings import load_settings
from traces.store import TraceStore


def _from_traces(args) -> dict:
    settings = load_settings()
    override = getattr(args, "db", None)
    if override:
        from dataclasses import replace

        settings = replace(settings, db_path=str(override))
    limit = getattr(args, "limit", None)
    with TraceStore(settings.path, create=False) as store:
        return run_traces(store, limit=1000 if limit is None else int(limit))


def cmd_evals(args) -> int:
    if getattr(args, "evals_action", "") != "run":
        print(f"evals: unknown action {getattr(args, 'evals_action', '')!r}", file=sys.stderr)
        return 2
    from_traces = bool(getattr(args, "from_traces", False))
    dataset = getattr(args, "dataset", None)
    if not from_traces and not dataset:
        print("evals run: --dataset or --from-traces is required", file=sys.stderr)
        return 2
    try:
        if from_traces:
            report = _from_traces(args)
        else:
            report = run_dataset(dataset, limit=getattr(args, "limit", None))
    except EvalInputError as exc:
        print(f"evals: [{exc.code}] {exc}", file=sys.stderr)
        return 2
    except TraceError as exc:
        print(f"evals: [{exc.code}] {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"evals: run failed: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(render_summary(report))
    return 0
