# Frontend Theming — Qubit Is Authoritative (2026-08-25)

**Signal:** User: "you fucking retard you created a theme that shouldn't exist, the current theme its made by you and its shit, I revert it now"
Previous session had overwritten `frontend/activity.html` with a plain 9.4K legacy HTML (no gif, no animations) while the user's selected Qubit theme lives in `~/Projects/Repos/voice-dashboard/frontend/` (40M build, `gif-background`, skeleton animations, glass nav, light/dark tokens). Voice bridge was temporarily preferring `bridge/frontend` first for speed over tunnel — user rejected it.

**Rule for all future work:**
- **Do NOT create or restore a plain legacy theme** in `qubit-voice-bridge/frontend/`. The Qubit dashboard in `voice-dashboard` is the single source of truth for visual design.
- `voicebridge/webui.py` must prefer `voice-dashboard/frontend` when it exists:
  ```python
  self._frontend_dir = Path.home() / "Projects/Repos/voice-dashboard/frontend"
  if not (self._frontend_dir / "index.html").exists():
      self._frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
  ```
  The opposite (bridge-first) was tried for tunnel speed and immediately reverted by user — do not reintroduce.
- If you must make pages faster without killing the theme, optimize **inside** `voice-dashboard/frontend/` (externalize the 41MB `data:image/gif;base64` to `/static/Logo/ascii-magic-4.gif`, add loading skeletons) — never replace it with a minimal stub.
- Treat `frontend/` changes as **visual/destructive** even if small — require explicit user direction before touching any `*.html`/`*.css` under `frontend/` or `voice-dashboard/frontend/`. Audit tasks (e.g. opencode audit) must not auto-migrate or overwrite the theme.

**How to tune yourself:** `code ~/Projects/Repos/voice-dashboard` + `code ~/Projects/Repos/openclaw-voice-bridge` — edits in voice-dashboard apply live via `WebUI._frontend_dir` without bridge code changes. Verify with `curl -s http://127.0.0.1:8890/ -o /tmp/_t; wc -c /tmp/_t` (expect ~40M with Qubit theme, not 12K).

**Verification (2026-08-25):** Bridge serves `voice-dashboard/frontend` first, `api/status` = `bridge up, tts: piper`, 93 tests pass. Do not change this without user saying "change the theme".
