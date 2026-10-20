"""A messaging gateway's first-ever DM carries the baseline profile-build offer."""

from unittest.mock import AsyncMock

import qubit_yaml as yaml
import pytest

from gateway.config import GatewayConfig, Platform
from gateway.run import GatewayRunner
from gateway.session import SessionSource, SessionStore


def _runner(tmp_path, monkeypatch):
    monkeypatch.setattr("gateway.run._qubit_home", tmp_path)
    monkeypatch.setenv("TELEGRAM_HOME_CHANNEL", "home")
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig()
    runner.session_store = SessionStore(sessions_dir=tmp_path / "sessions", config=runner.config)
    runner._deliver_platform_notice = AsyncMock()
    return runner


@pytest.mark.asyncio
async def test_first_dm_gets_the_profile_build_offer_once(tmp_path, monkeypatch):
    from agent.onboarding import PROFILE_BUILD_FLAG, profile_build_directive

    (tmp_path / "config.yaml").write_text("model: test\n", encoding="utf-8")
    runner = _runner(tmp_path, monkeypatch)
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="dm", user_id="u", chat_type="dm")

    first: list[str] = []
    await runner._hmwa_first_contact_notes(source, [], first)
    second: list[str] = []
    await runner._hmwa_first_contact_notes(source, [], second)

    assert first == [profile_build_directive().strip()]
    assert len(second) == 1 and second[0] != first[0]
    seen = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))["onboarding"]["seen"]
    assert seen[PROFILE_BUILD_FLAG] is True


def test_first_contact_signature_matches_across_turn_modules():
    """The turn mixin and its prepare sibling must expose one signature (no split)."""
    import inspect

    from gateway.run_turn import GatewayTurnMixin
    from gateway.run_turn_prepare import GatewayTurnPrepareMixin

    assert (
        inspect.signature(GatewayTurnMixin._hmwa_first_contact_notes)
        == inspect.signature(GatewayTurnPrepareMixin._hmwa_first_contact_notes)
    )
