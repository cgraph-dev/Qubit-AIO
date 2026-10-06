"""Adapter #2: OpenClaw — a JavaScript/TypeScript agent, hosted by the same framework.

This module is PURE DATA. It contains no engine code; the mechanism lives in
``voicebridge.agent_compat.framework``. Supporting a second agent meant writing a file like
this one, not touching the framework — which is the whole claim the framework exists to make.

It also has **no runtime dependency on OpenClaw source**. Deleting the OpenClaw checkout
changes nothing here — the tables describe name mappings and detection markers, and the names
they map to are Qubit's own.

WHY THIS ADAPTER LOOKS DIFFERENT FROM ``hermes.py`` (and why that is the point)
-------------------------------------------------------------------------------
Hermes is a Python agent, so its adapter is almost entirely ``module_aliases``: a foreign
``import hermes_cli`` resolves to Qubit's ``qubit_cli`` in-process. **OpenClaw is not Python.**
A pinned measurement of the tree shows it is a TypeScript/npm monorepo (v2026.6.8): of its
11 ``*.py`` files, none is an importable module and none imports an ``openclaw*`` name — every
one is build/e2e tooling (``skills/*/scripts``, ``scripts/e2e``, ``.agents/skills``).

That measurement is the load-bearing fact behind this file: **there is no Python module name
for this adapter to alias.** An in-process ``import openclaw`` does not exist upstream and must
NOT be fabricated — inventing a target would make foreign content appear to load while doing
nothing, exactly the silent failure the framework's doctrine forbids. So the three alias
tables are empty *by measurement, not by omission*, and the adapter instead records what
OpenClaw's ecosystem actually uses: its npm import surface, its ``package.json`` manifest
fields, and its state/env conventions. Those are what detection and discovery read.

HOW THE TABLES WERE DERIVED (all measured against the pin, not guessed)
-----------------------------------------------------------------------
* The pin measured is ``~/.cache/qubit-pins/openclaw`` (package ``openclaw``, version
  ``2026.6.8``, HEAD ``da03996ab7203fe6b9b4d8edb2b54726e4394c9c``).
* ``module_aliases`` / ``prefix_aliases`` / ``symbol_aliases`` — EMPTY. Measured: 0 Python
  modules named ``openclaw*`` exist in the tree, and 0 of its 11 Python files import one.
  There is no name to rename, so no mapping is stated. (Contrast Hermes, whose renames are
  real Python imports.)
* ``entry_point_groups`` — EMPTY, and deliberately so. OpenClaw's plugin distributions do NOT
  advertise a Python entry-point group; they are npm packages that declare two ``package.json``
  fields: ``openclaw.extensions`` (the entry-path list; 4 declarations measured) and the
  external-code-plugin contract fields ``openclaw.compat.pluginApi`` /
  ``openclaw.build.openclawVersion``. There is no console-entry-point string to add, so this
  table stays empty rather than carrying an invented group name. See ``reserved``.
* ``env_aliases`` — measured 1730 distinct ``OPENCLAW_*`` names across the tree; the home/config
  ones that correspond to a Qubit variable are listed (both verified live in Qubit's tree).
  The overwhelming majority (``OPENCLAW_GATEWAY_*``, ``OPENCLAW_TEST_*``, ``OPENCLAW_SKIP_*``,
  service-manager markers, …) have NO Qubit counterpart and are left UNVERIFIED/absent — a
  wrong mapping corrupts silently, an absent one fails loudly.
* ``detection_markers`` — the import strings OpenClaw content actually carries. Measured: the
  plugin-SDK path ``openclaw/plugin-sdk`` appears in 8865 import statements across 3421 files;
  the scoped workspace form ``@openclaw/`` appears in 1657 files. Both are IMPORT fragments,
  never prose. NOTE for a future reader: OpenClaw is JS, so its real statement reads
  ``import { x } from "openclaw/plugin-sdk"``; the framework matches the substring
  ``from <marker>`` (or ``import <marker>``), so the marker is stored WITH the leading
  double-quote (``"openclaw/plugin-sdk``) — that is the only form for which
  ``from "openclaw/plugin-sdk`` is actually present in the source. Verified by running the
  registry's own ``detect()``: the unquoted marker matches nothing.
* ``content_roots`` — OpenClaw's home is ``~/.openclaw`` (via ``OPENCLAW_HOME`` /
  ``OPENCLAW_STATE_DIR``); its plugin trees are ``<stateDir>/plugins`` and the workspace
  ``./.openclaw/extensions``. Both measured from the tree's own path joins, not assumed.
* ``reserved`` — the names Qubit or OpenClaw deliberately KEEPS, recorded so a future
  contributor sees the intent rather than "completing" an apparent gap.
"""
from __future__ import annotations

