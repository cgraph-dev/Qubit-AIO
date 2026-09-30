# Dashboard Logs Placement — Only Inside Settings (Aug 2026)

## User preference (hard)
Logs (`Live logs` streaming `GET /api/logs`) are **only** visible inside **Settings** on both pages
(Talk `/` and Activity `/activity`), and **never** as a standalone tab or panel.
- Talk: gear in the top bar (`#gearlink` → `#settings`) already toggles `.is-open`; place logs directly over the
  Connection settings box inside that `#settings` section.
- Activity: the same top-bar gear toggles the new `#settings` panel; the Feed vs. Logs tab pattern is deprecated
  and causes the "I can only open Settings on Talk" bug when kept separate.

## Implementation shape to preserve
- `ActivityFeed` ring (200) already preserves full `raw`; dashboard drill-down (`GET /api/item/:id`) is separate.
- For the dashboard: `activity.html` keeps a Feed-only main view; Settings is a hidden `<section id="settings" hidden>`
  toggled via `document.querySelectorAll('.glass-button.settings-button')` → `setSettingsOpen(!open)` where
  `setSettingsOpen` does `settingsPanel.hidden = !open; settingsPanel.classList.toggle('is-open', open);` and
  persists to `localStorage` (`vdb_settings_open`). Talk's handler does `panel.classList.toggle('is-open')` + `gear.aria-expanded`.
- Keep Talk and Activity settings behavior symmetric; don't reintroduce an Activity-only tab that diverges.

## Why class + hidden both matter
Some builds render `settings-panel` with `[hidden]{display:none!important}` while others use `.is-open{display:block}`.
Toggling both covers either CSS without relying on a single selector.
