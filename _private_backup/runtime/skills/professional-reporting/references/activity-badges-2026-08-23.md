# Activity activity.html rebuild — keep it small

The bridge's Activity page is **9.5 KB** with legend/badges, not 40 MB. The dashboard install (`~/Projects/Repos/voice-dashboard/frontend`) holds `index.html 40 MB` — it force-bundles deps incorrectly. Symptoms: `/activity` serves `41846736 B`, though the bridge's `frontend/activity.html` is `9555 B`. Don't increase bundle size to "add a gif"; add the gif as an `<img>` referencing a file in `frontend/` (or CDN) instead of stuffing `data:image/gif;base64` into the HTML.

Rebuild pattern (2026-08-23): legend `You said / Routed to… / Answered / Working… / Report saved / Failed`, filter `all / You said / Answered / Routed`, `badge-in 0.18s` + `bg-drift` gradient — verified `9555 B`, activity feed live (blocking on `request: ...` now shows `task ok` and `in`).
