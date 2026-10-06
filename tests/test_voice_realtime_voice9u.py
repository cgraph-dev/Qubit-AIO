"""Realtime-voice provider abstraction (speech-to-speech foundation).

Hermetic: pure types/registry/bridge, no I/O, no network, no secrets. Pins:

* the id type normalizes and keys a provider registry;
* the registry is order-deterministic, replaces duplicates in place, and
  resolves unknown/malformed/``None`` ids to ``None`` without raising;
* capabilities + audio-format constants are data, frozen, and JSON-safe;
* the null provider's bridge accepts every send, is never active, emits
  nothing, and never raises;
* bridge events map onto ``talk_events.TALK_EVENT_TYPES`` where a boundary
  exists, and unmapped events are dropped without moving the sequence;
* callbacks are invoked with the right shapes and a raising observer is
  swallowed.
"""
from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import realtime_voice as RV
from voicebridge.voice_edit.talk_events import TALK_EVENT_TYPES, TalkEventSequencer


# --------------------------------------------------------------- test doubles
class _FakeBridge(RV.RealtimeVoiceBridge):
    """Records sends so tests can assert the bridge contract."""

    def __init__(self, request, callbacks):
        self.request = request
        self.callbacks = callbacks
        self.sent = []
        self.closed = False
        self.active = True

    def send_audio(self, audio):
        self.sent.append(("audio", audio))

    def send_text(self, text):
        self.sent.append(("text", text))

    def send_tool_result(self, call_id, payload=None, options=None):
        self.sent.append(("tool", call_id, payload, options))

    def close(self):
        self.closed = True
        self.active = False

    def is_active(self):
        return self.active


class _FakeProvider(RV.RealtimeVoiceProvider):
    def __init__(self, provider_id, *, caps=None, config=None):
        self._id = provider_id
        self._caps = caps or RV.RealtimeVoiceProviderCapabilities()
        self._config = dict(config or {})

    @property
    def id(self):
        return self._id

    def capabilities(self):
        return self._caps

    def resolve_config(self, ctx):
        return dict(self._config)

    def create_bridge(self, request, callbacks):
        return _FakeBridge(request, callbacks)


class _Recorder:
    def __init__(self):
        self.events = []
        self.audio = []
        self.tools = []
        self.closes = []
        self.errors = []

    def callbacks(self):
        return RV.RealtimeVoiceBridgeCallbacks(
            on_event=self.events.append,
            on_audio=self.audio.append,
            on_tool_call=self.tools.append,
            on_close=self.closes.append,
            on_error=self.errors.append,
        )


# -------------------------------------------------------------------- ids
def test_provider_id_is_a_str_subclass_that_normalizes():
    pid = RV.RealtimeVoiceProviderId("  OpenAI  ")
    assert isinstance(pid, str)
    assert pid == "  OpenAI  "  # str semantics preserved on the instance
    assert RV.RealtimeVoiceProviderId.normalize("  OpenAI  ") == "openai"
    assert RV.RealtimeVoiceProviderId.NULL == "null"
    assert RV.NULL_PROVIDER_ID == "null"


def test_provider_id_normalize_returns_none_for_junk():
    for junk in (None, "", "   ", 123, object(), ["openai"]):
        assert RV.RealtimeVoiceProviderId.normalize(junk) is None


# --------------------------------------------------------------- registry
def test_registry_register_get_list_preserves_order():
    reg = RV.RealtimeVoiceProviderRegistry()
    a = _FakeProvider(RV.RealtimeVoiceProviderId("alpha"))
    b = _FakeProvider(RV.RealtimeVoiceProviderId("beta"))
    c = _FakeProvider(RV.RealtimeVoiceProviderId("gamma"))
    assert reg.register(a) is True
    assert reg.register(b) is True
    assert reg.register(c) is True
    assert reg.list() == (a, b, c)
    assert reg.ids() == ("alpha", "beta", "gamma")
    assert reg.get("alpha") is a
    assert reg.get("GAMMA") is c  # normalized lookup
    assert len(reg) == 3
    assert "beta" in reg


