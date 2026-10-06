"""Realtime-voice provider abstraction (speech-to-speech foundation).

Semantics ported from the OpenClaw reference (``src/talk/provider-types.ts`` +
``provider-registry.ts`` + ``provider-resolver.ts``): a realtime voice provider
is a named capability that advertises what it can do and can open a *bridge* --
a live duplex session that accepts mic audio, text, and tool results, and
pushes audio/transcripts/tool calls back through callbacks.

This module is the pure foundation only: types, a registry, an event mapping
into :mod:`voicebridge.voice_edit.talk_events`, and a no-op fallback. It does no
I/O, opens no socket, reads no secret, and never raises on junk, so callers can
wire the abstraction (and the orb/talk-event stream) before a real provider is
configured.

Ported semantics, not the TS verbatim:

* ``RealtimeVoiceProviderId`` is an open-ended id (upstream ``string``); it is a
  ``str`` subclass so it keys any dict while still signalling intent.
* Capabilities are data (audio in/out, tools, barge-in, audio formats).
* The registry is order-deterministic and fail-soft: unknown or malformed ids
  resolve to ``None``; a duplicate registration replaces in place.
* The bridge event vocabulary maps onto ``TALK_EVENT_TYPES`` where a boundary
  exists (see ``BRIDGE_EVENT_TO_TALK_EVENT``); unmapped events are dropped.

``NullRealtimeVoiceProvider`` is the local fallback when nothing is configured:
its bridge accepts every send, is never active, and emits nothing -- so a caller
can hold a real bridge object on a machine with no provider.

Pure: no I/O, no network, no secrets. Never raises.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

__all__ = [
    "BRIDGE_EVENT_TO_TALK_EVENT",
    "G711_ULAW_8000",
    "NULL_PROVIDER_CAPABILITIES",
    "NULL_PROVIDER_ID",
    "NullRealtimeVoiceBridge",
    "NullRealtimeVoiceProvider",
    "PCM16_16000_IN",
    "PCM16_24000_OUT",
    "RealtimeVoiceAudioFormat",
    "RealtimeVoiceBridge",
    "RealtimeVoiceBridgeCallbacks",
    "RealtimeVoiceBridgeCreateRequest",
    "RealtimeVoiceBridgeEvent",
    "RealtimeVoiceProvider",
    "RealtimeVoiceProviderCapabilities",
    "RealtimeVoiceProviderId",
    "RealtimeVoiceProviderRegistry",
    "RealtimeVoiceProviderResolveConfigContext",
    "RealtimeVoiceToolCallEvent",
    "RealtimeVoiceToolResultOptions",
    "build_default_registry",
    "emit_bridge_talk_event",
    "talk_event_type_for",
]


# ---------------------------------------------------------------------------
# Ids
# ---------------------------------------------------------------------------
class RealtimeVoiceProviderId(str):
    """Canonical realtime-voice provider id.

    A ``str`` subclass (upstream models the id as ``string``): an instance
    compares and hashes equal to its plain string form, so it keys any registry,
    while the type marks the intent and the class carries the well-known ids.
    """

    #: The no-op fallback provider's id.
    NULL = "null"

    @classmethod
    def normalize(cls, value: Any) -> Optional["RealtimeVoiceProviderId"]:
        """Trim/lowercase ``value`` into an id, or ``None`` for junk.

        Never raises: non-strings, blanks, and ``None`` all resolve to ``None``.
        """
        try:
            if isinstance(value, RealtimeVoiceProviderId):
                text = str(value)
            elif isinstance(value, str):
                text = value
            else:
                return None
            text = text.strip().lower()
            return cls(text) if text else None
        except Exception:
            return None


#: The id of the local no-op fallback.
NULL_PROVIDER_ID = RealtimeVoiceProviderId("null")


# ---------------------------------------------------------------------------
# Audio formats
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RealtimeVoiceAudioFormat:
    """One PCM/telephony audio encoding a bridge can carry.

    ``sample_rate_hz``/``channels`` are descriptive constants; the abstraction
    never resamples -- a provider either accepts the format or does not.
    """

    encoding: str = "pcm16"
    sample_rate_hz: int = 16000
    channels: int = 1

    def to_dict(self) -> Dict[str, Any]:
        """Plain, JSON-safe mapping (always safe)."""
        return {
            "encoding": self.encoding,
            "sample_rate_hz": self.sample_rate_hz,
            "channels": self.channels,
        }


#: Mic input: 16-bit PCM mono at 16 kHz (the wake/STT chain's rate).
PCM16_16000_IN = RealtimeVoiceAudioFormat("pcm16", 16000, 1)
#: Reply output: 16-bit PCM mono at 24 kHz (the provider's output rate).
PCM16_24000_OUT = RealtimeVoiceAudioFormat("pcm16", 24000, 1)
#: Telephony output some providers use (upstream's g711 lane).
G711_ULAW_8000 = RealtimeVoiceAudioFormat("g711_ulaw", 8000, 1)


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RealtimeVoiceProviderCapabilities:
    """What a provider can do, as data (never a guess)."""

    supports_audio_in: bool = False
    supports_audio_out: bool = False
    supports_tools: bool = False
    supports_barge_in: bool = False
    input_audio_formats: Tuple[RealtimeVoiceAudioFormat, ...] = ()
    output_audio_formats: Tuple[RealtimeVoiceAudioFormat, ...] = ()
    supports_video_frames: bool = False
    supports_session_resumption: bool = False
    transports: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        """Plain, JSON-safe mapping (always safe)."""
        return {
            "supports_audio_in": self.supports_audio_in,
            "supports_audio_out": self.supports_audio_out,
            "supports_tools": self.supports_tools,
            "supports_barge_in": self.supports_barge_in,
            "input_audio_formats": [
                f.to_dict() for f in self.input_audio_formats
            ],
            "output_audio_formats": [
                f.to_dict() for f in self.output_audio_formats
            ],
            "supports_video_frames": self.supports_video_frames,
            "supports_session_resumption": self.supports_session_resumption,
            "transports": list(self.transports),
        }


#: The honest capability set of the no-op fallback: nothing is supported.
NULL_PROVIDER_CAPABILITIES = RealtimeVoiceProviderCapabilities()


# ---------------------------------------------------------------------------
# Events + callbacks
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RealtimeVoiceBridgeEvent:
    """One provider-side bridge event (lifecycle/transport notice).

    Mirrors upstream ``RealtimeVoiceBridgeEvent``: ``direction`` is
    ``"client"``/``"server"``, ``type`` is the provider's raw event name (mapped
    onto a talk event by :func:`talk_event_type_for`).
    """

    direction: str = "server"
    type: str = ""
    detail: str = ""
    item_id: str = ""
    response_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Plain, JSON-safe mapping (always safe)."""
        return {
            "direction": self.direction,
            "type": self.type,
            "detail": self.detail,
            "item_id": self.item_id,
            "response_id": self.response_id,
        }


