"""Adapter #1: Hermes Agent — the reference integration for the foreign-agent framework.

This module is PURE DATA. It contains no engine code; the mechanism lives in
``voicebridge.agent_compat.framework``. Supporting a second agent means writing a file like
this one, not touching the framework.

It also has **no runtime dependency on Hermes source**. Deleting the Hermes install changes
nothing here — the tables describe name mappings, and the names they map to are Qubit's own.

HOW THE TABLES WERE DERIVED (all measured, not guessed)
------------------------------------------------------
* ``module_aliases`` / ``prefix_aliases`` — from the fork's actual renames, each verified by
  confirming the target module exists in Qubit's tree.
* ``symbol_aliases`` — measured across 240 upstream plugin files: only TWO renamed symbols are
  imported anywhere in the ecosystem, so this table is short by fact, not by omission.
* ``env_aliases`` — measured: all 69 distinct ``HERMES_*`` env names used by plugins are already
  live in Qubit's tree (0 unknown), so only the one genuinely renamed home variable is listed.
* ``reserved`` — the names Qubit deliberately keeps, recorded so a future contributor sees the
  intent rather than "fixing" an apparent gap.
"""
from __future__ import annotations

from ..framework import ForeignAdapter

#: Names Qubit deliberately KEEPS. Aliasing any of these would be wrong, and each one has a
#: reason — do not "complete" the tables below by adding them.
_RESERVED = (
    # The distribution / entry-point group name. Kept so pip-installed Hermes plugins stay
    # discoverable; the importable core never had a `hermes_agent` module.
    "hermes_agent",
    # Wire-format strings. Metric names are emitted as `hermes.*` and the dashboard expects
    # them (upstream uses `hermes.` 99x, Qubit's own contract file 21x). Rewriting these would
    # break metric ingestion.
    "hermes_version",
    # Legacy on-disk data paths (`~/.hermes/...`) that existing installs still read.
    "hermes_home",
)


ADAPTER = ForeignAdapter(
    name="hermes",
    # -- modules the fork renamed, verified present in Qubit's tree
    module_aliases={
        "hermes_cli": "qubit_cli",
        "hermes_constants": "qubit_constants",
        "hermes_logging": "qubit_logging",
        "hermes_state": "qubit_state",
        "hermes_bootstrap": "qubit_bootstrap",
        "hermes_platform": "qubit_platform",
        "hermes_yaml": "qubit_yaml",
        "hermes_time": "qubit_time",
        "hermes_dir": "qubit_dir",
        "hermes_tools": "qubit_tools",
        "hermes_state_errors": "qubit_state_errors",
    },
    # -- decomposed sibling families; prefix rules avoid enumerating 20+ siblings that would
    #    rot as upstream adds more
    prefix_aliases={
        "hermes_state_": "qubit_state_",
        "hermes_cli.": "qubit_cli.",
        "hermes_platform.": "qubit_platform.",
    },
    # -- modules that kept their name but renamed an ATTRIBUTE (measured: 2 ecosystem-wide)
    symbol_aliases={
        "qubit_constants": {"hermes_home_key": "qubit_home_key"},
        "tools.xai_http": {"hermes_xai_user_agent": "qubit_xai_user_agent"},
    },
    # -- the one genuinely renamed environment variable
    env_aliases={
        "HERMES_HOME": "QUBIT_HOME",
    },
    # -- entry-point groups Qubit keeps accepting, so pip plugins stay discoverable
    entry_point_groups=("hermes_agent.plugins",),
    # -- import markers that identify Hermes-sourced source files
    detection_markers=("hermes_cli", "hermes_agent", "hermes_platform", "hermes_state"),
    # -- conventional plugin dirs
    content_roots=("~/.hermes/plugins", "./.hermes/plugins"),
    reserved=_RESERVED,
)