def test_registry_unknown_and_malformed_ids_resolve_to_none():
    reg = RV.RealtimeVoiceProviderRegistry()
    reg.register(_FakeProvider(RV.RealtimeVoiceProviderId("alpha")))
    for bogus in (None, "", "nope", 123, object(), ["alpha"]):
        assert reg.get(bogus) is None
    assert reg.get(_FakeProvider(RV.RealtimeVoiceProviderId("alpha"))) is None
    assert "nope" not in reg


def test_registry_duplicate_replaces_in_place_without_moving_order():
    reg = RV.RealtimeVoiceProviderRegistry()
    a = _FakeProvider(RV.RealtimeVoiceProviderId("alpha"))
    b = _FakeProvider(RV.RealtimeVoiceProviderId("beta"))
    a2 = _FakeProvider(RV.RealtimeVoiceProviderId("ALPHA"))
    reg.register(a)
    reg.register(b)
    assert reg.register(a2) is True
    assert reg.list() == (a2, b)  # replaced, position preserved
    assert reg.ids() == ("alpha", "beta")
    assert reg.get("alpha") is a2
    assert len(reg) == 2


def test_registry_rejects_providers_without_a_usable_id_and_never_raises():
    reg = RV.RealtimeVoiceProviderRegistry()
    assert reg.register(None) is False
    assert reg.register(object()) is False
    assert reg.register(_FakeProvider("")) is False
    assert reg.register(_FakeProvider(None)) is False
    assert reg.register(123) is False
    assert reg.list() == ()
    assert reg.ids() == ()
    assert len(reg) == 0


def test_registry_reads_id_from_a_zero_arg_callable():
    class CallableId:
        def id(self):
            return "dynamic"

        def capabilities(self):
            return RV.RealtimeVoiceProviderCapabilities()

        def resolve_config(self, ctx):
            return {}

        def create_bridge(self, request, callbacks):
            return _FakeBridge(request, callbacks)

    reg = RV.RealtimeVoiceProviderRegistry()
    prov = CallableId()
    assert reg.register(prov) is True
    assert reg.get("dynamic") is prov


# ----------------------------------------------------------- capabilities
def test_capabilities_are_frozen_data_and_json_safe():
    caps = RV.RealtimeVoiceProviderCapabilities(
        supports_audio_in=True,
        supports_audio_out=True,
        supports_tools=True,
        supports_barge_in=True,
        input_audio_formats=(RV.PCM16_16000_IN,),
        output_audio_formats=(RV.PCM16_24000_OUT,),
        transports=("websocket",),
    )
    assert caps.supports_audio_in is True
    assert caps.supports_tools is True
    payload = caps.to_dict()
    assert payload["input_audio_formats"][0]["sample_rate_hz"] == 16000
    assert payload["output_audio_formats"][0]["sample_rate_hz"] == 24000
    assert payload["transports"] == ["websocket"]
    with pytest.raises(FrozenInstanceError):
        caps.supports_tools = False


def test_audio_format_constants_are_exact_and_frozen():
    assert RV.PCM16_16000_IN == RV.RealtimeVoiceAudioFormat("pcm16", 16000, 1)
    assert RV.PCM16_24000_OUT == RV.RealtimeVoiceAudioFormat("pcm16", 24000, 1)
    assert RV.G711_ULAW_8000 == RV.RealtimeVoiceAudioFormat("g711_ulaw", 8000, 1)
    assert RV.PCM16_16000_IN.encoding == "pcm16"
    assert RV.PCM16_24000_OUT.sample_rate_hz == 24000
    assert RV.PCM16_16000_IN.to_dict() == {
        "encoding": "pcm16", "sample_rate_hz": 16000, "channels": 1,
    }
    with pytest.raises(FrozenInstanceError):
        RV.PCM16_16000_IN.channels = 2