@dataclass(frozen=True)
class RealtimeVoiceToolCallEvent:
    """A provider-initiated function/tool call request."""

    item_id: str = ""
    call_id: str = ""
    name: str = ""
    args: Any = None

    def to_dict(self) -> Dict[str, Any]:
        """Plain, JSON-safe mapping (always safe)."""
        return {
            "item_id": self.item_id,
            "call_id": self.call_id,
            "name": self.name,
            "args": self.args,
        }


@dataclass(frozen=True)
class RealtimeVoiceToolResultOptions:
    """Options for :meth:`RealtimeVoiceBridge.send_tool_result`."""

    #: Do not ask the provider to generate a new assistant turn afterward;
    #: another channel already delivered the user-visible answer.
    suppress_response: bool = False
    #: The provider should continue the turn after the tool result.
    will_continue: bool = False


@dataclass
class RealtimeVoiceBridgeCallbacks:
    """Callbacks a bridge pushes provider output through.

    ``on_event``/``on_audio``/``on_tool_call`` are the primary sinks;
    ``on_close``/``on_error`` carry lifecycle. Every ``emit_*`` helper is
    fail-soft: a raising observer (or a missing one) never breaks delivery.
    """

    on_event: Optional[Callable[[RealtimeVoiceBridgeEvent], None]] = None
    on_audio: Optional[Callable[[bytes], None]] = None
    on_tool_call: Optional[Callable[[RealtimeVoiceToolCallEvent], None]] = None
    on_close: Optional[Callable[[str], None]] = None
    on_error: Optional[Callable[[Exception], None]] = None

    def emit_event(self, event: Any) -> None:
        """Deliver one event; never raises."""
        self._safe(self.on_event, event)

    def emit_audio(self, audio: Any) -> None:
        """Deliver one audio chunk; never raises."""
        self._safe(self.on_audio, audio)

    def emit_tool_call(self, call: Any) -> None:
        """Deliver one tool call; never raises."""
        self._safe(self.on_tool_call, call)

    def emit_close(self, reason: Any = "completed") -> None:
        """Deliver a close notice; never raises."""
        self._safe(self.on_close, reason)

    def emit_error(self, error: Any) -> None:
        """Deliver an error notice; never raises."""
        exc = error if isinstance(error, Exception) else Exception(str(error))
        self._safe(self.on_error, exc)

    @staticmethod
    def _safe(callback: Optional[Callable[[Any], None]], value: Any) -> None:
        if callback is None:
            return
        try:
            callback(value)
        except Exception:
            # An observer must never be able to break the bridge.
            pass


