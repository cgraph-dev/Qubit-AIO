"""``qubit traces`` subcommand parser."""
from __future__ import annotations

from typing import Callable


def build_traces_parser(subparsers, *, cmd_traces: Callable) -> None:
    parser = subparsers.add_parser(
        "traces", help="Inspect the local redacted trace store",
        description=("List, show, export or prune local traces. Tracing is opt-in; "
                     "the store never leaves this machine."))
    traces_sub = parser.add_subparsers(dest="traces_action", required=True)

    listing = traces_sub.add_parser("list", help="List recent traces")
    listing.add_argument("--limit", type=int, default=50, help="Maximum rows (default: 50)")
    listing.add_argument("--session", default=None, help="Only this session id")
    listing.add_argument("--json", action="store_true", help="Print JSON")
    listing.add_argument("--db", default=None, help="Override the trace store path")

    show = traces_sub.add_parser("show", help="Show one trace")
    show.add_argument("trace_id", help="Trace id")
    show.add_argument("--json", action="store_true", help="Print JSON")
    show.add_argument("--db", default=None, help="Override the trace store path")

    export = traces_sub.add_parser("export", help="Export traces as JSONL")
    export.add_argument("--output", default=None, help="Write to this file (default: stdout)")
    export.add_argument("--db", default=None, help="Override the trace store path")

    prune = traces_sub.add_parser("prune", help="Apply retention")
    prune.add_argument("--max-age-days", type=int, default=None,
                       help="Delete traces older than this (0 disables)")
    prune.add_argument("--max-records", type=int, default=None,
                       help="Keep only this many newest (0 disables)")
    prune.add_argument("--json", action="store_true", help="Print JSON")
    prune.add_argument("--db", default=None, help="Override the trace store path")

    parser.set_defaults(func=cmd_traces)
