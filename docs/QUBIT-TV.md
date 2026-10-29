# QubitTV — provenance, scope, and the web-vs-native boundary

**Status:** provenance and scope note. **Not a completion claim.** It records the
license and upstream origin of the QubitTV integration, what the desktop port
implements, and what remains unavailable. It does not assert live playback or
DRM support.

## What was adapted

Qubit has two QubitTV surfaces, both adapted from
[`asimons81/hermestv`](https://github.com/asimons81/hermestv):

1. **Qubit Desktop (native).** A Qubit Desktop plugin and Electron
   `WebContentsView` host. The plugin declares `capabilities: ['media.player']`
   and renders the host-owned `ctx.media.Surface` inside a right-docked
   workspace. It ports the upstream player manager, navigation policy, context
   tokens, media-job worker, renderer contracts and the seven session-scoped
   media tools. User-facing copy is QubitTV; IPC channels are `qubit:player:*`.
2. **Web dashboard launcher (fallback).** The `/qubit-tv` page presents the same
   service catalogue as browser-window links and an embedded YouTube URL/ID
   player. It is not the native player and does not claim native playback.

Service catalogue (both surfaces): YouTube, YouTube TV, Netflix, Prime Video,
Disney+, Hulu, HBO Max, Apple TV+, Peacock and Paramount+.

## Provenance and license

- Upstream repository: `https://github.com/asimons81/hermestv`
- Pinned commit: `fbd445b381ff5f371bf46bdc96a7d79362d9510f` (2026-10-25)
- License: **MIT**, `Copyright (c) 2025 Nous Research` — full text in the
  upstream `LICENSE`, reproduced with the attribution in
  `THIRD_PARTY_NOTICES.md` and in `qubit_runtime/plugins/qubit-tv/LICENSE`.
- Reusable source reviewed: `plugin-source/` (`desktop/src/plugin.jsx`,
  `plugin.yaml`, `__init__.py`) and `host-source/` (the desktop player host,
  renderer SDK, gateway/local player and TUI player agent).

The MIT notice and copyright are retained. Qubit's `LICENSE` applies only to
original Qubit material and does not relicense upstream-derived code.

## Native desktop port

| Layer | Location |
|---|---|
| Renderer contracts + SDK | `qubit_runtime/apps/desktop/src/lib/player-*.ts`, `src/sdk/desktop-media.tsx` |
| Electron host | `qubit_runtime/apps/desktop/electron/player/`, `player-ipc.ts`, `player-media-jobs.ts` |
| Worker | `qubit_runtime/apps/desktop/public/qubit-tv-worker.py` |
| Python routing | `qubit_cli/plugin_desktop.py`, `gateway/local_player.py`, `tui_gateway/player_agent.py` |
| Plugin (desktop) | `qubit_runtime/apps/desktop/src/plugins/qubit-tv/plugin.jsx` |
| Plugin (agent tools) | `qubit_runtime/plugins/qubit-tv/` |

Security model preserved: session-scoped context tokens, navigation generation,
closed/validated operations, owner-window binding, and no model-controlled
session id. The dispatcher supplies the durable session id; it is never in a
tool schema.

## What is unavailable

- **Widevine/CDM.** The shipped Electron 40.10.2 runtime has no `components`
  API (upstream pins Electron 44.9.3). The player returns the typed
  `unsupported` state and the QubitTV pane says protected playback is
  unavailable. Do not claim DRM playback.
- **Protected services.** Netflix, HBO Max, Apple TV+, Peacock, Disney+, Hulu,
  Prime Video and YouTube TV depend on the CDM; the pane may open the service
  page but does not claim sign-in or playback.
- **Live playback proof.** The port is covered by focused unit tests; a live
  packaged Electron run was not performed in this environment. Ordinary
  eligible YouTube navigation is the only path the upstream preview validates.