# -------------------------------------------------------------- null provider
def test_null_provider_identity_and_empty_capabilities():
    prov = RV.NullRealtimeVoiceProvider()
    assert prov.id == RV.NULL_PROVIDER_ID == "null"
    caps = prov.capabilities()
    assert caps == RV.NULL_PROVIDER_CAPABILITIES
    assert caps.supports_audio_in is False
    assert caps.supports_audio_out is False
    assert caps.supports_tools is False
    assert caps.supports_barge_in is False
    assert caps.input_audio_formats == ()
    assert caps.output_audio_formats == ()


def test_default_registry_contains_only_the_null_fallback():
    reg = RV.build_default_registry()
    assert reg.list() == (reg.get("null"),)
    assert isinstance(reg.get("null"), RV.NullRealtimeVoiceProvider)
    # a fresh factory never shares state
    assert RV.build_default_registry().list() is not reg.list()


def test_null_provider_bridge_accepts_every_send_and_is_never_active():
    prov = RV.NullRealtimeVoiceProvider()
    request = RV.RealtimeVoiceBridgeCreateRequest(
        provider_config={"model": "none"},
        audio_format=RV.PCM16_16000_IN,
        instructions="be quiet",
    )
    callbacks = RV.RealtimeVoiceBridgeCallbacks()
    bridge = prov.create_bridge(request, callbacks)
    assert isinstance(bridge, RV.RealtimeVoiceBridge)
    assert bridge.is_active() is False
    assert bridge.supports_tool_result_continuation is False
    # sends are accepted and discarded, including junk payloads
    bridge.send_audio(b"\x00\x01\x02")
    bridge.send_audio(None)
    bridge.send_text("hello")
    bridge.send_text(None)
    bridge.send_tool_result("call-1", {"ok": True})
    bridge.send_tool_result("call-2", None,
                            RV.RealtimeVoiceToolResultOptions(will_continue=True))
    bridge.send_tool_result(None, object())
    bridge.close()
    bridge.close()  # idempotent
    assert bridge.is_active() is False


def test_null_provider_emits_nothing_when_driven():
    prov = RV.NullRealtimeVoiceProvider()
    rec = _Recorder()
    bridge = prov.create_bridge(
        RV.RealtimeVoiceBridgeCreateRequest(), rec.callbacks()
    )
    bridge.send_audio(b"\x00")
    bridge.send_text("hi")
    bridge.send_tool_result("c", {})
    bridge.close()
    # documented: the null bridge has no output side and fires no callbacks
    assert rec.events == []
    assert rec.audio == []
    assert rec.tools == []
    assert rec.closes == []


def test_null_provider_resolve_config_returns_a_copy_and_never_raises():
    prov = RV.NullRealtimeVoiceProvider()
    ctx = RV.RealtimeVoiceProviderResolveConfigContext(
        cfg=None, raw_config={"voice": "x"}
    )
    out = prov.resolve_config(ctx)
    assert out == {"voice": "x"}
    out["voice"] = "mutated"
    assert ctx.raw_config["voice"] == "x"  # copy, not the caller's mapping
    assert prov.resolve_config(object()) == {}  # junk ctx -> {}


# ----------------------------------------------------------------- callbacks
def test_callbacks_are_invoked_with_the_right_shapes():
    rec = _Recorder()
    cbs = rec.callbacks()
    event = RV.RealtimeVoiceBridgeEvent(
        direction="server", type="response.audio.delta", response_id="r1"
    )
    tool = RV.RealtimeVoiceToolCallEvent(
        item_id="i1", call_id="c1", name="grep", args={"q": "x"}
    )
    cbs.emit_event(event)
    cbs.emit_audio(b"\x01\x02")
    cbs.emit_tool_call(tool)
    cbs.emit_close("completed")
    cbs.emit_error(RuntimeError("boom"))
    assert rec.events == [event]
    assert rec.events[0].type == "response.audio.delta"
    assert rec.audio == [b"\x01\x02"]
    assert rec.tools == [tool]
    assert rec.tools[0].name == "grep"
    assert rec.closes == ["completed"]
    assert isinstance(rec.errors[0], RuntimeError)


