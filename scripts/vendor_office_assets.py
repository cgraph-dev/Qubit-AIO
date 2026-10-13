#!/usr/bin/env python3
"""Vendor the Office art assets from /refs into the served dist plugin tree.

WHY THE DIST TREE (and not the app tree)
----------------------------------------
`docs/game-office/merge/90-CORRECTIONS.md` §C1 is the load-bearing fact. The
Office plugin's entry JS is served from `frontend/qubit-app/plugins/qubit-office/`
via the overlay fast path in `voicebridge/webui.py:_serve_qubit_plugin`, which
serves ONLY `.js/.json/.css` and only at shallow depth. A `.png` placed there
returns 404 (proven live this session).

The path that DOES serve PNG/SVG/WOFF2 is `_serve_qubit_static`
(`webui.py:1586`), which serves from `_qubit_dist()` = `frontend/qubit-dist/`
under the URL prefix `/plugins/`. So the art is vendored into

    frontend/qubit-dist/plugins/qubit-office/assets/

and fetched by the browser from `/plugins/qubit-office/assets/<path>`.

It is written to TWO trees, and both are required:

1. `frontend/qubit-web/public/plugins/qubit-office/assets/` — the durable,
   git-tracked source. `scripts/build_dashboard.sh` runs
   `vite build --emptyOutDir` into `frontend/qubit-dist/`, which WIPES that
   tree; vite then copies `public/` verbatim into it. This is exactly how the
   kanban and qubit-achievements plugin assets reach the served tree. Art that
   lived only in dist would be silently destroyed on the next dashboard build.
2. `frontend/qubit-dist/plugins/qubit-office/assets/` — the served copy, so the
   art is reachable immediately in the current working tree.

This is a COPY, not a symlink and not an import: `/refs` is a staging area that
is never served and may be deleted (rule M5). Nothing in the shipped tree
references `/refs`.

WHAT IT VENDORS (owner decisions O1/C4 — neutral families only)
---------------------------------------------------------------
The allowlist below is exact. It deliberately excludes every celebrity/meme
sprite family (`approved_elonmusk`, `approved_harrypotter`, `approved_xenomorph`,
…) even though they exist in the source: the office is a workforce, not a
costume party. Sound (`assets/sfx/*.wav`) is NOT vendored — owner decision O2
inlines the 12 bleats as base64 in the bundle instead, so no `.wav` ever needs
serving.

PROPERTIES
----------
* **Idempotent** — re-running writes byte-identical output. Verified by comparing
  the on-disk sha256 of every file against the freshly computed source hash.
* **`--check`** — exits non-zero if the vendored tree is stale (a file missing,
  mutated, or an unexpected extra file present). This is the CI gate.
* **Manifest** — writes `assets/MANIFEST.json` with `{path, sha256, bytes}` per
  file plus a `total_bytes` and the intentional `excluded_families` record.
* **Size cap** — refuses (exits non-zero, writes nothing) if the vendored tree
  would exceed 100 MB. Per owner decision, the Office art stays well under.

USAGE
-----
    .venv/bin/python scripts/vendor_office_assets.py            # write
    .venv/bin/python scripts/vendor_office_assets.py --check    # fail if stale
    .venv/bin/python scripts/vendor_office_assets.py --refs DIR # alternate source
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: The served dist plugin tree — the ONLY place a PNG is reachable (C1).
DIST_PLUGIN = REPO / "frontend" / "qubit-dist" / "plugins" / "qubit-office"

#: The durable, git-tracked source the dashboard build copies into dist.
#: `scripts/build_dashboard.sh` runs `vite build --emptyOutDir` into
#: `frontend/qubit-dist/`, which WIPES that tree; vite then copies
#: `frontend/qubit-web/public/` verbatim into it (that is how the kanban and
#: qubit-achievements plugin assets reach the served tree — verified identical).
#: So the vendored art must live under `public/plugins/` to survive a rebuild,
#: and is mirrored into dist so it serves immediately in this working tree.
PUBLIC_PLUGIN = REPO / "frontend" / "qubit-web" / "public" / "plugins" / "qubit-office"

#: Every tree the vendored assets are written to. Order: durable source first.
TARGETS = [PUBLIC_PLUGIN / "assets", DIST_PLUGIN / "assets"]

#: Back-compat alias: the primary (served) tree.
ASSET_ROOT = TARGETS[-1]

#: Default read-only staging area. Overridable with --refs.
DEFAULT_REFS = Path("/home/trick/Projects/Repos/refs")

#: Hard ceiling on the vendored tree. Owner decision: 100 MB.
MAX_TOTAL_BYTES = 100 * 1024 * 1024

#: The neutral sprite families kept for agent figures (O1/C4). Everything else
#: under `starnet/.../sprites/` is a celebrity/meme cosplay set and is excluded.
SPRITE_FAMILIES = [
    "approved_blank_amber",
    "approved_blank_blue",
    "approved_blank_green",
    "approved_blank_red",
    "approved_android",
    "approved_astronaut",
    "approved_animegirl",
]

#: Families present in the source that are deliberately NOT taken. Recorded in
#: the manifest so an auditor sees the exclusion was intentional, not an
#: unnoticed drop (the review agents' C4 finding).
EXCLUDED_FAMILIES = [
    "approved_alien", "approved_baddie", "approved_bear", "approved_bikini",
    "approved_bunnycutie", "approved_candyprincess", "approved_capybara",
    "approved_caseyjones", "approved_catinthehat", "approved_catwoman",
    "approved_crewmate", "approved_crthead", "approved_dario",
    "approved_elonmusk", "approved_endoskeleton", "approved_finn",
    "approved_freddyfazbear", "approved_ghostface", "approved_gothgirl",
    "approved_grimreaper", "approved_harrypotter", "approved_heisenberg",
    "approved_masterchief", "approved_morpheus", "approved_ninjaturtle",
    "approved_pepe", "approved_plaguedoctor", "approved_reptilian",
    "approved_ricksanchez", "approved_rickybobby", "approved_robocop",
    "approved_robot", "approved_samaltman", "approved_secretagent",
    "approved_skeleton", "approved_station_minion", "approved_tonymontana",
    "approved_tylerdurden", "approved_ultron", "approved_ultrondroid",
    "approved_vaultboy", "approved_voidwizard", "approved_xenomorph",
]

#: Directories whose every matching file is taken. Each entry is
#: (source dir relative to refs, destination dir relative to assets/, glob).
#: The allowlist is DIRECTORY-SCOPED on purpose: taking `starnet/` wholesale
#: would drag in 3.5 GB of build output (30-SOURCE-INVENTORY.md).
TREE_RULES: list[tuple[str, str, str]] = [
    # starnet furniture — the 48 props. Excludes the source's own manifest.json.
    ("starnet/frontend/assets/furniture", "furniture", "*.png"),
    # agent-quest CC0 tiles: floor surfaces and room exteriors (M7).
    ("agent-quest/client/public/assets/themes/tiny-swords-cc0/Terrain",
     "tiles/terrain", "**/*"),
    ("agent-quest/client/public/assets/themes/tiny-swords-cc0/BuildingsCustom",
     "tiles/buildings", "**/*"),
    # HUD chrome / seat badges — buttons, icons, pointers, ribbons, banners.
    ("agent-quest/client/public/assets/themes/tiny-swords-cc0/UI/Buttons",
     "ui/buttons", "**/*"),
    ("agent-quest/client/public/assets/themes/tiny-swords-cc0/UI/Icons",
     "ui/icons", "**/*"),
    ("agent-quest/client/public/assets/themes/tiny-swords-cc0/UI/Pointers",
     "ui/pointers", "**/*"),
    ("agent-quest/client/public/assets/themes/tiny-swords-cc0/UI/Ribbons",
     "ui/ribbons", "**/*"),
    ("agent-quest/client/public/assets/themes/tiny-swords-cc0/UI/Banners",
     "ui/banners", "**/*"),
]

#: Single-file rules: (source relative to refs, destination relative to assets/).
FILE_RULES: list[tuple[str, str]] = [
    ("starnet/frontend/assets/fonts/vt323.woff2", "fonts/vt323.woff2"),
]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_plan(refs: Path) -> list[tuple[Path, str]]:
    """Return the ordered [(absolute source, asset-relative dest)] allowlist.

    Fails loudly if a required source path is missing — the merge must not
    silently produce a partial tree.
    """
    plan: list[tuple[Path, str]] = []
    missing: list[str] = []

    for family in SPRITE_FAMILIES:
        src_dir = refs / "starnet/frontend/assets/sprites" / family
        if not src_dir.is_dir():
            missing.append(str(src_dir))
            continue
        for f in sorted(src_dir.glob("*.png")):
            plan.append((f, f"sprites/{family}/{f.name}"))

    for src_rel, dest_dir, glob in TREE_RULES:
        src_dir = refs / src_rel
        if not src_dir.is_dir():
            missing.append(str(src_dir))
            continue
        for f in sorted(src_dir.glob(glob)):
            if not f.is_file():
                continue
            # Preserve any nesting under the destination dir.
            rel = f.relative_to(src_dir).as_posix()
            plan.append((f, f"{dest_dir}/{rel}"))

    for src_rel, dest_rel in FILE_RULES:
        f = refs / src_rel
        if not f.is_file():
            missing.append(str(f))
            continue
        plan.append((f, dest_rel))

    if missing:
        raise SystemExit(
            "MISSING SOURCE PATHS (refs staging incomplete):\n  "
            + "\n  ".join(missing)
        )
    return plan


def compute_manifest(plan: list[tuple[Path, str]]) -> dict:
    entries = []
    total = 0
    for src, dest_rel in plan:
        size = src.stat().st_size
        total += size
        entries.append(
            {"path": dest_rel, "sha256": sha256_of(src), "bytes": size}
        )
    entries.sort(key=lambda e: e["path"])
    return {
        "asset_base_url": "/plugins/qubit-office",
        "schema": "qubit-office-assets/1",
        "source": "/refs (staging; never served, never imported)",
        "targets": [r.relative_to(REPO).as_posix() for r in TARGETS],
        "file_count": len(entries),
        "total_bytes": total,
        "max_total_bytes": MAX_TOTAL_BYTES,
        "excluded_families": EXCLUDED_FAMILIES,
        "files": entries,
    }


def expected_paths(plan: list[tuple[Path, str]]) -> set[str]:
    return {dest for _, dest in plan}


def actual_paths(asset_root: Path) -> set[str]:
    if not asset_root.is_dir():
        return set()
    out = set()
    for f in asset_root.rglob("*"):
        if f.is_file() and f.name != "MANIFEST.json":
            out.add(f.relative_to(asset_root).as_posix())
    return out


def do_write(plan: list[tuple[Path, str]], manifest: dict, refs: Path) -> int:
    total = manifest["total_bytes"]
    if total > MAX_TOTAL_BYTES:
        print(
            f"REFUSING: vendored tree would be {total} bytes "
            f"({total / 1048576:.1f} MB) > cap {MAX_TOTAL_BYTES} "
            f"({MAX_TOTAL_BYTES / 1048576:.0f} MB). Nothing written.",
            file=sys.stderr,
        )
        return 2

    manifest_json = json.dumps(manifest, indent=2, sort_keys=True) + "\n"

    for root in TARGETS:
        # Wipe and rewrite for a byte-identical, no-stale-extras result.
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=True)

        for src, dest_rel in plan:
            dest = root / dest_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)

        (root / "MANIFEST.json").write_text(manifest_json, encoding="utf-8")

    print(f"vendored {manifest['file_count']} files from {refs}")
    for root in TARGETS:
        print(f"  -> {root.relative_to(REPO)}")
    print(
        f"TOTAL SIZE: {total} bytes = {total / 1048576:.2f} MB "
        f"(cap {MAX_TOTAL_BYTES // 1048576} MB)"
    )
    return 0


def do_check(plan: list[tuple[Path, str]], manifest: dict) -> int:
    problems: list[str] = []

    for root in TARGETS:
        rel_root = root.relative_to(REPO)
        if not root.is_dir():
            problems.append(f"{rel_root}: does not exist")
            continue

        manifest_path = root / "MANIFEST.json"
        if not manifest_path.is_file():
            problems.append(f"{rel_root}: MANIFEST.json missing")
            continue

        on_disk_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if on_disk_manifest.get("total_bytes") != manifest["total_bytes"]:
            problems.append(
                f"{rel_root}: manifest total_bytes "
                f"{on_disk_manifest.get('total_bytes')} != current "
                f"{manifest['total_bytes']}"
            )

        # Every expected file present and hashing as recorded.
        recorded = {e["path"]: e for e in on_disk_manifest.get("files", [])}
        for dest_rel in sorted(expected_paths(plan)):
            f = root / dest_rel
            if not f.is_file():
                problems.append(f"{rel_root}: missing {dest_rel}")
                continue
            rec = recorded.get(dest_rel)
            if rec is None:
                problems.append(f"{rel_root}: not in manifest: {dest_rel}")
                continue
            actual_hash = sha256_of(f)
            if actual_hash != rec["sha256"]:
                problems.append(
                    f"{rel_root}: hash mismatch {dest_rel} "
                    f"(disk {actual_hash[:12]} != manifest {rec['sha256'][:12]})"
                )

        # No unlisted file under assets/ (catches an unnoticed drop).
        for extra in sorted(actual_paths(root) - expected_paths(plan)):
            problems.append(f"{rel_root}: unlisted file present: {extra}")

    if problems:
        print("STALE — vendored tree does not match source:", file=sys.stderr)
        for p in problems[:50]:
            print(f"  {p}", file=sys.stderr)
        if len(problems) > 50:
            print(f"  … and {len(problems) - 50} more", file=sys.stderr)
        return 1

    print(
        f"FRESH — {manifest['file_count']} files in {len(TARGETS)} trees, "
        f"{manifest['total_bytes'] / 1048576:.2f} MB, all hashes match"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Vendor Office art into the dist plugin tree.")
    ap.add_argument("--check", action="store_true", help="exit non-zero if the tree is stale")
    ap.add_argument("--refs", type=Path, default=DEFAULT_REFS, help="staging source root")
    args = ap.parse_args(argv)

    refs = args.refs.resolve()
    if not refs.is_dir():
        print(f"refs staging root not found: {refs}", file=sys.stderr)
        return 2

    plan = build_plan(refs)
    manifest = compute_manifest(plan)

    if args.check:
        return do_check(plan, manifest)
    return do_write(plan, manifest, refs)


if __name__ == "__main__":
    raise SystemExit(main())
