"""``qubit evals`` subcommand parser."""
from __future__ import annotations

from typing import Callable


def build_evals_parser(subparsers, *, cmd_evals: Callable) -> None:
    parser = subparsers.add_parser(
        "evals", help="Run the offline eval bench",
        description=("Summarize accuracy/latency/tokens/cost over a JSONL dataset or "
                     "the local trace store. No model calls and no network."))
    evals_sub = parser.add_subparsers(dest="evals_action", required=True)

    run = evals_sub.add_parser("run", help="Run the offline bench")
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("--dataset", default=None, help="JSONL dataset path")
    source.add_argument("--from-traces", action="store_true",
                        help="Read the local trace store instead of a dataset")
    run.add_argument("--limit", type=int, default=None,
                     help="Evaluate at most this many samples (newest traces first)")
    run.add_argument("--json", action="store_true", help="Print the report as JSON")
    run.add_argument("--db", default=None, help="Override the trace store path")

    parser.set_defaults(func=cmd_evals)
