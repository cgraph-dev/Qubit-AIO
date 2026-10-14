# Engine decision — Phaser 4.2.1 (owner ruling 2026-10-14)

## The ruling

The owner chose **Phaser 4.2.1** — *"pick engine best one with most features and newest and most capable
to work with ai"*.

`10-DECISIONS.md:45` forbade GUESSING a version: *"Select/pin the version after a small
compatibility/packaging spike; do not guess a dependency version or install it under this planning
permission."* The spike (F04) was completed first, the facts were put to the owner, and this is an
explicit ruling. That is the process the plan required.

**This supersedes P02's "Phaser 3"** for the same reason P02 named 3 when it was written: the plan
picked the current stable, and the current stable has since moved.

| | Value |
|---|---|
| Package | `phaser` |
| Version | **4.2.1** (pinned exactly — no `^`, no `~`) |
| Licence | **MIT** — Copyright (c) 2026 Richard Davey, Phaser Studio Inc. |
| Source | `https://github.com/phaserjs/phaser` via npm tarball, **unmodified** |
| ESM entry | `dist/phaser.esm.js` (8,405,542 bytes) |
| Local changes | **NONE.** `PhaserSceneHost.ts` only calls the library; no Phaser source was copied in |
| npm `latest` at the time | 4.2.1 (so Phaser 3.90.0 is the previous major, not the current stable) |

**Why 4 over 3, beyond "newest":** Phaser 4 is the GA line, has the larger forward development budget,
a modern ESM/bundler entry (`phaser.esm.js`, matching this project's Vite build), and an active
official AI/LLM integration surface. Phaser 3 (3.90.0) remains the conservative choice with the larger
legacy ecosystem; it is the fallback if 4.x proves unstable in practice.

## Live browser proof — Phaser 4.2.1 boots and its API matches what the host calls

Run against the **real ESM build of the pinned package**, in Chrome, not a mock
(`phaser_api_probe.py`; the stage serves `node_modules/phaser/dist/phaser.esm.js`):

| Check | Result |
|---|---|
| `Phaser.VERSION` | **`4.2.1`** |
| Game constructs with `type: Phaser.CANVAS`, banner/audio off | yes, **zero page errors** |
| `this.textures.createCanvas(...)` | present |
| `.getContext()` returns a usable 2D context (`fillRect`) | **true** |
| A pixel written through the context is **readable back after `.refresh()`** | **true** — the repaint path the host relies on works |
| `this.add.image(...).setOrigin(0,0)` | works |
| canvases while running → after `destroy(true)` | **1 → 0** (no leaked canvas on unmount) |

**This is the answer to the risk the plan raised** (`10-DECISIONS.md:45` warned against copying
Phaser 4 APIs into a Phaser 3 build): the APIs `PhaserSceneHost` uses are confirmed present in 4.2.1
by execution, not by reading the docs.

Two probe iterations were needed and the first result is worth recording: the built artifact
**throws `ReferenceError: React is not defined` unless a React global exists**, because React is
externalized. That is not a defect — it is the externalization working — but it means any future
browser test of this artifact must supply React first.

## Measured cost — read this before promoting the artifact

| Artifact | Raw | Gzip |
|---|---|---|
| Before the engine (F04 spike: 4 modules) | 5,025 B | 1,917 B |
| Input router + navigation wired in | 14,410 B | 4,790 B |
| **With Phaser 4.2.1** | **7,286,820 B (7.29 MB)** | **1,245,810 B (1.25 MB)** |

**The engine is ~500× the size of everything else in this plugin.** That is the price of the ruling and
it is stated plainly rather than buried: Phaser is a complete game framework, and importing it brings
the framework.

This is **not** shipped to users yet. `manifest.json` still names `qubit-office.bundle.js`; the game
entry is `qubit-office.game.js` and a test holds that separation. Promoting it is a **separate,
explicit decision**, and this size is the main input to it. Options if 1.25 MB gzip is unacceptable on
the served page: a custom Phaser build (Phaser supports excluding unused subsystems — this build uses
only `textures`, one scene, and its input layer), lazy-loading the game entry only when the Office tab
opens, or falling back to `CanvasSceneHost` (already implemented, 14 kB).

## What was built

| File | Role |
|---|---|
| `game/PhaserSceneHost.ts` | the Phaser implementation of `SceneHost` |
| `game/CanvasSceneHost.ts` | the Canvas2D implementation — **kept as a working fallback** |
| `game/SceneHost.ts` | the one contract both answer |

`OfficeGameShell.tsx` defaults to Phaser and takes the engine as a **factory argument**, so switching
back to Canvas is one argument, not a rewrite. Tests assert:
- the React layer never constructs Phaser directly;
- both hosts implement the same five methods;
- the Phaser host binds *and releases* its own gestures (one gesture vocabulary, not two);
- the host defines **no** `requestAnimationFrame` and **no** `update()` tick — Phaser owns the loop,
  and the host paints only revisions it was handed (C7).

## Provenance obligations met

- Exact package, version, licence and upstream URL recorded here (AGENTS.md).
- The MIT notice travels inside the built artifact (5241 occurrences of the licence text verified in
  the output).
- No Phaser source was copied into Qubit code; `LICENSE` is unaffected.
- `THIRD_PARTY_NOTICES.md` should gain a Phaser line before the artifact is ever published — recorded
  as an open item, not silently assumed done.

## Open
1. **Decide whether to promote `qubit-office.game.js`** to the served entry, given 1.25 MB gzip.
2. `THIRD_PARTY_NOTICES.md` — add the Phaser entry before publication.
3. F07 (first playable slice) is now unblocked and is the next packet.
