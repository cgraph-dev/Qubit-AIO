# Activity badges — human meanings + the 41 MB page incident

The `/activity` page used cryptic raw kinds (`summary`, `routing`, `done`, `user`) with a duplicate `const TOKEN` declaration and a missing `Authorization` header on `GET /api/activity`. Fixes below are the correct target shape for any activity-style feed.

## Badge map (the one the user asked for)

Raw event kind → human badge shown to the user:

| Raw kind | Human badge | Meaning |
|---|---|---|
| `user` / `asked` | You said | Your transcribed speech or typed message |
| `routing` / `decided` | Routed to… + destination | Which lane handled it (`openclaw` / `qubit` / `browser` / `local` / `conversation`) |
| `summary` / `done` / `answered` | Answered | Reply actually spoken back (summary, ≤40 words) |
| `status` / `running` / `interrupt` / `hotkey` | Working… | Task dispatched, still running |
| `raw_report` / `report` | Report saved | Full detailed report written to `<state_dir>/reports/*.md` |
| `fail` / `error` | Failed | Error or no speech detected |

The legend at the top of `/activity` is now required — never ship badges without it.

Styles: `b-asked` (`#1f6feb22`), `b-decided` (`#d29922`), `b-answered` (`#3fb950`), `b-running` (`#8b949e`), `b-report` (`#a371f7`), `b-failed` (`#f85149`). Filter `select#kindFilter` maps to these human kinds.

## The 41 MB page incident (2026-08-23)

`voice-dashboard/frontend/index.html` grew to **41 MB** by inlining a GIF as `data:image/gif;base64,…` (40 MB of raw GIF). Measured live: `GET /` 41 MB, `GET /activity` 41 MB — brutal over tunnel. Fix was *not* to switch fronts: keep the rich React frontend (it already has the gif as a file under `/static/Logo/ascii-magic-4.gif`), just stop inlining and send correct `Content-Type` for `.gif/.png/.css/.js` so the browser caches.

Rule: **never inline binary assets as `data:` URIs in HTML for the voice bridge** — serve as `/static/<path>` with proper MIME (`references/voice-dashboard-webui.md` §4 has the exact header branch).

## Live shape after the fix

Verified with the bridge restarted:

```
GET /              200 html len ~290 KB  (React, not 41 MB)
GET /activity      200 html   (with legend + animations + shimmer skeletons)
GET /reports       200 html
GET /api/activity?limit=3   200 json
GET /api/reports?limit=3&days=7  200 json
GET /api/logs?limit=3       200 json
```

The web UI layer (`voicebridge/webui.py`) is stdlib `http.server`, zero-deps — correctness is about bay-level headers/mime, not framework magic.
