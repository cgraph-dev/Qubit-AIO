"""Telegram notification-mode gating (#132516): "important" mode silences ordinary sends, but a
human-decision prompt (``is_approval_prompt``) must always push — a silently delivered prompt is
indistinguishable from "no prompt" and costs the full approvals.timeout before the command is refused.
"""

import pytest

from plugins.platforms.telegram.adapter import TelegramAdapter

# M6 Wave: frozen ad12263a5ed4's TelegramAdapter._notification_kwargs
# (plugins/platforms/telegram/adapter.py:791) admits a send when ``metadata["notify"]`` OR
# ``metadata["is_approval_prompt"]`` is set, so a human-decision prompt always pushes (#132516).
# This checkout's adapter.py:888 admits only ``metadata["notify"]`` — the ``is_approval_prompt``
# bypass is missing, so an approval/clarify prompt is silently delivered with
# ``disable_notification=True`` and waits out the full approvals timeout. PRODUCT gap.
pytestmark = pytest.mark.xfail(
    reason="PRODUCT: plugins/platforms/telegram/adapter.py:888 _notification_kwargs lacks the "
    "is_approval_prompt bypass (frozen ad12263a5ed4 adapter.py:791)",
    strict=False,
)


@pytest.mark.parametrize("mode,metadata,expected", [
    ("important", None, {"disable_notification": True}),
    ("important", {"thread_id": "t1"}, {"disable_notification": True}),
    ("important", {"notify": True}, {}),
    ("important", {"thread_id": "t1", "is_approval_prompt": True}, {}),
    ("all", None, {}),
    ("all", {"is_approval_prompt": True}, {}),
])
def test_notification_kwargs(mode, metadata, expected):
    adapter = object.__new__(TelegramAdapter)
    adapter._notifications_mode = mode
    assert adapter._notification_kwargs(metadata) == expected


def test_clarify_text_fallback_pushes_in_important_mode():
    """A failed native clarify card retries as base numbered text via adapter.send(); that
    human-decision prompt must not go out with disable_notification=True."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from gateway.config import PlatformConfig
    from gateway.run_turn_runner_clarify_delivery import text_fallback_coro

    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="t", extra={}))
    adapter._bot, adapter._app = AsyncMock(), MagicMock()
    adapter._bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1))
    adapter._notifications_mode = "important"

    asyncio.run(text_fallback_coro(
        adapter, chat_id="123", question="Which env?", choices=None, clarify_id="c1", session_key="s", metadata=None))

    assert adapter._bot.send_message.await_args.kwargs.get("disable_notification") is None
