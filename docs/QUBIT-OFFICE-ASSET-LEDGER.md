# Qubit Office — asset and license ledger

**Status:** Phase 0 inventory. Not a completion claim. No art is committed by this file.
**Local root:** `/home/trick/Desktop/UI Office` (operator-owned; not shipped).
**Measured:** 1,523 files, 404 MB.

## 1. Pack inventory

| Pack / file | Files | Size | Intended use | License status |
|---|---:|---:|---|---|
| `Modern_Interiors_RPG_Maker_Version/` + zip | 500 | 9.4 MB + 8.6 MB zip | floors, walls, furniture, room styles | LimeZu paid/local-only; verify pack license text before use |
| `moderninteriors-win.zip` | 1 | 156.7 MB | full Modern Interiors source | paid/local-only; do not commit |
| `modernuserinterface-win/` + zip | 982 | 5.1 MB + 4.5 MB zip | UI panels, HUD, buttons, frames | LimeZu Modern UI; paid/local-only; verify |
| `Character Generator 2.0 Linux Build.zip` | 1 | 98.5 MB | body/eyes/hair/outfit/skin generator | paid/local-only; do not ship generator binaries |
| `Character Generator 2.0 Setup.exe` | 1 | 73.5 MB | operator-only generator installer | paid/local-only; never commit |
| `Portrait_Generator_1.5.0_Linux_Build.zip` | 1 | 33.1 MB | portrait parts/atlas generator | paid/local-only; do not ship |
| `Portrait_Generator_Setup.exe` | 1 | 26.1 MB | operator-only installer | paid/local-only; never commit |
| `Serene_Village_revamped_v1.9/` + zip | 31 | 0.7 MB + 0.6 MB zip | outside-world/terrain candidate | license verify; not used yet |
| `Modern_Interiors_Free_v2.2*.zip` | 2 | 1.1 MB each | reference/free pack | free-pack terms may restrict commercial use; not used |

The archive contains duplicate `.zip` and extracted trees. The ledger records both; build scripts
must use one canonical extracted source and record its sha256.

## 2. Required asset record

Every asset used by a room, character, panel, map or effect must have:

```
asset_id, local_path, sha256, bytes, width/height, pack
license, license_source, paid, redistribution_allowed, credit_required
intended_use (room/style/character/portrait/UI/outside/FX)
department/room/style/character slot
extracted_from, atlas/frame coordinates
status (candidate/approved/in-use/rejected)
used_by, provenance, notes
```

## 3. Intended architecture

- **Modern Interiors:** 16×16 world baseline; floors, walls, furniture, props, room archetypes.
- **Modern UI:** panels, HUD, buttons, frames, icons; never mix 16/32/48 inside one room.
- **Character Generator 2.0:** body 9 / eyes 7 / hair 29 / outfit 33 / skin 9 parts; canonical
  character profile selects parts; no raw generator binary shipped.
- **Portrait Generator 1.5:** portrait cells/atlases for agent cards and close-ups.
- **Serene Village:** outside-world candidate only after license review; not used.
- **Paid art:** local-only. The repository ships recipes, coordinates and tooling, not the art.
- **Public tree:** no paid archives, extracted parts, derived atlases or screenshots that expose
  paid art beyond allowed credit/preview use.

## 4. License / redistribution rules

1. Record the exact license text and source before use.
2. Paid/LimeZu art stays outside the public tree.
3. Never commit `.zip`, `.exe`, generator binaries or extracted atlases.
4. Build scripts fail closed when an asset's license/provenance is missing.
5. Scan bundles, caches, screenshots, logs, backups and exports for paid-art leakage.
6. Keep required credit visible: `Office art: LimeZu — https://limezu.itch.io`.
7. If an asset cannot be shipped, stop with `BLOCKED — LICENSE REVIEW REQUIRED`; do not
   silently substitute or strip it.

## 5. Regeneration

A future `scripts/office_asset_ledger.py` must emit a per-file JSON/CSV with sha256, dimensions,
pack, license status and intended use. Until then, this summary is the authoritative inventory and
the local root is the source. Do not infer a part's license from its filename.
