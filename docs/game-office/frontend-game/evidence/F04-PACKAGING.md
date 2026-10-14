# F04 — packaging spike: what it proves, and what it does not

**Status: spike complete 2026-10-14.** The packaging path is proven. The **engine decision is NOT
made** — see § "The decision this does not make".

## What was built

| Artifact | Path | Owns |
|---|---|---|
| Build config | `frontend/qubit-web/vite.office-game.config.ts` | one entry → one IIFE, React external |
| Entry | `game/entry.ts` | registers `qubit-office` through the plugin SDK |
| Contract | `game/SceneHost.ts` | the engine-agnostic interface |
| Default host | `game/CanvasSceneHost.ts` | the Canvas2D implementation (spike control) |
| React host | `game/OfficeGameShell.tsx` | mounts exactly one instance, cleanly |
| Tests | `tests/test_office_game_packaging.py` | 12 assertions, all passing |

Output: `frontend/qubit-dist/plugins/qubit-office/qubit-office.game.js`.

**Additive by construction.** `manifest.json` still names `qubit-office.bundle.js`; a test asserts
it stays that way. Promoting the game entry to the served entry is a separate, explicit decision.

## Measured

| | Spike artifact | Current hand-stitched bundle |
|---|---|---|
| Bytes | **5,025** | 256,767 |
| Gzip | **1,917** | 84,755 |
| Lines | 188 | 5,493 |
| SHA-256 | `a8ef2122ef01` | `91fbd8c88368` |

**Do not read that as a 51× win.** The spike compiles **4 modules** (entry, SceneHost,
CanvasSceneHost, OfficeGameShell). The current bundle inlines **7 modules totalling ~5,300 source
lines** including the atlas loader and the sprite baker. The comparison is a packaging-path proof,
not a size verdict. The honest size figure arrives at F16 when the game entry contains comparable
functionality.

**Build cost: 34–131 ms, deterministic** — two builds of unchanged source are byte-identical
(asserted).

## What the spike proves

1. **TypeScript compiles to one IIFE the plugin loader can inject.** The loader has no
   multi-script support (`usePlugins.ts`: one `script.src = assetRoot/entry`), and this satisfies it.
2. **React is not bundled.** The artifact contains no React internals; the host supplies it.
   Falsified deliberately: clearing the `external` list made 5 tests fail, including
   `test_react_is_not_bundled`.
3. **The lifecycle is clean.** Mount binds exactly 6 listeners (pointerdown/move/up/cancel, wheel,
   click); `destroy()` releases all 6; `destroy()` is idempotent; a remount rebinds all 6. That is
   what makes StrictMode's mount→unmount→remount safe instead of stacking a second engine.
4. **The engine choice stays reversible.** `OfficeGameShell.tsx` imports the `SceneHost` interface
   and takes a factory; it contains no engine name. Swapping engines is one factory argument.
5. **Nothing is deleted by building.** `emptyOutDir: false` and `publicDir: false` are pinned, with
   tests. An earlier cut of this config had `emptyOutDir: true` pointed at a path inside the source
   plugin tree — a directory-delete inside a tree holding modules. That is fixed and guarded.

## The decision this does not make

**Phaser is not installed, not added to the manifest, and not imported.** F04's plan step 1 says
"verify/pin approved Phaser 3 version and APIs; do not copy Phaser 4 assumptions" — and
`10-DECISIONS.md:45` says the version is selected **after** the packaging spike, and forbids
guessing it under planning permission.

The facts as of 2026-10-14, for the owner's ruling:

- **Phaser 3's latest is `3.90.0`.** This is what P02 recommends.
- **Phaser 4 is now STABLE at `4.2.1`** (npm `latest` tag is 4.2.1; the `beta` tag still lists
  `4.0.0-rc.7`). It was RC-only when `10-DECISIONS.md` was written — which is exactly *why* P02
  says Phaser 3 and warns against copying Agent Quest's APIs.
- The reference repo `refs/agent-quest` declares `phaser@^4.0.0`.

So P02's premise has partially aged: Phaser 4 is no longer a pre-release. That does not make P02
wrong — Phaser 3 has the larger ecosystem and the plan's reasoning was about API stability — but the
owner should decide knowing 4.x is GA, not RC.

**A PixiJS alternative remains fully open**, per the plan's own renderer comparison, and the
`SceneHost` seam means trying it costs one factory implementation rather than a rewrite.

## Open
- Phaser 3.90.0 vs Phaser 4.2.1 vs PixiJS — owner decision, not taken here.
- Whether to promote `qubit-office.game.js` to the manifest entry — only after F07's slice passes
  the owner's visual gate.
