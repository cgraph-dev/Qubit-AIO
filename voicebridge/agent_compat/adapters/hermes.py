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
* ``skill_roots`` — measured on this machine with
  ``find ~/.hermes -maxdepth 3 -type d -name skills`` (→ the user tree ``~/.hermes/skills``,
  holding 215 ``SKILL.md`` files at depths ``<name>/SKILL.md`` and
  ``<category>/<name>/SKILL.md``) and ``ls ~/.hermes/skills``. The same command also matched two
  roots we do NOT claim: the install's own shipped copy under the home dir, and a bundled
  browser tool's ``skills`` dir — neither is the agent's user skill tree, so both are
  deliberately left out.
* ``skill_frontmatter_aliases`` — measured over the frontmatter of the 215 live skills and the
  58 skills in the pinned tree: the keys in use are ``name, description, version, author,
  license, platforms, metadata, prerequisites, dependencies, tags, triggers, environments,
  required_credential_files``. None of these is a rename of a Qubit key — the host parses the
  SAME keys — so this table is EMPTY by fact. It is declared (not omitted) so the mechanism is
  visible and a future renamed key has an obvious home.
* ``mcp_config_paths`` / ``mcp_config_keys`` — measured: ``find ~/.hermes -maxdepth 2 -iname
  '*mcp*'`` found NO standalone MCP config file (no ``.mcp.json`` / ``mcp.json`` /
  ``mcp_servers.json``; the only hits are logs, a lock, a schema cache, and the ``skills/mcp``
  folder). MCP servers are inlined in the main ``config.yaml`` under the top-level key
  ``mcp_servers:`` (confirmed: ``grep -n mcp ~/.hermes/config.yaml`` → line 617, with two
  entries, ``cgraph-graft`` and ``openclaw-voice-bridge-graft``). So the path is ``config.yaml``
  and the key is ``mcp_servers``, NOT a separate file.
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
    # -- where the agent keeps SKILLS (each a dir with SKILL.md); user tree only, measured
    skill_roots=("~/.hermes/skills",),
    # -- no frontmatter key is renamed (host parses the same keys) — empty by measurement
    skill_frontmatter_aliases={},
    # -- where the agent registers MCP servers: inline in its main config, not a separate file
    mcp_config_paths=("~/.hermes/config.yaml",),
    # -- the top-level key those entries live under
    mcp_config_keys=("mcp_servers",),
    reserved=_RESERVED,
)
