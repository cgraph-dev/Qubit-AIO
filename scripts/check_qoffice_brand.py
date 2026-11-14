#!/usr/bin/env python3
"""Guard the user-visible Qubit Office brand surface.

Only product copy is checked. Internal identifiers, API route paths, the
vendored bundle's own i18n, provenance records and the MIT notice are exempt on
purpose: renaming those is the later API/bundle phase, and the upstream name
must remain in the legal record.

Exit 0 when every checked surface is clean; exit 1 with one line per violation.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
VENDORED = PLUGIN / "habblaud"
BRAND_TS = PLUGIN / "game" / "app" / "brand.ts"
SHELL_TS = PLUGIN / "game" / "app" / "OfficeAppShell.tsx"

BRAND_TOKEN = re.compile("habblaud", re.IGNORECASE)

#: Metadata attributes that are product copy. The vendored tree's icon paths
#: and API routes are deliberately not scanned.
META_KEYS = {
    "og:site_name",
    "og:title",
    "og:image:alt",
    "twitter:title",
    "apple-mobile-web-app-title",
    "description",
}

LEGAL_LABEL_FIELD = "legalLabel"


def _label(path: Path) -> str:
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def strip_ts_comments(source: str) -> str:
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", without_block)


def _brand_field(source: str, name: str) -> str | None:
    match = re.search(
        rf'\b{name}:\s*("(?:[^"\\]|\\.)*")', source, flags=re.DOTALL)
    if not match:
        return None
    return json.loads(match.group(1))


def index_metadata_violations(path: Path) -> list[str]:
    if not path.is_file():
        return [f"missing {_label(path)}"]
    html = path.read_text(encoding="utf-8")
    problems: list[str] = []
    title = re.search(r"<title>(.*?)</title>", html, flags=re.DOTALL)
    if not title:
        problems.append(f"{_label(path)}: no <title>")
    elif BRAND_TOKEN.search(title.group(1)):
        problems.append(f"{_label(path)}: <title> {title.group(1)!r}")
    for match in re.finditer(r"<meta\b[^>]*>", html, flags=re.IGNORECASE):
        tag = match.group(0)
        key = re.search(r'(?:property|name)="([^"]+)"', tag)
        content = re.search(r'content="([^"]*)"', tag)
        if not key or not content or key.group(1) not in META_KEYS:
            continue
        if BRAND_TOKEN.search(content.group(1)):
            problems.append(
                f"{_label(path)}: {key.group(1)}={content.group(1)!r}")
    return problems


def manifest_violations(path: Path) -> list[str]:
    if not path.is_file():
        return [f"missing {_label(path)}"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{_label(path)}: invalid JSON: {exc}"]
    problems: list[str] = []
    for key in ("name", "short_name", "description"):
        value = data.get(key)
        if isinstance(value, str) and BRAND_TOKEN.search(value):
            problems.append(f"{_label(path)}: {key}={value!r}")
    if data.get("name") != "Qubit Office":
        problems.append(
            f"{_label(path)}: name={data.get('name')!r}, expected 'Qubit Office'")
    return problems


def plugin_manifest_violations(path: Path) -> list[str]:
    if not path.is_file():
        return [f"missing {_label(path)}"]
    data = json.loads(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    for key in ("label", "description"):
        value = data.get(key)
        if isinstance(value, str) and BRAND_TOKEN.search(value):
            problems.append(f"{_label(path)}: {key}={value!r}")
    if data.get("label") != "Qubit Office":
        problems.append(
            f"{_label(path)}: label={data.get('label')!r}, expected 'Qubit Office'")
    return problems


def brand_source_violations(path: Path = BRAND_TS) -> list[str]:
    if not path.is_file():
        return [f"missing {_label(path)}"]
    source = path.read_text(encoding="utf-8")
    problems: list[str] = []
    for field in ("name", "shortName", "title", "description", "credit"):
        value = _brand_field(source, field)
        if value is None:
            problems.append(f"{_label(path)}: missing brand field {field!r}")
            continue
        if BRAND_TOKEN.search(value):
            problems.append(
                f"{_label(path)}: user-visible {field} contains the upstream name")
    legal = _brand_field(source, LEGAL_LABEL_FIELD)
    if legal is None:
        problems.append(f"{_label(path)}: missing {LEGAL_LABEL_FIELD!r}")
    elif "Habblaud" not in legal or "MIT" not in legal:
        problems.append(
            f"{_label(path)}: {LEGAL_LABEL_FIELD!r} must keep the "
            "Habblaud MIT legal notice")
    return problems


def shell_copy_violations(path: Path = SHELL_TS) -> list[str]:
    if not path.is_file():
        return [f"missing {_label(path)}"]
    source = strip_ts_comments(path.read_text(encoding="utf-8"))
    if BRAND_TOKEN.search(source):
        return [f"{_label(path)}: shell copy still names the upstream product"]
    return []


def legal_notice_violations() -> list[str]:
    problems: list[str] = []
    licence = VENDORED / "LICENSE"
    if not licence.is_file():
        problems.append("missing vendored LICENSE")
    else:
        text = licence.read_text(encoding="utf-8")
        if "MIT License" not in text or "Márcio Junior" not in text:
            problems.append("vendored LICENSE lost the MIT copyright notice")
    provenance = VENDORED / "PROVENANCE.json"
    if not provenance.is_file():
        problems.append("missing vendored PROVENANCE.json")
    else:
        record = json.loads(provenance.read_text(encoding="utf-8"))
        if record.get("licence") != "MIT":
            problems.append("PROVENANCE.json does not record MIT")
        if record.get("commit") != "4ccce2537472fe5a8d432f06974494d2e778ff15":
            problems.append("PROVENANCE.json upstream commit changed")
    notices = REPO / "THIRD_PARTY_NOTICES.md"
    if not notices.is_file():
        problems.append("missing THIRD_PARTY_NOTICES.md")
    else:
        text = notices.read_text(encoding="utf-8")
        if "Habblaud" not in text or "MIT" not in text:
            problems.append("THIRD_PARTY_NOTICES.md lacks the Habblaud MIT record")
        if "4ccce2537472fe5a8d432f06974494d2e778ff15" not in text:
            problems.append("THIRD_PARTY_NOTICES.md lacks the upstream commit")
    return problems


def check() -> list[str]:
    problems: list[str] = []
    problems += index_metadata_violations(VENDORED / "index.html")
    problems += manifest_violations(VENDORED / "manifest.webmanifest")
    problems += plugin_manifest_violations(PLUGIN / "manifest.json")
    problems += brand_source_violations()
    problems += shell_copy_violations()
    problems += legal_notice_violations()
    return problems


def main() -> int:
    problems = check()
    if problems:
        for problem in problems:
            print(f"FAIL {problem}", file=sys.stderr)
        return 1
    print("ok: Qubit Office visible brand clean; MIT notice and provenance present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
