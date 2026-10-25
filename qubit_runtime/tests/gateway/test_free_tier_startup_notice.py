"""Home-channel startup notice names the free tier only when a guest carries the gateway's inference.

Post-freeze suite (absent at pin 43faacf): the fork gates "the free tier carries inference" on
``free_inference_active`` — a guest that claimed the welcome trial, read on the desktop's local
backend — so the seeded identity must carry that claim and the process must look like the desktop
backend. The notice contract itself (exactly one line, guest/anonymous never named, explicit keys
win) is unchanged.
"""

import base64
import json
import time

import pytest
from unittest.mock import AsyncMock

import gateway.run as gateway_run
from gateway.config import HomeChannel, Platform
from gateway.platforms.base import SendResult
from qubit_cli import anon_auth
from qubit_cli.auth import _auth_store_lock, _load_auth_store, _save_auth_store
from tests.gateway.restart_test_helpers import make_restart_runner
from tests.qubit_cli.anon_portal import as_local_desktop_backend



def _jwt(**claims) -> str:
    def seg(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    payload = {"sub": "nas_user:1", "client_id": "nas-anonymous", "account_tier": "anonymous",
               "scope": "inference:invoke", "exp": int(time.time()) + 900, **claims}
    return f"{seg({'alg': 'RS256'})}.{seg(payload)}.sig"


def _seed_nous(state: dict) -> None:
    with _auth_store_lock():
        store = _load_auth_store()
        store.setdefault("providers", {})["nous"] = state
        store["active_provider"] = "nous"
        _save_auth_store(store)


def _guest_state() -> dict:
    return {"auth_method": anon_auth.ANON_AUTH_METHOD, "account_tier": "anonymous", "anon_token": "anon_0001",
            "client_id": "nas-anonymous", "access_token": _jwt(), "expires_at": "2999-01-01T00:00:00+00:00",
            "inference_base_url": "https://welcome-api.nousresearch.com/v1",
            anon_auth.WELCOME_CLAIM_FLAG: True}


def _account_state() -> dict:
    return {"auth_method": "oauth", "access_token": _jwt(client_id="hermes-cli", account_tier="pro"),
            "refresh_token": "rt", "expires_at": "2999-01-01T00:00:00+00:00"}


@pytest.fixture
def nous_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway_run, "_qubit_home", tmp_path)
    monkeypatch.setenv("HERMES_SHARED_AUTH_DIR", str(tmp_path / "shared-store"))
    monkeypatch.setenv("HERMES_GUEST_ONBOARDING", "1")
    # The fork's free-tier route only carries inference on the desktop's local backend.
    as_local_desktop_backend(monkeypatch)
    # Provider precedence gates the line and is answered from persisted state only (no network at boot).
    for var in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NOUS_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    runner, adapter = make_restart_runner()
    runner.config.platforms[Platform.TELEGRAM].home_channel = HomeChannel(
        platform=Platform.TELEGRAM, chat_id="home-1", name="Home")
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="home"))
    return runner, adapter


async def _startup_message(runner, adapter) -> str:
    delivered = await runner._send_home_channel_startup_notifications()
    assert delivered == {("telegram", "home-1", None)}
    adapter.send.assert_called_once()
    return adapter.send.call_args.args[1]


@pytest.mark.asyncio
async def test_guest_inference_adds_exactly_one_free_tier_line(nous_runner):
    runner, adapter = nous_runner
    _seed_nous(_guest_state())
    assert anon_auth.has_free_tier_account()

    message = await _startup_message(runner, adapter)

    lines = message.splitlines()
    assert len(lines) == 2 and "free tier" in lines[1].lower()
    assert "guest" not in message.lower() and "anonymous" not in message.lower()


@pytest.mark.asyncio
async def test_signed_in_account_keeps_the_plain_online_notice(nous_runner):
    runner, adapter = nous_runner
    _seed_nous(_account_state())
    assert not anon_auth.has_free_tier_account()

    message = await _startup_message(runner, adapter)

    assert "free tier" not in message.lower()


@pytest.mark.asyncio
async def test_non_nous_provider_never_mentions_the_free_tier(nous_runner, monkeypatch):
    runner, adapter = nous_runner
    _seed_nous(_guest_state())  # identity exists for connectors, but inference is elsewhere
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")  # an explicit key wins provider precedence

    message = await _startup_message(runner, adapter)

    assert "free tier" not in message.lower()
