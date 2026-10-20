"""A connectors-only grant stays beside the inference credential; it never replaces or clears it.

Upstream guards both halves of the exchange: ``exchange_anon_jwt`` skips
``note_optional_challenges`` for a connectors-only token, and ``apply_exchange_to_state`` writes it
to ``connectors_token`` instead of overwriting the inference token (and its routing).
"""

from __future__ import annotations

import httpx
import pytest

from qubit_cli import anon_auth, anon_challenge
from tests.qubit_cli.anon_portal import PORTAL, WELCOME, make_jwt


def _exchange(monkeypatch, token: str) -> tuple[dict, list[dict]]:
    noted: list[dict] = []
    monkeypatch.setattr(anon_challenge, "note_optional_challenges",
                        lambda payload, portal: noted.append(payload))

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": token, "expires_in": 900})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return anon_auth.exchange_anon_jwt(client, PORTAL, "anon_0001", auth_state={}), noted


def test_connectors_only_exchange_does_not_touch_the_inference_challenge(monkeypatch):
    token = make_jwt(scope="connectors:invoke")
    payload, noted = _exchange(monkeypatch, token)
    assert payload["access_token"] == token
    assert noted == []


def test_inference_exchange_still_notes_optional_challenges(monkeypatch):
    token = make_jwt(scope="inference:invoke")
    _, noted = _exchange(monkeypatch, token)
    assert len(noted) == 1


def test_connectors_only_exchange_lands_beside_the_inference_token():
    inference = make_jwt(scope="inference:invoke")
    connectors = make_jwt(scope="connectors:invoke")
    state = {
        "access_token": inference,
        "expires_at": "2099-01-01T00:00:00+00:00",
        "inference_base_url": WELCOME,
    }

    anon_auth.apply_exchange_to_state(state, {"access_token": connectors})

    assert state["access_token"] == inference
    assert state["inference_base_url"] == WELCOME
    assert state["connectors_token"]["access_token"] == connectors
    assert state["connectors_token"]["expires_at"]


def test_inference_exchange_still_replaces_the_state_token():
    inference = make_jwt(scope="inference:invoke")
    state: dict = {}

    anon_auth.apply_exchange_to_state(
        state, {"access_token": inference, "inference_base_url": WELCOME})

    assert state["access_token"] == inference
    assert state["inference_base_url"] == WELCOME
    assert "connectors_token" not in state


def test_connectors_only_guest_is_visible_to_managed_tool_readers(monkeypatch):
    """The connector bearer a managed-tools call sends must be the connectors-only grant.

    A guest with no inference token has no ``access_token``; before this was wired the reader
    returned None and every managed-tools/connector call looked unauthenticated even though the
    grant was stored (writer/reader split).
    """
    from tools import managed_tool_gateway as mtg

    connectors = make_jwt(scope="connectors:invoke")
    state = {"auth_method": "anonymous", "anon_token": "anon_1"}
    anon_auth.apply_exchange_to_state(state, {"access_token": connectors})
    assert state["connectors_token"]["access_token"] == connectors

    monkeypatch.setattr(mtg, "_read_nous_provider_state", lambda: state)
    monkeypatch.setattr(mtg, "_read_user_token_override", lambda: None)

    assert mtg.peek_nous_access_token() == connectors
    monkeypatch.setattr(
        "qubit_cli.auth.resolve_nous_access_token",
        lambda **_: pytest.fail("a connectors-only grant must not run the inference refresh"),
    )
    assert mtg.read_nous_access_token() == connectors
