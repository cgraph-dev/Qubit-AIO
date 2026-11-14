# Qubit Office — asset and license ledger

**Status:** Phase 0 inventory. Not a completion claim. No art is committed by this file.
**Local root:** `/home/trick/Desktop/UI Office` (operator-owned; not shipped).
**Measured:** 2026-11-14 — **57,268 files, 904,388,468 bytes** (≈862 MiB; **1.1 GB** on disk),
**55,739** of them PNG/JPEG/WebP images across **16** top-level packs. Re-measured with
`scripts/office_asset_ledger.py`; the 1,523-file / 404 MB figure this file previously carried was
stale.

The **per-file ledger is generated to a local path outside the repository**, never committed
(paid-art metadata with operator paths must stay off-tree):

```bash
python scripts/office_asset_ledger.py \
    --source "/home/trick/Desktop/UI Office" \
    --out /tmp/qoffice-assets/manifest.json --check
# → wrote 57268 rows to /tmp/qoffice-assets/manifest.json  (exit 0)
```

Each row records relative path, bytes, sha256, PNG/JPEG/WebP width/height (headers parsed in pure
Python; no PIL, no pixel decode), top-level pack, extension, honest `license_status`
(`license_file_present` only when a LICENSE/README exists in that pack, else `unknown`), and an
`intended_use` candidate. `--out` is **refused inside the repository**.

## 1. Pack inventory

Measured 2026-11-14; counts are files in each top-level entry (extracted tree or archive).

| Pack / file | Files | Bytes | Intended use | License status |
|---|---:|---:|---|---|
| `Modern_Interiors_RPG_Maker_Version/` + zip | 500 + 1 | 9,444,262 + 8,585,131 | floors, walls, furniture, room styles | LimeZu paid/local-only; verify pack license text before use |
| `moderninteriors-win/` + zip | 52,969 + 1 | 163,405,005 + 156,686,302 | full Modern Interiors source | LimeZu paid/local-only; do not commit |
| `modernuserinterface-win/` + zip | 982 + 1 | 5,128,641 + 4,545,259 | UI panels, HUD, buttons, frames | LimeZu Modern UI; paid/local-only; verify |
| `Character Generator 2.0 Linux Build/` + zip | 1,631 + 1 | 228,050,377 + 98,537,001 | body/eyes/hair/outfit/skin generator | paid/local-only; do not ship generator binaries |
| `Character Generator 2.0 Setup.exe` | 1 | 73,482,946 | operator-only generator installer | paid/local-only; never commit |
| `Portrait_Generator_1.5.0_Linux_Build/` + zip | 1,080 + 1 | 93,774,096 + 33,084,842 | portrait parts/atlas generator | paid/local-only; do not ship |
| `Portrait_Generator_Setup.exe` | 1 | 26,075,219 | operator-only installer | paid/local-only; never commit |
| `Serene_Village_revamped_v1.9/` + zip | 31 + 1 | 680,253 + 637,207 | outside-world/terrain candidate | license verify; not used yet |
| `Modern_Interiors_Free_v2.2/` + zip | 66 + 1 | 1,171,045 + 1,100,882 | reference/free pack | free-pack terms may restrict commercial use; not used |

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

`scripts/office_asset_ledger.py` emits the per-file JSON/JSONL ledger described at the top of this
file: relative path, bytes, sha256, PNG/JPEG/WebP dimensions, pack, extension, honest
`license_status`, and an `intended_use` candidate. It reads metadata only, never copies art, and
refuses an `--out` inside the repository.

```bash
python scripts/office_asset_ledger.py --source "/home/trick/Desktop/UI Office" \
    --out /tmp/qoffice-assets/manifest.json --check      # 0 = complete/consistent ledger
python scripts/office_asset_ledger.py --source ... --summary   # counts only
```

`--check` exits `0` only if it found files, every row has sha256/bytes/pack, and the emitted row
count equals the walk count; `1` means an incomplete/inconsistent ledger, `2` a usage/source error
or an in-repo `--out`. Contract test:
`tests/office_contracts/test_office_asset_ledger.py` (synthetic tree, no real art).

Do not infer a part's license from its filename: `license_status` stays `unknown` unless a
LICENSE/README file is present in that pack, in which case it is recorded as
`license_file_present` (the name only — the terms still need a human review).