from ..framework import ForeignAdapter

#: Names deliberately KEPT. Aliasing any of these would be wrong; do not "complete" the
#: empty alias tables below by adding any of them.
_RESERVED = (
    # The npm package identity. OpenClaw plugin content is published as the npm package
    # `openclaw` (export `openclaw/plugin-sdk`) and the `@openclaw/*` scope. These are
    # package names, not Python modules — there is nothing in-process to rename them to, and
    # Qubit ships no npm package by that name. Reserved so nobody invents an alias.
    "openclaw",
    "@openclaw",
    # The `package.json` manifest sub-fields that identify OpenClaw plugin distributions.
    # They are data INSIDE a foreign manifest, not importable names; mapping them would be
    # meaningless. Kept as-is so OpenClaw packages stay self-describing.
    "openclaw.extensions",
    "openclaw.compat.pluginApi",
    "openclaw.build.openclawVersion",
    # OpenClaw's own state directory NAME (`~/.openclaw`). The env vars that POINT at it are
    # aliased below (their value is a path Qubit can retarget); the literal directory name is
    # left alone so an existing install's on-disk layout is not rewritten underneath it.
    "openclaw_home",
)


ADAPTER = ForeignAdapter(
    name="openclaw",
    # -- EMPTY BY MEASUREMENT. OpenClaw is a TypeScript/npm monorepo; it has no Python module
    #    named `openclaw*` and no Python file importing one, so there is no module rename to
    #    express here. (Hermes, a Python agent, is the adapter with a populated table.)
    module_aliases={},
    # -- EMPTY BY MEASUREMENT, same reason: no `openclaw*` Python module families exist.
    prefix_aliases={},
    # -- EMPTY BY MEASUREMENT: symbol_aliases only apply where a module kept its name but
    #    renamed an ATTRIBUTE. No such Python module exists for OpenClaw.
    symbol_aliases={},
    # -- the OPENCLAW_* names that correspond to a Qubit variable. Measured: 1730 distinct
    #    OPENCLAW_* names exist; only the home/config pointer has a Qubit counterpart, and
    #    both targets are live in Qubit's tree. Everything else is left OUT (fails loudly, as
    #    it should) rather than guessed.
    env_aliases={
        "OPENCLAW_HOME": "QUBIT_HOME",
        "OPENCLAW_STATE_DIR": "QUBIT_HOME",
    },
    # -- EMPTY BY MEASUREMENT: OpenClaw plugins are npm packages advertising `package.json`
    #    fields (`openclaw.extensions` + the compat fields), NOT a Python entry-point group.
    #    There is no console entry-point string to keep accepting.
    entry_point_groups=(),
    # -- import fragments that identify OpenClaw-sourced content. Both are the real import
    #    paths measured in the tree (8865x `openclaw/plugin-sdk`, 1657 files `@openclaw/`),
    #    never prose. The leading double-quote is PART of the marker on purpose: the framework
    #    matches the substring ``from <marker>``, and OpenClaw's real statement is
    #    ``import { x } from "openclaw/plugin-sdk"`` — so the marker must be the QUOTED form
    #    (``"openclaw/plugin-sdk``) for ``from "openclaw/plugin-sdk`` to be found. Verified by
    #    running the registry's own detect(): the unquoted form matches nothing.
    detection_markers=(
        '"openclaw/plugin-sdk',
        '"@openclaw/',
    ),
    # -- OpenClaw's conventional plugin/extension dirs (stateDir = ~/.openclaw by default;
    #    extensions also load from a workspace ``.openclaw/`` tree).
    content_roots=(
        "~/.openclaw/plugins",
        "./.openclaw/plugins",
        "./.openclaw/extensions",
    ),
    reserved=_RESERVED,
)
