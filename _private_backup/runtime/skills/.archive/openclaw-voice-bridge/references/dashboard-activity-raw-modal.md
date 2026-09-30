# Dashboard activity — click summary to see full raw output (2026-08-22)

## What changed
`frontend/activity.html` now treats every activity item with `raw` as expandable:
- Summary line gets `.has-raw` (dashed border, `↗ full text` hint, hover lift).
- Clicking the summary opens a modal (`#raw-modal`) showing the full `raw` text — dark backdrop, 860px card, monospace body, header with `Copy` + `✕`, supports `Esc` / backdrop click.
- Keeps the `<details> raw output (N chars)` fallback under each card.

## Why it matters
`orchestrator.py` caps `raw` at `[:8000]` before pushing to the activity feed (raised from `[:500]` this session). The old 500-char cap made the dashboard `raw output` a stub. The modal now actually shows the real output for both `local-script` and `openclaw` summaries.

## Implementation details
- Uses global `activityItems` index (`start + idx`) so paginated pages open the correct item — not the page-slice index.
- Modal reuses `activityItems[idx].raw` (not a second fetch) — instant, offline.
- CSS: `.activity-summary.has-raw`, `.activity-summary-hint`, `.raw-modal`, `.raw-modal-card`, `.raw-modal-head`, `.raw-modal-body`.
- JS: `openRawModal(idx)` / `closeRawModal()` + `feed.addEventListener('click', ...closest('.activity-summary.has-raw'))`.

## Pitfall
If you truncate `raw` upstream at `[:500]`, the modal is useless. Keep the feed `raw` at `[:8000]` (or `[:4000]` minimum) so the click actually reveals the full output.
