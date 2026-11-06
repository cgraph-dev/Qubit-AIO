"""Qubit-native HUD backend — the defect-free core of the absorbed HUD dashboard.

Provenance: `joeynyc/hermes-hudui` @ ``be83208f48b7a1c67b7816837c8df71a61ef7edf``
(MIT, Copyright (c) 2026 Hermes HUD Contributors). See ``PROVENANCE.json`` beside
this file for the file-by-file record.

The HUD used to read ``~/.hermes/`` and shell the ``hermes`` CLI. This package
reads QUBIT's own state instead:

* every path resolves from :func:`qubit_constants.get_qubit_home`;
* sessions/cost come from the ONE chat-session authority
  (:class:`voicebridge.qubit_session_store.QubitSessionStore`) — never a second
  opener on the 826 MB ``state.db``;
* cron comes from the live scheduler store through :mod:`voicebridge.cron_bridge`;
* plugins come from QUBIT's own plugin discovery (dashboard manifests + the
  ``plugins.enabled``/``plugins.disabled`` selection), never a manifest field
  read out of a directory.

Every collector is a pure function returning a JSON-ready ``dict``. This module
registers no route; the dashboard route table is owned elsewhere.

PORTS (7 in-scope HUD tabs' backends)
    collectors_health          <- backend/collectors/health.py (+ config.py merge)
    collectors_cron            <- backend/collectors/cron.py
    collectors_plugins         <- backend/collectors/plugins.py
    collectors_skills          <- backend/collectors/skills.py
    collectors_model_info      <- backend/collectors/model_info.py
    collectors_model_analytics <- backend/collectors/model_analytics.py

DROPPED (no truthful Qubit source; a missing panel is correct, a fabricated one
is not)
    SKIP: sudo governance  - HUD classified sudo from gateway.log prose; Qubit
                             has no structured sudo-approval ledger to read.
    SKIP: replay            - HUD replayed state.db rows through a schema it
                             was verified against (v16/23/26/31); Qubit's
                             schema genuinely differs and the session authority
                             exposes no replay/rewind read model.
    SKIP: corrections       - Qubit records no correction events.
    SKIP: patterns          - Qubit records no pattern-mining events.
    SKIP: verify            - HUD shelled `hermes verify`; Qubit has no verify
                             command and no verify artifact to read.
    SKIP: providers         - HUD shelled `hermes auth`; dropped in favour of
                             the credential-reference inventory Qubit does
                             expose (credential-presence diagnostics inside
                             collectors_health — see ``keys``).
    SKIP: projects          - HUD listed ~/projects; Qubit's projects.db has no
                             stable public read seam owned by a single writer.
    SKIP: profiles          - Qubit profiles are independent islands; the HUD's
                             cross-profile aggregate would need a second home
                             reader that violates island isolation.
    SKIP: memory            - HUD parsed ~/.hermes/memory markdown; Qubit's
                             memory providers are plugin-owned and expose no
                             single read authority.
    SKIP: gateway           - HUD shelled `hermes gateway`; Qubit's gateway
                             control is an operator/action surface (voicebridge.
                             approvals), not a read-only collector.
    SKIP: live chat         - out of scope (owner decision) and would need a
                             websocket this module does not own.
    SKIP: live model caps   - COLLAPSED into collectors_model_info, which reads
                             the real models.dev cache Qubit ships.
"""

from __future__ import annotations

__all__ = [
    "branding",
    "collectors_cron",
    "collectors_health",
    "collectors_model_analytics",
    "collectors_model_info",
    "collectors_plugins",
    "collectors_skills",
]

#: The HUD release these collectors were verified against (upstream pin).
HUD_UPSTREAM_PIN = "be83208f48b7a1c67b7816837c8df71a61ef7edf"
#: Upstream license carried with every vendored file.
HUD_UPSTREAM_LICENSE = "MIT"
HUD_UPSTREAM_COPYRIGHT = "Copyright (c) 2026 Hermes HUD Contributors"
