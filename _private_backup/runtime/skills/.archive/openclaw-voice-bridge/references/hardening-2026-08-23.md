# Hardening 2026-08-23 — router/worker, never-crash, 1:1 mirroring

## Router = thin, workers = full toolkit
Voice bridge must never do work itself. `VoiceOrchestrator._launch_task()` appends a nudge that makes Qubit/Qubit the WORKER. Router only classifies and dispatches. Nudge text (keep verbatim):

> You are the WORKER (Qubit/Qubit). The router is ONLY a router — you own the task: use your full toolkit to deliver it end-to-end.
> Extensions + tools you MUST use freely (never ask permission):
> • terminal — full PC shell: run any command, install deps, launch apps, edit files, check repos, system control.
> • browser (tool: browser) — full Chromium automation: navigate, click, type, screenshot, copy a site 1:1, extract HTML/CSS, analyze forums/discussions, fill forms, handle logins. For pixel-perfect 1:1 copies, open target URL, use `browser open/snapshot/screenshot`, copy DOM + computed styles, reconstruct faithfully.
> • vision — analyze any screenshot/image you captured, read text, verify layout matches source.
> • web search (perplexity/ddg) — research, forum threads, docs, release notes; keep excerpts verbatim in report.
> • files/repos/delegation/VS Code (`code` CLI) + all other extensions/skills.
> For personal web tasks prefer browser profile="user" (real sessions/cookies/payments); profile="openclaw" only for isolated.
> Screenshots OPTIONAL — only for verification.
> Always two parts: 1) FULL detailed report (tool outputs, screenshots refs, forum excerpts, terminal outputs) 2) last paragraph = 40-word spoken summary.
> Captcha/2FA → stop and tell user exactly what to do on screen.

Location: `voicebridge/orchestrator.py` `_launch_task()` `skills_nudge` block.

## 1:1 site copy + forum analysis pattern (for worker)
1. `browser open <url>` → `browser snapshot` (accessibility tree) + `browser screenshot` (pixel truth).
2. Copy DOM: `browser eval` to extract `document.documentElement.outerHTML` and computed styles for key containers.
3. Reconstruct in local file / canvas / report with same layout, colors, fonts; verify with second screenshot + vision compare.
4. Forums/discussions: same browser flow + `perplexity/ddg` search for thread, keep excerpts verbatim; use vision to read screenshots when HTML is JS-heavy.
5. All extensions already enabled: `tools.profile: full`, plugins `browser/canvas/perplexity` enabled, 32 skills ready (`browser-automation`, `coding-agent`, `tmux`, `canvas`). No install needed — just invoke.

## Never-crash checklist (this box: Ubuntu 24.04, 8GB VRAM, Scarlett 2i2)

| Failure | Fix | File:line |
|---|---|---|
| `AttributeError: 'W' object has no attribute 'feed'` on `/api/reports` | `WebUIHandler.app` returns `self.server.app`, not `handler.app`. Set BOTH: `handler.app = self` + `self._server.app = self` | `voicebridge/webui.py:871-876` |
| Gateway `keepalive ping timeout; no close frame received` → WS drop, route offline | `websockets.connect(..., ping_interval=20, ping_timeout=20)` | `voicebridge/openclaw/gateway_ws.py:83` |
| Watchdog only retried when route != gateway → stale `gateway` with dead `connected` stayed dead | `if route != gateway or (gateway and not gateway.connected): retry` | `voicebridge/orchestrator.py:404` |
| Systemd `Restart=on-failure` left clean exits dead | `Restart=always`, `RestartSec=2`, `[Unit] StartLimitIntervalSec/StartLimitBurst` | `~/.config/systemd/user/openclaw-voice-bridge.service` |
| Hotkey `xinput test-xi2 --root` loop dies → RCtrl dead forever | Mark `_xinput_dead`, add `ensure_alive()` lazy-restart, call it at top of `_run_hotkey_loop` | `voicebridge/hotkeys.py:119-150`, `orchestrator.py:2585` |\n| VAD false fallback on missing `webrtcvad` | Default `vad.backend: silero` (neural, `silero_vad_v6.onnx` preferred, v5 fallback). `webrtcvad` is optional. | `voicebridge/config.py`, `voice.yaml` |\n| ASR short clips marking `chars: 3-5` as filler | `parakeet CPU asr_ms 56-82ms` on `gain 8.0 / rms_gate 0.0018` is mic/VAD, not LLM quality — `voice-profiles/voice.yaml` + raising `vad.rms_gate` or holding PTT 0.5s longer fixes it; don't flip `tts.provider` for STT complaints | `events.jsonl: asr.transcribe` |
| Interrupt blocking HTTP | `_interrupt_task()` speaks "Paused. Go ahead." on daemon thread, never block `/api/interrupt` | `orchestrator.py:1688` |
| `psutil/websocket` import errors | They ARE in venv (`.venv` symlinks to `/usr/bin/python3.12`, system site-packages via `psutil 7.2.2`, `websocket-client`, `ddgs`). `pip` binary missing is expected (uv venv). Verify with `.venv/bin/python -c` | — |

## Verification after any bridge change
```bash
python3 -m py_compile voicebridge/orchestrator.py voicebridge/webui.py voicebridge/hotkeys.py voicebridge/openclaw/gateway_ws.py
systemctl --user daemon-reload && systemctl --user restart qubit-voice-bridge
sleep 3; curl -s http://127.0.0.1:8890/api/status | python3 -m json.tool
curl -s http://127.0.0.1:8890/api/reports?limit=1 | python3 -m json.tool | head
journalctl --user -u qubit-voice-bridge --no-pager -n 20 | tail
```
Expected: `bridge: up, route: gateway, busy: false`, no `AttributeError`, no `reader.drop`.
