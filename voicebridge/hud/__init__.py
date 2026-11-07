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

PORTS — 19 panels, each with its decision recorded in its own docstring
------------------------------------------------------------------------
Originally seven:

    collectors_health          <- backend/collectors/health.py (+ config.py merge)
    collectors_cron            <- backend/collectors/cron.py
    collectors_plugins         <- backend/collectors/plugins.py
    collectors_skills          <- backend/collectors/skills.py
    collectors_model_info      <- backend/collectors/model_info.py
    collectors_model_analytics <- backend/collectors/model_analytics.py
    collectors_sessions        <- Qubit-authored over the session authority

Then twelve a previous revision of this package DROPPED as "no truthful Qubit
source". That claim was measured and found wrong: most of the upstream
"dropped" tabs are thin routers over collectors whose inputs Qubit already
publishes. Each module below carries the PORT/MAP/GAP decision and the evidence:

    collectors_sudo          MAP   approvals journal + safety policy + the
                                   session authority (prose scraping refused)
    collectors_replay        MAP   session authority list()/messages()
    collectors_corrections   MAP   MemoryStore `supersedes` + `history` ledger
    collectors_patterns      PORT  upstream aggregates over authority inputs;
                                   tool sequences are reported GAP, not faked
    collectors_verify        MAP   voicebridge.checkpoints sha256 re-verify
    collectors_providers     MAP   voicebridge.credentials inventory (no shell)
    collectors_projects      PORT  voicebridge.projects registry + git enrichment
    collectors_profiles      MAP   profile registry + per-island authority read
    collectors_memory        MAP   voicebridge.active_memory + MemoryStore
    collectors_gateway       PORT  gateway_supervisor status + RPC registry
    collectors_live_chat     GAP   no chat WebSocket (WS_SINGLE_PATH); served as
                                   a polled snapshot of the typed event journal
    collectors_live_models   MAP   per-model models.dev caps; live PROBE is GAP

STILL REFUSED — the parts that remain genuinely absent
------------------------------------------------------
    live chat WS               Qubit keeps one event path; ``webui`` refuses a
                               second WS with ``WS_SINGLE_PATH``. The panel shows
                               the journal snapshot and names the refusal.
    live model probe           ``/api/providers/validate`` is a recorded
                               refusal (``_NO_NETWORK_PROBE``); capability rows
                               say "catalog", never "pinged".
    per-step replay trace      the authority publishes role/content/timestamp
                               only; tool_calls/reasoning/token_count are null.
    tool-workflow sequences    same missing column; ``tool_workflows`` is None
                               with a reason.
    sudo outcomes              upstream inferred them from tool blobs and log
                               prose. The journal's decision states are shown
                               instead; no outcome is guessed.
    gateway actions            upstream shelled the CLI; the HUD is a viewer.
"""

from __future__ import annotations

__all__ = [
    "branding",
    "collectors_corrections",
    "collectors_cron",
    "collectors_gateway",
    "collectors_health",
    "collectors_live_chat",
    "collectors_live_models",
    "collectors_memory",
    "collectors_model_analytics",
    "collectors_model_info",
    "collectors_patterns",
    "collectors_plugins",
    "collectors_profiles",
    "collectors_projects",
    "collectors_providers",
    "collectors_replay",
    "collectors_sessions",
    "collectors_skills",
    "collectors_sudo",
    "collectors_verify",
    "profiles_shim",
]

#: The HUD release these collectors were verified against (upstream pin).
HUD_UPSTREAM_PIN = "be83208f48b7a1c67b7816837c8df71a61ef7edf"
#: Upstream license carried with every vendored file.
HUD_UPSTREAM_LICENSE = "MIT"
HUD_UPSTREAM_COPYRIGHT = "Copyright (c) 2026 Hermes HUD Contributors"