# ---------------------------------------------------------------------------
# Bridge / provider contracts
# ---------------------------------------------------------------------------
class RealtimeVoiceBridge(ABC):
    """A live duplex realtime-voice session.

    Concrete bridges own the transport; this contract is transport-agnostic.
    """

    #: Whether :meth:`send_tool_result` may suppress/continue the turn.
    supports_tool_result_continuation: bool = False

    @abstractmethod
    def send_audio(self, audio: bytes) -> None:
        """Submit mic audio (the agreed input format) to the provider."""

    @abstractmethod
    def send_text(self, text: str) -> None:
        """Submit a text user message to the provider."""

    @abstractmethod
    def send_tool_result(
        self,
        call_id: str,
        payload: Any = None,
        options: Optional[RealtimeVoiceToolResultOptions] = None,
    ) -> None:
        """Return a tool call's result to the provider."""

    @abstractmethod
    def close(self) -> None:
        """Close the bridge (idempotent)."""

    @abstractmethod
    def is_active(self) -> bool:
        """True while the session is live and accepting/sending."""


@dataclass
class RealtimeVoiceBridgeCreateRequest:
    """Everything a provider needs to open one bridge (no secrets inline)."""

    provider_config: Dict[str, Any] = field(default_factory=dict)
    audio_format: Optional[RealtimeVoiceAudioFormat] = None
    instructions: str = ""
    auto_respond_to_audio: bool = True
    interrupt_response_on_input_audio: bool = True
    tools: Tuple[Dict[str, Any], ...] = ()
    #: Optional host config handle; opaque here so this module stays pure.
    cfg: Optional[Any] = None


@dataclass(frozen=True)
class RealtimeVoiceProviderResolveConfigContext:
    """Inputs to :meth:`RealtimeVoiceProvider.resolve_config`."""

    cfg: Optional[Any] = None
    raw_config: Mapping[str, Any] = field(default_factory=dict)


class RealtimeVoiceProvider(ABC):
    """A realtime-voice provider: identity, capabilities, bridge factory."""

    @property
    @abstractmethod
    def id(self) -> RealtimeVoiceProviderId:
        """Canonical provider id."""

    @abstractmethod
    def capabilities(self) -> RealtimeVoiceProviderCapabilities:
        """Advertised capabilities (data, never a guess)."""

    @abstractmethod
    def resolve_config(
        self, ctx: RealtimeVoiceProviderResolveConfigContext
    ) -> Dict[str, Any]:
        """Normalize/validate provider config; return a plain mapping."""

    @abstractmethod
    def create_bridge(
        self,
        request: RealtimeVoiceBridgeCreateRequest,
        callbacks: RealtimeVoiceBridgeCallbacks,
    ) -> RealtimeVoiceBridge:
        """Open a live bridge, pushing output through ``callbacks``."""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
