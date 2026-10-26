"""``qubit runtime`` subcommand parser."""
from __future__ import annotations

from typing import Callable


def build_runtime_parser(subparsers, *, cmd_runtime: Callable) -> None:
    """Attach the diagnostic ``runtime`` subcommand to ``subparsers``."""
    parser = subparsers.add_parser(
        "runtime", help="Inspect and run agent engines",
        description="Inspect the registered RuntimePort engines or run one diagnostic turn.")
    runtime_sub = parser.add_subparsers(dest="runtime_action", required=True)
    runtime_sub.add_parser("engines", help="List registered engines")
    run = runtime_sub.add_parser("run", help="Run one turn through an engine")
    run.add_argument("--engine", default="native", help="Engine name (default: native)")
    run.add_argument("--prompt", default=None, help="Prompt text")
    run.add_argument("--query-file", default=None,
                     help="Read the prompt from a UTF-8 file")
    run.add_argument("--json", action="store_true", help="Print the event stream as JSON")
    parser.set_defaults(func=cmd_runtime)
