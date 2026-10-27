"""``qubit optimize`` subcommand parser."""
from __future__ import annotations

from typing import Callable


def build_optimize_parser(subparsers, *, cmd_optimize: Callable) -> None:
    parser = subparsers.add_parser(
        "optimize", help="Offline prompt-variant selection",
        description=("Select among 2-4 declared prompt variants using already-recorded, "
                     "labeled samples. No model calls and no network."))
    optimize_sub = parser.add_subparsers(dest="optimize_action", required=True)

    variants = optimize_sub.add_parser(
        "variants", help="Score declared prompt variants on labeled samples",
        description=("Compare 2-4 prompt variants over a JSONL dataset or the local "
                     "trace store. Refuses without labels or enough samples."))
    source = variants.add_mutually_exclusive_group(required=True)
    source.add_argument("--dataset", default=None, help="JSONL dataset path")
    source.add_argument("--from-traces", action="store_true",
                        help="Read the local trace store instead of a dataset")
    variants.add_argument(
        "--variant", action="append", default=None, metavar="NAME=PATH",
        help="Candidate variant name and prompt file (repeat 2-4 times)")
    variants.add_argument(
        "--variants-file", default=None,
        help="JSON spec with target and variants (name/prompt/description)")
    variants.add_argument("--target", default="",
                          help="Recipe/schema identifier recorded in the report")
    variants.add_argument("--labels", default=None,
                          help="JSONL label sidecar for traces or dataset ids")
    variants.add_argument("--scorer", choices=["exact", "contains"], default="exact",
                          help="Built-in label scorer (default: exact)")
    variants.add_argument("--min-samples", type=int, default=5,
                          help="Minimum labeled samples per eligible variant (default: 5)")
    variants.add_argument("--limit", type=int, default=None,
                          help="Read at most this many samples")
    variants.add_argument("--json", action="store_true", help="Print JSON")
    variants.add_argument("--db", default=None, help="Override the trace store path")

    parser.set_defaults(func=cmd_optimize)
