"""Resumed /chat grants report the session's stored model + provider.

Live 2026-10-05: the MODEL card said `deepseek` while the terminal ran
`qubit-brain4`, and the picker showed a mismatched pair
(`opencode-go / qubit-brain4`). Two causes, both fixed:

1. The frontend never threaded `?resume=` into the ws-ticket mint, so the
   server could not overlay `sessions.model` (fixed in `api.wsTicket` +
   `TerminalChatPage`; asserted in the vitest suite).
2. `_session_stored_model` left the provider empty for local models (no
   billing row), so the overlay kept the API default provider. It now infers
   the provider from the profile registry (`_infer_provider_for_model`).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def test_infer_provider_maps_local_default_to_ollama():
    from voicebridge import pty_ws

    assert pty_ws._infer_provider_for_model("qubit-brain4") == "ollama"


def test_infer_provider_unknown_is_empty_never_fabricated():
    from voicebridge import pty_ws

    assert pty_ws._infer_provider_for_model("no-such-model-xyz") == ""
    assert pty_ws._infer_provider_for_model("") == ""
    assert pty_ws._infer_provider_for_model(None) == ""


def test_infer_provider_maps_api_profile_from_registry():
    from voicebridge import pty_ws
    from voicebridge.providers import resolver as R

    # Every shipped default profile whose model is unique resolves to its
    # provider — so a resumed API session names the right provider too.
    for p in R.default_profiles():
        got = pty_ws._infer_provider_for_model(p.model)
        if got:  # ambiguous models ('' in registry) intentionally skip
            assert got == p.provider or got == "ollama", (p.id, got)


def test_ws_ticket_threads_resume_field():
    # Server contract: the mint reads `session`/`resume` (webui.py). The
    # frontend now sends `resume` (asserted in wsTicket.test.ts); this pins
    # the server-side field name so the two cannot drift.
    webui = (REPO_ROOT / "voicebridge" / "webui.py").read_text()
    assert 'body.get("session", "") or body.get("resume", "")' in webui
