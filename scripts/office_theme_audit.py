#!/usr/bin/env python3
"""Report ``.ui-root`` variables in the vendored Habblaud CSS not allowlisted.

This is a DRIFT REPORT, not a gate. Qubit themes may only set the variables in
:data:`voicebridge.office.theme.ALLOWED_THEME_VARS`; when a new Habblaud build
adds a colour token, the mapping in ``voicebridge/office/theme.py`` should learn
it deliberately. Until it does, this script lists the variable so the omission
is visible instead of silent. It always exits 0 and never writes anything.

Only the base ``.ui-root`` declaration block is read. Layout variables declared
on state modifiers (``.ui-root.has-usage-row``) are the app's own geometry, not
theme colour tokens, and are deliberately out of scope.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from voicebridge.office.theme import ALLOWED_THEME_VARS  # noqa: E402

DEFAULT_CSS_DIR = (
    REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office" / "habblaud"
    / "bundle"
)

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_DECLARATION_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:")
# ``.ui-root``, ``html body .ui-root`` and ``:where(.ui-root)`` are the base
# root; ``.ui-root *`` / ``.ui-root.has-usage-row`` are descendants/states.
_BASE_ROOT_SELECTOR_RE = re.compile(r"(?:^|[\s>+~])\.ui-root$")
_WHERE_ROOT_SELECTOR_RE = re.compile(r"^:where\(\.ui-root\)$")


def _base_selector(selector: str) -> bool:
    selector = selector.strip()
    return bool(
        _BASE_ROOT_SELECTOR_RE.search(selector)
        or _WHERE_ROOT_SELECTOR_RE.match(selector)
    )


def parse_ui_root_vars(css: str) -> List[str]:
    """Every custom property declared by a base ``.ui-root`` rule, sorted."""
    css = _COMMENT_RE.sub("", css)
    found: set[str] = set()
    # The vendored bundles are flat: no nested rules carry ``.ui-root`` custom
    # properties, so a selector/body pair matcher is sufficient and keeps this
    # script dependency-free.
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selectors = [s for s in match.group(1).split(",") if _base_selector(s)]
        if not selectors:
            continue
        for name in _DECLARATION_RE.findall(match.group(2)):
            found.add(name)
    return sorted(found)


def collect_drift(
    css_texts: Iterable[str],
    allowed: Sequence[str] = ALLOWED_THEME_VARS,
) -> Dict[str, List[str]]:
    """The declared and unallowlisted ``.ui-root`` variables across files."""
    allowed_set = set(allowed)
    declared: set[str] = set()
    for text in css_texts:
        declared.update(parse_ui_root_vars(text))
    return {
        "declared": sorted(declared),
        "unallowlisted": sorted(declared - allowed_set),
        "allowed": sorted(declared & allowed_set),
    }


def _css_files(path: Path) -> List[Path]:
    if path.is_file():
        return [path]
    return sorted(path.glob("*.css"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        default=str(DEFAULT_CSS_DIR),
        help="a vendored CSS file or directory (default: the Habblaud bundle)",
    )
    parser.add_argument("--json", action="store_true", help="machine output")
    args = parser.parse_args(argv)

    root = Path(args.path)
    files = _css_files(root)
    if not files:
        if args.json:
            print(json.dumps({"error": f"no CSS found at {root}"}))
        else:
            print(f"no CSS found at {root}")
        return 0

    report = collect_drift(
        f.read_text(encoding="utf-8", errors="replace") for f in files)
    report["files"] = [str(f.relative_to(REPO)) if f.is_relative_to(REPO)
                       else str(f) for f in files]

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"files: {len(files)}")
        if report["unallowlisted"]:
            print("UNALLOWLISTED .ui-root variables (warning only):")
            for name in report["unallowlisted"]:
                print(f"  {name}")
        else:
            print("ok: every .ui-root variable is in ALLOWED_THEME_VARS")
        print(f"allowlisted: {len(report['allowed'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