class RealtimeVoiceProviderRegistry:
    """Order-deterministic, fail-soft provider registry.

    Registration order is preserved: re-registering an existing id replaces the
    provider *in place* (its position does not move). No method raises; unknown,
    malformed, or ``None`` ids resolve to ``None``.
    """

    def __init__(self) -> None:
        self._providers: Dict[str, RealtimeVoiceProvider] = {}

    @staticmethod
    def _id_of(provider: Any) -> Optional[RealtimeVoiceProviderId]:
        """Read a provider's id attribute/zero-arg callable; never raises."""
        try:
            raw = getattr(provider, "id", None)
        except Exception:
            return None
        if callable(raw) and not isinstance(raw, (str, RealtimeVoiceProviderId)):
            try:
                raw = raw()
            except Exception:
                return None
        return RealtimeVoiceProviderId.normalize(raw)

    def register(self, provider: Any) -> bool:
        """Add/replace ``provider``. Returns whether it was stored.

        Never raises; a provider with no usable id is rejected.
        """
        try:
            normalized = self._id_of(provider)
            if normalized is None:
                return False
            self._providers[str(normalized)] = provider
            return True
        except Exception:
            return False

    def get(self, provider_id: Any) -> Optional[RealtimeVoiceProvider]:
        """Resolve a provider by id/alias; ``None`` when unknown. Never raises."""
        try:
            normalized = RealtimeVoiceProviderId.normalize(provider_id)
            if normalized is None:
                return None
            return self._providers.get(str(normalized))
        except Exception:
            return None

    def list(self) -> Tuple[RealtimeVoiceProvider, ...]:
        """Providers in registration order (a copy). Never raises."""
        try:
            return tuple(self._providers.values())
        except Exception:
            return ()

    def ids(self) -> Tuple[str, ...]:
        """Registered keys in registration order. Never raises."""
        try:
            return tuple(self._providers.keys())
        except Exception:
            return ()

    def __contains__(self, provider_id: Any) -> bool:
        return self.get(provider_id) is not None

    def __len__(self) -> int:
        try:
            return len(self._providers)
        except Exception:
            return 0


# ---------------------------------------------------------------------------
# No-op fallback
# ---------------------------------------------------------------------------
class NullRealtimeVoiceBridge(RealtimeVoiceBridge):
    """A bridge that accepts every send, is never active, and emits nothing.

    The local fallback when no realtime provider is configured: callers can
    wire the abstraction and the talk-event stream without a live provider.
    """

    supports_tool_result_continuation = False

    def __init__(
        self,
        request: Optional[RealtimeVoiceBridgeCreateRequest] = None,
        callbacks: Optional[RealtimeVoiceBridgeCallbacks] = None,
    ) -> None:
        self.request = request
        self.callbacks = callbacks
        self._closed = False

    def send_audio(self, audio: bytes) -> None:
        """Accepted and discarded (no-op)."""

    def send_text(self, text: str) -> None:
        """Accepted and discarded (no-op)."""

    def send_tool_result(
        self,
        call_id: str,
        payload: Any = None,
        options: Optional[RealtimeVoiceToolResultOptions] = None,
    ) -> None:
        """Accepted and discarded (no-op)."""

    def close(self) -> None:
        """Mark closed (idempotent, no-op otherwise)."""
        self._closed = True

    def is_active(self) -> bool:
        """Always ``False``: the null bridge never opens a session."""
        return False


class NullRealtimeVoiceProvider(RealtimeVoiceProvider):
    """The local no-op provider (id ``"null"``); honest empty capabilities."""

    @property
    def id(self) -> RealtimeVoiceProviderId:
        return NULL_PROVIDER_ID

    def capabilities(self) -> RealtimeVoiceProviderCapabilities:
        return NULL_PROVIDER_CAPABILITIES

    def resolve_config(
        self, ctx: RealtimeVoiceProviderResolveConfigContext
    ) -> Dict[str, Any]:
        """Return the raw config unchanged (there is nothing to normalize)."""
        try:
            return dict(ctx.raw_config)
        except Exception:
            return {}

    def create_bridge(
        self,
        request: RealtimeVoiceBridgeCreateRequest,
        callbacks: RealtimeVoiceBridgeCallbacks,
    ) -> RealtimeVoiceBridge:
        return NullRealtimeVoiceBridge(request, callbacks)


