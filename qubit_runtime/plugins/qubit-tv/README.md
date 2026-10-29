# QubitTV plugin

Qubit-native adaptation of the MIT-licensed HermesTV experimental preview.

- Upstream: `asimons81/hermestv`
- Pin: `fbd445b381ff5f371bf46bdc96a7d79362d9510f` (2026-10-25)
- License: MIT, Copyright (c) 2025 Nous Research (see `LICENSE`)
- Adapted by: Qubit, 2026-10-29

The desktop pane lives in
`qubit_runtime/apps/desktop/src/plugins/qubit-tv/plugin.jsx` and is bundled
into `desktop/plugin.js` for the runtime plugin door. It declares the proposed
`media.player` capability and is opt-in (`defaultEnabled: false`). It renders a
native `ctx.media.Surface`, the ten service cards, dock/pop-out/pin/fullscreen,
back/forward/home, play/pause/mute/seek, session linking, context refresh,
Ask Qubit prompt seeding, transcript/download jobs, job list/status/cancel,
reveal, favorites, service search, and clear-local-data.

Protected playback (Widevine/CDM) is **not** implemented in this build. The pane
says so explicitly: a DRM service can open in the host player, but sign-in and
protected playback are not claimed.

## Tools

`qubit_tv_context`, `qubit_tv_control`, `qubit_tv_transcript`,
`qubit_tv_download`, `qubit_tv_jobs`, `qubit_tv_job_status`,
`qubit_tv_job_cancel`.

The dispatcher supplies the durable session id; it is never in the model schema
and never taken from model-controlled arguments. Tool names are the only public
surface — the upstream `hermestv_*` names are not registered as aliases.

## Build the desktop bundle

From this directory:

```bash
node scripts/build.mjs
```

The script bundles the app plugin source with esbuild, externalizing
`@qubit/plugin-sdk`, `react`, and `react/jsx-runtime`, then verifies the output
imports only those modules. The bundle is written to `desktop/plugin.js`.

## Host requirements

The pane needs `ctx.media.usePlayer()`, `ctx.media.Surface`, and
`host.openWorkspace`. Without `ctx.media` the pane renders a typed placeholder
("QubitTV needs host media support"). The Python tools need the host's
`ctx.desktop` media capability; an older host reports the tools unavailable.
