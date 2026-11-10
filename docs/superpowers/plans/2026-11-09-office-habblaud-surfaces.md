# Qubit Office — Habblaud additive surfaces (floors first) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Let the active office theme supply real floor art from the owner's purchased interior pack, while `classic` keeps today's procedural floors byte-identical. Habblaud may be extended (owner ruling 16) but must not regress.

**Architecture:** Qubit's theme payload gains a `surfaces` block (validated, versioned URLs) and is injected into the office document as `window.__QUBIT_OFFICE_THEME__`. The Habblaud source gains a read-only asset provider (`client/src/art/theme-assets.ts`) that preloads the theme's floor tiles and returns a `PixelBuf` per `FloorKind`; `floor.ts` uses it when present and otherwise runs the existing procedural code. No paid art is committed; the local pack is built from the owner's purchase. The patched client is re-vendored with updated provenance.

**Tech Stack:** Python (`voicebridge/office/theme.py`, builder/extractor, `webui.py`), TypeScript (Habblaud source), Pillow, Vitest, pytest.

## Global constraints
- `classic` default = current behavior; every surface falls back to procedural when absent/failed.
- No paid art in the repo; the local pack lives at `$QUBIT_HOME/office/themes/<id>`.
- Never edit the vendored `habblaud/` by hand: rebuild from the pinned source and vendor.
- No operator paths in shipped files; typed tokens/CSS rules unchanged.
- Focused tests only during implementation; no service restart; no full matrix; no VERIFIED claim.

## Floor map (measured 2026-11-09, 16×16 tiles)
Sheet: `1_Interiors/16x16/Room_Builder_subfiles/Room_Builder_Floors_16x16.png` (15×40 tiles).
| Habblaud `FloorKind` | col,row | Note |
|---|---|---|
| wood | 0,13 | warm planks |
| carpet | 0,16 | neutral beige |
| tile_check | 0,2 | light checker |
| tile_white | 0,5 | white tiles |
| marble | 12,6 | grey marble |
| concrete | 12,17 | grey concrete |
| grass / street / sidewalk | — | not in the interior pack; keep procedural |

## Tasks

### B0: Theme payload `surfaces` + office-document global (Qubit)
**Files:** `voicebridge/office/theme.py` (contract/payload/validation), `voicebridge/webui.py` (`_serve_app_html`), `tests/test_office_theme.py`, `tests/test_office_framing_policy.py`.
- [ ] Add `surfaces` to the full-pack contract: `{cell:int>0, floor:{<FloorKind>: relpath}}`; relpaths must be declared in `files`, suffix-checked, no path escapes. The recipe carries `surfaces` **coordinates** (`{cell, floor:{kind:{sheet,col,row}}}`), no art; `validate_recipe` accepts them.
- [ ] `theme_payload` exposes `surfaces: {cell, floor:{kind: url?v=...}}` only for the active full pack; classic has `surfaces: null`.
- [ ] `_serve_app_html` injects `<script>window.__QUBIT_OFFICE_THEME__=<json>;</script>` for `qubit-office` only (escaped `</script>`), with the same payload; classic included; other apps none.
- [ ] Tests: contract validation; payload URLs; classic null; office doc global present/escaped, other app none; no file map leaked.

### B1: Extract floors + build local pack (Qubit)
**Files:** `scripts/office_theme_extract.py` (floor map), `scripts/office_theme_build.py` (write `surfaces/floor_*.png` + manifest + recipe), `tests/test_office_theme_extract.py`, `tests/test_office_theme_build.py`.
- [ ] `extract_floor_tiles(source, out_dir, size=16) -> dict[kind, Path]` reads the map above from the zip in memory and writes 16×16 PNGs locally.
- [ ] Builder includes the surfaces in the local pack (`surfaces/floor_<kind>.png`), records sha/bytes, and writes the surface coordinates into `--recipe-out` (no art).
- [ ] Tests: map crops the expected pixels; unmapped kinds absent; recipe has coordinates only; no PNG in a recipe; `--check` semantics preserved.

### B2: Habblaud provider + floor hook (source worktree)
**Files (in `/home/trick/qubit-worktrees/habblaud-theme`):** create `client/src/art/theme-assets.ts`; modify `client/src/art/surfaces/floor.ts`; startup preload at the client entry; tests (`client/src/art/theme-assets.test.ts`).
- [ ] Provider reads `window.__QUBIT_OFFICE_THEME__`, exposes `preloadThemeAssets()`, `themeFloorTile(kind): PixelBuf | null` (cached, RGBA→PixelBuf), and `themeActive()`; absent/classic/failed ⇒ null.
- [ ] `floorChunk` returns a repeat fill from `themeFloorTile(kind)` when present; otherwise the unchanged procedural switch. Integer 16px tiles only.
- [ ] Preload before the first world render; if preload fails, render procedural.
- [ ] Tests: null on classic/absent; a synthetic 16×16 image yields a PixelBuf and a filled chunk; procedural output unchanged when absent (hash).

### B3: Build, vendor, verify, goldens
- [ ] Build the patched client (`npm run build:client`); `python3 scripts/vendor_habblaud.py --src /home/trick/qubit-worktrees/habblaud-theme`; update `PROVENANCE.json` (local-change list, same upstream commit).
- [ ] Run the vendored/office tests + `vendor_habblaud.py --check`; classic goldens byte-identical; themed floor golden; rollback = `office.theme: classic` + `git revert` the vendor commit.
- [ ] No live restart until the owner says so.

## Verification (focused)
- `PYTHONPATH=. TMPDIR=/tmp /home/trick/venvs/qubit-0.0.13/bin/python -m pytest -q tests/test_office_theme.py tests/test_office_theme_extract.py tests/test_office_theme_build.py tests/test_office_framing_policy.py`
- Habblaud: `npm test -- --run client/src/art` (or the focused files), `npm run build:client`.
- `python3 scripts/vendor_habblaud.py --check` exit 0 after the re-vendor; `scripts/office_theme_audit.py` exit 0.
- Classic: `/qubit-office` unchanged; themed: floors use the pack tiles; other surfaces procedural.
