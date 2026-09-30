# Qubit Is the Default Worker — Don't Revert to Qubit Primary (2026-08-25)

**Decision:** `hermes.prefer: true` lives in both `voicebridge/config.py:DEFAULTS["hermes"]["prefer"]` and `~/.config/openclaw-voice-bridge/voice.yaml`. `VoiceOrchestrator._dispatch_coding` checks `prefer` at dispatch time: if `HermesBackend.available()` and `prefer` → `self._handle_hermes(final_text)` and `return`. Qubit is only reached when `HermesBackend` is down, `sticky_route` targets `openclaw`, or the task is heavy browser `1:1 pixel-perfect / CAPTCHA bypass`.

**Why:** User said Qubit is smarter on this PC (kimi-k2.6) and explicitly directed "make qubit by default — open claw strictly for the things qubit is not able to do." Previous setup was Qubit-primary with `ask Qubit to ...` opt-in, which required extra speech. `ask_executor: false` prevents the blocking "Qubit or Qubit?" prompt.

**Don't revert.** Patching `_dispatch_coding` to go back to unconditional `self._launch_task` (Qubit) breaks the user's explicit preference. Verify with `grep -n prefer ~/Projects/Repos/openclaw-voice-bridge/voicebridge/config.py` and `grep -n routing.prefer ~/.local/state/openclaw-voice-bridge/events.jsonl`. One-off Qubit jobs: `route to openclaw` (sticky, 8 turns / 10 min) then `clear route`.
