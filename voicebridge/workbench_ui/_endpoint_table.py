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

#: 91 of 184 answer; 93 remain GAP. MEASURED from the shipped tables
#: (``PORT_SET`` + ``_EXTRA_PREFIXES`` + ``EXISTING_SET`` + the
#: ``command_family.ROUTES`` table). The doc's earlier 48/130 predated the
#: config, extension and auth wave AND under-counted the seven
#: ``_EXTRA_PREFIXES`` routes the gateway already served. ``PORT`` here is
#: therefore an ANSWERED count (85 PORT_SET-shaped + the 6 EXISTING live inside
#: it): what matters, and what is asserted, is that every endpoint the ported
#: page calls is either answered or answers a typed refusal, and that neither
#: number drifts silently from the code.
#:
#: Two lanes were live in the same tree while these numbers moved (the
#: file-family/commands wave served ``file/*`` and ``commands/*``), so
#: ``EXPECTED_ANSWERED`` is the whole answered set, not one lane's share.
#: 99 = the 8 ``file/*`` write verbs + ``file/raw`` + the 6 ``commands*``/``btw``
#: paths that wave added on top of the previous 91; every one of them answers
#: real data or a TYPED refusal (``WB_CAPABILITY_UNBOUND`` /
#: ``WB_BROKERED_WRITE``), so none is counted GAP.
#:
#: 118 = 99 + the settings/onboarding/updates wave's 19 (``handlers_settings.py``):
#: 11 answering real authority (auxiliary config, cost history, the four
#: onboarding verbs + three oauth verbs, system health, gateway status, git-info)
#: and 8 answering a NAMED refusal (self-hosted provider write, quota,
#: models/refresh, gateway restart/lifecycle x4, updates apply/force/clear_lock/
#: summary). A refusal still counts ANSWERED — what matters is that the panel
#: gets a reason instead of a spinner, and that no path silently invents data.
#:
#: 130 = 118 + the kanban family's 12 (``workbench_ui/kanban_family.py``). The
#: doc called all twelve GAP; the paths were missing, not the authority —
#: ``voicebridge.kanban`` is the ONE boards store the CLI, voice and
#: /qubit-boards already write. Ten answer real store data; ``kanban/links`` is
#: a NAMED refusal (the store has no dependency edges) and ``links/delete`` is
#: its idempotent no-op, both still ANSWERED because the panel gets a reason.
EXPECTED_ANSWERED = 130
EXPECTED_EXISTING = 6
EXPECTED_GAP = 54
EXPECTED_TOTAL = 184

#: The chat routes are PORT and always were: ``verdicts()`` below resolves
#: ``/api/chat/start``, ``/api/chat/steer``, ``/api/chat/stop`` and
#: ``/api/chat/stream/status`` from ``PORT_SET``, and ``/api/chat/stream`` is
#: served as SSE by ``workbench_ui_routes.stream_kind``. A ``docs/…§2.5`` group
#: row once claimed "Chat / streaming | 3" as the biggest gap; THIS table never
#: agreed (0 chat paths in ``gap``), so the count was right and the prose was
#: wrong. Kept as a guard: if a chat path ever lands in GAP again it will show
#: up as a count drift here, not as prose someone has to notice.
CHAT_ROUTES = ("/api/chat/start", "/api/chat/steer", "/api/chat/stop",
               "/api/chat/stream", "/api/chat/stream/status")


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
    # ``_EXTRA_PREFIXES`` are dispatched by the gateway too (they need the query
    # string or the raw sub-path, so they are not PORT_SET keys). Leaving them
    # out counted seven SERVED endpoints as GAP — the table under-reported what
    # the server actually answers, which is the opposite of a receipt.
    for k, v in R._EXTRA_PREFIXES.items():
        port.setdefault("/api/" + k, v)
    # The git / worktree / terminal / workspace families keep their own table
    # (``workbench_ui/command_family.py``). Counting them from THAT table rather
    # than from a copy here keeps this receipt honest: a family path that loses
    # its handler shows up as GAP again, which is the whole point.
    from voicebridge.workbench_ui import command_family as CF
    for k in CF.ROUTES:
        port.setdefault("/api/" + k, "command_family")
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
    if n_port + n_ex != EXPECTED_ANSWERED:
        bad.append(f"{n_port + n_ex} answered, doc says {EXPECTED_ANSWERED}")
    if n_ex != EXPECTED_EXISTING:
        bad.append(f"EXISTING {n_ex}, doc says {EXPECTED_EXISTING}")
    if n_gap != EXPECTED_GAP:
        bad.append(f"GAP {n_gap}, doc says {EXPECTED_GAP}")
    # A chat route in GAP is a real regression: those four are the pane's whole
    # turn path, and the count above would still pass if some OTHER path had
    # been un-ported in the same change. Naming them makes the drift specific.
    chat_missing = [r for r in CHAT_ROUTES
                    if r not in port and r not in existing and r != "/api/chat/stream"]
    if chat_missing:
        bad.append(f"chat routes fell out of PORT: {', '.join(chat_missing)}")
    for b in bad:
        print("DRIFT:", b)
    print("\nOK — code and doc agree" if not bad else "\nDRIFT — fix the doc or the shim")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
