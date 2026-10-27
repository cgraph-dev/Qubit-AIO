"""``qubit agent`` subcommand parser."""
from __future__ import annotations

import argparse
from typing import Callable


def build_agent_parser(subparsers, *, cmd_agent: Callable) -> None:
    """Attach ``agent roles`` / ``agent run`` to the top-level parser."""
    parser = subparsers.add_parser(
        "agent", help="List and run Qubit-native agent roles",
        description="List registered agent roles or run one role through the "
                    "native RuntimePort turn path.")
    agent_sub = parser.add_subparsers(dest="agent_action", required=True)

    listing = agent_sub.add_parser("roles", help="List registered agent roles")
    listing.add_argument("--json", action="store_true", help="Print JSON")

    run = agent_sub.add_parser("run", help="Run one agent role")
    run.add_argument("role", help="Role id (for example orchestrator)")
    run.add_argument("--prompt", default="",
                     help="Prompt text for this role turn")
    run.add_argument("--engine", default=argparse.SUPPRESS,
                     help="Engine name (default: native)")
    run.add_argument("--json", action="store_true", help="Print JSON")
    parser.set_defaults(func=cmd_agent)
