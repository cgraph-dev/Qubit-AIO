"""Voice-transcript dedup belongs to the bot that heard the utterance and the conversation it is bound to.

The runner keeps one dedup store for every Discord bot it serves. Keyed by guild and speaker alone, a
second bot (another profile) in the same guild dropped its own copy of an utterance because the first
bot had just recorded it, and a rebind to another text channel inherited the old binding's history.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import gateway.run as gateway_run
from gateway.config import Platform
from gateway.session import SessionSource

# M6 Wave: frozen ad12263a5ed4 keys voice-transcript dedup on the receiving adapter's
# binding ``(adapter._owner_profile, guild_id, text_ch_id)`` (gateway/run_voice.py:324-326,
# ``_is_duplicate_voice_transcript(binding, ...)``) so a second bot (another profile) in the
# same guild keeps its own copy. This checkout's run_voice.py:214 keys on ``(guild_id,
# user_id)`` alone and calls it with the bare guild id (run_voice.py:270) — the second bot's
# copy of the utterance is suppressed by the first bot's record. PRODUCT gap:
# test_dedup_is_scoped_to_the_receiving_bot_and_its_binding expects bot_a=1, bot_b=1 but gets
# bot_a=1, bot_b=0. xfail (non-strict); drop once the binding-scoped key lands.
pytestmark = pytest.mark.xfail(
    reason="PRODUCT: gateway/run_voice.py:214 keys voice-transcript dedup on (guild_id, user_id) "
    "instead of (owner_profile, guild_id, text_ch_id) (frozen ad12263a5ed4 run_voice.py:324)",
    strict=False,
)

_GUILD, _USER = 1, 42


class _Bot:
    """What the voice callback reads from a Discord adapter (weakref-able)."""

    def __init__(self, profile: str | None) -> None:
        self._owner_profile = profile
        self._voice_text_channels, self._voice_sources = {_GUILD: 700}, {}
        self._client = SimpleNamespace(get_channel=lambda _id: None, get_guild=lambda _id: None)
        self.handle_message = AsyncMock()

    def build_source(self, **fields) -> SessionSource:
        return SessionSource(platform=Platform.DISCORD, profile=self._owner_profile, **fields)


@pytest.mark.asyncio
async def test_dedup_is_scoped_to_the_receiving_bot_and_its_binding() -> None:
    bot_a, bot_b = _Bot(None), _Bot("bot-b")
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.adapters = {Platform.DISCORD: bot_a}
    runner._voice_mode, runner._session_db, runner.session_store = {}, None, MagicMock()
    runner._canonicalize = lambda source, **_kw: object()
    runner._is_user_authorized = lambda source: True

    async def say(bot: _Bot, text: str = "what broke on the ingest box") -> None:
        await runner._handle_voice_channel_input(_GUILD, _USER, text, adapter=bot)

    await say(bot_a)
    await say(bot_b)
    await say(bot_a)  # the same capture emitted twice: still suppressed
    assert (bot_a.handle_message.await_count, bot_b.handle_message.await_count) == (1, 1)
    bot_a._voice_text_channels[_GUILD] = 800  # /voice join from another text channel
    await say(bot_a)
    assert bot_a.handle_message.await_count == 2
