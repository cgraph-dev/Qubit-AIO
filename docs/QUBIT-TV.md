# QubitTV — provenance, scope, and the web-vs-native boundary

**Status:** provenance and scope note. **Not a completion claim.** It records the
license and upstream origin of the QubitTV integration and states what Qubit does
and does not ship. It does not assert that native or DRM playback works.

## What was adapted

Qubit's QubitTV surface adapts the **service list and launcher UI concept** of
[`asimons81/hermestv`](https://github.com/asimons81/hermestv) into a web
launcher. In scope:

- The catalogue of services the upstream plugin presents: YouTube, YouTube TV,
  Netflix, Prime Video, Disney+, Hulu, HBO Max, Apple TV+, Peacock and
  Paramount+.
- The "pick a service, launch it" interaction model (a grid/tiles that open the
  service), expressed as an ordinary web launcher.

Qubit's page, routing, styling and any state are original; nothing from the
upstream frontend bundle is copied in.

## Provenance and license

- Upstream repository: `https://github.com/asimons81/hermestv`
- Pinned commit: `fbd445b381ff5f371bf46bdc96a7d79362d9510f` (2026-10-25)
- License: **MIT**, `Copyright (c) 2025 Nous Research` — full text in the
  upstream `LICENSE`, reproduced with the attribution in
  `THIRD_PARTY_NOTICES.md`.
- Reusable source reviewed: `plugin-source/` — `desktop/src/plugin.jsx` (the
  service grid and launcher UI), `plugin.yaml` (the `hermestv` plugin manifest)
  and `__init__.py` (the session-scoped `hermestv_*` tool set).

The MIT notice and copyright are retained in `THIRD_PARTY_NOTICES.md`. Qubit's
`LICENSE` applies only to original Qubit material and does not relicense
upstream-derived code.

## What was not vendored

- The native Electron desktop plugin SDK (`@hermes/plugin-sdk`) and the
  `ctx.media` / `ctx.desktop.media_request` host capability.
- The embedded/Dock player, transcript and download job runner, and the custom
  HermesTV backend.
- Any credentials, browser profiles, runtime or CDM.

The upstream plugin is a **native Electron media plugin**, not a stock Hermes or
web plugin. It requires the independent HermesTV application host and its native
media SDK; a stock Hermes/Qubit install cannot load it.

## Web-vs-native limitation

QubitTV is a **web launcher**. It opens the selected service in the browser; it
does not embed the upstream native player.

- Ordinary eligible YouTube playback was the only path the upstream preview
  validated.
- Signed-in DRM services (Netflix, HBO Max, Apple TV+, Peacock and peers) are
  untested upstream and depend on the native player/CDM, which Qubit does not
  ship.
- Qubit therefore makes **no claim** of in-page, native, or DRM playback. Do not
  describe QubitTV as a media player in product copy.

## Where future UI work goes

The served Qubit dashboard is built from **`frontend/qubit-web`**; that is the
only correct source for QubitTV UI work. Do not build `qubit_runtime/web` (the
upstream Hermes SPA) into `frontend/qubit-dist`. Rebuild with:

```bash
KEEP_NODE_MODULES=1 bash scripts/build_dashboard.sh
node frontend/qubit-web/scripts/write-build-info.mjs
```

Any future QubitTV component belongs under `frontend/qubit-web/src`.
