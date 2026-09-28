"""``qubit fallback`` subcommand parser."""

from __future__ import annotations

from qubit_cli import install_source


def build_fallback_parser(subparsers) -> None:
    """Attach the ``fallback`` subcommand to ``subparsers``."""
    from qubit_cli.fallback_cmd import cmd_fallback

    fallback_parser = subparsers.add_parser(
        "fallback", help="Manage fallback providers (tried when the primary model fails)",
        description="Manage the fallback provider chain.  Fallback providers are tried "
            "in order when the primary model fails with rate-limit, overload, or "
            "connection errors.  See: "
            f"{install_source.docs_url('user-guide/features/fallback-providers')}")
    fallback_subparsers = fallback_parser.add_subparsers(dest="fallback_command")
    fallback_subparsers.add_parser(
        "list", aliases=["ls"], help="Show the current fallback chain (default when no subcommand)")
    fallback_subparsers.add_parser(
        "add",
        help="Pick a provider + model (same picker as `qubit model`) and append to the chain")
    fallback_subparsers.add_parser(
        "remove", aliases=["rm"], help="Pick an entry to delete from the chain")
    fallback_subparsers.add_parser("clear", help="Remove all fallback entries")
    fallback_parser.set_defaults(func=cmd_fallback)
