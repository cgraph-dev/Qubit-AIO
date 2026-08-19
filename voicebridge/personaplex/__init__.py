"""PersonaPlex full-duplex voice client.

PersonaPlex (nvidia/personaplex-7b-v1) is NVIDIA's Moshi-based realtime
speech-to-speech model. The server it ships (`python -m moshi.server`) exposes
one realtime WebSocket endpoint:

    GET /api/chat?voice_prompt=<file>&text_prompt=<persona>&seed=-1

Binary frame protocol (byte 0 = kind):
    kind 1 (0x01)  client -> server : Opus-encoded microphone audio
    kind 1 (0x01)  server -> client : Opus-encoded assistant audio
    kind 2 (0x02)  server -> client : UTF-8 assistant transcript deltas

This package implements a matching client (sphn Opus streaming, same as the
server) plus the higher-level duplex lane used by the orchestrator.
"""
from .client import DuplexClient, DuplexError, PersonaPlexUnavailable
from .duplex import PersonaPlexDuplex

__all__ = ["DuplexClient", "DuplexError", "PersonaPlexUnavailable", "PersonaPlexDuplex"]
