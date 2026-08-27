"""qubit-voice-agent: local push-to-talk voice interface for OpenClaw.

Layers:
  audio/    microphone capture, VAD endpointing, playback + interrupt
  asr/      speech-to-text adapters (faster-whisper active, PersonaPlex pluggable)
  tts/      text-to-speech adapters (piper active, edge/gateway optional)
  intent/   utterance classification + conversation/task/confirmation state
  openclaw/ gateway discovery + WS protocol client (+ CLI fallback)
  safety/   destructive-action policy, allow/deny list, dry-run-first
  orchestrator.py  main loop wiring the layers together
"""

__version__ = "0.1.0"
