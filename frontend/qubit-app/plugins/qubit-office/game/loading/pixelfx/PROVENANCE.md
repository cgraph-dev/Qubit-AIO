# pixelfx — vendored weather subset

This directory is a **local, minimal vendor** of
[`patch-gg/pixelfx`](https://github.com/patch-gg/pixelfx), used only by
Qubit Office's loading screen. It is not an npm dependency and no runtime
package manager step is required; the source files are committed beside the
component that uses them.

| | |
|---|---|
| Upstream | `https://github.com/patch-gg/pixelfx` |
| Pin (commit) | `044e12a45dc9cc312cec50ee606dfc98afe5adc2` |
| Version | `0.3.1` |
| Commit date | 2026-11-09 |
| License | MIT — see `LICENSE` and `NOTICE.md` in this directory |

## What was vendored

Only the modules the six weather effects import, plus the effects themselves.
Everything else in upstream (`act`, `rig`, `poses`, `scene`, `chain`, the other
87 effect categories, the gallery, the skill) is deliberately absent.

```
core/{math,color,sim,painter,font,actor,fx}.js   byte-identical
fx/weather/{_lib,god-rays,clouds,rain,storm,snow,fog}.js        byte-identical
core/index.js                                                    authored here (see below)
LICENSE, NOTICE.md                                               byte-identical
```

`core/index.js` upstream exports the whole library. This copy is a locally
authored **dependency boundary** that re-exports only the modules above, so a
future upstream extract cannot silently pull actors/rigs/scenes into the
loading screen. It is the only file in this subtree that is not byte-identical
to upstream.

`core/fx.js` imports `./actor.js` for `stockActor()`; `core/painter.js` imports
`./font.js`. Neither is exercised by the weather effects (they declare no
`actor` and draw no text), but they are vendored because removing the imports
would fork upstream source. The adapter in `../../effects.ts` only ever calls
`createFrame`, `createFx`, `composite` and the effect modules.

## Upstream notices

`NOTICE.md` is upstream's own notice file. It records that parts of the library
adapt Sprite Fusion's open-source pixel art VFX (`sim.js`, and the effects that
say so at the top of their file — including rain, snow, fog, clouds, god-rays
and storm) and Hollow Crown's acting timings. The weather effect headers keep
their "adapted from Sprite Fusion … MIT License, Copyright (c) 2026 Hugo
Duprez" attribution verbatim.

## How to refresh

1. Check out `patch-gg/pixelfx` at the pin above.
2. Copy the exact file list above from `src/`.
3. Keep `LICENSE` and `NOTICE.md` verbatim.
4. Re-run `game/loading/effects.test.ts` (the asset/vendor guard hashes this
   subset and fails on an unexplained change).
5. Update this table and the hashes in `VENDOR_HASHES.json` if the pin moves.

## Why not an npm dependency

The full package is a 244-effect gallery plus an authoring skill; Qubit needs
six weather loops at 320×180. A local subset keeps the shipped surface small,
lets the build stay dependency-free, and makes the exact provenance reviewable
in-tree. This is the plan's "$0 local subset" rule (§7 Pixelfx).
