# Orb Real Equalizer — design (owner-approved 2026-10-05)

Scope: desktop orb ONLY (dashboard widget untouched).

## Contract
- Green bars = voice, both directions (listening: live mic spectrum; speaking: TTS wav timeline).
- Yellow = thinking (existing motion, no audio to analyse). Orange = idle flat baseline. Red = error.
- Orb never opens the mic; helper is the single audio owner. No new transport, no new deps (numpy FFT in-venv).

## Pipeline
1. **Publish (desktop_helper.py)**: 100ms windows of mono-16kHz audio → numpy rFFT → 12 log-spaced bands 80Hz–8kHz → 0..1 with noise floor. Listening: live capture frames (existing LevelTapMic sites). Speaking: TTS wav FFT'd once at synth time into a band timeline.
2. **Transport**: heartbeat extras `spec_bands`+`spec_ts` (live), `spec_tts`+`spec_tts_ts` (reply timeline). Stale (>1s live, past reply-end) = silence. No audio bytes, no text, no new auth.
3. **Consume (orb/client.py)**: pure parsers + accessor on the already-fetched status/heartbeat payload. Zero extra HTTP.
4. **Render (orb/ember.py)**: bar renderer — 12 log-scaled bars, per-band fast-attack (~100ms) / slow-release (~400ms) smoothing, slow-falling peak-hold caps. Listening/speaking only; thinking/idle/error byte-identical rendering. Reduced-motion = static snapshot.
5. **Sync**: listening ≤1 poll lag; speaking tracks playback position (journaled start vs wav duration); stale eases to flat, never freezes mid-air.

## Tests (TDD, hermetic)
Tones light correct bands; stale flattens; attack/release pinned; timeline tracks; render stats prove bars in voice states + unchanged others; orb tree still opens no mic.

## Go-live
Orb restart only (helper restart for publish side). Eyes-on proof: talk → pumping green bars; reply → bars trace reply; done → orange flat ≤4s.
