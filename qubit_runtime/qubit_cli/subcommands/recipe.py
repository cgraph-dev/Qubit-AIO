"""``qubit recipe`` subcommand parser."""
from __future__ import annotations

from typing import Callable


def build_recipe_parser(subparsers, *, cmd_recipe: Callable) -> None:
    parser = subparsers.add_parser(
        "recipe", help="List and run Qubit recipes",
        description="List registered recipes or run one recipe through a RuntimePort engine.")
    recipe_sub = parser.add_subparsers(dest="recipe_action", required=True)

    listing = recipe_sub.add_parser("list", help="List registered recipes")
    listing.add_argument("--json", action="store_true", help="Print JSON")

    run = recipe_sub.add_parser("run", help="Run one recipe")
    run.add_argument("recipe_id", help="Recipe id (for example morning_digest)")
    run.add_argument("--engine", default="native", help="Engine name (default: native)")
    run.add_argument("--topic", default="",
                     help="Topic for input-driven recipes (for example deep_research)")
    run.add_argument("--dry-run", action="store_true",
                     help="Collect and render facts without calling a model")
    run.add_argument("--json", action="store_true", help="Print JSON")
    parser.set_defaults(func=cmd_recipe)
