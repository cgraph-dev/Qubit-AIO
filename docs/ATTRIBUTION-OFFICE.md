# Qubit Office — art attribution

**Status:** attribution record for Qubit Office theme art. It does not grant
rights to any pack and is not a release verdict. The legal boundary is recorded
in `LICENSE-ASSETS` and `THIRD_PARTY_NOTICES.md`; this file is the reader-facing
credit and the exact-pack record.

---

## Persistent credit (independent of a theme)

The Qubit Office host shell always renders a Credits/About line:

```text
Office art: LimeZu — https://limezu.itch.io
```

It is part of the shell
(`frontend/qubit-app/plugins/qubit-office/game/app/OfficeAppShell.tsx`,
`data-testid="office-app-credits"`), not the vendored Habblaud client and not
the theme pack. It is visible with `office.theme: classic`, with an active
theme, and after the local pack is deleted. The active theme's own `credit`
field is additional; it is never the only credit.

`LICENSE-ASSETS` records the same line and the per-pack terms.

---

## Credit line for the purchased packs

The two LimeZu packs used to build the office art require credit but forbid
redistribution:

```text
Office art: LimeZu — https://limezu.itch.io
```

Do **not** describe these packs as CC-BY. Their licence permits commercial and
non-commercial use, requires credit, and forbids resale/redistribution
(Modern User Interface additionally forbids NFT minting). CC-BY 4.0 applies only
to Serene Village, which is not used by the v1 theme.

---

## Exact packs and files used

The build reads the owner's purchase at the owner's local download. The purchase
is never copied into the repository; only the derived atlas a person builds
locally is written, under `$QUBIT_HOME/office/themes/<id>`.

| Pack | Archive | What the extractor reads |
|---|---|---|
| Character Generator 2.0 | `Character Generator 2.0 Linux Build.zip` | `Character Pieces/Bodies/{s}x{s}/Body_01.png` … (9 bodies); `Character Pieces/Outfits/{s}x{s}/Outfit_01_01.png` … (33 outfits); `Character Pieces/Eyes/{s}x{s}/Eyes_01.png` … (7 body eyes); `Character Pieces/Hairstyles/{s}x{s}/Hairstyle_01_01.png` … (29 body hairstyles) |
| Portrait Generator 1.5.0 | `Portrait_Generator_1.5.0_Linux_Build.zip` | `Portrait Pieces/Portrait_Generator - {s}x{s}/Skins/Skin 1.png` … (9 skins); `Eyes/Eyes 01.png` … (7 portrait eyes); `Hairstyles/Hairstyle 01 1.png` … (29 portrait hairstyles) |
| Modern User Interface | `modernuserinterface-win.zip` | the sampled UI palette sheet, and the `PG_*` portrait pieces when the Portrait Generator archive is absent |

`s` is the tier (16, 32 or 48); the default is 32. Portrait sheets measure
640x192 (a grid of 64x64 cells at the 32 tier); body sheets measure 1792x1280
(32x64 frames). The builder crops the calibrated cells and writes the derived
atlas; it does not copy a source sheet.

`Character Generator 2.0 Setup.exe`, `Portrait_Generator_Setup.exe`, and the
Modern Interiors/Serene Village archives are recorded in
`THIRD_PARTY_NOTICES.md` for provenance. They are not read by the v1 extractor
except where named above. The full Modern Interiors pack is not a build input;
the character art comes from the Character Generator archive.

---

## Generator tools are not shipped

- **Character Generator 2.0** — art: LimeZu's official character generator,
  included with the complete pack; the tool itself is GPLv3 upstream.
- **Portrait Generator 1.5.0** — a Gray Matter Studios application; art:
  LimeZu; no licence file in the archive.

Qubit does not ship, modify, or redistribute either tool. The build reads the
archives in memory. The recipe — not the tool — is what a person shares.

---

## Serene Village — CC-BY 4.0 TASL (not used in v1)

Serene Village is not used by the Qubit Office v1 theme. Its TASL is recorded
here so it is ready if a future theme uses it. Serene Village is CC-BY 4.0 (not
CC-BY-SA), so attribution and an indication of changes are required, with no
source-sharing obligation.

```text
Title:   Serene Village
Author:  LimeZu
Source:  https://limezu.itch.io
Licence: CC-BY 4.0 — https://creativecommons.org/licenses/by/4.0/
Changes: sliced into cells and positioned by the Qubit Office theme builder;
         no other modification.
```

If it is used: keep this TASL block, state the changes, and keep the persistent
LimeZu credit. The purchased Modern Interiors / Modern User Interface packs
remain under their paid-art licence and must never be described as CC-BY.

---

## How the art is handled

- The repository ships the recipe: semantic tokens, part indices, template
  metadata, credit and licence. It ships no archive, no extracted part sheet,
  and no atlas.
- The local builder slices, positions and composites the purchased pixels. It
  does not train models, generate new art, or mint an NFT.
- A shared recipe never carries LimeZu art. Each person builds against their
  own purchase.
- The attribution is persistent in the Qubit shell, so it does not depend on
  the owner having a theme pack installed.