def build_default_registry() -> RealtimeVoiceProviderRegistry:
    """A fresh registry with only the null fallback registered."""
    registry = RealtimeVoiceProviderRegistry()
    registry.register(NullRealtimeVoiceProvider())
    return registry


# ---------------------------------------------------------------------------
# Bridge events -> talk events
# ---------------------------------------------------------------------------
#: Provider bridge event name -> the ``talk_events.TALK_EVENT_TYPES`` boundary
#: it represents. Documented, read-only mapping; an absent key means the event
#: has no talk boundary and is dropped (transcript deltas, JSON noise, ...).
#:
#:   session.*            -> session.started / session.state / session.ended
#:   input_audio_buffer.* -> input.speech_started / input.speech_stopped
#:   conversation.item.*  -> transcript.partial / transcript.final
#:   response.created     -> response.started
#:   response.audio.delta -> response.audio.delta
#:   response.done        -> response.done
#:   response.*.done      -> tool.call / tool.result
#:   playback.*           -> playback.started / playback.stopped
#:   interrupt/cancelled  -> interrupt
#:   error                -> error
BRIDGE_EVENT_TO_TALK_EVENT: Mapping[str, str] = MappingProxyType({
    "session.created": "session.started",
    "session.started": "session.started",
    "session.ready": "session.started",
    "session.updated": "session.state",
    "session.ended": "session.ended",
    "session.closed": "session.ended",
    "input_audio_buffer.speech_started": "input.speech_started",
    "input_audio_buffer.speech_stopped": "input.speech_stopped",
    "input_audio_buffer.committed": "input.speech_stopped",
    "conversation.item.input_audio_transcription.delta": "transcript.partial",
    "conversation.item.input_audio_transcription.partial": "transcript.partial",
    "conversation.item.input_audio_transcription.completed":
        "transcript.final",
    "conversation.item.input_audio_transcription.final": "transcript.final",
    "response.created": "response.started",
    "response.started": "response.started",
    "response.audio.delta": "response.audio.delta",
    "response.output_audio.delta": "response.audio.delta",
    "response.done": "response.done",
    "response.completed": "response.done",
    "response.function_call_arguments.done": "tool.call",
    "response.output_item.done": "tool.call",
    "conversation.item.created": "tool.result",
    "response.audio_transcript.done": "transcript.final",
    "playback.started": "playback.started",
    "playback.stopped": "playback.stopped",
    "interrupt": "interrupt",
    "response.cancelled": "interrupt",
    "error": "error",
})


def talk_event_type_for(bridge_event_type: Any) -> Optional[str]:
    """Map a bridge event (name or :class:`RealtimeVoiceBridgeEvent`) to a
    ``TALK_EVENT_TYPES`` member, or ``None`` when there is no boundary.

    Accepts a raw string or an object with a ``type`` attribute. Never raises.
    """
    try:
        raw = getattr(bridge_event_type, "type", bridge_event_type)
        if not isinstance(raw, str):
            return None
        return BRIDGE_EVENT_TO_TALK_EVENT.get(raw)
    except Exception:
        return None


def emit_bridge_talk_event(
    sequencer: Any,
    bridge_event_type: Any,
    *,
    journal: bool = False,
    **fields: Any,
) -> Optional[Dict[str, Any]]:
    """Emit the talk event for ``bridge_event_type`` through ``sequencer``.

    Returns the emitted envelope, or ``None`` when there is no mapped boundary,
    no sequencer, or anything goes wrong (never raises -- a bridge event must
    never be able to break the ordered stream).
    """
    try:
        talk_type = talk_event_type_for(bridge_event_type)
        if talk_type is None or sequencer is None:
            return None
        return sequencer.emit(talk_type, journal=journal, **fields)
    except Exception:
        return None
