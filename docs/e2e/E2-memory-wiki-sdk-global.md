# Qubit E2 — the Memory Wiki plugin tab is broken on every route

> **Historical evidence — captured output retains pre-rebrand names.** The framing of this report
> is Qubit's; raw page/console captures and quoted command output below are preserved verbatim
> from the run. Pre-rebrand plugin globals appear only as literal evidence.

**Severity: high** (a whole feature tab is dead), **same root cause family as E1**.

A dashboard plugin is a *build artifact the host does not control*, so the host
is supposed to expose the SDK under **both** the canonical and legacy names.
`registry.ts` on disk does exactly that — but the served host bundle predates
that change and sets only the legacy name, while the installed `memory-wiki`
plugin reads the **canonical** one. The plugin therefore throws at import time.

### Evidence

`registry.ts` on HEAD sets four globals (`frontend/qubit-web/src/plugins/registry.ts:194-212`):

```js
window.__QUBIT_PLUGINS__ = registry;
window.__QUBIT_PLUGIN_SDK__ = sdk;
window.__HERMES_PLUGIN_SDK__ = sdk;      // legacy alias for old bundles
window.__HERMES_PLUGINS__ = registry;
```

The served host bundle sets **only** the two `__HERMES_` ones:

```
$ grep -o '__QUBIT_PLUGIN_SDK__' frontend/qubit-dist/assets/index-qwvk42Oz.js | wc -l
0
$ grep -o '__HERMES_PLUGIN_SDK__' frontend/qubit-dist/assets/index-qwvk42Oz.js | wc -l
1
```

Read back from the live page after boot (both token names injected so E1 does
not mask this):

```
__QUBIT_PLUGIN_SDK__     -> undefined
__HERMES_PLUGIN_SDK__    -> object
__QUBIT_PLUGINS__        -> undefined
__HERMES_PLUGINS__       -> object
```

The installed plugin reads the canonical name — its own shipped dist does this,
so this is **not** an old-bundle case, it is a new plugin against a stale host:

```
$ grep -o '__QUBIT_PLUGIN_SDK__' \
    ~/.local/state/qubit-voicebridge/runtime/plugins/memory-wiki/dashboard/dist/index.js
__QUBIT_PLUGIN_SDK__        <- the plugin's compiled dist
$ grep -n 'window.__QUBIT_PLUGIN_SDK__' \
    ~/.local/state/qubit-voicebridge/runtime/plugins/memory-wiki/dashboard/src/index.jsx
25:const SDK = window.__QUBIT_PLUGIN_SDK__;
26:const { React } = SDK;
```

Console, on **every** route (`/chat`, `/qubit-chat`, `/sessions`, `/models`):

```
TypeError: Cannot destructure property 'React' of 'SDK' as it is undefined.
    at http://127.0.0.1:8890/dashboard-plugins/memory-wiki/dist/index.js:5:9
    at http://127.0.0.1:8890/dashboard-plugins/memory-wiki/dist/index.js:324:3
```

The nav still advertises the tab (`nav has MEMORY WIKI: True`), so the user sees
a tab that renders an empty face — exactly the operator's "blank pane"
complaint, arriving by a different path from E1.

### Why it is not fixed here

Same as E1: the fix is a rebuild of the shared `frontend/qubit-dist`.
`registry.ts` on disk is already correct and needs no edit.

**Verification owed after the concurrent build lands:** assert
`typeof window.__QUBIT_PLUGIN_SDK__ === "object"` in the live page and that the
`memory-wiki` console TypeError is gone on all four routes.

### A hardening note (not applied — owner decision)

The host tolerates old plugin bundles but not *new* ones: it sets the names
`registry.ts` happens to list, and a plugin built against a **newer** host than
the one serving it throws. A plugin loader that (a) always publishes the
canonical names and (b) skips / reports a plugin whose entry throws, would turn
this class of drift from "dead tab" into "one tab reported unavailable". That
is a loader change in `frontend/qubit-web/src/plugins/` and was left to the
owner rather than made speculatively.
