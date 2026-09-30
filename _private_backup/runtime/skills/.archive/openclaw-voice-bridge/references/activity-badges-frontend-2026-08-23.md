# Activity badges redesign + frontend performance — Aug 23 2026

## Badges: from technical `kind` to human meaning
Old badges were raw `kind` values (`user`, `routing`, `summary`, `status`, `raw_report`, `fail`) — user: "i dont understand what are they fore, make it more professional."

New human badges (in `frontend/activity.html`):
- **You said** (`b-asked`, blue `#58a6ff`) — `kind:user/asked` = transcribed speech or typed message.
- **Routed to…** (`b-decided`, amber `#d29922`) — `kind:routing/decided` + `route/detail` destination shown ("Routed to openclaw/qubit/browser").
- **Answered** (`b-answered`, green `#3fb950`) — `kind:summary/done` = reply spoken back (≤40w summary).
- **Working…** (`b-running`, muted) — `kind:status/running/interrupt/hotkey` = task dispatched, still running.
- **Report saved** (`b-report`, purple `#bc8cff`) — `kind:raw_report` = full detailed markdown on disk.
- **Failed** (`b-failed`, red) — `kind:fail/error`.

`humanBadge(it)` maps old→new; `badgeClass()` picks color. Filter `<select>` now labels by human terms (`all / You said / Answered / Routed / Working / Reports / Failed`). Legend block at top explains each badge; subtle `badge-in 0.18s` + `bg-drift 18s` gradient animation (no 41MB inline GIF — see below).

## Frontend performance pitfall
`voice-dashboard/frontend` React build is **41 MB** (inline base64 GIF). `voicebridge/webui.py` previously preferred it:
```py
dashboard_frontend if ... else bridge_frontend   # chose 41 MB
```
Fix: prefer `bridge/frontend` (12 KB) first:
```py
bridge_frontend if (bridge_frontend/"index.html").exists() else dashboard_frontend
```
Verified live: `/ 12859 B`, `/activity 9555 B`, `/reports 8311 B`, all `200` on port 8890. The dashboard theme/brand (Qubit logo, view-transition) stays in `voice-dashboard/` for Vercel; the bridge serves the small copy for the tunnel.

## STT↔TTS confusion from user
User said "voice to text works like shit ... add open whisper as local model" but their real pain was TTS slowness (OpenAI 1.04s vs Piper 0.08s) — not STT quality. Reverting TTS to `tts.provider: piper` restored snappy feel; tuning STT (`rms_gate`, fallback) still helps empty captures. When user requests a voice speed change, clarify STT vs TTS before swapping engines.

## Related files
- `frontend/activity.html` — badge colors, legend, animations
- `frontend/index.html` / `frontend/reports.html` — same shell
- `voicebridge/webui.py:_frontend_dir` — precedence logic
- `references/stt-vad-fallback-2026-08-23.md` — the STT side of the same session
