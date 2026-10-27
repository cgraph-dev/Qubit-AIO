"""``qubit optimize`` handler: offline only, exit 0 ok / 2 bad input / 1 failure."""
from __future__ import annotations

import json
import sys

from evals.bench.dataset import EvalInputError
from evals.optimize.errors import OptimizeError
from evals.optimize.labels import load_labels
from evals.optimize.report import render_variant_report
from evals.optimize.runner import run_dataset_variants, run_traces_variants
from evals.optimize.variants import load_variants
from traces.errors import TraceError
from traces.settings import load_settings
from traces.store import TraceStore


def _trace_source(args) -> dict:
    settings = load_settings()
    override = getattr(args, "db", None)
    if override:
        from dataclasses import replace

        settings = replace(settings, db_path=str(override))
    limit = getattr(args, "limit", None)
    labels_path = getattr(args, "labels", None)
    labels = load_labels(labels_path) if labels_path else None
    variant_set = load_variants(
        getattr(args, "variant", None),
        variants_file=getattr(args, "variants_file", None),
        target=getattr(args, "target", "") or "",
    )
    with TraceStore(settings.path, create=False) as store:
        return run_traces_variants(
            store,
            variant_set,
            labels=labels,
            scorer=getattr(args, "scorer", "exact"),
            min_samples=getattr(args, "min_samples", 5),
            limit=limit,
            label_source=str(labels_path or ""),
        )


def cmd_optimize(args) -> int:
    if getattr(args, "optimize_action", "") != "variants":
        print(f"optimize: unknown action {getattr(args, 'optimize_action', '')!r}",
              file=sys.stderr)
        return 2
    from_traces = bool(getattr(args, "from_traces", False))
    dataset = getattr(args, "dataset", None)
    if not from_traces and not dataset:
        print("optimize variants: --dataset or --from-traces is required",
              file=sys.stderr)
        return 2
    try:
        if from_traces:
            report = _trace_source(args)
        else:
            labels_path = getattr(args, "labels", None)
            labels = load_labels(labels_path) if labels_path else None
            variant_set = load_variants(
                getattr(args, "variant", None),
                variants_file=getattr(args, "variants_file", None),
                target=getattr(args, "target", "") or "",
            )
            report = run_dataset_variants(
                dataset,
                variant_set,
                scorer=getattr(args, "scorer", "exact"),
                min_samples=getattr(args, "min_samples", 5),
                limit=getattr(args, "limit", None),
                labels=labels,
                label_source=str(labels_path or ""),
            )
    except OptimizeError as exc:
        print(f"optimize: [{exc.code}] {exc}", file=sys.stderr)
        return 2
    except EvalInputError as exc:
        print(f"optimize: [{exc.code}] {exc}", file=sys.stderr)
        return 2
    except TraceError as exc:
        print(f"optimize: [{exc.code}] {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"optimize: run failed: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(render_variant_report(report))
    return 0
