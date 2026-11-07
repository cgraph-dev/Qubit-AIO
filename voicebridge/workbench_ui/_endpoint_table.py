#!/usr/bin/env python3
"""Derive the endpoint port table from the SHIPPED code and the ported tree.

WHY THIS EXISTS
---------------------------------------------------------------------------
``docs/WORKBENCH_UI_PORT.md`` §2 claims "48 PORT / 6 EXISTING / 130 GAP of 184".
A claim in a doc is a promise; this script is the receipt. It reads

  * the endpoint set from the PORTED tree's own JS/HTML (the same grep the doc
    quotes), and
  * the verdicts from ``workbench_ui_routes.PORT_SET`` / ``EXISTING_SET``,

and prints the table. If the code and the doc disagree, the doc is wrong and
this script says so (exit 1). Run it after any change to the shim.

Usage:  python3 voicebridge/workbench_ui/_endpoint_table.py [--md]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE / "static"

#: 48 + 6 = 54 of 184. The exact numbers the doc's §2.2 states.
EXPECTED_PORT = 48
EXPECTED_EXISTING = 6
EXPECTED_GAP = 130
EXPECTED_TOTAL = 184


def endpoints_in_tree() -> list[str]:
    """Every distinct ``/api/...`` the ported JS and HTML call.

    Template tails are normalised (``/api/session/${sid}`` -> ``/api/session``)
    and the two non-endpoint fragments (``/api`` alone, ``/api/share/$``) are
    dropped, which is the same reduction the doc's §2.1 describes.
    """
    found: set[str] = set()
    for path in list(ROOT.rglob("*.js")) + list(ROOT.rglob("*.html")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r"['\"`](/api/[A-Za-z0-9_/.:$\-]*)", text):
            found.add(m.group(1))
        for m in re.finditer(r"/api/([A-Za-z0-9_/\-]*)\$\{", text):
            found.add("/api/" + m.group(1))
        for m in re.finditer(r"new URL\(\s*['\"`](/api/[A-Za-z0-9_/.:$\-]*)", text):
            found.add(m.group(1))
    out = []
    for e in found:
        e = re.sub(r"\$\{.*$", "", e).rstrip("/")
        if e in ("/api", "/api/share/$", ""):
            continue
        out.append(e)
    return sorted(set(out))


def verdicts() -> tuple[dict[str, str], dict[str, str], dict[str, str], list[str]]:
    """(port, existing, gap) maps endpoint -> handler-or-reason.

    ``PORT_SET`` keys are the path BELOW ``/api/wb/api/``; the endpoint the UI
    calls is ``/api/<that key>``.
    """
    sys.path.insert(0, str(HERE.parent.parent))
    from voicebridge import workbench_ui_routes as R  # noqa: E402

    port = {"/api/" + k: v for k, v in R.PORT_SET.items()}
    existing = {"/api/" + k: "qubit_compat pass-through" for k in R.EXISTING_SET}
    eps = endpoints_in_tree()
    gap = {e: "WB_NOT_IN_PORT_SET" for e in eps
           if e not in port and e not in existing}
    return port, existing, gap, eps


def main(argv: list[str]) -> int:
    port, existing, gap, eps = verdicts()
    as_md = "--md" in argv
    rows = [("PORT", e, port[e]) for e in sorted(port) if e in eps]
    rows += [("EXISTING", e, existing[e]) for e in sorted(existing) if e in eps]
    rows += [("GAP", e, gap[e]) for e in sorted(gap)]
    if as_md:
        print("| Verdict | Endpoint | Handler / reason |")
        print("|---|---|---|")
        for v, e, why in rows:
            print(f"| {v} | `{e}` | `{why}` |")
        print()
    print(f"measured endpoints in tree : {len(eps)}")
    print(f"PORT     : {sum(1 for v, _, _ in rows if v == 'PORT')}")
    print(f"EXISTING : {sum(1 for v, _, _ in rows if v == 'EXISTING')}")
    print(f"GAP      : {sum(1 for v, _, _ in rows if v == 'GAP')}")
    print(f"sum      : {len(rows)}")
    bad = []
    if len(eps) != EXPECTED_TOTAL:
        bad.append(f"{len(eps)} endpoints in tree, doc says {EXPECTED_TOTAL}")
    n_port = sum(1 for v, _, _ in rows if v == "PORT")
    n_ex = sum(1 for v, _, _ in rows if v == "EXISTING")
    n_gap = sum(1 for v, _, _ in rows if v == "GAP")
    if n_port != EXPECTED_PORT:
        bad.append(f"PORT {n_port}, doc says {EXPECTED_PORT}")
    if n_ex != EXPECTED_EXISTING:
        bad.append(f"EXISTING {n_ex}, doc says {EXPECTED_EXISTING}")
    if n_gap != EXPECTED_GAP:
        bad.append(f"GAP {n_gap}, doc says {EXPECTED_GAP}")
    for b in bad:
        print("DRIFT:", b)
    print("\nOK — code and doc agree" if not bad else "\nDRIFT — fix the doc or the shim")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
