#!/usr/bin/env python3
"""Generate docs/QOFFICE-REBRAND-INVENTORY.md from the tracked tree.

Counts are occurrence counts of the case-insensitive token ``habblaud`` per
tracked file. Classification is per file, because that is what a maintainer
acts on; the few files that mix categories are named in the doc's notes.

Run: python3 scripts/qoffice_rebrand_inventory.py
"""

from __future__ import annotations

import collections
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "QOFFICE-REBRAND-INVENTORY.md"
TOKEN = re.compile("habblaud", re.IGNORECASE)

#: This document and its generator describe the inventory itself; counting
#: their own prose would distort every category.
EXCLUDE = {"docs/QOFFICE-REBRAND-INVENTORY.md",
           "scripts/qoffice_rebrand_inventory.py"}

VENDORED = "frontend/qubit-app/plugins/qubit-office/habblaud/"

CATEGORIES = (
    "user-visible",
    "public API",
    "private code",
    "tests",
    "assets/bundle",
    "legal/provenance",
    "generated",
)


def tracked_files() -> list[str]:
    raw = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others",
         "--exclude-standard"], cwd=REPO)
    return [f for f in raw.decode("utf-8").split("\0") if f]


def classify(path: str) -> str:
    p = path.lower()
    name = os.path.basename(p)
    if p.startswith(VENDORED):
        if name.startswith("license") or name == "provenance.json":
            return "legal/provenance"
        return "assets/bundle"
    if (p.startswith("tests/") or "/tests/" in p
            or name.startswith("test_") or ".test." in name):
        return "tests"
    if ("third_party_notices" in p or name.startswith("license")
            or name == "provenance.json"
            or p == "docs/game-office/habblaud-provenance.md"):
        return "legal/provenance"
    if (p.startswith("docs/generated/") or p.startswith("frontend/qubit-dist/")
            or p.startswith("qubit_runtime/ui-tui/dist/")
            or name in ("qubit-office.game.js", "qubit-office.game.css")):
        return "generated"
    if (p == "voicebridge/qubit_compat.py" or p == "voicebridge/webui.py"
            or p == "scripts/public_tree_feature_parity.json"):
        return "public API"
    if p.startswith("docs/"):
        return "user-visible"
    if p.startswith("frontend/qubit-app/plugins/qubit-office/"):
        return "user-visible"
    return "private code"


def measure() -> list[tuple[str, int, str]]:
    rows: list[tuple[str, int, str]] = []
    for path in tracked_files():
        if path in EXCLUDE:
            continue
        target = REPO / path
        try:
            text = target.read_bytes().decode("utf-8", "ignore")
        except OSError:
            continue
        count = len(TOKEN.findall(text))
        if count:
            rows.append((path, count, classify(path)))
    return rows