def test_callbacks_without_sinks_never_raise_on_junk():
    cbs = RV.RealtimeVoiceBridgeCallbacks()
    cbs.emit_event(None)
    cbs.emit_audio(object())
    cbs.emit_tool_call(123)
    cbs.emit_close(None)
    cbs.emit_error("not-an-exception")  # coerced to Exception


def test_a_raising_observer_is_swallowed():
    def boom(_value):
        raise RuntimeError("observer down")

    cbs = RV.RealtimeVoiceBridgeCallbacks(
        on_event=boom, on_audio=boom, on_tool_call=boom,
        on_close=boom, on_error=boom,
    )
    # every emit must survive a broken observer
    cbs.emit_event(RV.RealtimeVoiceBridgeEvent(type="response.done"))
    cbs.emit_audio(b"\x00")
    cbs.emit_tool_call(RV.RealtimeVoiceToolCallEvent(call_id="c"))
    cbs.emit_close()
    cbs.emit_error(RuntimeError("x"))


# ------------------------------------------------------------- event mapping
def test_every_mapped_talk_type_is_a_real_talk_event_type():
    assert RV.BRIDGE_EVENT_TO_TALK_EVENT  # non-empty
    for mapped in RV.BRIDGE_EVENT_TO_TALK_EVENT.values():
        assert mapped in TALK_EVENT_TYPES, mapped


def test_talk_event_type_for_maps_known_boundaries():
    assert RV.talk_event_type_for("session.created") == "session.started"
    assert RV.talk_event_type_for("input_audio_buffer.speech_started") == \
        "input.speech_started"
    assert RV.talk_event_type_for("response.audio.delta") == \
        "response.audio.delta"
    assert RV.talk_event_type_for("response.done") == "response.done"
    assert RV.talk_event_type_for("response.function_call_arguments.done") == \
        "tool.call"
    assert RV.talk_event_type_for("conversation.item.created") == "tool.result"
    assert RV.talk_event_type_for("error") == "error"
    # accepts an event object, not just a raw name
    assert RV.talk_event_type_for(
        RV.RealtimeVoiceBridgeEvent(type="response.done")
    ) == "response.done"


def test_talk_event_type_for_returns_none_for_unmapped_or_junk():
    for junk in (None, "", "nope", 123, object(), ["error"], b"error"):
        assert RV.talk_event_type_for(junk) is None


def test_emit_bridge_talk_event_emits_through_a_sequencer():
    seq = TalkEventSequencer(now=lambda: 5.0)
    seq.begin_turn()
    envelope = RV.emit_bridge_talk_event(
        seq, "response.audio.delta", chars=10
    )
    assert envelope is not None
    assert envelope["type"] == "response.audio.delta"
    assert envelope["seq"] == 1
    assert envelope["turn_id"] == "turn-1"
    assert envelope["chars"] == 10


def test_emit_bridge_talk_event_drops_unmapped_without_moving_seq():
    seq = TalkEventSequencer(now=lambda: 5.0)
    assert RV.emit_bridge_talk_event(seq, "conversation.item.delta") is None
    assert RV.emit_bridge_talk_event(seq, None) is None
    assert RV.emit_bridge_talk_event(None, "response.done") is None
    assert seq.snapshot()["seq"] == 0


def test_emit_bridge_talk_event_never_raises_on_junk_fields():
    seq = TalkEventSequencer(now=lambda: 5.0)
    env = RV.emit_bridge_talk_event(
        seq, "error", detail=object(), nested={"a": [1, 2, 3]}
    )
    assert env is not None and env["type"] == "error"
    # a sequencer that throws is swallowed, never propagated
    class BadSeq:
        def emit(self, *a, **k):
            raise RuntimeError("sequencer down")

    assert RV.emit_bridge_talk_event(BadSeq(), "response.done") is None
