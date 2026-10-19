"""A replaced Telegram adapter's held inbound survives the swap (#132829).

PTB has already acked the update and advanced the offset, so an event the retired instance is
holding is not retried by Telegram: if the replacement does not adopt it, it is silently lost.
Two halves must both exist — the successor's ``adopt_held_inbound`` (the mixin on
``TelegramAdapter``) and the runner calling ``hand_over_held_inbound`` at publish time.
"""

import asyncio
import weakref

from gateway.config import Platform, PlatformConfig
from gateway.platforms.event import MessageEvent, MessageType
from gateway.platforms.helpers import hand_over_held_inbound


def _make_adapter():
    from plugins.platforms.telegram.adapter import TelegramAdapter

    adapter = object.__new__(TelegramAdapter)
    adapter._platform = Platform.TELEGRAM
    adapter.platform = Platform.TELEGRAM
    adapter.config = PlatformConfig(enabled=True, token="test-token")
    adapter._running = True
    adapter._fatal_error_code = None
    adapter._fatal_error_message = None
    adapter._fatal_error_retryable = True
    adapter._drop_delayed_deliveries = True   # the retired instance never reconnects
    adapter._held_inbound_events = []
    adapter._held_inbound_redispatch_task = None
    adapter.HELD_INBOUND_MAX = 64
    adapter.handle_message = _async_noop
    return adapter


async def _async_noop(*a, **k):
    return None


def _event(text="held across the swap"):
    from gateway.platforms.base import SessionSource

    return MessageEvent(text=text, message_type=MessageType.TEXT,
                        source=SessionSource(platform=Platform.TELEGRAM, chat_id="1", chat_type="dm"))


def test_the_replacement_adapter_can_adopt_a_predecessors_queue():
    """The capability the runner's publish-time call depends on."""
    from plugins.platforms.telegram.telegram_held_inbound import TelegramHeldInboundMixin

    assert issubclass(__import__(
        "plugins.platforms.telegram.adapter", fromlist=["TelegramAdapter"]).TelegramAdapter,
        TelegramHeldInboundMixin)


def test_hand_over_moves_the_held_event_and_retargets_its_transport():
    predecessor, successor = _make_adapter(), _make_adapter()
    event = _event()
    predecessor._held_inbound_events.append(event)

    hand_over_held_inbound(predecessor, successor)

    assert predecessor._held_inbound_events == []          # moved, not copied
    assert [e.text for e in successor._held_inbound_events] == ["held across the swap"]
    # A later hold on the retired instance forwards to the live one instead of stranding.
    assert predecessor._held_inbound_successor() is successor
    # The event keeps its resolved profile/authz but streams through the live transport.
    assert event.source._transport_adapter_ref() is successor


def test_a_retired_adapter_forwards_new_holds_to_its_successor():
    predecessor, successor = _make_adapter(), _make_adapter()
    hand_over_held_inbound(predecessor, successor)
    late = _event("arrived after the swap")

    predecessor._hold_inbound_event(late, where="test")

    assert successor._held_inbound_events == [late]
    assert predecessor._held_inbound_events == []


def test_hand_over_is_a_no_op_without_a_finished_replacement():
    predecessor = _make_adapter()
    predecessor._held_inbound_events.append(_event())

    hand_over_held_inbound(None, predecessor)   # first publish: no predecessor yet
    assert len(predecessor._held_inbound_events) == 1
    hand_over_held_inbound(predecessor, object())  # replacement without the capability
    assert len(predecessor._held_inbound_events) == 1