def render(rows: list[tuple[str, int, str]]) -> str:
    by_category = collections.defaultdict(lambda: [0, 0])
    for _path, count, category in rows:
        by_category[category][0] += 1
        by_category[category][1] += count
    total_files = len(rows)
    total_occurrences = sum(count for _path, count, _cat in rows)
    non_asset_test = sum(
        count for _path, count, category in rows
        if category not in ("assets/bundle", "tests"))

    lines = [
        "# Qubit Office rebrand inventory — every `habblaud` reference",
        "",
        "**Status:** generated inventory. This is a measurement, not a completion",
        "claim and not a `VERIFIED` verdict. Regenerate it; do not hand-edit counts.",
        "",
        "```bash",
        "python3 scripts/qoffice_rebrand_inventory.py",
        "```",
        "",
        "Counts are case-insensitive occurrences of the literal token `habblaud`",
        "(covers `Habblaud`, `habblaud`, `HABBLAUD_*`) in tracked/unignored files at the",
        "time this doc was generated. The inventory doc and its generator are",
        "excluded so their own prose does not inflate the numbers.",
        "",
        "## Summary",
        "",
        "| Category | Files | Occurrences |",
        "|---|---:|---:|",
    ]
    for category in CATEGORIES:
        files, occurrences = by_category.get(category, [0, 0])
        lines.append(f"| {category} | {files} | {occurrences} |")
    lines += [
        f"| **Total** | **{total_files}** | **{total_occurrences}** |",
        "",
        f"Non-asset, non-test references (the first-wave rebrand scope): "
        f"**{non_asset_test}**.",
        "",
        "### How to read the categories",
        "",
        "- **user-visible** — product docs, the Qubit Office shell, plugin",
        "  manifest, document/PWA metadata. This is the lane's primary surface.",
        "- **public API** — `voicebridge/qubit_compat.py` and `voicebridge/webui.py`",
        "  route names, handler symbols and schema ids under",
        "  `/api/office/habblaud/*`. Deliberately unchanged in this lane.",
        "- **private code** — internal comments, tooling, build scripts, package",
        "  metadata. No user reads these; renaming is deferred.",
        "- **tests** — assertions and fixtures that pin the compat surface.",
        "- **assets/bundle** — the vendored client's hashed JS/CSS chunks, art and",
        "  webfonts. Renaming bundle files or the vendored directory is a later",
        "  phase with alias/redirect support.",
        "- **legal/provenance** — MIT notice, `PROVENANCE.json`, upstream commit,",
        "  `THIRD_PARTY_NOTICES.md`. The upstream name **must stay** here.",
        "- **generated** — built artifacts (`qubit-office.game.js`, dist trees) and",
        "  `docs/generated/` records.",
        "",
        "## Per-file inventory",
        "",
        "| Occurrences | Category | File |",
        "|---:|---|---|",
    ]
    for path, count, category in sorted(rows, key=lambda r: (-r[1], r[0])):
        lines.append(f"| {count} | {category} | `{path}` |")
    lines += [
        "",
        "### Docs: what is product copy and what is provenance",
        "",
        "Technical docs legitimately name the upstream component when they",
        "document its vendored files, licence, upgrade procedure or `--check`",
        "gate (for example `docs/OFFICE-UI-THEMES.md` says the theme emits a",
        "Habblaud CSS variable). Those references are kept: replacing them would",
        "make the provenance unreadable, and `docs/BRAND.md` allows naming",
        "upstream provenance. Product-copy docs and every shipped UI surface",
        "name **Qubit Office**; the guard checks the shipped surfaces, not",
        "internal audit/status prose.",
        "",
        "## Changed in this lane",
        "",
        "| Surface | File | Change |",
        "|---|---|---|",
        "| Brand constants | `frontend/qubit-app/plugins/qubit-office/game/app/brand.ts` | New central module: name, shortName, title, description, logo path, LimeZu credit, MIT/legal label, upstream pin. |",
        "| Iframe document title, aria-label, screen-reader copy | `.../game/app/OfficeAppShell.tsx` | Reads the brand module; credits surface gains the MIT legal label. |",
        "| Capability note | `.../game/app/office-api.ts` | Removed the upstream name from a user-facing note string. |",
        "| Document title + OG/Twitter/Safari labels | `.../habblaud/index.html` | `Qubit Office`; description says \"your agents\", not \"Claude Code agents\". |",
        "| PWA manifest | `.../habblaud/manifest.webmanifest` | `name: Qubit Office`, `short_name: Office`. |",
        "| Vendoring transform | `scripts/vendor_habblaud.py` | Brand labels are applied by the documented, idempotent `--retransform`; internal identifiers, paths and bundle filenames untouched. |",
        "| Legal record | `THIRD_PARTY_NOTICES.md` | New Habblaud MIT entry with upstream commit and local changes; copyright holder unchanged. |",
        "| Guard | `scripts/check_qoffice_brand.py`, `tests/test_qoffice_brand.py` | Fails if visible copy regains the upstream name or the MIT/provenance record disappears. |",
        "",
        "## Deliberately left for the API/rename phase",
        "",
        "1. `/api/office/habblaud/*` route aliases and `h_office_habblaud_*` handler",
        "   names (public API category).",
        "2. The vendored directory and hash-named bundle files (`assets/bundle`),",
        "   plus `habblaud:locale` and other internal storage keys.",
        "3. The vendored client's own runtime i18n strings: they are inside the",
        "   hashed bundle and are renamed only with a real re-vendor/alias window.",
        "4. Test assertions and fixtures that pin the compat surface.",
        "5. `docs/game-office/HABBLAUD-*` provenance/isolation records and the",
        "   vendored `LICENSE`/`PROVENANCE.json` (legal record, must stay).",
        "",
        "## Legal/provenance that must remain",
        "",
        "- `frontend/qubit-app/plugins/qubit-office/habblaud/LICENSE` —",
        "  MIT, Copyright (c) 2026 Márcio Junior.",
        "- `frontend/qubit-app/plugins/qubit-office/habblaud/PROVENANCE.json` —",
        "  upstream `4ccce2537472fe5a8d432f06974494d2e778ff15`, local changes.",
        "- `THIRD_PARTY_NOTICES.md` — Habblaud MIT entry and upstream commit.",
        "- `docs/game-office/HABBLAUD-PROVENANCE.md` — vendoring rationale.",
        "",
        "## Verify",
        "",
        "```bash",
        "python3 scripts/check_qoffice_brand.py",
        "python3 scripts/vendor_habblaud.py --check",
        "python3 -m pytest -q tests/test_qoffice_brand.py",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    rows = measure()
    OUT.write_text(render(rows), encoding="utf-8")
    total = sum(count for _path, count, _cat in rows)
    print(f"wrote {OUT.relative_to(REPO)}: {len(rows)} files, {total} occurrences")
    return 0


if __name__ == "__main__":
    sys.exit(main())
