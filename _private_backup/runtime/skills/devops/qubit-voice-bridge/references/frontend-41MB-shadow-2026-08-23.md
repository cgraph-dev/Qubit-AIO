# Frontend shadow — 41MB pages served from `voice-dashboard` (2026-08-23)

**Symptom:** `curl /`, `/activity`, `/reports` each returned `len 41M` (`41840434 / 41594112 / 41583295` bytes) though `~/Projects/Repos/openclaw-voice-bridge/frontend/*.html` are 5-13 KB. Over tunnel on mobile the pages were brutally slow / failed.

**Root cause:** `voicebridge/webui.py` preferred `~/Projects/Repos/voice-dashboard/frontend` if it existed; that dir is a React build with `thinking-orb.js 209KB` + bundled assets making index 41M. Bridge should serve its own small voice frontend, not the big CGraph dashboard.

**Fix:** Reversed preference in `webui.WebUI.__init__`: try `Path(.../openclaw-voice-bridge/frontend)` first (voice pages ~12KB, fast), fall back to `voice-dashboard` only if missing. Full `voicebridge/config.py` change: `self._frontend_dir` logic swapped, comment added about tunnel speed.

**Verified live (after `systemctl --user restart`):**
`/ 12859, /activity 5662, /reports 8311`, all APIs `200`. `93 tests pass`.

**Also fixed:** duplicate shared-ledger block in `webui.do_GET /api/reports` appended `shared.recent()` twice (once with `category = _cat(it)`, once with `category = "shared-..."`), doubling report items. Kept the first block, removed the second.
